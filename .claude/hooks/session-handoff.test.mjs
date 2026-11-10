#!/usr/bin/env node
/**
 * Tests for session-handoff.mjs. Run: node .claude/hooks/session-handoff.test.mjs
 *
 * R7 — the fence for D39. What actually needs fencing is not the happy path
 * (a broken listing is obvious the first time somebody starts a session); it is
 * the FAILURE modes, because every one of them is silent:
 *
 *   * a repo with no HANDOFF.md must start normally — this hook runs in every
 *     session of every checkout, and a crash here is a repo nobody can open;
 *   * a malformed or half-edited file must not throw — it is edited by hand, by
 *     agents, and by merge resolution, so it WILL be malformed sometimes;
 *   * the OPEN/DONE split must hold, or deleted work reappears as live work;
 *   * `[OWNER]` must survive into the injected text, since that word is the
 *     difference between reporting a gate and walking through it.
 */
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const HOOK = path.join(path.dirname(fileURLToPath(import.meta.url)), 'session-handoff.mjs')

/** Run the hook against a throwaway repo root holding `handoff` (or nothing). */
function run(handoff) {
  const root = mkdtempSync(path.join(tmpdir(), 'handoff-'))
  try {
    if (handoff !== null) {
      mkdirSync(path.join(root, 'project-docs'), { recursive: true })
      writeFileSync(path.join(root, 'project-docs', 'HANDOFF.md'), handoff, 'utf-8')
    }
    const r = spawnSync(process.execPath, [HOOK], {
      env: { ...process.env, CLAUDE_PROJECT_DIR: root },
      encoding: 'utf-8',
    })
    const context = r.stdout.trim()
      ? JSON.parse(r.stdout).hookSpecificOutput.additionalContext
      : ''
    return { status: r.status, stdout: r.stdout, context, stderr: r.stderr }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

const entry = (title, extra = '') =>
  `### ${title}\n- **Check:** \`ls\`\n- **Added:** 2026-08-14\n${extra}\n`

const CASES = [
  ['no HANDOFF.md → silent, exit 0', () => {
    const r = run(null)
    return r.status === 0 && r.stdout.trim() === ''
  }],

  ['unreadable/garbage file → does not throw', () => {
    const r = run('%%% not markdown at all %%%')
    return r.status === 0 && !r.stderr.includes('Error')
  }],

  ['no OPEN section → reports empty rather than crashing', () => {
    const r = run('# HANDOFF\n\nprotocol prose only\n')
    return r.status === 0 && /empty/i.test(r.context)
  }],

  ['empty OPEN section → reports empty', () => {
    const r = run('# OPEN\n\n# DONE\n')
    return r.status === 0 && /empty/i.test(r.context)
  }],

  ['one open entry is listed by title', () => {
    const r = run(`# OPEN\n\n${entry('H-1 · do the thing · [AGENT]')}\n# DONE\n`)
    return r.context.includes('H-1 · do the thing')
  }],

  // ⚠️ The protocol says DELETE a finished entry, and the DONE section says so
  // too. If the parser ever read past `# DONE`, work that was deliberately
  // closed would come back as live work every session — the loudest possible
  // version of this file lying.
  ['content below # DONE is NEVER surfaced', () => {
    const r = run(
      `# OPEN\n\n${entry('H-1 · live · [AGENT]')}\n# DONE\n\n${entry('H-99 · buried · [AGENT]')}`
    )
    return r.context.includes('H-1 · live') && !r.context.includes('H-99')
  }],

  // The word that separates "report this gate" from "walk through it".
  ['[OWNER] survives into the injected text, and is counted', () => {
    const r = run(
      `# OPEN\n\n${entry('H-1 · deploy · [OWNER]')}\n${entry('H-2 · build · [AGENT]')}\n# DONE\n`
    )
    return r.context.includes('[OWNER]')
      && /1 agent, 1 owner-gated/.test(r.context)
      && /never do them/i.test(r.context)
  }],

  ['the injected text tells the session to PRUNE, not merely to read', () => {
    const r = run(`# OPEN\n\n${entry('H-1 · x · [AGENT]')}\n# DONE\n`)
    return /delete every entry/i.test(r.context)
  }],

  // Without this, a three-week-old "still pending" reads identically to one
  // written an hour ago, and the old one is usually wrong.
  ['an old entry is flagged with its age', () => {
    const r = run('# OPEN\n\n### H-1 · ancient · [OWNER]\n- **Added:** 2020-01-01\n\n# DONE\n')
    return /⚠️ \d+d old/.test(r.context)
  }],

  ['a fresh entry is NOT flagged', () => {
    const today = new Date().toISOString().slice(0, 10)
    const r = run(`# OPEN\n\n### H-1 · new · [AGENT]\n- **Added:** ${today}\n\n# DONE\n`)
    return !/d old/.test(r.context)
  }],

  ['a missing Added date does not crash or flag', () => {
    const r = run('# OPEN\n\n### H-1 · undated · [AGENT]\n- **Check:** `ls`\n\n# DONE\n')
    return r.status === 0 && r.context.includes('H-1 · undated') && !/d old/.test(r.context)
  }],

  ['output is the SessionStart hook contract', () => {
    const r = run(`# OPEN\n\n${entry('H-1 · x · [AGENT]')}\n# DONE\n`)
    const j = JSON.parse(r.stdout)
    return j.hookSpecificOutput.hookEventName === 'SessionStart'
      && typeof j.hookSpecificOutput.additionalContext === 'string'
  }],

  // Metorite is the state authority (D118). This file must keep saying so in
  // the prompt, or it slowly becomes the thing agents trust for state.
  ['the injected text defers to Metorite for state', () => {
    const r = run(`# OPEN\n\n${entry('H-1 · x · [AGENT]')}\n# DONE\n`)
    return /Metorite holds the state/.test(r.context) && /not state/i.test(r.context)
  }],
]

let failed = 0
for (const [name, fn] of CASES) {
  let ok = false
  try {
    ok = fn()
  } catch (e) {
    ok = false
    console.error(`   ${e.message}`)
  }
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}`)
  if (!ok) failed++
}
console.log(`\n${CASES.length - failed}/${CASES.length} passed`)
process.exit(failed ? 1 : 0)
