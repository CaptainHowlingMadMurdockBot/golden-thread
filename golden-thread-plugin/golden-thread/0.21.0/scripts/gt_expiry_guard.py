#!/usr/bin/env python3
"""gt_expiry_guard.py -- cut an idle session while its prompt cache is still warm (0.21.0).

    gt_expiry_guard.py arm --hook        Notification idle_prompt: arm one one-shot job
    gt_expiry_guard.py fire <session>    the job: re-check, then compact / fork / notify
    gt_expiry_guard.py backstop --hook   UserPromptSubmit: cancel the job; block a cold resume once
    gt_expiry_guard.py status [--json]   settings, armed jobs, the last actions
    gt_expiry_guard.py cancel [<session> | --all]

Request 2026-10-08-cache-expiry-guard-cut-before-reread. A session idle past its cache lifetime
(5 minutes, or 1 hour on a subscription) pays a full rebuild of its context on the next prompt;
measured on the owner's machine, expiry was 70.7% of all cache writes in a week. Cutting with
`/compact` BEFORE the lapse reads the context at cache price (about 0.1x) instead of writing it
again (1.25x or 2.0x). Verified live on 2026-10-08: `claude -p --resume <id> "/compact ..."` compacts
headlessly and its prefix is read from the cache (a 154K-token context: $0.058 warm, $0.355 cold).

HOW IT RUNS.
  arm       The idle_prompt hook fires about 60 s after Claude finishes with no typing. It measures
            the transcript (context from the last usage row, model, effort, the TTL from which of
            ephemeral_1h / ephemeral_5m the cache was written with), records a state file under
            ~/.claude/golden-thread/expiry-guard/ and schedules ONE job for last activity + TTL -
            margin. One job per idle period: the same transcript does not arm twice.
  fire      The job. Does nothing if the session changed since arming, is below
            expiry_guard.min_tokens, the master is off, or the cache already lapsed. Otherwise,
            by `expiry_guard` and the session being open or closed:
              auto      closed -> headless /compact (same model, effort, cwd)
                        open   -> the SAME compaction on a --fork-session copy + a notification with
                                  the resume command; the live session is never written to
              warn-only a notification only, never a model call   (the shipped default)
              off       nothing at all
  backstop  UserPromptSubmit. ANY prompt cancels an armed job. If the session is large and its
            cache has lapsed and the guard did not cut, the first prompt is blocked once (a blocked
            prompt never reaches the model, so it costs nothing) with the size, the estimated
            rebuild cost and three choices; sending again within 2 minutes goes through.

SAFETY. A session whose transcript changed since arming is never touched. The model and effort are
always the session's own (a different model misses the cache and costs more). Nothing is deleted.
Every automatic action is a line in guard.log. The headless run starts with every hook disabled and
GT_EXPIRY_GUARD_RUNNING=1, so it cannot arm, cancel or block itself.

WHY THE JSON `usage` CANNOT PROVE THE CACHE READ. A /compact call's `usage` reads 0 and the
transcript keeps no usage row for it. The proof is `modelUsage`: cumulative per session, so this
call's cache read is the result's figure minus the last `cost-state` row of the transcript.

SCHEDULING. macOS: a per-session launchd agent with a StartCalendarInterval (Month/Day/Hour/Minute),
bootstrapped by launchctl and removed by the job itself and by cancel; gt_schedule.py is built for
daily/weekly recurring jobs with a fixed table, a one-shot per session does not fit it, so only its
sandbox-safe runner and its recorded interpreter (the one macOS lets read ~/.claude) are reused.
Linux: `systemd-run --user --on-active`, else `at`. Windows: a ONCE task in Task Scheduler.

"OPEN". A live Claude Code process holds the session if the process that ran the arming hook is
still alive with the same start time, or any claude command line names the session id. When the
process table cannot be read the session counts as open: the safe side is a fork, never a write.

Exit codes: always 0 for the hooks (they fail open and silent); status/cancel 0.
"""
import argparse
import datetime
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GT_VERSION = "0.21.0"

# Defaults, in step with gt_settings.SETTINGS (a test pins that they agree). They are repeated here
# because a hook must not import gt_settings: it caches CONFIG at import and costs time.
DEFAULTS = {"mode": "warn-only", "closed": "compact", "open": "fork", "backstop": "on",
            "min_tokens": 150000, "margin_min": "auto", "notify": "desktop"}
CHOICES = {"mode": ("auto", "warn-only", "off"), "closed": ("compact", "notify", "off"),
           "open": ("fork", "notify", "off"), "backstop": ("on", "off"), "notify": ("desktop", "none")}
KEYS = {"mode": "expiry_guard", "closed": "expiry_guard.closed_sessions",
        "open": "expiry_guard.open_sessions", "backstop": "expiry_guard.resume_backstop",
        "min_tokens": "expiry_guard.min_tokens", "margin_min": "expiry_guard.margin_min",
        "notify": "expiry_guard.notify"}

TTL_5M, TTL_1H = 300, 3600
DEFAULT_TTL = TTL_1H                       # a subscription's default, used only when nothing recorded
MARGIN_AUTO_MIN = {TTL_5M: 5, TTL_1H: 10}
MIN_AFTER_IDLE = 150                       # never fire sooner than this after the last reply
RESEND_WINDOW = 120                        # a blocked prompt sent again within this goes through
LOCK_STALE = 900
WRITE_MULT = {TTL_5M: 1.25, TTL_1H: 2.0}   # cache write price / input price, by TTL
READ_MULT = 0.1
EFFORTS = ("low", "medium", "high", "xhigh", "max")
LABEL_PREFIX = "io.goldenthread.gt-expiry-"
KEEP_DAYS = 7
CUT_COMMANDS = ("/compact", "/clear", "/gt:gt-minimize", "/gt-minimize")
COMPACT_INSTRUCTIONS = ("Keep: the task in progress and its next step, decisions made and why, file "
                        "paths and names touched, commands and settings that worked, open questions. "
                        "Drop: tool output already acted on, repeated file dumps, dead ends.")


# ---- clock, paths -----------------------------------------------------------------------------

def clock():
    """GT_EXPIRY_GUARD_NOW (tests) or the wall clock."""
    v = os.environ.get("GT_EXPIRY_GUARD_NOW")
    try:
        return float(v) if v else time.time()
    except ValueError:
        return time.time()


def home():
    return os.path.expanduser("~")


def base_dir():
    return os.path.join(home(), ".claude", "golden-thread", "expiry-guard")


def state_path(sid):
    return os.path.join(base_dir(), "%s.json" % safe_id(sid))


def block_path(sid):
    return os.path.join(base_dir(), "%s.blocked" % safe_id(sid))


def log_path():
    return os.path.join(base_dir(), "guard.log")


def safe_id(sid):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(sid))[:80]


def short(sid):
    return str(sid)[:8]


def label_for(sid):
    return LABEL_PREFIX + safe_id(sid)[:12]


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def log(event, sid="", **kw):
    """One JSON line per automatic action. Ids are shortened; no prompt text, no file content."""
    try:
        row = dict({"ts": datetime.datetime.fromtimestamp(clock(), datetime.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"), "event": event, "session": short(sid)}, **kw)
        os.makedirs(base_dir(), exist_ok=True)
        with open(log_path(), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


# ---- settings ---------------------------------------------------------------------------------

def settings():
    """-> the seven settings, from vault-config.json (read directly: it is a hook). Anything
    unreadable or invalid falls back to the registered default."""
    cfg = read_json(os.path.join(home(), ".claude", "vault-config.json")) or {}
    out = dict(DEFAULTS)
    for k, name in KEYS.items():
        v = cfg.get(name)
        if not isinstance(v, str):
            continue
        v = v.strip().lower()
        if k in CHOICES:
            if v in CHOICES[k]:
                out[k] = v
        elif k == "min_tokens":
            if v.isdigit() and int(v) >= 1:
                out[k] = int(v)
        elif k == "margin_min":
            if v == "auto" or (v.isdigit() and int(v) >= 0):
                out[k] = v
    return out


def ledger_on():
    cfg = read_json(os.path.join(home(), ".claude", "vault-config.json")) or {}
    return str(cfg.get("savings_ledger") or "on").strip().lower() != "off"


# ---- running things (every outside effect goes through one of these, so tests can stub it) ----

def sandboxed():
    return bool(os.environ.get("GT_TEST_SANDBOX"))


class _Refused:
    returncode, stdout = 126, ""

    def __init__(self, what):
        self.stderr = "refused: %s from a test/sandbox (GT_TEST_SANDBOX set)" % what


def run_cmd(args, cwd=None, env=None, timeout=600):
    """Run claude or a notifier. A sandbox/test never starts either."""
    if sandboxed():
        return _Refused(" ".join(str(a) for a in args[:2]))
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, cwd=cwd, env=env,
                          timeout=timeout)


def _schedule_module():
    for p in (HERE, os.path.join(home(), ".claude", "golden-thread", "hooks")):
        if os.path.isfile(os.path.join(p, "gt_schedule.py")):
            if p not in sys.path:
                sys.path.insert(0, p)
            try:
                import gt_schedule
                return gt_schedule
            except Exception:                                   # noqa: BLE001
                return None
    return None


def sched_run(args, timeout=60, input=None, **_kw):
    """The scheduler runner. gt_schedule's refuses every state-changing scheduler verb under
    GT_TEST_SANDBOX; mine says the same for the verbs it adds (systemd-run, at, atrm)."""
    if sandboxed():
        return _Refused(" ".join(str(a) for a in args[:3]))
    gs = _schedule_module()
    if gs is not None:
        return gs.run(args, timeout=timeout, input=input)
    try:
        return subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout,
                              input=input)
    except (OSError, subprocess.SubprocessError) as exc:
        return _Refused(str(exc))


def have(tool):
    return shutil.which(tool) is not None


def platform_kind():
    forced = os.environ.get("GT_EXPIRY_GUARD_PLATFORM")
    if forced in ("macos", "linux", "windows"):
        return forced
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def interpreter():
    gs = _schedule_module()
    try:
        return gs.interpreter() if gs else (sys.executable or "python3")
    except Exception:                                           # noqa: BLE001
        return sys.executable or "python3"


def find_claude():
    p = shutil.which("claude")
    if p:
        return p
    for c in (os.path.join(home(), ".local", "bin", "claude"), "/usr/local/bin/claude",
              "/opt/homebrew/bin/claude"):
        if os.path.isfile(c):
            return c
    return "claude"


def notify(title, body, **_kw):
    """A desktop notification, best effort. macOS osascript; Linux notify-send; Windows a balloon."""
    kind = platform_kind()
    try:
        if kind == "macos":
            script = 'display notification %s with title %s' % (_osa(body), _osa(title))
            run_cmd(["osascript", "-e", script], timeout=15)
        elif kind == "linux":
            if have("notify-send"):
                run_cmd(["notify-send", title, body], timeout=15)
        else:
            ps = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object "
                  "System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
                  "$n.Visible = $true; $n.ShowBalloonTip(15000, '%s', '%s', 'Info'); Start-Sleep 16"
                  % (title.replace("'", "''"), body.replace("'", "''")))
            run_cmd(["powershell", "-NoProfile", "-Command", ps], timeout=30)
    except Exception:                                           # noqa: BLE001
        pass


def _osa(s):
    return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def say(title, body, cfg):
    if cfg.get("notify") == "desktop":
        notify(title, body)


# ---- processes: is the session open? -----------------------------------------------------------

def ps_table():
    """-> [{pid, ppid, cmd}] or None when the process table cannot be read (Windows, no ps)."""
    if os.name == "nt" or not shutil.which("ps"):
        return None
    try:
        r = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True,
                           timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)$", line)
        if m:
            out.append({"pid": int(m.group(1)), "ppid": int(m.group(2)), "cmd": m.group(3)})
    return out


def pid_start(pid):
    """The start time string of a pid (identifies it across pid reuse), or None."""
    if os.name == "nt":
        return None
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    s = r.stdout.strip()
    return s if r.returncode == 0 and s else None


def is_claude(cmd):
    toks = str(cmd).split()
    if not toks:
        return False
    exe = os.path.basename(toks[0]).lower()
    if exe in ("claude", "claude.exe"):
        return True
    return exe.startswith("node") and any("claude-code" in t for t in toks[1:3])


def claude_ancestor(table=None):
    """-> (pid, start, binary) of the Claude Code process above this hook, or (None, None, None)."""
    table = ps_table() if table is None else table
    if not table:
        return None, None, None
    by = {p["pid"]: p for p in table}
    pid, seen = os.getpid(), set()
    while pid in by and pid not in seen:
        seen.add(pid)
        p = by[pid]
        if is_claude(p["cmd"]):
            exe = p["cmd"].split()[0]
            return p["pid"], pid_start(p["pid"]), exe if os.path.isabs(exe) else None
        pid = p["ppid"]
    return None, None, None


def session_open(rec):
    """-> ("open" | "closed", why). Unknown counts as open: the safe side is a fork."""
    table = ps_table()
    if table is None:
        return "open", "process table unreadable"
    pid = rec.get("claude_pid")
    if pid:
        for p in table:
            if p["pid"] == pid and is_claude(p["cmd"]):
                start = rec.get("claude_start")
                if not start or pid_start(pid) == start:
                    return "open", "pid %s is alive" % pid
    sid = rec.get("session_id") or ""
    for p in table:
        if sid and sid in p["cmd"] and is_claude(p["cmd"]):
            return "open", "a claude command line names the session"
    return "closed", "no live claude process holds it"


# ---- the transcript ----------------------------------------------------------------------------

def _ts(raw):
    try:
        return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _n(d, k):
    try:
        return max(0, int(d.get(k) or 0))
    except (TypeError, ValueError, AttributeError):
        return 0


def measure(path):
    """-> {context, model, effort, ttl, last_activity, sig, compacted_after, cost_state} or None.

    context = input + cache read + cache write of the LAST main-chain usage row. ttl is read from
    the newest row that records which cache the write went to (1h or 5m). The signature covers
    message rows only -- Claude Code appends title/mode/last-prompt rows while idle, and those must
    not count as the session changing."""
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return None
    last_a = None                                        # (ts, context, model, effort, uuid)
    ttl = None
    n_msg, last_msg, last_msg_ts, compact_ts, cost = 0, "", None, None, None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue                                   # a half-written last line
            if not isinstance(row, dict):
                continue
            t = row.get("type")
            ts = _ts(row.get("timestamp"))
            if t == "cost-state":
                cost = row
                continue
            if t == "system" and (row.get("subtype") == "compact_boundary" or row.get("compactMetadata")):
                compact_ts = ts
                continue
            if t in ("user", "assistant") and not row.get("isSidechain"):
                if not row.get("isCompactSummary"):
                    n_msg += 1
                    last_msg = str(row.get("uuid") or "")
                    last_msg_ts = ts or last_msg_ts
            if t != "assistant" or row.get("isSidechain"):
                continue
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            model = str(msg.get("model") or "")
            if not usage or model.startswith("<"):
                continue
            ctx = _n(usage, "input_tokens") + _n(usage, "cache_read_input_tokens") \
                + _n(usage, "cache_creation_input_tokens")
            cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
            if _n(cc, "ephemeral_1h_input_tokens"):
                ttl = TTL_1H
            elif _n(cc, "ephemeral_5m_input_tokens"):
                ttl = TTL_5M
            last_a = (ts, ctx, model, str(row.get("effort") or ""), str(row.get("uuid") or ""))
    if last_a is None:
        return None
    if ttl is None:
        v = (os.environ.get("CLAUDE_CODE_PROMPT_CACHE_TTL") or "").strip().lower()
        ttl = TTL_5M if v in ("5m", "300", "5") else DEFAULT_TTL
    ts, ctx, model, effort, uuid = last_a
    last = ts
    if last is None:
        try:
            last = os.path.getmtime(path)
        except OSError:
            last = None
    compacted = bool(compact_ts and (ts is None or compact_ts >= ts))
    if compacted:
        ctx, last = 0, max(compact_ts, last or 0)
    return {"context": ctx, "model": model, "effort": effort, "ttl": ttl, "last_activity": last,
            "compacted_after": compacted,
            "sig": "%d:%s:%s" % (n_msg, last_msg, int(last_msg_ts or 0)), "cost_state": cost}


# ---- pricing, ledger ---------------------------------------------------------------------------

def _prices():
    dirs = [HERE, os.path.join(home(), ".claude", "golden-thread", "hooks")]
    cache = os.path.join(home(), ".claude", "plugins", "cache", "golden-thread-plugin", "gt")
    try:
        dirs += [os.path.join(cache, v, "scripts") for v in sorted(os.listdir(cache), reverse=True)]
    except OSError:
        pass
    for d in dirs:
        doc = read_json(os.path.join(d, "gt_model_prices.json"))
        if doc and isinstance(doc.get("prices"), list):
            try:
                return [(str(n), float(p)) for n, p in doc["prices"]], float(doc.get("default", 3.0))
            except (TypeError, ValueError):
                pass
    return [("opus", 5.0), ("haiku", 1.0), ("sonnet", 3.0)], 3.0


def price_for(model):
    """USD per million input tokens, list price; an unknown model gets the table's default."""
    table, default = _prices()
    m = (model or "").lower()
    for needle, price in table:
        if needle in m:
            return price
    return default


def rebuild_usd(ctx, ttl, model):
    """What the cold rebuild of `ctx` tokens costs at list price (cache write at 1.25x / 2.0x)."""
    return ctx / 1e6 * price_for(model) * WRITE_MULT.get(ttl, 2.0)


def opaque(s):
    return hashlib.sha256(str(s).encode("utf-8", "replace")).hexdigest()[:8]


def record_event(rec, action, now, warm=True, extra=None):
    """Append an `expiry-guard` event to the usage ledger gt_savings reads. Estimated: a cut saves
    (cold rebuild - warm compaction); a warning, a notification or a backstop claims nothing."""
    if not ledger_on():
        return
    ctx, ttl = int(rec.get("context") or 0), int(rec.get("ttl") or DEFAULT_TTL)
    cold = ctx * WRITE_MULT.get(ttl, 2.0)
    cut = action in ("compact", "fork") and warm
    ev = {"v": 1, "gt": GT_VERSION, "mechanism": "expiry-guard", "state": "estimated", "action": action,
          "key": "expiry-guard:%s:%d:%s" % (short(rec.get("session_id")), int(now), action),
          "session": short(rec.get("session_id")),
          "project": opaque(os.path.basename(os.path.dirname(rec.get("transcript_path") or ""))),
          "model": rec.get("model"), "context": ctx, "ttl": ttl,
          "actual_eq": round(ctx * READ_MULT) if cut else round(cold), "counterfactual_eq": round(cold),
          "ts": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    ev.update(extra or {})
    try:
        d = os.path.join(home(), ".claude", "golden-thread", "usage")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "savings.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n")
    except OSError:
        pass


# ---- scheduling --------------------------------------------------------------------------------

def fire_command(sid):
    return [interpreter(), os.path.abspath(__file__), "fire", sid]


def plist_path(sid):
    return os.path.join(home(), "Library", "LaunchAgents", label_for(sid) + ".plist")


def _uid_domain():
    return "gui/%d" % os.getuid() if hasattr(os, "getuid") else "gui/0"


def schedule_once(rec, fire_at):
    """Register a one-shot job for `fire_at`. -> (ok, why). Records scheduler handles in rec."""
    sid, kind = rec["session_id"], platform_kind()
    cmd = fire_command(sid)
    lt = time.localtime(fire_at)
    if kind == "macos":
        path = plist_path(sid)
        doc = {"Label": label_for(sid), "ProgramArguments": cmd, "RunAtLoad": False,
               "StartCalendarInterval": {"Month": lt.tm_mon, "Day": lt.tm_mday, "Hour": lt.tm_hour,
                                         "Minute": lt.tm_min},
               "StandardOutPath": os.path.join(base_dir(), "job.out"),
               "StandardErrorPath": os.path.join(base_dir(), "job.err"),
               "EnvironmentVariables": {"PATH": ":".join(filter(None, [
                   os.path.dirname(rec.get("claude_bin") or ""),
                   "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"]))}}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            os.makedirs(base_dir(), exist_ok=True)
            with open(path, "wb") as fh:
                plistlib.dump(doc, fh)
        except OSError as exc:
            return False, "could not write the launchd job (%s)" % exc
        r = sched_run(["launchctl", "bootstrap", _uid_domain(), path])
        if r.returncode != 0:
            _rm(path)
            return False, "launchctl refused it: %s" % _why(r)
        rec["job"] = {"kind": "launchd", "label": label_for(sid), "plist": path}
        return True, ""
    if kind == "windows":
        name = label_for(sid)
        tr = " ".join('"%s"' % c for c in cmd)
        r = sched_run(["schtasks", "/Create", "/TN", name, "/SC", "ONCE",
                       "/ST", time.strftime("%H:%M", lt), "/SD", time.strftime("%m/%d/%Y", lt),
                       "/TR", tr, "/F"])
        if r.returncode != 0:
            return False, "schtasks refused it: %s" % _why(r)
        rec["job"] = {"kind": "schtasks", "label": name}
        return True, ""
    delay = max(1, int(fire_at - clock()))
    if have("systemd-run") and have("systemctl"):
        unit = "gt-expiry-" + safe_id(sid)[:12]
        r = sched_run(["systemd-run", "--user", "--unit", unit, "--on-active=%ds" % delay,
                       "--timer-property=AccuracySec=1s", "--"] + cmd)
        if r.returncode == 0:
            rec["job"] = {"kind": "systemd", "unit": unit}
            return True, ""
        why = "systemd-run refused it: %s" % _why(r)
    else:
        why = "no systemd-run"
    if have("at"):
        stamp = time.strftime("%Y%m%d%H%M", lt)
        # `at` reads the job from stdin and answers "job N at ..." on stderr
        r = sched_run(["at", "-t", stamp], input=" ".join(shlex.quote(c) for c in cmd) + "\n")
        m = re.search(r"job\s+(\d+)", (r.stderr or "") + (r.stdout or ""))
        if r.returncode == 0 and m:
            rec["job"] = {"kind": "at"}
            rec["at_job"] = m.group(1)
            return True, ""
        why += "; at refused it: %s" % _why(r)
    return False, why


def unschedule(rec):
    """Remove a session's one-shot job. Never raises. The plist goes first: a launchd job that
    boots itself out ends its own process."""
    job = rec.get("job") or {}
    sid, kind = rec.get("session_id") or "", job.get("kind")
    try:
        if kind == "launchd":
            _rm(job.get("plist") or plist_path(sid))
            sched_run(["launchctl", "bootout", "%s/%s" % (_uid_domain(), job.get("label") or label_for(sid))])
        elif kind == "schtasks":
            sched_run(["schtasks", "/Delete", "/TN", job.get("label") or label_for(sid), "/F"])
        elif kind == "systemd":
            for suffix in (".timer", ".service"):
                sched_run(["systemctl", "--user", "stop", job["unit"] + suffix])
        elif kind == "at" and rec.get("at_job"):
            sched_run(["atrm", str(rec["at_job"])])
    except Exception:                                           # noqa: BLE001
        pass


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _why(r):
    return ((getattr(r, "stderr", "") or "").strip() or (getattr(r, "stdout", "") or "").strip()
            or "exit %s" % getattr(r, "returncode", "?"))[:200]


# ---- state records -----------------------------------------------------------------------------

def load_state(sid):
    return read_json(state_path(sid))


def save_state(rec):
    atomic_write(state_path(rec["session_id"]), json.dumps(rec, indent=1, sort_keys=True))


def prune(now):
    """Forget finished records older than a week and unload jobs nothing is armed for."""
    try:
        names = os.listdir(base_dir())
    except OSError:
        return
    for n in names:
        if not n.endswith(".json"):
            continue
        rec = read_json(os.path.join(base_dir(), n))
        if not rec:
            continue
        if rec.get("status") != "armed" and now - float(rec.get("armed_at") or 0) > KEEP_DAYS * 86400:
            _rm(os.path.join(base_dir(), n))
            _rm(os.path.join(base_dir(), n[:-5] + ".blocked"))


# ---- arm ---------------------------------------------------------------------------------------

def margin_seconds(cfg, ttl):
    m = cfg.get("margin_min")
    mins = MARGIN_AUTO_MIN.get(ttl, 10) if m == "auto" else int(m)
    # A margin as long as a 5 minute TTL would fire at the last reply, before the idle hook has even
    # run. Cap it so the job fires at least MIN_AFTER_IDLE seconds after the last reply.
    return max(0, min(mins * 60, ttl - MIN_AFTER_IDLE))


def arm(payload, now=None):
    """The idle_prompt hook. -> {armed, why, ...}. Never raises."""
    now = clock() if now is None else now
    try:
        return _arm(payload or {}, now)
    except Exception as exc:                                    # noqa: BLE001
        log("arm-error", (payload or {}).get("session_id", ""), error=type(exc).__name__)
        return {"armed": False, "why": "error: %s" % type(exc).__name__}


def _arm(p, now):
    if os.environ.get("GT_EXPIRY_GUARD_RUNNING"):
        return {"armed": False, "why": "the guard's own run"}
    nt = p.get("notification_type")
    if nt and nt != "idle_prompt":
        return {"armed": False, "why": "not an idle_prompt"}
    sid, path = str(p.get("session_id") or ""), p.get("transcript_path")
    cfg = settings()
    if cfg["mode"] == "off":
        return {"armed": False, "why": "off"}
    if not sid or not path:
        return {"armed": False, "why": "no session or transcript"}
    m = measure(path)
    if not m or m["last_activity"] is None:
        return {"armed": False, "why": "no transcript"}
    if m["compacted_after"] or m["context"] < cfg["min_tokens"]:
        return {"armed": False, "why": "below-threshold"}
    last, ttl = m["last_activity"], m["ttl"]
    old = load_state(sid)
    if old and old.get("status") == "armed" and old.get("sig") == m["sig"]:
        return {"armed": False, "why": "already armed for this idle period"}
    if old and old.get("status") == "armed":
        unschedule(old)                                          # the session moved on since
    if now >= last + ttl:
        return {"armed": False, "why": "the cache has already lapsed"}
    fire_at = max(last + ttl - margin_seconds(cfg, ttl), now + 20)
    if fire_at >= last + ttl:
        return {"armed": False, "why": "too late to cut"}
    pid, start, binary = claude_ancestor()
    rec = {"session_id": sid, "transcript_path": path, "cwd": p.get("cwd") or "", "model": m["model"],
           "effort": m["effort"], "ttl": ttl, "context": m["context"], "sig": m["sig"],
           "last_activity": last, "armed_at": now, "fire_at": fire_at, "status": "armed",
           "claude_pid": pid, "claude_start": start,
           "claude_bin": binary or find_claude(),
           "ttl_env": os.environ.get("CLAUDE_CODE_PROMPT_CACHE_TTL") or ""}
    ok, why = schedule_once(rec, fire_at)
    if not ok:
        log("schedule-failed", sid, why=why)
        return {"armed": False, "why": "could not schedule: " + why}
    save_state(rec)
    prune(now)
    log("armed", sid, fire_at=int(fire_at), context=m["context"], ttl=ttl, mode=cfg["mode"])
    return {"armed": True, "fire_at": fire_at, "why": ""}


# ---- fire --------------------------------------------------------------------------------------

def _cost_baseline(m, model):
    cs = (m or {}).get("cost_state") or {}
    use = (cs.get("modelUsage") or {}).get(model) or {}
    return {"read": _n(use, "cacheReadInputTokens"), "write": _n(use, "cacheCreationInputTokens"),
            "cost": cs.get("totalCostUSD") if isinstance(cs.get("totalCostUSD"), (int, float)) else None,
            "known": bool(use)}


def _parse_result(out):
    for text in (out, (out or "").strip().splitlines()[-1] if (out or "").strip() else ""):
        try:
            d = json.loads(text)
            if isinstance(d, dict):
                return d
        except ValueError:
            continue
    return None


def headless_compact(rec, m, fork):
    """Run `claude -p --resume <id> [--fork-session] "/compact ..."` with the session's own model,
    effort and cwd. -> dict(ok, session_id, cache_read, warm, cost_usd, why)."""
    args = [rec.get("claude_bin") or find_claude(), "-p", "--model", rec["model"],
            "--output-format", "json", "--settings", '{"disableAllHooks": true}', "--resume",
            rec["session_id"]]
    if fork:
        args.append("--fork-session")
    if rec.get("effort") in EFFORTS:
        args += ["--effort", rec["effort"]]
    args.append("/compact " + COMPACT_INSTRUCTIONS)
    env = dict(os.environ, GT_EXPIRY_GUARD_RUNNING="1")
    if rec.get("ttl_env"):
        env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = rec["ttl_env"]
    if rec.get("claude_bin"):
        env["PATH"] = os.path.dirname(rec["claude_bin"]) + os.pathsep + env.get("PATH", "")
    try:
        r = run_cmd(args, cwd=rec.get("cwd") or None, env=env, timeout=900)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "why": "could not start claude (%s)" % type(exc).__name__}
    doc = _parse_result(r.stdout)
    if r.returncode != 0 or not doc or doc.get("is_error") or doc.get("local_command") != "compact":
        return {"ok": False, "why": "claude did not run /compact headless: %s"
                % (_why(r) if r.returncode != 0 else "unexpected result")}
    new = str(doc.get("session_id") or "")
    if fork and (not new or new == rec["session_id"]):
        return {"ok": False, "why": "the fork did not get a new session id"}
    after = (doc.get("modelUsage") or {}).get(rec["model"]) or {}
    base = _cost_baseline(m, rec["model"])
    out = {"ok": True, "session_id": new, "cache_read": None, "warm": None, "cost_usd": None, "why": ""}
    if base["known"] and after:
        # modelUsage is cumulative per session; a fork starts from its own total
        read, cost_before = _n(after, "cacheReadInputTokens"), base["cost"]
        out["cache_read"] = read - base["read"] if read >= base["read"] else read
        out["warm"] = out["cache_read"] > 0
        if isinstance(doc.get("total_cost_usd"), (int, float)) and cost_before is not None:
            out["cost_usd"] = round(doc["total_cost_usd"] - cost_before, 6) \
                if doc["total_cost_usd"] >= cost_before else doc["total_cost_usd"]
    return out


def _lock(sid):
    p = os.path.join(base_dir(), "%s.lock" % safe_id(sid))
    os.makedirs(base_dir(), exist_ok=True)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
        os.close(fd)
        return p
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(p) > LOCK_STALE:
                os.utime(p)
                return p
        except OSError:
            pass
        return None


def fire(sid, now=None):
    """The one-shot job. -> {action, why, ...}. Never raises."""
    now = clock() if now is None else now
    lock = _lock(sid)
    if lock is None:
        return {"action": "skip", "why": "busy"}
    try:
        return _fire(sid, now)
    except Exception as exc:                                    # noqa: BLE001
        log("fire-error", sid, error=type(exc).__name__)
        return {"action": "skip", "why": "error: %s" % type(exc).__name__}
    finally:
        _rm(lock)


def _finish(rec, status, action, why, **extra):
    rec["status"] = status
    rec["finished_at"] = clock()
    rec.update({k: v for k, v in extra.items() if k in ("fork_id", "cache_read")})
    save_state(rec)
    unschedule(rec)                                              # last: a launchd job ends itself here
    return dict({"action": action, "why": why}, **extra)


def _skip(rec, status, why):
    log("skip", rec["session_id"], why=why)
    return _finish(rec, status, "skip", why)


def _name(rec):
    return os.path.basename((rec.get("cwd") or "").rstrip("/\\")) or "session"


def _k(n):
    return "%dK" % round(n / 1000)


def _fire(sid, now):
    rec = load_state(sid)
    if not rec or rec.get("status") != "armed":
        return {"action": "skip", "why": "not-armed"}
    cfg = settings()
    if cfg["mode"] == "off":
        return _skip(rec, "off", "off")
    m = measure(rec["transcript_path"])
    if not m:
        return _skip(rec, "gone", "no-transcript")
    if m["sig"] != rec.get("sig"):
        return _skip(rec, "changed", "changed")
    if m["last_activity"] is None or now >= m["last_activity"] + rec["ttl"]:
        return _skip(rec, "expired", "expired")
    if m["compacted_after"] or m["context"] < cfg["min_tokens"]:
        return _skip(rec, "below", "below-threshold")
    rec["context"] = m["context"]
    left = max(0, int((m["last_activity"] + rec["ttl"] - now) // 60))
    usd = rebuild_usd(m["context"], rec["ttl"], rec["model"])
    name = _name(rec)

    if cfg["mode"] == "warn-only":
        say("Prompt cache about to expire",
            "%s: %s tokens, the cache lapses in about %d min. Run /compact now to avoid a rebuild "
            "of roughly $%.2f." % (name, _k(m["context"]), left, usd), cfg)
        record_event(rec, "warn", now)
        log("warn", sid, context=m["context"], minutes_left=left)
        return _finish(rec, "warned", "warn", "warn-only")

    state, why_open = session_open(rec)
    policy = cfg["open"] if state == "open" else cfg["closed"]
    log("decide", sid, state=state, why=why_open, policy=policy)
    if policy == "off":
        return _finish(rec, "none", "none", "policy-off")
    if policy == "notify":
        say("Prompt cache about to expire",
            "%s: %s tokens, the cache lapses in about %d min. Run /compact now to avoid a rebuild "
            "of roughly $%.2f." % (name, _k(m["context"]), left, usd), cfg)
        record_event(rec, "notify", now)
        return _finish(rec, "notified", "notify", "policy-notify")

    fork = policy == "fork"
    again = measure(rec["transcript_path"])
    if not again or again["sig"] != rec["sig"]:                   # last look before touching it
        return _skip(rec, "changed", "changed")
    res = headless_compact(rec, m, fork)
    if not res["ok"]:
        say("Cache guard could not compact", "%s: %s. Nothing was changed; run /compact yourself."
            % (name, res["why"]), cfg)
        record_event(rec, "notify", now, extra={"failed": True})
        log("compact-failed", sid, why=res["why"])
        return _finish(rec, "failed", "notify", "compact-failed")
    warm = res["warm"] is not False
    extra = {"cache_read": res["cache_read"]}
    if fork:
        say("Compacted copy ready",
            "A compacted copy of %s is ready: claude -r %s. The open session was not touched."
            % (name, res["session_id"]), cfg)
    else:
        say("Session compacted while the cache was warm",
            "%s (%s tokens) was compacted. Resume as usual: claude -r %s"
            % (name, _k(m["context"]), sid), cfg)
    record_event(rec, "fork" if fork else "compact", now, warm=warm,
                 extra={"cache_read": res["cache_read"], "cost_usd": res["cost_usd"]})
    log("fork" if fork else "compact", sid, cache_read=res["cache_read"], warm=res["warm"],
        cost_usd=res["cost_usd"], new_session=short(res["session_id"]) if fork else "")
    if fork:
        extra["fork_id"] = res["session_id"]
    return _finish(rec, "forked" if fork else "cut", "fork" if fork else "compact", "",
                   warm=res["warm"], cost_usd=res["cost_usd"], **extra)


# ---- backstop ----------------------------------------------------------------------------------

def cancel(session=None):
    """Cancel an armed job (one session, or every one). -> how many were cancelled."""
    n = 0
    try:
        names = [safe_id(session) + ".json"] if session else sorted(os.listdir(base_dir()))
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".json"):
            continue
        rec = read_json(os.path.join(base_dir(), name))
        if rec and rec.get("status") == "armed":
            unschedule(rec)
            rec["status"] = "cancelled"
            save_state(rec)
            log("cancelled", rec.get("session_id", ""))
            n += 1
    return n


def backstop(payload, now=None):
    """The UserPromptSubmit hook. Cancels an armed job, then maybe blocks the prompt once.
    -> None (let it through) or {"decision": "block", "reason": ...}. Never raises."""
    now = clock() if now is None else now
    try:
        return _backstop(payload or {}, now)
    except Exception as exc:                                    # noqa: BLE001
        log("backstop-error", (payload or {}).get("session_id", ""), error=type(exc).__name__)
        return None


def _backstop(p, now):
    if os.environ.get("GT_EXPIRY_GUARD_RUNNING"):
        return None
    sid = str(p.get("session_id") or "")
    if sid:
        cancel(sid)                                  # any new prompt: the idle period is over
    cfg = settings()
    if cfg["mode"] == "off" or cfg["backstop"] == "off" or not sid:
        return None
    text = str(p.get("prompt") or "").strip().lower()
    if any(text == c or text.startswith(c + " ") for c in CUT_COMMANDS):
        return None
    prior = read_json(block_path(sid))
    if prior and now - float(prior.get("at") or 0) <= RESEND_WINDOW:
        _rm(block_path(sid))
        log("backstop-passed", sid)
        return None
    m = measure(p.get("transcript_path"))
    if not m or m["last_activity"] is None or m["compacted_after"]:
        return None
    gap = now - m["last_activity"]
    if gap <= m["ttl"] or m["context"] < cfg["min_tokens"]:
        return None
    usd = rebuild_usd(m["context"], m["ttl"], m["model"])
    atomic_write(block_path(sid), json.dumps({"at": now, "context": m["context"]}))
    rec = {"session_id": sid, "transcript_path": p.get("transcript_path"), "model": m["model"],
           "context": m["context"], "ttl": m["ttl"]}
    record_event(rec, "backstop", now)
    log("backstop-blocked", sid, context=m["context"], gap=int(gap))
    ttl_text = "1 hour" if m["ttl"] == TTL_1H else "5 minutes"
    reason = (
        "gt expiry guard: this session holds %s tokens and its prompt cache lapsed %d min ago "
        "(cache lifetime %s). Sending this prompt now rebuilds the whole context, about $%.2f at "
        "list price (estimated). Your prompt was NOT sent. Choose:\n"
        "  1. /compact  -- pay the rebuild once, then every later turn is small\n"
        "  2. /gt:gt-minimize, then /clear  -- keep the few things worth keeping and start fresh\n"
        "  3. send the same prompt again within 2 minutes to continue as it is\n"
        "Turn this check off: gt_settings.py set expiry_guard.resume_backstop off"
        % (_k(m["context"]), max(1, round((gap - m["ttl"]) / 60)), ttl_text, usd))
    return {"decision": "block", "reason": reason}


# ---- status, cli -------------------------------------------------------------------------------

def status(as_json=False):
    cfg = settings()
    recs = []
    try:
        for n in sorted(os.listdir(base_dir())):
            if n.endswith(".json"):
                r = read_json(os.path.join(base_dir(), n))
                if r:
                    recs.append(r)
    except OSError:
        pass
    tail = []
    try:
        with open(log_path(), encoding="utf-8") as fh:
            tail = [x.rstrip() for x in fh.readlines()[-5:]]
    except OSError:
        pass
    if as_json:
        return json.dumps({"settings": cfg, "sessions": recs, "log": tail}, indent=1)
    lines = ["expiry guard: %s (closed sessions: %s, open: %s, backstop: %s, min tokens %d, margin %s, notify %s)"
             % (cfg["mode"], cfg["closed"], cfg["open"], cfg["backstop"], cfg["min_tokens"],
                cfg["margin_min"], cfg["notify"])]
    armed = [r for r in recs if r.get("status") == "armed"]
    lines.append("armed jobs: %d" % len(armed))
    for r in armed:
        lines.append("  %s  %s  fires %s  %sK tokens  ttl %ds" % (
            short(r["session_id"]), _name(r),
            datetime.datetime.fromtimestamp(r["fire_at"]).strftime("%H:%M:%S"),
            round(r["context"] / 1000), r["ttl"]))
    done = [r for r in recs if r.get("status") != "armed"]
    if done:
        lines.append("finished: " + ", ".join("%s=%s" % (short(r["session_id"]), r.get("status")) for r in done[-5:]))
    if tail:
        lines.append("last actions:")
        lines += ["  " + t for t in tail]
    return "\n".join(lines)


def read_payload():
    try:
        raw = sys.stdin.read()
        d = json.loads(raw) if raw.strip() else {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gt_expiry_guard.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    for name in ("arm", "backstop"):
        sp = sub.add_parser(name)
        sp.add_argument("--hook", action="store_true")
    sp = sub.add_parser("fire")
    sp.add_argument("session")
    sp = sub.add_parser("status")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("cancel")
    sp.add_argument("session", nargs="?")
    sp.add_argument("--all", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 0 if (argv and ("--hook" in argv)) else 2
    if a.cmd == "arm":
        arm(read_payload())
    elif a.cmd == "backstop":
        out = backstop(read_payload())
        if out:
            sys.stdout.write(json.dumps(out))
    elif a.cmd == "fire":
        r = fire(a.session)
        print("expiry guard: %s%s" % (r["action"], (" (%s)" % r["why"]) if r.get("why") else ""))
    elif a.cmd == "status":
        print(status(a.json))
    elif a.cmd == "cancel":
        n = cancel(None if a.all or not a.session else a.session)
        print("expiry guard: cancelled %d armed job(s)" % n)
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    # paranoid.signed_manifest (0.21.0): when gt's code is sealed, a hook checks its own files
    # first (gt_integrity_gate: one stat() when the group is off). Only as a hook -- the CLI
    # forms of this script stay usable, and are the repair route.
    try:
        import gt_integrity_gate as _gig
    except ImportError:
        _gig = None
    if _gig is not None:
        _gig.check(__file__, "reporter", when="--hook" in sys.argv)
    sys.exit(main())
