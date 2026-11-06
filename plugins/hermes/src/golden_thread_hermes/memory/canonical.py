"""Bridge to Golden Thread's canonical task + handoff tools.

Canonical model (Golden Thread >= 0.17.2):
- Tasks are checkbox lines in each project's README.md under "## Tasks",
  managed by gt_task.py (ids like personal:29:9d7275; lifecycle done/drop/
  defer/shelve; aggregated into a ranked TASKS.md by gt_tasks.py).
- Handoffs are frontmatter-marked notes in Projects/<slug>/handoff/
  (lowercase, singular), tracked by gt_handoff_status.py with a status
  vocabulary (open/deferred/handled/closed/history) and per-item
  resolutions that gt_close refuses to bypass.

The Hermes adapter must never invent a parallel convention again: this
module shells out to the vault's own tool copies and parses their --json
output, so the two systems cannot drift.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HOOKS_DIR = Path("/root/.claude/golden-thread/hooks")
TOOLS_REL = Path("Projects") / "golden-thread" / "tools"
TASK_TOOL = "gt_task.py"
HANDOFF_TOOL = "gt_handoff_status.py"

TASK_ACTIONS = {"done": "done", "complete": "done", "close": "done",
                "drop": "drop", "defer": "defer", "shelve": "shelve"}


class CanonicalToolError(RuntimeError):
    """A canonical tool was missing or failed; carries its stderr."""


def _tool_candidates(vault: Path, name: str) -> List[Path]:
    vault = Path(vault)
    return [vault / TOOLS_REL / name, HOOKS_DIR / name]


def find_tool(vault: Path, name: str) -> Optional[Path]:
    """Version-matched copy inside the vault first, hooks dir second."""
    for cand in _tool_candidates(vault, name):
        if cand.is_file():
            return cand
    return None


def run_tool(vault: Path, tool: str, args: List[str], timeout: int = 180,
             want_json: bool = True) -> Any:
    """Run a canonical tool against the vault. Returns parsed --json or raw stdout."""
    script = find_tool(vault, tool)
    if script is None:
        raise CanonicalToolError(
            f"{tool} not found for this vault (looked in {TOOLS_REL} and {HOOKS_DIR})")
    cmd = [sys.executable, str(script), *args, "--vault", str(Path(vault))]
    if want_json:
        cmd.append("--json")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CanonicalToolError(f"{tool} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise CanonicalToolError(
            f"{tool} {' '.join(args[:2])} failed ({proc.returncode}): {detail}")
    if not want_json:
        return proc.stdout
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CanonicalToolError(
            f"{tool} returned non-JSON output: {proc.stdout[:300]}") from exc


# ---------------------------------------------------------------------------
# Tasks (README.md ## Tasks checkbox lines, via gt_task.py)
# ---------------------------------------------------------------------------

def list_tasks(vault: Path, project: Optional[str] = None) -> List[Dict[str, Any]]:
    data = run_tool(vault, TASK_TOOL, ["list"])
    items = data if isinstance(data, list) else data.get("tasks", [])
    if project:
        items = [t for t in items if t.get("project") == project]
    return items


def task_action(vault: Path, action: str, task_id: str, reason: str = "",
                until: str = "") -> Dict[str, Any]:
    """done/drop/defer/shelve a task by its canonical id (slug:LINE:HASH)."""
    sub = TASK_ACTIONS.get(action)
    if sub is None:
        raise CanonicalToolError(
            f"unknown task action {action!r} (expected one of {sorted(TASK_ACTIONS)})")
    args = [sub, task_id]
    if sub == "done":
        if reason:
            args += ["--reason", reason]
    else:
        args += ["--reason", reason or "no reason recorded"]
    if sub == "defer":
        if not until:
            raise CanonicalToolError("defer requires an until date (YYYY-MM-DD)")
        args += ["--until", until]
    run_tool(vault, TASK_TOOL, args, want_json=False)
    return {"status": "ok", "action": sub, "task": task_id}


# ---------------------------------------------------------------------------
# Handoffs (Projects/<slug>/handoff/*.md frontmatter notes, via gt_handoff_status.py)
# ---------------------------------------------------------------------------

def list_handoffs(vault: Path, project: Optional[str] = None) -> List[Dict[str, Any]]:
    data = run_tool(vault, HANDOFF_TOOL, ["list"])
    items = data if isinstance(data, list) else data.get("handoffs", [])
    if project:
        items = [h for h in items if h.get("project") == project]
    return items


def handoff_items(vault: Path, file_rel: str) -> List[Dict[str, Any]]:
    """A handoff's open items and how each was resolved."""
    data = run_tool(vault, HANDOFF_TOOL, ["items", file_rel])
    return data if isinstance(data, list) else data.get("items", [])


def handoff_resolve(vault: Path, file_rel: str, item: str = "", as_: str = "closed",
                    reason: str = "", task_id: str = "") -> Dict[str, Any]:
    """Record how an open item was dealt with (closed / dropped / folded into a task).

    item = the item's block id (h1, h2...) or its text. For as_='task', pass
    task_id to link an existing gt_task; otherwise the tool creates one.
    """
    args = ["resolve", file_rel, "--as", as_]
    if item:
        args += ["--item", item]
    if as_ == "task" and task_id:
        args += ["--task", task_id]
    if reason:
        args += ["--reason", reason]
    run_tool(vault, HANDOFF_TOOL, args, want_json=False)
    return {"status": "ok", "action": "resolve", "handoff": file_rel, "item": item}


def handoff_mark(vault: Path, file_rel: str, status: str, until: str = "",
                 reason: str = "") -> Dict[str, Any]:
    """Set a handoff's status: handled | deferred | open.

    A handoff is 'closed' by marking it handled (upstream gt_close writes the
    retire event only after every open item has a settled resolution).
    """
    if status not in ("handled", "deferred", "open"):
        raise CanonicalToolError(f"unsupported handoff status {status!r}")
    args = ["mark", file_rel, "--status", status]
    if status == "deferred":
        if not until:
            raise CanonicalToolError("deferred requires an until date (YYYY-MM-DD)")
        args += ["--until", until]
    if reason:
        args += ["--reason", reason]
    run_tool(vault, HANDOFF_TOOL, args, want_json=False)
    return {"status": "ok", "action": "mark", "handoff": file_rel, "mark": status}


def is_canonical_task_id(item_id: str) -> bool:
    """True if item_id looks like gt_task.py output, e.g. personal:29:9d7275."""
    parts = item_id.split(":")
    return len(parts) == 3 and parts[1].isdigit() and len(parts[2]) == 6


def is_canonical_handoff_path(p: str) -> bool:
    return str(p).replace(chr(92), "/").startswith("handoff/") and str(p).endswith(".md")


def to_handoff_rel(p: str) -> str:
    """Normalize user input to a canonical handoff rel path.

    'vault-permissions-audit' -> 'handoff/vault-permissions-audit.md'
    """
    s = str(p).strip()
    if s.lower().endswith(".md"):
        s = s[:-3]
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    return f"handoff/{s}.md"


HANDOFF_TOOL = "gt_handoff_status.py"
TASK_TOOL = "gt_task.py"