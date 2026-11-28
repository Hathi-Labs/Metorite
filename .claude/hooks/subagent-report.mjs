/**
 * subagent-report — show each sub-agent of a lane on the Agents map
 * (SW-114b, task 4136, agent_coordination.md §3.12.10 and §5.12).
 *
 * WHY. A sub-agent of Claude Code makes no claim. Without this hook the map
 * shows no builder, verifier or reviewer under a lane (the map read of
 * 2026-11-27 returned `children: 0` while a lane ran an Opus builder).
 *
 * HOW. Claude Code runs this script on SubagentStart and SubagentStop. The
 * hook input holds `transcript_path` (the parent session), `agent_id` and
 * `agent_type`. The script reads the parent transcript and finds the newest
 * run that the session claimed or started and did not finish or release
 * (`currentRun`). It prints that call for the `report_subagent` tool, and
 * `send` posts it to the MCP endpoint of the session with the bearer of the
 * connection's header helper. The edge writes or ends one child run of
 * SW-39 under that run.
 *
 * ⚠️ NEVER BLOCKS. Each path exits 0: no transcript, no open run, no helper,
 * a refused call, a timeout or a crash. A failure logs one line on stderr.
 * The script never prints a token.
 * OFF SWITCH. Set SUBAGENT_REPORT=off in the session's environment.
 * Fence: `node --test .claude/hooks/subagent-report.test.mjs`.
 */

import fs from 'node:fs'
import { execFileSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'

const MAX_BYTES = 40_000_000
/** The caps and the id shape of the edge (`tools.AGENT_LABEL_MAX`,
 * `tools.SUBAGENT_ID_MAX`, `tools.SUBAGENT_ID_PATTERN`). */
const LABEL_MAX = 40
const AGENT_ID_MAX = 64
const AGENT_ID = /^[A-Za-z0-9._:-]+$/
/** The edge requires `agent_type`. A sub-agent with no type gets this label. */
const FALLBACK_LABEL = 'subagent'
const TIMEOUT_MS = 4000
const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

const suffix = (name, s) => typeof name === 'string' && name.endsWith(`__${s}`)

function textOf(content) {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) return content.map((b) => (b && typeof b.text === 'string' ? b.text : '')).join('')
  return ''
}

function parseJson(s) {
  try {
    return JSON.parse(s)
  } catch {
    return null
  }
}

/** Parse transcript lines (JSONL). A bad line is skipped. */
export function parseTranscript(text) {
  const out = []
  for (const line of String(text).split('\n')) {
    if (!line.trim()) continue
    const e = parseJson(line)
    if (e) out.push(e)
  }
  return out
}

/** The run id of a claim or a start result, or null. */
function runOf(name, body) {
  if (!body || typeof body !== 'object') return null
  if (suffix(name, 'claim_task')) return body.task ? body.agent_run_id || null : null
  if (suffix(name, 'start_agent_run')) return (body.run && body.run.id) || null
  return null
}

/**
 * The newest run of this session that is still open: a successful
 * `claim_task` or `start_agent_run` with no later `finish_agent_run` or
 * `release_task` on that run. A park or a question keeps the run open.
 * Returns the run id, or null.
 */
export function currentRun(entries) {
  const uses = []
  const results = new Map()
  let idx = 0
  for (const e of entries) {
    const c = e && e.message && e.message.content
    if (!Array.isArray(c)) continue
    for (const b of c) {
      if (!b) continue
      if (b.type === 'tool_use') uses.push({ id: b.id, name: b.name, input: b.input || {}, idx: idx++ })
      else if (b.type === 'tool_result') results.set(b.tool_use_id, { isError: !!b.is_error, text: textOf(b.content) })
    }
  }
  const opened = []
  const ended = new Set()
  for (const u of uses) {
    if (suffix(u.name, 'finish_agent_run') || suffix(u.name, 'release_task')) {
      const r = results.get(u.id)
      if (r && !r.isError && typeof u.input.run_id === 'string') ended.add(u.input.run_id)
      continue
    }
    const r = results.get(u.id)
    if (!r || r.isError) continue
    const rid = runOf(u.name, parseJson(r.text))
    if (rid && UUID.test(rid)) opened.push({ rid, idx: u.idx })
  }
  for (let i = opened.length - 1; i >= 0; i--) {
    if (!ended.has(opened[i].rid)) return opened[i].rid
  }
  return null
}

/** Clean one value as the edge does: no control character, a cap. */
function clean(v, max) {
  if (typeof v !== 'string') return null
  // eslint-disable-next-line no-control-regex
  const s = v.replace(/[\u0000-\u001f\u007f-\u009f]/g, '').trim().slice(0, max)
  return s || null
}

/**
 * The `tools/call` arguments of `report_subagent` for one hook input, or
 * `{ skip: reason }`. `readFile` reads the transcript (a test passes a fake).
 */
export function buildCall(input, readFile = (p) => fs.readFileSync(p, 'utf8')) {
  if (!input || typeof input !== 'object') return { skip: 'no input' }
  const ev = input.hook_event_name
  const event = ev === 'SubagentStart' ? 'start' : ev === 'SubagentStop' ? 'stop' : null
  if (!event) return { skip: `not a sub-agent event: ${ev}` }
  // A bad id is skipped, never changed, so a start and its stop always map
  // to the same id or to no call at all.
  const agentId = typeof input.agent_id === 'string' ? input.agent_id : ''
  if (!agentId || agentId.length > AGENT_ID_MAX || !AGENT_ID.test(agentId)) return { skip: 'no usable agent_id' }
  const tp = input.transcript_path
  if (typeof tp !== 'string' || !tp) return { skip: 'no transcript_path' }
  let text
  try {
    const st = fs.statSync(tp)
    if (!st.isFile() || st.size > MAX_BYTES) return { skip: 'transcript too large or not a file' }
    text = readFile(tp)
  } catch {
    return { skip: 'transcript unreadable' }
  }
  const runId = currentRun(parseTranscript(text))
  if (!runId) return { skip: 'no open run in this session' }
  const args = { run_id: runId, agent_id: agentId, event, agent_type: clean(input.agent_type, LABEL_MAX) || FALLBACK_LABEL }
  return { args }
}

/**
 * The MCP entry of the user config (`~/.claude.json`, `mcpServers.metorite`):
 * `{ url, helper }`, or null. Only the user file counts, never a project file
 * or an environment variable, so a checkout cannot point the call at another
 * server or another helper. Only an https URL counts.
 */
export function mcpEntry(readFile = (p) => fs.readFileSync(p, 'utf8'), home = os.homedir()) {
  let cfg
  try {
    cfg = JSON.parse(readFile(path.join(home, '.claude.json')))
  } catch {
    return null
  }
  const e = cfg && cfg.mcpServers && cfg.mcpServers.metorite
  if (!e || typeof e.url !== 'string' || !/^https:\/\//.test(e.url)) return null
  return { url: e.url, helper: helperArgv(e.headersHelper) }
}

/**
 * The argv of a `headersHelper` command line, or null. The box entry of EA-19g
 * is `<helper> <box.env>`, two absolute paths. A line with a shell character,
 * or a first word that is not an absolute path, is refused, because the hook
 * runs it with no shell.
 */
export function helperArgv(line) {
  if (typeof line !== 'string' || /[`$|;&<>()'"\\*?{}\[\]~\n]/.test(line)) return null
  const argv = line.trim().split(/\s+/)
  return argv[0] && path.isAbsolute(argv[0]) ? argv : null
}

/** The Authorization header that the helper prints, or null. Never logged. */
export function bearerFrom(helper, run = (h) => execFileSync(h[0], h.slice(1), { timeout: TIMEOUT_MS, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })) {
  if (!helper) return null
  try {
    const out = JSON.parse(run(helper))
    const v = out && (out.Authorization || out.authorization)
    // The refusal sentinel of EA-19g is not a credential.
    if (typeof v !== 'string' || !/^Bearer \S+$/.test(v) || v.includes('helper-refused')) return null
    return v
  } catch {
    return null
  }
}

/** One JSON-RPC `tools/call` of `report_subagent`. Resolves to a short status. */
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
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'report_subagent', arguments: args } }),
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

async function readStdin() {
  const chunks = []
  for await (const c of process.stdin) chunks.push(c)
  return Buffer.concat(chunks).toString('utf8')
}

/** The hook. Each path ends with exit 0. */
export async function main(env = process.env) {
  if (env.SUBAGENT_REPORT === 'off') return 'skip: off'
  const input = parseJson(await readStdin())
  const built = buildCall(input)
  if (built.skip) return `skip: ${built.skip}`
  const entry = mcpEntry()
  return send(built.args, { entry, bearer: entry && bearerFrom(entry.helper) })
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname)) {
  main()
    .then((s) => {
      if (s !== 'ok') process.stderr.write(`subagent-report: ${s}\n`)
    })
    .catch(() => process.stderr.write('subagent-report: crash\n'))
    .finally(() => process.exit(0))
}
