#!/usr/bin/env python3
"""gt_accept.py -- the reviewed accept of a #conflict escalation (ported from 0.20.2, c27b8b6).

    gt_broker.py accept [--vault V]                          the open conflicts (read-only)
    gt_broker.py accept ID --show (--version N | --keep-current) [--vault V]
                                                             the diff it would apply (read-only)
    gt_broker.py accept ID (--version N | --keep-current) [--vault V]
                                                             apply it -- a person, at a terminal

WHY. The write broker never writes a design.md or anything under global-memory/ (review targets),
and it escalates every write it cannot decide on its own (two sessions replacing one section, a
section that changed under a replace, a farmed result into existing text). Each escalation is a
`spool/broker/conflicts/<date>-<id>.md` holding every version, a `.json` beside it with the same
versions as data, and a p1 #conflict task for the owner. This is the owner's way to settle one.

WHAT ACCEPT DOES. It takes ONE of the recorded versions (or keeps the current text), applies that
version's op to the target AS IT IS NOW with the broker's own renderers, shows the unified diff,
and -- after a person confirms -- writes it the broker's way: under the drain lock, after the
claim check (another LIVE session's claim holds it), atomically, and only if the target is still
byte-for-byte what the person reviewed AND the text to be written is the text whose hash was shown
(else nothing is written and it says so). It logs a broker row with decision `accept`, records the
resolution in the conflict file and closes the #conflict task through the vault's gt_task.py.
`--keep-current` writes nothing to the target and closes the conflict the same way.

THE accept_review SETTING (0.21.0; read from gt_settings, default `dialog`):
  dialog  the diff (capped at DIFF_MAX characters), then a typed confirmation phrase -- and with
          gt unlock on, the presence step-up (Touch ID / Windows Hello) first. As in 0.20.2.
  full    the WHOLE change first: path, both sha256 values, the line count, every hidden character
          (Cc, Cf, Zl, Zp, bidi, zero-width, tag characters) shown as <U+XXXX> with a count, then
          an "END OF CHANGES (N lines)" marker, and only AFTER the marker a code to type back --
          then the presence step-up. The code is bound to this conflict, both hashes and a
          per-run random nonce. A change over FULL_LIMIT changed lines is refused for `full` and
          belongs in an editor. It proves the end was reached, not that it was read.

WHO MAY ACCEPT -- never the model on its own say-so: stdin and stdout must be a terminal and the
person types the confirmation it prints; with gt unlock ON the scope gt:vault:accept (`step_up`)
raises a fresh platform factor first. Honest limit: with unlock OFF and sandbox mode OFF, a process
running as you can fake a terminal -- and can also simply write the file, so accept adds no new
power there. This line ships the TERMINAL route only; the MCP elicitation route (vault_accept) of
0.20.2 is not part of 0.21.0.

Exit: 0 applied / listed / shown | 1 refused (and why) | 2 usage or no vault.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import gt_tty                                                    # noqa: E402
import gt_broker as B                                            # noqa: E402
import gt_write_queue as wq                                      # noqa: E402

CONFLICT_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{12}$")
SCOPE = "gt:vault:accept"
DIFF_MAX = 12000
FULL_LIMIT = 200            # `full` review: most changed lines it will show
MODES = ("dialog", "full")
RESOLUTION = "## Resolution"


class Refused(Exception):
    def __init__(self, code, message, hints=None):
        super().__init__(message)
        self.code, self.message, self.hints = code, message, list(hints or [])


def conflicts_dir(vault: Path) -> Path:
    return Path(vault) / B.BROKER_REL / "conflicts"


def _sha(text):
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ reading a conflict ----

_VERSION = re.compile(r"^## Version (\d+) -- session (\S+) \(([^,]+), ([a-z-]+)\)\s*$")
_TITLE = re.compile(r"^# Write conflict: (.+?)(?: § (.+))?\s*$")


def _parse_md(text: str) -> dict:
    """The conflict .md written by gt_broker.write_conflict, read back (conflicts made before
    0.20.2 have no .json sidecar). Fences are longer than any backtick run in their content."""
    lines = text.split("\n")
    out = {"target": None, "section": None, "reason": "", "requests": []}
    i = 0
    cur = None
    while i < len(lines):
        ln = lines[i]
        m = _TITLE.match(ln)
        if m and out["target"] is None:
            out["target"], out["section"] = m.group(1).strip(), (m.group(2) or None)
        elif ln.startswith("- reason: ") and not out["reason"]:
            out["reason"] = ln[len("- reason: "):]
        else:
            m = _VERSION.match(ln)
            if m:
                cur = {"id": None, "session": m.group(2), "origin": m.group(3).strip(),
                       "op": m.group(4), "path": out["target"], "section": out["section"],
                       "key": None, "content": None}
                out["requests"].append(cur)
            elif cur is not None and ln.startswith("- request: `"):
                cur["id"] = ln[len("- request: `"):].rstrip("`")
            elif cur is not None and cur["content"] is None and re.match(r"^`{3,}$", ln):
                fence, body = ln, []
                i += 1
                while i < len(lines) and lines[i] != fence:
                    body.append(lines[i])
                    i += 1
                cur["content"] = "\n".join(body)
        i += 1
    out["resolved"] = any(ln.strip() == RESOLUTION for ln in lines)
    return out


def load(vault: Path, cid: str) -> dict:
    if not isinstance(cid, str) or not CONFLICT_ID.match(cid.strip()):
        raise Refused("bad_request", "a conflict id looks like 2026-10-04-0123456789ab (the "
                                     "file name under spool/broker/conflicts/, without .md)")
    cid = cid.strip()
    md = conflicts_dir(vault) / (cid + ".md")
    if not md.is_file() or md.is_symlink():
        raise Refused("not_found", "no conflict %s in spool/broker/conflicts/" % cid)
    text = md.read_text(encoding="utf-8")
    parsed = _parse_md(text)
    side = md.with_suffix(".json")
    data = None
    if side.is_file() and not side.is_symlink():
        try:
            data = json.loads(side.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
    if isinstance(data, dict) and isinstance(data.get("requests"), list) and data["requests"]:
        reqs = data["requests"]
        target = data.get("target") or reqs[0].get("path")
        reason = data.get("reason") or parsed["reason"]
        resolved = bool(data.get("resolved")) or parsed["resolved"]
    else:
        reqs, target, reason = parsed["requests"], parsed["target"], parsed["reason"]
        resolved = parsed["resolved"]
    if not target or not reqs:
        raise Refused("unreadable", "conflict %s names no target or holds no version" % cid)
    why = wq.path_refusal(Path(vault), target)
    if why:
        raise Refused("refused", "conflict %s targets %s, which accept may not write: %s"
                      % (cid, target, why))
    for r in reqs:
        if r.get("path") not in (None, target) or r.get("op") not in wq.OPS \
                or not isinstance(r.get("content"), str):
            raise Refused("unreadable", "conflict %s has a version that does not match its "
                                        "target, or an unknown op" % cid)
    return {"id": cid, "file": md, "rel": md.relative_to(vault).as_posix(), "target": target,
            "reason": reason, "requests": reqs, "resolved": resolved}


def list_open(vault: Path, include_resolved=False) -> list:
    d = conflicts_dir(vault)
    out = []
    if not d.is_dir():
        return out
    for md in sorted(d.glob("*.md")):
        if not CONFLICT_ID.match(md.stem):
            continue
        try:
            c = load(vault, md.stem)
        except Refused as e:
            out.append({"id": md.stem, "error": e.message})
            continue
        if c["resolved"] and not include_resolved:
            continue
        first = c["requests"][0]
        out.append({"id": c["id"], "target": c["target"], "section": first.get("section"),
                    "reason": c["reason"], "versions": len(c["requests"]),
                    "ops": [r.get("op") for r in c["requests"]], "resolved": c["resolved"],
                    "file": c["rel"]})
    return out


# ------------------------------------------------------------------ what it would write ----

def render(text, req) -> str:
    """The target's new full text: req's op applied to the target as it is NOW, with the
    broker's own renderers (no dedupe, no base check -- a person is choosing this version)."""
    op, sec, content = req["op"], req.get("section"), req["content"]
    if op in ("create", "replace-file"):
        return content if content.endswith("\n") else content + "\n"
    if op == "set-property":
        if text is None or not req.get("key"):
            raise Refused("refused", "a set-property version needs the target to exist and a key")
        new = wq.frontmatter_set(text, req["key"], content.strip())
        return new if new.endswith("\n") else new + "\n"
    lines = wq.split_lines(text) if text is not None else []
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if op == "append":
        return "\n".join(B._render_append(lines, sec, content)) + "\n"
    return "\n".join(B._render_replace(lines, sec, content)) + "\n"


def plan(vault: Path, cid: str, version=None, keep_current=False) -> dict:
    c = load(vault, cid)
    if c["resolved"]:
        raise Refused("resolved", "conflict %s is already resolved" % cid)
    if bool(keep_current) == (version is not None):
        raise Refused("bad_request", "choose exactly one: a version number, or keep-current")
    path = Path(vault) / c["target"]
    try:
        cur, cur_sha = B._read(path)
    except (OSError, UnicodeDecodeError) as e:
        raise Refused("unreadable", "%s cannot be read (%s)" % (c["target"], type(e).__name__))
    if keep_current:
        new = cur
    else:
        try:
            n = int(version)
        except (TypeError, ValueError):
            raise Refused("bad_request", "version must be a number")
        if not 1 <= n <= len(c["requests"]):
            raise Refused("bad_request", "conflict %s has versions 1-%d"
                          % (cid, len(c["requests"])))
        new = render(cur, c["requests"][n - 1])
    diff = "".join(difflib.unified_diff((cur or "").splitlines(True), (new or "").splitlines(True),
                                        "a/" + c["target"], "b/" + c["target"]))
    trunc = len(diff) > DIFF_MAX
    changed = sum(1 for ln in diff.split("\n")
                  if ln[:1] in ("+", "-") and not ln.startswith(("+++", "---")))
    return {"conflict": cid, "target": c["target"], "version": None if keep_current else int(version),
            "keep_current": bool(keep_current), "changed": new != cur,
            "current_sha256": cur_sha, "new_sha256": _sha(new), "diff": diff[:DIFF_MAX],
            "diff_truncated": trunc, "versions": len(c["requests"]), "reason": c["reason"],
            "changed_lines": changed, "diff_lines": len(diff.splitlines()),
            "_new": new, "_cur": cur, "_conflict": c, "_diff_full": diff}


def public(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


# ------------------------------------------------------------------ applying it ----

def apply(vault: Path, cid: str, version=None, keep_current=False, expect_sha=None,
          approved_by="?", grant=None, expect_new_sha=None) -> dict:
    """Write the reviewed version. The CALLER has already obtained the person's confirmation
    (and the unlock step-up when unlock is on); `expect_sha` is the target's sha256 as the person
    saw it in the diff -- anything else and nothing is written. `expect_new_sha` (0.21.0) is the
    sha256 of the text the person was shown it would write: if the text now computed differs, the
    approval does not cover it and nothing is written."""
    vault = Path(vault)
    lock = B._lock(vault)
    if lock is None:
        raise Refused("busy", "a broker drain is running; nothing written -- try again")
    try:
        p = plan(vault, cid, version, keep_current)
        if p["current_sha256"] != expect_sha:
            raise Refused("changed", "%s changed after the diff was shown; nothing written -- "
                                     "review the new diff" % p["target"])
        if expect_new_sha is not None and p["new_sha256"] != expect_new_sha:
            raise Refused("mismatch", "the text to write is not the text that was reviewed "
                                      "(sha256 differs); nothing written -- review it again")
        c = p["_conflict"]
        me = B.my_session()
        written = False
        if p["changed"]:
            holders, err = B.claim_holders(vault, p["target"], me or "", {me} if me else set())
            if err:
                raise Refused("held", "could not check claims, so not writing: %s" % err)
            if holders:
                raise Refused("held", "%s is claimed by live session %s (Core rule 1); nothing "
                                      "written -- accept again when it is released"
                              % (p["target"], ", ".join(sorted(set(holders)))))
            try:
                written = B._atomic_replace(vault / p["target"], p["_new"], p["current_sha256"])
            except OSError as e:
                raise Refused("refused", B._refusal(vault / p["target"], e))
            if not written:
                raise Refused("changed", "%s changed while it was being written; nothing "
                                         "written" % p["target"])
        what = "kept the current text" if keep_current else "applied version %d" % int(version)
        d = B.Drain(vault, False)
        d.record({"id": "accept-" + cid, "path": p["target"],
                  "section": c["requests"][0].get("section"),
                  "op": None if keep_current else c["requests"][int(version) - 1]["op"],
                  "session": me, "origin": "owner"}, "accept",
                 "%s of conflict %s, confirmed by %s" % (what, cid, approved_by),
                 conflict=c["rel"], grant=grant)
        _mark_resolved(c, what, approved_by)
    finally:
        lock.close()
    task = close_task(vault, c["rel"], cid, what)
    out = public(p)
    out.update({"ok": True, "decision": "accept", "written": written, "resolution": what,
                "approved_by": approved_by, "task": task})
    return out


def _mark_resolved(c, what, approved_by):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    try:
        with open(c["file"], "a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n%s\n\n- %s: %s (confirmed by %s)\n" % (RESOLUTION, ts, what, approved_by))
    except OSError:
        pass
    side = Path(c["file"]).with_suffix(".json")
    try:
        data = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else None
        if isinstance(data, dict):
            data["resolved"] = {"at": ts, "what": what, "by": approved_by}
            side.write_bytes(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
    except (OSError, ValueError):
        pass


def close_task(vault: Path, conflict_rel: str, cid: str, what: str) -> dict:
    """Close the #conflict task(s) whose ref:: names this conflict, through the vault's own
    gt_task.py (it queues and drains like any task write). Never fails the accept."""
    tool = Path(vault) / B.TOOLS_REL / "gt_task.py"
    if not tool.is_file():
        return {"closed": [], "note": "no gt_task.py in the vault; close the #conflict task by hand"}
    try:
        p = subprocess.run([sys.executable, str(tool), "list", "--vault", str(vault),
                            "ref:" + cid, "--json"], capture_output=True, text=True, timeout=60)
        rows = json.loads(p.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"closed": [], "note": "could not list the #conflict task; close it by hand"}
    if isinstance(rows, dict):
        rows = rows.get("tasks") or []
    closed, notes = [], []
    for r in rows:
        if "#conflict" not in str(r.get("text", "")):
            continue
        q = subprocess.run([sys.executable, str(tool), "done", r["id"], "--vault", str(vault),
                            "--reason=accepted: %s" % what], capture_output=True, text=True,
                           timeout=120)
        if q.returncode == 0:
            closed.append(r["id"])
        else:
            notes.append((q.stderr.strip().splitlines() or ["exit %d" % q.returncode])[-1])
    return {"closed": closed, "note": "; ".join(notes) or None}


# ------------------------------------------------------------------ unlock ----

def unlock_step_up(reason, client=None):
    """None when unlock is off or the step-up was granted; else a Refused to raise. The
    authority raises the factor prompt itself -- nothing here can answer it."""
    try:
        import gt_unlock_policy as P                          # noqa: PLC0415
        if not P.enabled_fast():
            return None, None
    except Exception:                                          # noqa: BLE001 - unreadable = on
        pass
    try:
        c = client or __import__("gt_unlock_client")
    except Exception:                                          # noqa: BLE001
        return Refused("unlock_unavailable", "gt unlock is on but its client cannot be loaded; "
                                             "refusing"), None
    v = c.check(SCOPE, request=True, reason=reason)
    if not v.get("allowed"):
        return Refused(str(v.get("code") or "locked"),
                       "gt unlock did not grant %s: %s" % (SCOPE, v.get("message") or "refused"),
                       v.get("hints")), None
    return None, v.get("grant")


# ------------------------------------------------------------------ the review ----

def review_mode() -> str:
    """The accept_review setting: `dialog` (default) or `full`. Read through gt_settings; an
    unknown, empty or unreadable value is `dialog`."""
    v = None
    try:
        import gt_settings as S                                   # noqa: PLC0415
        v = S.get("accept_review")
        if v is None:                       # not registered in this gt_settings: read it raw
            v = S._load().get("accept_review")
    except Exception:                                              # noqa: BLE001
        v = None
    v = v.strip().lower() if isinstance(v, str) else ""
    return v if v in MODES else "dialog"


def reveal(text: str):
    """-> (text with every hidden character shown as <U+XXXX>, how many there were). Hidden =
    category Cc, Cf, Zl or Zp (controls, bidi and zero-width marks, tag characters, line and
    paragraph separators), except the newline and tab the diff itself is made of."""
    out, n = [], 0
    for ch in text:
        if ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs"):
            out.append("<U+%04X>" % ord(ch))
            n += 1
        else:
            out.append(ch)
    return "".join(out), n


def confirm_code(p: dict, nonce: str) -> str:
    """The code a `full` review asks for: bound to the conflict, both hashes and this run's nonce,
    so it cannot be carried from another change or another run."""
    raw = "|".join([p["conflict"], str(p["current_sha256"]), str(p["new_sha256"]), nonce])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]


def full_review_text(p: dict, limit: int = FULL_LIMIT) -> str:
    """The whole change, for the `full` review, up to and including the end marker. Refused if it
    is over `limit` changed lines. The caller prints the code after this, never before."""
    if p["changed_lines"] > limit:
        raise Refused("too_large", "this change has %d changed lines, over the %d a `full` review "
                      "shows; open %s and the conflict file in an editor and decide there"
                      % (p["changed_lines"], limit, p["target"]),
                      ["or review it with accept_review dialog (the diff is capped)"])
    shown, hidden = reveal(p.get("_diff_full", p["diff"]))
    nlines = len(shown.splitlines())
    head = ["gt accept: REVIEW of %s (conflict %s, %s)" % (
                p["target"], p["conflict"],
                "keep current" if p["keep_current"] else "version %s" % p["version"]),
            "current sha256 %s  ->  new sha256 %s" % (p["current_sha256"], p["new_sha256"]),
            "%d diff line(s); %d hidden character(s) shown as <U+XXXX>" % (nlines, hidden), ""]
    return "\n".join(head) + "\n" + (shown if shown.endswith("\n") else shown + "\n") \
        + "END OF CHANGES (%d lines)\n" % nlines


# ------------------------------------------------------------------ CLI ----

def phrase(cid):
    return "accept " + cid[-6:]


def owner_argv(vault, a):
    """-> the exact command the person runs for a `needs_owner` refusal: the interpreter and broker
    by absolute path, the version choice and the vault, so nothing is left to retype (0.21.4)."""
    argv = [sys.executable or "python3", str(HERE / "gt_broker.py"), "accept", a.conflict]
    argv += ["--keep-current"] if a.keep_current else ["--version", str(a.version)]
    return argv + ["--vault", str(Path(vault).resolve())]


def hand_to_owner(argv):
    """-> the lines that hand `argv` over: on the clipboard (gt_owner_cmd), else printed. It never
    runs the command; the gate above is unchanged."""
    try:
        import gt_owner_cmd
        return gt_owner_cmd.hand(argv)["lines"]
    except Exception:                                            # noqa: BLE001
        import shlex
        return ["run this yourself, in a terminal:", "    " + shlex.join([str(x) for x in argv])]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gt_broker.py accept",
                                 description="apply one version of a #conflict escalation after "
                                             "you review the diff")
    ap.add_argument("conflict", nargs="?", help="conflict id (spool/broker/conflicts/<id>.md)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--version", type=int, help="the version number to apply")
    g.add_argument("--keep-current", action="store_true", help="write nothing; close the conflict")
    ap.add_argument("--show", action="store_true", help="print the diff and stop (read-only)")
    ap.add_argument("--all", action="store_true", help="list resolved conflicts too")
    ap.add_argument("--vault")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    vault = wq.find_vault(a.vault)
    if vault is None:
        print("gt_broker accept: no vault found; pass --vault", file=sys.stderr)
        return 2
    try:
        if not a.conflict:
            rows = list_open(vault, a.all)
            if a.json:
                print(json.dumps({"conflicts": rows}, indent=1))
            elif not rows:
                print("no open conflicts")
            for r in ([] if a.json else rows):
                print("%s  %s%s  (%s; %d version(s))  %s" % (
                    r["id"], r.get("target"), (" § " + r["section"]) if r.get("section") else "",
                    ",".join(r.get("ops") or []), r.get("versions", 0), r.get("reason", "")))
            return 0
        p = plan(vault, a.conflict, a.version, a.keep_current)
        if a.show:
            print(json.dumps(public(p), indent=1) if a.json else (p["diff"] or "(no change)"))
            return 0
        if not (gt_tty.has_terminal(sys.stdin) and gt_tty.has_terminal(sys.stdout)):
            e = Refused("needs_owner", "accept applies a reviewed write and needs a person at a "
                                       "terminal; run `gt_broker.py accept %s` yourself" % a.conflict)
            e.owner_argv = owner_argv(vault, a)
            raise e
        mode = review_mode()
        if mode == "full":
            text = full_review_text(p)                       # may refuse: too large
            print(text, end="")
            nonce = secrets.token_hex(8)
            want = confirm_code(p, nonce)
            print("code: %s" % want)
            prompt = "Type the code to apply this to %s: " % p["target"]
        else:
            err, grant = unlock_step_up("accept conflict %s into %s" % (a.conflict, p["target"]))
            if err:
                raise err
            print(p["diff"] or "(no change to %s)" % p["target"])
            if p["diff_truncated"]:
                print("... diff truncated; see %s" % p["_conflict"]["rel"])
            want = phrase(a.conflict)
            prompt = 'Type "%s" to apply this to %s: ' % (want, p["target"])
        try:
            got = input(prompt)
        except EOFError:
            got = ""
        if got.strip() != want:
            raise Refused("declined", "not confirmed; nothing written")
        if mode == "full":
            err, grant = unlock_step_up("accept conflict %s into %s" % (a.conflict, p["target"]))
            if err:
                raise err
        res = apply(vault, a.conflict, a.version, a.keep_current, p["current_sha256"],
                    approved_by="terminal", grant=grant, expect_new_sha=p["new_sha256"])
    except Refused as e:
        print("gt_broker accept: REFUSED (%s): %s" % (e.code, e.message), file=sys.stderr)
        for h in e.hints:
            print("  hint: %s" % h, file=sys.stderr)
        if getattr(e, "owner_argv", None):
            for ln in hand_to_owner(e.owner_argv):
                print("  " + ln, file=sys.stderr)
        return 1 if e.code != "bad_request" else 2
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print("accepted: %s -- %s%s" % (res["target"], res["resolution"],
                                        "" if res["task"].get("closed") else
                                        " (task: %s)" % (res["task"].get("note") or "none found")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
