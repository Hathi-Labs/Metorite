#!/usr/bin/env node
/**
 * pr-task-url — PreToolUse hook on `gh pr create`.
 *
 * SW-19c (task 871). CLAUDE.md §4 "Work under a Metorite task" step 4 says: put
 * the task URL in the pull request body. A rule with no fence erodes (R7), and
 * the owner sees agents skip it. This hook is the fence.
 *
 * It looks for `app.metorite.com/projects?task=` in the body of a `gh pr create`
 * call: in the command text itself (`--body`, a heredoc), or in the file named by
 * `--body-file` / `-F`. With none, it says what to do and how to find the task.
 *
 *   build mode (default)  the call goes on, and the reminder reaches the agent
 *                         as `additionalContext`. No `permissionDecision` is
 *                         set, so this hook never changes a permission prompt.
 *   TASK_URL_BLOCK=1      the call is refused: exit 2 and the reminder on stderr.
 *                         A `harden` pull request sets it, as it sets
 *                         STE_LINT_BLOCK=1 for the STE lint.
 *
 * Fails OPEN and silent, unlike plan-guard: a crash, a bad input, or a body that
 * the hook cannot read (a pipe, an unset variable) never stops a pull request.
 * The hook cannot prove an absence it cannot see, so it does not claim one.
 * Contract: PreToolUse hook, JSON on stdin. Fence: pr-task-url.test.mjs.
 *
 * ⚠️ The entry in `.claude/settings.json` that runs this file needs the
 * `guard-write` grant (CLAUDE.md §3a). Until the owner adds it, the hook does
 * not run. The line is in the pull request that added this file.
 */

import { readFileSync, statSync } from 'node:fs'
import path from 'node:path'

const TASK_URL = /app\.metorite\.com\/projects\?task=[0-9a-f-]{8,}/i
// `gh pr create` as a COMMAND: at the start of the text, or of a segment (after
// `;`, `&`, `|`, a newline or a bracket), with optional VAR=value words before it.
// It runs on the command with quoted text and heredocs blanked (see `blank`), so
// a commit message or an echo that only names the command does not match.
const PR_CREATE = /(?:^|[;&|(\n])\s*(?:\w+=\S*\s+)*gh\s+pr\s+create(?=\s|$)/
const BODY_FLAG = /(?:^|\s)(?:--body-file|-F)(?:=|\s+)/
const BODY_PATH = /^(?:"([^"]+)"|'([^']+)'|(\S+))/
// Calls that open nothing: help, and the browser form.
const NO_PR = /(?:^|\s)(?:--help|-h|--web|-w)(?=\s|$)/
const MAX_BODY_BYTES = 1_000_000
// A command this long is not a call to read. The blanking below is quadratic on
// a heredoc with no end line, so the hook skips it and stays silent.
const MAX_COMMAND_CHARS = 64_000

const REMINDER =
  'This pull request has no Metorite task URL in its body (CLAUDE.md §4, step 4). ' +
  'Put `https://app.metorite.com/projects?task=<task id>` in the body: `get_task` returns it as `url`. ' +
  'To find the task, call `my_work` or `search_tasks`. For work with no task, call `create_task` first, ' +
  'then `claim_task`. Then add the pull request with `add_task_reference`.'

function done() {
  process.exit(0)
}

/**
 * The command with the text of quotes and heredocs blanked, and the same length,
 * so an index in one is an index in the other.
 */
function blank(command) {
  const fill = (m) => m[0] + ' '.repeat(Math.max(0, m.length - 2)) + m[m.length - 1]
  return command
    .replace(/<<-?\s*(['"]?)(\w+)\1[^\n]*\n[\s\S]*?\n\s*\2(?=\s|$)/g, (m) => ' '.repeat(m.length))
    .replace(/"(?:\\.|[^"\\])*"|'[^']*'/g, fill)
}

/** Replace $NAME and ${NAME} that are set. An unset one stays, and the read fails. */
function expand(p) {
  return p.replace(/\$\{(\w+)\}|\$(\w+)/g, (m, a, b) => process.env[a || b] ?? m)
}

/** The text of the body file: a string, or null when the hook cannot read it. */
function readBodyFile(command, cwd) {
  // The flag is found with quoted text blanked, so a `-F` inside --body text is
  // not a flag. The path is read from the real text, because it may be quoted.
  const flag = BODY_FLAG.exec(blank(command))
  if (!flag) return undefined // no body file named
  const m = command.slice(flag.index + flag[0].length).match(BODY_PATH)
  if (!m) return null
  const named = expand(m[1] || m[2] || m[3] || '')
  if (!named || named === '-' || /\$/.test(named)) return null
  // A pipe, a device or a huge file must never stall the call: only a plain,
  // small file is read. /dev/stdin is the hook's own input, already read.
  if (/^\/(?:dev|proc)\//.test(named)) return null
  try {
    const file = path.resolve(cwd || process.cwd(), named)
    const st = statSync(file)
    if (!st.isFile() || st.size > MAX_BODY_BYTES) return null
    return readFileSync(file, 'utf-8')
  } catch {
    return null
  }
}

let raw = ''
try {
  raw = readFileSync(0, 'utf-8')
} catch {
  done()
}

let input
try {
  input = JSON.parse(raw)
} catch {
  done()
}

const command = input?.tool_input?.command
if (typeof command === 'string' && command.length > MAX_COMMAND_CHARS) done()
const blanked = typeof command === 'string' ? blank(command) : ''
const found = PR_CREATE.exec(blanked)
if (!found) done()

try {
  // The one call: from `gh` to the next separator. Its flags are read from the
  // real text, because a body-file path may be quoted.
  const start = found.index + found[0].search(/gh\s+pr\s+create/)
  const stop = blanked.slice(start).search(/[;&|\n]/)
  const call = command.slice(start, stop === -1 ? undefined : start + stop)
  if (NO_PR.test(blank(call))) done()
  if (TASK_URL.test(command)) done()
  const file = readBodyFile(call, input.cwd)
  if (file === null) done() // named, and unreadable: the hook cannot see the body
  if (typeof file === 'string' && TASK_URL.test(file)) done()
} catch {
  done()
}

if (process.env.TASK_URL_BLOCK === '1') {
  process.stderr.write(`BLOCKED by pr-task-url: ${REMINDER}\n`)
  process.exit(2)
}

process.stdout.write(
  JSON.stringify({
    hookSpecificOutput: { hookEventName: 'PreToolUse', additionalContext: REMINDER },
  }),
)
process.exit(0)
