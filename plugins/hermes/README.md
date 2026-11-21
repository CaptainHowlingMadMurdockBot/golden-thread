# golden-thread-hermes

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) memory provider
and guardrail plugin that turns a [Golden Thread](https://github.com/shaven/golden-thread)
vault into first-class session memory: surfacing GT tools, injecting vault
context, and writing session logs back through Golden Thread's write-queue
engine with the same conflict-escalation, deduplication, and claim-respect
semantics as every other GT client.

## What you get

- **53 adapter commands** registered in the GT namespace (`gt_open`, `gt_list`,
  `gt_create`, `gt_work`, `gt_decide`, `gt_recall`, `gt_minimize`, ...), with
  write-gated commands producing queued vault writes drained by the broker.
- **Per-session project binding.** Each Hermes session claims its own active
  project (`.gt/hermes-session-claims.json` in the vault, fcntl-locked).
  Sessions never share or inherit an active project: a session that starts
  without a claim is *unbound* — its first `gt_open('<project-slug>')` binds it.
- **Fresh-start semantics.** There is no cross-session "last opened" default.
  `plugins.golden-thread.active_project` is an optional *starter hint* read at
  session start, nothing more.
- **Observer Hooks guardrails** as a Hermes plugin (`golden-thread-guardrails`):
  Core-context injection on body-trust topics, block-gating on dangerous
  shell commands and diet/body-surveillance tools, and diet-culture language
  notes on assistant output. Depth differs from Claude Code's hook grid — the
  upstream guard scripts (secret-shape, spill, private, protected-path,
  vault-write guards) are adapter-side TODO (see `docs/UPSTREAM-CONTRACT.md`).
- **Bundled engine scripts.** The write engine (`gt_write_queue.py`,
  `gt_broker.py`) and the BM25 recall engine (`gt_recall.py`) ship in this
  repository and are used automatically when no separate Golden Thread install
  exists on the machine.

## Install (fresh machine, no Golden Thread anywhere)

1. Clone this repository:

   ```sh
   git clone https://github.com/shaven/golden-thread.git
   cd golden-thread
   ```

2. Install the plugin into the same Python environment Hermes runs from:

   ```sh
   /path/to/hermes-venv/bin/pip install -e plugins/hermes
   ```

   Editable install matters: the bundled engine scripts are located relative
   to this source tree.

3. Create (or pick) your Golden Thread vault directory and enable the
   provider in `$HERMES_HOME/config.yaml`:

   ```yaml
   memory:
     provider: golden-thread

   plugins:
     golden-thread:
       vault_path: /home/you/vaults/my-golden-vault   # REQUIRED on a new machine
   ```

   Optional keys (defaults shown):

   ```yaml
   plugins:
     golden-thread:
       active_project: ''          # optional per-session starter hint; sessions still bind individually
       core_rules_max_lines: 200
       project_context_max_lines: 400
       ui_pane_path: ''
       ui_pane_auto: true
   ```

   Without `vault_path` there is *no portable default* — provide it explicitly.
   The vault needs only its normal GT shape (`Core.md` plus
   `Projects/<slug>/`); the engine creates its spool directories on demand.

4. Restart Hermes (gateway and desktop/dashboard) and check the provider came
   up:

   ```
   gt list
   ```

   That returns the vault's projects the same way as any GT client.

## How engine scripts are found

For each engine script the adapter resolves, in order:

1. **`GT_HOOKS_DIR` env var** — used when it exists and contains the script.
   Point it at a full GT install's `hooks/` if you have one. Note: an env dir
   that lacks a script is treated as unset, and the next tier is used.
2. **Installed hooks dir** — `$HOME/.claude/golden-thread/hooks` (the upstream
   Claude Code plugin install), if present.
3. **Bundled repo release** — the newest of
   `golden-thread-plugin/golden-thread/<X.Y.Z>/scripts/` in this repository.
   This is what makes a fresh install work with no upstream GT install.

Recall follows the same pattern (tier 3 is
`golden-thread-plugin/golden-thread-recall/<X.Y.Z>/scripts/gt_recall.py`).
`GT_HOOKS_DIR` is a machine-level override only; a wrong-but-existing path
without the scripts silently falls through to the bundle by design.

## Session binding

- Binding is per-session: `gt_open` writes a claim into
  `<vault>/.gt/hermes-session-claims.json`.
- Multiple concurrent Hermes sessions can bind different projects at once.
- A session that dies unbinds at process exit; stale claims are cleaned up.
- Write-gated commands (`gt_work`, `gt_decide`, `gt_plan`, `gt_implement`,
  `gt_complete`, ...) raise an `unbound` refusal if the session has no claim —
  that is the designed fresh-start behavior, not an error.

## Verifying the install

- `gt list` → lists `Projects/` under `vault_path`.
- `gt_open('<slug>')` → binds this session, returns the claim.
- `gt_work('<line>')` → lands in `Projects/<slug>/Log/<YYYY-MM-DD.md>` after
  the broker drains the queue (drained synchronously on write and lazily
  before reads).
- Recall: `gt_recall('some question')` → BM25 ranking over vault notes.

## Architecture

- `src/golden_thread_hermes/memory/` — the memory provider: command registry
  (`COMMANDS`, 53 commands), vault access, write-queue integration, session
  claims, and settings.
- `src/golden_thread_hermes/guardrails/` — Observer Hooks plugin
  (`pre_llm_call` Core-rules injection, `pre_tool_call` blocking/nudging,
  `transform_llm_output` guardrail notes).
- `src/golden_thread_hermes/watch.py` / `watch/` — project file watching.
- `docs/UPSTREAM-CONTRACT.md` — the contracts this adapter depends on and
  exposes to upstream; `docs/adapter-surface.json` is the generated
  machine-readable manifest (`scripts/gen_adapter_surface.py`).

Engine scripts (`gt_write_queue.py`, `gt_broker.py`) are upstream Golden
Thread code — shared, not reimplemented; everything else in the surface is
adapter-local. Vault writes from all clients (Claude Code, Hermes, humans)
merge through the same spool-and-drain queue, so a Hermes session and a
Claude Code session can share one vault safely.

## Tests

From `plugins/hermes` (system python):

```sh
python3 -m pytest tests/ -q          # 54 tests
npx pyright src tests                # type surface
```
