"""Filesystem abstraction for a Golden Thread vault."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List


class Vault:
    """Read/write a Golden Thread vault on local disk."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()

    def core_path(self) -> Path:
        return self.root / "Core.md"

    def inbox_path(self) -> Path:
        return self.root / "Inbox.md"

    def memory_dir(self) -> Path:
        return self.root / "Memory"

    def projects_dir(self) -> Path:
        return self.root / "Projects"

    def read_core(self, max_lines: int = 200) -> str:
        path = self.core_path()
        if not path.exists():
            return ""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            return "\n".join(lines[:max_lines])
        except Exception:
            return ""

    def list_projects(self) -> List[str]:
        projects = self.projects_dir()
        if not projects.exists():
            return []
        return sorted([p.name for p in projects.iterdir() if p.is_dir() and not p.name.startswith(".")])

    def project_path(self, slug: str) -> Path:
        return self.projects_dir() / slug

    def ensure_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def slugify(self, name: str) -> str:
        slug = re.sub(r"[^\w\s-]", "", name).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug or "project"
