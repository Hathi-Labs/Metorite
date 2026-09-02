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

// Offsets from today, for the ranged `ALLOW-UNTIL` form. Computed rather than
// hard-coded: a fixture with a literal future date becomes a stale fixture the
// day it passes, and it passes silently — the suite goes green while the arm it
// covers has stopped being exercised.
const dayOffset = (n) => {
  const d = new Date(t.getFullYear(), t.getMonth(), t.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const FUTURE = dayOffset(30)
const YESTERDAY = dayOffset(-1)

const NO_GRANTS = projectWithGrants([])

const CASES = [
  // [name, payload, expectBlocked, projectDir?]
  ['force push', bash('git push --' + 'force origin main'), true],
  ['push -f', bash('git push -f origin ws-5'), true],
  ['filter-branch', bash('git filter-' + 'branch --tree-filter rm -rf secrets'), true],
  ['filter-repo', bash('git filter-' + 'repo --path secrets --invert-paths'), true],

  // ── reset --hard: the gate was BACKWARDS until 2026-09-02 ────────────────
  // It matched only `reset --hard origin`, so it blocked the ONE safe shape
  // and permitted every destructive one. These cases pin the corrected
  // direction, because the inversion read as deliberate for weeks.
  //
  // SAFE — a local pointer move to a ref that is already published.
  ['reset --hard to origin ref', bash('git reset --' + 'hard origin/main'), false],
  ['reset --hard to a topic branch on origin', bash('git reset --' + 'hard origin/ws-5-foo'), false],
  ['reset --hard to upstream ref', bash('git reset --' + 'hard upstream/main'), false],
  // DESTRUCTIVE — these orphan COMMITTED work, and the old form allowed them.
  ['reset --hard to HEAD~n orphans commits', bash('git reset --' + 'hard HEAD~3'), true],
  ['reset --hard to a raw sha orphans commits', bash('git reset --' + 'hard abc1234'), true],
  ['bare reset --hard discards the tree', bash('git reset --' + 'hard'), true],
  // ⚠️ `origin` alone is NOT a ref sync — it is ambiguous and not a branch.
  ['reset --hard to bare origin still blocks', bash('git reset --' + 'hard origin'), true],
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

  // ── the ranged form, ALLOW-UNTIL (2026-09-01) ──────────────────────────
  // The window must CLOSE BY ITSELF. Every case below exists to hold one half
  // of that: it opens while the date is ahead, and it is inert the moment the
  // date is behind or missing.
  [
    'ALLOW-UNTIL a future date grants the gate',
    write('deploy/compose.yml', 'services: {}'),
    false,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} deploy-write — dev phase`]),
  ],
  [
    'ALLOW-UNTIL today is INCLUSIVE of today',
    write('deploy/compose.yml', 'services: {}'),
    false,
    projectWithGrants([`ALLOW-UNTIL ${TODAY} deploy-write — last day`]),
  ],
  [
    'ALLOW-UNTIL yesterday has EXPIRED',
    write('deploy/compose.yml', 'services: {}'),
    true,
    projectWithGrants([`ALLOW-UNTIL ${YESTERDAY} deploy-write — window closed`]),
  ],
  [
    'ALLOW-UNTIL with NO date grants nothing',
    write('deploy/compose.yml', 'services: {}'),
    true,
    projectWithGrants(['ALLOW-UNTIL deploy-write — open-ended, must not parse']),
  ],
  [
    'ALLOW-UNTIL deploy covers ssh to the box',
    bash('ssh acb@203.0.113.4 systemctl status acb-gateway'),
    false,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} deploy — dev phase`]),
  ],
  [
    'ALLOW-UNTIL secrets covers reading .env on the box',
    bash('cat /opt/acb/app/.env'),
    false,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} secrets — dev phase`]),
  ],
  // ⚠️ THE RANGED FORM IS STILL PER-ID. A dev-phase window must not become a
  // skeleton key: force-push and history-rewrite are the two gates the owner
  // deliberately left OUT of the 2026-09-01 window, and nothing else's grant
  // may reach them.
  [
    'ALLOW-UNTIL deploy does NOT grant force-push',
    bash('git push --' + 'force origin main'),
    true,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} deploy — dev phase`]),
  ],
  [
    'ALLOW-UNTIL deploy does NOT grant history-rewrite',
    // ⚠️ Was `git reset --hard origin/main` until 2026-09-02. That shape is a
    // local sync now and correctly ALLOWED, so it stopped testing anything.
    // Use a rewrite that touches history other people have pulled.
    bash('git reset --' + 'hard HEAD~3'),
    true,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} deploy — dev phase`]),
  ],
  [
    'ALLOW-UNTIL never unlocks writing the grant file',
    write('.claude/OWNER_GRANTS.md', `ALLOW-UNTIL ${FUTURE} deploy — self-granted`),
    true,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} deploy — dev phase`]),
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

  // ── the `-m` false positive (2026-09-01) ───────────────────────────────
  // A `git add` of the grant file was refused because the commit message
  // carried `Co-Authored-By: … <noreply@anthropic.com>`, and SHELL_WRITE read
  // the `>` closing that address as a redirect. Nothing was written anywhere.
  // Every commit that names a protected path and carries a standard trailer
  // hit this — the shape that teaches people to drop a guard.
  [
    'a trailer with an email does not read as a redirect',
    bash('git add .claude/OWNER_GRANTS.md && git commit -m "grant" -m "Co-Authored-By: A B <x@y.com>"'),
    false,
  ],
  [
    'a -m body mentioning .env is prose, not a write',
    bash('git commit -m "document the .env layout for the box"'),
    false,
  ],
  [
    "a -m body mentioning deploy/ is prose too",
    bash("git commit -m 'move the deploy/ notes into the README'"),
    false,
  ],
  // ⚠️ THE STRIP MUST NOT BLIND THE GUARD. Each of these puts the write in the
  // COMMAND half, where neither strip reaches.
  [
    'a real redirect to .env still blocks, trailer or not',
    bash('echo SECRET=1 > .env && git commit -m "x <a@b.com>"'),
    true,
  ],
  [
    'a heredoc TARGETING .env still blocks',
    bash("cat > .env <<'EOF'\nA=1\nEOF"),
    true,
  ],
  [
    'a real write under deploy/ still blocks after a -m',
    bash('git commit -m "note" && cp x.service deploy/hostinger/x.service'),
    true,
  ],

  // ── OWNER_GATES must judge the STRIPPED command (2026-09-02) ───────────
  // The gate loop read the RAW command while the protected-path arm read the
  // stripped one. So a commit message DESCRIBING a gated act was read as
  // performing it. git never executes a `-m` body.
  [
    'a -m body describing a reset is prose',
    bash('git commit -m "the gate allowed reset --' + 'hard HEAD~3 until today"'),
    false,
  ],
  [
    'a -m body describing ssh is prose',
    bash('git commit -m "ssh ' + 'root@box to read the logs"'),
    false,
  ],
  [
    'a -m body describing a deploy script is prose',
    bash('git commit -m "document deploy/' + 'release.sh for the runbook"'),
    false,
  ],
  // ⚠️ THE STRIP MUST NOT HIDE A REAL ACT. Outside the quoted body, it blocks.
  [
    'a real ssh after a -m still blocks',
    bash('git commit -m "notes" && ssh ' + 'root@srv1.hostinger.com uptime'),
    true,
  ],
  [
    'a real reset after a -m still blocks',
    bash('git commit -m "notes" && git reset --' + 'hard HEAD~3'),
    true,
  ],

  // ── `>=` is a COMPARISON, not a redirect (2026-09-02) ──────────────────
  // Third false positive of one class in two days. This one refused a PURE
  // READ of the grant file, which is the read an agent must make to learn
  // what it may do. stripNoise() cannot reach it — the `>=` sat in an inline
  // script argument, not a quoted -m body or a heredoc.
  [
    'a >= comparison is not a redirect',
    bash('node -e "if (d >= today) console.log(1)" && cat .claude/OWNER_GRANTS.md'),
    false,
  ],
  [
    'a <= comparison near a protected path is not a write',
    // Reads `deploy/`, a PROTECTED_PATH, so it exercises the redirect arm.
    // ⚠️ NOT a `deploy/*.sh` path: that trips the `deploy` OWNER_GATE on its
    // own, and the case would then pass for the wrong reason.
    bash('python -c "print(1 <= 2)" && ls deploy/hostinger/'),
    false,
  ],
  // ⚠️ THE EXCLUSION MUST NOT BLIND THE ARM. Append and truncate are writes.
  [
    'append to a protected path still blocks',
    bash('echo X=1 >> .env'),
    true,
  ],
  [
    'truncating redirect to a protected path still blocks',
    bash('echo X=1 > .env'),
    true,
  ],
  [
    'append to the grant file still blocks',
    bash('echo ALLOW >> .claude/OWNER_GRANTS.md'),
    true,
  ],

  // ── restoring the grant file out of history is a WRITE ─────────────────
  // `git add`/`git commit` only record what the owner wrote. `git checkout --`
  // and `git restore` REPLACE the working copy, which on this file could
  // revive a grant the calendar already retired.
  [
    'git checkout of the grant file is a write',
    bash('git checkout -- .claude/OWNER_GRANTS.md'),
    true,
  ],
  [
    'git restore of the grant file is a write',
    bash('git restore .claude/OWNER_GRANTS.md'),
    true,
  ],
  [
    'git stash pop touching the grant file is a write',
    bash('git stash pop && cat .claude/OWNER_GRANTS.md'),
    true,
  ],
  [
    'git add of the grant file is NOT a write',
    bash('git add .claude/OWNER_GRANTS.md'),
    false,
  ],

  // ── the guard protects itself (2026-09-01) ─────────────────────────────
  // The cheapest bypass in the design was to edit the guard, or to delete its
  // entry from settings.json. GRANTABLE, not sealed — a guard no agent can
  // repair is a guard that rots.
  ['agent may not Write plan-guard', write('.claude/hooks/plan-guard.mjs', 'x'), true],
  ['agent may not Write the guard test', write('.claude/hooks/plan-guard.test.mjs', 'x'), true],
  ['agent may not Write settings.json', write('.claude/settings.json', '{}'), true],
  [
    'guard-write unlocks the guard',
    write('.claude/hooks/plan-guard.mjs', 'x'),
    false,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} guard-write — repair`]),
  ],
  [
    'a deploy grant does NOT unlock the guard',
    write('.claude/hooks/plan-guard.mjs', 'x'),
    true,
    projectWithGrants([`ALLOW-UNTIL ${FUTURE} deploy — dev phase`]),
  ],
  [
    'a shell write to settings.json is blocked too',
    bash('cp /tmp/settings.json .claude/settings.json'),
    true,
  ],
  // Reading the guard is always fine.
  ['agent may READ plan-guard', bash('cat .claude/hooks/plan-guard.mjs'), false],
  ['agent may RUN the guard test', bash('node .claude/hooks/plan-guard.test.mjs'), false],

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
