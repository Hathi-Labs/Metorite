#!/usr/bin/env node
/**
 * stop-children — before a session ends its turn, list the child processes it
 * still has that are older than 30 minutes (CIV-16, task 1828).
 *
 * WHY. On 2026-11-06 four wait loops (`until ... ; do sleep 5; done`) ran for
 * 10 to 12 hours on the box, and a test run hung for 10 h. The sessions that
 * started them had ended. The agent never saw what it left behind.
 *
 * It REPORTS and never kills. The first Stop with old children exits 2, so the
 * list reaches the agent, which then stops the jobs it does not need or names
 * the ones that stay. `stop_hook_active` is true on the second Stop, and the
 * hook then exits 0, so it can never loop.
 *
 * ⚠️ FAILS OPEN AND SILENT. No `ps` (Windows), a parse error or a crash all
 * end in exit 0. Fence: `node .claude/hooks/stop-children.test.mjs`.
 */

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'

export const MIN_AGE_SECONDS = 30 * 60

/** Parse `ps -eo pid=,ppid=,etimes=,args=` into rows. */
export function parsePs(text) {
  const rows = []
  for (const line of text.split('\n')) {
    const m = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/)
    if (m) rows.push({ pid: +m[1], ppid: +m[2], age: +m[3], cmd: m[4] })
  }
  return rows
}

/** The descendants of `root` that are older than `minAge`, oldest first. */
export function oldChildren(rows, root, minAge = MIN_AGE_SECONDS, self = process.pid) {
  const kids = new Map()
  for (const r of rows) {
    if (!kids.has(r.ppid)) kids.set(r.ppid, [])
    kids.get(r.ppid).push(r)
  }
  const out = []
  const seen = new Set([root])
  const stack = [root]
  while (stack.length) {
    for (const c of kids.get(stack.pop()) || []) {
      if (seen.has(c.pid)) continue
      seen.add(c.pid)
      stack.push(c.pid)
      if (c.pid !== self && c.age >= minAge) out.push(c)
    }
  }
  return out.sort((a, b) => b.age - a.age)
}

export function report(list) {
  const lines = list.slice(0, 12).map((p) => {
    const hours = (p.age / 3600).toFixed(1)
    return `  pid ${p.pid}  ${hours} h  ${p.cmd.slice(0, 100)}`
  })
  return (
    `You still have ${list.length} child process(es) older than 30 minutes:\n` +
    lines.join('\n') +
    '\nStop each one that the next step does not need (TaskStop, or kill the pid you started).' +
    ' In your final message, name the ones that stay, and say why.'
  )
}

function main() {
  let input = {}
  try {
    input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}')
  } catch {}
  if (input.stop_hook_active) return 0
  const text = execFileSync('ps', ['-eo', 'pid=,ppid=,etimes=,args='], { encoding: 'utf8', timeout: 5000 })
  const list = oldChildren(parsePs(text), process.ppid)
  if (!list.length) return 0
  process.stderr.write(report(list) + '\n')
  return 2
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    process.exit(main())
  } catch {
    process.exit(0)
  }
}
