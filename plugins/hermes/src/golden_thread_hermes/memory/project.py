"""Project-level operations inside a Golden Thread vault."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import canonical
from .vault import Vault


class ProjectManager:
    """CRUD + logging + workflows for a Golden Thread project."""

    def __init__(self, vault: Vault, active_slug: str = "") -> None:
        self.vault = vault
        self.active_slug = active_slug.strip()

    # -----------------------------------------------------------------------
    # Basic project operations
    # -----------------------------------------------------------------------

    def _require_active(self) -> None:
        if not self.active_slug:
            raise RuntimeError("No active project. Call gt_open first.")

    def open(self, slug: str) -> None:
        slug = slug.strip()
        project_dir = self.vault.project_path(slug)
        if not project_dir.exists():
            raise FileNotFoundError(f"Project '{slug}' does not exist")
        self.active_slug = slug

    async def open_command(self, slug: str, config: Any) -> Dict[str, Any]:
        self.open(slug)
        config.active_project = slug
        config.save()
        ctx = self.read_context()
        return {"status": "ok", "active_project": slug, "context_preview": ctx[:500]}

    def list(self) -> List[str]:
        return self.vault.list_projects()

    async def list_command(self) -> List[str]:
        return self.list()

    def create(self, name: str, idea: str) -> str:
        slug = self.vault.slugify(name)
        project_dir = self.vault.project_path(slug)
        if project_dir.exists():
            base = slug
            n = 2
            while project_dir.exists():
                slug = f"{base}-{n}"
                project_dir = self.vault.project_path(slug)
                n += 1
        self.vault.ensure_dir(project_dir)
        self.vault.ensure_dir(project_dir / "Decisions")
        self.vault.ensure_dir(project_dir / "Log")
        self.vault.ensure_dir(project_dir / "Memory")
        self.vault.ensure_dir(project_dir / "Tasks")
        self.vault.ensure_dir(project_dir / "Handoffs")
        self.vault._write(f"Projects/{slug}/idea.md", f"# {name}\n\n{idea}\n", op="replace-file")
        self.vault._write(f"Projects/{slug}/CONVENTIONS.md", f"# CONVENTIONS\n\nProject-specific conventions for {name}.\n", op="replace-file")
        self.vault._write(f"Projects/{slug}/runbook.md", f"# Runbook: {name}\n\nOperational notes and recurring procedures.\n", op="replace-file")
        self.active_slug = slug
        return slug

    async def create_command(self, name: str, idea: str, config: Any) -> Dict[str, Any]:
        slug = self.create(name, idea)
        config.active_project = slug
        config.save()
        return {"status": "created", "project": slug, "path": str(self.vault.project_path(slug))}

    def log(self, summary: str) -> None:
        self._require_active()
        log_dir = self.vault.project_path(self.active_slug) / "Log"
        self.vault.ensure_dir(log_dir)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"- {ts}: {summary}\n"
        self.vault._write(f"Projects/{self.active_slug}/Log/{today}.md", entry, op="append")

    async def work_command(self, summary: str) -> Dict[str, Any]:
        self.log(summary)
        return {"status": "logged", "project": self.active_slug}

    def decide(
        self,
        title: str,
        context: str,
        decision: str,
        consequences: str,
    ) -> str:
        self._require_active()
        decisions_dir = self.vault.project_path(self.active_slug) / "Decisions"
        self.vault.ensure_dir(decisions_dir)
        existing = sorted(decisions_dir.glob("ADR-*.md"))
        next_id = len(existing) + 1
        adr_id = f"ADR-{next_id:04d}"
        slug = re.sub(r"[^\w]+", "-", title).lower().strip("-")
        file_name = f"{adr_id}-{slug}.md" if slug else f"{adr_id}.md"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        body = (
            f"# {adr_id}: {title}\n\n"
            f"Date: {today}\n\n"
            f"## Context\n\n{context}\n\n"
            f"## Decision\n\n{decision}\n\n"
            f"## Consequences\n\n{consequences}\n"
        )
        self.vault._write(f"Projects/{self.active_slug}/Decisions/{file_name}", body, op="replace-file")
        return adr_id

    async def decide_command(
        self,
        title: str,
        context: str,
        decision: str,
        consequences: str,
    ) -> Dict[str, Any]:
        adr_id = self.decide(title, context, decision, consequences)
        return {"status": "recorded", "adr": adr_id}

    def add_memory(self, content: str, category: str = "general") -> None:
        self._require_active()
        mem_dir = self.vault.project_path(self.active_slug) / "Memory"
        self.vault.ensure_dir(mem_dir)
        file_path = mem_dir / f"{category}.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.vault._write(f"Projects/{self.active_slug}/Memory/{category}.md", f"- {ts}: {content}\n", op="append")

    def read_context(self, max_lines: int = 400) -> str:
        if not self.active_slug:
            return ""
        self.vault._drain_if_outstanding()
        project_dir = self.vault.project_path(self.active_slug)
        candidates = [
            project_dir / "idea.md",
            project_dir / "CONVENTIONS.md",
            project_dir / "runbook.md",
        ]
        lines: List[str] = []
        for cand in candidates:
            if cand.exists():
                text = cand.read_text(encoding="utf-8").splitlines()
                lines.extend(text)
                lines.append("")
                if len(lines) >= max_lines:
                    break
        # Append latest decision
        decisions_dir = project_dir / "Decisions"
        if decisions_dir.exists():
            adrs = sorted(decisions_dir.glob("ADR-*.md"))
            if adrs:
                lines.append("## Latest decision")
                lines.extend(adrs[-1].read_text(encoding="utf-8").splitlines()[:80])
                lines.append("")
        # Append latest log line
        log_dir = project_dir / "Log"
        if log_dir.exists():
            logs = sorted(log_dir.glob("*.md"))
            if logs:
                log_lines = logs[-1].read_text(encoding="utf-8").splitlines()
                if log_lines:
                    lines.append("## Latest log")
                    lines.extend(log_lines[-5:])
                    lines.append("")
        # Append open tasks and handoffs
        lines.extend(self._tasks_and_handoffs_lines(project_dir))
        return "\n".join(lines[:max_lines]).strip()

    def _tasks_and_handoffs_lines(self, project_dir: Path) -> List[str]:
        """Context feed. Prefers canonical sources; legacy folders are labeled.

        Canonical tasks: README.md ## Tasks via gt_task.py (the only model
        gt_task.py/gt_tasks.py/TASKS.md can see). Canonical handoffs:
        handoff/*.md via gt_handoff_status.py. Legacy Tasks/*.md and
        Handoffs/*.md are shown as 'legacy' so sessions know to migrate them.
        """
        lines: List[str] = []
        vault = self.vault.root

        # Canonical tasks
        try:
            tasks = canonical.list_tasks(vault, project_dir.name)
            if tasks:
                lines.append("## Open tasks (gt_task.py)")
                for t in tasks[:8]:
                    due = f" [due {t['due']}]" if t.get("due") else ""
                    ref = f" [ref {t['ref']}]" if t.get("ref") else ""
                    lines.append(f"- ({t.get('id')}) {t.get('text')}{due}{ref}")
                lines.append("")
        except canonical.CanonicalToolError:
            pass

        # Canonical handoffs
        try:
            handoffs = canonical.list_handoffs(vault, project_dir.name)
            if handoffs:
                lines.append("## Handoffs (gt_handoff_status.py)")
                for h in handoffs[-5:]:
                    note = f" — {h['note']}" if h.get("note") else ""
                    lines.append(f"- {h.get('path')} [{h.get('status')}]"
                                 f" open_items={h.get('open_items')}{note}")
                lines.append("")
        except canonical.CanonicalToolError:
            pass

        # Legacy fallback, clearly labeled
        tasks_dir = project_dir / "Tasks"
        if tasks_dir.exists():
            tasks = sorted(tasks_dir.glob("*.md"))
            open_tasks = [t for t in tasks if "[x]" not in t.read_text(encoding="utf-8")[:200]]
            if open_tasks:
                lines.append("## Open tasks (LEGACY Tasks/ folder — migrate)")
                for t in open_tasks:
                    lines.append(f"- {t.stem}")
                lines.append("")
        handoffs_dir = project_dir / "Handoffs"
        if handoffs_dir.exists():
            handoffs = sorted(handoffs_dir.glob("*.md"))
            if handoffs:
                lines.append("## Handoffs (LEGACY Handoffs/ folder — migrate)")
                for h in handoffs[-5:]:
                    lines.append(f"- {h.stem}")
                lines.append("")
        return lines

    # -----------------------------------------------------------------------
    # Command handlers
    # -----------------------------------------------------------------------

    async def close_command(self, config: Any) -> Dict[str, Any]:
        slug = self.active_slug
        if slug:
            # Write a tiny session closeout note.
            self.log(f"Session closed for {slug}")
        config.active_project = ""
        config.save()
        self.active_slug = ""
        return {"status": "closed", "was_active": slug}

    async def route_command(self, session_so_far: str) -> Dict[str, Any]:
        self._require_active()
        return {
            "status": "ok",
            "active_project": self.active_slug,
            "suggestion": (
                f"Session so far: {session_so_far[:200]!r}. "
                f"Output belongs in project '{self.active_slug}'. "
                "Use gt_work to record findings and gt_decide for new decisions."
            ),
        }

    async def query_command(self, q: str, lineage: str = "") -> Dict[str, Any]:
        self.vault._drain_if_outstanding()
        # Route through the vault's own read tool (gt_keyword_recall.py), not a
        # naive substring scan: it honors index.md, stop-words, stemming, name
        # weighting, and locked folders, exactly as gt-query does.
        return await self.vault.keyword_recall(q, lineage=lineage, active_project=self.active_slug)

    async def handle_command(
        self,
        kind: str,
        item_id: str = "",
        action: str = "",
        reason: str = "",
        until: str = "",
    ) -> Dict[str, Any]:
        self._require_active()
        vault = self.vault.root
        project_dir = self.vault.project_path(self.active_slug)

        # ---------------- Canonical tasks (gt_task.py) ----------------
        if kind == "tasks":
            if not item_id:
                try:
                    items = canonical.list_tasks(vault, self.active_slug)
                except canonical.CanonicalToolError as exc:
                    return {"status": "error", "detail": str(exc)}
                if not items:
                    folder = project_dir / "Tasks"
                    legacy = sorted(folder.glob("*.md")) if folder.exists() else []
                    open_legacy = [t for t in legacy
                                   if "[x]" not in t.read_text(encoding="utf-8")[:200]]
                    return {
                        "status": "ok", "kind": "tasks",
                        "source": "legacy-fallback" if open_legacy else "gt_task.py",
                        "count": len(open_legacy),
                        "items": [
                            {"id": t.stem, "source": "legacy-note",
                             "note": "one-note-per-task in Tasks/ is invisible to "
                                     "gt_task.py; migrate with "
                                     "migration.migrate_project(vault, '<slug>')"}
                            for t in open_legacy
                        ],
                    }
                return {
                    "status": "ok", "kind": "tasks", "source": "gt_task.py",
                    "count": len(items),
                    "items": [
                        {"id": t.get("id"), "text": t.get("text"),
                         "p": t.get("p"), "waiting": t.get("waiting"),
                         "ref": t.get("ref"), "due": t.get("due")}
                        for t in items
                    ],
                    "note": "ids are canonical (slug:LINE:HASH); act with "
                            "action=done|drop|defer|shelve (defer needs until=YYYY-MM-DD)",
                }
            # Resolve the id against the canonical list first.
            try:
                tasks = canonical.list_tasks(vault, self.active_slug)
            except canonical.CanonicalToolError as exc:
                return {"status": "error", "detail": str(exc)}
            match = next(
                (t for t in tasks
                 if t.get("id") == item_id
                 or (canonical.is_canonical_task_id(item_id) and str(t.get("id")).endswith(item_id))),
                None)
            if match:
                if not action:
                    return {"status": "ok", "item": match.get("id"),
                            "text": match.get("text"), "ref": match.get("ref"),
                            "state": match.get("waiting"),
                            "note": "add action=done|drop|defer|shelve"}
                try:
                    return canonical.task_action(vault, action, match["id"],
                                                 reason=reason, until=until)
                except canonical.CanonicalToolError as any_exc:
                    return {"status": "error", "detail": str(any_exc)}
            # Non-canonical id: legacy Tasks/ folder fallback.
            folder = project_dir / "Tasks"
            self.vault.ensure_dir(folder)
            target = folder / f"{item_id}.md"
            if not target.exists():
                return {"status": "error",
                        "detail": (f"task '{item_id}' not found in gt_task.py list "
                                   f"or {folder}")}
            text = target.read_text(encoding="utf-8")
            if action == "close":
                new_text = text.replace("- [ ]", "- [x]", 1) + \
                    f"\n\nClosed: {datetime.now(timezone.utc).isoformat()}\n"
                self.vault._write(f"Projects/{self.active_slug}/Tasks/{item_id}.md",
                                  new_text, op="replace-file")
                return {"status": "closed", "item": item_id, "source": "legacy"}
            if action == "drop":
                if not reason:
                    return {"status": "error", "detail": "reason required to drop item"}
                self.vault._write(f"Projects/{self.active_slug}/Tasks/{item_id}.md",
                                  text + f"\n\nDropped: {datetime.now(timezone.utc).isoformat()} — {reason}\n",
                                  op="replace-file")
                return {"status": "dropped", "item": item_id, "reason": reason,
                        "source": "legacy"}
            return {"status": "ok", "item": item_id, "action": action or "view",
                    "preview": text[:500], "source": "legacy",
                    "note": "this is a legacy Tasks/ note — migrate with "
                            "migration.migrate_project(vault, '<slug>')"}

        # ---------------- Canonical handoffs (gt_handoff_status.py) ----------------
        if kind == "handoffs":
            if not item_id:
                try:
                    items = canonical.list_handoffs(vault, self.active_slug)
                except canonical.CanonicalToolError as exc:
                    return {"status": "error", "detail": str(exc)}
                if not items:
                    folder = project_dir / "Handoffs"
                    legacy = sorted(folder.glob("*.md")) if folder.exists() else []
                    return {
                        "status": "ok", "kind": "handoffs",
                        "source": "legacy-fallback" if legacy else "gt_handoff_status.py",
                        "count": len(legacy),
                        "items": [
                            {"path": str(h.relative_to(vault)), "source": "legacy-note",
                             "note": "uppercase Handoffs/ is invisible to "
                                     "gt_handoff_status.py; migrate with "
                                     "migration.migrate_project(vault, '<slug>')"}
                            for h in legacy
                        ],
                    }
                return {
                    "status": "ok", "kind": "handoffs", "source": "gt_handoff_status.py",
                    "count": len(items),
                    "items": [
                        {"path": i.get("path"), "status": i.get("status"),
                         "recorded": i.get("recorded"),
                         "open_items": i.get("open_items"), "note": i.get("note")}
                        for i in items
                    ],
                }
            # Item ops: normalize the id, then dispatch on action.
            if not canonical.is_canonical_handoff_path(item_id):
                item_id = canonical.to_handoff_rel(item_id)
            try:
                if action in ("handled", "deferred", "open", "mark"):
                    status = "handled" if action in ("mark", "handled") else action
                    return canonical.handoff_mark(vault, item_id, status,
                                                  until=until, reason=reason)
                if action == "resolve":
                    if ":" in reason:
                        as_, res_reason = reason.split(":", 1)
                        res_reason = res_reason.strip()
                    else:
                        as_, res_reason = "closed", reason.strip()
                    as_ = {"closed": "closed", "dropped": "dropped", "task": "task"}.get(
                        as_.strip(), "closed")
                    return canonical.handoff_resolve(vault, item_id, item=item_id.split("/")[-1],
                                                     as_=as_, reason=res_reason)
                if action == "close":
                    return canonical.handoff_mark(vault, item_id, "handled",
                                                  reason=reason or "closed via gt_handle")
                if action in ("", "view"):
                    return {"status": "ok", "handoff": item_id,
                            "items": canonical.handoff_items(vault, item_id)}
                return {"status": "error",
                        "detail": f"unknown action '{action}' (handled|deferred|open|resolve|close|view)"}
            except canonical.CanonicalToolError as exc:
                return {"status": "error", "detail": str(exc)}

        return {"status": "error", "detail": f"unknown kind '{kind}' (tasks|handoffs)"}

    async def complete_command(
        self,
        kind: str,
        item_id: str = "",
        move: bool = False,
    ) -> Dict[str, Any]:
        self._require_active()
        vault = self.vault.root
        if kind == "project":
            # Mark project as complete in a handoff note.
            self.log(f"Project marked complete (move={move})")
            return {"status": "complete", "project": self.active_slug, "move": move}
        if kind == "task":
            # Canonical task completion via gt_task.py done.
            try:
                tasks = canonical.list_tasks(vault, self.active_slug)
            except canonical.CanonicalToolError as exc:
                return {"status": "error", "detail": str(exc)}
            match = next(
                (t for t in tasks
                 if t.get("id") == item_id
                 or (canonical.is_canonical_task_id(item_id)
                     and str(t.get("id")).endswith(item_id))),
                None)
            if match:
                try:
                    return canonical.task_action(vault, "done", match["id"])
                except canonical.CanonicalToolError as exc:
                    return {"status": "error", "detail": str(exc)}
            # Legacy note fallback.
            folder = self.vault.project_path(self.active_slug) / "Tasks"
            target = folder / f"{item_id}.md"
            if not target.exists():
                return {"status": "error", "detail": f"task '{item_id}' not found"}
            text = target.read_text(encoding="utf-8")
            new_text = text.replace("- [ ]", "- [x]", 1) + \
                f"\n\nCompleted: {datetime.now(timezone.utc).isoformat()}\n"
            self.vault._write(f"Projects/{self.active_slug}/Tasks/{item_id}.md",
                              new_text, op="replace-file")
            return {"status": "complete", "kind": "task", "item": item_id,
                    "source": "legacy"}
        if kind == "handoff":
            # Canonical handoff completion = mark handled; refuses if items open.
            if not canonical.is_canonical_handoff_path(item_id):
                item_id = canonical.to_handoff_rel(item_id)
            try:
                return canonical.handoff_mark(vault, item_id, "handled")
            except canonical.CanonicalToolError as exc:
                return {"status": "error", "detail": str(exc)}
        return {"status": "error",
                "detail": f"unknown kind '{kind}' (project|task|handoff)"}

    async def review_command(self) -> Dict[str, Any]:
        self.vault._drain_if_outstanding()
        inbox = self.vault.inbox_path()
        if not inbox.exists():
            return {"status": "ok", "inbox_items": 0, "note": "No INBOX.md found"}
        lines = inbox.read_text(encoding="utf-8").splitlines()
        items = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        # Move reviewed items into a daily note.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily = self.vault.root / "Daily" / f"{today}.md"
        self.vault.ensure_dir(daily.parent)
        if items:
            append_body = f"\n## Review {today}\n" + "".join(f"- {it}\n" for it in items)
            self.vault._write(f"Daily/{today}.md", append_body, op="append")
            self.vault._write("Inbox.md", "# INBOX\n\n", op="replace-file")
        return {"status": "reviewed", "inbox_items": len(items), "daily": str(daily)}

    async def plan_command(self, requirement: str) -> Dict[str, Any]:
        self._require_active()
        project_dir = self.vault.project_path(self.active_slug)
        plans_dir = project_dir / ".gt" / "plans"
        self.vault.ensure_dir(plans_dir)
        plan_id = f"plan-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        plan_file = plans_dir / f"{plan_id}.md"
        body = (
            f"# Plan: {plan_id}\n\n"
            f"Requirement: {requirement}\n\n"
            "## Phases\n\n"
            "1. Understand current state and risks.\n"
            "2. Design the smallest change that satisfies the requirement.\n"
            "3. Write or update tests first.\n"
            "4. Implement the change.\n"
            "5. Run checks (scan, lint, tests).\n"
            "6. Record decisions and close handoffs.\n\n"
            "status: draft\n"
        )
        self.vault._write(f"Projects/{self.active_slug}/.gt/plans/{plan_id}.md", body, op="replace-file")
        return {"status": "draft", "plan_id": plan_id, "path": str(plan_file)}

    async def implement_command(self, plan_id: str, phase: str = "") -> Dict[str, Any]:
        self._require_active()
        plan_file = self.vault.project_path(self.active_slug) / ".gt" / "plans" / f"{plan_id}.md"
        if not plan_file.exists():
            return {"status": "error", "detail": f"Plan {plan_id} not found"}
        text = plan_file.read_text(encoding="utf-8")
        if "status: approved" not in text and "status: draft" in text:
            return {"status": "error", "detail": "Plan is still draft. Approve it before implementing."}
        # In Hermes, implementation is delegated to the agent itself; this tool
        # returns the plan phase so the model can act on it.
        return {
            "status": "implementing",
            "plan_id": plan_id,
            "phase": phase or "next",
            "note": (
                "Hermes does not run code autonomously. Use the returned phase to drive "
                "the next edits, tests, and gt_work/gt_decide calls."
            ),
        }

    async def brief_command(self, project: str = "") -> Dict[str, Any]:
        slug = project or self.active_slug
        if not slug:
            return {"status": "error", "detail": "No project specified or active"}
        ctx = self.read_context()
        return {"status": "ok", "project": slug, "brief": ctx[:2000]}

    async def minimize_command(self) -> Dict[str, Any]:
        self._require_active()
        project_dir = self.vault.project_path(self.active_slug)
        # Estimate vault bytes for this project.
        total = sum(
            f.stat().st_size for f in project_dir.rglob("*") if f.is_file()
        )
        return {
            "status": "ok",
            "project": self.active_slug,
            "project_bytes": total,
            "suggestion": (
                "Consider moving stale Memory notes to Knowledge or archiving old Log files."
            ),
        }
