#!/usr/bin/env python3
"""gt_help.py -- open the offline /gt:gt-help page for a command (0.21.0, item #35).

  gt_help.py [<command>|start|workflows|security|all] [--persona a,b] [--print] [--no-open]
  gt_help.py --list            one line per page: key, command, what it does (opens nothing)

ONE page. `help/help.html` in the release is parameterised by its address: `#list` shows
/gt:gt-list, `#all` the full list, `#start` the Getting started tab (why gt, what it never does without
asking, where to use it, three things to say on day one, help when stuck), `#workflows` the Workflows
tab (the role workflows as conversations, filtered by persona; `&roles=all` shows every role),
`#security` the Security tab (the security levels, every security setting and how to change it, how
the release was tested -- owner 2026-10-10), no
fragment the index; `&persona=project-manager` orders the
index for that role and turns on plain mode; `&plain=1|0` overrides plain mode. The core
walk-throughs are inlined in help.html (a page opened from disk cannot fetch JSON); each installed
module's own come from help/help-data-<module>.js beside it, loaded by a plain <script src>, which
does work from file://. This script stages help.html and every installed module's data file into
~/.claude/golden-thread/help/ (so they sit side by side), then opens file://.../help.html#<command>.

Nothing is run for the user and nothing touches the network. The personas come from the `persona`
setting unless --persona says otherwise. Stdlib only; GT_HELP_NO_OPEN=1 prints the address and does not
open a browser (tests, no-desktop machines). GT_HELP_DIR overrides where help.html is found.
Exit: 0 | 2 unknown command or no page found.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import sys
import webbrowser
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
MARKET = "golden-thread-plugin"
# the tabs of help.html that are not a command's page (items #36, #38; security 2026-10-10)
TABS = {"start": "start", "getting-started": "start", "started": "start", "guide": "start",
        "workflows": "workflows", "workflow": "workflows",
        "security": "security", "secure": "security", "tests": "security"}


def home():
    return Path(os.path.expanduser("~"))


def _versions(parent):
    try:
        names = [d for d in os.listdir(parent) if re.fullmatch(r"\d+\.\d+\.\d+", d)]
    except OSError:
        return []
    return sorted(names, key=lambda s: tuple(int(x) for x in s.split(".")), reverse=True)


def help_dirs():
    """-> [Path]: help/ folders, the core release's first. A release beside this script, else the
    newest plugin of each name in the plugin cache."""
    env = os.environ.get("GT_HELP_DIR")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    out = []
    if (HERE.parent / "help").is_dir():
        out.append(HERE.parent / "help")
        repo = HERE.parent.parent.parent
        for m in sorted(x for x in repo.glob("golden-thread-*") if x.is_dir()):
            vs = _versions(m)
            if vs and (m / vs[0] / "help").is_dir():
                out.append(m / vs[0] / "help")
    cache = home() / ".claude" / "plugins" / "cache" / MARKET
    try:
        plugins = sorted(os.listdir(cache))
    except OSError:
        plugins = []
    for p in plugins:
        vs = _versions(cache / p)
        if vs and (cache / p / vs[0] / "help").is_dir() and (cache / p / vs[0] / "help") not in out:
            out.append(cache / p / vs[0] / "help")
    return out


def catalog():
    sys.path.insert(0, str(HERE))
    import gt_hints
    cat = gt_hints.catalog()
    pages = {}
    for cmd, b in cat.items():
        pages.setdefault(b["key"], []).append((cmd, b["does"]))
    return pages


def key_of(arg):
    a = arg.strip().lstrip("/")
    a = re.sub(r"^[a-z-]+:", "", a)                   # gt:gt-close -> gt-close
    a = a.split()[0] if a.split() else ""
    return a[3:] if a.startswith("gt-") and len(a) > 3 else a


def personas(arg):
    raw = arg
    if raw is None:
        try:
            with open(home() / ".claude" / "vault-config.json", encoding="utf-8") as fh:
                raw = json.load(fh).get("persona")
        except Exception:                             # noqa: BLE001
            raw = None
    if isinstance(raw, list):
        names = [str(x) for x in raw]
    else:
        names = str(raw or "").split(",")
    return [n.strip() for n in names if re.fullmatch(r"[a-z][a-z0-9-]{0,39}", n.strip())]


def stage(dirs):
    """Copy help.html, help-security.js and every module's help-data file side by side; -> the
    staged help.html."""
    core = next((d for d in dirs if (d / "help.html").is_file()), None)
    if core is None:
        return None
    out = home() / ".claude" / "golden-thread" / "help"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("help-data-*.js"):
        old.unlink()
    shutil.rmtree(out / "artifacts", ignore_errors=True)
    shutil.copy2(core / "help.html", out / "help.html")
    # the Security tab's data (2026-10-10), beside help.html like a module's data
    if (core / "help-security.js").is_file():
        shutil.copy2(core / "help-security.js", out / "help-security.js")
    for d in dirs:
        for f in sorted(d.glob("help-data-*.js")):
            shutil.copy2(f, out / f.name)
        # what the recorded commands made (0.21.0): each page's "Open what it made" link
        for f in sorted((d / "artifacts").glob("*.html")) if (d / "artifacts").is_dir() else []:
            (out / "artifacts").mkdir(exist_ok=True)
            shutil.copy2(f, out / "artifacts" / f.name)
    return out / "help.html"


def main(argv=None):
    ap = argparse.ArgumentParser(description="open the gt help page")
    ap.add_argument("command", nargs="?")
    ap.add_argument("--persona")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--print", dest="print_only", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    pages = catalog()
    if a.list:
        for k in sorted(pages):
            cmd, does = pages[k][0]
            print("%-16s %s -- %s" % (k, cmd, does))
        print("%d commands" % len(pages))
        return 0
    key = ""
    if a.command:
        word = a.command.strip().lower()
        key = "all" if word in ("all", "--all") else TABS.get(word) or key_of(a.command)
        if key not in ("all",) + tuple(TABS.values()) and key not in pages:
            near = difflib.get_close_matches(key, sorted(pages), n=4, cutoff=0.5)
            print("gt-help: no page for %r.%s" % (a.command, (" Closest: " + ", ".join(near)) if near else
                                                  " `gt_help.py --list` shows every command."), file=sys.stderr)
            return 2
    staged = stage(help_dirs())
    if staged is None:
        print("gt-help: help.html was not found in this release or the plugin cache.", file=sys.stderr)
        return 2
    frag = key
    ps = personas(a.persona)
    if ps:
        frag += ("&" if key else "") + "persona=" + ",".join(ps)
    url = staged.resolve().as_uri() + ("#" + frag if frag else "")
    if a.print_only or a.no_open or os.environ.get("GT_HELP_NO_OPEN"):
        print(url)
        return 0
    try:
        ok = webbrowser.open(url)
    except Exception:                                 # noqa: BLE001
        ok = False
    print(("gt-help: opened " if ok else "gt-help: could not open a browser; open this yourself: ") + url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
