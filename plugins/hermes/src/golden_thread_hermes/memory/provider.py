"""Golden Thread memory provider for Hermes Agent.

Exposes 52 Golden Thread commands as Hermes tools, backed by a local markdown
vault. The 39 canonical `/gt:gt-*` commands from the Claude Code plugin are all
present; the remaining tools are Hermes-specific helpers, aliases, or stubs for
UI-only / external-subsystem commands that have no Hermes equivalent.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider as BaseMemoryProvider
from tools.registry import tool_result, tool_error

from .commands import CommandContext, dispatch_command, get_command_schemas
from .config import GoldenThreadConfig
from .project import ProjectManager
from .session_claims import SessionClaims
from .vault import Vault


def _run_coroutine_sync(awaitable: Any) -> Any:
    """Drive a coroutine to completion from sync code, with or without a live loop.

    Hermes executes memory providers synchronously inside the agent's running
    event loop (desktop/gateway execution path). asyncio.run() called there
    raises "RuntimeError: cannot be called from a running event loop"; in that
    case run the coroutine on a worker thread with its own event loop instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)  # no live loop: direct/CLI context
    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
            box["error"] = exc

    worker = threading.Thread(target=_worker, name="gt-coro-bridge", daemon=True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


class GoldenThreadMemoryProvider(BaseMemoryProvider):
    """Hermes memory provider backed by a Golden Thread vault."""

    _NAME = "golden-thread"

    # Only the Hermes desktop chat ships the chat-widget surface that can
    # render ::preview{} HTML panes. tui/cli/telegram/acp sessions must not
    # see the pane instruction (it would print as raw text there).
    _PANE_PLATFORMS = frozenset({"desktop"})

    def __init__(self) -> None:
        self.config = GoldenThreadConfig.load()
        self.vault: Vault = Vault(self.config.vault_path)
        self.project = ProjectManager(self.vault, self.config.active_project)
        self._session_claims: SessionClaims | None = None
        self._init_capture_state()
        self._init_ui_state()

    @property
    def name(self) -> str:
        return self._NAME

    def is_available(self) -> bool:
        return self.config.vault_path.exists()

    def _init_capture_state(self) -> None:
        """Session-scoped tracking for the compression/session-end hooks.

        Deliberately does NOT touch self._session_id: this runs from
        initialize() and on_session_switch(), both of which assign the id
        themselves — resetting it here would wipe a freshly rotated id
        (2026-11-18: /new and compression would have blanked it)."""
        self._session_events: List[str] = []
        self._last_checkpoint_turns: int | None = None

    def _init_ui_state(self) -> None:
        """Session-scoped surface info for the desktop pane auto-offer."""
        self._platform: str = ""
        self._hermes_home: str = ""

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self.config = GoldenThreadConfig.load()
        self.vault = Vault(self.config.vault_path)
        self.project = ProjectManager(self.vault, self.config.active_project)
        self._session_claims = SessionClaims(self.config.vault_path)
        self._init_capture_state()
        self._session_id = session_id
        self._init_ui_state()
        self._platform = str(kwargs.get("platform", "") or "")
        self._hermes_home = str(kwargs.get("hermes_home", "") or "")

    def system_prompt_block(self) -> str:
        core = self.vault.read_core(max_lines=self.config.core_rules_max_lines)
        if not core:
            return ""
        block = (
            "Golden Thread Core Rules\n"
            "\n"
            f"{core}\n"
            "\n"
            "When the user says anything about a Golden Thread command (e.g., gt_*), "
            "call the matching tool."
        )
        # Claude Code injects the vault's core-rules/ tier on EVERY turn
        # (hooks/inject_core_rules.sh); system prompts in Hermes persist across
        # compaction, so session-start injection is the adapter equivalent.
        try:
            rules = self.vault.read_core_rules(max_lines=self.config.core_rules_max_lines)
        except Exception:  # never break session startup over the tier
            rules = ""
        if rules:
            block += "\n\n" + rules
        elif self.vault._core_rules_tier() is None:
            # Announce, don't be silent (upstream: degradation is announced,
            # not silent) — otherwise a vault without the tier reads as healthy.
            block += (
                "\n\n(core-rules/ directory not found in this vault; the per-turn "
                "Core rules tier is not injected. gt upgrade adds it.)"
            )
        try:
            pane = self._pane_block()
        except Exception:  # never break session startup over the pane offer
            pane = ""
        if pane:
            block += "\n\n" + pane
        return block

    # ------------------------------------------------------------------
    # Desktop command pane (gt-pane-button parity)
    #
    # The pane itself is a machine-local HTML file; this session-start
    # auto-offer lives in the system prompt so the agent emits the widget on
    # a fresh session (and re-offers after compression, which can swallow it).
    # ------------------------------------------------------------------

    def _pane_widget_path(self) -> str:
        """Locate the gt pane HTML file; "" when no pane exists locally."""
        explicit = (self.config.ui_pane_path or "").strip()
        candidates: List[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        else:
            home = Path(self._hermes_home) if self._hermes_home else None
            if home is None:
                from hermes_constants import get_hermes_home
                home = get_hermes_home()
            # Nearest first: profile dir, then its ancestors (named profiles
            # live under hermes_home/profiles/<name>/ and share the root pane).
            candidates.append(home / "gt-pane.html")
            for anc in list(home.parents)[:3]:
                candidates.append(anc / "gt-pane.html")
        for cand in candidates:
            try:
                if cand.is_file():
                    return str(cand)
            except OSError:
                continue
        return ""

    def _pane_block(self) -> str:
        if not self.config.ui_pane_auto or self._platform not in self._PANE_PLATFORMS:
            return ""
        pane_path = self._pane_widget_path()
        if not pane_path:
            return ""
        return (
            "Golden Thread command pane (desktop): on your first reply of a fresh "
            "session, and again on your first reply after a context compression, "
            "end your message with the pane widget below and mention it in one "
            "short line. To display it, emit this exact line on its own:\n"
            f'::preview{{file="{pane_path}"}}\n'
            "Do not re-emit it on ordinary later turns. Pane button clicks arrive "
            'as hidden user turns beginning with "GT pane →" — perform the '
            "requested gt action, then update the #gt-status div inside the pane "
            "file so the result appears on the pane itself."
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return get_command_schemas()

    # Commands that write into the *active project* of whatever they are
    # routed at. In an unbound session these are refused: sessions bind
    # themselves via gt_open/gt_create (fresh-start, 2026-11-20) — there is
    # no global fallback to write into anymore.
    _WRITE_TO_ACTIVE_COMMANDS = frozenset({
        "gt_work", "gt_decide", "gt_plan", "gt_implement", "gt_validate",
        "gt_validation", "gt_workflow", "gt_complete", "gt_handle",
        "gt_review",
    })

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        self._record_tool_event(tool_name, args)
        sid = str(kwargs.get("session_id", "") or self._session_id)
        _, scope = self._resolve_active(sid)
        if scope != "session" and tool_name in self._WRITE_TO_ACTIVE_COMMANDS:
            return tool_result({
                "status": "unbound",
                "message": "No project is claimed for this session. Hermes "
                           "sessions each bind their own project; run "
                           "gt_open('<project-slug>') in this session first.",
            })
        ctx = CommandContext(
            tool_name=tool_name,
            vault_path=self.vault,
            # Route tool calls at THIS session's claim: handle_tool_call does
            # not guarantee a runtime-passed session_id, so the maintained
            # self._session_id (on_session_switch) is the source of truth.
            project_manager=self._session_pm(sid),
            config=self.config,
            session_id=sid,
            session_claims=self.session_claims,
        )
        try:
            result = _run_coroutine_sync(dispatch_command(ctx, args))
        except Exception as exc:
            self._mark_last_event_error()
            return tool_error(str(exc))
        return tool_result(result)

    def _record_tool_event(self, tool_name: str, args: Dict[str, Any]) -> None:
        """Keep a bounded trail of gt_* activity for the compression hooks."""
        if not tool_name.startswith("gt_"):
            return
        brief = " ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:2])
        event = f"{tool_name}({brief})" if brief else tool_name
        self._session_events.append(event)
        if len(self._session_events) > 40:  # bounded ring, drop oldest
            del self._session_events[: len(self._session_events) - 40]

    def _mark_last_event_error(self) -> None:
        if self._session_events:
            self._session_events[-1] += " (error)"

    # ---------------------------------------------------------------------------
    # Per-session active-project routing
    #
    # One provider instance serves every session the agent multiplexes (desktop
    # windows, gateway chats, /new, /resume, compression rotations). Binding one
    # ProjectManager to the single global config.active_project made "whoever
    # gt_open'ed last wins" decide where EVERY session's writes landed — on
    # 2026-11-18 a Daggerheart Q&A session that never opened a project inherited
    # cqnw and misfiled its checkpoints into cqnw/Log. Active projects are now
    # claimed per session_id (SessionClaims). Fresh-start (2026-11-20): there
    # is no shared fallback at all — nothing reads or writes
    # config.active_project for routing, unbound sessions capture nothing, and
    # write-commands are refused until the session gt_open's its own project.
    # ---------------------------------------------------------------------------

    @property
    def session_claims(self) -> SessionClaims:
        if self._session_claims is None:
            self._session_claims = SessionClaims(self.config.vault_path)
        return self._session_claims

    def _resolve_active(self, session_id: str = "") -> tuple:
        """-> (slug, scope): scope is 'session' (this session claimed a project
        via gt_open/gt_create) or 'fallback' (no claim)."""
        sid = session_id or self._session_id
        claimed = ""
        if sid:
            try:
                claimed = self.session_claims.get(sid)
            except Exception:  # claims-file IO must never break memory routing
                pass
        if claimed:
            return claimed, "session"
        return self.config.active_project, "fallback"

    def _session_pm(self, session_id: str = "") -> ProjectManager:
        """ProjectManager bound to this session's (or fallback) project.

        Fresh per call on purpose: a shared instance would smuggle one
        session's slug into another session's writes."""
        return ProjectManager(self.vault, self._resolve_active(session_id)[0])

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        slug, scope = self._resolve_active(session_id)
        if scope == "fallback":
            # Never inject another session's project into a context that made
            # no claim — that is exactly how cqnw leaked everywhere. Under
            # fresh-start semantics there is no shared fallback either, so do
            # not name any project at all; just say how to bind.
            if self._unattended_session():
                return ""  # cron / background: stay silent, no nagging
            return (
                "Golden Thread: no project is claimed for this session and "
                "auto-capture is paused. Run gt_open('<project-slug>') to "
                "bind one."
            )
        if not slug:
            return ""
        ctx = self._session_pm(session_id).read_context()
        if not ctx:
            return ""
        return (
            f"Active Golden Thread project: {slug} (claimed for this session)\n"
            "---\n"
            f"{ctx}"
        )

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        pass

    def _unattended_session(self) -> bool:
        """True for cron jobs and one-shot single-query runs.

        Unattended sessions have no conversation worth capturing: before this
        gate, every cron turn's response text and its "session end" checkpoint
        landed in the active project's Log/ and flooded it with machine noise
        (2026-11-08: ~170 noise lines vs 11 real entries in one day). Cron
        jobs carry the HERMES_CRON_SESSION gateway session variable;
        single-query CLI runs get it (and the single-query marker) in
        os.environ. gt_* tool calls from such jobs still log as usual — only
        the automatic per-turn capture is gated.
        """
        markers: List[str] = []
        try:
            from gateway.session_context import get_session_env

            markers.append(get_session_env("HERMES_CRON_SESSION", ""))
        except Exception:  # pragma: no cover - no gateway runtime in-process
            pass
        markers.append(os.environ.get("HERMES_CRON_SESSION", ""))
        markers.append(os.environ.get("HERMES_SINGLE_QUERY_SESSION", ""))
        for marker in markers:
            if marker is True:
                return True
            text = str(marker).strip().lower()
            if text and text not in ("0", "false", "no", "off"):
                return True
        return False

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: List[Dict[str, Any]] | None = None,
    ) -> None:
        # Explicit session claims only (see _resolve_active): a session that
        # never claimed a project captures nothing — before the fix it would
        # misfile into the last-opened project's Log (the cqnw bug).
        # Stale-turn guard: after on_session_switch (e.g. /new or compression)
        # a late turn for the retired session id must not capture under the
        # old claim — only the provider's live session id writes.
        sid = session_id or self._session_id
        if sid != self._session_id:
            return
        slug, scope = self._resolve_active(sid)
        if scope == "session" and slug and not self._unattended_session():
            self._session_pm(sid).log(assistant_content[:240])

    # ------------------------------------------------------------------
    # Capture-before-compression hooks (gt-minimize parity)
    #
    # Claude Code's gt-minimize triages a session BEFORE compacting so facts
    # are not lost to compression. Hermes compresses automatically with no
    # manual trigger, so parity lives in two optional base-class hooks:
    # on_pre_compress runs before automatic compaction; on_session_end runs
    # at session close. Both append one deterministic line to the active
    # project's Log (vault trail), and on_pre_compress additionally returns
    # a checkpoint block Hermes surfaces in the compression summary.
    # ------------------------------------------------------------------

    def _events_digest(self) -> str:
        if not self._session_events:
            return "no gt_* tool activity"
        counts = Counter(e.split("(", 1)[0] for e in self._session_events)
        bycount = ", ".join(f"{name} ×{n}" for name, n in counts.most_common())
        return f"{bycount}; last: {self._session_events[-1]}"

    def _checkpoint_text(self, turns: int) -> str:
        slug, scope = self._resolve_active(self._session_id)
        if scope == "fallback":
            project = "none (not bound — gt_open to bind)"
        else:
            project = f"{slug} (session claim)"
        return (
            f"[GT checkpoint] active project: {project}; "
            f"transcript turns: {turns}; vault activity: {self._events_digest()}"
        )

    def _log_capture(self, label: str, turns: int) -> None:
        try:
            # Explicit session claims only. Compression and session-end hooks
            # carry no runtime session_id; they route via self._session_id,
            # kept current by on_session_switch (/new, /resume, compression).
            slug, scope = self._resolve_active(self._session_id)
            if scope == "session" and slug and not self._unattended_session():
                self._session_pm(self._session_id).log(f"{label}: turns≈{turns}; {self._events_digest()}")
        except Exception:  # never raise into the runtime's compression path
            pass

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        turns = len(messages) if messages else 0
        if self._last_checkpoint_turns != turns:
            self._log_capture("pre-compression checkpoint", turns)
            self._last_checkpoint_turns = turns
        if not self._resolve_active(self._session_id)[0] and not self._session_events:
            return ""
        return self._checkpoint_text(turns)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        turns = len(messages) if messages else 0
        self._last_checkpoint_turns = None  # next session starts fresh
        self._log_capture("session end", turns)

    def on_session_switch(
        self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False,
        rewound: bool = False, **kwargs: Any,
    ) -> None:
        """session_id reassigned mid-process (/new, /resume, /branch, /reset,
        compression). All persistent routing state lives in the shared
        SessionClaims file — only the in-memory id and the per-session capture
        trail need rotating here."""
        self._session_id = new_session_id
        self._init_capture_state()
        if parent_session_id and parent_session_id != new_session_id:
            # Compression/resume rotate the id of the SAME conversation: carry
            # its claim over so capture continues seamlessly under the new id.
            try:
                slug = self.session_claims.get(parent_session_id)
                if slug:
                    self.session_claims.set(new_session_id, slug)
                    self.session_claims.clear(parent_session_id)
            except Exception:  # claims-file IO must never break the switch
                pass
