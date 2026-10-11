// node --test golden-thread-pane/0.1.0/tests/pane_core.test.mjs
// The pure functions the gt pane's register.js imports: no Claude Code API is touched here.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  MAX_BUTTONS, HOTKEYS, hotkeyFor, indexForHotkey, parseButtons, serializeButtons,
  parseSetting, parsePersonas, personaSeed, moveButton, swapButtons, fitLabel,
  arrangeStart, arrangeReduce, bandLine, PLAIN_PERSONAS,
  newestReading, latestReading, usageFooter, sessionLines, STALE_MS, parseStatusSetting, footerPlan, CLOSE_TIP, shouldShowTip, serializeTipState,
} from '../hooks/pane_core.js'

const twelve = () => Array.from({ length: 12 }, (_, i) => ({ label: 'B' + (i + 1), prompt: 'Do thing ' + (i + 1) }))
const file = (buttons, version = 1) => JSON.stringify({ version, buttons })

test('hotkeys are 1-9, 0, q, w in order and map both ways', () => {
  assert.deepEqual(HOTKEYS, ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'q', 'w'])
  assert.equal(MAX_BUTTONS, 12)
  assert.equal(hotkeyFor(0), '1')
  assert.equal(hotkeyFor(9), '0')
  assert.equal(hotkeyFor(10), 'q')
  assert.equal(hotkeyFor(11), 'w')
  assert.equal(hotkeyFor(12), undefined)
  assert.equal(hotkeyFor(-1), undefined)
  for (let i = 0; i < 12; i++) assert.equal(indexForHotkey(hotkeyFor(i)), i)
  assert.equal(indexForHotkey('x'), -1)
})

test('parseButtons accepts a good file and trims nothing it should not', () => {
  const got = parseButtons(file(twelve()))
  assert.equal(got.length, 12)
  assert.deepEqual(got[0], { label: 'B1', prompt: 'Do thing 1' })
  assert.equal(parseButtons(file([{ label: 'Only', prompt: 'One' }])).length, 1)
})

test('parseButtons rejects a bad file with a one-line reason, never a crash', () => {
  const bad = [
    ['', /empty|JSON/i],
    ['{not json', /JSON/i],
    ['[]', /version|buttons/i],
    [file(twelve(), 2), /version/i],
    [file([]), /at least one/i],
    [file(twelve().concat([{ label: 'X', prompt: 'y' }])), /12/],
    [file([{ label: '', prompt: 'p' }]), /button 1.*label/i],
    [file([{ label: 'l', prompt: '' }]), /button 1.*prompt/i],
    [file([{ label: 'l', prompt: 5 }]), /button 1.*prompt/i],
    [file([{ label: 'x'.repeat(23), prompt: 'p' }]), /22/],
    [file([{ label: 'ok', prompt: 'a\nb' }]), /control/i],
    [file([{ label: 'a\u0007', prompt: 'b' }]), /control/i],
    [file([{ label: '  ', prompt: 'b' }]), /label/i],
    [file([null]), /button 1/i],
  ]
  for (const [text, re] of bad) {
    assert.throws(() => parseButtons(text), (e) => e instanceof Error && re.test(e.message) && !/\n/.test(e.message), JSON.stringify(text).slice(0, 40))
  }
})

test('a 22-character label is allowed and 22 is the limit', () => {
  assert.equal(parseButtons(file([{ label: 'x'.repeat(22), prompt: 'p' }])).length, 1)
})

test('serializeButtons round-trips through parseButtons', () => {
  const list = twelve()
  const text = serializeButtons(list)
  assert.ok(text.endsWith('\n'))
  assert.equal(JSON.parse(text).version, 1)
  assert.deepEqual(parseButtons(text), list)
})

test('parseSetting: open, closed, off; anything else is open', () => {
  assert.equal(parseSetting(JSON.stringify({ pane_at_start: 'closed' })), 'closed')
  assert.equal(parseSetting(JSON.stringify({ pane_at_start: 'off' })), 'off')
  assert.equal(parseSetting(JSON.stringify({ pane_at_start: 'open' })), 'open')
  assert.equal(parseSetting(JSON.stringify({})), 'open')
  assert.equal(parseSetting(JSON.stringify({ pane_at_start: 'sideways' })), 'open')
  assert.equal(parseSetting('not json'), 'open')
  assert.equal(parseSetting(undefined), 'open')
})

test('parsePersonas reads a comma list or an array, lower-cased, none dropped', () => {
  assert.deepEqual(parsePersonas(JSON.stringify({ persona: 'Executive, leadership' })), ['executive', 'leadership'])
  assert.deepEqual(parsePersonas(JSON.stringify({ persona: ['dba', 'operations'] })), ['dba', 'operations'])
  assert.deepEqual(parsePersonas(JSON.stringify({ persona: 'none' })), [])
  assert.deepEqual(parsePersonas(JSON.stringify({})), [])
  assert.deepEqual(parsePersonas('garbage'), [])
})

test('personaSeed takes the first persona with a usable pane, else the generic list', () => {
  const a = twelve().map((b) => ({ ...b, label: 'A' + b.label }))
  const g = twelve().map((b) => ({ ...b, label: 'G' + b.label }))
  const packs = { executive: { pane: a }, broken: { pane: [{ label: '', prompt: 'x' }] }, nopane: {} }
  assert.deepEqual(personaSeed(['executive'], packs, g), a)
  assert.deepEqual(personaSeed(['broken', 'executive'], packs, g), a)
  assert.deepEqual(personaSeed(['nopane'], packs, g), g)
  assert.deepEqual(personaSeed([], packs, g), g)
  assert.deepEqual(personaSeed(['unknown'], packs, g), g)
  assert.deepEqual(personaSeed(['executive'], packs, null), a)
  assert.deepEqual(personaSeed([], {}, null), [])
})

test('the plain-mode personas are the four the installer defaults to yes', () => {
  assert.deepEqual([...PLAIN_PERSONAS].sort(), ['executive', 'leadership', 'product-manager', 'project-manager'])
})

test('moveButton moves one item and keeps the rest in order; swapButtons swaps two', () => {
  const l = ['a', 'b', 'c', 'd'].map((x) => ({ label: x, prompt: x }))
  const names = (xs) => xs.map((x) => x.label).join('')
  assert.equal(names(moveButton(l, 2, 0)), 'cabd')
  assert.equal(names(moveButton(l, 0, 3)), 'bcda')
  assert.equal(names(moveButton(l, 1, 1)), 'abcd')
  assert.equal(names(moveButton(l, 3, 2)), 'abdc')
  assert.equal(names(l), 'abcd', 'the input is not changed')
  assert.throws(() => moveButton(l, 4, 0), /range/)
  assert.throws(() => moveButton(l, 0, -1), /range/)
  assert.equal(names(swapButtons(l, 0, 3)), 'dbca')
  assert.equal(names(swapButtons(l, 1, 1)), 'abcd')
  assert.throws(() => swapButtons(l, 0, 9), /range/)
})

test('fitLabel shortens to the width with an ellipsis, only when it must', () => {
  assert.equal(fitLabel('Overdue', 19), 'Overdue')
  assert.equal(fitLabel('x'.repeat(22), 19), 'x'.repeat(18) + '\u2026')
  assert.equal(fitLabel('abc', 1), '\u2026')
  assert.equal(fitLabel('abc', 0), '')
})

test('arrange: select, move up and down, stop at the ends, done returns the new list', () => {
  const l = ['a', 'b', 'c'].map((x) => ({ label: x, prompt: x }))
  const names = (xs) => xs.map((x) => x.label).join('')
  let s = arrangeStart(l)
  assert.equal(s.selected, null)
  assert.equal(s.dirty, false)
  s = arrangeReduce(s, { type: 'down' })
  assert.equal(names(s.list), 'abc', 'with nothing selected a move does nothing')
  s = arrangeReduce(s, { type: 'select', index: 0 })
  s = arrangeReduce(s, { type: 'up' })
  assert.equal(names(s.list), 'abc', 'cannot move above the top')
  s = arrangeReduce(s, { type: 'down' })
  assert.equal(names(s.list), 'bac')
  assert.equal(s.selected, 1, 'the selection follows the button')
  assert.equal(s.dirty, true)
  s = arrangeReduce(s, { type: 'down' })
  s = arrangeReduce(s, { type: 'down' })
  assert.equal(names(s.list), 'bca', 'cannot move below the bottom')
  s = arrangeReduce(s, { type: 'up' })
  assert.equal(names(s.list), 'bac')
  s = arrangeReduce(s, { type: 'select', index: 1 })
  assert.equal(s.selected, null, 'pressing the selected button again deselects')
  assert.equal(names(l), 'abc', 'the original list is never changed')
})

test('arrange: a mark sets the position directly', () => {
  const l = ['a', 'b', 'c', 'd'].map((x) => ({ label: x, prompt: x }))
  let s = arrangeStart(l)
  s = arrangeReduce(s, { type: 'select', index: 3 })
  s = arrangeReduce(s, { type: 'top' })
  assert.equal(s.list.map((x) => x.label).join(''), 'dabc')
  assert.equal(s.selected, 0)
})

test('bandLine is one short dim line for each state', () => {
  for (const st of [{ open: true }, { open: false }, { open: false, arranging: true }, { problem: 'buttons.json: bad' }]) {
    const t = bandLine(st)
    assert.equal(typeof t, 'string')
    assert.ok(t.length > 0 && t.length <= 90 && !/\n/.test(t), t)
  }
  assert.match(bandLine({ open: false }), /gt-pane/)
  assert.match(bandLine({ problem: 'buttons.json: bad' }), /buttons\.json: bad/)
})

// ---- the allowance footer: read-only figures from the usage module's saved readings ----------
const row = (o) => JSON.stringify({ ts: 1000, five_hour: null, seven_day: null, spend: null, ...o })
const NOW = 1_700_000_000_000   // ms

test('newestReading takes the last line that has a plan figure, skips junk, never throws', () => {
  const text = [row({ ts: 1, five_hour: 5, seven_day: 50 }), 'not json', row({ ts: 2, cache_write: 9 }),
    row({ ts: 3, five_hour: 7, seven_day: 51 }), row({ ts: 4 }), ''].join('\n')
  assert.equal(newestReading(text).ts, 3)
  assert.equal(newestReading(''), null)
  assert.equal(newestReading(undefined), null)
  assert.equal(newestReading(row({ ts: 4 })), null)
  assert.equal(newestReading('{"ts":"x","five_hour":3}'), null, 'a reading without a numeric time is not trusted')
  assert.equal(newestReading(row({ ts: 4, five_hour: 'lots' })), null)
})

test('latestReading parses the usage module\'s latest.json (one reading), and distrusts anything else', () => {
  const ok = JSON.stringify({ ts: 5, five_hour: 4, seven_day: 60 })
  assert.equal(latestReading(ok).seven_day, 60)
  assert.equal(latestReading(JSON.stringify({ ts: 5, five_hour: null, seven_day: 9 })).seven_day, 9)
  assert.equal(latestReading(''), null)
  assert.equal(latestReading(undefined), null)
  assert.equal(latestReading('{not json'), null)
  assert.equal(latestReading('[1,2]'), null)
  assert.equal(latestReading(JSON.stringify({ ts: 'x', five_hour: 3 })), null, 'a reading without a numeric time is not trusted')
  assert.equal(latestReading(JSON.stringify({ ts: 5, cache_write: 1 })), null, 'no plan figure, no reading')
  // a ledger text is not a latest file: callers fall back to newestReading for it
  assert.equal(latestReading(row({ ts: 3, five_hour: 7 }) + '\n' + row({ ts: 4, five_hour: 8 })), null)
})

test('usageFooter formats both figures and the time, fresh', () => {
  const ts = NOW / 1000 - 10 * 60
  const f = usageFooter({ ts, five_hour: 2.4, seven_day: 95.6 }, NOW, 0)
  assert.equal(f.stale, false)
  assert.deepEqual(f.lines, ['5-hour 2%', 'weekly 96%', 'as of ' + new Date(ts * 1000).toISOString().slice(11, 16)])
  assert.equal(f.text, f.lines.join('\n'))
})

test('usageFooter shows only the figures it has and nothing when it has none', () => {
  const ts = NOW / 1000
  assert.match(usageFooter({ ts, five_hour: 10, seven_day: null }, NOW, 0).text, /^5-hour 10%\nas of /)
  assert.match(usageFooter({ ts, five_hour: null, seven_day: 40 }, NOW, 0).text, /^weekly 40%\nas of /)
  assert.equal(usageFooter({ ts, five_hour: null, seven_day: null }, NOW, 0), null)
  assert.equal(usageFooter(null, NOW, 0), null)
})

test('usageFooter: older than one hour is stale, dimmed, and says its age', () => {
  assert.equal(STALE_MS, 60 * 60 * 1000)
  const at = (ageMs) => usageFooter({ ts: (NOW - ageMs) / 1000, five_hour: 3, seven_day: 9 }, NOW, 0)
  assert.equal(at(59 * 60 * 1000).stale, false)
  assert.equal(at(60 * 60 * 1000).stale, false, 'exactly one hour is still fresh')
  const old = at(61 * 60 * 1000)
  assert.equal(old.stale, true)
  assert.match(old.text, /\(1h ago\)$/)
  assert.match(at(5 * 3600 * 1000 + 600000).text, /\(5h ago\)$/)
  assert.match(at(26 * 3600 * 1000).text, /\(1d ago\)$/)
  assert.match(at(3 * 86400 * 1000).text, /\(3d ago\)$/)
})

test('usageFooter: a clock a little ahead is treated as fresh, never negative', () => {
  const f = usageFooter({ ts: NOW / 1000 + 90, five_hour: 1, seven_day: 1 }, NOW, 0)
  assert.equal(f.stale, false)
  assert.doesNotMatch(f.text, /ago|\(-/)
})

test('usageFooter shows the time in the given zone offset', () => {
  const ts = Date.UTC(2026, 9, 9, 17, 5) / 1000
  const now = Date.UTC(2026, 9, 9, 17, 10)
  assert.match(usageFooter({ ts, five_hour: 1, seven_day: 2 }, now, 0).text, /as of 17:05$/)
  assert.match(usageFooter({ ts, five_hour: 1, seven_day: 2 }, now, -300).text, /as of 12:05$/)
})

test('parseStatusSetting: pane_status is on unless it says off', () => {
  assert.equal(parseStatusSetting(JSON.stringify({ pane_status: 'off' })), 'off')
  assert.equal(parseStatusSetting(JSON.stringify({ pane_status: 'on' })), 'on')
  assert.equal(parseStatusSetting(JSON.stringify({})), 'on')
  assert.equal(parseStatusSetting(JSON.stringify({ pane_status: 'maybe' })), 'on')
  assert.equal(parseStatusSetting('garbage'), 'on')
  assert.equal(parseStatusSetting(undefined), 'on')
})

test('footerPlan: with pane_status off nothing is drawn and the usage file is not read', () => {
  assert.deepEqual(footerPlan('off'), { read: false, draw: false })
  assert.deepEqual(footerPlan('on'), { read: true, draw: true })
})

test('the close tip is shown until its state file says it was shown', () => {
  assert.equal(shouldShowTip(''), true)
  assert.equal(shouldShowTip(undefined), true)
  assert.equal(shouldShowTip('{ broken'), true)
  assert.equal(shouldShowTip('{}'), true)
  assert.equal(shouldShowTip(JSON.stringify({ close_tip_shown: 'yes' })), true, 'only true counts')
  assert.equal(shouldShowTip(serializeTipState()), false)
  assert.ok(serializeTipState().endsWith('\n'))
})

test('the close tip says how to turn the panel off, in the owner\'s words', () => {
  assert.equal(CLOSE_TIP,
    "To turn off the panel, make the change in settings using this command: /gt:gt-settings pane_at_start off  (or 'closed' to keep it loaded but closed; /gt-pane-buttons reopens it)")
})

test('sessionLines: context and the share of the turn the cache served', () => {
  assert.deepEqual(sessionLines(9.4, { input_tokens: 10, cache_read_input_tokens: 980, cache_creation_input_tokens: 10, output_tokens: 50 }),
    ['context 9%', 'cache 98%'])
})

test('sessionLines: a figure not known yet is left out, never shown as 0', () => {
  assert.deepEqual(sessionLines(undefined, null), [])
  assert.deepEqual(sessionLines(42, null), ['context 42%'])
  assert.deepEqual(sessionLines(null, { input_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }), [])
})
