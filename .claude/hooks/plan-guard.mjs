#!/usr/bin/env node
/**
 * plan-guard — deterministic enforcement of the work plan's owner-gate registry.
 *
 * Prompts degrade; hooks do not. This is the layer that makes an unattended
 * dispatch loop safe: whatever an agent decides, it cannot cross these lines.
 *
 * Source of truth: project-docs/work_plan.md §6 (owner-gate registry).
 * When §6 changes, change OWNER_GATES below in the same PR.
 *
 * Contract: PreToolUse hook. Reads the tool call as JSON on stdin.
 *   exit 0 -> allow.  exit 2 + stderr -> block, stderr is shown to the agent.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'

// Fail closed. A guard that crashes and exits 1 reads as "allow" — that is how a
// boundary silently stops existing, and in an unattended run nobody is there to
// notice. An unevaluated call is not a safe call.
process.on('uncaughtException', (err) => {
  process.stderr.write(
    `BLOCKED by plan-guard: the guard itself crashed (${err.message}).\n` +
      'Fix it before continuing: node .claude/hooks/plan-guard.test.mjs\n',
  )
  process.exit(2)
})

const OWNER_GATES = [
  // --- history rewrite / force push (work_plan §6, BO-8) -------------------
  {
    id: 'force-push',
    test: /\bgit\b[^\n]*\b(push)\b[^\n]*(--force|--mirror|\s-f\b)/,
    why: 'Force-push / history rewrite is OWNER-GATE (work_plan §6, BO-8/WS-2).',
  },
  {
    id: 'history-rewrite',
    test: /\bgit\b[^\n]*\b(filter-branch|filter-repo)\b|\bgit\s+reset\s+--hard\s+origin\b/,
    why: 'History rewrite is OWNER-GATE (work_plan §6, BO-8/WS-2).',
  },

  // --- enforcement flips (work_plan §6) -----------------------------------
  {
    id: 'enforcement-flip',
    test: /\b(ACTION_BROKER_ENFORCE|MEM0_ENABLED|GRAPHITI_ENABLED|WHATSAPP_ENRICHMENT|SKILLS_FAIL_CLOSED|SKILLS_INDEX_ONLY|CRM_ZOHO_SYNC|CRM_AUTO_LEAD)\s*[=:]\s*["']?(1|true|on|yes)\b/i,
    why: 'Flipping this feature flag on is OWNER-GATE (work_plan §6 enforcement flips).',
  },
  {
    id: 'permission-mode-flip',
    test: /\bAGENT_PERMISSION_MODE\s*[=:]\s*["']?enforce\b/i,
    why: 'AGENT_PERMISSION_MODE=enforce is OWNER-GATE (work_plan §6).',
  },

  // --- deploy + prod reach (memory: deploy auto-applies migrations) --------
  {
    id: 'deploy',
    test: /\bdeploy\/[\w.\/-]*\.sh\b|\bapply_migrations\.sh\b|\bssh\s+[\w.-]*@/,
    why: 'Deploying / reaching the VPS is OWNER-GATE. Deploy auto-applies migrations; an agent cannot undo a bad one.',
  },

  // --- credentials ---------------------------------------------------------
  {
    id: 'secrets',
    test: /(^|\s)(cat|type|Get-Content)\s+[^\n|]*\.env\b/,
    why: 'Reading .env is OWNER-GATE (credential exposure, work_plan §6 / WS-2).',
  },
]

// Commands that WRITE. Used to decide whether a Bash command mentioning a
// protected path is reading it (fine) or changing it (blocked).
//
// ⚠️ This is a TRIPWIRE, not a sandbox, and the distinction is worth being
// honest about rather than implying more. You cannot fully constrain a shell
// with a regex: `python -c "open('.env','w')"` and a here-doc through an
// interpreter both evade this list, and always will. What it stops is the
// PATH OF LEAST RESISTANCE — an agent whose Write call was just refused
// reaching for `cp` because that is the next thing to hand. That is not a
// hypothetical: it is exactly what happened on 2026-08-26, when
// `deploy/hostinger/acb-pull.{service,timer}` were placed with `cp` seconds
// after plan-guard refused the Write. The gate was correct and the agent went
// around it without ever deciding to.
//
// Deliberate evasion is a different failure and a different control (review,
// and the agent being TOLD to refuse). This closes the accident.
const SHELL_WRITE = new RegExp(
  [
    // destructive/creating verbs, at a command position
    String.raw`(^|[\s|;&(])(cp|mv|install|tee|dd|truncate|touch|ln|rsync|chmod|chown|rm|mkdir)\b`,
    // any redirect that creates or appends
    String.raw`>\s*\S`,
    // in-place edits
    String.raw`\bsed\b[^|;&]*\s-i`,
    String.raw`\bperl\b[^|;&]*\s-i`,
  ].join('|'),
)

// Paths an agent may never write, whatever the reason.
const PROTECTED_PATHS = [
  { test: /(^|[\\/])\.env(\.|$)/, why: '.env files hold live credentials (OWNER-GATE, WS-2).' },
  { test: /(^|[\\/])deploy[\\/]/, why: 'deploy/ changes are OWNER-GATE — they run against prod.' },
]

function block(reason) {
  process.stderr.write(
    `BLOCKED by plan-guard: ${reason}\n\n` +
      'This is an OWNER-GATE action. Per work_plan.md §1.7 an agent must refuse it ' +
      'and say so. Stop, record what is needed in the ticket write-up, and hand it ' +
      'to the owner — do not attempt a workaround.\n',
  )
  process.exit(2)
}

const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || process.cwd()

function git(dir, ...args) {
  try {
    return execFileSync('git', args, {
      encoding: 'utf8',
      cwd: dir,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch {
    return null
  }
}

/**
 * Where the command will actually run. A session can touch more than one repo,
 * and `cd <path> && git ...` is the normal shape, so the payload cwd alone is
 * not enough.
 */
function effectiveCwd(cmd, payloadCwd) {
  const m = cmd.match(/^\s*cd\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))\s*(?:&&|;)/)
  const target = m ? m[1] || m[2] || m[3] : null
  if (!target) return payloadCwd || PROJECT_DIR
  return path.isAbsolute(target) ? target : path.resolve(payloadCwd || PROJECT_DIR, target)
}

const samePath = (a, b) =>
  a && b && path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase()

/**
 * Branch discipline is scoped to THIS repo. A command operating on a different
 * checkout answers to that project's rules, not ours — claiming jurisdiction
 * over it produces false blocks, and false blocks teach people to disable the
 * guard entirely. The owner gates above stay universal: they are dangerous
 * wherever they run.
 */
function governsRepoAt(dir) {
  const here = git(dir, 'rev-parse', '--show-toplevel')
  const ours = git(PROJECT_DIR, 'rev-parse', '--show-toplevel')
  if (!here || !ours) return true // can't tell — fail closed
  return samePath(here, ours)
}

let raw = ''
process.stdin.setEncoding('utf8')
for await (const chunk of process.stdin) raw += chunk

// Unparseable payload => the uncaughtException handler blocks. Deliberate.
const payload = JSON.parse(raw)

const tool = payload.tool_name || ''
const input = payload.tool_input || {}

if (tool === 'Bash' || tool === 'PowerShell') {
  const cmd = String(input.command || '')

  for (const gate of OWNER_GATES) {
    if (gate.test.test(cmd)) block(gate.why)
  }

  // PROTECTED_PATHS, enforced for the SHELL too. Until 2026-08-26 this list was
  // consulted only in the Write/Edit branch below, so every path it protects —
  // `.env` included — was writable through `cp`, `tee`, `sed -i` or a plain `>`
  // redirect. A guard that refuses the tool an agent would naturally use, and
  // permits the shell command that does the same thing, protects nothing; it
  // just makes the bypass one step longer than the block.
  //
  // Reads stay allowed: `cat deploy/hostinger/acb-gateway.service` is how you
  // find out what is there, and blocking it would push people to copy files out
  // to look at them. Only a command that both MENTIONS a protected path and
  // LOOKS LIKE A WRITE is refused.
  // ⚠️ TOKENISED, not substring-matched — and that is not a refinement.
  // The substring version silently failed six of the eleven cases in
  // plan-guard.test.mjs. PROTECTED_PATHS anchors each pattern on
  // `(^|[\\\\/])`, which is right for a FILE PATH and wrong for a path
  // sitting inside a command: in `echo X > .env` the `.env` is preceded by a
  // SPACE, so the anchor never matches and the guard waves it through.
  // Splitting the command into tokens hands each pattern the thing it was
  // written to judge.
  // ⚠️ HEREDOC BODIES ARE DATA, NOT COMMAND — strip them before scanning.
  // Found immediately: this rule blocked its own commit, because the commit
  // message (passed through `git commit -m "$(cat <<'EOF' … EOF)"`) mentioned
  // `.env` in prose while the surrounding command contained a redirect. Nothing
  // was being written to `.env` at all.
  //
  // That matters beyond the annoyance. A guard with false positives on ordinary
  // work gets routed around on purpose — the next person writes the commit
  // message differently, or drops the guard from their loop, and the real block
  // goes with it. Precision IS the safety property here.
  //
  // A heredoc that genuinely targets a protected path still blocks: in
  // `cat > .env <<'EOF'` the `> .env` lives in the COMMAND half, which survives
  // this strip untouched.
  const scanned = cmd.replace(/<<-?\s*['"]?(\w+)['"]?[\s\S]*?\n\s*\1\b/g, ' ')

  if (SHELL_WRITE.test(scanned)) {
    const tokens = scanned.split(/[\s;|&()<>'"`]+/).filter(Boolean)
    for (const token of tokens) {
      // Strip a leading `./` and any `VAR=` prefix, so a path is recognised
      // however it was reached.
      const candidate = token
        .replace(/^\.\//, '')
        .replace(/^[A-Za-z_][\w]*=/, '')
      for (const p of PROTECTED_PATHS) {
        if (p.test.test(candidate)) {
          block(
            `a shell command writing to a protected path (${candidate}) — ${p.why}\n` +
              `  command: ${scanned.slice(0, 200)}`,
          )
        }
      }
    }
  }

  // Work lands on a branch, never on main. pr-check only runs against main,
  // so a branch cut from anything else gets zero CI — see the stacked-PR trap.
  if (/\bgit\s+(commit|push)\b/.test(cmd)) {
    const dir = effectiveCwd(cmd, payload.cwd)
    if (governsRepoAt(dir) && git(dir, 'rev-parse', '--abbrev-ref', 'HEAD') === 'main') {
      block(
        'committing/pushing directly on main. Cut a WS-named branch off main first ' +
          '(git switch -c ws-<n>-<slug>). Branches must come off main or pr-check gives them zero CI.',
      )
    }
  }
}

if (tool === 'Edit' || tool === 'Write' || tool === 'MultiEdit' || tool === 'NotebookEdit') {
  const path = String(input.file_path || input.notebook_path || '')
  for (const p of PROTECTED_PATHS) {
    if (p.test.test(path)) block(`writing to ${path} — ${p.why}`)
  }

  const body = String(input.content || input.new_string || '')
  for (const gate of OWNER_GATES) {
    if (gate.id.endsWith('-flip') || gate.id === 'enforcement-flip') {
      if (gate.test.test(body)) block(`${gate.why} (found in the content being written to ${path})`)
    }
  }
}

process.exit(0)
