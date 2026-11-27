# Engineering practice — how we build with live customers and agent authors

**Status:** DOCTRINE — binding · **Date:** 2026-08-10 · **Owner:** vjvarada ·
**Decision:** D28 (`work_plan.md` §3) · **Enforced rules:** R6, R7, R8
(`work_plan.md` §1) · **Classification:** CONTRACTS & DOCTRINE (`INDEX.md`)

**What this owns:** *how* we build — environments, release, migrations, the
division of work between agents, what a test has to be worth, and the security
posture once the users are not colleagues. It owns **no tickets**; the board owns
order, the owning specs own scope. Where it states a rule that binds, that rule
is R6/R7/R8 in `work_plan.md` §1 and this file is its reasoning.

**Written from measured failures in this repository, not from general advice.**
Every claim below cites something that actually happened here. That is
deliberate: an agent reading generic best practice will apply it generically.

---

## 0. The two premises everything else follows from

**0.1 — Our failures are delivery and integration failures, not coding
failures.** The evidence, all from a two-week window:

| What failed | What would have caught it |
|---|---|
| Migration number collisions, **three times** (WS-10, #403's 158, #399's 157–159) | Short-lived branches; a collision check |
| Two PRs green alone, **red together** (#399's cascade-map fence × #403's new table) — twice in one day | Continuous integration into main, not long branches |
| Four deploys reported success **while shipping nothing** (2026-08-07) | A deployed-SHA check, not a health check |
| `ALTER TABLE if` sat in the hand-run production promotion script | A fence comparing the artifact to reality (added 2026-08-10) |
| Migration 148 broke three write routes that predated it | Expand/contract (R6) |
| Five SQL bugs invisible to hermetic fakes, instant against real Postgres | Real-database verification (R8) |

None of these is a unit-test gap. **Spend effort on the seams between changes,
not on more coverage inside them.**

**0.2 — A rule binds an agent only when a test refuses to break it.** R5 already
says this out loud ("each enforced by an existing test, not by prose"). Generalise
it: prose in a spec is advisory and will be violated by the next agent who does
not read that paragraph; a red build is not negotiable. This is why the
structural fences — the cascade map derived from the migrations, the tenancy
ratchets, the AST path-fence checks — are the highest-value tests in the tree.
They catch *other agents' future work*, which is the entire alignment problem.

That premise is now **R7**: an architectural rule must name the test that
enforces it, or be labelled advisory. "We should always…" is not a rule.

---

## 1. Environments and the test database

**We run four environments** (D55, board WS-38):

1. **Local hermetic** — the unit suite, no database. Fast, and where most tests
   live. ⚠️ Its fakes agree with whatever SQL they get (see §4).
2. **Local scratch Postgres** — `scripts/dev_db.sh` (§1.1), with the migration
   ladders applied. Prove a migration here before you trust it.
3. **Staging** — `https://dev.metorite.com`, on the dev box. It serves the
   `staging` ref, and that ref takes every merge. A nightly rebuild from an
   anonymised dump of production keeps it production-shaped, so it cannot
   drift.
4. **Production** runs on its own box. It applies the `release` ref. A slot run
   moves that ref three times a day, at 05:30, 13:30 and 21:30 UTC (D194). A
   manual dispatch moves it for a hotfix. The home box's timer starts each
   slot run with a nightly dispatch (D178).

`specs/staging_and_daily_release.md` owns staging and the daily release. Root
`CLAUDE.md` §4 says what to do after a merge. Test on staging before
production.

**Why staging is a nightly copy.** CI replays the migration ladder from zero.
That proves only that the ladder is *self-consistent*. It does not prove that
the next migration survives real data. Our failures came from real data:
148's cast, the tags migration's `LATERAL` scope error, and the People write
routes.

Staging applies each merged migration to a copy of production's data
before production takes it. That copy is practice item P-1.

### 1.1 The local loop — build against a real database, then deploy

**Owner directive, 2026-08-30.** Design locally against a real database. Prove
the process. Deploy after that.

**Run this first, before any work that touches SQL:**

```sh
bash scripts/dev_db.sh                       # start both scratch databases
eval "$(bash scripts/dev_db.sh --export)"    # set both DSNs in this shell
uv run pytest tests/unit/test_customer_console_sql.py -q
bash scripts/dev_db.sh --down                # throw them away
```

The script starts two containers. Both are throwaway. It applies the whole
Console ladder, and it prints the two DSNs.

It applies each migration through the container's own `psql`. A Windows box
carries no `psql`, and `scripts/apply_customer_console_migrations.sh` needs
one. On the main development machine that script cannot run at all.

**Why a command, and not the paragraph above it.** §1 described the
`mt-scratch` pattern for weeks. A pattern is a thing a person must remember.

**What we measured on 2026-08-30.** A local `pytest` over the 26 Console suites
gave **123 passed and 843 skipped**. Every skip said *"R8 requires a REAL
Postgres"*. The run looked green. It had proved about one test in eight. With
the two DSNs set, the same command gives **967 passed and 4 skipped**.

⚠️ **A skip is not a pass, and pytest prints both in the same grey.** In a
full-tree run the count sits under 4900 passes, where nobody reads it. So
`tests/conftest.py` now prints a block at the end of any run that skipped tests
while the DSNs are unset. It is **advisory** (R7). It prevents nothing, and CI
remains the fence.

**What this catches that CI does not.** CI replays the ladder from zero, so
every database it tests is small and clean. A scratch database that has run the
whole suite holds hundreds of rows.

On 2026-08-30 that difference found a live defect in one command.
`usage_by_org` uses a LEFT JOIN, so an organization with no usage appears. It
then orders by credits **descending** and caps the page. So the cap removes
the row the LEFT JOIN exists to include, and it removes that row first. Below
100 organizations nothing shows it. Dev, CI and production all agreed it was fine.

**Two rules follow.**

1. **Run the database suites before you push, not after.** The loop takes 80
   seconds. CI takes two and a half minutes, one push later.
2. **A test must assert on rows it created.** The defect above surfaced as a
   test that scanned every returned row for a zero. It passed on a fresh
   database and failed on a full one. That test reported on the database
   instead of on the function.

### 1.2 Run the whole app locally — one command

**Owner request, 2026-10-30 (Metorite task LD-1).** Find the UI and function
bugs on this machine, before a push. Staging is the last check, not the first.

```sh
bash scripts/dev_up.sh           # start. The first run builds and seeds (about 2 min).
bash scripts/dev_up.sh --check   # health, then the tenant-isolation check
bash scripts/dev_up.sh --stop    # stop the app. --down also removes the data.
```

It gives each worktree its own stack on `127.0.0.1`, with its own ports:

- **Postgres** with the whole ladder and the RLS promotion. The gateway
  connects as `acb_app` (`NOSUPERUSER NOBYPASSRLS`), as in production.
- **Redis**, of its own.
- **The gateway** with `--reload`, and **the web app** (`next dev`), signed in
  as `dev@fracktal.in`.
- **Two made-up organizations:** Kite & Co. (yours) and Northwind. The seed
  checks that neither one sees the other, through the API and through an
  unbound read as `acb_app`.

**Nothing leaves the machine.** Each process starts with a clean environment
(`env -i`). The script refuses a checkout with a `.env`, because the gateway
reads `.env` itself. Production's switches (`scripts/dev_up/prod_flags.env`)
come across with the outbound ones held off, by the pattern of
`scripts/staging_refresh.sh`. Fence: `tests/unit/test_dev_up.py`.

**What it cannot test.** The stack holds no keys. Mail sync, sending, WhatsApp,
CRM sync, OAuth sign-in and model calls do not work. The owner retired staging
(D144), so §1.3.2 says where those get their check.

**Why the ladder runs inside the container.** Measured 2026-10-30 on the
Windows PC: `apply_migrations.sh` from Git Bash took 515 s for 310 files and
89 s with nothing to apply. Inside the container it took 38 s and 6 s.

⚠️ **Never point `pytest` at this database.** The suites reset what they touch
(§1.1). `--env` prints `DEV_UP_*` names, never `DATABASE_URL`, for that reason.

### 1.3 Preview, approve, gate, merge: the owner loop (D145)

**Owner directive, 2026-10-31 (Metorite task LD-7).** Show the owner a change
the moment it works. Run the full gates after the owner approves it. Move
production three times a day, at 05:30, 13:30 and 21:30 UTC (D194 supersedes
"once a day").

```text
build ──▶ preview ──▶ owner approves ──▶ local staging gate ──▶ merge ──▶ next release slot
            ▲               │
            └── change it ◀─┘
```

**1. Build with the stack up.** Start `bash scripts/dev_up.sh` in the worktree
when the slice starts, not when it ends. The gateway reloads and `next dev`
reloads, so the preview is ready when the code is.

**2. Preview.** When the slice works in the running app, send the owner one
message, in chat and on the task. It holds these four items:

- The URL of the page that shows the change, on `127.0.0.1`.
- What changed, in three lines or fewer.
- Where to click, step by step.
- For a UI change, the captures of the `visual-review` skill.

Then call `ask_on_task` with two options, **Approve** and **Change it**. In
the same step, push the branch and open the PR as a draft. GitHub's
`pr-check` then runs while the owner looks. It runs the tests that the diff can
affect (tier 1 of `specs/ci_velocity.md` §4, D176). The full suite runs on
`main` after the merge (tier 2). The R8 suites run there only after CIV-4b.

A slice with nothing to see skips the preview. Docs, tests, a script and a
refactor with no change in behaviour go straight to step 4.

**Small screen change (D200).** A small, low-risk change on an existing screen
skips the wait for approval. The slice is LOW or STANDARD, and it is one field,
one control, one line of text or a layout fix, or its whole change sits behind
a flag that is off. It still needs the captures, the verifier and the full
gates. After the merge, ask the owner "Looks right" or "Change it" on the task.
D200 lists the cases that do not qualify. When in doubt, preview first.

**A slice behind a flag that is off (D218, 2026-11-12).** It merges on green
with its captures and the `Visual review:` line, and does not wait here. After
the merge, ask the owner "Approve the flag flip" or "Change it" on the task.
The flag flips only after Approve. A new page, screen, navigation entry or flow
with no flag still waits for the preview before the merge. A HIGH slice keeps
its full loop (D218, `ci_velocity.md` §8).

**3. The owner answers.**

- **Approve:** go to step 4.
- **Change it:** build again, then send a new preview.
- **No answer in this session:** leave the PR as a draft and the question
  open. The next session finds the answer in `my_work` as `run_answered`.

**4. The gate (D218).** GitHub `pr-check` is the gate for every slice.
`bash scripts/ci_local.sh` is optional: run it when GitHub is down, or for an
early signal. Push when the verifier passes, not after each commit.

**The local staging gate (LD-9)** runs only for a slice that adds a migration
or changes the build path. Run `bash scripts/stage_local.sh` in the worktree.
The independent verifier, and the reviewer by tier, run beside it. The gate
tests the merge of the branch with today's `origin/main`, not the branch
alone. It runs four steps:

1. The full pr-check suite on a local CI lane (`scripts/ci_local.sh`). It is
   optional when `pr-check` already passed on the same head.
2. The production build: `next build` and `next start`, and the gateway with
   no reload, as `acb_app` under FORCE RLS.
3. The migration rehearsal. It restores last night's reduced copy at
   production's migration ledger. Then `scripts/apply_migrations.sh`, the
   runner that the deploy uses, applies the branch's new migrations.
4. Smoke: health, `/version`, the tenant-isolation check and the Playwright
   smoke set.

A pass posts the commit status `local-staging` = `success` on the exact head
commit, with a summary of the run. A failure posts `failure` and names the
step. Fence: `tests/unit/test_stage_local.py` (LD-9).

**5. Merge.** Run `bash scripts/premerge.sh > <log> 2>&1 && gh pr merge <n>
--squash`. From LD-10, `premerge.sh` refuses a migration or build-path slice
whose head commit has no `local-staging` pass. A new commit on the branch needs
a new pass. LD-10 names its fence.

**6. Production, three times a day (D194).** Each release slot, at 05:30,
13:30 and 21:30 UTC, ships the tip of `main` (§2 and
`specs/staging_and_daily_release.md` §4). An agent dispatches `deploy.yml`
only for a hotfix that the owner asks for, or when production is down. Report
a merge as "reaches production at the next release slot". After the release,
confirm `/version` `applied_sha`. The home box's timer starts each release
(D178). An agent never sets `nightly=true` by hand.

**Until LD-8 to LD-10 land.** The preview runs on the demo seed. The gate is
GitHub `pr-check`, and then `premerge.sh` (D218). Name the steps that ran in
the PR body.

**Steps 1 to 3 are advisory (R7).** No test makes a skipped preview fail. The
owner sees a missing preview, because no approval arrives.

#### 1.3.1 The production copy on this PC (LD-8a, LD-8b, LD-8c)

**Status, 2026-10-31:** 🟡 LD-8c is built (PR 1554), and it reaches
production at the 21:30 UTC release. LD-8b is built on its branch, and the
branch is now in a pull request. It was tested on a fake copy only. LD-8a is in
build. The spec-auditor gave GO-NARROWED and split LD-8 into three slices.

**Owner approval, 2026-10-31, in chat.** Production writes a reduced copy each
night (`scripts/staging_export.sh`). It holds the organizations in
`STAGING_EXPORT_SLUGS`, minimised by `infra/staging/minimise.sql`. It is about
83 MB, at `/var/lib/acb-staging-export/current/` on the production box. The
files are `root:acb`, mode 0640, so `ssh metorite` (user `acb`) reads them with
no sudo.

**Measured 2026-10-31, and the reason LD-8c goes first:**

- **The copy still holds secrets.** `minimise.sql` keeps `push_subscriptions`,
  the live push keys of real devices (`endpoint`, `p256dh`, `auth`). It also
  keeps `plugins.auth_config`, `whatsapp_member_links.code_hash` and
  `whatsapp_pending_acts.confirm_code_hash`. No script deletes them.
- **The nightly backup check fails.** On 2026-10-31 at 02:37 UTC, the restore
  in the verify container stopped: "memory required is 65 MB,
  maintenance_work_mem is 64 MB". The dump exists, but nobody proved it
  restorable. `staging_export.sh` runs only after a good verify, so no copy
  arrived.
- So **no copy comes to this PC before LD-8c lands and the next backup
  passes.** The owner approved a copy with its credentials blanked.

**LD-8c: AGENT-SAFE to build, HIGH to land (deploy path).** It ships at the
21:30 UTC release, which the home box starts (D178).

- `scripts/backup_db.sh` starts the verify container with
  `-c maintenance_work_mem=256MB`. Fence: `tests/unit/test_backup_deploy_wiring.py`.
- `minimise.sql` deletes `push_subscriptions` (with RE-9b), and blanks the
  three columns above. The plugin auth also leaves `plugins.manifest`. Each
  open WhatsApp act closes. Its step 6 name check also catches `p256dh`,
  `auth`, `code_hash`, any `token` and `encryption_key`. Fences:
  `tests/unit/test_staging_minimise_r8.py` (R8) and
  `tests/unit/test_staging_export.py`.
- Each restore script asks for 256 MB too (`restore_db.sh`,
  `staging_refresh.sh`). The production pooler ignores that request, so H-441
  holds the disaster-restore fix.
- Proof: the backup at 02:30 UTC on 2026-11-01 logs "restore verified", and a
  new `current` copy appears.

**LD-8a: `scripts/prod_copy.sh`. AGENT-SAFE, HIGH.**

- It pulls the copy over `ssh metorite` with `scp` or `ssh ... tar`, because
  Git Bash has no `rsync`. The target is `~/.metorite/prod-copy/<UTC date>/`.
- It limits the folder to the current user with `icacls`: no inheritance, one
  principal. NTFS ignores a POSIX mode, so the test checks the ACL.
- It checks `SHA256SUMS`. It takes the age from the remote `stat -c %Y` of
  `staging.dump`, prints it, and refuses a copy older than 36 hours.
- It keeps the 2 newest copies. It refuses a target inside a git work tree. It
  never prints the content of a file.
- Fence: `tests/unit/test_prod_copy.py`, with `ssh` replaced by a stub
  (`PROD_COPY_SSH`). It covers a bad checksum, a stale copy, the retention of
  2, and a target inside a git work tree.

**LD-8b: `bash scripts/dev_up.sh --data prod --as <email>`. AGENT-SAFE, HIGH.**

- The dump is `pg_dump` 17 (archive format 1.16). So this mode uses
  `pgvector/pgvector:pg17`. `.dev_up/state` records the data mode, and a switch
  needs `--reset`.
- It ports `scripts/staging_refresh.sh` steps 4 to 6 into the container, as one
  `bash -c` run: the roles from `roles.txt`, the schemas and extensions from
  `extensions.txt`, then `pg_restore --no-owner --no-acl` with the
  `SCHEMA - public` entry left out. A line `pg_restore: error` stops it.
- Before the gateway starts, it runs `DELETE FROM push_subscriptions`. This is
  a second defence after LD-8c.
- It skips the demo seed and the RLS promotion replay, because the copy holds
  both. It grants `acb_app` (NOSUPERUSER NOBYPASSRLS). Then
  `apply_migrations.sh` applies the branch's pending migrations.
- It reads production's switches through `DEV_UP_FLAGS_FILE=<copy>/flags.env`
  and the existing `flag_env`, so every held switch stays off. It never writes
  `scripts/dev_up/prod_flags.env`.
- `--as` names the member to sign in as. It must be an `app_user` with an
  active `org_membership`. With no `--as`, it refuses and lists the active
  members.
- The web app reads `DEV_SIGNED_IN_EMAIL` in `src/lib/gateway.ts`, in the
  `!isAuthEnabled` branch only. Production still returns no identity. Fence:
  `src/lib/authFailsClosed.test.ts`. The UI gate of
  `workbench/control_plane/AGENTS.md` applies.
- Fences: new cases in `tests/unit/test_dev_up.py`. R8 on the restored copy:
  `relforcerowsecurity` holds on the tenant tables, `push_subscriptions` has 0
  rows, and `acb_app` reads 0 rows of another organization.

**How LD-8b runs (as built).**

```sh
bash scripts/dev_up.sh --data prod --as <email>     # the newest copy
bash scripts/dev_up.sh --data prod --copy <DIR> --as <email>
bash scripts/dev_up.sh --data prod --db-only        # the database only
bash scripts/dev_up.sh --reset --data demo          # back to the demo seed
```

- The copy folders are in `~/.metorite/prod-copy/`. `PROD_COPY_DIR` changes
  the folder. A run takes the newest folder by name. A name that starts with
  `.` is a pull that did not finish, so the run ignores it.
- Before a restore, the script checks that all five files are there, and that
  `SHA256SUMS` names the four data files. Then it runs `sha256sum -c`.
- The pg17 container starts with `-c maintenance_work_mem=256MB`, after the
  image name. An IVFFlat index on a large mailbox needs more than the 64 MB
  default. A server flag reaches every session. `PGOPTIONS` is not used,
  because the Supabase session pooler ignores it (measured 2026-10-31).
- The stack keeps the copy that it restored, and its `flags.env`. A newer copy
  needs `--reset`.
- The member check reads the list of active members as `acb`, and matches
  `--as` in the script. The email never goes into the SQL.
- `--db-only` builds the database and stops, with no gateway and no web app.
  The R8 fence uses it.
- `--check` skips the tenant-isolation check in this mode, because that check
  reads the demo seed.
- `--refresh-flags` read the staging box, and the owner retired that box
  (D144). Now `--refresh-flags` exits 2 and names `--data prod`.
- R8 fence: `tests/unit/test_dev_up_prod_r8.py`. It builds a fake copy in a
  private `pgvector/pgvector:pg17` container, from the ladder and the RLS
  promotion, by the queries of `scripts/staging_export.sh`. It needs Docker.

**Non-goals.** No write to production. No change to the export schedule. The
copy is never used by CI, never goes into git, a log, a PR or a chat message,
and never leaves this PC.

**Verify with:**

```sh
uv run pytest tests/unit/test_prod_copy.py tests/unit/test_dev_up.py \
  tests/unit/test_staging_export.py tests/unit/test_staging_box.py \
  tests/unit/test_backup_deploy_wiring.py -q
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_staging_minimise_r8.py -q
cd workbench/control_plane && npx vitest run src/lib/authFailsClosed.test.ts
```

#### 1.3.2 What local staging cannot test

This PC holds no production keys. So it cannot test OAuth sign-in on the real
domain, mail, WhatsApp, CRM sync or model calls. It cannot test Caddy, the
systemd units or `deploy/*.sh` on a box either. The release's own smoke jobs
(`smoke` and `chat-smoke`) check those on production, and then the owner does.
A flag that needs a live check still waits for the owner (D144 point 6).

## 2. Release: deploy is not release

**Deploy** = the code is on the box. **Release** = a customer can see the
behaviour. Keep them separate, because that separation is what makes a bad merge
a non-event.

- **A merge deploys to staging, and production takes three releases a day**,
  at 05:30, 13:30 and 21:30 UTC (WS-38, D194,
  `specs/staging_and_daily_release.md`).
- **Ship dark.** Every feature lands behind a flag, defaulting OFF, and the flip
  is a deliberate act — for anything outward-facing or spend-bearing, an
  **owner-gated** one (`work_plan.md` §6). This is already our habit and it is
  the main reason merges rarely become incidents. Keep it.
- **Release in rings, not broadcasts.** Ring 0 is **us** — Fracktal is customer
  #0 and dogfooding is a free canary. Ring 1 is one friendly customer. Ring 2 is
  everyone.

  > ⚠️ **"Silo" is about PLACEMENT, never about architecture. We are
  > multi-tenant from customer #1.** `saas_multitenancy.md` §5.1 condition 2 is
  > unambiguous: *every silo runs the pooled schema, with `organization_id`
  > populated and **RLS enabled from day one**, even though the database holds
  > one tenant — a silo is a pooled deployment with N=1, and cutover is a data
  > move rather than a migration. Skipping this is what turns the bridge into a
  > rewrite.* There is **no phase in which single-tenant code is acceptable**;
  > D15 demoted the deployment from "the tenant boundary" to "a placement, a
  > priced tier", and R5 binds every PR regardless of customer count. What
  > changes at the 8–12 cutover is only N.

  While customers 1–5 are separately *placed*, the rings are physical and cost
  nothing — an advantage of the bridge rather than overhead to escape. After the
  pooled cutover a ring is a per-org feature flag, which **D25.2** already fixed
  as the only version-skew mechanism we support (one codebase, one schema, always
  latest).
- **The ring order matters more than the clock.** The clock is 05:30, 13:30
  and 21:30 UTC each day (D194), and the home box's timer starts it (D178). The order is "us first, then one, then all".

## 3. Migrations — the sharpest edge we have

**⚠️ The deploy applies migrations BEFORE restarting the services.** There is
therefore always a window in which the *old* code runs against the *new* schema.
With one internal user that is a shrug. With customers it is an outage. So:

**R6 — expand/contract, always.** A migration must be compatible with the code
that is *currently running*:

- Add columns **nullable with a default**; never `NOT NULL` in the same deploy as
  the code that fills them.
- **Never rename in place.** Add the new name, backfill, switch readers, drop the
  old one in a *later* release.
- A unique constraint or CHECK over existing data lands `NOT VALID` and is
  validated in a guarded block — migration 148 already does this correctly and is
  the reference (it quarantines duplicates rather than failing
  `CREATE UNIQUE INDEX` and blocking the deploy).
- The contract half (drop, tighten) is a **separate, later** migration. Two
  releases, never one.

**Rollback: we cannot, and should stop implying we can.** A forward-only numbered
ladder with no blue/green means recovery is **roll forward or restore from
backup** — not `git revert`. That is an acceptable choice at this scale, and it
makes two things non-negotiable before customer #1: an off-box backup copy, and a
deploy whose result we can actually verify (§6).

**Numbering.** Take the next free number **at build time**, never earlier (R1),
and re-check it at merge. Three collisions in two weeks is a signal about branch
lifetime (§5), not about carelessness.

**The hand-run promotion set** (`infra/postgres/generated/`) is applied by hand
against production in a maintenance window. It is therefore held to a higher
standard than a numbered migration, not a lower one: regenerate it in the PR that
adds a table, and let
`test_tenant_coverage.py::test_the_generated_set_on_disk_matches_the_tables_that_exist`
prove the artifact matches reality. That fence exists because the committed set
contained a statement that could not run.

## 4. Testing — what a test is worth when an agent wrote it

**An agent will always produce a passing test.** Coverage is therefore close to
meaningless as a signal here, and the suite's job changes: it must be *hard to
satisfy vacuously*.

- **Verified red first.** A test that has never failed has never been shown to
  test anything. Required for every fence and every bug fix.
- **Mutation testing** for anything touching money, auth, tenancy or an outward
  write: change the logic, watch the test go red, revert. We already do this
  ("N mutants red and reverted") more rigorously than most commercial teams —
  it is the correct answer to "the tests pass, but do they test anything?", and
  it is also how we found tests that were passing for the wrong reason.
- **R8 — verify SQL against a real database.** Hermetic fakes agree with whatever
  SQL they are handed, which is why five live bugs shipped green: a `CAST(:param
  AS timestamptz)` asyncpg refuses to encode, a fake matching `lower(col) =
  :param` against NULL (which SQL never does), an implicit-comma `LEFT JOIN`
  whose `ON` clause could not see its table. Any change whose subject is a query,
  a migration, or a predicate is verified against a real Postgres before it is
  believed.
- **Prefer structural fences to example tests** when the rule is general. A test
  that reads the whole tree and asserts an invariant ("every module declaring a
  route is imported", "the cascade map equals what the migrations declare",
  "no new engine outside the seam") defends against agents who have not read the
  spec — which is all future agents.
- **Keep the eval locks.** `propose()`, the capability ranking, and the golden
  cases are EVAL-LOCKED because an LLM-shaped "fix" can silently degrade quality
  in a way no assertion catches.
- **Fakes are a convenience, never the fence.** If the seam under test is
  mocked, the test proves the mock. (The attachment suite passed 25 green while
  every upload answered 422, because `record_activity` was monkeypatched.)

## 5. Dividing work between agents

**Partition by seam, not by feature size or file count.** Two agents may run in
parallel only when neither can touch the other's contract. When they must share
one — the work plan, the entitlement seam, a fence that enumerates tables —
**serialise them**. The merge cost exceeds the parallelism gain, and the failure
mode is not a conflict marker but two changes that are each correct and jointly
wrong (#399 × #403, twice in one day, both caught only at merge).

**Keep branches short and integrate into main constantly.** The branch that
carried WS-27 o–z lived for days and grew its own parallel tenancy thread, its
own migration numbers, and its own handover document. It merged, but only after a
human sat and reconciled it. **Long branches are the root cause behind the
renumber trap, the cross-PR fences, and the duplicated multi-tenancy design** —
three distinct symptoms, one cause.

**Cap work in flight.** The constraint is not agent capacity; it is integration
bandwidth, and that is one person. Three or four open branches is the ceiling. A
queue of six (2026-08-09) is above it. More machines do not raise this cap.
`docs/dev_capacity.md` sizes lanes, sub-agents and CI to the machines that we
have, inside this cap.

**The supervisor loop:** audit the spec (is it dispatchable?) → set the risk
tier → implement one narrowed slice → **independently** verify against the
acceptance criteria → adversarially review the diff (HIGH and STANDARD tiers,
not LOW) → open a PR → merge, watch the deploy and the `main` unit job, and
test on staging.
`.claude/commands/next-ticket.md` owns the steps and the tiers (owner decision,
2026-10-16). Locally, run `scripts/affected_tests.py`. pr-check runs the
tests that the diff can affect (`affected_tests.py --gate`), and the full
suite when the diff is FULL. `main` runs the full suite after the merge. The
tiers are in `specs/ci_velocity.md` §4 (D176). Two properties make it work:

1. **The verifier must not be the implementer**, and re-derives its facts from
   the code rather than trusting the write-up.
2. **The reviewer is adversarial by construction.** An agent asked "is this
   correct?" answers yes. An agent asked "find the input where this is wrong"
   finds it. Prompt for refutation, never for confirmation.

**Anchors are re-verified at dispatch, never trusted from authoring time** (the
§1 contract already says this; it is here because it is the most common way an
agent builds against a file that moved).

## 5.1 The harness travels with the repo (D29)

**The development philosophy is only real where it is installed.** Until
2026-08-10 `.claude/` was entirely gitignored, so every checkout that was not the
owner's laptop — **a cloud Claude instance, a fresh clone, a headless run, an
agent's own worktree** — ran with *no* `plan-guard`, *no* supervisor-worker agent
definitions and *no* `/next-ticket`. The owner-gate registry (§6 of the work
plan) was being enforced by a file that travelled by accident.

**Tracked, because it is doctrine in executable form:** `.claude/AGENTS.md` ·
`.claude/agents/` (spec-auditor, ws-implementer, ws-verifier, diff-reviewer) ·
`.claude/commands/` · `.claude/hooks/` (`plan-guard.mjs` + its test, `rtk-bash.sh`)
· `.claude/settings.json` (wires the hooks; contains no secrets).

**Still ignored, because it is local or derived:** `settings.local.json`
(per-machine permissions), `worktrees/` (ephemeral full checkouts),
`scheduled_tasks.lock`, `skill-observations/`, and `skills/` (third-party skill
bundles — tooling a developer installs, not project doctrine).

**Derived indexes are rebuilt, never committed.** The CodeGraph symbol index
(`.codegraph/`) stays ignored: it is a per-checkout SQLite database that would be
large, churn on every commit and conflict on every merge. Its *config*
(`.mcp.json`, `codegraph.json`) is tracked, so a fresh environment gets the tool
and builds its own index. **Anything derivable from the source is rebuilt on the
new machine; only the sources of truth are tracked.**

**The fence (R7):** `node .claude/hooks/plan-guard.test.mjs` runs blocking in
`pr-check`. Break the guard and CI is red — rather than the guard quietly
permitting everything on a machine nobody is watching.

## 6. Security once the users are not colleagues

The threat model changes completely at customer #1, and
`saas_multitenancy.md` §0.9 already states the conclusion correctly: **our agents
execute model-generated tool calls over content ingested from email and
WhatsApp** — content an attacker can author. Therefore:

- **Isolation must fail closed and be impossible to forget.** Row-level security
  returns zero rows when the tenant is unset; a hand-written
  `WHERE organization_id = ?` fails *open* the one time an agent forgets it, and
  that omission is invisible until it leaks. This is why the isolation budget
  belongs on the database and the execution plane, not in application predicates.
- **Anything irreversible or outward-facing is owner-gated** and an agent must
  refuse it **by name** (`work_plan.md` §6, enforced by `plan-guard.mjs`). Live
  credentials, member/role writes, enforcement flips, cutovers, production
  one-offs. This list gets longer with customers, not shorter.
- **Per-tenant credentials, never a deployment singleton** (MT-0d), and never a
  run's secret in process-global state (MT-0a).
- **The container/microVM tier stops being optional at the pooled cutover**
  (D16). Hold that line; "trusted colleagues" is exactly the premise selling
  externally retires.
- **Least-privilege applies to agents too**: read-only transports where reads
  suffice, method allow-lists, confirm-before-send fail-closed, and a path fence
  that is itself tested against synthetic sources so "the fence went blind" is a
  red test rather than a silent gap.

## 7. Definition of done, with customers

A change is done when **all** of these hold — not when CI is green:

1. Acceptance criteria in the owning spec are met, and the **verification
   commands in that spec were run** (not equivalents).
2. Any rule it introduces names its enforcing fence (R7).
3. Migrations follow expand/contract (R6) and were applied against a real
   database (R8).
4. It is behind a flag if it changes behaviour a customer can see.
5. **Delivery is verified by evidence, not by a green job** — the migration
   ledger lines, the deployed SHA, the log line. "The deploy job passed" has been
   wrong four times in one day.
6. The owning spec's status header is updated in the same PR (R4).

## 7a. Two-speed delivery: build first, harden when the feature is done

Owner directive, 2026-10-19. The guardrails cost more time than they saved.
We measured 70 red CI runs on 15-19 October. A process fence caused 29 of
them. A real bug in the PR caused 9. Each red run cost about 30 minutes.

**A feature now moves at two speeds.**

**1. Build.** One PR or many, until the feature works.

- The product tests block.
- The SAFETY fences block. They guard tenancy, auth, secrets, migrations,
  money, and the deploy and CI path.
- The PROCESS fences are advisory. They guard naming, docs, registries, the
  design system and source-string pins. CI shows a warning and goes on.
- STE lint is advisory. The hook reports an error and goes on. The
  hardening pass sets `STE_LINT_BLOCK=1`, and then it blocks.
- The agent loop is implement, then verify (`/next-ticket` step 1d). The
  adversarial review, the
  visual review, the board row, the spec header (R4) and new fence tests (R7)
  wait for the hardening pass.
- **Both run the change on the local stack** (§1.2, owner request 2026-10-30).
  The implementer checks each criterion in the running app before it reports.
  The verifier checks them again on its own run and quotes the evidence.
  Staging is then a smoke check, plus a full test of what the PC cannot run:
  the deploy path, real OAuth, real webhooks and syncs, and a migration on
  production-shaped data.
- **A small slice skips the spec audit** when its Metorite task states the
  acceptance criteria (`/next-ticket` step 2). A HIGH-tier slice, and the
  first slice of a spec, keep it.
- A HIGH-tier slice keeps the full loop: tenancy, RLS, auth, identity, a
  migration, money, credits, secrets, a member or role write, or the deploy
  path.
- **A UI diff is the exception for the look** (owner directive, 2026-10-27).
  In build mode the UI gate fences block, and a UI diff gets the four-capture
  visual review. It also gets item 8 of `diff-reviewer`, the UI checklist.
  The full adversarial review still follows the tier of the slice
  (`/next-ticket` step 1b). `workbench/control_plane/DESIGN_SYSTEM.md` §11
  holds the gate. The other process fences stay advisory.
- **The CI tiers (D176).** A PR runs lint, the SAFETY fences and the unit
  tests that its diff can affect. A FULL diff runs all of them. `main` runs
  the full suite in batches, and a red batch is fixed forward at once. The R8
  suites join that batch with CIV-4b, and CIV-3 merges only after CIV-4b.
  `specs/ci_velocity.md` §4 owns the tiers.

**2. Harden.** One PR when the feature works, labelled `harden`.

- The process fences block. Run `bash scripts/prepush.sh --harden` first.
- Run `diff-reviewer` on the WHOLE feature diff, not on each slice.
- Write the fence tests that R7 asks for, and the items for this feature in
  `project-docs/hardening_backlog.md`.
- Update the board row and the spec header.

**What never relaxes:** plan-guard, the safety fences, the R8 database
suites, the migration ladder replay, and the R1 migration-number guard. We
cannot roll back production, and every customer shares one database.

**The lists.** `tests/fences.json` sorts each static fence into `safety` or
`process`. A fence is `safety` when its failure would reach production or a
customer: a 404, a 422, a wrong write, a leak. A fence is `process` when it
guards naming, docs, registries, wording, the look or lockstep. A new static
fence goes into one list in the PR that adds it. If you are not sure, choose
`safety`. Fence: `tests/unit/test_fence_lists.py`.

**Before you push.** `bash scripts/prepush.sh` runs the fences and the
affected tests in a few minutes.

**The whole suite, on the home server or this PC.** GitHub `pr-check` is the
gate (D218), so this run is optional. Use it when GitHub is down, or for an
early signal. `bash scripts/ci_local.sh` runs every pr-check job on a preset
machine. The machine is preset, so a run
spends its time on tests, not on installs. The jobs come from
`.github/workflows/pr-check.yml` through `scripts/ci_pc/steps.py`, so the two
cannot drift.

- **Two lanes (task 1534).** The home server runs the suite as user `ci`
  (`ci@metorite-home`, port 2222, over Tailscale). This PC's `metorite-ci`
  WSL2 distro takes the run when the home server is busy or does not answer.
  So two suites can run at once. `--home` or `--pc` picks one lane. Each lane
  runs one suite at a time, behind a lock.
- **Setup.** This PC's lane: `bash scripts/ci_pc/setup.sh`. The home server:
  `scripts/home_server/remote_access.ps1`, then `wsl_setup.ps1`. That runs
  `setup.sh`, which presets the `ci` user with the same `bootstrap.sh`.
- It tests a COMMIT. Commit first.
- Neither lane can read the Windows drives or start a Windows program. The
  code reaches it through `git`, never through a mount.
- It is not the merge gate. pr-check on GitHub is.
- Each lane shares ONE WSL2 VM with the other distros of its machine. So it
  sizes its shards by free memory, caps each container, and refuses to start
  below 8 GB free. On 2026-10-20 an uncapped run hung the VM for every session.
- When WSL or Docker hangs, run `bash scripts/ci_pc/doctor.sh` from
  Windows. `--fix` repairs it in a known order: `wsl --shutdown`, then the
  WSL service (one UAC prompt), then Docker Desktop and only its own distro,
  then the containers that were running.
- Fence: `tests/unit/test_ci_pc.py`.

## 7b. Land before build (owner directive, 2026-11-26)

**Status: ACTIVE spec. No slice is built (verified against code
2026-11-26).** Each slice ships dark when it is built. Epic: WS-CIV task 1628.
Spec task: 4040 (LBB-0). Part A, the backlog itself, is task 4019 (lane-land)
and task 4039 (pc-land-core). `ci_velocity.md` points here.

**Owner directive, 2026-11-26 ~16:10 UTC:** "If work has already been done,
then we should try and see how to push it to production. There needs to be
something in our harness that protects us from making this kind of mistake
and ensures that previously done work is pushed to production."

**Evidence, 2026-11-26 15:52 UTC.** 124 pull requests were open. 94 were older
than 7 days, 58 conflicted with main, 50 had no CI result and 27 were red. Only
13 were ready. The lanes built faster than the mergers landed, and a task read
Done when its PR merged, or even when its PR only opened. Nothing made a lane
land its old work before it took new work.

**Owner decision, 2026-11-26 ~16:25 UTC:** "yes, turn on the claim gate too"
(rule 2, the claim gate, is approved, and its flip is approved after
verification).

### 7b.1 The six rules

Each rule names its fence (R7). Each slice ships dark behind its flag or its
mode, default off.

**Rule 1. Done means live.** A task is Done only when production runs its
merge commit. The deployed SHA (`GET /version`, `applied_sha`) must contain
that commit (`git merge-base --is-ancestor`). Until then the task stays in a
review status, or in progress when its project has no review status.
- The merger no longer sets Done at the merge. It posts "merged, not live:
  <sha>" on the task and leaves the status.
- `scripts/home_server/live_check.py` runs after each release slot (05:30,
  13:30, 21:30 UTC). It reads `/version` once, a read only, and lists each task
  whose merged PR is now live. The merger lane applies `set_task_status` Done
  from that list, until the box token (task 1819) lets the script write.
- It also lists each task that is Done with a PR whose merge commit is not
  live after two release slots. The merger lane moves each one back and says
  why.
- Mode: `LIVE_CHECK_MODE=off|dry|on` in the box unit. `dry` prints the list
  and writes nothing.
- Fence: `tests/unit/test_live_check.py` (an ancestor is live, a commit after
  the deployed SHA is not, `/version` with no answer moves nothing).

**Rule 2. Landing comes first (the claim gate).** §7b.2 holds the design.
- `my_work` lists the open PRs of the caller's agent label that are older
  than 24 hours, before any new task, with the reason `land_your_pr`.
- An agent's claim of a new task is refused while its member and agent label
  hold an open PR older than 48 hours that no merger has queued.
- Flags: `CLAIM_GATE_SHADOW_ORGS` (log only) and `CLAIM_GATE_ORGS` (refuse).
- Fences: §7b.2 "Fences".

**Rule 3. The landing sweeper.** A box timer runs every 2 hours over every
open PR: `scripts/home_server/landing_sweeper.py`.
- It skips a PR of author `ishaanpilar`, an `ishaan-*` branch, a draft, and a
  PR that anyone pushed to in the last 24 hours.
- A PR that is behind main and has no conflict: it merges main into the head
  branch with `gh api -X PUT repos/<repo>/pulls/<n>/update-branch`. That is a
  merge commit, never a rebase or a force-push. The new head starts
  pr-check.
- A PR whose pr-check is green on a head that holds today's main, and whose
  own diff has the same `git patch-id --stable` as a head with a
  `VERIFY: PASS <sha>` line: it hands the PR to the merge bot
  (`lane_handover.sh --pr`) when its tier is LOW or STANDARD. A HIGH PR gets
  a `REQUEUE` line for the box merger.
- A PR older than 48 hours that conflicts or is red: it writes one `LAND`
  line to `~/lanes/landing.tsv`, once for each head. lane-land files a "Land
  PR N" task, tag `landing`, for each new line, until the box token lets the
  sweeper file it.
- Mode: `LANDING_SWEEPER_MODE=off|dry|on` in the unit. `dry` writes the lines
  it would act on to `~/lanes/landing-sweeper.log` and changes nothing.
- Fence: `tests/unit/test_landing_sweeper.py` (each skip rule, update-branch
  only for a clean behind PR, a hand-over only on a patch-id match, one `LAND`
  line for each head).

**Rule 4. A fleet closes with zero orphans.** When a lane stops, each open PR
that it pushed has a disposition.
- A disposition is one of two. The PR is queued with a live merger: a
  `hand_over.sh` run or a `REQUEUE` line, after the last push. Or it is
  handed to a named landing owner: a `release_task` handoff or a `NOTE` that
  names the PR and the owner lane.
- "Released for other agents" is not a disposition.
- `.claude/hooks/stop-disposition.mjs` reads the transcript. It finds each
  PR that the session pushed or opened, and blocks the Stop once for each PR
  with no disposition. It names the PR and the two choices. It fails open. Like
  the rest of this hook, it runs only in a background job
  (`stop-disposition.mjs`, the `CLAUDE_JOB_DIR` marker).
- The box wind-down script is `~/lanes/winddown.sh` today, and it is not in
  the repo. LBB-4 moves it to `scripts/home_server/lanes/winddown.sh`. It then
  lists each open PR of each lane that it stops, and refuses to stop a lane
  with an orphan unless `--force-orphans`.
- Switch: `STOP_DISPOSITION_PRS=off|warn|block`, default `off`. The owner or
  metorite-78 sets `warn`, then `block`.
- Fences: `node .claude/hooks/stop-disposition.test.mjs` and
  `tests/unit/test_winddown.py`.

**Rule 5. Merge capacity follows build capacity.**
- The merge bot is the default for LOW and STANDARD PRs. A lane hands over
  with `scripts/hand_over.sh` and never merges by hand.
- A merge gate trusts a green pr-check on a head that holds today's main. That
  is the early exit of `premerge.sh` today.
- A head that is behind main gets the trial merge and the collision fences.
  It re-runs tests on the merge result only when the union adds a migration or
  touches an R8 suite (`affected_tests.py --r8`). Otherwise the clean trial
  merge and a green pr-check on the head decide.
- HIGH tier: the merge path. The change waits for PR 2407 (one time budget)
  and PR 2488 (the release freeze) to land first.
- Switch: `PREMERGE_MERGE_TEST=auto|1|0`, with `auto` the new default only
  after the owner approves the flip.
- Fence: `tests/unit/test_premerge.py` (a behind head with no migration and no
  R8 file runs no merge test, and one with either runs it).

**Rule 6. Visibility.** Built work that is not live is shown, and a breach
opens a fix task.
- The weekly delivery review (CIV-10, `.claude/commands/delivery-review.md`)
  measures `built_not_live`: each open PR over 24 hours, with its age. Its
  thresholds: more than 30 open PRs, or any PR older than 5 days. Each breach
  opens one fix task, as the other rows of that table do.
- My Day shows a "Built, not live" row in its "Needs you" list for a member
  whose lanes hold such PRs, from `pm_task_refs`. Flag:
  `BUILT_NOT_LIVE_ORGS`.
- Fences: `tests/unit/test_delivery_review.py` (the metric and its two
  thresholds) and `tests/unit/test_built_not_live_r8.py` (the My Day row).

### 7b.2 The claim gate (rule 2)

**What it is, and what it is not.** The gate guides agents that cooperate.
It is not a security control. It reads `pm_agent_runs.agent_label`, which is
display data (`agent_coordination.md` §3.7). A lane that changes its label
escapes the gate. The gate never opens, grants or shows anything. It only
refuses an agent's own new claim. `agent_coordination.md` §3.7 records this
reader in a dated amendment, and `tests/unit/test_agent_label_fence.py`
names `gateway/routes/projects/claim_gate.py` as the one module that may read
the label for a decision.

**The traps, and the answer to each.**

1. **Every lane pushes as GitHub user vjvarada.** A count by member refuses
   every lane, because 119 PRs are open. So the gate counts by agent label.
   A PR belongs to the agent label of its **owner run**: the run that held
   the task when the reference was added. §7b.8 defines it (amended
   2026-11-27, LBB-2b, task 4056). The first text said "the newest run on
   the task", and that gave a PR to a merger that only claimed and released
   its task. A NULL label binds the PR to claims that send no label.
2. **No deadlock.** The gate never refuses these claims:
   - a claim of a task that holds a `pull_request` reference with `merged_at`
     NULL and `state` NULL, `open` or `draft`. That covers the task of the old
     PR, a "Land PR N" task, and a merger's claim.
   - a claim of a task with the tag `landing`. Tags compare with no case
     (migration 156).
   - a claim of a task where the caller already has an open run: the same
     member, connection and session key, the test of `_OPEN_RUN_SQL`
     (`agent_runs.py`). That test reads no label.
   - a claim by a person. A person's claim sends no label and opens no agent
     run (`agent_runs.py`, the person branch of the claim).
3. **"Queued" lives on the box.** The gateway never reads a box file. The
   lane tells the server: after it runs `hand_over.sh` or writes a `REQUEUE`
   line, it calls `add_task_reference` again on the same PR URL with
   `landing: "queued"`. LBB-2 makes `hand_over.sh` print that call as its last
   line. A `queued` mark expires after 24 hours, so a PR that the bot sent
   BACK counts again by the next day. A lane that re-queues sends the mark
   again. At each merge, the merger sends `landing: "gone"`. LBB-1 owns that
step in `scripts/home_server/lanes/lane-merge.md`, because LBB-1 also removes
the Done at the merge.
4. **The refusal is for an agent.** It names each PR, its age in hours, its
   task number and one next step.

**The three doors.** SW-45 checks the claim scope at three doors, and the
gate runs at the same three, right after `check_claim_scope`:
- `claim_task` (`gateway/routes/projects/tasks.py`).
- `set_assignees` when an agent adds itself (`tasks.py`, the call of
  `check_claim_scope`).
- `start_agent_run` (`gateway/routes/projects/agent_runs.py`, the call of
  `check_claim_scope`).

**The record.** One expand-only migration (R6) adds three nullable columns to
`pm_task_refs`. No new table. The table is already tenant-scoped with FORCE
row level security (255), so the columns inherit it (R5).
- `landing_state TEXT NULL`, CHECK `landing_state IN ('queued', 'parked',
  'gone')`.
- `landing_at TIMESTAMPTZ NULL` and `landing_by TEXT NULL`.
- `add_task_reference` (the edge tool and `POST` of
  `gateway/routes/projects/refs.py`) takes an optional `landing` field.
  `insert_ref` changes from `ON CONFLICT ... DO NOTHING` to an update of the
  three columns, only when `landing` is sent. `landing_by` is `actor(user)`.
  The change writes one `link` activity with `ref_action: landing`.
- A repeat of the same URL with no `landing` changes nothing, as today.
- Who may write `landing`: each caller who may add a reference to the task
  today. No new permission.
- `gone` means merged or closed, said by the lane or the merger. It never
  expires. A `parked` mark exempts the PR only while its task's status
  category is backlog or todo.

**A counted PR.** The gate counts a reference when each of these is true:
- Its kind is `pull_request`, and its `state` is NULL, `open` or `draft`.
- Its `merged_at` is NULL.
- Its task is visible to the caller, not archived, and not in a done or
  cancelled status. Its project is open. (A PR on a task that the lane cannot
  write is never counted, because the lane could not clear it.)
- It is older than 48 hours from `created_at`, or 24 hours for `my_work`.
- Its owner run (trap 1, §7b.8) has the caller's member and the caller's
  agent label. A PR with no owner run counts for nobody.
- Its `landing_state` is NULL. Or it is `queued`, with `landing_at` older
  than 24 hours. Or it is `parked`, on a task that is not in backlog or todo.
- No run on its task has the status `awaiting` (a run that waits on a
  person, with an open question).

**The refusal.** A 422 through `_refuse_claim` (`tasks.py`), with
`detail.error = "land_your_pr"`. `_refuse_claim` gains an optional `prs`
list for the body. The body lists up to 5 counted PRs, oldest
first. Each entry has the PR number (parsed from `ref`), the age in hours,
the task number and a `next` code. It also has the URL, but only when the
URL matches `^https://github\.com/[^/]+/[^/]+/pull/[0-9]+$`. The edge adds
`land_your_pr` to its coded claim refusals (`_CLAIM_CODED`, `tools.py`),
because `coded_statuses=(422, 403)` already carries a 422. It also adds
`land_your_pr` to `_OUTSIDE_CODED`, which `set_assignees` uses, and 422 to the
coded statuses of `start_agent_run`. So all three doors show the same text. It renders a fixed
text from these values, never text that a person wrote. The `next` code:
- `answer` when `AGENT_RUN_ANSWER_WAITS_SQL` (`filters.py`) finds an
  answered run on the task. "Answered" is a derived state, not a status: "A person
  answered on task T. Read the answer and act on it."
- `land` otherwise: "Merge main into PR N, verify it, and queue it. Then call
  add_task_reference on task T with landing queued."
- Each entry also gets one line: "If PR N is merged or closed, call
  add_task_reference on task T with landing gone."

**The `my_work` row.** `my_work` takes an optional `agent_label`, as
`claim_task` does, and joins `_TAKES_LABEL` in
`tests/unit/test_agent_label_fence.py`. With no label, it lists the PRs bound
to no label.
- A new gateway read lists the counted PRs over 24 hours for the caller and
  label: `GET /projects/agent-work/landing` in
  `gateway/routes/projects/claim_gate.py`. The pair
  `("GET", "/projects/agent-work/landing")` joins `MCP_ROUTES`
  (`packages/acb_auth/acb_auth/agent_oauth.py`).
- The edge adds `land_your_pr` to `MY_WORK_REASONS` (`tools.py`). The row
  keys gain `pr_number`, `pr_url` (same pattern as above, else null) and
  `age_hours`.
- Position: after the waiting runs (`run_queued`, `run_answered`,
  `run_stopping`), and before `held_by_you`, `check_due`, `assigned` and
  `own_app_free`.
- Either flag shows the row.

**Flags.** Two `Settings` fields: `claim_gate_shadow_orgs` and
`claim_gate_orgs`, in `acb_common/flag_registry.py` when PR 2380 is on main,
else in `settings.py`. Both are reloadable `*_orgs` lists with `*`, read by
one reader each in `claim_gate.py`, with entries in `docs/flag_reload.md`.
- `CLAIM_GATE_SHADOW_ORGS` logs `claim.gate would_refuse` with the PR numbers,
  and refuses nothing.
- `CLAIM_GATE_ORGS` refuses, and logs `claim.gate refused`.
- Neither gates money or a real person.

**Who it binds.** Every member, every lane, and the lanes of each person. The
PR body of the build tells Ishaan, because his lanes are bound too.

**The flip.** Not by the builder. Before the shadow flip, metorite-78 checks
three things (added 2026-11-27, §7b.8, advisory: no test fences a flip):
- LBB-2b (§7b.8) is on main and in a release.
- `agent_runs_enabled` is on for Hathi Labs in production. With it off, no run
  exists, and the gate counts nothing.
- A production count of runs in a terminal status with `ended_at` NULL is
  zero, or each one is named. The owner rule reads `ended_at`.

Before `CLAIM_GATE_ORGS`, LBB-2c (task 4064) is on main. After the merge and a
release, metorite-78
sets `CLAIM_GATE_SHADOW_ORGS` for Hathi Labs (`02cf1f16`) and reads one day of
`claim.gate` lines. Then it sets `CLAIM_GATE_ORGS` for Hathi Labs, and after
one more day, `*`. Each step is gate `enforcement-flip`. The grant of
`.claude/OWNER_GRANTS.md` ends on 2026-11-30, so a step after that date needs
a new grant line or the owner's own hand.

**Accepted risk.** Every session with no label shares one bucket. So a
session with no label can be refused for a PR of another session with no
label. metorite-78 measures this on the shadow day, before `CLAIM_GATE_ORGS`
goes on.

**Fences.**
- `tests/unit/test_claim_gate_r8.py`, on a real database: each rule of "A
  counted PR", each exemption of trap 2, the three doors, the shadow flag
  refuses nothing, and the `landing` write.
- `tests/unit/test_claim_gate_edge.py`: the refusal text for each `next` code
  at each of the three doors, a URL that fails the pattern is left out, and
  the `my_work` row and its position.
- `tests/unit/test_agent_label_fence.py`: `claim_gate.py` is the one allowed
  decision reader of the label. No SQL constant outside `claim_gate.py` names
  `agent_label` in a `WHERE` clause.
- `tests/unit/test_flag_reload.py`: one reader test per flag, in the shape of
  the `_READERS` table (PR 2380) or of the per-flag tests on main.

**Done when.**
1. With both flags off, every existing claim, assignee and run-start test
   passes unchanged, and no claim reads the new query.
2. With `CLAIM_GATE_ORGS` on, an agent claim of a new task is refused (422
   `land_your_pr`) for a label that holds a counted PR, and the text names
   each PR, its age, its task and the next step.
3. Each exemption of trap 2 passes with the flag on.
4. `my_work` lists the `land_your_pr` row in its place.
5. `hand_over.sh` prints the `add_task_reference ... landing queued` line.

**Verify with.**

```
eval "$(bash scripts/dev_db.sh --export)"
PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest -p no:cacheprovider \
  tests/unit/test_claim_gate_r8.py tests/unit/test_claim_gate_edge.py \
  tests/unit/test_agent_label_fence.py tests/unit/test_mcp_edge_claim.py \
  tests/unit/test_agent_claim_scope_r8.py tests/unit/test_task_refs_route.py \
  tests/unit/test_flag_reload.py tests/unit/test_mcp_edge_coverage.py
```

**Tier.** HIGH (the claim path is next to auth, and a migration): the
spec-auditor, an Opus builder, an independent verifier, an Opus diff-review
and R8 tests on a real database.

### 7b.3 Slices

Each slice row names its anchors and its gate. The build is AGENT-SAFE for
each slice. Each flip or `on` mode is OWNER-GATE.

| Order | Rung | Slice | Task | Tier | Flag or mode | Anchors | Gate |
|---|---|---|---|---|---|---|---|
| 1 | 1 | LBB-2 the claim gate and the `land_your_pr` row | 4041 | HIGH | `CLAIM_GATE_SHADOW_ORGS`, `CLAIM_GATE_ORGS` | §7b.2 | build AGENT-SAFE, flip `enforcement-flip` |
| 1b | 1 | LBB-2b the owner run of a PR (§7b.8) | 4056 | HIGH | none: it acts under the LBB-2 flags | `claim_gate.py` `_counted_sql`, `scripts/hand_over.sh` | build AGENT-SAFE after LBB-2 is on main. On main before the shadow flip |
| 1c | 1 | LBB-2c the `set_assignees` door sends the label | 4064 | HIGH | none: it acts under the LBB-2 flags | the edge tool `set_assignees`, `tasks.py` | spec first. On main before `CLAIM_GATE_ORGS` |
| 2 | 1 | LBB-4 zero orphans at a stop | 4042 | STANDARD | `STOP_DISPOSITION_PRS` | `.claude/hooks/stop-disposition.mjs`, `~/lanes/winddown.sh` | build AGENT-SAFE, `block` by the owner |
| 3 | 1 | LBB-3a the landing sweeper, a dry report (§7b.5) | 4043 | STANDARD | `LANDING_SWEEPER_MODE=dry` | `scripts/home_server/merge_bot.sh`, `scripts/hand_over.sh`, `scripts/release_freeze.sh` | build AGENT-SAFE, the timer and `dry` are `deploy` |
| 3b | 1 | LBB-3b the sweeper's actions | (to make) | HIGH (merge path) | `LANDING_SWEEPER_MODE=on` | §7b.5 rules | NO-GO until metorite-78 takes the two decisions of §7b.5 |
| 4 | 2 | LBB-1 done means live | 4044 | STANDARD | `LIVE_CHECK_MODE` | `GET /version`, the merger steps of `lane-merge.md` | build AGENT-SAFE, `on` is `deploy` |
| 5 | 2 | LBB-6 built, not live, in My Day and the review | 4045 | STANDARD, UI | `BUILT_NOT_LIVE_ORGS` | `.claude/commands/delivery-review.md`, My Day "Needs you" (WS-58) | build AGENT-SAFE, D145 preview before the flip |
| 6 | 2 | LBB-5 merge capacity follows build capacity | 4046 | HIGH (merge path) | `PREMERGE_MERGE_TEST=auto` | `scripts/premerge.sh` step 4 | build AGENT-SAFE, the default flip is the owner's |

Rung 1 stops new orphans and lands the old ones. Rung 2 shows the owner what
is live and makes merges cheaper. The demo of rung 1: on production, a lane
with a PR older than 48 hours sees `land_your_pr` first in `my_work`, and its
claim of a new task is refused with the PR named.

Each slice other than LBB-2 gets its own "Done when" and "Verify with" lines
in this section when its builder starts, and its own spec-auditor run.

**Out of scope.** Closing a PR (the owner closes, from the close list of task
4019). A change to `TOOL_PACKS`. Any flag flip by a builder.

### 7b.4 LBB-4: zero orphans at a stop (task 4042)

**Status: built (dark, `STOP_DISPOSITION_PRS` unset = off). Disposition 3 stays off until PR 2898 is on main.** Tier STANDARD (a hook and a box script, no server
code). It needs no `guard-write` grant: `stop-children.mjs` already calls the
hook, so `.claude/settings.json` does not change.

**Narrowed scope.** The box wind-down script cannot see which PR belongs to
which lane: it holds no Metorite credential, and every lane pushes as one
GitHub user. So the box part changes the message that the script sends, and
the Stop hook does the check in each session.

**The hook.** `.claude/hooks/stop-disposition.mjs` gains `openPrs(entries)`.
It reads the same transcript entries as `openClaims`: `tool_use` blocks, and
the `tool_result` blocks that answer them, with `is_error`. It never reads an
exit code, and it orders events by the `tool_use` index, as `openClaims` does.

- **A PR of the session** is a number N that one of these gives:
  - a Bash `tool_use` whose command matches `gh pr create`, with a result that
    is not an error and holds `github.com/<owner>/<repo>/pull/N`.
  - an `add_task_reference` `tool_use` whose `url` is a pull request URL with N.
- **A push to PR N** is a Bash `tool_use` whose command matches `git push`,
  with a result that is not an error, and that names the head branch of PR N.
  The hook learns that branch only from the transcript: the `--head` value of
  `gh pr create`, or the `headRefName` in a result of `gh pr view N`. With
  neither, a push proves nothing for N.
- **A disposition of PR N** is one of these, at an index after the last push
  to N (or after N first appears, with no push):
  1. A Bash `tool_use` that runs `hand_over.sh N <task> <tier> <note>`, with a
     result that is not an error and holds the line
     `handed over: PR N (task ...` that `lane_handover.sh` prints. This means
     queued with the merge bot, not merged.
  2. A Bash `tool_use` whose command holds `REQUEUE`, `merge-queue.tsv` and N as
     a word, with a result that is not an error. The line format is
     `REQUEUE<TAB>pr<TAB>task<TAB>lane<TAB>note`, as in
     `printf 'REQUEUE\t%s\t%s\t%s\t%s\n' N T lane 'note' >> ~/lanes/merge-queue.tsv`.
     The box merger lane reads these lines for a HIGH PR (its lane notes,
     `~/lanes/lane-merge-*.txt`, 2026-11-26). The merge bot does not read
     them. `lane-merge.md` (~53) and `lane_handover.sh` (~35) still call this
     file history. That text is stale, and task 4042 notes it for a fix.
  3. An `add_task_reference` `tool_use` on the URL of N with `landing` set to
     `queued`, `parked` or `gone`. This disposition exists only once PR 2898
     (LBB-2) is on main. Before that, it is not counted.
  4. A `release_task` `tool_use` whose handoff `next` holds `PR N` and a lane
     name (`lane-` or `pc-` followed by letters, digits or dashes).
  5. A result of `gh pr view N` that is not an error and shows the state
     `MERGED` or `CLOSED`.
- A bare `release_task` with no lane is not a disposition ("released for
  other agents").
- **The switch.** `STOP_DISPOSITION=off` and the background marker
  (`CLAUDE_JOB_DIR`) apply first, as today. Then `STOP_DISPOSITION_PRS`:
  `off` or unset (the default) checks nothing. `warn` writes the list to
  stderr, and the Stop goes on. `block` blocks the Stop once
  (`stop_hook_active` true passes).
- **The signature.** `checkDisposition` returns `{ block, warn }`, each a
  message or null. `main` in `stop-children.mjs` returns code 2 with `block`,
  and prints `warn` to stderr with code 0. The existing tests change to read
  `.block`.
- **The message** names each PR and the two choices (queue it with a live
  merger, or hand it to a named landing lane). It ends with
  "To turn this check off: STOP_DISPOSITION_PRS=off".
- **It fails open.** A transcript that the hook cannot parse gives no PR and
  no block.

**The wind-down message.** The box script `~/lanes/winddown.sh` is not in the
repo. The lander puts its text in the task. LBB-4 adds
`scripts/home_server/lanes/winddown.sh`: the same `wake()` loop, and the lane
session ids come as arguments, not as fixed lines. Its message drops "queue a
PR only if it is ready and green". It says: "For each open PR that you
pushed, queue it with a live merger (hand_over.sh or a REQUEUE line), or hand
it to a named landing lane in a NOTE and in the release_task handoff." The
box copy moves to the repo copy by hand (AGENT-SAFE for the lane that owns the
box script).

**Done when** (each with a test in `.claude/hooks/stop-disposition.test.mjs`,
or in `tests/unit/test_winddown.py` for item 5):
1. With `STOP_DISPOSITION_PRS` unset, `checkDisposition` gives `block` null for
   a transcript with an open PR, and every existing test still passes.
2. With `block`, a transcript that ran `gh pr create` for PR N and ends with a
   bare `release_task` gives a `block` message that names PR N.
3. Each of the five dispositions clears PR N. A `git push` to the head branch
   of N after the disposition brings N back.
4. With `warn`, `main` prints the list to stderr and returns code 0.
5. `scripts/home_server/lanes/winddown.sh` holds the new message, no fixed
   session id (no match for `[0-9a-f]{8}` as a `wake` argument in the file),
   and passes `bash -n`.

**Verify with.**

```
node .claude/hooks/stop-disposition.test.mjs
node .claude/hooks/stop-children.test.mjs
PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest -p no:cacheprovider \
  tests/unit/test_winddown.py tests/unit/test_task_routine.py
```

**Gate.** The build is AGENT-SAFE. `warn` and then `block` on the box lanes
are gate `enforcement-flip` (a dated grant or the owner). `warn` comes first,
then `block` after one day with no false block.

### 7b.5 LBB-3: the landing sweeper (task 4043)

**Status: LBB-3a built (dry only).** LBB-3b is not built. The audit of 2026-11-27 found that the sweeper's
actions are unsafe until two decisions are taken, so LBB-3 has two parts.
- **LBB-3a (this build): a dry report.** The sweeper reads every open PR and
  writes what it WOULD do to its own file. It calls no GitHub write, writes
  no queue line, and hands nothing over. Tier STANDARD.
- **LBB-3b (later, HIGH): the actions.** It waits on two decisions by
  metorite-78 or the owner:
  1. Whether `~/lanes/merge-queue.tsv` is the HIGH queue again. The box
     merger lane reads it today (its lane notes), but `lane-merge.md`,
     `lane_handover.sh`, `merge_bot.sh` and `docs/home_box.md` say nothing
     reads it.
  2. A limit on the GitHub runs that updates may spend.
  The rules below are the rules that LBB-3b will act on. LBB-3a reports them,
  so one day of its report shows what LBB-3b would do.

**What runs.** `scripts/home_server/landing_sweeper.py` (Python standard
library, plus `gh` and `git`). The units
`scripts/home_server/metorite-landing-sweeper.service` and `.timer` run it
every 2 hours, in the shape of `metorite-answer-watcher.service`:
`Type=oneshot`, `EnvironmentFile=-%h/.config/metorite/landing-sweeper.env`,
`ExecStart=/usr/bin/python3 %h/.local/lib/metorite/landing_sweeper.py` (an
install copies the script there, as for the answer watcher),
`TimeoutStartSec=900`. The script keeps its own clone,
`~/lanes/sweeper-src`, and makes it with `git clone` when it is missing. It
runs `gh` from that clone.

**The mode.** `LANDING_SWEEPER_MODE` from the env file. A missing file or
`off` (the default): the script exits 0 and calls nothing. `dry`: it writes
its report. LBB-3a has no `on`: any other value is read as `off`, and logs
one line that says so.

**The report.** One line for each open PR in each run, appended to
`~/lanes/landing-sweeper.tsv` (only the sweeper writes this file, and
lane-land reads it):
`<UTC time><TAB><N><TAB><head sha><TAB><would><TAB><reason>`.
`would` is one of `SKIP`, `UPDATE`, `HANDOVER`, `REQUEUE`, `LAND` or `WAIT`.

**What it reads.**
- `gh pr list --state open --limit 300 --json number,author,headRefName,headRefOid,baseRefName,isDraft,isCrossRepository,mergeable,title,body,createdAt`.
- For each PR not skipped by the name rules: the `committedDate` of its last
  commit (`gh pr view N --json commits`), and its checks (`gh pr checks N`),
  read with the newest-row rule of `PY_CHECKS` in `merge_bot.sh` and its
  list of required checks.
- The box queues: `~/lanes/merge-bot-queue.tsv`, its result log
  `~/lanes/merge-bot.log`, and `~/lanes/merge-queue.tsv`.
- Its clone: each run fetches `main` and `refs/pull/N/head` for each PR that
  rule 5 reaches. A short `<sha>` resolves only from those objects. Else the
  PR gets `SKIP` with reason `error verify sha`.
- `scripts/release_freeze.sh` (exit 75 means a freeze window).

**The rules, first match wins.**
1. `SKIP`: the author is `ishaanpilar`; the head branch starts with `ishaan-`
   or `auto-revert/`, or matches `PROTECTED_HEAD_RE` of `merge_bot.sh`; a
   draft; a base other than `main`; a fork; the head is the base of another
   open PR; `mergeable` is `UNKNOWN`; or the last commit is less than 24 hours
   old ("pushed" in rule 3 means the `committedDate` of the last commit).
2. `WAIT`: the PR is held by a merger. That is one of these:
   - its hand-overs in `~/lanes/merge-bot-queue.tsv` outnumber its DONE and
     BACK lines in `~/lanes/merge-bot.log`, and it has no DONE (the count rule
     of `read_script` in `hand_over.sh`);
   - any `REQUEUE` line for N in `~/lanes/merge-queue.tsv`, while N is open. A
     real line: `REQUEUE<TAB>2487<TAB>3590<TAB>box-loop<TAB>CI red cause found: ...`
     (keyword, PR, task, lane, note).
   Also `WAIT`, for every rule below, while `release_freeze.sh` exits 75 or 1
   (a freeze, or an answer it cannot read).
3. `LAND`: `mergeable` is `CONFLICTING`, or a required check is red, and the
   PR is older than 48 hours. The reason names the conflict or the red checks.
4. `UPDATE`: the head does not hold main
   (`git merge-base --is-ancestor origin/main <head>` fails in the clone),
   and no required check is green on the head. A head that is green but
   behind is not updated: the merger merges it (lane-merge.md "do not merge
   main into a green branch").
5. `HANDOVER` or `REQUEUE`: every required check is green, the body has the
   line `VERIFY: PASS <sha>` (regex `^VERIFY: PASS ([0-9a-f]{7,40})`, the last
   such line wins), and the own diff of the head has the same
   `git patch-id --stable` as the own diff of `<sha>`. The own diff of a
   commit C is `git diff $(git merge-base origin/main C) C`.
   - `HANDOVER` only when `~/lanes/merge-bot-queue.tsv` holds an earlier
     hand-over of this PR by a lane at the same patch-id (so a person's
     approval step ran once). Else `REQUEUE`, so the merger lane checks the
     loop.
   - The tier comes from a body line `Tier: <LOW|STANDARD|HIGH>`, else from
     the title (`(HIGH`, `(STANDARD` or `(LOW`). With neither, the tier is
     HIGH, on purpose: most PRs name no tier, and HIGH goes to a person.
     HIGH is always `REQUEUE`.
6. `LAND` with reason `no verify sha`: green, older than 48 hours, and no
   `VERIFY: PASS` line.
7. Else `SKIP` with reason `nothing to do`.

**Test seams.** `LANDING_SWEEPER_GH` (the `gh` command), `LANDING_SWEEPER_FREEZE_CMD`
(the freeze check), `LANDING_SWEEPER_LANES` (the folder of the queue files and
the report) and `LANDING_SWEEPER_SRC` (the clone).

**Fails safe.** A `gh` or `git` call that fails gives that PR a `SKIP` line with
reason `error <step>`. An answer that does not parse ends the run with exit 0
and one log line.

**Done when** (each with a test in `tests/unit/test_landing_sweeper.py`, with
a fake `gh` that records every call, a temp git repo for the ancestry and the
patch-ids, and temp queue files):
1. `off`, a missing env file, or any value other than `dry`: no `gh` call.
2. `dry`: one report line for each open PR, and no `gh` call other than the
   reads above (no `api -X`, no `pr merge`, no `pr edit`, no `pr comment`),
   and no write to a queue file.
3. Each rule above, in its order, with one test for each `would` value and
   each skip reason.
4. A freeze (`release_freeze.sh` exit 75, through a fake) turns every action
   into `WAIT`.
5. A different patch-id gives no `HANDOVER` or `REQUEUE`.
6. The units: the timer runs every 2 hours, the service reads the env file
   with a `-`, and holds no secret.

**Verify with.**

```
PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest -p no:cacheprovider \
  tests/unit/test_landing_sweeper.py
uv run ruff check scripts/home_server/landing_sweeper.py tests/unit/test_landing_sweeper.py
```

**Rollout (not in the build).** Install the units on the box and set
`LANDING_SWEEPER_MODE=dry`: gate `deploy`. Read one day of
`~/lanes/landing-sweeper.tsv`, then take the two decisions above for LBB-3b.

### 7b.6 LBB-1: done means live (task 4044)

**Status: LBB-1a built (report only). LBB-1b not built (verified against code 2026-11-27).** Rung 1. The demo:
the owner reads `~/lanes/live-check.tsv` on the box and sees, for each merge
of the last three days, whether production runs it. LBB-1 has two parts.
- **LBB-1a (this build): a report.** After each release slot, a box script
  lists each recent merge and says whether production runs it. It writes only
  its own file. Tier STANDARD. The build is AGENT-SAFE.
- **LBB-1b (later): the board.** The merger lane moves a task to Done only
  when the report says LIVE, and leaves a "merged, not live" comment before
  that. This changes step f of `scripts/home_server/lanes/lane-merge.md`.
  metorite-78 makes that change after one day of the report. A script cannot
  write the board yet: the box has no Metorite credential (task 1819).

**What runs.** `scripts/home_server/live_check.py` (Python standard library,
plus `git`). The units `scripts/home_server/metorite-live-check.service` and
`.timer` run it at 06:00, 14:00 and 22:00 UTC, 30 minutes after each slot, in
the shape of `metorite-answer-watcher.service`:
`EnvironmentFile=-%h/.config/metorite/live-check.env`,
`ExecStart=/usr/bin/python3 %h/.local/lib/metorite/live_check.py`,
`Type=oneshot`, `TimeoutStartSec=300`. The timer ships disabled, as the
answer-watcher timer does.

**The env file** (mode 0600, no secret). Its full key set:
- `LIVE_CHECK_MODE`: a missing file or `off` (the default) exits 0 with no
  call. `dry` writes the report. Any other value reads as `off` and logs one
  line to stderr, which journald keeps.
- `LIVE_CHECK_GATEWAY_URL`: the production gateway, with no default. When it
  is missing, the run writes one `UNKNOWN` line and exits 0.

**What it reads.**
- `GET <LIVE_CHECK_GATEWAY_URL>/version`, a read with no credential, as
  `scripts/ci_deploy_reach.sh` does. The deployed SHA is `applied_sha` only:
  `sha` names the gateway checkout, and a failed web build could then read
  LIVE. A null `applied_sha` or a failed call gives one `UNKNOWN` line and
  exit 0. The value may be short, so the script resolves it with
  `git rev-parse` in its clone.
- The merges, from two logs. It ignores every other line type:
  - `~/lanes/merge-bot.log`: the line `DONE <pr> <sha>`, split on spaces, with
    the full 40-character merge SHA. Its task is `-`.
  - `~/lanes/merge-queue.log`: the line `DONE<TAB><pr><TAB><sha><TAB>(...)`,
    split on tabs. The task comes from the regex `task (\d+)` in the last
    field, else `-`.
  - One report line for each (pr, sha) in each run, even when both logs name
    it.
- Its clone `~/lanes/live-check-src`. Each run fetches `main` and the
  deployed SHA. The script makes the clone with `git clone` when it is
  missing.

**The time of a merge** is the committer date of its SHA in the clone. A
merge is in the report when that date is within 72 hours of now. A SHA that
the clone cannot fetch has no date: it gives `UNKNOWN` and is always
written.

**The states, for each merge.**
- `LIVE`: `git merge-base --is-ancestor <merge> <deployed>` succeeds.
- `WAITING`: not live, and fewer than two slot instants lie in the interval
  (merge time, now].
- `STUCK`: not live, and two or more slot instants lie in that interval. The
  slot instants are 05:30, 13:30 and 21:30 UTC each day. The script counts
  them in epoch UTC seconds, so midnight needs no special case.
- `UNKNOWN`: the merge SHA or the deployed SHA cannot be fetched or resolved.

A slot ships `green/main` (deploy.yml, CIV-30), not the tip of `main`. So a
merge that came after `green/main` stays unreleased through a slot. The
states count the slots that ran, not the slots that could ship this merge. A
merge after `green/main` can be `STUCK`, and that is correct: it is built
work that is not live.

**The report.** One line for each merge in each run, appended to
`~/lanes/live-check.tsv`:
`<UTC time><TAB><pr><TAB><merge sha><TAB><task or -><TAB><state><TAB><deployed sha or ->`.
A run-level `UNKNOWN` line (a null `applied_sha`, a failed read or a missing
URL) holds `-` in the pr, merge SHA and task columns. Its time is now, in UTC.

**Test seams** (read from the environment; the env file sets only the two keys
above). `LIVE_CHECK_VERSION_CMD` (the `/version` read),
`LIVE_CHECK_LANES` (the folder of the logs and the report),
`LIVE_CHECK_SRC` (the clone), `LIVE_CHECK_NOW` (now, epoch seconds).

**Fence (R7).** `tests/unit/test_live_check.py` holds the rules of this
section. No rule here is advisory.

**Done when** (each with a test in `tests/unit/test_live_check.py`, with a
temp git repo and temp log files):
1. `off`, a missing env file, or another value: no read and no write.
2. `dry`: one report line for each (pr, sha) of both log shapes, with the
   task read from the queue shape, and none for a merge older than 72 hours.
3. Each state. An ancestor is `LIVE`. A merge after the deployed SHA is
   `WAITING` with one slot instant in its interval, and `STUCK` with two (one
   test crosses 21:30 and then 05:30). A SHA that cannot be fetched is
   `UNKNOWN`.
4. A `/version` with a null `applied_sha`, a failed read, or a missing URL
   gives one `UNKNOWN` line and exit 0.
5. The unit text: the timer's `OnCalendar` lines are 06:00, 14:00 and 22:00
   UTC. The service has `EnvironmentFile=-` and holds no secret. The test
   parses the files, because no systemd runs in a unit test.

**Verify with.**

```
PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest -p no:cacheprovider \
  tests/unit/test_live_check.py
uv run ruff check scripts/home_server/live_check.py tests/unit/test_live_check.py
```

**Rollout (not in the build).** Install and enable the units, and set
`LIVE_CHECK_MODE=dry` with the production gateway URL: gate `deploy`. After
one day, metorite-78 changes lane-merge.md step f (LBB-1b).

### 7b.7 LBB-6: visibility (task 4045)

**Status: LBB-6a built (the review metric). LBB-6b not built: the My Day row,
dark behind `BUILT_NOT_LIVE_ORGS`, with a D145 owner preview before the flip.**

**Done when** (LBB-6a):
1. `scripts/delivery_metrics.py` gives the key `built_not_live`: `open_prs`
   (open PRs that are not drafts), `over_24h` (each open PR older than 24
   hours: `number`, `title`, `age_days`, oldest first) and `oldest_days`. It
   reuses the paging of `_old_prs`. A failing `gh` call gives `null` and an
   `errors` entry.
2. `.claude/commands/delivery-review.md` lists `built_not_live` in its metric
   list. It adds two threshold rows: `built_not_live.open_prs` over 30, and
   `built_not_live.oldest_days` over 5. Each breach opens one fix task.
3. Tests in `tests/unit/test_delivery_metrics.py` cover the key (a draft is
   not counted, a PR of 23 hours is not in `over_24h`, the order), the failing
   call, and the command file that names the key.
4. This section holds the status above.

**Fence.** `tests/unit/test_delivery_metrics.py`.

**Verify with.**

```
PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest -p no:cacheprovider   tests/unit/test_delivery_metrics.py
```

### 7b.8 LBB-2b: the owner run of a PR (task 4056)

**Status.** Spec, 2026-11-27. The facts below were checked against the code of
PR 2898 at 145d99c98a. Every anchor of this section is in that PR, and none is
on main yet. The code waits until PR 2898 is on main. **It must merge before
anyone sets `CLAIM_GATE_SHADOW_ORGS`** (the flip list of §7b.2). The shadow
day cannot show this defect, because the gate logs nothing for a PR that it
does not count.

**The defect (review P2 of PR 2898).** The first rule bound a PR to the newest
run on its task, in any status. Lane L hands over PR N and releases the task.
The merger claims the task, comments and releases it. Its run is now the
newest. When the `queued` mark expires, PR N counts for the merger and not
for L. L claims new work freely. The merger's `my_work` then tells it to merge
main into PR N, and `lane-merge.md` step d forbids that.

**The rule.** The owner run of a PR reference `f` is the `pm_agent_runs` row
`n` with each of these:
- `n.task_id IS NOT NULL` and `n.task_id = f.task_id`.
- `n.created_at <= f.created_at`.
- `n.ended_at IS NULL`, or `n.ended_at >= f.created_at`.

When two rows qualify, the row with the highest `created_at` wins, then the
highest `id`. The PR belongs to the member and the agent label that the owner
run holds when the gate runs. A run that starts after the reference is added
is never the owner. The rule reads two timestamps that each row already
holds, so it needs no migration and no backfill.

**The facts that the rule stands on.**
- `insert_ref` (`gateway/routes/projects/refs.py`) is the one insert of
  `pm_task_refs`. Both of its statements keep `created_at`: `ON CONFLICT DO
  NOTHING`, or an update of the three `landing` columns only. So a repeat of
  the same URL never moves the owner.
- The GitHub App inserts references too. The webhook calls
  `link_pull_request` (`gateway/routes/projects/github_autolink.py`) for a PR
  on an `mt-` branch, at each event of `AUTOLINK_ACTIONS` (`webhook.py`):
  opened, reopened, edited, synchronize and ready_for_review. That calls
  `insert_ref` with `via = 'github'`. The first link sets `created_at`, and
  that is most often the time the PR opened. A lane opens its PR inside its
  run, so the owner is that run, and the lane's later add changes nothing.
  When the first link comes on a later event, the owner is the run that holds
  the task then. That can be a merger, as in case 6.
- A release and a finish set `ended_at`. A run that waits or is in progress
  has `ended_at` NULL.

**Who is bound, case by case.** Each case is one R8 test.
1. **A merger after a hand-over.** L opens PR N in run A (the webhook or L
   adds the reference) and releases. The merger claims (run B) and releases.
   The owner is A, so PR N counts for L and never for the merger.
2. **The owning lane after a BACK.** L claims again (run C) to fix PR N. The
   owner stays A, with the same label, so nothing changes for L.
3. **A landing lane that adopts the PR.** Lane D claims the task and adds the
   same URL again, with or without `landing`. The reference keeps its
   `created_at`, so the owner stays A, and the PR stays bound to L. D's
   `queued` mark exempts it for 24 hours.
4. **A reference with no owner run.** A person adds it outside a run, a text
   link (`via = 'text'`) or the webhook (`via = 'github'`) adds it while no
   run holds the task, or the task had no run then. No row qualifies, and the
   PR counts for nobody.
5. **The references that exist on the flip day.** They bind by the same rule.
   A reference that was added inside a run binds to that run. The rest fall
   under case 4.
6. **A new reference inside a merger's own run.** A merger that opens its own
   PR, or adds a new URL, in its run B owns that PR. That is correct: it is
   the merger's work.

**Accepted, in writing.** Each item is a property of the runs, not of this
rule.
- **The label is read when the gate runs.** A claim that adopts an open run
  with no session key gives that run the claimer's label (`_set_run_label`,
  `agent_runs.py`). The PR then moves with the run to the adopter, as the run
  does. A resume or a reopen clears the label, and a reopen sets `ended_at`
  NULL again. Then the PR binds to the no-label bucket until a claim labels
  the run again.
- **An adopted PR stays with the lane that opened it** (case 3). If that lane
  has stopped, the PR counts for a label that claims nothing.
- **A dead lane's run keeps `ended_at` NULL.** The sweeps end runs only on
  closed tasks. So that run owns each later reference on its task until a
  takeover ends it.
- **A tie in one transaction goes to the run.** Both columns default to
  `now()`, and the rule uses `<=`.
- **A reopen widens the window.** A reopened run's window covers its idle gap
  too. So a reference added in that gap binds to the reopened run.

**What the gate cannot see.** Cases 3 and 4, and a dead lane, can leave an
open PR that no live lane is bound to. The gate does not report it. It is a
known blind spot. Two reports list open PRs whatever their owner, and neither
says why a PR is unbound:
- the landing sweeper report (LBB-3a, §7b.5, `~/lanes/landing-sweeper.tsv`),
  after its units are installed (gate `deploy`);
- the weekly `built_not_live` metric (LBB-6a, §7b.7), which leaves drafts out.

**The `hand_over.sh` line (review P3).** LBB-2 adds a last line to
`hand_over.sh`, and that line holds the placeholder `"<the task_id of task
N>"`. A lane that copies it makes a call that fails, so the PR stays
unmarked. The line changes to say what to fill in: "Fill in the task id:
call search_tasks with N, then call add_task_reference with that id, the PR
URL and landing queued." It prints the real PR URL. The script calls no MCP.

**Out of scope, to LBB-2c (task 4064, row 1c of §7b.3).**
- The `set_assignees` door checks the no-label bucket, because the `PUT` sends
  no label (review P3). The fix is a new edge parameter, or a skip for a
  labelled session. The second changes the three doors that the owner
  approved.
- The `add_task_reference` description says that an unmarked PR "can block
  your next claim" in every org. That is false while the flags are off.

**Fences.** Two test files fence this slice. The flip order has no test.
- `tests/unit/test_claim_gate_r8.py`, on a real database:
  - one test for each of cases 1 to 4 and case 6, with a reference of `via =
    'github'` in case 1 and in case 4;
  - the old "newest run" case turned around: a run that starts after the
    reference never owns it;
  - each fixture run in a terminal status has `ended_at` set, as production
    has.
- `tests/unit/test_hand_over.py`: the last line holds no `<` and `>`
  placeholder, it names `search_tasks`, and it holds the real PR URL.
- The mutation that names the rule: drop `n.created_at <= f.created_at` from
  the owner read, and the case 1 test turns red.
- The PR rewrites the two places that still state the newest-run rule: the
  docstring of `claim_gate.py` and the mutation C2 at the top of
  `tests/unit/test_claim_gate_r8.py`.
- The flip order ("LBB-2b before the shadow flip") is advisory. No test can
  fence a flag flip. The flip list of §7b.2 carries it.

**Done when.**
1. A PR that a merger claimed and released after the hand-over counts for the
   lane that opened it, and not for the merger.
2. Cases 2, 3, 4 and 6 give the owners above.
3. Every LBB-2 test passes. A fixture that makes a run after its PR
   reference, or a terminal run with `ended_at` NULL, changes to the
   production shape. The PR names each fixture that changed, and the reason.
4. `hand_over.sh` prints a line with no placeholder and with the real PR URL.

**Verify with.**

```
eval "$(bash scripts/dev_db.sh --export)"
PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest -p no:cacheprovider \
  tests/unit/test_claim_gate_r8.py tests/unit/test_claim_gate_edge.py \
  tests/unit/test_agent_label_fence.py tests/unit/test_mcp_edge_claim.py \
  tests/unit/test_agent_claim_scope_r8.py tests/unit/test_task_refs_route.py \
  tests/unit/test_flag_reload.py tests/unit/test_mcp_edge_coverage.py \
  tests/unit/test_hand_over.py
uv run ruff check apps/services/gateway/gateway/routes/projects/claim_gate.py \
  tests/unit/test_claim_gate_r8.py tests/unit/test_hand_over.py
```

**Tier.** HIGH (the claim path): the spec-auditor, a builder, an independent
verifier, an Opus diff-review and R8 tests on a real database.

## 8. What to fix before customer #1 — in order

This list names them so that nobody finds them again from zero. Each belongs
to an existing board row.

Rows 1 and 3 are built. `GET /version` serves the deployed SHA
(`tests/unit/test_build_info.py`). Staging applies each migration to a nightly
copy of production first (§1). The board row holds the state of the others.

| # | Item | Why it is a business risk, not tech debt | Home |
|---|---|---|---|
| 1 | **SHA in `/health`** | Without it "deployed" is a guess; four deploys were blessed while shipping nothing | WS-25 |
| 2 | **`BACKUP_REMOTE`** — an off-box copy | One copy on the same provider account is not a backup; losing the account loses the customer's data | BO-23 / `backup_and_restore.md` §4.2 (owner-deferred 2026-08-05 — **revisit at customer #1**) |
| 3 | **Migration rehearsal against a prod-shaped restore** (P-1, §1) | The ladder-from-zero replay cannot see the failures we actually get | WS-5 (CI gates) |
| 4 | **Promote and rehearse the RLS set** — ⚠️ **a hard gate, not a nice-to-have** | §5.1 condition 2 requires RLS **enabled from day one on customer #1's silo**, so "the policies are still un-promoted" and "we have a paying customer" cannot both be true. It is hand-applied in a window, and until 2026-08-10 it contained a statement that could not run | WS-29 (MT-1b) |
| 5 | **`run_lifecycle_sweep` tenant binding** | Sweeps every tenant's roots with no predicate, on a path H2 never reaches | WS-29 MT-1d |

---

## 9. What this document is not

It is not a substitute for the board (`work_plan.md` owns order and gates), not a
substitute for an owning spec (they own scope and acceptance), and not a licence
to refactor: an agent may **not** rewrite existing code to conform to §3 or §4
without a ticket. These rules bind **new and changed** work. Where the tree
already violates them, that is a finding for the board, not a mandate to fix in
passing.
