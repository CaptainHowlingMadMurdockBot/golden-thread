"""Golden Thread memory provider implementation.

Implements the Hermes MemoryProvider ABC for filesystem-backed Golden Thread vaults.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from hermes_cli.config import cfg_get
from tools.registry import tool_error, tool_result

from .config import GoldenThreadConfig
from .vault import Vault
from .project import ProjectManager

logger = logging.getLogger(__name__)


class GoldenThreadMemoryProvider(MemoryProvider):
    """Hermes memory provider backed by a Golden Thread Obsidian vault."""

    def __init__(self) -> None:
        self._config: Optional[GoldenThreadConfig] = None
        self._vault: Optional[Vault] = None
        self._project: Optional[ProjectManager] = None
        self._session_id: str = ""

    # -----------------------------------------------------------------------
    # MemoryProvider contract
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "golden-thread"

    def is_available(self) -> bool:
        try:
            cfg = GoldenThreadConfig.load()
        except Exception:
            return False
        return cfg.vault_path.exists() and cfg.vault_path.is_dir()

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._config = GoldenThreadConfig.load()
        self._vault = Vault(self._config.vault_path)
        self._project = ProjectManager(self._vault, self._config.active_project)
        if self._config.active_project:
            try:
                self._project.open(self._config.active_project)
            except Exception as e:
                logger.warning("Failed to open active project %r: %s", self._config.active_project, e)

    def shutdown(self) -> None:
        self._project = None
        self._vault = None

    def system_prompt_block(self) -> str:
        if not self._vault or not self._config:
            return ""
        core = self._vault.read_core(self._config.core_rules_max_lines)
        project_intro = ""
        if self._project and self._project.active_slug:
            project_intro = f"\nActive Golden Thread project: {self._project.active_slug}\n"
        return (
            "\n# Golden Thread\n"
            f"{project_intro}"
            "You have access to Golden Thread memory tools: gt_open, gt_list, gt_create, gt_work, gt_decide.\n"
            "Use them to keep project state, decisions, and logs in the Golden Thread vault.\n\n"
            "## Core rules\n"
            f"{core}\n"
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._vault or not self._project or not self._project.active_slug:
            return ""
        try:
            ctx = self._project.read_context(self._config.project_context_max_lines)
            return f"## Golden Thread context ({self._project.active_slug})\n{ctx}\n" if ctx else ""
        except Exception as e:
            logger.debug("Golden Thread prefetch failed: %s", e)
            return ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        # Golden Thread is write-via-tool; do not auto-dump turns.
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "gt_open",
                "description": "Activate a Golden Thread project by slug. Future gt_work / gt_decide calls write to it.",
                "parameters": {
                    "type": "object",
                    "properties": {"project_slug": {"type": "string", "description": "Slug of the project folder under Projects/."}},
                    "required": ["project_slug"],
                },
            },
            {
                "name": "gt_list",
                "description": "List available Golden Thread project slugs in the vault.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "gt_create",
                "description": "Create a new Golden Thread project with an idea note.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Human-readable project name."},
                        "idea": {"type": "string", "description": "One-paragraph project idea."},
                    },
                    "required": ["name", "idea"],
                },
            },
            {
                "name": "gt_work",
                "description": "Append a timestamped progress entry to the active project's log.",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string", "description": "What was done or learned."}},
                    "required": ["summary"],
                },
            },
            {
                "name": "gt_decide",
                "description": "Write an Architecture Decision Record (ADR) for the active project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "context": {"type": "string"},
                        "decision": {"type": "string"},
                        "consequences": {"type": "string"},
                    },
                    "required": ["title", "context", "decision", "consequences"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._project or not self._vault:
            return tool_error("Golden Thread provider is not initialized")
        try:
            if tool_name == "gt_open":
                slug = str(args.get("project_slug", "")).strip()
                self._project.open(slug)
                # Persist active project in config so it survives restarts.
                cfg = GoldenThreadConfig.load()
                cfg.active_project = slug
                cfg.save()
                self._config = cfg
                return tool_result({"active_project": slug})
            if tool_name == "gt_list":
                slugs = self._vault.list_projects()
                return tool_result({"projects": slugs})
            if tool_name == "gt_create":
                name = str(args.get("name", "")).strip()
                idea = str(args.get("idea", "")).strip()
                slug = self._project.create(name, idea)
                self._project.open(slug)
                return tool_result({"project": slug})
            if tool_name == "gt_work":
                summary = str(args.get("summary", "")).strip()
                self._project.log(summary)
                return tool_result({"logged": True})
            if tool_name == "gt_decide":
                title = str(args.get("title", "")).strip()
                context = str(args.get("context", "")).strip()
                decision = str(args.get("decision", "")).strip()
                consequences = str(args.get("consequences", "")).strip()
                adr_id = self._project.decide(title, context, decision, consequences)
                return tool_result({"adr": adr_id})
        except Exception as e:
            logger.warning("Golden Thread tool %s failed: %s", tool_name, e)
            return tool_error(str(e))
        return tool_error(f"Unknown Golden Thread tool: {tool_name}")

    def backup_paths(self) -> List[str]:
        try:
            cfg = GoldenThreadConfig.load()
            if cfg.vault_path.exists():
                return [str(cfg.vault_path)]
        except Exception:
            pass
        return []

    # -----------------------------------------------------------------------
    # Optional hooks
    # -----------------------------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        pass

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = new_session_id

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Mirror built-in memory writes to the active project's Memory folder.
        if not self._project or not self._project.active_slug:
            return
        try:
            if action == "add" and content.strip():
                self._project.add_memory(content)
        except Exception:
            pass
