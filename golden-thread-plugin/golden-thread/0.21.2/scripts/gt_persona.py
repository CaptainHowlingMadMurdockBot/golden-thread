#!/usr/bin/env python3
"""gt_persona.py -- personas: named bundles of defaults, shipped as data (0.21.0, Stage 1).

A persona changes what gt OFFERS, never what it ENFORCES. Stage 1 carries one section, `scaffold`:
extra files and extra sections for a new project, on top of the standard structure.

  gt_persona.py list [--packs DIR]
  gt_persona.py show <name> [--packs DIR]
  gt_persona.py resolve [--project SLUG] [--vault V] [--json] [--title T] [--packs DIR]
  gt_persona.py apply <project-dir> [--project SLUG] [--vault V] [--title T] [--personas a,b]
                                              # additive, idempotent; vault_init uses the same call
  gt_persona.py check [--packs DIR]          # schema check over every pack; exit 1 on a problem
  gt_persona.py audience [--project SLUG] [--vault V]
                                              # the Audience block for writing agents and wrap-ups (#45);
                                              # empty when persona_aligned is off or no persona is set

RESOLUTION. The user's list is the `persona` key of ~/.claude/vault-config.json (a comma list or a
JSON array). A project's README frontmatter `persona:` REPLACES it for that project (`none` means
no persona). Several personas: the union of their scaffold files and sections; where two name the
same file (or the same section heading in one file) the first listed wins. No persona: an empty
scaffold, and callers change nothing -- behaviour is byte-identical to a gt without personas.

PACKS come from packs/personas/ in the RELEASE dir (the parent of this script's scripts/ folder),
never the caller's directory: a hook runs a copy of a script, and a copy without packs/ is the bug
the secrets scan had. `--packs` overrides for tests and local authoring.

REFUSED at load: any key named lockdown, sandbox_mode, unlock, test_gate or protected_paths at any
depth, any unknown key, a path that escapes the project or names a standard file, a name that does
not match its file. A refused pack is reported and never listed or resolved.
Stdlib only.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROTECTED = ("lockdown", "sandbox_mode", "unlock", "test_gate", "protected_paths")
STANDARD_FILES = ("README.md", "idea.md", "source.md", "research.md", "decisions.md",
                  "design.md", "CLAUDE.md", "runbook.md", "memory/MEMORY.md")
SECTION_TARGETS = ("README.md", "idea.md", "source.md", "research.md", "decisions.md",
                   "design.md", "runbook.md")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
MAX_PACK_BYTES = 64 * 1024
TOP_KEYS = {"schema", "name", "title", "description", "scaffold", "pane", "audience"}
AUDIENCE_MAX = 300
PANE_MAX, PANE_LABEL_MAX, PANE_PROMPT_MAX = 12, 22, 500


class PersonaError(Exception):
    pass


def packs_dir(override=None):
    """packs/personas/ in the release dir this script lives in (or the override)."""
    if override:
        return override
    return os.path.join(os.path.dirname(HERE), "packs", "personas")


def _walk_keys(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + "/" + str(k), str(k)
            for x in _walk_keys(v, path + "/" + str(k)):
                yield x
    elif isinstance(node, list):
        for i, v in enumerate(node):
            for x in _walk_keys(v, "%s[%d]" % (path, i)):
                yield x


def _bad_path(p):
    if not isinstance(p, str) or not p or "\\" in p or p.startswith("/") or ":" in p:
        return True
    parts = p.split("/")
    if any(s in ("", ".", "..") or s.startswith(".") for s in parts):
        return True
    return not p.endswith(".md")


def _pane_text_ok(v, limit):
    return (isinstance(v, str) and v.strip() != "" and len(v) <= limit
            and not any(ord(c) < 32 or 127 <= ord(c) < 160 or c in "\u2028\u2029" for c in v))


def _pane_problems(pane):
    """The gt pane's default buttons (item #41): up to 12 of {label, prompt}, one line each."""
    if not isinstance(pane, list) or not pane or len(pane) > PANE_MAX:
        return ["pane must be a list of 1 to %d buttons" % PANE_MAX]
    out = []
    for i, b in enumerate(pane):
        if not isinstance(b, dict) or set(b) != {"label", "prompt"}:
            out.append("pane[%d] must be {label, prompt}" % i)
        elif not _pane_text_ok(b["label"], PANE_LABEL_MAX):
            out.append("pane[%d].label must be 1 to %d characters with no control character" % (i, PANE_LABEL_MAX))
        elif not _pane_text_ok(b["prompt"], PANE_PROMPT_MAX):
            out.append("pane[%d].prompt must be one non-empty line of at most %d characters" % (i, PANE_PROMPT_MAX))
    return out


def validate_pack(data, name):
    """-> a list of problems; empty means the pack is acceptable."""
    if not isinstance(data, dict):
        return ["pack is not a JSON object"]
    probs = []
    for where, key in _walk_keys(data):
        if key in PROTECTED:
            probs.append("names %s (%s): a persona cannot touch enforcement" % (key, where))
    for k in data:
        if k not in TOP_KEYS and k not in PROTECTED:
            probs.append("unknown key %r" % k)
    if data.get("schema") != 1:
        probs.append("schema must be 1")
    if not isinstance(data.get("name"), str) or not NAME_RE.match(data.get("name") or ""):
        probs.append("name must match %s" % NAME_RE.pattern)
    elif data["name"] != name:
        probs.append("name %r does not match its file name %r" % (data["name"], name))
    if not isinstance(data.get("title"), str) or not data.get("title"):
        probs.append("title must be a non-empty string")
    if "description" in data and not isinstance(data["description"], str):
        probs.append("description must be a string")
    if "audience" in data and not _pane_text_ok(data["audience"], AUDIENCE_MAX):
        probs.append("audience must be one non-empty line of at most %d characters" % AUDIENCE_MAX)
    if "pane" in data:
        probs.extend(_pane_problems(data["pane"]))
    sc = data.get("scaffold")
    if not isinstance(sc, dict):
        return probs + ["scaffold must be an object"]
    for k in sc:
        if k not in ("files", "sections") and k not in PROTECTED:
            probs.append("unknown scaffold key %r" % k)
    files = sc.get("files", [])
    if not isinstance(files, list):
        probs.append("scaffold.files must be a list")
        files = []
    for i, f in enumerate(files):
        if not isinstance(f, dict) or set(f) - {"path", "content"} or "path" not in f or "content" not in f:
            probs.append("scaffold.files[%d] must be {path, content}" % i)
            continue
        if _bad_path(f["path"]):
            probs.append("scaffold.files[%d].path %r must be a relative .md path inside the project" % (i, f["path"]))
        elif f["path"] in STANDARD_FILES:
            probs.append("scaffold.files[%d].path %r is a standard project file; a persona adds, never replaces" % (i, f["path"]))
        if not isinstance(f["content"], str):
            probs.append("scaffold.files[%d].content must be a string" % i)
    secs = sc.get("sections", [])
    if not isinstance(secs, list):
        probs.append("scaffold.sections must be a list")
        secs = []
    for i, s in enumerate(secs):
        if not isinstance(s, dict) or set(s) != {"file", "heading", "body"}:
            probs.append("scaffold.sections[%d] must be {file, heading, body}" % i)
            continue
        if s["file"] not in SECTION_TARGETS:
            probs.append("scaffold.sections[%d].file %r is not one of %s" % (i, s["file"], ", ".join(SECTION_TARGETS)))
        if not isinstance(s["heading"], str) or not s["heading"].strip() or "\n" in s["heading"]:
            probs.append("scaffold.sections[%d].heading must be one non-empty line" % i)
        if not isinstance(s["body"], str):
            probs.append("scaffold.sections[%d].body must be a string" % i)
    return probs


def _load_file(path, name):
    """-> (data, problems)."""
    try:
        if os.path.getsize(path) > MAX_PACK_BYTES:
            return None, ["pack is larger than %d bytes" % MAX_PACK_BYTES]
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, ["cannot be read as JSON (%s)" % type(exc).__name__]
    probs = validate_pack(data, name)
    return (None if probs else data), probs


def _pack_files(pdir):
    try:
        names = sorted(os.listdir(pdir))
    except OSError:
        return []
    return [n for n in names if n.endswith(".json") and not n.endswith(".schema.json")]


def check_all(pdir=None):
    """-> [(file, problem)] for every pack under pdir; a missing directory is itself a problem."""
    pdir = packs_dir(pdir)
    if not os.path.isdir(pdir):
        return [(pdir, "persona pack directory is missing")]
    out = []
    for fn in _pack_files(pdir):
        _, probs = _load_file(os.path.join(pdir, fn), fn[:-5])
        out.extend((fn, p) for p in probs)
    return out


def list_packs(pdir=None):
    """-> [{name, title, description}] for the packs that pass the schema, in name order."""
    pdir = packs_dir(pdir)
    out = []
    for fn in _pack_files(pdir):
        data, _ = _load_file(os.path.join(pdir, fn), fn[:-5])
        if data:
            out.append({"name": data["name"], "title": data["title"], "description": data.get("description", "")})
    return out


def valid_names(pdir=None):
    return [p["name"] for p in list_packs(pdir)]


def load_pack(name, pdir=None):
    pdir = packs_dir(pdir)
    valid = valid_names(pdir)
    if name not in valid:
        raise PersonaError("unknown persona %r; valid personas: %s" % (name, ", ".join(valid) or "(none installed)"))
    data, _ = _load_file(os.path.join(pdir, name + ".json"), name)
    return data


def _names(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = value.replace("[", "").replace("]", "").split(",")
    out = []
    for v in value if isinstance(value, list) else []:
        v = str(v).strip().strip("'\"").lower()
        if v and v != "none" and v not in out:
            out.append(v)
    return out


def _config_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".claude", "vault-config.json")


def user_personas(home=None):
    try:
        with open(_config_path(home), encoding="utf-8") as fh:
            return _names(json.load(fh).get("persona"))
    except (OSError, ValueError, AttributeError):
        return []


def _vault(vault, home=None):
    if vault:
        return vault
    if os.environ.get("GT_VAULT"):
        return os.environ["GT_VAULT"]
    try:
        with open(_config_path(home), encoding="utf-8") as fh:
            return json.load(fh).get("vault_path")
    except (OSError, ValueError, AttributeError):
        return None


def _project_readme(vault, slug):
    base = os.path.join(vault, "Projects")
    cands = [os.path.join(base, slug, "README.md")]
    try:
        for d in sorted(os.listdir(base)):
            cands.append(os.path.join(base, d, slug, "README.md"))
    except OSError:
        pass
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def project_override(vault, slug):
    """-> None (no `persona:` key) or the project's list (possibly empty for `none`)."""
    readme = _project_readme(vault, slug) if vault and slug else None
    if not readme:
        return None
    try:
        with open(readme, encoding="utf-8") as fh:
            text = fh.read().replace("\r\n", "\n")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    for line in text[4:end].split("\n"):
        m = re.match(r"^persona:\s*(.*)$", line)
        if m:
            return _names(m.group(1))
    return None


def resolve(vault=None, slug=None, home=None):
    """-> the effective persona names: the project's README `persona:` if present, else the user's list."""
    ov = project_override(_vault(vault, home), slug) if slug else None
    return ov if ov is not None else user_personas(home)


def audience_block(names, pdir=None):
    """-> the "Audience:" block for the agents that WRITE for the user (item #45), or "".
    One line per persona that has an `audience`, in the order given; the first leads. Presentation
    only -- the block says so, and gt_agent_spec.py sends it to writing stages and nothing else."""
    lines, done = [], []
    for n in names:
        if n in done:
            continue
        done.append(n)
        a = (load_pack(n, pdir).get("audience") or "").strip()
        if a:
            lines.append("- %s" % a)
    if not lines:
        return ""
    return "\n".join(
        ["Audience:", "You are writing for the reader below. Several roles are listed in order; the "
         "first leads, so where they differ follow the first."] + lines +
        ["This changes how you present the result (order, plain words, how much detail), never what "
         "is recorded as true: every fact, citation and verification state stays exactly as the "
         "material gives it, and you add no claim to suit the reader. If the request says \"give me the "
         "details\", give the full detail for that answer."]) + "\n"


def resolve_scaffold(names, title, pdir=None):
    """-> {"files": [{path, content}], "sections": [{file, heading, body}]}, {TITLE} filled.
    Union over the personas in order; the first to name a file (or a file+heading) wins."""
    files, sections, seen_f, seen_s = [], [], set(), set()
    done = []
    for n in names:
        if n in done:
            continue
        done.append(n)
        sc = load_pack(n, pdir)["scaffold"]
        for f in sc.get("files", []):
            if f["path"] not in seen_f:
                seen_f.add(f["path"])
                files.append({"path": f["path"], "content": f["content"].replace("{TITLE}", title or "")})
        for s in sc.get("sections", []):
            key = (s["file"], s["heading"].strip().lower())
            if key not in seen_s:
                seen_s.add(key)
                sections.append({"file": s["file"], "heading": s["heading"],
                                 "body": s["body"].replace("{TITLE}", title or "")})
    return {"files": files, "sections": sections}


def apply_scaffold(proj, scaffold):
    """Write a resolved scaffold into the project dir `proj`, additively: a file that exists is left
    alone, a section is appended only when its heading is absent. -> [("CREATED"|"UPDATED"|"SKIPPED", rel)]."""
    out = []
    for f in scaffold["files"]:
        dest = os.path.join(proj, *f["path"].split("/"))
        if os.path.exists(dest):
            out.append(("SKIPPED", f["path"]))
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f["content"])
        out.append(("CREATED", f["path"]))
    for s in scaffold["sections"]:
        dest = os.path.join(proj, s["file"])
        head = "## " + s["heading"].strip()
        try:
            with open(dest, encoding="utf-8", newline="") as fh:
                cur = fh.read()
        except OSError:
            out.append(("SKIPPED", s["file"]))
            continue
        if re.search(r"(?m)^%s\s*$" % re.escape(head), cur):
            out.append(("SKIPPED", s["file"] + " " + head))
            continue
        nl = "\r\n" if "\r\n" in cur else "\n"
        lead = "" if (cur == "" or cur.endswith("\n")) else "\n"
        add = lead + "\n" + head + "\n\n" + s["body"].rstrip("\n") + "\n"
        with open(dest, "a", encoding="utf-8", newline="") as fh:
            fh.write(add.replace("\n", nl))
        out.append(("UPDATED", s["file"] + " " + head))
    return out


def _audience_setting():
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import gt_settings
        v = gt_settings.get("persona_aligned")
        return v if v in ("on", "off") else "on"
    except Exception:                                    # noqa: BLE001 - the default is on
        return "on"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gt_persona.py", description=__doc__.split("\n")[0])
    ap.add_argument("--packs", default=None, help="persona pack directory (default: the release's packs/personas)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sh = sub.add_parser("show")
    sh.add_argument("name")
    rs = sub.add_parser("resolve")
    rs.add_argument("--project")
    rs.add_argument("--vault")
    rs.add_argument("--json", action="store_true")
    rs.add_argument("--title", default="")
    sub.add_parser("check")
    au = sub.add_parser("audience")
    au.add_argument("--project")
    au.add_argument("--vault")
    ap_ = sub.add_parser("apply")
    ap_.add_argument("project_dir")
    ap_.add_argument("--project")
    ap_.add_argument("--vault")
    ap_.add_argument("--title", default="")
    ap_.add_argument("--personas", help="comma list; default: resolve")
    # --packs is accepted after the subcommand too
    for p in sub.choices.values():
        p.add_argument("--packs", dest="packs_sub", default=None)
    a = ap.parse_args(argv)
    pdir = a.packs_sub or a.packs
    try:
        if a.cmd == "list":
            for p in list_packs(pdir):
                print("%-18s %s" % (p["name"], p["title"]))
            for fn, prob in check_all(pdir):
                sys.stderr.write("gt_persona: refused %s: %s\n" % (fn, prob))
        elif a.cmd == "show":
            print(json.dumps(load_pack(a.name, pdir), indent=2))
        elif a.cmd == "check":
            probs = check_all(pdir)
            for fn, prob in probs:
                print("%s: %s" % (fn, prob))
            print("persona packs: %s (%d checked)" % ("FAIL" if probs else "ok", len(_pack_files(packs_dir(pdir)))))
            return 1 if probs else 0
        elif a.cmd == "audience":
            # Empty output when persona_aligned is off or no persona has an audience line.
            if _audience_setting() == "on":
                sys.stdout.write(audience_block(resolve(a.vault, a.project), pdir))
        elif a.cmd == "apply":
            names = _names(a.personas) if a.personas else resolve(a.vault, a.project)
            for kind, rel in apply_scaffold(a.project_dir, resolve_scaffold(names, a.title, pdir)):
                print("%s %s" % (kind, rel))
        else:
            names = resolve(a.vault, a.project)
            if a.json:
                print(json.dumps({"personas": names, "scaffold": resolve_scaffold(names, a.title, pdir)}, indent=2))
            else:
                for n in names:
                    print(n)
    except PersonaError as exc:
        sys.stderr.write("gt_persona: %s\n" % exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
