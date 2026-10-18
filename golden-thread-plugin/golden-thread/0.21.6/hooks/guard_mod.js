// gt expiry guard, the in-session half (0.21.2). Claude Code runs this inside the open session (gt
// supports Claude Code 2.1.292 and later); the guard's background copy is the fallback when it cannot act.
//
// The guard's Python half cannot compact an open session: the running Claude Code holds the
// conversation in its own memory. This mod can ($.session.compact), so for an open session the
// cut happens in place, while the prompt cache is still warm, and the user comes back to a small
// session with nothing to press.
//
// A mod is never told its session id, so the two halves pair up through files in
// ~/.claude/golden-thread/expiry-guard/mod/:
//   turn-<turnId>.json  this mod, at the end of each main-loop turn: the folder and the time
//   plan-<turnId>.json  the guard's idle hook, when it pairs exactly one turn with its session:
//                       what to do (compact | ask), when, and where to save the handoff
//   done-<turnId>.json  this mod: what it did, so the background job stands down
// No pairing, an ambiguous one, or a failure here leaves the background job to act as before.
//
// LAST RESORT, NO HOOK AT ALL. Both hooks that can write a plan (Stop and the idle notification)
// may fail to run: Claude Code's idle notifier is one 60 s timer that gives up for the whole idle
// period when the terminal was touched. If no plan has arrived by the last useful poll, this mod
// plans for itself: it reads expiry_guard.* from ~/.claude/vault-config.json (mode must be auto,
// open_sessions compact or ask, min_tokens respected, defaults as gt_settings registers them),
// asks Claude Code for the context size ($.session.usage; no answer, no plan), takes the cache
// lifetime from CLAUDE_CODE_PROMPT_CACHE_TTL (else 1 hour) and acts at turn end + lifetime - margin
// - EARLY_S, the margin rule the Python half uses. It never acts when a plan exists (the plan wins)
// or when a done record for the turn exists, and the next prompt cancels it like any plan. Its done
// record carries "source": "self".
//
// AUTO-COMPACT AND THE COMMAND (0.21.4). The same capture-then-cut path also runs on a schedule the
// owner sets, not only on idleness: auto_compact (off | 30..90, a percent of the context window) in
// vault-config.json. At the end of a main-loop turn, when the context has passed that percent, the
// session is known to have no background work running, no done record exists for the turn and at
// least AUTO_MIN_TURNS turns have passed since the last cut, it sends the capture turn (strict: if
// that turn cannot be sent, or ends in an error, nothing is cut) and then compacts in place. This is
// the ONE owner of "compact now" at a turn boundary: the idle plan above only ever acts after the
// idle period, and the turn marker + done record written here make the Python half stand down.
//
// BUSY OR NOT. A cut while a background agent or shell runs would lose what it reports back, so the
// automatic cut needs a positive "nothing running". The source is bg-<session>.json, written by
// gt_expiry_guard.py's Stop hook from Claude Code's own `background_tasks` list at the end of every
// reply. It counts only when it is this session's (its id), written during this turn, and says
// known: true. Anything else -- no marker yet (looked for again for a few seconds: the Stop hook and
// this hook race), another session's, an old one, one that cannot say, or a Claude Code that does not
// tell the mod its session id -- is "cannot tell", and cannot tell never cuts: it says so once per
// session and leaves the idle plan to work as usual.
//
// The command /gt:gt-minimize compact has the model capture and write the handoff itself, then drops
// request-<session>.json (gt_compact.py request), in this folder or, when sandbox mode keeps it out of
// ~/.claude/golden-thread, in ~/.gt-scratch/compact. Keyed by SESSION, not folder: several sessions
// often share a folder, and only the session that asked may take it, so a mod that cannot learn its
// session id takes no request. The mod cuts at that turn's end, with no second capture, and puts
// "Read the handoff at <the vault handoff>" in the prompt box.
//
// What this file may reach (tests/test_expiry_guard_inplace.py pins the list): HOME and the cache
// lifetime variable, files under ~/.claude/golden-thread/expiry-guard and ~/.gt-scratch/compact,
// ~/.claude/vault-config.json (read only), the session's id, context size and compaction, the
// prompt box, a log line.
// No shell, no network, no process, no model call of its own.

const POLL_MS = [75000, 150000, 300000]   // when to look for the plan after a turn ends
const STALE_S = 900                        // a plan or claim older than this is ignored
const EARLY_S = 120                        // a self-made plan acts this long before the Python job would
const MIN_AFTER_IDLE_S = 150               // the margin never reaches closer than this to the turn's end
const INSTRUCTIONS = 'Keep: the task in progress and its next step, decisions made and why, file paths and names touched, commands and settings that worked, open questions. Drop: tool output already acted on, repeated file dumps, dead ends.'
const SELF_DEFAULTS = { mode: 'warn-only', open: 'compact', minTokens: 150000, margin: 'auto', capture: 'on', auto: 0 }
const CAPTURE_WAIT_S = 600                 // a capture turn that has not ended by then is given up on
// Capture first (owner, 2026-10-13: "We only want it to do a handoff"): before the cut, one warm turn
// saves what is not yet in the vault and writes the handoff, so the compaction keeps a handoff, not
// whatever the summary happens to keep. It runs while the cache is warm, so it is mostly cache reads.
const AUTO_MIN_TURNS = 6                    // at most one automatic cut per this many main-loop turns
const AUTO_DEFAULT_WINDOW = 200000         // when Claude Code reports tokens but no window size
const BUSY_FRESH_S = 60                     // a busy marker older than this is not this turn's
const BUSY_RETRY_MS = [1000, 1500, 2500]    // look again for the marker after these waits (about 5 s in all)
const SAVE_STEPS = "(1) if this session produced findings, decisions or facts that are not yet in the vault, " +
  "queue them to the open project's research.md (decisions to decisions.md) through gt_write_queue.py and apply them " +
  'with gt_broker.py drain, naming --vault, as the core rules say; skip this if nothing is new. (2) Then reply with a ' +
  'handoff of at most 40 lines: the task in progress and its next step, decisions made and why, files touched, commands ' +
  "that worked, open questions, what step 1 saved, and the open project's slug with the paths of its memory/MEMORY.md " +
  'and source.md, to be read only when the work needs them (do not read them now). Start no other work: the session is ' +
  'compacted when this reply ends.'
const AFTER_CAPTURE = ' The last reply is the handoff written for this compaction: keep it in full, with its project pointer.'
function capturePrompt(plan) {
  if (plan && plan.auto) {
    return 'gt auto-compact: this session passed ' + Math.round(Number(plan.pct) || 0) + '% of its context window (the auto_compact ' +
      'setting) and is about to be compacted. Before that: ' + SAVE_STEPS
  }
  return 'gt expiry guard: this session has been idle and is about to be compacted while its prompt cache ' +
    'is still warm. Before that: ' + SAVE_STEPS
}
function srcOf(plan) {
  return plan.auto ? { source: 'auto', auto: true } : plan.self ? { source: 'self', self: true } : { source: 'plan' }
}

let cwd = ''
let interactive = false
let timers = []
let pending = ''                           // the turn this mod is waiting on, if any
let turnAt = 0                             // when that turn ended, in seconds
let turnsSince = AUTO_MIN_TURNS            // main-loop turns since the last automatic or requested cut
let lastRequestAt = 0                      // the newest compact request already acted on
let capture = null                         // {home, turn, plan, at} while the capture turn runs
let turnStartedAt = 0                      // when the current main-loop turn started, in seconds
let toldCannotTell = false                 // the "cannot tell" notice is said once per session

function dirOf(home) {
  return home + '/.claude/golden-thread/expiry-guard/mod'
}
// gt_compact.py's fallback when sandbox mode denies writes under ~/.claude/golden-thread.
function scratchOf(home) {
  return home + '/.gt-scratch/compact'
}
function safe(id) {
  return String(id || '').replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 80)
}
function nowS() {
  return Date.now() / 1000
}
function stopAll() {
  for (const t of timers) clearTimeout(t)
  timers = []
  pending = ''
}
async function cacheTtlEnv($) {
  try {
    return String((await $.env.get('CLAUDE_CODE_PROMPT_CACHE_TTL')) || '')
  } catch (err) {
    return ''
  }
}
async function homeDir($) {
  try {
    return (await $.env.get('HOME')) || ''
  } catch (err) {
    return ''
  }
}
async function readJson($, path) {
  try {
    if (!(await $.fs.exists(path))) return null
    const v = JSON.parse(await $.fs.read(path))
    return v && typeof v === 'object' ? v : null
  } catch (err) {
    return null
  }
}
async function writeJson($, path, obj) {
  try {
    await $.fs.write(path, JSON.stringify(obj) + '\n')
    return true
  } catch (err) {
    return false
  }
}
function baseOf(home) {
  return home + '/.claude/golden-thread/expiry-guard'
}
// Claude Code 2.1.296 tells a mod its session id ($.session.id), so the guard pairs a turn with
// its session exactly. 2.1.292 to 2.1.295 do not: the marker then carries no id and the guard falls
// back to folder and time, and this mod takes no compact request and makes no automatic cut.
async function sessionId($) {
  try {
    const id = await $.session.id()          // no id from this Claude Code: caught below
    return typeof id === 'string' ? id : ''
  } catch (err) {
    return ''
  }
}

function captureFailed(e) {
  if (!e) return false
  const st = String(e.status || e.outcome || e.reason || '').toLowerCase()
  return !!(e.error || e.aborted || e.interrupted || ['error', 'failed', 'aborted', 'cancelled', 'canceled'].includes(st))
}

function kTokens(n) {
  return Math.round((Number(n) || 0) / 1000) + 'K'
}

// The plan says compact: do it now, in place, and leave the summary where the guard said.
async function cutNow($, home, turn, plan) {
  const done = dirOf(home) + '/done-' + safe(turn) + '.json'
  const src = srcOf(plan)
  await writeJson($, done, Object.assign({ turn, status: 'compacting', at: nowS() }, src))
  let r
  try {
    r = await $.session.compact({ instructions: String(plan.instructions || '') })
  } catch (err) {
    await writeJson($, done, Object.assign({ turn, status: 'failed', at: nowS(), why: String((err && err.message) || err).slice(0, 200) }, src))
    return
  }
  const first = r && Array.isArray(r.messages) && r.messages[0]
  const summary = first && typeof first.text === 'string' ? first.text : ''
  if (summary && plan.handoff) {
    const head = '# Handoff: ' + (plan.name || 'session') + ', compacted in place\n\n' +
      'Written ' + new Date().toISOString() + ' by the gt expiry guard while the prompt cache was warm.\n' +
      'The open session continues from this summary; it is kept here so a later session can too.\n\n'
    try {
      await $.fs.write(plan.handoff, head + summary + '\n')
    } catch (err) {
      // the session is compacted either way; only the copy on disk is missing
    }
  }
  await writeJson($, done, Object.assign({
    turn, status: 'compacted', at: nowS(),
    before: r && r.tokensBefore, after: r && r.tokensAfter, handoff: summary ? plan.handoff || '' : '',
  }, src))
  // Owner, 2026-10-14: the handoff was written but nothing offered to load it. Put the pickup in the
  // prompt box so Enter reads it; the user can clear it and type something else instead.
  let filled = false
  const pickup = plan.pickup || plan.handoff
  if (summary && pickup) {
    try {
      const f = await $.prompt.fill({ text: 'Read the handoff at ' + pickup + ' and pick up where we left off.' })
      filled = !!(f && f.isFilled)
    } catch (err) {
      filled = false
    }
  }
  try {
    await $.ui.log((plan.auto ? 'Auto-compacted this session at ' + Math.round(Number(plan.pct) || 0) + '% context (' :
      plan.manual ? 'Compacted this session on request (' : 'Compacted this session while its cache was warm (') + kTokens(r && r.tokensBefore) +
      ' to ' + kTokens(r && r.tokensAfter) + ' tokens).' +
      (summary && pickup ? ' Handoff saved: ' + pickup +
        (filled ? '. Press Enter to load it.' : '. Ask me to read it to pick up.') : ' Carry on as usual.'))
  } catch (err) {
    // the notice is a courtesy
  }
}

// The plan says compact: capture first (one warm turn that saves and writes the handoff), then cut
// when that turn ends. capture_first off, or a prompt that cannot be sent, cuts at once as before.
async function compactNow($, home, turn, plan) {
  const cfg = await selfSettings($, home)
  if (!plan.strict && (cfg.capture === 'off' || plan.capture === false)) return cutNow($, home, turn, plan)
  const src = srcOf(plan)
  capture = { home, turn, plan: Object.assign({}, plan, { instructions: String(plan.instructions || INSTRUCTIONS) + AFTER_CAPTURE }), at: nowS() }
  await writeJson($, dirOf(home) + '/done-' + safe(turn) + '.json', Object.assign({ turn, status: 'capturing', at: nowS() }, src))
  try {
    await $.prompt.submit({ text: capturePrompt(plan) })
  } catch (err) {
    capture = null
    if (plan.strict) return abortCapture($, home, turn, plan)   // capture before cut: no save, no cut
    return cutNow($, home, turn, plan)
  }
  timers.push(setTimeout(() => { giveUpCapture($, turn).catch(() => {}) }, CAPTURE_WAIT_S * 1000))
}

async function abortCapture($, home, turn, plan) {
  await writeJson($, dirOf(home) + '/done-' + safe(turn) + '.json', Object.assign({ turn, status: 'capture-failed', at: nowS() }, srcOf(plan)))
  try {
    await $.ui.log('Auto-compact skipped: the save step could not run, so nothing was cut.')
  } catch (err) {
    // the notice is a courtesy
  }
}

async function giveUpCapture($, turn) {
  if (!capture || capture.turn !== turn) return
  const c = capture
  capture = null
  await writeJson($, dirOf(c.home) + '/done-' + safe(turn) + '.json', { turn, status: 'capture-timeout', at: nowS() })
}

// The plan says ask: say so, and put /compact in the prompt box so Enter does it.
async function askNow($, home, turn, plan) {
  let filled = false
  try {
    const r = await $.prompt.fill({ text: '/compact' })
    filled = !!(r && r.isFilled)
  } catch (err) {
    filled = false                         // no prompt box to fill: the notice alone
  }
  try {
    await $.ui.log('This session (' + kTokens(plan.context) + ' tokens) will lose its prompt cache in about ' +
      Math.max(1, Math.round((Number(plan.expires_at) - nowS()) / 60)) + ' min. ' +
      (filled ? 'Press Enter to compact it now, while that is cheap.' : 'Type /compact now, while that is cheap.'))
  } catch (err) {
    // nothing else to do
  }
  await writeJson($, dirOf(home) + '/done-' + safe(turn) + '.json',
    Object.assign({ turn, status: 'asked', at: nowS(), filled }, srcOf(plan)))
}

async function act($, turn, own) {
  if (pending !== turn) return
  const home = await homeDir($)
  if (!home) return
  if (await readJson($, dirOf(home) + '/done-' + safe(turn) + '.json')) return   // already acted: never twice
  let plan = await readJson($, dirOf(home) + '/plan-' + safe(turn) + '.json')
  if (plan && plan.turn !== turn) plan = null
  if (!plan && own) plan = own                                // no plan from the guard: the one made here
  if (!plan) return                                           // the guard withdrew it: a prompt came
  if (nowS() >= Number(plan.expires_at || 0)) return          // too late: a cut now would be cold
  pending = ''
  if (plan.action === 'compact') await compactNow($, home, turn, plan)
  else if (plan.action === 'ask') await askNow($, home, turn, plan)
}

// The guard's margin rule (gt_expiry_guard.margin_seconds) for a cache lifetime in seconds.
function marginS(margin, ttl) {
  const mins = margin === 'auto' ? (ttl === 300 ? 5 : 10) : Number(margin)
  return Math.max(0, Math.min((Number.isFinite(mins) ? mins : 10) * 60, ttl - MIN_AFTER_IDLE_S))
}

// expiry_guard.* from vault-config.json, anything unreadable or invalid falling back to the default.
async function selfSettings($, home) {
  const cfg = (await readJson($, home + '/.claude/vault-config.json')) || {}
  const get = (k) => (typeof cfg[k] === 'string' ? cfg[k].trim().toLowerCase() : '')
  const out = Object.assign({}, SELF_DEFAULTS)
  if (['auto', 'warn-only', 'off'].includes(get('expiry_guard'))) out.mode = get('expiry_guard')
  if (['compact', 'ask', 'fork', 'notify', 'off'].includes(get('expiry_guard.open_sessions'))) out.open = get('expiry_guard.open_sessions')
  const mt = get('expiry_guard.min_tokens')
  if (/^[0-9]+$/.test(mt) && Number(mt) >= 1) out.minTokens = Number(mt)
  const ac = String(cfg.auto_compact == null ? '' : cfg.auto_compact).trim().toLowerCase()
  out.auto = /^[0-9]+$/.test(ac) && Number(ac) >= 30 && Number(ac) <= 90 ? Number(ac) : 0
  if (['on', 'off'].includes(get('expiry_guard.capture_first'))) out.capture = get('expiry_guard.capture_first')
  const mg = get('expiry_guard.margin_min')
  if (mg === 'auto' || /^[0-9]+$/.test(mg)) out.margin = mg
  return out
}

async function contextTokens($) {
  try {
    const u = await $.session.usage()        // no answer throws: caught below
    const n = Number(u && u.context && u.context.tokens)
    return Number.isFinite(n) && n > 0 ? n : 0
  } catch (err) {
    return 0                               // no answer: no plan
  }
}

// Context fill, from what Claude Code reports: a percent when it gives one, else tokens over a
// window when it gives one, else tokens over AUTO_DEFAULT_WINDOW.
async function contextInfo($) {
  let u = null
  try {
    u = await $.session.usage()
  } catch (err) {
    return { pct: null, tokens: 0 }
  }
  const c = (u && u.context) || {}
  const tokens = Number(c.tokens)
  let pct = null
  for (const k of ['percent', 'pct', 'percentage', 'usedPercent']) {
    if (pct === null && Number.isFinite(Number(c[k])) && c[k] !== null && c[k] !== '') pct = Number(c[k])
  }
  if (pct === null && Number.isFinite(tokens) && tokens > 0) {
    let win = AUTO_DEFAULT_WINDOW
    for (const k of ['max', 'limit', 'window', 'size', 'maxTokens', 'contextWindow']) {
      if (Number(c[k]) > 0) { win = Number(c[k]); break }
    }
    pct = (tokens / win) * 100
  }
  return { pct, tokens: Number.isFinite(tokens) ? tokens : 0 }
}

// 'idle' | 'busy' | 'unknown', from the Stop hook's bg-<session>.json (see BUSY OR NOT above).
async function busyState($, home, sid) {
  if (!sid) return 'unknown'
  const m = await readJson($, dirOf(home) + '/bg-' + safe(sid) + '.json')
  if (!m || m.session !== sid || m.known !== true) return 'unknown'
  const at = Number(m.at || 0)
  if (!(at > 0) || nowS() - at > BUSY_FRESH_S || (turnStartedAt > 0 && at < turnStartedAt - 1)) return 'unknown'
  const n = Number(m.running)
  if (!Number.isFinite(n)) return 'unknown'
  return n > 0 ? 'busy' : 'idle'
}

async function tellCannotTell($, pct) {
  if (toldCannotTell) return
  toldCannotTell = true
  try {
    await $.ui.log('Auto-compact did not cut at ' + Math.round(Number(pct) || 0) + '% context: gt cannot tell ' +
      'whether background work is running in this session, and it never cuts on a guess. ' +
      '/gt:gt-minimize compact still works; this is said once per session.')
  } catch (err) {
    // the notice is a courtesy
  }
}

// The idle plan's polls, from the turn's end, for a turn the automatic cut did not take after all.
function armIdle($, turn, endedAt) {
  pending = turn
  turnAt = endedAt
  const gone = Math.max(0, (nowS() - endedAt) * 1000)
  timers = POLL_MS.map((ms, i) => setTimeout(() => { lookForPlan($, turn, i).catch(() => {}) }, Math.max(1000, ms - gone)))
}

// The marker was not there at the turn's end: look again a few times, then cut, defer or stand down.
async function recheckBusy($, home, turn, sid, plan, idx, endedAt) {
  const st = await busyState($, home, sid)
  if (st === 'idle') {
    turnsSince = 0
    return compactNow($, home, turn, plan)
  }
  if (st === 'unknown' && sid && idx < BUSY_RETRY_MS.length - 1) {
    timers.push(setTimeout(() => {
      recheckBusy($, home, turn, sid, plan, idx + 1, endedAt).catch(() => {})
    }, BUSY_RETRY_MS[idx + 1]))
    return
  }
  if (st === 'unknown') await tellCannotTell($, plan.pct)
  armIdle($, turn, endedAt)                                   // busy: the next turn boundary tries again
}

async function findRequest($, home, sid) {
  if (!sid) return null                                       // no id: never take another session's request
  for (const path of [dirOf(home) + '/request-' + safe(sid) + '.json', scratchOf(home) + '/request-' + safe(sid) + '.json']) {
    const req = await readJson($, path)
    if (req && req.session === sid) return { req, path }
  }
  return null
}

// At a turn boundary: a requested cut (/gt:gt-minimize compact) or an automatic one (auto_compact).
// Returns true when a cut is scheduled (or still being decided), so the idle plan is not also set up
// for this turn; a decision not to cut sets the idle plan up itself.
async function wantedCut($, home, turn, sid) {
  turnsSince += 1
  const found = await findRequest($, home, sid)
  const req = found && found.req
  const name = (cwd.split('/').filter(Boolean).pop()) || 'session'
  if (req && !req.consumed && typeof req.handoff === 'string' && req.handoff &&
      Number(req.at) > lastRequestAt && nowS() - Number(req.at) <= STALE_S) {
    // The model already captured and wrote the handoff this turn (gt_compact.py refuses without it).
    const plan = { turn, action: 'compact', manual: true, name, instructions: INSTRUCTIONS, expires_at: nowS() + 3600,
      handoff: baseOf(home) + '/handoff-manual-' + safe(turn) + '.md', pickup: req.handoff }
    timers.push(setTimeout(() => {
      (async () => {
        lastRequestAt = Number(req.at)
        turnsSince = 0
        await writeJson($, found.path, Object.assign({}, req, { consumed: turn }))
        await cutNow($, home, turn, plan)
      })().catch(() => {})
    }, 1000))
    return true
  }
  const cfg = await selfSettings($, home)
  if (!(cfg.auto > 0) || turnsSince < AUTO_MIN_TURNS) return false
  if (await readJson($, dirOf(home) + '/done-' + safe(turn) + '.json')) return false
  const info = await contextInfo($)
  if (info.pct === null || info.pct < cfg.auto) return false
  const st = await busyState($, home, sid)
  if (st === 'busy') return false                                   // a background task is running: try the next turn
  const plan = { turn, action: 'compact', auto: true, strict: true, pct: info.pct, context: info.tokens, name,
    written_at: nowS(), expires_at: nowS() + 3600, instructions: INSTRUCTIONS,
    handoff: baseOf(home) + '/handoff-auto-' + safe(turn) + '.md' }
  const endedAt = nowS()
  if (st === 'idle') {
    timers.push(setTimeout(() => {
      turnsSince = 0
      compactNow($, home, turn, plan).catch(() => {})
    }, 1000))
    return true
  }
  if (!sid) {                                                       // nothing to look again for
    await tellCannotTell($, info.pct)
    return false
  }
  timers.push(setTimeout(() => { recheckBusy($, home, turn, sid, plan, 0, endedAt).catch(() => {}) }, BUSY_RETRY_MS[0]))
  return true
}

// No plan arrived. At the last poll that still leaves time, decide whether to cut by ourselves.
async function considerSelfPlan($, home, turn, idx) {
  const cfg = await selfSettings($, home)
  if (cfg.mode !== 'auto' || (cfg.open !== 'compact' && cfg.open !== 'ask')) return
  const raw = (await cacheTtlEnv($)).trim().toLowerCase()
  const ttl = raw === '5m' || raw === '300' || raw === '5' ? 300 : 3600
  const base = turnAt + ttl - marginS(cfg.margin, ttl)         // when the Python job would act
  const next = POLL_MS[idx + 1]
  if (next !== undefined && turnAt + next / 1000 < base - 20) return   // a later poll still has time
  const fireAt = Math.max(nowS() + 2, base - EARLY_S)
  if (fireAt >= turnAt + ttl - 30) return                      // nothing warm left to protect
  if (await readJson($, dirOf(home) + '/done-' + safe(turn) + '.json')) return
  const tokens = await contextTokens($)
  if (tokens < cfg.minTokens) return
  const own = {
    turn, action: cfg.open, written_at: nowS(), fire_at: fireAt, expires_at: turnAt + ttl,
    context: tokens, name: (cwd.split('/').filter(Boolean).pop()) || 'session',
    handoff: baseOf(home) + '/handoff-self-' + safe(turn) + '.md', instructions: INSTRUCTIONS, self: true,
  }
  for (const t of timers) clearTimeout(t)
  timers = [setTimeout(() => { act($, turn, own).catch(() => {}) }, Math.max(1000, (fireAt - nowS()) * 1000))]
}

async function lookForPlan($, turn, idx) {
  if (pending !== turn) return
  const home = await homeDir($)
  if (!home) return
  const plan = await readJson($, dirOf(home) + '/plan-' + safe(turn) + '.json')
  if (!plan || plan.turn !== turn || nowS() - Number(plan.written_at || 0) > STALE_S) {
    await considerSelfPlan($, home, turn, idx)
    return
  }
  for (const t of timers) clearTimeout(t)
  timers = [setTimeout(() => { act($, turn).catch(() => {}) },
    Math.max(1000, (Number(plan.fire_at) - nowS()) * 1000))]
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    cwd = (e && e.cwd) || ''
    interactive = !!(e && e.isInteractive)
    return next(e)
  })

  // Anything the person starts cancels what was waiting: the idle period is over.
  on('turn.start', async ($, e, next) => {
    if (!(e && e.agentId) && !capture) {                // the capture turn is this mod's own
      stopAll()
      turnStartedAt = nowS()
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    try {
      if (!interactive || (e && e.agentId) || !(e && e.turnId)) return r
      if (capture) {
        // The capture turn ended: cut now, outside this hook (a compaction from inside it would
        // wait on the turn the hook is holding).
        const c = capture
        capture = null
        stopAll()
        if (c.plan.strict && captureFailed(e)) {            // capture before cut: a failed save cuts nothing
          await abortCapture($, c.home, c.turn, c.plan)
          return r
        }
        setTimeout(() => { cutNow($, c.home, c.turn, c.plan).catch(() => {}) }, 1000)
        return r
      }
      stopAll()
      const home = await homeDir($)
      if (!home) return r
      const turn = String(e.turnId)
      const sid = await sessionId($)
      const mark = sid ? { turn, cwd, at: nowS(), session: sid, v: 2 } : { turn, cwd, at: nowS(), v: 1 }
      const ok = await writeJson($, dirOf(home) + '/turn-' + safe(turn) + '.json', mark)
      if (!ok) return r
      if (await wantedCut($, home, turn, sid)) return r
      pending = turn
      turnAt = nowS()
      timers = POLL_MS.map((ms, i) => setTimeout(() => { lookForPlan($, turn, i).catch(() => {}) }, ms))
    } catch (err) {
      // the guard's background job is the fallback; a turn never fails over this
    }
    return r
  })
}
