"""Per-session active-project claims for the Golden Thread Hermes plugin.

Hermes drives ONE provider instance through many sessions: desktop windows,
gateway chats, /new, /resume and compression rotations all arrive alternately
at the same GoldenThreadMemoryProvider. Until 2026-11-18 the provider bound a
single global config.active_project ("whoever gt_open'ed last wins"), so a
session that never called gt_open inherited a foreign project and every
auto-writer (per-turn paste, pre-compression checkpoint, session-end line)
misfiled into it — e.g. a Daggerheart Q&A session landing in cqnw/Log.

Design (Projects/hermes-golden-thread Log/2026-11-15.md, implemented here):
bind active projects per session_id. The claims file lives under the vault's
.gitignored-free .gt/ dir so gateway, desktop and CLI processes that point at
the same vault all see the same claims, and tests stay hermetic (tmp vault).
config.active_project survives only as a vestigial read: fresh-start
(2026-11-20) removed every seed write and every seed read from routing.
Unbound sessions capture nothing, refusals on write-commands ("unbound"),
and gt_open/gt_create claim the calling session ONLY.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict

_PRUNE_DAYS = 7
_LOCK_SUFFIX = ".lock"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry_ts(entry: Any) -> float:
    """Timestamp of a claim entry; 0.0 for malformed entries (pruned at once)."""
    try:
        return datetime.fromisoformat(str(entry.get("updated_at", ""))).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0.0


class SessionClaims:
    """Which Golden Thread project each Hermes session has claimed.

    File: <vault>/.gt/hermes-session-claims.json
          {"sessions": {"<session_id>": {"project": "<slug>", "updated_at": "..."}}, ...}

    Writes are atomic (tmp + os.replace) and serialized per-process with an
    flock; readers need no lock because os.replace publishes whole-file states.
    """

    def __init__(self, vault_path: Any) -> None:
        self.path = Path(vault_path) / ".gt" / "hermes-session-claims.json"

    # -- internals -----------------------------------------------------------

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"sessions": {}}

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".claims-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _rmw(self, mutate: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
        """Read-modify-write under an exclusive lock; returns the new state."""
        # The lock sidecar is created before _write() gets a chance to make
        # the .gt/ parent, so a first-ever claim on a fresh vault must mkdir.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + _LOCK_SUFFIX, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self._read()
            result = mutate(data)
            self._write(result)
            return result

    @staticmethod
    def _prune(sessions: Dict[str, Any], max_age_days: int = _PRUNE_DAYS) -> int:
        """Drop expired/malformed entries in place. Returns removed count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
        stale = [sid for sid, entry in sessions.items() if _entry_ts(entry) < cutoff]
        for sid in stale:
            del sessions[sid]
        return len(stale)

    # -- public API ----------------------------------------------------------

    def get(self, session_id: str) -> str:
        """Claimed project slug for this session; '' when none."""
        if not session_id:
            return ""
        entry = self._read()["sessions"].get(session_id)
        if isinstance(entry, dict) and isinstance(entry.get("project"), str):
            return entry["project"]
        return ""

    def set(self, session_id: str, slug: str) -> None:
        """Claim `slug` for `session_id`; prunes expired entries as a side effect."""
        if not session_id or not slug:
            return

        def mutate(data: Dict[str, Any]) -> Dict[str, Any]:
            sessions = data["sessions"]
            self._prune(sessions)
            sessions[session_id] = {"project": slug, "updated_at": _now_iso()}
            return data

        self._rmw(mutate)

    def clear(self, session_id: str) -> str:
        """Release this session's claim. Returns the previously claimed slug."""
        previous = self.get(session_id)
        if previous:

            def mutate(data: Dict[str, Any]) -> Dict[str, Any]:
                data["sessions"].pop(session_id, None)
                return data

            self._rmw(mutate)
        return previous

    def prune(self, max_age_days: int = _PRUNE_DAYS) -> int:
        """Manually expire stale entries (auto-pruned on every set())."""
        cleared = {"n": 0}

        def mutate(data: Dict[str, Any]) -> Dict[str, Any]:
            cleared["n"] = self._prune(data["sessions"], max_age_days)
            return data

        self._rmw(mutate)
        return cleared["n"]
