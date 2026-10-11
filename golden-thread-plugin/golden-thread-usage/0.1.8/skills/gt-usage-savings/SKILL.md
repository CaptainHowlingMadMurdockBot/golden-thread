---
name: gt-usage-savings
description: "Show what gt's own mechanisms saved or cost in tokens, per mechanism, with gt's overhead as a cost line and a net figure, from the local savings ledger. Use when the user says: is gt saving me tokens, what does gt save, how much does gt cost me, savings ledger, show my gt savings, is gt worth it, export my savings, benchmark gt."
plain:
  - cmd: "/gt-usage:gt-usage-savings"
    does: "Shows what gt's own mechanisms saved or cost you in tokens."
    how: "Reads the local savings ledger and prints, per mechanism, the saving, gt's own overhead and the net figure. Nothing leaves your machine."
    when: "You wonder whether gt pays for itself."
    next: "/gt-usage:gt-usage"
    hear: "is gt saving me tokens|show my gt savings"
model_intent: balanced
---

# Golden Thread Usage: savings ledger

**Under gt sandbox mode** (the session-start line says SANDBOX MODE; `gt_settings.py get sandbox_mode` prints `on`) nothing here needs the vault: the ledger and the transcripts it reads live under `~/.claude/`, which stays readable. Every step runs as written.

What gt's mechanisms actually used against what the same work would have used set up differently,
with gt's own overhead as a cost beside the savings and a net figure at the bottom. It is recorded
on this machine from Claude Code's own transcripts. **No figure is ever sent anywhere.**

## Run it

```bash
python3 ~/.claude/golden-thread/hooks/gt_savings.py record     # catch up first (silent hook does it each session)
python3 ~/.claude/golden-thread/hooks/gt_savings.py report     # last 7 days
```

Show the table verbatim, then add **one sentence**: the net figure and which rows are only
`estimated` or `design`. Do not restate the table in prose.

Options a person asks for in words:

- a different window: `report --days 30`, or `report --since 2026-10-01 --until 2026-10-07`
- one project: `projects` lists the opaque ids the ledger holds, then `report --project <id>`
  (the ledger stores a hash of the project, never its name)
- "what would `average` have cost me this week": `report --profile average` (or `very-high`,
  `inherit`). Only the counterfactual columns move; the actual columns never do
- a shareable summary: `export --days 7 --out summary.md`. It holds mechanism totals, their
  states and a bar chart: no vault path, project, session or page content. Say where the file was
  written, and that sending it anywhere is the user's own act
- a head-to-head: run the same task twice, once with gt and once without, then
  `benchmark --task <label> --with-gt <tokens> --without-gt <tokens>`. Those are the only figures
  the report labels **measured** for the "without gt" side

## Rules when reporting any of this

- **Keep the state next to the number.** `measured` came from transcript usage fields or a
  head-to-head benchmark; `estimated` is derived from measured inputs (a counterfactual price,
  characters / 4); `design` is a claim with no measurement behind it. Never present an `estimated`
  or `design` figure as measured, and never fold a `design` row into the net.
- **Dollars follow `savings_billing`.** On `api` (invoiced per token, how most companies pay) a
  saved dollar is spend avoided: say so, and lead with the Core-rules headline line. On
  `subscription` (the default) they stay a list-price equivalent, as the next rule says.
- **Tokens are the unit, dollars are a list-price equivalent, not a bill.** A subscription is not
  billed per token. Say so whenever a dollar figure is quoted.
- **Effort is not claimed.** There is no per-call token field for effort, so the report does not
  attribute a saving to it.
- **The overhead line is partial.** It counts the Core-rule text injected each prompt. Session-start
  output, report cards and hook context are not measured; say so if the net is quoted.
- **The Core-rules rows are a benchmark applied, not a measurement.** `Core rules (rework avoided)`
  is the session's own work times the factor a 2026-10-11 A/B benchmark measured (rules off cost
  1.262x per finished task): `estimated`, and only as wide as that benchmark's tasks. Secret leaks,
  release re-runs and persona work are the `design` row beside it, outside the net.
- **If recording is off**, the report says so (`savings_ledger` is `off`) and shows only what was
  recorded before. Do not turn it on for the user; `/gt:gt-settings savings_ledger on` does.
- **An empty ledger is not zero savings.** It means nothing has been recorded yet.

## What it is not

It is not a promise. On a machine whose profile is `very-high` and whose agents already run at the
session model, most rows are legitimately close to nothing, and the report shows that rather than
a design figure.

## Settings

`savings_ledger` — `on` (default) or `off`. Listed by `/gt:gt-settings show`.
