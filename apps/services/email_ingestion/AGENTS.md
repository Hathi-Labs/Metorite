# Email Ingestion — Multi-Provider Email Sync Engine

## Purpose

Fetches emails from connected email accounts (Gmail, Microsoft 365, IMAP/SMTP)
and stores them in the Postgres `email_messages` cache for fast UI queries.
Also provides an aiosmtpd inbound SMTP server for receiving mail directly
(no vendor lock-in) and a background sync scheduler.

## Ownership

- Owner: Metorite Core team
- Path: apps/email_ingestion/
- DB: email_accounts, email_messages, email_attachments, email_folders, email_sync_log

## Architecture

```
providers/
├── base.py        — Abstract BaseEmailProvider + dataclasses (EmailMessage, SyncResult, etc.)
├── gmail.py       — Gmail REST API provider (OAuth 2.0)
├── outlook.py     — Microsoft Graph provider (OAuth 2.0)
├── imap.py        — IMAP/SMTP provider for generic email servers (imaplib + smtplib)
├── factory.py     — build_provider, the one name-to-class seam
├── app_credentials.py — oauth_app, the one reader of the OAuth app credentials
inbound.py         — aiosmtpd inbound SMTP receiver (persists to email_messages)
scheduler.py       — Background sync scheduler (per-account asyncio tasks)
```

## Providers

All providers implement the `BaseEmailProvider` abstract interface:
- `authenticate()`, `list_folders()`, `list_messages()`, `get_message()`
- `send_message()`, `modify_message()`, `trash_message()`
- `sync_messages(history_id)` — incremental sync, returns `SyncResult` with `messages` list
- `get_attachment()`

## Key Contracts

1. **SyncResult.messages** must be populated with full `EmailMessage` objects.
   The sync endpoint and scheduler both use this to persist messages to `email_messages`.

2. **ON CONFLICT (account_id, provider_message_id) DO UPDATE** — upsert pattern ensures
   idempotent syncs. Deleted messages move to `folder='TRASH'` locally.

3. **history_id format is provider-specific:**
   - Gmail: Google historyId (string)
   - Outlook: deltaToken (string)
   - IMAP: `"{last_uid}:{uidvalidity}"` — on UIDVALIDITY change, forces full resync

4. **Credentials** stored as AES-256-GCM encrypted JSONB in `email_accounts.credentials_encrypted`,
   decrypted at sync time through `acb_llm.key_store`.
   The blob holds the tokens of the member and nothing else (WS-17 EM-T3a).
   The OAuth app credentials come from settings through `oauth_app`, and
   `build_provider` passes them in. A provider ignores a `client_id` or
   `client_secret` in the blob, and `export_credentials` drops them. Do not add
   a fallback to the blob, because that keeps a revoked secret alive. The
   Microsoft authority for mail is always `common`. Fence:
   `tests/unit/test_email_connect_backend.py`.

5. **received_at** must be parsed from provider-native format into timezone-aware datetime.
   Never leave it `None` — it's the primary sort key for the message list.

## Inbound SMTP Server

`inbound.py` runs an aiosmtpd SMTP server that accepts inbound emails and persists
them directly to `email_messages`.  Started/stopped via the gateway lifespan.

- Config: `EMAIL_INBOUND_HOST`, `EMAIL_INBOUND_PORT`, `EMAIL_INBOUND_ACCOUNT_ID`
- Wire your domain's MX record or email forwarding at this host:port
- No external provider needed — fully open source

## Background Sync Scheduler

`scheduler.py` manages per-account asyncio tasks that call `_sync_account()` in a loop.
- Launched UNCONDITIONALLY from the gateway lifespan; `start_background_sync()`
  carries its own default-ON launch-defang kill-switch `EMAIL_SYNC_ENABLED`
  INSIDE the function (WS-29) — OFF only on an explicit falsey token
  (`0`/`false`/`no`/`off`), returning `{}` and opening no session. The gate
  lives in the start function, never at the call site. R7:
  `tests/unit/test_launch_defang_kill_switches.py`.
- **Tenancy (WS-17 EM-T1b-1).** The scheduler opens no engine of its own. Every
  session comes from `acb_common.db`. The startup sweep reads the RLS-exempt
  `organization` table once, unbound. It then opens one `tenant_session(org)`
  for each organization. Each per-org statement also filters on
  `organization_id`, so an account binds only its owning organization on a
  catalog without RLS. A failure in one organization does not stop the sweep.
  Each loop binds its organization with `bind_tenant`.
  `_sync_account` takes `organization_id` or reads `current_tenant()`, and
  raises `TenantUnbound` when it has neither.
- ⚠️ **No `commit()` inside a `tenant_session` block.** A commit ends
  `SET LOCAL`, so each statement after it runs with no tenant. Split the work
  into phases, and give each phase its own `tenant_session(org)`. R7:
  `tests/unit/test_email_scheduler_tenancy.py`.
- Interval: `email_accounts.sync_interval_secs` (default 300s)
- Account lifecycle: `refresh_account_sync(account_id, organization_id)` /
  `remove_account_sync()` called from CRUD routes. The organization comes from
  the session or the verified OAuth state, never from request input.
- `get_scheduler_status()` returns state for health checks

## Dependencies

- `aiosmtpd>=1.4.6` — inbound SMTP server
- `sqlalchemy[asyncio]>=2.0` — async Postgres access
- `asyncpg>=0.29.0` — Postgres driver
- `httpx>=0.27.0` — HTTP client for REST APIs (Gmail, Outlook)
- `acb-llm` — credential encryption/decryption
- `acb-common` — settings, logging

## Child DOX Index

None — leaf directory.
