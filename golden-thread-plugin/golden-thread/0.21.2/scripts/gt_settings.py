#!/usr/bin/env python3
"""Golden Thread settings: one registry, one file, full user control.

Every OPTIONAL behaviour this system performs on its own -- checking component drift
at session start, printing a report card at compact, and whatever is added later --
must be something the user can see and switch off. A system that acts automatically
and cannot be inspected or disabled is not trustworthy, however good its intentions,
and the whole point of Golden Thread is that a mechanism which cannot be verified is
not enforcement.

Settings live in `~/.claude/vault-config.json` beside `vault_path`. That file
already exists on every install and is already read by the hooks, so no new
location is introduced.

## What this registry does NOT cover, and why that is not an oversight

Four hooks run unconditionally and have no entry here. They are not missing; they are
the mechanism that carries and backstops the Core rules themselves:

    inject_core_rules.sh     UserPromptSubmit + SessionStart/compact -- injects the
                             rules. Off, there is no Core tier at all.
    validate_response.sh     Stop -- the Validated backstop for
                             core_timestamp_every_message and
                             core_no_secrets_in_transcript.
    guard_session_claims.sh  PreToolUse -- core_concurrent_session_claim.
    guard_vault_writes.sh    PreToolUse -- core_explicit_vault_target.

A switch here would make them the one thing they must never be: enforcement that the
session being enforced can turn off. That is the same judgement recorded for modules
in gt_components.CORE_ENFORCEMENT_HOOKS (owner decision, 2026-09-14) -- a mechanism a
user can switch off must not be able to take a Core rule's mechanism with it.

This is a claim about SWITCHING, not about visibility. install.sh names every hook it
wires, `gt_doctor.py` reports whether each is registered and current, and the vault's
CLAUDE.md tells the assistant to say the state out loud each session. Removing one is
a deliberate act -- uninstall, or delete its registration from ~/.claude/settings.json
-- and it stays deliberate rather than becoming a config key.

Two guards ARE registered, and the line is not "core versus not". `test_gate` governs
core_test_before_commit because a gate that fires where it cannot be satisfied is a
gate people switch off for everything, so it is better switched off knowingly, per
repo, than bypassed wholesale; `protected_paths` governs guard_protected_paths.sh,
which is tied to no Core rule. (0.18.1 adds a third: `foreign_checkout_guard` governs
guard_foreign_checkout.sh, also tied to no Core rule.)

## Adding a setting

Append one entry to SETTINGS. Nothing else needs to change: `/gt:gt-settings`
renders whatever is registered, validates against `values`, and the reader
helper below gives any script the value with its default applied. An optional
automatic behaviour that is not in this registry is not a setting -- it is an
undocumented behaviour, and that is the thing this file exists to prevent.
"""
import json
import os
import re
import sys

CONFIG = os.path.expanduser("~/.claude/vault-config.json")


def _lockdown_detail():
    """The lockdown chart, from gt_lockdown's own table, so the setting cannot drift from it."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import gt_lockdown
        return (gt_lockdown.table_text() + "\n\n"
                "Changing it writes (or removes) only gt's own allow/deny rules, backs up\n"
                "settings.json first and logs the change; restart Claude Code afterwards.\n"
                "gt never changes the level by itself -- you choose it at install or here.")
    except Exception:                            # noqa: BLE001
        return "strict | trust-local | trust-network | insecure (gt_lockdown.py missing)"

SETTINGS = {
    "allin_timeout": {
        "default": "300",
        "values": ["300", "600", "1200", "1800", "3600"],
        "summary": "Seconds each all-in check may take, in gt-allin and the commit gate.",
        "detail": (
            "300     the default; enough for a typical repo\n"
            "600-3600  for a large repo whose scan takes longer\n"
            "\n"
            "A check that runs past it is COULD NOT RUN, which refuses a commit like any other\n"
            "unknown. The commit gate runs the checks one after another and gives the whole\n"
            "run 12 times this. Until 0.19.1 it was a fixed 300 s, and gt's own repo (29\n"
            "release folders) could never pass the gate."),
    },
    "agent_models": {
        "default": "task",
        "values": ["task", "session"],
        "summary": "Pick each specialist agent's model by its task, not the session's.",
        "detail": (
            "task     each agent stage runs at its spec's tier: classify and draft haiku,\n"
            "         extract and place sonnet, reconcile, verify and generalize opus  (default)\n"
            "session  no model is passed; every agent runs on the session's model\n"
            "\n"
            "Whatever the skills' model profile, so a very-high machine still reads documents\n"
            "on sonnet (owner, 2026-10-02). Per-agent overrides, by stage or job type:\n"
            "gt_model_policy.py set --agent extract --model haiku [--effort low]. Since\n"
            "0.20.1 each stage's agent definition (gt:<stage>) carries model AND effort --\n"
            "classify and draft haiku (no effort), extract and place sonnet medium, the rest\n"
            "opus high -- and changing this setting rewrites them at once."),
    },
    "adhoc_agent_model": {
        "default": "task",
        "values": ["task", "require", "off"],
        "summary": "Give an ad-hoc agent (general-purpose, Explore, Plan) a model by its task, "
                   "not the session's (0.21.0).",
        "detail": (
            "task     an Agent spawn that names no model gets its type's tier from the intent pack:\n"
            "         general-purpose, claude and Plan balanced (sonnet medium), Explore fast\n"
            "         (haiku)  (default)\n"
            "require  a spawn that names no model is refused, with the reason, so the session\n"
            "         names one\n"
            "off      nothing is changed: an ad-hoc agent runs on the session's model\n"
            "\n"
            "gt's own agents (gt:<stage>, gt-lotr:*) carry a model and effort in their\n"
            "definitions; an ad-hoc spawn carries neither, and on the owner's machine build\n"
            "helpers ran on Opus for a week because the session did (2026-10-11). A PreToolUse\n"
            "hook on Agent sets the model through the tool's input. Never touched: a spawn that\n"
            "names a model, a plugin's or your own agent type, a fork, and everything when\n"
            "agent_models is session. Per-type override:\n"
            "gt_model_policy.py set --agent general-purpose --model opus --effort high.\n"
            "See what a type gets: gt_agent_model.py resolve Explore"),
        "group": "Models and cost",
    },
    "vault_hints": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Name up to three vault pages relevant to each prompt (titles only).",
        "detail": (
            "off     nothing is added\n"
            "on      a UserPromptSubmit hook matches the prompt against index.md and adds at\n"
            "        most three '- <title> — <path>' lines  (default)\n"
            "\n"
            "It reads one file, index.md, and never a page body; the session decides whether\n"
            "to open anything. Nothing is added below the match threshold, without a vault,\n"
            "or past its 0.8 s budget. A per-turn cost when on; it shipped off in 0.19.1\n"
            "(request 2026-10-02-prompt-relevant-vault-hints) and on since 0.21.0 (owner,\n"
            "2026-10-08). Turn it off to drop the per-turn cost."),
    },
    "next_step_hints": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "After a reply, show you one 'Next:' line naming the command to type next "
                   "(0.21.0; costs no tokens).",
        "detail": (
            "off     nothing is shown\n"
            "on      a Stop hook shows ONE line to you when a rule matches what just happened or\n"
            "        the vault state, for example:\n"
            "          Next: /gt:gt-close -- saves what this session learned and closes it (...)\n"
            "\n"
            "The rules: a write-back finished (/gt:gt-close), tasks were created\n"
            "(/gt:gt-list tasks mine), handoffs are open (/gt:gt-handle handoff), overdue p1 tasks\n"
            "(/gt:gt-handle task), a large session (/compact), calibration older than\n"
            "model_calibration_remind_days (/gt:gt-calibrate). At most one per reply, the same hint\n"
            "not again within 10 turns, nothing when no rule fits. It goes to you through the\n"
            "hook's systemMessage and never enters the model's context: no tokens, no model call.\n"
            "Every hint ends 'see it run: /gt:gt-help <command>' (request\n"
            "2026-10-08-next-step-and-prompt-hints, 0.21.0)."),
    },
    "prompt_hints": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "When your words describe something a gt command does, show you one 'Tip:' line "
                   "naming it (0.21.0; costs no tokens).",
        "detail": (
            "off     nothing is shown\n"
            "on      a UserPromptSubmit hook matches what you typed against the phrases each\n"
            "        skill lists and shows ONE line to you, for example:\n"
            "          Tip: /gt:gt-create task -- tracks this as a task in the project (...)\n"
            "\n"
            "'remind me to ...' (/gt:gt-create task), 'what's next' (/gt:gt-list tasks mine, or\n"
            "/gt:gt-next once it exists), 'hand this off' (/gt:gt-create handoff), 'we're done with\n"
            "this project' (/gt:gt-complete), 'how much have I used' (/gt-usage:gt-usage),\n"
            "'is everything healthy' (/gt:gt-doctor). Your prompt still goes to Claude unchanged;\n"
            "the tip is advice only. One per prompt, the same tip not again within 10 prompts, and\n"
            "a 0.2 s budget. It goes to you through systemMessage, so it costs no tokens\n"
            "(request 2026-10-08-next-step-and-prompt-hints, 0.21.0)."),
    },
    "model_calibration": {
        "default": "off",
        "values": ["off", "report"],
        "summary": "gt-optimize checks whether each agent stage runs on the right model and effort.",
        "detail": (
            "off     gt-optimize runs exactly as before and spends nothing extra  (default)\n"
            "report  gt-optimize adds a calibration pass: recent runs of each agent stage are\n"
            "        re-run one tier down, one tier up and one effort higher, a zero-context\n"
            "        deep-tier adjudicator judges the outputs, and each stage gets keep,\n"
            "        move-down, move-up or raise-effort with the evidence and the exact\n"
            "        gt_model_policy.py command. Nothing is applied.\n"
            "\n"
            "Spends allowance when on: the sample and the budget are the two settings below,\n"
            "and a pass whose estimate exceeds the budget refuses before running anything\n"
            "(gt_model_calibrate.py; request 2026-10-03-optimize-calibrates-agent-models, 0.21.0)."),
    },
    "model_calibration_sample": {
        "default": "3",
        "values": ["2", "3", "5", "10"],
        "summary": "Recorded runs sampled per agent stage by a calibration pass.",
        "detail": (
            "The newest runs whose prompt was kept; fewer than model_calibration_min_runs and the\n"
            "stage is reported\n"
            "as not calibrated. Each sample costs one run per neighbouring choice plus one\n"
            "adjudication."),
    },
    "model_calibration_budget": {
        "default": "2",
        "values": ["0.5", "1", "2", "5", "10", "25", "unlimited"],
        "summary": "Most a calibration pass may spend, in USD at list price (estimated first); "
                   "`unlimited` removes the dollar cap, never the weekly-allowance line.",
        "detail": (
            "The pass prices every run before it starts and refuses, running nothing, when\n"
            "the estimate exceeds this."),
    },
    "pipeline_keep_prompts": {
        "default": "on",
        "values": ["on", "off"],
        "summary": "Keep each pipeline agent's prompt beside its packet, on every route, so model "
                   "calibration has runs to sample (0.21.0).",
        "detail": (
            "on   the per-agent route keeps the prompt it gave each unit (packet --prompt-file),\n"
            "     as the workflow route always has: <run>/prompts/<stage>/<unit>.md in the vault's\n"
            "     pipeline spool  (default)\n"
            "off  the per-agent route keeps none (the workflow route still needs its prompt files)\n"
            "Only prompts gt rendered are kept; they already passed the intake scan."),
    },
    "pipeline_prompt_keep": {
        "default": "28d",
        "values": ["7d", "14d", "28d", "60d", "90d", "10", "20", "50", "100", "all"],
        "summary": "How long kept pipeline prompts are held: an age (28d by default), or a count per "
                   "stage, newest first (0.21.0).",
        "detail": (
            "Kept prompts feed model calibration (pipeline_keep_prompts). Each time a packet is\n"
            "recorded, the prompts that are out of the window are deleted from the pipeline spool:\n"
            "  28d (7d, 14d, 60d, 90d)  anything older than that goes. Whatever the age, at most 200\n"
            "                           per stage are kept, newest first  (28d is the default)\n"
            "  10 .. 100                the newest that many per stage, across all runs\n"
            "  all                      every one (the spool then grows without limit)\n"
            "A prompt whose unit has no packet yet is never deleted. Calibration spreads its samples\n"
            "across what is kept, so a longer window is a more representative sample."),
        "plain": {
            "intro": (
                "To judge whether gt's helper agents run on the right model, gt keeps the instructions it "
                "gave them for a while and later re-tests a few of them. This is how long it keeps them."),
            "options": {
                "7d": {"summary": "One week of history."},
                "14d": {"summary": "Two weeks of history."},
                "28d": {"summary": "Four weeks of history (the default); never more than 200 per kind of helper."},
                "60d": {"summary": "Two months of history; never more than 200 per kind of helper."},
                "90d": {"summary": "Three months of history; never more than 200 per kind of helper."},
                "10": {"summary": "The newest 10 per kind of helper, however old."},
                "20": {"summary": "The newest 20 per kind of helper, however old."},
                "50": {"summary": "The newest 50 per kind of helper, however old."},
                "100": {"summary": "The newest 100 per kind of helper, however old."},
                "all": {"summary": "Keep every one; the folder grows without limit."},
            },
            "recommended": "28d",
            "not_sure": "Leave it at 28d. A month of history gives a fair sample and the folder stays small.",
            "change_later": "Run /gt:gt-settings pipeline_prompt_keep <value> in Claude (or gt_settings.py set pipeline_prompt_keep <value>).",
        },
    },
    "model_calibration_max_weekly_pct": {
        "default": "90",
        "values": ["50", "70", "80", "90", "95", "100"],
        "summary": "A calibration pass will not start, and stops part-way, once the weekly "
                   "allowance is at or past this percent (0.21.0).",
        "detail": (
            "Read from gt-usage's newest allowance reading (statusline); with no reading in\n"
            "the last 6 hours the pass is not refused. A pass stopped part-way keeps the\n"
            "results it has; score them with gt_model_calibrate.py score <plan-dir>.\n"
            "With budget `unlimited`, an estimate over 5 USD also asks you at a terminal."),
    },
    "model_calibration_runner": {
        "default": 'claude -p --model {model} {effort_args} --no-session-persistence --strict-mcp-config --tools {tools} --allowedTools {tools} --settings \'{"disableAllHooks":true}\'',
        "values": None,
        "validate": "one command line containing {model}; no shell operators (; | & > < `  $()",
        "summary": "The command a calibration pass runs each candidate with, so effort is calibrated "
                   "too (0.21.0; set to empty for the Agent-tool route, which cannot set effort).",
        "detail": (
            "Given the prompt on stdin; {model}, {effort}, {effort_args} and {tools} are filled in.\n"
            "{tools} is the stage's own tool list (what its agent definition carries), so each\n"
            "candidate can read what the real agent reads.\n"
            "Shipped as the default:\n"
            "  claude -p --model {model} {effort_args} --no-session-persistence --strict-mcp-config --tools {tools} --allowedTools {tools} --settings '{\"disableAllHooks\":true}'\n"
            "Set it to empty (`set model_calibration_runner \"\"`) and the pass writes a plan and the skill spawns each unit with the\n"
            "Agent tool, which takes a model but no effort, so effort is not calibrated.\n"
            "A command that runs on your machine: a security setting -- with gt unlock on it needs\n"
            "a fresh confirmation, and an agent cannot change it."),
    },
    "model_calibration_min_runs": {
        "default": "3",
        "values": ["2", "3", "5", "10"],
        "summary": "Fewer recorded runs than this and an agent stage gets \"not enough runs\" "
                   "instead of a verdict (0.21.0).",
        "detail": (
            "Raise it for verdicts on more evidence; a stage below it is listed as not\n"
            "calibrated, with how many runs it has and how many it needs."),
    },
    "model_calibration_remind_days": {
        "default": "60",
        "values": ["0", "30", "60", "90"],
        "summary": "Days after which the report card suggests re-running calibration "
                   "(0 = never; 0.21.0).",
        "detail": (
            "Models change and so does your work: after this many days since the last pass, or\n"
            "when a newer model has shipped since, the report card adds one line suggesting\n"
            "/gt:gt-optimize with calibration. It never runs it."),
    },
    "component_updates": {
        "default": "report",
        "values": ["off", "report", "confirm", "auto"],
        "summary": "What to do when INSTALLED hooks/scripts differ from what is checked in.",
        "detail": (
            "off     nothing is checked\n"
            "report  print the drift, change nothing  (default)\n"
            "confirm print the drift and the exact command to apply it\n"
            "auto    apply stale/missing silently\n"
            "\n"
            "`auto` never overwrites a file where the INSTALLED copy is newer than the\n"
            "source, and never deletes one absent from the source. On 2026-08-29 the real\n"
            "drift ran that direction: a naive updater would have reverted the timestamp\n"
            "validator and deleted the claim guard. Note these files EXECUTE on every\n"
            "prompt and the plugin source sits in a cloud-synced folder, so `auto` means a\n"
            "sync from another machine can change what runs here."),
    },
    "version_check": {
        "default": "report",
        "values": ["off", "report"],
        "summary": "Check at session start whether a newer plugin version is checked in.",
        "detail": (
            "off     no checking\n"
            "report  name the newer version and how to install it  (default)\n"
            "\n"
            "`component_updates` asks whether the installed FILES match a given version.\n"
            "This asks whether that version is still the newest one available -- the axis\n"
            "the component check is blind along. On 2026-08-30 gt 0.6.0 was found\n"
            "installed with 0.9.4 checked in beside it since the day before, four hooks\n"
            "registered in settings.json pointing at files that had never been copied. A\n"
            "component check aimed at 0.6.0 reported clean the whole time.\n"
            "\n"
            "There is deliberately no `auto`. Installing a version rewrites hook\n"
            "registrations and prunes caches; doing that mid-session leaves the running\n"
            "session executing hooks that no longer match the ones on disk. An upgrade is\n"
            "a decision with a restart attached."),
    },
    "orphan_check": {
        "default": "report",
        "values": ["off", "report", "reap"],
        "summary": "Look for abandoned Claude WORKERS (background shells) at session start.",
        "detail": (
            "off     no checking\n"
            "report  list stalled workers and how to reap them  (default)\n"
            "reap    terminate stalled workers automatically\n"
            "\n"
            "OWNERSHIP is the first test (0.15.0). A shell with a live `claude` process\n"
            "above it belongs to that session: it is listed as information -- session id,\n"
            "claude pid, uptime, and `WAITING on: <what it polls>` for a poll loop -- and\n"
            "is never reaped, whatever its CPU says and even under `reap`. ORPHAN means no\n"
            "`claude` remains above it. THIS session's own workers are the exception to the\n"
            "quiet half: undeclared or idle ones are still raised, because they are yours to\n"
            "answer for.\n"
            "\n"
            "Only then does CPU decide, and for an orphan it decides alone: consumed across\n"
            "the whole process tree, not age and not whether someone wrote a note about it.\n"
            "On 2026-08-29 ten orphans were found alive across three sessions, the oldest at\n"
            "10 days 23 hours, every one having burned under 0.05 seconds of CPU. All ten\n"
            "would have passed a documentation check. A worker younger than five minutes is\n"
            "not judged at all -- it has not had time to accumulate any.\n"
            "\n"
            "A DECLARED worker that is stalled is reported more urgently, not less --\n"
            "someone was told work was happening and it is not. `reap` only ever kills\n"
            "stalled ORPHANS; one consuming CPU is never touched, and neither is one whose\n"
            "session is still alive."),
    },
    "push_check": {
        "default": "report",
        "values": ["off", "report"],
        "summary": "Check at session start whether the vault has commits not yet pushed.",
        "detail": (
            "off     no checking\n"
            "report  name the count, the age of the oldest, and the push command  (default)\n"
            "\n"
            "The vault is a git repo so its truth survives one disk and reaches the other\n"
            "machines. A commit that never leaves is not backed up, is invisible to a\n"
            "session on another host, and looks finished: gt-work reports success and the\n"
            "tree goes clean. On 2026-09-02 the vault was found 16 commits ahead of origin\n"
            "with the oldest dating back weeks. Nothing had failed -- every session had\n"
            "committed correctly, none had pushed, and no check asked.\n"
            "\n"
            "A branch with NO UPSTREAM is reported separately and more loudly. It cannot be\n"
            "ahead, so a naive count returns zero and reads as healthy, when in fact the\n"
            "commits have nowhere to go at all.\n"
            "\n"
            "There is deliberately no `auto`. Pushing is outward-facing: it publishes to a\n"
            "remote others read, it can be rejected, and an unpushed commit is sometimes\n"
            "correct -- work held back on purpose. A push that surprises its author is\n"
            "worse than a delay that annoys them."),
    },
    "surface": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "At session start, show MUST DO deadlines, new handoffs and pre-compaction state.",
        "detail": (
            "off  nothing is surfaced\n"
            "on   every session: overdue and near items from <vault>/deadlines.md, counted\n"
            "     down live; once each: handoffs under a week old and state files gt_state\n"
            "     wrote before a compaction; and a count of open tasks citing a handoff  (default)\n"
            "\n"
            "Until 0.17.2 gt wrote handoffs, state files and handoff tasks and NOTHING at\n"
            "session start read any of them, so each reached the next session only if\n"
            "someone thought to look. Two credential rotations sat 15 days overdue at the\n"
            "top priority that way (2026-09-28): priority is a sort order, not an alarm.\n"
            "This is the alarm; the task stays the record. It never writes to the vault."),
    },
    "daily_comms_content": {
        "default": "follow",
        "values": ["follow", "off"],
        "summary": "Whether THIS machine may put email and Teams information into the daily note: follow the vault's policy, or force it off here.",
        "detail": (
            "follow  use the vault-wide policy  (default)\n"
            "off     never on this machine, whatever the vault says -- e.g. a work machine\n"
            "\n"
            "The policy itself lives in the shared vault and is OFF by default:\n"
            "  gt_daily.py --vault <vault> --comms-content on|off\n"
            "A machine can only tighten it, never loosen it: the vault is shared, so mail or\n"
            "chat content written on one machine is readable from every other -- including a\n"
            "work machine whose employer does not want such content anywhere Claude can read\n"
            "(owner, 2026-09-30). While off, gt_daily makes no room for a --comms section,\n"
            "offers it in no handoff, and if one still holds content a NOTE says so -- it never\n"
            "deletes another tool's writing."),
    },
    "release_announce": {
        "default": "off",
        "values": ["off", "draft", "post"],
        "summary": "What dev/publish.sh does about the release's Discussions announcement: warn, write a draft, or post it.",
        "detail": (
            "off    warn that the release is unannounced, as before  (default)\n"
            "draft  write the announcement to a file and print its path; a person posts it\n"
            "post   create the Discussion in the repo's Announcements category\n"
            "\n"
            "One post covers every release no Discussion names yet, built from the CHANGELOG.\n"
            "It passes the same scrub as a release (employer/host terms, home paths, IP\n"
            "addresses, credential scan) first; a hit posts nothing and leaves the draft.\n"
            "Announcing never blocks a release -- the release is published by then\n"
            "(owner, 2026-09-30)."),
    },
    "agent_specialization": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Whether ingest and validation skills hand their work to a job-typed specialist agent.",
        "detail": (
            "off  every skill runs inline\n"
            "on   a skill with a stage x kind spec (extract, classify, reconcile, draft,\n"
            "     verify, ... x code, docs, tool, session, wiki -- 0.18.1) spawns a specialist\n"
            "     agent with that spec applied; its full output\n"
            "     lands in the spool and the session sees a summary  (default, 0.21.0)\n"
            "\n"
            "Independent of skeptic_pass: this does not switch the gt-work skeptic on or off.\n"
            "A job type with no spec runs inline with a notice. Specs are data:\n"
            "gt_agent_spec.py list | validate <spec>.\n"
            "\n"
            "gt-ingest is the exception (0.21.0): with this off it still uses specialist agents\n"
            "for the run, as a per-run override that never writes this setting -- see\n"
            "ingest_specialists, which switches that off."),
    },
    "skeptic_pass": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Whether /gt:gt-work runs a skeptic agent over the session's findings before they land.",
        "detail": (
            "on   a zero-context agent reads the research entries about to be written and\n"
            "     flags unverified or overclaimed figures first; nothing is dropped, the\n"
            "     session decides  (default since 0.21.0, owner 2026-10-08)\n"
            "off  write-back as before: no skeptic pass\n"
            "\n"
            "Independent of agent_specialization (0.18.1): this alone turns the skeptic on,\n"
            "and does not hand ingest or validation to specialist agents. What is shown is\n"
            "capped by skeptic_max_flags; what the user does with each flag is recorded when\n"
            "skeptic_record_outcomes is on."),
    },
    "skeptic_max_flags": {
        "default": "7",
        "values": ["3", "5", "7", "10", "15", "all"],
        "summary": "Most skeptic flags shown to the user at write-back; the rest are one command away (0.21.0).",
        "detail": (
            "The top N by severity: contradictions first, then overclaimed and\n"
            "unsupported-number, then scope, stale, unverified. The rest are kept, not dropped:\n"
            "gt_skeptic.py rank <record> --all lists every flag. `all` shows everything.\n"
            "(owner, 2026-10-08)"),
    },
    "skeptic_record_outcomes": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Record what the user does with each skeptic flag (fix, keep or drop), so acceptance rates can be computed (0.21.0).",
        "detail": (
            "on   /gt:gt-work appends one small record per flag the user decided on to the\n"
            "     vault's skeptic spool (the entry's title, the kind of flag and the decision), and\n"
            "     gt_skeptic.py outcomes reads them back as acceptance rates per kind  (default)\n"
            "off  nothing is recorded\n"
            "(owner, 2026-10-08)"),
    },
    "findings_calibration": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Whether the calibrate pass (/gt:gt-calibrate) and write-back check that each finding's verification label is backed by the evidence it carries (0.21.0).",
        "detail": (
            "on   /gt:gt-calibrate also re-grades recorded findings (research.md and memory):\n"
            "     an observed symptom is told apart from an inferred mechanism, no finding is\n"
            "     calibrated above `unverified` without attached evidence (a command and its\n"
            "     output, a file and line, a measurement, or the user's statement), and an\n"
            "     overstated finding is reported. write-back runs the same check on what it is\n"
            "     about to land  (default)\n"
            "off  neither runs\n"
            "\n"
            "Reporting writes nothing and spends nothing (no model call). A re-label is applied\n"
            "only on the owner's yes, as a new entry through the write queue; the recorded\n"
            "finding is never rewritten (gt_model_calibrate.py findings | relabel; request\n"
            "2026-10-08-calibrate-verification-state-of-findings)."),
    },
    "ingest_specialists": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Whether /gt:gt-ingest uses specialist agents for the run even when agent_specialization is off (0.21.0).",
        "detail": (
            "on   when agent_specialization is off, a gt-ingest run behaves as if it were on:\n"
            "     stages with a stage x kind spec go to specialist agents, the rest run inline.\n"
            "     This is a per-run override; the stored agent_specialization is never changed\n"
            "     and gt-validate, gt-promote and gt-work still follow it  (default)\n"
            "off  gt-ingest follows agent_specialization like every other skill\n"
            "\n"
            "At the end of every ingest the savings are printed: how many tokens of source\n"
            "material the agents kept out of the session (estimated), and recorded in the\n"
            "savings ledger. (owner, 2026-10-08)"),
    },
    "closeout_later_days": {
        "default": "30",
        "values": None,
        "validate": "a whole number of days, 1 to 365",
        "summary": "How many days a close-out `later` answer defers the question when no date is given (0.21.0).",
        "detail": (
            "A whole number of days, 1 to 365. `gt_closeout.py answer <slug> later` with no\n"
            "date, and `--until` left out, defers that far from today; before 0.21.0 this was\n"
            "a fixed 14. The report card shows one summary line while any deferral is active."),
    },
    "closeout_limit": {
        "default": "3",
        "values": None,
        "validate": "a whole number, 1 to 100, or all",
        "summary": "How many finished-looking projects one session close asks about (0.21.0).",
        "detail": (
            "A whole number, 1 to 100, or all. At /compact and session end the report card\n"
            "names each project the close-out probe thinks may be finished; with this set it\n"
            "names at most that many -- the projects this session wrote to first -- and one\n"
            "line counts the rest. A project not named is not recorded as asked, so it comes\n"
            "up at a later close. all is the behaviour before 0.21.0: every project, every\n"
            "close (owner, 2026-10-10: \"limit the number of items to close at the end of a\n"
            "session\")."),
    },
    "handoff_surface": {
        "default": "any",
        "values": ["any", "project", "manual"],
        "summary": "Where a handoff that has not been handled is shown.",
        "detail": (
            "any      every session start, whatever project is opened  (default)\n"
            "project  only when /gt:gt-open opens the handoff's own project\n"
            "manual   only when you run /gt:gt-handle handoff\n"
            "\n"
            "A handoff keeps being shown until it is handled (marked, or every task citing\n"
            "it closed) or deferred to a date. It is shown as one line -- path, project, age,\n"
            "open items -- never its body, so being told costs no project context; handling\n"
            "it is /gt:gt-handle handoff. `project` needs /gt:gt-open: a session that never\n"
            "opens the project never sees it, which is why `any` is the default (owner,\n"
            "2026-09-28)."),
    },
    "task_surface": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "At session start, one line counting p:: 1 tasks waiting on you.",
        "detail": (
            "off  nothing\n"
            "on   one line: p:: 1 tasks waiting on you, how many overdue, how many open over\n"
            "     a week, and the commands to list and work them  (default)\n"
            "\n"
            "A count, never the tasks: being told must cost no project context. Tasks deferred\n"
            "to a later date (gt_task.py defer) are not counted until that date. Counted by\n"
            "the vault's gt_task.py, which parses with the TASKS.md rollup's own rules."),
    },
    # `watch` and `closeout_check` (and `report_card`, below) moved out in 0.15.0: the watch
    # and report-card modules declare them in module.json and they register from there
    # while the module is installed. Their explanations moved with them (`detail`).
    "test_gate": {
        "default": "auto",
        "values": ["off", "warn", "auto", "block"],
        "summary": "Refuse a `git commit` of code whose tests have not been seen to pass.",
        "detail": (
            "off    commit whatever you like; the Core rule is not injected either\n"
            "warn   allow the commit, but say the tests were not seen to pass\n"
            "auto   block when the repo HAS a discoverable test command, warn when it\n"
            "       does not  (default)\n"
            "block  block regardless -- a repo with no tests cannot commit code\n"
            "\n"
            "Enforces core_test_before_commit through a PreToolUse guard. Evidence is a\n"
            "receipt: tests/run.sh and dev/release-check.sh write one when they pass, any\n"
            "project can write one with `gt_test_receipt.py record --ok`, and editing a file\n"
            "after a run invalidates the receipt for that file automatically.\n"
            "\n"
            "`auto` exists because a gate that fires where it cannot be satisfied is a gate\n"
            "people switch off for everything. A repo with no test entry point is not asked\n"
            "to have one; a repo that has one is held to it.\n"
            "\n"
            "Never blocked: docs-only commits, a repo containing `.gt-no-test-gate`, a\n"
            "command run with GT_TEST_GATE=off, and anything the guard cannot parse.\n"
            "\n"
            "On 2026-09-12 a release was committed and pushed with a stale MANIFEST.json.\n"
            "The gate that catches exactly that existed and had been run before the last few\n"
            "edits rather than after. The discipline was not the problem; it had no\n"
            "mechanism."),
    },
    "parallel_work": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Run divisible work in parallel instead of one unit at a time.",
        "detail": (
            "off  everything runs serially, and the Core rule asking for parallelism is\n"
            "     NOT injected -- a rule the user has switched off must stop being\n"
            "     asserted, or the setting is decoration\n"
            "on   independent units run concurrently, up to `parallel_max`  (default)\n"
            "\n"
            "This is the switch behind the Core rule core_parallel_when_beneficial, and it\n"
            "reaches two places: the rule injected into every turn, and the default worker\n"
            "count of the test runner (tests/prun.py).\n"
            "\n"
            "The serial default was never a decision. A loop, a sweep over 43 projects and\n"
            "a test suite all run on one core unless someone says otherwise, and nothing\n"
            "reports the waste -- the work completes, correctly, slowly, and its output is\n"
            "identical to the fast version. Measured here on 2026-09-12: the 672-test suite\n"
            "took 509s serial and 108s parallel, 4.7x, with CPU going from roughly one core\n"
            "to 404%.\n"
            "\n"
            "`off` is for when a machine is busy with something else, or when a parallel run\n"
            "is producing a failure a serial run does not -- that difference is a finding\n"
            "worth keeping, which is why the serial path stays supported rather than being\n"
            "removed as dead weight."),
    },
    "parallel_max": {
        "default": "auto",
        "values": None,
        "validate": "auto, or a positive integer number of workers",
        "summary": "Ceiling on concurrent workers. `auto` = as many as the machine allows.",
        "detail": (
            "auto  as many as this MACHINE allows -- the ceilings are measured at install\n"
            "      and again at every upgrade, and stored as `parallel_profile`: cores for\n"
            "      CPU-bound work, 2x cores (bounded by memory) for I/O-bound work, which\n"
            "      is what the tools here mostly do  (default)\n"
            "N     never more than N workers at once, whatever the work\n"
            "\n"
            "`auto` is deliberately not a number YOU have to choose. It resolves through\n"
            "`parallel_profile`, which install.sh measures on this machine and re-measures on\n"
            "every upgrade -- so a new machine or a RAM change is picked up without anyone\n"
            "editing a config, and nothing in the source pretends to know your hardware.\n"
            "Run `gt_settings.py detect-machine --write` to re-measure by hand, or\n"
            "`detect-machine` alone to see what would be measured. Your OWN setting is never\n"
            "overwritten by that: the profile records the hardware, this setting records what\n"
            "you will allow.\n"
            "\n"
            "Set a number when the machine has to stay responsive for something else, or\n"
            "when a remote end is rate-limited. `1` is not the same as parallel_work=off:\n"
            "one worker still runs through the parallel path, so it does not tell you\n"
            "whether the parallel path is what broke a test. Use `off` for that."),
    },
    "brief_absence_days": {
        "default": "7",
        "values": ["off", "3", "7", "14", "30"],
        "summary": "How long away from a project before /gt:gt-open leads with a generated catch-up brief.",
        "detail": (
            "N    when a project has not been opened on this machine for N days, gt-open\n"
            "     starts with one generated paragraph (150 words at most): commits since\n"
            "     the last open, the newest research entry, the oldest open p::1 task and\n"
            "     anything waiting on you. Then it reads the files as usual.  (default 7)\n"
            "off  never on its own; `/gt:gt-open <slug> --brief` still asks for one\n"
            "\n"
            "The brief is assembled from git and structured fields (gt_catchup.py), never a\n"
            "summary of prose, and is labelled as generated. A project with no commits\n"
            "while you were away gets none (0.18.1)."),
    },
    "foreign_checkout_guard": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Deny git commit/push inside a checkout you declared as another machine's.",
        "detail": (
            "on   a `git commit` or `git push` whose directory is inside a path listed in\n"
            "     `foreign_checkouts` in ~/.claude/vault-config.json is denied, naming the\n"
            "     checkout and the supported route. Read-only git and file writes are\n"
            "     untouched. With nothing declared it denies nothing.  (default)\n"
            "off  no check\n"
            "\n"
            "Ownership is DECLARED, never guessed from a path, remote or credential:\n"
            "  python3 ~/.claude/golden-thread/hooks/guard_foreign_checkout.py add <path> \\\n"
            "      [--label 'the build machine'] [--route 'run copygt.sh there']\n"
            "  ... list | remove <path>\n"
            "One-off exception, visible in the transcript: GT_FOREIGN_CHECKOUT=allow git push\n"
            "\n"
            "Why: 2026-09-11 a session committed a release into another machine's checkout\n"
            "and started rewriting its remote URL to get the push through (0.18.1)."),
    },
    "protected_paths": {
        "default": "ask",
        "values": ["off", "ask"],
        "summary": "Prompt before Write/Edit to core-rules, global-memory, local packs, gt hooks or settings.json; refuse overwriting a Source.",
        "detail": (
            "ask  a Write or Edit to the vault's core-rules/, global-memory/ or\n"
            "     Projects/golden-thread/packs/, to ~/.claude/golden-thread/, or to\n"
            "     ~/.claude/settings.json always shows the permission prompt, whatever the\n"
            "     permission mode; overwriting or editing an EXISTING file under Sources/\n"
            "     is refused -- supersede it with a new file instead. Creating a new Source\n"
            "     is unaffected.  (default)\n"
            "off  no check\n"
            "\n"
            "These paths load into every session of every project (core-rules,\n"
            "global-memory), enforce everything else (the hooks and settings.json), decide\n"
            "what every scan checks (packs/, added 0.16.0 -- local packs outrank both core\n"
            "and contributed definitions and are not checked by anyone else), or are\n"
            "immutable by convention (Sources). Until 0.12.9 nothing but skill prose kept a\n"
            "session from writing them. `ask` does not block legitimate work -- promoting a\n"
            "fact to global-memory still works -- it makes a person see it happen.\n"
            "\n"
            "Covers the Write, Edit, MultiEdit and NotebookEdit tools. It does NOT see a\n"
            "shell command that writes the same file (cp, sed -i, a script); gt's own vault\n"
            "tools are covered by guard_vault_writes instead."),
    },
    "unlock": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "gt unlock: agents need your presence (TOTP + Touch ID / Windows Hello) for LOTR, secrets, publishing and gt's guards.",
        "detail": (
            "off  nothing is gated; every hook's path is unchanged  (default)\n"
            "on   the unlock authority (gt_unlockd.py) decides: LOTR connections, sealed and\n"
            "     brokered credentials, publish credentials, edits to gt's hooks/settings and\n"
            "     the security settings below need a grant, after you prove presence\n"
            "\n"
            "The source of truth is the unlock policy (~/.claude/golden-thread/unlock/policy.json,\n"
            "tightened by an administrator's floor), not vault-config.json: `set unlock on|off`\n"
            "runs `gt_unlock.py policy enable|disable`, which needs your enrolled factors.\n"
            "Enrol first: gt_unlock.py enroll touchid (macOS) / hello (Windows), then totp;\n"
            "with TOTP alone: enroll totp, then gt_unlock.py policy enable --factors totp.\n"
            "`gt_unlock.py status` names the next step.\n"
            "It is not anti-malware: something already running as you can wait for you to\n"
            "unlock. SECURITY.md says exactly what each level stops (0.20.1)."),
    },
    "lockdown": {
        "default": "strict",
        "values": ["strict", "trust-local", "trust-network", "insecure", "paranoid"],
        "summary": "How much Claude may run without asking: allow rules gt writes into "
                   "~/.claude/settings.json for the level you choose (0.20.5). paranoid is "
                   "strict plus the paranoid switch, never a stored level (0.21.0).",
        "detail": _lockdown_detail(),
        "plain": {
            "intro": (
                "Claude Code asks before it runs most commands or edits your files. This choice sets "
                "how much it may do WITHOUT asking. gt does it by adding allow rules (entries in "
                "Claude Code's settings that say \"this one is fine, do not ask\") for the level you "
                "pick. The levels change how many questions you get; they are not a wall around "
                "your computer."),
            "options": {
                "strict": {
                    "summary": "Claude asks before it runs anything; nothing is allowed ahead of time.",
                    "who": "Anyone new to AI assistants, or working with data they cannot afford to expose.",
                    "without_asking": "Only what Claude Code already allows on its own: reading files and answering.",
                    "still_asks": "Every shell command, every file edit and every network request.",
                    "example": "Claude wants to run the tests: it asks you each time.",
                    "trade_off": "The most control and the most prompts; a long task stops often for a yes.",
                },
                "trust-local": {
                    "summary": "Claude may look around, run your tests and save work in git; it asks for anything outside the project.",
                    "who": "Most people: you want Claude to get on with ordinary project work, but not to use the network or other machines unasked.",
                    "without_asking": "List and read files, look at git history, run the project's tests, edit files inside the project folder, and save work with git add and git commit.",
                    "still_asks": "Anything that uses the network or another computer, sends code anywhere (git push), installs software, deletes files, or edits outside the project.",
                    "example": "Claude wants to run the tests: it just runs them. Claude wants to push to GitHub: it asks you.",
                    "trade_off": "Far fewer prompts than strict, in exchange for trusting Claude inside the project folder; a mistaken edit there is possible (git history lets you undo it).",
                },
                "trust-network": {
                    "summary": "As trust local, plus the network: other machines, pushing code, downloads and installs.",
                    "who": "Developers and administrators who work across servers, want fewer interruptions, and trust the project and the sites involved.",
                    "without_asking": "Everything in trust local, plus ssh and file copies to other machines, git push, pull and fetch, downloads with curl, installing packages, and fetching web pages.",
                    "still_asks": "Any other shell command, and edits outside the project.",
                    "example": "Claude wants to push your branch: it just does. Claude wants to delete a folder: it asks you.",
                    "trade_off": "Convenient for real work, but code and data can now leave this machine, and an installed package runs with your permissions, without a prompt.",
                },
                "insecure": {
                    "summary": "Claude may run any command and edit any file without asking.",
                    "who": "Throwaway machines, test VMs and containers where nothing of value can be damaged.",
                    "without_asking": "Any shell command, any file edit, and web searches.",
                    "still_asks": "Almost nothing; sending, merging or deleting through LOTR (gt's gateway to GitHub, Jira and Microsoft 365) still asks for your consent.",
                    "example": "Claude wants to delete a folder: it just does.",
                    "trade_off": "No prompts and no safety net: one wrong command can delete or send anything you can.",
                },
                "paranoid": {
                    "summary": "Strict, plus locks on gt's own installed code so nothing can change it behind your back.",
                    "who": "People whose machine or work makes tampering a real worry: shared computers, sensitive clients, regulated work.",
                    "without_asking": "The same as strict: only what Claude Code already allows on its own.",
                    "still_asks": "Everything strict asks, and a change to gt's own code or its protection needs you in person (gt unlock, or a typed confirmation at a terminal).",
                    "example": "Something tries to edit one of gt's own program files: it is refused, and the attempt is logged.",
                    "trade_off": "The most protection and the most friction: updating gt or turning a protection off needs you there. Turning it on needs gt unlock set up first, unless you accept the weaker typed confirmation (paranoid.require_unlock off).",
                },
            },
            "recommended": "trust-local",
            "not_sure": (
                "Choose trust-local. It removes the prompts for looking at things and running "
                "tests, which people click through without reading, and keeps every prompt that "
                "matters: the network, installs, deletes and pushes."),
            "change_later": (
                "Run /gt:gt-settings lockdown <level> in Claude (or gt_settings.py set lockdown "
                "<level> in a terminal), then restart Claude Code. Nothing here is permanent. "
                "This level is a starting point, not a package. Every other security item is its "
                "own setting that you can turn on or off separately at any time with "
                "/gt:gt-settings <name> <value>: sandbox mode (a fence that keeps Claude's shell "
                "commands out of your vault and gt's files), each part of paranoid (locks on gt's "
                "own code) and each of its single-use tokens (a one-time permit you create for one "
                "action), gt unlock (your fingerprint or a code before credentials are used), "
                "protected paths, and the push and commit fingerprints. /gt:gt-settings shows them "
                "all; ./install.sh --help security explains each one and how to change it."),
        },
    },
    "testing_profile": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "Set-and-forget testing: pre-approve test runners so an unattended run stops "
                   "asking, and leave every Core-rule gate alone (0.21.0).",
        "detail": (
            "off  Claude Code asks about test commands as it always does  (default)\n"
            "on   gt_testing_profile.py wrote allow rules into the project's\n"
            "     .claude/settings.json (or ~/.claude/settings.json with --scope user) and\n"
            "     recorded exactly which, so `off` takes out those and nothing you edited:\n"
            "       pytest, python3 -m pytest/unittest, npm test, node --test, vitest run, bats,\n"
            "       npm audit, pip-audit, semgrep, osv-scanner, and gt's own dev tests\n"
            "       (bash dev/remote-test.sh, bash tests/run.sh, GT_TEST_VERSION=* bash\n"
            "       tests/run.sh, python3 tests/prun.py);\n"
            "       Edit and Read of ONE sandbox folder you name with --sandbox, if you do.\n"
            "\n"
            "Never allowed, and refused by name: ssh/scp/rsync, rm, git push, curl/wget, sudo,\n"
            "a shell or interpreter with no script, cat/tail/grep, a sandbox that is the vault,\n"
            "inside it, your home folder or any credential or start-up folder, and anything\n"
            "that skips permissions or turns a gate off. Shell commands are not confined to\n"
            "the sandbox folder by these rules; only Claude's file tools are.\n"
            "\n"
            "Use the testing subcommand, not `set`:\n"
            "  gt_settings.py testing on [--sandbox DIR] [--until 4h] [--scope project|user] [--dry-run]\n"
            "  gt_settings.py testing off | status\n"
            "Turning it on needs a terminal and, when gt unlock is on, a fresh confirmation.\n"
            "`--until` ends it by itself; show and the session-start sweep say so when it does."),
        "plain": {
            "intro": (
                "A long test run stops every few minutes to ask \"may I run pytest?\". Those "
                "questions are a convenience, not protection: running your tests in a project "
                "cannot leak a secret or touch the vault. This turns the questions off for a "
                "short list of test and scanning commands, in one project, and nothing else. "
                "The Core rules (the vault write guard, the explicit-vault-target check and the "
                "secrets check at the end of each reply) keep running exactly as before."),
            "options": {
                "off": {
                    "summary": "Claude asks about test commands as it does today.",
                    "who": "Anyone not running a long unattended test or scan.",
                    "without_asking": "Only what Claude Code already allows.",
                    "still_asks": "Each test command that is not already allowed.",
                    "example": "Claude wants to run pytest: it asks you each time.",
                    "trade_off": "The most control and the most prompts.",
                },
                "on": {
                    "summary": "Test runners and scanners run without asking, in this project, until you turn it off.",
                    "who": "You, when you want a test or security-assessment run to go on while you are away.",
                    "without_asking": "pytest, npm test, node --test, vitest, bats, npm audit, semgrep and similar, gt's own test commands, and edits inside one sandbox folder you name.",
                    "still_asks": "Everything else: ssh, pushing code, deleting files, downloads, and any edit outside the sandbox folder. The Core-rule checks still refuse a vault write or a secret.",
                    "example": "Claude runs the whole suite and the scanner while you are out; it still asks before it pushes.",
                    "trade_off": "A test command that has been tampered with would run without a question, so use it on code you trust, with --until so it ends by itself.",
                },
            },
            "recommended": "off",
            "not_sure": "Leave it off until you are about to start a long unattended run; then switch it on with --until 4h.",
            "change_later": (
                "Run /gt:gt-settings testing on --until 4h (or gt_settings.py testing on --until 4h "
                "in a terminal), and gt_settings.py testing off to end it. Nothing here is permanent."),
        },
    },
    "persona": {
        "default": "",
        "values": None,
        "validate": "a comma list of installed persona names (gt_persona.py list), or none",
        "summary": "Who you are: the persona packs that shape a new project's extra files (0.21.0).",
        "detail": (
            "(empty)  no persona: new projects are exactly the standard structure  (default)\n"
            "a,b      one or more installed personas, first listed wins a conflict; their\n"
            "         extra scaffold files are added (never replacing a standard file)\n"
            "none     clears it\n"
            "\n"
            "gt_persona.py list shows the installed personas. A project's README frontmatter\n"
            "`persona: a,b` replaces this list for that project (`persona: none` opts out).\n"
            "A persona changes what gt offers, never what it enforces: it cannot touch Core\n"
            "rules, lockdown, sandbox_mode, unlock, test_gate or protected_paths."),
        "plain": {
            "intro": (
                "Tell gt what you do, and a new project starts with the extra files people in that "
                "role usually want (a spec stub for a developer, for example). It only adds files. "
                "It never changes what gt enforces or what Claude may do without asking."),
            "recommended": "none",
            "not_sure": "Choose none. A new project is then the standard structure, and you can pick a role at any time.",
            "change_later": "Run /gt:gt-settings persona <name> in Claude (or gt_settings.py set persona <name>), no restart needed; it applies to projects you create afterwards.",
        },
    },
    "persona_aligned": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Persona-aligned execution: shape drafts, briefs, summaries and wrap-ups for your role (0.21.0).",
        "detail": (
            "on   gt_agent_spec.py render adds a short 'Audience:' block, built from your persona(s) in the\n"
            "     order listed (the first leads), to the stages that WRITE for you: draft, briefs, summaries\n"
            "     and the gt-work / gt-complete wrap-up. It is never given to extract, classify, reconcile,\n"
            "     verify, validate, generalize, place or the skeptic: a role changes how a result is\n"
            "     presented, never what is recorded as true.  (default; nothing happens with no persona)\n"
            "off  no Audience block anywhere; prompts are byte-identical to a gt without it"),
        "plain": {
            "intro": (
                "If you told gt what you do (the persona setting), gt can ask the parts of Claude that write "
                "for you to write for someone in your role: plain language and status first for a manager, "
                "commands and file names for a developer. It never changes what is found or recorded as "
                "true, only how results written for you are shaped. Say \"give me the details\" in a "
                "prompt and that one answer is given in full, whatever your role."),
            "options": {
                "on": {"summary": "Drafts, briefs, summaries and wrap-ups are shaped for your role. Nothing changes if you have no persona."},
                "off": {"summary": "Everything is written the same way for everyone."},
            },
            "recommended": "on",
            "not_sure": "Leave it on. It does nothing until you pick a persona, and it never touches how facts are checked.",
            "change_later": "Run /gt:gt-settings persona_aligned <on|off> in Claude (or gt_settings.py set persona_aligned <value>).",
        },
    },
    "sandbox_mode": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "gt sandbox mode: fence Claude's shell and file tools off the vault and gt's state; reach the vault through gt's MCP and write queue.",
        "detail": (
            "Since 0.20.2 every skill works under it: each says which gt-vault MCP tool takes a\n"
            "step that would run a vault script from Claude's shell (gt_log, gt_tasks, gt_adr,\n"
            "gt_lint, gt_broker drain, ...), and hands you the few that need you (settings,\n"
            "install, upgrade, sync, Core rules) as one command to run in a terminal.\n"
            "\n"
            "off  Claude's shell and file tools reach the vault and gt's state as before  (default)\n"
            "on   gt_sandbox.py writes into ~/.claude/settings.json (and records what it wrote):\n"
            "       sandbox.enabled true, allowUnsandboxedCommands false, failIfUnavailable true\n"
            "       -- Claude Code's OS sandbox around Bash/PowerShell/Monitor and their children\n"
            "       (Seatbelt on macOS, bubblewrap on Linux and WSL2);\n"
            "       sandbox.filesystem.denyWrite: the vault, ~/.claude/golden-thread (hooks, state,\n"
            "       unlock home), the LOTR home and store, ~/.claude/plugins, settings.json and\n"
            "       vault-config.json; denyRead: the unlock home, LOTR, locked vault folders (and\n"
            "       the whole vault while sandbox_vault_reads is deny); allowWrite: ~/.gt-inbox,\n"
            "       the queue inbox, and ~/.gt-scratch, the stage agents' private scratch\n"
            "       folders; permissions.deny Read(...)/Edit(...) rules for the same\n"
            "       paths, because the file tools run outside the sandbox\n"
            "\n"
            "The vault is then read through gt's vault MCP server (vault_list, vault_read,\n"
            "vault_search) and written only through the queue (the vault_queue_write tool, or\n"
            "gt_write_queue.py, which drops its request in ~/.gt-inbox/queue/); the broker\n"
            "applies it outside the sandbox. Hooks and MCP servers run outside the sandbox by\n"
            "Claude Code's design, so they keep working. Restart Claude Code after switching.\n"
            "\n"
            "NATIVE WINDOWS has no Claude Code sandbox: only the permission rules are written\n"
            "-- friction, not a boundary. Linux/WSL2 need bubblewrap and socat; without them\n"
            "turning this on is refused, because failIfUnavailable would stop Claude Code from\n"
            "starting. Turning it OFF from inside the sandbox fails by design (~/.claude is\n"
            "write-protected there): run it in a terminal. With gt unlock on, switching it off\n"
            "needs a fresh confirmation. Not anti-malware: something already running as you\n"
            "is not fenced. SECURITY.md, 'gt sandbox mode' (0.20.1)."),
        "plain": {
            "intro": (
                "Sandbox mode (a fence the operating system puts around Claude's shell commands, so "
                "they cannot write to places you did not allow) is a stronger protection than the "
                "security level. With it on, Claude reaches your notes vault and gt's own files "
                "only through a narrow gateway, an MCP (Model Context Protocol) server: a small "
                "helper program that Claude Code connects to and calls like a tool."),
            "options": {
                "off": {
                    "summary": "Claude's shell and file tools reach your vault and gt's files directly (the default).",
                    "who": "People who want gt to be simple, or who work where the operating system's fence cannot run.",
                    "without_asking": "Read and write the vault and gt's folders with the normal tools, within the security level you chose.",
                    "still_asks": "Whatever your security level asks; nothing extra.",
                    "example": "Claude edits a note in your vault: it writes the file directly.",
                    "trade_off": "Simplest and fastest, but a mistaken or manipulated command can reach your vault and gt's own files.",
                },
                "on": {
                    "summary": "The operating system fences your vault and gt's files off from Claude's shell; Claude reaches them only through gt's gateway.",
                    "who": "People who keep valuable notes or credentials in the vault and want a hard limit on what any command can touch, and accept some friction for it.",
                    "without_asking": "Read the vault and queue changes to it through the gateway, which applies them for Claude.",
                    "still_asks": "Settings, install, upgrade and sync steps are handed to you as a command to run yourself in a terminal.",
                    "example": "Claude wants to save a note: it queues the change through the gateway, and gt writes the file a moment later, outside the fence.",
                    "trade_off": "A real fence against stray writes, in exchange for a restart, a slightly slower vault and a few steps you run yourself. Needs the operating system's own sandbox (built in on macOS, bubblewrap on Linux).",
                },
            },
            "recommended": "off",
            "not_sure": "Leave it off. The security level you chose already controls what Claude may run; turn this on later if you want a hard fence.",
            "change_later": "Run gt_settings.py set sandbox_mode on (or off) in a terminal, then restart Claude Code.",
        },
    },
    "sandbox_vault_reads": {
        "default": "deny",
        "values": ["deny", "allow"],
        "summary": "Under sandbox mode, whether Claude's shell and file tools may also READ the vault.",
        "detail": (
            "deny   the vault is read-denied too: Claude reads it only through the vault MCP\n"
            "       tools, which skip locked folders and cap what one call returns  (default)\n"
            "allow  the shell and file tools may read the vault (gt's vault scripts keep working\n"
            "       from Claude's shell); writes stay denied, and locked folders stay unreadable\n"
            "\n"
            "Only takes effect while sandbox_mode is on; changing it re-applies the settings.\n"
            "With deny, gt's own vault tools (gt_tasks, gt_lint, ...) cannot read the vault from\n"
            "Claude's shell either -- run them in a terminal, or use the MCP tools."),
    },
    "symlink_writes_outside_vault": {
        "default": "refuse",
        "values": ["refuse", "allow"],
        "summary": "Whether a vault tool may append through a link that points OUTSIDE the vault.",
        "detail": (
            "refuse  a vault file that is a link to somewhere outside the vault is not written\n"
            "        through; the tool stops and names the link  (default)\n"
            "allow   write through it -- for a link you made on purpose, e.g. a log shared with\n"
            "        another folder or machine\n"
            "        Every write it lets through is logged in the vault, with the file, where it\n"
            "        really went and the lines written: Projects/golden-thread/outside-vault-writes.jsonl\n"
            "\n"
            "A link that resolves INSIDE the vault is always written through, unless it reaches\n"
            "design.md or global-memory/, which no tool writes, link or not, whatever this says.\n"
            "Outside the vault is opt-in because the text can be model-chosen: a planted link to\n"
            "a shell profile would turn it into code (0.20.2, review M-A)."),
    },
    "push_fingerprint": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "A fingerprint (gt unlock step-up) before every git push from the repos in push_fingerprint_repos.",
        "detail": (
            "off  pushes are not gated  (default)\n"
            "on   one switch (gt 0.20.3): needs one enrolled factor (enroll touchid, or hello on Windows), turns gt\n"
            "     unlock on in a PUSH-ONLY profile -- every scope open except gt:publish and the\n"
            "     unlock policy/enrollment, which need a fresh confirmation -- and installs a git\n"
            "     pre-push hook in each repo in push_fingerprint_repos. A push of main or a tag\n"
            "     from a repo that ships dev/release-check.sh runs it --quick first (docs gate).\n"
            "     `off` removes exactly those hooks and restores the earlier unlock policy.\n"
            "Proof: gt_push_guard.py check. Limit: a hook is skipped by `git push --no-verify`;\n"
            "the lock is a push credential sealed behind gt:publish (SECURITY.md)."),
    },
    "push_fingerprint_seal_token": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "With push_fingerprint on, also seal the GitHub push token behind gt:publish (the lock).",
        "detail": (
            "off  the pre-push hook asks for the fingerprint; git uses gh's token as usual, so an\n"
            "     assistant session can still push after your touch  (default)\n"
            "on   the token moves into gt unlock's sealed store and git gets it only under gt:publish,\n"
            "     so a plain `git push --no-verify` still asks (limits: SECURITY.md 5.5a). gt unlock serves secrets only to its\n"
            "     MCP shim: run `push_fingerprint on` from YOUR terminal, and expect pushes started by an\n"
            "     assistant session to be refused. Set this before turning push_fingerprint on."),
    },
    "push_fingerprint_repos": {
        "default": "",
        "values": None,
        "validate": "comma-separated absolute paths of git repositories",
        "summary": "The git repositories push_fingerprint guards (comma-separated paths).",
        "detail": (
            "Absolute paths, comma-separated. Worktrees share their repository's hooks, so one\n"
            "path covers every worktree of it. Set this before `push_fingerprint on`."),
    },
    "expiry_guard": {
        "default": "warn-only",
        "values": ["auto", "warn-only", "off"],
        "summary": "Cut an idle session before its prompt cache lapses, so a cold resume never "
                   "rebuilds a huge context (0.21.0).",
        "detail": (
            "warn-only  a desktop notification when a big idle session is about to lose its cache;\n"
            "           never a model call  (default)\n"
            "auto       also cut it: a closed session is compacted headlessly while the cache is\n"
            "           still warm; an open one gets a compacted fork and the resume command\n"
            "off        nothing is armed, scheduled, notified or blocked\n"
            "\n"
            "An idle session pays a full rebuild of its context on the next prompt once the cache\n"
            "lapses (5 minutes, or 1 hour on a subscription). A hook arms one scheduled job per idle\n"
            "period; it fires shortly before the lapse, leaves a session that changed alone, and\n"
            "always uses the session's own model, effort and folder. Details and the other\n"
            "expiry_guard.* settings: gt_expiry_guard.py status\n"
            "(request 2026-10-08-cache-expiry-guard-cut-before-reread)."),
        "plain": {
            "intro": (
                "While you are away, the model keeps your conversation warm for a few minutes so the "
                "next message is cheap. Come back after it has gone cold and the whole conversation "
                "is read again at full price. This guard watches for that."),
            "options": {
                "warn-only": {
                    "summary": "A desktop notice when a big idle session is about to go cold; it never acts (the default).",
                    "who": "Anyone who leaves long sessions open and would like a nudge.",
                    "without_asking": "Show you a notice. Nothing is changed and no model is called.",
                    "still_asks": "Everything: you decide whether to trim the session.",
                    "example": "You go to lunch with a large session open: a notice says it will go cold in two minutes.",
                    "trade_off": "No surprises and no cost, but you have to act on the notice yourself.",
                },
                "auto": {
                    "summary": "Also trims the idle session for you before it goes cold.",
                    "who": "People who would rather not think about it and accept that gt compacts a session unasked.",
                    "without_asking": "Compact (summarise and shorten) a session that nobody has open; for one that is open, make a shortened copy and tell you how to resume it.",
                    "still_asks": "Nothing about the compacting itself.",
                    "example": "You close the laptop on a huge session: gt shortens it while the memory is still warm, so tomorrow's resume is cheap.",
                    "trade_off": "Saves the most, but the session you resume is a summary of the original, not every word.",
                },
                "off": {
                    "summary": "Nothing is watched, scheduled or shown.",
                    "who": "People who manage their own sessions.",
                    "without_asking": "Nothing.",
                    "still_asks": "Nothing; gt stays silent about the cache.",
                    "example": "You return to a cold session: it re-reads everything and you pay for it.",
                    "trade_off": "No background job and no notices, at the risk of an expensive resume.",
                },
            },
            "recommended": "warn-only",
            "not_sure": "Leave it at warn-only. It costs nothing, changes nothing, and tells you when it matters.",
            "change_later": "Run /gt:gt-settings expiry_guard <value> in Claude (or gt_settings.py set expiry_guard <value>); it takes effect at once.",
        },
    },
    "expiry_guard.closed_sessions": {
        "default": "compact",
        "values": ["compact", "notify", "off"],
        "summary": "What auto does to an idle session no terminal has open (acts only when "
                   "expiry_guard is auto).",
        "detail": (
            "compact  run `claude -p --resume <id> \"/compact ...\"` with the same model, effort and\n"
            "         folder while the cache is warm  (default)\n"
            "notify   only tell you the cache is about to lapse\n"
            "off      do nothing for closed sessions\n"
            "\n"
            "A session counts as closed when no live Claude Code process holds it. When that cannot\n"
            "be told, it counts as open."),
    },
    "expiry_guard.open_sessions": {
        "default": "compact",
        "values": ["compact", "ask", "fork", "notify", "off"],
        "summary": "What auto does to an idle session still open in a terminal (acts only when "
                   "expiry_guard is auto).",
        "detail": (
            "compact  the session compacts itself, in place, while its cache is warm; you come\n"
            "         back to a small session with nothing to press  (default)\n"
            "ask      a notice, and /compact put in the prompt box so Enter does it\n"
            "fork     compact a --fork-session copy, save its summary as a handoff, and notify\n"
            "         you with `claude -r <new id>`; the open session is never written to\n"
            "notify   only tell you the cache is about to lapse\n"
            "off      do nothing for open sessions\n"
            "\n"
            "compact and ask run inside the session (a gt mod, Claude Code 2.1.267 or later; the\n"
            "prompt box needs 2.1.268). On an older Claude Code, or when the mod cannot act, they\n"
            "do what fork does."),
    },
    "expiry_guard.handoff_pickup": {
        "default": "on",
        "values": ["on", "off"],
        "summary": "After a fork, offer /clear to continue from the saved handoff, and load it.",
        "detail": (
            "on   when you come back to a session whose cache lapsed, the first choice is /clear,\n"
            "     and the fresh session in that terminal is given the handoff the fork saved\n"
            "     (default)\n"
            "off  the handoff is still saved under ~/.claude/golden-thread/expiry-guard, but\n"
            "     nothing offers or loads it"),
    },
    "expiry_guard.resume_backstop": {
        "default": "on",
        "values": ["on", "off"],
        "summary": "Block the first prompt of a large, expired session once, with the cost and "
                   "three choices.",
        "detail": (
            "on   when you return to a session past its cache lifetime and over\n"
            "     expiry_guard.min_tokens that the guard did not cut, the first prompt is blocked\n"
            "     once (a blocked prompt never reaches the model, so it costs nothing); the\n"
            "     message gives the size and the estimated rebuild cost. /compact, or send the\n"
            "     same prompt again within 2 minutes  (default)\n"
            "off  never blocks\n"
            "\n"
            "Works in warn-only and auto; expiry_guard off disables it."),
    },
    "expiry_guard.min_tokens": {
        "default": "150000",
        "values": None,
        "validate": "a whole number of tokens, 1 or more",
        "summary": "Contexts smaller than this are left alone by the cache-expiry guard "
                   "(default 150000).",
        "detail": (
            "Tokens of context (input + cache read + cache write of the last reply). Below it a\n"
            "rebuild costs little and the guard does not arm, cut or block. Raise it to act on\n"
            "fewer sessions, lower it to act on more."),
    },
    "expiry_guard.margin_min": {
        "default": "auto",
        "values": None,
        "validate": "auto, or a whole number of minutes (0 or more)",
        "summary": "How many minutes before the cache lapses the guard fires (auto: 5 for a "
                   "5-minute cache, 10 for a 1-hour cache).",
        "detail": (
            "auto  5 minutes before a 5-minute cache lapses, 10 before a 1-hour cache\n"
            "N     that many minutes\n"
            "\n"
            "The job never fires sooner than 150 seconds after Claude's last reply, so a 5-minute\n"
            "margin on a 5-minute cache fires 150 s in, not at once. The cache lifetime is read\n"
            "from the session's own transcript."),
    },
    "expiry_guard.notify": {
        "default": "desktop",
        "values": ["desktop", "none"],
        "summary": "Whether the cache-expiry guard shows a desktop notification.",
        "detail": (
            "desktop  macOS notification centre, a Linux notify-send, a Windows balloon  (default)\n"
            "none     silent; the guard still acts, and every action is a line in\n"
            "         ~/.claude/golden-thread/expiry-guard/guard.log"),
    },
    "io_read_allow": {
        "default": "",
        "values": None,
        "validate": "comma-separated absolute folders (or ~/folders) that exist",
        "summary": "Folders the disk-read broker (disk_read / disk_list) opens without a "
                   "prompt, beside the vault and every project's source.md repo (0.21.0).",
        "detail": (
            "Comma-separated absolute folders, ~/ allowed. Empty (the default) leaves only the\n"
            "vault and the code repos your vault projects name in source.md (`**Repo:**` and\n"
            "`local` rows of the deployment table) on the list.\n"
            "Anything else the broker reads needs your presence: Touch ID / Windows Hello / a\n"
            "one-time code through gt unlock, or Claude Code's permission prompt when none is\n"
            "enrolled. Credential folders, key files and .env files are never readable, with or\n"
            "without approval, whatever is listed here.\n"
            "Add one: python3 gt_settings.py io allow <folder>.  See it all: gt_settings.py io show.\n"
            "Changing this is a security setting: with gt unlock on it needs a fresh confirmation."),
    },
    "io_fetch_allow": {
        "default": "",
        "values": None,
        "validate": "comma-separated domains, addresses or CIDR ranges (no scheme, no path)",
        "summary": "Domains the network-fetch broker (net_fetch) opens without a prompt "
                   "(subdomains included); also where the owner lists localhost or a private "
                   "address on purpose (0.21.0).",
        "detail": (
            "Comma-separated: a domain (docs.example.com covers it and its subdomains), an\n"
            "address (127.0.0.1) or a CIDR range (10.0.0.0/8, no shorter than /8). Empty (the\n"
            "default) lists nothing: every public domain asks for your presence, and the\n"
            "private, loopback, link-local and metadata addresses, `localhost`, and any name\n"
            "that RESOLVES to one are never fetched. Listing a NAME does not open a private\n"
            "answer for it: list the address itself, on purpose. net_fetch sends no cookies or\n"
            "credentials whatever is listed here.\n"
            "Add one: python3 gt_settings.py io allow <domain>.  See it all: gt_settings.py io show.\n"
            "Changing this is a security setting: with gt unlock on it needs a fresh confirmation."),
    },
    "io_fetch_max_bytes": {
        "default": "1048576",
        "values": None,
        "validate": "a whole number of bytes, 1 or more",
        "summary": "The most one net_fetch call returns (default 1048576 = 1 MB); the rest is "
                   "dropped and the result says truncated (0.21.0).",
        "detail": (
            "A byte count. A longer body is cut at this length (on a character boundary) and the\n"
            "result says truncated. Only text, JSON and XML are returned, whatever the size."),
    },
    "io_tools": {
        "default": "on",
        "values": ["on", "off"],
        "summary": "Offer the I/O brokers' tools (disk_read / disk_list / net_fetch) in the "
                   "gt-vault MCP server, whatever vault_mcp says (0.21.0; on by default).",
        "detail": (
            "on   disk_read / disk_list / net_fetch are offered, even with the vault tools off  (default)\n"
            "off  no broker tool is offered or callable\n"
            "The broker is the narrow, gated route for reading outside the vault: allow list at\n"
            "once, anything else asks for your presence, credentials never. Turning it off leaves\n"
            "only Claude Code's general Read tool for such reads."),
    },
    "io_write_presence": {
        "default": "on",
        "values": ["on"],
        "summary": "Every disk_write needs a fresh confirmation (or a live timed grant for that "
                   "repo); this cannot be switched off (0.21.0, item #20 stage 3).",
        "detail": (
            "on  every disk_write asks for Touch ID / Windows Hello / a one-time code through gt\n"
            "    unlock (or Claude Code's own permission prompt, stated, where none is enrolled)\n"
            "    or runs inside a timed window you opened for one repo or folder  (the only value)\n"
            "There is no `off`: a switch that could be turned off would say a write was gated when\n"
            "it was not. The same holds at every lockdown level."),
    },
    "io_write_ci": {
        "default": "never",
        "values": ["never", "allow"],
        "summary": "Whether disk_write may write CI workflow files (.github/workflows and the "
                   "like); only you can lift it (0.21.0, item #20 stage 3).",
        "detail": (
            "never  CI definitions (.github/workflows, .github/actions, .gitlab-ci.yml,\n"
            "       .circleci, Jenkinsfile, azure-pipelines.yml ...) are refused  (default)\n"
            "allow  they are written like any other file in the work tree: still with presence,\n"
            "       a backup and the path in the prompt\n"
            "Everything else disk_write refuses stays refused whatever this says: ~/.claude, shell\n"
            "start-up files, LaunchAgents, ~/.ssh, .git, .env and key files, the vault.\n"
            "A security setting: with gt unlock on, changing it needs a fresh confirmation an\n"
            "agent cannot give. Workflow files run with the repository's secrets once pushed."),
    },
    "io_write_max_bytes": {
        "default": "1048576",
        "values": None,
        "validate": "a whole number of bytes, 1 or more",
        "summary": "The most one disk_write call writes (default 1048576 = 1 MB) (0.21.0).",
        "detail": (
            "A byte count. A longer content is refused whole, never cut. Text only: content with\n"
            "a NUL byte is refused."),
    },
    "io_read_max_bytes": {
        "default": "1048576",
        "values": None,
        "validate": "a whole number of bytes, 1 or more",
        "summary": "The most one disk_read call returns (default 1048576 = 1 MB); a bigger file "
                   "is paged with next_offset (0.21.0).",
        "detail": (
            "A byte count. A file longer than this comes back in pieces, each at most this long;\n"
            "the result says truncated and gives next_offset. A binary file is refused whatever\n"
            "the size. The vault tools keep their own, smaller caps."),
    },
    "commit_fingerprint": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "A fingerprint on every commit: a Secure Enclave key signs it (gt_sign.py), in the repos in push_fingerprint_repos.",
        "detail": (
            "off  commits are not signed by gt  (default)\n"
            "on   one switch (gt 0.20.3, macOS): creates a Secure Enclave signing key that needs a\n"
            "     currently enrolled finger for every use, and sets each repo in\n"
            "     push_fingerprint_repos to sign every commit and tag with it (git's ssh signing).\n"
            "     Per repo instead (0.21.0): inside a repo, `gt_sign.py on --here` signs just that\n"
            "     one -- the repo carries `gt.commitfingerprint on` in its own .git/config, no\n"
            "     global list is edited, and `gt_sign.py check --heal` repairs a repo whose wiring\n"
            "     was lost (deleted .git/config, re-init). `off --here` turns off that repo only.\n"
            "     Every commit asks for Touch ID; a rebase of ten commits is ten touches.\n"
            "     `off` puts the earlier git config back exactly; the key is kept.\n"
            "The server half makes the server refuse unsigned commits: `gt_sign.py github` shows the\n"
            "plan (the key as a GitHub signing key, and a ruleset requiring signed commits on the\n"
            "default branch with no bypass), `--apply` asks before changing it. Proof: gt_sign.py check --live."),
    },
    "accept_review": {
        "default": "dialog",
        "values": ["dialog", "full"],
        "summary": "How an accept of a #conflict version is reviewed: only the presence dialog, or the whole change shown first and bound to its hash.",
        "detail": (
            "dialog  Touch ID/Hello presence only, as before  (default)\n"
            "full    the whole change is shown to its last line, then a code, then presence. Hidden\n"
            "        characters are made visible with a count; the approval is bound to the exact\n"
            "        content hash, so a change that differs from what was shown cannot be written.\n"
            "        A change above 200 changed lines is refused for `full` and goes to an editor.\n"
            "        With no display, the terminal route works unchanged.\n"
            "Changing it needs the settings step-up when gt unlock is on: it is a security setting.\n"
            "The accept tool reads it (gt_settings.get('accept_review'))."),
    },
    # --- 2F/persona: the integrator adds the `persona` setting here (Phase 4) ---
    "vault_mcp": {
        "default": "auto",
        "values": ["auto", "on", "off"],
        "summary": "Whether gt's vault MCP server offers its tools (vault_list/read/search, vault_queue_write).",
        "detail": (
            "auto  offered while sandbox_mode is on, otherwise not  (default)\n"
            "on    always offered, to Claude Code and to any MCP client that starts the server\n"
            "off   never offered\n"
            "\n"
            "The server ships in gt's plugin manifest, so Claude Code starts it with every\n"
            "session; when its tools are not offered it lists none and reads nothing. It is\n"
            "itself writes only a new Sources/ file and the body of an ADR slot its own session\n"
            "allocated; every other vault write is queued with gt_write_queue's own validation\n"
            "and decided by the broker, or made by the vault's own tools, and none writes\n"
            "design.md or global-memory/ (0.20.2). It never returns a file outside the vault\n"
            "or inside a locked folder, and caps what one call returns. With gt unlock on, reads\n"
            "need the gt:vault:read scope and are served only to the session's registered vault\n"
            "server (door mcp_only), never to a process from Claude's shell. Restart Claude Code\n"
            "after switching (request 2026-10-02-vault-mcp-read-server, 0.20.1)."),
    },
    "decision_signals": {
        "default": "default",
        "values": None,
        "validate": "default, off, or ;-separated edits: +phrase adds, -phrase removes",
        "summary": "Phrases gt-lint's decision-candidate check looks for in design.md/research.md.",
        "detail": (
            "default  the built-in list: we chose, we decided, this is intentional,\n"
            "         don't change this, do not change this, deliberately, by design,\n"
            "         we use ... instead of, this workaround, existing behavio(u)r is\n"
            "         correct, trade-off we accepted  (default)\n"
            "off      no phrases: the check reports nothing\n"
            "edits    `;`-separated changes to the built-in list -- `+we went with` (or a\n"
            "         bare `we went with`) adds, `-deliberately` removes. e.g.\n"
            "         gt_settings.py set decision_signals \"+we went with;-deliberately\"\n"
            "\n"
            "Matching is case-insensitive and line by line; `...` in a phrase matches up to\n"
            "60 characters of anything. A hit is only a CANDIDATE in the review queue --\n"
            "nothing writes an ADR. Decline one line for good with\n"
            "`suppress: <path>:#<hash>` in lint-declines.md (the queue entry prints it).\n"
            "Added 0.18.1: decisions stated in prose and never recorded are re-debated by\n"
            "the next session, or changed because nothing said they were deliberate."),
    },
    # -- 0.18.1, group g5 (review stamps, vault sync, reminders) ------------------------
    "review_stamp": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Stamp last_reviewed on a Knowledge page when gt-query or gt-open reads it.",
        "detail": (
            "on   after /gt:gt-query answers from a Knowledge page, or /gt:gt-open loads one\n"
            "     as project context, gt_review_stamp.py queues `last_reviewed: <today>` on\n"
            "     it through the write queue  (default)\n"
            "off  nothing is stamped\n"
            "\n"
            "The wiki lint's review-due check runs from the NEWER of last_reviewed and\n"
            "updated, so a page read every week stops showing up as review-due merely\n"
            "because nobody has edited it. A page never stamped behaves exactly as before.\n"
            "Each stamp is one frontmatter line, at most once per page per day."),
    },
    "sync_check": {
        "default": "off",
        "values": ["off", "cached", "fetch"],
        "summary": "At session start, say whether the vault is BEHIND its upstream (gt_sync).",
        "detail": (
            "off     no behind-check  (default)\n"
            "cached  compare with the remote-tracking ref already on disk -- no network,\n"
            "        but only as fresh as the last fetch (the line says how old that is)\n"
            "fetch   run one bounded `git fetch` first (a few seconds at most, never a\n"
            "        password prompt), then compare\n"
            "\n"
            "The push check says when this machine has commits origin lacks. The opposite\n"
            "-- origin has commits this machine lacks -- needs a fetch to see, and a fetch\n"
            "is network I/O at session start, which is why it is opt-in. Pull with\n"
            "/gt:gt-sync pull (fast-forward only; never merges or rebases)."),
    },
    "reminder_days": {
        "default": "7",
        "values": ["1", "3", "7", "14", "30"],
        "summary": "Reminders cover overdue items plus items due within this many days.",
        "detail": (
            "Overdue items are always included. A row of <vault>/deadlines.md due within\n"
            "this many days is included too  (default 7). Applies to the push channels\n"
            "(reminder_macos, reminder_relay, reminder_email); the session-start MUST DO\n"
            "block keeps its own 14-day window."),
    },
    "reminder_macos": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "Reminder channel: a macOS notification from the scheduled reminder job.",
        "detail": (
            "off  no notification  (default)\n"
            "on   the `reminder` job (gt_schedule.py install reminder) posts one macOS\n"
            "     notification listing overdue and near items. No credential.\n"
            "\n"
            "Setup and test: gt_reminder.py setup macos / gt_reminder.py check macos."),
    },
    "reminder_relay": {
        "default": "off",
        "values": ["off", "sms", "discord"],
        "summary": "Reminder channel: SMS or Discord through your notification relay.",
        "detail": (
            "off      nothing is sent  (default)\n"
            "sms      POST the reminder to your relay, routed to SMS\n"
            "discord  POST the reminder to your relay, routed to Discord\n"
            "\n"
            "The relay URL and any credential live in a mode-600 file written by your\n"
            "secrets store (~/.claude/golden-thread/reminder/relay.json), never here.\n"
            "Setup and test: gt_reminder.py setup relay / gt_reminder.py check relay."),
    },
    "reminder_email": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "Reminder channel: an email through your SMTP server.",
        "detail": (
            "off  nothing is sent  (default)\n"
            "on   the reminder job sends one email over SMTP (STARTTLS or SSL)\n"
            "\n"
            "Server, addresses and password live in a mode-600 file written by your secrets\n"
            "store (~/.claude/golden-thread/reminder/email.json), never here.\n"
            "Setup and test: gt_reminder.py setup email / gt_reminder.py check email."),
    },
    # ---- 0.18.1: execution (fast build loop, execution metrics, measured profile) ----
    "execution_metrics": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Record one row per execution of tests, pipeline steps and gt skills (gt_metrics.py).",
        "detail": (
            "on   every instrumented run -- tests/prun.py, a release pipeline step, a gt skill --\n"
            "     appends one row: process, duration, exit, units, parallelism, tokens where\n"
            "     known, machine, commit. Arguments are HASHED, never stored, and a value with a\n"
            "     credential shape is stored only as its hash  (default)\n"
            "off  nothing is recorded, by anything\n"
            "\n"
            "The rows live in the vault project's metrics/ folder (or ~/.claude/golden-thread/\n"
            "metrics/ for a project with no folder) and never leave the machine except through\n"
            "the vault. `/gt:gt-optimize --execution` reads them: a rolling baseline per process,\n"
            "regressions against it, and savings that are measured rather than assumed."),
    },
    "scoped_receipts": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "On a feature branch, accept a test receipt from the tests mapped to the changed files.",
        "detail": (
            "on   on a branch that is not the default branch, the commit guard accepts a\n"
            "     receipt from `tests/run.sh --affected` (prun.py --affected) for the files it\n"
            "     covered. The default branch, and every release gate, still need a FULL-suite\n"
            "     receipt  (default)\n"
            "off  every commit needs a full-suite receipt\n"
            "\n"
            "Why: gt 0.17.11 ran its ~2,400-test suite about eight times in one day, including\n"
            "for single-tool changes. The full suite stays required once per release."),
    },
    "test_tmpdir": {
        "default": "noindex",
        "values": ["off", "noindex"],
        "summary": "Where the test runner puts throwaway files (TMPDIR for prun.py).",
        "detail": (
            "off      leave TMPDIR as the system sets it\n"
            "noindex  (default since 0.21.0) a cache folder Spotlight and the sync agents ignore:\n"
            "         ~/Library/Caches/gt-tests.noindex on macOS, $XDG_CACHE_HOME/gt-tests\n"
            "         (else ~/.cache/gt-tests) elsewhere\n"
            "\n"
            "GT_TEST_TMPDIR=<path> overrides both for one run. On 2026-10-01 a full local run\n"
            "drove a Mac's load to 50-96: every throwaway install was file churn that the\n"
            "indexer, the sync agents and the virus scanner all reacted to. Known snag: a\n"
            "folder carrying com.apple.provenance refused copies from an Intel Homebrew Python\n"
            "(EPERM) -- `gt_doctor.py --only execution` reports a translated (Rosetta) shell."),
    },
    "runners": {
        "default": "",
        "values": None,
        "validate": "comma-separated ssh host aliases, or empty",
        "summary": "Remote hosts that may run tests and calibration (prun.py --hosts, gt_bench.py --hosts).",
        "detail": (
            "empty  no host is ever contacted  (default)\n"
            "a,b    these ssh aliases may receive a `git archive` of the COMMITTED tree (code\n"
            "       only -- nothing from the vault ever leaves the machine) and run tests or\n"
            "       the calibration workload there\n"
            "\n"
            "Opt-in by design: nothing is sent anywhere unless it is listed here. gt_bench.py\n"
            "--hosts measures each one; prun.py --hosts splits units across them, heaviest\n"
            "first, attributes every failure to its host, and reports an unreachable host as\n"
            "skipped -- never as passing."),
    },
    # install_demo was removed in 0.14.0: the demo is a module, and whether it is
    # installed is a module choice (install.sh --with/--without demo, recorded in
    # ~/.claude/golden-thread/install-choices.json). A user's install_demo key in
    # vault-config.json is left alone; a later migration removes it.
    # -- 0.18.1: memory and gt-optimize ------------------------------------------------
    "knowledge_access_log": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Log each Read of a Knowledge/ page to <vault>/usage/knowledge.jsonl (local, never committed).",
        "detail": (
            "on   the log_knowledge_read hook (PostToolUse on Read) appends one line per\n"
            "     Knowledge page read: page, short session id, date  (default)\n"
            "off  nothing is logged; gt-optimize's knowledge-unused finding then has no new\n"
            "     evidence and reports from whatever the log already holds\n"
            "\n"
            "Git shows when a page was WRITTEN, never whether anyone READ it, so a page written\n"
            "once and never consulted looked exactly like one read every week. The log lives\n"
            "in usage/, which the hook makes git-ignored with its own usage/.gitignore."),
    },
    "knowledge_unread_days": {
        "default": "90",
        "values": ["30", "60", "90", "180", "365"],
        "summary": "Days without a read before a Knowledge page is labelled unread.",
        "detail": (
            "gt-query labels such a match [unread since <date>] (and orders equally good\n"
            "matches by last use, then last read); gt-optimize lists such pages as review\n"
            "candidates. Nothing expires, moves or is hidden because of it. With no read log\n"
            "(usage/knowledge.jsonl) neither changes anything (gt_use.py, 0.21.0)."),
    },
    "knowledge_read_often": {
        "default": "10",
        "values": ["5", "10", "20", "40"],
        "summary": "Reads in 30 days that make a never-used Knowledge page a review candidate.",
        "detail": (
            "A page read at least this often in 30 days with no USE -- never cited by a later\n"
            "write-back, answered from, enforced or acted on (gt_use.py) -- is listed by\n"
            "gt-optimize as read often but never used. Core rules and pages marked\n"
            "`preventive: true` are never listed as unused: an obeyed rule never fires (0.21.0)."),
    },
    "optimize_session_days": {
        "default": "30",
        "values": ["7", "30", "90", "180"],
        "summary": "Window, in days, for gt-optimize's session member (prompt-cache cost).",
        "detail": (
            "How far back gt_optimize_session.py reads the Claude Code transcripts when\n"
            "classifying cache writes (cold / growth / expiry / invalid). --days overrides it\n"
            "for one run. A longer window catches a project untouched this month whose old\n"
            "sessions were the largest cost; a shorter one reflects current habits."),
    },
    "optimize_avoidable_pct": {
        "default": "50",
        "values": ["25", "50", "65", "75"],
        "summary": "gt_optimize_session.py --check with no PCT: exit 3 above this avoidable share.",
        "detail": (
            "The avoidable share is cache writes caused by expiry (a session idled past its\n"
            "cache lifetime) or invalidation (the cached prefix changed), as a percentage of\n"
            "all cache writes. `--check PCT` names a threshold for one run; this is the one\n"
            "used when PCT is left out."),
    },
    "memory_contradiction_check": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Whether /gt:gt-work checks memory notes it wrote against their neighbours for contradictions.",
        "detail": (
            "on   after write-back, gt_memory_check.py compares the memory notes written or\n"
            "     changed this session with same-topic notes in the project, and shows each\n"
            "     pair whose sentences disagree (enabled/disabled, a different value for the\n"
            "     same key) with a question for you. Nothing is changed  (default)\n"
            "off  the pass is skipped"),
    },
    "promotion_candidates": {
        "default": "on",
        "values": ["off", "on"],
        "summary": "Whether /gt:gt-work lists promotion candidates it can detect mechanically.",
        "detail": (
            "on   after write-back, gt_promote_detect.py lists memory notes edited in 3 of\n"
            "     the project's last 5 memory commits (-> research.md / decisions.md),\n"
            "     research.md sections that overlap another project's (-> Knowledge/), and\n"
            "     global-memory notes naming a project (gt_lint's global-scope-leak,\n"
            "     -> demotion). Nothing is promoted without your yes  (default)\n"
            "off  the pass is skipped"),
    },
    "promotion_overlap": {
        "default": "80",
        "values": ["60", "70", "80", "90"],
        "summary": "Token overlap (%) at which two projects' research.md sections are a Knowledge/ candidate.",
        "detail": (
            "Overlap is the shared distinct words of two sections over the smaller section's\n"
            "distinct words (no stemming). Lower finds more, and more noise."),
    },
    # -- the validation host (0.18.1): gt_check.py runs module checkers, gt_apply.py
    #    writes the fixes they propose. --
    "commit_checks": {
        "default": "off",
        "values": ["off", "on"],
        "summary": "Refuse a `git commit` whose staged content no passing gt_check.py run covers.",
        "detail": (
            "off  commits behave exactly as before; checkers run only when asked  (default)\n"
            "on   the commit guard refuses a commit unless every staged file's EXACT staged\n"
            "     bytes appear in a gt_check.py receipt on which every applicable checker\n"
            "     passed. Run `gt_check.py run --staged`, then commit.\n"
            "\n"
            "One guard for every module's checkers, not one hook per module. `cannot-check`\n"
            "(a missing tool, a timeout, malformed output) is not a pass. Per commit:\n"
            "GT_CHECK_GATE=off git commit ...; per repo: `.gt-no-test-gate`."),
    },
    "addon_fixes": {
        "default": "propose",
        "values": ["off", "propose", "apply"],
        "summary": "What happens to the fixes a checker proposes: ignored, listed for you, or applied.",
        "detail": (
            "off      proposals are discarded; findings are reported as before\n"
            "propose  proposals are kept and shown with their diffs; nothing changes until\n"
            "         you run `gt_apply.py apply <id>` (or --all)  (default)\n"
            "apply    after a check run, proposals are applied automatically -- but only a\n"
            "         FIRST-PARTY checker's (its script matches the release MANIFEST); any\n"
            "         other is capped at propose, and the output says so\n"
            "\n"
            "Every apply re-reads the file under a lock and writes only if its hash still\n"
            "equals the one the checker examined; runs EVERY applicable checker on a staged\n"
            "copy first; refuses protected paths, files claimed by another live session,\n"
            "files outside the checker's findings, and content rules (exec bits, new files,\n"
            "symlinks, invisible Unicode, secret/scrub matches, size, HTML scripts and new\n"
            "hosts). Applied fixes are left uncommitted; `gt_apply.py undo <id>` restores."),
    },
    "addon_fix_size_limit": {
        "default": "16k",
        "values": ["1k", "4k", "16k", "64k", "256k"],
        "summary": "Largest size change (bytes added or removed) one add-on fix may make.",
        "detail": (
            "A formatter's fix is usually small. A proposal that grows or shrinks a file by\n"
            "more than this is refused by gt_apply.py whatever its grant -- a fix that\n"
            "large is a rewrite, and a rewrite needs a person.  (default 16k)"),
    },
}

# -- Models and cost (0.21.0) ----------------------------------------------------------------
HINTS_GROUP = "Hints"
HINTS_ORDER = ("vault_hints", "next_step_hints", "prompt_hints", "hints_as_choices")
MODELS_GROUP = "Models and cost"
SETTINGS["model_profile"] = {
    "default": "inherit",
    "values": ["average", "very-high", "inherit"],
    "summary": "The model and effort each skill runs at (a profile); applied at once.",
    "detail": (
        "average    fast skills haiku, balanced sonnet medium, deep opus high  (intent pack)\n"
        "very-high  opus xhigh everywhere\n"
        "inherit    gt writes nothing: every skill runs on the session's model  (default)\n"
        "\n"
        "Kept with the install choices (install-choices.json), not in vault-config.json:\n"
        "`set model_profile X` records the choice and re-applies it to the installed skills and\n"
        "agents, exactly as `gt_model_policy.py choose X` then `apply`. Per-skill and per-agent\n"
        "overrides, with each agent's cost and calibration verdict: /gt:gt-settings models."),
    "group": MODELS_GROUP,
    "plain": {
        "intro": (
            "Claude comes in sizes: small models are quick and cheap, large ones think harder and "
            "cost more. gt's skills are of different kinds, and this picks which size each one runs "
            "on. It changes cost and speed, never what Claude is allowed to do."),
        "options": {
            "average": {
                "summary": "Quick jobs on a small model, balanced ones on a middle model, deep ones on the large model.",
                "who": "Most people: good answers where they matter, low cost where they do not.",
                "without_asking": "Each gt skill picks its model for you.",
                "still_asks": "Nothing; you can still set one skill's model yourself.",
                "example": "Filing a note uses the small model; reviewing a design uses the large one.",
                "trade_off": "A cheap, sensible mix, with the occasional job that would have been better on a bigger model.",
            },
            "very-high": {
                "summary": "The large model at high effort for every skill.",
                "who": "People who want the best answer every time and do not mind the cost or the wait.",
                "without_asking": "Every gt skill runs on the largest model.",
                "still_asks": "Nothing.",
                "example": "Filing a note uses the large model too.",
                "trade_off": "The best quality, the slowest and the most expensive.",
            },
            "inherit": {
                "summary": "gt chooses nothing: every skill uses whatever model your session is on.",
                "who": "People who already manage models themselves.",
                "without_asking": "Nothing; gt leaves the choice alone.",
                "still_asks": "Nothing.",
                "example": "You start a session on the middle model: every skill uses it.",
                "trade_off": "Full control and no tuning from gt.",
            },
        },
        "recommended": "average",
        "not_sure": "Choose average. It is the setting gt is tested with, and it keeps routine work cheap.",
        "change_later": "Run /gt:gt-settings models profile <name> in Claude (or gt_settings.py set model_profile <name>); it applies at once.",
    },
}
# -- Tokens and replies (0.21.0, items 37, 39, 42, 43) ---------------------------------------
TOKENS_GROUP = "Tokens and replies"
TOKENS_ORDER = ("token_profile", "reply_length", "allowance_note", "allowance_note_threshold")

# Row #43 of the 0.21.0 plan, exactly: one choice sets these seven settings. `model_profile` for
# minimum is the shipped `average` profile (the cheapest profile gt has); calibrated also applies the
# stored calibration verdicts, if any; maximum is `very-high`. skeptic_pass and vault_hints take
# the shipped defaults for calibrated (on, off), off/off for minimum and on/on for maximum.
TOKEN_PROFILES = {
    "minimum": {"model_profile": "average", "reply_length": "terse", "skeptic_pass": "off",
                "vault_hints": "off", "expiry_guard": "auto", "ingest_specialists": "on",
                "model_calibration": "off"},
    "calibrated": {"model_profile": "average", "reply_length": "brief", "skeptic_pass": "on",
                   "vault_hints": "off", "expiry_guard": "auto", "ingest_specialists": "on",
                   "model_calibration": "report"},
    "maximum": {"model_profile": "very-high", "reply_length": "normal", "skeptic_pass": "on",
                "vault_hints": "on", "expiry_guard": "warn-only", "ingest_specialists": "on",
                "model_calibration": "report"},
}


SETTINGS["token_profile"] = {
    "default": "calibrated",
    "values": ["minimum", "calibrated", "maximum", "manual"],
    "summary": "One choice that sets seven token-related settings together: minimum, calibrated "
               "or maximum (0.21.0).",
    "detail": (
        "minimum     model_profile average, reply_length terse, skeptic_pass off, vault_hints off,\n"
        "            expiry_guard auto, ingest_specialists on, model_calibration off\n"
        "calibrated  model_profile average (and any stored calibration verdicts applied), reply_length\n"
        "            brief, skeptic_pass on, vault_hints off, expiry_guard auto, ingest_specialists\n"
        "            on, model_calibration report  (default)\n"
        "maximum     model_profile very-high, reply_length normal, skeptic_pass on, vault_hints on,\n"
        "            expiry_guard warn-only, ingest_specialists on, model_calibration report\n"
        "manual      gt sets nothing; every setting is yours\n"
        "\n"
        "Choosing a profile writes those seven settings now. Change any one of them afterwards and\n"
        "`show` reads 'manual (based on <profile>)' -- the profile you chose is kept, and changing the\n"
        "setting back restores its name. Shown at the top of /gt:gt-settings."),
    "group": TOKENS_GROUP,
    "plain": {
        "intro": (
            "Claude's work costs tokens, which is your plan's allowance or your bill. Seven of gt's "
            "settings trade quality against that cost: which model size each skill uses, how long "
            "replies are, whether a second agent double-checks your notes, whether gt suggests pages, "
            "how an idle session is trimmed, whether helper agents are used for imports, and whether "
            "gt tests its own model choices. This picks all seven at once."),
        "options": {
            "minimum": {
                "summary": "Spend as little as possible: short answers, no double-checking, the cheaper models.",
                "who": "People watching their allowance closely.",
                "trade_off": "The lowest cost, with the shortest replies and the least checking."},
            "calibrated": {
                "summary": "A balanced default: brief answers, checking on, and gt tests its own model choices (the default).",
                "who": "Most people.",
                "trade_off": "A modest extra cost now and then, when gt re-tests its model choices, in return for models that fit the work."},
            "maximum": {
                "summary": "Quality first: the largest model, full-length answers, page suggestions on.",
                "who": "People who want the best answer every time and do not mind the cost.",
                "trade_off": "The best quality, the highest cost and the most to read."},
            "manual": {
                "summary": "gt picks nothing; you set each of the seven yourself.",
                "who": "People who already tune these settings by hand.",
                "trade_off": "Full control, and nothing is kept in step for you."},
        },
        "recommended": "calibrated",
        "not_sure": "Choose calibrated. It keeps replies short and tests its own choices, and every one of the seven can still be changed alone.",
        "change_later": "Run /gt:gt-settings token_profile <minimum|calibrated|maximum> in Claude (or gt_settings.py set token_profile <value>); it takes effect at once.",
    },
}
SETTINGS["reply_length"] = {
    "default": "normal",
    "values": ["normal", "brief", "terse"],
    "summary": "How long Claude's replies are: normal, brief (about 5 lines, answer first) or terse "
               "(one or two sentences) (0.21.0).",
    "detail": (
        "normal  nothing is added  (default)\n"
        "brief   the per-turn hook adds one line: answer first, keep the reply to about 5 lines unless\n"
        "        the user asks for more\n"
        "terse   one line: answer first, in one or two sentences, no preamble or recap\n"
        "\n"
        "It asks Claude, it does not cut Claude off: a reply that needs more says so, and 'tell me more'\n"
        "always gets more. Works in the terminal and in Claudian. One line of context per turn."),
    "group": TOKENS_GROUP,
    "plain": {
        "intro": (
            "Claude can answer at length or briefly. This asks it to give the answer first and keep the "
            "reply short, so you read less and spend less. It changes only how much Claude writes, never "
            "what it does, and you can always ask for more."),
        "options": {
            "normal": {"summary": "Claude answers as it normally would."},
            "brief": {"summary": "The answer first, then about five lines at most unless you ask for more."},
            "terse": {"summary": "The answer first, in one or two sentences, with no preamble or recap."},
        },
        "recommended": "brief",
        "not_sure": "Choose brief. Most answers fit in five lines, and 'tell me more' is one sentence away.",
        "change_later": "Run /gt:gt-settings reply_length <normal|brief|terse> in Claude (or gt_settings.py set reply_length <value>); it applies from the next message.",
    },
}
SETTINGS["allowance_note"] = {
    "default": "on",
    "values": ["off", "on"],
    "summary": "Above allowance_note_threshold, replies carry your last saved 5-hour and weekly allowance "
               "(0.21.0).",
    "detail": (
        "on   when the last saved 5-hour or weekly reading is at or above the threshold, the per-turn\n"
        "     hook adds one line with both figures and their age, so Claude says so in its answer\n"
        "     (default). For apps with no status line, Claudian above all.\n"
        "off  nothing is added\n"
        "\n"
        "The reading is the usage module's latest.json (written each time a terminal session renders\n"
        "its status line); the age says how old it is. A window that has reset since is not quoted.\n"
        "Without the usage module there is no reading and nothing is added."),
    "group": TOKENS_GROUP,
    "plain": {
        "intro": (
            "Your plan has a 5-hour and a weekly allowance. Claudian cannot show them, so when you are "
            "close to a limit gt asks Claude to mention the last figure it saved, and how old it is, in "
            "its answer. Nothing is said while you are below the threshold."),
        "options": {
            "on": {"summary": "Mention the saved allowance in replies once it is at or above the threshold."},
            "off": {"summary": "Never add the allowance to replies."},
        },
        "recommended": "on",
        "not_sure": "Leave it on. It is silent until you are near a limit.",
        "change_later": "Run /gt:gt-settings allowance_note <on|off> in Claude (or gt_settings.py set allowance_note <value>).",
    },
}
SETTINGS["allowance_note_threshold"] = {
    "default": "90",
    "values": ["50", "70", "80", "90", "95"],
    "summary": "The percent of the 5-hour or weekly allowance at which replies start to carry it (0.21.0).",
    "detail": (
        "The allowance note speaks once either the 5-hour or the weekly saved reading is at or above\n"
        "this percent. 90 is the default; 50 speaks early, 95 only when you are nearly out."),
    "group": TOKENS_GROUP,
    "plain": {
        "intro": "How close to a limit you must be before Claude mentions your allowance in its answers.",
        "options": {
            "50": {"summary": "From half the allowance used."},
            "70": {"summary": "From 70% used."},
            "80": {"summary": "From 80% used."},
            "90": {"summary": "From 90% used (the default)."},
            "95": {"summary": "Only when you are nearly out, 95% used."},
        },
        "recommended": "90",
        "not_sure": "Leave it at 90: late enough to be rare, early enough to act.",
        "change_later": "Run /gt:gt-settings allowance_note_threshold <percent> in Claude (or gt_settings.py set allowance_note_threshold <value>).",
    },
}
SETTINGS["hints_as_choices"] = {
    "default": "on",
    "values": ["off", "on"],
    "summary": "After a gt command, also offer 2-3 next steps as a clickable question (0.21.0).",
    "detail": (
        "on   the per-turn hook adds one line asking Claude to end a gt command with ONE question\n"
        "     (AskUserQuestion) that offers 2-3 next steps and lets you type your own. The steps come\n"
        "     from the same next-step data as the 'Next:' line (gt_hints.py choices <command>). It\n"
        "     works in the terminal and in Claudian. One line of context per turn.  (default)\n"
        "off  no question; the text 'Next:' line (next_step_hints) is unaffected either way"),
    "plain": {
        "intro": (
            "After a gt command finishes, Claude can offer the usual next steps as buttons you click, "
            "with room to type something else. The plain 'Next:' line under the reply stays as it is."),
        "options": {
            "on": {"summary": "Offer two or three next steps as a clickable question after a gt command."},
            "off": {"summary": "No question; only the 'Next:' line."},
        },
        "recommended": "on",
        "not_sure": "Leave it on. You can always ignore the question and type what you want.",
        "change_later": "Run /gt:gt-settings hints_as_choices <on|off> in Claude (or gt_settings.py set hints_as_choices <value>).",
    },
}

# -- Calibration schedule and budgets (0.21.0, item 44) --------------------------------------
SETTINGS["model_calibration_schedule"] = {
    "default": "manual",
    "values": ["off", "weekly", "monthly", "manual"],
    "summary": "When gt re-tests its model choices by itself: weekly, monthly, only when you run it, or "
               "never (0.21.0).",
    "detail": (
        "off      never; /gt:gt-calibrate and gt-optimize are the only ways\n"
        "manual   only when you run it  (default)\n"
        "weekly   a pass runs on its own once a week\n"
        "monthly  a pass runs on its own once a month\n"
        "\n"
        "The next run is always counted from the LAST run of any kind, so a manual run today moves the\n"
        "next automatic one to a week (a month) from today. A run missed because the machine was off\n"
        "happens once, at the next session start -- never a backlog. A scheduled run stays inside\n"
        "model_calibration_budget, model_calibration_max_weekly_pct and the preset's cap. The next\n"
        "due date is shown here and on /gt:gt-settings models."),
    "plain": {
        "intro": (
            "gt can re-test its own model choices now and then, because models change and so does your "
            "work. A test costs a little of your allowance. This sets whether it happens by itself and "
            "how often."),
        "options": {
            "off": {"summary": "Never by itself; only when you ask for it."},
            "manual": {"summary": "Only when you run it (the default)."},
            "weekly": {"summary": "About once a week, counted from the last test."},
            "monthly": {"summary": "About once a month, counted from the last test."},
        },
        "recommended": "manual",
        "not_sure": "Leave it at manual until you have seen what one test costs (gt_model_calibrate.py plan --estimate).",
        "change_later": "Run /gt:gt-settings model_calibration_schedule <off|manual|weekly|monthly> in Claude (or gt_settings.py set model_calibration_schedule <value>).",
    },
}
SETTINGS["model_calibration_budget_preset"] = {
    "default": "none",
    "values": ["none", "home", "corporate"],
    "summary": "A ready-made budget for calibration: home (monthly, small sample) or corporate "
               "(a dollar cap per seat per month) (0.21.0).",
    "detail": (
        "none       no preset; the individual settings apply  (default)\n"
        "home       sets the schedule to monthly and the sample to 2; a pass stops at the weekly\n"
        "           allowance line (model_calibration_max_weekly_pct)\n"
        "corporate  sets the schedule to monthly and the sample to 3; a pass is refused once this\n"
        "           month's calibration spend (an estimate, kept in\n"
        "           ~/.claude/golden-thread/model-calibration/spend.json) plus the next pass would pass\n"
        "           model_calibration_seat_cap_usd\n"
        "\n"
        "Setting a preset writes those values once; you can change them afterwards. See what a pass\n"
        "costs first: gt_model_calibrate.py plan --estimate."),
    "plain": {
        "intro": (
            "Re-testing models costs tokens. A preset sets a sensible spending limit for your situation "
            "so a test can never run away with your budget."),
        "options": {
            "none": {"summary": "No preset; you set the schedule, sample and budget yourself (the default)."},
            "home": {"summary": "For a personal plan: a small test once a month that stops when your weekly allowance is nearly used."},
            "corporate": {"summary": "For a paid, metered account: a small test once a month, refused once the month's spend would pass a dollar cap per person."},
        },
        "recommended": "none",
        "not_sure": "Choose home on a personal plan, corporate on a metered company account; otherwise none.",
        "change_later": "Run /gt:gt-settings model_calibration_budget_preset <none|home|corporate> in Claude (or gt_settings.py set model_calibration_budget_preset <value>).",
    },
}
SETTINGS["model_calibration_seat_cap_usd"] = {
    "default": "25",
    "values": ["5", "10", "25", "50", "100", "250"],
    "summary": "With the corporate preset: the most calibration may cost one person in a month, in USD "
               "(0.21.0).",
    "detail": (
        "Used only while model_calibration_budget_preset is corporate. Each pass that runs adds its\n"
        "estimate to this month's total (spend.json); a pass whose estimate would take the month over\n"
        "this is refused before anything is run. The month starts on the 1st."),
    "plain": {
        "intro": "The most one person's model re-testing may cost in a calendar month, in US dollars. It applies with the corporate preset.",
        "options": {k: {"summary": "Up to %s dollars a month." % k} for k in ("5", "10", "25", "50", "100", "250")},
        "recommended": "25",
        "not_sure": "Leave it at 25; one pass usually costs well under a dollar.",
        "change_later": "Run /gt:gt-settings model_calibration_seat_cap_usd <dollars> in Claude (or gt_settings.py set model_calibration_seat_cap_usd <value>).",
    },
}

# the order they are listed in; each is also reachable by its own name
MODELS_ORDER = ("model_profile", "agent_models", "adhoc_agent_model", "model_calibration", "model_calibration_sample",
                "model_calibration_budget", "model_calibration_runner",
                "model_calibration_min_runs", "model_calibration_remind_days",
                "model_calibration_max_weekly_pct", "model_calibration_schedule",
                "model_calibration_budget_preset", "model_calibration_seat_cap_usd",
                "pipeline_keep_prompts", "pipeline_prompt_keep")
for _n in MODELS_ORDER:
    SETTINGS[_n]["group"] = MODELS_GROUP
for _n in HINTS_ORDER:
    SETTINGS[_n]["group"] = HINTS_GROUP


# -- Paranoid: installed-code integrity (0.21.0, item 21) ------------------------------------
# One master and one switch per part, grouped behind it. Children follow the master unless set
# explicitly (stored on / off / follow); every key is a security setting. The data and the plain
# words live in gt_paranoid.py, so the installer's questions (item #33) can reuse the text.
def _paranoid_plain(gp):
    """The plain-language entry for the master switch, built from gt_paranoid.TEXT so the words
    live once (item #33: the installer, --questions, gt-init and explain all read this)."""
    t = gp.TEXT[gp.MASTER]
    return {
        "intro": (
            "Paranoid protects gt's own program files from being changed behind your back. Those "
            "files include gt's hooks (small programs Claude Code runs automatically at set "
            "moments, which carry out your rules), so whoever can edit them can switch the rules off."),
        "options": {
            "off": {
                "summary": "gt's files are ordinary files you own; nothing extra protects them (the default).",
                "who": "Most people starting out, and anyone who upgrades often and wants no extra steps.",
                "without_asking": "Anything running as you, Claude included, can edit gt's program files if your security level lets it edit files.",
                "still_asks": "Whatever your security level asks; nothing extra.",
                "example": "A command Claude was tricked into running rewrites one of gt's guards: nothing stops it, and gt notices at the next session start.",
                "trade_off": "No extra steps on upgrades, but the protection is noticing afterwards, not preventing.",
            },
            "on": {
                "summary": "gt's installed files are locked by the operating system, unlocking needs you, and a signed list of the files is checked before each hook runs.",
                "who": t["who"],
                "without_asking": "Nothing about gt's own files: they cannot be changed until you unlock them.",
                "still_asks": "A confirmation (Touch ID, or a typed line) each time gt's files must be unlocked for an upgrade or a repair; some shell commands that touch gt's folders stop and ask.",
                "example": "You run an upgrade: gt asks you to confirm, unlocks the files, upgrades them and locks them again.",
                "trade_off": t["costs"],
            },
        },
        "parts": [title for _k, _n, title in gp.PARTS],
        "more": gp.explain_text(gp.MASTER),
        "recommended": "off",
        "not_sure": "Leave it off for now. Turn it on when you let Claude run commands on a machine that holds things you care about, and you are ready to confirm an unlock on upgrades.",
        "change_later": "Run gt_settings.py set paranoid on (or off) in a terminal; the status is shown by gt_integrity.py status.",
    }


def _paranoid_settings():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import gt_paranoid as gp
    except Exception:                            # noqa: BLE001 - no group, no settings
        return
    SETTINGS[gp.MASTER] = {
        "default": "off", "values": ["off", "on"], "group": gp.GROUP,
        "summary": "Lock gt's installed code so it cannot be changed behind your back.",
        "detail": gp.explain_all() + "\n" + (
            "Set with:  python3 gt_settings.py set paranoid on | off\n"
            "A part:    python3 gt_settings.py set paranoid.<part> on | off | follow\n"
            "Status:    python3 ~/.claude/golden-thread/hooks/gt_integrity.py status"),
        "plain": _paranoid_plain(gp)}
    for key, _num, title in gp.PARTS:
        SETTINGS[key] = {
            "default": "off", "values": ["off", "on", "follow"], "group": gp.GROUP,
            "summary": "Paranoid part: %s." % title,
            "detail": gp.explain_text(key) + "\nfollow = do what `paranoid` does; on / off = "
                                           "decide this part yourself (off is logged)."}
    # single-use action tokens (item #32): the group's own master, then one switch per action
    for key in (gp.ACTION_MASTER,) + gp.ACTION_KEYS:
        name = key[len(gp.ACTION_PREFIX):] if key != gp.ACTION_MASTER else None
        SETTINGS[key] = {
            "default": "off", "values": ["off", "on", "follow"], "group": gp.GROUP,
            "summary": ("Single-use action tokens: the master for every action below." if not name
                        else "Single-use token for %s: %s." % (name, gp.ACTIONS_BY_NAME[name]["covers"])
                        + ("" if gp.ACTIONS_BY_NAME[name]["wired"] else " (not enforced yet)")),
            "detail": gp.explain_text(key) + "\nfollow = do what the group above does; on / off = "
                                           "decide this action yourself (off is logged)."}


_paranoid_settings()


def _model_profile():
    """The recorded model profile (install-choices.json), or None when none was recorded."""
    try:
        with open(os.path.expanduser("~/.claude/golden-thread/install-choices.json"),
                  encoding="utf-8") as fh:
            p = (json.load(fh).get("model") or {}).get("profile")
        return p if p in SETTINGS["model_profile"]["values"] else None
    except Exception:                            # noqa: BLE001
        return None


def _module_settings():
    """Register the settings every effectively-ON module declares (0.14.0).

    Read from each module's newest module.json via the gt_components beside this file
    (not whatever `gt_components` sys.path would find), so the registry and the
    installer agree on which modules are on. The plugin root is this file's own when it
    runs from a release (<root>/golden-thread/<ver>/scripts), otherwise the one
    install.sh wired into settings.json. A module setting never overrides a gt one.
    Anything missing -- an older gt_components, no plugin root -- registers nothing.

    0.15.0, the first modules with settings (watch, report card), found three gaps:

      * a module's `detail` was dropped, so `explain` lost the text saying why a setting
        exists. It is now carried when the module declares it.
      * with no reachable plugin source (moved, renamed, a gt-src folder deleted) nothing
        registered, so `get("watch")` answered None from inside the installed hook. The
        installed plugin caches are now read instead: install.sh keeps a cache only for
        modules that are on, and copies module.json into it.
      * design §4 "Remove": a value the user set survives a module being switched off.
        A module that is in the source tree but OFF registers its settings marked
        `orphaned` -- still readable, shown only when set, never deleted -- so turning it
        back on finds the value where it was left.
    """
    try:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "gt_components_for_settings", os.path.join(here, "gt_components.py"))
        gc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gc)
        ver_dir = os.path.dirname(here)
        if (os.path.basename(os.path.dirname(ver_dir)) == "golden-thread"
                and gc._newest_release(os.path.dirname(ver_dir)) is not None):
            root, gt_version = os.path.dirname(os.path.dirname(ver_dir)), os.path.basename(ver_dir)
        else:
            root, gt_version = gc.plugin_root_from_settings(), None
        mods = []                                   # (name, data, orphaned)
        if root and os.path.isdir(root):
            det = gc.module_detail(root, gt_version=gt_version)
            for m in gc.discover_modules(root):
                if m["reasons"]:
                    continue
                mods.append((m["name"], m["data"], det.get(m["name"], {}).get("state") != "on"))
        else:
            mods = [(n, d, False) for n, d in _cached_modules(gc)]
        # ON modules first, so an orphaned copy of a key never shadows a live one.
        for name, data, orphaned in sorted(mods, key=lambda t: t[2]):
            for s in data.get("settings") or []:
                if s["key"] in SETTINGS:
                    continue
                detail = s.get("detail") if isinstance(s.get("detail"), str) else s["summary"]
                SETTINGS[s["key"]] = {
                    "default": s["default"], "values": list(s["values"]),
                    "summary": s["summary"], "module": name, "orphaned": orphaned,
                    "detail": "%s\n\nProvided by the %s module%s." % (
                        detail, name, " (currently OFF: a value you set is kept, and takes "
                        "effect again with install.sh --with %s)" % name if orphaned else "")}
    except Exception:
        return


def _cached_modules(gc):
    """-> [(name, data)] for every valid module.json in the installed plugin caches."""
    base = os.path.expanduser("~/.claude/plugins/cache/golden-thread-plugin")
    out = []
    try:
        plugins = sorted(os.listdir(base))
    except OSError:
        return out
    for plugin in plugins:
        vd = gc._newest_release(os.path.join(base, plugin))
        if vd is None:
            continue
        data, reasons = gc.validate_module(vd)
        if data and not reasons:
            out.append((data["name"], data))
    return out


_module_settings()


def _gate_settings():
    """Register `gate.<name>` for every presence gate (0.21.0). Each is an on/off switch whose
    state lives in the unlock policy (gt_unlock.py gate ...), not in vault-config.json, so what
    `show` says is what the authority enforces."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import gt_unlock_policy as gp
    except Exception:                            # noqa: BLE001 - no registry, no gate switches
        return
    for name in sorted(gp.GATES):
        g = gp.GATES[name]
        if g.get("param_required"):
            continue
        always = bool(g.get("always_on"))
        SETTINGS["gate.%s" % name] = {
            "default": "on" if always else "off", "values": ["on"] if always else ["off", "on"],
            "gate": name,
            "summary": "Presence gate (%s): %s." % (g["feasibility"], g["summary"]),
            "detail": _gate_detail(name, g)}


def _gate_detail(name, g):
    if g.get("always_on"):
        return ("on   %s  (always)\n"
                "Where it is enforced: %s.\n"
                "This gate has no off position: the scope is a fresh confirmation whenever gt\n"
                "unlock is on, and with unlock off the broker uses Claude Code's own permission\n"
                "prompt and records that weaker factor. `gt_settings.py io show` lists the rest."
                % (g["summary"], g["enforcement"]))
    param = ""
    if g["param"]:
        param = ("\nPer %s: `gate.%s:<%s>` gates only that one (gt_settings.py set "
                 "gate.%s:<%s> on)." % (g["param"], name, g["param"], name, g["param"]))
    return ("off  nothing is wired  (default)\n"
            "on   %s\n"
            "Where it is enforced: %s.%s\n"
            "Turning it on raises the scope to a fresh confirmation in the unlock policy and, for\n"
            "the git gates, installs a hook in the repo you are in (or --repo via gt_unlock.py\n"
            "gate on). Off removes exactly that. With gt unlock off the gate is wired but asks\n"
            "nothing yet. `gt_unlock.py gate check` names any gate whose wiring is missing."
            % (g["summary"], g["enforcement"], param))


def _gate_instance(name):
    """`gate.<gate>:<param>` -> a synthesized spec, else None. Parameterised gates are not
    enumerable, so they are not in SETTINGS; any valid instance is a setting."""
    if not name.startswith("gate."):
        return None
    try:
        import gt_unlock_policy as gp
        gname, param = gp.parse_gate(name[len("gate."):])
    except Exception:                            # noqa: BLE001
        return None
    if not param:
        return None
    g = gp.GATES[gname]
    return {"default": "off", "values": ["off", "on"], "gate": gname, "param": param,
            "summary": "Presence gate (%s) for %s %s: %s." % (g["feasibility"], g["param"], param,
                                                              g["summary"]),
            "detail": _gate_detail(gname, g)}


def _gate_state(spec, name):
    """on / off for a gate setting, read from the user's unlock policy file."""
    try:
        import gt_unlock_policy as _gp
        if _gp.GATES[spec["gate"]].get("always_on"):
            return "on"                          # the I/O broker gates cannot be switched off
    except Exception:                            # noqa: BLE001
        pass
    try:
        import gt_unlock_client
        import gt_unlock_policy as gp
        pol, _prob = None, None
        with open(os.path.join(gt_unlock_client.home(), "policy.json"), encoding="utf-8") as fh:
            pol = json.load(fh)
        label = gp.gate_label(spec["gate"], spec.get("param"))
        return "on" if any(gp.gate_label(e.get("gate"), e.get("param")) == label
                           for e in gp.gates_of(pol)) else "off"
    except Exception:                            # noqa: BLE001
        return "off"


_gate_settings()


def _load():
    try:
        with open(CONFIG) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(d):
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, indent=2)
        fh.write("\n")


# Settings that switch a GUARD off (0.20.1). With gt unlock on, changing one needs the scope
# gt:settings:security -- a fresh confirmation the unlock authority raises -- so a session
# cannot loosen its own guards with one command. With unlock off nothing changes.
SECURITY_KEYS = ("protected_paths", "test_gate", "foreign_checkout_guard", "component_updates",
                 "commit_checks", "addon_fixes", "unlock", "sandbox_mode", "sandbox_vault_reads",
                 "symlink_writes_outside_vault",
                 # loosening what Claude may run without asking must not be one agent command away
                 "lockdown",
                 # the fingerprint guards (0.20.4, independent review): off from a session must ask
                 "push_fingerprint", "push_fingerprint_seal_token", "push_fingerprint_repos",
                 "commit_fingerprint",
                 # how an accept is reviewed is a guard setting too (0.21.0)
                 "accept_review",
                 # what the disk-read broker opens without asking (0.21.0)
                 "io_read_allow", "io_read_max_bytes", "io_tools",
                 # what the network-fetch broker opens without asking (0.21.0, item #20 stage 2)
                 "io_fetch_allow", "io_fetch_max_bytes",
                 # what the disk-write broker may write (0.21.0, item #20 stage 3); the CI lift is
                 # the owner's alone, and io_write_presence has no `off` to guard
                 "io_write_ci", "io_write_max_bytes", "io_write_presence",
                 # a command the calibration pass runs on this machine (0.21.0)
                 "model_calibration_runner",
                 # one agent for reads AND writes removes the split that stops text in a read
                 # result steering a write (0.21.0; registered by the lotr module)
                 "agent_split",
                 # the paranoid group (0.21.0): installed-code integrity. Anything under it is
                 # gated, including keys a later item adds (is_security_key)
                 "paranoid", "paranoid.os_lock", "paranoid.code_write_gate",
                 "paranoid.bash_guard", "paranoid.signed_manifest", "paranoid.plugin_dirs",
                 "paranoid.require_unlock")


def is_security_key(name):
    """A key whose change needs the settings step-up when unlock is on. The paranoid group is
    matched by prefix, so `paranoid.action_tokens.*` (item #32) is covered without a list edit."""
    if name in SECURITY_KEYS:
        return True
    try:
        import gt_paranoid
        return gt_paranoid.is_paranoid_key(name)
    except Exception:                            # noqa: BLE001
        return name == "paranoid" or name.startswith("paranoid.")


def _sandbox_switch(name, value, d):
    """sandbox_mode / sandbox_vault_reads (0.20.1): write or remove the Claude Code settings
    BEFORE the value is recorded, so vault-config.json never says "on" for settings that were
    refused. -> None to proceed, else an exit code (the refusal is printed)."""
    try:
        import gt_sandbox
    except ImportError:
        print("refused: gt_sandbox.py is not beside gt_settings.py; reinstall gt")
        return 1
    mode = value if name == "sandbox_mode" else (d.get("sandbox_mode") or "off")
    reads = value if name == "sandbox_vault_reads" else (d.get("sandbox_vault_reads") or "deny")
    try:
        if mode == "on":
            rep = gt_sandbox.apply(vault=d.get("vault_path"), reads=reads)
        elif name == "sandbox_mode":
            rep = gt_sandbox.remove()
        else:
            return None                          # reads changed while off: nothing to write
    except gt_sandbox.SandboxError as e:
        print("refused: %s" % e)
        return 1
    except OSError as e:
        print("refused: could not write the Claude Code settings (%s). Inside Claude Code's "
              "sandbox ~/.claude is write-protected by design: run this in a terminal." % e)
        return 1
    for c in rep.get("changes") or []:
        print("  " + c)
    for n in rep.get("notes") or []:
        print("  note: " + n)
    if rep.get("restart"):
        print("  Restart Claude Code for this to take effect.")
    return None


def _linux_package_hint(packages, os_release="/etc/os-release", which=None):
    """The install command for `packages` on THIS Linux, from /etc/os-release (ID, ID_LIKE),
    else from which package manager is on PATH. Usability run 2026-10-04: the hint said
    apt-get on a dnf machine."""
    import shutil
    which = which or shutil.which
    ids = []
    try:
        with open(os_release, "r", encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.strip().partition("=")
                if k in ("ID", "ID_LIKE"):
                    ids += v.strip().strip('"').lower().split()
    except OSError:
        pass
    managers = (("apt", ("debian", "ubuntu"), "sudo apt-get install %s"),
                ("dnf", ("fedora", "rhel", "centos"), "sudo dnf install %s"),
                ("pacman", ("arch",), "sudo pacman -S %s"),
                ("zypper", ("suse", "opensuse", "sles"), "sudo zypper install %s"))
    pk = " ".join(packages)
    for _name, family, cmd in managers:
        if any(i in family or any(i.startswith(f) for f in family) for i in ids):
            return cmd % pk
    for name, _family, cmd in managers:
        if which("apt-get" if name == "apt" else name):
            return cmd % pk
    return "install %s with your package manager" % pk


def _sandbox_prereqs_missing(name, value):
    """-> the refusal text when turning sandbox mode on (or changing its reads while it is
    on) cannot work on this Linux / WSL2 because bubblewrap or socat is missing, else None.
    Checked before any unlock confirmation is asked for."""
    d = _load()
    mode = value if name == "sandbox_mode" else (d.get("sandbox_mode") or "off")
    if mode != "on":
        return None
    try:
        import gt_sandbox
        pm = gt_sandbox.platform_mode()
        if pm not in ("linux", "wsl2"):
            return None
        missing = gt_sandbox.linux_deps_missing()
    except Exception:                            # noqa: BLE001 - _sandbox_switch reports it
        return None
    if not missing:
        return None
    pkgs = ["bubblewrap" if m == "bwrap" else m for m in missing]
    return ("refused: sandbox mode on %s needs %s, which %s not installed; Claude Code would "
            "refuse to start without it. Install it with:\n  %s\nthen run this again. Nothing "
            "was changed and no code was asked for."
            % ("WSL2" if pm == "wsl2" else "Linux", " and ".join(missing),
               "is" if len(missing) == 1 else "are", _linux_package_hint(pkgs)))


def _unlock_gate(name):
    """-> None when the change may proceed, else the refusal text. Fails closed when unlock is
    on and the authority cannot be asked."""
    try:
        import gt_unlock_policy
        if not gt_unlock_policy.enabled_fast():
            return None
        import gt_unlock_client
        ans = gt_unlock_client.tty_answer if sys.stdin and sys.stdin.isatty() else None
        v = gt_unlock_client.check("gt:settings:security", request=True,
                                   reason="change the gt setting %s" % name, answer=ans)
    except Exception as e:                       # noqa: BLE001
        return "gt unlock is on and the authority could not be asked (%s)" % type(e).__name__
    return None if v.get("allowed") else "%s (%s)" % (v.get("message"), v.get("code"))


def get(name):
    """Value with the registered default applied. For use by hooks and scripts."""
    spec = SETTINGS.get(name) or _gate_instance(name)
    if name == "paranoid" or name.startswith("paranoid."):
        # the EFFECTIVE value: a part that is not set follows its parent, then the master
        try:
            import gt_paranoid
            return "on" if gt_paranoid.effective(name, _load()) else "off"
        except Exception:                        # noqa: BLE001
            return spec["default"] if spec and spec["default"] in ("on", "off") else "off"
    if not spec:
        return None
    if spec.get("gate"):
        return _gate_state(spec, name)
    if name == "model_profile":
        return _model_profile() or spec["default"]
    if name == "testing_profile":
        try:
            import gt_testing_profile
            return "on" if gt_testing_profile.is_on() else "off"
        except Exception:                        # noqa: BLE001
            return spec["default"]
    if name == "unlock":
        try:
            import gt_unlock_policy
            return "on" if gt_unlock_policy.enabled_fast() else "off"
        except Exception:                        # noqa: BLE001
            return spec["default"]
    v = _load().get(name)
    if v is None and name == "model_calibration_runner":
        return spec["default"]       # unset ships the recommended runner; an explicit "" is kept
    if v is None:
        v = ""
    elif not isinstance(v, str):
        # Including JSON true/false. A `["yes", "no"]` branch mapping those to strings
        # lived here until 0.16.5; no setting has ever declared those values, gt's or a
        # module's, so it never ran. A hand-written `true` is a typed value where a
        # named one belongs, and falls back to the default like any other non-string.
        sys.stderr.write("gt_settings: %s=%s in %s is not a string; using the default %r\n"
                         % (name, json.dumps(v), CONFIG, spec["default"]))
        v = ""
    v = v.strip() if name in CASE_KEPT else v.strip().lower()
    if spec["values"] is None:
        return v if _freeform_ok(name, v) else spec["default"]
    return v if v in spec["values"] else spec["default"]


# settings whose value is case-sensitive (a path, a command line: `--allowedTools` is not
# `--allowedtools`); get() and set_value() leave these as typed
CASE_KEPT = ("io_read_allow", "model_calibration_runner")


def _persona_names(value):
    return [x.strip() for x in value.split(",") if x.strip()]


def _persona_unknown(value):
    """-> the names in a persona value that no installed pack provides ([] when all are known)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import gt_persona
    except ImportError:                          # a copy without the script cannot judge a name
        return []
    valid = gt_persona.valid_names()
    return [n for n in _persona_names(value) if n not in valid]


def _freeform_ok(name, value):
    """Validate a setting whose values are not a closed list.

    Only `parallel_max` is free-form, and it is free-form for a reason: a worker
    ceiling is a number, and enumerating 1..64 in `values` would be a list nobody
    reads pretending to be a type. Anything unparseable falls back to the registered
    default rather than being written through, so a typo cannot silently uncap or
    serialise every run.
    """
    if name == "decision_signals":
        # A phrase list, not a closed set (0.18.1); see its `detail`. One line, non-empty.
        return bool(value.strip()) and "\n" not in value
    if name == "push_fingerprint_repos":        # 0.20.3: absolute paths, comma-separated
        parts = [x.strip() for x in value.split(",") if x.strip()]
        return all(os.path.isabs(os.path.expanduser(x)) for x in parts) and "\n" not in value
    if name == "io_read_allow":                 # 0.21.0: existing absolute folders, comma-separated
        parts = [x.strip() for x in value.split(",") if x.strip()]
        return "\n" not in value and all(
            os.path.isabs(os.path.expanduser(x)) and os.path.isdir(os.path.expanduser(x))
            for x in parts)
    if name == "model_calibration_runner":      # 0.21.0: one plain command line, {model} in it
        v = value.strip()
        if not v:
            return True
        if "\n" in v or "{model}" not in v or any(c in v for c in (";", "|", "&", ">", "<", "`", "$(")):
            return False
        try:
            import shlex
            return bool(shlex.split(v))
        except ValueError:
            return False
    if name == "closeout_limit":                # 0.21.0: 1..100 or all
        return value == "all" or (value.isdigit() and 1 <= int(value) <= 100)
    if name == "closeout_later_days":           # 0.21.0: 1..365
        return value.isdigit() and 1 <= int(value) <= 365
    if name == "io_read_max_bytes":             # 0.21.0
        return value.isdigit() and int(value) >= 1
    if name == "io_write_max_bytes":            # 0.21.0, item #20 stage 3
        return value.isdigit() and int(value) >= 1
    if name == "io_fetch_max_bytes":            # 0.21.0, item #20 stage 2
        return value.isdigit() and int(value) >= 1
    if name == "io_fetch_allow":                # 0.21.0: domains / addresses / CIDRs, comma-separated
        parts = [x.strip() for x in value.split(",") if x.strip()]
        if "\n" in value:
            return False
        if not parts:
            return True
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        try:
            import gt_io_policy
        except ImportError:                      # a copy without the script cannot judge an entry
            return True
        return all(gt_io_policy.check_fetch_entry(x)[1] is None for x in parts)
    if name == "expiry_guard.min_tokens":       # 0.21.0
        return value.isdigit() and int(value) >= 1
    if name == "expiry_guard.margin_min":       # 0.21.0
        return value == "auto" or value.isdigit()
    if name == "persona":                       # 0.21.0: installed persona names, comma-separated
        return _persona_unknown(value) == []
    if name == "runners":                       # 0.18.1: ssh aliases, comma-separated
        return bool(re.fullmatch(r"[a-z0-9._@,-]*", value))
    if name != "parallel_max":
        return False
    if value == "auto":
        return True
    try:
        return int(value) >= 1
    except ValueError:
        return False


def parallel_jobs(want, io_bound=False):
    """-> worker count to use for `want` units of independent work, honouring settings.

    The single place the two settings turn into a number, so every caller answers
    "how many workers" the same way instead of each inventing a policy.

    `parallel_work=off` returns 1 -- serial, through whatever path the caller uses.
    `parallel_max=auto` returns the profile's `cpu_max` (cores), or its `io_max` when
    the work is I/O-bound: 2x cores, bounded by memory. These tools spend their time
    waiting on install.sh, SSH and HTTP, so more workers than cores is the correct
    answer there and `auto` means "as many as the machine allows", not "exactly ncpu".
    The 2x rule is stated once, in detect_machine(), and `parallel_max` quotes it; this
    docstring said "cores + 4 (capped)" until 0.16.5, which no code here has ever done.
    """
    want = max(1, int(want))
    if get("parallel_work") == "off":
        return 1
    prof = machine_profile()
    cap = prof["io_max"] if io_bound else prof["cpu_max"]
    limit = get("parallel_max")
    if limit != "auto":
        cap = min(cap, int(limit))
    return max(1, min(want, cap))


def detect_machine():
    """-> the ceilings THIS machine supports, measured now.

    `auto` has to mean "as much as this machine allows", and the machine is not
    knowable from a number someone wrote down: 20 workers was right for the laptop it
    was chosen on and arbitrary everywhere else. So the ceilings are derived from the
    hardware and stored at install time (see write_machine_profile), not guessed at
    each call and not frozen into the source.

    cpu_max = logical cores. More processes than cores on CPU-bound work costs
    context switching and returns nothing.

    io_max = 2x cores, and never more workers than there is memory to hold them
    (~1 worker per 256 MB, which is generous for a subprocess that spends its life
    waiting). I/O-bound work is waiting, not computing, so oversubscribing cores is
    the correct answer -- the limit is memory and the remote end, not the CPU.
    """
    import platform
    import socket
    import subprocess

    def sysctl(name):
        try:
            out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True)
            return int(out.stdout.strip())
        except Exception:
            return 0

    cores = os.cpu_count() or 4
    physical = sysctl("hw.physicalcpu") or cores
    membytes = sysctl("hw.memsize")
    if not membytes:                      # Linux
        try:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        membytes = int(line.split()[1]) * 1024
                        break
        except Exception:
            membytes = 0
    memory_gb = round(membytes / (1024 ** 3), 1) if membytes else 0.0
    by_memory = int(memory_gb * 4) if memory_gb else 10 ** 6   # ~1 worker / 256 MB
    return {
        "cores": cores,
        "physical_cores": physical,
        "memory_gb": memory_gb,
        "cpu_max": max(1, cores),
        "io_max": max(2, min(cores * 2, by_memory)),
        "host": socket.gethostname(),
        "platform": platform.platform(),
    }


def machine_profile():
    """The stored profile, or a live reading if none has been written yet.

    Stored, because a profile is measured at install and re-measured at update -- it
    must not drift under a running session, and every tool on the machine must get the
    same answer. Missing is not an error: a clone with no ~/.claude still has to work,
    it just falls back to reading the machine now.
    """
    prof = _load().get("parallel_profile")
    if isinstance(prof, dict) and prof.get("cpu_max") and prof.get("io_max"):
        try:
            return {"cpu_max": max(1, int(prof["cpu_max"])),
                    "io_max": max(1, int(prof["io_max"]))}
        except (TypeError, ValueError):
            pass
    return detect_machine()


def write_machine_profile(force=False):
    """Measure and store the profile. Called by install.sh on install AND upgrade.

    Returns (profile, changed). It rewrites `parallel_profile`, which is a record of
    the HARDWARE, and never touches `parallel_work` or `parallel_max`, which are the
    user's preferences: re-running the installer must not undo a ceiling somebody chose
    deliberately. A profile whose numbers are unchanged is not rewritten, so the
    detected_at timestamp means "when this machine last looked different".
    """
    import datetime
    d = _load()
    if not d.get("vault_path"):
        return None, False
    fresh = detect_machine()
    old = d.get("parallel_profile") or {}
    # 0.18.1: a MEASURED profile (gt_bench.py) is kept across installs and upgrades while the
    # hardware is the same -- replacing a measurement with the rule of thumb it replaced would
    # undo it silently. A new machine or a core-count change still re-detects.
    if (old.get("source") == "measured" and not force
            and all(old.get(k) == fresh[k] for k in ("cores", "host"))):
        return old, False
    same = all(old.get(k) == fresh[k] for k in ("cores", "cpu_max", "io_max", "host"))
    if same and not force:
        return old, False
    fresh["detected_at"] = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    d["parallel_profile"] = fresh
    _save(d)
    return fresh, True


HOOK_FLAG = "--hook"


def hook_args(argv=None):
    """-> (argv without the hook flag, True if it was present).

    install.sh registers every hook command with `--hook`. That flag, not a
    guess, is what decides whether output is wrapped as hook JSON. 0.9.6 used
    `sys.stdout.isatty()`, which is False for a hook but ALSO for `| tee`, cron,
    a subagent and every command the assistant runs through its Bash tool -- so
    the plain-text report the docstrings promised came out as an escaped JSON
    blob for everyone except a human at a terminal.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    is_hook = HOOK_FLAG in argv or os.environ.get("GT_HOOK") == "1"
    return [a for a in argv if a != HOOK_FLAG], is_hook


def emit(text, event="SessionStart", as_hook=None):
    """Deliver a startup report to BOTH the user and the model.

    A SessionStart hook's plain stdout goes into the model's context only -- it is
    never rendered in the user's terminal. So every one of these checks was already
    "said out loud" in the sense its own comments claim, but only to the assistant.
    From the user's side a clean check and a hook that never ran are the same thing:
    silence. That is the 2026-08-30 shape again, one layer out -- the check was
    honest, its delivery went to the wrong audience.

    `systemMessage` is the field that surfaces to the user on any platform;
    `additionalContext` is what the model reads. Both are sent deliberately: the
    model must keep receiving this, because CLAUDE.md asks the assistant to open
    each session by stating these results, and that prose rule is the backstop for
    anything this JSON cannot reach.

    Hook JSON is produced only when the caller says it is a hook (`as_hook`, or
    the `--hook` flag or GT_HOOK=1 seen by `hook_args`). Every other caller -- a
    human, a pipe, the assistant's Bash tool -- gets the plain text unchanged.
    """
    text = (text or "").strip()
    if not text:
        return
    if as_hook is None:
        as_hook = hook_args()[1]
    if not as_hook:
        print(text)
        return
    json.dump({
        "systemMessage": text,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": text,
        },
    }, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def capture(fn, *a, **kw):
    """Run fn, returning (result, text-it-printed).

    Lets the reporting functions keep their many plain print() calls -- they are
    readable that way, and they are also the manual CLI output -- while the hook
    entry point decides how the result is delivered.

    If fn raises part-way, everything it printed so far is KEPT and the failure is
    appended to it, so the report is delivered with the crash rather than replaced
    by it. 0.9.6 dropped the buffer on the way out: a worker report that had
    already listed three orphans, then hit an unexpected error in reap(), reached
    nobody -- only a traceback on stderr, which a hook shows to no one.
    """
    import contextlib
    import io
    import traceback
    buf = io.StringIO()
    r = None
    try:
        with contextlib.redirect_stdout(buf):
            r = fn(*a, **kw)
    except Exception as exc:
        buf.write("\n  ... check aborted: %s: %s\n" % (type(exc).__name__, exc))
        sys.stderr.write(traceback.format_exc())
    return r, buf.getvalue()


def _paranoid_group_names():
    """The paranoid keys in display order, or [] when the group is not registered."""
    try:
        import gt_paranoid as gp
    except Exception:                            # noqa: BLE001
        return []
    return [k for k in gp.KEYS if k in SETTINGS]


def _paranoid_label(cfg):
    import gt_paranoid as gp
    st = gp.state(cfg)
    if st["label"] == "paranoid (partial)" and st["off"]:
        return "%s -- off: %s" % (st["label"], ", ".join(k.split(".", 1)[1] for k in st["off"]))
    return st["label"]


def _paranoid_mark(name, cfg):
    import gt_paranoid as gp
    if name == gp.MASTER:
        return ""
    r = gp.raw(name, cfg)
    return "   (follows paranoid)" if r == "follow" else "   (set explicitly)"


def _testing_profile_lines():
    """The loosened-machine line: an active testing profile is never silent, and an expired one
    is removed here (and said so) -- show is one of the places the sweep runs."""
    try:
        import gt_testing_profile
        return gt_testing_profile.status_lines()
    except Exception as e:                       # noqa: BLE001
        return ["testing profile: could not be read (%s)" % type(e).__name__]


def token_profile_state():
    """-> {"stored", "label", "differs"}: the chosen token profile and whether the seven settings it
    sets still agree with it. A single change turns the label into 'manual (based on X)'."""
    stored = get("token_profile")
    mapping = TOKEN_PROFILES.get(stored)
    if mapping is None:
        return {"stored": stored, "label": "manual", "differs": []}
    differs = [(k, get(k), v) for k, v in mapping.items() if get(k) != v]
    label = stored if not differs else "manual (based on %s)" % stored
    return {"stored": stored, "label": label, "differs": differs}


def token_profile_lines():
    st = token_profile_state()
    out = ["Token profile: %s   (one choice for the seven settings below; change it with: set token_profile "
           "minimum|calibrated|maximum)" % st["label"]]
    mapping = TOKEN_PROFILES.get(st["stored"])
    if mapping is None:
        out.append("  manual: gt keeps none of them in step; "
                   + " · ".join("%s %s" % (k, get(k)) for k in TOKEN_KEYS))
    elif st["differs"]:
        out.append("  " + " · ".join("%s is %s (%s sets %s)" % (k, now, st["stored"], want)
                                      for k, now, want in st["differs"]))
    else:
        out.append("  sets: " + " · ".join("%s %s" % (k, v) for k, v in mapping.items()))
    return out


TOKEN_KEYS = tuple(TOKEN_PROFILES["calibrated"])


def _next_calibration_line():
    try:
        import gt_calibration_schedule as cs
        return cs.describe(get("model_calibration_schedule"))
    except Exception as e:                       # noqa: BLE001
        return "could not be worked out (%s)" % type(e).__name__


def show():
    cfg = _load()
    print("Golden Thread settings   (%s)" % CONFIG)
    print()
    for line in token_profile_lines():           # the profile leads: it is the one choice that sets seven
        print(line)
    print()
    groups = [(None, [n for n, s in SETTINGS.items()
                      if not s.get("module") and not s.get("group")]),
              (HINTS_GROUP, [n for n in HINTS_ORDER if n in SETTINGS]),
              (TOKENS_GROUP, [n for n in TOKENS_ORDER if n in SETTINGS]),
              (MODELS_GROUP, [n for n in MODELS_ORDER if n in SETTINGS])]
    pgroup = _paranoid_group_names()
    if pgroup:
        groups.append((SETTINGS[pgroup[0]]["group"], pgroup))
    for mod in sorted({s["module"] for s in SETTINGS.values() if s.get("module")}):
        # An OFF module's settings are listed only when the user has set one: the value is
        # kept, and saying so beats a key that silently vanished from the list.
        names = [n for n, s in SETTINGS.items() if s.get("module") == mod
                 and (not s.get("orphaned") or cfg.get(n) not in (None, ""))]
        if names:
            groups.append((mod, names))
    for mod, names in groups:
        if mod and any(SETTINGS[n].get("orphaned") for n in names):
            print("module %s   (OFF — settings kept, not in effect; install.sh --with %s)"
                  % (mod, mod))
            print()
        elif mod == HINTS_GROUP:
            print("%s   (shown to you, never to the model: no tokens)" % HINTS_GROUP)
            print()
        elif mod == TOKENS_GROUP:
            print("%s   (what Claude is asked to write, and the profile that sets them)" % TOKENS_GROUP)
            print()
        elif mod == MODELS_GROUP:
            print("%s   (per-agent view: /gt:gt-settings models)" % MODELS_GROUP)
            print()
        elif pgroup and mod == SETTINGS[pgroup[0]]["group"]:
            print("%s   (status: %s; explain with: explain paranoid)"
                  % (mod, _paranoid_label(cfg)))
            print()
        elif mod:
            print("module %s" % mod)     # a module's settings, under its name
            print()
        for name in names:
            spec = SETTINGS[name]
            raw = _model_profile() if name == "model_profile" else cfg.get(name)
            cur = get(name)
            mark = "" if raw not in (None, "") or spec.get("gate") \
                else "   (default — not set in the file)"
            if pgroup and name in pgroup:
                mark = _paranoid_mark(name, cfg)
            if name == "lockdown":
                try:
                    import gt_lockdown
                    if gt_lockdown.shown_current() == "paranoid":
                        mark += "   (shown as paranoid: strict with the paranoid switch on)"
                except Exception:                # noqa: BLE001
                    pass
            print("  %-20s %s%s" % (name, cur, mark))
            print("  %-20s %s" % ("", spec["summary"]))
            opts = " | ".join(spec["values"]) if spec["values"] else spec["validate"]
            print("  %-20s options: %s" % ("", opts))
            if name == "model_calibration_schedule":
                print("  %-20s next calibration: %s" % ("", _next_calibration_line()))
            print()
    try:                                         # parameterised gates that are on (0.21.0)
        import gt_unlock_client
        import gt_unlock_policy as gp
        with open(os.path.join(gt_unlock_client.home(), "policy.json"), encoding="utf-8") as fh:
            inst = [gp.gate_label(e.get("gate"), e.get("param")) for e in gp.gates_of(json.load(fh))
                    if e.get("param")]
    except Exception:                            # noqa: BLE001
        inst = []
    for label in sorted(inst):
        n = "gate.%s" % label
        sp = _gate_instance(n)
        if sp:
            print("  %-20s %s" % (n, get(n)))
            print("  %-20s %s" % ("", sp["summary"]))
            print("  %-20s options: off | on" % "")
            print()
    prof = cfg.get("parallel_profile")
    if isinstance(prof, dict):
        print("  %-20s %s" % ("parallel_profile",
                              "cpu_max %s, io_max %s   (%s core(s), %s GB, measured %s)"
                              % (prof.get("cpu_max"), prof.get("io_max"),
                                 prof.get("cores"), prof.get("memory_gb"),
                                 prof.get("detected_at", "?"))))
        print("  %-20s %s" % ("", "what `parallel_max: auto` resolves to on this machine; "
                                  "measured at install, re-measured on upgrade"))
        print()
    for line in _testing_profile_lines():
        print(line)
    print()
    print("What the security settings above do not reach:")
    print(mcp_surface_text("  "))
    print()
    print("per agent: python3 gt_settings.py models   (model, effort, cost and calibration for each)")
    print("change with:  python3 gt_settings.py set <name> <value>")
    print("explain with: python3 gt_settings.py explain <name>")
    line = _lotr_summary()
    if line:
        print("\n%s  -- details: /gt:gt-settings lotr" % line)
    return 0


# ------------------------------------------------- the surface gt does not gate (0.21.0) ----
# Request 2026-10-08-surface-ungated-mcp-servers-in-settings (owner requirement): the person tuning
# unlock, lockdown and sandbox mode must see, in the same place, what those settings do NOT reach.
# The detection is gt_doctor.mcp_surface() -- the doctor's own -- so the two never disagree.

HARDENING_SETTINGS = ("unlock", "lockdown", "sandbox_mode")


def _mcp_surface_lines():
    try:
        import gt_doctor
        m = gt_doctor.mcp_surface()
    except Exception as e:                       # noqa: BLE001
        return ["MCP servers outside LOTR -- could not be checked (%s); treat them as not "
                "covered by unlock or sandbox mode." % type(e).__name__]
    if m["state"] == "unknown":
        return ["MCP servers outside LOTR -- could not be checked (%s); treat them as not "
                "covered by unlock or sandbox mode." % m.get("why", "no answer")]
    if m["state"] == "clear":
        return ["MCP servers outside LOTR -- none: every MCP server Claude Code may load goes "
                "through LOTR or gt-vault, so this surface is fully within gt's reach."]
    return ["%d MCP server(s) outside LOTR (%s)." % (m["count"], ", ".join(m["names"])),
            "gt unlock and sandbox mode do NOT cover them: the unlock level covers LOTR and "
            "gt-vault only, so raising gt's settings does not harden these servers.",
            "Where each one's credentials live: /gt:gt-doctor (row mcp)%s."
            % ((", " + m["ref"]) if m.get("ref") else "")]


def mcp_surface_text(indent=""):
    return "\n".join(indent + l for l in _mcp_surface_lines())


SECURITY_HELP_INTRO = """\
Every security setting in Golden Thread: what it does, and how to change it.

HOW TO CHANGE ANY SETTING BELOW
  In Claude Code:  /gt:gt-settings <name> <value>      e.g.  /gt:gt-settings paranoid on
  In a terminal:   python3 ~/.claude/golden-thread/hooks/gt_settings.py set <name> <value>
  One in full:     gt_settings.py explain <name>
  The current values on this machine:  gt_settings.py security
  Each one is its own switch: turn any of them on or off separately, at any time. The security
  level chosen at install (lockdown) is a starting point, not a package.
  With gt unlock on, changing any of these but vault_mcp asks you to prove you are present
  first, so an agent cannot loosen them on its own. Restart Claude Code after changing lockdown,
  sandbox_mode, sandbox_vault_reads or vault_mcp.

WHICH ONE DOES WHAT
  lockdown   which commands Claude may run WITHOUT ASKING you. It cuts prompts; it is not a
             wall: above the strictest level a shell command can print any file you can read.
  paranoid   locks gt's own installed code so nothing can rewrite the guards; its
             action_tokens parts make chosen actions need a single-use permit you create.
  unlock     credentials, LOTR connections and publishing need your finger (Touch ID /
             Windows Hello) or a code before an agent can use them.
  sandbox    fences Claude's shell and file tools off the vault and gt's own state.
  The rest   individual guards: protected paths, the commit test gate, push and commit
             fingerprints, the I/O brokers' limits, and more -- each below.
  SECURITY.md is the full guide: the threat model and what each piece does NOT stop.
"""


def security_keys_ordered():
    """Every security setting (is_security_key), the big switches first, then registry order."""
    first = ["lockdown", "paranoid"] + sorted(k for k in SETTINGS if k.startswith("paranoid.")
                                                and not k.startswith("paranoid.action_tokens")) \
        + [k for k in SETTINGS if k.startswith("paranoid.action_tokens")] \
        + ["unlock", "sandbox_mode", "sandbox_vault_reads", "vault_mcp"]
    first = [k for k in first if k in SETTINGS]
    return first + [k for k in SETTINGS if is_security_key(k) and k not in first]


def security_help():
    """`security --full` (owner 2026-10-10): the intro, then every security setting in full,
    each under two rows of '=' with the command that changes it. Read from the registry, so a
    setting added later appears here without an edit. install.sh --help security prints this."""
    print(SECURITY_HELP_INTRO)
    row = "=" * 72
    for k in security_keys_ordered():
        print("\n%s\n%s" % (row, row))
        explain(k)
        print("  change it:  /gt:gt-settings %s <value>   (or gt_settings.py set %s <value>)" % (k, k))
    return 0


def security_view():
    """`gt_settings.py security`: the hardening settings and the surface they do not reach."""
    print("Security settings   (change with: set <name> <value>; explain <name>)")
    print()
    for name in HARDENING_SETTINGS:
        if name in SETTINGS:
            print("  %-14s %s" % (name, get(name)))
            print("  %-14s %s" % ("", SETTINGS[name]["summary"]))
    print()
    print("What these settings do not reach")
    print(mcp_surface_text("  "))
    return 0


# ---------------------------------------------------------------- LOTR view (0.20.2) ----
# Owner, 2026-10-06: what is connected to LOTR and whether it works must be easy to find.
# gt core cannot import gt-lotr (a separate plugin), so this runs the INSTALLED lotr.py.

def _lotr_script():
    rec = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "installed_plugins.json")
    try:
        with open(rec) as fh:
            e = (json.load(fh).get("plugins", {}).get("gt-lotr@golden-thread-plugin") or [])[0]
        path = os.path.join(str(e.get("installPath") or ""), "scripts", "lotr.py")
    except Exception:                                           # noqa: BLE001
        return None
    return path if os.path.isfile(path) else None


def _lotr_table(check=False, timeout=60):
    import subprocess                                           # noqa: PLC0415
    script = _lotr_script()
    if not script:
        return None
    # -X utf8, not PYTHONIOENCODING: -I ignores every PYTHON* variable, so on Windows the
    # child printed the table's ✓/✗ through cp1252 and crashed (found on gt-win11d, 0.20.2)
    args = ([sys.executable, "-I", "-X", "utf8", script, "status", "--table"]
            + (["--check"] if check else []))
    try:
        # read it back as UTF-8 too (Windows would decode the marks as cp1252)
        p = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "LOTR: the gateway did not answer (%s)\n" % type(e).__name__
    return p.stdout or p.stderr or "LOTR: no answer (exit %s)\n" % p.returncode


def _lotr_not_set_up(err=None):
    """The line for an uninitialized gateway (error code not_initialized): say what is true and the
    command that fixes it -- the gateway's own hint when it gave one."""
    hint = None
    for h in (err or {}).get("hints") or []:
        if isinstance(h, str) and h.startswith("lotr "):
            hint = h
            break
    return "LOTR: not set up on this machine -- run: " + (
        hint or "lotr --home ~/.config/gt-lotr/personal init --zone personal --mode local")


def _lotr_summary():
    """The one LOTR line plain `show` ends with; None when LOTR is not installed.

    Always a summary or an honest sentence, never raw JSON. The live bug of 2026-10-09 was
    `LOTR: {` -- the first line of a pretty-printed JSON answer, taken as the summary -- and it
    came back in two shapes after the 0.21.0 fix for the error object: a JSON STATUS (an installed
    lotr that ignores --table) and a JSON array."""
    out = _lotr_table(timeout=20)          # a cold Python start on a busy Windows box can take >8 s
    if out is None:
        return None
    lines = [l for l in out.splitlines() if l.startswith("LOTR:") and not _json_start(l[5:])]
    if lines:
        return lines[-1]
    try:
        doc = json.loads(out)
    except ValueError:
        doc = None
        # JSON followed by other text (a traceback on stderr): decode the leading document
        try:
            doc, _end = json.JSONDecoder().raw_decode(out.lstrip())
        except ValueError:
            pass
    if isinstance(doc, dict):
        if doc.get("connections") is not None and doc.get("ok", True):
            counts = {}
            for c in doc.get("connections") or []:
                st = (c.get("state") if isinstance(c, dict) else None) or "not used yet"
                counts[st] = counts.get(st, 0) + 1
            order = ("error", "needs sign-in", "connected", "not used yet", "disabled")
            parts = ["%d %s" % (counts[k], k) for k in order if counts.get(k)]
            parts += ["%d %s" % (n, k) for k, n in sorted(counts.items()) if k not in order]
            return "LOTR: " + (", ".join(parts) if parts else "no connections")
        err = doc.get("error") or {}
        if isinstance(err, dict):
            if err.get("code") == "not_initialized":
                return _lotr_not_set_up(err)
            msg = err.get("message") or err.get("code")
            if msg:
                return "LOTR: " + str(msg)[:120]
        return "LOTR: no answer"
    if doc is not None:                              # a list, a string, a number: not a summary
        return "LOTR: no answer"
    for l in out.splitlines():
        l = l.strip()
        if l and not _json_start(l):
            return "LOTR: " + l[:120]
    return "LOTR: no answer"


def _json_start(text):
    return text.strip()[:1] in ("{", "[")


def lotr_view(check=False):
    out = _lotr_table(check=check)
    if out is None:
        print("LOTR is not installed on this machine (install gt with --with lotr).")
        return 0
    sys.stdout.write(out)
    return 0


def _lockdown_modules_text():
    """The rules each enabled module adds to the current level, by module (0.21.0)."""
    try:
        import gt_lockdown
        level = gt_lockdown.current()
        rows = [r for r in gt_lockdown.explain(level) if r["source"] != gt_lockdown.CORE]
        probs = gt_lockdown.module_rules(level)["problems"]
    except Exception:                            # noqa: BLE001
        return ""
    out = ["", "Module rules at your level (%s), each with the module that contributed it:" % level]
    if not rows:
        out.append("  none -- no enabled module declares a rule for this level")
    for r in rows:
        out.append("  %-5s %-40s %s" % (r["key"], r["rule"], r["source"]))
    out += ["  note: " + p for p in probs]
    out.append("Full list for any level: gt_lockdown.py explain [LEVEL]")
    return "\n".join(out)


def plain_text(name, options=None):
    """The one plain-language rendering of a setting's `plain` field, for people who are not
    technical: what it is, each choice (who it is for, what Claude does without asking, what it
    still asks, an everyday example, the trade-off), "Not sure?" and "change it later".

    The installer at a terminal, `install.sh --questions`, /gt:gt-init and `explain` all print
    THIS, so the explanations cannot drift apart (item #33). `options` replaces the registry's
    option table where the choices are not a fixed list (the personas, read from their packs).
    -> "" for a setting with no plain field."""
    return render_plain((SETTINGS.get(name) or {}).get("plain"), options)


def render_plain(pl, options=None):
    """plain_text() for a `plain` dict that is not a registry entry (the status line and ast-grep
    questions, which are install choices but not settings)."""
    if not pl:
        return ""
    opts = options if options is not None else pl.get("options") or {}
    out = [pl["intro"].strip(), ""]
    if opts:
        out.append("The choices:")
    for val, o in opts.items():
        tag = "  (recommended)" if val == pl.get("recommended") else ""
        out.append("  %s -- %s%s" % (val, o["summary"], tag))
        for key, label in PLAIN_LABELS:
            if o.get(key):
                out.append("      %s: %s" % (label, o[key]))
        out.append("")
    if pl.get("parts"):
        out.append("The parts behind the switch:")
        out += ["  - %s" % t for t in pl["parts"]]
        out.append("")
    if pl.get("not_sure"):
        out.append("Not sure? %s" % pl["not_sure"])
    if pl.get("change_later"):
        out.append("You can change it later: %s" % pl["change_later"])
    return "\n".join(out).rstrip() + "\n"


PLAIN_LABELS = (("who", "Who it is for"), ("without_asking", "Claude can do without asking"),
                ("still_asks", "Claude still asks"), ("example", "Everyday example"),
                ("trade_off", "The trade-off"))


def explain(name):
    spec = SETTINGS.get(name) or _gate_instance(name)
    if not spec:
        print("unknown setting: %s" % name)
        print("known: %s" % ", ".join(SETTINGS))
        return 2
    print("%s  (current: %s, default: %s)" % (name, get(name), spec["default"]))
    print()
    print(spec["summary"])
    print()
    print(spec["detail"])
    if spec.get("plain"):
        print()
        print(plain_text(name))
    if name == "lockdown":
        print(_lockdown_modules_text())
    if name in HARDENING_SETTINGS:
        print()
        print("What this setting does not reach:")
        print(mcp_surface_text("  "))
    return 0


def check_against_machine(value, force=False):
    """-> (refuse: bool, message or None) for a parallel_max the user typed.

    A ceiling the hardware cannot honour is not a preference, it is a typo with
    consequences: `40` on a four-core laptop makes nothing faster, it thrashes, and the
    person who typed it has no way to learn that from a confirmation message. So the
    value is checked against the measured profile at the moment it is set -- the only
    moment anyone is paying attention to it.

    Two bands, because "more than the processors" is not automatically wrong:

      * above io_max (2x cores)  -> refused. Nothing here can use it.
      * cores < N <= io_max      -> allowed WITH a warning. Oversubscribing cores is
                                    correct for I/O-bound work and useless for
                                    CPU-bound work; the user should know which they
                                    are buying.

    --force covers the case the profile cannot see: a fan-out over a hundred
    high-latency remotes is bounded by the network, not by this CPU. It is explicit, so
    it shows up in the transcript instead of being assumed.
    """
    if value == "auto":
        return False, None
    n = int(value)
    prof = machine_profile()
    cores, io_max = prof["cpu_max"], prof["io_max"]
    if n > io_max and not force:
        return True, ("%d is more than this machine can use: %d core(s), so at most %d "
                      "workers even for I/O-bound work.\n"
                      "  `auto` already means \"as many as this machine allows\", and it "
                      "re-measures itself on every upgrade.\n"
                      "  If you meant it -- a fan-out bounded by the network rather than "
                      "this CPU -- re-run with --force." % (n, cores, io_max))
    if n > cores:
        return False, ("note: %d exceeds this machine's %d core(s). That helps I/O-bound "
                       "work (waiting on SSH, HTTP, subprocesses) and does nothing for "
                       "CPU-bound work." % (n, cores))
    return False, None


def _gate_switch(name, spec, value):
    """gate.<x> on|off: wire or unwire through `gt_unlock.py gate`, which writes the unlock policy
    (the source of truth). The value is never recorded in vault-config.json. -> exit code."""
    import subprocess
    from gt_unlock_policy import gate_label
    was = get(name)
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gt_unlock.py")
    label = gate_label(spec["gate"], spec.get("param"))
    rc = subprocess.call([sys.executable, cli, "gate", value, label])
    if rc != 0:
        return rc
    print("%s: %s -> %s" % (name, was, value))
    return 0


def _lockdown_rung(value):
    """`paranoid` on the ladder (0.21.0) is a shortcut, not a stored level: strict plus the paranoid
    switch on. Leaving it (the ladder shows paranoid and another rung is picked) turns the switch
    off first, with the step-up that already needs. -> (exit code, None) when this finished or
    refused the change; (None, level) when the plain level change should go ahead."""
    import gt_lockdown
    import gt_paranoid as gp
    on = gp.effective(gp.MASTER, _load())
    shown = gt_lockdown.shown(gt_lockdown.current(), on)
    if value == "paranoid":
        if shown == "paranoid":
            print("lockdown: paranoid (strict, with the paranoid switch on) -- nothing to change")
            return 0, None
        if not on:
            rc = _set_paranoid(gp.MASTER, "on")
            if rc:
                print("lockdown not changed: paranoid needs the paranoid switch, refused above.")
                return rc, None
        return None, "strict"
    if shown == "paranoid":
        rc = _set_paranoid(gp.MASTER, "off")
        if rc:
            print("lockdown not changed: leaving paranoid turns the paranoid switch off, refused above.")
            return rc, None
    return None, value


def _set_paranoid(name, value):
    """`set paranoid[.part] <value>` (0.21.0). Every key here is a security setting: with gt unlock
    on the change needs the settings step-up; with it off, LOWERING a key that is on needs a person
    at a terminal (a typed confirmation), so an agent's shell cannot switch the protection off.
    Raising is free. After the change the machine is brought in line (lock, unlock, unseal, unwire)
    and the change is logged; if that fails the setting is put back."""
    import gt_paranoid as gp
    d = _load()
    if not d.get("vault_path"):
        print("refusing to write %s: it has no vault_path. Run /gt:gt-init first." % CONFIG)
        return 2
    try:
        import gt_unlock_policy
        unlock_on = gt_unlock_policy.enabled_fast()
    except Exception:                            # noqa: BLE001
        unlock_on = False
    new = dict(d)
    if value == "follow":
        new.pop(name, None)
    else:
        new[name] = value
    old_eff, new_eff = gp.effective(name, d), gp.effective(name, new)
    if (name == gp.MASTER and new_eff and not old_eff and not unlock_on
            and gp.effective("paranoid.require_unlock", new)):
        print("refused: paranoid is not turned on because gt unlock is not set up, and "
              "paranoid.require_unlock is on.\n"
              "  Set it up first:  python3 gt_unlock.py policy enable   (Touch ID / Windows Hello / "
              "an authenticator app)\n"
              "  or accept the weaker form (a typed confirmation, a pinned list):\n"
              "    python3 gt_settings.py set paranoid.require_unlock off")
        return 1
    refusal = _unlock_gate(name)
    if refusal:
        print("refused: changing %s needs a fresh confirmation (gt unlock): %s" % (name, refusal))
        return 1
    if old_eff and not new_eff and not unlock_on:
        import gt_integrity
        ok, how, msg = gt_integrity.require_presence(
            "turn off %s" % name, cfg=d, force=True)
        if not ok:
            print("refused: turning %s off needs a person at a terminal: %s" % (name, msg))
            print("  Run it yourself in a terminal: python3 %s set %s %s"
                  % (os.path.abspath(__file__), name, value))
            return 1
    was_raw = gp.raw(name, d)
    d_new = new
    _save(d_new)
    print("%s: %s -> %s" % (name, get_from(d, name), get_from(d_new, name)))
    try:
        import gt_integrity
        gt_integrity.log_change(gt_integrity.home_dir(), event="setting", key=name,
                                was=get_from(d, name), now=get_from(d_new, name),
                                raw_was=was_raw, raw_now=gp.raw(name, d_new),
                                partial=gp.state(d_new)["label"] == "paranoid (partial)")
        ok, lines = gt_integrity.apply(gt_integrity.home_dir())
    except Exception as e:                       # noqa: BLE001
        ok, lines = False, ["could not apply: %s: %s" % (type(e).__name__, e)]
    for ln in lines:
        print("  " + ln)
    if not ok:
        _save(d)
        print("  reverted: %s is back to %s" % (name, get_from(d, name)))
        return 1
    st = gp.state(d_new)
    if st["label"] == "paranoid (partial)":
        print("  paranoid (partial) -- off: %s" % ", ".join(k.split(".", 1)[1] for k in st["off"]))
    return 0


# model_calibration_budget_preset (0.21.0, item 44): setting a preset writes these once.
BUDGET_PRESETS = {
    "home": {"model_calibration_schedule": "monthly", "model_calibration_sample": "2"},
    "corporate": {"model_calibration_schedule": "monthly", "model_calibration_sample": "3"},
}


def _apply_budget_preset(value):
    rc = 0
    for key, val in BUDGET_PRESETS[value].items():
        if get(key) != val:
            rc = rc or set_value(key, val)
    if value == "home":
        print("  home: a small pass once a month; it stops when the weekly allowance reaches "
              "model_calibration_max_weekly_pct (%s%%)." % get("model_calibration_max_weekly_pct"))
    else:
        print("  corporate: a pass is refused once this month's calibration spend would pass "
              "model_calibration_seat_cap_usd (%s USD)." % get("model_calibration_seat_cap_usd"))
    return rc


def _set_token_profile(value):
    """`set token_profile <value>`: write the seven settings of the profile, then record the choice.
    manual writes nothing else. A setting that cannot be changed stops the run and is named; the
    choice is only recorded when all seven are in place."""
    d = _load()
    if not d.get("vault_path"):
        print("refusing to write %s: it has no vault_path. Run /gt:gt-init first." % CONFIG)
        return 2
    was = get("token_profile")
    mapping = TOKEN_PROFILES.get(value)
    if mapping:
        for key, want in mapping.items():
            if get(key) == want:
                continue
            rc = set_value(key, want)
            if rc:
                print("token_profile %s stopped at %s (exit %d); the profile was not recorded." % (value, key, rc))
                return rc
        if value == "calibrated":
            try:                                 # the stored calibration verdicts, if there are any
                import gt_models_page
                gt_models_page.cmd_apply_calibration([])
            except Exception as e:               # noqa: BLE001
                print("  (stored calibration verdicts not applied: %s)" % type(e).__name__)
    d = _load()
    d["token_profile"] = value
    _save(d)
    print("token_profile: %s -> %s" % (was, value))
    return 0


def get_from(cfg, name):
    """The effective on/off of a paranoid key in a given config dict."""
    import gt_paranoid as gp
    return "on" if gp.effective(name, cfg) else "off"


def set_value(name, value, force=False):
    spec = SETTINGS.get(name) or _gate_instance(name)
    if not spec:
        print("unknown setting: %s" % name)
        print("known: %s" % ", ".join(SETTINGS))
        return 2
    # paths keep their case (0.20.3): lowercasing a repo path breaks it on a case-sensitive disk
    value = (value or "").strip() if name in ("push_fingerprint_repos",) + CASE_KEPT \
        else (value or "").strip().lower()
    if name == "persona":
        if value == "none":
            value = ""
        value = ",".join(_persona_names(value))
        unknown = _persona_unknown(value)
        if unknown:
            import gt_persona
            print("invalid value %r for persona: unknown %s" % (value, ", ".join(unknown)))
            print("valid: %s" % (", ".join(gt_persona.valid_names()) or "(none installed)"))
            return 2
    bad = (not _freeform_ok(name, value)) if spec["values"] is None \
        else (value not in spec["values"])
    if bad:
        print("invalid value %r for %s" % (value, name))
        print("valid: %s" % (spec["validate"] if spec["values"] is None
                             else " | ".join(spec["values"])))
        return 2
    if name == "paranoid" or name.startswith("paranoid."):
        return _set_paranoid(name, value)
    if name == "testing_profile":
        # state lives in gt_testing_profile's own record, not vault-config.json; on/off act on the
        # current project with the defaults (no sandbox, no expiry) -- `testing on ...` takes options
        import gt_testing_profile
        return gt_testing_profile.main([value])
    if name == "token_profile":
        return _set_token_profile(value)
    if name == "model_profile":
        was = get(name)
        import gt_models_page
        rc = gt_models_page.set_profile(value)
        if rc == 0 and was != value:
            print("%s: %s -> %s" % (name, was, value))
        return rc
    if name == "parallel_max":
        refuse, msg = check_against_machine(value, force=force)
        if refuse:
            print("invalid value %r for parallel_max" % value)
            print("  " + msg)
            return 2
        if msg:
            print("  " + msg)
    if name in ("sandbox_mode", "sandbox_vault_reads"):
        # A sandbox that cannot run here is refused BEFORE gt unlock asks for codes (0.20.1,
        # finding M6: the bwrap check came after two TOTP codes, and suggested a --force this
        # command does not take). First the cheap check, which names what is missing and the
        # distro's install command and also covers a change to the reads setting while sandbox
        # mode is on; nothing is asked of anyone before it.
        missing = _sandbox_prereqs_missing(name, value)
        if missing:
            print(missing)
            return 1
    if name == "sandbox_mode" and value == "on":
        # Then the strict one: gt_sandbox.preflight really RUNS bwrap and socat, which catches
        # what a PATH lookup cannot (Ubuntu 24.04's AppArmor "uid map: Permission denied").
        # It runs second so that the answer of the cheap check never depends on the host.
        try:
            import gt_sandbox
            gt_sandbox.preflight()
        except ImportError:
            pass
        except gt_sandbox.SandboxError as e:
            print("refused: %s Nothing was changed and no code was asked for." % e)
            return 1
    # `unlock` itself is not gated here: `gt_unlock.py policy enable|disable` asks the
    # authority for K FRESH factors (step-up + K, ignoring any grant), which is stricter than
    # this gate -- asking both made one command need codes from two 30 s windows (M6).
    if (name in SECURITY_KEYS or (spec.get("gate") and value == "off")) and name != "unlock":
        refusal = _unlock_gate(name)
        if refusal:
            print("refused: changing %s needs a fresh confirmation (gt unlock): %s"
                  % (name, refusal))
            return 1
    if spec.get("gate"):
        return _gate_switch(name, spec, value)
    if name == "agent_split" and value == "combined":
        # the lotr module registers the setting; core must not import lotr, so the reason is
        # duplicated here. Combined mode is only offered when the user chose the insecure level.
        try:
            import gt_lockdown
            level = gt_lockdown.current()
        except Exception:                        # noqa: BLE001
            level = "strict"
        if level != "insecure":
            print("refused: agent_split combined is only offered at the insecure lockdown level "
                  "(it is %s): the read/write split is what stops text in a read result "
                  "steering a write." % level.replace("-", " "))
            return 1
    if name in ("push_fingerprint", "commit_fingerprint"):
        # the guard does the work and reports it; the value is recorded only when it succeeded
        import subprocess
        guard = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "gt_push_guard.py" if name == "push_fingerprint" else "gt_sign.py")
        rc = subprocess.call([sys.executable, guard, value])
        if rc != 0:
            return rc
    if name == "unlock":
        import subprocess
        cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gt_unlock.py")
        return subprocess.call([sys.executable, cli, "policy",
                                "enable" if value == "on" else "disable"])
    if name == "lockdown":
        rc, value = _lockdown_rung(value)
        if rc is not None:
            return rc
    d = _load()
    if not d.get("vault_path"):
        # Refuse to create a config that would leave the hooks unable to find the
        # vault -- writing a partial file here would break enforcement, not extend it.
        print("refusing to write %s: it has no vault_path. Run /gt:gt-init first."
              % CONFIG)
        return 2
    was = get(name)
    if name in ("sandbox_mode", "sandbox_vault_reads") and (was != value or name == "sandbox_mode"):
        rc = _sandbox_switch(name, value, dict(d, **{name: value}))
        if rc is not None:
            return rc
    if name == "lockdown":
        # the rules are written first; the value is recorded only when that succeeded
        try:
            import gt_lockdown
            rep = gt_lockdown.apply(value)
        except Exception as e:                   # noqa: BLE001
            print("refused: %s. Nothing was changed." % e)
            return 1
        for c in rep["changes"]:
            print("  " + c)
        if rep["changes"]:
            print("  settings.json backed up first; restart Claude Code for the change to apply")
    d[name] = value
    _save(d)
    print("%s: %s -> %s" % (name, was, value))
    if name == "model_calibration_budget_preset" and value in BUDGET_PRESETS:
        return _apply_budget_preset(value)
    if name == "agent_models" and was != value:
        # The stage agent definitions carry model and effort in their frontmatter (0.20.1), so
        # they follow the setting now, not at the next install. Only the agent files change.
        import subprocess
        mp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gt_model_policy.py")
        if os.path.isfile(mp):
            subprocess.call([sys.executable, mp, "apply", "--agents-only"])
    if spec.get("orphaned"):
        print("  note: the %s module is off, so this is kept but not in effect until "
              "install.sh --with %s" % (spec["module"], spec["module"]))
    return 0


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("show", "list"):
        return show()
    if a[0] == "lotr":
        return lotr_view(check="--check" in a[1:])
    if a[0] == "security":
        return security_help() if "--full" in a[1:] else security_view()
    if a[0] == "explain" and len(a) > 1:
        return explain(a[1])
    if a[0] == "models":
        # the Models and cost page and its verbs (0.21.0): `models [set|reset|profile|apply-calibration]`
        import gt_models_page
        return gt_models_page.cli(a[1:])
    if a[0] == "testing":
        # the set-and-forget testing profile (0.21.0, #40): `testing on|off|status|sweep [options]`
        import gt_testing_profile
        return gt_testing_profile.main(a[1:] or ["status"])
    if a[0] == "io":
        # the I/O brokers (0.21.0): `io show` | `io allow <folder>`
        import gt_io_policy
        return gt_io_policy.cli(a[1:], setter=set_value)
    if a[0] == "set" and len(a) > 2:
        force = "--force" in a
        rest = [x for x in a[1:] if x != "--force"]
        if len(rest) < 2:
            print("usage: gt_settings.py set <name> <value> [--force]")
            return 2
        return set_value(rest[0], rest[1], force=force)
    if a[0] == "get" and len(a) > 1:
        print(get(a[1]) or "")
        return 0
    if a[0] in ("detect-machine", "machine"):
        force = "--force" in a
        if "--write" in a or force:
            prof, changed = write_machine_profile(force=force)
            if prof is None:
                print("refusing to write %s: it has no vault_path. Run /gt:gt-init first."
                      % CONFIG)
                return 2
            print("parallel_profile %s: %d core(s), %.1f GB -> cpu_max %d, io_max %d"
                  % ("updated" if changed else "unchanged", prof.get("cores", 0),
                     prof.get("memory_gb", 0.0), prof["cpu_max"], prof["io_max"]))
            return 0
        prof = detect_machine()
        print(json.dumps(prof, indent=2))
        return 0
    if a[0] == "jobs":
        # The budget as a NUMBER, for shell scripts and for any project's own tooling:
        #   JOBS=$(gt_settings.py jobs 42 --io-bound)
        # Without this the two settings are only reachable from Python, and a bash
        # script's only option is to invent its own policy -- which is what the Core
        # rule core_parallel_when_beneficial is trying to stop happening in every repo.
        rest = [x for x in a[1:] if x not in ("--io-bound", "--io")]
        io_bound = len(rest) != len(a[1:])
        try:
            want = int(rest[0]) if rest else 10 ** 6
        except ValueError:
            print("usage: gt_settings.py jobs [<units>] [--io-bound]", file=sys.stderr)
            return 2
        print(parallel_jobs(want, io_bound=io_bound))
        return 0
    print("usage: gt_settings.py [show | get <name> | set <name> <value> | "
          "explain <name> | security | testing [on|off|status] | models [set|reset|profile|apply-calibration] | io [show | allow <folder>] | jobs [<units>] [--io-bound] | "
          "detect-machine [--write] [--force]]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
