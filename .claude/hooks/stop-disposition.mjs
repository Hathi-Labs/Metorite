/**
 * stop-disposition — a background turn must not end with a claimed run and no
 * disposition (SW-61, task 1903, agent_coordination.md §3.10.11).
 *
 * WHY. Board audit of task 1900: sessions ended with a claimed task and no
 * word to anyone. A question that reached only the terminal reached nobody.
 *
 * HOW. `stop-children.mjs` calls `checkDisposition` on each Stop, so no new
 * entry in `.claude/settings.json` is needed. The check reads the transcript
 * (`transcript_path`). It needs no token and calls no MCP. It matches tool
 * names by suffix (`claim_task`). A claim counts only when its result is not
 * an error, so a 409 never blocks. A disposition for a claim is one of:
 *   finish_agent_run, release_task, ask_on_task, set_task_status (any call
 *   on the task, the hook has no token to read the status), or a `waiting`
 *   post on `ci` or with `wake_on` (a park, SW-60 and SW-66).
 *
 * BACKGROUND ONLY. The marker is the env var CLAUDE_JOB_DIR, which Claude
 * Code sets in a background job. STOP_DISPOSITION_ENV names another variable.
 * OFF SWITCH. Set STOP_DISPOSITION=off in the session's environment.
 *
 * ⚠️ FAILS OPEN. No transcript, a parse error or a crash all pass. The hook
 * blocks once: `stop_hook_active` true passes.
 * Fence: `node .claude/hooks/stop-disposition.test.mjs`.
 */

import fs from 'node:fs'

const MAX_BYTES = 40_000_000

export const DISPOSITIONS = [
  'review or Done (set_task_status, then finish_agent_run)',
  'a question with ask_on_task',
  'a park on CI (post_agent_activity with waiting)',
  'release_task, with a handoff',
]

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
  for (const line of text.split('\n')) {
    if (!line.trim()) continue
    const e = parseJson(line)
    if (e) out.push(e)
  }
  return out
}

/** True when a `waiting` post is a park: on ci, or with a wake condition. */
function isPark(input) {
  const w = input && input.waiting
  if (!w || typeof w !== 'object') return false
  return w.on === 'ci' || (Array.isArray(input.wake_on) && input.wake_on.length > 0)
}

/**
 * The claims that have no later disposition. Returns `[{ task_id, run_id }]`.
 */
export function openClaims(entries) {
  const uses = new Map() // tool_use id -> { name, input, idx }
  const results = new Map() // tool_use id -> { isError, text }
  let idx = 0
  for (const e of entries) {
    const c = e && e.message && e.message.content
    if (!Array.isArray(c)) continue
    for (const b of c) {
      if (!b) continue
      if (b.type === 'tool_use') uses.set(b.id, { name: b.name, input: b.input || {}, idx: idx++ })
      else if (b.type === 'tool_result') results.set(b.tool_use_id, { isError: !!b.is_error, text: textOf(b.content), idx: idx++ })
    }
  }
  const claims = []
  for (const [id, u] of uses) {
    if (!suffix(u.name, 'claim_task')) continue
    const r = results.get(id)
    if (!r || r.isError) continue
    const body = parseJson(r.text)
    // A 409 and any refusal come back as text with no task object.
    if (!body || !body.task) continue
    claims.push({ task_id: u.input.task_id, run_id: body.agent_run_id || null, idx: u.idx })
  }
  const open = []
  for (const cl of claims) {
    let done = false
    for (const u of uses.values()) {
      if (u.idx <= cl.idx) continue
      const sameTask = u.input.task_id && u.input.task_id === cl.task_id
      const sameRun = cl.run_id && u.input.run_id === cl.run_id
      if (suffix(u.name, 'finish_agent_run') && (sameRun || !u.input.run_id)) done = true
      else if (suffix(u.name, 'release_task') && (sameRun || !u.input.run_id)) done = true
      else if (suffix(u.name, 'ask_on_task') && sameTask) done = true
      else if (suffix(u.name, 'set_task_status') && sameTask) done = true
      else if (suffix(u.name, 'post_agent_activity') && sameRun && isPark(u.input)) done = true
      if (done) break
    }
    if (!done) open.push({ task_id: cl.task_id, run_id: cl.run_id })
  }
  return open
}

export function message(open) {
  const list = open.map((c) => `  task ${c.task_id}${c.run_id ? `  run ${c.run_id}` : ''}`).join('\n')
  return (
    `You claimed ${open.length} task(s) and gave no disposition:\n${list}\n` +
    'End each one with exactly one of these:\n' +
    DISPOSITIONS.map((d) => `  - ${d}`).join('\n') +
    '\nA question for the owner goes through ask_on_task. A question that only reaches the terminal reaches nobody.' +
    ' A comment is evidence, not a disposition. Do this now, then end the turn.'
  )
}

/** One check. Returns null to pass, or the block message. Never throws. */
export function checkDisposition(input, env = process.env) {
  try {
    if (input.stop_hook_active) return null
    if (env.STOP_DISPOSITION === 'off') return null // the kill switch
    const marker = env.STOP_DISPOSITION_ENV || 'CLAUDE_JOB_DIR'
    if (!env[marker]) return null
    const file = input.transcript_path
    if (!file || fs.statSync(file).size > MAX_BYTES) return null
    const open = openClaims(parseTranscript(fs.readFileSync(file, 'utf8')))
    return open.length ? message(open) : null
  } catch {
    return null
  }
}
