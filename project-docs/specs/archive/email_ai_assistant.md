# Email AI Assistant — Overview, Architecture & Feature Inventory

> **⚠️ ARCHIVED 2026-07-22 — superseded by [`../email_app_master_plan.md`](../email_app_master_plan.md)**,
> the consolidated single source of truth (current state + prioritized roadmap). Known staleness
> in this doc: agent tools are 42 (not 67), migrations run to 87 (not 43), the multi-Gmail
> success criterion is retired, and §8's gap list has been re-verified and absorbed into the
> master plan. Kept for architecture detail, the provider capability matrix, and design rationale.
>
> **Product:** Metorite · **Feature:** Email AI Assistant App · **Updated:** 2026-06-29 · **Version:** 2.0
> **Status at archive time:** 🟢 Live on the VPS. Full email client (Gmail / Microsoft 365 / IMAP) with multi-account sync, two-way write-back, conversation/threading UI, a complete inbox-zero-parity automation suite (rules, Reply Zero, drafting, sender categorization, cold-email blocker, inbox cleaner, analytics, digests) and an agent-backed assistant chat.
>
> **Companion docs:**
> - [`email_inbox_zero_parity_plan.md`](./email_inbox_zero_parity_plan.md) — the historical parity roadmap.
> - [`email_app_review.md`](./email_app_review.md) — the milestone build log (chronological history of what shipped).

---

## 1. Overview

The Email AI Assistant is a **custom app** in the Metorite Control Plane that provides a full-featured, AI-assisted email client. It connects to multiple providers (Gmail, Microsoft 365 / Outlook, generic IMAP/SMTP), syncs mail into Postgres, presents a multi-panel UI (accounts · folders · list · conversation reader · automation scenes · assistant chat), and layers an inbox-zero-style automation suite plus a tool-using MAF agent on top.

### Key requirements (original intent)

- **R1** — Multiple providers: Gmail (Workspace/personal), Microsoft 365/Outlook, IMAP/SMTP.
- **R2** — Multiple accounts per provider.
- **R3** — Connected from the Integrations page / in-app OAuth.
- **R4** — Per-account credentials (own OAuth tokens).
- **R5** — AI assistant reads, summarizes, drafts, finds urgent, suggests unsubscribes, manages rules.
- **R6** — Full email ops: read, send, reply, forward, archive, delete, flag, label, move.
- **R7** — Near-real-time sync (polling + Outlook Graph webhook; Gmail Pub/Sub still backlog).
- **R8** — Search across all connected accounts.

All eight are met today (R7 partially — Gmail push is still polling-only; see §8).

---

## 2. Architecture (current)

```
┌──────────────────────────────────────────────────────────────────────┐
│                       CONTROL PLANE (Next.js)                          │
│  /email — Email AI Assistant App                                       │
│  ┌──────────┬────────────┬───────────────┬──────────────────────────┐ │
│  │ Accounts │ Email list │ Conversation  │ Automation scenes /       │ │
│  │ + folders│ + toolbar  │ reader+composer│ Assistant chat (overlay) │ │
│  └──────────┴────────────┴───────────────┴──────────────────────────┘ │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │ HTTP/SSE  (Next.js /api/email proxy)
┌───────────────────────────────▼────────────────────────────────────────┐
│                          GATEWAY (FastAPI)                               │
│  routes/email/  — layered package (~6k LOC across submodules)            │
│   core.py           shared kernel: router, models, DB/Redis, provider    │
│                     instantiation, mappers, body caps, safe-JSON          │
│   transport/        accounts · messages · folders · attachments ·         │
│                     oauth · send · sync   (mailbox I/O + two-way sync)    │
│   automation/       rules · engine · runner · drafting · replyzero ·      │
│                     senders · assistant   (inbox-zero parity)            │
│   digest.py         digest generation + scheduling                        │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │
┌───────────────────────────────▼────────────────────────────────────────┐
│           EMAIL INGESTION  (apps/email_ingestion/)                       │
│   providers/{base,gmail,outlook,imap}.py   provider abstraction          │
│   providers/label_colors.py                25-preset cross-provider palette│
│   scheduler.py     per-account async sync loop + post-sync automation     │
│   reconcile.py     full-snapshot deletion reconciliation (Outlook)        │
│   inbound.py       optional aiosmtpd inbound SMTP receiver                 │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │
┌───────────────────────────────▼────────────────────────────────────────┐
│                         DATA STORE (Postgres)                            │
│  email_accounts · email_messages · email_attachments · email_folders ·   │
│  email_sync_log · email_rules · email_actions · email_executed_rules ·   │
│  email_rule_patterns · email_newsletters · email_senders ·               │
│  email_cold_senders · email_assistant_settings · email_knowledge ·       │
│  email_thread_status · email_ai_drafts · email_learned_patterns          │
│  (migrations 17 → 43 — see §7)                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Agent architecture

```
EMAIL ASSISTANT AGENT  (apps/agent-email-assistant/)
  Runtime : native MAF (agent-framework-core), OpenAIChatCompletionClient → gateway /v1
  Model   : per-account model roles (rule / draft / chat); default chat = tier-powerful
  Tools   : 67 explicit email tools (read/triage, actions, drafting/send, rules CRUD,
            settings, KB, sender categorization, reply-zero, follow-ups, unsubscribe,
            cold-email, digest, sync, artifacts) + injected (call_agent, web_search,
            memory: remember/recall_timeline/save_memory/save_episode, write_artifact)
  Surfaces: runs identically in the Chat app and the Email app (dual-surface parity)
```

---

## 3. Multi-account credential model

`email_accounts` holds one row per connected mailbox with an **AES-256-GCM-encrypted JSON credential blob** (provider-specific: OAuth client/refresh/access tokens + scopes for Gmail/Microsoft; host/port/username/password for IMAP). OAuth app credentials are persisted alongside user tokens so refresh-token rotation works. Tokens auto-rotate on 401 and the rotated blob is re-persisted by the sync loop and on any provider call.

Per-account: `sync_enabled`, `sync_interval_secs` (default 300), `last_synced_at`, `last_history_id` (Gmail historyId / Outlook delta / IMAP `uid:uidvalidity`), `initial_sync_done` (gates the one-time deep backfill), `sync_status`.

---

## 4. Sync strategy (current)

- **Deep sync (first connect):** 1-year multi-folder backfill (inbox/sent/drafts/archive/junk/trash + user folders/labels), gated by `initial_sync_done`.
- **Incremental sync (recurring, default 300s):**
  - **Gmail** — `history.list` (historyId); detects add/delete/labelAdded/labelRemoved.
  - **Outlook** — **full-snapshot sweep** (delta is **disabled in production** — it returned 0 changes while mail arrived, silently halting sync; full sweep is the reliable fallback) + Graph **push subscription** for instant wake.
  - **IMAP** — UID-based (`UIDNEXT`/`UIDVALIDITY`), **INBOX-only**.
- **Post-sync automation** (runs only when new mail arrived): auto-run rules → auto-categorize new senders → classify thread reply-status → auto-archive AUTO_ARCHIVED senders → send scheduled digest → send follow-up reminders → ensure/renew Outlook push subscription.
- **Deletion reconciliation:** Outlook full snapshots trash local messages absent from the refetched window; incremental providers skip this.
- **Learn-from-the-client:** incremental sync diffs old↔new categories per message and teaches FROM include/exclude rule patterns from user-applied/removed labels.

---

## 5. Frontend layout

`workbench/control_plane/src/app/email/` — `page.tsx` orchestrates:
- **Desktop:** left rail (accounts + folder tree + automation nav) → email list → conversation reader, with one **unified toolbar** spanning list+reader below the top bar.
- **Mobile:** single-pane list/detail with a 4-tab bottom bar; AI chat opens full-screen.
- **Automation scenes** replace the list+reader as full overlays (left rail stays); folder click exits the scene.
- Background **soft refresh** every 20s (visible tab only) surfaces assistant/upstream changes without a manual reload.

---

## 6. Feature inventory (SHIPPED) — classified

Legend: ✅ shipped · 🟡 shipped with a documented limitation. Provider gaps are called out per item; see the matrix in §6.12.

### 6.1 Accounts & connectivity
- ✅ Multi-account, multi-provider (Gmail, Microsoft 365/Outlook, IMAP/SMTP); per-account color/label/avatar.
- ✅ In-app **OAuth** for Gmail and Microsoft (Entra ID, tenant-aware) — gateway-side token exchange, CSRF `state`, auto workbench-URL derivation, scopes incl. `Mail.ReadWrite`/`Mail.Send`/`MailboxSettings.ReadWrite`.
- ✅ **IMAP/SMTP** manual add (host/port/SSL/credentials) with connection validation.
- ✅ **Account reconnect** flow (refresh stale tokens in place, force re-sync); per-account OAuth-expiry banner with "Reconnect".
- ✅ Account CRUD (add/update label & sync toggle/delete + cascade message purge); scheduler lifecycle hooks on create/update/delete.

### 6.2 Sync engine
- ✅ Per-account background async sync loop (configurable interval); manual **Sync now**, **Resync**, and **Hard resync** (purge + refetch).
- ✅ Deep 1-year initial backfill; incremental thereafter (Gmail history / Outlook full-sweep / IMAP UID).
- ✅ Outlook **Graph push webhook** (`/email/webhook/microsoft`) — create/renew/delete subscription; instant wake on new mail.
- ✅ On-demand **backfill** endpoint (paged, older history).
- ✅ Message upsert preserves body on conflict (no first-sync content loss); body caps (500 KB text / 2 MB HTML) at sync time.
- ✅ Importance + categories + `List-Unsubscribe` link captured at sync time.
- 🟡 Outlook **delta disabled** (full-sweep fallback); IMAP **INBOX-only** (UIDs not folder-namespaced); incremental deletion only tracked via Outlook full snapshots.

### 6.3 Mailbox & reading
- ✅ Canonical system folders (Inbox/Starred/Sent/Drafts/Archive/Junk/Trash) **+ provider user folders/labels** with per-folder counts and unread badges.
- ✅ Email list: sender, unread dot, **thread-count badge**, importance triangle, attachment icon, star, flag, snippet, colored label chips; multi-select with select-all; pull-to-refresh (mobile); infinite scroll + "Load older from server".
- ✅ **Conversation / threading view** (Gmail-style oldest→newest), collapse/expand cards, draft-in-thread renders as an editable composer.
- ✅ **Outlook-style trailing-mail collapse** — quoted history hidden behind a "•••" toggle, never edited, reattached verbatim on send.
- ✅ **Lazy body hydration** — Outlook syncs headers only; full body fetched + persisted on first open; "Load full message from provider" for truncated bodies.
- ✅ **Sandboxed HTML rendering** (DOMPurify; scripts/handlers stripped, `<style>` kept) with a **remote-image proxy** (SSRF-guarded, size-capped) and a per-message "Show images" toggle; plain-text fallback.
- ✅ Per-message badges: Important / Flagged / Unread, recipients, date, applied labels with colors.
- ✅ **Priority inbox** ranking (`/email/priority`): blends needs-reply / unread / importance / starred / personal-sender, excludes bulk.
- ✅ Full-text **search** (tsvector over subject/body/sender) + rich filters (date range, read/starred/attachments/importance/from/sender-category, sort newest/oldest/importance).
- ✅ **Label filtering** — click a label chip to filter the inbox; active-filter strip.

### 6.4 Labels & colors
- ✅ User-applicable labels listed with color info; create-label inline with color picker.
- ✅ **Settable label/category colours** via a single **25-preset cross-provider palette** (`label_colors.py`); preset id is the wire token; colours **round-trip to Gmail labels and Outlook master categories** (deterministic fallback color by name).
- 🟡 Gmail label-id→name surfacing handled; IMAP has no label/category concept (no-op).

### 6.5 Compose / reply / forward
- ✅ Full-screen **composer** (To/Cc/Bcc, subject, body) and **inline reply/forward composer** in the reader.
- ✅ Reply / **Reply-All** (auto-recipients minus self, Cc/Bcc editable) / Forward, with the original quoted below (read-only, reattached on send).
- ✅ **Drafts**: Gmail-style **auto-save** (debounced, creates/updates a real provider Draft), **edit-in-place** in the thread, **native draft send**, discard; "pop out" inline → full composer for Bcc/attachments.
- ✅ **Attachments**: native file picker **and** workspace-artifact attach (agent outputs); base64 + artifact resolution; send attachments end-to-end across providers.
- ✅ **AI compose** (`ComposerAI` → `/email/compose-assist`): "Draft" / "Improve" on the editable body only — quote-safe (trailing quotes never sent to the LLM).
- ✅ **Undo-send** toast with grace period.
- 🟡 IMAP draft update/native-send not implemented (falls back to create-new + delete-old); Outlook has no star (kept local-only).

### 6.6 Actions & two-way sync
- ✅ Mark read/unread, star, flag, archive, delete/trash, **move to folder**, **add/remove labels** — all **write back to the provider best-effort** after the authoritative local commit (provider failures logged, never fail the UX).
- ✅ **Bulk actions** (`/email/messages/bulk`): archive / delete / label / unsubscribe by selector (sender / category / older-than date).
- ✅ Toolbars & surfaces: unified desktop toolbar, per-message + bulk action rows, **Command Palette (Cmd/Ctrl+K)**, right-click context menu (move/categories flyouts), mailbox actions (refresh, mark-spam, resync), more-menu (mark spam, report phishing, block sender→archive rule, download `.eml`, print).

### 6.7 AI automation — Rules engine
- ✅ **Rule CRUD** + **NL→rule generation** (`/email/rules/generate`); preset pack install (To Reply, Awaiting Reply, FYI, Actioned, Newsletter, Marketing, Calendar, Receipt, Notification, Cold Email) in canonical system order; reset.
- ✅ **Conditions:** NL instruction + static From/To/Subject/Body patterns, **AND/OR** operator, `run_on_threads`, Cc/Bcc awareness; classifier given owner address, To/Cc roles, `about`, date, and inbox-zero guidelines; **forced JSON output**.
- ✅ **Actions:** ARCHIVE, LABEL (fixed **or** AI-resolved via `{{…}}` prompt), MOVE_FOLDER, MARK_READ, STAR, MARK_SPAM/TRASH, REPLY, FORWARD, DRAFT_EMAIL, CALL_WEBHOOK/URL — with per-action `delay_minutes` and **workspace-artifact attachments**.
- ✅ **Learned classification patterns** (`email_rule_patterns`): bidirectional-substring FROM + number/ID-generalised SUBJECT, include/exclude, source-tagged (FIX / LABEL_ADDED / LABEL_REMOVED / AI / USER), **consistency-gated** (≥3 consistent matches), short-circuit the LLM; learned **from manual labels in the client** too; **Fix** flow teaches FROM+SUBJECT and applies directly.
- ✅ **Multi-rule execution** toggle (apply >1 matching rule) with `isPrimary` (most-specific) ordering.
- ✅ **History** tab: per-execution log with status (APPLIED/PENDING/SKIPPED/REJECTED/UNDONE/ERROR), matched-rule + match-source + AI reasoning, action breakdown & per-action errors, **approve / reject / undo** (undo restores folder + removes added labels).
- ✅ **Test** tab: single-email test + **test-on-recent-inbox** sweep with per-email match rationale (non-mutating).
- ✅ **Process-past** — bulk apply rules to historical mail with live progress.
- ✅ **Auto-run on arrival** (sync-loop hook + Outlook webhook), gated by the Auto-run setting; drafts are **held for approval / never auto-sent**.

### 6.8 AI automation — Reply Zero (reply tracking)
- ✅ First-class **needs-reply classifier** (full-thread inbound status determination), now a **projection of the rules pipeline** (not a parallel classifier); statuses NEEDS_REPLY / AWAITING / FYI / DONE, mutually exclusive on re-evaluation.
- ✅ Reply Zero scene with **To reply / Awaiting reply / Done** tabs; per-thread auto-draft, save-to-drafts, mark-done/reopen.
- ✅ **Follow-up reminders**: configurable **business-day** windows (awaiting + needs-reply, fractional days), "Follow-up" label, auto-draft nudge using the nudge prompt, badge matching the window; on-demand scan + scheduled run; idempotent. Follow-up clock anchors to the **reply time**, not the inbound message.
- ✅ Replying flips To Reply → Awaiting/Actioned immediately; a just-sent reply shows in the thread and flips the chip; archiving a thread drops it from active buckets; trashed threads hidden.

### 6.9 AI automation — Drafting (the email agent)
- ✅ All drafting routes through the **email MAF agent** (`_agent_draft_reply` / `/email/draft-reply`), context-injected:
  - **Writing style** (explicit + auto-derived `learned_writing_style`, regenerated as evidence accrues),
  - **Knowledge base** (per-account titled snippets, budgeted),
  - **Personal instructions**,
  - **Scoped reply memories** (kind FACT/PROCEDURE/PREFERENCE × scope SENDER>DOMAIN>TOPIC>GLOBAL),
  - **Full thread context** (oldest→newest),
  - **Similar-thread precedent** (Mem0 retrieval) + **past replies to this sender** (sent-mail tone mirroring),
  - **Classification hints**, today's date, signature auto-append.
- ✅ **Learn-from-edits**: the AI draft is captured per thread (`email_ai_drafts`); on send the diff is distilled into deduped/weighted scoped memories.
- ✅ **Confidence gate** (`draft_confidence`: ALL_EMAILS / STANDARD / HIGH_CONFIDENCE) with an inbox-zero HIGH/MEDIUM/LOW grounded-vs-assumption rubric and a robust NO_DRAFT matcher; **sensitive-data protection** skips drafting on sensitive mail; AI-placeholder lines (`[Your Name]`) stripped.
- ✅ Inter-agent **handoff** to `sales` / `task-manager` specialists via `call_agent`.

### 6.10 AI automation — Sender categorization, cold-email, inbox cleaner
- ✅ **Sender categorization** (LLM, 8 categories) — manual trigger **and just-in-time on new senders** in the sync loop; category counts; learned auto-assignment.
- ✅ **Cold-email blocker** (first-time/unreplied senders) — OFF / LABEL / ARCHIVE modes + whitelist (`email_cold_senders`).
- ✅ **Inbox Cleaner** (merged Unsubscriber + Archiver): sender list by frequency/read-rate with status (Unhandled/Approved/Unsubscribed/Auto-archived); **real one-click unsubscribe** (RFC 8058 `List-Unsubscribe` HTTP(S) + mailto + provider filters: Gmail filter / Outlook rule via migration 43; HTML-link scrape fallback); **block-on-failure**; bulk **age-sweep archive** (7/30/90/365 days, "Newsletters only"); auto-categorize button.

### 6.11 Analytics, digest & assistant chat
- ✅ **Analytics** (`/email/analytics/overview`): totals (unread/sent/archived/starred/attachments), read-rate, volume-over-time, top senders (+unread), by-folder, **rule-automation stats** (processed count, by-rule, action breakdown).
- ✅ **Digest**: view (markdown, counts + top senders + needs-reply + category breakdown); email-to-self; **scheduled** OFF/DAILY/WEEKLY with day-of-week + time-of-day; last-sent dedup; runs via the sync loop.
- ✅ **Assistant chat** (`EmailAssistantChat`): full-scene panel pinned to the `email-assistant` agent, **dual-surface** (identical in the Chat app + Email app via shared `AgentChat` + `buildEmailAssistantPersona`), session restore/merge across devices, **Mem0** memory injection, **inbox context** (account list + first-turn snapshot + open email), and the **"Fix" bridge** from the Assistant tab into chat.
  - ✅ Rich **chat cards**: clickable inbox lists that open the email, inline body expansion, read_email card, manage_inbox card (names action + count), apply_labels card (coloured chips), rule cards (When→Then), in-chat triage (archive/mark-read).
  - ✅ **Confirm-before-send**: `send_email`/`send_reply`/`send_draft` block on a confirmation card (`request_confirmation`) — the user approves the actual send in chat; degrades to send when non-interactive.

### 6.12 Model configuration
- ✅ **Three per-account task-specific models** (migration 42): **`rule_model`** (default tier-fast — classification/labeling), **`draft_model`** (default tier-powerful — writing), **`chat_model`** (default tier-powerful — the chat panel). Selectable from Settings; all configured LiteLLM tiers/models exposed.
- ✅ Routed **BYOK** through the gateway `/v1`; native MAF agents honor the selected tier; **JSON mode forced** wherever structured output is parsed.
- 🟡 The non-streaming `run_agent` BYOK path still detours via Copilot→402→self-anneal to DeepSeek (works, wasteful — see §8).

### Provider capability matrix

| Capability | Gmail | Outlook (M365) | IMAP/SMTP |
|---|---|---|---|
| List / fetch / send / reply / forward | ✅ | ✅ | ✅ |
| Native drafts (create/update/send) | ✅ | ✅ | 🟡 create only |
| Flags (read/star/flag) | ✅ | 🟡 no star | 🟡 flag only, no star |
| Move to folder | ✅ | ✅ (re-IDs message) | ❌ no-op |
| Labels/categories + colors | ✅ | ✅ (master categories) | ❌ |
| Auto-archive filter on unsubscribe | ✅ filter | ✅ inbox rule | ❌ |
| Attachments (send + download) | ✅ | ✅ | ✅ |
| Importance / categories capture | ✅ | ✅ | partial |
| Incremental sync | ✅ history | 🟡 full-sweep (delta off) | 🟡 UID, INBOX-only |
| Push / webhook | ❌ (polling) | ✅ Graph subscription | ❌ |
| Multi-folder sync | ✅ | ✅ | ❌ INBOX-only |

---

## 7. Database schema (migrations 17 → 43)

| Table | Added | Stores |
|---|---|---|
| `email_accounts` | 17 (+34) | Connected mailboxes; encrypted creds; sync state; `initial_sync_done` (34) |
| `email_messages` | 17 (+18,21) | Synced mail cache; FTS index; `importance`/`categories` (18); `rules_processed_at` (21) |
| `email_attachments` | 17 | Attachment metadata |
| `email_folders` | 17 | Per-account folder/label metadata + counts |
| `email_sync_log` | 17 | Sync audit trail |
| `email_rules` | 19 | Automation rules (NL + static conditions, system_type) |
| `email_actions` | 19 (+32,33) | Rule actions; `delay_minutes`+`attachments` (32); `label_ai`/`content_manual` (33) |
| `email_executed_rules` | 19 (+36) | Rule-execution audit; `match_source`+`action_errors` (36) |
| `email_newsletters` | 19 (+43) | Unsubscribe disposition; `auto_archive_filter_id` (43) |
| `email_assistant_settings` | 20 (+22,26,27,39,40,41,42) | Per-account config (see below) |
| `email_senders` | 22 | Per-sender category |
| `email_cold_senders` | 22 | Cold-email verdicts + whitelist |
| `email_knowledge` | 26 | Draft knowledge base |
| `email_thread_status` | 27 | Reply Zero per-thread status |
| `email_ai_drafts` | 28 | Captured AI draft per thread (learn-from-edits) |
| `email_learned_patterns` | 28 (+38,39) | Draft preferences; `kind`/`scope_type`/`scope_value`/`is_style_evidence` (38) |
| `email_rule_patterns` | 31 | Deterministic per-rule FROM/SUBJECT patterns |
| (webhooks) | 25 | Graph push subscription state |

**`email_assistant_settings` evolution:** `about`/`signature`/`auto_run` (20) → `cold_email_blocker` (22) → `personal_instructions`/`writing_style`/`draft_replies` (26) → `follow_up_days` (27) → `learned_writing_style`+evidence count (39) → `fallback_model` (40, **removed in 42**) → `chat_model` (41) → **`rule_model`/`draft_model`/`chat_model`** roles (42). Also holds digest cadence, draft_confidence, follow-up windows, multi_rule_execution, sensitive_data_protection.

**Recent migrations (29+) at a glance:** 31 learned rule patterns · 32 delayed actions + artifacts · 33 AI-vs-manual action toggles · 34 initial-sync flag · 36 rule-exec metadata · 38 scoped reply memory · 39 learned writing style · 40 fallback model (removed) · 41 chat model · 42 three model roles · 43 provider-native unsubscribe filters.

---

## 8. NOT YET BUILT / PARTIAL

Grouped by area. Detailed acceptance criteria and the inbox-zero parity audit live in [`email_inbox_zero_parity_plan.md`](./email_inbox_zero_parity_plan.md). **(Archived note: this section has been re-verified 2026-07-22 and absorbed into `../email_app_master_plan.md` §5-§7.)**

### 8.1 Provider / sync gaps
- 🟡 **Outlook delta sync** disabled — running on full-snapshot sweep; needs a verified delta/deletion approach for true incremental + instant non-inbox changes.
- 🟡 **IMAP multi-folder sync** — INBOX-only until provider message IDs are folder-namespaced end-to-end (UID uniqueness collision); IMAP `move_to_folder`, labels, filters, draft update/send are no-ops by design.
- ❌ **Gmail Pub/Sub push** — Gmail still polls; Outlook Graph push already shipped.
- 🟡 **Outlook `/move` re-IDs the message** — stored `provider_message_id` can go stale until the next sync re-keys it (local folder already correct).
- 🟡 Scheduler has no exponential-backoff retry on sync errors (catches + logs).

### 8.2 Automation / intelligence gaps
- ❌ **Calendar / scheduling context in drafts** (availability + booking links) — needs a calendar integration first.
- ❌ **PDF / attachment content into draft context** — extract attached-PDF text for grounding.
- ❌ **AI "Clean" flow** — AI-scored bulk inbox cleanup with a review queue (beyond rule-based age-sweep + cleaner).
- ❌ **Attachment auto-filing** → Drive/OneDrive.
- ❌ **Meeting briefs** (email + calendar context).
- 🟡 **External MCP / CRM tools** in drafting context — approximated today via the sales/task-manager specialist agents; real MCP tools land with Metorite's MCP integrations.
- 🟡 Categorization **historical backfill with a date range** ("how far back") + a coverage report — today is limit-based / just-in-time.
- 🟡 Learning is per-draft-edit; no learning from **bulk actions** (e.g. "always archive newsletters").

### 8.3 Notifications / channels
- ⏳ **Chat-tool follow-up notifications** (Slack / Microsoft Teams / Telegram) — inbox-zero pushes follow-up nudges as interactive cards (Open / Mark-done). We surface follow-ups **in-app only**; needs a messaging-channel integration (OAuth + routing + outbound card + Mark-done webhook).
- 🟡 Digest delivery is **email-to-self only**; no custom recipient list or in-app/channel delivery.

### 8.4 Reliability / scale hardening (single-primary-user safe today)
- 🟡 **Non-streaming `run_agent`** BYOK detours via Copilot→402→self-anneal to DeepSeek; mirror the BYOK pre-injection block to make it DeepSeek-primary.
- 🟡 **OAuth `state` in-memory dict** → move to Redis+TTL for multi-worker deploys.
- 🟡 **DB session held across LLM/provider I/O** in rule/update paths → release before slow calls (pool-starvation risk under concurrency).
- 🟡 **LLM rule-matching is one call per email** with no shared cap → batch + global concurrency cap / per-account daily budget.
- 🟡 **N+1 queries + missing indexes** (`list_accounts`, `_load_rules`, reply-zero `DISTINCT ON`) → batch + add `email_messages(account_id, thread_id, received_at DESC)` index + FK on `email_thread_status.last_message_id`.
- 🟡 Multi-user tool scoping relies on the memory ContextVar + `ACB_AGENT_USER_EMAIL` fallback (reliable single-user; multi-user needs ContextVar propagation into the Copilot SDK tool-callback context).
- 🟡 Sanitize raw provider error text before surfacing/persisting (can embed tokens/URLs); encode `Content-Disposition` filename.
- 🟡 History tab: full per-message **timeline** view is the remaining parity gap.

### 8.5 UI polish
- 🟡 Demo mode (`NEXT_PUBLIC_EMAIL_DEMO=1`) falls back to bundled mock data when offline (dev-only).
- 🟡 Mobile uses per-view toolbars; the unified toolbar is desktop-only.

---

## 9. Key design decisions

| Decision | Rationale |
|---|---|
| `email_accounts` separate table (encrypted JSONB blob) | The `provider_keys` store is 1:1 service→key; multi-account needs N:1 with rotating tokens. |
| Sync to Postgres, not live-proxy | Fast queries, FTS, offline; avoids per-render rate limits. |
| Local commit authoritative, provider write-back best-effort | UX never blocks on a provider hiccup; failures logged, reconciled on next sync. |
| Reply Zero as a **projection of the rules pipeline** | One classifier, not a parallel system; fixed "all mail in To Reply" and keeps statuses consistent. |
| One MAF agent, all tools injected, dual-surface | Same executor/streaming/mutation/memory as every other agent; identical in Chat + Email apps. |
| Three per-account model roles | Cheap/fast classification, powerful drafting, powerful chat — tuned independently; replaced the agent+fallback model. |
| 25-preset cross-provider color palette | One canonical token round-trips to both Gmail label colors and Outlook master categories. |
| Confirm-before-send in chat | The agent never silently sends; the user approves the actual outbound in a blocking card. |
| Layered `routes/email/` package | `core` kernel + `transport` (mailbox I/O) + `automation` (inbox-zero) + `digest`; replaced a ~5.6k-line monolith. |

---

## 10. Success criteria — status

- ✅ Connect 2+ Gmail + 1+ Microsoft accounts.
- ✅ Unified inbox; mail appears within a sync cycle (instant on Outlook via webhook).
- ✅ Assistant summarizes/triages unread; drafts personalized replies (style + KB + memory).
- ✅ Send/reply/forward + attachments across connected accounts.
- ✅ Cross-account full-text search.
- ✅ Mobile-responsive layout.
- 🟡 Sub-5-min latency on Gmail depends on the polling interval (Pub/Sub push backlog).
