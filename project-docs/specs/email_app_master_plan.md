# Email App — Master Plan (single source of truth)

> **Product:** Metorite · **Feature:** Email AI Assistant App · **Created:** 2026-07-22
> **Status (verified on the box, 2026-10-01 17:57 UTC):** 🟢 **Email sync is ON in production.**
> The interim Microsoft app is installed (§10.2, D-EM-2). **§10 owns** Email for every organization.
> **§10 progress:** ✅ **EM-T1a is MERGED (#559) and serving since 2026-10-01.** The gateway
> signs the OAuth state, the callback runs behind the session, and the Graph webhook binds a tenant.
> ✅ **EM-T1b-1 is MERGED (#560) and serving since 2026-10-01** (§10.4.2). The scheduler and the
> sync core bind a tenant. ✅ **EM-T1b-2 is MERGED (#561)** (§10.4.2).
> ✅ **Email sync is ON in production since 2026-10-01 17:57 UTC.** The live check passed with
> `sync.scheduler_started accounts=0`. The Microsoft app is installed on the box, and
> Microsoft sign-in is live with it (§10.2, D-EM-2 interim). ✅ **EM-T3a (#563) and EM-T3b (#564) are MERGED. Email is live in the nav.** ✅ EM-T3c (#566), EM-T2a (#567), EM-T2b (#565) and EM-T2c (#568) are MERGED. ✅ EM-T3d MERGED (#571).
> ✅ **EM-T4a-1 MERGED (#570). EM-T4a-0 MERGED (#572). EM-T5 MERGED (#569), dark.** Sync phases (e) and (f) hold no session across a provider or model call (§10.4.6).
> 📝 **EM-T6 is SPECIFIED (2026-10-02).** Guided mailbox onboarding, in five parts (§10.4.7). ✅ **EM-T6a MERGED (#577, 2026-10-02, migration 225).** 🔨 **EM-T6b is BUILT, not merged** (branch `email-t6b`, no migration). The import runs newest first, in batches, with progress and resume.
> ✅ **EM-T4c MERGED (#575, 2026-10-02).** A 401 during a sync refreshes the token once, and the request goes again (§10.4.6).
> ✅ **EM-T6d, part 1 (range step and progress) MERGED (#579, 2026-10-02).** UI only (§10.4.7).
> ✅ **EM-T4f parts 1 and 2 MERGED (#578, 2026-10-02).** One sync runs at a time for each mailbox, which fixes the wait of 2 minutes. A disconnect answers 409 after 5 seconds when a sync holds the row, and it removes the Graph subscription (§10.4.6).
> ✅ **EM-T7 MERGED (#574, 2026-10-02, §10.4.9).** Automatic reply drafting is OFF for a new mailbox (D-EM-6).
> ✅ **EM-T5b-1 and EM-T5b-2 (narrowed) MERGED (#576, 2026-10-02), as ONE PR.** The four triage questions follow the System One conventions. With `email.rule_match=on`, Jev decides the rule match with no LLM path, and the automatic run touches new mail only (§10.4.8). The modes stay `off` in code, and the orchestrator sets them on the box after the deploy.
> **Earlier status (history):** live on the VPS for one Outlook account until the RLS cutover of 2026-08-23.
> **Last status change before §10:** 2026-08-04 — **P0 connect-flow outage CLOSED** (§7 Tier 1 item 1, partial).
> Nobody but the already-connected owner could add a mailbox from 2026-07-29 to 2026-08-04:
> the Connect button navigated the browser straight at the gateway, which default-deny 401s.
> Fixed by routing the authorize leg through a Next BFF, and the `user_email` override — the
> cross-tenant half of the same item — is closed with it. `_oauth_states` durability and the
> unauthenticated callback stayed open under §7. EM-T1a closes both (§10.4.1).
>
> **This document supersedes and consolidates all prior email planning docs:**
> - [`archive/email_ai_assistant.md`](./archive/email_ai_assistant.md) — the v2.0 feature inventory (2026-06-29; historical reference for architecture detail and the provider matrix)
> - [`archive/email_inbox_zero_parity_plan.md`](./archive/email_inbox_zero_parity_plan.md) — the inbox-zero parity roadmap (open items carried into §5-§6 here)
> - [`archive/email_tool_consolidation.md`](./archive/email_tool_consolidation.md) — tool-surface plan (63→42 done; unfinished merges carried into §6)
> - [`archive/email_app_review.md`](./archive/email_app_review.md) — the M0→M9 build log (history only)
>
> **Evidence appendix:** [`archive/email_feature_review_2026-07.md`](./archive/email_feature_review_2026-07.md) *(archived 2026-08-01 per §9, Phases 1–2 being long complete)* —
> the 2026-07-22 eight-agent full audit. Every defect referenced below (IDs like "review §2.1")
> carries file:line evidence there. When an item in this plan lands, mark it here and, if it
> came from the review, note the PR next to the review item.

---

## 1. Product vision — who this is for and what "done" means

**The customer today is one founder running their company from one Outlook mailbox.** The app is
not a webmail clone; it is an **AI chief-of-staff for email**. "Fully featured" means the
customer can trust it to:

1. **Triage without supervision** — every arriving message gets one honest classification;
   conversations are never splintered; bulk mail is filed or killed.
2. **Surface only what needs a human** — Reply Zero, follow-ups, and the digest tell the truth
   about what's waiting, with numbers that are never fabricated.
3. **Draft in the customer's own voice** — replies that need light edits, not rewrites, learned
   from real sent mail (not from correspondents' quoted text).
4. **Close loops** — a promise made in a sent reply becomes a tracked task; a thread awaiting a
   reply nudges at the right time; nothing falls through.
5. **Act safely** — nothing outbound or destructive happens without explicit confirmation;
   automation failures are visible, never silent.

**Trust is the product.** The 2026-07 review's core finding: the architecture is sound (no P0s)
but several surfaces *lie* — a Fix that saves and says it didn't, a sweep that fails and reports
success, a digest counting all-time threads as "awaiting your reply", APPLIED audit rows that
never reached the mailbox. Phase 1 exists because a customer who catches the product lying once
stops delegating to it. That is the PM lens for everything below: **honesty first, convergence
second, new capability third.**

### Explicit non-goals (reset from the old spec)
- **Multi-account / multi-provider parity is not a near-term goal.** The old success criterion
  "Connect 2+ Gmail + 1+ Microsoft accounts" is retired. Gmail and IMAP code stays (latent,
  test-covered where cheap) but no feature work targets them until a second real account exists.
- **Inbound SMTP receiving** — dead subsystem, removing (§6 decisions).
- **Inbox-zero feature-checklist parity as an end in itself** — parity was the scaffolding;
  the roadmap now optimizes for this customer's jobs, not the reference app's feature list.

---

## 2. Current state (condensed; details in the archived inventory + review)

**Architecture** (verified sound by the review): Next.js `/email` app (34 components) →
FastAPI `routes/email/` layered package (`core` / `transport/` / `automation/` / `digest`,
~16.3k lines) → `email_ingestion` service (provider abstraction, per-account async sync loop,
post-sync hook registry) → Postgres (migrations 17→87 — *Update 2026-08-01, doc-truth pass:
"17→87" was the range as of 2026-07-22; later phases added 88 (attachments dedupe), 89
(`internet_message_id`), 90 (snooze), 93 (Needs-Reply rename) and 119 (`email_contacts`,
§3.14), and the repo migration head is 140+ — per the numbering rule, never take a "next
free number" from a spec, only from `infra/postgres/`*). One MAF agent (42 tools), dual-surface
chat. Single-writer seams verified: one message upsert, one rule matcher, one LLM-JSON choke
point, one label writer, one signature assembler.

**Feature verdicts from the 2026-07-22 review:**

| Feature | Verdict |
|---|---|
| Classification core (engine/rules) | Sound — no drifted matcher copies |
| Runner / Reply Zero modules | Working, needs mechanical split (2,116 / 1,971-line files) |
| Inbox Cleaner | Sound — principled evidence ladder; 5 targeted fixes |
| Learned patterns | Sound design + one live bug (silent-save Fix) |
| Drafting / writing style | Real 5-layer system; learning inputs quote-polluted |
| Knowledge base | Real but naive (recency first-fit, no relevance ranking) |
| Digest | Wired end-to-end; semantically broken in the middle |
| Analytics | Sound; strongest-audited module |
| Search lexical / semantic | Sound / shipped-but-unlaunchable |
| Sync/transport | Sound skeleton; two drifted sync cores |
| Assistant chat | Sound; confirmation gate holes on 5 tools |

**Recently closed (do not re-plan):** #110/#111 one-classification-per-conversation; #112
thread repair; **#113 sweep armistice** (cleaner excluded from NEEDS_REPLY/AWAITING/DONE
threads); the 192 APPLIED-404 mystery (root-caused, #100/mig 86/#102/#103); analytics rebuild
(#99); auto-learn gate rework (#97 + #104); pattern approval (#96 + mig 85); Fix-teaches-
guidance (#105); whole-mailbox cleaner (#78/#91/#93).

**Doctrines that survived audit — do not "simplify" these away:**
- Reply Zero and sender categories are **projections of the rules pipeline**, never parallel classifiers.
- A conversation has **one** classification, re-evaluated per message; FYI status rows are **not** proof of conversation-ness; statused threads are **not the cleaner's to label** (#113).
- A metric ships **only if the user can act on it**; backlogs are levels, not flows.
- Drafts are **never auto-sent**; backfills **never draft** (user directive); live Reply-rule drafting stays ON.
- The sweep **never classifies** — it only projects existing evidence; internal domains are never blanket-labelled; Sent is skipped.
- Chat send tools **fail closed** when non-interactive.
- Local-commit-authoritative for *user* actions, but **provider-first for automation writes** (`apply_label` order. Phase 1 extends this to all rule actions).

### 2.1 Triage moves to the `decide` task — CP-13e (added 2026-09-23, D75)

The owner chose TypeSafe's Jev for fast typed decisions. Triage is its first
app adopter. `customer_console.md` §6A.14 is the contract, and this section
records only what the email app must keep true.

1. **`decide` replaces the AI step INSIDE the rules pipeline.** The doctrine
   above holds: one classification, and no parallel classifier. The pattern
   steps still run first, and the Uncategorized fallback still runs last.
2. **The order of adoption**, cleanest first:

   | Order | Call | Now | Shape |
   |---|---|---|---|
   | 1 | Cold-email check, `senders.py:1245` `_llm_is_cold` | `tier-fast` | boolean |
   | 2 | Auto-learn sender pin, `learning.py:67` | `tier-balanced` | boolean, with a 0.9 threshold |
   | 3 | Thread status, `replyzero.py:334` | `tier-balanced`, then `tier-powerful` | choice of 3 or 4 |
   | 4 | Rule classifier, `engine.py:322` `_llm_pick_rule` | the account's `rule_model` | choice of the enabled rules, plus none |

   Amended 2026-10-02 by D-EM-7. The rule match now covers the multi-rule mode too, and `rule_model` no longer applies. §10.4.8 holds the current anchors.

3. **Each one runs in shadow first.** It logs both answers and acts on the old
   one, until the agreement on this mailbox is measured. Then it switches to `on`. In `on`, no LLM call runs (D-EM-8).
4. **No escalation to an LLM** (D-EM-8, 2026-10-02). A threshold on the probability decides. A decided thread status gets no new `· auto` tag, because a second ask of the same thread gives the same answer. An old `· auto` row gets one more check.
5. ⚠️ **The rule count has no cap.** A user can write more than 20 rules, and
   accuracy falls as options grow. One boolean for each rule keeps each judgment small. A long rule list costs more questions, not a harder question. No LLM path remains (D-EM-8). The window measures accuracy against the rule count.
6. **Multi-rule execution moves too** (D-EM-7, 2026-10-02). One boolean for each rule replaces the multi-label LLM call. §10.4.8 holds the shape.
7. **Tier 1 item 3's semaphore covers `decide` calls too.** A decision is cheap,
   but a second mailbox still doubles the traffic.
8. **The owner answered residency for email** (D-EM-9, 2026-10-02). `DECIDE_ENABLED` and the router credential on the box are still owner acts (H-166).

---

## 3. Prioritization model

Four phases, strictly ordered by the PM lens from §1:

- **P0 · Phase 1 — Stop the lying** (correctness fixes, hours-to-a-day each; ~1 week total).
  Every item here is a place the product misreports its own behavior.
- **P1 · Phase 2 — Converge the seams** (structural PRs, ~2-3 weeks). Kills whole defect
  *classes* (drifted copies) instead of patching instances; unblocks Phase 3 features.
- **P2 · Phase 3 — Finish the product** (customer-visible features, ~3-4 weeks). What "fully
  featured" actually requires for this customer.
- **P3 · Phase 4 — Harden and scale** (security batch + multi-user prerequisites; ongoing).

Effort: **XS** <2h · **S** ≤1d · **M** 2-4d · **L** ~1wk.

### Remaining work at a glance (updated 2026-07-22, post-Phase-2 + same-day review)

Phases 1-3 are **done and live** (every non-parked item). What remains, by kind:

| Kind | Item | Where |
|---|---|---|
| **Live verification** (user, minutes) | Label-learn cycle · manual Sync + Resync · digest send retry (post-#148/#150) · ghost-merge dry→`--apply` · Phase-1 1.3/1.4/1.5 after a real cycle | §4, §5 notes |
| **Parked — dedicated session** (user's call) | 2.5 semantic search deep-dive (also unblocks dormant 3.1 voice few-shot) · 3.3b schedule-send (design ready; the one auto-send feature) | §5 2.5, §6 3.3 |
| **Owner decisions pending** | Build-or-kill batch-delete (§6 table — all six verified still present; one S PR) · manual pattern-add flow (badge exists, no create UI) · read-state push-on-open | §6, §7 Tier 2 |
| **Before any 2nd user/account** | Phase 4 Tier 1 (OAuth owner-binding → session-across-I/O → LLM cap → N+1s/indexes → 401 mid-sync retry) | §7 |
| **Hygiene, fold into next touch** | `followups.py` inline label-mirror → `actions.apply_label` · ~20 hand-rolled provider pairs → `provider_session` | §5 notes |
| **Added 2026-08-01 (doc-truth pass)** — §3.14 contacts follow-ups (shipped 2026-07-27, post-dating this table) | Contact card + `email_contacts` (mig 119) are LIVE; open items: `PATCH /email/contacts/{email}` (manual edit honoring `manual_fields`) · `GET /email/contacts` (paged list/search) · duplicate-merge design (decide BEFORE a Contacts view ships) · avatars (needs extra OAuth scope) · optional post-sync backfill | §3.14 |

---

## 4. Phase 1 — Stop the lying (P0) — ✅ COMPLETE, **MERGED + DEPLOYED** (PR #114, 2026-07-22)

All twelve items done (1.1 was already fixed by PR #113 mid-review). Merged to main as
PR #114 and live on the VPS. 706 email unit tests pass (+10 new); repo-wide CI-blocking
lint (`F821,F601,F602,F502,F7,B006`) and frontend `tsc` clean.

| # | Fix | Status | Where |
|---|---|---|---|
| 1.1 | Cleaner sweep labels conversation messages | ✅ **PR #113** | `_CLEANUP_SCOPE` |
| 1.2 | `_upsert_rule_pattern` returns True on success (Fix-with-pin no longer says "Nothing was saved") | ✅ `9031ee1` | `rules.py` |
| 1.3 | Cleaner failure honesty: abort live sweep on auth failure; `_sweep_job` stamps error; `failed` counter; UI surfaces the real error | ✅ `e71bed4` | `cleanup.py`, `BulkUnsubscribeView.tsx` |
| 1.4 | Engine tri-state via `LLMUnavailable`; never stamp `rules_processed_at` on an outage; all 5 callers handle it | ✅ `640ddfc` | `engine.py`, `runner.py`, `replyzero.py` |
| 1.5 | Provider-first in `_apply_rule_actions` (a refused action leaves no phantom local folder) | ✅ `a119f2e` | `runner.py` |
| 1.6 | Digest truth: needs-reply from `email_thread_status`; category filter via `canonical_cleanup_category`; preview==sent; UTC-honest labels | ✅ `a49f14c` | `digest.py`, UI |
| 1.7 | `undoSend` restores cc/attachments/artifacts + splits the body back into main+quote | ✅ `fb4a289` | `emailStore.ts`, `ComposePanel.tsx`, `page.tsx` |
| 1.8 | Fail-closed `_confirm_destructive` on the 5 unguarded tools + `@_annotate_risk` | ✅ `9ff75a2` | `agents.py` |
| 1.9 | Quote-strip at all three learning seams | ✅ `f089e11` | `drafting.py`, `assistant.py` |
| 1.10 | `/messages` FTS → `websearch_to_tsquery` (fixes `find_urgent`) | ✅ `50f8e25` | `messages.py` |
| 1.11 | Trust panel split into `repairable`/`permanent_failures`; button gated on repairable; dead "Try again" fixed | ✅ `111eee3` | `analytics.py`, `AnalyticsView.tsx` |
| 1.12 | LLM-failure draft fallback → sentinel on automation paths (human template only interactive) + no Mem0 pollution | ✅ `02b77a0` | `drafting.py` |

**Exit criterion met:** every number, toast, and status the app shows is either true or absent.

**Deferred from 1.4 into Phase 2** (deliberately out of scope for the minimal outage fix):
the `provisional` boolean column replacing the `'· auto'` reason-suffix self-heal marker
(review §3.1 P2-4) — a schema change; fold into 2.2's `classify_and_apply` work.

**Live verification owed on the real account** (1.3/1.4/1.5 need a real sync cycle to
confirm — see memory note on verifying-after-a-cycle).

---

## 5. Phase 2 — Converge the seams (P1)

> **Status re-audit 2026-07-22** (evidence read directly from code, not the plan —
> the table below had drifted badly out of date). Several items were quietly
> completed alongside Phase 1 / migrations 88–90 and never struck here.
> **DONE:** 2.4, 2.8, 2.9, 2.10, 2.12. **Schema+code done, one-off remains:** 2.6.
> **PARTIAL:** 2.1, 2.2, 2.7 (specifics per row). **Not started:** 2.3, 2.11.
> **Parked by the user:** 2.5 (see [`email-semantic-search-revisit`] memory).

| # | Work | Kills / unblocks | Effort | Source |
|---|---|---|---|---|
| 2.1 ◐→ | Collapse the two sync cores + label-learning as a post-sync hook. **The DEFECT half is FIXED:** label-learning is revived on the scheduler path — `PostSyncHooks.learn_label_changes` (carries the per-message `(message, old_categories)` captured pre-upsert in `_sync_account`'s persist, since the upsert overwrites categories on a categories-authoritative provider); `run_label_learn_hook` invokes it after commit; the gateway registers `scheduler_hooks.learn_label_changes` → the shared `sync.learn_from_label_change_events` orchestrator. So the scheduler + webhook paths (which poll every ~300s) now learn from manual label changes instead of dropping them. Gated on incremental-only, same as the manual route. **Collapse DONE too:** `trigger_sync` is now a thin wrapper — ownership check + `_run_manual_sync` → `_sync_account` (the ONE core; ~268 duplicated lines deleted, incl. the now-redundant inline learning, which flows through the hook like every other path). `resync_account` routes through the same helper — fixing a latent crash where it called the trigger_sync ROUTE with `user` in the `background` slot (unresolved `Depends` → 500 on every direct resync). Parity tests re-pinned on the single core; `transport/sync.py` is no longer an ingest path (guard added). **2.1 COMPLETE.** | Kills refresh-token loss on manual sync, cursor drift, and revives label-learning (was dead in production — scheduler path never ran it) | M-L | review §3.2 |
| 2.2 ✅◐ | `classify_and_apply()` — centralize match → conversation-resolve → apply → watermark. **DONE (the substance):** match→resolve was already one place (`classify_matches`, `engine.py:832`; run-message bypass fixed; `approved_includes_only` explicit `engine.py:523`); apply+watermark is now ALSO one place — `_apply_matches` + `_stamp_processed_watermark` (`runner.py`) collapse the 4 hand-rolled copies (run-message, process-past, `_run_rules_job`, PENDING-retry). The watermark's "only when the run could act" guard and the `sole_match` learning guard each live in exactly one function; process-past routes through the shared apply+watermark while keeping its DELIBERATE oldest-first raw-match classify carve-out (inline resolve would let a thread's oldest message decide status). **Optional remainder:** surfacing `approved_includes_only`/`resolve` as one explicit policy arg on a single façade (today callers pass their own to `classify_matches`) — cosmetic; not blocking. | The #110 invariant enforced in ONE place instead of 2-of-5 call sites; closes the run-message/process-past bypasses and the unreviewed-pattern blast radius | M | review §3.1, §2.2 |
| 2.3 ✅ | Split `runner.py` → `actions.py` (label writers + `_apply_rule_actions` dispatcher) / `learning.py` (auto-learn gate) / `jobs.py` (already extracted) + routes+jobs staying in `runner.py` (2,344 → 1,735); split `replyzero.py` → `chat.py` (AI chat/quick-action SSE, ~390 lines finally leave Reply Zero) / `followups.py` (reminder scan+job) with the thread-status AUTHORITY + views staying in `replyzero.py` (2,167 → 1,565) — deliberate deviation from a separate `thread_status.py`: every consumer imports the authority from `replyzero`, and a rename adds churn with no concern-separation gain. `runner` re-exports the moved action names so lazy importers + tests keep addressing that seam. **The `LIKE '%sender%'` evidence collision fixed while moving** (`learning._sender_consistent_for_rule` now matches the sender by exact case-folded equality — substring matching let `a@b.com` corroborate/veto `aa@b.com`'s history, corrupting the auto-learn consistency bar). | 4,000 lines of mixed concerns; kills every lazy per-row import; the chat SSE code (~390 lines) finally leaves Reply Zero | M | review §3.1 |
| ~~2.4~~ | ~~Draft transport carries cc/bcc/attachments~~ ✅ **cc/bcc #119, attachments #123** | Killed silent draft data loss AND the three-way native-vs-full-send branching | M | review §3.2 |
| 2.5 ⏸ | Fix embeddings sweep SQL (`convert_to(...,'UTF8')`), align hash semantics, add a real-Postgres test, **enable `email_semantic_search_enabled` in prod**. **PARKED by the user** — deep-dive in a dedicated session (flag wiring + broken sweep SQL + live embed cost). | Semantic search stops being shipped-but-unlaunchable; **prerequisite for 3.1** | S | review §3.3 |
| 2.6 ✅◐ | `internet_message_id` column + `$select` + upsert dedupe + one-off merge of the ghost pairs. **Schema+code DONE** (mig 89 + ingest reclaim). **One-off written:** `scripts/merge_ghost_messages.py` — merges rows sharing a backfilled `(account_id, internet_message_id)`: keeps the richest (classified-then-newest) survivor, carries a sibling's categories/watermark if the survivor lacks them, repoints the SET-NULL FKs (`email_executed_rules`, `email_rule_guidance`), deletes the ghosts (attachments/embeddings cascade). Dry-run by default; reports (does NOT merge) rows still NULL — those must re-sync first to learn their id. **To run (user, live):** sync to backfill ids → `uv run python scripts/merge_ghost_messages.py` (dry) → `--apply`. | Kills duplicate classification of Outlook-rekeyed messages and the ghost rows skewing thread heuristics | M | review §3.1 |
| 2.7 ✅ | Digest = projection — DONE. `_digest_categories` and `_digest_top_senders` no longer run their own SQL: they COMPOSE `analytics._categories` (message categories with the per-thread status fallback) and `analytics._noisy_senders` (mail you neither read nor ever answered) under the digest's own scope predicate (this account, inbox-only, self-excluded) and window — one computation, two projections; the analytics helpers were already scope/window-parameterized, so analytics.py itself is untouched. The configured-category filter became a post-filter on the composed rows (canonicalised the same way). Semantic upgrades disclosed: the category breakdown now matches the Analytics chart (was the `email_senders` rollup, which disagreed), and "Top senders" became "Noisy senders you never answer" (raw volume on a founder's mailbox just lists colleagues). HTML body / empty-suppression / self-exclusion were already done. **Dialogs merged:** ONE self-contained `DigestSettingsDialog` (categories + schedule + send-to-email, self-fetches settings+rules, `onSaved` syncs the embedding screen) used by BOTH DigestView and AI-Settings; both drifted local copies deleted. | Digest stops re-deriving (wrongly) what analytics already computes correctly | M | review §2.5 |
| ~~2.8~~ | ~~`email_attachments` UNIQUE `(message_id, provider_attachment_id)` + dedupe migration~~ ✅ **mig 88 + both inserts (`messages.py:512,668`) name the arbiter** | Closes the dormant Gmail duplication bug at the schema level | S | review §3.2 |
| ~~2.9~~ | ~~Shared `JobTracker` with sequence-token guard; concurrency guard on manual sweeps~~ ✅ **`automation/jobs.py::JobTracker` adopted by cleaner/runner/replyzero; `is_running` guard at `cleanup.py:937,1000` + `replyzero.py:1812`** | Kills the job-clobbering class | S | review §2.1 |
| ~~2.10~~ | ~~Sync-loop exponential backoff + orphaned-`running` sweep; cache `masterCategories`; Graph 429 `Retry-After`~~ ✅ **`scheduler.py::_next_backoff`(cap 3600s)+`_close_orphaned_syncs`; `outlook.py` per-instance `_master_categories` cache + `_MAX_RETRY_AFTER_SECS` 429 handling** | Stops hammering a revoked account every 300s; cuts every Outlook label apply from 3 Graph calls to 2 | S-M | review §2.1, §3.2 |
| 2.11 ✅ | `provider_session()` context helper (instantiate → authenticate → persist rotated creds on exit) replacing the boilerplate copies. **DONE (#139 + #144 + #150):** `core.provider_session` async CM (persists rotated creds in a `finally` only on clean exit; `user_email=None` = unscoped background mode via `_provider_for_account_any`). Converged: hydrate, both draft-create handlers, digest test-send + scheduled-send, mailto-unsubscribe, `/send`, drafts upsert/send, cleaner sweep, senders background jobs, and (#150, found in review) `download_attachment` — which had been the one site with NO persist *and* no `authenticate()` at all. **Scope honesty:** ~20 hand-rolled instantiate+persist PAIRS remain (`messages.py` ×5, `folders.py`, `cleanup.py` ×4, `replyzero.py` ×5, `runner.py` ×8, `sync.py::_ensure_subscription`, `followups.py` ×2) — each spot-checked to pair its persist correctly, so no dropped-token defect today; converge them opportunistically when touching those files, not as a dedicated pass. | Credential-rotation safety by construction | S | review §3.1 |
| ~~2.12~~ | ~~Promote the repair script's damaged-threads SQL to a maintained health metric~~ ✅ **`analytics.py::count_damaged_conversation_threads` (shared w/ the repair script) → `data_health.damaged_threads`** | The #110 invariant gets a permanent regression alarm instead of a one-off script | S | review §3.1 |

**Exit criterion:** no behavior-bearing logic exists in two places; every invariant has exactly
one enforcement point.

**PHASE 2 COMPLETE (2026-07-22).** Every item closed: 2.1 (sync-core collapse + scheduler label-learning), 2.2 (one apply+watermark enforcement point), 2.3 (runner/replyzero splits + LIKE-collision fix), 2.4, 2.6 (mig 89 + ghost-merge script — the one-off `--apply` run on live data is the user's call, after a sync backfills `internet_message_id`), 2.7 (digest = projection + one dialog), 2.8, 2.9, 2.10, 2.11 (provider_session on all write/background paths), 2.12. The single deliberate exception: **2.5 semantic search stays PARKED by the user** (dedicated deep-dive session). Landed as PRs #139, #140, #141, #143, #144, #145, #146, #147.

**Post-completion review (2026-07-22, same day — three parallel audit agents over merged main, findings hand-verified).** The structure held: single watermark writer confirmed (`runner._stamp_processed_watermark` is the only `rules_processed_at` UPDATE in the tree), no import cycles among the split automation modules, re-exports + route flattening correct, digest verified as a true projection (no leftover local aggregate SQL), `_run_manual_sync` delegation semantics verified (ownership check, error propagation, post-sync hook, `full=True` on resync). Three defects found and fixed same-day:
- **#148 (prod 500, user-reported):** the digest commitments query bound `:aid` in both a uuid and a text context — Postgres deduces ONE type per prepared-statement parameter, so it failed on *every* call — and its best-effort `except` left the transaction aborted, killing the next query in `/digest/send` while the preview looked fine. Fixed: single-context bind + rollback-and-log in the catch. **Lesson recorded:** a mocked-DB test cannot catch asyncpg type deduction, and `except Exception: return []` on a shared session is a transaction poison — every best-effort DB catch must roll back.
- **#150:** the same no-rollback pattern in `learning.py`'s two gate probes (directly upstream of the audit-row INSERT on the same session) — patched with rollback + log.
- **#150:** `download_attachment` was the last RAW provider call site (no `authenticate()`, no rotated-cred persist) — converted to `provider_session`.
Remaining hygiene from the review (SMELL, not defects): `followups.py:177` re-implements the local label-mirror append inline instead of calling `actions.apply_label` — fold into the next touch of that file.

---

## 6. Phase 3 — Finish the product (P2, customer-visible)

Ranked by value to the founder-on-Outlook customer:

| # | Feature | Customer job | Effort | Notes |
|---|---|---|---|---|
| ~~3.1~~ | ~~**Sent-mail few-shot drafting**~~ ✅ **#131** (code shipped; DORMANT until semantic flag ON) | Draft in my voice | M | `_fetch_sent_fewshot` cosine-matches the account's Sent mail → `<voice>` block. Zero cost when `email_semantic_search_enabled` is off; enabling it (token-costing live sweep) is a confirm-point. |
| ~~3.2~~ | ~~**Conversation collapse in the mailbox list**~~ ✅ **#136** | Triage at thread level | M | `GET /messages?collapse=true` (default for the browse) DISTINCT-ONs the conversation key `COALESCE(thread_id, id::text)`, newest-in-view per thread; total counts conversations. Search stays per-message. |
| 3.3 | **Snooze** ✅ **#137** / **schedule-send** ⏳ | Control timing | M-L | **Snooze DONE (#137, mig 90):** `snoozed_until` column, query-time wake (no scheduler), thread-wide stamp, Snoozed view, right-click presets. **Schedule-send PENDING** — outbound; design ready (send-later table + `deliver_scheduled_sends` post-sync hook + composer UI); the one auto-send-on-live-account item — surfaced to the customer for a go/semantics call before building. |
| ~~3.4~~ | ~~**H6 — Fix strips the wrong label**~~ ✅ **#124** | Corrections stick | S | `remove_label` + `correct_applied_labels` strip the wrong rules' LABEL values off the message + apply the corrected one. |
| ~~3.5~~ | ~~**KB relevance ranking**~~ ✅ **#132** | Grounded drafts | S-M | Lexical relevance ranking (not recency first-fit) via `_load_assistant_about(query=)`, always-on; KB dropped from the thread-status classifier (`include_kb=False`). Embedding-cosine variant deferred. |
| ~~3.6~~ | ~~**Pattern review UX completion**~~ ✅ **#127** (reject in-force / restore rejected) | Trust the teaching loop | S | Deferred: manual pattern-add / `USER` badge, None-Fix exclude re-expose. |
| ~~3.7~~ | ~~**Reclassify that finishes the job**~~ ✅ **#129** | One-click recovery | S-M | Now drains the whole mailbox (loop-until-empty), resumable no-progress stop, JobTracker progress + status endpoint, concurrency guard. |
| ~~3.8~~ | ~~**Rule-path draft context parity** + compose-assist learning~~ ✅ **#125** | Auto-drafts as good as manual | S | Runner routes through `_build_reply_context`; compose-assist stores AI draft; follow-up nudge hydrates; pop-out passes `messageId`. |
| ~~3.9~~ | ~~**History per-message timeline**~~ ✅ **#135** | Audit any message | S-M | `GET /messages/{id}/timeline` — received anchor + each `email_executed_rules` row for the message, chronological; `MessageTimelineModal` off the detail "More" menu. Reuses the audit rows, no new table. |
| ~~3.10~~ | ~~**Calendar context in drafts**~~ ✅ **#133** | Scheduling replies | M | `_asks_about_scheduling` heuristic + `_fetch_calendar_context` (internal calendar = gtd_items hard-dates) → calendar block in the draft prompt only on scheduling asks. External calendar sync stays deferred. |
| ~~3.11~~ | ~~**Digest as the daily brief**~~ ✅ **#128** | One glance a day | S-M | Backlog aging (oldest NEEDS_REPLY) + commitments-due (open gtd_items linked via origin) lead both bodies + in-app cards. |
| ~~3.12~~ | ~~Search filter UI completion~~ ✅ **#126** | Find anything | S | Date-range / sender-category / importance pills + FilterMenu sections; `importance` added to `/search`. |
| ~~3.13~~ | ~~**Digest → mailbox DASHBOARD**~~ ✅ (2026-07-22, user-requested) | Act, not read | M | Live-audit found the digest's action half broken/unactionable: 10/29 NEEDS_REPLY were threads the user had ALREADY answered (determiner returned REPLY on our-side-last threads and the authority wrote it — now CLAMPED to AWAITING in `recompute_thread_status`, keyed on the real last speaker); a trashed thread sat in the backlog (thread lists/counts now exclude trash/junk); 91 AWAITING threads and 3-of-4 undated commitments were invisible. `_generate_digest(full=)`: dashboard projection = full lists + thread/message ids + awaiting + undated commitments; email keeps small caps (one computation, two projections). UI: `DashboardView` (feature key stays `digest`) — needs-reply + waiting-on-them ledgers with click-through (`openEmailById`) and row actions (Mark done via `/reply-zero/resolve`, Snooze-1d), commitments incl. undated, category/noisy-sender analytics. |

**Explicitly deferred** (revisit after Phase 3): AI-scored "Clean" review queue; PDF/attachment
content grounding; attachment auto-filing; meeting briefs; learning from bulk actions;
categorization backfill date-range + coverage report; Gmail Pub/Sub push; richer AG-UI typed
`requires_confirmation` events + rule-suggestion approve card; email-KB → other-agents/Mem0
bridge (needs a scoping design so account-scoped memories stay private).

**Shipped 2026-07-23 (second wave, user-directed):**
- **"Reply" → "Needs Reply" rename** (#168 + #170 hotfix, mig 93): mig rewrites owned data;
  `persist._RENAMED_LABELS` canonicalises at ingest (the categories-authoritative provider
  re-asserts old labels every sync); legacy aliases at every resolve seam. Prod-verified:
  0 old-label / 167 new-label messages.
- **Outlook-desktop quote collapse** (#171): text-heuristic boundary (From/Sent/To header
  block, "On … wrote:", "Original Message") in `quoting.ts` — the Word renderer emits no
  marker ids at all.
- **Fix-anywhere** (#172): FixDialog from any mailbox row's context menu + Cleaner sender
  rows. **Dismiss ≠ Done** shipped (#172): `/reply-zero/resolve dismiss=true` → FYI, tasks
  left open.
- **Uncategorized = state, not label** (#175): pill click recategorizes and branches on WHY
  it failed — no-match (healthy classifier) auto-opens Fix; classifier-down surfaces as a
  backend fault, never "fix your rules". Indicator unwritable at all four category writers;
  fixed #168 regression where `CONVERSATION_LABELS_LOWER` lacked "needs reply" (Needs-Reply-
  only mail counted as uncategorized in every facet).
- **Rules-view unification** (#177): learned patterns nested under their rule in RulesTab
  (same PatternRow as Settings, inline approve/reject/forget); header states the pipeline
  order (patterns → AI → Uncategorized/Fix). Stores stay separate — approval gate (#96/#97)
  and provenance preserved.

**Dashboard v2 candidates** (brainstormed 2026-07-22 with 3.13; each is an owner call):
- **Draft-from-dashboard**: a ✍️ action on a needs-reply row that opens the thread with an AI
  draft already prepared (wire to the existing compose-assist path).
- **Per-thread Nudge** on waiting-on-them rows: one-click AI follow-up draft (the follow-up
  drafter exists; needs a per-thread endpoint + the confirm-before-send gate).
- **AI priority ordering**: rank the reply queue by urgency/importance (sender importance ×
  age × content), not just age — the current oldest-first surfaces dead loops.
- **Click-through analytics**: category chips → filtered mailbox view; noisy senders → Email
  Cleaner row (unsubscribe/block from the dashboard).
- **Morning-brief LLM one-liner**: a single sentence ("2 urgent: X's quote, Y's contract")
  atop the dashboard and the emailed digest — costed, opt-in.

### 3.14 Contact card ✅ (2026-07-27, user-requested) — and the Contacts view it unlocks

**Shipped.** Clicking a display name, avatar orb, or any To/Cc recipient opens a people
card (`ContactCard.tsx`, `GET /email/contacts/card`): identity, signature-derived phone /
title / company / links with per-field copy buttons, correspondence stats (received / sent /
unread / last seen), the sender's category and any Cleaner suppression, and the person's last
three messages as previews that open on click. Wired into the reading pane's sender block,
its To/Cc lines, and every message header in the conversation view.

**The part that matters for later: the card WRITES what it learns.** Every open upserts into
`email_contacts` (mig `119`) — display name, job title, organisation, phone numbers, links,
with `source_message_id` + `parsed_at` for provenance. So the mailbox accumulates a people
directory as a side effect of being read, with no data entry, no directory sync, and no
external service. There is no separate crawl to build and nothing to backfill: the addresses
a user actually looks at are exactly the ones worth having.

Rules the writer already enforces (do not weaken these when building on it):
- **`manual_fields[]` is permanent.** It names the columns a human edited; the signature
  writer skips them forever. Without it, the next mail silently reverts a correction — the
  one failure that would make a Contacts view untrustworthy.
- **An empty parse never blanks a stored value.** A one-line reply with no sign-off leaves
  yesterday's phone number intact.
- **Derived facts are never stored.** Message counts, first/last seen, unread are a live
  query over `email_messages`; freezing them would be wrong the moment mail arrives.
- **The domain guess is display-only.** "acme.com" → "Acme" is shown when nothing better is
  known, and is deliberately never written — it is a guess about the address, not something
  the person told us.
- Rows are per email ACCOUNT and cascade with it (matching `email_senders` /
  `email_newsletters`), so disconnecting one mailbox never deletes another's knowledge.

**Contacts view — what is already there when it gets built:**

| Needs | Status |
|---|---|
| A people store to list / search / sort | ✅ `email_contacts`, populated and growing |
| Per-person detail (title, org, phones, links) | ✅ same table |
| Correspondence stats + recent mail per person | ✅ `GET /email/contacts/card` returns both |
| Group by company | ✅ `idx_email_contacts_org` exists for exactly this |
| Editing a contact by hand | ⏳ needs `PATCH /email/contacts/{email}` writing the field **and** appending its column name to `manual_fields` — the storage contract is already designed for it, the endpoint is not written |
| Listing / searching contacts | ⏳ needs `GET /email/contacts` (paged, `q=`, `organization=`) |
| Merging duplicates (same human, two addresses) | ⏳ undesigned — decide whether it is a `merged_into` pointer or a person-level parent row BEFORE the view ships, because retrofitting identity onto a flat address table is the expensive version |
| Photos / avatars | ⏳ undesigned; provider APIs (Gmail People, Graph) are the only real source and both need OAuth scope the app does not currently request |

**Backfill option** (not built, cheap when wanted): the same parse can run over each account's
recent mail in a post-sync hook, so the directory fills without waiting for someone to click
every sender. Deliberately deferred — it is a body-text scan over the whole mailbox, and the
click-driven path already covers everyone the user cares about.

### Build-or-kill decisions (each needs a one-line owner call)

| Item | Recommendation |
|---|---|
| Rule-action `delay_minutes` (stored/edited, never executed) | **Kill the UI knob** until a deferred-action executor has a real use case; the column stays |
| Slack/Telegram draft delivery ("Coming soon" UI) | **Remove the dead UI**; rebuild when a messaging integration exists platform-wide |
| Inbound SMTP receiver (`inbound.py`, no launcher, broken DB URL) | **Delete the subsystem** (git history preserves it) |
| Orphan endpoints: `/email/ai/chat`, `/email/ai/quick-action`, `GET /newsletters`, `POST /artifacts/import` | **Delete** (~250 lines; clients were removed) |
| Dead frontend: `useEmails.ts`, 6 dead `api.ts` exports, ~20 fossil card keys | **Delete** |
| Write-only tables `email_folders`, `email_sync_log` | Keep `email_sync_log` (cheap audit); **drop the `email_folders` mirror write** or start reading it |
| Gmail (1,106 lines) + IMAP (792 lines) providers | **Keep latent**, mark unsupported in docs; no feature work; fix only schema-level hazards (2.8) |
| Unfinished tool merges (M7 `manage_rule`, M8 `manage_knowledge`, M13 `manage_labels`) | **Close the plan at 42 tools** — measured value of further merging is low; delete the fossil card keys instead |
| `Support`/`Unknown` sender categories, `'user'` category-override reservation | Remove from the API vocabulary or build the manual set-category flow in 3.6 |

**Verified 2026-07-22:** every kill-candidate above is *still present in code* — the table records
recommendations, not executed work. Evidence: `inbound.py` exists unlaunched (only its own
docstring references its start function); `GET /newsletters` (`senders.py:465`) and
`POST /artifacts/import` (`send.py:205`) have zero frontend callers; `useEmails.ts` has zero
importers; the Slack/Telegram "Coming soon" block renders at `RulesTab.tsx:1215-1244`;
`delay_minutes` is an editable input at `RulesTab.tsx:1180-1189`; `email_folders` is written at
`folders.py:177-187` and read nowhere. `/email/ai/chat` + `/email/ai/quick-action` are
*deliberately* retained for external callers (in-app clients removed by design) — not part of the
kill batch. **One approved batch-delete PR (S) closes all six.** Also confirmed still open from
3.6's deferral: the manual pattern-add flow — the `USER`/"Manual" badge exists but there is no UI
or API client to create a pattern by hand.

---

## 7. Phase 4 — Harden and scale (P3)

> **Re-verified against main 2026-07-22** (agent audit, file:line evidence checked): every item
> below is STILL REAL. Ranked by urgency **before any second user/account is added** — today,
> with one trusted user on one account, none of these is an active incident.

**Tier 1 — must land before a second user/account:**
1. **OAuth owner-binding** (`transport/oauth.py`) — 🟡 **PARTIALLY CLOSED 2026-08-04.**

   **What this item did not say, and what it cost.** Ranked here as "not an active incident
   with one trusted user", it *was* an active incident for everyone else. The two Connect
   buttons (`email/page.tsx`, `integrations/page.tsx`) navigated the browser straight at
   `${NEXT_PUBLIC_GATEWAY_URL}/email/oauth/{provider}/authorize` — deliberately, because the
   response is a 302 to the provider and a `fetch()` cannot put a consent screen in the address
   bar. A top-level navigation carries no Bearer and no `X-User-Email`: session cookies are on
   the workbench origin, not `api.*`, and a navigation cannot add custom headers. When
   default-deny landed app-wide (57ec82d9, 2026-07-29) the authorize leg — correctly absent from
   `main.PUBLIC_ROUTES` — began answering `{"detail":"Authentication required"}` to every user,
   before the handler and its `user_email` fallback ever ran. **Nobody could connect an email
   account for six days.** It surfaced only when a colleague tried; the owner's mailbox predated
   the change. `git log -S` finds no commit that ever added the authorize leg to `PUBLIC_ROUTES`,
   so the fallback parameter this item flagged as a *security* defect had, by then, never been a
   working path at all.

   **CLOSED — routed through the BFF, not opened up.** The navigation now targets
   `workbench/control_plane/src/app/api/email/oauth/[provider]/authorize/route.ts`, which runs
   server-side with the session, calls the same gated gateway route with `gatewayHeaders()` and
   `redirect: "manual"`, and re-issues the upstream `Location` as its own redirect. No gateway
   route changed and **no new public surface exists**. Adding the authorize template to
   `PUBLIC_ROUTES` was the tempting repair and is strictly worse than the outage: the handler
   writes `{"user_id": …}` into the state the callback turns into an `email_accounts` row, so
   anonymous + identity-from-a-query-parameter lets anyone bind a mailbox to a colleague's
   account. `tests/unit/test_email_oauth_authorize_wiring.py` pins the template *out* of
   `PUBLIC_ROUTES` for that reason.

   **CLOSED — the identity override.** `user_id` was `user_email or user.email`; it is now
   `user.email or user_email`, so the authenticated identity outranks the parameter and the
   parameter survives only as a fallback for a caller with no header identity. The browser no
   longer sends it at all, and the BFF does not forward it.

   > **Superseded by §10.4 EM-T1a (2026-10-01).** A stateless signed state replaces the Redis
   > design below, and the callback moves behind the BFF session. Read §10 for the fix.
   > EM-T1a built that fix on 2026-10-01, so the paragraph below is history.

   **CLOSED by EM-T1a (history).** `oauth_callback` was unauthenticated (no `get_current_user`) and its `state` is
   an unsigned random token. `_oauth_states` (`oauth.py`) is still a module-level in-process dict:
   **every deploy restarts the gateway, so any flow in flight when a deploy lands loses its state
   and the callback fails validation** — the user is bounced to
   `/email/oauth/callback?error=invalid_state` with no explanation. It is also not shared across
   workers, and entries are never expired, so abandoned flows leak forever. Fix remains: signed
   state + verified callback, Redis + TTL. Recorded in `apps/services/gateway/AGENTS.md` and in a
   comment on the dict itself.
2. **Stop holding DB sessions across LLM/provider I/O** — `_run_rules_job` opens one session
   and holds it across the whole message loop including `_llm_json` awaits (`engine.py:747,815`)
   and provider HTTP in the apply path; same in `_process_past_emails_job`. One account = a
   pinned connection for seconds; N accounts = pool exhaustion.
3. **LLM concurrency cap / per-account budget** — per-run bounds exist (run limit 50,
   process-past 2000, 366-day span) but nothing coordinates *across* jobs/accounts; each account
   gets its own perpetual loop task plus user-triggered background jobs. A second account
   doubles uncapped classify traffic. Add a shared semaphore + per-account daily budget.
4. **N+1s + indexes** — `list_accounts` runs one COUNT per account (`accounts.py:63-72`, twice);
   `_load_rules` fetches actions per-rule (`rules.py:118-125`) and is called once **per email**
   in the match loop. Missing: `email_messages(account_id, thread_id, received_at DESC)`
   composite; `email_thread_status.last_message_id` has no FK and no index.
5. **401-retry mid-sync** — providers refresh only at the initial `authenticate()` probe; the
   cached client bakes a static bearer header (`outlook.py:119-130`, `gmail.py:172-183`) and
   never rebuilds on a mid-stream 401 (Outlook retries only 429). A token expiring during a deep
   backfill fails the whole cycle. Fix: refresh + rebuild client + retry once.

**Tier 2 — real but lower urgency (defense-in-depth / polish):**
- **SSRF DNS-rebind**: unsubscribe fetcher (`senders.py:586-644`) and image proxy
  (`attachments.py:74-104`) both resolve-then-refetch by hostname; redirect-based SSRF is
  already closed (manual per-hop revalidation), the residual is the rebind TOCTOU. Fix by
  pinning the resolved IP into the transport.
- **Webhook `clientState`** (`sync.py:346`): NULL stored state accepts unverified notifications;
  `!=` not `secrets.compare_digest`. Impact ceiling is a forced sync, not data injection.
- **Workspace path containment**: three `startswith` sites (`send.py:78,227`,
  `actions.py:200`); `.resolve()` already kills `../`, residual is the sibling-prefix case →
  `Path.is_relative_to`.
- **Read-state on open is local-only** (`messages.py:617-624`, no provider write-back on the
  implicit mark-read; explicit PATCH *is* two-way). Decide: push on open, or drop the stale
  "two-way" comment. Outlook star support decision (local-only today).
- Sanitize provider error text before persisting/surfacing.
- Agent config hygiene: regenerate `config.json` `own_tool_scope` from `_TOOLS`; fix
  `instructions.md` references to ungranted tools.
- BYOK `run_agent` (non-streaming) DeepSeek-primary (mirror the streaming pre-injection block).
- Opportunistic: converge the ~20 remaining hand-rolled provider instantiate+persist pairs onto
  `provider_session` when touching their files (see 2.11 scope note).

---

## 8. Operating rules (unchanged, carried forward)

- **Testing:** every item above lands with unit tests in `tests/unit/` (CI-gated); the review
  showed two bugs (silent-save Fix, embeddings SQL) that existed *because* tests mocked the
  seam under test — prefer exercising the real function/SQL. Integration tests opt-in;
  Playwright e2e for UI. Deploy gate = `pytest tests/unit/` (one red test silently blocks
  deploy — check `gh run list` after pushing).
- **CI reality:** pr-check is Python-only (no frontend build — validate `control_plane`
  locally); stacked PRs get zero CI (empty check list ≠ passing).
- **Migrations** auto-apply on deploy; runtime-mutable state lives in Postgres (deploy runs
  `git reset --hard`).
- **Docs:** this file is the plan; the review is the evidence; archive is history. When a
  phase item ships, update its row here (strike + PR#) rather than writing a new doc.

---

## 9. Documentation map (after 2026-07-22 cleanup)

| Doc | Role |
|---|---|
| **This file** | The plan + live status. Single source of truth for "what next". |
| [`archive/email_feature_review_2026-07.md`](./archive/email_feature_review_2026-07.md) | Evidence appendix (defect detail, file:line). ✅ Archived 2026-08-01 (doc-truth pass) per this row's own instruction — Phases 1-2 completed 2026-07-22. |
| [`archive/email_ai_assistant.md`](./archive/email_ai_assistant.md) | Historical feature inventory + architecture detail + provider matrix (v2.0, 2026-06-29). |
| [`archive/email_inbox_zero_parity_plan.md`](./archive/email_inbox_zero_parity_plan.md) | Historical parity roadmap; open items absorbed here. |
| [`archive/email_tool_consolidation.md`](./archive/email_tool_consolidation.md) | Historical tool plan; closed at 42 tools (§6 decision). |
| [`archive/email_app_review.md`](./archive/email_app_review.md) | M0→M9 build log. |

---

## 10. Outlook for every organization — tenancy, onboarding, triage (2026-10-01)

> **Owner directive, 2026-10-01:** "Customers should not do complex things to integrate their
> email." The setup that a customer does must feel like a consumer app. This section is the plan
> that brings Email back to production for more than one organization. It wins over §1's
> "one founder, one mailbox" premise and over §7 where the two disagree.

### 10.1 Measured state (2026-10-01)

- *(History. Sync is ON since 2026-10-01, see the header.)* **Email was OFF in production.** The box set `EMAIL_SYNC_ENABLED=false`, and the journal logs
  `sync.email_sync_disabled` at each gateway start. The cutover runbook turned it off because the
  email background paths bind no tenant (H4 slices 6b and 6c).
- *(History. The interim app is installed since 2026-10-01.)* **No Microsoft app existed on the box.** `MSFT_OAUTH_CLIENT_ID` is empty, and nothing else
  supplies a client ID. Nobody can connect an Outlook mailbox today.
- **The Outlook code is complete.** Graph sync, send, drafts, categories, the push webhook and
  the automation pipeline all exist. The gap is tenancy and onboarding, not provider code.
- **Five service paths bind no tenant.** These are the OAuth callback, the Graph webhook,
  subscription renewal, the sync scheduler, and the `_get_db()` sites that the sync pipeline
  reaches. EM-T1a bound the first three. §10.4.2 holds the measured count of the rest. Under FORCE RLS as `acb_app`, each one reads zero rows and each insert fails.
- **The OAuth state is weak.** It is an unsigned token in an in-process dict
  (`transport/oauth.py`). A restart loses it, a second worker cannot see it, and it never expires.

### 10.2 Decisions (owner, 2026-10-01)

| Id | Decision |
|---|---|
| **D-EM-1** | **Metorite owns ONE multi-tenant Microsoft app.** It accepts work accounts and personal Outlook.com accounts (authority `common`). Its credentials live on the operator side. A customer never registers an app, pastes a key, or opens Integrations. |
| **D-EM-2** | **The app lives in a dedicated Metorite Entra directory**, not in `fracktal.in`. The directory of customer zero must not hold the product. ⚠️ **Interim (owner, 2026-10-01):** until that app exists, Metorite uses the publisher-verified Fracktal Works app `3bfeff54-14fb-4cee-8b17-2c8d41cad6a8` ("CommandCenter"), set to any Entra tenant plus personal accounts. The same app also serves Microsoft sign-in. A later move to a Hathi Labs app makes each mailbox reconnect once. |
| **D-EM-3** | **The app gets Microsoft publisher verification**, so the consent screen shows a verified Metorite. Without it, many company tenants block the app. |
| **D-EM-4** | **A mailbox is private to the member who connects it.** An org admin sees how many members connected, never their mail. This follows the private-first default of D12. |
| **D-EM-5** | **Outlook is the only provider in the connect flow.** Google shows "coming soon". IMAP stays hidden until its connect path works. |
| **D-EM-6** | **Automatic reply drafting is OFF for a new mailbox. A member turns it on in AI settings.** Owner, 2026-10-02: "Turn the default autodraft emails to off." This reverses migration 82 for a new mailbox. A stored choice does not change. EM-T7 builds it (§10.4.9). |
| **D-EM-7** | **Every email triage decision goes through the `decide` task on `tier-decide`, and no member can change it** (owner, 2026-10-02). The decisions are the rule match (one rule and multi-rule), the thread status, the cold check and the sender pin. Settings and the email agent lose the rules-model choice. Text work stays on the LLM tiers: drafts, compose, the digest brief, the voice profile, template fill, rule generation and chat. |
| **D-EM-8** | **No fallback model** (owner, 2026-10-02, revised the same day). When `decide` gives no answer, the email stays undecided. No rule applies, `rules_processed_at` stays NULL, and the next cycle asks again. The log line is `decide.unavailable` with the reason. Resilience is a backup step in the Router chain of `tier-decide`, which an operator binds. For email, this replaces adoption rules 2 and 3 of `customer_console.md` §6A.14. |
| **D-EM-9** | **The owner approves residency for email triage** (owner, 2026-10-02). Tenant mail content may go to TypeSafe and to AI/ML API for the D-EM-7 decisions. This answers H-166 item 3 for email, and for no other app. `DECIDE_ENABLED` stays an owner act (`work_plan.md` §6.1 WS-31 (i)). |
| **D-EM-10** | **The import never reaches back more than 6 months.** No sync path writes a message older than 6 months. The code counts a month as 30 days, so the ceiling is 180 days. (Owner, 2026-10-02.) |
| **D-EM-11** | **The member chooses the import range at the first connect.** The choices are 0 to 6 months, and the default is 1 month. A choice of 0 imports no old mail, only the mail that arrives after the connect. (Owner, 2026-10-02.) |
| **D-EM-12** | **The import goes from the newest mail to the oldest, across all folders.** When the storage limit stops it, the newest mail is present and the gap is at the old end. (Owner, 2026-10-02.) |
| **D-EM-13** | **A sync after a pause continues from the last sync point.** A pause is sync turned off, a token that failed, or a reconnect. The next sync gets the mail since the last sync point. It does not import the range again. (Owner, 2026-10-02.) |
| **D-EM-14** | **Each mailbox has a storage limit of 500 MB.** The meter measures the copy that Metorite keeps: message rows, bodies, attachment records and embeddings. At the limit, the import stops going back, and Metorite asks the member to remove older mail from Metorite. A removal deletes the copy in Metorite only. Metorite never deletes or changes mail in the Outlook mailbox of the member. (Owner, 2026-10-02. The answers to three checks follow this table.) |
| **D-EM-15** | **After the import, Metorite asks the member to set up AI rules.** The rules then run over the imported mail and give insights. The step offers no model choice, because EM-T5b moves the rules to the `decide` tier. Reply drafting is a separate step that the member turns on (D-EM-6). (Owner, 2026-10-02.) |
| **D-EM-16** | **A short guided setup takes the member through each stage.** The stages are connect, the range, the import, the storage notice when it applies, AI rules, and done. The import shows real progress: the count of messages, an estimate of the total and the phase. A spinner alone is not progress. (Owner, 2026-10-02.) |

**The owner answers to the three checks of EM-T6c (2026-10-02).** Each answer adds to D-EM-14. §10.4.7 gives the reason for each check.

- **Q1 (owner, 2026-10-02).** The limit of 500 MB is for each mailbox, not for each member.
- **Q2 (owner, 2026-10-02).** New mail continues to sync at the limit. Only the import of older mail stops.
- **Q3 (owner, 2026-10-02).** At the limit, the body backfill and the embeddings stop. A message that the member opens still loads its body live from the provider.
- **Q4 (owner, 2026-10-02).** At the limit, an open shows the body and stores nothing. So an open never takes a mailbox past the limit.

**The owner decisions for the Jev demo (2026-10-02).** Each one narrows D-EM-7 to D-EM-9 for EM-T5b-2. §10.4.8 holds the build.

- **(a) (owner, 2026-10-02).** `DECIDE_ENABLED=true` is ON in production since 12:16 UTC. One smoke `decide` call from the box reached Jev, with a probability of 0.99 in 1.5 s.
- **(b) (owner, 2026-10-02).** Jev decides the rule match for ALL organizations, with no shadow window ("Do jev for all").
- **(c) (owner, 2026-10-02).** The demo scope is the rule match only (`email.rule_match`). The thread status, the cold check and the sender pin keep the old path, and shadow stays allowed for them.
- **(d) (owner, 2026-10-02).** The AUTOMATIC run touches new mail only: mail that arrived after the member made the first enabled rule of the mailbox. Older synced mail changes only through "Process past emails".

### 10.3 The customer flow (the acceptance target for EM-T3)

1. **Empty state.** Email shows "Connect your email" with one large Microsoft 365 / Outlook
   button. No step asks the member to configure OAuth.
2. **Sign-in.** Microsoft sign-in opens with the address of the member as the `login_hint`.
3. **Consent.** Microsoft shows the verified Metorite app. The member accepts. With the interim app,
   Microsoft shows "CommandCenter" by Fracktal Works, so this step passes only after §10.5.
4. **First sync.** Metorite shows "Connected as you@company.com" and a progress state. The inbox
   fills batch by batch, newest first (EM-T6b).
   A first sync that fails shows the reconnect banner, not the progress state.
5. **Admin approval required.** When the Microsoft tenant of the customer blocks consent by members, Microsoft returns an
   error to the callback. Metorite shows a guided page, never a raw error. The page gives two
   paths. "Ask your IT admin" sends a prefilled email or copies the admin-consent link. "I am the
   admin" opens the admin-consent endpoint, and one approval covers the whole company.
6. **Pre-approval (optional).** In Settings → Organization → Email, an org admin can approve the
   app for the company ahead of time. The admin then sees the connected-member count.
7. **Reconnect.** When a refresh token fails, a banner offers one-click "Reconnect Outlook".
8. **Disconnect.** One button in the account menu inside Email removes the tokens and the synced
   data of the mailbox.

### 10.4 Slices

Each slice ships dark. `EMAIL_SYNC_ENABLED` is `true` on the box since 2026-10-01, after the live
check of §10.4.2 passed. To change it is gate `enforcement-flip`.

| Slice | Gate | Scope | Done when |
|---|---|---|---|
| **EM-T1a** | 🟢 AGENT-SAFE | ✅ **MERGED #559, 2026-10-01.** **Signed state, a callback behind the session, and a webhook that binds a tenant.** See §10.4.1. | See §10.4.1. |
| **EM-T1b** | 🟢 AGENT-SAFE | ✅ **EM-T1b-1 MERGED #560 and EM-T1b-2 MERGED #561 (2026-10-01).** Sync ON. **The sync scheduler and the sync pipeline bind a tenant.** Two PRs: EM-T1b-1 (scheduler, sync core, hooks), then EM-T1b-2 (ten automation sites). See §10.4.2. | See §10.4.2. |
| **EM-T2** | 🟢 AGENT-SAFE | **Isolation fences.** Account uniqueness includes `organization_id` (expand and contract, R6). The attachment cache keys go through `tenant_redis`. A fence fails when an email query reads a child table without the owner scope (D-EM-4). | See §10.4.5. |
| **EM-T3a** | 🟢 AGENT-SAFE | ✅ **MERGED #563 (2026-10-02).** **The backend for the connect flow.** The app credentials come from settings, never from the account blob. The authorize leg sends `login_hint`. The callback maps the consent errors of Microsoft. The accounts API returns `initial_sync_done`. See §10.4.3. | See §10.4.3. |
| **EM-T3b** | 🟢 AGENT-SAFE · promotion by owner decision (2026-10-01, H-21) | ✅ **MERGED #564 (2026-10-02).** **The connect UI, and Email in the sidebar.** The empty state, the guided page for admin approval (mail and copy link), first-sync progress, reconnect, disconnect inside Email, and the promotion from `preview` to `live`. See §10.4.3. | See §10.4.3. |
| **EM-T3c** | 🟢 AGENT-SAFE · security review | ✅ **MERGED #566 (2026-10-02).** **The return leg of admin consent.** A public landing page for an IT admin with no Metorite session, and a BFF branch for `admin_consent` and `tenant`. It writes nothing. | A return from the admin-consent endpoint lands on a page that says "Approved". It writes no row. |
| **EM-T3d** | 🟢 AGENT-SAFE · after EM-T2c | ✅ **MERGED #571 (2026-10-02).** **Pre-approval in Settings, and the connected-member count.** An Email tab in Organisation, with a pre-approve link and seven counts from an admin-only route. See §10.4.3. | See §10.4.3. |
| **EM-T4** | 🟢 AGENT-SAFE · 🔴 two flips (`enforcement-flip`) | ✅ **EM-T4a-1 MERGED #570 and EM-T4a-0 MERGED #572 (2026-10-02).** ✅ **EM-T4c MERGED #575 (2026-10-02).** **§7 Tier 1 items 2 to 5, and Graph delta.** Nine parts, each one PR: EM-T4a-0 (request jobs bind a tenant, first), EM-T4a-1 to EM-T4a-4 (sessions across I/O), EM-T4b (cap and budget), EM-T4c (401 retry), EM-T4d (delta in shadow) and EM-T4e (§7 item 4). See §10.4.6. | See §10.4.6. |
| **EM-T5** | 🟢 build · 🔴 real mail | ✅ **MERGED #569, dark (2026-10-02).** **Triage on Jev.** This is CP-13e (`customer_console.md` §6A.14, and §2.1 here). It is built to shadow mode. Real mail waits for the H-166 owner acts. | See §10.4.4. |
| **EM-T5b** | AGENT-SAFE build · OWNER "go" for `on` on a box and for the merge of EM-T5b-3 | ✅ **EM-T5b-1 and EM-T5b-2 (narrowed to the rule match) MERGED #576 (2026-10-02).** The owner gave the "go" for `email.rule_match=on` for all organizations (§10.2, decisions (a) to (d)). **The rules engine and every triage decision on Jev, with no LLM path** (D-EM-7 to D-EM-9). Four parts: EM-T5b-1 (the questions rebuilt, multi-rule in shadow), EM-T5b-2 (`on`, undecided on failure, no rules-model choice), EM-T5b-3 (hardcode, and delete the old path) and EM-T5b-4 (the "not sorted yet" notice). See §10.4.8. | See §10.4.8. |
| **EM-T6** | 🟢 AGENT-SAFE | **SPECIFIED (2026-10-02). EM-T6a MERGED #577.** **Guided mailbox onboarding.** A range of 0 to 6 months at the first connect, an import newest first in batches with real progress, and a resume after a pause. A limit of 500 MB for each mailbox, with removal from Metorite only. A guided setup that ends at AI rules. Five parts, each one PR: EM-T6a to EM-T6e. See §10.4.7. | See §10.4.7. |
| **EM-T7** | 🟢 AGENT-SAFE | ✅ **MERGED #574 (2026-10-02).** **Automatic reply drafting is OFF for a new mailbox (D-EM-6).** Migration 224 sets the column default to false. The model, the GET and the presets agree with it. See §10.4.9. | See §10.4.9. |
| **§10.5** | 🔴 OWNER-GATE | Register the Microsoft app, verify the publisher, and install the credentials (`env-write`). | The client ID is on the box, and one test mailbox connects. |

#### 10.4.1 EM-T1a in full

**Status.** ✅ Merged as #559 on 2026-10-01, and production serves it (`03582902`). The new fences are `test_email_oauth_state.py`, `test_email_tenant_bind_rls.py` (R8)
and `callback/route.test.ts`. The signer is `transport/signing.py`.

**Scope.**

1. **Signed state.** The authorize leg signs a state with HMAC-SHA256. The state holds a version,
   a nonce, the organization, the member, the provider, `redirect_after` and an expiry of 10
   minutes. The MAC input starts with the purpose `email-oauth-state:v1`, so no other token that
   the same secret signs can pass as a state. The secret is `gateway_session_secret`, and the
   signer refuses a value in `acb_auth.member_proof.PUBLIC_DEFAULT_SECRETS`. The in-process
   `_oauth_states` dict goes.
2. **The authorize leg takes the identity from the session only.** It reads the organization and
   the member from `get_current_user`. When either is missing, it refuses with 403. The
   `user_email` query fallback and the `anonymous` path go.
3. **The callback runs behind the session (R-4).** The redirect URI moves to the BFF:
   `https://app.metorite.com/api/email/oauth/{provider}/callback`. The BFF attaches the session,
   calls the gateway callback, and passes on its 302. The gateway callback is no longer exempt
   from the feature gate. It verifies the MAC, the expiry and the provider. It then checks that
   the member of the session is the member in the state. Last, `resolve_identity` must return the
   organization in the state. Every failure redirects with `error=invalid_state`.
   Anchors: the new route `workbench/control_plane/src/app/api/email/oauth/[provider]/callback/route.ts`
   copies the authorize route beside it. Remove the callback template from the exempt list in
   `routes/email/core.py`, from `PUBLIC_ROUTES` in `gateway/main.py`, and from
   `tests/unit/test_org_access_enforcement.py`. Invert `test_the_callback_leg_stays_public` in
   `tests/unit/test_email_oauth_authorize_wiring.py`. `_build_redirect_uri` returns the BFF path,
   and the authorize leg and the token exchange must use the same value. Do not add
   `response_mode=form_post`, because a cross-site POST drops the Lax session cookie.
4. **The callback writes inside one `tenant_session(org)`.** The explicit `commit()` calls go,
   because the seam commits on exit. `refresh_account_sync` runs after the block.
5. **The webhook binds a tenant from a signed value (R11).** `_ensure_subscription` builds
   `notificationUrl` as `…/email/webhook/microsoft?org=<uuid>&sig=<hmac>`, with the purpose
   `email-graph-webhook:v1`. The webhook echoes `validationToken` first. It then parses `org` as a
   UUID and verifies `sig`. On a failure it returns 202 and queues nothing. It matches the
   subscription and the `clientState` inside one `tenant_session(org)`.
6. **The webhook refuses a null `clientState`.** It compares with `hmac.compare_digest`.
7. **`_ensure_subscription` binds a tenant or does nothing.** It reads `current_tenant()`. When no
   tenant is bound, it logs `email.subscription_unbound` and makes no DB call and no Graph call.
   A renewal also updates `notificationUrl`. When Graph refuses that update, the code deletes the
   subscription and creates a new one.

**Non-goals.** No Redis nonce store. No change to `_sync_account` or the scheduler, which is
EM-T1b. No new environment variable. The redirect URI of Gmail moves to the BFF with the URI of
Microsoft, because the two share one template. Gmail stays hidden (D-EM-5), so no Google
registration changes now. No change to `proxy.ts`: a member whose session ends inside the
10-minute window sees a JSON 401, and EM-T3 owns that polish.

**Done when.**

- A tampered, expired, wrong-provider or wrong-purpose state redirects with `invalid_state`.
- The default or an empty secret refuses to sign.
- An authorize call with no organization returns 403.
- A callback whose session member differs from the state member redirects with `invalid_state`.
- A fence proves that the BFF callback uses `gatewayHeaders`, `redirect: "manual"` and the
  `location` header, forwards only `code`, `state`, `error` and `error_description`, and refuses a
  `Location` of another origin.
- The callback template is absent from all three exempt lists.
- A provider `error` with no `code` lands on `/email/oauth/callback?error=...`, never on a raw 422.
- A callback for a member of org B writes a row of org B, and org A cannot read that row.
- A webhook with a missing, malformed or unsigned `org` queues nothing and returns 202.
- A webhook that names org A with a subscription of org B matches nothing.
- A notification for an account whose stored `clientState` is null queues no sync.
- `_ensure_subscription` with no bound tenant makes no Graph call and no DB write. With a bound
  tenant, the `notificationUrl` carries that organization and a valid signature.
- R8: the callback and webhook SQL run against a real database as a non-owner role, for two
  organizations.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_oauth_authorize_wiring.py tests/unit/test_email_webhook.py \
  tests/unit/test_email_imports.py tests/unit/test_db_engine_seam.py \
  tests/unit/test_org_access_enforcement.py tests/unit/test_member_proof.py <new EM-T1a tests> -q
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

The R8 tests must show PASSED, not SKIPPED. A skip means that the database is not up.

**Recorded risks.**

- **R-1.** The state and the webhook signature use `gateway_session_secret` with a purpose
  prefix. A new secret would cost an `env-write` and buy nothing.
- **R-3.** A Graph subscription made before EM-T1a has no `org` in its URL. The renewal path
  writes the signed URL, but that path does not run yet. The only caller of
  `_ensure_subscription` is the loop of the scheduler (`_account_sync_loop`). A manual sync or a
  resync does not call it. Until EM-T1b binds the scheduler, the loop binds no tenant, so
  `_ensure_subscription` returns at once. No push subscription exists for a new account or an
  old one, and mail arrives only through the poll of the scheduler. Email sync is OFF in production in any case.
  EM-T1b closes this risk.
- **R-4 (closed here).** A signed state alone can be replayed for 10 minutes and is not tied to a
  browser. An attacker could start the flow and get a victim to consent, and the mailbox of the
  victim would then attach to the attacker. The callback behind the session closes this, because
  the member of the session must match the state.

#### 10.4.2 EM-T1b in full

**Status.** ✅ EM-T1b-1 is MERGED as #560 and serving (`bad592be`, 2026-10-01). The new fence is
`tests/unit/test_email_scheduler_tenancy.py`, with R8 cases for the sweep, the sync core and
`mailbox_owner`. ✅ **EM-T1b-2 is MERGED as #561** (`b467b6de`, 2026-10-01). The ten functions open `_tenant_session()` in phases and call no `commit()`. The new fence is `tests/unit/test_email_automation_tenancy.py`. It holds the AST fence over the ten, the case with no tenant, the BackgroundTask case and the five R8 families. `H2_BASELINE_ELSEWHERE` is now 92.

**Fix round 1 (2026-10-01).** The cleanup sweep calls the provider half of `apply_label` (`push_label`) with no session open. Each item then writes its mirror and its audit row in its own block. A best-effort write that swallows its own failure now runs inside `_savepoint` (`routes/email/core.py`), so the failure rolls back only the savepoint and the block goes on. This covers the Reply Zero projection of the runner, the label mirror and the body hydrate of the follow-ups, `hydrate_message_body` and `_revert_unreconciled`. A failed filed write of the Reply Zero backfill no longer skips its sent threads. The fences are in `test_email_automation_tenancy.py`.

**Owner.** This slice IS WS-29 H4 slice 6b for the email scheduler and pipeline
(`saas_multitenancy_handover.md`). This spec owns it from 2026-10-01.

**Measured at dispatch (2026-10-01, `03582902`).** 25 `_get_db()` sites remain in the email code.
13 are on the sync pipeline, and that count includes `mailbox_owner`. 12 are jobs that a request
starts. A request job keeps the organization of the request, because Starlette runs
BackgroundTasks inside the tenant scope and `asyncio.create_task` copies the context.

**The mechanism for a commit part way (decided).** A `commit()` inside one `tenant_session`
ends `SET LOCAL`, and each statement after it runs with no tenant. EM-T1b uses **(A) a split
into phases**: each phase opens its own `tenant_session(org)` and calls no `commit()`. The seam
of `acb_common.db` does not change in EM-T1b. The EM-T1b-2 audit may propose (B), a listener on
the seam that applies `set_config` again on each new transaction. Only an edit to this section
can choose (B). The EM-T1b-2 audit (2026-10-01) confirmed (A) for all 20 commits and did not
propose (B).

##### EM-T1b-1 — the scheduler, the sync core and the scheduler hooks

1. **The private engines go.** `scheduler.py` loses its three `create_async_engine` sites and their
   helpers. Every session comes from `acb_common.db`. The scheduler entry leaves `_ALLOWED` in
   `test_db_engine_seam.py`.
2. **The startup sweep binds per organization.** The `EMAIL_SYNC_ENABLED` gate stays first. One
   unbound read lists `organization` (RLS-exempt), as `routes/tasks/calendar.py` `_run_rollover_sweep`
   does. For each organization, one `tenant_session(org)` runs `_close_orphaned_syncs` and lists the
   accounts with `sync_enabled`. Each task starts with its organization.
3. **`_account_sync_loop` binds its organization.** It calls `bind_tenant(org)` at the top and
   `release_tenant` in `finally`. This replaces any context that a request passed on. All hooks then
   run bound, and `_ensure_subscription` creates the Graph subscription again (R-3 closes).
4. **`_get_account_sync_interval` reads in `tenant_session(org)`.**
5. **`refresh_account_sync(account_id, organization_id)` takes the organization.** The OAuth
   callback passes the organization of the verified state. The two callers in
   `transport/accounts.py` pass the organization of the `UserContext`. A missing organization raises
   `TenantUnbound`.
6. **`_sync_account` binds or refuses, and splits into phases.** It uses `organization_id` or
   `current_tenant()`. When it has neither, it raises `TenantUnbound` and writes nothing. The phases are:
   - (a) Read the account, set `syncing`, and write `email_sync_log`.
   - (b) Call the provider with NO session open.
   - (c) Write the rotated credentials, the messages, the labels and the reconcile.
   - (d) Write the final account and log rows.
   - (e) Run `backfill_missing_bodies`.
   - (f) Run `embed_pending_messages`.

   The error path writes in a new `tenant_session(org)`.
7. **The scheduler hooks bind.** `auto_run_rules_for_account` and `learn_label_changes` use
   `tenant_session()`. `learn_from_label_change_events` gets its own block or loses its commit.
   `mailbox_owner` reads inside `tenant_session()` when a tenant is bound. It keeps its one unbound
   discovery read for the case with no tenant.
8. **The fences move with the code.** Lower `H2_BASELINE_ELSEWHERE` by the measured drop. Point the
   `email_probe` of `test_launch_defang_kill_switches.py` at the organization read. Update the
   `_no_sync` stub of `test_email_tenant_bind_rls.py` to the new signature. Correct the
   "separate process" comment in `_ALLOWED`, because the gateway lifespan starts the scheduler.

##### EM-T1b-2 — the automation sites that the pipeline reaches

The ten functions, all under `apps/services/gateway/gateway/routes/email/`:

| Function | File |
|---|---|
| `_run_rules_job` | `automation/runner.py` |
| `sweep_uncategorized` | `automation/cleanup.py` |
| `_categorize_senders_job`, `_maybe_auto_archive`, `_bulk_reconcile_provider` | `automation/senders.py` |
| `_maybe_classify_threads`, `_mark_thread_replied`, `apply_thread_status_correction` | `automation/replyzero.py` |
| `_maybe_send_digest` | `digest.py` |
| `_maybe_send_follow_up_reminders` | `automation/followups.py` |

Each one changes to `_tenant_session()` with the ambient tenant, and keeps no `_get_db()` and no
`commit()`. They hold 20 commits today. Dispatch EM-T1b-2 after EM-T1b-1 merges.

Two rules apply to each function:

- The broad `except Exception` wraps the `async with` block, never the reverse. Otherwise a
  swallowed DB error makes the seam commit an aborted transaction.
- No block covers more external I/O than the code covers today. EM-T4 owns the I/O that stays.

**Non-goals (both parts).** `inbound.py` (the gateway does not start it). The 12 request jobs. The
work of EM-T4 on sessions across I/O, except the phase split of item 6. The organization lookup of
CRM auto-lead. The `TODO(WS-29 slice 6b)` of the orchestrator in `executor.py`.

**Done when (EM-T1b-1).**

- R8, as the non-owner role, two organizations. `start_background_sync` starts exactly the
  `sync_enabled` accounts of both organizations, each with its own organization.
- R8: `_sync_account` with a fake provider writes `email_messages` and `email_sync_log` rows with
  the right `organization_id`. The other organization reads none of them.
- R8: `_close_orphaned_syncs` resets the rows of both organizations.
- R8: `mailbox_owner` with a bound tenant returns the owner under FORCE RLS.
- `_sync_account` with no organization and no bound tenant raises `TenantUnbound` and writes nothing.
- `refresh_account_sync` with no organization raises. The OAuth callback passes the organization.
- An AST fence finds no `.commit()` inside a `tenant_session` block in `scheduler.py`.
- `_ALLOWED` has no entry for `scheduler.py`, and `H2_BASELINE_ELSEWHERE` is lower.
- With the flag OFF, `start_background_sync` returns `{}` and opens no session.

**Done when (EM-T1b-2).**

- An AST fence finds no `await _get_db()` and no `.commit()` in any of the ten functions. A
  companion test proves that the fence can fail.
- With no tenant bound, each function opens no session and writes nothing.
- A hermetic test proves that a FastAPI BackgroundTask sees `current_tenant()` equal to the
  organization of the request, under `TenantScopeMiddleware`.
- R8, as the non-owner role, for organizations A and B. In each family, the second write is the
  one that came after a former commit part way. Organization A reads none of the rows of B.
  - runner: two inbox rows get `rules_processed_at`. Their `email_executed_rules` rows carry B.
  - cleanup: two decided messages give two `email_executed_rules` rows in B.
  - senders: categorize writes `email_senders` rows in B. A failed auto-archive reverts in B.
  - replyzero: one filed and one gap thread give two `email_thread_status` rows in B. A status
    correction writes in B.
  - digest and followups: `last_digest_at` and `follow_up_reminded_at` land in B.
- `H2_BASELINE_ELSEWHERE` drops by the measured count, which the audit expects to be 10.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_db_engine_seam.py tests/unit/test_launch_defang_kill_switches.py \
  tests/unit/test_email_tenant_bind_rls.py tests/unit/test_email_oauth_state.py \
  tests/unit/test_email_webhook.py tests/unit/test_email_manual_sync_parity.py \
  tests/unit/test_email_sync_backoff.py tests/unit/test_email_retry_and_uncategorized.py \
  tests/unit/test_email_cleanup_backfill.py tests/unit/test_email_tool_consolidation.py \
  tests/unit/test_background_ai_member.py tests/unit/test_email_digest.py \
  tests/unit/test_email_imports.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_bulk_apply.py tests/unit/test_email_categorization.py \
  tests/unit/test_email_cleanup_apply_cap.py tests/unit/test_email_cleanup_sweep.py \
  tests/unit/test_email_fix_feedback.py tests/unit/test_email_follow_up_scan.py \
  tests/unit/test_email_pattern_approval.py tests/unit/test_email_process_past_drafting.py \
  tests/unit/test_email_reclassify_resumable.py tests/unit/test_email_reply_zero.py \
  tests/unit/test_email_rulepath_draft_parity.py tests/unit/test_email_thread_status_parity.py \
  tests/unit/test_crm_auto_lead.py tests/unit/test_email_automation_tenancy.py -q -rs
uv run ruff check apps/services/email_ingestion apps/services/gateway/gateway/routes/email tests/unit
```

The R8 tests must show PASSED, not SKIPPED.

**Live check result (2026-10-01).** ✅ Passed. `/version` served `b467b6de`. The gateway role
`acb_app` has no BYPASSRLS. All five organizations held 0 email accounts, so step 4 did not stop
the flip. After the restart, the journal showed `sync.scheduler_started accounts=0` and no tenant
error.

**Live check, then the flip (after EM-T1b-2 merges).**

1. Confirm that `/version` serves a SHA that contains EM-T1b-2.
2. Confirm that the gateway role is not the table owner and has no BYPASSRLS.
3. With the flag still `false`, count the accounts per organization and read `sync_enabled`, and
   the digest and auto-run settings of each account.
4. ⚠️ **Stop and ask the owner when step 3 finds an account with `sync_enabled`.** The old accounts
   may still hold the credentials of the earlier app. A flip would then read the real mail of a
   customer, spend AI credits, and let `_maybe_send_digest` send mail (§3a rule 3).
5. Write `EMAIL_SYNC_ENABLED=true` and restart the gateway. Report the act, the box and the evidence
   in the same message.
6. Within 300 seconds, read the journal. Expect `sync.scheduler_started accounts=N`, with N equal to
   the count of step 3. Expect no `TenantUnbound`, no `organization_id` null error and no
   `email.subscription_unbound`. Also grep for `no tenant bound`, because the automation functions log a caught
   `TenantUnbound` with that text. Expect new `email_sync_log` rows with `status='success'`.
7. Rollback: set the flag to `false` and restart.

**Risks.** The shared pool holds 8 sessions plus 4 overflow per process. A deep backfill that
holds one session across Graph I/O can starve the requests. The phase split of item 6 makes this
risk smaller, but it does not remove it. Phases (b) and (c) hold no session across the sync fetch.
The ten automation jobs of EM-T1b-2 also keep some I/O inside a block: the model call of each runner and backfill row, `bulk_apply`, the digest send and the follow-up draft. EM-T4 owns these too. Two sessions of the sync core still stay open across external I/O, and EM-T4 owns both:

- Phase (e) holds its session across the Graph calls of `backfill_missing_bodies`.
- Phase (f) holds its session across `litellm.aembedding` in `embed_pending_messages`.

The two halves are not safe apart: after EM-T1b-1 alone, the automation hooks fail closed.
Do not flip between the two PRs.

#### 10.4.3 EM-T3 in full

**Order.** EM-T3a, then EM-T3b, then EM-T3c. EM-T3d waits for EM-T2. Each part is one PR.

**What an agent cannot test.** Step 3 of §10.3 passes only after §10.5. With the interim app,
Microsoft shows "CommandCenter" by Fracktal Works. Steps 5 and 6 need a real tenant that blocks
consent by members, so the owner tests them by hand. The agent acceptance below replaces them.

##### EM-T3a — the backend for the connect flow

1. **The app credentials come from settings.** The OAuth callback stops copying `client_id`,
   `client_secret` and `tenant_id` into the account blob. Delete `_provider_oauth_app_creds` in
   `transport/oauth.py`. The providers in `email_ingestion/providers/` (`outlook.py`, `gmail.py`,
   through `factory.py` `build_provider`) read the app credentials from `get_settings()` and ignore
   any value in the blob. `export_credentials` writes token fields only. When settings hold no
   credential, the provider does not fall back to the blob, because that is how a revoked secret
   stays alive. No migration: a stale blob field does no harm, and the next token write removes it.
2. **One authority for mail.** The Microsoft authority is `common`. The email code no longer reads
   `AUTH_MICROSOFT_ENTRA_ID_TENANT` or `AUTH_MICROSOFT_TENANT_ID`, because a sign-in tenant must
   not make mail single-tenant.
3. **`login_hint`.** The authorize leg sends `login_hint`, by default the email of the session. An
   optional `login_hint` query is accepted only when it parses as an address. It is a hint, never
   an identity. No `prompt=select_account`.
4. **Consent errors.** The gateway callback accepts `error_description` and takes only a code that
   matches `AADSTS\d{5,6}` from it. It never echoes the text. AADSTS90094, 90095 and 65001 map to
   `admin_consent_required`. AADSTS65004, and `access_denied` with no known code, map to
   `consent_declined`. Every other error goes through `_provider_error_reason`.
5. **First-sync flag.** `EmailAccountModel` in `transport/accounts.py` gains `initial_sync_done`,
   and the three account reads return it.

**Non-goals.** No UI. No change to `proxy.ts`. No admin-consent endpoint. No nav change.

**Done when.**

- A new account blob holds no `client_id`, `client_secret` or `tenant_id`.
- A refresh with a blob that holds an old `client_secret` sends the secret from settings.
- `export_credentials` after a refresh holds no app credential.
- With the sign-in tenant set to a GUID, the authorize URL still uses `/common/`.
- The authorize URL carries `login_hint` with the email of the session. A malformed hint is dropped.
- `access_denied` with AADSTS90094 lands on `/email/oauth/callback?error=admin_consent_required`.
  With AADSTS65004, and with no code, it lands on `error=consent_declined`. No description text
  appears in the Location.
- `GET /email/accounts` returns `initial_sync_done`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_oauth_authorize_wiring.py tests/unit/test_email_oauth_state.py \
  tests/unit/test_email_tenant_bind_rls.py tests/unit/test_email_webhook.py \
  tests/unit/test_email_imports.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_manual_sync_parity.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_email_labels_upstream.py tests/unit/test_email_reply_threading.py \
  tests/unit/test_outlook_drafts.py tests/unit/test_outlook_folders_move.py \
  tests/unit/test_email_attachment_inline.py tests/unit/test_h201_email_artifact_sources.py \
  <new EM-T3a tests> -q -rs
uv run ruff check apps/services/email_ingestion apps/services/gateway/gateway/routes/email tests/unit
```

**Risk R1 (H-207).** Each mailbox that connects before EM-T3a merges holds the secret in its blob.
The owner must not delete the old secret (H-207 step 5) until EM-T3a serves.

##### EM-T3b — the connect UI, and Email in the sidebar

Files: `workbench/control_plane/src/app/email/page.tsx` (the status fetch, the onboarding modal,
`handleConnect`, the reconnect banner, the add-account modal), `app/email/oauth/callback/page.tsx`,
`app/email/components/AccountSidebar.tsx`, `app/email/lib/emailStore.ts`, `src/lib/nav.ts`,
`src/lib/nav.test.ts` and `project-docs/specs/launch_surface.md` §2.

**Done when.**

- No path in `app/email` links to `/integrations`.
- `page.tsx` holds no "Configure OAuth" text and no fetch of `/api/integrations/status`.
- `handleConnect` has no IMAP branch. Gmail shows disabled, with "Coming soon".
- A vitest proves that `admin_consent_required` and `consent_declined` each show guided copy, not
  "unexpected error". The admin page offers a prefilled email and a copy of the admin-consent link.
- An account with `initial_sync_done=false` shows "Connected as" with the address and a progress state.
- The reconnect banner sends the mailbox address as `login_hint`.
- Disconnect calls `DELETE /email/accounts/{id}` from the account menu inside Email (§10.3 step 8).
- `nav.ts` marks Email `live`. `nav.test.ts` adds `["personal", "/email"]` to its live set. Its
  preview-permission example moves from `/email` to `/whatsapp`.
- `launch_surface.md` §2 lists Email as live, and HANDOFF H-21 gets a dated line.
- The callback page uses status tokens, not raw `emerald-*` classes.
- Visual review (the `visual-review` skill) in light mode, at compact density, with a changed
  accent, at mobile width, and beside Calendar. The PR carries the screenshots.

**Verify with.** `cd workbench/control_plane && npx tsc --noEmit && npx vitest run`.

**Known limit.** When a tenant turns on the admin consent workflow (AADSTS90095), Microsoft can
keep the member on its own "Approval required" form. Then no error returns to Metorite.

**As built (2026-10-02).** The decisions live in `app/email/lib/connect.ts`, and
`connect.test.ts` holds each item above. The client ID of the admin-consent link comes from a
new gateway route, `GET /email/oauth/{provider}/app`. It returns the client ID and the redirect
URI of the Microsoft app, and never the secret (fence `tests/unit/test_email_oauth_app_info.py`).
The BFF authorize route forwards `login_hint`.

**Fix round 1 (2026-10-02).** A first sync with `sync_status = 'error'` is not pending, so the
poll stops and the reconnect banner owns that state. A hidden tab makes no poll request. The
page draws "Connect your email" only after the first account read settles. The mobile bottom
bar hides its email tabs while the empty state shows. "Try again" for Gmail goes back to the
connect choices (`/email?connect=1`). The email router's real `exempt=[...]` list must equal
`GATED_ROUTERS` (fence in `tests/unit/test_org_access_enforcement.py`).

##### EM-T3c — the return leg of admin consent

**Problem.** The admin-consent link of EM-T3b sends no `state`. Microsoft returns the admin to the
redirect URI with `admin_consent=True` and `tenant`, and with no `code`. On a refusal, Microsoft
returns `error` and `error_description` instead.

Before EM-T3c, the BFF callback called `requireIdentity()` first. An admin with no Metorite
session got a JSON 401. A signed-in admin got `invalid_state`.

**Scope.**

1. **An admin branch in the BFF callback.** The branch runs before `requireIdentity()`.
   It never calls the gateway.
   The branch runs when `code` is absent and one of these is true:
   - `admin_consent` is present.
   - `error` is present and `state` is absent.

   Every other request takes the member path of EM-T1a, with no change.
2. **The result.** The branch makes one token from a fixed set:
   - `approved`, when `admin_consent` equals `true` in any case and `error` is absent.
   - `declined`, when `error` is `access_denied`, or the description holds AADSTS65004.
   - `failed`, for every other case.
   The branch sends 303 with a RELATIVE `Location`.
   For `approved`, the Location is `/oauth/approved`.
   For the other two, it is `/oauth/approved?result=declined` or `/oauth/approved?result=failed`.
3. **A public page at `/oauth/approved`.** The page holds no session and makes no fetch.
   It reads `result` only, and it treats any value outside the set as `failed`.
   For `approved`, it says "Approved". It also says that members of the organization can now
   connect their mailbox. For the other two, it gives fixed guided copy.
4. **The door.** Add `/oauth/approved` to `PUBLIC_PAGES` in `proxy.ts`. Add
   `/oauth/approved` to `CHROMELESS_ROUTES` in `nav.ts`. Let the exact path
   `/api/email/oauth/microsoft/callback` pass `proxy.ts` without a session. The route stays the
   boundary for the member path: `requireIdentity()` still refuses a signed-out member.
5. **"I am the admin".** The guided page for `admin_consent_required` gets a third action.
   It opens `adminConsentUrl(app)` in the same tab.
   Remove the "did not finish" sentence from `adminConsentMailto()`.
   Remove the warning paragraph that starts "Until EM-T3c merges" from this section.

**Non-goals.**

- No gateway change, no migration, no table and no write of any kind.
- No change to the redirect URI. A new URI is an Entra owner act (§10.5).
- No `state` marker on the admin-consent link. Links already sent hold no state, and the branch
  must accept them. The branch writes nothing, so a marker would protect nothing.
- No record of the tenant GUID. EM-T3d owns pre-approval and the member count.
- No change to the member path of EM-T1a, and no kinder page for a signed-out member.

**Done when.**

- A signed-out GET with `admin_consent=True&tenant=<guid>` returns 303 to `/oauth/approved`.
- The same request with a session returns the same 303.
- With `error=access_denied` and AADSTS65004 and no state, the Location is
  `/oauth/approved?result=declined`.
- With `error=server_error` and no state, the Location is `/oauth/approved?result=failed`.
- In each admin case, the test proves that `gatewayFetch`, `gatewayHeaders` and
  `requireIdentity` are not called.
- No Location holds the tenant, the description or any other request value. The test
  sends a hostile `tenant` and `error_description`, and the Location equals the expected constant.
- A request with `code` and `state` and no session still answers 401 and never reaches the gateway.
- A request with `error` and a `state` still goes to the gateway, as in EM-T1a.
- `proxy()` passes a signed-out GET to `/oauth/approved` and to
  `/api/email/oauth/microsoft/callback`. It still answers 401 for `/api/email/oauth/microsoft/authorize`
  and for `/api/email/accounts`.
- `isChromeless("/oauth/approved")` is true. `featureForPath("/oauth/approved")` is null.
- The page shows "Approved" for no `result`. It shows the `failed` copy for `result=<script>`.
- The guided page has an "I am the admin" link whose `href` equals `adminConsentUrl(app)`.
- The `adminConsentMailto()` body does not contain "did not finish".
- Visual review (the `visual-review` skill) of `/oauth/approved` when signed out, in light mode and at
  mobile width. The PR carries the screenshots.

**Files.** `workbench/control_plane/src/app/api/email/oauth/[provider]/callback/route.ts` and
`route.test.ts`, `src/proxy.ts`, a new `src/proxy.test.ts`, a new `src/app/oauth/approved/page.tsx`
with its test, `src/lib/nav.ts`, `src/lib/nav.test.ts`, `src/app/email/lib/connect.ts`,
`connect.test.ts` and `src/app/email/oauth/callback/page.tsx`.

**Verify with.**

```bash
cd workbench/control_plane
npx tsc --noEmit
npx vitest run src/app/api/email/oauth src/app/email src/app/oauth src/proxy.test.ts \
  src/lib/nav.test.ts src/lib/authFailsClosed.test.ts src/lib/access.test.ts
npx vitest run
node ../../.claude/hooks/ste-lint.mjs ../../project-docs/specs/email_app_master_plan.md
```

**Risks.**

- **Open redirect.** The branch builds the Location from constants only. No request value
  reaches it.
- **Reflected content.** The page renders fixed copy. It reads one token from a fixed set and
  never renders the tenant or the description.
- **Enumeration.** The branch makes no lookup and no gateway call. Every tenant gets the same
  answer, so the page tells nothing about who uses Metorite.
- **A forged "Approved".** Anyone can open `/oauth/approved` by hand. It changes nothing, because
  Microsoft holds the approval. The copy must not claim more than "Microsoft reported an approval".
- **The proxy exemption.** It is one exact path. The route keeps `requireIdentity()` for every
  request that is not an admin return, so the member path keeps its boundary.
- **Known limit.** The admin consent workflow (AADSTS90095) can keep the admin on a Microsoft
  form. Then nothing returns to Metorite.

**As built (2026-10-02).** The branch is `adminReturn()` in the BFF callback `route.ts`. It runs
for the `microsoft` provider only, because only that provider has an admin-consent link. A
Gmail request keeps the member path. A `code` or `state` with an empty value counts as present,
so that request also keeps the member path.

The page is `src/app/oauth/approved/`. Its copy lives in `view.ts`. `approved.test.ts` renders
the real page. The proxy fence is `src/proxy.test.ts`.

##### EM-T3d — pre-approval and the connected-member count

**Status.** ✅ MERGED #571 (2026-10-02). Audited against `ea9467a9` on 2026-10-02. EM-T3d waits for EM-T2c only,
because the fence of EM-T2c holds `OWNER_SCOPE_EXEMPT`. It does not need EM-T2a. Production has
`email_accounts.organization_id`, and the scheduler already filters on it (EM-T1b-1).

**Problem.** §10.3 step 6 has no surface. Today an admin can approve the app only after a member
gets the consent error. No route tells an admin how many members connected a mailbox. D-EM-4 lets
an admin see that count, and never the mail.

**Scope.**

1. **The count route.** Add `GET /email/admin/connections` to `transport/accounts.py`. Name the
   handler `org_connection_counts`. Do not add a new module.
2. **The gate.** The route depends on `require_permission("admin:members:read")` from `acb_auth`.
   That is the same test that `/auth/me` reports as `is_admin`. The email router adds `feature:email`.
3. **The tenant.** The handler takes `user` and no other parameter. It answers 403 when
   `user.organization_id` is empty, before it opens a session.
4. **The read.** The handler reads through `_tenant_session()`. Its SQL also filters on
   `organization_id = CAST(:org AS uuid)`, from `user.organization_id`. One SELECT computes every
   count with `count(*) FILTER (...)`.
5. **The answer.** The response model `OrgConnectionCounts` holds seven integers: `members`,
   `mailboxes`, `microsoft`, `gmail`, `imap`, `sync_errors` and `first_sync_pending`. `members` is
   `count(DISTINCT lower(user_id))`. `sync_errors` counts the rows in sync status `error`.
   `first_sync_pending` counts `NOT initial_sync_done`.
6. **The fence entry.** Add `org_connection_counts` to `OWNER_SCOPE_EXEMPT` in
   `tests/unit/test_email_owner_scope_fence.py`. The reason says that the route is for admins only.
   It also says that the query returns counts, and no address, no member and no account id.
7. **The Email tab.** Add a fifth tab, "Email", to `OrganizationAdmin.tsx`. Put the tab in a new
   `EmailTab.tsx`, as a container that fetches and a pure `EmailTabView` that draws. Put the mapper
   and the copy in a new `lib/emailConnections.ts`.
8. **Pre-approval.** The tab calls `getMailAppInfo()` from `app/email/lib/api.ts`. When that returns
   an app, the tab shows a link to `adminConsentUrl(app)`. The link opens a new browser tab, with
   `rel="noopener noreferrer"`. Microsoft then sends the admin to `/oauth/approved` (EM-T3c).
9. **The count on the tab.** The tab reads `/api/email/admin/connections` through the BFF catch-all
   `api/email/[...path]`. It shows the seven counts and nothing else.
10. **The docs.** In `launch_surface.md` §6.2, add a fifth tab, "Email", that points to this
    section. Add Email to the tabs of the Organisation row in its §2 table. In the header comment
    of `OrganizationAdmin.tsx`, change "Four tabs" to "Five tabs".

**Non-goals.**

- No record of an approval. Microsoft holds the approval. The `tenant` value on the return comes
  with no state, so a record of it would trust a value that anyone can forge.
- No "approved" badge. Metorite cannot know if the organization approved the app.
- No migration, no table and no write of any kind.
- No list of members, no address, no account id and no `sync_error` text.
- No join to `app_user`. A purge deletes the mailbox of a removed member. Until then, it counts.
- No pre-approval for Gmail or IMAP. Only Microsoft has an admin-consent step (D-EM-5).
- No new BFF route, and no copy of `adminConsentUrl` or `getMailAppInfo`.

**Done when.**

- R8, as admin of org A: the route returns the counts of org A only, while org B holds rows too.
  The same test as admin of org B returns the counts of org B only.
- R8: the response JSON holds no `@` and no seeded address, for each organization.
- R8: a member with two mailboxes counts once in `members` and twice in `mailboxes`.
- R8: `sync_errors`, `first_sync_pending` and each provider count equal the seeded rows.
- A member with `feature:email` and no `admin:members:read` gets 403. The handler body does not run.
- An admin with no organization gets 403, and the handler opens no session.
- The handler signature holds `user` and no other parameter (R5).
- Each field of `OrgConnectionCounts` is an `int`. The test fails when a field of another type appears.
- `OWNER_SCOPE_EXEMPT` holds `org_connection_counts` with its reason, and the fence passes.
- `EmailTabView` draws a link whose `href` equals `adminConsentUrl(app)`, with `target="_blank"`
  and `rel="noopener noreferrer"`.
- With no app, `EmailTabView` draws no link. It draws a fixed sentence instead.
- `mapConnectionCounts` keeps the seven integer fields only. It drops a string field, for example
  `email_address`.
- A failed count read draws an error sentence. It never draws "0 members".
- The markup of `EmailTabView` holds no `@` for a fixture with counts.
- `launch_surface.md` §6.2 lists five tabs.
- Do a visual review of the Email tab with the `visual-review` skill. Use light mode, compact density,
  a changed accent, mobile width, and a view beside Seat assignments. The PR carries the screenshots.

**Files.**

- `apps/services/gateway/gateway/routes/email/transport/accounts.py`: the route and the model.
- `tests/unit/test_email_owner_scope_fence.py`: one entry.
- A new `tests/unit/test_email_org_connection_counts.py`. It holds the 403 cases, the model
  fence and an R8 class. Copy the R8 shape of `test_email_accounts_initial_sync_rls.py`. Copy the
  403 shape of `test_billing_proxy_route.py`, which overrides `get_current_user`.
- `workbench/control_plane/src/app/settings/organization/OrganizationAdmin.tsx`: the tab.
- A new `src/app/settings/organization/EmailTab.tsx`.
- A new `src/app/settings/organization/lib/emailConnections.ts` and `emailConnections.test.ts`.
- A new `src/app/settings/organization/emailTab.test.ts`. It draws `EmailTabView` with
  `renderToStaticMarkup`. Vitest reads `*.test.ts` only, so use `createElement`, not JSX.
- `project-docs/specs/launch_surface.md` §2 and §6.2.
- This section, the EM-T3d row of §10.4, and the WS-17 row of the board.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_org_connection_counts.py \
  tests/unit/test_email_owner_scope_fence.py tests/unit/test_email_accounts_initial_sync_rls.py \
  tests/unit/test_email_imports.py tests/unit/test_org_access_enforcement.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit
cd workbench/control_plane
npx tsc --noEmit
npx vitest run src/app/settings/organization src/app/email/lib src/lib/theme src/lib/nav.test.ts
npx vitest run
node ../../.claude/hooks/ste-lint.mjs ../../project-docs/specs/email_app_master_plan.md \
  ../../project-docs/specs/launch_surface.md
```

The R8 class must show PASSED, not SKIPPED.

**Risks.**

- **A read across members.** This is the one email route that reads every mailbox row of an
  organization. The admin gate, the tenant predicate and FORCE RLS each limit it. The model of
  integers limits what it returns.
- **Small numbers.** In a small organization, a count can point at a person. "1 Gmail mailbox" can
  identify the one member who uses Gmail. D-EM-4 accepts a count, so the tab shows it.
- **A manager sees it.** The seeded `manager` role holds `admin:members:read`. A manager sees the
  count, as a manager sees the roster.
- **A forged approval.** The link is public, and the return writes nothing. The tab never says
  that the organization approved the app.
- **A fresh developer database.** It has no `email_accounts.organization_id` until EM-T2a (H-104).
  The scheduler has the same need. The R8 class runs on the promoted catalog, which has the column.
- **A removed member.** Their mailbox counts until a purge. The tab says "members connected a
  mailbox". It does not say "of your members".
- **Known limit.** The live approval needs a real Microsoft tenant. The owner tests it by hand, as
  the start of §10.4.3 says.

**As built (2026-10-02).** The handler reads through `_tenant_session()` with the ambient tenant.
Its SQL also names the organization of the session. When the two disagree, the read finds no row
and every count is zero. An R8 case holds that rule.

The tab is the last of the five, after Requests. The roster buttons do not show on it. The fences
are `tests/unit/test_email_org_connection_counts.py` and the fence entry. On the UI side, they are
`lib/emailConnections.test.ts` and `emailTab.test.ts`. The copy never uses the word "approved",
and a test refuses it.

#### 10.4.4 EM-T5 in full

**Status.** 🔨 MERGED #569 (shadow, dark) (branch `email-t5`, 2026-10-02).
The audit ran on 2026-10-02 against `0e2cfa8a`. EM-T5 is CP-13e
(`customer_console.md` §6A.14). That section keeps the four adoption rules.
This section is the build contract. Every mode stays `off` on every box, and
`DECIDE_ENABLED` stays off. The registry is `gateway/decide_features.py`, and
the fence is `tests/unit/test_email_decide_shadow.py`.

**Gate.** 🟢 AGENT-SAFE: the code, in modes `off` and `shadow`, against a fake
`decide`. 🔴 OWNER-GATE (`work_plan.md` §6.1 WS-31 (i), H-166): `DECIDE_ENABLED`
on a box, and any mode other than `off` on a box. The §3a window does not open
either one.

**Scope.**

1. **The mode registry.** Create `apps/services/gateway/gateway/decide_features.py`.
   It holds one mode for each feature: `off`, `shadow` or `on`. Every default is `off`.
   The four features are `email.cold_check`, `email.sender_pin`,
   `email.thread_status` and `email.rule_pick`.
2. **The override.** Add `decide_feature_modes` and `decide_feature_orgs` to
   `acb_common` settings. Both default to an empty string.
   `decide_feature_modes` reads `feature=mode` pairs, with a comma between pairs.
   `decide_feature_orgs` lists the organization ids that may run a mode other than `off`.
   An empty list allows no organization.
   An unknown feature or an unknown mode resolves to `off` and logs
   `decide.mode_refused`.
3. **`on` is refused in EM-T5.** A mode of `on` resolves to `off` and logs
   `decide.mode_refused`. EM-T5b lifts this with measured thresholds.
4. **One shadow helper.** The helper lives in `decide_features.py`. It asks the
   registry for the mode of the feature and the organization from
   `current_tenant()`. When the mode is `off`, it returns and makes no call.
5. **Shadow runs beside the old call.** The helper runs `acb_llm.decide` at the
   same time as the old LLM call, with a 5-second bound. The feature acts on the
   old answer in every case.
6. **The identity comes from the run context.** Rename
   `acb_llm.routed._attribution` to `run_attribution`, and keep the old name as an alias.
   The helper passes its fields to `decide()`. The helper reads no member from mail or a request.
7. **The log line.** Each shadow call logs `decide.shadow` with the feature, the
   account id, the old answer, the new answer, `agree`, the confidence or the
   probability, the latency in ms, the option count and the `request_id`. It
   logs no subject, body, sender, reason or rule name.
8. **The errors.** `DecideUnavailable` logs `decide.fallback` with its reason.
   In shadow mode, `DecideRequestInvalid` logs `decide.shadow_invalid` at error level with
   its reason code, and the feature continues on the old answer. A request that
   is not valid must never stop triage.
9. **The four sites**, in this order:
   - `automation/senders.py` `_llm_is_cold`: one boolean question.
   - `automation/learning.py` `_ai_confirms_sender_pattern`: one boolean question.
     The log records `agree` at the 0.9 probability threshold.
   - `automation/replyzero.py` `_llm_determine_thread_status`: one choice
     question. The options are REPLY, AWAITING_REPLY and DONE. FYI is an option
     only when the user did not send last. Compare with the final answer after
     the escalation, and log the old `confident` flag.
   - `automation/engine.py` `_llm_pick_rule`: one choice question. The options
     are the enabled instruction rules plus `none`. The option keys are
     `r0`, `r1` and so on. Log the option count.
10. **Clip to the Console limits.** Clip each criterion to 1000 characters.
    Send the same email text that the old call sends. Do not send more.

**Non-goals.**

- No change to `DECIDE_ENABLED`, and no mode other than `off`, on any box.
- No real mail. Every test uses a fake `decide`.
- No `on` mode and no confidence gate. EM-T5b builds both after the owner acts.
- No change to `_llm_pick_rules`. Multi-rule execution stays on the LLM.
- No table for the shadow results.
- No second classifier. Every answer that the feature acts on is the old answer.
- No change to the concurrency cap. EM-T4 wraps the one helper.

> **Amended 2026-10-02 by D-EM-7 and D-EM-8.** Two non-goals above end with EM-T5. The other non-goals still bind EM-T5.
>
> EM-T5b changes `_llm_pick_rules`, so multi-rule execution moves to `decide`. EM-T5b builds `on` with no LLM path, and with no gate that calls an LLM.

**Done when.**

- With every mode `off`, a fake `decide` records zero calls on all four sites.
- With a mode of `shadow` and an organization that is not on the list, the fake records zero calls.
- With `shadow` and an organization on the list, each site makes one `decide` call.
  The site returns the old answer when the two answers disagree.
- A `DecideUnavailable` from the fake logs `decide.fallback`, and the site returns the old answer.
- A `DecideRequestInvalid` from the fake logs `decide.shadow_invalid`, and the site returns the old answer.
- A mode of `on`, or an unknown value, resolves to `off` and logs `decide.mode_refused`.
- The `decide.shadow` record holds the listed fields. It holds no subject, body or sender.
- Inside `job_member_scope("owner@acme.com")`, `decide()` gets that member with
  `member_proven` True. With no scope, it gets no member.
- The rule question for N enabled rules holds N + 1 options, and `none` is one of them.
- The thread-status question holds FYI only when the user did not send last.
- A shadow call that runs past 5 seconds does not delay the old answer by more than 5 seconds.
- `test_console_dependency_boundary.py` passes unchanged.

**Verify with.**

```bash
uv run pytest tests/unit/test_email_decide_shadow.py \
  tests/unit/test_acb_llm_decide.py \
  tests/unit/test_console_dependency_boundary.py \
  tests/unit/test_background_ai_member.py \
  tests/unit/test_email_auto_learn_gate.py \
  tests/unit/test_email_reply_zero.py \
  tests/unit/test_email_thread_single_classification.py \
  tests/unit/test_email_classifier_unavailable.py \
  tests/unit/test_email_apply_and_watermark.py \
  tests/unit/test_crm_auto_lead.py \
  tests/unit/test_email_rules_engine.py -q -rs
uv run ruff check apps/services/gateway/gateway/decide_features.py \
  packages/acb_llm/acb_llm/routed.py \
  packages/acb_common/acb_common/settings.py \
  tests/unit/test_email_decide_shadow.py
```

The four files above must show no ruff finding. The `routes/email/automation`
directory already has findings on `main`. Run ruff on it on the branch and on
the base, and compare the counts per file and per code. The branch must show
no new finding in any file.

EM-T5 writes no SQL, so R8 does not apply. If the slice adds SQL, start
`scripts/dev_db.sh` and confirm that no test skips.

**Recorded risks.**

- **R-1.** Shadow on a box sends tenant mail to two sub-processors. The owner
  answers this in H-166, and the organization list limits it.
- **R-2.** Two sites hold a DB session across the LLM call. Shadow adds up to
  5 seconds inside that session. EM-T4 item 2 removes the session hold.
- **R-3.** The rule count has no cap. The log records the option count, so
  EM-T5b can measure accuracy against it.
- **R-4.** A log rotation can lose the sample. EM-T5b decides whether a table is needed.

#### 10.4.5 EM-T2 in full

**Status.** ✅ EM-T2a MERGED (#567, migration 223 applied on production 2026-10-02 06:41 UTC). ✅ EM-T2b MERGED (#565). ✅ EM-T2c MERGED (#568). Audited against
`0e2cfa8a` on 2026-10-02. EM-T2 has three parts, and
each part is one PR. EM-T2b and EM-T2c do not depend on EM-T2a. EM-T3d waits for EM-T2c.

**Measured state (2026-10-02).**

- `17_email_accounts.sql:30` declares `UNIQUE(user_id, provider, email_address)` with no tenant.
  `47_email_default_account.sql:17` declares `idx_email_accounts_one_default` on `(user_id)`
  with no tenant either.
- No numbered migration declares `email_accounts.organization_id`. Only
  `generated/01_add_columns.sql` declares it, and the ladder does not replay that file (H-104).
  Production has the column. A fresh developer database does not.
- No code names the old constraint. `_save_account` (`transport/oauth.py`) and
  `create_account` (`transport/accounts.py`) read first and then insert. Neither uses `ON CONFLICT`.
- The collision is latent today, because `app_user` holds each address once. It fires when a
  member moves to another organization and connects again. Row level security hides the old row,
  so the read finds nothing and the insert fails with a unique violation.
- `core.py` `_get_redis()` opens a raw client per call. The key `email:att:cache:{id}` in
  `transport/attachments.py` has no tenant. No code binds the tenant of `tenant_redis`.
- The pool of `get_tenant_redis()` decodes replies as UTF-8. A binary attachment does not decode,
  so a plain conversion turns every cache read into a silent miss.
- 107 route handlers exist in `routes/email/`. 94 carry an owner predicate or call an owner
  helper. 13 carry neither, and each has a reason.
- **One leak.** `_build_chat_context` (`automation/chat.py`) keeps an `account_id` that the
  member does not own when the member has zero mailboxes or more than one. It then reads the
  counts and sender categories of that mailbox into the prompt.

##### EM-T2a — account uniqueness per organization (migration 223)

1. Add `infra/postgres/223_email_accounts_unique_per_tenant.sql`. Copy the shape of migration 209.
2. Declare `organization_id UUID REFERENCES organization (id) ON DELETE CASCADE DEFAULT
   current_setting('app.tenant_id', true)::uuid`. Use `ADD COLUMN IF NOT EXISTS`. Write
   `REFERENCES` before `DEFAULT`.
3. Fill each NULL row from `app_user` where `lower(email) = lower(user_id)`. When only one
   organization exists, give it the remaining rows. Report the rows left NULL with `RAISE WARNING`.
4. Drop the constraint `email_accounts_user_id_provider_email_address_key`. Create
   `uq_email_accounts_org_owner_mailbox` on `(organization_id, user_id, provider, email_address)`.
5. Drop `idx_email_accounts_one_default`. Create `uq_email_accounts_org_one_default` on
   `(organization_id, user_id) WHERE is_default`.
6. Run the file between `BEGIN` and `COMMIT`. Do not use `CONCURRENTLY`. The table is small, and a
   failed concurrent build leaves an INVALID index.
7. `create_account` names `organization_id` in its INSERT, from the session. With no
   organization in the session, it returns 403.

**One file, not two releases (R6).** The drop makes the rule weaker, never stronger. Old code
never names the constraint, so old code works on the new schema. Migration 209 did the same.

**Done when.**

- A member with a mailbox row in org A connects the same mailbox in org B. Both rows exist.
- A second row with the same organization, member, provider and address raises a unique violation.
- The first mailbox of a member in org B gets `is_default = true` while org A holds a default.
- 223 applies to a database that has the generated tenancy phases and the old constraint.
- 223 applies to a fresh ladder database with no tenancy phases. A second run changes nothing.
- No unique index on `email_accounts`, other than the primary key, lacks `organization_id`.
- No code and no migration names a conflict target on `email_accounts`.
- R8: the cases above run against a real database as a non-owner role, for two organizations.

**Fence.** `tests/unit/test_email_account_unique_per_tenant.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_account_unique_per_tenant.py \
  tests/unit/test_email_tenant_bind_rls.py tests/unit/test_email_accounts_initial_sync_rls.py \
  tests/unit/test_org_purge_tenant.py tests/unit/test_tenant_coverage.py \
  tests/unit/test_tenancy_insert_fence.py tests/unit/test_h3_rls_promotion_rehearsal.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit
```

The R8 tests must show PASSED, not SKIPPED. After the deploy, read the ledger line for 223 and
`\d email_accounts` on the box.

**As built (2026-10-02).** `create_account` also puts the organization in its duplicate read
and in the default test. Both then match the new indexes with or without row level security.
The fence runs 223 in the production order on the promoted catalog, and on a dedicated
database that holds the ladder up to 222.

⚠️ **Residual.** A full replay (`MIGRATION_REPLAY_ALL=1`) runs 47 again before 223. When one
member holds a default mailbox in two organizations, 47 then fails on its old index. Migration
209 has the same shape for `people`. The ledger deploy does not replay 47.

##### EM-T2b — the attachment cache goes through `tenant_redis`

**Status.** ✅ MERGED #565 (2026-10-02).

1. Extend the seam. `get_tenant_redis(binary=True)` returns the same wrapper over a second pool
   with `decode_responses=False`. Do not add a second wrapper class. Size the second pool small.
2. Delete `_get_redis()` from `core.py` and its import from `transport/attachments.py`.
3. After the owner check, `download_attachment` binds with `organization_scope(user.organization_id)`.
4. Build the key with `key("email-att", attachment_id)`.
5. When the session has no organization, skip the cache and make no Redis call.
6. Delete the `core.py` entry from `_ALLOWED_DIRECT_REDIS` in `test_tenant_redis.py`.
7. Update the MT-1e status in `saas_multitenancy.md`.

**Non-goals.** No size cap on the cache. No read of the old keys, because they expire in one
hour. No change to the session that stays open across the provider call (EM-T4).

**Done when.**

- `core.py` and `attachments.py` import no `redis` package. The allowlist ratchet passes.
- A cache entry that org A writes is a miss for org B with the same attachment id.
- The key that the handler writes starts with `cc:<organization>:email-att:`.
- Bytes that are not valid UTF-8 come back unchanged through the binary client.
- A session with no organization gets the attachment from the provider, with no Redis call.

**Fences.** `tests/unit/test_tenant_redis.py` (the ratchet and a new binary case) and
`tests/unit/test_email_attachment_cache_tenancy.py`.

**Verify with.**

```bash
uv run pytest tests/unit/test_tenant_redis.py tests/unit/test_email_attachment_cache_tenancy.py \
  tests/unit/test_email_attachment_download.py tests/unit/test_email_attachment_inline.py -q
uv run ruff check packages/acb_common apps/services/gateway/gateway/routes/email tests/unit
```

##### EM-T2c — the owner-scope fence (D-EM-4)

1. In `_build_chat_context`, use `account_id` only when the member owns it. Otherwise use the one
   mailbox of the member, or none.
2. `ai_chat` reads `_account_models` with the resolved id only.
3. Add `tests/unit/test_email_owner_scope_fence.py`. It parses each `@router` handler in
   `gateway/routes/email/`.
4. A handler passes when it carries an owner predicate on `user_id`. It also passes when it calls
   `_account_scope`, `_assert_account_owner` or `provider_session`.
5. Every other handler must have an entry in `OWNER_SCOPE_EXEMPT` with a reason.
6. The list starts with the 13 handlers of 2026-10-02: `ai_chat`, `quick_action`,
   `cleanup_status`, `compose_assist`, `compose_assist_stream`, `process_past_status`,
   `voice_profile_status`, `image_proxy`, `oauth_authorize`, `oauth_app_info`, `oauth_callback`,
   `import_artifact` and `microsoft_webhook`.
7. A second list names each module outside `routes/email` that reads an email child table:
   `crm/activities.py`, `crm/auto_lead.py`, `tasks/capture_email.py`, `tasks/email_link.py`
   and the `email_ingestion` package. Each entry has a reason.
8. `tasks/email_link.py` writes the thread status of the mailbox in the task origin. It does not
   check who closes the task. Add an owner guard: when the member who closes the task does not own
   the mailbox, the code skips the mailbox write and logs it.
9. EM-T3d adds one entry for the connected-member count. The reason says that the query returns a
   count and no address.

**Limit.** The fence reads one function at a time. It does not follow data. The R8 case below
covers the leak that it cannot see.

**Done when.**

- Member B sends the `account_id` of the mailbox of member A to `POST /email/ai/chat`. The
  context holds no count, no category and no address of that mailbox (R8, one organization).
- Member B closes a task whose origin is a mailbox of member A. The thread status of that mailbox
  does not change.
- The fence fails on a synthetic handler that reads `email_messages` with no owner proof.
- The fence fails on a new module outside `routes/email` that reads an email child table.
- A stale entry, or an entry with no reason, fails the fence.

**As built (2026-10-02).** `_build_chat_context` starts with no resolved id. It keeps the body's
`account_id` only when the member owns it. `ai_chat` reads `_account_models` with that resolved id.
`propagate_task_done_to_thread` takes the keyword `closer_email` with no default.
`_closer_owns_mailbox` compares `user_id` with no regard to case, because the closer's email
arrives in lowercase. When the closer does not own the mailbox, the code logs
`tasks.email_link.not_owner` and writes nothing. The outside list also names
`routes/notes/dispatch.py`. That module reads `email_assistant_settings` for an account that it
selects with `user_id`, and the measurement of 2026-10-02 did not list it.

**Fences.** `tests/unit/test_email_owner_scope_fence.py` and
`tests/unit/test_email_chat_context_owner.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_owner_scope_fence.py \
  tests/unit/test_email_chat_context_owner.py tests/unit/test_email_imports.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit
```

#### 10.4.6 EM-T4 in full

**Status.** ✅ EM-T4a-1 MERGED (#570, 2026-10-02). ✅ EM-T4a-0 MERGED (#572, 2026-10-02). ✅ EM-T4c MERGED (#575, 2026-10-02). The other six parts are not built. The audit of 2026-10-02 read each anchor below in the code at `ea9467a9`. EM-T4 has nine parts, and each part is one PR.

**Gate.** 🟢 AGENT-SAFE: the code of each part, with each new setting at its default. 🔴 OWNER-GATE (`enforcement-flip`): `EMAIL_LLM_BUDGET_MODE=enforce` on a box, and any `EMAIL_OUTLOOK_DELTA` value other than `off` on a box.

**Order.**

1. EM-T4a-0 goes first, because it fixes a live defect (see its section). EM-T4a-1 and EM-T4c
   follow. They do not depend on each other.
2. EM-T4b waits for EM-T5 to merge, because it wraps the shadow helper.
3. EM-T4a-2 waits for EM-T5, because both change `engine.py` and `replyzero.py`.
4. EM-T4a-3 waits for EM-T4a-2. EM-T4a-4 waits for EM-T4a-0, EM-T4a-3 and EM-T4b.
5. EM-T4d waits for EM-T4c and EM-T6b. EM-T4c also changes `_get_client` in `outlook.py`, and EM-T6b also changes `sync_messages` (§10.4.7).
6. EM-T4e waits for EM-T2a, because both change `transport/accounts.py`. It takes the next free migration number at build time (R1).

**Measured state: sessions across external I/O on the sync path.**

- Phase (e) of `_sync_account` holds one `tenant_session(org)` across up to 25 `provider.get_message` calls (`scheduler.py:412-418`, `body_backfill.py:97-99`).
- Phase (f) holds one session across `litellm.aembedding` (`scheduler.py:423-429`, `email_embeddings.py:73`). It does nothing while `email_semantic_search_enabled` is false, which is its default (`settings.py:630`).
- `_run_rules_job` opens one block for each row (`runner.py:1648`). The block covers the rule pick, the thread status call, the provider actions, the template call and the draft agent.
- `_maybe_classify_threads` opens one block for each gap thread (`replyzero.py:1277`). `_mark_thread_replied` holds its first block across the status call (`replyzero.py:918-937`).
- `_maybe_send_follow_up_reminders` holds one block for up to 50 threads (`followups.py:92-258`). The block covers `authenticate`, `set_labels`, the body fetch, the draft agent and `create_draft`.
- `_maybe_send_digest` holds one block across the digest model call and `send_message` (`digest.py:708-772`).
- `_bulk_reconcile_provider` holds one block across `bulk_apply` and across sleeps of 2 and 8 seconds (`senders.py:460`, `senders.py:493-520`).
- `_ensure_subscription` holds one block across `authenticate` and the Graph subscription calls (`transport/sync.py:433-473`). It runs at most once in 12 hours for each mailbox.

**Measured state: the shape to copy, and the cost of the old shape.**

- The cleanup sweep already has the right shape. It calls `push_label` with no session, and then it writes the mirror and the audit row in a new block (`cleanup.py:629-651`).
- The pool holds 8 sessions plus 4 overflow. A wait for a free session fails after 10 seconds (`acb_common/settings.py:94-104`).
- On 2026-08-06, a session held open across a model call blocked a migration, and the lock queue stalled each later reader (`replyzero.py:1241-1247`).

**Measured state: the request jobs.**

- 12 sites in jobs that a request starts still call `_get_db()`, for example `runner.py:1409`, `replyzero.py:1382` and `drafting.py:1781`. Each one carries an `# H4` marker.
- `_get_db()` binds no tenant (`acb_common/db.py:177-187`). Under FORCE RLS such a session reads zero rows. So on the box these jobs most likely read nothing. Nobody has measured this on the box.

**Measured state: the model calls.**

- 11 sites call `core._llm_json` (`core.py:644`). 9 sites call a model directly: `actions.py:295`, `assistant.py:615`, `drafting.py:636`, `drafting.py:891`, `drafting.py:1036`, `drafting.py:1279`, `drafting.py:1526`, `voice_profile.py:695` and `email_embeddings.py:73`.
- Nothing limits these calls across mailboxes. The gateway runs as one uvicorn process (`deploy/hostinger/acb-gateway.service:13`).
- CP-7 owns credit budgets (`work_plan.md` §4, the Budgets row). The EM-T4 budget counts calls. It stops a loop that runs away, and it never prices anything.

**Measured state: the 401.**

- Each provider writes the bearer token into its client once (`outlook.py:126-137`, `gmail.py:180-191`). Only `authenticate()` refreshes on a 401.
- `_graph_send` retries one 429 (`outlook.py:139-161`). Outlook makes 36 calls on an httpx client, and only 3 go through `_graph_send`. Gmail makes 22 and has no wrapper.

**Measured state: delta.**

- `sync_messages` sets `history_id = None` (`outlook.py:1061`). So each poll sweeps 6 system folders and each user folder (`outlook.py:1104-1160`).
- Commits `55bec57f` and `a350b578` turned delta off on 2026-06-23. A seeded inbox token returned 0 changes in each cycle while new mail arrived. Nobody found the cause.
- The dead branch (`outlook.py:1063-1103`) has four defects. It keeps only the bare `$deltatoken`, and it sends `$top`. It reads one page with no `@odata.nextLink`, and it moves each `@removed` item to TRASH.
- The cursor column is `last_history_id TEXT` (`17_email_accounts.sql:25`). It can hold a JSON map of links, so delta needs no migration.

**Measured state: §7 item 4.** The row said "items 2 to 5" but named delta in place of item 4. Item 4 is still real:

- `list_accounts` runs one COUNT for each account (`transport/accounts.py:69`, `transport/accounts.py:242`).
- `_load_rules` reads the actions once for each rule (`automation/rules.py:119-121`). The engine calls it once for each email.
- `email_thread_status.last_message_id` has no index (`27_email_reply_tracking.sql:18`). No index on `email_messages` starts with `(account_id, thread_id, received_at)`.

**The split pattern (decided).**

Each part uses (A) of §10.4.2. Read in one `_tenant_session()` block. Call the model or the provider with no session open. Write in a new block, with no `commit()`. A write carries each value that the provider returned, for example the new id after a move. A best-effort write inside a block runs in `_savepoint`.

Option (B), a listener on the seam, stays rejected.

**One fence for every part (R7).**

Add `tests/unit/_io_watch.py`. It counts the open `_tenant_session` blocks, and it gives a watched fake model and a watched fake provider. Each fake fails the test when a block is open during its call. Each part adds its functions to `tests/unit/test_email_no_session_across_io.py`. A companion test proves that the fence fails on a function that holds a block across a fake call.

##### EM-T4a-0 — the request jobs bind a tenant (first, 2026-10-02)

**Status (2026-10-02).** ✅ MERGED #572. The ten jobs open `_tenant_session()` in phases and call no `commit()`. `routes/email` keeps one `_get_db()` site, the discovery read of `mailbox_owner`. `H2_BASELINE_ELSEWHERE` is now 80. The fence is `tests/unit/test_email_request_jobs_tenancy.py`: the AST fences, the cases with no tenant, the stream task and R8.

**Build notes.** Three helpers lost their commit: `_mark_history_held_back`, `_maybe_refresh_learned_style` and `_project_thread_status_for_backfill`. `_store_ai_draft` and `_maybe_refresh_learned_style` swallow their own failure, so each now runs in `_savepoint`. `_learn_from_sent` uses two blocks, not three. Block B holds the pattern rows, Mem0 and the style refresh, so the commit count of `test_email_learning.py` stays at 2. An early return inside a block now commits that block. For the jobs that use `provider_session`, the only write that this adds is the rotated credentials.

**Two fixes beyond the scope (2026-10-02).** `process_past_emails` passed `not req.is_test` as the
`dry_run` of the job since f1a13861. So every apply from the UI ran as a preview, and a preview
applied. It now passes `req.is_test`. After this merge, "Process past emails" moves and labels real
mail for the first time. The route also refuses a second run on a mailbox while one runs, as
reclassify and the cleanup sweep do. Fences: `test_the_job_gets_dry_run_equal_to_is_test` and
`test_a_second_run_on_one_mailbox_is_refused` in `test_email_process_past_progress.py`.

**Why first.** Email is live in the nav since #564. The 12 request jobs below open `_get_db()`,
which binds no tenant. Under FORCE RLS each one reads zero rows. So compose assist answers
"Account not found", and Process past emails, reclassify, the voice profile, learn-from-sent and
the block filters do nothing. Production showed almost no email traffic on 2026-10-02, so no
member has hit this yet.

**Scope.**

1. Each of the 12 `_get_db()` sites with an `# H4` marker opens `_tenant_session()` instead.
   The sites are in `drafting.py` (330, 533, 1781), `cleanup.py` (891, 904), `voice_profile.py`
   (351), `replyzero.py` (977, 1382, 1405), `senders.py` (812, 846) and `runner.py` (1409).
2. A BackgroundTask keeps the tenant of its request, because Starlette runs it inside the tenant
   scope. A task that `asyncio.create_task` starts copies the context. So each site uses the ambient
   tenant. A site with no tenant raises `TenantUnbound`, and the job logs it and stops.
3. A site that commits part way uses the phase split (A) of §10.4.2: one block for each phase, and
   no `commit()`.
4. `mailbox_owner` keeps its one discovery read.
5. Correct each `# H4` comment. It says "no ambient tenant to inherit", and that is false.
6. Lower `H2_BASELINE_ELSEWHERE` by the measured count.

**Non-goals.** No split of a session across external I/O, which EM-T4a-4 owns. No change to what a
job does. No cap and no budget, which EM-T4b owns. "Process past emails" keeps its own ceiling
(`test_email_process_past_cost_guard.py`), so the jobs do not need EM-T4b first.

**Done when.**

- An AST fence finds no `await _get_db()` in `routes/email` other than `mailbox_owner`. A companion
  test proves that the fence can fail.
- With no tenant bound, each job opens no session and writes nothing.
- R8: `_compose_assist_run` for a member of org B finds the account of that member.
- R8: "Process past emails" over a seeded range in org B stamps `rules_processed_at` in org B.
  Org A reads none of it.
- R8: one job from each other file (cleanup, voice profile, reclassify, block filter) writes in org B.
- The existing suites of each job pass with no changed expected value.

**Files.** The six files above. The tests are a new `tests/unit/test_email_request_jobs_tenancy.py`
and `tests/unit/test_db_engine_seam.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_request_jobs_tenancy.py tests/unit/test_db_engine_seam.py \
  tests/unit/test_email_cleanup_backfill.py tests/unit/test_email_reclassify_resumable.py \
  tests/unit/test_email_process_past_cost_guard.py tests/unit/test_email_process_past_drafting.py \
  tests/unit/test_email_process_past_idempotent.py tests/unit/test_email_process_past_progress.py \
  tests/unit/test_email_automation_tenancy.py tests/unit/test_background_ai_member.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit/test_email_request_jobs_tenancy.py
```

The R8 tests must show PASSED, not SKIPPED.

##### EM-T4a-1 — the sync core, phases (e) and (f)

1. Split `backfill_missing_bodies` into three steps: read the candidates, fetch the bodies, and write the bodies.
2. Phase (e) runs the read in one `tenant_session(org)`, the fetch with no session, and the write in a second block.
3. Split `embed_pending_messages` the same way. Phase (f) calls `_embed_batch` with no session open.
4. The read and write steps take a session and open none. Each block lives in `scheduler.py`, so the existing fence counts it.
5. No step calls `commit()`. The seam commits on exit.
6. Extend `test_no_session_is_open_during_the_provider_calls` (`test_email_scheduler_tenancy.py:665`) to `get_message` and `_embed_batch`.

**Non-goals.** No change to the batch sizes, to phases (a) to (d), or to the gateway.

**Done when.**

- In phase (e), a watched fake provider gets each `get_message` call with zero open sessions.
- With semantic search on, a watched `_embed_batch` gets its call with zero open sessions.
- A fetch that fails for one message leaves the bodies of the other messages written.
- With no candidates, phase (e) opens one session and makes no provider call.
- R8: a sync for org B writes the body into the row of org B. Org A reads none of it.
- The commit fence of `test_email_scheduler_tenancy.py` also covers `body_backfill.py` and `email_embeddings.py`.

**Files.** `apps/services/email_ingestion/email_ingestion/scheduler.py`, `body_backfill.py` and `email_embeddings.py`. The test is `tests/unit/test_email_scheduler_tenancy.py`.

**As built (2026-10-02).** `body_backfill.py` has three steps: `select_missing_bodies`,
`fetch_bodies` and `write_bodies`. `email_embeddings.py` has three steps:
`select_pending_embeddings`, `compute_embeddings` and `write_embeddings`. The read step of
phase (f) returns `None` when semantic search is off. `scheduler.py` runs each phase in
`_backfill_bodies` and `_embed_messages`. `_sync_account` keeps the log line of each failure.
The old `backfill_missing_bodies` and `embed_pending_messages` are gone, because the scheduler
was their only caller.

**Fences.** `test_no_session_is_open_during_the_provider_calls` and
`test_the_sync_steps_take_a_session_and_never_commit`, with its companion
`test_the_sync_step_fence_is_not_vacuous`. The R8 case is
`test_phases_e_and_f_write_the_fetched_bodies_into_org_b`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_scheduler_tenancy.py tests/unit/test_email_embeddings_hash.py \
  tests/unit/test_email_deep_sync.py tests/unit/test_email_manual_sync_parity.py \
  tests/unit/test_email_sync_backoff.py tests/unit/test_db_engine_seam.py -q -rs
uv run ruff check apps/services/email_ingestion tests/unit/test_email_scheduler_tenancy.py
```

The R8 tests must show PASSED, not SKIPPED.

##### EM-T4a-2 — the decision core

1. Split each function that reads and then asks a model. The read step takes `db`. The ask step takes no `db`.
2. The functions are `classify_matches` with its two match helpers (`engine.py:741-920` at `01d760e6`) and `resolve_conversation_status_matches` (`replyzero.py:657`).
3. The other functions are `recompute_thread_status` (`replyzero.py:894`), `_ai_confirms_sender_pattern` (`learning.py:67`) and `_maybe_block_cold` (`senders.py:1282`). EM-T5b-1 moves these lines again, so read them again at dispatch.
4. `recompute_thread_status` writes the status in a new block. It writes only when the newest message of the thread is still `ctx.last_message_id`.
5. The runner loop, the gap loop of `_maybe_classify_threads` and `_mark_thread_replied` use the split.
6. The EM-T5 shadow helper wraps the ask step only. In `on`, `decide_features.ask` is the ask step.

**Non-goals.** No change to a prompt, a model tier or a decision. No change to the action tail, which is EM-T4a-3.

**Done when.**

- The watched fake model gets each call with zero open sessions in the runner, the gap loop and `_mark_thread_replied`.
- A thread that gets a newer message during the ask step keeps its status row. The next cycle decides it again.
- `test_email_classify_matches.py`, `test_email_thread_single_classification.py` and `test_email_rules_engine.py` pass with no changed expected value.
- R8: the runner and the gap loop write `email_thread_status` and `email_executed_rules` rows in org B. Org A reads none of them.

**Files.** `routes/email/automation/engine.py`, `replyzero.py`, `learning.py`, `senders.py` and `runner.py`, with the fence files.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_no_session_across_io.py tests/unit/test_email_classify_matches.py \
  tests/unit/test_email_thread_single_classification.py tests/unit/test_email_rules_engine.py \
  tests/unit/test_email_reply_zero.py tests/unit/test_email_thread_status_parity.py \
  tests/unit/test_email_auto_learn_gate.py tests/unit/test_email_classifier_unavailable.py \
  tests/unit/test_email_apply_and_watermark.py tests/unit/test_email_decide_shadow.py \
  tests/unit/test_email_automation_tenancy.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit
```

##### EM-T4a-3 — the action tail on the sync path

1. `_apply_rule_actions` (`actions.py:309`) plans, then pushes, then records. The provider calls, the template call and the draft run with no session.
2. One block then writes the mirrors, the new ids and the audit row.
3. `_reconcile_thread_labels` (`replyzero.py:678`) writes the mirror in a block. It calls `set_labels` after the block closes. The mirror stays first.
4. `_maybe_send_follow_up_reminders` reads in one block. It labels, fetches and drafts with no session. It stamps each thread in its own block.
5. `_maybe_send_digest` builds the digest in one block and sends with no session. It stamps `last_digest_at` in a new block after the send returns.
6. `_bulk_reconcile_provider` calls `bulk_apply` and sleeps with no session. It writes the new ids and the reverts in a block after each try.
7. `_ensure_subscription` reads in one block, calls Graph with no session, and writes in a second block.

**Non-goals.** No change to which actions run. Automation writes stay provider-first (§2).

**Done when.**

- The watched fakes get each call with zero open sessions in the six functions.
- An Outlook move that gives a new id writes that id to `email_messages` and to the audit row.
- A failed provider action writes `FAILED` and no mirror, as `test_email_rule_action_failures.py` states today.
- A digest send that raises leaves `last_digest_at` unchanged.
- R8: the mirrors, the audit rows and the stamps land in org B. Org A reads none of them.

**Files.** `routes/email/automation/actions.py`, `drafting.py`, `replyzero.py`, `followups.py` and `senders.py`, with `routes/email/digest.py` and `transport/sync.py`.

**Verify with.** The command of EM-T4a-2, plus `test_email_rule_action_failures.py`, `test_email_digest.py`, `test_email_follow_up_scan.py`, `test_email_bulk_apply.py`, `test_email_webhook.py` and `test_email_rulepath_draft_parity.py`.

##### EM-T4a-4 — the request jobs

1. EM-T4a-0 already moved the 12 sites to `_tenant_session()`. This part adds the split only.
2. `mailbox_owner` keeps its one discovery read (`scheduler_hooks.py:41`).
3. Each job gets the split of EM-T4a-2 and EM-T4a-3.
4. Lower `H2_BASELINE_ELSEWHERE` by the measured count.

**Non-goals.** No change to what a job does, or to its progress tracker.

**Done when.**

- An AST fence finds no `await _get_db()` in `routes/email` other than `mailbox_owner`. A companion test proves the fence can fail.
- The watched fakes get each call with zero open sessions in each job.
- R8: "Process past emails" over a seeded range in org B stamps `rules_processed_at` in org B. Org A reads none of it.
- R8: `_compose_assist_run` (`drafting.py:1781`) for a member of org B finds the account of that member.

⚠️ **Cost note.** EM-T4a-0 turns these jobs on. "Process past emails" keeps its own ceiling in the
API, and EM-T4b adds the shared cap and the budget.

##### EM-T4b — one cap and one daily budget for the model calls

1. Add `apps/services/email_ingestion/email_ingestion/llm_cap.py`. It holds one `asyncio.Semaphore` for the process and one context manager, `llm_slot(account_id)`.
2. Add three settings to `acb_common/settings.py`. `email_llm_concurrency` defaults to 4. `email_llm_daily_calls` defaults to 2000.
3. `email_llm_budget_mode` is `off`, `log` or `enforce`. It defaults to `log`.
4. An automation scope marks the calls that the cap and the budget bind. A ContextVar holds it.
5. `as_mailbox_owner`, `process_new_mail` and each request job of EM-T4a-4 open the scope.
6. Outside the scope, `llm_slot` takes no slot and counts nothing. A member who asks for a draft never waits behind the sync loop.
7. `llm_slot` is re-entrant. A task that holds a slot goes through a nested `llm_slot` with no second permit.
8. The budget counts calls for each mailbox for each UTC day. The key is `key("email-llm", account_id, <date>)` from `tenant_redis`.
9. The helper uses `incr` and an `expire` of 2 days. It binds `organization_scope(current_tenant())` for the call.
10. In `log` mode, a call past the limit runs. It logs `email.llm_budget_exceeded` once a day for each mailbox.
11. In `enforce` mode, a call past the limit raises `LLMBudgetExhausted`, a new exception in `llm_cap.py`. It makes no model call.
12. The rule pick turns any model failure into `LLMUnavailable` (`engine.py:334-338`). So the runner leaves the message unstamped, and static and pattern rules still apply.
13. `_llm_determine_thread_status` raises `LLMBudgetExhausted` again. It does not write its `· auto` fallback for it.
14. When Redis fails, the budget logs `email.llm_budget_unavailable` and the call runs. The cap still binds.
15. `core._llm_json` and the 9 direct sites enter `llm_slot`. `run_agent_stream` (`chat.py:217`) is exempt, because a member drives it.
16. In `shadow`, `decide_features._ask` takes a slot only when one is free. If none is free, it logs `decide.shadow_skipped` with `reason=cap` and makes no call. In `on` (EM-T5b-2), `decide_features.ask` waits for a slot, as `_llm_json` does.

**Non-goals.** No credit budget, no price and no token count, because CP-7 owns them. No cap across processes, because the box runs one. No UI. No change to `acompletion_with_fallback`.

**Done when.**

- With a cap of 2, five automation calls at one time never run more than two fake model calls at once.
- A nested `llm_slot` inside a held slot completes with a cap of 1.
- A call outside the automation scope takes no slot and adds nothing to the counter.
- In `enforce` mode, call 2001 of one mailbox in one UTC day raises `LLMBudgetExhausted`. The fake model records no call.
- In that case the runner leaves `rules_processed_at` NULL on the message. A static rule still applies to another message.
- In `log` mode, call 2001 runs, and `email.llm_budget_exceeded` logs once.
- The key for mailbox X in org A differs from the key for the same id in org B. Each key starts with `cc:<org>:email-llm:`.
- With Redis down, the call runs and `email.llm_budget_unavailable` logs.
- With no free slot, a shadow call makes no `decide` call. The old answer returns with no extra wait.
- An AST fence finds each model await in `routes/email` and `email_ingestion` inside `llm_slot` or inside `_llm_json`. A companion test proves the fence can fail.
- `test_tenant_redis.py` passes with no new allowlist entry.

**Files.** A new `email_ingestion/llm_cap.py`, `acb_common/settings.py`, `routes/email/core.py`, the 9 direct sites, `scheduler_hooks.py` and `gateway/decide_features.py`. The test is a new `tests/unit/test_email_llm_cap.py`.

**Verify with.**

```bash
uv run pytest tests/unit/test_email_llm_cap.py tests/unit/test_tenant_redis.py \
  tests/unit/test_email_classifier_unavailable.py tests/unit/test_email_apply_and_watermark.py \
  tests/unit/test_email_reply_zero.py tests/unit/test_email_decide_shadow.py \
  tests/unit/test_background_ai_member.py tests/unit/test_email_layering.py \
  tests/unit/test_email_process_past_cost_guard.py -q -rs
uv run ruff check apps/services/email_ingestion apps/services/gateway/gateway packages/acb_common tests/unit
```

##### EM-T4c — refresh on a 401 during a sync, and try once more

1. Add one `httpx.Auth` class in `providers/base.py`. It sets the bearer from the current access token on each request.
2. On a 401, the class refreshes once under an `asyncio.Lock`. It then sends the same request once more.
3. `_get_client` in `outlook.py` and `gmail.py` passes that class. It sets no `Authorization` header.
4. A second 401 after the refresh goes back to the caller. There is no third try.
5. A refresh that fails raises, and the sync fails as it does today. The reconnect banner then shows.
6. The error path of `_sync_account` writes the credentials when `credentials_dirty()` is true. It uses its own `tenant_session(org)`.

**Non-goals.** No IMAP change. No refresh by expiry time. No change to the 429 retry or to `provider_session`.

**Done when.** The tests use `httpx.MockTransport`, because an `AsyncMock` client skips the auth flow.

- A request that gets a 401 refreshes once, sends again with the new token, and returns 200.
- Two requests that get a 401 at the same time cause one refresh.
- A second 401 after the refresh returns 401. The transport sees no third request.
- An Outlook sweep whose third page gets a 401 returns each page.
- The same cases pass for Gmail.
- After a refresh during a sync and a later failure, `credentials_encrypted` holds the new refresh token.
- A fence fails when either `_get_client` sets an `Authorization` header.

**Files.** `apps/services/email_ingestion/email_ingestion/providers/base.py`, `outlook.py`, `gmail.py` and `scheduler.py`. The test is a new `tests/unit/test_email_provider_401_retry.py`.

**As built (2026-10-02).** `RefreshingBearer` in `providers/base.py` reads `_access_token` on each
request. On a 401 it takes `_refresh_lock`, which each provider makes in `__init__`. It refreshes
only when the token is still the one that the request used.

**Bodies.** Today no request on either client sends a stream or a file. The flow reads each body
into memory before the first try anyway, so a stream can go out a second time.

**The error path.** It gets the new credentials from `_dirty_credentials` before its block opens.
So a failure there cannot cancel the error status.

**Measured before the change.** The token expired at page 3 of the inbox. The Outlook sweep and the
Gmail sweep each returned no message, because each sweep drops a folder that raises. The scheduler
then wrote a successful sync of 0 messages. `reconcile_full_snapshot` sends nothing to the trash,
because it only reads the folders that the sweep returned.

**Fix round 1 (2026-10-02).** This round repairs the three defects that the review found.

1. A body fetch in phase (e) can refresh after phase (d) wrote the credentials. A successful sync then
   kept the old tokens. Now a short `tenant_session(org)` block after phase (e) writes them again, but
   only when they changed after phase (d). Phase (f) makes no provider call. Fix round 2 changed
   what a failure of that write does.
2. A mailbox that refuses each request, with a token endpoint that works, posted to the token
   endpoint for each request. A probe saw 32 posts in one tick. Now `RefreshingBearer` keeps the
   token whose refresh the token endpoint refused, or whose new token got a 401 too. Each later 401
   with that token goes back to the caller with no refresh and no second try. A success with that
   token clears it. Also,
   `authenticate` does not refresh a token that a refresh on the same instance made. A sync calls
   `authenticate` two times. Without this rule, the token endpoint gets three posts in one tick. Now
   it gets two.
3. `list_folders` took the 400 of a failed refresh for a rejected `$select`, and sent the request
   again. Now a 400 from the token endpoint goes back to the caller.

**Fix round 2 (2026-10-02).** This round repairs the one P2 and the three P3s of the second review.

1. **Process past kept no rotated tokens.** Its apply loop builds its own provider. Before EM-T4c
   that loop could not refresh, and now a 401 there refreshes. The job now writes the tokens with
   `core._persist_rotated_creds` in its `finally`, in a short `_tenant_session()` block of its own.
   So a job that fails keeps them too. Three older paths had the same gap, and each one now calls
   the same helper after its last provider call: `undo_execution` (`runner.py`),
   `correct_applied_labels` (`actions.py`) and `get_full_body` (`transport/messages.py`).
2. **The flow remembers only a refusal.** `_refresh_refused` in `providers/base.py` says which failure of
   a refresh is a refusal: a 400 or a 401 from the token endpoint, or missing app credentials. A
   timeout, a transport error, a 5xx or a body that is not JSON can pass. So the next 401 tries the
   refresh again, and process past does not stamp a message that it could not touch.
3. **Two refreshes in one sync.** A test now refreshes before phase (d) and again in phase (e), and
   it expects two writes. The mutation `if now is None or written is not None: return` passed every
   test before this round.
4. **A failed write after phase (e) no longer fails the sync.** Before, the sync returned an
   error, so `_webhook_sync` skipped `process_new_mail`, a manual sync answered 500, and the loop
   doubled its backoff. Now a failed write goes again once in a new `tenant_session(org)` block. The
   log names the class of the error and no token, and the sync keeps its success.

**Follow-ups (named, not built).**

- **EM-T4c-f1, compare-and-set for the credential writes.** Phase (d) and the error path write
  `credentials_encrypted` with `WHERE id = :id` only. A request job can rotate the tokens while a
  sync runs, and then a stale write of the sync overwrites the newer tokens. The fix adds
  `AND credentials_encrypted = :prev` to each write, where `:prev` is the value that the writer read.
- **EM-T4c-f2, a premise to verify.** The comments say that Microsoft revokes the old refresh token
  on use. Nothing has verified this. To write the new tokens is correct in both cases.
- **EM-T4c-f3, a failed `authenticate` after a refresh.** Process past and the rules job set the
  provider to `None` when `authenticate` returns false. A refresh inside that `authenticate` then
  stays in memory.

**Fences.** `tests/unit/test_email_provider_401_retry.py`: 68 tests, and four of them are R8. Against
the source before fix round 2, nineteen of the new tests are red. Eleven mutations of the first
round, ten of fix round 1 and twelve of fix round 2 each turn a test red. The scheduler tests and
the process-past R8 test use the real `OutlookProvider` and the real refresh.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_provider_401_retry.py tests/unit/test_outlook_labels_cache_and_429.py \
  tests/unit/test_outlook_drafts.py tests/unit/test_outlook_folders_move.py \
  tests/unit/test_gmail_normaliser.py tests/unit/test_email_connect_backend.py \
  tests/unit/test_email_provider_session.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_process_past_progress.py tests/unit/test_email_request_jobs_tenancy.py \
  tests/unit/test_email_automation_tenancy.py tests/unit/test_email_fix_strips_label.py \
  tests/unit/test_email_rules_admin.py tests/unit/test_email_tool_consolidation.py \
  tests/unit/test_email_owner_scope_fence.py -q -rs
uv run ruff check apps/services/email_ingestion tests/unit/test_email_provider_401_retry.py
```

##### EM-T4d — Graph delta, in shadow first

1. Add `email_outlook_delta` to settings: `off`, `shadow` or `on`. The default is `off`.
2. A value of `on` resolves to `shadow` and logs `email.delta_mode_refused`. Only an edit of this section can lift that.
3. Delta runs for each swept folder through `/me/mailFolders/{id}/messages/delta`. It follows each `@odata.nextLink` to the `@odata.deltaLink`.
4. It stores each link whole, and it calls a stored link as it is. It sends `Prefer: odata.maxpagesize=100` and no `$top`.
5. The cursor in `last_history_id` is a JSON object with a version key and one link for each folder.
6. A cursor value that does not parse means "no cursor", and the poll does a full sweep.
7. In `shadow`, each poll runs the full sweep and the delta. It writes from the full sweep only.
8. It logs `email.delta_shadow` with three counts: in both, only in the sweep, and only in the delta.
9. A new user folder gets a cursor on its next poll. A folder that is gone loses its cursor.

**Non-goals.** No `on` mode. No delete rule for a tombstone. No change to Gmail, IMAP or the deep first sync. No new column and no migration. Delta keeps the floor of EM-T6a, and a reconnect keeps the cursor (D-EM-13).

**Done when.**

- Against a fake Graph, a delta round of three pages stores the last `@odata.deltaLink` of each folder, whole.
- The next poll calls each stored link as it is, with no `$top`.
- A stored bare token, or text that is not JSON, gives a full sweep and no error.
- In `shadow`, the rows written equal the rows of the full sweep alone.
- The `email.delta_shadow` record holds the three counts and the folder count. It holds no subject and no address.
- A value of `on` resolves to `shadow` and logs `email.delta_mode_refused`.

**Live check before any `on` (gate `enforcement-flip`).** Set `shadow` for one test mailbox. After 7 days, each `email.delta_shadow` line must show 0 "only in the sweep" for new mail. A later part, EM-T4d-2, then proposes `on` and a delete rule.

**Files.** `apps/services/email_ingestion/email_ingestion/providers/outlook.py`, `scheduler.py` and `acb_common/settings.py`. The test is a new `tests/unit/test_outlook_delta_shadow.py`.

**Verify with.**

```bash
uv run pytest tests/unit/test_outlook_delta_shadow.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_email_manual_sync_parity.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_provider_401_retry.py -q -rs
uv run ruff check apps/services/email_ingestion tests/unit/test_outlook_delta_shadow.py
```

##### EM-T4e — §7 item 4, the N+1 reads and the indexes

1. `_load_rules` reads the actions of all rules of the account in one query. It groups them in Python.
2. `list_accounts` reads the counts of all accounts in one grouped query, at both sites.
3. Add one migration with the next free number at build time (R1). Copy the locking note of migration 170.
4. It creates an index on `email_messages (account_id, thread_id, received_at DESC)`. It creates an index on `email_thread_status (last_message_id)`.
5. Use `CREATE INDEX IF NOT EXISTS`, not `CONCURRENTLY`.

**Non-goals.** No foreign key, because a message delete must not cascade into a status row. No other N+1.

**Done when.**

- `_load_rules` for 5 rules issues 2 queries, and its result equals the result of the old code.
- `list_accounts` for 3 accounts issues one count query.
- The migration applies to a fresh ladder database. A second run changes nothing.
- R8: `EXPLAIN` of the thread read in `build_thread_context` names the new index on a seeded table.

**Files.** `routes/email/automation/rules.py`, `routes/email/transport/accounts.py` and a new file in `infra/postgres/`. The test is a new `tests/unit/test_email_n_plus_one.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_n_plus_one.py tests/unit/test_email_rules_engine.py \
  tests/unit/test_email_multi_account.py tests/unit/test_email_rules_admin.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit/test_email_n_plus_one.py
```

##### EM-T4f — a disconnect stops the sync first, then deletes, and removes the Graph subscription

**Status (2026-10-02).** ✅ Part 1 and part 2 MERGED (#578), with fix round 2. Part 2 fixes the cause of the wait of 2 minutes: one sync runs at a time for each mailbox. Part 1 keeps its bound of 5 seconds on the `DELETE` for a sync that holds the row.

This is a tenth part of EM-T4. The orchestrator added it on 2026-10-02 from production evidence. It is not owner-gated.

**The defect (production, 2026-10-02).** A member connected Outlook, and the first sync stored 6410 messages. The member clicked Disconnect while the sync ran. The journal shows this order:

- 08:51:06 UTC: `sync.account_failed`, "canceling statement due to statement timeout", on `INSERT INTO email_messages`.
- 08:51:08: `sync.backoff next_in=600`.
- 08:51:10: `sync.loop_removed`. Then `DELETE /email/accounts/<id>` returned 204. Then `sync.account_done synced=6410`.
- 08:57:48: Microsoft still sent a POST to `/email/webhook/microsoft` for the deleted mailbox.

The `statement_timeout` in production is 2 minutes.

**The cause (corrected by the review of fix round 1, 2026-10-02).**

1. Nothing makes `_sync_account` run one at a time for each mailbox. `_scheduler_tasks` tracks the loop only. Five other callers run it:
   - the manual sync (`transport/sync.py:241`)
   - the resync (`transport/sync.py:296`, through `_run_manual_sync`)
   - the webhook sync (`transport/sync.py:317`)
   - the cleanup backfill (`automation/cleanup.py:905`)
   - the runner backfill (`automation/runner.py:1418`)
2. In production, the loop and a second first sync wrote the same keys, `(account_id, provider_message_id)`. The upsert is `ON CONFLICT ... DO UPDATE` (`persist.py:92`). So the `INSERT` of the loop waited for the uncommitted rows of the other run. It waited until the statement timeout of 2 minutes.
3. The loop then logged `sync.backoff`. A cancelled task cannot log that, because `CancelledError` passes `except Exception`. So the run that logged `synced=6410` was not the loop.
4. The old route also waited for the loop task inside the block of its `DELETE` (`transport/accounts.py:365-405`, `scheduler.py:778-791`). So the `DELETE` held its locks until the task ended. The new order fixes this part only.
5. The `DELETE` still waits for the KEY SHARE lock of each sync outside the loop. Each foreign key check holds one until its phase commits. The reviewer reproduced this on the scratch database as `acb_app_h3rls`.
6. No code called `OutlookProvider.delete_subscription` (`outlook.py:690`). That method also swallowed each error and read no status. The Graph subscription stayed until it expired, and Microsoft sent notifications for a mailbox that we deleted.

`update_account` (`transport/accounts.py:408-469`) calls `remove_account_sync` after its block closes, so it holds no lock during the wait. EM-T4f does not change it.

**Part 1 scope (built).**

1. Phase 1 reads the row in a short block, with the owner predicate. A row that is absent, or that another member owns, gives 404, and no loop stops. Phase 1 also reads the organization of the row.
2. With no block open, the route records whether a loop runs, and then it calls `remove_account_sync`.
3. Phase 2 runs `SET LOCAL lock_timeout = '5s'`, the `DELETE` and the default re-election, in a new block. Its `RETURNING` gives the provider, the credentials and the subscription id.
4. A lock timeout (SQLSTATE 55P03) answers 409: "A sync is still writing mail for this mailbox. Try again in a moment."
5. On any failure of phase 2, the route starts the loop again with `refresh_account_sync` and the organization of the row. It does this only when a loop ran before step 2. So a paused mailbox, or a box with `EMAIL_SYNC_ENABLED` off, gets no loop. The restart runs in a task of its own behind `asyncio.shield`, so a second cancel of the request cannot stop it (fix round 2).
6. With no block open, a `microsoft` row with a subscription id gets `delete_subscription`, with a bound of 5 seconds. The route builds the provider through `_instantiate_provider`, the gateway adapter over `build_provider`.
7. `delete_subscription` returns the HTTP status of Graph, and it raises on a transport error. 204 and 404 log `email.disconnect.subscription_deleted`. Each other status, error or timeout logs `email.disconnect.subscription_delete_failed`, and the route still returns 204. The log holds the status or the error class, and never a token. `_renew_or_replace` keeps its best effort around the delete.
8. Each phase is one block with no `commit()`, which is mechanism (A) of §10.4.2.
9. Both disconnect surfaces keep the mailbox on a refusal, and they show the reason of the gateway. The surfaces are the `DisconnectDialog` of the Email app and Remove on the Email tab of Integrations.

**Part 2 scope (built, with fix round 2).**

1. `_sync_account` takes an `asyncio.Lock` for each mailbox, and then it runs `_sync_cycle`, which holds the old body. The lock key is the account id in lower case, so an id in upper case gets the same lock. Nothing else calls `_sync_cycle`, except the one rerun below. The gateway is one uvicorn process (`deploy/hostinger/acb-gateway.service`), so a lock in the process is enough.
2. Each call has a busy mode. The loop, the webhook, the manual sync and the resync pass `if_busy="skip"`. They get `{"skipped": "busy", "synced": 0}` at once, and the log says `sync.skipped_busy`. A skip is a success with nothing synced, so the loop adds no backoff.
3. A skip marks the mailbox "rerun requested". The holder clears the mark when its cycle starts, because that cycle fetches after each earlier skip. The holder then runs ONE more shallow cycle (`deep=False`) under the lock. It does this only when its cycle ends with no error and no cancel, and no caller waits. The rerun keeps the tenant binding of the holder. Two skips cause one rerun.
4. The manual sync and the resync never wait, because the Control Plane proxy gives each POST 30 seconds. While a sync runs, they answer 409 at once: "A sync is already running for this mailbox. New mail appears when it finishes." A full sync and the resync say "Start the full sync again when it finishes", because the rerun is shallow.
5. The deep downloads of cleanup and Process past use the default, `"wait"`. They wait up to `SYNC_LOCK_WAIT_SECS` (600 seconds), and then they run. After the bound, a waiter logs `sync.busy_wait_timeout` and gets the busy result.
6. A busy result or an `error` result ends a deep download as an error, with the reason from `download_failure`. Before, the job said "done" with 0 fetched. A raised error in Process past stays best effort, as before.
7. The resync passes `purge` and `reset_cursor` into `_sync_account`. Phase (a) of the cycle applies them, under the lock. Before, the route applied them before the lock, so a running tick could write the cursor back.
8. Why 600 seconds: in production, the first sync of 6410 messages fell inside a window of about 4 minutes. Ten minutes is more than twice that. A longer wait means the holder is stuck.
9. No caller holds a session while it waits. The wrapper opens none, and the cycle opens its own blocks after the lock. No caller calls `_sync_account` or `_run_manual_sync` inside a block.
10. The wrapper releases the lock on each exit of the cycle, a cancel included. A cancelled waiter leaves with no lock. A count of holders and waiters goes with each lock. At zero, the lock, the count and the rerun mark all go.
11. The cycle returns `{"error": "Account not found", "gone": True}` when the row is gone. The loop then stops and logs `sync.loop_row_gone`. It drops its entry from `_scheduler_tasks` only while the entry is its own task.

**Part 2 bounds and trade-offs (recorded).**

- New mail that arrives during a sync appears when that sync ends, through the one rerun. Behind a long deep download, the mail appears when the download ends.
- A skip during the rerun itself causes no second rerun. That mail appears at the next tick of the loop.
- The lock lives in one process. A second gateway process needs a database lock in its place.

**Why the Graph call comes after the delete.** The loop renews the subscription, and `_renew_or_replace` can replace it with a new id. A read before the loop stops can hold an old id. The `RETURNING` of phase 2 reads the id after the stop. A token that `authenticate()` refreshes needs no write, because the row is gone.

**Non-goals.**

- No change to `update_account`.
- No migration.

**Follow-ups (recorded, not built).**

- **F-1. An orphan subscription.** A cancel can stop the loop between `create_subscription` and the `UPDATE` that stores the id in `_ensure_subscription` (`transport/sync.py:453-473`). Then no row names the new subscription, and it lives until it expires.
- **F-2. An open client.** `OutlookProvider` has no close method. The httpx client that `_get_client` opens for the Graph delete stays open until the process collects it.
- **F-3. A second SQLSTATE walk.** `_is_lock_timeout` in `transport/accounts.py` copies the walk of `is_transient` in `routes/projects/import_writer.py`. A third copy must move the walk to a shared module.
- **F-4. The loop registry keys.** `_scheduler_tasks` keeps the id as the caller gave it. The mailbox lock folds case (fix round 2), and this registry does not. A disconnect with an id in upper case then stops no loop. The loop stops by itself when it finds the row gone.

**Done when (part 1).**

- a. `remove_account_sync` runs with no block open, after the ownership read and before the `DELETE`.
- b. A member who does not own the mailbox gets 404, and `remove_account_sync` does not run.
- c. A `microsoft` row with a subscription id gets `delete_subscription` with that id. Against the real `OutlookProvider` and `httpx.MockTransport`, 204 and 404 log `subscription_deleted`, and 403 and 500 log `subscription_delete_failed` with the status. A Graph call that raises, or that is slower than the bound, still gives 204.
- d. A row with no subscription id, or a `gmail` row, builds no provider and makes no Graph call.
- e. The `DELETE` runs after `SET LOCAL lock_timeout` in the same block. A lock timeout gives 409 and starts the loop again with the organization of the row. Any other failure of phase 2 starts the loop again and raises. With no loop before step 2, nothing starts.
- f. With the real scheduler, a failed `DELETE` leaves a running loop. A cancel during the `DELETE`, and a second cancel during the restart, still let the restart finish.
- g. Both disconnect surfaces keep the mailbox on a refusal and show the detail of the gateway.
- R8: the blocks run against the promoted two-org catalog as the role with no bypass. The row and its messages are gone, and the default moves to the other mailbox. A member of the other organization gets 404. When a second connection holds a KEY SHARE lock on the row, the route answers 409 after about 5 seconds. The row stays, and a new loop runs with the organization of the row. The test bounds the call at 20 seconds, so a route with no `lock_timeout` fails it and does not hang.

**Done when (part 2).**

- h. While a sync of a mailbox runs, a skip call returns the busy result and runs no cycle. A waiting call runs after the first one ends. A second mailbox syncs at the same time. An id in upper case is the same mailbox.
- i. The wrapper releases the lock after an exception, after a cancel of the holder and after a cancel of a waiter. A waiter gives up at the bound. After 25 syncs, the dicts are empty.
- j. The loop passes `if_busy="skip"`, and a skip adds no backoff. The loop stops when its row is gone, and it keeps the entry of a newer loop.
- k. While a sync runs, the manual sync and the resync answer 409 at once, with no wait. The busy resync writes nothing.
- l. A skip during a held sync causes exactly one shallow rerun after the holder ends, with the tenant of the holder. Two skips cause one rerun. A waiter, an error or a cancel of the holder causes none.
- m. A busy or failed deep download ends the cleanup job and the Process past job as an error, with the reason.
- n. Structure: only `_sync_account` and its rerun call `_sync_cycle`. The wrapper opens no session. Each call of `_sync_account` in a file that is not a test has a constant busy mode. A mode that is not a constant fails the check, and no call into the lock runs inside a block.
- R8: sync A parks inside phase (c) with an uncommitted row. Sync B of the same mailbox does not reach the provider until A ends, and a skip call returns busy. Both then finish with 3 rows and no duplicate. A resync with `purge` and `reset_cursor` deletes the old rows and gives the provider no cursor.

**Fence (R7).** `tests/unit/test_email_disconnect_order.py`, `tests/unit/test_email_sync_one_at_a_time.py`, `workbench/control_plane/src/app/email/lib/connect.test.ts` and `workbench/control_plane/src/app/integrations/emailRemove.test.ts`.

**Files.** `transport/accounts.py`, `transport/sync.py`, `providers/outlook.py`, `scheduler.py`, `automation/cleanup.py` and `automation/runner.py`. In the Control Plane: `email/components/DisconnectDialog.tsx`, `email/lib/connect.ts`, `email/lib/emailStore.ts` and `integrations/page.tsx`. Plus the four tests, and the updated `test_email_webhook.py` and `test_email_manual_sync_parity.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_disconnect_order.py tests/unit/test_email_sync_one_at_a_time.py \
  tests/unit/test_email_request_jobs_tenancy.py tests/unit/test_email_accounts_initial_sync_rls.py \
  tests/unit/test_email_webhook.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_provider_401_retry.py tests/unit/test_email_process_past_progress.py -q -rs
uv run ruff check apps/services/gateway/gateway/routes/email/transport/accounts.py \
  tests/unit/test_email_disconnect_order.py tests/unit/test_email_sync_one_at_a_time.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/email src/app/integrations
```

⚠️ The `promoted` fixture always takes the same database name, `<ladder database>_h3rls`. Another worktree that runs an R8 suite on the same scratch server drops it. To run alone, point `TENANT_LADDER_DATABASE_URL` at a database name of your own on that server.

**Recorded risks.**

- **R-1.** A split can write a decision that a newer message made stale. EM-T4a-2 checks `last_message_id` before the write.
- **R-2.** A split can lose atomicity between a provider act and its mirror. The provider acts first, as today. When the mirror write fails, the next sync corrects the row.
- **R-3.** EM-T4a-4 can turn on jobs that do nothing on the box today. EM-T4b must merge first.
- **R-4.** The 401 retry sends a request twice. Each body in both providers is JSON or form data, so httpx can send it again.
- **R-5.** Delta stopped new mail once, and nobody found the cause. So EM-T4d builds shadow only, and the full sweep stays the source of truth.
- **R-6.** A budget of 2000 calls is a guess for one mailbox. The `log` mode measures the real count before anyone sets `enforce`.

#### 10.4.7 EM-T6 in full

**Status.** SPECIFIED (2026-10-02). EM-T6a is MERGED (#577, 2026-10-02). EM-T6b is BUILT, not merged (2026-10-02). EM-T6c to EM-T6e are not built. The audit read each anchor below in the code at `01d760e6`. The owner decisions are D-EM-10 to D-EM-16 (§10.2). EM-T6 has five parts, and each part is one PR.

**EM-T6d, part 1 (range step and progress).** ✅ MERGED (#579, 2026-10-02). The narrowing is under EM-T6d below.

**Gate.** AGENT-SAFE: all five parts. No part flips a flag. The limit is the setting `EMAIL_MAILBOX_STORAGE_LIMIT_MB`, with a default of 500. A change of it on a box is gate `env-write`. The owner answered the three checks of EM-T6c on 2026-10-02 (§10.2). An agent must not run the removal route of EM-T6c on a production mailbox, because that is a production one-off.

**Order.**

1. EM-T6a waits for EM-T4c to merge, because both change `_sync_account` and `providers/outlook.py`.
2. EM-T6b waits for EM-T6a. EM-T6c waits for EM-T6b.
3. EM-T6d waits for EM-T6a, EM-T6b and EM-T7. Before EM-T7, the recommended rules turn on drafting, and D-EM-15 forbids that.
4. EM-T6e waits for EM-T6c and EM-T6d.
5. EM-T4d waits for EM-T6b, because both change `sync_messages` in `providers/outlook.py`.
6. EM-T4e and EM-T6a both change `transport/accounts.py`, and each takes a migration number. The second to merge rebases and takes its number again (R1).

**Owner checks (answered, 2026-10-02).** §10.2 records each answer as a dated line. The reason for each check stays here.

- **Q1. Per mailbox, or per member?** The owner wrote "every user". One member can connect two mailboxes. A sum for each member is one more SQL clause, but the notice must then name a mailbox. **Answer: per mailbox.**
- **Q2. Does new mail still sync at the limit?** A mailbox that silently stops new mail is worse than a mailbox over its limit. **Answer: yes.** New mail continues to sync, and only the import of older mail stops.
- **Q3. Do the body backfill and the embeddings stop at the limit?** Outlook syncs headers only, about 2 KB for each message. Phase (e) then adds 25 bodies at each sync, newest first. So the bodies are most of the copy, and the import alone seldom reaches 500 MB. **Answer: yes, both stop at the limit.** A message that the member opens still loads its body live.

**Measured state (2026-10-02, `01d760e6`).**

- **The first sync reaches back 365 days.** `INITIAL_SYNC_DAYS = 365` (`scheduler.py:59`) sets the floor of the deep sync (`scheduler.py:314-323`). Three more callers force a deep sync. They are Resync (`transport/sync.py:296`), Clean older mail (`automation/cleanup.py:905`) and Process past emails (`automation/runner.py:1418`). With no `since`, each one reaches back 365 days.
- **The recurring poll has no time floor.** `sweep_since = since if deep else None` (`outlook.py:1111`). Each poll reads the newest 2 pages of 100 in each folder (`outlook.py:1012`), whatever their age. So a quiet user folder adds mail that is years old at the first poll. This breaks D-EM-10, and it breaks a range of 0 months.
- **The order is newest first in one folder, not across folders.** Each folder page asks for `$orderby=receivedDateTime desc` (`outlook.py:395`). The sweep reads the six system folders one by one, and then the user folders (`outlook.py:1113-1142`). A stop part way would keep a full inbox and no sent mail.
- **The sync holds every message in memory and writes them in one block at the end** (`scheduler.py:341-377`). A deep sweep can read 200 pages for each folder (`outlook.py:1013`). A crash part way loses the whole import, and the next sync starts again.
- **A reconnect does NOT start a full import again.** `_save_account` writes `last_history_id = NULL` (`transport/oauth.py:447`). Outlook ignores that cursor (`outlook.py:1061`), and no code resets `initial_sync_done`. So the next sync is the shallow poll. The comment at `transport/oauth.py:428-429` says the opposite, and it is wrong.
- **The real defect is a gap.** After a long pause, the poll reads only the newest 200 messages of each folder. The older mail of the pause never arrives. `last_synced_at` is the last sync point, and phase (d) writes it only on a success (`scheduler.py:425-438`).
- **No meter exists.** No code measures the size of a mailbox. `email_attachments` holds metadata only (`17_email_accounts.sql:92-102`). The bytes of an attachment come from the provider on demand, through a cache of one hour (EM-T2b).
- **A delete of `email_messages` reaches four tables.** `email_attachments` and `email_embeddings` cascade (`17_email_accounts.sql:94`, `73_email_embeddings.sql:21`). `email_executed_rules.message_id` and `email_rule_guidance.message_id` become NULL (`19_email_automation.sql:75`, `87_email_rule_guidance.sql:37`). `email_thread_status.last_message_id` and `email_contacts.source_message_id` have no foreign key (`27_email_reply_tracking.sql:18`, `119_email_contacts.sql:47`).
- **The body backfill is newest first already** (`body_backfill.py:121`).
- **An open stores the body that it loads.** When a member opens a message with no stored body, `get_message` fetches the body from the provider and writes it to the row (`transport/messages.py:624-654`).
- **The rules wait for a rule.** `auto_run_rules_for_account` returns when the mailbox has no enabled rule (`scheduler_hooks.py:108-118`). After that, it runs over 50 inbox messages at each cycle, newest first, with no age bound (`scheduler_hooks.py:119`, `runner.py:1604-1608`).
- **The recommended rules turn on drafting today.** The preset "Needs Reply" carries `DRAFT_EMAIL` (`rules.py:182-184`). EM-T7 changes that.
- **The connect UI shows a spinner only.** `FirstSyncBanner.tsx` draws a spinner and fixed copy. The page polls `GET /email/accounts` every 5 seconds while a first sync runs (`page.tsx:223-260`, `FIRST_SYNC_POLL_MS` at `lib/connect.ts:115`). `src/components/ui/ProgressBar.tsx` exists, and its first caller is the Projects import.
- **The OAuth state holds no range.** The signed state holds a version, a nonce, the organization, the member, the provider, `redirect_after` and an expiry (`transport/signing.py:129-137`). The BFF authorize route forwards `redirect_after` and `login_hint` only (`api/email/oauth/[provider]/authorize/route.ts:113-118`).

##### EM-T6a — the import floor and the range choice (backend)

**Status.** ✅ MERGED (#577, 2026-10-02).

**As built.**

- The migration is `225_email_import_onboarding.sql`. `email_ingestion/import_window.py` owns the ceiling, the range and the floor.
- The core drops a message below the floor in the session of phase (c). The reconcile then reads the same list.
- Closing the guided setup through the PATCH does not restart the sync loop, because a restart cancels a sync in flight. A change of `label` or `sync_enabled` restarts it, as before.
- Item 13 also corrected three claims that the audit did not list: `automation/cleanup.py` at about 728 and 943, and `automation/runner.py` at about 858. `providers/base.py` got one docstring line.
- `schema.generated.sql` is not regenerated, and scope item 1 no longer asks for it. The snapshot is stale since `079af091`, and a dump of the ladder rewrites all 7161 lines. A refresh is a separate change. The R8 suite proves the columns instead.
- Consequence: the reconcile of the recurring Outlook poll no longer reaches stored mail older than the floor. So a delete in Outlook of such mail stays in Metorite. This follows from item 5.
- Consequence: stored mail below the floor no longer gets moves or read-state changes from Outlook, because the sweep no longer reads it. Graph gives a moved message a new id, and the provider sends no `ImmutableId` header.
- F5: a reconnect no longer clears a stale Gmail `last_history_id`. Only a Resync clears it now. The risk is low, because D-EM-5 keeps Gmail out of the connect flow.
- Fix round 1 (2026-10-02). "Clean older mail → Everything" sends no date, and the route now passes the ceiling, never `import_since`. "Load older" (`POST /email/accounts/{id}/backfill`) writes no message older than the ceiling, and it stops at the first page that reaches below it. `sync_floor` returns UTC, because Outlook writes the wall time with a `Z`.

**Scope.**

1. **One migration.** Add one file in `infra/postgres/` with the next free number at build time (R1). Name it `<n>_email_import_onboarding.sql`. It adds eight columns to `email_accounts` with `ADD COLUMN IF NOT EXISTS`. Each column is nullable, with no default, no CHECK and no backfill (R6). The columns are `import_since TIMESTAMPTZ`, `import_reached_at TIMESTAMPTZ`, `import_phase TEXT`, `import_count INTEGER`, `import_estimate INTEGER`, `stored_bytes BIGINT`, `stored_bytes_at TIMESTAMPTZ` and `onboarding_done_at TIMESTAMPTZ`. EM-T6b to EM-T6e add no migration. Do not regenerate `schema.generated.sql` here. It is stale since `079af091`, and a refresh is a separate change.
2. **One floor function.** Add `email_ingestion/import_window.py`. It holds the ceiling, the floor and the conversion of a range to a date. A month is 30 days. The ceiling is `now - 180 days`. Delete `INITIAL_SYNC_DAYS`.
3. **The floor rule.** A member act can pass an explicit `since`. Its floor is the later of that `since` and the ceiling. Every other sync takes the choice of the member. Its floor is the later of `import_since` and the ceiling. The ceiling alone binds a row with `import_since` NULL, because that mailbox connected before EM-T6.
4. **`_sync_account` passes the floor on every sync**, deep or shallow. The explicit callers are Process past emails and Clean older mail. A Resync passes no `since`, so the choice of the member binds it.
5. **Outlook applies the floor on every sweep.** `sync_messages` passes `since` to `_sweep_folder` for the recurring poll too (`outlook.py:1111`). The page count of the recurring poll does not change in EM-T6a.
6. **The core is the backstop.** The core drops each message older than the floor before phase (c) writes. It keeps the message when its row is already stored. It keeps a message with no `received_at`. One query reads the stored ids of the old messages. The core logs `sync.dropped_below_floor` with the count.
7. **The authorize leg takes `import_months`.** The value is an integer from 0 to 6, and it is 1 when absent. Any other value answers 400, and the route signs no state. The signed state carries it as `import_months`.
8. **An old state still works.** `verify_oauth_state` accepts a state with no `import_months` and reads it as 1. A state signed before the deploy then completes. A state whose `import_months` is not an integer from 0 to 6 does not verify.
9. **The callback writes the range for a new mailbox only.** The INSERT writes `import_since = now() - 30 * N days`. For N = 0, it writes `import_since = now()`, `initial_sync_done = true` and `import_phase = 'done'`.
10. **A reconnect keeps the sync point (D-EM-13).** The UPDATE of a reconnect writes the credentials, `sync_status = 'idle'` and `sync_error = NULL`. It no longer writes `last_history_id = NULL`. It ignores `import_months`. Correct the comment at `transport/oauth.py:428-429`.
11. **The member closes the guided setup through the PATCH.** `AccountUpdateModel` gains `onboarding_done: bool | None`. True writes `onboarding_done_at = now()`, and false writes NULL. The handler keeps its owner predicate.
12. **The account API returns two fields.** `EmailAccountModel` gains `import_since` and `onboarding_done`. Each of the three reads of an account returns them.
13. **Text that is false goes.** Remove each "1 year", "one-year" and "365 days" claim about the sync. They are in `scheduler.py:57-59` and `:310`, `outlook.py:1027` and `:1107`, `transport/sync.py:195`, `:232`, `:264` and `:288`, and `automation/cleanup.py:873-874`.

**Non-goals.**

- No batches, no progress and no resume. EM-T6b owns them.
- No meter and no limit. EM-T6c owns them.
- No UI. The BFF does not forward `import_months` until EM-T6d, so each new connect gets the default of 1 month.
- No retention. Mail already stored that is older than the ceiling stays.
- No change to Gmail or IMAP other than the backstop of the core (D-EM-5).
- No change to the folder set of the sweep.

**Done when.**

- R8: the new migration applies to a fresh ladder database and to the promoted catalog. A second run changes nothing.
- An authorize call with `import_months=3` signs a state that verifies with `import_months` 3. With no value, the state verifies with 1.
- `import_months` of `7`, `-1` or `x` answers 400, and the test proves that `sign_oauth_state` was not called.
- A state with no `import_months` claim gives a new mailbox an `import_since` 30 days back.
- R8, as the non-owner role, for two organizations. A callback for a new mailbox in org B with `import_months=2` writes `import_since` 60 days back. The tolerance is 5 seconds. Org A reads none of it.
- R8: with `import_months=0`, the new row has `initial_sync_done = true` and an `import_since` within 5 seconds of now.
- R8: a reconnect leaves `import_since`, `initial_sync_done`, `last_synced_at` and `last_history_id` unchanged, and it writes the new credentials.
- With a fake provider, `_sync_account` passes a `since` equal to the floor in six cases. The cases are the first import, a recurring poll and a Resync. They are also an explicit `since` older than the ceiling, an explicit `since` newer than it, and a row with `import_since` NULL.
- A call with an explicit `since` 365 days back gives the provider a `since` 180 days back.
- Against a fake Graph, a recurring poll sends `receivedDateTime ge <floor>` in the `$filter` of the first page of each folder.
- R8: a fake provider that ignores `since` returns three messages older than the floor, and one of them is already stored. The sync inserts neither new one, and it updates the stored one.
- A test finds no `INITIAL_SYNC_DAYS` in `email_ingestion` or `routes/email`.
- `PATCH /email/accounts/{id}` with `onboarding_done: true` writes `onboarding_done_at`. A member who does not own the mailbox gets 404.
- `GET /email/accounts` returns `import_since` and `onboarding_done`.
- `test_email_owner_scope_fence.py` passes with no new entry.

**Files.** A new `infra/postgres/<n>_email_import_onboarding.sql`. Under `apps/services/email_ingestion/email_ingestion/`: a new `import_window.py`, `scheduler.py` and `providers/outlook.py`. Under `apps/services/gateway/gateway/routes/email/`: `transport/oauth.py`, `transport/signing.py`, `transport/accounts.py`, `transport/sync.py` (text only) and `automation/cleanup.py` (text only). The tests are a new `tests/unit/test_email_import_floor.py`, with updates to `test_email_deep_sync.py` and `test_email_oauth_state.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_import_floor.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_email_oauth_state.py tests/unit/test_email_oauth_authorize_wiring.py \
  tests/unit/test_email_connect_backend.py tests/unit/test_email_tenant_bind_rls.py \
  tests/unit/test_email_scheduler_tenancy.py tests/unit/test_email_manual_sync_parity.py \
  tests/unit/test_email_sync_backoff.py tests/unit/test_email_accounts_initial_sync_rls.py \
  tests/unit/test_email_account_unique_per_tenant.py tests/unit/test_email_owner_scope_fence.py \
  tests/unit/test_email_cleanup_backfill.py tests/unit/test_email_process_past_progress.py \
  tests/unit/test_tenancy_insert_fence.py tests/unit/test_db_engine_seam.py -q -rs
uv run ruff check apps/services/email_ingestion/email_ingestion/import_window.py \
  tests/unit/test_email_import_floor.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

The first ruff run names the new files only. The second run is the blocking set of CI. A run over the whole directories reports 113 older findings, and the changed files hold some of them.

The R8 tests must show PASSED, not SKIPPED. After the deploy, read the ledger line of the new migration and `\d email_accounts` on the box.

##### EM-T6b — newest first, in batches, with progress and resume (backend)

**Status.** BUILT, not merged (2026-10-03, branch `email-t6b`), with fix rounds 1 and 2. It sits on `main` with EM-T6a (#577), EM-T4f (#578) and EM-T6d part 1 (#579).

**As built.**

- `import_batches` takes the keyword `on_estimate`. Outlook awaits it once, before the first batch. The core then writes the estimate in a block of its own.
- The default import of the base class does not call `on_estimate`. So Gmail and IMAP show a count and no estimate.
- Outlook counts only the folders whose first page opened. A missing folder adds no count and does not make the estimate NULL.
- A resume writes the count so far plus the new count as the estimate. The import writes the message at the resume point again, and the count and the estimate both include it.
- Outlook pages each folder by time (fix rounds 1 and 2). Each next page is a new query with `lt` the second after the oldest message of the last page, and the stream drops the ids that it read again. A `$skip` link shifted when a message moved out of a folder, and the import lost the message at the page edge.
- Exchange keeps a fraction of a second, and Graph shows whole seconds. `le 10:00:05` is `le 10:00:05.000`, so it dropped the rest of a split second. In a simulation of 200,000 messages it lost 400. With `lt` the loss is 0, and the resume bound uses `lt` too.
- When one second fills a whole page, one query with `$top=1000` reads that second, and the next page starts below it. A page that adds no message ends the folder, and so does `IMPORT_MAX_PAGES` (5000). Both log `sync.import_folder_capped`.
- A 404 on a first page skips only Archive or a user folder, and the log says `sync.import_folder_skipped`. A 404 on any other system folder fails the import, and so does any other failure. The next sync resumes.
- A page that answers 429, 503 or 504 waits for `Retry-After`, with the bound of `_graph_send`, and tries once more. The recurring sweep does the same.
- When an import fails, the cycle still runs the recurring sweep and phase (c), so new mail lands (owner answer Q2). Phase (d) then writes `sync_status = 'error'` with the error, and the next sync resumes the import.
- When a sweep page fails short of the catch-up watermark, the sweep keeps the pages that it read and sets `catch_up_incomplete`. Phase (d) writes that mail and keeps `last_synced_at`. A first page that fails keeps the old rule: the sweep skips that folder.
- When the deep sync of a member act ends with no error, one block reconciles deletions against `(id, folder, received_at)` of each message that its import wrote. Only a provider with `import_full_snapshot` does this, which today is Outlook.
- Two guards protect that reconcile (fix round 2). It keeps a row whose `updated_at` is after phase (a), because a move, a rule action or a draft during the import writes the row, and Graph gives a moved message a new id. When a folder has more candidates than 50, or 2% of its rows, the reconcile leaves that folder and logs `sync.import_reconcile_skipped`.
- Follow-up for EM-T6d: `isFirstSyncPending` and `onboardingStage` must also check `syncEnabled`. When the member turns sync off during the import, the phase stays `importing`, and the panel would freeze.
- `sync_messages` takes `catch_up`. Gmail and IMAP accept it and ignore it, so `gmail.py` and `imap.py` change by one argument each.
- A deep sync of a member act writes no progress, also when the first import is not done. The next tick of the loop then runs the first import.
- The `synced` result and `messages_synced` count the rows of the import and of the recurring sweep together.
- Phase (c) and each batch of an import use one write, `_write_messages`.
- The EM-T6a tests of the first import and of a deep sync now read the import call. The R8 fakes of `test_email_scheduler_tenancy.py` and `test_email_sync_one_at_a_time.py` get an empty import.
- The catch-up sweep is not in batches. After a long pause, it holds the new mail in memory and writes it in one block at phase (c).

**Scope.**

1. **One provider method.** `BaseEmailProvider` in `providers/base.py` gains `import_batches(since, until, size=100)`. It is an async iterator of lists. Each list is newest first, and the lists in sequence are newest first across every swept folder. The default calls `sync_messages(deep=True, since=since)`, drops each message newer than `until`, sorts and cuts. Gmail and IMAP use the default.
2. **Outlook merges the folders.** `OutlookProvider.import_batches` opens one page stream for each folder of the deep sweep. Each stream filters on `receivedDateTime ge {since}`, and on `receivedDateTime le {until}` when `until` is set. The merge always takes the newest head across the streams. It reads the next page of a folder only when that folder holds the newest head. The folder set and the canonical folder names do not change.
3. **The estimate.** Before the first batch, Outlook asks each folder for `$count=true` with the same filter and `$top=1`. The sum is the estimate. When a folder gives no count, the estimate stays NULL and the import goes on.
4. **The first import runs in batches.** When `initial_sync_done` is false, `_sync_account` runs `import_batches` in place of the deep sweep. It fetches each batch with no session open. One `tenant_session(org)` then writes the messages of the batch and the progress. No block calls `commit()`.
5. **The progress columns.** Before the first batch, the import writes `import_phase = 'counting'` and then `import_estimate`. With each batch, it writes `import_phase = 'importing'`, `import_reached_at` and `import_count`. `import_reached_at` is the oldest `received_at` in the batch. `import_count` adds the rows that the batch wrote.
6. **The end of the import.** It writes `initial_sync_done = true` and `import_phase = 'done'`. The recurring sweep then runs in the same call, and phase (d) writes `last_synced_at` as today. An error leaves the progress as it is, and the error path writes `sync_status = 'error'` as today.
7. **Resume.** When `import_reached_at` is set, the import starts there, with `until = import_reached_at`. The upsert makes the overlap at that point harmless. The import never reads again the mail that is newer than that point.
8. **A deep sync runs in batches too.** Resync, Process past emails and Clean older mail use `import_batches` from now to the floor. They write no progress column, and they do not change `initial_sync_done`. The recurring sweep then runs in the same call, as after a first import.
9. **Catch-up after a pause (D-EM-13).** The watermark is `last_synced_at - 1 hour`, or `created_at` when `last_synced_at` is NULL. The recurring sweep reads at least 2 pages of each folder, as today. It reads more pages while the oldest message of the last page is newer than the watermark. `DEEP_SYNC_MAX_PAGES` caps it, and the floor still binds.
10. **The reconcile and the label learner stay on the recurring sweep.** An import batch runs neither.
11. **The account API returns the progress.** `EmailAccountModel` gains `import_reached_at`, `import_phase`, `import_count` and `import_estimate`.
12. **The docs.** §10.3 step 4 changes to: "The inbox fills batch by batch, newest first." Update `apps/services/email_ingestion/AGENTS.md`.

**Non-goals.** No meter and no limit (EM-T6c). No UI (EM-T6d). No Graph delta (EM-T4d). No change to the page count of a normal poll. No change to the folder set.

**Done when.**

- Against a fake Graph with three folders whose dates interleave, the batches in sequence are in `received_at` order, newest first, across the folders.
- The fake Graph records that a folder gets its second page only after that folder holds the newest head.
- A watched fake provider gets each batch fetch with zero open sessions.
- After each batch, `import_reached_at` equals the oldest `received_at` written, and `import_count` equals the rows written so far.
- With the count calls answering, `import_estimate` equals the sum of the folder counts. When a count call fails, the estimate is NULL, and the import still writes each message.
- A fake that raises on the third batch leaves the rows of two batches and `initial_sync_done = false`. The next sync asks for `until` equal to `import_reached_at`. It writes the rest and ends with `initial_sync_done = true` and `import_phase = 'done'`.
- With `last_synced_at` 21 days back and 600 new messages in one folder, a sync writes all 600. With `last_synced_at` 5 minutes back, a sync reads 2 pages of each folder, as today.
- After 21 days with sync turned off, a sync writes the mail of those days. It requests no page older than the floor.
- A Resync writes in batches, and it does not change `initial_sync_done` or the progress columns.
- No batch of an import calls `reconcile_full_snapshot`.
- R8, as the non-owner role, for two organizations. An import in org B writes its rows and its progress in org B. Org A reads none of them.
- The commit fence of `test_email_scheduler_tenancy.py` passes with the new blocks.

**Files.** Under `apps/services/email_ingestion/email_ingestion/`: `providers/base.py`, `providers/outlook.py`, `scheduler.py` and `AGENTS.md`. Under `routes/email/`: `transport/accounts.py`. The tests are a new `tests/unit/test_email_import_batches.py`, with updates to `test_email_deep_sync.py` and `test_email_scheduler_tenancy.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_import_batches.py tests/unit/test_email_import_floor.py \
  tests/unit/test_email_deep_sync.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_manual_sync_parity.py tests/unit/test_email_sync_backoff.py \
  tests/unit/test_email_cleanup_backfill.py tests/unit/test_email_process_past_progress.py \
  tests/unit/test_email_provider_401_retry.py tests/unit/test_email_accounts_initial_sync_rls.py \
  tests/unit/test_db_engine_seam.py -q -rs
uv run ruff check apps/services/email_ingestion tests/unit/test_email_import_batches.py
```

The R8 tests must show PASSED, not SKIPPED.

##### EM-T6c — the storage meter, the limit, and "remove older mail from Metorite" (backend)

**Waits for** EM-T6b. The owner answered Q1, Q2 and Q3 on 2026-10-02 (§10.2). The items marked (Q2) and (Q3) carry those answers.

**Scope.**

1. **The setting.** Add `email_mailbox_storage_limit_mb: int = 500` to `acb_common/settings.py`. The limit in bytes is that value times 1,048,576.
2. **The meter.** Add `measure_stored_bytes(db, account_id)` to a new `email_ingestion/storage.py`. One SELECT sums `pg_column_size` of each column of variable length in the `email_messages` rows of the mailbox. It adds the `email_attachments` rows and the `email_embeddings` rows of the mailbox. It writes `stored_bytes` and `stored_bytes_at`.
3. **The meter reads no body.** `pg_column_size` of a stored value reads its size from the stored header, so the meter does not fetch the bodies. Do not use `octet_length`, and do not take the size of a whole row.
4. **When the meter runs.** After each import batch, and at the end of each sync in phase (d).
5. **The limit stops the import.** After a batch, when `stored_bytes` is at or over the limit, the import fetches no next batch. A first import then writes `import_phase = 'limit'` and `initial_sync_done = true`. A deep sync of a member act stops in the same way, and `_sync_account` returns `limit: true` in its result.
6. **(Q3) Phases (e) and (f) stop at the limit.** At or over the limit, the body backfill makes no provider call, and the embeddings make no model call. A message that the member opens still loads its body live.
7. **(Q2) New mail still syncs at the limit.** The recurring sweep writes new mail at any meter value. Only the import of older mail stops.
8. **The preview route.** `GET /email/accounts/{id}/storage/older?before=<date>` returns the count of messages and the bytes that a removal would free. It writes nothing.
9. **The removal route.** `POST /email/accounts/{id}/storage/remove-older` with `{"before": "<date>"}` removes the mail of that mailbox received before that date. Both routes carry the owner predicate on `user_id`. A `before` that is not in the past answers 400.
10. **What the removal deletes.** It works in chunks of 1,000 messages, and each chunk is one `_tenant_session()` block with no `commit()`. It first deletes the `email_executed_rules` rows of those messages, and then the messages. The attachment rows and the embeddings cascade. Last, it deletes each `email_thread_status` row of the mailbox whose thread has no message left.
11. **What the removal keeps.** The rules, the learned patterns, the rule guidance, the senders and the contacts.
12. **After the removal.** `import_since` becomes the later of `import_since` and `before`, so a Resync does not import that mail again. The meter runs again. The answer holds the count removed and the new `stored_bytes`.
13. **The removal never reaches the provider.** `storage.py` imports nothing from `email_ingestion.providers`. Neither the routes nor `storage.py` calls `provider_session` or `build_provider`.
14. **The account API.** `EmailAccountModel` gains `stored_bytes` and `storage_limit_bytes`.

**Non-goals.** No limit for each member (Q1). No retention by age. No delete of an attachment file, because Metorite stores none. No UI (EM-T6e). No change to the mailbox in Outlook, ever (D-EM-14).

**Done when.**

- R8: a body of 100 KB of random base64 raises the meter of mailbox X by 100,000 bytes or more. The meter of mailbox Y does not change.
- R8: an `email_embeddings` row raises the meter of its mailbox.
- With a limit of 64 KB, a fake provider offers 50 messages with a body of 4 KB each. The import stops after the first batch that takes the meter to the limit, and it fetches no next batch. It writes `import_phase = 'limit'`. Each stored message is newer than each message that it did not store.
- A Resync at the limit stops in the same way, and its result holds `limit: true`.
- (Q3) At the limit, phases (e) and (f) make no provider call and no model call.
- (Q3) At the limit, a member who opens a message with no stored body gets the body.
- (Q4) At the limit, that open writes no body to the row (`transport/messages.py:624-654`), so the meter
  does not change. Under the limit, the open stores the body as it does today.
- (Q2) At the limit, the next poll still writes a new message.
- The preview returns the count and the bytes, and the count of `email_messages` rows does not change.
- R8: a removal with `before` 30 days back deletes each message of the mailbox older than that date, and no message of another mailbox. It deletes their `email_executed_rules` rows, and it moves `import_since` to `before`.
- After that removal, a Resync writes no message older than `before`.
- With `build_provider` and `provider_session` patched to raise, a removal still succeeds.
- An AST fence finds no import of `email_ingestion.providers`, `build_provider` or `provider_session` in `storage.py` or in the two handlers. A companion test proves that the fence can fail.
- An AST fence finds no `.commit()` in `storage.py`.
- R8, for two organizations: a member who does not own the mailbox gets 404 from both routes.
- `test_email_owner_scope_fence.py` passes with no new entry.

**Files.** `packages/acb_common/acb_common/settings.py`. Under `apps/services/email_ingestion/email_ingestion/`: a new `storage.py` and `scheduler.py`. Under `routes/email/`: `transport/accounts.py`. The test is a new `tests/unit/test_email_storage_limit.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_storage_limit.py tests/unit/test_email_import_batches.py \
  tests/unit/test_email_import_floor.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_embeddings_hash.py tests/unit/test_email_owner_scope_fence.py \
  tests/unit/test_email_accounts_initial_sync_rls.py tests/unit/test_org_purge_tenant.py -q -rs
uv run ruff check apps/services/email_ingestion packages/acb_common \
  apps/services/gateway/gateway/routes/email/transport tests/unit/test_email_storage_limit.py
```

The R8 tests must show PASSED, not SKIPPED.

##### EM-T6d — the guided setup: range, progress, AI rules and done (UI)

**Waits for** EM-T6a, EM-T6b and EM-T7. Before EM-T7, "Use the recommended rules" also turns on drafting, because the preset "Needs Reply" carries `DRAFT_EMAIL` (`rules.py:182-184`).

**Narrowed (orchestrator, 2026-10-02).** The owner wants an Email demo with the import timeline. The owner deferred the rules step and the storage UI. So EM-T6d has two parts.

- **Part 1** builds items 1 to 7, 12 and 13. It is BUILT, not merged. `onboardingStage` returns `importing` or `null` only. Part 2 adds `rules`.
- **Part 2** builds items 8 to 11: the rules step, the drafting step and "Done". It is not dispatched.
- **Part 1 degrades with no new field.** The progress panel draws only when the gateway sends `import_phase` (EM-T6b). Until then, the page keeps `FirstSyncBanner`, because its text is true and a bar held at 0% is not. That covers a mailbox with no `import_since` and a gateway with EM-T6a only (orchestrator, fix round 1).
- **Merge part 1 after EM-T6a.** This branch merges after EM-T6a. Before it, the gateway drops import_months, and the step text is false.
- **A retry is a first connect (fix round 1).** "Try again" and "Approved? Connect again" on the callback page open the range step again, through `/email?connect=1&provider=…`. The step shows the range that the member chose last in the tab, from `sessionStorage`, or 1.
- **Clean older mail (EM-T6a review).** Every deep download stops at 180 days, so the choices are 1, 3 and 6 months. Each choice sends an explicit `since_date`, because EM-T6a reads a null as the member's import range. `lib/cleanOlderMail.ts` holds the choices.
- **Follow-up: arrow keys.** The range step is a radio group of `Button`s, and Tab moves between them. Arrow keys belong in a shared radio-group primitive, because `SpaceSettings.tsx` has the same gap.
- **Item 13.** "Last 6 months" was already a choice. So "Last year" goes, and "Last 6 months" asks for 180 days, the ceiling of EM-T6a.

**Scope.**

1. **The stage model.** Add a pure `app/email/lib/onboarding.ts`. `onboardingStage(account)` returns `importing`, `rules` or `null`. It returns `null` when `onboarding_done` is true, or when `import_since` is NULL, which marks a mailbox connected before EM-T6. It also returns `null` while `sync_status` is `error`, because the reconnect banner owns that state (EM-T3b). It returns `importing` while `initial_sync_done` is false. Otherwise it returns `rules`. EM-T6e adds `storage`.
2. **The range step.** A click on "Microsoft 365 / Outlook" in `ConnectChoices.tsx` opens a second step before the sign-in. The title is "How much of your mail should Metorite import?" It offers seven choices, 0 to 6 months, and the default is 1 month. The choice 0 reads "Only new mail". The copy says that Metorite never imports mail older than 6 months.
3. **The range goes to the gateway.** "Continue to Microsoft" starts the authorize leg with `import_months`. `connectQuery` in `lib/connect.ts` gains `importMonths`. The BFF authorize route forwards `import_months` only when it matches `^[0-6]$`. The reconnect banner sends no `import_months`, because a reconnect keeps the range (D-EM-13).
4. **The import step shows real progress (D-EM-16).** A panel in place of `FirstSyncBanner` draws `ProgressBar` from `src/components/ui/`. With an estimate, the percent is `import_count / import_estimate`, and the detail reads "1,240 of about 3,100 messages". With no estimate, the percent is the share of the range done, and the detail reads "back to 14 Sep".
5. **The share of the range done** is `(now - import_reached_at) / (now - import_since)`. It is 0 until the first batch lands.
6. **The phase line.** `counting` reads "Counting your mail". `importing` reads "Importing your mail, newest first". The panel never shows a spinner alone while the phase is `counting` or `importing`.
7. **The source of the progress.** The panel reads the fields of `GET /email/accounts`. The first-sync poll of `page.tsx` already reads them every 5 seconds (`FIRST_SYNC_POLL_MS`). Add no endpoint and no stream.
8. **The rules step (D-EM-15).** It opens when the import ends. The title is "Set up AI rules". It says that the rules run over the imported mail and sort it. It offers three actions: "Use the recommended rules", "Choose my own" and "Skip for now".
9. **What the rules actions do.** "Use the recommended rules" calls `installPresetRules`. "Choose my own" opens AI Settings on its Rules tab, through `setAutomationFeature("ai-settings")`. After a choice, a "See insights" link opens the `analytics` view. The step names no model and offers no model choice (EM-T5b).
10. **The drafting step is opt-in (D-EM-6).** It shows only after a rule exists, because the drafting action lives on the "Needs Reply" rule (`rules.py:409-441`). A switch "Draft replies for me" is off when it opens. To turn it on, the step reads `getAssistantSettings`, sets `draft_replies` to true, and saves with `saveAssistantSettings`. When the switch stays off, the step writes nothing.
11. **Done.** "Done" and "Skip setup" send `PATCH /email/accounts/{id}` with `onboarding_done: true`. The setup then never shows again for that mailbox.
12. **Where it draws.** The panel sits at the top of the mail pane, where `FirstSyncBanner` draws today (`page.tsx:869`). It is not a modal, so the member can read the mail that arrives.
13. **Copy that promises a year goes.** `BulkUnsubscribeView.tsx:26` and `:1475` say that the first sync fetches one year. The choice "Last year" of Process past emails (`RulesTab.tsx:1511`) becomes "Last 6 months", because no import reaches further (D-EM-10).

**Non-goals.**

- No storage UI (EM-T6e).
- No change to the rules editor. Process past emails changes its longest choice only (item 13).
- No model picker, and no new gateway route.
- No range step for Gmail, which shows "Coming soon".

**Done when.**

- A vitest for `onboardingStage` covers each stage. It also covers `null` for `import_since` NULL, for `onboarding_done` and for `sync_status = 'error'`.
- The range step offers 0 to 6 months, and its default is 1.
- `connectQuery` with `importMonths` 3 holds `import_months=3`.
- The BFF authorize route forwards `import_months=3`. It drops `7`, `-1` and `x` (`route.test.ts`).
- The reconnect target holds no `import_months`.
- For `import_count` 1240 and `import_estimate` 3100, the panel draws a `progressbar` with `aria-valuenow` 40. The detail reads "1,240 of about 3,100 messages".
- With no estimate, the panel draws a `progressbar` whose value is the share of the range done, and a "back to" date.
- The markup holds a `progressbar` while the phase is `counting` or `importing`.
- The markup of the rules step holds no "model", in any case.
- "Use the recommended rules" calls `installPresetRules` once, with the account id.
- The drafting switch is off when it opens. Turning it on saves `draft_replies: true` and changes no other field.
- "Done" sends `onboarding_done: true`.
- No copy in `app/email` says that the first sync fetches a year, and no choice of Process past emails reaches further than 6 months.
- Do a visual review with the `visual-review` skill. Use light mode, compact density, a changed accent, mobile width, and a view beside Calendar. The PR carries a screenshot of each step.

**Files.** Under `workbench/control_plane/src/app/email/`: a new `lib/onboarding.ts` with `onboarding.test.ts`, `lib/connect.ts` and `connect.test.ts`, `lib/api.ts`, `lib/types.ts`, `components/ConnectChoices.tsx`, a new `components/ImportRangeStep.tsx`, a new `components/OnboardingPanel.tsx`, `page.tsx`, `components/automation/BulkUnsubscribeView.tsx` and `components/automation/ai-settings/RulesTab.tsx`. Also `src/app/api/email/oauth/[provider]/authorize/route.ts` and `route.test.ts`. Vitest reads `*.test.ts` only, so a render test uses `createElement`, not JSX.

**Verify with.**

```bash
cd workbench/control_plane
npx tsc --noEmit
npx vitest run src/app/email src/app/api/email/oauth src/lib/theme src/lib/nav.test.ts
npx vitest run
node ../../.claude/hooks/ste-lint.mjs --staged
```

##### EM-T6e — the storage notice and the removal dialog (UI)

**Waits for** EM-T6c and EM-T6d.

**Scope.**

1. **The notice.** Email shows a notice when `stored_bytes` is at or over `storage_limit_bytes`, or when `import_phase` is `limit`. An example: "This mailbox uses 512 MB of its 500 MB in Metorite. Metorite stopped importing older mail." The action is "Remove older mail from Metorite".
2. **The dialog.** The member picks a date. The choices keep the newest 1, 2, 3 or 6 months, or take a date from a picker. The dialog calls the preview route and shows "N messages, about X MB".
3. **The words of the dialog.** It says: "This removes mail from Metorite only. Your Outlook mailbox does not change." The confirm button calls the removal route with `before`.
4. **The stage.** `onboardingStage` gains `storage` between `importing` and `rules`. The panel offers the same dialog, and "Keep it as it is".

**Non-goals.** No backend change. No removal without a preview.

**Done when.**

- The notice draws for `stored_bytes` at the limit, and for `import_phase = 'limit'`. It does not draw under the limit.
- The dialog calls the preview before the member can confirm. The confirm sends `before` as the chosen date.
- The dialog markup holds the sentence "Your Outlook mailbox does not change."
- No copy in the notice or the dialog says that Metorite deletes mail in Outlook.
- `onboardingStage` returns `storage` for `import_phase = 'limit'` when `onboarding_done` is false.
- Do a visual review with the `visual-review` skill. Use light mode, compact density, a changed accent, mobile width, and a view beside Calendar. The PR carries the screenshots.

**Files.** Under `workbench/control_plane/src/app/email/`: a new `components/StorageNotice.tsx`, a new `components/RemoveOlderMailDialog.tsx`, `components/OnboardingPanel.tsx`, `lib/onboarding.ts`, `lib/api.ts` and the tests.

**Verify with.** The command of EM-T6d.

##### Recorded risks (EM-T6)

- **R-1. Cost.** After a rule exists, the rules run over 50 inbox messages at each cycle, with no age bound (`scheduler_hooks.py:119`). An import of 6 months can hold thousands of messages, at one model call each. EM-T4b measures the calls, and EM-T5b moves the rule pick to `decide`.
- **R-2. The estimate.** A folder may give no `$count`. The estimate then stays NULL, and the panel shows the share of the range done.
- **R-3. The meter is not the disk.** It leaves out the indexes, the full-text index and the vector index, and dead rows. It counts what a member can remove.
- **R-4. The meter runs at each sync.** It reads the rows of the mailbox once, with no body. Measure its time on the box after EM-T6c.
- **R-5. The inbox fills during the import.** A member can act on mail while older mail still arrives. The rules run newest first, so a decision on new mail does not wait for old mail.
- **R-6. A removal deletes the history of the rules for that mail.** The analytics of the removed period change.
- **R-7. A member act can import removed mail again.** Process past emails with an explicit date is bound by the ceiling only. The member asked for that mail, and the limit still binds.
- **R-8. A mailbox from before EM-T6 keeps its old mail.** It can hold mail older than 180 days. EM-T6 imports no new mail older than the ceiling, and it deletes nothing by age.
- **R-9. Disconnect deletes the data.** A later connect of that mailbox is a first connect, with the range step again.
- **R-10. An open at the limit (answered, Q4).** The open path stores the body that it loads (`transport/messages.py:624-654`). The owner decided on 2026-10-02 that at the limit an open shows the body and stores nothing. EM-T6c adds that check to the open path. A reopen at the limit loads the body live again.

#### 10.4.8 EM-T5b in full

**Status.** ✅ EM-T5b-1 and EM-T5b-2 (narrowed) MERGED (#576, 2026-10-02), as ONE PR. EM-T5b-3 and EM-T5b-4 are SPEC ONLY. The audit read each anchor at `01d760e6`. EM-T5b has four parts, and each part is one PR. D-EM-7 to D-EM-9 are the decisions. The "Question conventions" of `customer_console.md` §6A.14 are the contract for each question.

**EM-T5b-1 as built (2026-10-02).** `engine.py` holds `_rule_match_requests`, `_read_rule_match` and the result type `RuleMatch`. `RuleMatch.as_pick` and `RuleMatch.as_picks` give the two return shapes of item 10, for EM-T5b-2. `_fetch_sender_history` gives the history rows, and the old prompt keeps its text form. `ThreadContext.messages` holds the thread as facts. `build_thread_context` builds them only when `email.thread_status` is not `off`, so in `off` the live context is the one from before. These choices of the build are not in the text above:

- `decide_features.CHOICE_TEXT_BUDGET` (80 000 characters) is the text that all the options of one choice share. Without it, `best` over 254 long rules fails the window check of the Console.
- `decide_features.clip_fact` bounds each fact of a state twice: the raw text by the clip above, and its JSON-escaped form by two times that clip. The Console measures the escaped state, where one control character takes six characters.
- `_status_corrections_block` keeps its text, and it returns a `StatusCorrections` that also carries the notes for `decide`. They are newest first, with the conversation-rule notes first, and with no `[<rule name>]` prefix. The status path makes no new database read.
- The log names an answer by our own key (`r<i>`, `none` or `unknown`), never by the raw string of the vendor.
- `engine._MOVE_ACTIONS` is the one set of moving actions. The undo in `runner.py` imports it.

The fences are `tests/unit/test_email_decide_questions.py` and the updated `tests/unit/test_email_decide_shadow.py`.

**The owner narrowed EM-T5b-2 (2026-10-02).** The decisions (a) to (d) of §10.2 apply:

- (a) `DECIDE_ENABLED=true` is ON in production since 12:16 UTC, and one smoke call reached Jev (0.99 in 1.5 s).
- (b) The rule match runs on Jev for all organizations, with no shadow window.
- (c) Only `email.rule_match` goes `on`. The other three features keep the old path.
- (d) The automatic run touches new mail only.

**EM-T5b-2 as built, narrowed (2026-10-02).** It follows the scope of EM-T5b-2 below, reduced to the rule match:

1. `decide_features.ON_FEATURES` holds `email.rule_match` only. `on` for any other feature still resolves to `off` and logs `decide.mode_refused`.
2. `DECIDE_FEATURE_ORGS` accepts `*`, which means every organization. With an empty value, no organization runs. A call with no tenant stays `off`.
3. `decide_features.ask` runs the requests in `on`. Its bound is `ON_BOUND_S`, the 10-second client bound of item 2 below, shared by all the requests of one call. It never calls an LLM.
4. In `on`, `_llm_pick_rule` returns `RuleMatch.as_pick` and `_llm_pick_rules` returns `RuleMatch.as_picks`. So every caller uses the Jev answer: the automatic run (`classify_matches`), Process past emails, the Test routes and the re-run of one message.
5. With no decision, the matcher raises `DecisionUnavailable`, a subclass of `LLMUnavailable`. The callers already skip the `rules_processed_at` stamp on that signal, so the next cycle asks again (D-EM-8). The reasons are a timeout, `DecideUnavailable`, `DecideRequestInvalid` (also `decide.request_invalid` at error level), a reply that is not a `Decision`, and an answer that `_read_rule_match` cannot read.
6. Each call in `on` names the mailbox owner, `email_accounts.user_id`, as a proven member (`engine._decide_member`). A deployment Router key refuses a call with no member, and a request job runs as its request member.
7. The automatic run (caller `scheduler`) selects only mail that arrived at or after the oldest `created_at` of the enabled rules of the mailbox (`rules.NEW_MAIL_FLOOR_SQL`, through `runner._NEW_MAIL_ONLY`). With no enabled rule it selects nothing, and the hook already returns early then. The manual run and Process past keep no floor. Fix round 2: the Reply Zero backfill (`_maybe_classify_threads`) applies the same floor to its INBOX gap threads, because it calls `classify_matches` and writes a status and provider labels. Its sent and filed rows keep no floor. The member's "Reclassify" reuses that backfill, so it no longer reaches older inbox threads either. The floor has two trade-offs:
   - Disabling or deleting the oldest enabled rule, or "Reset rules", moves the floor forward. Mail between the old floor and the new floor is then reachable only through Process past.
   - A rule that was made disabled and enabled later keeps its creation time as the floor.

   A stored floor for each account, a nullable column, is the later fix.
8. No member chooses the rules model (D-EM-7). The card is gone from Settings, and `rule_model` is gone from the settings model, the GET answer, the PUT SQL, `_account_models` and the email agent tool. The column stays (R6), and nothing reads or writes it. The old rule call that runs outside `on` uses `tier-fast`.
9. `decide.decided` logs the keys, the probabilities, the `message_id` and each `request_id`, with no tenant text. `decide.unavailable` logs the feature, the account, the `message_id` and the reason.

**Fix round 2 (2026-10-02).**

- A conversation rule that moves mail needs 0.7 on its own `conv` option (item 8 of "The rule match request"). A label-only one keeps the plurality of the choice.
- At most one rule that moves mail applies to an email: the most probable one. A tie goes to the canonical order. The log names the others in `dropped_moves`.
- After a 402 or a 403 verdict, `ask` makes no Router call for that organization for 15 minutes (`REFUSAL_COOLDOWN_S`). Each email stays undecided with the reason `cooldown`, and the runner writes no stamp. The map lives in the one gateway process.
- `run_rules_on_message` applies nothing and stamps nothing when it gets no second answer, as the table of EM-T5b-2 item 6 says.
- The Reply Zero backfill keeps the new-mail floor (item 7 above).
- On a new-mail cycle the scheduler runs the backfill twice: once inside `process_new_mail`, and once from the every-cycle hook (`scheduler.py`). For the scheduler, the first call adds only an earlier run before auto-archive and a second capped batch. For the manual sync and the Graph webhook, `process_new_mail` is the only call. Not refactored.

Not built in this narrowing: the thread status, the cold check and the sender pin in `on`, the startup check (item 9 below), the `· auto` change (item 7 below) and the docstrings of `acb_llm/decide.py` (item 10 below).

The fences are `tests/unit/test_email_decide_on.py` (R8 for the runner, Process past and the floor), `tests/unit/test_email_assistant_settings.py` (R8 for the stored `rule_model`) and `workbench/control_plane/src/app/email/lib/noRuleModel.test.ts`.

**Gate.**

- AGENT-SAFE: the code of all four parts, against a fake `decide`.
- OWNER-GATE (§6.1 WS-31 (i), H-166): `DECIDE_ENABLED` and the router credential on a box.
- `shadow` on a box, after `DECIDE_ENABLED`, is gate `enforcement-flip`.
- OWNER "go": any `on` on a box, and the merge of EM-T5b-3. Each one changes live triage, and EM-T5b-3 removes the way back.

**Order.**

1. EM-T5b-1 goes first. EM-T4a-2 waits for it, because both change the same five functions.
2. The owner acts of H-166 follow. Then a shadow window runs for 7 days or 300 decided emails, whichever ends later.
3. EM-T5b-2 merges dark. The owner reads the window, then says "go" for `on`.
4. EM-T5b-3 merges after 7 days of `on`, with `decide.unavailable` on fewer than 1% of decisions.
5. EM-T5b-4 does not depend on the others.

**The model calls of Email (measured 2026-10-02).**

| Site | Anchor | Moves to `decide` | Question |
|---|---|---|---|
| Rule match, one rule | `engine.py:322` `_llm_pick_rule` | Yes | One boolean per rule, and two choices |
| Rule match, multi-rule | `engine.py:390` `_llm_pick_rules` | Yes | The same request |
| Thread status | `replyzero.py:333` `_llm_determine_thread_status` | Yes | One choice of 3 or 4 |
| Cold check | `senders.py:1245` `_llm_is_cold` | Yes | One boolean |
| Sender pin | `learning.py:67` `_ai_confirms_sender_pattern` | Yes | One boolean, threshold 0.9 |
| Cleanup sweep | `cleanup.py:490` `sweep_uncategorized` | No model call | It projects rule labels |
| Sender categories | `senders.py:1049` `_categorize_senders_job` | No model call | It projects rule labels |
| Draft consult plan | `drafting.py:1075` | No. CP-13f owns it | It writes a question too |
| Template fill | `actions.py:268` `_render_template` | No | Text |
| Morning brief | `digest.py:516` | No | Text |
| Rule generation | `rules.py:483` | No | Text |
| Drafts, memories, style | `drafting.py:449, 638, 735, 941`, `assistant.py:599`, `voice_profile.py:215, 253, 692` | No | Text |
| Email chat | `chat.py:221` | No | An agent run |
| Embeddings | `email_ingestion/email_embeddings.py:87` | No | Vectors |

**The rule match request.** EM-T5b-1 builds it, and EM-T5b-2 acts on it.

1. **Candidates.** Nothing changes before the AI step. The reply gate, the patterns and the static rules run first (`engine.py:741-802`).
2. **One boolean for each candidate that is not a conversation-status rule.** Its key is `r<i>`. `criteria.true` holds the rule name, its text and the user's corrections for it. `criteria.false` says that the email does not meet the rule, or that the rule excludes it.
3. **One choice, `conv`, over the conversation-status candidates, plus `none`.** Reply, Awaiting Reply, FYI and Done exclude each other. So they share one choice, and they are not four booleans. Each option uses the `r<i>` key of its rule, and holds what `criteria.true` holds.
4. **One choice, `best`, over every candidate, plus `none`.** It runs only for 2 to 254 candidates, because a choice takes 255 options or fewer. It ranks the matched rules, and it never adds a match. Its options use the same keys and text as `conv`.
5. **The state** is the object under "State shapes". It holds facts only.
6. **The instructions** hold the question and the guidance of `_CLASSIFIER_GUIDELINES`. They also hold the account-wide corrections, newest first, clipped to 1500 characters.
7. **Requests.** One request holds 16 questions or fewer. `conv` and `best` go in the first request. All requests run at the same time. When one request fails, the email is undecided.
8. **A match.** A boolean matches at its threshold or above. A rule that moves mail has a threshold of 0.7. Such a rule has an `ARCHIVE`, `MOVE_FOLDER`, `TRASH` or `MARK_SPAM` action, the set of `runner.py:591`. Every other rule has 0.5. `conv` matches when its answer is not `none`. A conversation rule that moves mail also needs 0.7 on its own option, as the thresholds table says (fix round 2). At most one rule that moves mail matches, the one with the highest probability.
9. **The main rule.** It is the `best` answer when that rule matched. If not, it is the matched rule with the highest probability. A tie goes to the canonical order of `_load_rules`.
10. **The return shapes do not change.** One-rule mode returns the main rule as `{"index", "reason"}`, or None. Multi-rule returns each match as `{"index", "reason", "primary"}`. So `classify_matches`, `_apply_matches`, the runner and process-past need no change.
11. **The reason** is `Matched by AI (probability 0.83).`, because System One returns no reason text.
12. **Why `best` is in the same request.** The brief proposed a second request over the matched rules only. One request is one round trip, and one email then has one answer or none.

**State shapes.** No state holds a persona, a command or a question.

The rule match:

```json
{"email": {"from": {"name": "", "address": ""}, "to": "", "cc": "", "date": "",
           "subject": "", "attachments": "", "body": "<first 1500 characters>"},
 "direction": "received | sent_by_owner | sent_by_owner_organisation",
 "mailbox_owner": {"address": "", "name": "", "recipient_role": "to | cc | other",
                   "about": "<first 1200 characters>"},
 "sender_history": [{"rule": "Newsletter", "count": 4}]}
```

The cold check uses `email` and `direction` from above, and adds `"sender": {"prior_contact": false}`.

The thread status:

```json
{"thread": [{"side": "owner | owner_organisation | other_party", "from": "", "to": "",
             "cc": "", "owner_cc_only": false, "date": "", "subject": "",
             "attachments": "", "body": "<first 1500 characters>"}],
 "earlier_messages_omitted": 0,
 "last_message_side": "owner | owner_organisation | other_party",
 "mailbox_owner": {"address": "", "about": "<first 1200 characters>"}}
```

The sender pin:

```json
{"sender": {"address": "", "domain": "", "public_mail_domain": true,
            "automated_local_part": false},
 "recent_messages": [{"subject": "<120 characters>", "snippet": "<160 characters>"}]}
```

- `direction` maps `sender_scope`. `external` becomes `received`, `self` becomes `sent_by_owner`, and `internal` becomes `sent_by_owner_organisation`.
- `recipient_role` maps `_recipient_role`. `direct` becomes `to`, `cc` stays `cc`, and an empty value becomes `other`.
- `sender_history` comes from `_fetch_classification_hints`, as rows and not as text.
- The thread keeps its newest messages within 8000 characters, as `_THREAD_PROMPT_BUDGET` does.
- `public_mail_domain` reads `cleanup._SHARED_DOMAINS`. `automated_local_part` reads `engine._NO_REPLY_PREFIXES`. Add no new list.
- The state carries the facts that the old prompt carried. It may add a computed fact. It adds no other tenant text.

**The instructions.** Copy these words. Each one names a state field by its path, and none names an option key.

The rule boolean `r<i>`:

```text
Does the rule that the criteria describe apply to the email in `email`?
Guidance:
- Judge `email` on its own content. `sender_history` is a hint only.
- When the rule text excludes some emails, an excluded email does not meet the rule.
- When `direction` is not "received", the owner's side sent the email. A rule for
  received mail (receipts, newsletters, marketing, cold outreach) does not apply,
  unless the rule names outbound mail.
- A rule about replying applies only when the email asks the owner for a response.
  When `mailbox_owner.recipient_role` is "cc", a reply is usually not needed.
- Use `mailbox_owner.about` to judge what matters to the owner.
Corrections from the user. They override the guidance:
- <account-wide corrections, newest first>
```

`criteria.false` for each rule: "The email does not meet the rule, or the rule excludes it."

The choice `conv`:

```text
Which conversation rule in the criteria fits the email in `email`?
Guidance:
- A conversation rule fits mail from a person that is part of an exchange.
- Bulk, automated and one-way mail fits no conversation rule.
- <the direction, cc and reply lines of the rule boolean>
Corrections from the user. They override the guidance:
- <account-wide corrections, newest first>
```

`none` for `conv`: "No conversation rule fits. The email is bulk, automated or one-way mail."

The choice `best`:

```text
Which one rule in the criteria fits the email in `email` most specifically?
Guidance:
- Prefer the most specific rule. Choose a catch-all rule only when no specific rule fits.
- <the exclude, direction, cc and reply lines of the rule boolean>
Corrections from the user. They override the guidance:
- <account-wide corrections, newest first>
```

`none` for `best`: "No rule fits this email."

The thread status:

```text
What is the status of the thread in `thread`, from the side of the mailbox owner?
Guidance:
- Weigh the whole thread. The last message decides whose turn it is. An earlier
  open question counts only when the last message did not resolve it.
- A message whose `side` is "owner" or "owner_organisation" is from the owner's side.
  A reply from the owner's organisation means that the owner's side acted.
- When someone else promised something, the other party owes the next step.
- When the owner promised a later reply or deliverable, the owner owes a reply.
- When `last_message_side` is the owner's side, and that message asks nothing and
  promises nothing, the thread is complete.
- A message where `owner_cc_only` is true, with no direct ask, needs no reply.
Corrections from the user. They override the guidance:
- <the notes of _status_corrections_block, one per line>
```

The criteria of the thread status copy the old rubric, one clause per key: `replyzero.py:372-385` for REPLY, AWAITING_REPLY and DONE, and `:354-357` for FYI. FYI is an option only when `last_message_side` is `other_party`.

The cold check:

```text
Is the email in `email` cold outreach?
Guidance:
- Cold outreach is unsolicited sales, marketing or recruiting mail from someone
  with no relationship with the mailbox owner.
- The owner has never sent mail to this sender, so `sender.prior_contact` is false.
```

The sender pin:

```text
Will every future email from `sender` belong under the rule in the yes criterion?
Guidance:
- Answer yes only for a list or no-reply address that exists to send one kind of message.
- A person is a no.
- When `sender.public_mail_domain` is true, answer no, unless `recent_messages`
  show a plainly automated sender.
- Mixed content across `recent_messages` is a no.
- When in doubt, answer no.
```

For the pin, `criteria.true` is "Every message from this sender fits this rule." and then the rule name and text. `criteria.false` is "A person, a sender of mixed content, or a sender that can send mail that needs a reply."

**Thresholds.** These are start values, and the shadow window tunes them. Each one is a named constant beside its site.

| Decision | Start value | Why |
|---|---|---|
| A rule that only labels | 0.5 | A wrong label costs one Fix |
| A rule that moves mail | 0.7 | A wrong move hides real mail from the inbox |
| Cold check | 0.5 | The old prompt asked for no margin |
| Sender pin | 0.9 | The old prompt asked for 90% sure, and a pin is permanent |
| Thread status | none | The choice decides. The log keeps the confidence |

**The log line.** Each call logs the feature, `account_id`, `message_id`, each `request_id`, the latency, and the counts of questions and requests. It also logs these values:

- the probability of each boolean, under its key, for example `p_r0`
- for each choice, the answer key, the confidence and the margin of the top two probabilities
- the matched keys and the main key
- in shadow, the old keys, `agree_set`, `agree_main` and `p_old`, the probability of the old answer

It logs no rule name, subject, body, address or `about`.

**Cost and latency.** Nobody has measured either on this box.

- The presets give 8 questions, so one request serves one email. A request serves up to 14 booleans with both choices.
- Input is near 3000 to 4000 tokens: about 1000 for the state, and about 300 for each question.
- The vendor states 70 to 500 ms. The old rule call is one chat completion with up to 800 or 1500 output tokens.
- The client bound is 10 seconds (`console_resolve.py:2503`). In `on`, a timeout leaves the email undecided.
- The EM-T4b cap wraps `decide_features.ask` (B8).

##### EM-T5b-1 — the questions, rebuilt, in shadow

**Scope.**

1. Rename `email.rule_pick` to `email.rule_match` in `decide_features.FEATURES`. It covers both modes of the rule match. `on` stays refused.
2. In `engine.py`, add `_rule_match_requests`, which builds the requests above. Add `_read_rule_match`, a pure function that reads the answers.
3. `_llm_pick_rule` and `_llm_pick_rules` both go through `decide_features.shadow` with `email.rule_match`. Multi-rule had no shadow before.
4. `decide_features.shadow` accepts a list of requests from `build`. It runs them at the same time inside the one 5-second bound. It merges the answers by question id.
5. Rebuild `_status_question`, `_cold_question` and `_sender_pin_question` to the shapes above. `ThreadContext` gains a list of structured messages. `thread_text` stays for the old call.
6. Pass `message_id` to each site from the runner, process-past, the gap loop and `recompute_thread_status`.
7. Widen the log lines as above.
8. The old LLM call still acts. No member sees a change.

**Non-goals.** No `on`. No change to a caller of `classify_matches`. No split of a session, which EM-T4a-2 owns. No Settings change. No Console change.

**Done when.**

- With every mode `off`, a fake `decide` records zero calls on all four features.
- `email.rule_pick=shadow` resolves to `off` and logs `decide.mode_refused`.
- For the 10 preset rules, one request holds 6 booleans, `conv` with 5 options and `best` with 11 options.
- For 20 cleanup rules, the questions split into 2 requests of 16 or fewer. `conv` and `best` are in the first. The fake sees both calls start before either one ends.
- Each request for 1, 15, 16, 17, 254, 255 and 300 rules passes `customer_console.decide.decide_refusal`. So does a request with a rule text of 10 000 characters, 50 corrections and a body of 20 000 characters.
- Above 254 candidates, the request holds no `best`.
- `json.dumps(state)` holds none of "You are", "Respond", "Determine", "Choose" and "acting on behalf". Each state is a dict with the listed keys.
- The rule state holds the `about`, the sender history, the recipient role and the direction. The instructions hold the account-wide corrections, and the state does not.
- `_read_rule_match`: probabilities of 0.7 and 0.2 match `r0` only. A `best` answer that did not match gives the matched rule with the highest probability. A tie goes to the canonical order.
- `_read_rule_match`: with all probabilities under the threshold, one-rule mode gets None, and multi-rule gets `[]`. A rule that moves mail does not match at 0.6.
- With `email.rule_match=shadow` and a listed organization, `_llm_pick_rules` makes one request and returns the LLM answer. The log holds both key sets.
- No `decide.*` line that `decide_features` writes for a call holds a seeded rule name, subject, body or address, and each one holds `message_id`. `decide.mode_refused` and the lines of the facade itself carry no message id, by design: they do not belong to one email.
- The thread status criteria hold "promised a follow-up", "the OTHER person's court" and "Taking ownership". FYI is an option only when `last_message_side` is `other_party`.
- No instructions text holds `REPLY`, `AWAITING_REPLY`, `DONE`, `FYI`, `r0` or `none`.
- The pin state marks `gmail.com` as a public mail domain, from `cleanup._SHARED_DOMAINS`. The rule text is in `criteria.true`, never in the state.
- The suites of `classify_matches`, the runner, process-past and Reply Zero pass with no changed expected value.

**Files.** `apps/services/gateway/gateway/decide_features.py`. In `apps/services/gateway/gateway/routes/email/automation/`: `engine.py`, `replyzero.py`, `senders.py`, `learning.py` and `runner.py` (the `message_id` only). `apps/services/gateway/AGENTS.md` line 21. `tests/unit/test_email_decide_shadow.py`, and a new `tests/unit/test_email_decide_questions.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_decide_questions.py tests/unit/test_email_decide_shadow.py \
  tests/unit/test_email_cold_gate_case.py \
  tests/unit/test_customer_console_decide.py tests/unit/test_acb_llm_decide.py \
  tests/unit/test_console_dependency_boundary.py tests/unit/test_background_ai_member.py \
  tests/unit/test_email_auto_learn_gate.py tests/unit/test_email_reply_zero.py \
  tests/unit/test_email_thread_single_classification.py tests/unit/test_email_thread_status_parity.py \
  tests/unit/test_email_classifier_unavailable.py tests/unit/test_email_apply_and_watermark.py \
  tests/unit/test_email_classify_matches.py tests/unit/test_email_guidance_reaches_prompt.py \
  tests/unit/test_email_rules_engine.py tests/unit/test_crm_auto_lead.py \
  tests/unit/test_email_process_past_cost_guard.py tests/unit/test_email_process_past_drafting.py \
  tests/unit/test_email_process_past_idempotent.py tests/unit/test_email_process_past_progress.py -q -rs
uv run ruff check apps/services/gateway/gateway/decide_features.py \
  tests/unit/test_email_decide_shadow.py tests/unit/test_email_decide_questions.py
node .claude/hooks/ste-lint.mjs --staged
```

Compare ruff on `routes/email/automation` with the base, for each file and code, as §10.4.4 says.

**One live path changes (fix round 1, 2026-10-02).** EM-T5b-1 changes one live path, the prior-contact read of the cold gate (`senders._PRIOR_CONTACT_SQL`), because the case-sensitive JSONB test read a contact with capitals as a stranger, and the `decide` state then said `prior_contact: false` as a fact. The read now compares addresses without case. `tests/unit/test_email_cold_gate_case.py` proves it on a real database (R8, as the non-owner role under FORCE RLS), so the run must show no skip.

**The shadow window.** It starts after EM-T5b-1 merges and H-166 is done. Set all four features to `shadow` in `DECIDE_FEATURE_MODES`, and the Fracktal organization id in `DECIDE_FEATURE_ORGS`. Run it for 7 days or 300 decided emails, whichever ends later. Then report for each feature: the agreement rate, the `decide.unavailable` rate, the p50 and p95 latency, and the matched rules for each email. The owner reads 20 disagreements by `message_id`, because agreement with the LLM is not accuracy.

##### EM-T5b-2 — `on`, with no LLM path

**Scope.**

1. `on` becomes a legal mode for the four email features only. For any other name, `on` still resolves to `off`.
2. Add `decide_features.ask(feature, *, account_id, message_id, build)`. It runs the requests within the 10-second client bound and returns one merged `Decision`.
3. On `DecideUnavailable`, a timeout, `DecideRequestInvalid` or any other error, `ask` logs and returns None. It never calls an LLM.
4. Each site reads `mode_for(feature)`. In `on`, it calls `ask` and reads the answer. In other modes, it runs `shadow` as today.
5. A success logs `decide.decided`. A missing answer logs `decide.unavailable` with the feature, `account_id`, `message_id` and the reason. `DecideRequestInvalid` also logs `decide.request_invalid` at error level.
6. What each site does when `ask` returns None (D-EM-8):

| Site | When `ask` returns None |
|---|---|
| Rule match | Raise `DecisionUnavailable`, a new subclass of `LLMUnavailable` in `engine.py`. Each caller already skips the stamp and applies nothing |
| Thread status inside `classify_matches` | Raise `DecisionUnavailable` again, before the broad handler at `replyzero.py:726`. The runner skips the row |
| Thread status in `recompute_thread_status` | Write nothing and return None. `_mark_thread_replied` leaves the labels. The gap loop asks again in the next cycle |
| `run_rules_on_message` | Treat a second missing answer as the first one (`runner.py:1081-1084`). Apply nothing and stamp nothing |
| Cold check | Not cold. No row and no label. The rule outcome stands, so the runner stamps the message |
| Sender pin | No pin |
| Cleanup sweep, sender categories | No change. Mail with no rule decision stays "no evidence" for this pass |

7. A decided thread status never gets the `· auto` tag. An old `· auto` row gets one more check.
8. **Remove the rules-model choice (D-EM-7).**
   - Delete the `rule_model` card (`SettingsTab.tsx:415-422`), the field (`types.ts:580-581`) and the label (`EmailToolCards.tsx:760`).
   - Delete `rule_model` from `AssistantSettingsModel`, from the GET answer and from the PUT SQL (`assistant.py`).
   - Delete the `rule` key from `_DEFAULT_TASK_MODELS` and `_account_models`. The old rule call uses `tier-fast`.
   - Delete the `rule_model` parameter and its docstring line from `update_assistant_settings` (`agents.py:1063`, `:1093-1095`, `:1125`).
   - Keep the column (R6). Correct `infra/postgres/README.md:80`.
9. **The startup check.** `register_email_post_sync_hooks` (`scheduler_hooks.py:238`) logs `email.decide_not_wired` at error level, once. It does so when a feature is `on` and `decide_enabled` is false or `router_is_wired()` is false.
10. Correct the docstrings at `acb_llm/decide.py:10-13` and `:85-92`. For email, the caller leaves the email undecided.

**Non-goals.** No change to a default mode, which stays `off`. No mode change on a box. No column drop. No change to the draft, compose or chat models.

**Done when.**

- In `on`, a fake `decide` drives each of the four sites. A fake `_llm_json` records zero calls.
- In `on`, a `DecideUnavailable` from the fake leaves `rules_processed_at` NULL and applies nothing. `decide.unavailable` logs the four fields. A fake that sleeps past the bound does the same.
- In `on`, a `DecideRequestInvalid` leaves the email undecided and logs at error level.
- In `on`, a missing thread status inside `classify_matches` writes no `email_thread_status` row, and the runner skips the row. `_mark_thread_replied` writes nothing.
- In `on`, a missing cold answer writes no `email_cold_senders` row, and the runner stamps the message.
- In `on`, a missing pin answer writes no `email_rule_patterns` row.
- `run_rules_on_message` with a missing second answer applies nothing and stamps nothing.
- A decided thread status has no `· auto` in its reason.
- `on` for a name outside the four resolves to `off` and logs `decide.mode_refused`.
- The startup check logs once when a feature is `on` and the box is not wired. It logs nothing when the box is wired.
- `AssistantSettingsModel` has no `rule_model` field, and GET has no `rule_model` key. `update_assistant_settings` has no `rule_model` parameter.
- A vitest source scan finds no `rule_model` under `src/app/email` or `src/components/email`.
- R8: a settings row with `rule_model = 'tier-balanced'` keeps that value after a PUT. GET leaves it out.
- In `off` and `shadow`, the old rule call uses `tier-fast`, whatever `rule_model` holds.

**Files.**

- `gateway/decide_features.py` and `routes/email/scheduler_hooks.py`
- In `routes/email/automation/`: `engine.py`, `replyzero.py`, `senders.py`, `learning.py`, `runner.py` and `assistant.py`
- `packages/acb_llm/acb_llm/decide.py`, the docstrings only
- `apps/agents/agent-email-assistant/agents.py`
- In `workbench/control_plane/src/`: `app/email/components/automation/ai-settings/SettingsTab.tsx`, `app/email/lib/types.ts` and `components/email/EmailToolCards.tsx`
- `infra/postgres/README.md`
- Tests: a new `tests/unit/test_email_decide_on.py`, `tests/unit/test_email_assistant_settings.py`, and a new `src/app/email/lib/noRuleModel.test.ts`

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_decide_on.py tests/unit/test_email_assistant_settings.py \
  tests/unit/test_email_decide_questions.py tests/unit/test_email_decide_shadow.py \
  tests/unit/test_email_classifier_unavailable.py tests/unit/test_email_apply_and_watermark.py \
  tests/unit/test_email_reply_zero.py tests/unit/test_email_thread_single_classification.py \
  tests/unit/test_email_auto_learn_gate.py tests/unit/test_email_classify_matches.py \
  tests/unit/test_email_rules_engine.py tests/unit/test_acb_llm_decide.py \
  tests/unit/test_console_dependency_boundary.py tests/unit/test_email_layering.py -q -rs
uv run ruff check apps/services/gateway/gateway/decide_features.py \
  tests/unit/test_email_decide_on.py tests/unit/test_email_assistant_settings.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/email src/components/email
```

The changed automation files carry old ruff findings, so compare them with the base for each file and code, as §10.4.4 says. `scheduler_hooks.py` does not change, and its 8 old RUF100 findings are not part of this check.

The R8 case must show PASSED, not SKIPPED.

**After the merge, on the owner's "go".** Set the four features to `on` for the Fracktal organization id. Report the act, the box and the evidence in the same message. The evidence is one `decide.decided` line for each feature, each with a `request_id`.

##### EM-T5b-3 — hardcode, and delete the old path

**Scope.**

1. The four email features leave `FEATURES`. Each site calls `ask` with no mode check.
2. Delete the old LLM body of each site and the four email calls to `shadow`. Delete `_STATUS_MODEL`, `_STATUS_MODEL_ESCALATION` and each prompt helper that nothing reads.
3. The startup check runs whenever email sync is on.
4. A pair in `DECIDE_FEATURE_MODES` for an email feature is now unknown, so it logs `decide.mode_refused`.

**Gate.** The merge waits for the owner's "go". The evidence is 7 days of `on`, with `decide.unavailable` on fewer than 1% of decisions. After this merge, a revert is the only way back.

**Done when.**

- `FEATURES` holds no name that starts with `email.`.
- An AST fence finds no `_llm_json` or `acompletion` call in the four decision functions. A companion test proves that the fence can fail.
- With `decide_enabled` false, a run decides no email and stamps nothing. The startup check logs at error level.
- The suites of EM-T5b-2 pass, less the email shadow cases.

**Files.** The Python files of EM-T5b-2, `tests/unit/test_email_decide_shadow.py`, and a new `tests/unit/test_email_no_llm_classifier.py`.

**Verify with.** The commands of EM-T5b-2, with `tests/unit/test_email_no_llm_classifier.py` added.

##### EM-T5b-4 — "not sorted yet", for the member

**Why.** §1 item 5 says that automation failures are visible, never silent. Under D-EM-8, an outage leaves mail unsorted, and nothing tells the member.

**Scope.**

1. Add one route with the owner scope. It returns `unsorted`. That is the count of inbox messages in the mailbox of the member that wait for the rules. Such a message has `rules_processed_at` NULL, `rules_held_back_at` NULL, and a `received_at` older than 30 minutes. The route counts only when auto-run is on.
2. Add a banner in Email, beside `FirstSyncBanner.tsx`. It says "3 new emails are not sorted yet. They are safe in your inbox, and sorting starts again by itself."
3. The banner shows nothing for a count of 0, and nothing when the read fails.
4. The count is a fact. It stays true for any cause: `decide` down, a failed sign-in or a stopped sync.

**Done when.**

- R8: the count for member A of org A leaves out the mailbox of member B of org A, and each mailbox of org B.
- The route passes `test_email_owner_scope_fence.py` with no exemption.
- The banner view draws the sentence for 3. It draws nothing for 0 or for an error, and it draws no `@`.
- A visual review in light mode, at compact density and with a changed accent, beside the inbox.

**Files.** One route in `routes/email/automation/runner.py`. A new banner in `workbench/control_plane/src/app/email/components/`. One new test file on each side.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_unsorted_count.py tests/unit/test_email_owner_scope_fence.py -q -rs
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/email
```

**Recorded risks.**

- **R-1. Accuracy is not measured.** Jev has never answered our rubric. On Outlook, a cleanup rule moves mail to a folder, so a false match hides a real email. The shadow window measures agreement with the LLM, not truth.
- **R-2. No fallback.** A vendor outage, a 429, a revoked key, an unbound tier, `DECIDE_ENABLED` off or a Console outage stops triage for every mailbox on the box. Mail stays in the inbox, unsorted, and each cycle asks again. Today `tier-decide` has no backup step.
- **R-3. More matches for each email.** Booleans do not compete, so multi-rule can apply more rules than the LLM did. When two rules move one email, the last move wins. The window counts the matched rules for each email.
- **R-4. History loses the reason.** System One gives no reason text. History shows the probability, not a sentence.
- **R-5. The model can change with no record.** The reseller allows only `typesafe/jev`. CP-13i records the dated id. Until it does, a threshold can drift with no trace.
- **R-6. An object state through the reseller has no live proof.** The CP-13h fences send a string state. The window is the first live proof, and it must pass before any `on`.
- **R-7. A session stays open across the call** until EM-T4a-2. In `on`, the bound is 10 seconds.
- **R-8. `DECIDE_ENABLED` is box-wide.** On an org key, it also opens the chat `decide` tool on tenant content, which D-EM-9 does not name.
- **R-9. Retry load in an outage.** The runner asks again for its newest 50 unsorted messages in each cycle. Each one costs one failed Console call and no vendor charge.
- **R-10. A developer box decides nothing** after EM-T5b-3, unless it reaches a Console. The tests use the fake.

#### 10.4.9 EM-T7 — automatic reply drafting is OFF by default

**Status (2026-10-02).** ✅ MERGED #574, with fix round 1. Migration 224 sets the column default to false. The fence is `tests/unit/test_email_auto_draft_defaults.py`, with R8 on a private database.

**Why (D-EM-6, §10.2).** Each automatic draft is a call on the drafting model. A member turns drafting on, and does not find it already on. Migration 81 set both defaults to false. Migration 82 set `draft_replies` back to true on 2026-07-20. D-EM-6 reverses 82 for a new mailbox.

**Scope.**

1. Add a migration that sets the default of `email_assistant_settings.draft_replies` to false.
2. Take its number at build time (R1). It was 224. Make it expand only and idempotent.
3. Set `AssistantSettingsModel.draft_replies` to false.
4. With no settings row, make the GET answer `rules.py::reply_rule_drafts`.
5. Make `reply_rule_drafts` true only when a reply rule of the mailbox carries `DRAFT_EMAIL`.
6. Make `generate_writing_style` store that answer when it creates the first settings row.
7. Remove `DRAFT_EMAIL` from the Needs Reply preset.
8. Make "Add defaults" and "Reset rules" add `DRAFT_EMAIL` only when the stored `draft_replies` is true.
9. Remove `DRAFT_EMAIL` from the preset copy in `RulesTab.tsx`.
10. Make `SettingsTab.tsx` draw the switch through `assistantSettings.ts::autoDraftRepliesOn`.
11. Make the PUT answer carry every key of the GET.
12. Keep `follow_up_auto_draft` false, and pin it with a test.

**Fix round 1 (2026-10-02).** A rule runs its own `DRAFT_EMAIL` and never reads the setting. A mailbox from before D-EM-6 got that action from the old presets. With no settings row, the GET read false while the engine drafted. The first save of another field then removed the action. So the GET now answers what the reply rule does, and a stored row still wins. A new mailbox has no rules, so it still reads false.

The same round fixed an older defect in the PUT. Its answer left out `morning_brief_enabled`, `signature_text` and `learned_writing_style`. SettingsTab keeps that answer and saves it back, so the morning brief turned off at the next save.

**Non-goals.** The migration changes no stored row. A sweep of all 5 production organizations on 2026-10-02 found 0 mailboxes, 0 settings rows and 0 rules. So no live row is at stake. The backfill does not change, because it was already OFF. This slice does not refresh `schema.generated.sql`.

An AI settings tab that was open before the deploy can save the old ON value back. The window is short, so this slice accepts it. Reset rules does not keep drafting for a mailbox from before D-EM-6 with no settings row. It installs the new presets, as a reset should.

The feature stays. A member can turn drafting on, and the save adds `DRAFT_EMAIL` to Needs Reply.

**Done when.**

- A new mailbox, with no settings row and no rules, reads `draft_replies: false` from the GET.
- With no settings row, a Needs Reply rule that carries `DRAFT_EMAIL` reads true. A rule without it reads false.
- A stored row wins over the rules in both directions.
- A save that omits the field stores false.
- The PUT answer carries every key of the GET.
- R8: the migration sets the column default to false on a fresh ladder. A second run changes nothing.
- R8: a row stored true before the migration stays true. A new row without the column gets false.
- R8: the GET, `reply_rule_drafts`, `stored_draft_replies` and `generate_writing_style` run on the private database. Each one reads its own mailbox only.
- No preset path and no new-mailbox path adds `DRAFT_EMAIL`. Each path has a test.
- A vitest proves that SettingsTab draws the switch OFF for a new mailbox.

**Files.** `infra/postgres/224_auto_draft_default_off.sql`, and `assistant.py` and `rules.py` in `routes/email/automation/`. In the control plane, `RulesTab.tsx`, `SettingsTab.tsx` and the new `app/email/lib/assistantSettings.ts`. The shared fixture is `tests/fixtures/email_new_mailbox_settings.json`. The tests are `test_email_auto_draft_defaults.py`, `test_email_presets.py`, `test_email_knowledge.py` and `assistantSettings.test.ts`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_auto_draft_defaults.py tests/unit/test_email_presets.py \
  tests/unit/test_email_knowledge.py tests/unit/test_email_assistant_settings.py \
  tests/unit/test_email_draft_replies_action.py tests/unit/test_email_rules_engine.py -q -rs
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/email
```

The R8 tests must show PASSED, not SKIPPED.

### 10.5 Owner runbook — register the Metorite Microsoft app (D-EM-1 to D-EM-3)

These are one-time owner acts. No customer ever repeats them.

1. Create a Microsoft Entra directory for Metorite. Use an account that Metorite owns.
2. In that directory, open **App registrations** and select **New registration**.
3. Set the name to `Metorite`.
4. Set the supported account types to **any organizational directory and personal Microsoft accounts**.
5. Add the web redirect URI `https://app.metorite.com/api/email/oauth/microsoft/callback`.
   This is the app domain, not `api.`, because the callback runs behind the session (EM-T1a).
   For local tests, you can also add `http://localhost:3001/api/email/oauth/microsoft/callback`.
   ⚠️ EM-T1a moved this URI from `api.` to the app domain. Each registration, new or reused,
   must list it. The interim app lists it since 2026-10-01.
6. Under **API permissions**, add these delegated Microsoft Graph permissions: `Mail.ReadWrite`,
   `Mail.Send`, `MailboxSettings.ReadWrite`, `User.Read` and `offline_access`.
7. Under **Branding**, add the Metorite logo, the home page, and the privacy and terms URLs on
   `metorite.com`.
8. Under **Certificates & secrets**, create a client secret. Record its expiry date.
9. Join the Microsoft AI Cloud Partner Program. Verify `metorite.com` as the publisher domain, and
   complete publisher verification on the app.
10. Give the client ID and the secret to an agent session. The agent writes `MSFT_OAUTH_CLIENT_ID`
    and `MSFT_OAUTH_CLIENT_SECRET` to the box under gate `env-write`. Do not paste the secret into
    a chat that a transcript keeps. Write it on the box yourself, or use a one-time channel.

**Effect on mailboxes connected today.** A refresh token belongs to the app that issued it. Each
mailbox connected through an earlier app must reconnect once, through the EM-T3 banner.
