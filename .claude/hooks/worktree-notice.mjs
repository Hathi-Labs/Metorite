#!/usr/bin/env node
/**
 * worktree-notice — say, at session start, whether this session has the
 * working tree to itself.
 *
 * WHY. Owner directive 2026-09-03: every development session starts in its own
 * git worktree, so sessions can run in parallel. Two sessions in ONE checkout
 * fight over one working tree — one switches branch under the other, `git
 * status` mixes their edits, and a `reset --hard` by either destroys the
 * other's uncommitted work. None of that announces itself; it looks like the
 * other session being wrong.
 *
 * This hook cannot change the session's directory, and it does not try. It
 * reports which checkout it is in and names the one command that fixes it.
 * The agent decides, with the fact in front of it rather than three tool calls
 * away.
 *
 * ⚠️ FAILS OPEN AND SILENT. A notice is not a gate. `plan-guard.mjs` is the
 * layer that refuses things; this one only informs, so a crash here must never
 * cost a session. Every path below ends in exit 0.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'

const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || process.cwd()

function git(...args) {
  try {
    return execFileSync('git', args, {
      encoding: 'utf8',
      cwd: PROJECT_DIR,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch {
    return null
  }
}

try {
  // `--git-common-dir` is the SHARED .git for every worktree; `--git-dir` is
  // this checkout's own. In the primary checkout they resolve to the same
  // place. In a worktree they do not. That difference is the whole test, and
  // it needs no path convention to hold.
  const gitDir = git('rev-parse', '--absolute-git-dir')
  const commonDir = git('rev-parse', '--path-format=absolute', '--git-common-dir')
  const branch = git('rev-parse', '--abbrev-ref', 'HEAD')

  if (!gitDir || !commonDir) process.exit(0) // not a repo, or an old git

  const same = path.resolve(gitDir).toLowerCase() === path.resolve(commonDir).toLowerCase()

  if (!same) {
    console.log(
      `## Worktree: yes — this session is isolated\n\n` +
        `Branch \`${branch}\`, at \`${PROJECT_DIR}\`. Another session can work in ` +
        `parallel without touching your files.`,
    )
    process.exit(0)
  }

  const count = (git('worktree', 'list') || '').split('\n').filter(Boolean).length

  console.log(
    `## ⚠️ You are in the PRIMARY checkout, not a worktree\n\n` +
      `Owner directive 2026-09-03: **a development session starts in its own ` +
      `git worktree**, so sessions run in parallel. Here, a second session ` +
      `switches the branch under you and a \`reset --hard\` by either destroys ` +
      `the other's uncommitted work.\n\n` +
      `Before you start work:\n\n` +
      '```\n' +
      `bash scripts/worktree.sh new <slug>\n` +
      '```\n\n' +
      `Then \`cd\` into the path it prints. ${count} worktrees exist now — ` +
      `\`bash scripts/worktree.sh list\` shows them.\n\n` +
      `⚠️ Two exceptions, and they are the common case for a SHORT session: ` +
      `reading or answering a question changes nothing and needs no worktree, ` +
      `and neither does a one-file fix the owner is watching. Use judgement — ` +
      `this is a directive about parallel WORK, not a toll on every session.`,
  )
} catch {
  // Deliberately silent. See the header.
}

process.exit(0)
