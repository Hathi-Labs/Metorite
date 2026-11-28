// Fence of SW-114b (task 4136): node --test .claude/hooks/subagent-report.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { buildCall, currentRun, parseTranscript } from './subagent-report.mjs'

const R1 = '11111111-1111-4111-8111-111111111111'
const R2 = '22222222-2222-4222-8222-222222222222'
const T1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
let n = 0
const use = (name, input) => {
  const id = `toolu_${++n}`
  return [{ message: { content: [{ type: 'tool_use', id, name, input }] } }, id]
}
const result = (id, body, isError = false) => ({
  message: { content: [{ type: 'tool_result', tool_use_id: id, is_error: isError, content: [{ type: 'text', text: typeof body === 'string' ? body : JSON.stringify(body) }] }] },
})
const claim = (rid, task = T1) => {
  const [u, id] = use('mcp__metorite__claim_task', { task_id: task })
  return [u, result(id, { task: { id: task }, agent_run_id: rid })]
}
const start = (rid, task = T1) => {
  const [u, id] = use('mcp__metorite__start_agent_run', { task_id: task })
  return [u, result(id, { run: { id: rid }, created: true })]
}
const endRun = (tool, rid, isError = false) => {
  const [u, id] = use(`mcp__metorite__${tool}`, { run_id: rid })
  return [u, result(id, { ok: true }, isError)]
}

test('a claim gives its run', () => {
  assert.equal(currentRun(claim(R1)), R1)
})

test('a start gives its run', () => {
  assert.equal(currentRun(start(R2)), R2)
})

test('the newest open run wins', () => {
  assert.equal(currentRun([...claim(R1), ...claim(R2)]), R2)
})

test('a finished or released run is not open, and the older open run is', () => {
  assert.equal(currentRun([...claim(R1), ...claim(R2), ...endRun('finish_agent_run', R2)]), R1)
  assert.equal(currentRun([...claim(R1), ...endRun('release_task', R1)]), null)
})

test('a refused finish keeps the run open', () => {
  assert.equal(currentRun([...claim(R1), ...endRun('finish_agent_run', R1, true)]), R1)
})

test('a 409 claim, an error result and a claim with no task give no run', () => {
  const [u1, id1] = use('mcp__metorite__claim_task', { task_id: T1 })
  const [u2, id2] = use('mcp__metorite__claim_task', { task_id: T1 })
  assert.equal(currentRun([u1, result(id1, 'Error: 409 task_held'), u2, result(id2, { agent_run_id: R1 })]), null)
  const [u3, id3] = use('mcp__metorite__claim_task', { task_id: T1 })
  assert.equal(currentRun([u3, result(id3, { task: { id: T1 }, agent_run_id: R1 }, true)]), null)
})

test('a run id of a bad shape is ignored', () => {
  assert.equal(currentRun(claim('not-a-uuid')), null)
})

test('a park and a question keep the run open', () => {
  const [p, pid] = use('mcp__metorite__post_agent_activity', { run_id: R1, waiting: { on: 'ci' } })
  const [q, qid] = use('mcp__metorite__ask_on_task', { task_id: T1 })
  assert.equal(currentRun([...claim(R1), p, result(pid, {}), q, result(qid, {})]), R1)
})

test('parseTranscript skips a bad line', () => {
  assert.equal(parseTranscript('{"a":1}\nnot json\n\n{"b":2}').length, 2)
})

function withTranscript(entries, fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'subrep-'))
  const p = path.join(dir, 't.jsonl')
  fs.writeFileSync(p, entries.map((e) => JSON.stringify(e)).join('\n'))
  try {
    return fn(p)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

test('buildCall maps a start and a stop', () => {
  withTranscript(claim(R1), (p) => {
    const s = buildCall({ hook_event_name: 'SubagentStart', transcript_path: p, agent_id: 'ag-1', agent_type: 'ws-verifier' })
    assert.deepEqual(s, { args: { run_id: R1, agent_id: 'ag-1', event: 'start', agent_type: 'ws-verifier' } })
    const e = buildCall({ hook_event_name: 'SubagentStop', transcript_path: p, agent_id: 'ag-1', agent_type: 'ws-verifier' })
    assert.equal(e.args.event, 'stop')
  })
})

test('buildCall cleans the label and caps it at 60', () => {
  withTranscript(claim(R1), (p) => {
    const s = buildCall({ hook_event_name: 'SubagentStart', transcript_path: p, agent_id: 'ag\u0007-1', agent_type: `x\n${'y'.repeat(80)}` })
    assert.equal(s.args.agent_id, 'ag-1')
    assert.equal(s.args.agent_type.length, 60)
    assert.ok(!/[\n\u0007]/.test(s.args.agent_type))
  })
})

test('buildCall skips, and never throws, on each bad input', () => {
  assert.ok(buildCall(null).skip)
  assert.ok(buildCall({ hook_event_name: 'Stop' }).skip)
  assert.ok(buildCall({ hook_event_name: 'SubagentStart', transcript_path: '/x' }).skip)
  assert.ok(buildCall({ hook_event_name: 'SubagentStart', agent_id: 'a' }).skip)
  assert.ok(buildCall({ hook_event_name: 'SubagentStart', agent_id: 'a', transcript_path: '/no/such/file' }).skip)
  withTranscript([], (p) => {
    assert.match(buildCall({ hook_event_name: 'SubagentStart', agent_id: 'a', transcript_path: p }).skip, /no open run/)
  })
})

import { bearerFrom, helperArgv, mcpEntry, send } from './subagent-report.mjs'

test('mcpEntry reads only an https entry, and an absolute helper', () => {
  const cfg = (e) => () => JSON.stringify({ mcpServers: { metorite: e } })
  assert.deepEqual(mcpEntry(cfg({ url: 'https://mcp.example/mcp', headersHelper: '/h' }), '/home'), { url: 'https://mcp.example/mcp', helper: ['/h'] })
  assert.equal(mcpEntry(cfg({ url: 'http://evil/mcp' }), '/home'), null)
  assert.equal(mcpEntry(cfg({ url: 'https://x/mcp', headersHelper: 'rel/h' }), '/home').helper, null)
  assert.equal(mcpEntry(() => 'not json', '/home'), null)
  assert.equal(mcpEntry(() => { throw new Error('x') }, '/home'), null)
})

test('bearerFrom takes a bearer and refuses the sentinel, junk and a crash', () => {
  assert.equal(bearerFrom('/h', () => '{"Authorization":"Bearer mbt_abc"}'), 'Bearer mbt_abc')
  assert.equal(bearerFrom('/h', () => '{"Authorization":"Bearer box-token-helper-refused"}'), null)
  assert.equal(bearerFrom('/h', () => 'nope'), null)
  assert.equal(bearerFrom('/h', () => { throw new Error('timeout') }), null)
  assert.equal(bearerFrom(null), null)
})

test('send posts one tools/call of report_subagent and maps the answer', async () => {
  let seen
  const fake = (body, status = 200) => async (url, opts) => {
    seen = { url, opts }
    return { ok: status < 300, status, json: async () => body }
  }
  const args = { run_id: R1, agent_id: 'a', event: 'start' }
  const entry = { url: 'https://mcp.example/mcp' }
  assert.equal(await send(args, { entry, bearer: 'Bearer t', fetchImpl: fake({ result: { structuredContent: {} } }) }), 'ok')
  const msg = JSON.parse(seen.opts.body)
  assert.equal(msg.method, 'tools/call')
  assert.equal(msg.params.name, 'report_subagent')
  assert.deepEqual(msg.params.arguments, args)
  assert.equal(seen.opts.headers.authorization, 'Bearer t')
  assert.equal(await send(args, { entry, bearer: 'Bearer t', fetchImpl: fake({}, 401) }), 'fail: HTTP 401')
  assert.equal(await send(args, { entry, bearer: 'Bearer t', fetchImpl: fake({ result: { isError: true } }) }), 'fail: tool error')
  assert.equal(await send(args, { entry, bearer: 'Bearer t', fetchImpl: async () => { throw new Error('down') } }), 'fail: network')
  assert.match(await send(args, { entry: null, bearer: null }), /^skip/)
})

test('the script exits 0 and prints no token, also on junk input', async () => {
  const { spawnSync } = await import('node:child_process')
  const r = spawnSync(process.execPath, [new URL('./subagent-report.mjs', import.meta.url).pathname], { input: 'junk', encoding: 'utf8', timeout: 10000, env: { ...process.env, HOME: os.tmpdir() } })
  assert.equal(r.status, 0)
  assert.equal(r.stdout, '')
  assert.ok(!/Bearer/.test(r.stderr))
})

test('helperArgv splits the box entry and refuses a shell line', () => {
  assert.deepEqual(helperArgv('/b/metorite-box-headers /c/box.env'), ['/b/metorite-box-headers', '/c/box.env'])
  assert.equal(helperArgv('helper /c/box.env'), null)
  assert.equal(helperArgv('/b/h; rm -rf /'), null)
  assert.equal(helperArgv('/b/h $(id)'), null)
  assert.equal(helperArgv('/b/h | nc x 1'), null)
  assert.equal(helperArgv(42), null)
})
