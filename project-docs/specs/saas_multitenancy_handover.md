# Multi-tenancy handover — execution runbook for an agent with database access

**Status:** 🟢 **In execution — H1 scratch gate PASSED 2026-08-09; H3 REHEARSED on
scratch 2026-08-22; H6 SLICES 1 + 3a + ORPHAN-CLOSURE + 3b (READ CUTOVER, behind `IDENTITY_CUTOVER`
default OFF) + 4-EXPAND (RBAC RE-KEY) SHIPPED (dark) 2026-08-22** (see
H1's result block and the H3 REHEARSAL RESULT block; the two-org isolation fixture + brick
characterization landed as `tests/unit/test_h3_rls_promotion_rehearsal.py`, and the app_user
sign-in brick is now written up as an OWNER DECISION in §H3.2; **the H6 dark slices** = the
identity-shadow dual-write + catch-up backfill (migration 182), the forward status mirror +
reconcile (migration 183, D48), the orphan-closure — purge now deletes the shadow and the
invite/approve mirror moved post-commit — **slice 3b, the read cutover** (`resolve_identity`'s
tenant-discovery read moves to the RLS-EXEMPT `user_identity ⋈ org_membership` (active-only);
`deps._with_resolved_access` binds BEFORE the bound role leg, which now runs GUC-bound via
`tenant_session`; all behind `IDENTITY_CUTOVER`, default OFF = byte-identical to today) — and the
**RBAC re-key EXPAND** (migration 184: nullable `user_identity_id` on the three RBAC tables,
backfilled via the lower(email) bridge, dual-written on the five Python RBAC INSERTs + bridged
GUC-bound at mirror-time for the sixth, `provision_org_owner`). All fenced by
`tests/unit/test_h6_identity_shadow.py` + `tests/unit/test_h6_rbac_rekey.py` +
`test_h3_rls_promotion_rehearsal.py` — **no read moved while the flag is OFF; the flag-ON
identity leg resolves an active member GREEN unbound under phase-4 RLS while the flag-OFF
app_user read still bricks RED**, see §H6. Building the read cutover (slice 3b) + the per-module
RBAC read cutovers DARK is AGENT-SAFE per D48; only the `IDENTITY_CUTOVER` flip, the CONTRACT drop
of the old `user_id`, H3 phase-4 promotion, and running the prod backfill remain OWNER-GATE, not
enacted**) ·
**Created:** 2026-08-08 ·
**Owner:** vjvarada ·
⚠️ **Updated 2026-08-19: a ticket was minted that this runbook has no H-slot for —
`saas_multitenancy.md` §11 **MT-1j · Tenant-side organization provisioning**.** It is not
a reordering: MT-1j is buildable and R8-testable **now**, independently of H1–H8, but
**executing it against a real second organization waits on H3** (D43-C — before the RLS
promotion, org #2 has no database-level isolation). Its slice 6 lands **before H6**, which
rewrites the same two upserts. H6's anchors below were re-measured in the same pass.
**Board row:** WS-29 · **Parent:** [`saas_multitenancy.md`](saas_multitenancy.md) ·
**Shapes:** [`saas_multitenancy_implementation.md`](saas_multitenancy_implementation.md)

> **What this document is.** Everything built so far was built in an environment with **no
> database and no Docker daemon**. That is why three migrations have never been applied and
> the RLS phases sit outside the deploy sequence. This is the runbook for an agent that
> *does* have a database — it owns **order, gates and verification**, not architecture.
>
> **If this doc and the parent disagree, the parent is right and this is stale.**

---

## 0. Paste this to your agent first

```
You are executing WS-29 (multi-tenancy) on Metorite.

READ IN THIS ORDER, FULLY, BEFORE TOUCHING ANYTHING:
  1. project-docs/specs/saas_multitenancy_handover.md   (this runbook — order + gates)
  2. project-docs/specs/saas_multitenancy.md            (§0.1, §0.9, §1, §5.1, §11)
  3. project-docs/specs/saas_multitenancy_implementation.md  (SQL + seam shapes)
  4. AGENTS.md at the repo root, then every AGENTS.md on the path to each file you edit

NON-NEGOTIABLE:
- Work H1 → H8 IN ORDER. Each has a GATE that must pass before the next starts.
- H3 (RLS phase 4) is a CLIFF. If you apply it before H2 is complete and verified,
  every query in the product returns zero rows. Do not reorder it.
- Never run `ruff check .` — this tree has ~1983 pre-existing errors. Lint only the
  files you touched, and compare against HEAD to prove you introduced none.
- Never run `pytest tests/unit/` as a whole directory — it hangs against a live DB.
  Name files.
- Verify every anchor (file:line) with grep before editing. This corpus has shipped
  stale anchors repeatedly; two were found stale during this workstream alone.
- A test that SKIPS is not a test that PASSED. Use -v or -rs and read the skips.
- Do not git push to any branch other than claude/command-center-multitenant-a30fgy.

Start with H1. Report the GATE result before moving on.
```

---

## 1. State of the tree, measured 2026-08-08

**Built and pushed** (branch `claude/command-center-multitenant-a30fgy`):

| Ticket | State | Note |
|---|---|---|
| MT-0a per-run credentials | ✅ | ContextVar replaces process-global `os.environ` |
| MT-0b self-mutation containment | ✅ | migration **157** — scratch-applied + verified 2026-08-09 (H1); prod = PR #404 |
| MT-0c-1 no raw-SQL agent tools | ✅ | `query_history` rewritten; ratchet added |
| MT-0d per-org provider keys | ✅ | migration **158** — scratch-applied + verified 2026-08-09 (H1); prod = PR #404 |
| MT-1a control plane | ◐ | migration **159** — scratch-applied + verified 2026-08-09 (H1); identity cutover NOT done |
| MT-1b RLS | ◐ | generated into `infra/postgres/generated/`, **never applied** (H3's act, after H2 — the scratch DB `mt-scratch` is its test target) |
| MT-1c binding seam | ◐ | `tenant_session()` built; **561 call sites unconverted** |
| MT-1e Redis wrapper | ◐ | built; **~58 key sites unconverted** |
| MT-1i leak sites | ✅ | five predicates derived; one DB-backed criterion open |
| MT-1j org provisioning | 🔲 | **minted 2026-08-19**, not built. Six slices; no H-slot — build it in parallel, **execute it after H3** (D43-C) |
| MT-0c-2 container tier | ⏸ | OWNER-GATE, parked by D16 until the pooled cutover |

**Test baseline** — everything below should still pass when you finish:

```bash
uv run pytest \
  tests/unit/test_integration_env_scoping.py \
  tests/unit/test_mt0b_self_mutation_containment.py \
  tests/unit/test_mt0c1_no_raw_sql_agent_tools.py \
  tests/unit/test_mt0d_per_org_credentials.py \
  tests/unit/test_tenant_placement.py \
  tests/unit/test_tenant_session.py \
  tests/unit/test_tenant_coverage.py \
  tests/unit/test_tenant_redis.py \
  tests/unit/test_db_engine_seam.py \
  tests/unit/test_psycopg_seam.py \
  tests/unit/test_org_access_control.py \
  tests/unit/test_app_grants.py -v -rs
```

⚠️ **Two of these SKIP without a database and are the whole reason you exist:**
`test_tenant_coverage.py::test_live_catalog_has_column_force_and_policy` and
`::test_app_role_cannot_bypass_rls`. **A green run that skips them proves the SQL was
written, not that it works.**

---

## 2. The correction that changes the plan's size

**The conversion surface is 561 sites across 138 files, not the "~200" the parent spec
says.** Measured 2026-08-08:

```bash
grep -rhoE "await _?get_db\(\)" --include=*.py apps packages | wc -l   # 561
grep -rlE  "await _?get_db\(\)" --include=*.py apps packages | wc -l   # 138
```

The undercount happened because the dominant idiom is the **aliased** import —
`from gateway.db import get_db as _get_db`, then `await _get_db()` (441 sites) — and a
grep for `get_db()` alone misses it. Heaviest files: `routes/tasks/items.py` (23),
`routes/notes/meeting_bot.py` (18), `routes/email/automation/runner.py` (15),
`rules.py` (15), `senders.py` (14), `routes/tasks/calendar.py` (13).

**H2 is therefore the long pole of this whole workstream**, not H3.

---

## 3. The gate sequence

```
H1  migrations 157/158/159   ──►  H2  convert 561 call sites  ──►  H3  RLS phases 1-4
                                                                        │
H4  background jobs  ◄──────────────────────────────────────────────────┘
H5  Redis conversion        (parallel with H4)
H6  identity cutover        (parallel with H4)
H7  subdomain resolution    (after H6)
H8  blobs + partitioning    (last; needs a window)
```

**The one ordering that is not negotiable: H2 before H3.** Everything else can move.

---

## H1 · Apply and verify migrations 157, 158, 159 · 🟢 AGENT-SAFE

Three migrations have **never touched a database**. They were statically checked only:
all four auto-generated constraint names confirmed against `schema.generated.sql`, and no
FK anywhere references the primary keys being re-pointed.

**Do:**
1. Restore a **production dump into a scratch database**. Not an empty one — an empty
   database hides every backfill and constraint problem these migrations can have.
2. Apply `157`, `158`, `159` in order.
3. Verify:
   ```sql
   -- 157
   SELECT slug, first_party FROM organization;              -- default => true, others false
   -- 158
   \d provider_keys                                          -- PK (organization_id, provider)
   SELECT count(*) FROM provider_keys WHERE organization_id IS NULL;   -- 0
   \d model_config  \d mcp_servers                           -- composite PKs
   SELECT indexdef FROM pg_indexes WHERE indexname='plugins_org_name_key';
   -- 159
   SELECT count(*) FROM tenant_placement;                    -- = count(*) FROM organization
   SELECT count(*) FROM user_identity;                       -- = distinct lower(email) in app_user
   SELECT count(*) FROM org_membership;
   ```
4. Then apply them to production via the normal deploy.

**Done when:** all three applied to a restored copy with the queries above returning the
stated values, **and** the baseline test set still passes.

> ⚠️ **Known risk, stated so you look for it.** 158 does
> `ALTER TABLE ... DROP CONSTRAINT ... ADD PRIMARY KEY` on four tables. If any of them
> holds duplicate `(organization_id, <key>)` rows the ADD fails and the migration aborts
> mid-file. Check for duplicates **before** applying, not after.
>
> ⚠️ 159's seed reads `app_user.display_name`. An earlier draft read `u.name` and would
> have failed on apply — that bug was caught by reading `schema.generated.sql:1579`, not by
> testing. **Assume there is another one and look.**

**GATE:** production is on 157/158/159 and the baseline suite is green.

> **H1 RESULT (2026-08-09) — scratch half PASSED; prod half is the owner's merge.**
> Executed on a local Docker scratch (`mt-scratch`, pgvector:pg16, 127.0.0.1:5433 —
> plan-guard makes every VPS/deploy path OWNER-GATE, so "restored production dump"
> became "full-ladder replica": 00→156 replayed clean, 154 files, zero failures, plus a
> synthetic seed exercising every backfill path — case-duplicate emails, NULL org, empty
> email, all four re-keyed credential tables). 157/158/159 applied clean, re-ran
> idempotently, and **every verify query below returned the stated value**. Baseline:
> **213 passed / 2 skipped** — after fixing a real defect this gate flushed out:
> `import litellm` runs `load_dotenv()` at import and planted a dev `.env`'s
> `DATABASE_URL` mid-collection, un-skipping the two DB gates against an unmigrated
> local DB (`tests/conftest.py` launch snapshot, commit `817596b5`). Red-check done:
> pointed at the migrated scratch, both H3 gates un-skip and fail on their real
> assertions. The prophesied "another `u.name`-style bug" was not found — but
> `schema.generated.sql` itself is **stale since ~migration 113**, so the static checks
> above were made against a stale artifact; the scratch replay is the real reference.
> **Remaining for the GATE, owner's acts:** (1) optionally repeat the apply on a scratch
> restored from the *production* dump (runbook in PR #404's description); (2) merge
> **PR #404** — deploy auto-applies via the ledger; verify by the three
> `- 15N_*.sql ... ok` deploy-log lines, never the job conclusion; (3) run the verify
> queries below against prod. H2 dispatches only after that.

---

## H2 · Convert 561 session-acquisition sites to `tenant_session()` · 🟢 AGENT-SAFE · **the long pole**

> ### ◐◐ H2 NEARLY DONE 2026-08-10 (same day) — nine packages converted, 111 sites left, ALL classified
>
> Two waves of parallel slice-agents converted the rest of the gateway's request
> handlers on top of the Projects slice below: **notes** (61 converted/33 left),
> **whatsapp** (38/14), **email** (98/27), **tasks** (73/6), **workflows** (26/17),
> **apps** (32/6), **crm** (28/3), **people** (4/0), **admin** (23/0).
> `H2_BASELINE_ELSEWHERE` banked stepwise **494 → 111**, and every one of the 111
> remaining sites is CLASSIFIED in place: a `# H4:` (background consumer — scheduler,
> pipeline, sink, broker handler, `asyncio.create_task`) or `# H4/H6:`
> (service-identity route — provider webhook, bridge secret, hook token, OAuth
> callback) marker naming the tenant-derivation source for the conversion H4/H6 owns.
> Zero-remainder packages are pinned by the parametrized
> `test_converted_packages_stay_converted` (projects/crm/people/admin +
> `H2_EXEMPT_FILES`); whatsapp and apps hold at exact per-file counts
> (`H2_WHATSAPP_EXEMPT_SITES`, `H2_APPS_EXEMPT_SITES`).
>
> **Live verification (R8):** every wave ended with a real-Postgres smoke — wave 1
> and wave 2 integration smokes drove one converted read per package under a bound
> GUC on a migrated scratch cluster; the tasks slice additionally proved actual
> FORCE-RLS org-A/org-B row isolation; unbound sessions raise `TenantUnbound`
> everywhere.
>
> **What "H2 done" still needs:** the 111 H4/H6 sites are NOT H2's — they convert
> with explicit tenants in H4 (jobs/consumers) and H6 (service-identity + identity
> cutover). H2's own remaining act is nothing in `apps/` or `packages/` — the
> original done-when ("grep returns 0") is superseded by this classification: the
> grep now returns exactly the named, pinned, owned remainder.

> ### ◐ H2 STARTED 2026-08-10 — central binding SHIPPED + the Projects slice converted
>
> **The "do this first" step is done:** `_with_resolved_access` (acb_auth/deps.py) calls
> `bind_tenant(organization_id)` when identity resolves — the one place, from the
> `app_user` row, never a header (R11) — and the gateway's `TenantScopeMiddleware`
> (main.py) opens a fresh scope per request and releases it after the response.
> `tests/unit/test_tenant_request_binding.py` pins both, including
> "`system:internal` binds nothing" and no-leak-across-sequential-requests.
>
> **`routes/projects` is converted** (84 sites, the largest single package): every
> handler is `async with _tenant_session() as db:` where `_tenant_session` IS the
> shared seam (identity asserted in `test_db_engine_seam.py`). ~~One named exemption:
> `agent_dispatch.py` (2 sites)~~ ✅ **that exemption is GONE (WS-27aa, 2026-08-10)** —
> not by converting it to the ambient `tenant_session()`, which would have been the
> inheritance H4 forbids, but by giving it an EXPLICIT tenant: `pm.task.assigned` now
> carries the task's own `organization_id`, stamped by `set_assignees` inside the
> request's bound session, and every session in the sink is `tenant_session(that_org)`.
> An event without one is refused (a WARNING log line, no write — recording the refusal
> on the task timeline is impossible without the unbound session being refused).
> **`routes/projects` is therefore at ZERO unbound sites and holds no H2 exemption at
> all**, so Projects is not what gates the phase-4 promotion.
>
> **Ratchets** (test_db_engine_seam.py): `routes/projects` must stay at ZERO
> unconverted sites; the remainder elsewhere is frozen at **`H2_BASELINE_ELSEWHERE =
> 494`** and only ratchets down (progress must be banked by lowering the constant).
>
> ⚠️ **A live run found a defect in `tenant_session()` itself:** the literal
> `SET LOCAL app.tenant_id = :tenant` is a Postgres syntax error through the extended
> protocol — `SET` cannot bind a parameter, and every hermetic test was green with it.
> Fixed to `SELECT set_config('app.tenant_id', :tenant, true)` (identical
> transaction-local semantics), pinned by `test_tenant_session.py`, and proven by a
> live scratch-Postgres smoke: unbound → `TenantUnbound`; converted handlers write
> under the GUC; rows stamped with the bound org. **Converters of the remaining
> packages: the runbook below stands unchanged** — but test against real Postgres at
> least once per package; this class of defect is invisible to fakes.
>
> Conversion notes that generalize (learned on the Projects slice): handlers' explicit
> `await db.commit()` goes away (the wrapper commits on clean exit — and a mid-block
> commit would END the transaction and drop the GUC for everything after it, so a
> handler that genuinely needs two transactions needs two `async with` blocks);
> read-only endpoints now commit an empty transaction, so tests asserting
> `committed == 0` as a "writes nothing" proxy must assert on statements/rows instead;
> hermetic fakes swap in via an `asynccontextmanager` patched over the package's
> `_tenant_session` alias, commit-on-clean-exit so one-transaction contracts stay
> observable.

`acb_common.db.tenant_session()` exists and is tested. `get_db()` still exists, is
documented as **not** tenant-bound, and every one of the 561 sites still uses it.

**Do it file by file, smallest first**, and commit per file or small group. Do **not**
attempt a mechanical repo-wide rewrite: `get_db()` returns a session the caller closes,
while `tenant_session()` is an async context manager that owns the transaction — the call
shape changes, not just the name.

> ⚠️ **Two sites are NOT mechanical — audited 2026-08-10 (WS-27 alignment):**
>
> 1. **`routes/projects/core.py::resolve_organization_id`** reads the caller's
>    tenant *from the database, inside the session* (`app_user.organization_id`,
>    by authenticated email). `tenant_session()` needs the tenant **before** the
>    session opens, so converting Projects is circular until this moves to the
>    auth/pre-session layer. Note also that `bind_tenant` / `release_tenant` /
>    `current_tenant` have **zero callers repo-wide** — no middleware binds yet,
>    so that layer is the real first step of H2, not the 561 call sites.
>    ⚠️ It also encodes **one person = one organization** (migration 161's
>    D-MT-1), which **H6 retires**: `user_identity` + `org_membership` make
>    multi-org real. There are **21 such `app_user`-derived org reads across 6
>    modules** (projects, people, admin `members`/`_common`, rooms,
>    `acb_auth/access`) — H6's "`app_user` reads are gone or reduced to a
>    compatibility view" is a bigger surface than that sentence suggests.
> 2. **Background jobs** — ~~see MT-1d's named site in the parent spec
>    (`run_lifecycle_sweep`): a scheduled path H2 does not reach at all.~~
>    ✅ **That site is CLOSED (WS-27aa, 2026-08-10)** and it is the worked
>    example for the rest of H4: **resolve on an unbound session, then bind
>    explicitly.** Two sessions, in that order — the first decides the tenant
>    (so it cannot already be inside one) and writes nothing; the second is
>    `tenant_session(org)` and does the work. The stored fact was the workflow
>    **owner** (`workflows.owner_email` → `app_user.organization_id`), because
>    `workflows` carries no `organization_id` until H3 phase 1. The remaining
>    background sites in `workflows/service.py` (`_pm_task_updater`, the run
>    lifecycle) still need it.

```python
# before
db = await _get_db()
try:
    rows = await db.execute(text(SQL), params)
finally:
    await db.close()

# after
async with tenant_session() as db:
    rows = await db.execute(text(SQL), params)
```

**Where the tenant comes from** — and this is the part to get right, not the mechanics:
- **Request handlers:** bind once, centrally, from the authenticated session. Add
  `bind_tenant(user.organization_id)` in the gateway middleware / the app-wide dependency
  that already resolves `UserContext`, and release it after the response. Then the 561 sites
  need no tenant argument at all. **Do this before converting any handler.**
- **Jobs, brokers, consumers:** H4. Do not let a job inherit an ambient tenant.
- **Never** from a header, query parameter or body field — `user_management_contract.md`
  **R11**.

**Done when:**
1. `grep -rE "await _?get_db\(\)" --include=*.py apps packages` returns **0**.
2. A ratchet in `tests/unit/test_db_engine_seam.py` (or a sibling) fails the build if
   `get_db` is called again outside its own module.
3. The baseline suite passes, plus every app's own tests.
4. Manual smoke: sign in, open Chat / Email / Tasks / CRM / Projects / Notes, confirm each
   returns data. **A missing tenant binding presents as an empty list, not an error** —
   that is the fail-closed property, and it means an empty screen is the symptom you are
   hunting for.

**GATE:** zero `get_db()` call sites, ratchet in place, product verified working.

> This is where the work actually is. Expect it to dominate the schedule, and resist the
> temptation to do H3 first because it looks like the interesting part.

---

## H3 · Apply the RLS phases · 🟢 AGENT-SAFE to apply · ⚠️ **PHASE 4 IS A CLIFF**

`infra/postgres/generated/{01,02,03,04}` — regenerate with
`uv run python scripts/gen_tenant_migration.py`. **135 tables, 11 exempt.**

These are outside the deploy sequence on purpose. `apply_migrations.sh` carries a
lock-timeout design written after a **14h44m outage** where a hung session held a lock, the
runner queued an `ACCESS EXCLUSIVE` behind it, and Postgres's FIFO lock queue put every
later reader behind the *waiting* ALTER. Sending mail stopped. This is that shape, 135
times over.

**Before phase 1:** create the app role.
```sql
CREATE ROLE acb_app LOGIN PASSWORD :'pw' NOINHERIT;   -- NOT superuser, NOT owner, NOT BYPASSRLS
GRANT CONNECT ON DATABASE :db TO acb_app;
GRANT USAGE ON SCHEMA public TO acb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acb_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acb_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO acb_app;
```
Point the gateway, orchestrator and ingestion processes at `acb_app`. **Migrations keep
running as the owner.**

| Phase | What | Safety |
|---|---|---|
| 1 `add_columns` | nullable ADD COLUMN | no scan, no meaningful lock — safe live |
| 2 `backfill` | batched UPDATE | re-runnable and interruptible; the slow one |
| 3 `constraints` | SET NOT NULL + FK + index | **ACCESS EXCLUSIVE, scans every table — window required.** Apply table-by-table if needed. Never behind a long transaction |
| 4 `policies` | ENABLE + FORCE + policy | instant — **and the cliff** |

> 🚨 **Phase 4 gate.** The instant it applies, any connection that has not bound
> `app.tenant_id` reads **zero rows**. That is `§0.1`'s fail-closed property working as
> designed. **H2 must be complete and verified in production before you run phase 4.** If
> you are unsure whether H2 is complete, you are not ready.
>
> **Rollback for phase 4** (fast, and it is the only phase you will want to roll back):
> ```sql
> ALTER TABLE <t> DISABLE ROW LEVEL SECURITY;   -- per table, or scripted across the 135
> ```
> Phases 1–3 are additive and do not need rolling back.

**Done when:**
1. **(claim a — RLS applies)** all four phases applied, **and**
   ```bash
   DATABASE_URL=... uv run pytest tests/unit/test_tenant_coverage.py -v -rs
   ```
   shows the two previously-skipped tests as **PASSED**, not skipped. ⚠️ See
   §H3.3 — as rehearsed, `test_live_catalog_has_column_force_and_policy` needs a
   one-line correctness fix first (it does not yet subtract `HOMONYM_BLOCKED`),
   and applying that fix is guarded because it edits the security fence.
2. **(claim b — bootstrap + sign-in resolve, CHARACTERIZATION done-when, added
   2026-08-22)** the rehearsal demonstrates **whether** bootstrap + sign-in
   identity resolution succeed under phase-4 policies; **if not, it records the
   exact fix as an owner decision.** Rehearsed: they do **not** resolve — the
   unbound `app_user` reads return zero rows and the bootstrap write is refused
   by WITH CHECK (§H3.2). The characterization lives in
   `tests/unit/test_h3_rls_promotion_rehearsal.py::TestSignInBrickCharacterization`
   and the owner decision in §H3.2.
3. **(two-org isolation, MT-1i's owed fixture)** a real-Postgres two-org fixture
   binds a session to org A and proves it reads ONLY org A rows and cannot WRITE
   a row stamped org B — `passed`, never `skipped`
   (`tests/unit/test_h3_rls_promotion_rehearsal.py::TestTwoOrgIsolation`).

**GATE:** claim (a)'s two tests pass against the live catalog (after §H3.3's fix),
and claims (b) + the two-org fixture pass on scratch.

---

## H3 REHEARSAL RESULT (2026-08-22) — scratch only; live promotion is OWNER-GATE

> Executed on a local Docker scratch (`pgvector/pgvector:pg16`, 127.0.0.1:5443)
> — plan-guard makes every VPS/deploy path OWNER-GATE, so this is a **rehearsal
> of the promotion**, not the promotion. Stood up a **dedicated** database, ran
> `tests/unit/_tenant_ladder.py::apply_ladder` (full ladder 01→ladder tip on
> `pgvector/pgvector:pg16` — stock `postgres:16` cannot build `01_schema.sql`,
> which needs `uuid-ossp` + `vector`), created the non-privileged `acb_app` role
> per §H3's pre-phase-1 SQL (NOT superuser / owner / BYPASSRLS), seeded two
> organizations, then applied `generated/{01,02,03,04}.sql` **by hand in phase
> order** (the ladder deliberately never replays `generated/`). All four phases
> applied clean; RLS came up on **137 of 140** scoped tables.
>
> **What passed (evidence, real DB, never skipped):**
> - **Two-org isolation + WITH-CHECK + fail-closed** (MT-1i's owed fixture) —
>   `TENANT_LADDER_DATABASE_URL=… uv run pytest
>   tests/unit/test_h3_rls_promotion_rehearsal.py -v -rs` → **12 passed**
>   (10 DB-backed on real Postgres + 2 always-on structural arm-checks; 0 skipped).
>   Bound to org A: sees its 2 `apps` rows, 0 of org B's; org B sees its 3;
>   **unbound → 0 rows** (fail-closed); an A-bound INSERT stamped org B is
>   refused (`new row violates row-level security policy`); the phase-1 DEFAULT
>   stamps the bound tenant.
> - **The app role cannot bypass RLS** —
>   `test_tenant_coverage.py::test_app_role_cannot_bypass_rls` → **PASSED** as
>   `acb_app` (not super, not BYPASSRLS).
>
> **What the brick characterization SHOWED — sign-in does NOT survive phase 4.**
> Running the code's **own** SQL (`_ACCESS_SQL`, `_BOOTSTRAP_OWNER_SQL`) and the
> real `acb_auth.access` functions as `acb_app` with no `app.tenant_id` bound:
> `resolve_access(owner).is_active = False` (logs `access_unprovisioned_signin`
> even though the row EXISTS — RLS hides it); `resolve_identity(owner) =
> (None, None)` (no tenant to bind — the chicken-and-egg); `ensure_owner_bootstrap()
> = None` with the underlying `new row violates row-level security policy for
> table "app_user"`. This is the 2026-07-30 lockout shape, now by construction
> for every user. **The fix is §H3.2, an OWNER DECISION — not enacted here.**
>
> **What did NOT pass, and why it is not a promotion defect:**
> `test_tenant_coverage.py::test_live_catalog_has_column_force_and_policy`
> **FAILED** on exactly `['crm_activities', 'crm_contacts', 'crm_deals']` — the
> three CRM homonym tables. RLS applied cleanly to every table the generator
> scopes; these three are `HOMONYM_BLOCKED` (excluded from the generated set on
> purpose) and the gate test does not yet subtract them. See §H3.3 — this is a
> one-line correctness fix to the gate, guarded because it edits the fence.

### H3.1 Promotion runbook — exact order (what an owner runs, in a window)

**We cannot roll back** (R6, forward-only ladder; recovery is roll-forward or
restore) — so every step below is rehearsed on a scratch restore of the
**production** dump first, in this exact order:

1. **Prereq — the app role**, once, as the DB owner (§H3 pre-phase-1 SQL):
   `CREATE ROLE acb_app LOGIN PASSWORD … NOSUPERUSER NOCREATEDB NOCREATEROLE
   NOBYPASSRLS`; `GRANT CONNECT`/`USAGE`/`SELECT,INSERT,UPDATE,DELETE ON ALL
   TABLES`/`USAGE,SELECT ON ALL SEQUENCES`; `ALTER DEFAULT PRIVILEGES … GRANT …`.
   Point the gateway, orchestrator and ingestion processes at `acb_app`.
   **Migrations keep running as the owner.**
2. **H2 complete and verified in production first** (the non-negotiable ordering).
   The bind the policies read is `SELECT set_config('app.tenant_id', :org, true)`
   inside a transaction — `packages/acb_common/acb_common/db.py:292` in
   `tenant_session()` (opened by the request middleware
   `apps/services/gateway/gateway/main.py` `TenantScopeMiddleware`, filled from
   the authenticated session by `acb_auth`). No bind ⇒ the GUC is NULL ⇒ zero
   rows (fail-closed). This is what H2 makes true for all 561 sites.
3. **Apply the SAFE phases, by hand, in order**, from `infra/postgres/generated/`:
   `01_add_columns.sql` (nullable ADD COLUMN — safe live) → `02_backfill.sql`
   (batched, re-runnable — the slow one) → `03_constraints.sql` (**ACCESS
   EXCLUSIVE**, scans each table — window; table-by-table if needed; never behind
   a long transaction). **STOP after phase 3. Do NOT apply `04_policies.sql` yet.**
4. **🚨 PHASE-4 GATE — the whole point of this rehearsal. Do NOT proceed to
   step 5 until BOTH are true:** (a) H2 is complete and verified in production
   (step 2), and (b) the app_user brick fix in **§H3.2 is ratified and enacted**
   — either the app_user carve-out is in the generator, OR H6's identity cutover
   has landed. **As generated today, phase 4 bricks sign-in for every user** (an
   unbound `app_user` read returns zero rows → identity resolution fails; see the
   REHEARSAL RESULT above). If either condition is unmet, you are not ready —
   stop here.
5. **Apply the cliff — ONLY after step 4 is cleared:** `04_policies.sql`
   (**the cliff** — instant; the moment it applies, any unbound connection reads
   zero rows). This is irreversible except by the phase-4 rollback in step 7.
6. **Verification queries** (as `acb_app`, against the promoted catalog):
   ```bash
   DATABASE_URL=postgresql+asyncpg://acb_app:<pw>@<host>:<port>/<db> \
     uv run pytest tests/unit/test_tenant_coverage.py \
       ::test_app_role_cannot_bypass_rls \
       ::test_live_catalog_has_column_force_and_policy -v -rs
   ```
   plus a spot check: `SELECT count(*) FROM app_data;` returns 0 unbound, and the
   real row count inside `BEGIN; SELECT set_config('app.tenant_id', '<org>',
   true); SELECT count(*) FROM app_data; COMMIT;`.
7. **Rollback for phase 4 only** (the one you will want): `ALTER TABLE <t>
   DISABLE ROW LEVEL SECURITY;` per table. Phases 1–3 are additive.

### H3.2 The app_user sign-in brick — OWNER DECISION (written up, NOT enacted)

**Confirmed on scratch:** `app_user` gets a phase-4 policy
(`04_policies.sql:128-133`), but identity resolution reads `app_user.organization_id`
on an **unbound** session to discover the tenant (`acb_auth/access.py` —
`_ACCESS_SQL`/`resolve_access`, `resolve_identity`, `_BOOTSTRAP_OWNER_SQL`/
`ensure_owner_bootstrap`; all via the plain `get_session_factory()`). Under
fail-closed RLS: unset GUC → NULL → zero rows → bind never happens → **sign-in
bricks**, and the owner bootstrap's INSERT is refused by WITH CHECK.
`user_identity`/`org_membership` are already EXEMPT (control plane), which is why
H6's identity cutover exists.

Two ways forward. **Both are owner calls; neither is enacted in this change** —
the EXEMPT map IS the security review and H3-before-H6 is an owner-set ordering.

- **Option A — an app_user bootstrap carve-out designed INTO
  `scripts/gen_tenant_migration.py`.** Concretely: add `app_user` to the
  generator's `EXEMPT` map with a reason (e.g. *"identity table read unbound to
  discover the tenant; the tenant-scoped identity is H6's `org_membership`, which
  is exempt for the same reason"*), regenerate the four phase files, so `app_user`
  carries **no** RLS. **Prototyped on scratch (throwaway `ALTER TABLE app_user
  DISABLE ROW LEVEL SECURITY`, never committed):**
  - **Benefit, measured:** the unbound identity read returns the row → **sign-in
    survives** (0 rows before the carve-out, 1 after).
  - **Real tenant data stays isolated, measured:** with `app_user` exempt, an
    org-A-bound session still sees only org A's `apps` (1), org B only its own —
    the carve-out touches nothing but `app_user`.
  - **Cost, measured:** `app_user` becomes **cross-tenant readable** — an
    org-A-bound session sees org B's `app_user` rows (2 of 2). That leaks the
    user directory *and* each user's `organization_id`/`role`/`status` across
    tenants. It is the same posture `user_identity` already accepts, but
    `app_user` carries more (org + role + status), so it is a real widening.
    Mitigation the owner may prefer over a bare EXEMPT: a **carve-out POLICY**
    that keeps `WITH CHECK` (writes stay tenant-stamped) and narrows `USING` to
    what identity resolution needs — but that is a bespoke policy the generator
    does not emit today, i.e. more generator surface than option A's one line.
  - ⚠️ **This branch does NOT make the EXEMPT change** — the prototype lived in a
    throwaway SQL toggle, reverted; the committed generator and its EXEMPT map
    are untouched.
- **Option B — sequence H6 (identity cutover) BEFORE a clean phase-4 sign-in.**
  H6 moves sign-in onto `user_identity` + `org_membership` (already EXEMPT), so
  the unbound read hits an exempt table and no carve-out is needed; `app_user`
  can then be dropped to a compatibility view or fully scoped. This retires the
  "one person = one organization" encoding (migration 161's D-MT-1) as a bonus.
  Cost: H6 is a larger, live-sign-in-path change (21 `app_user`-derived org reads
  across 6 modules per H6) and it reorders H3/H6, which is an owner call.

**Recommendation: Option B, with Option A as a bridge only if phase 4 must ship
before H6.** Evidence: Option A un-bricks sign-in but *widens* cross-tenant
exposure of exactly the table (`app_user`) whose per-tenant `organization_id`,
`role` and `status` are the isolation we are promoting phase 4 to enforce — so it
trades a directory leak for a promotion, and the mitigation that removes the leak
(a bespoke carve-out policy) is more generator surface than option A claims to be.
Option B removes the unbound read from a scoped table entirely, which is the root
cause, and folds in the multi-org identity work H6 owns anyway. If the schedule
forces phase 4 first, ship Option A's carve-out **and** open the app_user
directory-leak as a tracked item that H6 closes. **Owner ratifies; not enacted
here.**

### H3.3 Gate finding — the live-catalog test vs `HOMONYM_BLOCKED`

`test_tenant_coverage.py::test_live_catalog_has_column_force_and_policy` subtracts
only `EXEMPT`, not `HOMONYM_BLOCKED`, from the set it demands be scoped. But
`gen_tenant_migration` scopes `discover_tables() - EXEMPT - HOMONYM_BLOCKED`
(`main()`; the sibling drift test at `:104` uses that exact set), so a **faithful**
phase-4 promotion always leaves `crm_contacts`/`crm_deals`/`crm_activities` with
an `organization_id` column and no policy — and the gate then flags them and can
**never go green**. Rehearsed 2026-08-22: it failed on exactly those three.

**Proposed one-line fix** (an OWNER-ratified change to the fence, deliberately not
applied here because the classifier guards edits that weaken a security gate):
add `and r["table_name"] not in gen.HOMONYM_BLOCKED` to the `bad` comprehension,
with a comment that the CRM homonym hole is tracked separately (still 🔴 owner
call — rename the column) in `HOMONYM_BLOCKED`, the leak audit and
`test_tenancy_boundary.py`, and is NOT a promotion failure. This aligns the gate
with the generator's contract without masking the real CRM hole (entry into
`HOMONYM_BLOCKED` is itself gated by the generator's homonym refusal). Until it
lands, claim (a)'s live-catalog test cannot pass; `test_app_role_cannot_bypass_rls`
already passes.

---

## H4 · Bind a tenant in every background job (MT-1d) · 🟢 AGENT-SAFE · ◐ tasks/calendar SHIPPED (dark)

*"A job that forgets doesn't leak one row; it leaks unbounded."*

Jobs have no request, so no session to inherit from. Cover: the ingestion scheduler
(`email_ingestion/scheduler.py`, three engines), `inbound.py`, the Redis Streams consumer
(`ingestion/consumer.py`), the reconciler, orchestrator agent runs, and broker handlers.

**Done when:** every queued/scheduled unit carries `organization_id` on its record and
binds it before any DB access; a job constructed **without** one **refuses to run** rather
than defaulting; a test proves the refusal.

> ✅ **Tasks + Calendar SHIPPED 2026-08-22 (WS-29, DARK — byte-identical pre-flip).**
> The 5 scheduler + rollover background sites now bind each job's own tenant via
> `tenant_session(org)` (the one GUC seam) and REFUSE (`TenantUnbound`, never
> default) when no org is resolvable:
> - `routes/tasks/scheduler.py` — `_run_one_cycle` / `_read_interval` bound
>   single-org (org threaded from the loop); `start_background_sync` /
>   `_enabled_accounts_by_org` is now a **per-org sweep** — it enumerates orgs
>   from the RLS-EXEMPT `organization` table on an unbound session, then binds
>   `tenant_session(org)` per org to read that org's `task_accounts`.
> - `routes/tasks/calendar.py` — `_rollover_one_user` bound single-user/org;
>   `_run_rollover_sweep` is the matching per-org sweep over `gtd_settings`.
>
> Each single-org job wraps its DB work in `tenant_session(org)`; the two sweeps
> keep ONE unbound `get_db()` each for the exempt-`organization` enumeration
> (`test_db_engine_seam.py` elsewhere-baseline 111 → 108). R7 fences (R8, real
> non-priv `acb_app` role on the phase-4 catalog, in
> `tests/unit/test_h3_rls_promotion_rehearsal.py`): `calendar-rollover-bound-under-rls`
> and `tasks-scheduler-bound-under-rls` — GREEN bound (each sweep releases/reads
> only its own org's rows across TWO seeded orgs), RED unbound (0 rows read /
> WITH-CHECK refused), and a no-org unit RAISES `TenantUnbound`.
> ⚠️ **The tasks broker handler (`routes/tasks/broker_handlers.py`) is NOT in
> this change** — a separate later PR, dormant unless `ACTION_BROKER_ENFORCE`.

> ✅ **Two of these are done, and they are the pattern to copy** (2026-08-10):
> `routes/crm/auto_lead` (the mailbox owner's org) and, by **WS-27aa**,
> `routes/projects` — `run_lifecycle_sweep` (the workflow owner's org) and
> `agent_dispatch` (the task's own org, carried **on the event payload**).
> Three shapes, one rule: **resolve on an unbound session, bind explicitly,
> refuse if the resolution finds nothing.** The dispatch case adds the one worth
> generalising — when the unit is an *event consumer*, the tenant belongs on the
> **event**, stamped by the emitter inside its bound session, because the
> consumer has nowhere legitimate to look it up. And note what refusing costs:
> a consumer with no tenant cannot record its own refusal in tenant data
> either — it logs and returns.

Also here: **`scripts/import_hr_people.py:177`** (§0.1 path 9) — it opens its own engine and
**upserts people rows**, which are tenant data. It must take a tenant from argv. Under
phase-4 policies it currently writes unowned rows or fails.

---

## H5 · Convert the Redis key sites (MT-1e remainder) · 🟢 AGENT-SAFE

`acb_common/tenant_redis.py` is built and cannot express an unprefixed key. **~58 key sites
across 10 clients** still bypass it. The module docstring holds the migration path.
**Do not write a dual-read shim** — every key is cache, presence or a bounded stream, so
conversion is a cache-cold event, not a data migration.

⚠️ **Three things the ratchets structurally cannot catch. Handle each by hand:**
1. **`routes/chat.py:707`** — `SCAN match="cc:active:*"` **enumerates every tenant's
   sessions** once a second exists. Highest severity in the inventory. Fix first.
2. **`ingestion/consumer.py:95`** — `_GROUP = "cc-ingest"` is **one consumer group shared by
   all tenants**. §1.9 requires one per tenant.
3. **Untenanted non-`cc:` namespaces**, invisible to the `cc:` ratchet:
   `ingestion:{clickup,zoho,gmail,dlq}` (`queue.py`), `session_mem:`
   (`acb_memory/session_cache.py:34`), `email:att:cache:` (`attachments.py`). Plus
   **`orchestrator/agents.py:436`**, which hands `redis_url` to `agent_framework`'s
   `RedisHistoryProvider` — chat history keyed **outside this wrapper entirely**. That one
   needs a decision, not a conversion.

**Done when:** both allow-lists in `test_tenant_redis.py` are empty and its stale-entry
tests still pass; the three items above are individually resolved and each resolution
recorded.

---

## H6 · Identity cutover (MT-1a-2) · 🟡 CAREFUL — live sign-in path · ◐ SLICES 1+3a+ORPHAN-CLOSURE+3b+4-EXPAND+RLS-BIND-HARDENING+PROVISION-RLS-BIND SHIPPED (dark)

Migration 159 created `user_identity` + `org_membership` and seeded them. `app_user` is
still authoritative and **nothing reads the new tables**.

⚠️ **SLICE 1 SHIPPED 2026-08-22 (WS-29, DARK — no read moved).** The shadow tables were
COMPLETE only for members present at 159 and STALE for every invite/bootstrap since. Slice
1 closes that so H6's read cutover has current tables to move onto, WITHOUT moving any read:
- **Dual-write** on both `app_user` write paths — `acb_auth.access.mirror_identity_membership`
  (best-effort, own session, mirroring `_record_signin_request`) is called after
  `_BOOTSTRAP_OWNER_SQL` (in `ensure_owner_bootstrap`) and after `_PROVISION_MEMBER_SQL` (in
  `_common.provision_member`). It upserts one `user_identity` per `lower(email)` and a
  **create-only** `org_membership` (`ON CONFLICT (organization_id, user_id) DO NOTHING`,
  no `SET organization_id`) — so it can NEVER move an identity between orgs (done-when 3).
- **Catch-up backfill** — `infra/postgres/182_identity_membership_catchup.sql` re-runs 159's
  idempotent seed for every member added since (additive, `DO NOTHING`, re-run = 0 net change).
- Fence: `tests/unit/test_h6_identity_shadow.py` (R8, in `pr-check.yml`'s skip guard) proves
  one email holds membership in TWO orgs, the create-only guard never rewrites the first, and
  the backfill reconciles + is idempotent (done-when 2 + 3). `app_user` reads and both upserts
  are byte-identical — the dual-write is purely additive on separate statements/session.

🚨 **SLICE 1 MIRRORS EXISTENCE, NOT STATUS — a HARD INPUT the read cutover MUST handle
(reviewer P2, could become a P0 in the cutover).** The dual-write is create-only and fires
only on invite/approve/bootstrap; the suspend / remove / reactivate paths (`members.py`
`update_member`/`remove_member`) mutate `app_user.status` and do NOT call the mirror, and
`ON CONFLICT DO NOTHING` suppresses any status update on the existing row. So
`org_membership.status` (and `joined_at`/`last_active_at`) drift STALE from `app_user`.
Harmless while dark — no tenant-plane reader consumes `org_membership.status`
(`console_resolve` filters `resolved_at IS NOT NULL` only). ✅ **The forward half — status
mirroring on suspend/remove/reactivate — is now built (slice 3a below);** and ✅ **the two
related inputs this note deferred are now CLOSED by the orphan-closure slice** (see the CUTOVER
CHECKLIST): the create-only invite/approve over-count ORPHAN is gone because the mirror moved
POST-COMMIT (a rolled-back caller txn now mirrors nothing), and the two identity/org SQL
constants are pinned byte-equal by `test_h6_identity_shadow.py::TestTheOrphanClosure`. For
historical context this note read: *the create-only invite path can leave an over-count ORPHAN
row (`resolved_at` NULL, no `app_user`) if the caller's txn rolls back after `provision_member`
returns — migration 182 (`DO NOTHING`, no `DELETE`) cannot prune it; and the two identity/org
SQL constants are byte-identical to `console_resolve`'s
(`_UPSERT_IDENTITY_SQL`/`_ORG_BY_SLUG_SQL`) but not pinned equal by a
test (silent-drift risk — a later slice should add the equality assertion or lift them to a
shared module; a module-level import is blocked by `test_console_dependency_boundary`'s
importer cap).* Both are done now — see the CUTOVER CHECKLIST.

✅ **SLICE 3a SHIPPED 2026-08-22 (WS-29, DARK — D48 RATIFIED) closes the FORWARD half of
the status-drift P0 above.** D48 (2026-08-22, owner-ratified; `work_plan.md` §3) re-keys
RBAC onto `user_identity` in two phases with status mirrored FORWARD — reconcile-from-
`app_user` is impossible post-RLS (the identity leg reads UNBOUND while `app_user` is
RLS-forced), so status must live current in the RLS-EXEMPT `org_membership.status`. Slice
3a builds exactly that, still moving NO read:
- **Forward status mirror** — `acb_auth.access.mirror_membership_status` (best-effort, own
  session, mirroring `mirror_identity_membership`) called after the authoritative
  `app_user` write in `members.py`'s `update_member` (suspend/reactivate/activate) and
  `remove_member` (remove), and in `_common.provision_member` (approve's invited→active,
  which the create-only mirror could not propagate). It is a **scoped UPDATE of an EXISTING
  (org, identity) row** — sets ONLY `status` (+ `joined_at` on activation, as `app_user`
  does), NEVER `organization_id`/`user_id`, so it can never move an identity between orgs;
  a missing row is a 0-row no-op (existence stays 159/182/slice-1's job).
- **Reconcile migration** — `infra/postgres/183_org_membership_status_reconcile.sql` aligns
  every already-drifted `org_membership.status` to `app_user.status`
  (`IS DISTINCT FROM`-guarded, idempotent, no INSERT/DELETE, never touches `resolved_at`).
- Fence: `tests/unit/test_h6_identity_shadow.py` extended (R8) — suspend/remove/reactivate
  propagate, the mirror + reconcile never move an identity (org-B row untouched), the
  reconcile aligns a drifted row and re-runs at 0 changes, and `app_user` is byte-identical
  after the mirror. `app_user` stays authoritative; the mirror is additive on separate
  statements/session.
✅ **ORPHAN-CLOSURE SLICE SHIPPED 2026-08-22 (WS-29, DARK — D48) — the CUTOVER CHECKLIST below
is now CLOSED at the SOURCE.** This is the slice that lands BEFORE slice 4 (the RBAC re-key)
and the flip, because an active `org_membership` orphan (an identity with no live `app_user`)
becomes a wrong-ADMIT the moment RBAC stops gating on `app_user`, and reconcile 183 can never
reach it (it joins the deleted/rolled-back `app_user`). So each orphan had to be closed where
it is CREATED, not where it is read. All four were DARK (no reader consumes
`org_membership.status` yet); all four are now closed:
1. **PURGE orphan — CLOSED.** `members.py` `purge_member` (`_PURGE_DELETES` ~:452) deletes
   `app_user` but not the shadow, so it now also calls `acb_auth.access.purge_identity_shadow`
   post-commit (the call is `members.py` ~:678; best-effort, own session): it deletes ONLY the
   org's `org_membership` row — the active-shadow wrong-ADMIT source. It **NEVER** touches the
   GLOBAL `user_identity`: deleting it on the human's LAST membership was a check-then-cascade
   cross-tenant erasure RACE (a concurrent other-org membership committed between the
   `NOT EXISTS` re-eval and the delete was CASCADE-erased — org A's purge wiping org B's
   member), so the identity delete was dropped and the race eliminated by construction. A
   membership-less `user_identity` is harmless (the identity leg reads
   `user_identity ⋈ org_membership` → no org → no access, fail-closed) and re-used on re-join
   via `ON CONFLICT (lower(email))`; global `user_identity` lifecycle/pruning of the
   membership-less leftover is deferred to slice 5's atomic mirror+prune. A failed best-effort
   purge is NOT self-healed by 182/183 (182 never deletes; 183 joins the now-gone `app_user`) —
   that swallowed-failure window is also slice 5's. Fence: `test_h6_identity_shadow.py`
   `TestThePurgeClosure` (R8, incl. the kept-identity case) + `TestTheOrphanClosure`'s
   `no-user_identity-delete-on-purge` (shape).
2. **APPROVE-ROLLBACK orphan — CLOSED.** The provision-path mirror used to commit on its OWN
   session INSIDE the caller's still-open txn. Both mirror calls moved OUT of
   `_common.provision_member` into its callers (`members.invite_member`,
   `access_requests.approve_access_request`), fired only AFTER their `_tenant_session` commits —
   matching how `update_member`/`remove_member` already mirror. So a concurrent-approve 409
   (`access_requests._decide`) or the `member["status"] != "active"` guard rolls the whole block
   back BEFORE the mirror runs, and no active shadow is ever committed. Fence:
   `TestTheOrphanClosure` post-commit-ordering (the mirror call is lexically after the block's
   single commit; `provision_member` names neither mirror).
3. **Create-only over-count orphan — CLOSED by the same move.** A caller rollback after
   `provision_member` returns no longer commits an existence orphan: the mirror runs only once
   the caller's commit is durable, so a rollback mirrors nothing.
4. **Un-pinned SQL — CLOSED.** `access._MIRROR_IDENTITY_SQL`/`_MIRROR_ORG_BY_SLUG_SQL` are now
   pinned byte-equal to `console_resolve._UPSERT_IDENTITY_SQL`/`_ORG_BY_SLUG_SQL` by
   `test_h6_identity_shadow.py::TestTheOrphanClosure::test_the_identity_write_sql_is_pinned_equal_to_the_console_upserts`.
   A TEST importing `console_resolve` is NOT counted by `test_console_dependency_boundary`'s
   PRODUCTION importer cap (it sweeps `packages/` + `apps/`, never `tests/`), so pinning the
   equality here is the alternative that clause named to lifting the constants to a shared module.

**Slice 3b — the read cutover, correctly stated.** The earlier "read status only for rows still
present in `app_user` (join / intersect)" is UNBUILDABLE post-RLS: under H3 phase-4 the identity
resolution runs on an UNBOUND session, `app_user` is FORCE-RLS'd, so it returns ZERO rows and
any join/intersect against it is empty for EVERYONE. What 3b actually does, with
`IDENTITY_CUTOVER` ON under phase-4 RLS:
- the **identity leg** reads identity + org + status from `user_identity ⋈ org_membership`
  UNBOUND (both tables are RLS-EXEMPT) and filters `org_membership.status = 'active'` — this is
  what resolves *which tenant* and *is this member live* on the one path that must work before a
  tenant is bound;
- the **role leg** stays BOUND, and its `app_user`-derived permission read remains the ACCESS
  authority — so any residual shadow orphan that source-closure somehow missed resolves to
  **bind-with-no-access** (fail-closed: an identity with no live `app_user` grant gets nothing),
  never a wrong-ADMIT.
Orphan SOURCE-closure (the checklist above) is THIS orphan-closure slice and a PREREQUISITE of
3b + the flip: it is what lets the identity leg trust its own `status='active'` filter — the
filter is only safe because a purged/rolled-back ghost no longer sits in `org_membership`.

**Slice 3b done-when (testable):** on the promoted two-org catalog
(`test_h3_rls_promotion_rehearsal.py`'s `promoted()` fixture), a clean active member resolves
GREEN under phase-4 RLS with the session UNBOUND and `IDENTITY_CUTOVER` ON, while the
pre-cutover brick (the `app_user` UNBOUND read returning zero rows) reproduces RED with the flag
OFF — the same red/green characterization the H3 rehearsal already lands, now driven through the
identity leg.

### Slice 4 — RBAC re-key (EXPAND) · ✅ SHIPPED (dark) 2026-08-22 · repaired 2026-08-22 (provision-owner + RBAC-first bridge; round 2 — the bridge is now GUC-BOUND under FORCE RLS) · the shared first PR

D48 re-keys the three RBAC tables — `user_role`, `user_permission_override`, `org_group_member` —
from `app_user.id` → `user_identity.id` in an expand/contract. **This EXPAND is the shared first
PR:** it adds the column, backfills it, indexes it and dual-writes it, so the per-module READ
cutovers below can each move onto a populated column in their own later PR. It moves **NO read**.

- **Migration 184** (`184_rbac_user_identity_rekey_expand.sql`; highest was 183, re-check at
  merge per R1) adds nullable `user_identity_id UUID` to each of the three tables, BACKFILLS it
  via the `lower(email)` bridge (RBAC.user_id → `app_user.id` → `lower(app_user.email)` →
  `user_identity.id`), and indexes the new column per table. Additive, forward-only, idempotent
  (`IS DISTINCT FROM ui.id`-guarded → re-run = 0 rows), mirroring 158's credential re-key + 182's
  backfill shape. The FK is **`ON DELETE SET NULL`, not 158's `CASCADE`** — `user_identity_id` is
  a nullable SHADOW while `app_user.id` stays the authoritative key, so a deleted identity must
  NULL the shadow, never cascade-delete a still-authoritative grant (the CONTRACT slice re-keys
  to `CASCADE` when the shadow takes over). It creates no table and does **not** touch the three
  tables' RLS (ENABLE/FORCE/policies stay in `generated/04_policies.sql`).
- **Dual-write** `user_identity_id` on the FIVE Python RBAC INSERTs (the identity-FIRST order),
  resolving the identity through the same bridge at insert time. The write sites (re-verified by
  grep, one bridge each):
  - `user_role` — `acb_auth/access.py` `_BOOTSTRAP_OWNER_SQL` (resolves by `lower(:email)` — its
    `app_user` is in the same `WITH` and unreadable, and at bootstrap the identity is minted
    AFTER the commit, so a fresh box writes NULL, reconciled by the mirror-time backfill below +
    a later migration re-run) and `gateway/routes/admin/_common.py` `set_roles`;
  - `user_permission_override` — `admin/groups.py` (the center-access grant) and `admin/members.py`
    `set_member_overrides`;
  - `org_group_member` — `admin/groups.py` `add_group_member`.
  The `DELETE`s keyed on `user_id` stay correct during the dark expand and are unchanged
  (`_common.set_roles`, `members.set_member_overrides`'s delete-then-insert, `members.remove_member`).
- **The SIXTH RBAC INSERT — `provision_org_owner`, bridged at mirror-time (repair 2026-08-22).**
  `provision_org_owner` (the tenant-plane SQL function, `infra/postgres/180_...:153-155`) INSERTs
  the owner's `user_role` DURING provisioning — before any `user_identity` exists (provisioning
  mints the owner's identity only AFTER it commits), so the five in-INSERT dual-writes cannot reach
  it. It is instead bridged when the identity is ensured: the provision caller
  (`acb_common.provisioning.provision_local_organization`) reuses the ONE identity-creation seam
  (`acb_auth.access.mirror_identity_membership`, best-effort, own session, after the authoritative
  commit — no second `user_identity` writer, no SQL fork of `provision_org_owner`), and that mirror
  now ALSO backfills the human's existing RBAC rows (`_bridge_rbac_to_identity`: a scoped UPDATE per
  RBAC table via the same `lower(email)` bridge, setting ONLY the still-NULL `user_identity_id`,
  idempotent, moves no grant). Because the mirror is called by invite / approve / bootstrap AND now
  provision, this closes the RBAC-FIRST order (a provisioned owner, a bootstrap owner, any future
  one) in one place — so the earlier "exactly five, no sixth missed" claim was wrong. **Why it
  matters:** without it a freshly-provisioned owner's `user_role.user_identity_id` stays NULL and,
  once `IDENTITY_CUTOVER` flips the role leg onto that column, they resolve to NO roles → locked
  out of their own org (fail-closed, but a real break, on a path run continuously).
- **The bridge is GUC-BOUND, because the three RBAC tables are RLS-FORCED (repair 2026-08-22, round
  2).** `user_role` / `user_permission_override` / `org_group_member` all carry `FORCE ROW LEVEL
  SECURITY` keyed on `organization_id = current_setting('app.tenant_id', true)::uuid`
  (`generated/04_policies.sql`), so `_bridge_rbac_to_identity`'s UPDATEs run on an UNBOUND session
  see the GUC as NULL → 0 visible rows → they silently no-op (the best-effort catch swallows it) and
  the owner's `user_identity_id` stays NULL under real phase-4 RLS — the SAME class as slice 3b's
  unbound `resolve_access`. The fix runs the bridge inside the ONE GUC seam `tenant_session()`
  (`SET LOCAL app.tenant_id`, no second GUC path) bound to the human's org — threaded in from the
  mirror's already-resolved org (== the human's `app_user.organization_id`; a human is in ONE org,
  migration 162). The org is NOT re-derived by reading `app_user` in the bridge: that read is itself
  unbound → 0 rows, the chicken-and-egg the H3 rehearsal pins. This **corrects the earlier R5
  justification that over-generalized** "the shadow tables are RLS-EXEMPT so no bind is needed" from
  the mirror's OWN writes (`user_identity` / `org_membership`, genuinely exempt) to the bridge's
  writes (RLS-FORCED). The same GUC-bind requirement binds **any** H6 write path touching an
  RLS-forced table — the SECURITY-INVOKER-under-FORCE-RLS tension migration 179's header flagged as
  "REVISIT AT H3". Fence: `test_h6_rbac_rekey.py::TestTheRealBridgeFunctionUnderForceRls` (drives
  the real bridge as the non-priv `acb_app` role — RED if the bind is removed; the bypass-role R8
  tests are blind to it) + `::TestTheBridgeSqlNeedsTheGucBindUnderForceRls` (bound GREEN / unbound
  RED at the SQL level).
- **Testable EXPAND done-when (R8, real PG):** after migration 184 on a ladder-replayed DB,
  `user_identity_id` becomes correct for EVERY RBAC row once the human's identity exists — it equals
  the `user_identity.id` whose `lower(email)` matches its `user_id`'s `app_user.email` (dual-write
  when identity-FIRST; mirror-time backfill when RBAC-FIRST), NULL only where no identity exists yet;
  the migration re-run and the mirror re-run are both 0-change no-ops (idempotent); a fresh Python
  RBAC INSERT (via `set_roles` / bootstrap / group add) populates both columns to the same identity;
  and driving `provision_local_organization` end-to-end leaves the owner with a `user_identity` AND
  their owner `user_role.user_identity_id` == that identity. The migration-184 backfill still
  reconciles pre-existing rows. Fence: **`tests/unit/test_h6_rbac_rekey.py`** (structural half incl.
  `TestTheProvisionOwnerBridgeMechanism`; R8 half — `TestTheBackfill`, `TestTheDualWriteAtRuntime`,
  `TestTheProvisionOwnerIsBridged`, `TestRbacCreatedBeforeIdentityIsBridged`, and — the round-2
  FORCE-RLS fences — `TestTheRealBridgeFunctionUnderForceRls` (real bridge as the non-priv `acb_app`
  role) + `TestTheBridgeSqlNeedsTheGucBindUnderForceRls` (bound GREEN / unbound RED); the last two
  reuse `test_h3_rls_promotion_rehearsal.promoted` for the phase-4-promoted catalog — on
  `TENANT_LADDER_DATABASE_URL`, in `pr-check.yml`'s skip guard).

### Slice 4 — per-module READ cutover · 🟡 later parallel PRs, one per module

Each module's `app_user.id`-keyed RBAC read moves onto `user_identity_id` in its OWN PR, behind
`IDENTITY_CUTOVER`. **Per-module done-when:** with the flag ON, that module's access answer is
**byte-identical** to the `app_user.id`-keyed answer for a clean member, and **zero**
`app_user.id`-keyed RBAC joins remain in that module. The 15 read sites, grouped by module
(⚠️ = TRICKY, see below):

- **acb_auth** — `access.py` `_ACCESS_SQL` (:166-194), `_ORG_OWNER_SQL` (:705-713),
  `_GROUP_MEMBER_SQL` (:836-846)⚠️
- **admin** — `_common.py` `roles_for_user` (:270-281), `caller_rank` (:292-305),
  `owner_count` (:308-321)⚠️ · `members.py` list subquery (:117-129), `_load_overrides` (:749-760),
  `_role_permission_map` (:763-777)
- **projects** — `core.py` `_MY_GROUPS_SQL` (:599-605)⚠️, `_EFFECTIVE_PERMISSIONS_SQL` (:760-771)⚠️ ·
  `mapping.py` `_GROUP_MEMBERS_SQL` (:98-104)⚠️
- **people** — `chart.py` (:98-105)
- **gateway** — `rooms.py` `MY_GROUPS_SQL` (:165-173), session-visibility `EXISTS` (:404-413)

⚠️ **The 5 TRICKY reads** (`access._GROUP_MEMBER_SQL`, `_common.owner_count`,
`projects/core._MY_GROUPS_SQL`, `projects/core._EFFECTIVE_PERMISSIONS_SQL`,
`projects/mapping._GROUP_MEMBERS_SQL`) read `app_user.status`/`app_user.organization_id`
ALONGSIDE the RBAC join. Under phase-4 RLS `app_user` is unreadable on the unbound leg, so a bare
key-swap breaks them: **status/org must come from `org_membership`** (slice 3a's forward mirror is
what makes that safe), not from an `app_user` join. These are not a mechanical rename.

(The audit found **zero** RBAC refs in `crm`/`auto_lead`, `people/core`, `workflows/service`,
`projects/assignees` — they are not in the cutover set.)

**Dependency order.** The EXPAND builds **parallel to slice 3b** (they touch different regions of
`access.py` — 3b the READ functions, this the `_BOOTSTRAP_OWNER_SQL` INSERT). The per-module READ
cutovers build **AFTER both 3b (the resolve split + `IDENTITY_CUTOVER` flag) AND this EXPAND
land**. 🔴 **OWNER-GATE, do NOT enact:** the CONTRACT migration dropping the old `user_id` column,
flipping `IDENTITY_CUTOVER`, H3 phase-4 promotion, and running the 184 backfill against prod.

✅ **SLICE 3b SHIPPED 2026-08-22 (WS-29, DARK behind `IDENTITY_CUTOVER`, default OFF).** The read
cutover, built exactly as stated above and no wider (RBAC re-key remains slice 4):
- **The flag** — `acb_auth.access.identity_cutover_enabled()` reads `IDENTITY_CUTOVER` (unset =
  OFF, fail-closed, the `deps._refuse_llm_key_identity` env idiom). Read server-side in
  `deps._with_resolved_access` (the one place that resolves + binds); NOT the Next-side
  `CUSTOMER_CONSOLE_RESOLVE_ENABLED` — the two-phase resolve is entirely server-side in `acb_auth`.
- **The identity leg** — `acb_auth.access.resolve_identity` reads the new `_IDENTITY_LEG_SQL`
  (`user_identity ⋈ org_membership ⋈ organization`, all RLS-EXEMPT, filtered `status='active'` —
  the `console_resolve._READ_SQL` shape, reused not forked) when the flag is ON, and the
  byte-identical `app_user` statement when OFF.
- **The orchestration** — `deps._with_resolved_access` runs the identity leg FIRST, `bind_tenant`s
  the resolved org, THEN calls `resolve_access` (the role leg), so its `app_user`-derived
  permission read stays the ACCESS authority (a residual shadow orphan resolves to
  bind-with-no-access, never a wrong-admit). ⚠️ **The role leg is GUC-bound INSIDE `resolve_access`,
  not by `bind_tenant` (P0 repair 2026-08-22 — see below).** `bind_tenant` sets only the
  `_TENANT` ContextVar; the GUC `app.tenant_id` that phase-4 RLS keys on is applied ONLY by
  `tenant_session()` (`set_config(..., true)`, db.py). So when the flag is ON and a tenant is
  bound, `resolve_access` opens its `app_user` read through `tenant_session()` (the ONE GUC seam,
  reused not forked — R5); flag OFF, or ON with nothing bound, keeps the raw unbound session
  byte-identical to today.
- Fences (R7, R8): `test_h3_rls_promotion_rehearsal.py::TestTheReadCutoverIdentityLeg` — flag-ON
  GREEN, flag-OFF RED (brick, even with the shadow seeded), and a SUSPENDED member refused by the
  `status='active'` filter, through `resolve_identity`'s own call path; **`::TestTheReadCutoverEndToEnd`
  (the end-to-end-two-phase fence, P0 repair) — the FULL `deps._with_resolved_access` composition
  (identity leg → bind → role leg) UNBOUND with the flag ON admits a clean ACTIVE member
  is_active=True WITH their real role (GREEN), and the mutation (role leg blind to the bound tenant)
  → is_active=False (RED), plus the multi-org-safe deny**; all against the phase-4 catalog as
  `acb_app` UNBOUND. `test_h6_identity_shadow.py::TestTheReadCutoverFlag` — the DB-free flag-switch
  (OFF→`app_user`, ON→exempt shadow), default-OFF env idiom, and the multi-org COUNT decision
  (one binds, two deny).
- **DEFERRED to a later slice (not the sign-in brick, and RBAC-blocked):** `org_owner_of` /
  `_HAS_OWNER_SQL` / `ensure_owner_bootstrap` resolve the OWNER *role*, which lives in the
  FORCE-RLS'd `user_role` table with no exempt-table equivalent (`org_membership` has no `role`
  column) — moving them IS the slice-4 RBAC re-key this slice's gate excludes. `membership_of` is
  a signup-path read whose active-only semantics belong with that path's decision, not the
  sign-in cutover.

🔧 **SLICE 3b REPAIR ROUND 2026-08-22 (WS-29, branch `ws-29-h6-slice3b-readcutover-v2`) — one P0 + two P2s from diff-review, all closed DARK.**
- **P0 — the role leg was NOT actually RLS-bound (total-lockout brick unfixed).** `bind_tenant`
  (db.py) sets only the `_TENANT` ContextVar; the GUC `app.tenant_id` phase-4 RLS keys on is
  applied in EXACTLY ONE place — `tenant_session()`'s `set_config(..., true)`. But `resolve_access`
  opened a RAW `get_session_factory()` session with no GUC, so under phase-4 RLS + `IDENTITY_CUTOVER`
  ON its read of the FORCE-RLS'd `app_user`/`user_role` returned 0 rows → `EffectiveAccess(is_active=False)`
  → **every member locked out**, the exact brick the cutover exists to remove (it only "worked"
  pre-phase-4, where `app_user` is readable unbound — the condition under which the slice is
  pointless). **Fix:** when the flag is ON and a tenant is bound (`current_tenant()` set),
  `resolve_access` opens its read through `tenant_session()` — the existing GUC seam, reused not
  forked (R5); the OFF/unbound path stays the raw session, byte-identical. Fence: the new
  `TestTheReadCutoverEndToEnd` (R8) — GREEN admits, mutation (role leg blind to the bind) → RED
  is_active=False. This is the fence whose absence let the P0 through: the prior GREEN drove only
  `resolve_identity` in isolation, never `resolve_access` under the bind.
- **P2a — multi-org identity-leg determinism.** `_IDENTITY_LEG_SQL`'s `ORDER BY joined_at DESC …
  LIMIT 1` could bind the WRONG org for a human with >1 active membership (console-created
  memberships leave `joined_at` NULL → tiebreak on slug). Now `LIMIT 2` + a COUNT decision in
  `resolve_identity`: exactly one active membership binds; more than one with no disambiguating host
  is the WorkspaceChooserRequired / deny case (`(None, None)`; host-based selection is MT-1f) —
  never a silent arbitrary bind. Fences: the multi-org-safe cases in `TestTheReadCutoverEndToEnd`
  (R8, two real memberships) and `TestTheReadCutoverFlag` (DB-free COUNT).
- **P2b — `user_id` id-space divergence.** ON, `resolve_identity` returns `user_identity.id`; OFF,
  `app_user.id` — different UUID spaces, surfaced by `/auth/me` (`routes/admin/me.py`). No consumer
  keys on it (the backend keys on email; chat.py's "user_id" is the email), so it is DOCUMENTED as
  an opaque per-human identity token, never an `app_user` FK, at `resolve_identity`,
  `UserContext.user_id` (roles.py) and the `/auth/me` payload — so a future consumer cannot silently
  treat it as an `app_user` key.
- ✅ ~~**STILL-OPEN WRITE-BRICK before the flip (co-located here from §H3.2).**~~ **CLOSED
  2026-08-22 by the H6 RLS-BIND HARDENING slice (WS-29) — see the PRE-FLIP RLS-BINDING CHECKLIST
  below.** `resolve_access`'s READ was GUC-bound in the 3b repair; `ensure_owner_bootstrap()` now
  resolves the `default` org's id from the RLS-EXEMPT `organization` table and runs its
  `_HAS_OWNER_SQL` read + `_BOOTSTRAP_OWNER_SQL` INSERT inside `tenant_session(default_org_id)` (the
  ONE GUC seam, reused not forked — R5), so an ownerless box CAN bootstrap its first owner under
  phase-4 RLS. For historical context the note read: *`ensure_owner_bootstrap()` still runs
  `_BOOTSTRAP_OWNER_SQL`'s INSERT into the RLS-FORCED `app_user` at gateway startup on an UNBOUND
  session … its `WITH CHECK` compares `organization_id` against the unset GUC (NULL) and REFUSES the
  write — an ownerless box can never bootstrap its first owner (§H3.2, and
  `test_the_unbound_owner_bootstrap_write_is_rejected` characterizes it).* The raw-SQL
  characterization stays RED by design (the unbound INSERT is still refused); the FIX is proven
  GREEN by `test_h3_rls_promotion_rehearsal.py::TestEnsureOwnerBootstrapBindUnderForceRls` (the real
  function bootstraps under FORCE RLS as `acb_app`, unbound at entry; bound GREEN / unbound RED at
  the SQL level).

### H6 PRE-FLIP RLS-BINDING CHECKLIST · ◐ 4 fixes SHIPPED (dark) 2026-08-22 · 2 OWNER-DECISION items + the H4 dependency OPEN

The systematic RLS-binding audit that this checklist records asked one question of
every site that touches an RLS-FORCED table: *does it run on a session with
`app.tenant_id` bound?* The GUC is set in EXACTLY ONE place —
`tenant_session()`'s `set_config('app.tenant_id', …, true)` (`acb_common/db.py`);
`bind_tenant()` sets only the `_TENANT` ContextVar, never the GUC. A site that
reads/writes a FORCE-RLS'd table on an UNBOUND session reads 0 rows / refuses
writes once phase-4 is live — the class the 3b P0 and the slice-4 bridge already
hit. The audit closed the three agent-safe sites and itemised the rest for the
owner. **Nothing here flips a flag or promotes RLS — all of it is DARK (binds are
no-ops until phase-4 is live).**

**(a) FIXED here (WS-29 H6 RLS-bind hardening, DARK):**
1. **`resolve_session_access`** (`acb_auth/access.py`) — its participant/member
   fold reads `chat_session_participant` + `org_group_member` + `app_user` (all
   FORCE-RLS'd). Now reads through `tenant_session()` when the cutover is ON and a
   tenant is bound (the request path); the unbound/no-tenant path (the background
   folds — see the H4 dependency below) stays byte-identical and fail-closed.
   Fence: `test_h3_rls_promotion_rehearsal.py::TestResolveSessionAccessBindUnderForceRls`
   (bound GREEN / unbound-mutation RED, non-priv `acb_app`).
2. **`ensure_owner_bootstrap`** (`acb_auth/access.py`) — resolves the `default`
   org id from the RLS-EXEMPT `organization` table, then runs `_HAS_OWNER_SQL` +
   `_BOOTSTRAP_OWNER_SQL` inside `tenant_session(default_org_id)`, so first-owner
   bootstrap is no longer bricked under phase-4. Fence:
   `::TestEnsureOwnerBootstrapBindUnderForceRls` (real function GREEN; bound/unbound
   RED at the SQL level).
3. **The ratchet blind-spot fence** — `test_db_engine_seam.py`'s `get_db` ratchet
   matched only `await get_db()` and was BLIND to sites that open
   `get_session_factory()()` / `_get_session_factory()()` directly, which is the
   EXACT class every unbound-RLS bug hid in. NEW `test_no_new_unbound_factory_opens`
   pins every direct factory open in `apps/`+`packages/` to a reviewed per-file
   count (`_FACTORY_OPEN_ALLOW`), so a new unreviewed one goes RED. This is what
   stops the whole class recurring silently.
4. **New-org provisioning writes** (was OWNER-DECISION (b)1; FIXED 2026-08-22, WS-29
   `ws-29-provision-rls-bind`) — `provision_organization` creates the `organization`
   row + `tenant_placement` (both RLS-EXEMPT — they land unbound), so the org
   **exists** before the create act's FORCE-RLS'd writes (`org_role_permission` via
   `provision_org_roles`; `app_user` + `user_role` via `provision_org_owner`). The
   earlier "no tenant to bind yet / the GUC would be the org that does not exist"
   framing was wrong: the org id is known the moment the exempt row lands.
   **Migration 185** (`185_provision_org_rls_bind.sql`; highest was 184, re-check at
   merge per R1) CREATE OR REPLACEs `provision_organization` forward-only (R6;
   179/180 byte-untouched) to `PERFORM set_config('app.tenant_id', v_org_id::text,
   true)` right after the org id is known and BEFORE the forced writes — the ONE GUC
   seam (`SET LOCAL`, transaction-scoped, propagates into the PERFORMed
   sub-functions), so the create act's own writes satisfy the policy. **SECURITY
   INVOKER preserved** (no privilege escalation, no DEFINER outliving its reason —
   `SET LOCAL` was preferred over SECURITY DEFINER exactly as 179's "REVISIT AT H3"
   note anticipated). The `provisioning.py` factory opens stay allow-listed in
   `_FACTORY_OPEN_ALLOW` (the Python still opens an unbound session; the SQL binds
   inside the act). Fence: `test_h3_rls_promotion_rehearsal.py::
   TestProvisionOrgBindUnderForceRls` (R7 `provision-under-force-rls`; R8, non-priv
   `acb_app` on the phase-4 catalog — GREEN the real 185 provisions unbound; RED the
   no-set_config mutation is refused with a row-level-security violation; the owner
   app_user/user_role write bound-GREEN/unbound-RED; 180's create-only guard still
   holds under the bind). 🔴 EXECUTING a real customer provision stays OWNER-GATE.

**(b) OWNER-DECISION — NOT fixed here (each needs an owner act; all stay 🔴):**
1. **`access_request`** — FORCE-RLS'd (`generated/04_policies.sql`), but its
   writers are **tenant-less by construction**: a sign-in request
   (`_record_signin_request`) is filed for an UNPROVISIONED email, before any
   tenant is known, so nothing can bind. Decision owed: **add `access_request` to
   `gen_tenant_migration.EXEMPT`** (it is control-plane-ish — a queue of people
   who are not yet members) **or** give it a deliberate tenant column + a bound
   writer. Until decided, its writes refuse under phase-4.
2. **`reconcile_orphaned_runs`** (`routes/workflows/service.py:628`, `_get_db()`
   at `:640`) — a CROSS-TENANT startup sweep: `UPDATE workflow_runs SET
   status='failed' WHERE status='running'` over EVERY org's rows. `workflow_runs`
   is FORCE-RLS'd, so unbound under phase-4 it updates 0 rows and dead runs never
   get marked failed. Needs a **per-org loop** (bind each tenant in turn) **or a
   BYPASSRLS maintenance role** — an owner call on the deploy/role side.

**(c) THE H4 DEPENDENCY (the largest open item, blocks the flip for background
features).** ~111 unbound `get_db()` sites remain outside `routes/projects`
(`test_db_engine_seam.py`'s `H2_BASELINE_ELSEWHERE`), each a scheduler / webhook /
consumer / service-identity path that carries no request and so binds no tenant.
Under phase-4 RLS every one reads 0 rows / refuses writes — the feature goes DARK
at the flip, silently. The two background callers of `resolve_session_access`
(`executor._integration_authorizer`, `chat_fold._run_authority`) are in this set:
their fold correctly fails closed to actor-only today, but the shared-run
authority only comes back once they thread an explicit tenant. **H4 (explicit-
tenant threading for these jobs/consumers) MUST land before `IDENTITY_CUTOVER` is
flipped alongside phase-4**, or those features are the ones that go dark. H4 owns
retiring the whole set; this checklist is the flip's precondition list, not H4's
work.

**Gate (D48 — H6 "ships dark, lands dark BEFORE H3").** Building H6 slices DARK — flag OFF,
byte-identical `app_user` writes/reads, no read moved — is 🟢 AGENT-SAFE, and that INCLUDES
slice 3b behind `IDENTITY_CUTOVER` (default OFF). What stays 🔴 OWNER-GATE is only: flipping
`IDENTITY_CUTOVER` on a live box, H3 phase-4 promotion, and running the 182 backfill against
prod. (The earlier "slice 3b … stays OWNER-GATE" wording conflated building-it-dark with
flipping-the-flag; D48 separates them.)

⚠️ **The two upserts that block this — anchors re-measured 2026-08-19:**
`acb_auth/access.py` (`_BOOTSTRAP_OWNER_SQL`) and
`gateway/routes/admin/_common.py` (`_PROVISION_MEMBER_SQL`), both `app_user` upserts.
Line numbers deliberately dropped here on 2026-08-19: they have now drifted three times
(`members.py:173`/`access.py:447`, then `:205`/`:509`, then `:550`/`:599`) and MT-1j slice
6 moved them again. **Re-derive with grep on the SQL constant names**; the parent spec
once cited them in `members.py`, where they have never been. `access.py:205` was never one
of the pair — it is `access_request`'s upsert.

⚠️ ~~**They do not merely block H6 — they are broken today.**~~ **REPAIRED 2026-08-19 —
`saas_multitenancy.md` §11 MT-1j slice 6, which landed before H6 as planned.** Migration
162 had dropped `app_user_email_key`, so `ON CONFLICT (email)` matched no unique index:
the predicted **42P10** reproduced red on a ladder-replayed Postgres, and because Postgres
resolves the arbiter at **plan** time it took out the fresh-insert path as well — meaning
`ensure_owner_bootstrap()` could never bootstrap an owner (it swallowed the error) and
every invite raised. Both statements now say `ON CONFLICT (lower(email))`; fence
`tests/unit/test_app_user_upserts.py` (20 tests, R8, in `pr-check.yml`'s skip guard).
**H6 still rewrites the same two statements onto `org_membership`** — slice 6 made them
work against today's schema and moved no identity.

⚠️ **The invite path is an account-takeover primitive under two orgs** — an
`INSERT … ON CONFLICT (email) DO UPDATE SET organization_id = …` moves a human between
tenants. It must become an insert into `org_membership`, never an update of the identity.

**Done when:** sign-in resolves through `user_identity` + `org_membership`; a test proves
one email can hold active membership in **two** organizations; the invite path cannot move
an existing identity between orgs; `app_user` reads are gone or reduced to a compatibility
view.

---

## H7 · Subdomain tenant resolution (MT-1f) · 🟢 AGENT-SAFE · after H6

**Done when:** the workbench resolves `<slug>.<domain>`; the tenant claim rides the
**authenticated session**; the gateway derives the tenant from the session or a
tenant-scoped API key **only**; and a test asserts an `X-Organization-Id` header, query
parameter or body field is **ignored, not honoured** (R11).

> The subdomain is a **lookup to verify against the session**, never an assertion. A
> trusted subdomain is a header with a friendlier name.

---

## H8 · Blobs and partitioning (MT-1g, MT-1h) · 🟡 needs a window

- **MT-1g:** `agent_blob.content` is `BYTEA` — file content inside Postgres
  (`71_agent_blob_store.sql:30`). Move to object storage keyed `<org_id>/…`; table keeps
  metadata + pointer. Worth doing in any tenancy model.
- **MT-1h:** partition the heavy tables on `organization_id` — **LIST for the largest
  tenants, HASH/default for the tail.** One partition per tenant across all tenants
  recreates the catalog pressure §1.8 rejects. Targets: `email_messages`, the
  `*_embeddings` vector tables, `chat_message`, `audit_event`.
- **Per-tenant logical backup is a required capability, not a side effect.** "Restore this
  one customer to yesterday" must be answerable, and `pg_restore` on a pooled instance
  cannot answer it. Build the export/import job and **run it end to end at least once.**

---

## 4. Open criteria that need your database

Carried here so they are not lost in the parent spec:

1. **`tenancy_and_visibility.md` §2 done-when 3** — the DB-backed two-org behavioural
   fixture for the `group:` expansion. Lives in `test_session_authority.py` /
   `test_rooms.py`, both of which **skip entirely** without Postgres (21 skipped). Seed two
   `organization` rows with identically-slugged `org_group`s and disjoint members; assert
   org A's expansion never admits org B's member. **The PR must quote a run showing
   `passed`, never `skipped`.**
2. **`test_tenant_coverage.py`'s two DB tests** — H3's gate.
3. **The Mem0 decision (§0.1 path 8)** — conninfo options, a per-tenant role, or
   scope-string-only isolation. §2.4 of the implementation spec has the three options and
   their costs. **Leaving it undecided fails MT-1c.**

---

## 5. Standing rules — violating these is how this goes wrong

1. **`SET LOCAL`, never `SET`.** A session-scoped `SET` survives the connection's return to
   the pool and the next borrower — a different customer — inherits it.
2. **`SET LOCAL` needs a real transaction.** Outside one it is a *silent no-op*: every
   query then returns nothing, which reads as "the feature is broken", not "tenancy is
   broken".
3. **Fail closed, everywhere.** Unresolvable tenant → refuse. Never "the usual one". Four
   modules already implement this identically (`key_store._resolve_org`,
   `mutation._self_mutation_permitted`, `placement.resolve_placement`,
   `db.tenant_session`) — match them.
4. **No agent ever gets a raw-SQL tool**, and no agent-reachable path may set
   `app.tenant_id`. §0.9.3 makes this a **condition on the pooled decision**. Violate it and
   §1 must be re-taken.
5. **No second scoping doctrine.** Tenant isolation is `organization_id` + RLS; visibility
   *inside* a tenant stays `email | group:<slug> | org` and is unchanged.
6. **Re-derive every anchor with grep.** Two published anchors were found stale during this
   workstream — one by 62 lines.
7. **Never `ruff check .`** (~1983 pre-existing). Never `pytest tests/unit/` as a directory
   (hangs on a live DB). **A skip is not a pass.**

---

## 6. What is deliberately NOT in scope

- **MT-0c-2, the container/microVM agent tier.** 🔴 OWNER-GATE, parked by **D16**. D10's
  "trusted colleagues" threat model survives the silo phase; T2 becomes a precondition of
  the **§5.1 pooled cutover** (customer 8–12), not of Phase 0. **An agent must refuse to
  build it and say so.**
- **MT-2 … MT-5** (entitlements, AI credits, billing). ~~Blocked on owner inputs~~
  **⚠️ STALE — every owner input is now ANSWERED** (D18 2026-08-09 → D19 → final
  shape **D23 2026-08-10**: Center packages are the sales object — **§2.4b is the
  pricing shape of record**, §2.4's table is the internal atom ledger; credit unit
  + metering scope in §3.2, seat rules in §4.2, Razorpay-only payments, India-only
  residency. ⚠️ D23 also adds MT-2 schema this handover predates: `center_package`,
  `plan_catalog`, and `user_module_seat.source` — see `_implementation.md` §4). What still blocks dispatch is **engineering
  paperwork, not decisions**: the seven-point ticket contract (per-item done-whens
  + verification commands) has not been written onto §2/§3/§4. An implementer
  reading this section must NOT refuse MT-2/MT-3 on "missing owner inputs" — that
  reason expired 2026-08-09. The customer-facing console is scoped separately in
  `specs/subscription_console.md` (WS-30).

---

## 7. References

Parent: [`saas_multitenancy.md`](saas_multitenancy.md) — §0.1 connection inventory (ten
paths) · §0.9 the three planes · §1 tenancy · §5.1 rollout · §6 blockers · §11 tickets.
Shapes: [`saas_multitenancy_implementation.md`](saas_multitenancy_implementation.md) —
§1 migration templates · §2 the seam · §3 control plane · §6 sandbox · §7 runbooks ·
§8 the ten-row trap table.
Binding: [`user_management_contract.md`](user_management_contract.md) **R11** ·
[`tenancy_and_visibility.md`](tenancy_and_visibility.md) §2–§5 (visibility, unchanged) ·
`work_plan.md` WS-29 · **D15** · **D16** · §6 owner-gate registry.
