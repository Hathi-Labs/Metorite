/**
 * report_job — show an external job of a lane on the Agents map
 * (SW-116, task 4206, agent_coordination.md §3.12.11).
 *
 * WHY. A lane waits on jobs that Metorite cannot see: a GitHub Actions run,
 * local CI, the merge queue, a release, a review. The map shows each job
 * under the run of the lane when the lane reports it.
 *
 * USAGE.
 *   node scripts/report_job.mjs --run <run_id> <kind> <job_id> <state> <name> [link]
 *   kind:  github_action | ci_local | merge_queue | release | review
 *   state: queued | running | passed | failed
 *
 * WHY --run IS REQUIRED. The hook of SW-114b finds the run of a session from
 * the transcript that Claude Code gives a hook. A plain script that a lane
 * calls from its shell gets no transcript path, and the newest transcript on
 * the disk can belong to another session on the same box. So the caller
 * passes the run id that `claim_task` or `start_agent_run` gave it.
 *
 * HOW. It reuses `mcpEntry` and `bearerFrom` of the SW-114b hook
 * (`.claude/hooks/subagent-report.mjs`): the MCP entry of the user config
 * and the bearer of its header helper. It posts one `tools/call` of
 * `report_job`. A lane reports each change of state, never each poll.
 *
 * ⚠️ NEVER BLOCKS. Each path exits 0: a bad argument, no hook module, no MCP
 * entry, a refused call, a timeout or a crash. A failure logs one line on
 * stderr. The script never prints a token.
 * OFF SWITCH. Set REPORT_JOB=off in the environment.
 * Fence: `node --test scripts/report_job.test.mjs`, run by
 * `tests/unit/test_report_job_script.py`.
 */

import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** Copies of the edge (`tools.JOB_KINDS`, `JOB_STATES`, `JOB_ID_MAX`,
 * `JOB_ID_PATTERN`, `AGENT_LABEL_MAX`, `JOB_LINK_MAX`). */
export const KINDS = ['github_action', 'ci_local', 'merge_queue', 'release', 'review']
export const STATES = ['queued', 'running', 'passed', 'failed']
const JOB_ID_MAX = 64
const JOB_ID = /^[A-Za-z0-9._:-]+$/
const NAME_MAX = 40
const LINK_MAX = 2048
const TIMEOUT_MS = 4000
const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/
const HOOK = new URL('../.claude/hooks/subagent-report.mjs', import.meta.url)

/** Clean one value as the edge does: no control character, a cap. */
function clean(v, max) {
  if (typeof v !== 'string') return null
  // eslint-disable-next-line no-control-regex
  const s = v.replace(/[\u0000-\u001f\u007f-\u009f]/g, '').trim().slice(0, max)
  return s || null
}

/**
 * The `tools/call` arguments of `report_job` for one command line, or
 * `{ skip: reason }`. A bad value is skipped, never changed, so two reports
 * of one job always name the same row or make no call.
 */
export function parseArgs(argv) {
  const rest = []
  let run = null
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--run') {
      run = argv[i + 1] ?? null
      i++
    } else rest.push(argv[i])
  }
  if (typeof run !== 'string' || !UUID.test(run)) return { skip: 'no --run <run_id>' }
  const [kind, jobId, state, rawName, rawLink] = rest
  if (!KINDS.includes(kind)) return { skip: `unknown kind: ${kind}` }
  if (typeof jobId !== 'string' || !jobId || jobId.length > JOB_ID_MAX || !JOB_ID.test(jobId)) {
    return { skip: 'no usable job_id' }
  }
  if (!STATES.includes(state)) return { skip: `unknown state: ${state}` }
  const name = clean(rawName, NAME_MAX)
  if (!name) return { skip: 'no name' }
  const args = { run_id: run.toLowerCase(), job_id: jobId, kind, name, state }
  if (rawLink !== undefined && rawLink !== '') {
    if (typeof rawLink !== 'string' || rawLink.length > LINK_MAX || !/^https:\/\/\S+$/.test(rawLink)) {
      return { skip: 'the link is not an https link' }
    }
    args.link = rawLink
  }
  return { args }
}

/** One JSON-RPC `tools/call` of `report_job`. Resolves to a short status. */
export async function send(args, { entry, bearer, fetchImpl = fetch } = {}) {
  if (!entry || !bearer) return 'skip: no MCP entry with a header helper (an OAuth session cannot report)'
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), TIMEOUT_MS)
  try {
    const r = await fetchImpl(entry.url, {
      method: 'POST',
      signal: ac.signal,
      headers: {
        authorization: bearer,
        'content-type': 'application/json',
        accept: 'application/json, text/event-stream',
        'mcp-protocol-version': '2025-11-25',
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'report_job', arguments: args } }),
    })
    if (!r.ok) return `fail: HTTP ${r.status}`
    const body = await r.json().catch(() => null)
    if (!body || body.error) return `fail: rpc ${body && body.error ? body.error.code : 'no body'}`
    if (body.result && body.result.isError) return 'fail: tool error'
    return 'ok'
  } catch (e) {
    return `fail: ${e && e.name === 'AbortError' ? 'timeout' : 'network'}`
  } finally {
    clearTimeout(timer)
  }
}

/** The `mcpEntry` and `bearerFrom` of the SW-114b hook, or null. */
async function loadHook() {
  try {
    const m = await import(HOOK.href)
    return typeof m.mcpEntry === 'function' && typeof m.bearerFrom === 'function' ? m : null
  } catch {
    return null
  }
}

/** The script. Each path resolves to a status, and the caller exits 0. */
export async function main(argv = process.argv.slice(2), env = process.env, { hook = loadHook, fetchImpl } = {}) {
  if (env.REPORT_JOB === 'off') return 'skip: off'
  const built = parseArgs(argv)
  if (built.skip) return `skip: ${built.skip}`
  const h = await hook()
  if (!h) return 'skip: no hook module (.claude/hooks/subagent-report.mjs of SW-114b)'
  const entry = h.mcpEntry()
  return send(built.args, { entry, bearer: entry && h.bearerFrom(entry.helper), fetchImpl })
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main()
    .then((s) => {
      if (s !== 'ok') process.stderr.write(`report_job: ${s}\n`)
    })
    .catch(() => process.stderr.write('report_job: crash\n'))
    .finally(() => process.exit(0))
}
