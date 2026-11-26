// Fence for stop-disposition.mjs (SW-61, task 1903). pr-check.yml runs it.
// Run: node .claude/hooks/stop-disposition.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { openClaims, checkDisposition, parseTranscript, message } from './stop-disposition.mjs'
import { main } from './stop-children.mjs'

const T = 'aaaaaaaa-0000-0000-0000-000000000001'
const R = 'bbbbbbbb-0000-0000-0000-000000000002'
let n = 0
const use = (name, input) => {
  const id = `tu${++n}`
  return [{ type: 'assistant', message: { content: [{ type: 'tool_use', id, name, input }] } }, id]
}
const res = (id, content, is_error = false) => ({ type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: id, content, is_error }] } })
function claim(ok = true) {
  const [u, id] = use('mcp__metorite__claim_task', { task_id: T })
  return [u, res(id, ok ? JSON.stringify({ task: { id: T }, agent_run_id: R }) : 'task_held: another session holds it', !ok)]
}
const call = (name, input) => [use(`mcp__metorite__${name}`, input)[0]]

test('a claim with no disposition is open', () => {
  assert.deepEqual(openClaims(claim()), [{ task_id: T, run_id: R }])
})
test('a 409 claim, no claim, and a text refusal pass', () => {
  assert.equal(openClaims(claim(false)).length, 0)
  assert.equal(openClaims([]).length, 0)
  const [u, id] = use('mcp__metorite__claim_task', { task_id: T })
  assert.equal(openClaims([u, res(id, 'refused')]).length, 0)
})
test('each disposition closes the claim', () => {
  for (const [name, input] of [
    ['finish_agent_run', { run_id: R }],
    ['release_task', { run_id: R }],
    ['ask_on_task', { task_id: T }],
    ['set_task_status', { task_id: T }],
    ['post_agent_activity', { run_id: R, type: 'thought', waiting: { on: 'ci', until_minutes: 30 } }],
    ['post_agent_activity', { run_id: R, type: 'thought', waiting: { on: 'agent', until_minutes: 30 }, wake_on: [{ kind: 'pr_merged' }] }],
  ]) assert.equal(openClaims([...claim(), ...call(name, input)]).length, 0, name)
})
test('a plain thought, a comment or another task does not close it', () => {
  assert.equal(openClaims([...claim(), ...call('post_agent_activity', { run_id: R, type: 'thought' })]).length, 1)
  assert.equal(openClaims([...claim(), ...call('add_comment', { task_id: T })]).length, 1)
  assert.equal(openClaims([...claim(), ...call('set_task_status', { task_id: 'other' })]).length, 1)
  assert.equal(openClaims([...claim(), ...call('release_task', { run_id: 'other-run' })]).length, 1)
})
test('a disposition before the claim does not count', () => {
  assert.equal(openClaims([...call('release_task', { run_id: R }), ...claim()]).length, 1)
})

function transcript(entries) {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sd-')), 't.jsonl')
  fs.writeFileSync(f, entries.map((e) => JSON.stringify(e)).join('\n') + '\n')
  return f
}
test('blocks once in a background session, then passes', () => {
  const f = transcript(claim())
  const env = { CLAUDE_JOB_DIR: '/x' }
  const m = checkDisposition({ transcript_path: f }, env)
  assert.ok(m)
  for (const w of ['set_task_status', 'ask_on_task', 'park on CI', 'release_task', 'never only', 'terminal']) assert.ok(m.includes(w) || /question/.test(m), w)
  assert.equal(checkDisposition({ transcript_path: f, stop_hook_active: true }, env), null)
})
test('an interactive session passes', () => {
  assert.equal(checkDisposition({ transcript_path: transcript(claim()) }, {}), null)
})
test('fails open: missing file, junk file', () => {
  assert.equal(checkDisposition({ transcript_path: '/nope' }, { CLAUDE_JOB_DIR: '/x' }), null)
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sd-')), 'j')
  fs.writeFileSync(f, 'not json\n{')
  assert.equal(checkDisposition({ transcript_path: f }, { CLAUDE_JOB_DIR: '/x' }), null)
  assert.deepEqual(parseTranscript('x\n'), [])
})
test('stop-children main blocks with exit 2 through the disposition check', () => {
  const f = transcript(claim())
  const old = process.env.CLAUDE_JOB_DIR
  process.env.CLAUDE_JOB_DIR = '/x'
  try {
    const r = main(JSON.stringify({ transcript_path: f }), '')
    assert.equal(r.code, 2)
    assert.match(r.message, /ask_on_task/)
    assert.equal(main(JSON.stringify({ transcript_path: f, stop_hook_active: true }), '').code, 0)
  } finally {
    if (old === undefined) delete process.env.CLAUDE_JOB_DIR
    else process.env.CLAUDE_JOB_DIR = old
  }
})
test('message names the four dispositions', () => {
  const m = message([{ task_id: T, run_id: R }])
  for (const w of ['set_task_status', 'ask_on_task', 'waiting', 'release_task']) assert.ok(m.includes(w), w)
})
test('STOP_DISPOSITION=off turns the check off', () => {
  const f = transcript(claim())
  assert.equal(checkDisposition({ transcript_path: f }, { CLAUDE_JOB_DIR: '/x', STOP_DISPOSITION: 'off' }), null)
})
