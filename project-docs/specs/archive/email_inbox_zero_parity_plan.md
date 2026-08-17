# Email Automation — Roadmap & Remaining Work

**Status:** living document · **Owner:** email app · **Created:** 2026-06-21 · **Updated:** 2026-06-29
**Upstream reference:** [elie222/inbox-zero](https://github.com/elie222/inbox-zero) (AGPLv3) —
cloned locally at `reference/inbox-zero` (gitignored) for its real prompts, default rules, and UI.

This is the **forward-looking companion** to [`email_ai_assistant.md`](./email_ai_assistant.md)
(the feature inventory — what shipped). This doc tracks the **remaining** inbox-zero parity gaps
and the **deferred backend hardening**. When something here lands, move its summary into the
inventory doc and mark it ✅ here.

> **Most of the original Phase 0–8 roadmap is now shipped.** The phase-by-phase detail has been
> collapsed into §2 (audit) and §3 (what's left). The earlier blow-by-blow is preserved in
> [`email_app_review.md`](./email_app_review.md) (build log) and git history.

---

## 1. Goal (unchanged)

A user should be able to: auto-categorize senders/mail (backfill + just-in-time); auto-draft
replies for the *right* emails (Reply Zero needs-reply detection) using their writing style, a
knowledge base, and personal instructions; get follow-up reminders for sent mail; configure the
assistant (KB, multi-rule, learned patterns, writing style, personal instructions); and use History
and Test tabs with inbox-zero depth. **All of these now exist** — remaining gaps are in §3.

---

## 2. Current-state audit (verified 2026-06-29)

Legend: ✅ done · 🟡 partial · ❌ missing

| Capability | inbox-zero | Metorite today | State |
|---|---|---|---|
| Rules: auto-run on new mail | yes | sync-loop hook **+ Outlook Graph webhook** (instant) | ✅ |
| Rules: LLM "best rule" match (forced JSON) | yes | `engine.py` classifier, owner/To/Cc/about/date + guidelines | ✅ |
| Rules: multi-condition (AI + static + category, AND/OR) | yes | full condition builder | ✅ |
| Rules: multiple-apply + `isPrimary` | yes | `multi_rule_execution` toggle + primary ordering | ✅ |
| Rule actions (archive/label/draft/forward/move/webhook/delay/attachments) | yes | full set; drafts never auto-send | ✅ |
| Learned classification patterns (FROM/SUBJECT, include/exclude, consistency-gated) | yes | `email_rule_patterns` + learn-from-client-labels + Fix | ✅ |
| Sender categorization (smart categories) | on-demand + new senders | manual **+ just-in-time on new senders** | ✅ |
| Cold-email blocker | yes | OFF/LABEL/ARCHIVE + whitelist | ✅ |
| Reply Zero: needs-reply / awaiting / done | yes (dedicated) | full-thread classifier, **projection of rules pipeline** | ✅ |
| Draft replies via agent (style + KB + instructions + memory) | yes | `_agent_draft_reply` → MAF drafter, full context | ✅ |
| Scoped reply memory (kind × scope) | yes | migration 38 (FACT/PROC/PREF × SENDER/DOMAIN/TOPIC/GLOBAL) | ✅ |
| Learned writing style (auto-derived) | yes | migration 39, evidence-gated regen | ✅ |
| Thread context + similar-thread + sender-reply-examples + classification hints | yes | all injected | ✅ |
| Draft confidence rubric (HIGH/MED/LOW) + NO_DRAFT gate | yes | `draft_confidence` + robust matcher | ✅ |
| Follow-up reminders (business-day windows, auto-draft nudge) | yes | scan + scheduled, anchored to reply time | ✅ |
| Digest | yes | view + email + scheduled DAILY/WEEKLY | ✅ |
| History: status + reasoning + match-source + approve/reject/undo | yes | shipped; **full per-message timeline** pending | 🟡 |
| Test: interactive run vs real recent mail (non-mutating) | yes | single + recent-inbox + rationale | ✅ |
| Email MAF agent (chat manages rules/settings/KB via tools) | yes | 67 tools, dual-surface, confirm-before-send | ✅ |
| Bulk unsubscribe (real RFC 8058 + provider filters) | yes | Inbox Cleaner: HTTP+mailto+Gmail filter/Outlook rule | ✅ |
| Bulk archive (age-sweep) | yes | 7/30/90/365, newsletters-only | ✅ |
| Categorization backfill **with date range** | yes | limit-based / just-in-time only | 🟡 |
| Follow-up notifications to Slack/Teams/Telegram | yes | in-app only | ❌ |
| Calendar context in drafts | yes | none (no calendar integration) | ❌ |
| Gmail Pub/Sub push | yes | polling only (Outlook push shipped) | ❌ |

---

## 3. What's left (open work)

### Automation / intelligence
- 🟡 **Categorization historical backfill with a date range** ("how far back") + coverage report.
  Acceptance: backfill since a chosen date categorizes senders in that window; idempotent re-run.
- ❌ **AI "Clean" flow** — AI-scored bulk inbox cleanup with a review queue (beyond rule-based age-sweep).
- ❌ **Calendar / scheduling context in drafts** (availability + booking link) — needs a calendar integration.
- ❌ **PDF / attachment content** extracted into the draft context for grounding.
- ❌ **Attachment auto-filing** → Drive/OneDrive; **meeting briefs** (email + calendar).
- 🟡 **Real MCP / CRM tools** in drafting context — approximated via sales/task-manager agents today.
- 🟡 Learn from **bulk actions** (not just per-draft edits).

### Notifications / channels
- ⏳ **Chat-tool follow-up notifications** (Slack / Microsoft Teams / Telegram) as interactive
  Open / Mark-done cards — `send-follow-up-notification.ts`. Needs a messaging-channel integration
  (OAuth + routing + outbound card + Mark-done webhook). Build alongside the MCP integrations.
- 🟡 **Digest delivery** beyond email-to-self (custom recipients / in-app / channel).

### History / UI
- 🟡 **History full per-message timeline** view (the one remaining History parity gap).

### Cross-cutting
- ❌ **Gmail Pub/Sub push** parity (Outlook Graph push shipped).
- 🟡 Mirror the BYOK pre-injection block into non-streaming **`run_agent`** (today it detours
  Copilot→402→self-anneal to DeepSeek — works, wasteful).

---

## 4. The email MAF agent (reference)

`apps/agent-email-assistant` — native MAF, `OpenAIChatCompletionClient` → gateway `/v1`, per-account
model roles (rule/draft/chat). **67 explicit tools** spanning read/triage, inbox actions,
drafting/send (confirm-before-send), rules CRUD + history + patterns, settings, knowledge base,
sender categorization, Reply Zero + follow-ups, unsubscribe + cold-email, digest, and sync — plus
injected `call_agent` / `web_search` / Mem0 memory / `write_artifact`. Drafting path injects writing
style + KB + personal instructions + scoped memories + thread context + precedent + sender examples.
Full tool list and context-injection detail: see `agents.py` and `instructions.md`.

---

## 5. Testing strategy (app-wide suite)

See [`tests/README.md`](../../tests/README.md). Principles:
- **Unit** (`tests/unit/`, CI-gated, no network/DB): mock provider + DB + LLM; pure logic + dispatch.
  Email feature tests live here (`test_email_*.py`): `test_email_rules_engine`, `test_email_webhook`,
  `test_email_assistant_settings`, `test_email_categorization`, `test_email_knowledge`,
  `test_email_drafting`, `test_email_needs_reply`, `test_email_followups`,
  `test_email_rule_conditions`, `test_email_history_undo`, `test_email_messages_filter`.
- **Integration** (`tests/integration/`, `@pytest.mark.integration`, opt-in): real DB/provider.
- **Frontend e2e** (`workbench/control_plane/e2e/`, Playwright).
- Run: `uv run pytest tests/unit/ -q` (fast) · `make cov` · CI runs `tests/unit/ -x -v` on PR.
- **Every new feature adds tests for its acceptance criteria before it's "done."**

---

## 6. Deferred backend hardening (from the multi-agent code review)

The **critical/high bugs were already fixed and deployed** (DB-engine leak, Outlook delta token,
digest due-check, ai_chat IDOR, send_email token persistence, backfill body, agent `follow_up_days`).
The items below **do not affect current functionality** at single-primary-user scale — they're
robustness / scalability / maintainability work to do before opening the app to many concurrent
users/accounts.

### Quick correctness wins (low risk)
- **401-retry on mid-session token expiry** (`providers/outlook.py`, `gmail.py`): the live
  `AsyncClient` freezes the access token; a token expiring mid-sync 401s with no retry and flips the
  account to `error` even though the refresh token is valid. On 401: refresh + rebuild `_http`, retry once.
- **`find_urgent` / OR-search**: the agent sends `"urgent OR deadline OR …"` into `plainto_tsquery`,
  which ANDs all terms → rarely matches. Use `websearch_to_tsquery` or per-term merge.
- **Webhook `clientState` when unset**: reject notifications when no `clientState` is stored
  (currently skips the check if NULL); use `secrets.compare_digest`.
- **Don't re-raise inside best-effort provider blocks** (`update_message`/`delete_message`): the
  local DB change already committed; a provider `HTTPException` shouldn't fail the user action.

### Scalability (before multi-user scale)
- **OAuth `state` → Redis+TTL** (`_oauth_states` in-memory dict breaks multi-worker + restarts).
- **Don't hold a DB session across LLM/provider I/O** (`_run_rules_job`, `update_message`): release
  before slow calls, re-acquire to write — prevents pool starvation under concurrency.
- **LLM batching + global concurrency cap**: rule-matching makes one LLM call per email per cycle per
  account with no shared cap. Batch per call; bound with a shared semaphore / per-account daily budget.
- **N+1 queries + indexes**: `list_accounts` (unread per account), `_load_rules` (actions re-fetched
  per email in the match loop), `reorder_rules` → batch with aggregates / `LEFT JOIN` / `unnest`. Add
  `email_messages(account_id, thread_id, received_at DESC)` for the reply-zero `DISTINCT ON` queries;
  add FK + index on `email_thread_status.last_message_id` and make the reply-zero JOIN a LEFT JOIN.

### Maintainability
- The old ~5.6k-line `email.py` was **already split** into the layered `routes/email/` package
  (`core` + `transport/` + `automation/` + `digest`). Remaining: keep provider-instantiation
  centralized through `_instantiate_provider`; pair with a shared session-factory.

### Minor / informational
- Return generic client errors (some handlers echo raw provider exception text that can embed
  tokens/URLs); sanitize `sync_error` before persisting; encode `Content-Disposition` filename
  (header-injection guard).
- IMAP is INBOX-only and its flag two-way-sync is a base no-op (documented limitation) — namespace
  UIDs by folder before syncing Sent/Drafts.
- Scheduler error path catches + logs but has no exponential-backoff retry.
