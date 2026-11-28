// Fence of scripts/report_job.mjs (SW-116, task 4206). Run:
//   node --test scripts/report_job.test.mjs
// tests/unit/test_report_job_script.py runs it in the unit suite.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

import { KINDS, STATES, main, parseArgs, send } from './report_job.mjs'

const RUN = '6a0c6c1e-6f8e-4a51-9a52-2f3b1b2e9d10'
const LINK = 'https://github.com/acme/repo/actions/runs/42'
const ENTRY = { url: 'https://mcp.example.test/mcp', helper: ['/bin/true'] }
const BEARER = 'Bearer abc123'

function fakeFetch(answer = { ok: true, json: async () => ({ result: { isError: false } }) }) {
  const calls = []
  const f = async (url, init) => {
    calls.push({ url, init })
    return answer
  }
  f.calls = calls
  return f
}

const fakeHook = () => async () => ({ mcpEntry: () => ENTRY, bearerFrom: () => BEARER })

test('the kinds and the states are the lists of the edge', () => {
  assert.deepEqual(KINDS, ['github_action', 'ci_local', 'merge_queue', 'release', 'review'])
  assert.deepEqual(STATES, ['queued', 'running', 'passed', 'failed'])
})

test('a good line builds the arguments of report_job', () => {
  const { args } = parseArgs(['--run', RUN, 'github_action', 'run-42', 'running', 'pr-check', LINK])
  assert.deepEqual(args, { run_id: RUN, job_id: 'run-42', kind: 'github_action', name: 'pr-check', state: 'running', link: LINK })
  const noLink = parseArgs(['ci_local', 'ci-1', 'passed', 'local CI', '--run', RUN.toUpperCase()])
  assert.deepEqual(noLink.args, { run_id: RUN, job_id: 'ci-1', kind: 'ci_local', name: 'local CI', state: 'passed' })
})

test('a bad line is skipped, never changed', () => {
  const bad = [
    ['github_action', 'run-42', 'running', 'pr-check'],
    ['--run', 'not-a-run', 'github_action', 'run-42', 'running', 'pr-check'],
    ['--run', RUN, 'deploy', 'run-42', 'running', 'pr-check'],
    ['--run', RUN, 'github_action', 'a b', 'running', 'pr-check'],
    ['--run', RUN, 'github_action', 'x'.repeat(65), 'running', 'pr-check'],
    ['--run', RUN, 'github_action', 'run-42', 'done', 'pr-check'],
    ['--run', RUN, 'github_action', 'run-42', 'running', '\u0007'],
    ['--run', RUN, 'github_action', 'run-42', 'running', 'pr-check', 'http://x.test/a'],
    ['--run', RUN, 'github_action', 'run-42', 'running', 'pr-check', 'https://a b'],
  ]
  for (const argv of bad) assert.ok(parseArgs(argv).skip, argv.join(' '))
})

test('the name is cleaned and capped at 40', () => {
  const { args } = parseArgs(['--run', RUN, 'release', 'r1', 'queued', `  ${'n'.repeat(50)}\n`])
  assert.equal(args.name, 'n'.repeat(40))
})

test('send posts one tools/call of report_job with the bearer', async () => {
  const f = fakeFetch()
  const s = await send({ run_id: RUN }, { entry: ENTRY, bearer: BEARER, fetchImpl: f })
  assert.equal(s, 'ok')
  assert.equal(f.calls.length, 1)
  assert.equal(f.calls[0].url, ENTRY.url)
  assert.equal(f.calls[0].init.headers.authorization, BEARER)
  const body = JSON.parse(f.calls[0].init.body)
  assert.equal(body.method, 'tools/call')
  assert.equal(body.params.name, 'report_job')
  assert.deepEqual(body.params.arguments, { run_id: RUN })
})

test('send reports each failure and never throws', async () => {
  assert.match(await send({}, { entry: null, bearer: BEARER }), /^skip/)
  assert.match(await send({}, { entry: ENTRY, bearer: null }), /^skip/)
  assert.equal(await send({}, { entry: ENTRY, bearer: BEARER, fetchImpl: fakeFetch({ ok: false, status: 401 }) }), 'fail: HTTP 401')
  const toolError = fakeFetch({ ok: true, json: async () => ({ result: { isError: true } }) })
  assert.equal(await send({}, { entry: ENTRY, bearer: BEARER, fetchImpl: toolError }), 'fail: tool error')
  const down = async () => {
    throw new TypeError('fetch failed')
  }
  assert.equal(await send({}, { entry: ENTRY, bearer: BEARER, fetchImpl: down }), 'fail: network')
})

test('main sends the call, and skips with no hook, no run or the off switch', async () => {
  const f = fakeFetch()
  const argv = ['--run', RUN, 'merge_queue', 'pr-2969', 'queued', 'merge queue']
  assert.equal(await main(argv, {}, { hook: fakeHook(), fetchImpl: f }), 'ok')
  assert.equal(JSON.parse(f.calls[0].init.body).params.arguments.kind, 'merge_queue')
  assert.equal(await main(argv, { REPORT_JOB: 'off' }, { hook: fakeHook(), fetchImpl: f }), 'skip: off')
  assert.match(await main(argv, {}, { hook: async () => null, fetchImpl: f }), /^skip: no hook module/)
  assert.match(await main(argv.slice(2), {}, { hook: fakeHook(), fetchImpl: f }), /^skip: no --run/)
  assert.equal(f.calls.length, 1)
})

test('the script exits 0 on a bad line and on a crash path', () => {
  const script = fileURLToPath(new URL('./report_job.mjs', import.meta.url))
  for (const argv of [[], ['--run', RUN, 'deploy', 'x', 'running', 'n']]) {
    // execFileSync throws on a non-zero exit, so a return is the proof.
    execFileSync(process.execPath, [script, ...argv], { stdio: ['ignore', 'ignore', 'pipe'], env: { ...process.env, HOME: '/nonexistent' } })
  }
})
