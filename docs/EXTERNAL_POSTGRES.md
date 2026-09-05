# Running Metorite on a managed Postgres (Supabase et al.)

The application seam (`acb_common.db`, one `DATABASE_URL`) is fully portable.
What assumes a local dockerised Postgres is the **ops shell** around it. This
page is the complete delta for pointing a Metorite box at a managed database.
Written 2026-08-17 for the metorite.com production bring-up (HANDOFF H-11).
The tenant DB and the customer-console DB are **separate** Supabase projects.
The two planes each define an `organization` table with a different shape, so
they cannot share a schema.

⚠️ **The dashboard names invert this page's vocabulary. Read the config
variable, never the project name.** The owner named the projects for what they
hold, which is clearer, and the opposite of the word "tenant" below:

| Supabase project (2026-09-02) | This page calls it | The variable that points at it |
|---|---|---|
| Metorite **Application** Database | the tenant plane | `DATABASE_URL` · `PGHOST` · `POSTGRES_USER` |
| Metorite **Tenant** Database | the customer console | `CUSTOMER_CONSOLE_DATABASE_URL` |

A DSN in the wrong one of those two is not a typo. It puts our provider
credentials and the credit ledger on a customer's data plane.

## 1. Connection string

- **Use the session-mode pooler DSN** (port 5432 on Supabase's pooler host).
  Direct `db.<ref>.supabase.co:5432` is IPv6-only on most plans; the
  **transaction**-mode pooler (`:6543`) breaks asyncpg's server-side prepared
  statements — if you must use it, also set `DB_STATEMENT_CACHE_SIZE=0`
  (see `acb_common/settings.py`).
- `DATABASE_URL=postgresql+psycopg://postgres.<ref>:<password>@<pooler-host>:5432/postgres`
  — keep the `+psycopg` form (the sync engine uses it verbatim; the async seam
  coerces to asyncpg itself).
- `?sslmode=require` is supported on **both** engines: psycopg natively, and
  the async seam renames it to asyncpg's `ssl=` (`acb_common/db.py`; fence:
  `tests/unit/test_dsn.py`). Both drivers negotiate TLS by default, so it is
  optional. **Nothing else belongs in the query string** — params are
  forwarded as connection options, so a SQLAlchemy dialect-only arg (e.g.
  `prepared_statement_cache_size`) fails the connect; use the env knobs
  instead.
- Also set `POSTGRES_USER` / `POSTGRES_DB` in `.env` to the managed values —
  the shell tooling (migration runner, preflight) reads those two keys, not
  `DATABASE_URL`, and the runner's explicit `-U` outranks any `PGUSER` env.

## 2. One-time schema bootstrap

`infra/postgres/01_schema.sql` (extensions + the nine core tables) only ever
ran via the container's initdb hook, and `apply_migrations.sh` skips `00_*` /
`01_*` by design. On a fresh managed DB run, once:

```bash
PGHOST=... PGPORT=5432 PGUSER=postgres PGPASSWORD=... PGSSLMODE=require \
  PG_DB=postgres bash scripts/bootstrap_external_db.sh
```

It creates `uuid-ossp`, `vector`, `pg_trgm` **in the `public` schema**
(managed dashboards default extensions into an `extensions` schema, which
breaks every `uuid_generate_v4()` column default), verifies, then applies
`01_schema.sql`. Idempotent.

## 3. Migrations

`apply_migrations.sh` already has the seam: `PG_MODE=local` switches it from
`docker exec acb-postgres` to plain `psql` over libpq. Put in the box `.env`:

```
PG_MODE=local
PGHOST=<pooler-host>
PGPORT=5432
POSTGRES_USER=postgres.<ref>
POSTGRES_DB=postgres
PGPASSWORD=<password>
PGSSLMODE=require
SKIP_PRE_MIGRATION_BACKUP=1
```

(`POSTGRES_USER`/`POSTGRES_DB`, not `PGUSER`/`PGDATABASE`: the runner passes
an explicit `-U "$PG_USER"` computed from those two keys, and an explicit
`-U` outranks the `PG*` env vars.)

⚠️ **To target a DIFFERENT database without editing `.env`, export `PG_USER`
and `PG_DB`.** Those two names are the override. `POSTGRES_USER` and
`POSTGRES_DB` are read from `.env` only, so exporting them changes nothing.
The runner resolves `PG_USER="${PG_USER:-${env_user:-acb}}"`, and its own
comment says this is "how a replay targets a scratch database".

Measured 2026-09-02, on the Mumbai bring-up. A run that exported
`POSTGRES_USER` for the new project still authenticated as the OLD project's
role, because `.env` still named it. The failure reads as a host problem,
because the host in the error is the new one:

    FATAL: (ENOTFOUND) tenant/user postgres.<old-ref> not found

`scripts/vps_apply.sh` lifts exactly those keys from `.env` into the runner's
environment before it starts (the deploy delivers the script over stdin, so
`.env` is the only channel that survives).

`SKIP_PRE_MIGRATION_BACKUP=1` is a **deliberate, understood trade**
(`apply_migrations.sh` spells out what it waives): the local backup path needs
`pg_dumpall --globals-only` and `createdb` — superuser on the cluster, which a
managed provider does not grant. The provider's PITR is the restore point —
turn it on before flipping this. The apply script disables `acb-backup.timer`
automatically when it lifts `PG_MODE=local` (and keeps it disabled on every
run): the unit has no EnvironmentFile, would default to the local container,
and a dump of that EMPTY container passes `--verify-restore` — a green false
restore point.

## 3.1 🔴 The ladder brings up NO row-level security

**A database built only from `apply_migrations.sh` has ZERO tenant isolation.**
Every page loads, every query works, and every tenant reads every other tenant.
Nothing warns you.

Measured 2026-09-02 on a fresh Mumbai project, straight after the runner
reported `193 applied, 0 already recorded`:

    tables 157 · migrations 193 · rls_on 0 · rls_forced 0

The cause is structural, not a defect. **No numbered migration contains
`ENABLE ROW LEVEL SECURITY`.** All 140 live in
`infra/postgres/generated/04_policies.sql`, and the ladder never replays
`generated/`. So a new deployment must apply the four generated files, in
order, as the owner:

```bash
cd infra/postgres/generated
for f in 01_add_columns.sql 02_backfill.sql 03_constraints.sql 04_policies.sql; do
  psql "$DSN" -v ON_ERROR_STOP=1 -f "$f"
done
```

⚠️ **`04_policies.sql` is a CLIFF.** After it applies, a connection that has
not bound `app.tenant_id` reads zero rows. Its header states the prerequisite:
a deploy must land MT-1c first, and somebody must verify it. Check the box
before you run this file — `IDENTITY_CUTOVER=true` and
`ACB_GRAPH_TENANT_BIND=true`, with `EMAIL_SYNC_ENABLED` and
`WORKFLOW_SCHEDULER_ENABLED` false.

**Count the rows to verify, never trust the exit code.** Expect `rls_on = rls_forced =
policies = 140` — the number `04_policies.sql` declares in its own header.
A live database may read higher, because it can carry drift from earlier
manual acts. The generator is the authority, not a running system.

## 4. Things that will bite (each verified in this tree)

| Trap | Where | What to do |
|---|---|---|
| `scripts/setup_secrets.sh` rewrites `DATABASE_URL` to `localhost` and rotates `GATEWAY_INTERNAL_TOKEN` | `setup_secrets.sh:13-25` | **Never run it on a managed-DB box.** |
| `scripts/import_hr_people.py` defaults to a localhost DSN when `DATABASE_URL` is unset | `:173` | export `DATABASE_URL` first |
| The compose `core` profile still starts the (unused) `acb-postgres` container | `infra/docker-compose.yml` | harmless; remove from the profile only as its own reviewed change |
| Pool arithmetic assumes `max_connections=100` | `acb_common/settings.py` (`db_pool_size`/`db_max_overflow`) | redo the math against the provider tier; pooler mitigates |
| Agent workspace files are BYTEA **in Postgres** (no object store) | `infra/postgres/71_agent_blob_store.sql` | size the provider plan for it |
| RLS policies are staged, NOT applied | `infra/postgres/generated/04_policies.sql` | on a LIVE database, promoting them is a planned maintenance act (see its header). On a NEW one, §3.1 applies — skip it and the database has no tenant isolation at all |
| Customer Console DSN is separate and has no default | `customer_console/db.py` (`CUSTOMER_CONSOLE_DATABASE_URL`) | second project; never point it at the tenant DB |

## 5. What is NOT replaced

Redis stays a local container (`REDIS_URL`) — Supabase does not provide it.
LiteLLM runs in-process (no proxy). Meeting media and call recordings remain
on the box's disk.
