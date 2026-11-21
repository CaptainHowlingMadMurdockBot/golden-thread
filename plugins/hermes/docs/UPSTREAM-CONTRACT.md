# Golden Thread ↔ Hermes: integration contract & upstream development guidelines

Audience: Golden Thread upstream maintainers and agents building the next
gt release(s). This document is written from the Hermes adapter side of the
integration (adapter at `plugins/hermes/`, verified against upstream gt
**0.22.1**). It has two jobs:

1. Pin down the contracts the adapter depends on, so upstream knows what
   a change to them costs on the Hermes side.
2. A short per-feature checklist that keeps Golden Thread usable from
   **non-Claude-Code hosts**, so "with Hermes in mind" becomes a standing
   part of next-version development instead of an afterthought.

Terms: **MUST** = breakage if changed without coordination; **SHOULD** =
strong preference; **REQUESTED** = upstream-side addition that would make
integration cheap. Host-agnostic is the design axis: any agent platform
that mounts the same vault and filesystem should be able to consume the
engine.

## 1. How the Hermes adapter consumes upstream (current reuse map)

| Upstream capability | Adapter today | Upstream code path |
| --- | --- | --- |
| Vault writes | **Shared** — shells out to upstream engine | `hooks/gt_write_queue.py`, `hooks/gt_broker.py` |
| Recall (BM25) | Shared (bundled copy) | `hooks/gt_recall.py` (preferred) or bundled `golden-thread-plugin/golden-thread-recall/<X.Y.Z>/scripts/gt_recall.py` |
| Watch | Reimplemented | shares upstream state dir `~/.claude/golden-thread/watch` |
| 53 gt commands | Reimplemented adapter-local | no upstream invocation |
| Session claims (multi-session guard) | Reimplemented | upstream has none today (see §3) |
| Settings engine | Divergent | adapter writes a vault-local YAML upstream does not read (see §4) |

Everything else (guardrail hooks, unlock/crypto, farm, intake, ledger,
agents/packs) is absent on the Hermes side today — adapter-side TODO, not an
upstream requirement.

Engine scripts live at `hooks/` under the installed plugin directory. Hermes
resolves each script in order: (1) env `GT_HOOKS_DIR` when it exists and
contains the named script, (2) `$(HOME)/.claude/golden-thread/hooks` if
present, (3) the newest bundled repo release,
`golden-thread-plugin/golden-thread/<X.Y.Z>/scripts/` — so a fresh Hermes
install runs with no upstream GT install on the machine. An env dir that
lacks a script is treated as unset (falls through to the bundle).

## 2. Stability contracts the adapter depends on

- **C1 (MUST) Write-path engine CLI.** `gt_write_queue.py` and
  `gt_broker.py` must stay standalone-invocable with stable CLI/stdin
  contracts, stable spool layout (under `Projects/golden-thread/spool/`),
  and unchanged drain semantics (conflict escalation, deduplication,
  claim-respect). This is the only hard-shared code path; a silent schema
  change breaks every Hermes vault write. If a change is unavoidable,
  version the spool format or gate it behind a setting.
- **C2 (MUST) `GT_HOOKS_DIR` env override.** Adapters point integration at
  arbitrary install locations by env (Hermes semantics: an env dir that
  lacks the script is treated as unset, falling through to a bundled copy).
  Keep it honored (or bless a
  documented equivalent) wherever new engine defaults are added — never
  hardcode the `~/.claude` prefix inside engine logic.
- **C3 (SHOULD) Engine-script discipline.** Host-agnostic engine scripts
  should: accept documented CLI/stdin; emit machine output on stdout
  (JSON) and keep logs on stderr; exit non-zero on failure; take all
  roots (vault, state, hooks) via parameters/env rather than assumption.
  Hooks that are inherently Claude-Code-specific (stdin payload shape,
  event-name assumptions) SHOULD be marked as such so adapters know they
  cannot be reused.
- **C4 (SHOULD) Recall location convention.** Either ship
  `hooks/gt_recall.py` in the standard install (that would immediately
  improve the Hermes side) or keep the bundled-release convention
  `golden-thread-recall/<X.Y.Z>/scripts/gt_recall.py` stable. Hermes applies
  the same bundled-release convention to the write engine
  (`golden-thread/<X.Y.Z>/scripts/`), so keep that layout stable too.

## 3. Multi-client vault invariants (the "with Hermes in mind" core rule)

**One vault is shared by many concurrent agent sessions — across hosts.** A
real incident from development: one session's project switch re-pointed
*other concurrent sessions'* logging at the wrong project (misfiled log
entries), because routing ran through a single global "last-opened project"
pointer. Hermes now enforces the following contract — it would be a good
engine-level invariant for any host:

- **Zero writes without a per-session claim.** A session that has not
  claimed a project MUST NOT cause vault writes of any kind (work logs,
  checkpoints, auto-capture, compaction artifacts). Writes without a claim
  are the integration bug class that produced real misrouted data.
- **No global default pointer.** Opening a project in one session must not
  re-point any other session. Global "active project" state is the
  single-writer-host assumption that breaks under multi-client reality;
  if upstream ever needs a claims file, make it **multi-client by design**
  (per-claim client/session id, e.g. current Hermes file
  `<vault>/.gt/hermes-session-claims.json`), and coordinate on the schema
  before the first file-based formalization so hosts do not collide.
- **Refusal must be observable and harmless.** A claim-less session that
  attempts a write should get a clear refusal response (status + how to
  claim), and nothing on disk should change — a refusal is a successful
  outcome at the engine level, not an error to hide.

## 4. Settings engine

Today there are two disconnected settings systems: upstream
`gt_settings.py` manages a multi-key token-saving system (usage_plan,
farm_first, watch, expiry_guard, cut_handoff_facts, auto_clear,
auto_compact, …) while the Hermes adapter reads/writes a vault-local
settings YAML that no upstream engine consults.

- **REQUESTED** a machine-readable settings schema per release
  (e.g. `settings.schema.json`: key, type, default, applies-to host),
  plus continuing the existing CHANGELOG `Default flip:` convention —
  that convention is genuinely useful and is how adapter-side reviews
  find behavior changes (gt-upgrade --preview reads it today).
- Adapter-side (tracked separately): bridge gt_settings to the upstream
  key system instead of a parallel store, so upstream token-saving
  features reach Hermes hosts.

## 5. Capability manifest (request)

A per-release, machine-readable surface manifest would turn manual parity
combs (what we do today: enumerate 130+ hooks and 50-odd commands by hand)
into a mechanical diff:

- **REQUESTED** `commands.json` (or MANIFEST extension): per command,
  name, args schema, write-class (read/write/engine), engines-invoked,
  and a host-agnostic flag.
- Mirror on the Hermes side: this PR ships `docs/adapter-surface.json`
  (generated by `scripts/gen_adapter_surface.py`) so the same diff works
  against the adapter surface.

## 6. Per-feature checklist for next-version development

For every new command, hook, or default, before release:

- [ ] Vault writes flow through the queue/broker — no direct writes bypassing the queue.
- [ ] Scope-aware: respects per-session claims; refuses cleanly without a claim; never falls back to a global default target.
- [ ] Engine logic stays host-agnostic; Claude-Code-specific policy is isolated (and marked, per §3/C3).
- [ ] Path roots (vault/hooks/state) come from parameters/env, not hardcoded host paths.
- [ ] stdout is machine output; logs go to stderr.
- [ ] Settings changes include a schema entry (§4 request) and, for default changes, a CHANGELOG `Default flip:` line.
- [ ] If the feature was ported from / mirrored in a non-Claude host, note it; if not portable, state why (so adapters know what to reimplement).
- [ ] New engine scripts are importable standalone and covered by the surface manifest (§5 request).

## 7. What the adapter already works around at 0.22.1 (context)

So next-version work does not have to rediscover integration costs: the
adapter holds per-session claims itself, gates its 10 project-write
commands behind them, keeps auto-capture paused for unbound sessions, and
resolves upstream engine paths by env. Anything in this list that gains an
engine-level contract (§2, §3) can later be simplified on the adapter side
or deleted.

---
*Verification basis: upstream hooks/ inventory and grep checks at 0.22.1;
adapter source on branch hermes-agent-fixes; behavior probes: cold-start
write (queue + drain) and recall resolved from the bundled repo release with
no upstream install present, GT_HOOKS_DIR override, home-relative default.*
