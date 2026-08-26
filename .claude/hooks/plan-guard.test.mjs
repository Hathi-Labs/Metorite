#!/usr/bin/env node
/**
 * Tests for plan-guard.mjs. Run: node .claude/hooks/plan-guard.test.mjs
 *
 * Note: several patterns are assembled by concatenation on purpose. The guard
 * also inspects file *content* being written, so a fixture containing a literal
 * flag flip would block its own creation.
 *
 * ⚠️ EVERY CASE RUNS AGAINST A SYNTHETIC PROJECT DIR, never the real one.
 * The guard reads `.claude/OWNER_GRANTS.md` from `CLAUDE_PROJECT_DIR`, so a
 * suite pointed at the real repo would pass or fail depending on what the owner
 * happened to write there this morning. The grant work on
 * `governance-d45-owner-grants` shipped WITHOUT this isolation, which is why its
 * 35 cases would have started failing the first day a grant existed.
 */
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const GUARD = path.join(HERE, 'plan-guard.mjs')
const REPO = path.resolve(HERE, '..', '..')

const bash = (command) => ({ tool_name: 'Bash', tool_input: { command } })
const write = (file_path, content) => ({ tool_name: 'Write', tool_input: { file_path, content } })

/** A throwaway project dir holding exactly the grant lines given. */
function projectWithGrants(lines) {
  const dir = mkdtempSync(path.join(tmpdir(), 'pg-'))
  mkdirSync(path.join(dir, '.claude'), { recursive: true })
  if (lines !== null) {
    writeFileSync(path.join(dir, '.claude', 'OWNER_GRANTS.md'), lines.join('\n') + '\n', 'utf8')
  }
  return dir
}

const t = new Date()
const TODAY = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`

const NO_GRANTS = projectWithGrants([])

const CASES = [
  // [name, payload, expectBlocked, projectDir?]
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
  ['cp into deploy/', bash('cp /tmp/x deploy/hostinger/acb-pull.service'), true],
  ['redirect into dotenv', bash('echo SECRET' + '=1 > .env'), true],
  ['tee into dotenv', bash('echo x | tee .env.production'), true],
  ['append into dotenv', bash('echo x >> apps/services/.env'), true],
  ['mv into deploy/', bash('mv /tmp/unit deploy/hostinger/acb-x.timer'), true],
  ['rm inside deploy/', bash('rm deploy/hostinger/acb-gateway.service'), true],
  ['install into deploy/', bash('install -m 644 /tmp/u deploy/hostinger/u.timer'), true],

  // Reads MUST survive.
  ['cat a deploy unit', bash('cat deploy/hostinger/acb-gateway.service'), false],
  ['grep inside deploy/', bash('grep -n Exec deploy/hostinger/acb-gateway.service'), false],
  ['ls deploy/', bash('ls -la deploy/hostinger/'), false],
  ['cp between safe paths', bash('cp src/a.ts src/b.ts'), false],
  ['mkdir somewhere safe', bash('mkdir -p build/out'), false],

  // ⚠️ FALSE-POSITIVE REGRESSION (heredoc prose).
  [
    'prose mentioning dotenv',
    bash("git commit -m \"$(cat <<'EOF'\nfix: stop writing to .env > here\nEOF\n)\""),
    false,
  ],
  ['heredoc INTO dotenv', bash("cat > .env <<'EOF'\nSECRET" + "=1\nEOF"), true],

  // ── DESCRIPTOR REDIRECTS ARE NOT FILE WRITES (added 2026-08-26, H-53) ─────
  //
  // ⚠️ The redirect arm read `>\s*\S`, which matches `2>&1` and `2>/dev/null`.
  // Neither writes a file. `2>&1` duplicates a descriptor and `/dev/null` is the
  // bit bucket. Measured within ONE HOUR of the path rule landing: the guard
  // refused three PURE READS in a single session. A guard that fires on ordinary
  // work gets routed around on purpose — precision is the safety property.
  ['read deploy/ with 2>&1', bash('ls -1 deploy/hostinger/*.service 2>&1'), false],
  ['read deploy/ with 2>/dev/null', bash('grep -rn Exec deploy/hostinger/ 2>/dev/null'), false],
  ['read dotenv path with 2>/dev/null', bash('grep -c TASKS .env.example 2>/dev/null'), false],
  ['discard stdout to /dev/null', bash('cat deploy/hostinger/x.service > /dev/null'), false],
  // ...but a real write still blocks even when stderr is discarded.
  ['cp into deploy/ with 2>/dev/null', bash('cp /tmp/x deploy/hostinger/u.timer 2>/dev/null'), true],

  // ── COMMITTED TEMPLATES ARE NOT SECRETS (added 2026-08-26) ────────────────
  //
  // `.env.example` is in git. Git guarantees it holds no credential, and the
  // `Secret scan` CI check enforces that on every PR. Treating it as `.env`
  // bought nothing and cost two hand-offs (H-30, H-34). Owner directive.
  ['read .env.example', bash('cat .env.example'), false],
  ['write .env.example', write('.env.example', 'TASKS_LENS' + '=0'), false],
  ['append to .env.example', bash('echo FOO' + '=0 >> .env.example'), false],
  ['sed -i .env.example', bash('sed -i /CLICKUP/d .env.example'), false],
  // ...while the real thing stays shut.
  ['write .env.production still blocked', write('.env.production', 'X=1'), true],

  // ── OWNER GRANTS (D45) ───────────────────────────────────────────────────
  //
  // ⚠️ THIS IS THE HALF THAT WAS NEVER LANDED. The owner wrote 22 ALLOW lines
  // between 2026-08-19 and 2026-08-26 and every one was ignored, because
  // `plan-guard.mjs` on `main` never opened the file. Silent, too — nothing told
  // the owner their grant was not read.
  [
    'granted deploy-write allows the write',
    write('deploy/compose.yml', 'services: {}'),
    false,
    projectWithGrants([`ALLOW ${TODAY} deploy-write — test`]),
  ],
  [
    'granted deploy allows ssh',
    bash('ssh root@srv1.hostinger.com uptime'),
    false,
    projectWithGrants([`ALLOW ${TODAY} deploy — test`]),
  ],
  [
    'granted env-write allows the .env write',
    write('apps/services/.env', 'X=1'),
    false,
    projectWithGrants([`ALLOW ${TODAY} env-write — test`]),
  ],
  [
    'a grant for ANOTHER gate does not unlock this one',
    bash('ssh root@srv1.hostinger.com uptime'),
    true,
    projectWithGrants([`ALLOW ${TODAY} secrets — wrong gate`]),
  ],
  [
    'a STALE grant is inert',
    write('deploy/compose.yml', 'services: {}'),
    true,
    projectWithGrants(['ALLOW 2020-01-01 deploy-write — long past']),
  ],
  [
    'a malformed grant line is inert',
    write('deploy/compose.yml', 'services: {}'),
    true,
    projectWithGrants([`ALLOW deploy-write ${TODAY} — fields swapped`]),
  ],
  [
    'no grants file at all is inert, not a crash',
    write('deploy/compose.yml', 'services: {}'),
    true,
    projectWithGrants(null),
  ],

  // ⚠️ THE GRANT FILE IS NEVER AGENT-WRITABLE, AND THAT IS NOT GRANTABLE.
  // A grant an agent can write is not a grant. Both cases below carry a
  // deploy-write grant to prove it does not help.
  [
    'agent may not Write the grant file',
    write('.claude/OWNER_GRANTS.md', `ALLOW ${TODAY} deploy — self-granted`),
    true,
    projectWithGrants([`ALLOW ${TODAY} deploy-write — test`]),
  ],
  [
    'agent may not shell-append to the grant file',
    bash(`echo "ALLOW ${TODAY} deploy" >> .claude/OWNER_GRANTS.md`),
    true,
    projectWithGrants([`ALLOW ${TODAY} deploy-write — test`]),
  ],
  // Reading it is fine — an agent must be able to see what it may do.
  ['agent may READ the grant file', bash('cat .claude/OWNER_GRANTS.md'), false],

  ['plain pytest', bash('uv run pytest tests/unit/'), false],
  ['ruff', bash('uv run ruff check .'), false],
  ['git status', bash('git status --short'), false],
  ['branch off main', bash('git switch -c ws-5-ci-gates main'), false],
  ['flag set OFF', bash('SKILLS_INDEX_ONLY' + '=0 uv run pytest'), false],
  ['normal source edit', write('apps/services/gateway/main.py', 'x = 1'), false],
  ['spec edit', write('project-docs/specs/skills_registry.md', '## Status'), false],
]

let failed = 0
for (const [name, payload, expectBlocked, projectDir] of CASES) {
  const res = spawnSync(process.execPath, [GUARD], {
    input: JSON.stringify(payload),
    encoding: 'utf8',
    env: { ...process.env, CLAUDE_PROJECT_DIR: projectDir || NO_GRANTS },
  })
  const blocked = res.status === 2
  const ok = blocked === expectBlocked
  if (!ok) failed++
  console.log(
    `${ok ? 'ok  ' : 'FAIL'}  ${name.padEnd(40)} expected=${expectBlocked ? 'block' : 'allow'} got=${blocked ? 'block' : 'allow'}`,
  )
  if (!ok && res.stderr) console.log(`        ${res.stderr.split('\n')[0]}`)
}

// Branch discipline depends on the live branch, so this one runs against the
// REAL repo and reports rather than asserting a fixed answer.
const onMain = spawnSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], {
  encoding: 'utf8',
  cwd: REPO,
}).stdout.trim() === 'main'
const commit = spawnSync(process.execPath, [GUARD], {
  input: JSON.stringify({ ...bash('git commit -m "wip"'), cwd: REPO }),
  encoding: 'utf8',
  env: { ...process.env, CLAUDE_PROJECT_DIR: REPO },
})
const commitBlocked = commit.status === 2
const branchOk = commitBlocked === onMain
if (!branchOk) failed++
console.log(
  `${branchOk ? 'ok  ' : 'FAIL'}  ${'commit on main'.padEnd(40)} onMain=${onMain} blocked=${commitBlocked}`,
)

console.log(failed === 0 ? `\nall ${CASES.length + 1} cases passed` : `\n${failed} FAILED`)
process.exit(failed === 0 ? 0 : 1)
