#!/usr/bin/env node
/**
 * worktree-notice — say, at session start, whether this session has the
 * working tree to itself, and whether the rules it loaded are current.
 *
 * WHY. Owner directive 2026-09-03: every development session starts in its own
 * git worktree, so sessions can run in parallel. Two sessions in ONE checkout
 * fight over one working tree — one switches branch under the other, `git
 * status` mixes their edits, and a `reset --hard` by either destroys the
 * other's uncommitted work. None of that announces itself; it looks like the
 * other session being wrong.
 *
 * STALE RULES. A session loads CLAUDE.md and `.claude/` from its checkout. On
 * 2026-11-03 the primary checkout sat 258 commits behind `origin/main`, so the
 * owner's sessions loaded rules from before D135 (work under a Metorite task)
 * and D145 (preview, approve, gate, merge), and nothing said so. This hook now
 * names the gap and the one command that closes it. It reads the local
 * `origin/main` ref and never fetches, so it stays fast and works offline.
 *
 * This hook cannot change the session's directory, and it does not try. It
 * reports which checkout it is in and names the one command that fixes it.
 * The agent decides, with the fact in front of it rather than three tool calls
 * away.
 *
 * ⚠️ FAILS OPEN AND SILENT. A notice is not a gate. `plan-guard.mjs` is the
 * layer that refuses things; this one only informs, so a crash here must never
 * cost a session. Every path below ends in exit 0.
 *
 * Test: `node .claude/hooks/worktree-notice.test.mjs`.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || process.cwd()

/** The files whose content IS the rules a session loads. */
export const RULE_PATHS = ['CLAUDE.md', 'AGENTS.md', '.claude/commands', '.claude/agents', '.claude/hooks']

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

/**
 * The stale-rules notice, or '' when the rules are current.
 * @param {number} behind commits that origin/main has and HEAD lacks
 * @param {string[]} changed rule files that differ between HEAD and origin/main
 * @param {number} ahead commits that HEAD has and origin/main lacks
 */
export function staleRulesNotice(behind, changed, ahead) {
  if (!(behind > 0) || !changed || changed.length === 0) return ''
  const shown = changed.slice(0, 6).map((f) => `\`${f}\``).join(', ')
  const more = changed.length > 6 ? `, and ${changed.length - 6} more` : ''
  const fix =
    ahead > 0
      ? `This branch has ${ahead} commits of its own, so merge \`origin/main\` into it ` +
        `when its work allows.`
      : `This branch has no commits of its own. When \`git status\` shows no ` +
        `tracked change, run \`git merge --ff-only origin/main\` here, then start ` +
        `a new session.`
  return (
    `## ⚠️ The rules this session loaded are ${behind} commits behind origin/main\n\n` +
    `These rule files differ from \`origin/main\` (as of the last fetch): ${shown}${more}. ` +
    `Root \`CLAUDE.md\` on main is the rule of record. Until you update, read it ` +
    `with \`git show origin/main:CLAUDE.md\`.\n\n` +
    `${fix}`
  )
}

function main() {
  // `--git-common-dir` is the SHARED .git for every worktree; `--git-dir` is
  // this checkout's own. In the primary checkout they resolve to the same
  // place. In a worktree they do not. That difference is the whole test, and
  // it needs no path convention to hold.
  const gitDir = git('rev-parse', '--absolute-git-dir')
  const commonDir = git('rev-parse', '--path-format=absolute', '--git-common-dir')
  const branch = git('rev-parse', '--abbrev-ref', 'HEAD')

  if (!gitDir || !commonDir) return // not a repo, or an old git

  // One call gives both counts: "<ahead>\t<behind>". The diff runs only when
  // the checkout is behind, so a current checkout pays for one git call.
  const [ahead = 0, behind = 0] = (git('rev-list', '--left-right', '--count', 'HEAD...origin/main') || '')
    .split(/\s+/)
    .map(Number)
  const changed =
    behind > 0
      ? // Three dots: only what main changed since this branch left it, never
        // this branch's own edits to the rules.
        (git('diff', '--name-only', 'HEAD...origin/main', '--', ...RULE_PATHS) || '')
          .split('\n')
          .filter(Boolean)
      : []
  const stale = staleRulesNotice(behind, changed, ahead)
  if (stale) console.log(stale + '\n')

  const same = path.resolve(gitDir).toLowerCase() === path.resolve(commonDir).toLowerCase()

  if (!same) {
    console.log(
      `## Worktree: yes — this session is isolated\n\n` +
        `Branch \`${branch}\`, at \`${PROJECT_DIR}\`. Another session can work in ` +
        `parallel without touching your files.`,
    )
    return
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
}

const isMain = Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)

if (isMain) {
  try {
    main()
  } catch {
    // Deliberately silent. See the header.
  }
  process.exit(0)
}
