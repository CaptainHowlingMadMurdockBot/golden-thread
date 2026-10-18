# golden-thread-hermes

A Hermes memory-provider plugin and Observer Hooks guardrail plugin for [Golden Thread](https://github.com/shaven/golden-thread).

## What it does

- **Memory provider** (`golden-thread`) exposes Golden Thread projects as Hermes tools:
  - `gt_open(project_slug)` — set an active project.
  - `gt_list()` — list project slugs.
  - `gt_create(name, idea)` — create a new project.
  - `gt_work(summary)` — append a timestamped log entry to the active project.
  - `gt_decide(title, context, decision, consequences)` — write an ADR under the active project.
  - Plus automatic context injection from the vault's `Core.md` and the active project's notes.
- **Observer Hooks guardrails** (`golden-thread-guardrails`) enforces selected Golden Thread rules without requiring explicit tool calls.

## Install

```bash
cd /path/to/golden-thread/plugins/hermes
pip install -e .
```

Then activate the memory provider in Hermes:

```yaml
memory:
  provider: golden-thread

plugins:
  golden-thread:
    vault_path: /mnt/obsidian-vaults/eman-mobile/golden-vault
    active_project: hermes-golden-thread
```

## Configuration

The provider reads `plugins.golden-thread` from `config.yaml`:

- `vault_path` — absolute path to the Golden Thread vault directory.
- `active_project` — slug of the project to open by default.
- `core_rules_max_lines` — max lines from `Core.md` to inject (default: 200).
- `project_context_max_lines` — max lines from project docs to inject (default: 400).

## Tools

All tools return JSON strings for Hermes.
