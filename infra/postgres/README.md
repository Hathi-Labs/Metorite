# Postgres schema (`infra/postgres/`)

The database schema for Metorite — chat history, the email subsystem, and
the core business/infra tables. This directory is the **authoritative schema**:
there is no ORM-managed schema; everything is plain SQL migrations.

## Source of truth

- **Current shape of any table** → [`schema.generated.sql`](./schema.generated.sql)
  (a consolidated `pg_dump --schema-only` snapshot). Read this to answer "what
  columns does `email_assistant_settings` have *right now*?" without replaying
  40+ migrations. **It is generated — never hand-edit it.** Regenerate with
  [`scripts/dump_schema.sh`](../../scripts/dump_schema.sh).
- **History / intent of a change** → the numbered `NN_*.sql` migration files.
  Each carries a header comment explaining what and why.

> If `schema.generated.sql` is missing, run `scripts/dump_schema.sh` against a
> DB that has had all migrations applied. It needs the `acb-postgres` container
> running (see `infra/docker-compose.yml`).

## How migrations work (read before adding one)

- Files apply in **lexical/numeric order** (`00_*`, `01_*`, … `44_*`) via
  [`scripts/apply_migrations.sh`](../../scripts/apply_migrations.sh).
- **`00_*` and `01_*` are init-only.** `docker-compose.yml` mounts *only* those
  two into `/docker-entrypoint-initdb.d`, which runs **once** on an empty data
  volume. Everything `02+` reaches the DB **only** because `apply_migrations.sh`
  re-runs it on every deploy. ⚠️ A fresh `docker compose up` *without* running
  the migration script yields a DB missing chat history, all email tables, etc.
  **Do not add new tables to the compose init mount** — rely on the runner.
- Every `02+` migration **must be idempotent**: `CREATE TABLE/INDEX IF NOT
  EXISTS`, `ADD COLUMN IF NOT EXISTS`, `INSERT … ON CONFLICT DO NOTHING`, guarded
  `DO $$ … $$`. The runner executes all of them on every deploy.

### Adding or changing schema
1. Add `NN_<topic>.sql` (next number) with a header comment (what / why /
   `Depends on:`), written idempotently.
2. Run `scripts/apply_migrations.sh` (locally or it runs on deploy).
3. Run `scripts/dump_schema.sh` and commit the refreshed `schema.generated.sql`
   alongside your migration.

## Conventions (two dialects exist — match the table's era)

- **Naming:** `00–02` use **singular** names (`chat_session`, `chat_message`,
  `person`) with `*_idx` index suffixes. `08+` use **plural** (`email_accounts`,
  `email_messages`) with `idx_<table>_<cols>` prefixes. New tables should follow
  the `08+` (plural + `idx_`) convention.
- **PKs:** chat tables use client-supplied `TEXT` ids; email/infra tables use
  `UUID DEFAULT gen_random_uuid()`.
- **Timestamps:** `TIMESTAMPTZ DEFAULT now()` everywhere. Exception:
  `chat_message.timestamp_ms BIGINT` (JS epoch-ms) is the column the hot read
  sorts on.
- **Status/enum columns** in the email subsystem are free `TEXT` with allowed
  values only in comments (the `00–09` tables use `CHECK (… IN (…))`). Prefer a
  `CHECK` constraint for new status columns to prevent casing/typo drift.

## Schema map

### Chat (UI history) — `02`, `10`
| Table | PK | Purpose | Key cols |
|---|---|---|---|
| `chat_session` | `id TEXT` | one row per sidebar conversation | `user_id`, `agent_name`, `service_session_id` |
| `chat_message` | `(session_id, id)` | one settled message turn; `session_id → chat_session ON DELETE CASCADE` | `role`, `content`, `timestamp_ms`, `tool_events`/`progress_lines`/`custom_events` (JSONB), `reasoning`, `agent_state` (JSONB) |

Hot read (`apps/gateway/gateway/routes/chat.py :: _get_messages`):
`WHERE session_id=? [AND timestamp_ms < ?] ORDER BY timestamp_ms DESC LIMIT ?`,
served by `chat_message_session_ts_idx (session_id, timestamp_ms DESC)` (`44`).

### Email subsystem — `17`–`44`
All email tables hang off `email_accounts (id UUID)` and declare explicit
`ON DELETE` FKs (`CASCADE` for children; `SET NULL` for the audit log so history
survives). Highlights:

| Table | Purpose |
|---|---|
| `email_accounts` | connected mailbox (provider, encrypted creds, webhook, sync state) |
| `email_messages` | synced messages (`from/to/cc/bcc` JSONB, `labels[]`, `categories[]`, FTS GIN); well-indexed on `(account_id, folder, received_at DESC)` |
| `email_attachments` | `message_id → email_messages CASCADE` |
| `email_folders` / `email_sync_log` | folder map / sync history |
| `email_assistant_settings` | per-account AI config (PK = `account_id`): the three model roles (`rule_model`/`draft_model`/`chat_model`), digest, follow-up, cold-blocker, writing style |
| `email_rules` / `email_actions` / `email_executed_rules` | rule engine + audit log |
| `email_newsletters` / `email_senders` / `email_cold_senders` | bulk/sender classification |
| `email_contacts` (`119`) | the people directory the mailbox learns by itself — name, title, org, phones, links per (account, address), parsed from the person's own signature by the contact card. `manual_fields[]` names columns a human edited; the signature writer never overwrites those. Counts / last-seen are NOT here (derived live from `email_messages`) |
| `email_knowledge` / `email_learned_patterns` / `email_rule_patterns` | draft knowledge + learned classification patterns |
| `email_thread_status` / `email_ai_drafts` | per-thread Reply-Zero status + AI draft cache |

> Drafts themselves live at the provider and are mirrored in the frontend store
> — there is no `email_drafts` table.

### Core / infra — `01`, `03`–`15`, `35`
`person/customer/project/task/deal/message/meeting/action_item/audit_event`
(`01`, business knowledge — note `message` here is *not* chat history),
`pending_commit` (`03`), `provider_keys` (`08`/`11`), `app_user` (`09`),
`dynamic_agents` (`15`), `model_config` (`35`, runtime model visibility/tiers),
plus mem0 / eval / plugins / mcp / custom_api tables (`06`/`07`/`12`/`13`/`14`).
