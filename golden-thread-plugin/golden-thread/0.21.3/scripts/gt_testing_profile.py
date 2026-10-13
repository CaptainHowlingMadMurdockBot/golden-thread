#!/usr/bin/env python3
"""gt_testing_profile -- a set-and-forget testing profile: stop pressing enter during a test run (0.21.0).

    gt_testing_profile.py on  [--scope project|user] [--project-dir DIR] [--runners "a,b,..."]
                              [--sandbox DIR] [--until 4h] [--dry-run]
    gt_testing_profile.py off [--scope project|user] [--project-dir DIR]
    gt_testing_profile.py status
    gt_testing_profile.py sweep [--hook]      end every profile whose --until has passed

WHY. An unattended test run stops every few minutes for a yes on the same harmless commands:
pytest, node --test, npm audit, and gt's own tests/run.sh and dev/remote-test.sh (owner,
2026-10-09: "I want testing to run without having to sit there the whole time"). Those prompts
are a convenience gate, not a security control. The Core-rule gates ARE the security model, and
this profile never touches them: the vault write guard, the explicit-vault-target check and the
secrets Stop hook run exactly as before, and nothing here is a hook, a mode or an environment
variable.

WHAT IT WRITES. Claude Code allow rules (permissions.allow) for a reviewed set of commands, and
Edit/Read rules for ONE folder you name with --sandbox. Nothing else:

    default runners  pytest, python3 -m pytest/unittest, npm test, node --test, vitest run, bats,
                     npm audit, pip-audit, semgrep, osv-scanner, and gt's own dev commands
                     (bash dev/remote-test.sh, bash tests/run.sh, GT_TEST_VERSION=* bash
                     tests/run.sh, the same from inside tests/, python3 tests/prun.py)
    --runners        replaces that set with your own comma list of command prefixes
    --sandbox DIR    Edit and Read rules for DIR only; without it no write rule is added

Never written, and refused by name: a bare tool (Bash, Edit), ssh/scp/rsync, rm, git push, curl,
wget, sudo, a shell or interpreter with no script to scope it, a reader of arbitrary files (cat,
tail, grep), --dangerously-skip-permissions, defaultMode bypassPermissions, GT_TEST_GATE, any
hook change, and a sandbox that is the vault, inside it, containing it, your home folder, ~/.claude
or any other credential, login or start-up folder (gt_io_policy's write never-list, the same one
the disk broker uses). No read-only log commands are added: `tail`/`grep` rules cannot be scoped
to a folder, so each would also read a credential file.

HOW IT MERGES (gt_lockdown's pattern; settings.json cannot hold comments, so no BEGIN/END
markers). Only rules the file did not already hold become gt's; they are recorded in
~/.claude/golden-thread/testing/state.json, one record per settings file. The settings file is
copied to ~/.claude/golden-thread/backups/settings.json.<stamp>.testing before any change.
`off` removes exactly the recorded rules and nothing hand-edited; when the file is still exactly
as `on` left it, the original bytes are put back (a file that did not exist is deleted), so
on then off is byte-identical.

SCOPE. The project's .claude/settings.json is the default. `--scope user` writes
~/.claude/settings.json, which reaches every project on this machine -- broader, and said so.
`--until 4h` (minutes or hours, at most 7 days) ends the profile by itself: the session-start
sweep (gt_grant.py sweep), gt_settings.py show and `status` remove an expired one and say so.

WHO. Like a grant, turning it on is yours to do: it needs a terminal (an agent's shell has none)
and a fresh gt unlock confirmation when unlock is on. `off`, `status` and `--dry-run` need
neither: they only tighten or read. `--dry-run` prints the exact diff and writes nothing.
Restart Claude Code (or let it reload settings) after a change.

Stdlib only. Exit: 0 ok, 1 refused, 2 usage.
"""
import base64
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import gt_sandbox as sb  # noqa: E402  (settings paths, JSON helpers, atomic write)

STATE_SCHEMA = 1
SCOPES = ("project", "user")

# --------------------------------------------------------------------- what is allowed
# Command prefixes. A prefix becomes Bash(<prefix>:*), Claude Code's prefix rule.
STATIC_RUNNERS = (
    "pytest", "python3 -m pytest", "python3 -m unittest", "npm test", "node --test",
    "vitest run", "bats", "npm audit", "pip-audit", "semgrep", "osv-scanner",
)
# gt's own dev test commands (dev/remote-test.sh, tests/run.sh, tests/prun.py): where the owner
# presses enter today. `GT_TEST_VERSION=*` is how a release's tests are selected; the `run.sh`
# forms are the same command run from inside tests/.
GT_RUNNERS = (
    "bash dev/remote-test.sh", "bash tests/run.sh", "GT_TEST_VERSION=* bash tests/run.sh",
    "bash run.sh", "GT_TEST_VERSION=* bash run.sh", "python3 tests/prun.py",
)
DEFAULT_RUNNERS = STATIC_RUNNERS + GT_RUNNERS

# Environment variables a runner prefix may set. Nothing that loads code (LD_PRELOAD, PYTHONPATH,
# NODE_OPTIONS) and, by name, never the test gate.
ENV_OK = ("GT_TEST_VERSION", "GT_TEST_JOBS", "GT_REMOTE_TEST_HOST", "PYTHONDONTWRITEBYTECODE",
          "CI", "NODE_ENV", "PYTHONUTF8")

# First words that are never a runner, each with the reason the refusal gives.
NEVER_CMD = {
    "ssh": "it reaches other machines", "scp": "it copies to other machines",
    "sftp": "it reaches other machines", "rsync": "it copies to other machines",
    "rm": "it deletes", "rmdir": "it deletes", "mv": "it moves files", "cp": "it copies files",
    "dd": "it overwrites devices", "ln": "it makes links", "tee": "it writes files",
    "curl": "it fetches and sends over the network", "wget": "it fetches over the network",
    "nc": "it opens network connections", "ncat": "it opens network connections",
    "sudo": "it runs as root", "su": "it runs as another user", "doas": "it runs as root",
    "chmod": "it changes permissions", "chown": "it changes owners",
    "kill": "it stops processes", "pkill": "it stops processes by pattern",
    "killall": "it stops processes by pattern", "eval": "it runs any text", "exec": "it runs any text",
    "xargs": "it runs any command", "find": "it can delete and run commands",
    "env": "it runs any command", "open": "it opens anything", "osascript": "it scripts the Mac",
    "cat": "it reads any file, credentials included", "head": "it reads any file, credentials included",
    "tail": "it reads any file, credentials included", "less": "it reads any file",
    "more": "it reads any file", "grep": "it reads any file, credentials included",
    "rg": "it reads any file, credentials included", "sed": "it reads and rewrites any file",
    "awk": "it runs any program", "strings": "it reads any file", "base64": "it reads any file",
    "printenv": "it prints the environment, secrets included", "set": "it prints the environment",
    "export": "it changes the environment", "source": "it runs any file", ".": "it runs any file",
    "npx": "it downloads and runs a package", "pip": "it installs packages",
    "pip3": "it installs packages", "brew": "it installs software",
}
# git: only reading subcommands are ever a runner prefix.
GIT_READ = ("status", "log", "diff", "show")
SHELLS = ("bash", "sh", "zsh", "dash", "ksh", "fish")
INTERPRETERS = ("python", "python3", "node", "perl", "ruby", "deno", "bun", "php", "pwsh",
                "powershell", "cmd", "lua", "Rscript")
PY_MODULES = ("pytest", "unittest", "coverage", "nose2")
SCRIPT_EXT = (".sh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl")
# anywhere in a runner: credential, login, hook, settings and gate names
FORBIDDEN_TEXT = ("gt_test_gate", "skip-permissions", "bypasspermissions", "dangerously",
                  "defaultmode", "disableallhooks", "golden-thread", "settings.json",
                  "settings.local", "id_rsa", "id_ed25519", ".credentials", ".npmrc", ".pypirc",
                  ".git-credentials", "authorized_keys", "vault-config")
FORBIDDEN_DIRS = (".ssh", ".aws", ".gnupg", ".claude", ".config", ".git")
_META = re.compile(r"[;&|<>$`(){}\\\n\r\"']")
_ENV = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class ProfileError(Exception):
    """A refusal: the message names the rule. Exit 1."""


class UsageError(Exception):
    """Exit 2."""


# --------------------------------------------------------------------- helpers
def _now(now=None):
    if now is not None:
        return float(now)
    env = os.environ.get("GT_GRANT_NOW")           # gt_grant's own injectable clock
    try:
        return float(env) if env else time.time()
    except ValueError:
        return time.time()


def _dir(h=None):
    return os.path.join(sb.gt_home(h), "testing")


def state_path(h=None):
    return os.path.join(_dir(h), "state.json")


def log_path(h=None):
    return os.path.join(_dir(h), "log.jsonl")


def load_state(h=None):
    st = sb._read_json(state_path(h), None)
    if not isinstance(st, dict) or st.get("schema") != STATE_SCHEMA \
            or not isinstance(st.get("profiles"), dict):
        return {"schema": STATE_SCHEMA, "profiles": {}}
    return st


def _save_state(st, h=None):
    if not st["profiles"]:
        try:
            os.unlink(state_path(h))
        except OSError:
            pass
        return
    os.makedirs(_dir(h), exist_ok=True)
    sb._atomic_write(state_path(h), json.dumps(st, indent=2, sort_keys=True) + "\n")


def _log(entry, h=None):
    os.makedirs(_dir(h), exist_ok=True)
    entry = dict(entry, ts=time.strftime("%Y-%m-%d %H:%M:%S %z"))
    with open(log_path(h), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _backup(path, h=None):
    if not os.path.exists(path):
        return None
    d = os.path.join(sb.gt_home(h), "backups")
    os.makedirs(d, exist_ok=True)
    bak = os.path.join(d, "settings.json.%s.testing" % time.strftime("%Y%m%d_%H%M%S"))
    if not os.path.exists(bak):
        shutil.copyfile(path, bak)
    return bak


def settings_for(scope, project_dir=None, h=None):
    if scope == "user":
        return sb.settings_path(h)
    base = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(os.path.abspath(os.path.expanduser(base)), ".claude", "settings.json")


def _load_doc(path):
    raw = _read_bytes(path)
    if raw is None:
        return {}, None
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise ProfileError("refused: %s is not valid JSON (%s); fix it first -- gt never "
                           "overwrites a file it cannot read" % (path, e))
    if not isinstance(d, dict):
        raise ProfileError("refused: %s is not a JSON object" % path)
    return d, raw


# --------------------------------------------------------------------- the runner checks
def check_runner(text, h=None):
    """-> the normalised command prefix, or raises ProfileError naming the rule it broke."""
    t = (text or "").strip()

    def no(why):
        raise ProfileError("refused: runner %r: %s" % (t, why))
    if not t:
        raise ProfileError("refused: an empty runner")
    low = t.lower()
    for bad in FORBIDDEN_TEXT:
        if bad in low:
            no("it names %s, which this profile never touches (gates, hooks, settings and "
               "credentials are outside it)" % bad)
    vault = sb.vault_path(h)
    if vault and (vault in t or os.path.realpath(vault) in t):
        no("it names the vault, which a test profile never reaches")
    if t.startswith("-"):
        no("it is an option, not a command")
    if _META.search(t):
        no("shell syntax (; & | < > $ ` ( ) quotes) would chain a second command behind the "
           "approved one")
    try:
        toks = shlex.split(t)
    except ValueError as e:
        no("it does not parse (%s)" % e)
    i = 0
    while i < len(toks) and _ENV.match(toks[i]):
        name = _ENV.match(toks[i]).group(1)
        if name not in ENV_OK:
            no("the variable %s may not be set by a runner (allowed: %s)" % (name, ", ".join(ENV_OK)))
        i += 1
    cmd = toks[i:]
    if not cmd:
        no("only variables, no command")
    first = cmd[0]
    for tok in cmd:
        if "*" in tok or tok.startswith(("/", "~")) or ".." in tok.split("/"):
            no("%r is an absolute, home or parent path or a wildcard; name the command relative to "
               "the project" % tok)
        parts = [x.lower() for x in tok.replace("\\", "/").split("/")]
        base = parts[-1]
        if any(p in FORBIDDEN_DIRS for p in parts) or base == ".env" or base.startswith(".env."):
            no("%r reaches a credential, login or hook location" % tok)
    if first in NEVER_CMD:
        no("%s is never a runner: %s" % (first, NEVER_CMD[first]))
    if first == "git":
        if len(cmd) < 2 or cmd[1] not in GIT_READ:
            no("git is only allowed as one of: %s (never push, config, reset or clean)"
               % ", ".join("git " + g for g in GIT_READ))
    elif first in SHELLS:
        if len(cmd) < 2 or cmd[1].startswith("-") or not cmd[1].endswith(SCRIPT_EXT):
            no("%s with no script file runs any text; name the script (bash tests/run.sh)" % first)
    elif first in INTERPRETERS:
        ok = (len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] in PY_MODULES) \
            or (first == "node" and len(cmd) >= 2 and cmd[1] == "--test") \
            or (len(cmd) >= 2 and not cmd[1].startswith("-") and cmd[1].endswith(SCRIPT_EXT))
        if not ok:
            no("%s with no script file runs any code; name a test module (-m pytest, --test) or "
               "a script" % first)
    elif first == "npm":
        if len(cmd) < 2 or cmd[1] not in ("test", "audit"):
            no("npm is only allowed as npm test or npm audit (npm run, install and exec run "
               "package scripts)")
    return " ".join(toks)


def runner_rule(prefix):
    return "Bash(%s:*)" % prefix


def parse_runners(spec, h=None):
    """A comma list -> the checked prefixes; None -> the default set (each default is checked too)."""
    items = list(DEFAULT_RUNNERS) if spec is None else [x for x in spec.split(",") if x.strip()]
    if not items:
        raise UsageError("--runners needs at least one command")
    out = []
    for x in items:
        p = check_runner(x, h)
        if p not in out:
            out.append(p)
    return out


# --------------------------------------------------------------------- the sandbox check
def check_sandbox(path, h=None):
    """-> the real path of the one folder writes are allowed in, or raises ProfileError."""
    import gt_io_policy as iop
    raw = (path or "").strip()
    if not raw:
        raise ProfileError("refused: --sandbox needs a folder")
    p = os.path.abspath(os.path.expanduser(raw))
    real = os.path.realpath(p)

    def no(rule, why):
        raise ProfileError("refused (%s): --sandbox %s %s" % (rule, raw, why))
    if os.path.dirname(real) == real:
        no("root", "is the whole file system, not a sandbox")
    home = os.path.realpath(sb.home(h))
    if real == home:
        no("home", "is your home folder itself; name a folder inside it")
    vault = iop.vault_root(h)
    if vault and (iop._inside(real, vault) or iop._inside(vault, real)):
        no("vault", "is, or contains, the vault (vault writes keep the write queue)")
    if iop._inside(home, real):
        no("home", "contains your home folder")
    nv = iop._write_never(real, h) or iop._write_never(os.path.join(real, "x"), h)
    if nv:
        no(nv[0], "is not writable by design: %s" % nv[1])
    # a never-folder that lives INSIDE the sandbox: ~/.config would reach ~/.config/gh
    for pre in iop.HOME_NEVER:
        if iop._inside(os.path.join(home, *pre), real):
            no("credential", "contains ~/%s, which is never written" % "/".join(pre))
    if iop.never_reason(real, h):
        no("credential", "is a credential location (%s)" % iop.never_reason(real, h))
    return real


def sandbox_rules(real):
    return [_rule_path(real, "Edit"), _rule_path(real, "Read")]


def _rule_path(real, tool):
    return "%s(%s)" % (tool, sb.rule_path(real, True))


# --------------------------------------------------------------------- on
def parse_until(text):
    import gt_grant
    try:
        kind, secs = gt_grant.parse_duration(text)
    except ValueError as e:
        raise UsageError(str(e))
    if kind != "timed":
        raise UsageError("--until %r: use <N>m or <N>h (at most 7 days); leave it out for no "
                         "expiry" % text)
    return secs


def build_rules(runners, sandbox_real, h=None):
    """-> the rules to add, each judged by gt_lockdown's own rule checks."""
    import gt_lockdown as ld
    rules = [runner_rule(r) for r in runners]
    if sandbox_real:
        rules += sandbox_rules(sandbox_real)
    for r in rules:
        why = ld._judge_rule(r, "allow", "insecure") or (
            "%s would allow a consent-tier op" % r if ld._is_consent_allow(r) else None)
        if why:
            raise ProfileError("refused: " + why)
    return rules


def _confirm_terminal(reason):
    import gt_grant
    if not (sys.stdin and sys.stdin.isatty()) and os.environ.get("GT_GRANT_ALLOW_NO_TTY") != "1":
        raise ProfileError("refused: a testing profile is yours to turn on, and there is no "
                           "terminal here. Run this in your own terminal (or "
                           "gt_settings.py testing on ...).")
    refused = gt_grant._confirm(reason)
    if refused is not None:
        raise ProfileError("refused: turning it on needs a fresh confirmation (gt unlock exit "
                           "%s); nothing changed." % refused)


def _dump(d):
    return json.dumps(d, indent=2) + "\n"


def _diff(path, old_text, new_text):
    return "".join(difflib.unified_diff(
        old_text.splitlines(True), new_text.splitlines(True),
        fromfile=path if old_text else "/dev/null (the file does not exist yet)",
        tofile=path))


def turn_on(scope="project", project_dir=None, runners=None, sandbox=None, until=None,
            dry_run=False, h=None, now=None, out=print):
    if scope not in SCOPES:
        raise UsageError("--scope must be one of: %s" % ", ".join(SCOPES))
    secs = parse_until(until) if until else None
    prefixes = parse_runners(runners, h)
    real = check_sandbox(sandbox, h) if sandbox else None
    rules = build_rules(prefixes, real, h)
    path = settings_for(scope, project_dir, h)
    doc, raw = _load_doc(path)
    t = _now(now)
    st = load_state(h)
    rec = st["profiles"].get(path)
    owned = list(rec["added"]) if rec else []
    lst = sb._get_list(doc, "permissions.allow")
    lst = list(lst) if lst is not None else None
    new_doc = json.loads(json.dumps(doc))
    nl = sb._ensure_list(new_doc, "permissions.allow")
    for e in owned:                                    # a re-run that wants less drops the surplus
        if e not in rules and e in nl:
            nl.remove(e)
    owned = [e for e in owned if e in rules]
    added = []
    for r in rules:
        if r not in nl:
            nl.append(r)
            added.append(r)
    owned += [r for r in added if r not in owned]
    sb._prune_empty(new_doc, "permissions.allow")
    if isinstance(new_doc.get("permissions"), dict) and not new_doc["permissions"]:
        del new_doc["permissions"]
    new_text = _dump(new_doc)
    changed = new_doc != doc or raw is None
    old_text = raw.decode("utf-8") if raw is not None else ""
    if scope == "user":
        out("note: --scope user is broader: the rules reach EVERY project on this machine, not "
            "one repo. The project scope is the default.")
    if dry_run:
        out("dry run: nothing is written. This is the change `on` would make to %s:" % path)
        out(_diff(path, old_text, new_text) if changed else "(no change: every rule is already there)")
        if secs:
            out("it would expire at %s" % _stamp(t + secs))
        return 0
    _confirm_terminal("testing profile on (%s, %d rules)" % (scope, len(rules)))
    expires = t + secs if secs else None
    if changed:
        bak = _backup(path, h)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sb._atomic_write(path, new_text)
        written = _sha(new_text.encode("utf-8"))
    else:
        bak, written = None, (rec or {}).get("written_sha")
    if rec:
        restore = rec.get("restore")
        if _sha(raw or b"") != rec.get("written_sha"):
            restore = None                             # edited since `on`: no exact restore
    else:
        restore = {"existed": raw is not None,
                   "b64": base64.b64encode(raw).decode("ascii") if raw is not None else None}
    new = {
        "scope": scope, "project_dir": os.path.dirname(os.path.dirname(path)) if scope == "project"
        else None, "settings_path": path, "added": owned, "sandbox": real,
        "runners": prefixes, "created_at": (rec or {}).get("created_at", t), "expires_at": expires,
        "written_sha": written, "restore": restore,
    }
    st["profiles"][path] = new
    _save_state(st, h)
    _log({"on": path, "scope": scope, "rules": len(owned), "added": len(added),
          "expires_at": expires}, h)
    out("testing profile: ON (%s, %s), %d rules  (%d added to %s)" % (
        scope, _expiry_text(expires), len(owned), len(added), path))
    if bak:
        out("  settings backed up first: %s" % bak)
    out("  Claude Code applies the rules when it reloads settings; restart it if it still asks.")
    out("  The Core-rule gates are untouched. Turn it off with: gt_testing_profile.py off%s"
        % (" --scope user" if scope == "user" else ""))
    return 0


# --------------------------------------------------------------------- off
def _expiry_text(expires):
    return "no expiry" if expires is None else "expires " + _stamp(expires)


def _stamp(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _end(path, rec, h=None):
    """Take one recorded profile out of its settings file. -> the list of removed rules."""
    cur = _read_bytes(path)
    restore = rec.get("restore")
    removed = []
    if cur is not None and restore and rec.get("written_sha") and _sha(cur) == rec["written_sha"]:
        # still exactly as `on` left it: put the original bytes back
        _backup(path, h)
        removed = list(rec["added"])
        if restore.get("existed"):
            sb._atomic_write(path, base64.b64decode(restore["b64"]).decode("utf-8"))
        else:
            os.unlink(path)
            _rmdir_if_empty(os.path.dirname(path))
        return removed
    if cur is None:
        return removed
    doc, _ = _load_doc(path)
    lst = sb._get_list(doc, "permissions.allow")
    for e in rec["added"]:
        if lst is not None and e in lst:
            lst.remove(e)
            removed.append(e)
    sb._prune_empty(doc, "permissions.allow")
    if isinstance(doc.get("permissions"), dict) and not doc["permissions"]:
        del doc["permissions"]
    if removed:
        _backup(path, h)
        if not doc and not (restore or {}).get("existed", True):
            os.unlink(path)
            _rmdir_if_empty(os.path.dirname(path))
        else:
            sb._atomic_write(path, _dump(doc))
    return removed


def _rmdir_if_empty(d):
    try:
        os.rmdir(d)
    except OSError:
        pass


def turn_off(scope="project", project_dir=None, h=None, out=print):
    path = settings_for(scope, project_dir, h)
    st = load_state(h)
    rec = st["profiles"].get(path)
    if not rec:
        out("testing profile: not on for %s (nothing to change)" % path)
        return 0
    removed = _end(path, rec, h)
    del st["profiles"][path]
    _save_state(st, h)
    _log({"off": path, "removed": len(removed)}, h)
    out("testing profile: off (%s): removed %d rule(s) from %s; hand-edited rules untouched"
        % (rec["scope"], len(removed), path))
    out("  Restart Claude Code for the change to apply.")
    return 0


# --------------------------------------------------------------------- sweep, status
def sweep(h=None, now=None):
    """End every profile whose --until has passed. -> [message]. Removes only; never adds."""
    t = _now(now)
    st = load_state(h)
    msgs = []
    for path, rec in sorted(st["profiles"].items()):
        exp = rec.get("expires_at")
        if exp is not None and t >= exp:
            try:
                n = len(_end(path, rec, h))
            except Exception as e:                       # noqa: BLE001 - say so, keep the record
                msgs.append("testing profile (%s) expired but could not be removed from %s: %s"
                            % (rec.get("scope"), path, e))
                continue
            del st["profiles"][path]
            _log({"expired": path, "removed": n}, h)
            msgs.append("testing profile (%s) expired at %s and was removed: %d rule(s) taken out "
                        "of %s" % (rec.get("scope"), _stamp(exp), n, path))
    if msgs:
        _save_state(st, h)
    return msgs


def profiles(h=None):
    return sorted(load_state(h)["profiles"].items())


def summary_line(rec):
    return "testing profile: ON (%s, %s), %d rules" % (
        rec.get("scope"), _expiry_text(rec.get("expires_at")), len(rec.get("added") or []))


def is_on(h=None, now=None):
    sweep(h, now)
    return bool(load_state(h)["profiles"])


def status_lines(h=None, now=None, sweep_first=True):
    lines = list(sweep(h, now)) if sweep_first else []
    ps = profiles(h)
    if not ps:
        lines.append("testing profile: off")
        return lines
    for path, rec in ps:
        lines.append(summary_line(rec))
        lines.append("  settings: %s" % path)
        if rec.get("sandbox"):
            lines.append("  writes allowed only under: %s" % rec["sandbox"])
        for r in rec.get("added") or []:
            lines.append("  allow %s" % r)
    return lines


def status(h=None, now=None, out=print):
    for l in status_lines(h, now):
        out(l)
    return 0


# --------------------------------------------------------------------- CLI
def _parse(argv):
    """Options are taken by hand: a value such as --dangerously-skip-permissions must reach the
    refusal that names it, not die in an option parser."""
    opts = {"scope": "project", "project_dir": None, "runners": None, "sandbox": None,
            "until": None, "dry_run": False, "hook": False}
    takes = {"--scope": "scope", "--project-dir": "project_dir", "--runners": "runners",
             "--sandbox": "sandbox", "--until": "until"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in takes:
            if i + 1 >= len(argv):
                raise UsageError("%s needs a value" % a)
            opts[takes[a]] = argv[i + 1]
            i += 2
        elif a == "--dry-run":
            opts["dry_run"] = True
            i += 1
        elif a == "--hook":
            opts["hook"] = True
            i += 1
        else:
            raise UsageError("unknown argument %r" % a)
    if opts["scope"] not in SCOPES:
        raise UsageError("--scope must be one of: %s" % ", ".join(SCOPES))
    return opts


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if a else 2
    cmd, rest = a[0], a[1:]
    try:
        if cmd not in ("on", "off", "status", "sweep"):
            raise UsageError("unknown command %r" % cmd)
        o = _parse(rest)
        if cmd == "on":
            return turn_on(o["scope"], o["project_dir"], o["runners"], o["sandbox"], o["until"],
                           o["dry_run"])
        if cmd == "off":
            return turn_off(o["scope"], o["project_dir"])
        if cmd == "status":
            return status()
        msgs = sweep()                                   # sweep
        if o["hook"]:
            if msgs:
                print(json.dumps({"systemMessage": "gt testing profile: " + " ".join(msgs)}))
        else:
            for m in msgs:
                print(m)
        return 0
    except ProfileError as e:
        print(str(e))
        return 1
    except UsageError as e:
        print("usage: %s" % e, file=sys.stderr)
        print("gt_testing_profile.py on|off|status|sweep [--scope project|user] [--project-dir DIR] "
              "[--runners a,b] [--sandbox DIR] [--until 4h] [--dry-run]", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
