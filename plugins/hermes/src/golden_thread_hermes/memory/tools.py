"""Golden Thread tools exposed to Hermes.

These mirror the spirit of the Claude Code plugin's gt-open / gt-work / gt-create
commands while using Hermes tool-call semantics.
"""

from __future__ import annotations

from typing import Any

ToolDefinition = dict[str, Any]

from .config import GoldenThreadConfig
from .project import ProjectManager
from .vault import Vault


TOOLS: list[ToolDefinition] = [
    {
        "name": "gt_open",
        "description": "Open a Golden Thread project and load its context into the session.",
        "parameters": {
            "type": "object",
            "required": ["project_slug"],
            "properties": {
                "project_slug": {
                    "type": "string",
                    "description": "Slug of the project to activate",
                },
            },
        },
    },
    {
        "name": "gt_list",
        "description": "List all Golden Thread projects in the vault.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "gt_create",
        "description": "Create a new Golden Thread project.",
        "parameters": {
            "type": "object",
            "required": ["name", "idea"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable project name",
                },
                "idea": {
                    "type": "string",
                    "description": "One-line project idea/purpose",
                },
            },
        },
    },
    {
        "name": "gt_work",
        "description": "Append a summary line to the active project's daily log.",
        "parameters": {
            "type": "object",
            "required": ["summary"],
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "What was done or decided in this work block",
                },
            },
        },
    },
    {
        "name": "gt_decide",
        "description": "Record an Architecture Decision Record for the active project.",
        "parameters": {
            "type": "object",
            "required": ["title", "context", "decision", "consequences"],
            "properties": {
                "title": {"type": "string"},
                "context": {"type": "string"},
                "decision": {"type": "string"},
                "consequences": {"type": "string"},
            },
        },
    },
]


async def dispatch(
    tool_name: str,
    arguments: dict[str, Any],
    vault: Vault,
    projects: ProjectManager,
    config: GoldenThreadConfig,
) -> dict:
    if tool_name == "gt_open":
        return await projects.open(arguments["project_slug"], config)

    if tool_name == "gt_list":
        return {"projects": await projects.list()}

    if tool_name == "gt_create":
        return await projects.create(arguments["name"], arguments["idea"], config)

    if tool_name == "gt_work":
        active = config.active_project
        if not active:
            return {"status": "error", "detail": "No active project. Run gt_open first."}
        await projects.append_log(active, arguments["summary"])
        return {"status": "logged", "project": active}

    if tool_name == "gt_decide":
        active = config.active_project
        if not active:
            return {"status": "error", "detail": "No active project. Run gt_open first."}
        return await projects.add_decision(
            active,
            arguments["title"],
            arguments["context"],
            arguments["decision"],
            arguments["consequences"],
        )

    return {"status": "error", "detail": f"Unknown Golden Thread tool: {tool_name}"}
