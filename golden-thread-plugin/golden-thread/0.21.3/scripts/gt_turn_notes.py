#!/usr/bin/env python3
"""gt_turn_notes.py -- the lines the per-turn hook adds to the model's context (0.21.0).

    gt_turn_notes.py            print the lines the hook would add now (a way to look at them)

inject_core_rules.sh calls lines() after the Core rules. Three settings each add at most one line,
and every one is silent when its setting is off or its data is missing:

  reply_length (#39)         normal adds nothing. brief: answer first, about 5 lines unless asked.
                             terse: answer first, one or two sentences.
  allowance_note (#42)       above allowance_note_threshold (default 90) percent, the last SAVED
                             5-hour and weekly readings with their age, so an answer carries the
                             figure in an app with no status line (Claudian).
  hints_as_choices (#37)     after a gt command, offer 2-3 next steps as an AskUserQuestion.

The allowance reading is the usage module's latest.json (its newest reading, written whole each time
a reading is recorded). The ledger readings.jsonl is the fallback, and only its tail is read: it
grows without bound. A window whose reset time has passed is not quoted -- its figure is history.

This runs on every prompt: it imports only the standard library, never raises and never blocks.
DEFAULTS repeats the registry's defaults so no import of gt_settings is needed on the hot path;
tests/test_gt_turn_notes.py pins them against the registry.
"""
import json
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {"reply_length": "normal", "allowance_note": "on", "allowance_note_threshold": "90",
            "hints_as_choices": "on"}
TAIL_BYTES = 128 * 1024


def _home():
    return os.path.expanduser("~")


def _cfg():
    try:
        with open(os.path.join(_home(), ".claude", "vault-config.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


def _get(cfg, name):
    v = cfg.get(name)
    return str(v).strip().lower() if isinstance(v, str) and v.strip() else DEFAULTS[name]


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _usable(row):
    return isinstance(row, dict) and _num(row.get("ts")) and (
        _num(row.get("five_hour")) or _num(row.get("seven_day")))


def newest_reading(usage_dir=None):
    """The newest saved allowance reading: latest.json, else the tail of readings.jsonl. None when
    there is no usable one. Never raises."""
    d = usage_dir or os.path.join(_home(), ".claude", "golden-thread", "usage")
    try:
        with open(os.path.join(d, "latest.json"), encoding="utf-8") as fh:
            row = json.load(fh)
        if _usable(row):
            return row
    except Exception:                                    # noqa: BLE001
        pass
    try:
        with open(os.path.join(d, "readings.jsonl"), "rb") as fh:
            try:
                fh.seek(-TAIL_BYTES, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    for text in reversed(tail.splitlines()):
        try:
            row = json.loads(text)
        except ValueError:
            continue
        if _usable(row):
            return row
    return None


def age_words(seconds):
    s = max(0, int(seconds))
    if s < 90:
        return "just now"
    if s < 90 * 60:
        return "%d minutes ago" % (s // 60)
    if s < 48 * 3600:
        return "%d hours ago" % (s // 3600)
    return "%d days ago" % (s // 86400)


def reply_length_line(value):
    if value == "brief":
        return ("Reply length (reply_length is brief): answer first, then keep the reply to about "
                "5 lines unless the user asks for more.")
    if value == "terse":
        return ("Reply length (reply_length is terse): answer first, in one or two sentences with "
                "no preamble or recap, unless the user asks for more.")
    return None


def allowance_line(row, threshold, now=None):
    """-> the note, or None when no live window is at or above `threshold` percent."""
    now = time.time() if now is None else now
    if not _usable(row):
        return None
    parts, hot = [], False
    for label, key in (("5-hour", "five_hour"), ("weekly", "seven_day")):
        v = row.get(key)
        if not _num(v):
            continue
        reset = row.get(key + "_resets")
        if _num(reset) and reset < now:
            continue                                     # that window has reset since: history
        parts.append("%s %d%%" % (label, round(v)))
        hot = hot or v >= threshold
    if not hot:
        return None
    return ("Allowance note (allowance_note is on): the user's plan allowance was last saved as %s "
            "(saved %s). Say so in one short sentence in your reply, with that age, so a user "
            "with no status line sees it." % (" and ".join(parts), age_words(now - row["ts"])))


def choices_line():
    return ("Next-step choices (hints_as_choices is on): when you finish a /gt: command or a gt "
            "skill, end your reply with ONE AskUserQuestion offering 2-3 next steps (the user can "
            "also type their own). Get the options by running: python3 %s choices '<the command "
            "you ran>'. Skip it if the command failed or you just asked the user something."
            % os.path.join(HERE, "gt_hints.py"))


def lines(now=None):
    """Every line to add, in order. Each part is guarded: a failure in one drops only that line."""
    out = []
    cfg = _cfg()
    try:
        ln = reply_length_line(_get(cfg, "reply_length"))
        if ln:
            out.append(ln)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        if _get(cfg, "allowance_note") != "off":
            try:
                thr = float(_get(cfg, "allowance_note_threshold"))
            except ValueError:
                thr = float(DEFAULTS["allowance_note_threshold"])
            ln = allowance_line(newest_reading(), thr, now)
            if ln:
                out.append(ln)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        if _get(cfg, "hints_as_choices") != "off":
            out.append(choices_line())
    except Exception:                                    # noqa: BLE001
        pass
    return out


def main(argv):
    for ln in lines():
        print(ln)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:                                    # noqa: BLE001
        sys.exit(0)
