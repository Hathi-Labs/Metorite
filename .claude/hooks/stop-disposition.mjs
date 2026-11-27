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
 * PRs (LBB-4, task 4042): STOP_DISPOSITION_PRS=warn|block checks open PRs.
 * Off by default. See engineering_practice.md section 7b.4.
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

const PR_URL = /github\.com\/[^\s/"]+\/[^\s/"]+\/pull\/(\d+)/
const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const inputText = (v) => (typeof v === 'string' ? v : v == null ? '' : JSON.stringify(v))

/**
 * LBB-1 (PR 2898, `claim_gate.py`) adds the `landing` field on
 * `add_task_reference`. Until that PR is on main, no session can send it, so
 * disposition 3 stays off. Flip this to true in the PR that finds
 * `claim_gate.py` on main. The test covers both values (LBB-4, task 4042).
 */
export const LANDING_ENABLED = false

/**
 * LBB-4 (engineering_practice.md section 7b.4): the PRs of this session that
 * have no later disposition. Returns `[{ pr, branch }]`. It reads `tool_use`
 * blocks and the `tool_result` blocks that answer them. It never reads an exit
 * code. It orders events by the `tool_use` index, as `openClaims` does.
 */
export function openPrs(entries, opts = {}) {
  const landingOn = opts.landing ?? LANDING_ENABLED
  const uses = []
  const results = new Map()
  let idx = 0
  for (const e of entries) {
    const c = e && e.message && e.message.content
    if (!Array.isArray(c)) continue
    for (const b of c) {
      if (!b) continue
      if (b.type === 'tool_use') uses.push({ name: b.name, input: b.input || {}, idx: idx++, id: b.id })
      else if (b.type === 'tool_result') results.set(b.tool_use_id, { isError: !!b.is_error, text: textOf(b.content) })
    }
  }
  const okResult = (u) => {
    const r = results.get(u.id)
    return r && !r.isError ? r.text : null
  }
  const bash = uses
    .filter((u) => u.name === 'Bash' && typeof u.input.command === 'string')
    .map((u) => ({ ...u, cmd: u.input.command, out: okResult(u) }))

  const prs = new Map() // N -> { first, branch }
  const seen = (n, at, branch) => {
    const p = prs.get(n) || { first: at, branch: null }
    if (at < p.first) p.first = at
    if (branch && !p.branch) p.branch = branch
    prs.set(n, p)
  }
  for (const u of bash) {
    if (/\bgh\s+pr\s+create\b/.test(u.cmd) && u.out !== null) {
      const m = PR_URL.exec(u.out)
      if (m) {
        const h = /(?:--head|-H)[\s=]+["']?([^\s"']+)/.exec(u.cmd)
        seen(m[1], u.idx, h && h[1])
      }
    }
  }
  for (const u of uses) {
    if (!suffix(u.name, 'add_task_reference')) continue
    const m = PR_URL.exec(inputText(u.input.url))
    const r = results.get(u.id)
    if (m && !(r && r.isError)) seen(m[1], u.idx, null)
  }
  for (const u of bash) {
    const m = /\bgh\s+pr\s+view\s+(\d+)/.exec(u.cmd)
    if (!m || u.out === null || !prs.has(m[1])) continue
    const h = /"headRefName"\s*:\s*"([^"]+)"/.exec(u.out) || /headRefName:\s*(\S+)/.exec(u.out)
    if (h) seen(m[1], prs.get(m[1]).first, h[1])
  }

  const open = []
  for (const [n, p] of prs) {
    let from = p.first
    if (p.branch) {
      const re = new RegExp(`(?:^|[^\\w./-])${esc(p.branch)}(?![\\w./-])`)
      for (const u of bash) {
        if (u.out === null || !/\bgit\s+push\b/.test(u.cmd)) continue
        if (re.test(u.cmd) || re.test(u.out)) from = Math.max(from, u.idx)
      }
    }
    const wordN = new RegExp(`(?:^|\\D)${n}(?!\\d)`)
    let done = false
    for (const u of bash) {
      if (u.idx <= from || u.out === null) continue
      if (new RegExp(`hand_over\\.sh\\s+${n}\\b`).test(u.cmd) && new RegExp(`handed over: PR ${n} \\(task`).test(u.out)) done = true
      else if (u.cmd.includes('REQUEUE') && u.cmd.includes('merge-queue.tsv') && wordN.test(u.cmd)) done = true
      else if (
        new RegExp(`\\bgh\\s+pr\\s+view\\s+${n}\\b`).test(u.cmd) &&
        /(?:"state"\s*:\s*"|\bstate:?\s*|^\s*)(?:MERGED|CLOSED)\b/im.test(u.out)
      ) done = true
      if (done) break
    }
    for (const u of done ? [] : uses) {
      if (u.idx <= from) continue
      if (landingOn && suffix(u.name, 'add_task_reference')) {
        const m = PR_URL.exec(inputText(u.input.url))
        if (m && m[1] === n && ['queued', 'parked', 'gone'].includes(u.input.landing)) done = true
      } else if (suffix(u.name, 'release_task')) {
        const h = u.input.handoff
        const next = h && typeof h === 'object' ? inputText(h.next) : inputText(h)
        if (new RegExp(`\\bPR\\s*#?${n}\\b`).test(next) && /\b(?:lane|pc)-[A-Za-z0-9-]+/.test(next)) done = true
      }
      if (done) break
    }
    if (!done) open.push({ pr: n, branch: p.branch })
  }
  return open
}

export function prMessage(open) {
  const list = open.map((p) => `  PR ${p.pr}${p.branch ? `  (branch ${p.branch})` : ''}`).join('\n')
  return (
    `You opened or pushed ${open.length} PR(s) and gave no disposition:\n${list}\n` +
    'End each one with one of these two choices:\n' +
    '  - queue it with a live merger: run hand_over.sh, or append a REQUEUE line to ~/lanes/merge-queue.tsv\n' +
    '  - hand it to a named landing lane: put "PR N" and the lane name in the next field of the release_task handoff\n' +
    'A push to the branch after a disposition brings the PR back. Do this now, then end the turn.\n' +
    'To turn this check off: STOP_DISPOSITION_PRS=off'
  )
}

/**
 * One check. Returns `{ block, warn }`, each a message or null. Never throws.
 * `STOP_DISPOSITION_PRS`: off or unset checks no PR, `warn` reports and goes
 * on, `block` blocks once.
 */
export function checkDisposition(input, env = process.env) {
  const none = { block: null, warn: null }
  try {
    if (input.stop_hook_active) return none
    if (env.STOP_DISPOSITION === 'off') return none // the kill switch
    const marker = env.STOP_DISPOSITION_ENV || 'CLAUDE_JOB_DIR'
    if (!env[marker]) return none
    const file = input.transcript_path
    if (!file || fs.statSync(file).size > MAX_BYTES) return none
    const entries = parseTranscript(fs.readFileSync(file, 'utf8'))
    const open = openClaims(entries)
    let block = open.length ? message(open) : null
    let warn = null
    const mode = env.STOP_DISPOSITION_PRS
    if (mode === 'warn' || mode === 'block') {
      const prs = openPrs(entries)
      if (prs.length) {
        if (mode === 'block') block = block ? `${block}\n${prMessage(prs)}` : prMessage(prs)
        else warn = prMessage(prs)
      }
    }
    return { block, warn }
  } catch {
    return none
  }
}
