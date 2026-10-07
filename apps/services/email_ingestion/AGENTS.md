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
storage.py         — the storage meter, the limit, and the removal of older mail (EM-T6c)
html_tier.py       — the HTML hot window and its two flags (EM-S1)
llm_cap.py         — the cap and the daily budget of the email model calls (EM-T4b)
```

## Providers

All providers implement the `BaseEmailProvider` abstract interface:
- `authenticate()`, `list_folders()`, `list_messages()`, `get_message()`
- `send_message()`, `modify_message()`, `trash_message()`
- `sync_messages(history_id)` — incremental sync, returns `SyncResult` with `messages` list.
  It takes `catch_up`, the watermark after a pause. Outlook reads more pages
  back to it. IMAP ignores it and reads by its UID cursor. Gmail ignores it while
  it has a cursor, because its history reads each change since the cursor
  (WS-17 EM-G4b, contract 10). After a pause longer than the history of Gmail,
  the cursor is stale, and the sweep after the 404 reads back to `catch_up`.
  It takes `delta_shadow` too (WS-17 EM-T4d). Outlook then runs the Graph
  delta after the sweep, and Gmail and IMAP ignore it.
- `import_batches(since, until, size)` — the import in lists, newest first
  across every folder (WS-17 EM-T6b). The default of the base class calls a
  deep `sync_messages`, then sorts and cuts, and only IMAP uses it. Outlook
  merges one page stream for each folder, and reads the next page of a
  folder only when that folder held the newest head. It awaits `on_estimate`
  once with the sum of the folder counts, or with `None`. Gmail pages one
  list of all mail (contract 12).
- `get_attachment()`

## Key Contracts

1. **SyncResult.messages** must be populated with full `EmailMessage` objects.
   The sync endpoint and scheduler both use this to persist messages to `email_messages`.

2. **ON CONFLICT (account_id, provider_message_id) DO UPDATE** — the upsert makes sure
   that a sync is idempotent. Deleted messages move to `folder='TRASH'` locally.
   A deleted row in `drafts` goes instead (WS-17 EM-G4b E-B2). Each edit of a
   draft in Gmail web deletes its old id. Only a marker with
   `EmailMessage.deletion_marker` deletes a row, and only the Gmail history sets
   it. Never key the rule on the subject, because a real draft can have it.
   - **`persist.upsert_message` reclaims a re-keyed row only with `reclaim=True`
     (WS-17 EM-G1, D-EM-34).** Each update-path caller passes
     `getattr(provider, "REKEYS_MESSAGE_IDS", False)`, and only Outlook sets it.
     Gmail never changes an id, so a reclaim would fold two Gmail messages with
     one Message-ID into one row. A new caller names `reclaim=`. R7:
     `tests/unit/test_email_rekey_reclaim.py`.

3. **history_id format is provider-specific:**
   - Gmail: the Google historyId as text, in ASCII digits. While a failed fetch
     holds the cursor, it is `{"held_cycles": <n>, "history_id": <id>, "v": 1}`
     (contract 10). Text that is neither form is no cursor, and the sync seeds
     again. The sync log keeps the plain id, and NULL for the JSON form.
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

7. **One cap and one daily budget for the email model calls (WS-17 EM-T4b).**
   `llm_cap.py` owns both. It imports no `gateway` module, and it reads the
   tenant from `acb_common.db.current_tenant`. Fence:
   `tests/unit/test_email_llm_cap.py`.
   - A job that acts on a mailbox opens the automation scope with
     `automation_job` or `automation_scope`. A call that a member drives never
     opens it. Outside the scope, `llm_slot` takes no permit and counts nothing.
   - `llm_slot` wraps the leaf model await only. Do not wrap an enclosing
     function. A task started inside a held slot runs under that slot, with
     no permit and no count of its own. So a slot around a function runs its
     model calls uncapped and uncounted. The AST fence of the test finds a
     model await outside `llm_slot`, a `decide` facade call inside one, and
     each call inside one that is not a leaf. Its one exception is the
     gather of `decide` requests in `gateway.decide_features._ask_all`.
   - The cap is one `asyncio.Semaphore` for the process, because the gateway
     runs as one process. `EMAIL_LLM_CONCURRENCY=0` means no cap, and 0 is
     the default.
   - The budget counts model requests for each mailbox for each UTC day under
     `tenant_redis.key("email-llm", account_id, date)`. `log` is the default,
     and it never refuses a call. `enforce` raises `LLMBudgetExhausted` past
     `EMAIL_LLM_DAILY_CALLS`, before the model call. 🔴 `enforce` on a box is
     OWNER-GATE.
   - A call that reaches no model counts nothing. A refusal, a body that
     raises and a body that times out give the count back with `decrby`. The
     50% and 100% lines log after a call that succeeded.
   - Each Redis command waits 0.25 seconds at most. A failure or a timeout
     opens a breaker for 60 seconds and logs `email.llm_budget_unavailable`
     once. While it is open, the budget counts nothing and the call runs,
     also in `enforce`. The cap still binds.

8. **The Gmail parse (WS-17 EM-G2, D-EM-33).** `GmailProvider.get_message` is
   the one caller of `_parse_gmail_message`. The sweep, the deep sync, the body
   backfill and the gateway reads all reach the parse through it. Fence:
   `tests/unit/test_gmail_parse.py`, with the fixtures in
   `tests/unit/fixtures/gmail/`.
   - `_iter_gmail_parts` is the one walk of the MIME tree. The attachment list
     and the body walk use it. Do not add a second walk.
   - The body walk reads the tree at any depth. It takes the first
     `text/plain` part and the first `text/html` part, and it skips each file.
     A single-part HTML mail fills `body_html` only, as Outlook does.
   - Each part decodes with the charset of its `Content-Type`, else UTF-8. A
     sender picks the charset, so a codec that raises falls back to UTF-8 too.
     A raise fails the parse, and `list_messages` then skips the mail and
     records it (contract 9).
   - A part with a file name, a part with the disposition `attachment` and a
     `message/*` part are files. The body walk skips them.
   - `_parse_headers` keys each header by its lower-case name. Read a header
     by that name.
   - `internet_message_id` is the `Message-ID` value, trimmed. It keeps its
     angle brackets and its case, the form of Graph, because
     `automation/identity.py` compares the column with `=`.
   - `_split_addresses` runs `getaddresses` with `strict=False` on the raw
     text of each address header. The strict parser drops a whole list for
     one stray comma. The encoded words of each name decode after the split.
   - A list leaves out each entry with no `@`. When the first entry of
     `From` has no `@`, the address is the text inside the angle brackets.
   - The system labels set the folder: `TRASH`, `SPAM`, `DRAFT`, `SENT`,
     `INBOX`, else `archive`. A user label never sets it (O-GM-1). The user
     labels go to `categories`.
   - `list_messages` keeps the folder of the parse, and `canonical_override`
     never sets it. The folder key `archive` pages with `GMAIL_ARCHIVE_QUERY`
     and sends no `labelIds`.

9. **The Gmail client seam (WS-17 EM-G4a).** `_get_client` in `gmail.py`
   sets `auth=GmailBearer(self)`. `GmailBearer` is a `RefreshingBearer`, so
   contract 6 still holds. It adds the rate limits of Gmail to each call.
   Do not add a retry at a call site. Fence:
   `tests/unit/test_gmail_rate_limits.py`.
   - A 429, or a 403 with the reason `rateLimitExceeded` or
     `userRateLimitExceeded`, waits for `Retry-After` or a back-off. Then the
     request goes again, 3 times at most. Then the flow raises
     `GmailRateLimited`, an `httpx.HTTPStatusError`.
   - A plain 403 and a 5xx go back to the caller at once.
   - One wait is 30 seconds at most, and a longer `Retry-After` ends the
     tries. The waits of one client add up to 60 seconds at most, so one
     sync cycle waits 60 seconds at most for rate limits.
   - Only a GET, HEAD, PUT or DELETE, or a POST to `modify`, `batchModify`,
     `trash` or `untrash`, goes again. Each other POST goes once, so a send
     never goes out twice. A new POST that a second try cannot change goes
     into `_REPEATABLE_POST_ACTIONS`.
   - A fetch whose tries are spent raises, and each loop of `sync_messages`
     raises it again through `_raise_rate_limit`. So the sync fails and
     backs off, and the cursor stays.
   - A fetch that fails for another reason calls `_record_fetch_failure`. It
     logs `gmail.fetch_failed` with the message id, the class of the error
     and the status, never a subject or an address. The record goes into
     `SyncResult.errors`.
   - The `authenticate` probe and the token post use their own clients, so
     the seam does not wrap them.

10. **The Gmail history cursor (WS-17 EM-G4b).** `email_accounts.last_history_id`
    holds it, and no column was added. Fence:
    `tests/unit/test_gmail_history_cursor.py`, with two R8 cases.
    - **The seed.** `seed_cursor` reads `users.getProfile`. The base class
      returns None, so Outlook and IMAP get no seed. With no cursor,
      `scheduler._seed_before_import` seeds BEFORE the import, and the sweep
      with no cursor seeds before it reads. A failed seed never stops new
      mail. It logs, and the next cycle seeds again. A spent rate limit is no
      failed seed: a `ProviderRateLimited` fails the cycle.
    - **The read.** The history read follows each `nextPageToken`. The cursor
      is the `historyId` of the last answer. `labelsAdded` and
      `labelsRemoved` fetch the message in full, as `messagesAdded` does.
    - **A stale cursor.** A 404 seeds, then lists all mail after the later of
      the watermark and the floor, for `DEEP_SYNC_MAX_PAGES` pages at most. A
      short sweep returns no cursor and sets `catch_up_incomplete`, so the
      stale cursor stays. The count of the Outlook catch-up decides the
      abandon. Then `_next_cursor` writes `SyncResult.reseed_history_id`.
      The scheduler logs each reset with the mailbox id. A failed reseed
      backs the loop off.
    - **A failed fetch.** A 5xx or a transport error keeps the old position.
      The cursor counts its held cycles, not the cycles of each message. At
      `GMAIL_FETCH_HOLD_CYCLES` (3), it moves on and logs
      `gmail.fetch_abandoned` for each message that still fails. The cursor
      holds no message id. A rate limit fails the cycle.

11. **Gmail send and drafts (WS-17 EM-G3a, O-GM-2).** Fence:
    `tests/unit/test_gmail_send_and_drafts.py`.
    - `_build_gmail_mail` is the one MIME builder of `send_message`,
      `create_draft` and `update_draft`. Do not add a second one. It gives
      bytes, and `_gmail_raw` wraps them for the plain URI (contract 16). A
      body with HTML is `multipart/alternative`, with the text part first.
      Each attachment takes the type that the caller gives, else the type of
      its name.
    - A reply reads its parent from Gmail (`format=metadata`), never from
      the local row, and sets `In-Reply-To` and `References`. A parent read
      that fails never fails the send.
    - **The draft id rule.** A Gmail draft has a draft id and a message id.
      `create_draft` and `update_draft` return the MESSAGE id, and the local
      row holds it, because the sync finds that id. `update_draft` and
      `send_draft` take the message id and find the draft id through
      `drafts.list`. Gmail gives a draft a new message id at each update,
      so a caller moves its row to the id that `update_draft` returns, and
      it sends that id. A message id that is no draft raises
      `GmailDraftNotFound`.
    - `trash_message` discards a draft with `drafts.delete`, which removes
      it for good, as Gmail does. It adds the id to `discarded_drafts`, so
      the caller deletes the local row.
    - Gmail replaces the whole draft at each update. So `update_draft`
      reads the files of the draft from Gmail (`_draft_files`) and builds
      them in again, and `attachments` adds files to them. Metorite keeps
      no bytes of a draft file. A read that fails fails the update, because
      an update with no files would delete them in Gmail (review round 1).
    - `_draft_files` reads the draft as one raw mail (`format=raw`) and
      takes the files of the top level with `iter_attachments`. Gmail
      opens an attached mail into its parts in `format=full`. Do not walk
      into a `message/*` part. It is one file, and the builder puts it in
      as a mail part (review round 2).

12. **The Gmail import (WS-17 EM-G5a).** `GmailProvider.import_batches`
    pages one `messages.list` of all mail. Do not put back a list for each
    label. Fence: `tests/unit/test_gmail_import.py`, and the R8 case in
    `TestTheImportOnARealDatabase` of `test_email_import_batches.py`.
    - The list sends `includeSpamTrash=true`, no `labelIds`, and
      `q=after:<floor> before:<bound>` in epoch seconds. So archived mail
      with no label comes too, and a message with two labels comes once.
    - The bound of a resume is the whole second of `until`, plus 1 second,
      as for Outlook. A message newer than `until` drops, and a message
      older than the floor drops too.
    - Each page is one batch, sorted again newest first. The page fetches
      its ids in parallel, `GMAIL_IMPORT_FETCHES` (10) at once, and each id
      once in the whole import.
    - Each page gathers every fetch before it yields. A raise cancels the
      other fetches of the page, and waits for them. So the storage limit
      closes the import with no fetch in flight.
    - A 5xx or a transport error on a fetch fails the import, and the next
      cycle resumes. A 404, another 4xx or a parse error keeps its record
      (contract 9), and the batch goes on.
    - `on_estimate` gets the `resultSizeEstimate` of the first answer once,
      before the first fetch. `IMPORT_MAX_PAGES` (5000) ends the import and
      logs `gmail.import_capped`.
    - The import never calls `sync_messages(deep=True)`. `_deep_sweep`
      stays for a direct call. `import_full_snapshot` is True, so the
      deep sync of a member act reconciles the deletions (contract 15).

13. **A file on an Outlook draft (WS-17 EM-T9).** `_attach_files` in
    `outlook.py` adds each file, or it raises `ProviderAttachmentFailed`
    with the file name. Fence: `tests/unit/test_outlook_attachments.py`.
    - A file under 3,000,000 bytes goes in one POST. Each 2xx passes,
      because Graph answers 201.
    - A larger file goes through an upload session, in PUTs of 2 MiB. A
      session that Graph refuses for the minimum size falls back to one POST.
    - The PUTs go through a client with no auth. Do not send them through
      the Graph client, because it puts the bearer on each request.
    - The upload URL holds a token. `_UploadUrlFilter` on the `httpx` logger
      removes its query. No error text, traceback or log line holds the URL.
    - `_attach_files` never deletes a draft. Only `create_draft` deletes a
      new draft whose file failed, and `update_draft` keeps the draft.
14. **The Gmail move and the filter list (WS-17 EM-G3b, D-EM-33).** Fence:
    `tests/unit/test_gmail_move_and_filters.py`.
    - `move_to_folder` reads the name with `canonical_folder`, so each alias
      of a system folder takes the system branch. Sent and drafts raise
      `ValueError`. So does the name of a system label or a `CATEGORY_*`
      label, because a move to "Starred" would star the message.
    - Each other name is a user label. One `modify` adds it and removes
      `INBOX`, `TRASH` and `SPAM`. A label that Gmail could not make raises.
      The name goes to Google with its case.
    - A move to Archive removes `INBOX`, `TRASH` and `SPAM` too. A move to
      Junk adds `SPAM` and removes `INBOX` and `TRASH`. Gmail ranks `TRASH`
      first, so a move that keeps it leaves the message in Trash (review
      round 1).
    - `folder_after_move(name)` gives the folder key that a move leaves. The
      base gives `canonical_folder(name)`. Gmail gives `archive` for a user
      label, and `None` for a move that it refuses. Read it only through
      `local_folder_after_move`, because some test fakes do not subclass the
      base.
    - `list_filters` reads the key `filter` of `users.settings.filters.list`.
      A plain 403 gives `[]`, and `GmailRateLimited` passes up.

15. **The Gmail reconcile (WS-17 EM-G5b).** A Resync, "Clean older mail"
    and "Process past emails" trash a row that the member deleted in Gmail.
    Fence: `tests/unit/test_gmail_import.py`, and the R8 cases in
    `TestTheImportOnARealDatabase` of `test_email_import_batches.py`.
    - The confirm asks Gmail by the provider id, through `message_gone`.
      Never ask by the Message-ID, because one Message-ID can be on two
      Gmail messages. Do not give Gmail a `message_exists`.
    - Only a 404 whose body gives the reason `notFound` is a delete. A 200
      keeps the row, also in `TRASH` or `SPAM`. A bare 404 and each other
      answer raise, and the row stays.
    - `message_gone` refuses an id that is not letters, digits, `-` and
      `_`, before any request. So a stored id cannot send the read to
      another resource.
    - Rows in drafts stay out (`import_reconcile_skips_drafts`). Gmail gives
      a draft a new id at each update, so a missing draft id proves no
      delete. The history removes an old draft row (contract 2).
    - At `IMPORT_MAX_PAGES` the import sets `import_capped`. The Resync then
      runs no reconcile and logs `sync.import_reconcile_skipped
      reason=capped`.
    - No Gmail sync result sets `full_snapshot`, so the recurring reconcile
      stays for Outlook only.

16. **The size of a Gmail mail (WS-17 EM-G3c-1).** `GmailProvider._write_mail`
    is the one write of `send_message`, `create_draft` and `update_draft`. Do
    not send a write around it. Fence: `tests/unit/test_gmail_mail_size.py`.
    - A mail with a file, or a built mail over `GMAIL_PLAIN_MAX_BYTES`
      (1 MiB), goes to the upload URI `GMAIL_UPLOAD_BASE` with
      `uploadType=multipart`. Each other mail keeps the plain URI and `raw`.
    - `update_draft` decides on the files after the read-back of the draft,
      never on `attachments`. An autosave adds no file, and the draft can
      still hold one.
    - The upload goes through `_get_client()`, so contract 9 holds for it.
      Its body is `multipart/related`: the metadata in JSON, then the raw
      mail as `message/rfc822`. Never use `files=`, because it builds
      `multipart/form-data`.
    - The metadata omits `threadId` where the plain JSON body omits it.
      A draft write nests it in `message`.
    - A built mail over `GMAIL_MAIL_MAX_BYTES` (36,700,160 bytes, the
      `maxSize` of the discovery document) raises `GmailMailTooLarge` before
      the write. A 413 raises it too, read from the status code only. It is
      a `ProviderMailTooLarge`, its text holds no URL, and the routes answer
      413. Outlook and IMAP never raise it.
    - The upload POST of `messages.send` and `drafts.create` gets one try,
      and the PUT of `drafts.update` keeps its retry on a 429.

17. **The To of a reply draft (WS-17 EM-T10 item 6).** `create_draft` takes a
    keyword-only `exact_to: bool = False` on the base class and on each
    provider. Fence: `tests/unit/test_outlook_draft_cc.py`.
    - Outlook's `createReply` sets the To to the sender, or to the Reply-To.
      With `exact_to`, the PATCH of the reply writes `to` too, so a reply-all
      draft keeps each address. Gmail and IMAP put `to` into the mail, so they
      ignore the keyword.
    - `PUT /email/drafts` passes `exact_to=True`, because the member typed
      that To. The rule REPLY passes `exact_to=bool(a.get("to_address"))`,
      so only a To typed into the rule goes over the To of `createReply`.
      Each other caller keeps the default, so a Reply-To stays. The fence
      counts the callers in `drafting.py`, `actions.py`, `followups.py` and
      `notes/dispatch.py`. A new caller there fails it.

18. **The HTML hot window (WS-17 EM-S1, D-EM-49).** `html_tier.py` is the one
    owner of the window. Fence: `tests/unit/test_email_html_tier.py`.
    - A message older than `HTML_HOT_DAYS` (90) is cold. A message with no
      `received_at` is never cold. Every time is in UTC.
    - `from_provider()` reads `EMAIL_HTML_FROM_PROVIDER`. `hot_only()` is
      true only when `EMAIL_HTML_HOT_ONLY` is true too. No other module reads
      the two flags, and no other module writes the number 90 next to
      `received_at`. The fence fails on each.
    - `import_window.py` owns the sync window. Neither module imports the
      other (D-EM-53).

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
  - A delta failure never changes the four sweep fields. A 410 or a 400 of
    Graph drops the link of its folder, and any other failure keeps it.
  - 🔴 **Each link must start with `GRAPH_API_BASE` and a slash** (review
    round 1). `_graph_link` checks the stored link, each next link and the
    delta link before a request or a store. Else the bearer goes to the host
    that the cursor names. A refused link drops, and the log names no URL.
  - Only a normal incremental cycle of the background loop runs the delta.
    A first import, a deep sync, a cycle before `initial_sync_done` and each
    caller that is not the loop (Sync now, the webhook, the rerun, the agent
    tool) run none (`scheduler._runs_delta_shadow`, EM-T4d-f3).
  - A shadow cycle writes NULL into `email_sync_log.provider_history_id`.
    Only `last_history_id` keeps the cursor.
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
  (a). A move, a rule action or a draft during the import writes that row,
  and Graph gives a moved message a new id. When a folder has more
  candidates than 50, or 2% of its rows, the reconcile leaves that folder
  and logs `sync.import_reconcile_skipped`. `trash_import_rows` checks
  `updated_at` again in its own block.
  - **The confirm (WS-17 EM-G5b).** Each candidate is `(row id,
    provider_message_id, internet_message_id)`. With no session open,
    `_confirm_gone` looks up at most 50 candidates for each folder. The
    provider decides the key. Outlook asks `message_exists` by the
    Message-ID, so a message that the member moved in the Outlook client
    keeps its row. Gmail asks `message_gone` by the provider id
    (contract 15).
  - A failed lookup, or a row with no key, keeps its row. A spent rate
    limit (`ProviderRateLimited`) stops the lookups, and each row that was
    not looked up keeps its folder. A provider that sets
    `import_reconcile_skips_drafts` gives no row in drafts to the confirm.
- ⚠️ **Each mailbox has a storage limit (WS-17 EM-T6c, D-EM-14).**
  `storage.py` is the one owner of the limit, the meter and the removal.
  The limit is `email_mailbox_storage_limit_mb` (500) for each mailbox. The
  meter sums `pg_column_size` of each column of variable length. Do not use
  `octet_length` or the size of a whole row. It runs in the block of each
  import batch and in phase (d). At the limit, the import fetches no next
  batch, and a first import ends at `import_phase = 'limit'`. Phases (e)
  and (f) do not run at the limit (owner answer Q3). New mail still syncs
  (Q2). The removal deletes Metorite's copy only, and `storage.py` imports
  nothing from `providers`. R7: `tests/unit/test_email_storage_limit.py`.
  - **The steps of the removal (the gaps G1 to G5, 2026-10-04).** Each step
    takes a session, opens none and never commits. `advance_import_since`
    runs first, before any delete. The preview and `remove_older_chunk` skip
    the folder `drafts` through `KEPT_FOLDERS_SQL`, so an unsent draft
    stays. No step touches Mem0, because no Mem0 key names one mail.
  - **Review round 1 (2026-10-04).** `remove_older_chunk` returns
    `RemovedChunk`, with the threads of the mail that it deleted.
    `delete_empty_thread_status` and `delete_orphan_ai_drafts` take those
    threads, and they keep a row of any other thread. `end_limit_phase`
    writes `import_phase = 'done'` only under the limit, and only when no
    gap is left below `import_reached_at`. `advance_import_since` writes
    `onboarding_done_at` for a mailbox with no `import_since`, so the guided
    setup does not open for it.
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
  of the other. The lock key is the canonical UUID, `str(uuid.UUID(id))`, so
  each form of one id takes one lock (EM-T6c review round 1). An id that is
  not a UUID keeps its text in lower case. The loop, the webhook,
  the manual sync and the resync pass `if_busy="skip"` and get
  `SYNC_SKIPPED_BUSY`. A skip marks the mailbox, and the holder then runs ONE
  more shallow cycle under the lock (`_rerun_once`). The deep downloads wait
  up to `SYNC_LOCK_WAIT_SECS` (600 seconds), and `download_failure` turns a
  busy or failed result into a job error. A resync passes `purge` and
  `reset_cursor`, and phase (a) applies them under the lock. A new call must
  pass a constant mode. The lock lives in this process, which is enough while
  the gateway is one uvicorn process. Call it with no session open. R7:
  `tests/unit/test_email_sync_one_at_a_time.py`.
- ⚠️ **A member act that is not a sync holds the same lock through
  `hold_mailbox` (WS-17 EM-T6c gap G3).** The removal of older mail is its
  one caller. It waits `wait_secs`, then raises `MailboxBusy`, and the route
  answers 409. It counts in `_sync_lock_users`, so `sync_busy` is true while
  it holds or waits. A loop cycle or a webhook sync then skips, and its new
  mail waits for the next loop cycle. Call it with no session open. Do not
  add a second lock beside it. R7: `tests/unit/test_email_storage_limit.py`.
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
