#!/usr/bin/env python3
"""gt_savings.py -- what each gt mechanism actually used vs what it would have used without it.

    gt_savings.py record   [--hook] [--days N] [--projects-dir DIR] [--max-seconds S]
    gt_savings.py ingest-run --since ISO [--session ID ...] [--run ID] [--projects-dir DIR] [--json]
    gt_savings.py note-read --mechanism gt-open|digest --read FILE... --instead FILE...
    gt_savings.py benchmark --task LABEL --with-gt TOKENS --without-gt TOKENS
    gt_savings.py report   [--days N | --since D --until D] [--project ID] [--profile P] [--json]
    gt_savings.py net      [--days N]            # one line: the net figure, for gt-usage
    gt_savings.py export   [--days N] [--out FILE]
    gt_savings.py projects                       # the opaque project ids the ledger holds

THE LEDGER, ~/.claude/golden-thread/usage/savings.jsonl, beside readings.jsonl. Append-only, one
line per mechanism event, on this machine only. It holds numbers and identifiers and nothing
else: no prompt text, no file content, no path, no project name (a project is an 8-character
hash of its transcript directory). It is never written into a vault or a git repo, and no
figure is ever sent anywhere: sharing is `export`, a file the user chooses to hand on.

WHERE THE NUMBERS COME FROM. Claude Code transcripts (~/.claude/projects/**/*.jsonl, subagent
transcripts included) already carry per-request `usage`: input, output, cache read, cache
creation. `record` reads them with the same reader rules as gt_optimize_session.read_turns --
one request is one turn however many streamed rows repeat it, and a half-written last line is
skipped -- plus the output tokens and the tool-result sizes that reader does not keep. It adds
no model call and prints nothing, so it adds no text to a session.

EVERY FIGURE CARRIES ITS VERIFICATION STATE and the report never promotes one silently:
    measured   straight from transcript usage fields, or a head-to-head `benchmark`
               (the Core-rules row applies a benchmarked factor to measured usage: estimated)
    estimated  derived from measured inputs (a counterfactual price, characters / 4)
    design     a claim with no measurement behind it -- shown as a claim, never as a number
A row's state is the WEAKEST state among its events.

UNITS. Savings are in token-equivalents at the session's model: a request's tokens are weighted
by what each kind costs (input 1, output 5, cache read 0.1, cache write 1.25 for five minutes
and 2.0 for an hour -- the multipliers gt_optimize_session uses; output at 5x input is the
published ratio, an assumption here) and scaled by that model's list price over the session
model's. So an agent run on a cheaper model than the session's is a saving even though it used
the same tokens. USD is the same figure at list price: on a subscription (savings_billing subscription,
the default) an equivalent, not a bill; on API billing (savings_billing api) spend avoided. Prices come
from gt_model_prices.json, one table, so a price change re-prices history without re-recording.

PROFILES. `--profile` re-prices the SAME recorded work: inherit prices every run at the session
model, very-high at opus, average at the model its tier (haiku / sonnet / opus) maps to. Only
the counterfactual columns move; the actual columns are read from the ledger and never recomputed.
Effort has no per-call token field, so its effect is not claimed.

Setting `savings_ledger` (on, the default | off) in module.json. Off stops all recording; the
report says so and still shows what was recorded earlier.

Exit: 0 ok | 2 usage | 4 nothing recorded to report.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".claude", "golden-thread", "usage")
LEDGER = os.path.join(BASE, "savings.jsonl")
STATE = os.path.join(BASE, "savings.state.json")
SCHEMA = 1
GT_VERSION = "0.21.0"

def _script_dirs():
    """Where gt's own scripts may be: the hooks dir, this tree's release, the installed cache
    (gt_optimize_session.py is not copied to the hooks dir, only to the plugin cache)."""
    out = [os.path.join(HOME, ".claude", "golden-thread", "hooks"), HERE,
           os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "golden-thread",
                        GT_VERSION, "scripts")]
    cache = os.path.join(HOME, ".claude", "plugins", "cache", "golden-thread-plugin", "gt")
    try:
        vers = sorted((v for v in os.listdir(cache) if v.replace(".", "").isdigit()),
                      key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
        out += [os.path.join(cache, v, "scripts") for v in vers]
    except (OSError, ValueError):
        pass
    return out


for _p in reversed(_script_dirs()):
    if os.path.isdir(_p) and os.path.isfile(os.path.join(_p, "gt_optimize_session.py")) \
            and _p not in sys.path:
        sys.path.insert(0, _p)
for _p in _script_dirs():
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
try:
    import gt_optimize_session as _gos                       # noqa: E402
except Exception:                                            # noqa: BLE001
    _gos = None

W_IN, W_OUT, W_READ, W_5M, W_1H = 1.0, 5.0, 0.1, 1.25, 2.0
CHARS_PER_TOKEN = 4                      # an estimate, and labelled one
STATES = ("measured", "estimated", "design")        # strongest first

# mechanism -> (label, what the counterfactual is). The order is the report's.
MECHANISMS = (
    ("agent-models", "Agent models (task tiers)", "same tokens priced at the session model"),
    ("model-profile", "Model profile per skill", "the skill at the session model"),
    ("gt-open", "gt-open lazy memory load", "every memory file the index lists"),
    ("digest", "Research digest", "the whole research.md"),
    ("agent-isolation", "Agents keeping dumps out of main context",
     "the tool output the agent consumed"),
    ("lotr", "LOTR specialist agents", "direct MCP payload into main context"),
    ("gt-minimize", "gt-minimize / compact timing", "projected re-reads at the pre-cut size"),
    ("expiry-guard", "Cache-expiry guard (cut before re-read)",
     "a cold rebuild of the idle context at cache-write price"),
    ("core-rules", "Core rules (rework avoided)",
     "the session's own work at the benchmarked rules-off rework rate"),
    ("core-rules-wider", "Core rules: leaks, releases, persona work",
     "secret clean-up, release re-runs and persona rework the benchmark did not cover"),
)
DESIGN_NOTES = {
    "model-profile": "no per-skill token record exists yet; the profile's effect is a design figure",
    "gt-minimize": "the cache tokens after a cut are not recorded per session yet; design figure",
    "core-rules-wider": "not benchmarked yet (rules off, 7 of 15 runs leaked a key)",
}
PROFILES = ("average", "very-high", "inherit")
TIER_OF = (("haiku", "haiku"), ("sonnet", "sonnet"), ("opus", "opus"))
# The 2026-10-05 LOTR measurement, the ledger's first measured row (25 Jira queries in one
# session): 382,400 tokens of main context connected directly, 8,545 through LOTR plus a
# specialist agent.
LOTR_SEED = {"key": "seed:lotr-2026-10-05", "mechanism": "lotr", "state": "measured",
             "actual_eq": 8545, "counterfactual_eq": 382400, "note": "25 Jira queries; measured 2026-10-05"}
# The 2026-10-11 Core-rules A/B benchmark: 90 headless runs (opus, sonnet, haiku x three task
# levels x rules on/off x 5), each failed run re-prompted until right, so the figure is the cost
# of a CORRECT result. Per finished task the rules-off work cost 1.262x the rules-on work,
# weighted by the owner's 36-hour request mix; nearly all of it was commits that broke the tests.
# Applied to each main session's own tokens, so the row is `estimated`, and it can only be as
# wide as the benchmark: leaks and persona work are the design row beside it.
# $GT_SAVINGS_CORE_RULES_FACTOR overrides it (a fixture, or a re-run benchmark).
CORE_RULES_REWORK = 0.262


def core_rules_factor():
    try:
        return max(0.0, float(os.environ.get("GT_SAVINGS_CORE_RULES_FACTOR", CORE_RULES_REWORK)))
    except ValueError:
        return CORE_RULES_REWORK


# ---- settings, pricing -----------------------------------------------------------------

def ledger_on():
    """The savings_ledger setting, read from the one config file gt_settings keeps (so this works
    in a hook before the module's settings are registered); anything but "off" is on."""
    try:
        with open(os.path.join(HOME, ".claude", "vault-config.json"), encoding="utf-8") as fh:
            v = json.load(fh).get("savings_ledger")
    except (OSError, ValueError, AttributeError):
        v = None
    return str(v or "on").strip().lower() != "off"


BILLING = ("subscription", "api")


def billing():
    """The savings_billing setting: `api` when tokens are what the account is invoiced for, so a
    USD figure is spend avoided; `subscription` (the default) when they are not, so it is only a
    list-price equivalent. Read from the same config file as savings_ledger."""
    try:
        with open(os.path.join(HOME, ".claude", "vault-config.json"), encoding="utf-8") as fh:
            v = str(json.load(fh).get("savings_billing") or "").strip().lower()
    except (OSError, ValueError, AttributeError):
        v = ""
    return v if v in BILLING else "subscription"


def usd_words():
    """-> how a USD figure is named under the billing setting."""
    return ("USD spend avoided at list price" if billing() == "api"
            else "USD list-price equivalent")


def price_for(model):
    """USD per million INPUT tokens (list price), via the one table; unknown -> the default."""
    if _gos is not None:
        return _gos.price_for(model)[0]
    return 3.0


def tier_of(model):
    m = (model or "").lower()
    for needle, tier in TIER_OF:
        if needle in m:
            return tier
    return "other"


def weighted(u):
    """Price-weighted tokens of one usage dict, at a price of 1 per input token."""
    return (u.get("input", 0) * W_IN + u.get("output", 0) * W_OUT + u.get("cache_read", 0) * W_READ
            + u.get("cache_write_5m", 0) * W_5M + u.get("cache_write_1h", 0) * W_1H)


def raw_tokens(u):
    return (u.get("input", 0) + u.get("output", 0) + u.get("cache_read", 0)
            + u.get("cache_write_5m", 0) + u.get("cache_write_1h", 0))


def short(s, n=8):
    return str(s or "")[:n]


def opaque(s):
    return hashlib.sha256(str(s).encode("utf-8", "replace")).hexdigest()[:8]


# ---- the ledger ------------------------------------------------------------------------

def read_ledger(path=None):
    """-> {key: latest event}. Append-only on disk; the newest line per key wins on read."""
    out = {}
    try:
        with open(path or LEDGER, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("key"):
                    out[ev["key"]] = ev
    except OSError:
        pass
    return out


def append(events):
    if not events:
        return 0
    os.makedirs(BASE, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n")
    return len(events)


def stamp(ev, ts=None):
    ev.setdefault("v", SCHEMA)
    ev.setdefault("gt", GT_VERSION)
    ev["ts"] = ts or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ev


# ---- reading transcripts ----------------------------------------------------------------

def _num(d, k):
    try:
        return max(0, int(d.get(k) or 0))
    except (TypeError, ValueError, AttributeError):
        return 0


def _text_len(content):
    if isinstance(content, str):
        return len(content)
    n = 0
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                if isinstance(b.get("text"), str):
                    n += len(b["text"])
                elif isinstance(b.get("content"), (str, list)):
                    n += _text_len(b["content"])
    return n


def read_file(path, seen, since):
    """-> one record for a transcript file, or None. `seen` dedups API calls across files."""
    rec = {"sid": "", "sidechain": False, "models": {}, "usage": dict.fromkeys(
        ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"), 0),
        "turns": 0, "prompts": 0, "tool_chars": 0, "last_text_chars": 0, "last_ts": None,
        "first_ts": None, "timeline": []}
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue                         # a half-written last line
            if not isinstance(row, dict):
                continue
            rec["sid"] = rec["sid"] or str(row.get("sessionId") or row.get("session_id") or "")
            if row.get("isSidechain"):
                rec["sidechain"] = True
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            ts = _gos._ts(row.get("timestamp")) if _gos else None
            if since is not None and ts is not None and ts < since:
                continue
            if row.get("type") == "user":
                content = msg.get("content")
                if isinstance(content, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            rec["tool_chars"] += _text_len(b.get("content"))
                elif not row.get("isMeta") and not row.get("isSidechain") and _text_len(content):
                    rec["prompts"] += 1
                continue
            if row.get("type") != "assistant":
                continue
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            if not usage:
                continue
            dedup = (row.get("requestId"), msg.get("id"))
            if dedup == (None, None):
                dedup = ("uuid", row.get("uuid"))
            if dedup in seen:
                continue
            seen.add(dedup)
            cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
            w1h, w5m = _num(cc, "ephemeral_1h_input_tokens"), _num(cc, "ephemeral_5m_input_tokens")
            write = _num(usage, "cache_creation_input_tokens")
            if write and not (w1h or w5m):
                w5m = write
            u = rec["usage"]
            u["input"] += _num(usage, "input_tokens")
            u["output"] += _num(usage, "output_tokens")
            u["cache_read"] += _num(usage, "cache_read_input_tokens")
            u["cache_write_5m"] += w5m
            u["cache_write_1h"] += w1h
            rec["turns"] += 1
            m = msg.get("model") or ""
            rec["models"][m] = rec["models"].get(m, 0) + 1
            if ts is not None and m:
                rec["timeline"].append((ts, m))
                if rec["first_ts"] is None or ts < rec["first_ts"]:
                    rec["first_ts"] = ts
            n = _text_len([b for b in (msg.get("content") or [])
                           if isinstance(b, dict) and b.get("type") == "text"])
            if n:
                rec["last_text_chars"] = n
            rec["last_ts"] = ts or rec["last_ts"]
    return rec if rec["turns"] or rec["prompts"] else None


def session_model_at(tl, ts):
    """-> the main session's model at time `ts` (its last reply at or before then), or None.
    Before the session's first reply, its first reply's model."""
    if not tl or ts is None:
        return None
    at = None
    for t, m in tl:
        if t <= ts:
            at = m
        else:
            break
    return at or tl[0][1]


def modal(models):
    return max(models, key=models.get) if models else ""


def project_of(projects_dir, path):
    rel = os.path.relpath(path, projects_dir)
    return opaque(rel.split(os.sep)[0])


def _recent_first(files, since):
    """-> the files written at or after `since`, newest first. A file last written before the
    window opened holds no row inside it, so it is not opened at all."""
    floor = since.timestamp() if since is not None else None
    stamped = []
    for path in files:
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if floor is None or m >= floor:
            stamped.append((m, path))
    return [p for _m, p in sorted(stamped, reverse=True)]


def events_from_transcripts(projects_dir, since, deadline=None):
    """-> (events, files_read). Pure over the transcripts: no ledger, no clock but `since` and
    `deadline` (a time.monotonic() value): once it passes, no further file is opened, and the
    newest files have already been read. At least one file is always read."""
    if _gos is None:
        return [], 0
    files = _recent_first(_gos.transcript_files(projects_dir), since)
    seen, recs, nread = set(), [], 0
    for path in files:
        if nread and deadline is not None and time.monotonic() > deadline:
            break
        nread += 1
        r = read_file(path, seen, since)
        if r:
            r["project"] = project_of(projects_dir, path)
            r["fid"] = opaque(os.path.relpath(path, projects_dir))
            recs.append(r)
    session_model, timeline = {}, {}
    for r in recs:
        if not r["sidechain"]:
            sm = session_model.setdefault(r["sid"], {})
            for m, n in r["models"].items():
                sm[m] = sm.get(m, 0) + n
            timeline.setdefault(r["sid"], []).extend(r.get("timeline") or [])
    for tl in timeline.values():
        tl.sort(key=lambda e: e[0])
    events = []
    per_turn = injection_tokens()
    for r in recs:
        sid8 = short(r["sid"])
        # The counterfactual for an agent is the model the session was on WHEN THE AGENT
        # STARTED, not the session's most common model over the window: a session that moved
        # between Opus and Sonnet made every agent spawned in its Opus hours look like an Opus
        # choice made under Sonnet (2026-10-11: -94M token-equivalents of phantom cost from one
        # session). The modal model is only the fallback when no timestamp places the agent.
        smodel = (session_model_at(timeline.get(r["sid"]), r.get("first_ts"))
                  or modal(session_model.get(r["sid"], {})))
        base = {"session": sid8, "project": r["project"]}
        if not r["sidechain"]:
            if per_turn and r["prompts"]:
                events.append(dict(base, key="overhead:%s:%s" % (sid8, r["fid"]),
                                   mechanism="overhead", state="estimated", turns=r["prompts"],
                                   per_turn_tokens=per_turn, model=modal(r["models"]),
                                   actual_eq=r["prompts"] * per_turn, counterfactual_eq=0))
                # The rules ran in this session, so its work is priced against the rework
                # the benchmark measured without them. The main loop is at its own model.
                w, factor = weighted(r["usage"]), core_rules_factor()
                if w and factor:
                    events.append(dict(base, key="core-rules:%s:%s" % (sid8, r["fid"]),
                                       mechanism="core-rules", state="estimated",
                                       model=modal(r["models"]), factor=factor,
                                       actual_eq=round(w), counterfactual_eq=round(w * (1 + factor))))
            continue
        model = modal(r["models"])
        if smodel and model:
            w = weighted(r["usage"])
            ratio = price_for(model) / price_for(smodel)
            events.append(dict(base, key="agent-models:%s:%s" % (sid8, r["fid"]),
                               mechanism="agent-models", state="estimated", model=model,
                               session_model=smodel, tier=tier_of(model), usage=r["usage"],
                               actual_tokens=raw_tokens(r["usage"]), actual_eq=round(w * ratio),
                               counterfactual_eq=round(w)))
        if r["tool_chars"] and r["last_text_chars"]:
            consumed = r["tool_chars"] // CHARS_PER_TOKEN
            returned = r["last_text_chars"] // CHARS_PER_TOKEN
            events.append(dict(base, key="agent-isolation:%s:%s" % (sid8, r["fid"]),
                               mechanism="agent-isolation", state="estimated", model=model,
                               actual_eq=returned, counterfactual_eq=consumed))
    return events, nread


# ---- measuring gt's own overhead --------------------------------------------------------

def injection_tokens():
    """Tokens one prompt pays for the Core-rule injection, or None when it cannot be measured.

    $GT_SAVINGS_INJECTION_TOKENS overrides (a fixture, or a measured figure). Otherwise the
    installed hook is run once with an empty event and its injected text is counted (characters
    / 4 -- an estimate, so the overhead row is labelled one). Cached by the hook's size+mtime."""
    env = os.environ.get("GT_SAVINGS_INJECTION_TOKENS")
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            return None
    hook = os.path.join(HOME, ".claude", "golden-thread", "hooks", "inject_core_rules.sh")
    try:
        st = os.stat(hook)
    except OSError:
        return None
    sig = "%d:%d" % (st.st_size, int(st.st_mtime))
    state = load_state()
    cached = state.get("injection")
    if isinstance(cached, dict) and cached.get("sig") == sig:
        return cached.get("tokens")
    try:
        r = subprocess.run(["bash", hook], input="{}", capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout)
        ctx = (data.get("hookSpecificOutput") or {}).get("additionalContext") or ""
        tokens = len(ctx) // CHARS_PER_TOKEN
    except Exception:                                        # noqa: BLE001
        return None
    state["injection"] = {"sig": sig, "tokens": tokens}
    save_state(state)
    return tokens


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(d):
    try:
        os.makedirs(BASE, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, STATE)
    except OSError:
        pass


# ---- commands: record --------------------------------------------------------------------

def cmd_record(a):
    if not ledger_on():
        return 0
    projects = a.projects_dir or os.path.join(HOME, ".claude", "projects")
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=a.days)
    started = time.monotonic()
    events, nfiles = events_from_transcripts(projects, since, started + a.max_seconds)
    have = read_ledger()
    new = []
    for ev in events:
        old = have.get(ev["key"])
        if old and all(old.get(k) == ev.get(k) for k in ("actual_eq", "counterfactual_eq", "turns")):
            continue                              # unchanged since last recorded
        new.append(stamp(ev))
        if time.monotonic() - started > a.max_seconds:
            break
    if LOTR_SEED["key"] not in have:
        new.append(stamp(dict(LOTR_SEED, session="", project="")))
    append(new)
    if not a.hook:
        print("savings: %d event(s) recorded from %d transcript file(s)" % (len(new), nfiles))
    return 0


def read_cost(paths):
    """-> (tokens estimate, files readable). Characters / 4; paths are used and never stored."""
    chars, n = 0, 0
    for p in paths:
        try:
            chars += os.path.getsize(p)
            n += 1
        except OSError:
            pass
    return chars // CHARS_PER_TOKEN, n


def cmd_note_read(a):
    if not ledger_on():
        return 0
    if a.mechanism not in ("gt-open", "digest"):
        print("savings: --mechanism must be gt-open or digest", file=sys.stderr)
        return 2
    read, nr = read_cost(a.read)
    instead, ni = read_cost(a.instead)
    if not nr or not ni:
        print("savings: nothing readable to measure", file=sys.stderr)
        return 2
    ev = {"key": "%s:%d" % (a.mechanism, time.time_ns()), "mechanism": a.mechanism,
          "state": "estimated", "session": "", "project": opaque(a.project or ""),
          "files_read": nr, "files_instead": ni, "actual_eq": read, "counterfactual_eq": instead}
    append([stamp(ev)])
    print("savings: %s read %d tokens against %d for the full set" % (a.mechanism, read, instead))
    return 0


def _since(text):
    """An ISO time (naive = local) -> an aware UTC datetime, or None."""
    try:
        d = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d.astimezone(datetime.timezone.utc)


def cmd_ingest_run(a):
    """What the specialist agents of ONE ingest run kept out of the session (0.21.0, item #31).

    The same measurement as the agent-isolation mechanism of `record` -- the tool output a
    subagent consumed (characters / 4) against the result it returned -- restricted to this
    session's subagent transcripts that began at or after the run's start. The events are
    recorded under the keys `record` would give them, so the hook's own pass never counts them twice.
    Prints one JSON object with --json, else the figures; always `estimated`."""
    since = _since(a.since)
    if since is None:
        print("savings: --since must be an ISO time", file=sys.stderr)
        return 2
    projects = a.projects_dir or os.path.join(HOME, ".claude", "projects")
    events, _n = events_from_transcripts(projects, since)
    want = {short(s) for s in (a.session or []) if s}
    iso = [e for e in events if e["mechanism"] == "agent-isolation"
           and (not want or e.get("session") in want)]
    consumed = sum(e["counterfactual_eq"] for e in iso)
    returned = sum(e["actual_eq"] for e in iso)
    recorded = 0
    if iso and ledger_on():
        have = read_ledger()
        new = []
        for ev in iso:
            old = have.get(ev["key"])
            if old and old.get("actual_eq") == ev["actual_eq"] \
                    and old.get("counterfactual_eq") == ev["counterfactual_eq"]:
                continue
            new.append(stamp(dict(ev, ingest_run=opaque(a.run or ""))))
        recorded = append(new)
    out = {"consumed": consumed, "returned": returned, "kept": max(0, consumed - returned),
           "agents": len(iso), "state": "estimated", "recorded": recorded,
           "ledger": "on" if ledger_on() else "off"}
    if a.json:
        print(json.dumps(out, sort_keys=True))
    else:
        print("agents %d, consumed %d, returned %d, kept %d tokens (estimated); %d event(s) recorded"
              % (out["agents"], consumed, returned, out["kept"], recorded))
    return 0


def cmd_benchmark(a):
    """A head-to-head: the without-gt side is MEASURED because the same task was run both ways."""
    ev = {"key": "benchmark:%s:%d" % (opaque(a.task), time.time_ns()), "mechanism": "benchmark",
          "state": "measured", "session": "", "project": "", "task_id": opaque(a.task),
          "actual_eq": a.with_gt, "counterfactual_eq": a.without_gt}
    if ledger_on():
        append([stamp(ev)])
    print("savings: benchmark %s recorded: with gt %d, without %d tokens"
          % (ev["task_id"], a.with_gt, a.without_gt))
    return 0


# ---- the report --------------------------------------------------------------------------

def weakest(states):
    return max(states, key=STATES.index) if states else "design"


def reprice(ev, profile):
    """-> the counterfactual of an event under `profile`; only agent-models events move."""
    if profile is None or ev.get("mechanism") != "agent-models":
        return ev.get("counterfactual_eq", 0)
    u, model, smodel = ev.get("usage") or {}, ev.get("model"), ev.get("session_model")
    w = weighted(u)
    if profile == "inherit":
        target = smodel
    elif profile == "very-high":
        target = "opus"
    else:                                         # average: the tier's own model
        target = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}.get(ev.get("tier"), model)
    return round(w * price_for(target) / price_for(smodel))


def in_window(ev, since, until):
    ts = str(ev.get("ts", ""))[:10]
    return (not since or ts >= since) and (not until or ts <= until)


def summarise(events, profile=None):
    rows = {}
    for ev in events:
        m = ev.get("mechanism")
        r = rows.setdefault(m, {"actual": 0, "cf": 0, "n": 0, "states": [], "usd": 0.0})
        cf = reprice(ev, profile)
        r["actual"] += ev.get("actual_eq", 0)
        r["cf"] += cf
        # USD: the saving at the session model's list price (the default price when unknown)
        r["usd"] += (cf - ev.get("actual_eq", 0)) * price_for(
            ev.get("session_model") or ev.get("model")) / 1e6
        r["n"] += 1
        r["states"].append(ev.get("state", "design"))
    return rows


def build(a):
    ledger = read_ledger()
    since = a.since or (datetime.datetime.now(datetime.timezone.utc)
                        - datetime.timedelta(days=a.days)).strftime("%Y-%m-%d")
    evs = [e for e in ledger.values() if in_window(e, since, a.until)
           and (not a.project or e.get("project") in (a.project, opaque(a.project)))]
    rows = summarise([e for e in evs if e.get("mechanism") != "overhead"
                      and e.get("mechanism") != "benchmark"], a.profile)
    over = summarise([e for e in evs if e.get("mechanism") == "overhead"])
    bench = summarise([e for e in evs if e.get("mechanism") == "benchmark"])
    return since, evs, rows, over.get("overhead"), bench.get("benchmark")


def render(a):
    since, evs, rows, over, bench = build(a)
    lines = ["gt savings ledger -- %s onward%s" % (since, (" (re-priced under %s)" % a.profile)
                                                  if a.profile else ""),
             "recording is %s (setting savings_ledger)" % ("on" if ledger_on() else
                                                           "OFF -- showing what was recorded earlier"),
             "units: token-equivalents at the session model; " + (
                 "USD is spend avoided at list price (savings_billing api)" if billing() == "api"
                 else "USD is list-price, not a bill (savings_billing subscription)"), "",
             "%-42s %12s %14s %12s  %s" % ("mechanism", "actual", "counterfactual", "saved", "state")]
    saved_total, saved_usd, summary = 0, 0.0, []
    for key, label, cf in MECHANISMS:
        r = rows.get(key)
        if not r:
            note = DESIGN_NOTES.get(key, "no events recorded in this window")
            lines.append("%-42s %12s %14s %12s  %s" % (label, "-", "-", "-", "design: " + note))
            summary.append({"mechanism": key, "state": "design"})
            continue
        saved = r["cf"] - r["actual"]
        saved_total += saved
        saved_usd += r["usd"]
        st = weakest(r["states"])
        lines.append("%-42s %12d %14d %12d  %s (%d event%s)"
                     % (label, r["actual"], r["cf"], saved, st, r["n"], "" if r["n"] == 1 else "s"))
        summary.append({"mechanism": key, "actual": r["actual"], "counterfactual": r["cf"],
                        "saved": saved, "usd": round(r["usd"], 4), "state": st, "events": r["n"]})
    over_tokens = over["actual"] if over else 0
    over_usd = -over["usd"] if over else 0.0
    if over:
        lines.append("%-42s %12d %14d %12d  %s (%d event%s) -- a COST"
                     % ("gt overhead (Core-rule injection)", over_tokens, 0, -over_tokens,
                        weakest(over["states"]), over["n"], "" if over["n"] == 1 else "s"))
    else:
        lines.append("%-42s %12s %14s %12s  design: overhead not measured here"
                     % ("gt overhead (Core-rule injection)", "-", "-", "-"))
    net, net_usd = saved_total - over_tokens, saved_usd - over_usd
    cr = rows.get("core-rules")
    cr_net = (cr["cf"] - cr["actual"] - over_tokens) if cr else None
    cr_usd = (cr["usd"] - over_usd) if cr else None
    if cr:
        # the headline a company reads first: what the Core rules are worth net of their own cost
        lines[4:4] = ["Core rules, net of their own cost: %d token-equivalents (%.2f %s), %s"
                      % (cr_net, cr_usd, usd_words(), weakest(cr["states"] + (over["states"] if over else []))), ""]
    lines += ["", "net: %d token-equivalents saved (%.2f %s)%s"
              % (net, net_usd, usd_words(), "" if over else " -- before gt's own overhead, which is not measured"),
              "SessionStart output, report cards and hook context are NOT in the overhead line "
              "(design: not measured)."]
    if bench:
        lines.append("benchmarks (head-to-head, measured): %d run(s), with gt %d, without gt %d"
                     % (bench["n"], bench["actual"], bench["cf"]))
    if not evs:
        lines.append("nothing recorded in this window -- run `gt_savings.py record` or start a session")
    return "\n".join(lines), {"since": since, "net": net, "net_usd": round(net_usd, 4),
                              "billing": billing(), "core_rules_net": cr_net,
                              "core_rules_net_usd": None if cr_usd is None else round(cr_usd, 4),
                              "overhead": over_tokens, "rows": summary,
                              "events": len(evs)}


def cmd_report(a):
    text, data = render(a)
    print(json.dumps(data, indent=1) if a.json else text)
    return 0 if data["events"] else 4


def cmd_net(a):
    a.since = a.until = a.project = a.profile = None
    _, data = render(a)
    print("gt net saving, last %d days: %d token-equivalents (%.2f %s)%s"
          % (a.days, data["net"], data["net_usd"], usd_words(),
             "" if data["events"] else " -- nothing recorded yet"))
    return 0


def cmd_projects(a):
    counts = {}
    for ev in read_ledger().values():
        if ev.get("project"):
            counts[ev["project"]] = counts.get(ev["project"], 0) + 1
    for p, n in sorted(counts.items()):
        print("%s  %d event(s)" % (p, n))
    return 0


def cmd_export(a):
    """A scrubbed summary: per-mechanism totals and states and a bar chart. No session ids, no
    project ids, no task ids, no paths -- only numbers and the mechanism names above."""
    a.project = a.profile = a.since = a.until = None
    _, data = render(a)
    out = ["# gt savings summary", "",
           "Last %d days. Token-equivalents at the session model; %s. Every row carries how it "
           "was established." % (a.days, "USD is spend avoided at list price (API billing)"
                                 if billing() == "api" else "USD is a list-price equivalent, not a bill"), "",
           "| mechanism | actual | counterfactual | saved | state |", "|---|---:|---:|---:|---|"]
    labels = {k: l for k, l, _ in MECHANISMS}
    peak = max([abs(r.get("saved", 0)) for r in data["rows"]] + [1])
    chart = []
    for r in data["rows"]:
        if "saved" in r:
            out.append("| %s | %d | %d | %d | %s |" % (labels[r["mechanism"]], r["actual"],
                                                       r["counterfactual"], r["saved"], r["state"]))
            chart.append("%-42s %s %d" % (labels[r["mechanism"]],
                                          "#" * max(0, round(30 * r["saved"] / peak)), r["saved"]))
        else:
            out.append("| %s | - | - | - | design (not measured) |" % labels[r["mechanism"]])
    out += ["", "gt overhead (Core-rule injection): %d token-equivalents" % data["overhead"],
            "", "**Net: %d token-equivalents** (%.2f %s)" % (data["net"], data["net_usd"], usd_words())]
    if data.get("core_rules_net") is not None:
        out += ["", "**Core rules, net of their own cost: %d token-equivalents** (%.2f %s, estimated)"
                % (data["core_rules_net"], data["core_rules_net_usd"], usd_words())]
    out += ["", "```", *chart, "```"]
    text = "\n".join(out) + "\n"
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("savings: wrote %s" % a.out)
    else:
        sys.stdout.write(text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="gt's measured token savings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record", help="record events from the transcripts (silent with --hook)")
    p.add_argument("--hook", action="store_true")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--projects-dir")
    p.add_argument("--max-seconds", type=float, default=5.0)
    p = sub.add_parser("ingest-run", help="tokens one ingest run's specialist agents kept out of the session")
    p.add_argument("--since", required=True)
    p.add_argument("--session", action="append")
    p.add_argument("--run")
    p.add_argument("--projects-dir")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("note-read", help="record tokens read vs the full set (gt-open, digest)")
    p.add_argument("--mechanism", required=True)
    p.add_argument("--read", nargs="+", required=True)
    p.add_argument("--instead", nargs="+", required=True)
    p.add_argument("--project")
    p = sub.add_parser("benchmark", help="record a head-to-head run of one task, with and without gt")
    p.add_argument("--task", required=True)
    p.add_argument("--with-gt", type=int, required=True)
    p.add_argument("--without-gt", type=int, required=True)
    for n in ("report", "net", "export", "projects"):
        p = sub.add_parser(n)
        p.add_argument("--days", type=int, default=7)
        if n == "report":
            p.add_argument("--since")
            p.add_argument("--until")
            p.add_argument("--project")
            p.add_argument("--profile", choices=PROFILES)
            p.add_argument("--json", action="store_true")
        if n == "export":
            p.add_argument("--out")
    a = ap.parse_args(argv)
    return {"record": cmd_record, "ingest-run": cmd_ingest_run, "note-read": cmd_note_read, "benchmark": cmd_benchmark,
            "report": cmd_report, "net": cmd_net, "export": cmd_export,
            "projects": cmd_projects}[a.cmd](a)


if __name__ == "__main__":
    hook = "--hook" in sys.argv[1:]
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:                                        # noqa: BLE001
        if not hook:
            raise
        sys.exit(0)                                          # never block a session start
