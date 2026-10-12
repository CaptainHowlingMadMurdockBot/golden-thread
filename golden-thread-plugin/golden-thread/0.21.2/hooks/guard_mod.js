// gt expiry guard, the in-session half (0.21.2). Claude Code 2.1.267 or later runs this inside the
// open session; anything older ignores it and the guard's background copy does the work instead.
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
// What this file may reach (tests/test_guard_mod.py pins the list): HOME, files under
// ~/.claude/golden-thread/expiry-guard, the session's own compaction, the prompt box, a log line.
// No shell, no network, no process, no model call of its own.

const POLL_MS = [75000, 150000, 300000]   // when to look for the plan after a turn ends
const STALE_S = 900                        // a plan or claim older than this is ignored

let cwd = ''
let interactive = false
let timers = []
let pending = ''                           // the turn this mod is waiting on, if any

function dirOf(home) {
  return home + '/.claude/golden-thread/expiry-guard/mod'
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
function kTokens(n) {
  return Math.round((Number(n) || 0) / 1000) + 'K'
}

// The plan says compact: do it now, in place, and leave the summary where the guard said.
async function compactNow($, home, turn, plan) {
  const done = dirOf(home) + '/done-' + safe(turn) + '.json'
  await writeJson($, done, { turn, status: 'compacting', at: nowS() })
  let r
  try {
    r = await $.session.compact({ instructions: String(plan.instructions || '') })
  } catch (err) {
    await writeJson($, done, { turn, status: 'failed', at: nowS(), why: String((err && err.message) || err).slice(0, 200) })
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
  await writeJson($, done, {
    turn, status: 'compacted', at: nowS(),
    before: r && r.tokensBefore, after: r && r.tokensAfter, handoff: summary ? plan.handoff || '' : '',
  })
  try {
    await $.ui.log('gt: compacted this session while its cache was warm (' + kTokens(r && r.tokensBefore) +
      ' to ' + kTokens(r && r.tokensAfter) + ' tokens). Carry on as usual.' +
      (summary && plan.handoff ? ' Summary saved: ' + plan.handoff : ''))
  } catch (err) {
    // the notice is a courtesy
  }
}

// The plan says ask: say so, and put /compact in the prompt box so Enter does it.
async function askNow($, home, turn, plan) {
  let filled = false
  try {
    const r = await $.prompt.fill({ text: '/compact' })
    filled = !!(r && r.isFilled)
  } catch (err) {
    filled = false                         // 2.1.267 has no prompt.fill: the notice alone
  }
  try {
    await $.ui.log('gt: this session (' + kTokens(plan.context) + ' tokens) will lose its prompt cache in about ' +
      Math.max(1, Math.round((Number(plan.expires_at) - nowS()) / 60)) + ' min. ' +
      (filled ? 'Press Enter to compact it now, while that is cheap.' : 'Type /compact now, while that is cheap.'))
  } catch (err) {
    // nothing else to do
  }
  await writeJson($, dirOf(home) + '/done-' + safe(turn) + '.json', { turn, status: 'asked', at: nowS(), filled })
}

async function act($, turn) {
  if (pending !== turn) return
  const home = await homeDir($)
  if (!home) return
  const plan = await readJson($, dirOf(home) + '/plan-' + safe(turn) + '.json')
  if (!plan || plan.turn !== turn) return                     // the guard withdrew it: a prompt came
  if (nowS() >= Number(plan.expires_at || 0)) return          // too late: a cut now would be cold
  pending = ''
  if (plan.action === 'compact') await compactNow($, home, turn, plan)
  else if (plan.action === 'ask') await askNow($, home, turn, plan)
}

async function lookForPlan($, turn) {
  if (pending !== turn) return
  const home = await homeDir($)
  if (!home) return
  const plan = await readJson($, dirOf(home) + '/plan-' + safe(turn) + '.json')
  if (!plan || plan.turn !== turn || nowS() - Number(plan.written_at || 0) > STALE_S) return
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
    if (!(e && e.agentId)) stopAll()
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    try {
      if (!interactive || (e && e.agentId) || !(e && e.turnId)) return r
      stopAll()
      const home = await homeDir($)
      if (!home) return r
      const turn = String(e.turnId)
      const ok = await writeJson($, dirOf(home) + '/turn-' + safe(turn) + '.json', { turn, cwd, at: nowS(), v: 1 })
      if (!ok) return r
      pending = turn
      timers = POLL_MS.map((ms) => setTimeout(() => { lookForPlan($, turn).catch(() => {}) }, ms))
    } catch (err) {
      // the guard's background job is the fallback; a turn never fails over this
    }
    return r
  })
}
