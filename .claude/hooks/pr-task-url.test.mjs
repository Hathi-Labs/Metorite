#!/usr/bin/env node
/**
 * Tests for pr-task-url.mjs. Run: node .claude/hooks/pr-task-url.test.mjs
 *
 * R7 — the fence for CLAUDE.md §4 step 4 (SW-19c). The cases fence the two paths
 * of the task (a `gh pr create` with no task URL gets the reminder, and one with
 * the URL does not) and the failure modes that would make the hook worse than
 * none: it must never stop a pull request by mistake, and it must never touch a
 * permission decision.
 */
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const HOOK = path.join(path.dirname(fileURLToPath(import.meta.url)), 'pr-task-url.mjs')
const URL_OK = 'https://app.metorite.com/projects?task=1651fc66-a21b-4776-8556-df5dbd6664de'

const dir = mkdtempSync(path.join(tmpdir(), 'prurl-'))
writeFileSync(path.join(dir, 'with.md'), `Task: ${URL_OK}\n\nbody\n`)
writeFileSync(path.join(dir, 'without.md'), 'Task: none\n\nbody\n')

function run(command, { env = {}, stdin } = {}) {
  const input = stdin ?? JSON.stringify({ tool_name: 'Bash', cwd: dir, tool_input: { command } })
  const e = { ...process.env, ...env }
  if (!('TASK_URL_BLOCK' in env)) delete e.TASK_URL_BLOCK
  const r = spawnSync(process.execPath, [HOOK], { input, env: e, encoding: 'utf-8' })
  let context = ''
  try {
    context = r.stdout.trim() ? JSON.parse(r.stdout).hookSpecificOutput.additionalContext : ''
  } catch {
    context = 'UNPARSEABLE'
  }
  return { status: r.status, stdout: r.stdout, stderr: r.stderr, context }
}

const warned = (r) => r.status === 0 && /no Metorite task URL/.test(r.context) && r.stderr === ''
const silent = (r) => r.status === 0 && r.stdout === '' && r.stderr === ''

const CASES = [
  ['a command that is not gh pr create is silent', () => silent(run('git status'))],
  ['gh pr view is silent', () => silent(run('gh pr view 12'))],
  ['gh pr create-like names do not match', () => silent(run('gh pr created'))],

  ['no task URL in --body → the reminder, and the call goes on', () => {
    const r = run('gh pr create --title "x" --body "does a thing"')
    return warned(r) && /search_tasks/.test(r.context) && /create_task/.test(r.context)
  }],
  ['--fill with no body → the reminder', () => warned(run('gh pr create --fill'))],
  ['the task URL in --body → silent', () => silent(run(`gh pr create --title x --body "Task: ${URL_OK}"`))],
  ['the task URL in a heredoc body → silent', () => silent(run(
    `gh pr create --title x --body "$(cat <<'EOF'\nTask: ${URL_OK}\nEOF\n)"`))],
  ['gh pr create after a separator is found', () => warned(run('git push -u origin b && gh pr create --title x --body y'))],

  ['--body-file with the URL → silent', () => silent(run('gh pr create --body-file with.md'))],
  ['--body-file without the URL → the reminder', () => warned(run('gh pr create --body-file without.md'))],
  ['-F and a quoted path work', () => warned(run('gh pr create -F "without.md"'))],
  ['--body-file with a set variable is expanded', () => silent(
    run('gh pr create --body-file "$PRDIR/with.md"', { env: { PRDIR: dir } }))],

  // The hook cannot prove an absence it cannot see.
  ['a body file that cannot be read → silent, never a false alarm', () => silent(run('gh pr create --body-file nope.md'))],
  ['--body-file - (stdin) → silent', () => silent(run('gh pr create --body-file -'))],
  ['an unset variable in the path → silent', () => silent(run('gh pr create --body-file "$NOT_SET_ANYWHERE/x.md"'))],

  ['a URL of another site is not a task URL', () => warned(run(
    'gh pr create --body "see https://example.com/projects?task=1651fc66-a21b-4776-8556-df5dbd6664de"'))],

  ['TASK_URL_BLOCK=1 and no URL → exit 2, the reminder on stderr, no stdout', () => {
    const r = run('gh pr create --body "x"', { env: { TASK_URL_BLOCK: '1' } })
    return r.status === 2 && /BLOCKED by pr-task-url/.test(r.stderr) && /task URL/.test(r.stderr) && r.stdout === ''
  }],
  ['TASK_URL_BLOCK=1 with the URL → allowed', () =>
    silent(run(`gh pr create --body "${URL_OK}"`, { env: { TASK_URL_BLOCK: '1' } }))],
  ['TASK_URL_BLOCK=1 on a command that is not gh pr create → allowed', () =>
    silent(run('ls', { env: { TASK_URL_BLOCK: '1' } }))],

  // A hook that sets `permissionDecision` would skip the user's permission prompt
  // for gh pr create. It must only add context.
  ['the output never sets a permission decision', () => {
    const r = run('gh pr create --body "x"')
    const out = JSON.parse(r.stdout).hookSpecificOutput
    return out.hookEventName === 'PreToolUse' && !('permissionDecision' in out) && !('decision' in JSON.parse(r.stdout))
  }],

  ['garbage on stdin → silent exit 0', () => silent(run('', { stdin: '%%% not json' }))],
  ['empty stdin → silent exit 0', () => silent(run('', { stdin: '' }))],
  ['a tool call with no command → silent exit 0', () => silent(run('', { stdin: '{"tool_name":"Read","tool_input":{"file_path":"x"}}' }))],
]

let failed = 0
for (const [name, fn] of CASES) {
  let ok = false
  try {
    ok = fn()
  } catch (e) {
    console.error(`   ${e.message}`)
  }
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}`)
  if (!ok) failed++
}
rmSync(dir, { recursive: true, force: true })
console.log(`\n${CASES.length - failed}/${CASES.length} passed`)
process.exit(failed ? 1 : 0)
