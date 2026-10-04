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
├── base.py        — Abstract BaseEmailProvider, the dataclasses (EmailMessage, SyncResult and the others) and RefreshingBearer
├── gmail.py       — Gmail REST API provider (OAuth 2.0)
├── outlook.py     — Microsoft Graph provider (OAuth 2.0)
├── imap.py        — IMAP/SMTP provider for generic email servers (imaplib + smtplib)
├── factory.py     — build_provider, the one name-to-class seam
├── app_credentials.py — oauth_app, the one reader of the OAuth app credentials
inbound.py         — aiosmtpd inbound SMTP receiver (persists to email_messages)
scheduler.py       — Background sync scheduler (per-account asyncio tasks)
import_window.py   — the import floor: the ceiling, the range and the floor (EM-T6a)
```

## Providers

All providers implement the `BaseEmailProvider` abstract interface:
- `authenticate()`, `list_folders()`, `list_messages()`, `get_message()`
- `send_message()`, `modify_message()`, `trash_message()`
- `sync_messages(history_id)` — incremental sync, returns `SyncResult` with `messages` list.
  It takes `catch_up`, the watermark after a pause. Outlook reads more pages
  back to it. Gmail and IMAP ignore it, because their cursors read each change.
  It takes `delta_shadow` too (WS-17 EM-T4d). Outlook then runs the Graph
  delta after the sweep, and Gmail and IMAP ignore it.
- `import_batches(since, until, size)` — the import in lists, newest first
  across every folder (WS-17 EM-T6b). The default of the base class calls a
  deep `sync_messages`, then sorts and cuts. Outlook merges one page stream
  for each folder, and reads the next page of a folder only when that folder
  held the newest head. It awaits `on_estimate` once with the sum of the
  folder counts, or with `None`.
- `get_attachment()`

## Key Contracts

1. **SyncResult.messages** must be populated with full `EmailMessage` objects.
   The sync endpoint and scheduler both use this to persist messages to `email_messages`.

2. **ON CONFLICT (account_id, provider_message_id) DO UPDATE** — upsert pattern ensures
   idempotent syncs. Deleted messages move to `folder='TRASH'` locally.

3. **history_id format is provider-specific:**
   - Gmail: Google historyId (string)
   - Outlook: NULL, or the JSON cursor of the delta shadow,
     `{"v": 1, "folders": {<sweep key>: {"link": <url>, "at": <UTC time>}}}`
     (WS-17 EM-T4d). The sweep never reads it. Text that does not parse is
     no cursor, and each folder seeds again.
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

6. **The bearer goes on each request (WS-17 EM-T4c).** `_get_client` in Gmail and
   Outlook sets no `Authorization` header. It passes `auth=RefreshingBearer(self)`,
   which reads `_access_token` for each request. On a 401 it refreshes once under
   `_refresh_lock` and sends the same request once more. A second 401 goes back to
   the caller. Fence: `tests/unit/test_email_provider_401_retry.py`.
   - **One refresh for each token that a refresh cannot help.** The flow keeps
     the token whose refresh the token endpoint refused, or whose new token got a
     401 too. Each later 401 with that token goes back to the caller with no
     refresh. A success with that token clears it.
   - Only a 400 or a 401 from the token endpoint, or missing app credentials, is
     a refusal (`_refresh_refused`). A timeout, a transport error or a 5xx can
     pass, so the next 401 tries the refresh again.
   - `authenticate` does not refresh a token that a refresh on the same instance
     made. A sync calls `authenticate` two times, so without this rule a mailbox
     that refuses each request costs three token posts in one tick.

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
- ⚠️ **No session across an external call (WS-17 EM-T4a-1).** Phase (b)
  calls the provider with no session open. Phases (e) and (f) read in one
  `tenant_session(org)`, then call the provider or the model with no session
  open. Then they write in a second block. The steps in `body_backfill.py`
  and `email_embeddings.py` take a session, open none, and never call
  `commit()`. R7: `tests/unit/test_email_scheduler_tenancy.py`.
- ⚠️ **The error path keeps refreshed credentials (WS-17 EM-T4c).** A refresh
  during the sync makes the credentials dirty. The error path then writes them
  in its own `tenant_session(org)`, beside the error status. Microsoft can
  rotate a refresh token on use, so a lost new token can force a reconnect.
  R7: `tests/unit/test_email_provider_401_retry.py`.
- ⚠️ **Phase (e) can refresh after phase (d) wrote the credentials.** So a
  short block after phase (e) writes them again, but only when they changed
  after phase (d). That write never fails the sync. A failed write goes again
  once in a new block, and the log names the class of the error, never a token.
- ⚠️ **The import floor binds every sync, and the ceiling binds every path
  (WS-17 EM-T6a, D-EM-10).** `import_window.py` is the one owner of the
  ceiling (180 days), the range (0 to 6 months) and the floor. Do not write a
  second date rule. `_sync_account` passes the floor on every sync, deep or
  shallow. An explicit `since` of a member act binds when it is newer than the
  ceiling. Otherwise `import_since` binds. Outlook sends the floor on each
  sweep. The core drops a message below the floor before phase (c), unless
  Metorite holds its row. Clean older mail with no date passes the ceiling.
  The "Load older" backfill of a folder (`gateway/routes/email/transport/folders.py`)
  writes nothing below the ceiling and stops paging there. Every floor is in
  UTC. R7: `tests/unit/test_email_import_floor.py`.
- ⚠️ **The import runs in batches (WS-17 EM-T6b).** While `initial_sync_done`
  is false, `_import_in_batches` runs before the recurring sweep. It fetches
  each batch with no session open. One `tenant_session(org)` then writes the
  batch and its progress, through `_write_messages`, the one write of phase
  (c). The progress columns are `import_phase`, `import_count`,
  `import_estimate` and `import_reached_at`. When an import fails, they stay,
  and the next sync resumes with `until = import_reached_at`. A deep sync of a
  member act (`deep=True`) imports in batches too, and writes no progress and
  no `initial_sync_done`. No import batch runs the reconcile or the label
  learner. The recurring sweep runs after each import in the same call, so
  new mail always syncs (owner answer Q2, spec §10.2). It reads back to
  `last_synced_at - 1 hour`, or `created_at`, after a pause (D-EM-13). R7:
  `tests/unit/test_email_import_batches.py`.
- ⚠️ **A failure never stops new mail (EM-T6b fix rounds 2 and 3, owner
  answer Q2).** A failed import does not raise. The cycle still runs the
  recurring sweep and phase (c). Phase (d) then writes `sync_status =
  'error'` with the error, and the next cycle resumes the import. A failed
  import on a mailbox that never synced keeps `last_synced_at` NULL, so the
  next sweep reads back to `created_at`.
- ⚠️ **A sweep folder that stops short keeps the watermark (EM-T6b fix
  rounds 3 and 4).** A first page that fails with a status other than 403 or
  404 leaves its folder short. A later page before the watermark does too.
  The sweep keeps the pages that it read and sets `catch_up_incomplete`.
  Phase (d) writes that mail, keeps `last_synced_at` and writes a
  `sync_error` note with the folder name only. The loop counts the cycle as
  a soft failure and backs off.
  - **Only the loop counts.** A cycle of the loop passes `from_loop=True`,
    and only that cycle adds to the count. The webhook, the manual sync, the
    rerun and the deep downloads run when mail arrives or a member acts. If
    they counted, a busy mailbox could abandon a gap in minutes.
  - **The abandon is sticky.** After `CATCH_UP_MAX_MISSES` (6) short cycles
    of the loop in a row, the watermark moves on, and the log says
    `sync.catch_up_abandoned` with the folder and the gap. A later short
    cycle adds no count, keeps no watermark and does not back off. It writes
    the note of the abandon again. Only a complete cycle clears the count
    and the abandon.
  - The count and the abandon live in the process, so a restart clears
    them. The note reaches the API only, because the UI shows `sync_error`
    only when `sync_status` is `error`. The later full fix is a watermark for
    each folder. R7: `tests/unit/test_email_import_batches.py`.
- ⚠️ **A 403 or a 404 skips only Archive or a user folder (EM-T6b fix round
  4).** The import and the recurring sweep use one rule, `_skips_folder` in
  `providers/outlook.py`. On Inbox, Sent, Drafts, Junk or Deleted Items, a
  403 or a 404 fails the cycle, so the member sees the error. R7:
  `tests/unit/test_email_import_batches.py`.
- ⚠️ **The Graph delta runs in shadow only, and the sweep stays the one
  writer (WS-17 EM-T4d).** `email_outlook_delta` is `off`, `shadow` or `on`,
  and `email_outlook_delta_accounts` lists the mailboxes that run it.
  `scheduler.outlook_delta_mode` is the one reader. `on` resolves to `shadow`
  and logs `email.delta_mode_refused`. In `shadow`, `sync_messages` reads the
  delta of each swept folder AFTER the sweep. It compares the new mail, and
  `_sync_cycle` logs `email.delta_shadow` with counts only. The cursor goes
  into `last_history_id`. Phase (d) keeps it through `COALESCE`, so `off`
  never clears it.
  - A delta failure never changes the four sweep fields. A 410 drops the
    link of its folder, and any other failure keeps it.
  - No `@removed` item becomes a `[DELETED]` marker, so the reconcile reads
    the sweep alone.
  - One poll reads at most 20 delta pages for each folder.
  - 🔴 A value other than `off` on a box is OWNER-GATE (`enforcement-flip`).
    Shadow ADDS Graph calls, so list a test mailbox only. R7:
    `tests/unit/test_outlook_delta_shadow.py`.
- ⚠️ **An Outlook import pages by time, never by `$skip` (EM-T6b fix rounds
  1 and 2).** Each next page is a new query with `lt` the second after the
  oldest message of the last page. Exchange keeps a fraction of a second, and
  `le` that second dropped the rest of a split second. The stream drops the
  ids that it read again. When one second fills a page, one query with
  `$top=1000` reads that second. A 429, 503 or 504 page tries once more
  after `Retry-After`.
- ⚠️ **Three guards protect the reconcile of a member-act import (EM-T6b
  fix rounds 2 and 3).** `reconcile.import_reconcile_candidates` takes
  `(id, folder, received_at)` tuples. It keeps a row written after phase
  (a), because a move, a rule action or a draft during the import writes it
  and Graph gives a moved message a new id. When a folder has more
  candidates than 50, or 2% of its rows, the reconcile leaves that folder
  and logs `sync.import_reconcile_skipped`. Then, with no session open, the
  provider looks up each candidate by `internetMessageId`, at most 50 for
  each folder. A message that Graph still has keeps its row, because the
  member moved it in the Outlook client. A failed lookup, or a row with no
  internet message id, keeps its row too. `trash_import_rows` checks
  `updated_at` again in its own block.
- Interval: `email_accounts.sync_interval_secs` (default 300s)
- Account lifecycle: `refresh_account_sync(account_id, organization_id)` /
  `remove_account_sync()` called from CRUD routes. The organization comes from
  the session or the verified OAuth state, never from request input.
- ⚠️ **Call `remove_account_sync` with no session open (WS-17 EM-T4f).** It
  waits for the loop task. A caller with an open block holds its locks for as
  long as the task runs. A disconnect reads the row first, then stops the
  loop, then deletes in a new block. R7:
  `tests/unit/test_email_disconnect_order.py`.
- ⚠️ **One sync at a time for each mailbox (WS-17 EM-T4f part 2).**
  `_sync_account` takes an `asyncio.Lock` for the mailbox, then runs
  `_sync_cycle`. Call `_sync_account` and never `_sync_cycle`, or two syncs
  of one mailbox upsert the same keys and one waits on the uncommitted rows
  of the other. The lock key is the id in lower case. The loop, the webhook,
  the manual sync and the resync pass `if_busy="skip"` and get
  `SYNC_SKIPPED_BUSY`. A skip marks the mailbox, and the holder then runs ONE
  more shallow cycle under the lock (`_rerun_once`). The deep downloads wait
  up to `SYNC_LOCK_WAIT_SECS` (600 seconds), and `download_failure` turns a
  busy or failed result into a job error. A resync passes `purge` and
  `reset_cursor`, and phase (a) applies them under the lock. A new call must
  pass a constant mode. The lock lives in this process, which is enough while
  the gateway is one uvicorn process. Call it with no session open. R7:
  `tests/unit/test_email_sync_one_at_a_time.py`.
- The loop stops when its row is gone (`ACCOUNT_GONE`). It drops its entry
  from `_scheduler_tasks` only while the entry is its own task, and it takes
  no `_scheduler_lock` for that.
- `OutlookProvider.delete_subscription` returns the HTTP status of Graph and
  raises on a transport error. The caller decides what a status means.
- `get_scheduler_status()` returns state for health checks. A disconnect
  reads it to know whether a loop ran, so a failed disconnect starts only a
  loop that ran before.

## Dependencies

- `aiosmtpd>=1.4.6` — inbound SMTP server
- `sqlalchemy[asyncio]>=2.0` — async Postgres access
- `asyncpg>=0.29.0` — Postgres driver
- `httpx>=0.27.0` — HTTP client for REST APIs (Gmail, Outlook)
- `acb-llm` — credential encryption/decryption
- `acb-common` — settings, logging

## Child DOX Index

None — leaf directory.
