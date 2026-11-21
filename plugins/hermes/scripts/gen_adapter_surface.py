#!/usr/bin/env python3
"""Generate docs/adapter-surface.json from the gt command registry.

Hermes adapter keeps a mechanical snapshot of its gt_* surface so upstream
(and other hosts) can diff what Hermes exposes after each upstream release.
Regenerate after any surface change and commit the result:

    python3 scripts/gen_adapter_surface.py

Run from plugins/hermes/ (any cwd works; paths are file-relative).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC = PLUGIN_ROOT / "src"
sys.path.insert(0, str(SRC))

from golden_thread_hermes.memory import commands as cmd_mod  # noqa: E402
from golden_thread_hermes.memory.provider import (  # noqa: E402
    GoldenThreadMemoryProvider,
)


def main() -> int:
    out = PLUGIN_ROOT / "docs" / "adapter-surface.json"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip() or None
    except OSError:
        commit = None
    write_gate = sorted(GoldenThreadMemoryProvider._WRITE_TO_ACTIVE_COMMANDS)
    doc = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": commit,
        "command_count": len(cmd_mod.COMMANDS),
        "write_to_active_commands": write_gate,
        "commands": [
            {
                "name": c.name,
                "description": c.description,
                "parameters": c.parameters,
                "required": c.required,
                "writes_to_active": c.name in write_gate,
            }
            for c in cmd_mod.COMMANDS
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({doc['command_count']} commands)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
