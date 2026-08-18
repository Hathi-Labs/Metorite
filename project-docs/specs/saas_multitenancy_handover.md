# Multi-tenancy handover — execution runbook for an agent with database access

**Status:** 🟢 **In execution — H1 scratch gate PASSED 2026-08-09** (see H1's result
block; prod apply rides **PR #404**, the owner's merge) · **Created:** 2026-08-08 ·
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

**Done when:** all four phases applied, **and**
```bash
DATABASE_URL=... uv run pytest tests/unit/test_tenant_coverage.py -v -rs
```
shows the two previously-skipped tests as **PASSED**, not skipped.

**GATE:** those two tests pass against the live catalog.

---

## H4 · Bind a tenant in every background job (MT-1d) · 🟢 AGENT-SAFE

*"A job that forgets doesn't leak one row; it leaks unbounded."*

Jobs have no request, so no session to inherit from. Cover: the ingestion scheduler
(`email_ingestion/scheduler.py`, three engines), `inbound.py`, the Redis Streams consumer
(`ingestion/consumer.py`), the reconciler, orchestrator agent runs, and broker handlers.

**Done when:** every queued/scheduled unit carries `organization_id` on its record and
binds it before any DB access; a job constructed **without** one **refuses to run** rather
than defaulting; a test proves the refusal.

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

## H6 · Identity cutover (MT-1a-2) · 🟡 CAREFUL — live sign-in path

Migration 159 created `user_identity` + `org_membership` and seeded them. `app_user` is
still authoritative and **nothing reads the new tables**.

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
