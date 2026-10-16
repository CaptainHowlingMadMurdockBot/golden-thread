#!/usr/bin/env python3
"""gt_compact.py -- ask the open session to compact itself once this turn ends (0.21.4).

    gt_compact.py request --handoff FILE [--session ID] [--cwd DIR] [--json]
    gt_compact.py status [--session ID] [--cwd DIR] [--json]

The command half of /gt:gt-minimize compact. A shell script cannot compact a running Claude Code;
the in-session mod (hooks/guard_mod.js) can. So the skill has the model capture this session's
findings and write the handoff first, then runs `request`, which drops a small request file the
mod reads when the turn ends. The mod cuts in place and puts
"Read the handoff at <FILE> and pick up where we left off." in the prompt box: one step for
the user.

ONE SESSION, NOT ONE FOLDER. The owner runs several sessions at once, often in the same folder, so
the request is named and matched by SESSION ID: request-<session>.json, with the id inside it too.
Only the mod of that session takes it (Claude Code 2.1.296+ tells a mod its id; a mod that cannot
learn it takes no request, so below 2.1.296 this does nothing and the skill says to type /compact). The id comes from --session, else $CLAUDE_CODE_SESSION_ID (set in
every Claude Code shell), $CLAUDE_SESSION_ID or $GT_SESSION_ID; with none, `request` refuses.

  request   refuses (exit 1) unless --handoff names the handoff: capture before cut, so a
            compaction can never be asked for ahead of its handoff. Accepted: an existing,
            non-empty file; or, under gt sandbox mode, a handoff still waiting in the queue inbox
            (~/.gt-inbox/queue/) -- either the inbox file itself or the vault path an inbox request
            will create. Otherwise writes ~/.claude/golden-thread/expiry-guard/mod/request-<id>.json,
            or, when that folder cannot be written (sandbox mode denies it),
            ~/.gt-scratch/compact/request-<id>.json, which the mod also reads. One line out.
  status    prints the auto_compact setting (off, or the percent) and whether a request is
            waiting for this session.

  --handoff FILE  the handoff the skill just wrote (an absolute path; the pickup line names it)
  --session ID    this session's id (default: the environment, above)
  --cwd DIR       the session's folder (default: the current directory); recorded, not matched
  --json          one JSON object instead of a line

auto_compact (gt_settings.py: off, or a percent 30-90) is the same cut done without asking, when
the context passes that percent; this script is not involved in that path.

Exit: 0 done | 1 refused (no handoff, no session id) | 2 usage. Never touches the vault.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "GT_SESSION_ID")


def mod_dir() -> Path:
    return Path.home() / ".claude" / "golden-thread" / "expiry-guard" / "mod"


def scratch_dir() -> Path:
    """Where the request goes when mod_dir() cannot be written (gt sandbox mode denies writes under
    ~/.claude/golden-thread; ~/.gt-scratch is the sandbox's writable place, as gt_spill uses it).
    guard_mod.js reads both."""
    return Path.home() / ".gt-scratch" / "compact"


def safe(sid: str) -> str:
    """The mod's safe(id), so both halves name the same file."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", sid or "")[:80]


def session_id(given: str = "") -> str:
    if given:
        return given
    for var in SESSION_ENV:
        v = os.environ.get(var, "").strip()
        if v:
            return v
    return ""


def request_paths(sid: str) -> list:
    name = "request-%s.json" % safe(sid)
    return [mod_dir() / name, scratch_dir() / name]


def setting() -> str:
    try:
        cfg = json.loads((Path.home() / ".claude" / "vault-config.json").read_text(encoding="utf-8"))
        v = str(cfg.get("auto_compact", "off")).strip().lower()
    except (OSError, ValueError, AttributeError):
        v = "off"
    return v if v.isdigit() and 30 <= int(v) <= 90 else "off"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(str(tmp), "wb") as f:                         # bytes: LF on every platform
        f.write(text.encode("utf-8"))
    os.replace(str(tmp), str(path))


def _nonempty_file(p: Path) -> bool:
    """is_file() raises PermissionError (not False) on a path the sandbox denies: that is "not
    visible here", never a crash."""
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def inbox_dir() -> Path:
    return Path.home() / ".gt-inbox" / "queue"


def _in_inbox(p: Path) -> bool:
    try:
        root = os.path.realpath(str(inbox_dir()))
        return os.path.commonpath([os.path.realpath(str(p)), root]) == root
    except (ValueError, OSError):
        return False


def queued_in_inbox(h: Path) -> bool:
    """True when a queue-inbox request (gt_write_queue.deposit_inbox) will create `h` with content:
    {"gt_inbox": 1, "vault": <vault>, "request": {"path": <rel>, "content": ...}}."""
    try:
        names = sorted(os.listdir(str(inbox_dir())))
    except OSError:
        return False
    want = os.path.realpath(str(h))
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            d = json.loads((inbox_dir() / n).read_text(encoding="utf-8"))
            req = d.get("request") or {}
            if not (d.get("vault") and req.get("path") and str(req.get("content") or "").strip()):
                continue
            if os.path.realpath(os.path.join(str(d["vault"]), str(req["path"]))) == want:
                return True
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return False


def handoff_state(h: Path | None) -> str:
    """-> "file" | "inbox" | "" (refused)."""
    if h is None or not h.is_absolute():
        return ""
    if _nonempty_file(h):
        return "inbox" if _in_inbox(h) else "file"
    return "inbox" if queued_in_inbox(h) else ""


def _out(a, ok: bool, msg: str, **extra) -> None:
    if a.json:
        print(json.dumps(dict({"ok": ok, ("why" if not ok else "msg"): msg}, **extra)))
    else:
        print(msg, file=sys.stdout if ok else sys.stderr)


def do_request(a) -> int:
    cwd = a.cwd or os.getcwd()
    h = Path(a.handoff) if a.handoff else None
    where = handoff_state(h)
    if not where:
        _out(a, False, "refused: no handoff written yet (%s); write it first, then ask for the compact"
             % (a.handoff or "none given"))
        return 1
    sid = session_id(a.session)
    if not sid:
        _out(a, False, "refused: this session's id is not known here (no --session, no "
             "CLAUDE_CODE_SESSION_ID), so no request can be matched to it; type /compact instead "
             "(the handoff is at %s)" % h)
        return 1
    rec = {"v": 2, "session": sid, "cwd": cwd, "handoff": str(h).replace("\\", "/"), "at": time.time()}
    written, errors = None, []
    for path in request_paths(sid):
        try:
            _write(path, json.dumps(rec) + "\n")
            written = path
            break
        except OSError as exc:
            errors.append("%s: %s" % (path.parent, exc.__class__.__name__))
    if written is None:
        _out(a, False, "refused: could not write the request (%s); type /compact instead (the handoff "
             "is at %s)" % ("; ".join(errors), rec["handoff"]))
        return 1
    note = " (queued in the sandbox inbox; it lands in the vault at the next drain)" if where == "inbox" else ""
    msg = ("compact requested: it runs when this reply ends, then the handoff is offered in the prompt box "
           "(if nothing happens, type /compact; the handoff is at %s%s)" % (rec["handoff"], note))
    if a.json:
        print(json.dumps({"ok": True, "handoff": rec["handoff"], "request": str(written), "handoff_in": where}))
    else:
        print(msg)
    return 0


def do_status(a) -> int:
    sid = session_id(a.session)
    waiting = bool(sid) and any(_nonempty_file(p) for p in request_paths(sid))
    out = {"auto_compact": setting(), "request_waiting": waiting, "session": sid or None}
    print(json.dumps(out) if a.json else "auto_compact: %s; request waiting: %s" % (out["auto_compact"], "yes" if waiting else "no"))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="gt_compact.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    for name, fn in (("request", do_request), ("status", do_status)):
        c = sub.add_parser(name)
        c.add_argument("--cwd", default="")
        c.add_argument("--session", default="")
        c.add_argument("--json", action="store_true")
        if name == "request":
            c.add_argument("--handoff", default="")
        c.set_defaults(fn=fn)
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_usage(sys.stderr)
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
