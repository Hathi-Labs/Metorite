#!/usr/bin/env node
/**
 * Tests for plan-guard.mjs. Run: node .claude/hooks/plan-guard.test.mjs
 *
 * Note: several patterns are assembled by concatenation on purpose. The guard
 * also inspects file *content* being written, so a fixture containing a literal
 * flag flip would block its own creation.
 */
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const GUARD = path.join(path.dirname(fileURLToPath(import.meta.url)), 'plan-guard.mjs')
const bash = (command) => ({ tool_name: 'Bash', tool_input: { command } })
const write = (file_path, content) => ({ tool_name: 'Write', tool_input: { file_path, content } })

const CASES = [
  // [name, payload, expectBlocked]
  ['force push', bash('git push --' + 'force origin main'), true],
  ['push -f', bash('git push -f origin ws-5'), true],
  ['filter-branch', bash('git filter-' + 'branch --tree-filter rm -rf secrets'), true],
  ['skills flag flip', bash('export SKILLS_INDEX_ONLY' + '=1'), true],
  ['mem0 flip', bash('MEM0_ENABLED' + '=true uv run pytest'), true],
  ['permission enforce', bash('AGENT_PERMISSION_MODE' + '=enforce make test'), true],
  ['deploy script', bash('bash deploy/release.sh'), true],
  ['apply migrations', bash('./apply_' + 'migrations.sh'), true],
  ['ssh to host', bash('ssh root@srv1.hostinger.com uptime'), true],
  ['read dotenv', bash('cat apps/services/.env'), true],
  ['write dotenv', write('apps/services/.env.local', 'X=1'), true],
  ['write deploy dir', write('deploy/compose.yml', 'services: {}'), true],
  ['flip inside content', write('config.json', 'GRAPHITI_ENABLED' + '=true'), true],

  // ── PROTECTED_PATHS through the SHELL (added 2026-08-26) ──────────────────
  //
  // Until this date PROTECTED_PATHS was consulted ONLY for Write/Edit, so every
  // path it guards — `.env` included — was writable with `cp`, `tee`, `sed -i`
  // or a plain `>`. A guard that refuses the tool an agent naturally reaches for
  // and permits the shell command doing the same thing protects nothing.
  //
  // ⚠️ Not hypothetical, and not a near-miss: `deploy/hostinger/acb-pull.*` were
  // placed with `cp` seconds after plan-guard refused the Write, on this date.
  // The first case below is that exact command.
  ['cp into deploy/', bash('cp /tmp/x deploy/hostinger/acb-pull.service'), true],
  ['redirect into dotenv', bash('echo SECRET' + '=1 > .env'), true],
  ['tee into dotenv', bash('echo x | tee .env.production'), true],
  ['append into dotenv', bash('echo x >> apps/services/.env'), true],
  ['mv into deploy/', bash('mv /tmp/unit deploy/hostinger/acb-x.timer'), true],
  ['rm inside deploy/', bash('rm deploy/hostinger/acb-gateway.service'), true],
  ['install into deploy/', bash('install -m 644 /tmp/u deploy/hostinger/u.timer'), true],

  // Reads MUST survive. Blocking them would push the next agent to copy files
  // out of deploy/ just to look at them — turning a read into a write, which is
  // the opposite of what this rule is for.
  ['cat a deploy unit', bash('cat deploy/hostinger/acb-gateway.service'), false],
  ['grep inside deploy/', bash('grep -n Exec deploy/hostinger/acb-gateway.service'), false],
  ['ls deploy/', bash('ls -la deploy/hostinger/'), false],
  ['cp between safe paths', bash('cp src/a.ts src/b.ts'), false],
  ['mkdir somewhere safe', bash('mkdir -p build/out'), false],

  // ⚠️ FALSE-POSITIVE REGRESSION. The path rule blocked its own commit the
  // moment it was written: a commit message passed through a heredoc mentioned
  // `.env` in PROSE while the surrounding command held a redirect. Nothing was
  // being written to `.env` at all.
  //
  // This case matters more than it looks. A guard that fires on ordinary work
  // gets routed around ON PURPOSE — the next person rewords the commit message,
  // or drops the hook from their loop, and the real block leaves with it.
  // Precision is a safety property, not a nicety.
  [
    'prose mentioning dotenv',
    bash("git commit -m \"$(cat <<'EOF'\nfix: stop writing to .env > here\nEOF\n)\""),
    false,
  ],
  // ...but a heredoc that genuinely targets one still blocks, because the
  // redirect lives in the COMMAND half, which survives the strip.
  ['heredoc INTO dotenv', bash("cat > .env <<'EOF'\nSECRET" + "=1\nEOF"), true],

  ['plain pytest', bash('uv run pytest tests/unit/'), false],
  ['ruff', bash('uv run ruff check .'), false],
  ['git status', bash('git status --short'), false],
  ['branch off main', bash('git switch -c ws-5-ci-gates main'), false],
  ['flag set OFF', bash('SKILLS_INDEX_ONLY' + '=0 uv run pytest'), false],
  ['normal source edit', write('apps/services/gateway/main.py', 'x = 1'), false],
  ['spec edit', write('project-docs/specs/skills_registry.md', '## Status'), false],
]

let failed = 0
for (const [name, payload, expectBlocked] of CASES) {
  const res = spawnSync(process.execPath, [GUARD], { input: JSON.stringify(payload), encoding: 'utf8' })
  const blocked = res.status === 2
  const ok = blocked === expectBlocked
  if (!ok) failed++
  console.log(
    `${ok ? 'ok  ' : 'FAIL'}  ${name.padEnd(22)} expected=${expectBlocked ? 'block' : 'allow'} got=${blocked ? 'block' : 'allow'}`,
  )
  if (!ok && res.stderr) console.log(`        ${res.stderr.split('\n')[0]}`)
}

// Branch discipline depends on the live branch; report it rather than asserting.
const onMain = spawnSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], { encoding: 'utf8' }).stdout.trim() === 'main'
const commit = spawnSync(process.execPath, [GUARD], {
  input: JSON.stringify(bash('git commit -m "wip"')),
  encoding: 'utf8',
})
const commitBlocked = commit.status === 2
const branchOk = commitBlocked === onMain
if (!branchOk) failed++
console.log(
  `${branchOk ? 'ok  ' : 'FAIL'}  ${'commit on main'.padEnd(22)} onMain=${onMain} blocked=${commitBlocked}`,
)

console.log(failed === 0 ? `\nall ${CASES.length + 1} cases passed` : `\n${failed} FAILED`)
process.exit(failed === 0 ? 0 : 1)
