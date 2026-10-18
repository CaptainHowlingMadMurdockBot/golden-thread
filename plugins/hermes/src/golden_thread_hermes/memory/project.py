"""Project-level operations inside a Golden Thread vault."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .vault import Vault


class ProjectManager:
    """CRUD + logging for a Golden Thread project."""

    def __init__(self, vault: Vault, active_slug: str = "") -> None:
        self.vault = vault
        self.active_slug = active_slug.strip()

    def open(self, slug: str) -> None:
        slug = slug.strip()
        project_dir = self.vault.project_path(slug)
        if not project_dir.exists():
            raise FileNotFoundError(f"Project '{slug}' does not exist")
        self.active_slug = slug

    def create(self, name: str, idea: str) -> str:
        slug = self.vault.slugify(name)
        project_dir = self.vault.project_path(slug)
        if project_dir.exists():
            # Append a counter to avoid collisions.
            base = slug
            n = 2
            while project_dir.exists():
                slug = f"{base}-{n}"
                project_dir = self.vault.project_path(slug)
                n += 1
        self.vault.ensure_dir(project_dir)
        self.vault.ensure_dir(project_dir / "Decisions")
        self.vault.ensure_dir(project_dir / "Log")
        (project_dir / "idea.md").write_text(
            f"# {name}\n\n{idea}\n", encoding="utf-8"
        )
        (project_dir / "CONVENTIONS.md").write_text(
            f"# CONVENTIONS\n\nProject-specific conventions for {name}.\n",
            encoding="utf-8",
        )
        self.active_slug = slug
        return slug

    def log(self, summary: str) -> None:
        if not self.active_slug:
            raise RuntimeError("No active project. Call gt_open first.")
        log_dir = self.vault.project_path(self.active_slug) / "Log"
        self.vault.ensure_dir(log_dir)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"- {ts}: {summary}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def decide(
        self,
        title: str,
        context: str,
        decision: str,
        consequences: str,
    ) -> str:
        if not self.active_slug:
            raise RuntimeError("No active project. Call gt_open first.")
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
        (decisions_dir / file_name).write_text(body, encoding="utf-8")
        return adr_id

    def add_memory(self, content: str, category: str = "general") -> None:
        if not self.active_slug:
            raise RuntimeError("No active project. Call gt_open first.")
        mem_dir = self.vault.project_path(self.active_slug) / "Memory"
        self.vault.ensure_dir(mem_dir)
        file_path = mem_dir / f"{category}.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"- {ts}: {content}\n")

    def read_context(self, max_lines: int = 400) -> str:
        if not self.active_slug:
            return ""
        project_dir = self.vault.project_path(self.active_slug)
        candidates = [
            project_dir / "idea.md",
            project_dir / "CONVENTIONS.md",
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
        return "\n".join(lines[:max_lines]).strip()
