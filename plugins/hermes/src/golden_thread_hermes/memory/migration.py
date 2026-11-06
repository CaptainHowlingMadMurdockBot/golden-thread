"""One-shot migration: legacy Tasks/ + Handoffs/ items -> canonical locations.

Legacy (the bug): one-note-per-item directories `Tasks/` and `Handoffs/`
that no canonical Golden Thread tool reads.
Canonical:
- tasks = checkbox lines in the project's README.md `## Tasks`, added via
  gt_task.py, with the legacy note kept as a detail page referenced by --ref.
- handoffs = `handoff/<name>.md` with frontmatter (type: handoff,
  status: ..., handoff_id: H-...), tracked by gt_handoff_status.py.

Legacy files are moved (not deleted) to `<project>/Attic/legacy-handoffs/`
so history survives and the wrong-location copies stop misleading tools
and sessions.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .canonical import (HOOKS_DIR, TOOLS_REL, find_tool, handoff_mark,
                        list_tasks, run_tool, task_action)
from .vault import Vault

ATTIC = "Attic/legacy-handoffs"
OWNER = (100000, 100000)  # ADR-0003: vault files must be owned by host UID/GID 100000

FM_KEYS = ("type", "project", "status", "handoff_id", "created")

STATUS_WORD = re.compile(r"\b(NOT STARTED|IN PROGRESS|COMPLETE|DONE|RESOLVED)\b", re.I)


def _fix_owner(paths: List[Path]) -> None:
    """Host-side writes land as 0:0; the livesync bridge (LXC 100000) must own them."""
    import os
    for p in paths:
        try:
            os.chown(p, *OWNER)
        except OSError:
            pass  # not the owner-perms host; leave as-is


def _extract_status(text: str) -> str:
    m = re.search(r"\*\*Status:\*\*\s*(.+)", text)
    line = m.group(1) if m else ""
    hit = STATUS_WORD.search(line)
    if hit:
        w = hit.group(1).lower()
        return "handled" if w in ("complete", "done", "resolved") else "open"
    return "open"


def to_handoff_rel(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-").lower()
    return f"handoff/{name}.md"


def seed_readme(vault: Path, project: str) -> str:
    """Create the canonical README.md skeleton if the project has none.

    gt_task.py refuses new tasks for projects without Projects/<slug>/README.md
    (its add scans those for `## Tasks`), so migration must seed it first.
    Returns the slug seeded, or "" if a README already existed.
    """
    vault = Path(vault)
    readme = vault / "Projects" / project / "README.md"
    if readme.exists():
        return ""
    fm = "\n".join([
        "type: project",
        f"slug: {project}",
        "domain: TODO",
        "stage: active",
        "tags: []",
    ])
    body = "\n".join([
        f"# {project}",
        "",
        "> <!-- one-line vision -->",
        "",
        "## Tasks",
        "",
        "<!-- Format: - [ ] text [p:: 1|2|3] [waiting:: user|agent|external|parked] ",
        "[since:: YYYY-MM-DD] -- rolled up into /TASKS.md; do not edit that file by hand. -->",
        "",
    ])
    note = f"---\n{fm}\n---\n\n{body}"
    v = Vault(vault)
    v._write(str(readme.relative_to(vault)).replace("\\", "/"), note, op="replace-file")
    _fix_owner([readme])
    return project


def build_handoff_note(text: str, project: str, created: str) -> str:
    """Wrap a legacy note's body in the canonical frontmatter contract."""
    status = _extract_status(text)
    hid = "H-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4]
    lines = [l for l in text.splitlines() if not l.startswith("Closed: ")
             and not l.startswith("Completed: ")]
    body = "\n".join(lines).strip()
    fm = "\n".join([
        f"type: handoff",
        f"project: {project}",
        f"status: {status}",
        f"handoff_id: {hid}",
        f"created: {created}",
    ])
    return f"---\n{fm}\n---\n\n{body}\n"


def migrate_handoff(vault: Path, project: str, legacy_file: Path,
                    mark: bool = True) -> Dict[str, Any]:
    """Move one legacy handoff note into handoff/ with canonical frontmatter."""
    text = legacy_file.read_text(encoding="utf-8")
    rel = f"Projects/{project}/{to_handoff_rel(legacy_file.name)}"
    created = legacy_file.name[:10] if re.match(r"\d{4}-\d{2}-\d{2}", legacy_file.name) \
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note = build_handoff_note(text, project, created)

    attic = legacy_file.parent.parent / ATTIC
    attic.mkdir(parents=True, exist_ok=True)
    legacy_file.rename(attic / legacy_file.name)
    _fix_owner([attic / legacy_file.name])

    vault = Path(vault)
    v = Vault(vault)
    v._write(rel, note, op="replace-file")
    written = vault / rel
    _fix_owner([written])

    result: Dict[str, Any] = {"migrated": str(legacy_file.name), "to": rel,
                              "status": _extract_status(text)}
    if mark:
        try:
            handoff_mark(vault, rel, _extract_status(text))
            result["marked"] = True
        except Exception as exc:  # noqa: BLE001 - report, don't abort siblings
            result["marked"] = False
            result["mark_error"] = str(exc)[:200]
    return result


def migrate_task(vault: Path, project: str, legacy_file: Path,
                 priority: int = 1, waiting: str = "agent") -> Dict[str, Any]:
    """Keep the note as a detail page; add the canonical README task citing it."""
    vault = Path(vault)
    text = legacy_file.read_text(encoding="utf-8")
    m = re.search(r"^#\s+Task:\s*(.+)$", text, re.M)
    title = (m.group(1) if m else legacy_file.stem).strip()

    status = _extract_status(text)
    if status == "handled":
        return {"migrated": legacy_file.name, "skipped": "already handled"}

    # Move first so the canonical ref cites the archive's final location.
    attic = legacy_file.parent.parent / "Attic" / "legacy-tasks"
    attic.mkdir(parents=True, exist_ok=True)
    legacy_file.rename(attic / legacy_file.name)
    ref_new = str((vault / f"Projects/{project}/Attic/legacy-tasks/{legacy_file.name}").relative_to(vault)).replace("\\", "/")
    _fix_owner([attic / legacy_file.name])

    args = ["add", title, "--project", project, "--ref", ref_new,
            "--p", str(priority), "--waiting", waiting]
    run_tool(vault, "gt_task.py", args, want_json=False)
    return {"migrated": legacy_file.name, "task_added": title, "ref": ref_new, "status": status}


def migrate_project(vault: Path, project: str) -> Dict[str, Any]:
    """Migrate one project's legacy Tasks/ + Handoffs/ items. Idempotent."""
    vault = Path(vault)
    v = Vault(vault)
    v._drain_if_outstanding()
    pdir = Path(vault) / "Projects" / project
    out: Dict[str, Any] = {"project": project, "handoffs": [], "tasks": []}

    handoffs_dir = pdir / "Handoffs"
    if handoffs_dir.is_dir():
        for f in sorted(handoffs_dir.glob("*.md")):
            out["handoffs"].append(migrate_handoff(vault, project, f))

    tasks_dir = pdir / "Tasks"
    if tasks_dir.is_dir():
        seed_readme(vault, project)  # gt_task.py add requires README.md ## Tasks
        for f in sorted(tasks_dir.glob("*.md")):
            out["tasks"].append(migrate_task(vault, project, f))

    # Drain the queue so canonical writes land, then fix ownership on everything touched.
    v._drain()
    pdir_rel = vault / "Projects" / project
    touched = [p for p in [
        vault / f"Projects/{project}/README.md",
        vault / "Projects/golden-thread/TASKS.md",
        pdir_rel / "handoff",
        pdir_rel / "Attic",
    ] if p.exists()]
    all_files: List[Path] = []
    for p in touched:
        if p.is_dir():
            all_files.extend(q for q in p.rglob("*") if q.is_file())
        else:
            all_files.append(p)
    _fix_owner(all_files)
    out["fixed_owner"] = [str(p) for p in all_files]
    return out


def migrate_all(vault: Path, projects: List[str]) -> List[Dict[str, Any]]:
    return [migrate_project(vault, p) for p in projects]