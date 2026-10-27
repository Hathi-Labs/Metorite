# Email Ingestion — Multi-Provider Email Sync Engine

History of this file is in git (git log -p).

## Purpose

Fetches emails from connected email accounts (Gmail, Microsoft 365, IMAP/SMTP)
and stores them in the Postgres `email_messages` cache for fast UI queries.
Also provides an aiosmtpd inbound SMTP server for receiving mail directly
(no vendor lock-in) and a background sync scheduler.

## Ownership

- Owner: Metorite Core team
- Path: apps/services/email_ingestion/
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
persist.py         — the message upsert
reconcile.py       — the reconcile of rows that the provider deleted
post_sync.py       — the hooks after a sync, and their flag readers
import_window.py   — the import floor: the ceiling, the range and the floor
storage.py         — the storage meter, the limit, and the removal of older mail
html_tier.py       — the HTML hot window and its two flags
llm_cap.py         — the cap and the daily budget of the email model calls
```

A bare test file name below is under `tests/unit/`. Keep the contract numbers,
because specs cite them.

## Providers

All providers implement the `BaseEmailProvider` abstract interface:
- `authenticate()`, `list_folders()`, `list_messages()`, `get_message()`
- `send_message()`, `modify_message()`, `trash_message()`, `get_attachment()`
- `sync_messages(history_id)` — incremental sync. It returns `SyncResult` with a `messages` list. It takes `catch_up`, the watermark after a pause, and `delta_shadow`.
- `import_batches(since, until, size)` — the import in lists, newest first across every folder.
- `get_inline_images(...)` — the parts of one message with a Content-ID, for the `cid:` pictures of the reading pane. It answers `InlineRead`, and IMAP gives none.

## Key Contracts

1. **SyncResult.messages** must hold full `EmailMessage` objects. The sync endpoint and the scheduler both persist from it.
2. **ON CONFLICT (account_id, provider_message_id) DO UPDATE** makes a sync idempotent. A deleted message moves to `folder='TRASH'` locally.
   - Only a marker with `EmailMessage.deletion_marker` deletes a row in `drafts`. Never key the rule on the subject.
   - `persist.upsert_message` reclaims a re-keyed row only with `reclaim=True`. Only Outlook sets `REKEYS_MESSAGE_IDS`, and a new caller names `reclaim=`. Fence: `test_email_rekey_reclaim.py`.
   - Only Outlook sets `forwards_natively`. A 413 of Graph raises `OutlookMailTooLarge`, a `ProviderMailTooLarge`. Fence: `test_email_forward.py`.
3. **history_id format is provider-specific:**
   - Gmail: the historyId as ASCII digits, or `{"held_cycles": <n>, "history_id": <id>, "v": 1}` while a failed fetch holds it. Other text is no cursor.
   - Outlook: NULL, or the JSON cursor of the delta shadow. The sweep never reads it.
   - IMAP: `"{last_uid}:{uidvalidity}"`. A UIDVALIDITY change forces a full resync.
4. **Credentials** are AES-256-GCM encrypted JSONB in `email_accounts.credentials_encrypted`, decrypted at sync time through `acb_llm.key_store`.
   - The blob holds the member's tokens only. The OAuth app credentials come from settings through `oauth_app`, and `build_provider` passes them in.
   - Do not add a fallback to the blob, because that keeps a revoked secret alive. The Microsoft authority for mail is always `common`. Fence: `test_email_connect_backend.py`.
5. **received_at** must be a timezone-aware datetime. Never leave it `None`, because it is the main sort key of the message list.
6. **The bearer goes on each request.** Gmail and Outlook pass `auth=RefreshingBearer(self)` and set no `Authorization` header.
   - On a 401 it refreshes once under `_refresh_lock` and sends the request once more. A second 401 goes back to the caller.
   - When the token endpoint refuses a refresh (`_refresh_refused`), that token gets no second refresh. Fence: `test_email_provider_401_retry.py`.
7. **One cap and one daily budget for the email model calls.** `llm_cap.py` owns both, imports no `gateway` module, and reads the tenant from `acb_common.db.current_tenant`.
   - A mailbox job opens `automation_job` or `automation_scope`. A member-driven call never opens it.
   - `llm_slot` wraps the leaf model await only, never an enclosing function. The AST fence finds a model await outside it.
   - 🔴 `enforce` of the budget on a box is OWNER-GATE. A Redis fault opens a breaker, and the call runs. Fence: `test_email_llm_cap.py`.
8. **The Gmail parse.** `GmailProvider.get_message` is the one caller of `_parse_gmail_message`. Fence: `test_gmail_parse.py` (fixtures in `tests/unit/fixtures/gmail/`).
   - `_iter_gmail_parts` is the one walk of the MIME tree. Do not add a second walk.
   - `_fill_attachment_bodies` fetches a body part by `attachmentId` once, within `GMAIL_FILL_MAX_BYTES`. Do not lift that cap for one route.
   - `internet_message_id` keeps its angle brackets and its case, because `automation/identity.py` compares with `=`. Only the system labels set the folder.
9. **The Gmail client seam.** `_get_client` sets `auth=GmailBearer(self)`, which adds the Gmail rate limits to each call. Do not add a retry at a call site.
   - Only an idempotent request goes again, so a send never goes out twice. A new POST that a second try cannot change goes into `_REPEATABLE_POST_ACTIONS`.
   - A spent rate limit raises `GmailRateLimited`, so the sync fails and the cursor stays. Fence: `test_gmail_rate_limits.py`.
10. **The Gmail history cursor** lives in `email_accounts.last_history_id`.
   - `scheduler._seed_before_import` seeds BEFORE the import. A failed seed never stops new mail.
   - A finished page moves the position to the `id` of its last record, never to the `historyId` of the answer. A 404 means a stale cursor and reseeds.
   - Fence: `test_gmail_history_cursor.py` (R8 cases).
11. **Gmail send and drafts.** `_build_gmail_mail` is the one MIME builder of `send_message`, `create_draft` and `update_draft`. Do not add a second one.
   - The local row holds the MESSAGE id of a draft, and `update_draft` returns a new one. A caller moves its row to that id.
   - `update_draft` reads the draft's files and inline parts back from Gmail (`_draft_files`), and a failed read fails the update. Fence: `test_gmail_send_and_drafts.py`.
12. **The Gmail import.** `GmailProvider.import_batches` pages one `messages.list` of all mail. Do not put back a list for each label.
   - A 5xx or a transport error fails the import, and the next cycle resumes. `IMPORT_MAX_PAGES` caps it.
   - Fences: `test_gmail_import.py`, `test_email_import_batches.py` (R8).
13. **A file on an Outlook draft.** `_attach_files` adds each file or raises `ProviderAttachmentFailed`. Fence: `test_outlook_attachments.py`.
   - Upload-session PUTs go through a client with no auth, never the Graph client. No error text or log line holds the upload URL (`_UploadUrlFilter`).
14. **The Gmail move and the filter list.** Fences: `test_gmail_move_and_filters.py`, `test_move_into_sent_refused.py`.
   - No provider moves a mail into Sent or Drafts (`MOVE_REFUSED_FOLDERS`). Read the folder after a move only through `local_folder_after_move`.
15. **The Gmail reconcile.** The confirm asks Gmail by the provider id through `message_gone`, never by the Message-ID. Do not give Gmail a `message_exists`.
   - Only a 404 with the reason `notFound` is a delete. Rows in drafts stay out (`import_reconcile_skips_drafts`). Fence: `test_gmail_import.py`.
16. **The size of a Gmail mail.** `GmailProvider._write_mail` is the one write of a send or a draft. Do not send a write around it.
   - Never use `files=` for the upload, because it builds `multipart/form-data`. A mail over `GMAIL_MAIL_MAX_BYTES` raises `GmailMailTooLarge`. Fence: `test_gmail_mail_size.py`.
17. **The To of a reply draft.** `create_draft` takes a keyword-only `exact_to: bool = False`. The fence counts its callers, and a new caller fails it. Fence: `test_outlook_draft_cc.py`.
18. **The HTML hot window.** `html_tier.py` is the one owner of the window and of `EMAIL_HTML_FROM_PROVIDER` and `EMAIL_HTML_HOT_ONLY`. Fence: `test_email_html_tier.py`.
   - No other module reads the two flags or writes the number 90 next to `received_at`. Neither `html_tier.py` nor `import_window.py` imports the other.
   - A new writer of `body_html` uses the same rule and joins `ALLOWED_HTML_WRITERS`. `_html_to_text` is the one text maker, and it runs in linear time. Fence: `test_email_html_hot_only.py`.

## Inbound SMTP Server

`inbound.py` runs an aiosmtpd SMTP server that accepts inbound emails and
persists them directly to `email_messages`. The gateway lifespan starts and stops it.

- Config: `EMAIL_INBOUND_HOST`, `EMAIL_INBOUND_PORT`, `EMAIL_INBOUND_ACCOUNT_ID`
- Wire your domain's MX record or email forwarding at this host:port

## Background Sync Scheduler

`scheduler.py` manages per-account asyncio tasks that call `_sync_account()` in a loop.
- The gateway lifespan launches it unconditionally. The kill switch `EMAIL_SYNC_ENABLED` (default on) lives INSIDE `start_background_sync()`, never at the call site. Fence: `test_launch_defang_kill_switches.py`.
- **Tenancy.** The scheduler opens no engine of its own. The startup sweep reads `organization` unbound, then opens one `tenant_session(org)` for each organization. `_sync_account` raises `TenantUnbound` with no tenant.
- ⚠️ **No `commit()` inside a `tenant_session` block**, because a commit ends `SET LOCAL`. Give each phase its own `tenant_session(org)`. Fence: `test_email_scheduler_tenancy.py`.
- ⚠️ **No session across an external call.** Read in one block, call the provider or the model with no session open, then write in a second block.
- ⚠️ **The error path keeps refreshed credentials.** Microsoft can rotate a refresh token on use. Fence: `test_email_provider_401_retry.py`.
- ⚠️ **The import floor binds every sync, and the ceiling binds every path.** `import_window.py` is the one owner of the ceiling (180 days), the range and the floor. Do not write a second date rule. Fence: `test_email_import_floor.py`.
- ⚠️ **The import runs in batches.** `_write_messages` is the one write of phase (c). A failure never stops new mail: the recurring sweep runs after each import. Fence: `test_email_import_batches.py`.
- ⚠️ **A sweep folder that stops short keeps the watermark.** Only a cycle of the loop (`from_loop=True`) counts a miss. After `CATCH_UP_MAX_MISSES` misses the watermark moves on.
- ⚠️ `_skips_folder` in `providers/outlook.py` is the one rule for a 403 or a 404 on a folder. An Outlook import pages by time, never by `$skip`.
- ⚠️ **The Graph delta runs in shadow only, and the sweep stays the one writer.** `scheduler.outlook_delta_mode` is the one reader, and `on` resolves to `shadow`.
  - 🔴 `_graph_link` checks that each link starts with `GRAPH_API_BASE` and a slash, or the bearer goes to another host.
  - 🔴 A value other than `off` on a box is OWNER-GATE (`enforcement-flip`). Fence: `test_outlook_delta_shadow.py`.
- ⚠️ **Three guards protect the reconcile of a member-act import** (`reconcile.import_reconcile_candidates`, the 50-row and 2% skip, `trash_import_rows`). A failed lookup keeps its row.
- ⚠️ **Each mailbox has a storage limit.** `storage.py` is the one owner of the limit, the meter and the removal, and it imports nothing from `providers`.
  - The meter sums `pg_column_size`. Do not use `octet_length`. The removal deletes Metorite's copy only and skips `drafts` (`KEPT_FOLDERS_SQL`). Fence: `test_email_storage_limit.py`.
- ⚠️ `post_sync.triage_once_per_cycle` is the one reader of `EMAIL_TRIAGE_ONCE_PER_CYCLE`, and `post_sync.status_skips_rule_match` of `EMAIL_STATUS_SKIPS_RULE_MATCH`. Fence: `test_email_triage_once.py`.
- Interval: `email_accounts.sync_interval_secs` (default 300s)
- `refresh_account_sync(account_id, organization_id)` and `remove_account_sync()` serve the CRUD routes. The organization comes from the session or the verified OAuth state, never from request input.
- ⚠️ **Call `remove_account_sync` with no session open**, because it waits for the loop task. Fence: `test_email_disconnect_order.py`.
- ⚠️ **One sync at a time for each mailbox.** Call `_sync_account` and never `_sync_cycle`. A new call must pass a constant mode, with no session open. Fence: `test_email_sync_one_at_a_time.py`.
- ⚠️ **A member act that is not a sync holds the same lock through `hold_mailbox`.** Do not add a second lock beside it.
- `get_scheduler_status()` returns state for health checks.

## Dependencies

- `aiosmtpd>=1.4.6` — inbound SMTP server
- `sqlalchemy[asyncio]>=2.0` — async Postgres access
- `asyncpg>=0.29.0` — Postgres driver
- `httpx>=0.27.0` — HTTP client for REST APIs (Gmail, Outlook)
- `acb-llm` — credential encryption/decryption
- `acb-common` — settings, logging

## Child DOX Index

None — leaf directory.
