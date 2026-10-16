#!/usr/bin/env python3
"""gt_agent_model.py -- give an ad-hoc Agent spawn a model and effort by its task (0.21.0).

gt picks the model and effort of its OWN agents (gt:extract, gt-lotr:jira, ...): their
definitions carry both. An agent the session spawns ad hoc -- `general-purpose`, `Explore`,
`Plan`, `claude` -- carries neither, so Claude Code runs it on whatever model the session is on
at that moment. On the owner's machine that put build helpers on Opus for a week (2026-10-11:
the savings ledger's single largest row). This PreToolUse hook closes that gap.

  gt_agent_model.py --hook          PreToolUse on Agent|Task: reads the tool input on stdin
  gt_agent_model.py resolve TYPE    print what an un-modelled spawn of TYPE would get

Setting `adhoc_agent_model`:
  task     (default) a spawn that names no model gets its type's tier from the intent pack:
           general-purpose, claude and Plan the balanced tier, Explore the fast tier --
           sonnet medium and haiku with the shipped pack. A per-agent override
           (gt_model_policy.py set --agent general-purpose --model opus
           --effort high) wins.
  require  a spawn that names no model is refused, with the reason, so the session names one
  off      nothing is changed

Left alone, always: a spawn that already names a model; a type gt does not list above (a
plugin's or the user's own agent, whose definition carries its model); `fork` (a fork always
runs on the session's model); and everything when `agent_models` is `session` (the owner chose
the session's model for agents). Any error fails open: the spawn goes ahead unchanged and the
hook prints nothing.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Claude Code's built-in agent types that carry no model of their own -> the intent they get.
# An empty or missing subagent_type is general-purpose (the Agent tool's default).
ADHOC_INTENT = {"": "balanced", "general-purpose": "balanced", "claude": "balanced",
                "Plan": "balanced", "Explore": "fast"}
SETTING = "adhoc_agent_model"
VALUES = ("task", "require", "off")


def _setting(name, default):
    try:
        import gt_settings                                    # noqa: E402
        return gt_settings.get(name) or default
    except Exception:                                         # noqa: BLE001 - fail open
        return default


def resolve(subagent_type):
    """-> (model alias or None, effort or None, source) for an un-modelled spawn of this type.
    (None, None, why) means leave it alone."""
    t = subagent_type or ""
    if t not in ADHOC_INTENT:
        return None, None, "not an ad-hoc type (its definition carries its model)"
    if _setting("agent_models", "task") == "session":
        return None, None, "agent_models is session"
    import gt_model_policy as P                               # noqa: E402
    o = P.model_choices(P.paths(None))["agents"].get(t or "general-purpose")
    if isinstance(o, dict) and o.get("model"):
        return o["model"], o.get("effort"), "override for %s" % (t or "general-purpose")
    intent = ADHOC_INTENT[t]
    return P.intent_settings(intent, None, "intent %s" % intent)


def decide(tool_input, setting):
    """-> hook output dict, or None to stay silent. Pure apart from resolve()."""
    if setting not in ("task", "require") or not isinstance(tool_input, dict):
        return None
    t = tool_input.get("subagent_type") or ""
    if t == "fork" or tool_input.get("model") or t not in ADHOC_INTENT:
        return None
    if setting == "require":
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": (
                "gt (adhoc_agent_model require): name a model for this %s agent -- "
                "model: haiku, sonnet or opus, and an effort if it matters."
                % (t or "general-purpose"))}}
    model, effort, source = resolve(t)
    if not model:
        return None
    new = dict(tool_input)
    new["model"] = model
    if effort and not tool_input.get("effort"):
        new["effort"] = effort
    shown = model + ("/" + new["effort"] if new.get("effort") else "")
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "gt: %s agent runs on %s (%s; adhoc_agent_model task)"
                                    % (t or "general-purpose", shown, source),
        "updatedInput": new}}


def hook(stdin=None):
    try:
        raw = (stdin if stdin is not None else sys.stdin).read()
        payload = json.loads(raw or "{}")
        if payload.get("tool_name") not in ("Agent", "Task"):
            return 0
        out = decide(payload.get("tool_input"), _setting(SETTING, "task"))
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception:                                         # noqa: BLE001 - fail open, silent
        return 0
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--hook"] or argv == ["hook"]:
        return hook()
    if argv[:1] == ["resolve"]:
        t = argv[1] if len(argv) > 1 else "general-purpose"
        model, effort, source = resolve(t)
        print("%s: %s" % (t, ("%s%s  (%s)" % (model, "/" + effort if effort else "", source))
                          if model else "left alone (%s)" % source))
        return 0
    print(__doc__.strip())
    return 0 if argv[:1] in (["-h"], ["--help"]) else 2


if __name__ == "__main__":
    sys.exit(main())
