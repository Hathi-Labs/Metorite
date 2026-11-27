// Fence for stop-disposition.mjs (SW-61, task 1903). pr-check.yml runs it.
// Run: node .claude/hooks/stop-disposition.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { openClaims, openPrs, checkDisposition, parseTranscript, message, prMessage, LANDING_ENABLED } from './stop-disposition.mjs'
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
  const m = checkDisposition({ transcript_path: f }, env).block
  assert.ok(m)
  for (const w of ['set_task_status', 'ask_on_task', 'park on CI', 'release_task', 'never only', 'terminal']) assert.ok(m.includes(w) || /question/.test(m), w)
  assert.equal(checkDisposition({ transcript_path: f, stop_hook_active: true }, env).block, null)
})
test('an interactive session passes', () => {
  assert.equal(checkDisposition({ transcript_path: transcript(claim()) }, {}).block, null)
})
test('fails open: missing file, junk file', () => {
  assert.equal(checkDisposition({ transcript_path: '/nope' }, { CLAUDE_JOB_DIR: '/x' }).block, null)
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sd-')), 'j')
  fs.writeFileSync(f, 'not json\n{')
  assert.equal(checkDisposition({ transcript_path: f }, { CLAUDE_JOB_DIR: '/x' }).block, null)
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
  assert.equal(checkDisposition({ transcript_path: f }, { CLAUDE_JOB_DIR: '/x', STOP_DISPOSITION: 'off' }).block, null)
})

// ---- LBB-4 (task 4042): open PRs at a stop ----
const bash = (command, out, isErr = false) => {
  const [u, id] = use('Bash', { command })
  return [u, res(id, out, isErr)]
}
const URL_ = (n) => `https://github.com/acme/repo/pull/${n}`
const create = (n, branch = 'feat-x') => bash(`gh pr create --head ${branch} --title t`, URL_(n))
const push = (branch = 'feat-x') => bash(`git push origin ${branch}`, `To github.com\n * [new branch] ${branch} -> ${branch}`)
const view = (n, out) => bash(`gh pr view ${n} --json state,headRefName`, out)
const bareRelease = () => call('release_task', { run_id: R, handoff: { next: 'released for other agents' } })
const prs = (es, o) => openPrs(es, o).map((p) => p.pr)
const ENV = { CLAUDE_JOB_DIR: '/x' }

test('LBB-4 item 1: PRS unset or off checks nothing, block stays null', () => {
  const f = transcript([...create(7), ...bareRelease()])
  assert.equal(checkDisposition({ transcript_path: f }, ENV).block, null)
  assert.equal(checkDisposition({ transcript_path: f }, { ...ENV, STOP_DISPOSITION_PRS: 'off' }).block, null)
  assert.equal(checkDisposition({ transcript_path: f }, ENV).warn, null)
})
test('LBB-4 item 2: block names PR N after gh pr create and a bare release_task', () => {
  const f = transcript([...create(7), ...bareRelease()])
  const env = { ...ENV, STOP_DISPOSITION_PRS: 'block' }
  const { block } = checkDisposition({ transcript_path: f }, env)
  assert.match(block, /PR 7\b/)
  assert.match(block, /hand_over\.sh/)
  assert.ok(block.endsWith('To turn this check off: STOP_DISPOSITION_PRS=off'))
  assert.equal(checkDisposition({ transcript_path: f, stop_hook_active: true }, env).block, null)
  assert.equal(checkDisposition({ transcript_path: f }, { ...env, STOP_DISPOSITION: 'off' }).block, null)
  assert.equal(checkDisposition({ transcript_path: f }, { STOP_DISPOSITION_PRS: 'block' }).block, null)
})
test('LBB-4: a PR from add_task_reference counts, an error result does not', () => {
  assert.deepEqual(prs(call('add_task_reference', { task_id: T, url: URL_(9) })), ['9'])
  const [u, id] = bash('gh pr create', 'boom', true)
  assert.deepEqual(prs([u, res(id, 'x', true)]), [])
  assert.deepEqual(prs(bash('gh pr create', 'no url here')), [])
})
test('LBB-4 item 3: each disposition clears PR N', () => {
  const base = create(7)
  const cases = {
    'hand_over': bash('bash scripts/hand_over.sh 7 4042 STANDARD note', 'handed over: PR 7 (task 4042) at abc, tier STANDARD'),
    'REQUEUE': bash("printf 'REQUEUE\\t7\\t4042\\tlane-x\\tn\\n' >> ~/lanes/merge-queue.tsv", ''),
    'release with lane': call('release_task', { run_id: R, handoff: { next: 'PR 7 goes to lane-merge-1' } }),
    'release with pc lane': call('release_task', { run_id: R, handoff: { next: 'land PR 7 via pc-land-core' } }),
    'MERGED': view(7, '{"state":"MERGED","headRefName":"feat-x"}'),
    'CLOSED': view(7, '{"state":"CLOSED"}'),
  }
  for (const [k, d] of Object.entries(cases)) assert.deepEqual(prs([...base, ...d]), [], k)
  // the landing disposition: counts only when enabled
  const landing = call('add_task_reference', { task_id: T, url: URL_(7), landing: 'queued' })
  assert.deepEqual(prs([...base, ...landing], { landing: false }), ['7'])
  assert.deepEqual(prs([...base, ...landing], { landing: true }), [])
  for (const l of ['parked', 'gone']) assert.deepEqual(prs([...base, ...call('add_task_reference', { task_id: T, url: URL_(7), landing: l })], { landing: true }), [])
  assert.deepEqual(prs([...base, ...call('add_task_reference', { task_id: T, url: URL_(7), landing: 'other' })], { landing: true }), ['7'])
  assert.equal(LANDING_ENABLED, false) // PR 2898 is not on main
})
test('LBB-4 item 3: things that are not a disposition', () => {
  const base = create(7)
  assert.deepEqual(prs([...base, ...bash('bash hand_over.sh 7 4042 STANDARD n', 'failed', true)]), ['7'])
  assert.deepEqual(prs([...base, ...call('release_task', { run_id: R, handoff: { next: 'PR 7 later' } })]), ['7'])
  assert.deepEqual(prs([...base, ...call('release_task', { run_id: R, handoff: { next: 'PR 8 to lane-x' } })]), ['7'])
  assert.deepEqual(prs([...base, ...view(7, '{"state":"OPEN"}')]), ['7'])
  assert.deepEqual(prs([...base, ...bash("printf 'REQUEUE' >> merge-queue.tsv # 70", '')]), ['7'])
})
test('LBB-4 item 3: a push to the head branch after the disposition brings N back', () => {
  const done = bash('bash hand_over.sh 7 4042 STANDARD n', 'handed over: PR 7 (task 4042) at abc')
  assert.deepEqual(prs([...create(7), ...done]), [])
  assert.deepEqual(prs([...create(7), ...done, ...push()]), ['7'])
  assert.deepEqual(prs([...create(7), ...done, ...push('other-branch')]), [])
  assert.deepEqual(prs([...create(7), ...done, ...push(), ...done]), [])
  assert.deepEqual(prs([...create(7), ...done, ...bash('git push origin feat-x', 'rejected', true)]), [])
  // the branch from gh pr view when the PR came from add_task_reference
  const ref = call('add_task_reference', { task_id: T, url: URL_(8) })
  assert.deepEqual(prs([...ref, ...view(8, '{"state":"OPEN","headRefName":"br8"}'), ...done.slice(0, 0), ...bash('hand_over.sh 8 1 T n', 'handed over: PR 8 (task 1)'), ...push('br8')]), ['8'])
  // with no known branch a push proves nothing
  assert.deepEqual(prs([...ref, ...bash('hand_over.sh 8 1 T n', 'handed over: PR 8 (task 1)'), ...push('br8')]), [])
})
test('LBB-4 item 4: warn prints to stderr and main returns code 0', () => {
  const f = transcript([...create(7), ...bareRelease()])
  const keep = { j: process.env.CLAUDE_JOB_DIR, p: process.env.STOP_DISPOSITION_PRS }
  process.env.CLAUDE_JOB_DIR = '/x'
  process.env.STOP_DISPOSITION_PRS = 'warn'
  try {
    const w = checkDisposition({ transcript_path: f }, process.env)
    assert.equal(w.block, null)
    assert.match(w.warn, /PR 7\b/)
    const r = main(JSON.stringify({ transcript_path: f }), '1 0 9 init\n')
    assert.equal(r.code, 0)
    assert.match(r.message, /PR 7\b/)
    process.env.STOP_DISPOSITION_PRS = 'block'
    const b = main(JSON.stringify({ transcript_path: f }), '1 0 9 init\n')
    assert.equal(b.code, 2)
    assert.match(b.message, /PR 7\b/)
  } finally {
    for (const [k, v] of [['CLAUDE_JOB_DIR', keep.j], ['STOP_DISPOSITION_PRS', keep.p]]) {
      if (v === undefined) delete process.env[k]
      else process.env[k] = v
    }
  }
})
test('LBB-4: fails open on an unparsable transcript, and prMessage lists each PR', () => {
  const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sd-')), 'j')
  fs.writeFileSync(f, 'junk\n{')
  assert.deepEqual(checkDisposition({ transcript_path: f }, { ...ENV, STOP_DISPOSITION_PRS: 'block' }), { block: null, warn: null })
  const m = prMessage([{ pr: '1', branch: 'a' }, { pr: '2', branch: null }])
  assert.match(m, /PR 1[\s\S]*PR 2/)
})
