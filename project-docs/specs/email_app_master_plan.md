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
> 📝 **EM-T6 is SPECIFIED (2026-10-02).** Guided mailbox onboarding, in five parts (§10.4.7). ✅ **EM-T6a MERGED (#577, 2026-10-02, migration 225).** ✅ **EM-T6b MERGED (#580, 2026-10-03, no migration).** The import runs newest first, in batches, with progress and resume. ✅ **EM-T6c MERGED (#615, 2026-10-04, no migration): a port of `8b4cb4dfc` that closes the gaps G1 to G5 and the findings of review round 1.** ✅ **EM-T6e MERGED (#619, 2026-10-04):** the storage notice, the removal dialog and the storage step, UI and BFF only.
> ✅ **EM-T4c MERGED (#575, 2026-10-02).** A 401 during a sync refreshes the token once, and the request goes again (§10.4.6).
> ✅ **EM-T6d, part 1 (range step and progress) MERGED (#579, 2026-10-02).** UI only (§10.4.7).
> ✅ **EM-T6d, part 2 (rules step, drafting step and Done) MERGED (#581, 2026-10-03).** UI only (§10.4.7).
> ✅ **EM-T4f parts 1 and 2 MERGED (#578, 2026-10-02).** One sync runs at a time for each mailbox, which fixes the wait of 2 minutes. A disconnect answers 409 after 5 seconds when a sync holds the row, and it removes the Graph subscription (§10.4.6).
> ✅ **EM-T4e MERGED (#586, 2026-10-03, migration 226).** The rules and the account reads make one read for their child rows. One new index serves the thread reads (§10.4.6).
> ✅ **EM-T4d MERGED (#614, 2026-10-04, no migration).** The Graph delta of Outlook runs in shadow beside the full sweep, behind `EMAIL_OUTLOOK_DELTA`, which is `off` by default. The sweep stays the one writer (§10.4.6). Review round 1 fixed seven findings, and the first is a host check on each delta link.
> ✅ **EM-T4b MERGED (#617, 2026-10-04), dark.** One cap and one daily budget bind the email model calls. The cap is 0 and the budget mode is `log` (§10.4.6).
> ✅ **EM-T4a-2 PR-A MERGED (#621, 2026-10-04).** `_mark_thread_replied` asks the thread status with no session open. A guard voids a status write when a newer inbound message arrived during the ask (§10.4.6).
> ✅ **EM-T7 MERGED (#574, 2026-10-02, §10.4.9).** Automatic reply drafting is OFF for a new mailbox (D-EM-6).
> ✅ **EM-T5b-1 and EM-T5b-2 (narrowed) MERGED (#576, 2026-10-02), as ONE PR.** The four triage questions follow the System One conventions. With `email.rule_match=on`, Jev decides the rule match with no LLM path, and the automatic run touches new mail only (§10.4.8). The modes stay `off` in code, and the orchestrator sets them on the box after the deploy. **Production:** `email.rule_match=on` for all organizations since 16:31 UTC on 2026-10-02.
> ✅ **EM-T5b-2 in full MERGED (#593, 2026-10-03), OFF in production until the owner's go.** `on` now opens the thread status, the cold check and the sender pin too, each with no LLM path. The startup check logs a box that cannot reach `decide` (§10.4.8). Review fix round 3 adds the move bar of 0.7 to an archiving cold check and to a status whose rule moves mail. It asks a sure status before the rule match, and it puts the new-mail floor on the sent rows.
> 📝 **§11 multi-inbox is SPECIFIED (2026-10-03).** Several mailboxes for one member: the AI context, the mailbox chip, All inboxes and the From row (D-EM-17 to D-EM-28, slices EM-T8a to EM-T8g). ✅ **EM-T8a MERGED (#587, 2026-10-03).** It fixes the wrong-sender defects. ✅ **EM-T8b MERGED (#588, 2026-10-03, migration 227).** Each mailbox has a name and a colour chip. ✅ **EM-T8c MERGED (#592, 2026-10-03).** The From row shows which mailbox sends, and warns when it does not fit. ✅ **EM-T8d MERGED (#596, 2026-10-03).** All inboxes lists the mail of each mailbox, and each row names its mailbox. ✅ **EM-T8e-2 MERGED (#597) and EM-T8e-3 MERGED (#599), 2026-10-03.** The chat tools bind each act to one mailbox, and the chat has a scope: one mailbox or All inboxes.
> 📝 **§12 Gmail beside Outlook is SPECIFIED (2026-10-04).** The owner amended D-EM-5, so Gmail and Google Workspace mailboxes join Outlook in the connect flow. §12 holds D-EM-31 to D-EM-35, the slices EM-G1 to EM-G10 and the Google runbook. ✅ **EM-G1 is MERGED (#625, 2026-10-05).** The re-key reclaim runs only for Outlook (D-EM-34). ✅ **EM-G2 is MERGED (#626, 2026-10-05):** the Gmail parse and the folder model of D-EM-33 (§12.3.2). ✅ **EM-G4a is MERGED (#629, 2026-10-05):** the Gmail rate limits and the record of a failed fetch (§12.3.5.1). ✅ **EM-G4b is MERGED (#632, 2026-10-05):** the Gmail history cursor and its recovery (§12.3.5.2). ✅ **EM-G3a is MERGED (#634, 2026-10-05):** Gmail send and drafts (§12.3.3). ✅ **EM-G7 is MERGED (#637, 2026-10-05).** The connect backend asks the two scopes of D-EM-31 and answers the capability read of D-EM-35. `EMAIL_GMAIL_CONNECT` keeps Gmail dark (D-EM-36), and no Integrations write can set a mail-app key (O-GM-5). ✅ **EM-G8 is MERGED (#638, 2026-10-05):** the connect UI, dark, because Gmail stays "Coming soon" while the capability read says no (§12.3.10). ✅ **EM-G7b is MERGED (#639, 2026-10-05):** `EMAIL_GMAIL_CONNECT_MEMBERS` narrows the Gmail connect to the listed members, for the live test of the owner (§12.3.9b). ✅ **EM-G9 is MERGED (#640, 2026-10-05):** the parity tests of a Gmail and Outlook pair, with no SQL change (§12.3.11). The orchestrator amended D-EM-36: the flag flips for the owner's test after EM-G5a, EM-G9 and EM-G7b merge (§12.2). ✅ **EM-G5a is MERGED (#641, 2026-10-05):** the Gmail import reads one list of all mail, with an estimate and a resume (§12.3.6.1). ✅ **EM-G5b is MERGED (#647, 2026-10-05):** a Gmail Resync trashes a row only after Gmail answers 404 `notFound` to its provider id (§12.3.6.2). ✅ **EM-G3b is MERGED (#645, 2026-10-05):** a Gmail move to a user label, and the Gmail filter list (§12.3.4).
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
- ~~Multi-account / multi-provider parity is not a near-term goal.~~ **§11 replaces this for
  several Outlook mailboxes of one member (2026-10-03, D-EM-17).** Provider parity is still not a
  goal: D-EM-5 keeps Outlook the only provider in the connect flow. *(History: the old success
  criterion "Connect 2+ Gmail + 1+ Microsoft accounts" is retired. Gmail and IMAP code stays latent.)*
  **2026-10-04:** the owner amended D-EM-5. Gmail joins Outlook in the connect flow, and §12 owns that plan.
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

**2026-10-04.** The Gmail row above is superseded. The owner amended D-EM-5, and §12 plans the Gmail work beside Outlook. IMAP stays latent.

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
| **D-EM-5** | ~~**Outlook is the only provider in the connect flow.** Google shows "coming soon".~~ **Amended by the owner, 2026-10-04: Gmail and Google Workspace mailboxes join Outlook in the connect flow.** Metorite owns ONE Google OAuth app, and the setup stays one click: Connect, consent, done. §12 owns the plan (D-EM-31 to D-EM-35, slices EM-G1 to EM-G10). IMAP stays hidden until its connect path works. |
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
| **EM-T4** | 🟢 AGENT-SAFE · 🔴 two flips (`enforcement-flip`) | ✅ **EM-T4a-1 MERGED #570 and EM-T4a-0 MERGED #572 (2026-10-02).** ✅ **EM-T4c MERGED #575 (2026-10-02).** ✅ **EM-T4e MERGED #586 (2026-10-03, migration 226).** ✅ **EM-T4b MERGED (#617, 2026-10-04), dark** (cap 0, budget `log`). **§7 Tier 1 items 2 to 5, and Graph delta.** Nine parts, each one PR: EM-T4a-0 (request jobs bind a tenant, first), EM-T4a-1 to EM-T4a-4 (sessions across I/O), EM-T4b (cap and budget), EM-T4c (401 retry), EM-T4d (delta in shadow) and EM-T4e (§7 item 4). See §10.4.6. | See §10.4.6. |
| **EM-T5** | 🟢 build · 🔴 real mail | ✅ **MERGED #569, dark (2026-10-02).** **Triage on Jev.** This is CP-13e (`customer_console.md` §6A.14, and §2.1 here). It is built to shadow mode. Real mail waits for the H-166 owner acts. | See §10.4.4. |
| **EM-T5b** | AGENT-SAFE build · OWNER "go" for `on` on a box and for the merge of EM-T5b-3 | ✅ **EM-T5b-1 and EM-T5b-2 (narrowed to the rule match) MERGED #576 (2026-10-02).** The owner gave the "go" for `email.rule_match=on` for all organizations (§10.2, decisions (a) to (d)). 🔨 **EM-T5b-2 in full (the thread status, the cold check and the sender pin in `on`) BUILT, NOT MERGED (`email-t5b2`, 2026-10-03).** **The rules engine and every triage decision on Jev, with no LLM path** (D-EM-7 to D-EM-9). Four parts: EM-T5b-1 (the questions rebuilt, multi-rule in shadow), EM-T5b-2 (`on`, undecided on failure, no rules-model choice), EM-T5b-3 (hardcode, and delete the old path) and EM-T5b-4 (the "not sorted yet" notice). See §10.4.8. | See §10.4.8. |
| **EM-T6** | 🟢 AGENT-SAFE | **SPECIFIED (2026-10-02). EM-T6a MERGED #577. EM-T6b MERGED #580. EM-T6d parts 1 and 2 MERGED #579 and #581. EM-T6c MERGED #615 (2026-10-04).** ✅ **EM-T6e MERGED #619 (2026-10-04).** **Guided mailbox onboarding.** A range of 0 to 6 months at the first connect, an import newest first in batches with real progress, and a resume after a pause. A limit of 500 MB for each mailbox, with removal from Metorite only. A guided setup that ends at AI rules. Five parts, each one PR: EM-T6a to EM-T6e. See §10.4.7. | See §10.4.7. |
| **EM-T7** | 🟢 AGENT-SAFE | ✅ **MERGED #574 (2026-10-02).** **Automatic reply drafting is OFF for a new mailbox (D-EM-6).** Migration 224 sets the column default to false. The model, the GET and the presets agree with it. See §10.4.9. | See §10.4.9. |
| **EM-T9** | 🟢 AGENT-SAFE · full review (data) · 🔴 live check | ✅ **MERGED #643 (2026-10-05).** The live check of the owner is still open.** **A LIVE defect: a file of 3 MB or more on an Outlook draft is lost with no error.** An upload session for a large file, and a failed file stops the send. See §10.4.10. | See §10.4.10. |
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
  For Gmail, EM-G8 adds help for a Workspace admin in its place (§12.3.10).
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

**Status.** ✅ EM-T4a-1 MERGED (#570, 2026-10-02). ✅ EM-T4a-0 MERGED (#572, 2026-10-02). ✅ EM-T4c MERGED (#575, 2026-10-02). ✅ EM-T4f MERGED (#578, 2026-10-02). ✅ EM-T4e MERGED (#586, migration 226, 2026-10-03).

✅ EM-T4d MERGED (#614, 2026-10-04, no migration, dark: `email_outlook_delta=off`). ✅ EM-T4b MERGED (#617, 2026-10-04, dark: cap 0, budget `log`). ✅ EM-T4a-2 PR-A MERGED (#621, 2026-10-04).

EM-T4a-2 PR-B, EM-T4a-3 and EM-T4a-4 are not built. The audit of 2026-10-04 narrowed EM-T4a-2 to two PRs (see its section). The audit of 2026-10-02 read each anchor below in the code at `ea9467a9`. EM-T4 has nine parts, and each part is one PR.

**Gate.** 🟢 AGENT-SAFE: the code of each part, with each new setting at its default. 🔴 OWNER-GATE (`enforcement-flip`): `EMAIL_LLM_BUDGET_MODE=enforce` on a box, and any `EMAIL_OUTLOOK_DELTA` value other than `off` on a box. The dev-phase window of CLAUDE.md §3a does NOT open `EMAIL_LLM_BUDGET_MODE=enforce`. `enforce` holds back triage and drafts from a paying mailbox. So it is a product limit, and the owner decides it.

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
- Phase (f) holds one session across `litellm.aembedding` (`scheduler.py:423-429`, `email_embeddings.py:73`). It does nothing while `email_semantic_search_enabled` is false, which is its default (`settings.py:690`).
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

**Measured state: the model calls (verified at `012483a43`, 2026-10-04).**

- 11 sites call `core._llm_json` (`core.py:726`), and its one model await is `core.py:749`. It has no `account_id` parameter (`core.py:726-732`).
- `decide_features` has no `_ask`. The helper that sends the `decide` requests is `_ask_all` (`decide_features.py:489`).
- Nothing limits these calls across mailboxes. The gateway runs as one uvicorn process with no `--workers` (`deploy/hostinger/acb-gateway.service:13`).
- `customer_console.md` CP-7 owns the credit caps for each member (its §4.5). The EM-T4b budget counts model requests. It stops a loop that runs away, and it never prices anything.

The four triage decisions. Each one has an `on` path and an old path. The old path runs in `off` and in `shadow`. On the box only the rule match runs `on`. The other three stay `off` until the owner's go (EM-T5b-2 in full, #593).

| Decision | `on` | The old path |
|---|---|---|
| Rule match. `email.rule_match=on` is live for all organizations | `engine.py:761` `ask`, then `_ask_all` (`decide_features.py:526-530`). The Router path. The slot waits | `engine.py:860` and `:946`, `_llm_json` |
| Thread status | `replyzero.py:560` `ask` | `replyzero.py:675` `_llm_json`, up to two tries |
| Cold check | `senders.py:1339` `ask` | `senders.py:1353` `_llm_json` |
| Sender pin. The caller is `runner.py:1246` | `learning.py:161` `ask` | `learning.py:183` `_llm_json` |
| Shadow, all four | — | `shadow` (`decide_features.py:687`) starts the task at `:728`. The task tries for a slot, or it skips |

The other model calls. "In" means inside the automation scope of EM-T4b item 5.

| Call | The model await | Function | Scope |
|---|---|---|---|
| Template fill | `actions.py:305` | `_render_template` (`:278`) | In |
| Drafter | `drafting.py:932` (stream) and `:938` | `_llm_draft_reply` (`:756`) | In from the rule DRAFT action (`actions.py:521`), the follow-ups (`followups.py:236`) and Process past. Out from `/draft-reply` (`drafting.py:1786`) and the reply mode of `/compose-assist` (`drafting.py:1924`) |
| Consult plan | `drafting.py:1152` `_llm_json` | `_draft_consult_plan` (`:1108`) | As the drafter |
| Specialist consults | `drafting.py:1577` `run_agent`, up to 90 s each, one after the other | `_orchestrate_draft` (`:1495`) | As the drafter |
| MAF drafter | `drafting.py:1330` `run_agent` | `_draft_via_maf_agent` (`:1290`). Nothing calls it | Delete it in the build |
| `/compose-assist` | `drafting.py:1082` and `:1088` | `_llm_compose_assist` (`:969`) | Out |
| Reply memories | `drafting.py:484` `_llm_json` | `_llm_extract_reply_memories` (`:455`) | In |
| Learned style | `drafting.py:657` | `_llm_summarize_writing_style` (`:644`) | In |
| Digest | `digest.py:525` `_llm_json` | `_digest_brief` (`:488`) | In |
| Voice profile build | `voice_profile.py:244` and `:281` `_llm_json` | `_llm_observe_batch` (`:215`) and `_llm_synthesize_profile` (`:253`) | In |
| Voice sample | `voice_profile.py:699` | `sample_voice_profile` (`:673`) | Out |
| Writing style | `assistant.py:629` | `_llm_writing_style` (`:613`) | Out |
| Rule generation | `rules.py:619` `_llm_json` | `_llm_generate_rules` (`:588`) | Out |
| Embeddings | `email_embeddings.py:87` `aembedding` | `_embed_batch` (`:78`), from `scheduler.py:357`. Off by default (`settings.py:690`) | In from the cleanup backfill only. See the follow-ups |
| Chat | `chat.py:226` `run_agent_stream` | `ai_chat` (`:144`) | Exempt (EM-T4b item 15) |
| Mem0 | `drafting.py:1528` (`remember`, two calls), `drafting.py:623`, `drafting.py:1628` and `assistant.py:691` (`add_memories_background`) | `_orchestrate_draft`, `_learn_from_sent` and `generate_writing_style` | Out of EM-T4b (its item 20) |

**Measured state: the 401.**

- Each provider writes the bearer token into its client once (`outlook.py:126-137`, `gmail.py:180-191`). Only `authenticate()` refreshes on a 401.
- `_graph_send` retries one 429 (`outlook.py:139-161`). Outlook makes 36 calls on an httpx client, and only 3 go through `_graph_send`. Gmail makes 22 and has no wrapper.

**Measured state: delta.**

- `sync_messages` sets `history_id = None` (`outlook.py:1298`). So each poll sweeps 6 system folders and each user folder (`outlook.py:1341-1404`).
- Commits `55bec57f` and `a350b578` turned delta off on 2026-06-23. A seeded inbox token returned 0 changes in each cycle while new mail arrived. Nobody found the cause.
- The dead branch (`outlook.py:1300-1340`) has four defects. It keeps only the bare `$deltatoken`, and it sends `$top`. It reads one page with no `@odata.nextLink`, and it moves each `@removed` item to TRASH.
- The cursor column is `last_history_id TEXT` (`17_email_accounts.sql:25`). It can hold a JSON map of links, so delta needs no migration.

**Measured state: §7 item 4.** The row said "items 2 to 5" but named delta in place of item 4. Item 4 is still real:

- `list_accounts` runs one COUNT for each account (`transport/accounts.py:69`, `transport/accounts.py:242`).
- `_load_rules` reads the actions once for each rule (`automation/rules.py:119-121`). The engine calls it once for each email.
- `email_thread_status.last_message_id` has no index (`27_email_reply_tracking.sql:18`). No index on `email_messages` starts with `(account_id, thread_id, received_at)`.

**The split pattern (decided).**

Each part uses (A) of §10.4.2. Read in one `_tenant_session()` block. Call the model or the provider with no session open. Write in a new block, with no `commit()`. A write carries each value that the provider returned, for example the new id after a move. A best-effort write inside a block runs in `_savepoint`.

Option (B), a listener on the seam, stays rejected.

**One fence for every part (R7).**

The fence is `test_no_session_is_open_during_the_provider_calls` in `tests/unit/test_email_scheduler_tenancy.py:665`. It counts the open `tenant_session` blocks. Its watched fake provider and its watched model call fail the test when a block is open during a call. A part that adds a provider call or a model call to the sync path adds that call to the watched fake.

That fence cannot see a block in `routes/email`. So EM-T4a-2 has its own fence in `tests/unit/test_email_automation_tenancy.py` (see its section). This paragraph named `tests/unit/_io_watch.py` and `tests/unit/test_email_no_session_across_io.py` until 2026-10-04. Neither file exists, so do not cite them.

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

**Status (2026-10-04).** ✅ PR-A MERGED #621 (2026-10-04). PR-B is not built.

The audit of 2026-10-04 read each anchor below in the code at `04a64ba4d`. The part ships as two PRs, and PR-A goes first. It adds no setting, no flag and no migration. The PR-A notes follow the Verify block.

**Gate.** 🟢 AGENT-SAFE for the whole part.

**Two PRs.**

- **PR-A.** No session is open across the status ask of `_mark_thread_replied`. `recompute_thread_status` splits into a read step, an ask step and a write step. The write step carries the guard of item 6.
- **PR-B.** The classify core in `_run_rules_job` (the runner loop) and in the gap loop of `_maybe_classify_threads`. If the diff of PR-B passes about 600 lines, it splits in two:
  - **B1.** `off`, `shadow`, and the rule match in `on`.
  - **B2.** The thread status in `on`. B2 must merge before the owner turns `email.thread_status` on.

**Scope.** The paths are under `routes/email/automation/`, at `04a64ba4d`.

1. Split each function that reads and then asks a model. The read step takes `db`. The ask step takes no `db`.
2. The rule match: `classify_matches` (`engine.py:1415`) with its two match helpers, `_match_email_to_rule` (`:1267`) and `_match_email_to_rules_multi` (`:1335`). Their asks are `_decide_rule_match` (`:750`), `_llm_pick_rule` (`:807`) and `_llm_pick_rules` (`:892`).
3. The thread status of the resolver: `resolve_conversation_status_matches` (`replyzero.py:1230`), with `status_before_match` (`:1122`), `_resolve_on` (`:1169`) and `_determine_status_of` (`:1044`).
4. The thread status of a reply: `recompute_thread_status` (`replyzero.py:1499`). It asks at `:1548` and writes at `:1575`.
5. `_mark_thread_replied` (`replyzero.py:1583`, `@automation_job` at `:1582`) is the one path that reaches `recompute_thread_status`. The runner and the gap loop write the status through `project_reply_status_from_matches`, which asks no model. Block A (`:1611-1630`) is open across the ask. Block B (`:1647`) is open across `set_labels`, and EM-T4a-3 owns it.
6. **The guard of the status write** (decided 2026-10-04). The read step records the newest non-NULL `received_at` of the thread in this mailbox. The write step writes the status only when no message of that thread is newer. The test counts only messages outside the `sent` and `drafts` folders.
   - The folder test is `LOWER(COALESCE(folder,'')) NOT IN ('sent','drafts')`. The sent copy of the member's own reply started the recompute, so it must not void the write. A new inbound message must void it.
   - A message with a NULL `received_at` never voids the write. A tie (an equal `received_at`) does not void it.
   - The guard compares with the newest STORED row, never with `ctx.last_message_at`. That value is `now()` for a pending reply (`replyzero.py:1491`). The pattern is `MAX(received_at) > seen.received_at` (`runner.py:1384-1396`).
   - The guard and the upsert are ONE SQL statement. The upsert carries the `NOT EXISTS`. Today `_upsert_thread_status` (`replyzero.py:48-83`) upserts on `(account_id, thread_id)` with no guard.
   - A voided write writes no row and reconciles no labels.
7. The EM-T5 shadow helper wraps the ask step only. In `on`, `decide_features.ask` is the ask step.
8. The composed forms that take `db` stay for the request paths of EM-T4a-4. These paths are `run_rules_on_message` (`runner.py:1010`), `test_rules` (`:94`), `test_rules_recent` (`:137`) and `_process_past_emails_job` (`:1444`). A test may change a call shape. It never changes an expected value.

**Moved to EM-T4a-3 (2026-10-04).** This part named three more functions until the audit of 2026-10-04. Each one does I/O of the action tail, so EM-T4a-3 owns it:

- `_ai_confirms_sender_pattern` (`learning.py:121`) is the sender pin, and the runner calls it after the rule match (`runner.py:1246`).
- `_maybe_block_cold` (`senders.py:1400`) is the cold check, and it blocks the sender at the provider.
- `_restore_conversation_messages` (`replyzero.py:985`) moves mail at the provider, and `_determined_matches` reaches it.

**Non-goals.** No change to a prompt, a model tier or a decision. No change to the action tail, which is EM-T4a-3. No `llm_slot` around a call that is not a leaf (EM-T4b item 7). The model await stays in its slot in `_llm_json` or `_ask_all`.

**Done when.**

- The watched model gets each rule-match and thread-status ask with zero open sessions, in the runner, the gap loop and `_mark_thread_replied`.
- `_mark_thread_replied` is the one path to `recompute_thread_status`. A thread that gets a newer stored inbound message during the ask keeps its status row, and the job reconciles no labels. The next cycle decides it again.
- `test_email_classify_matches.py`, `test_email_thread_single_classification.py` and `test_email_rules_engine.py` pass with no changed expected value.
- R8: the runner and the gap loop write `email_thread_status` and `email_executed_rules` rows in org B. Org A reads none of them.

**The fence (R7).** The scheduler fence (`test_no_session_is_open_during_the_provider_calls`, `test_email_scheduler_tenancy.py:669`) cannot see a block in `routes/email`. So the fence of this part lives in `tests/unit/test_email_automation_tenancy.py`. It extends `_open_count_session` (`:751`), as `test_the_sweep_holds_no_session_across_set_labels` (`:765`) uses it. It watches the model at two leaves, and it patches each leaf once:

- `acb_llm.decide`. `_ask_all` imports it at call time (`decide_features.py:518`).
- `acb_llm.context.acompletion_with_fallback`. `_llm_json` imports it at call time (`core.py:770`).

Each module binds `_tenant_session` under its own name. So the fence patches each module that opens a block on the path. A companion test plants an ask inside a block and shows that the fence fails.

**Files.** `routes/email/automation/replyzero.py` (PR-A), `engine.py` and `runner.py` (PR-B), with the fence file `tests/unit/test_email_automation_tenancy.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_automation_tenancy.py tests/unit/test_email_classify_matches.py \
  tests/unit/test_email_thread_single_classification.py tests/unit/test_email_rules_engine.py \
  tests/unit/test_email_reply_zero.py tests/unit/test_email_thread_status_parity.py \
  tests/unit/test_email_auto_learn_gate.py tests/unit/test_email_classifier_unavailable.py \
  tests/unit/test_email_apply_and_watermark.py tests/unit/test_email_decide_shadow.py \
  tests/unit/test_email_decide_on.py tests/unit/test_email_decide_questions.py \
  tests/unit/test_email_llm_cap.py tests/unit/test_email_cold_gate_case.py -q -rs
```

With the database exported, the run shows 0 skips. At `04a64ba4d` with no database, the run shows 504 passed and 45 skipped.

Ruff: compare the count of each changed file with the base. At `04a64ba4d` the counts are `engine.py` 7, `replyzero.py` 23, `learning.py` 6, `senders.py` 13 and `runner.py` 17. A new test file has 0.

**PR-A as built (2026-10-04).**

- `replyzero.py` has three steps. `read_thread_status(db, ...)` returns a `StatusRead`, or None for a thread with no rows. `ask_thread_status(read)` takes no `db`. `write_thread_status(db, read, verdict)` takes `db` and opens no block.
- `StatusRead` holds the account, the thread, the trigger, the context, the about text, the corrections and the member. The context holds the self addresses. The ask and the write read none of them again.
- `ThreadContext.newest_received_at` is the newest non-NULL `received_at` of the stored rows. `StatusRead.seen_at` returns it, and the guard compares with it.
- `_upsert_thread_status` takes `guard` and `seen_at`, and it returns True when it wrote the row. With `guard`, the statement is `INSERT ... SELECT ... WHERE NOT EXISTS (...) ON CONFLICT ... RETURNING 1`. Without `guard`, the statement is the old one.
- `_mark_thread_replied` keeps `@automation_job`. Block A reads. A self-only thread writes its FYI row in Block A and asks nothing. The ask runs with no block open. Block W writes the status.
- A voided write logs `email.thread_status_write_voided` and ends the job. The job then builds no provider and reconciles no labels. Block B does not change, and EM-T4a-3 owns it.
- The composed `recompute_thread_status(db, ...)` runs the three steps on one `db`. Its write uses the guard too.
- PR-A adds no `llm_slot`, no setting, no flag and no migration.

**An agent decision (D16).** The read can see no row with a date. Then `seen_at` is NULL, and any dated row outside `sent` and `drafts` voids the write. That follows the order of `build_thread_context` (`ASC NULLS FIRST`). The decision of item 6 did not name this case.

**Fences (R7).** All are in `tests/unit/test_email_automation_tenancy.py`.

- `email-decision-core-no-session-across-the-ask`: `test_the_status_ask_runs_with_no_session_open` in `off`, `shadow` and `on`. The companion is `test_the_ask_fence_can_fail`.
- `email-status-write-guard`: `TestTheStatusWriteGuard` (R8, six cases), `test_the_guard_compares_with_the_newest_stored_row` and `test_a_voided_write_reconciles_no_labels`.
- Two more cases: `test_a_self_only_thread_writes_in_the_read_block_and_asks_nothing` and `test_a_spent_budget_in_the_ask_writes_nothing`.

Two fakes of `_upsert_thread_status` in `test_email_reply_zero.py` now return True, which is the new call shape. No expected value changed.

**Mutations of PR-A.** Each mutation ran against `test_email_automation_tenancy.py` on a real Postgres. After each one, `replyzero.py` came back to the same SHA-256. A name in brackets is a case of `TestTheStatusWriteGuard`.

| Mutation | Red |
|---|---|
| A block open across the ask in `_mark_thread_replied` | `test_the_status_ask_runs_with_no_session_open`, all three modes |
| The guard removed (`WHERE true`) | `[newer-inbound-in-b]` |
| The guard voids on a `sent` row | `[newer-sent-in-b]` |
| The guard voids on a `drafts` row | `[newer-draft-in-b]` |
| The guard compares with `ctx.last_message_at` | `[newer-inbound-in-b]` and `test_the_guard_compares_with_the_newest_stored_row` |
| A tie voids (`>=` for `>`) | Five of the six R8 cases, because the stored row ties with itself |
| The job reconciles the labels after a voided write | `[newer-inbound-in-b]` and `test_a_voided_write_reconciles_no_labels` |

**Verified (2026-10-04).** On a private database, the Verify block gave 563 passed and 0 skipped. The 132 files `tests/unit/test_email_*.py` gave 2564 passed and 0 skipped. The ruff counts did not change: `replyzero.py` 23 and `test_email_automation_tenancy.py` 0.

##### EM-T4a-3 — the action tail on the sync path

1. `_apply_rule_actions` (`actions.py:309`) plans, then pushes, then records. The provider calls, the template call and the draft run with no session.
2. One block then writes the mirrors, the new ids and the audit row.
3. `_reconcile_thread_labels` (`replyzero.py:678`) writes the mirror in a block. It calls `set_labels` after the block closes. The mirror stays first.
4. `_maybe_send_follow_up_reminders` reads in one block. It labels, fetches and drafts with no session. It stamps each thread in its own block.
5. `_maybe_send_digest` builds the digest in one block and sends with no session. It stamps `last_digest_at` in a new block after the send returns.
6. `_bulk_reconcile_provider` calls `bulk_apply` and sleeps with no session. It writes the new ids and the reverts in a block after each try.
7. `_ensure_subscription` reads in one block, calls Graph with no session, and writes in a second block.
8. `_ai_confirms_sender_pattern` (`learning.py:121`) reads in one block and asks with no session (moved from EM-T4a-2, 2026-10-04).
9. `_maybe_block_cold` (`senders.py:1400`) asks and blocks the sender at the provider with no session (moved from EM-T4a-2).
10. `_restore_conversation_messages` (`replyzero.py:985`) moves mail with no session and writes the new ids in a block (moved from EM-T4a-2).

**Non-goals.** No change to which actions run. Automation writes stay provider-first (§2).

**Done when.**

- The watched fakes get each call with zero open sessions in the six functions.
- An Outlook move that gives a new id writes that id to `email_messages` and to the audit row.
- A failed provider action writes `FAILED` and no mirror, as `test_email_rule_action_failures.py` states today.
- A digest send that raises leaves `last_digest_at` unchanged.
- R8: the mirrors, the audit rows and the stamps land in org B. Org A reads none of them.

**Files.** `routes/email/automation/actions.py`, `drafting.py`, `replyzero.py`, `followups.py`, `senders.py`, `learning.py` and `runner.py`, with `routes/email/digest.py` and `transport/sync.py`.

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

**Status.** ✅ MERGED #617 (2026-10-04), dark. Review round 1 fixed four findings and recorded three more on 2026-10-04. The audit narrowed it on 2026-10-04 and verified it at 012483a43. The build keeps the defaults (budget `log`, cap 0) and makes no box change. The as-built notes and the review round 1 note follow the Verify block.

**Order.** EM-T5 merged (#569), so item 2 of the EM-T4 order no longer holds this part. EM-T4a-2 splits functions in `engine.py` and `replyzero.py`, and items 12 and 13 below touch the same files. So EM-T4a-2 must not run in parallel with EM-T4b.

1. Add `apps/services/email_ingestion/email_ingestion/llm_cap.py`. It holds one `asyncio.Semaphore` for the process, the automation scope and one context manager, `llm_slot()`.
2. Add three settings to `acb_common/settings.py`. `email_llm_concurrency` defaults to 0, and 0 means no cap (item 18). `email_llm_daily_calls` defaults to 2000.
3. `email_llm_budget_mode` is `off`, `log` or `enforce`. It defaults to `log`.
4. An automation scope marks the calls that the cap and the budget bind. A ContextVar holds it, and the scope carries the account id of the mailbox. `llm_slot()` reads the account id from the scope. `_llm_json` keeps its signature.
5. These functions open the scope:
   - `as_mailbox_owner` (`scheduler_hooks.py:53`). It wraps `process_new_mail` (`:122-123`) and the hooks of the thread status, the digest and the follow-ups (`:307-309`).
   - `_run_rules_job` (`runner.py:1666`) and `_process_past_emails_job` (`runner.py:1442`).
   - `_reclassify_reply_zero_job` (`replyzero.py:2071`) and `_mark_thread_replied` (`replyzero.py:1576`).
   - `_build_voice_profile_job` (`voice_profile.py:338`) and `_learn_from_sent` (`drafting.py:531`).
   - The cleanup jobs `_sweep_job` (`cleanup.py:703`) and `_backfill_and_clean_job` (`cleanup.py:869`).
   - `_maybe_classify_threads` (`replyzero.py:1834`), the Reply Zero backfill. Each caller is a background path. On a mailbox with no status row, the Reply Zero list starts it as a `BackgroundTask` (`replyzero.py:2344`). Before review round 1 that task ran outside each scope (finding C).

   These stay outside the scope, because a member drives each one:
   - `/compose-assist` (`_compose_assist_run`, `drafting.py:1863`) and `/draft-reply` (`draft_reply_smart`, `drafting.py:1765`).
   - The voice sample (`voice_profile.py:673`), the writing style (`assistant.py:613`) and the rule generation (`rules.py:588`).
   - The chat (`chat.py:144`).
6. Outside the scope, `llm_slot` takes no slot and counts nothing. A member who asks for a draft never waits behind the sync loop.
7. A slot wraps only a leaf, the model await itself. A task started inside a held slot runs under that slot, with no permit and no count of its own. So a nested `llm_slot` takes no second permit, and a cap of 1 cannot deadlock. The AST fence keeps each call that is not a leaf out of a slot (review round 1, finding A). Its one exception is the gather of the `decide` requests in `_ask_all` (item 19).
8. The budget counts model requests for each mailbox for each UTC day. The key is `key("email-llm", account_id, <date>)` from `tenant_redis`.
9. The helper uses `incr` and an `expire` of 2 days. It binds `organization_scope(current_tenant())` for the call. A call that reaches no model gives its count back with `decrby` (review round 1, finding B). That covers a refusal in `enforce`, a body that raises and a body that times out.
10. In `log` mode, each call runs, because `log` never refuses a call. The budget logs `email.llm_budget_count` when a mailbox reaches 50% and then 100% of the limit (R-6). Each line logs once a day for each mailbox. Past the limit, `email.llm_budget_exceeded` logs once a day for each mailbox. Each of these lines logs after a call that succeeded, and never for a count that went back.
11. In `enforce` mode, a call past the limit raises `LLMBudgetExhausted`, a new exception in `llm_cap.py`. It makes no model call.
12. The rule match at the budget, in `enforce`, by mode:
    - With `email.rule_match=on` (live), `ask` makes no Router call. It logs `decide.unavailable` with `decide_reason=budget` and returns None. `_decide_rule_match` then raises `DecisionUnavailable` (`engine.py:767`).
    - In `off` and `shadow`, `_llm_json` raises `LLMBudgetExhausted` in the `_old` path. Its handler (`engine.py:877-881`, and `:970-973` for multi-rule) turns it into `LLMUnavailable`.
    - Either way the runner leaves `rules_processed_at` NULL (`runner.py:1777-1785`). So the mail stays undecided (D-EM-8), and a later cycle retries it.
13. `_llm_determine_thread_status` raises `LLMBudgetExhausted` again (its handler is `replyzero.py:682-684`). It does not write its `· auto` fallback for it.
14. Each Redis command of the budget waits 0.25 s at most. A failure or a timeout opens a breaker for 60 s, and logs `email.llm_budget_unavailable` once. While the breaker is open, the budget counts nothing and the call runs, also in `enforce`. The cap still binds (review round 1, finding D).
15. `core._llm_json` (`core.py:749`) and each other model await in the tables above enter `llm_slot`. Two kinds of call are exempt. A member drives `run_agent_stream` (`chat.py:226`), and Mem0 is out of this slice (item 20).
16. In `shadow`, the `_ask_all` task (`decide_features.py:698`) takes a slot only when one is free. If none is free, it logs `decide.shadow_skipped` with `reason=cap` and makes no call. In `on`, `_ask_all` waits for a slot, as `_llm_json` does.
17. `llm_slot` wraps only the model await itself, the leaf call. It never wraps an enclosing function. So a parent holds no permit while it awaits a child. Each child task takes its own permit at its own model await. If a parent held its permit at a cap of 1, its children could never get one. That is a deadlock.
18. The cap has an off position. `EMAIL_LLM_CONCURRENCY=0` means no cap. 0 is the shipped default, so the cap ships dark (CLAUDE.md §4). The cap is one `asyncio.Semaphore` in the process. That is correct, because the gateway runs one uvicorn with no `--workers` (`deploy/hostinger/acb-gateway.service:13`). With the cap set, a call that waits longer than 1 second logs `email.llm_cap_wait` with the wait in ms.
19. One `_ask_all` call holds one permit, because one bound covers all of its requests (`decide_features.py:512-518`). The slot wait counts inside `ON_BOUND_S` (10 s, `decide_features.py:137`). A wait past the bound leaves the mail undecided (D-EM-8), and a later cycle retries it. The budget counts each request that `_ask_all` sends, because the cost follows the requests.
    - One call sends one request for each 16 questions (`QUESTION_LIMIT`, `decide_features.py:179`).
    - The rule match asks one boolean for each candidate that is not a conversation rule, and the choices `conv` and `best` (`engine.py:506-561`). So one request holds up to 14 booleans with both choices. The presets give 8 questions, so one email sends one request.
    - The thread status, the cold check and the sender pin each send one request with one question.
20. Mem0 is OUT of this slice. Mem0 runs only with `MEM0_ENABLED`, which defaults to false (`mem0_enabled`, `acb_common/settings.py:676`). Turning it on is an open owner question. So the Mem0 sites of the table take no permit and count nothing.

**Non-goals.** No credit budget, no price and no token count, because CP-7 owns them. No cap across processes, because the box runs one, and no cap for each organization (the known limit below). No UI. No change to `acompletion_with_fallback`, and no Mem0 (item 20).

**Known limit for M1: the noisy neighbour.** The cap is global for the process. It is not a cap for each organization. So the import of one organization can hold every slot, and the mail of each other organization waits. This is a known limit for M1, "a second org can exist safely".

**Owner decisions at the flip to `enforce`.**

1. The go for `enforce`.
2. The limit. 2000 is a guess (R-6).
3. Whether "Process past emails" counts. Its ceiling is also 2000 (`runner.py:977`), so one run can spend the budget of a whole day.
4. Q-MB-1 (§11.8), because the total of an organization is the count of its mailboxes times the limit.

Agent work that must land BEFORE the flip (review round 1, finding G). Neither item binds in `log`, the shipped mode.

5. **The template fill at the budget.** `_render_template` (`actions.py:279-318`) catches `LLMBudgetExhausted` and returns the raw template. In `enforce`, a LABEL then gets a literal `{{...}}` name. A REPLY, DRAFT, FORWARD or SEND gets raw placeholders. A FORWARD note or a SEND subject can reach a third party, and the runner then stamps the row. The fix must decide what a rule with several actions does when one action cannot render.
6. **One warning a day, not one a cycle.** In `enforce`, a spent mailbox logs WARNING lines on each cycle until UTC midnight. The lines are `decide.unavailable`, `email.classify_unavailable_skip` and `email.mark_thread_replied_failed`. Check the budget once for each job, and log once.
7. **A timeout gives back spend that the model billed.** On a timeout, `_ask_all` gives back all its requests, also the ones that already answered (`llm_cap.py:303-309`). The drafter consult does the same for a whole agent run. In `enforce`, a mailbox whose calls keep running slow then never reaches its limit. Count the answered requests before the bound cancels them. In `log`, the same rule makes the count a little low for slow calls (re-verify of round 1, P2).
8. **Shadow traffic shares the budget.** In `shadow`, the decide requests and the old call that acts spend one budget. In `enforce`, a shadow request can take the last unit, and the call that acts is then refused. Decide whether a shadow request counts.

**Follow-ups.**

- Mem0 joins the cap in its own slice when the owner turns it on.
- Phase (f) runs `_embed_batch` from `_sync_account` (`scheduler.py:1225`), outside each scope of item 5. Only the cleanup backfill reaches it inside a scope. The slice that sets `email_semantic_search_enabled` on a box opens the scope for phase (f).
- The build deletes `_draft_via_maf_agent` (`drafting.py:1290`), because nothing calls it. Its entry in `_RUN_AGENT_WITHOUT_SESSION_USER` (`test_background_ai_member.py:825-828`) goes with it.

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
- **F1.** With `email.rule_match=on`, a call past the limit in `enforce` makes no Router call. `ask` returns None with `decide_reason=budget`, and the runner leaves `rules_processed_at` NULL.
- **F2.** The same case with the feature `off` takes the `_old` path and gets the same NULL stamp.
- **F3.** With a cap of 1, a gather of two child model calls never runs two at once. It completes within a test timeout, with no deadlock. A slot wraps only a leaf. A task started inside a held slot runs under that slot. The fence keeps each call that is not a leaf out of a slot.
- **F4.** With the shipped defaults (`EMAIL_LLM_CONCURRENCY=0`), no call takes a permit.
- **F5.** One `_ask_all` call takes one permit, and the budget counts the requests that it sends (item 19).
- **F6.** A call from `/compose-assist`, `/draft-reply`, the voice sample or the chat takes no permit and counts nothing.
- **F7.** In `log`, the 50% line and the 100% line each fire once for each mailbox for each day.
- **F8.** The AST fence covers `routes/email/**`, `email_ingestion/**` and `gateway/decide_features.py`. Its callee list is `acompletion_with_fallback`, `acompletion_stream_text`, `aembedding`, `run_agent` and the `decide` facade (`ask`, `shadow` and `_ask_all`). A companion test proves that it can fail.

**Files.** A new `email_ingestion/llm_cap.py`, `acb_common/settings.py`, `routes/email/core.py` and `gateway/decide_features.py`. The direct sites of the tables above, and the scope functions of item 5 with `scheduler_hooks.py`. `routes/email/automation/engine.py` and `replyzero.py`, for items 12 and 13. The test is a new `tests/unit/test_email_llm_cap.py`, and `test_background_ai_member.py` drops the entry of the deleted MAF drafter.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_llm_cap.py tests/unit/test_tenant_redis.py \
  tests/unit/test_email_classifier_unavailable.py tests/unit/test_email_apply_and_watermark.py \
  tests/unit/test_email_reply_zero.py tests/unit/test_email_decide_shadow.py \
  tests/unit/test_email_decide_on.py tests/unit/test_email_decide_questions.py \
  tests/unit/test_background_ai_member.py tests/unit/test_email_layering.py \
  tests/unit/test_email_process_past_cost_guard.py tests/unit/test_email_rulepath_draft_parity.py \
  tests/unit/test_email_follow_up_scan.py tests/unit/test_email_digest.py \
  tests/unit/test_internal_ai_is_routed.py -q -rs
uv run ruff check apps/services/email_ingestion apps/services/gateway/gateway packages/acb_common tests/unit
```

With the database exported, the run must show 0 skips. The baseline at `012483a43` with no database was 480 passed and 31 skipped, over the 14 files that exist today. `test_email_llm_cap.py` is new.

**As built (2026-10-04).** Branch `email-llm-budget`. It ships dark, with the cap at 0 and the budget mode at `log`. It takes no migration.

1. `email_ingestion/llm_cap.py` holds the scope, the cap and the budget. It imports no `gateway` module.
2. `automation_job` is a decorator. It opens the scope for a job whose first argument is the account id. The signature of the job does not change.
3. The nine jobs of item 5 carry `automation_job`. `as_mailbox_owner` opens the scope beside `job_member_scope`. Review round 1 added the ninth, `_maybe_classify_threads`.
4. `llm_slot(requests=1, wait=True, settle=True)` takes the permit first, then counts, then runs the call. A call that finds no free permit counts nothing. The slot yields a `Charge`, which gives the count back and logs the marks.
5. The re-entrant guard covers this task and each task that a held slot starts. Such a slot takes no second permit and counts nothing. So the guard cannot deadlock.
6. Each Redis command of the budget waits 0.25 s at most (`BUDGET_REDIS_TIMEOUT_S`). It waited 2 s before review round 1. A failure opens the breaker of item 14 (`BUDGET_BREAKER_S`).
7. A limit under 1 means no limit. An unknown mode reads as `log`, and `email.llm_budget_mode_refused` logs once.
8. `_ask_all` holds one slot around the gather of its requests. In `shadow` the slot does not wait. `NoFreeSlot` then logs `decide.shadow_skipped` with `reason=cap`. The gather returns each failure and never raises. So `_ask_all` passes `settle=False`, and it settles the charge after the slot with the count of failed requests.
9. Item 12 needed no change in `engine.py`. Each `_old` handler already turns each failure into `LLMUnavailable`.
10. Item 13: `recompute_thread_status` passes `LLMBudgetExhausted` up. So `_mark_thread_replied` logs its failure and writes nothing.
11. The build deletes `_draft_via_maf_agent` and `_strip_draft_markers`, its one helper. Nothing else called either of them.
12. `tests/conftest.py` sets `EMAIL_LLM_BUDGET_MODE=off` for the test run. With the `log` default, the unit suites wrote real keys into the Redis of a dev machine. The new suite sets each mode itself, over a fake client under the real `TenantRedis`.

**Anchors that differed from this section.**

- The section says that `test_email_layering.py` forbids a `gateway` import in `llm_cap.py`. That test reads `scheduler.py` only. The fence is `test_the_cap_module_does_not_import_the_gateway` in the new suite.
- The Files line names `engine.py` for item 12. The build needed no change there (point 9).

**Known behaviour, recorded.**

- In `off` and `shadow`, the thread-status resolver treats a spent budget as each other failure. It keeps the match for each message, and the runner stamps the row. Only the rule match leaves a row undecided at the budget.
- In `log`, each model call in the scope sends two Redis commands, `incr` and `expire`. A call that gives its count back sends a third, `decrby`.
- While the breaker is open, `enforce` fails open. Each call runs and counts nothing, as on a Redis error before review round 1. The breaker skips a give-back too, so a call that failed then can stay counted.
- A refusal in `enforce` gives its count back. So the process keeps a record of the refusal line, and `email.llm_budget_exceeded` logs once a day for each mailbox. A restart can log it once more.
- With calls at one time, a mark or the exceeded line can go unlogged. This happens when the call that crossed it fails, and a parallel call already passed it.

**Fences.** Each line of "Done when" has a test in `tests/unit/test_email_llm_cap.py`.

| Line | Test |
|---|---|
| Done when 1 | `test_a_cap_of_two_never_runs_more_than_two_calls_at_once` |
| Done when 2 | `test_a_nested_slot_completes_with_a_cap_of_one` |
| Done when 3 | `test_a_call_outside_the_scope_takes_no_slot_and_counts_nothing` |
| Done when 4 | `test_enforce_refuses_call_2001_before_the_model` |
| Done when 5, F1, F2 | `TestTheRunnerAtTheBudget` (R8, three cases) |
| Done when 6 | `test_log_runs_call_2001_and_logs_exceeded_once` |
| Done when 7 | `test_the_key_of_one_mailbox_id_differs_between_two_orgs` |
| Done when 8 | `test_with_redis_down_the_call_runs_and_the_cap_still_binds` |
| Done when 9 | `test_a_full_cap_skips_the_shadow_with_no_extra_wait` |
| Done when 10, F8 | `test_each_model_await_sits_inside_llm_slot`, and the four companion tests |
| Done when 11 | `test_tenant_redis.py`, with no new allowlist entry |
| F3 | `test_two_children_at_a_cap_of_one_never_overlap_and_never_deadlock` |
| F4 | `test_with_the_shipped_defaults_no_call_takes_a_permit` |
| F5 | `test_one_ask_all_takes_one_permit_and_counts_each_request` |
| F6 | the four tests that end in `takes_no_permit_and_counts_nothing` |
| F7 | `test_log_counts_at_fifty_and_a_hundred_percent_once_a_day` |
| Review A, item 7 | `test_the_fence_finds_a_call_in_a_slot_that_is_not_a_leaf` (five shapes) |
| Review A, item 7 | `test_the_one_exception_is_the_gather_of_decide_in_ask_all` and `test_the_exception_is_narrow` |
| Review A, item 7 | `test_the_fence_fails_on_the_real_drafter_with_a_slot_around_a_non_leaf` (ITEM1-b and the drafter) |
| Review B, items 9 and 10 | `test_a_call_that_fails_gives_its_count_back_and_logs_no_mark` and `test_a_call_that_times_out_gives_its_count_back` |
| Review B, item 11 | `test_enforce_gives_the_refused_count_back_and_logs_once_a_day` |
| Review B, item 19 | `test_ask_all_counts_only_the_requests_that_got_an_answer` (three cases) |
| Review C, item 5 | `test_the_cold_start_backfill_of_reply_zero_opens_the_scope` and `test_each_mailbox_job_opens_the_scope` |
| Review D, item 14 | `test_a_redis_that_hangs_costs_the_bound_once_and_the_breaker_closes_after_60_s` |

**Mutations (R7).** Each row changed one place in the code. The named tests went red, and the file went back to its exact SHA-256 before the next row. 29 rows, 29 red. Review round 1 added the last eight rows.

| Fence | Mutation | Red |
|---|---|---|
| Done when 1 | `llm_slot` takes no permit | 1 test |
| Done when 2 | no re-entrant guard | 1 test, a deadlock past its bound |
| Done when 3 | the scope is always open | 1 test |
| Done when 4 | `enforce` never refuses | 1 test |
| Done when 5, F2 | the `_old` rule path returns None for a failure | 2 tests, one R8 |
| Done when 5, F1 | `_ask_all` lets `LLMBudgetExhausted` escape | 2 tests, one R8 |
| Done when 6 | `email.llm_budget_exceeded` logs on each call past the limit | 1 test |
| Done when 7 | the key drops the account id | 1 test |
| Done when 8 | a Redis error fails the call | 1 test |
| Done when 9 | the shadow waits for a slot | 1 test |
| Done when 10, F8 | `_llm_json` loses its slot | 1 test |
| F8 | the fence sees no slot | 2 tests |
| Done when 11 | `llm_cap.py` imports `redis` | `test_no_direct_redis_client_outside_the_wrapper` |
| F3 | the scope holds one permit for all of its calls | 1 test |
| F4 | the shipped cap is 4 | 2 tests |
| F5 | `_ask_all` counts one request | 1 test |
| F6 | a draft reply opens the scope | 1 test |
| F7 | the 50% and 100% lines fire on each call | 1 test |
| Item 13 | the status call writes its `· auto` fallback | 1 test |
| Item 5 | `_run_rules_job` opens no scope | 3 tests, one R8 |
| Item 5 | `as_mailbox_owner` opens no scope | 1 test |
| Review A | ITEM1-b: a slot around the gather of `_orchestrate_draft` | `test_each_model_await_sits_inside_llm_slot` |
| Review A | a slot around the `_llm_draft_reply` call of `_orchestrate_draft` | `test_each_model_await_sits_inside_llm_slot` |
| Review B | a body that raises keeps its count | 2 tests |
| Review B | a refusal in `enforce` keeps its count | 4 tests, two R8 |
| Review B | `_ask_all` keeps the count of a failed request | 2 tests |
| Review B | the marks log before the call | 6 tests |
| Review C | `_maybe_classify_threads` opens no scope | 2 tests |
| Review D | no breaker, and a bound of 2.0 s | 2 tests |

**Verified (2026-10-04, a private database).** The Verify block gave 565 passed and 0 skipped. All the `test_email_*.py` suites, with the seam and tenancy fences, gave 2603 passed. The 2 skips there are the two `test_tenant_coverage.py` tests that need `DATABASE_URL`, which `scripts/dev_db.sh` does not set on purpose.

**Review round 1 (2026-10-04).** An independent verifier passed the slice with findings, and an adversarial reviewer approved it with findings. The orchestrator recorded a decision for each one. This round built four fixes and recorded the rest. The branch rebased onto `5e268c766` (#614) first, and onto `3d11922c2` (#615) at the end. Only the docs had conflicts.

| Finding | Fix or record | Fence |
|---|---|---|
| A. A task started in a held slot skips the cap. ITEM1-b survived the old fence. | The hold stays, because it stops a deadlock at a cap of 1. The fence now refuses each call in a slot that is not a leaf. The one exception is the gather of `_ask_all`. Item 7 and F3 say so. Two slot bodies of `drafting.py` and one of `email_embeddings.py` moved their non-leaf calls out. | `test_the_fence_finds_a_call_in_a_slot_that_is_not_a_leaf`, `test_the_exception_is_narrow`, `test_the_fence_fails_on_the_real_drafter_with_a_slot_around_a_non_leaf` |
| B. A call that failed still used the budget, so an outage looked like a busy mailbox. | The refusal check and the `incr` stay before the call. A refusal, a raise and a timeout give the count back with `decrby`. `TenantRedis` gained `decrby` beside `incr`. The marks log after a call that succeeded. `_ask_all` gives back each request that got no answer. The R8 case now expects a count of 1. | `test_a_call_that_fails_gives_its_count_back_and_logs_no_mark`, `test_ask_all_counts_only_the_requests_that_got_an_answer`, `TestTheRunnerAtTheBudget` |
| C. The cold start of the Reply Zero list ran the backfill with no scope. | `_maybe_classify_threads` carries `automation_job`, because each caller is a background path. Item 5 lists it. | `test_the_cold_start_backfill_of_reply_zero_opens_the_scope` |
| D. A Redis that hung added 2 s to each model call in `log`. | The bound is 0.25 s. A failure opens a breaker for 60 s and logs once. `enforce` fails open while it is open. | `test_a_redis_that_hangs_costs_the_bound_once_and_the_breaker_closes_after_60_s` |
| E. The gateway `AGENTS.md` put the thread-status raise and the NULL stamp in one sentence. | The rule match leaves the row NULL. In `off` and `shadow` the resolver catches the thread-status raise, and the runner stamps the row. | None, a text fix |
| F. A status paragraph of this section held 10 sentences. | The rebase took the split of #614, so each status paragraph holds 5 sentences. | `ste-lint.mjs` |
| G. Two gaps bind in `enforce` only. | Recorded as items 5 and 6 of the owner list above, as agent work before the flip. | None, recorded |

**Verified after review round 1 (2026-10-04, a private database).** The Verify block gave 584 passed and 0 skipped. It gave 584 passed once more with `EMAIL_LLM_BUDGET_MODE=log` and a throwaway Redis index, and that index then held one key with a count of 1. On `3d11922c2`, all the `test_email_*.py` suites, with the seam fences, gave 2651 passed. The 2 skips there are the same two `test_tenant_coverage.py` tests.

**The re-verify of round 1 (2026-10-04): PASS with findings.** No P0 and no P1. With the shipped defaults, the live rule match gave the same decisions and the same `decide.*` lines as `main`, apart from the count. Two P2 items went to the owner list above, as items 7 and 8. This note records the P3 items, and nothing fixes them yet:

- A give-back after a timeout, or a `settle` after a failed request, can add 0.25 s past `ON_BOUND_S`. This happens once in each breaker window (`llm_cap.py:308`, `decide_features.py:557`).
- The consult's `agent_timeout` now covers the wait for a permit (`drafting.py:1522`). With a cap set, a consult can time out while it waits, and the draft then goes on without it.
- `DECRBY` on a key that expired leaves a negative key with no TTL (`llm_cap.py:383`). An `expire` that times out after the first `incr` of a day also leaves a key with no TTL. The cost is one stray key.
- A 50% or 100% line can log twice in a day, when a give-back takes the count under the mark after the line logged.
- The fence accepts any leaf in the gather of `_ask_all`, not only the decide leaf (`test_email_llm_cap.py:1326`). It also misses a coroutine built before the slot and awaited inside it, and a slot opened through an alias. No live site has these shapes. A later slice can make the fence narrow.

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

**Status (2026-10-04).** ✅ MERGED #614. Review round 1 fixed seven findings. There is no migration. The code ships with `email_outlook_delta=off`, so it changes nothing on a box.

To set `shadow` on a box is a later, separate act (gate `enforcement-flip`). Settle EM-T4d-f3 before that act. The fence is `tests/unit/test_outlook_delta_shadow.py`, with R8 in `test_email_scheduler_tenancy.py` on a private database. The As-built notes, the review round 1 note and the mutation table follow the Verify block.

1. Add `email_outlook_delta` to settings: `off`, `shadow` or `on`. The default is `off`.
2. A value of `on` resolves to `shadow` and logs `email.delta_mode_refused`. Only an edit of this section can lift that.
3. Delta runs for each swept folder through `/me/mailFolders/{id}/messages/delta`. It follows each `@odata.nextLink` to the `@odata.deltaLink`.
4. It stores each link whole, and it calls a stored link as it is. It sends `Prefer: odata.maxpagesize=100` and no `$top`.
   The delta sends the same `$select` as the sweep and no `IdType` preference, so the ids of the two reads compare.
5. The cursor in `last_history_id` is `{"v": 1, "folders": {<folder key>: {"link": <url>, "at": <UTC time>}}}`.
   The folder key is the key of the sweep: a well-known name or a folder id.
6. A cursor value that does not parse means "no cursor", and the poll does a full sweep.
7. In `shadow`, each poll runs the full sweep and the delta. It writes from the full sweep only.
8. It logs `email.delta_shadow`. The record compares NEW mail only.
   - For each folder, a message is new when its `receivedDateTime` is after `at`, the end of the last round of that folder.
   - `both`, `sweep_only` and `delta_only` count the ids of the new mail.
   - A folder in its first round counts as `seeding` and adds no count.
   - An `@removed` item adds to `removed` only.
9. A new user folder gets a cursor on its next poll. A folder that is gone loses its cursor.
   A failed folder list (`_user_sweep_folders` returns `[]` at `outlook.py:1267-1270`) keeps each link.
10. `email_outlook_delta_accounts` lists the account ids that run the mode, with a comma between ids. An empty list runs no delta.
11. A delta failure never changes the sync.
    - A raise, a timeout, a 4xx or a 5xx leaves the four sweep fields unchanged: `messages`, `full_snapshot`, `catch_up_incomplete` and `catch_up_folders`.
    - A 410 or a 400 of Graph drops that link, and the folder seeds again at the next poll.
      The reason for the 400 (review round 1, F3): a stored link is a fixed request, so Graph sends the same 400 at each poll.
    - A 400 of the token endpoint is a refused refresh (EM-T4c), not a bad link, so it keeps the link.
    - Any other failure keeps the link: a 401, a 429, a 5xx, a timeout or a raise.
    - A 403 or a 404 on a system folder that each mailbox has also keeps it.
    - The poll logs `email.delta_shadow_failed` with the folder count and the status.
12. In `shadow`, no `@removed` item becomes a `[DELETED]` marker. The reconcile reads the sweep alone.
13. Read at most 20 delta pages per folder in one poll. A folder at that limit stores its `@odata.nextLink` and continues at the next poll.
    The reason: a seed round for a 6-month mailbox can add more than 100 Graph calls under the mailbox lock.

**Non-goals.** No `on` mode. No delete rule for a tombstone. No change to Gmail, IMAP or the deep first sync. No new column and no migration. Delta keeps the floor of EM-T6a, and a reconnect keeps the cursor (D-EM-13).

**Done when.**

- Against a fake Graph, a delta round of three pages stores the last `@odata.deltaLink` of each folder, whole.
- The next poll calls each stored link as it is, with no `$top`.
- A stored bare token, or text that is not JSON, gives a full sweep and no error.
- In `shadow`, the rows written equal the rows of the full sweep alone.
- The `email.delta_shadow` record holds the three counts and the folder count. It holds no subject and no address.
- A value of `on` resolves to `shadow` and logs `email.delta_mode_refused`.
- With `off`, the poll sends zero delta requests, and `new_history_id` is `None`.
- Item 9: a new user folder seeds on its first poll. A folder that the list no longer returns loses its link. A failed folder list keeps each link.
- Item 10: with `shadow` and an empty account list, no mailbox sends a delta request. Only a listed account sends one.
- Item 11: a raise, a 410 or a 500 leaves the sweep result unchanged, and the cycle succeeds. A 410 or a 400 drops that link, and a 500 or a 429 keeps it.
- Item 12: an `@removed` item writes no TRASH row.
- Item 13: a folder at 20 pages stores its `@odata.nextLink`, and the next poll continues from it.

**Live check before any `on` (gate `enforcement-flip`).** Set `shadow` for one test mailbox. After 7 days, each `email.delta_shadow` line must show 0 "only in the sweep" for new mail. A later part, EM-T4d-2, then proposes `on` and a delete rule.

**Files.** `apps/services/email_ingestion/email_ingestion/providers/outlook.py`, `scheduler.py` and `acb_common/settings.py`. The test is a new `tests/unit/test_outlook_delta_shadow.py`.

**Fences (R7).** `tests/unit/test_outlook_delta_shadow.py` uses the real `OutlookProvider` and a fake Graph on `httpx.MockTransport`.

| Fence | The rule |
|---|---|
| `email-delta-off-no-call` | With `off`, no `/messages/delta` request, and `new_history_id=None`. An unknown value resolves to `off`. |
| `email-delta-on-refused` | `on` resolves to `shadow` and logs `email.delta_mode_refused`. |
| `email-delta-account-scope` | With `shadow` and an empty account list, no mailbox sends a delta. Only a listed account sends one. |
| `email-delta-links-whole` | A round of three pages stores the last `@odata.deltaLink` of each folder, byte for byte. The next poll calls it as it is, with `Prefer: odata.maxpagesize=100` and no `$top`. |
| `email-delta-bad-cursor` | A bare token, text that is not JSON, JSON with another version, and NULL each give a full sweep, a seed round and no error. |
| `email-delta-sweep-only` | `off` and `shadow` give equal `messages`, `full_snapshot`, `catch_up_incomplete` and `catch_up_folders`. An `@removed` item writes no TRASH row. |
| `email-delta-failure-isolated` | A raise, a 410 or a 500 leaves the sweep result unchanged, and the cycle succeeds. A 410 or a 400 drops that link, and a 500 or a 429 keeps it. |
| `email-delta-new-mail-counts` | A new message that the fake delta leaves out gives `sweep_only=1`. A message older than `at` adds no count. The record holds no subject, no address and no link. |
| `email-delta-folder-set` | A new user folder seeds on its first poll. A folder that the list no longer returns loses its link. A failed or short folder list keeps each link that the poll does not read. |
| `email-delta-floor` | The first request of a seed round filters `receivedDateTime ge <floor>`. |
| `email-delta-page-cap` | A folder at 20 pages stores its nextLink and continues at the next poll. |
| `email-delta-link-host` | Review round 1. A link that does not start with the Graph base URL gets no request and no store. The link drops, and the log names no URL. |
| `email-delta-sync-log` | Review round 1. A shadow cycle writes NULL into the sync log row. `off` writes what it wrote before. |
| `email-delta-normal-cycle` | Review round 1. A first import and a deep sync send no delta request. |
| `email-catch-up-folder-name` | Review round 1. A short user folder goes into `catch_up_folders` by its canonical name. It pins the behaviour of the sweep before EM-T4d. |

Three more checks bind this part.

- Keep `test_email_scheduler_tenancy.py::test_no_session_is_open_during_the_provider_calls` green. If a part adds a provider call outside `sync_messages`, the watched fake must watch it.
- Keep `test_email_manual_sync_parity.py:73-75` green. Phase (d) keeps `COALESCE`, so `off` keeps a stored cursor.
- R8 in `test_email_scheduler_tenancy.py`, as the app role for two organizations: a JSON cursor from `shadow` lands in the row of org B only. A later `off` cycle keeps it.

**Verify with.**

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_outlook_delta_shadow.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_email_manual_sync_parity.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_provider_401_retry.py tests/unit/test_email_import_batches.py \
  tests/unit/test_email_import_floor.py tests/unit/test_email_sync_one_at_a_time.py \
  tests/unit/test_email_sync_backoff.py tests/unit/test_outlook_labels_cache_and_429.py \
  tests/unit/test_outlook_folders_move.py tests/unit/test_db_engine_seam.py -v -rs
uv run ruff check tests/unit/test_outlook_delta_shadow.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

- The R8 cases must show PASSED, not SKIPPED.
- A diff of `ruff check` on `outlook.py`, `scheduler.py` and `settings.py` must show no new finding against the 26 that exist on 2026-10-04.

**As built (2026-10-04).**

1. `scheduler.outlook_delta_mode(account_id)` is the one reader of `email_outlook_delta` and `email_outlook_delta_accounts`. It caches the parse of each value, so a refusal logs once for each process. The list compares ids in lower case.
2. `_sync_cycle` passes `delta_shadow` to `sync_messages` for a `microsoft` row only. It then logs `email.delta_shadow` from `SyncResult.delta_report`, with the account id and counts only.
3. `delta_shadow` is a keyword-only boolean, so `on` cannot reach the provider. The abstract method in `providers/base.py` declares it. Gmail and IMAP take it and ignore it, as they ignore `catch_up`. That is one signature line in each file, and no change of behaviour.
4. The delta runs AFTER the full sweep, inside the one `sync_messages` call, so no session is open (R5). A message that arrives between the two reads adds to `delta_only`, never to `sweep_only`.
5. A round that the page cap cuts stores its next link with `"at": null`. The folder counts as `seeding` until a round ends, because a cut round cannot compare its new mail. Item 13 did not say what `at` holds in that state.
6. `_user_sweep_folder_list` returns the user folders and whether the folder list answered in full. `_user_sweep_folders` keeps its old answer for the import.
7. `_MESSAGE_SELECT` holds the `$select` that the sweep and the delta share. The dead branch of `outlook.py:1300-1340` is gone, and so is the false comment at `:1291-1297`.
8. `email_sync_log.provider_history_id` gets NULL in a shadow poll, as in `off` for Outlook (review round 1, F5). Only `email_accounts.last_history_id` holds the cursor. Nothing reads the column of the log.

**Two rules beyond the items, and two known limits.**

- ⚠️ A 403 or a 404 on Archive or a user folder skips that folder in the delta. It adds no count, no link and no failure. This is the one rule of the sweep, `_skips_folder`. Without it, a mailbox with no Archive logs `email.delta_shadow_failed` on each poll.
- ⚠️ A failure outside one folder, for example a defect in the compare, gives no new cursor. Phase (d) then keeps the stored cursor, and the record counts each folder as failed.
- `at` is the clock of the box when the round ends. A box clock that runs behind Exchange by more than one round trip can show a false `sweep_only`.
- A new message that moves or goes away between the sweep and the delta of its folder can show a false `sweep_only`. Examples are a draft that the member sends, and a rule of the Outlook client. The delta then reports the message as `@removed`, or not at all. So read each `sweep_only` with the `removed` count beside it.

**Review round 1 (2026-10-04).** An independent verifier passed the slice with seven findings. The orchestrator recorded a decision for each, and this round built the fixes. Each item names the finding, the fix and the fence.

- **F1, the host of a link (security).** The delta called a stored link with no host check, so the bearer went to any host in the cursor.
  - `_graph_link` in `outlook.py` refuses a link that does not start with `GRAPH_API_BASE` and a slash.
  - It checks the stored link, each `@odata.nextLink` and the `@odata.deltaLink`, before a request or a store.
  - A refused link drops, and the folder seeds again at the next poll.
  - The log says `email.delta_link_refused` with the folder and the source, and never the URL.
  - The seed path is relative to the base URL of the client, so it cannot leave Graph. Fence: `email-delta-link-host`.
- **F2, a short folder list.** `list_folders` skips a failed `childFolders` read, so the delta dropped the link of a nested folder.
  - `list_folders` now sets `_folder_list_partial`, and `_user_sweep_folder_list` then returns False.
  - The delta keeps each stored link that the poll does not read. A folder that the list gives follows the normal rules.
  - The list is the same as before, so the sweep and `off` do not change. Fence: `email-delta-folder-set`.
- **F3, a 400 on a stored link.** A 400 kept the link for ever. Now a 400 of Graph drops it as a 410 does (item 11). A 400 of the token endpoint keeps it, because that is a refused refresh. Fence: `email-delta-failure-isolated`.
- **F4, three gaps of R7.** The mutants MV6, MV7 and MV8 of the verifier survived. New tests fence three rules. Fence: `email-delta-failure-isolated`.
  - A defect in the compare leaves the sweep result unchanged, and the cycle logs one failure line.
  - A 403 or a 404 on a user folder skips it, and that is not a failure.
  - Any failure other than a refused link, a 410 or a 400 keeps the link.
- **F5, the cursor in the sync log.** Each shadow poll wrote 3 to 17 KB into a new `email_sync_log` row, and nothing read it. Now a shadow cycle writes NULL there, as `off` does for Outlook. A provider with a cursor of its own still writes it. Fence: `email-delta-sync-log`.
- **F6, a long cycle.** The scheduler ran the delta after a first import and after a deep sync. That can push a manual sync past the 30 seconds of the Control Plane proxy.
  - `_runs_delta_shadow` in `scheduler.py` now runs it on a normal incremental cycle only.
  - `deep=True` runs no delta, and no cycle runs it before `initial_sync_done`. Fence: `email-delta-normal-cycle`.
- **F7, an old gap.** No test pinned how `catch_up_folders` names a user folder. A new test pins the canonical name in both branches of the sweep. It changes no behaviour. Fence: `email-catch-up-folder-name` in `tests/unit/test_email_import_batches.py`.
- **The R8 case changed with F5.** `test_a_shadow_cursor_lands_in_org_b_and_off_keeps_it` now expects NULL in the log row. It still proves that org A cannot read the cursor or the log row of org B.

**The fences, as built.** `tests/unit/test_outlook_delta_shadow.py` holds 60 tests after review round 1, and it held 34 before. The R8 case is `test_a_shadow_cursor_lands_in_org_b_and_off_keeps_it`.

| Fence | Tests |
|---|---|
| `email-delta-off-no-call` | `test_off_sends_no_delta_request_and_returns_no_cursor`, `test_the_scheduler_with_off_sends_no_delta_and_keeps_the_cursor`, `test_an_unknown_value_resolves_to_off` (5 values) |
| `email-delta-on-refused` | `test_on_resolves_to_shadow_and_logs_the_refusal` (3 spellings) |
| `email-delta-account-scope` | `test_shadow_resolves_to_shadow_for_a_listed_account_only`, `test_an_empty_account_list_sends_no_delta_from_any_mailbox` |
| `email-delta-links-whole` | `test_a_round_of_three_pages_stores_each_delta_link_whole`, `test_the_delta_sends_the_select_of_the_sweep` |
| `email-delta-bad-cursor` | `test_a_bad_cursor_gives_a_full_sweep_and_a_seed_round` (5 cursors), `test_the_cursor_round_trips_and_refuses_every_other_shape` |
| `email-delta-sweep-only` | `test_shadow_writes_exactly_what_off_writes`, `test_a_removed_item_writes_no_trash_row` |
| `email-delta-failure-isolated` | `test_a_failed_delta_leaves_the_sweep_unchanged` (raise, 500, 503, 429, 403 and 404 on the inbox, 410, 400), `test_a_failed_delta_keeps_the_cycle_a_success`, `test_a_mailbox_with_no_archive_logs_no_failure`, `test_a_refused_refresh_on_a_stored_link_keeps_the_link`, `test_a_403_or_404_on_a_user_folder_skips_it` (2), `test_a_defect_in_the_compare_leaves_the_sweep_unchanged` (2 helpers), `test_a_defect_in_the_compare_keeps_the_cycle_a_success_and_logs_once` |
| `email-delta-new-mail-counts` | `test_the_record_counts_new_mail_only`, `test_the_record_holds_no_subject_no_address_and_no_link` |
| `email-delta-folder-set` | `test_the_folder_set_follows_the_sweep`, `test_a_failed_child_folder_read_keeps_the_link_of_the_nested_folder` |
| `email-delta-link-host` | `test_a_stored_link_that_is_not_a_graph_link_sends_no_request` (5 links), `test_a_link_in_a_graph_answer_that_is_not_a_graph_link_is_refused` (next, delta) |
| `email-delta-sync-log` | `test_a_shadow_cycle_writes_no_cursor_into_the_sync_log`, `test_off_still_writes_the_cursor_of_a_provider_into_the_sync_log`, and the R8 case |
| `email-delta-normal-cycle` | `test_only_a_normal_cycle_sends_a_delta_request` (5 cycles) |
| `email-catch-up-folder-name` | `test_email_import_batches.py::test_a_short_user_folder_is_named_by_its_canonical_name` (2 branches) |
| `email-delta-floor` | `test_a_seed_round_filters_on_the_floor` |
| `email-delta-page-cap` | `test_a_folder_at_the_page_cap_goes_on_at_the_next_poll` |
| R5, no session across the delta | `test_the_delta_runs_with_no_session_open`, and `test_no_session_is_open_during_the_provider_calls` stays green |
| R8 | `test_email_scheduler_tenancy.py::TestTheSyncCoreWritesItsOwnTenant::test_a_shadow_cursor_lands_in_org_b_and_off_keeps_it` |

**Mutations (2026-10-04).** Each mutation changed `outlook.py` or `scheduler.py`, ran the named tests, and put the file back. A SHA-256 check confirmed each file byte for byte. 23 of 23 mutations turned a test red. The control M15p is the mutation of M15 against the static parity fence alone. It stays green, so the R8 case is the fence that catches it.

| # | Mutation | Fence | Result |
|---|---|---|---|
| M1 | The delta runs with no `delta_shadow` | `email-delta-off-no-call` | red, 2 failed |
| M2 | An unknown value passes as its own mode | `email-delta-off-no-call` | red, 3 failed |
| M3 | `on` resolves to `on` | `email-delta-on-refused` | red, 3 failed |
| M3b | `on` logs no refusal | `email-delta-on-refused` | red, 3 failed |
| M4 | The account list is not read | `email-delta-account-scope` | red, 2 failed |
| M4b | The scheduler passes `delta_shadow` for each mailbox | `email-delta-account-scope` | red, 1 failed |
| M5 | The provider decodes the stored link (`%3D` to `=`) | `email-delta-links-whole` | red, 1 failed |
| M5b | No `Prefer` header | `email-delta-links-whole` | red, 1 failed |
| M5c | Each delta request sends `$top` | `email-delta-links-whole` | red, 1 failed |
| M6 | The version check is gone | `email-delta-bad-cursor` | red, 2 failed |
| M7 | An `@removed` item becomes a `[DELETED]` marker | `email-delta-sweep-only` | red, 2 failed |
| M8 | A 410 keeps the link | `email-delta-failure-isolated` | red, 1 failed |
| M8b | A failed folder raises, and no guard catches it | `email-delta-failure-isolated` | red, 4 failed |
| M9 | The compare counts all mail, not new mail | `email-delta-new-mail-counts` | red, 2 failed |
| M9b | The delta runs before the sweep | `email-delta-new-mail-counts` | red, 1 failed |
| M10 | A failed folder list drops the user links | `email-delta-folder-set` | red, 1 failed |
| M10b | A folder that is gone keeps its link | `email-delta-folder-set` | red, 1 failed |
| M11 | A seed round sends no floor | `email-delta-floor` | red, 1 failed |
| M12 | A cut round stores an `at` | `email-delta-page-cap` | red, 1 failed |
| M12b | The page cap is 30 | `email-delta-page-cap` | red, 1 failed |
| M13 | A 404 on Archive is a failure | `email-delta-failure-isolated` | red, 1 failed |
| M14 | A session is open across `sync_messages` | R5 | red, 2 failed |
| M15 | Phase (d) writes `COALESCE(:history_id, NULL)` | R8 | red, the R8 case failed |
| M15p | M15, against `test_email_manual_sync_parity.py` alone | control | green, as expected |

**Mutations of review round 1 (2026-10-04).** The same method, with a SHA-256 check of `outlook.py` and `scheduler.py` after each run. 19 of 19 mutations turned a test red. MV6, MV7 and MV8 are the three mutants of the verifier that survived before this round. This round rebuilt them from the text of the finding.

| # | Mutation | Fence | Result |
|---|---|---|---|
| F1a | The stored link goes out with no host check | `email-delta-link-host` | red, 5 failed |
| F1b | A next link goes out with no host check | `email-delta-link-host` | red, 1 failed |
| F1c | A delta link goes into the cursor with no host check | `email-delta-link-host` | red, 1 failed |
| F1d | A refused link stays in the cursor | `email-delta-link-host` | red, 5 failed |
| F2a | A short folder list reads as a full list | `email-delta-folder-set` | red, 1 failed |
| F2b | `list_folders` sets no mark on a failed `childFolders` read | `email-delta-folder-set` | red, 1 failed |
| F2c | A short list keeps the link of a folder that it reads | `email-delta-folder-set` | red, 1 failed |
| F3a | A 400 keeps the link | `email-delta-failure-isolated` | red, 1 failed |
| F3b | A 400 of the token endpoint drops the link | `email-delta-failure-isolated` | red, 1 failed |
| MV6 | The outer guard of `sync_messages` catches nothing | `email-delta-failure-isolated` | red, 3 failed |
| MV7 | A 403 or a 404 on a user folder is a failure | `email-delta-failure-isolated` | red, 2 failed |
| MV8 | Each 4xx drops the link | `email-delta-failure-isolated` | red, 4 failed |
| F5a | The sync log gets the shadow cursor | `email-delta-sync-log` | red, 1 failed |
| F5a-R8 | F5a, against the R8 case | R8 | red, 1 failed |
| F5b | The sync log gets NULL for each provider | `email-delta-sync-log` | red, 1 failed |
| F6a | A deep sync runs the delta | `email-delta-normal-cycle` | red, 2 failed |
| F6b | A cycle before `initial_sync_done` runs the delta | `email-delta-normal-cycle` | red, 2 failed |
| F7a | A catch-up page names a user folder by its id | `email-catch-up-folder-name` | red, 1 failed |
| F7b | A failed first page names a user folder by its id | `email-catch-up-folder-name` | red, 1 failed |

**Follow-ups (named, not built).**

- **EM-T4d-2**, as above: `on` and a delete rule, after the live check.
- **EM-T4d-f1.** Count an id that the sweep saw and that the delta reports as `@removed` in the same round apart from `sweep_only`. It would remove the false `sweep_only` of a draft that the member sends. Item 8 says that an `@removed` item adds to `removed` only, so this needs an edit of item 8.
- **EM-T4d-f2 (review round 1).** The `httpx` logger at INFO can print the URL of each request. A delta URL holds its `$deltatoken`, and a sweep URL is in the log the same way. Decide the level of that logger for the whole service. This round changed no logging.
- **EM-T4d-f3 (re-verify of round 1). Settle this before anyone sets `shadow` on a box.** A plain "Sync now" by a member sends `deep=None`, so it runs the delta like a loop cycle (`sync.py:264-266`). The webhook, the rerun and the agent tool `sync_account` do the same. A folder with no stored link then seeds, with up to 20 pages, inside the 30-second budget of the proxy. That happens on the first shadow poll, after a Resync, after a dropped link and for a new user folder. The fix is to run the delta only from the loop (`from_loop`), or to accept the cost for the few listed mailboxes.
- **EM-T4d-f4 (re-verify of round 1).** When the `childFolders` read fails on every poll, the delta keeps the link of a deleted top-level user folder. The growth stops at the count of deleted folders.

##### EM-T4e — §7 item 4, the N+1 reads and the indexes

**Status (2026-10-03).** ✅ MERGED (#586). The migration is 226. The fence is `tests/unit/test_email_n_plus_one.py`, with R8 on a private database.

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

**As built (2026-10-03).**

1. `_load_rules` reads the rules, and then the actions of all the rules in one read (`_ACCOUNT_ACTIONS_SQL`). Python groups the actions by rule. With no rule, it makes one read only.
2. The read of the actions orders on `created_at, ctid`. One write gives all the actions of a rule the same `created_at`. The old read of one rule gave such a tie in table order. One sort over all the rules does not keep that order, so the read names `ctid`.
3. `_unread_counts` in `transport/accounts.py` reads the unread count of each mailbox in one grouped read. `_account_scope` holds the owner predicate. `list_accounts` and `set_default_account` both call it. `list_accounts` returns each field that it returned before.
4. Migration 226 (`226_email_thread_index.sql`) creates `idx_email_messages_thread_received` with `CREATE INDEX IF NOT EXISTS`. It adds no foreign key, and it copies the locking note of migration 170. That note names the real holder of the lock: a sync phase that writes `email_messages`. The build took 42 ms on 16 000 rows, warm.
   - ⚠️ **The status index of item 4 is OUT of scope** (review, 2026-10-03). No query filters on `email_thread_status.last_message_id`. Each read joins the status row to `email_messages` on the primary key of the message (`digest.py`, `followups.py`, `replyzero.py`). The status upsert changes `last_message_id` on almost every write. So that index adds write cost and serves no read. The fence checks that 226 puts no index on the column.
5. ⚠️ **One change from item 4.** The thread index is `received_at DESC NULLS LAST`, not `received_at DESC`. A plain `DESC` is `DESC NULLS FIRST`. `build_thread_context` orders `ASC NULLS FIRST`, and a backward scan of `DESC NULLS LAST` gives that order. With a plain `DESC` on the scratch database, the planner kept the index of migration 17 and a Sort. With `NULLS LAST`, it used the new index and no Sort. Ten of the eleven thread reads in `routes/email` and `routes/tasks` get their order from it.
6. ⚠️ **A finding.** §7 item 4 calls the composite index missing. But migration 17 already has `idx_email_messages_thread` on `(account_id, thread_id)`, and that index finds the rows of a thread. The new index can remove the Sort, and nothing more. For a thread of 2 to 30 messages, the planner gives the two plans almost the same cost. When the syncs of several mailboxes interleave their mail in the table, the planner reads a thread of 4 or more through the new index with a bitmap scan, and then it sorts. The drop of the old index is a later contract step (R6), in a migration of its own. `HANDOFF.md` H-210 carries it.
7. The R8 `EXPLAIN` test seeds that interleaved layout. The plan of the planner names the new index, which is the done-when. With `enable_sort` off, the plan is a backward scan of the new index, and a plain `DESC` index fails that check.

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

- **R-1.** A split can write a stale decision, because a newer message can come in during the ask. EM-T4a-2 guards the write with the newest stored `received_at` that its read saw (its item 6).
- **R-2.** A split can lose atomicity between a provider act and its mirror. The provider acts first, as today. When the mirror write fails, the next sync corrects the row.
- **R-3.** EM-T4a-4 can turn on jobs that do nothing on the box today. EM-T4b must merge first.
- **R-4.** The 401 retry sends a request twice. Each body in both providers is JSON or form data, so httpx can send it again.
- **R-5.** Delta stopped new mail once, and nobody found the cause. So EM-T4d builds shadow only, and the full sweep stays the source of truth.
- **R-6.** A budget of 2000 calls is a guess for one mailbox. The `log` mode measures the real count before anyone sets `enforce`.
- **R-7.** A known limit of the EM-T4a-2 guard. A mailbox with no enabled rule can keep a stale status after a voided write. The cause is the new-mail floor of the gap query, which is NULL there. This kind of stale status exists today.

#### 10.4.7 EM-T6 in full

**Status.** EM-T6a and EM-T6b MERGED (#577, #580). EM-T6d parts 1 and 2 MERGED (#579, #581). EM-T6c MERGED (#615, 2026-10-04), a port of `8b4cb4dfc` with the gaps G1 to G5 and review round 1 closed. EM-T6e ✅ MERGED (#619, 2026-10-04), with review round 1 closed. Anchors re-verified at `3d11922c2` on 2026-10-04.

**EM-T6d, part 1 (range step and progress).** ✅ MERGED (#579, 2026-10-02). The narrowing is under EM-T6d below.

**EM-T6d, part 2 (rules step, drafting step and Done).** ✅ MERGED (#581, 2026-10-03). The notes are under EM-T6d below.

**Gate.** AGENT-SAFE: all five parts. No part flips a flag. The limit is the setting `EMAIL_MAILBOX_STORAGE_LIMIT_MB`, with a default of 500. A change of it on a box is gate `env-write`. The owner answered the three checks of EM-T6c on 2026-10-02 (§10.2). An agent must not run the removal route of EM-T6c on a production mailbox, because that is a production one-off.

**Order.**

1. EM-T6a waits for EM-T4c to merge, because both change `_sync_account` and `providers/outlook.py`.
2. EM-T6b waits for EM-T6a. EM-T6c waits for EM-T6b.
3. EM-T6d waits for EM-T6a, EM-T6b and EM-T7. Before EM-T7, the recommended rules turn on drafting, and D-EM-15 forbids that.
4. EM-T6e waits for EM-T6c and EM-T6d.
5. EM-T4d waits for EM-T6b, because both change `sync_messages` in `providers/outlook.py`.
6. EM-T4e and EM-T6a both change `transport/accounts.py`, and each takes a migration number. The second to merge rebases and takes its number again (R1).
7. EM-T6f waits for EM-T6e. EM-T6f is a path that resumes the import of a mailbox in the phase `limit` under the limit. This spec records it and does not specify it (EM-T6e, open points).

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
  **2026-10-04:** D-EM-5 changed. EM-G4 reseeds a stale Gmail cursor with no Resync (§12.3.5).
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

**Status.** ✅ MERGED (#580, 2026-10-03), with fix rounds 1, 2, 3 and 4. It sits on `main` with EM-T6a (#577), EM-T4f (#578) and EM-T6d part 1 (#579).

**As built.**

- `import_batches` takes the keyword `on_estimate`. Outlook awaits it once, before the first batch. The core then writes the estimate in a block of its own.
- The default import of the base class does not call `on_estimate`. So IMAP shows a count and no estimate.
  EM-G5a gives Gmail its own import with an estimate (built 2026-10-05, not merged, §12.3.6.1).
- Outlook counts only the folders whose first page opened. A missing folder adds no count and does not make the estimate NULL.
- A resume writes the count so far plus the new count as the estimate. The import writes the message at the resume point again, and the count and the estimate both include it.
- Outlook pages each folder by time (fix rounds 1 and 2). Each next page is a new query with `lt` the second after the oldest message of the last page, and the stream drops the ids that it read again. A `$skip` link shifted when a message moved out of a folder, and the import lost the message at the page edge.
- Exchange keeps a fraction of a second, and Graph shows whole seconds. `le 10:00:05` is `le 10:00:05.000`, so it dropped the rest of a split second. In a simulation of 200,000 messages it lost 400. With `lt` the loss is 0, and the resume bound uses `lt` too.
- When one second fills a whole page, one query with `$top=1000` reads that second, and the next page starts below it. A page that adds no message ends the folder, and so does `IMPORT_MAX_PAGES` (5000). Both log `sync.import_folder_capped`.
- A 403 or a 404 on a first page skips only Archive or a user folder (fix round 4). The log says `sync.import_folder_skipped` with the status. A 403 or a 404 on any other system folder fails the import, and so does any other failure. The next sync resumes.
- A page that answers 429, 503 or 504 waits for `Retry-After`, with the bound of `_graph_send`, and tries once more. The recurring sweep does the same.
- When an import fails, the cycle still runs the recurring sweep and phase (c), so new mail lands (owner answer Q2). Phase (d) then writes `sync_status = 'error'` with the error, and the next sync resumes the import. A failed import on a mailbox that never synced keeps `last_synced_at` NULL (fix round 3), so the next sweep reads back to `created_at`.
- When a sweep folder stops short of the catch-up watermark, the sweep keeps the pages that it read and sets `catch_up_incomplete`. Phase (d) writes that mail and keeps `last_synced_at`. Fix round 3: a first page that fails with a status other than 403 or 404 also leaves its folder short.
- The recurring sweep uses the rule of the import for a 403 or a 404 (fix round 4). It skips Archive or a user folder. A 403 or a 404 on Inbox, Sent, Drafts, Junk or Deleted Items fails the cycle, and the error path writes `sync_status = 'error'`. Before fix round 4, the sweep skipped each folder that answered 403 or 404, and the member saw no error.
- A short catch-up writes a `sync_error` note with the folder name only, and the loop backs off as for a failure. Only a cycle of the loop adds to the count (fix round 4). The webhook, the manual sync, the rerun and the deep downloads run when mail arrives or when a member acts. If they counted, a busy mailbox could abandon a gap in a few minutes. The loop backs off, so 6 cycles of the loop take about 3 hours.
- After 6 short cycles of the loop in a row, the watermark moves on, and the log says `sync.catch_up_abandoned` with the folder and the gap. The abandon stays until a complete cycle (fix round 4). Until then, a short cycle of any caller adds no count, keeps no watermark and does not back off. It writes the note of the abandon again.
- A complete cycle reads every folder back to the watermark. It clears the count and the abandon. A restart clears them too, because they live in the process. The later full fix is a watermark for each folder.
- The note of a short catch-up reaches the API only (fix round 4, a recorded decision). `sync_status` stays `idle`, and `GET /email/accounts` returns `sync_error` for each mailbox. The UI shows `sync_error` only when `sync_status` is `error`, so the member does not see the note. EM-T6b has no UI (the non-goals). Follow-up for EM-T6d: show the note on an idle mailbox.
- When the deep sync of a member act ends with no error, one block reconciles deletions against `(id, folder, received_at)` of each message that its import wrote. Only a provider with `import_full_snapshot` does this, which today is Outlook.
- Three guards protect that reconcile (fix rounds 2 and 3). It keeps a row whose `updated_at` is after phase (a), because a move, a rule action or a draft during the import writes the row, and Graph gives a moved message a new id. When a folder has more candidates than 50, or 2% of its rows, the reconcile leaves that folder and logs `sync.import_reconcile_skipped`.
- Then, with no session open, Outlook looks up each candidate by `internetMessageId`, at most 50 for each folder. A message that Graph still has keeps its row, because the member moved it in the Outlook client. A failed lookup, or a row with no internet message id, keeps its row too. The trash checks `updated_at` again in its own block.
- EM-T6d part 2 (#581) closed the follow-up of the EM-T6b review. `isFirstSyncPending` and `onboardingStage` check `syncEnabled`, so the panel does not freeze when the member turns sync off during the import.
- `sync_messages` takes `catch_up`. Gmail and IMAP accept it and ignore it, so `gmail.py` and `imap.py` change by one argument each.
  EM-G4b (#632) fixed the loss of Gmail mail after a pause. Gmail now stores a history cursor, and a stale cursor sweeps back to the watermark (§12.1 GM-16 to GM-18, §12.3.5.2).
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

**Narrowed (2026-10-04).** Port `8b4cb4dfc` onto `main`. Do not build it again.

- A dry-run apply onto `main` gives five conflicts: `email_ingestion/scheduler.py`, `transport/accounts.py`, `email_ingestion/AGENTS.md`, this spec and `work_plan.md`. Seven files apply clean.
- On `main`, `_reconcile_import` takes `provider`. The old commit calls it without `provider`, so the port adds it.
- The port takes no migration. Migration 225 holds `stored_bytes` and `stored_bytes_at`. A later change that needs a column takes the next free number at build time (R1).

**The gaps G1 to G5 (2026-10-04).** The old commit leaves five gaps. The port closes each one, and these rules bind it.

- **G1. The removal keeps drafts.** The preview and the removal skip the folder `drafts`, so an unsent draft stays. This is an orchestrator decision, and the owner can reverse it.
- **G2. At the limit, `core.hydrate_message_body` writes no body** (`core.py:365-418`). It returns the body that it loads. The reply drafter and the follow-up path call it.
- **G3. The removal holds the mailbox lock.** A new public helper in `scheduler.py` takes the lock that each sync takes (`scheduler.py:769-860`). The removal waits 5 seconds for it, then answers 409. Its first block under the lock moves `import_since`, before any delete.
- **G4. A removal can end the `limit` phase.** The last block writes `import_phase = 'done'` when the phase was `limit` and the new meter is under the limit. Review round 1 adds a third test: no gap is left below `import_reached_at`.
- **G5. The last block deletes the orphan drafts of the AI.** It deletes the `email_ai_drafts` row of each thread that this removal emptied (review round 1).

**Status.** ✅ MERGED #615 (2026-10-04). Four commits on `5e268c766`: the narrowing, the port of `8b4cb4dfc`, the gaps G1 to G5, and review round 1. It adds no migration. The fence is `tests/unit/test_email_storage_limit.py`: 72 tests, 28 of them R8, and 0 skip.

**As built.**

- `email_ingestion/storage.py` owns the limit, the meter, the preview and the steps of the removal. Each step takes a session, opens none and never commits.
- The meter is one statement. It sums `pg_column_size` of 16 message columns, 5 attachment columns and 3 embedding columns. An R8 test compares the three lists with `pg_attribute`, so a new column of variable length fails until the meter names it.
- The meter runs in the block of each import batch and in the block of phase (d). A first import at the limit writes `import_phase = 'limit'`. `_cycle_result` takes a keyword `limit`, and each import at the limit returns `limit: true`.
- A deep sync that stops at the limit runs no import reconcile, and it logs `sync.import_reconcile_skipped reason=limit`. A page that the import did not read can hold mail of the same second as its last message.
- Phases (e) and (f) moved into `_backfill_and_embed`. At the limit, neither phase runs, and the cycle logs `sync.storage_limit`. The write of refreshed credentials still runs between the two phases.
- The two routes live in `transport/storage.py`. The preview answers `before`, `messages` and `bytes`. The removal answers `before`, `removed`, `stored_bytes` and `storage_limit_bytes`. `EmailAccountModel` carries `stored_bytes` and `storage_limit_bytes` for EM-T6e.
- A `before` that is not an ISO date answers 400. A value with no zone is UTC. The removal keeps a message with no `received_at`.
- (Q4) At the limit, the open skips its body UPDATE. The attachment rows of the open still land, because the download route needs the stored row id.
- (G1) `KEPT_FOLDERS_SQL` is the one draft filter. It copies the folder test of `body_backfill.py`, so `drafts`, `Drafts` and `draft` stay, and a NULL folder is not a draft.
- (G2) `hydrate_message_body` reads the meter in its first SELECT, through a `LEFT JOIN` on `email_accounts`, so the check costs no query.
- (G3) `scheduler.hold_mailbox` is the new public helper, and `MailboxBusy` is its refusal. The ownership read runs before the lock, so a stranger gets 404 and never learns that a sync runs. The wait is `REMOVAL_LOCK_WAIT_S`, 5 seconds.
- (G4) `end_limit_phase` writes `done` only under the limit, and its `WHERE` also names `import_phase = 'limit'` and the gap test of review round 1.
- (G5) `delete_orphan_ai_drafts` runs in the last block. No step calls Mem0.
- The port changed two tests of `main`. The fake account row of `test_email_n_plus_one.py` gains `stored_bytes`, because each account read now returns it. The `email_ingestion/` entry of `test_email_owner_scope_fence.py` gets a new reason (B6), and it stays the one entry.

**Review round 1 (2026-10-04).** An independent verifier and an adversarial reviewer read the branch. Each finding, its fix and its fence:

- **(P1) The limit binds "Load older".** `transport/folders.py::backfill_folder` reads `stored_bytes` in its owner read. At the limit, it writes no row, builds no provider, and answers `exhausted`. Fence `email-storage-rr1-load-older`: `test_load_older_at_the_limit_writes_nothing_and_calls_no_provider`.
- **(P1, the UI)** The store writes `backfillExhausted` from that answer, so the list hides the button and shows no error. The reviewer wrote that a scroll calls the route. The scroll observer pages the database only (`handleAutoLoad`), and a click on the button calls the route (`EmailList.tsx:300-303`).
- **(P2) A removal closes the guided setup of a mailbox from before EM-T6.** `_ADVANCE_IMPORT_SINCE` writes `onboarding_done_at` when the old `import_since` is NULL. A mailbox in its guided setup keeps NULL. Fence `email-storage-rr1-onboarding`: `test_a_removal_closes_the_setup_of_a_mailbox_from_before_em_t6`.
- **(P2) The `limit` phase ends only with no gap.** `_END_LIMIT_PHASE` also needs `import_reached_at IS NULL OR import_since >= import_reached_at`. Fence `email-storage-rr1-gap`: `test_the_limit_phase_ends_only_when_no_gap_is_left`.
- **(Noted) The lock key is the canonical UUID.** `scheduler._lock_key` returns `str(uuid.UUID(id))`. An id that is not a UUID keeps its text in lower case, and the key never raises. Both storage routes answer 404 for such an id before a block opens.
- **(Noted) The fence of the lock key.** Fence `email-storage-rr1-lock-key`: `test_the_lock_key_is_the_canonical_uuid`, `test_an_id_that_is_not_a_uuid_answers_404_before_any_block`, and `test_a_removal_answers_409_while_a_sync_holds_the_mailbox`. In the third test, the sync names the mailbox in upper case with no hyphens.
- **(Noted, and verifier P2) The orphan deletes take only the threads of this removal.** `remove_older_chunk` returns `RemovedChunk`, with the threads of the deleted mail (`RETURNING thread_id`). The route collects them for the last block. Fence `email-storage-rr1-orphans`: `test_the_orphan_deletes_touch_only_the_threads_of_this_removal`.
- **(Verifier P2) A draft in a non-English Outlook mailbox.** No code change. The open points below record it.
- **(Verifier P3)** The base of this section now reads `5e268c766`, the merge of EM-T4d (#614). This round removed the STE errors on the added lines of `email_ingestion/AGENTS.md`, `work_plan.md` and this section.

**Agent decisions of review round 1.** The owner can reverse each one.

- **"Load older" can load again mail that a removal took out.** Its floor stays the ceiling of 180 days, as EM-T6a chose (fix round 1 of EM-T6a). It is an explicit act of the member, and the limit still binds it. The reason: a floor that only a removal sets needs a new column, and so a migration. That column is the path to reverse this decision.
- **The orphan deletes take only the threads that the removal emptied.** A thread status or a draft of the AI from any other cause stays. A reply from mailbox B to mail of mailbox A stores the pair (B, the thread of A) (`automation/drafting.py:1943-1945`). Before this round, each removal in B deleted that draft.
- **The fence of the orphan deletes seeds three rows that must stay.** (a) A row of A for a thread that the removal did not touch. (b) A row of mailbox B of the same member for the emptied thread. (c) A row of a colleague's mailbox in the same organization for the emptied thread.

**Open points.**

- During a removal, a loop cycle or a webhook sync skips, and its new mail waits for the next loop cycle. A holder sync whose rerun came while the removal waited loses that rerun, because `_rerun_once` reads a waiter as a sync.
- R-4 still holds: measure the time of the meter on the box after the deploy.
- Before merge, the orchestrator reads the meter of each production mailbox (R-8). The read is the SELECT form only.
- EM-T6e draws the notice and the dialog.
- **(Review round 1, item 3) Nothing closes the gap of a `limit` phase.** A loop cycle runs no import, because `initial_sync_done` is true. A Resync (`deep=True`) imports from now down to `import_since`, so it can fill the gap up to the limit. It writes no progress column, so the phase stays `limit` and `import_reached_at` does not move. Only a removal with `before` at or after `import_reached_at` ends the phase. EM-T6e must decide a resume path, for example a deep import that ends the phase when it reaches the floor under the limit. **Closed for EM-T6e (2026-10-04):** EM-T6e draws the gap as one line with no action (D2). EM-T6f owns the resume path (Order, item 7).
- **(Review round 1, item 6) A draft in a non-English Outlook mailbox is not kept.** `email_messages` has no draft flag, and the Outlook provider does not read `isDraft`. `providers/outlook.py:419-422` does not request `wellKnownName`, because a consumer account answers 400 to it. So a Drafts folder with a local name (`Entwürfe`, `Brouillons`) is a user folder, and its rows get `folder = 'entwürfe'`. G1 does not keep them, and `body_backfill.py:120` has the same rule.
- **(Item 6, the provider follow-up)** Classify the Drafts folder by the alias `/me/mailFolders/drafts`, or store `isDraft` for each message. Then `KEPT_FOLDERS_SQL` reads it. The branch `email-delta-shadow` changes `outlook.py` now, so this round did not.
- **(Review round 1, item 9) A large removal can take longer than 30 seconds.** The chunk loop runs inside the request, and the Control Plane proxy gives a POST 30 seconds. EM-T6e must plan for a long removal. For example, the dialog reads the meter again after a timeout of the proxy. **Closed by EM-T6e (2026-10-04):** the BFF gives the removal 120 seconds, and the dialog follows a removal that outlives the proxy through the preview (D1).
- **"Load older" takes no mailbox lock.** A "Load older" that runs during a removal can write a few rows older than `before`.
- **"Load older" reads the meter of the last sync.** Under the limit, each call writes up to 300 messages, and the meter runs again at the next sync. So a member can go past the limit by the pages of one sync interval.
- **A removal that fails part way keeps some orphan rows.** Its last block does not run, so the rows of the threads that its chunks emptied stay. A later removal does not see those threads. A disconnect deletes them, because both tables cascade from `email_accounts`.

**Mutation checks (2026-10-04).** For each mutation, the script changed the code, ran the named tests on a private database, and put the file back. A SHA-256 check proved each restore.

| Mutation | What it changes | Tests that went red |
|---|---|---|
| G1, chunk | Drop the draft filter from `_CHUNK_IDS` | `test_the_removal_keeps_each_draft`, `test_the_preview_and_the_removal_skip_the_drafts[chunk]` |
| G1, preview | Drop the draft filter from `_PREVIEW` | `test_the_removal_keeps_each_draft`, `test_the_preview_and_the_removal_skip_the_drafts[preview]` |
| G2 | Write the body in `hydrate_message_body` at any meter | `test_hydrate_at_the_limit_returns_the_body_and_writes_none[at_limit]` |
| G3, no lock | `hold_mailbox` acquires nothing | `test_a_removal_answers_409_while_a_sync_holds_the_mailbox`, `test_a_removal_runs_when_the_sync_ends_inside_the_wait` |
| G3, floor last | Move `advance_import_since` from the first block to the last block | `test_the_first_block_under_the_lock_moves_the_floor`, `test_a_removal_deletes_the_older_mail_of_one_mailbox` |
| G3, wait in a block | Open a `_tenant_session` in the same `async with` as the lock | `test_the_removal_waits_for_the_lock_with_no_block_open` |
| G4, no end | Drop the call of `end_limit_phase` | `test_a_removal_under_the_limit_ends_the_limit_phase[under]`, `test_a_removal_in_one_mailbox_leaves_the_other_unchanged` |
| G4, end at the limit | `end_limit_phase` ends the phase at any meter | `test_a_removal_under_the_limit_ends_the_limit_phase[still_full]` |
| G5, no delete | Drop the call of `delete_orphan_ai_drafts` | `test_the_removal_deletes_the_orphan_ai_drafts_and_no_memory`, `test_a_removal_in_one_mailbox_leaves_the_other_unchanged` |
| G5, Mem0 | Call `get_memory_client` at the end of the removal | `test_the_removal_deletes_the_orphan_ai_drafts_and_no_memory` |
| Multi-inbox | Move `import_since` of each mailbox of the member | `test_a_removal_in_one_mailbox_leaves_the_other_unchanged` |
| No provider reach | Name `build_provider` inside `hold_mailbox` | `test_no_provider_method_is_reachable_from_a_route` |
| RR1 item 1 | Replace the limit check of `backfill_folder` with `if False:` | `test_load_older_at_the_limit_writes_nothing_and_calls_no_provider[at_limit]` |
| RR1 item 2, no CASE | Drop the `onboarding_done_at` line of `_ADVANCE_IMPORT_SINCE` | `test_a_removal_closes_the_setup_of_a_mailbox_from_before_em_t6[before_em_t6]` |
| RR1 item 2, every row | `WHEN import_since IS NULL` becomes `WHEN true` | `test_a_removal_closes_the_setup_of_a_mailbox_from_before_em_t6[in_setup]` |
| RR1 item 3 | Drop the gap test from `_END_LIMIT_PHASE` | `test_the_limit_phase_ends_only_when_no_gap_is_left[gap_left]` |
| RR1 item 4, key | `_lock_key` goes back to the id in lower case | `test_the_lock_key_is_the_canonical_uuid`, `test_a_removal_answers_409_while_a_sync_holds_the_mailbox` |
| RR1 item 4, route | Drop the `_account_uuid` call of the removal | `test_an_id_that_is_not_a_uuid_answers_404_before_any_block`, all four ids |
| RR1 item 5, status mailbox | `ts.account_id = :aid` becomes `(ts.account_id = :aid OR true)` | `test_the_orphan_deletes_touch_only_the_threads_of_this_removal` |
| RR1 item 5, draft mailbox | `d.account_id = :aid` becomes `(d.account_id = :aid OR true)` | `test_the_orphan_deletes_touch_only_the_threads_of_this_removal` |
| RR1 item 5, status threads | Add `OR true` to the thread filter of `_DELETE_EMPTY_THREAD_STATUS` | `test_the_orphan_deletes_touch_only_the_threads_of_this_removal` |
| RR1 item 5, draft threads | Add `OR true` to the thread filter of `_DELETE_ORPHAN_AI_DRAFTS` | `test_the_orphan_deletes_touch_only_the_threads_of_this_removal` |
| RR1 item 5, route | The route stops the collection of the threads | `test_the_first_block_under_the_lock_moves_the_floor`, `test_the_removal_deletes_the_orphan_ai_drafts_and_no_memory` |

Item 6 of review round 1 changed no code, so it has no mutation.

**Scope.**

1. **The setting.** Add `email_mailbox_storage_limit_mb: int = 500` to `acb_common/settings.py`. The limit in bytes is that value times 1,048,576.
2. **The meter.** Add `measure_stored_bytes(db, account_id)` to a new `email_ingestion/storage.py`. One SELECT sums `pg_column_size` of each column of variable length in the `email_messages` rows of the mailbox. It adds the `email_attachments` rows and the `email_embeddings` rows of the mailbox. It writes `stored_bytes` and `stored_bytes_at`.
3. **The meter reads no body.** `pg_column_size` of a stored value reads its size from the stored header, so the meter does not fetch the bodies. Do not use `octet_length`, and do not take the size of a whole row.
4. **When the meter runs.** After each import batch, and at the end of each sync in phase (d).
5. **The limit stops the import.** After a batch, when `stored_bytes` is at or over the limit, the import fetches no next batch. A first import then writes `import_phase = 'limit'` and `initial_sync_done = true`. A deep sync of a member act stops in the same way, and `_sync_account` returns `limit: true` in its result.
6. **(Q3) Phases (e) and (f) stop at the limit.** At or over the limit, the body backfill makes no provider call, and the embeddings make no model call. A message that the member opens still loads its body live.
7. **(Q2) New mail still syncs at the limit.** The recurring sweep writes new mail at any meter value. Only the import of older mail stops.
8. **The preview route.** `GET /email/accounts/{id}/storage/older?before=<date>` returns the count of messages and the bytes that a removal would free. It writes nothing.
9. **The removal route.** `POST /email/accounts/{id}/storage/remove-older` with `{"before": "<date>"}` removes the mail of that mailbox received before that date. Both routes carry the owner predicate on `user_id`. A `before` that is not in the past answers 400. Both routes go in a new module, `transport/storage.py`.
10. **What the removal deletes.** It works in chunks of 1,000 messages, and each chunk is one `_tenant_session()` block with no `commit()`. It skips the folder `drafts` (G1). It first deletes the `email_executed_rules` rows of those messages, and then the messages. The attachment rows and the embeddings cascade. Last, it deletes the thread status and the AI draft of each emptied thread (G5, review round 1).
11. **What the removal keeps.** The rules, the learned patterns, the rule guidance, the senders, the contacts and the unsent drafts (G1). The Mem0 memories of the mailbox stay, because no Mem0 key names one mail.
12. **The import floor and the meter.** The first block under the lock moves `import_since` to the later of `import_since` and `before`, before any delete (G3). So a Resync does not import that mail again, also after a removal that fails part way. The last block runs the meter again, and it ends the `limit` phase under the limit (G4). The answer holds the count removed and the new `stored_bytes`.
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
- (Q4) At the limit, that open writes no body (`transport/messages.py:674-687`). Attachment rows may still land, because the download route needs the row id. Under the limit, the open stores the body as it does today.
- (Q2) At the limit, the next poll still writes a new message.
- The preview returns the count and the bytes, and the count of `email_messages` rows does not change.
- R8: a removal with `before` 30 days back deletes each message of the mailbox older than that date, and no message of another mailbox. It deletes their `email_executed_rules` rows, and it moves `import_since` to `before`.
- After that removal, a Resync writes no message older than `before`.
- With `build_provider` and `provider_session` patched to raise, a removal still succeeds.
- An AST fence finds no import of `email_ingestion.providers`, `build_provider` or `provider_session` in `storage.py` or in the two handlers. A companion test proves that the fence can fail.
- An AST fence finds no `.commit()` in `storage.py`.
- R8, for two organizations: a member who does not own the mailbox gets 404 from both routes.
- `test_email_owner_scope_fence.py` passes with no new entry. Its `email_ingestion/` entry gets a new reason text only.
- (G1) The removal keeps each message in the folder `drafts`, and the preview does not count it.
- (G2) At the limit, `hydrate_message_body` returns the body and writes none.
- (G3) While a sync holds the mailbox, the removal answers 409. The removal moves `import_since` before its first delete.
- (G4) R8: a removal that takes the meter under the limit writes `import_phase = 'done'` when no gap is left below `import_reached_at`.
- (G5) R8: the removal deletes the `email_ai_drafts` row of each thread that it emptied, and no other row. A patched Mem0 client gets no call.
- R8, multi-inbox: a removal in mailbox A of member M leaves mailbox B of M unchanged.

**Files.** `packages/acb_common/acb_common/settings.py`. Under `apps/services/email_ingestion/email_ingestion/`: a new `storage.py` and `scheduler.py`. Under `routes/email/`: `core.py`, `transport/accounts.py`, `transport/messages.py`, `transport/__init__.py` and a new `transport/storage.py`. The DOX files `apps/services/email_ingestion/AGENTS.md` and `apps/services/gateway/AGENTS.md`. The test is a new `tests/unit/test_email_storage_limit.py`.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_storage_limit.py tests/unit/test_email_import_batches.py \
  tests/unit/test_email_import_floor.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_email_scheduler_tenancy.py tests/unit/test_email_sync_one_at_a_time.py \
  tests/unit/test_email_cleanup_backfill.py tests/unit/test_email_process_past_progress.py \
  tests/unit/test_email_embeddings_hash.py tests/unit/test_email_owner_scope_fence.py \
  tests/unit/test_email_accounts_initial_sync_rls.py tests/unit/test_email_keep_separate.py \
  tests/unit/test_org_purge_tenant.py tests/unit/test_db_engine_seam.py \
  tests/unit/test_outlook_delta_shadow.py -q -rs
uv run ruff check apps/services/email_ingestion/email_ingestion/storage.py \
  apps/services/gateway/gateway/routes/email/transport/storage.py \
  tests/unit/test_email_storage_limit.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

The R8 tests must show PASSED, not SKIPPED.

Before merge, read the meter (the SELECT form only) for each production mailbox. A mailbox from before EM-T6 can already be over 500 MB (R-8).

##### EM-T6d — the guided setup: range, progress, AI rules and done (UI)

**Waits for** EM-T6a, EM-T6b and EM-T7. Before EM-T7, "Use the recommended rules" also turns on drafting, because the preset "Needs Reply" carries `DRAFT_EMAIL` (`rules.py:182-184`).

**Narrowed (orchestrator, 2026-10-02).** The owner wants an Email demo with the import timeline. The owner deferred the rules step and the storage UI. So EM-T6d has two parts.

- **Part 1** builds items 1 to 7, 12 and 13. ✅ MERGED (#579). `onboardingStage` returns `importing` or `null` only. Part 2 adds `rules`.
- **Part 2** builds items 8 to 11: the rules step, the drafting step and "Done". ✅ MERGED (#581, 2026-10-03). `onboardingStage` returns `rules` after the import. `components/OnboardingRulesStep.tsx` draws it, and its decisions are in `lib/onboarding.ts`.
- **Part 2 offers "Process past emails" (owner decision (d), #576).** The automatic rule run touches only mail that arrived after the first enabled rule. So, once a rule exists, the step offers "Sort my imported mail". It opens AI Settings with "Process past emails" from the date of `import_since`, and the dialog counts the mail before it spends a model call.
- **Part 2, the ways out.** "Done", "Skip for now" and the "Skip setup" button send `onboarding_done: true`. The page writes the returned account into the store, so a failed re-read cannot bring the step back (fix round 1).
- **Part 2, the drafting switch (fix round 1).** It shows the stored `draft_replies`, read on each mount, and stays disabled until the read returns or when it fails. It shows only with an enabled reply rule, the rule of `_is_reply_rule` in `rules.py`. Without one, a line names the "Needs Reply" rule. The client match mirrors the two tuples of `rules.py`, and a test parses them.
- **Part 2, the Process past date (fix round 1).** The Rules tab reports when it opened the dialog, and the page clears the date. A return to the Rules tab no longer opens it again.
- **Part 2, "Only new mail" (recorded).** With EM-T6b, a finished import with no imported row offers no "Sort my imported mail". Before EM-T6b, the account API gives no field that tells it after the first day, so the action can show for such a mailbox then.
- **Part 2, a paused mailbox (EM-T6b review).** `onboardingStage` and `isFirstSyncPending` return nothing while sync is off, so no panel freezes. The bar stops at 100% when `import_count` passes `import_estimate`.
- **Part 2, the switch (visual review).** In light mode the OFF track of the email `Toggle` vanished on a white card. Its OFF track is now `bg-muted-foreground/30`, for each of its callers.
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

**Status.** ✅ MERGED #619 (2026-10-04). Four commits on `04a64ba4d`: the narrowing, the build, the status with the visual review, and review round 1. The gateway does not change.

**As built.**

- `lib/storage.ts` holds each decision: the limit, the copy, the MB format, the choices, the kept ids and `removalReducer`. The reducer is the one state machine of the dialog, so a test drives each path with no DOM.
- `shortDate` moved from `lib/onboarding.ts` to `lib/utils.ts`, and `onboarding.ts` re-exports it. `onboarding.ts` and `mailbox.ts` import `storage.ts`, so `storage.ts` reads the date from `utils.ts` with no cycle.
- The storage step is its own component, `components/StorageStep.tsx`. `components/StorageNotice.tsx` draws the two kinds of notice: `limit` with the action, and the gap line of D2.
- The rule of the proxy lives in `src/app/api/email/[...path]/postTimeout.ts`. The set of AI paths moved there with no change, because a Next route file may export only its handlers.
- The dialog calls both routes from the click, never from an effect. React runs an effect twice in development, and a removal must not go twice. Only the timer of the follow-up is an effect.
- The confirm sends the `before` that the preview answered, which is Python ISO text with `+00:00`. `previewOlderMail` encodes it, because a bare `+` in a query reads as a space.
- A 400 or a 404 shows the detail of the gateway. A 500 or a 503 shows "Metorite could not finish the removal. Try again." The dialog then reads the accounts and the preview again, because some chunks can be gone.
- The page shortcuts stop while the dialog is open. Without that, `#` with focus on a dialog button deleted the open mail behind the dialog.
- The month choices have no default, because the act removes mail. The member picks one.
- The dialog names the provider that does not change: Outlook for Microsoft, and Gmail for a Gmail mailbox.
- The switcher mark shows for one mailbox too, as the error mark does. In All inboxes, the notice names a mailbox at the limit only. The gap line shows in the own view of its mailbox.
- "Keep it as it is" keeps at most 50 ids, and the oldest id goes first.
- The scans of `onboarding.test.ts` and `onboardingRules.test.ts` now read `setupStage`, the one stage read of the page. The page still calls `onboardingStage` once.

**Fences.**

- `lib/storage.test.ts`: `email-storage-limit` (A1), `email-storage-copy` (A2, A3), `email-storage-confirm` (A8, A9), `email-storage-follow-up` (A11, A13), `email-storage-meter-store` (A14) and `email-storage-kept` (D6).
- `lib/removeOlderMailDialog.test.ts`: `email-storage-dialog-name` (A4), `email-storage-dialog-copy` (A10), `email-storage-dialog-busy` (A11) and `email-storage-dialog-ui` (D5).
- `lib/onboarding.test.ts`: `email-storage-stage` (A7). `lib/allInboxes.test.ts`: `email-storage-mailbox` (A5, A6) and `email-storage-switcher` (UC-12).
- `src/app/api/email/[...path]/postTimeout.test.ts`: `email-storage-proxy-budget` (A12). It also runs the real POST handler and reads the budget of the gateway call.
- Review round 1 adds `email-storage-no-zero` and `email-storage-one-guard` in `lib/storage.test.ts`. It adds `email-storage-dialog-leaves` in `lib/allInboxes.test.ts`.
- Review round 1 also adds four fences in `lib/removeOlderMailDialog.test.ts`: `email-storage-unconfirmed-view`, `email-storage-follow-up-stops`, `email-storage-one-confirm` and `email-storage-no-zero-flow`. It widens `email-storage-dialog-copy`.

**Review round 1 (2026-10-04).** An independent verifier and an adversarial reviewer read the build. Neither found a path to the wrong mailbox, to the wrong date, or to a second POST. This round closes their findings, item by item.

1. **A stale id stopped the page shortcuts.** The guard read `removingId`, and that id stayed set when the mailbox left the list. The dialog then drew nothing, so its `onClose` never ran.
   - Fix: `removalMailbox` in `lib/mailbox.ts` looks up the mailbox, and the guard reads that value. The page clears an id that names no mailbox.
   - The clear runs during render, as React documents. An effect would add a new error of the lint rule `set-state-in-effect`.
   - Fence: `email-storage-dialog-leaves`.
2. **A missing number read as 0.** The proxy sends `{}` for a 200 body that it cannot read. The mappers read that as 0, so the follow-up said "done", and the result said "removed 0".
   - Fix: `requiredCount` in `lib/api.ts` throws for a count that is not a finite number of 0 or more. A thrown preview is a failed count.
   - A thrown removal has no status, so the dialog follows it (D1). `noMessagesLeft` in `lib/storage.ts` is true only for an exact 0, so NaN cannot end the follow-up.
   - Fences: `email-storage-no-zero` and `email-storage-no-zero-flow`.
3. **No fence held A13 on the view.** A mutation that drew `STORAGE_COPY.failed` after 3 minutes stayed green. Copy that said "could not finish" stayed green too.
   - Fix: a render test draws the phase `unconfirmed` and pins its words. It refuses each failure string, and any word like "fail", "error" or "could not".
   - Fence: `email-storage-unconfirmed-view`.
4. **The copy scan was too narrow.** It matched `delet*` near "outlook" only. The product verb is "remove", and the copy names Gmail too.
   - Fix: the scan reads each value of `STORAGE_COPY`, each drawn text, and each `aria-label` and `title`. It matches "remove" or "delete" with Outlook, Gmail, the provider or the mail server, in either order.
   - `[^.]*` stops at a period, so "This removes mail from Metorite only." and "Your Outlook mailbox does not change." pass. A case proves that the scan can fail.
   - The test pins `confirm`, `action` and `dialogTitle` as literals. Fence: `email-storage-dialog-copy`.
5. **No fence held the cleanup of the poll timer.** The body of the effect is now `followUp` in `components/RemoveOlderMailDialog.tsx`, which takes its calls as an argument.
   - Fix: the cleanup of `followUp` clears the timer. An answer that comes after the cleanup changes nothing.
   - A test with fake timers stops it, then goes past 5 seconds and 180 seconds. No preview runs, and no state changes. Fence: `email-storage-follow-up-stops`.
6. **Two guards decided the POST.** The dialog checked the phase and the preview before the POST, and the reducer did the same check again. The two agreed, but nothing bound them.
   - Fix: `acceptedConfirm` in `lib/storage.ts` asks the reducer. `sendRemoval` sends the POST only for its value, and the confirm button reads the same function.
   - The first version of `acceptedConfirm` read only the phase after the reducer. A refused confirm in the phase `removing` keeps that phase, so the fence found the error.
   - Fences: `email-storage-one-guard` and `email-storage-one-confirm`.
7. **A15.** The visual review below now says where the captures are, and who looked at them.
8. **The spec text.** The status commit cut the first sentence of the paragraph "Narrowed", and this round puts it back. The status line names the real commits and their base. The six new lint errors of this section are gone.
9. **The command palette.** The open points record it as a follow-up.

**Mutation checks (2026-10-04).** For each mutation, a script changed the code, ran the named tests and put the file back. A SHA-256 check proved each restore. All 27 mutations of the build went red. All 16 mutations of review round 1, the rows "R1", went red too.

| Mutation | What it changes | Tests that went red |
|---|---|---|
| A1 | The limit takes `>` in place of `>=` | `storage.test.ts`, 1 |
| A2 | The notice drops "Metorite stopped importing older mail." | `storage.test.ts`, 2 |
| A3, action | The gap line draws the action | `storage.test.ts`, 1 |
| A3, under | Each mailbox under the limit gets the gap line | `storage.test.ts`, 1 |
| A4 | The dialog draws no chip | `removeOlderMailDialog.test.ts`, 1 |
| A5, pool | `storageMailbox` reads a separate mailbox too | `allInboxes.test.ts`, 1 |
| A5, id | The notice opens the dialog for `selectedAccountId` | `allInboxes.test.ts`, 1 |
| A6, banner | The reconnect banner no longer wins | `allInboxes.test.ts`, 1 |
| A6, step | The storage step no longer wins | `allInboxes.test.ts`, 1 |
| A7, meter | The stage `storage` drops the meter test | `onboarding.test.ts`, 3 |
| A7, keep | The stage ignores "Keep it as it is" | `onboarding.test.ts`, 1 |
| A8, late | A late answer for an earlier choice lands | `storage.test.ts`, 1 |
| A8, zero | The confirm is on at 0 messages | `storage.test.ts` and `removeOlderMailDialog.test.ts`, 2 |
| A9 | The confirm sends its own key, not the `before` of the answer | `storage.test.ts`, 5 |
| A10 | The copy says that Metorite deletes mail in Outlook | `removeOlderMailDialog.test.ts`, 3 |
| A11 | A 409 clears the choice | `storage.test.ts` and `removeOlderMailDialog.test.ts`, 2 |
| A12, rule | The removal loses its 120 s | `postTimeout.test.ts`, 2 |
| A12, width | The pattern takes three segments or more | `postTimeout.test.ts`, 1 |
| A13, 502 | A 502 reads as a failure | `storage.test.ts`, 1 |
| A13, time | The follow-up runs with no end | `storage.test.ts`, 1 |
| A13, zero | The follow-up never confirms at 0 messages | `storage.test.ts`, 2 |
| A14, meter | `withRemovalMeter` keeps the old meter | `storage.test.ts`, 1 |
| A14, page | The page writes no answer into the store | `storage.test.ts`, 1 |
| D6 | A refused store throws | `storage.test.ts`, 1 |
| UC-12 | The switcher draws no mark | `allInboxes.test.ts`, 2 |
| Shortcuts | The page shortcuts run under the dialog | `allInboxes.test.ts`, 1 |
| Key | The notice keys on the bare id again | `storage.test.ts`, 1 |
| R1, 1a | The shortcut guard reads `removingId` again | `allInboxes.test.ts`, 2 |
| R1, 1b | The page keeps an id that names no mailbox | `allInboxes.test.ts`, 2 |
| R1, 2a | The preview reads a missing count as 0 | `storage.test.ts`, 1 |
| R1, 2b | The removal reads a missing count as 0 | `storage.test.ts` and `removeOlderMailDialog.test.ts`, 2 |
| R1, 2c | The reducer reads NaN as 0 | `storage.test.ts`, 1 |
| R1, 2d | `noMessagesLeft` reads NaN as 0 | `storage.test.ts`, 3 |
| R1, 3a | The view after 3 minutes draws the failure words | `removeOlderMailDialog.test.ts`, 1 |
| R1, 3b | The words after 3 minutes claim a failure | `removeOlderMailDialog.test.ts`, 2 |
| R1, 4a | The copy says that Metorite also removes the mail in Outlook | `removeOlderMailDialog.test.ts`, 1 |
| R1, 4b | The confirm reads "Remove from Outlook" | `removeOlderMailDialog.test.ts`, 2 |
| R1, 5a | The cleanup keeps the timer | `removeOlderMailDialog.test.ts`, 1 |
| R1, 5b | The effect drops the cleanup of `followUp` | `storage.test.ts`, 1 |
| R1, 5c | An answer after the cleanup still changes the state | `removeOlderMailDialog.test.ts`, 1 |
| R1, 6a | The POST goes on the preview alone | `storage.test.ts` and `removeOlderMailDialog.test.ts`, 3 |
| R1, 6b | The dialog checks the phase and the preview itself again | `storage.test.ts` and `removeOlderMailDialog.test.ts`, 2 |
| R1, 6c | `acceptedConfirm` skips the reducer | `storage.test.ts` and `removeOlderMailDialog.test.ts`, 3 |

**Verification (2026-10-04, after review round 1).**

- `npx tsc --noEmit` exits 0.
- The command below with `src/app/email src/app/api/email src/components src/lib/theme src/lib/nav.test.ts` passes 53 files and 1122 tests.
- The full `npx vitest run` passes 298 of 300 files and 5766 of 5770 tests. The 4 failures are in `layoutBoundary.test.ts` and `reportsLiveOverview.test.ts`.
- Those two files read `ReportsView.tsx`, which this branch does not change. They pass when that file has LF line ends, so the CRLF checkout on Windows causes them.

**Visual review (A15, 2026-10-04).** The rig of the `visual-review` skill ran on the local Next dev server, with each `/api` call stubbed. No gateway, no database and no mailbox took part, so no call reached the removal route. The captures use dark, light, compact density, a changed accent and mobile width.

- The captures show the notice in All inboxes, the switcher marks and each state of the dialog. They also show the storage step, the state after "Keep it as it is" and the gap line.
- The captures exist at review time in a private scratch folder, and not in the repo. The `gh` tool cannot attach an image to a PR. So the PR lists what the captures show, and the orchestrator looked at the notice and the dialog in light mode before the merge.
- The review found a duplicate React key. After "Keep it as it is", the notice and the rules step drew for one mailbox with one key. The keys now carry a prefix, and `storage.test.ts` fences it.
- At mobile width, the action of the notice squeezed the words into a narrow column. The row now wraps, and the action drops below the words.
- Review round 1 changes no drawn state, so the captures stay current.

**Narrowed (orchestrator, 2026-10-04).** The spec-auditor cleared this slice as GO-NARROWED at `3d11922c2`. The orchestrator accepts the design decisions D1 to D7 below. EM-T6e changes the UI and the BFF proxy only, and the gateway does not change. The gate stays AGENT-SAFE (Gate, above). An agent must not run the removal route on a production mailbox, and the visual review uses the local stack only.

**The two routes (EM-T6c, `transport/storage.py`).**

- The preview is `GET /email/accounts/{id}/storage/older?before=<ISO>`. It answers `{before, messages, bytes}`, and `before` comes back as ISO text in UTC. It writes nothing, and it counts no draft (G1).
- The removal is `POST /email/accounts/{id}/storage/remove-older` with `{"before": "<ISO>"}`. It answers `{before, removed, stored_bytes, storage_limit_bytes}`.
- Both routes answer 400 for a `before` that is not ISO text or not in the past. A value with no zone is UTC. Both answer 404 for an id that is not a UUID, and for a mailbox of another member.
- The removal answers 409 when a sync holds the mailbox for more than 5 seconds. The detail is "A sync is running for this mailbox. Try again when it ends." (`REMOVAL_BUSY_DETAIL`).
- `GET /email/accounts` carries `stored_bytes` and `storage_limit_bytes` for each mailbox. `stored_bytes` is null before the first meter run. A null meter is not at the limit, and a meter equal to or over the limit is at the limit. The limit is the setting in MB times 1,048,576.

**Design decisions (orchestrator, 2026-10-04).** The owner can reverse each one.

- **D1. A long removal.** The BFF gives `accounts/<id>/storage/remove-older` a budget of 120 seconds. A pattern match in a small module makes that choice, and the module has its own test. After a 502, a 504 or a network error from the removal, the dialog says "The removal continues". It reads the preview again every 5 seconds, with the same `before`. At 0 messages, it reads the accounts again and shows the new meter.
- **D1, the end.** After 3 minutes, the dialog says that Metorite cannot confirm the removal. It never reports a failure that it cannot prove. It does not read the meter alone, because the meter does not tell a removal in progress from a removal that stopped. The gateway does not change, and it gets no 202.
- **D2. The gap.** A mailbox in the phase `limit` under the limit shows one line with no action. For example: "Metorite imported this mailbox back to 14 Sep. It stopped there at the storage limit." EM-T6f owns a path that resumes the import (Order, item 7).
- **D3, which notice.** With one mailbox in view, Email shows the notice of that mailbox only. In All inboxes, Email shows one notice, for the first pooled mailbox at the limit in the order of the list. `storageMailbox` in `lib/mailbox.ts` decides it, beside `attentionMailbox`. A separate mailbox shows its notice in its own view only.
- **D3, the switcher.** The switcher marks each mailbox at the limit, a separate one too. EM-T6e owns this mark of UC-12 (§11.5). The mark is a `Badge` or an icon with an `aria-label`, in the `warning` tone.
- **D3, the name.** With two or more mailboxes, the notice and the dialog draw `MailboxChip` and the address, and the copy names the label. With one mailbox, the copy says "This mailbox". The dialog calls both routes with the id of the mailbox that it names, never the selected mailbox or `poolHome`.
- **D3, the reconnect banner wins.** Email shows no storage notice for the mailbox that `attentionMailbox` names. It shows none for a mailbox while its stage is `storage`, because the storage step names it.
- **D4. The copy.** See "The words" below.
- **D5. The UI contract.** The dialog is `Modal` with `Button`s, as `MailboxEditDialog.tsx` is, because `ConfirmDialog` cannot disable its confirm. The date is `Input type="date"`, with `max` today. The month choices are a radio group of `Button`s, as in `ImportRangeStep.tsx`. The notice uses the `warning` tokens of the reconnect banner, with no raw amber. No file outside `src/components/ui/` imports `@base-ui/react`, and no `fixed inset-0` div draws.
- **D6. The stage.** See scope item 4.
- **D7. Load older.** No change.

**Scope.**

1. **The fields.** `EmailAccount` in `lib/types.ts` gains `storedBytes?: number | null` and `storageLimitBytes?: number`. `mapAccount` in `lib/api.ts` maps them with `optionalCount`. When the gateway sends no `storage_limit_bytes`, Email draws no storage UI. The comment of `importPhase` names `limit`.
2. **The calls.** `lib/api.ts` gains `previewOlderMail(accountId, before)` and `removeOlderMail(accountId, before)`.
3. **The decisions.** A new pure `lib/storage.ts` holds `atStorageLimit`, the copy, the MB format, `keepNewestBefore(months, now)` and the state of the D1 follow-up. `storageMailbox` goes in `lib/mailbox.ts`.
4. **The stage (D6).** `onboardingStage(account, { storageKept })` returns `storage` only when four things are true. The phase is `limit`, and the meter is at the limit. `onboardingDone` is false, and the member did not choose "Keep it as it is".
   - "Keep it as it is" stores the id of the mailbox in `localStorage`. It stores ids only, with a `try` around each read and write. The stage then moves to `rules`, and the notice stays.
   - After a removal that goes under the limit, the stage moves to `rules`, also with a gap.
   - `page.tsx` draws the storage step between the import panels and the rules step. The step offers the dialog and "Keep it as it is".
5. **The notice (D3, D2).** `components/StorageNotice.tsx` draws below the reconnect banner in `page.tsx`.
6. **The dialog.** `components/RemoveOlderMailDialog.tsx` is built on `Modal`. The member keeps the newest 1, 2, 3 or 6 months, or picks a date. The dialog calls the preview, then the removal, and it shows the 409 detail and the D1 follow-up. On success, it writes the new meter into the store and reads the accounts again.
7. **The proxy budget (D1).** `src/app/api/email/[...path]/route.ts` reads the budget of a POST from the new module.
8. **The switcher mark (UC-12).** `components/AccountSidebar.tsx` marks each mailbox at the limit.

**The words (D4).**

- The notice: "This mailbox uses 512 MB of its 500 MB in Metorite. Metorite stopped importing older mail." The action: "Remove older mail from Metorite". With two or more mailboxes, the label takes the place of "This mailbox".
- The dialog: "This removes mail from Metorite only. Your Outlook mailbox does not change." Then: "Your rules, senders and unsent drafts stay."
- The note: "Load older can import this mail again, until the mailbox is at its limit."
- The preview: "12,400 messages, about 380 MB". One MB is 1,048,576 bytes.
- A 409 shows the detail of the gateway as it is.
- `before` is the ISO instant of local midnight of the chosen day, never a bare date.

**Non-goals.** No gateway change, and no 202. The BFF proxy may change (D1). No removal without a preview. No change to Load older (D7). No resume of the import (EM-T6f).

**Done when.**

- The notice draws for `stored_bytes` at the limit, and for `import_phase = 'limit'`. In the phase `limit` under the limit, it draws the gap line of D2 with no action. It does not draw under the limit in any other phase.
- The dialog calls the preview before the member can confirm. The confirm sends the `before` of that preview, the ISO instant of local midnight of the chosen day.
- The dialog markup holds the sentence "Your Outlook mailbox does not change."
- No copy in the notice or the dialog says that Metorite deletes mail in Outlook.
- `onboardingStage` returns `storage` for `import_phase = 'limit'` when `onboarding_done` is false, the meter is at the limit, and the member did not keep it (D6).
- A1. `atStorageLimit` is false for a null or absent meter, for an absent limit, and under the limit. It is true for a meter equal to or over the limit.
- A2. For 512 MB of 500 MB, the notice reads "This mailbox uses 512 MB of its 500 MB in Metorite. Metorite stopped importing older mail."
- A3. Under the limit with the phase `done`, no notice draws. In the phase `limit` under the limit, the gap line draws with no removal action.
- A4. With two mailboxes, the notice and the dialog draw the chip and the address, and the copy names the label. With one mailbox, the copy says "This mailbox".
- A5. In All inboxes, `storageMailbox` is the first pooled mailbox at the limit, and never a separate one. The dialog calls both routes with the id of that mailbox, not the id of `poolHome`.
- A6. No notice draws for the mailbox that `attentionMailbox` names, or for a mailbox in the stage `storage`.
- A7. The stage table of D6: `storage` in the phase `limit` at the limit. The stage is `rules` under the limit and after "Keep it as it is". It is `null` with `onboardingDone`.
- A8. The confirm stays disabled until the preview of the current choice answers, and at 0 messages. A late answer for an earlier choice does not enable it.
- A9. The confirm sends the `before` of the preview that answered.
- A10. The dialog holds the Metorite-only copy. No text in the notice or the dialog says that Metorite deletes mail in Outlook.
- A11. A 409 shows the detail of the gateway, and the choice stays.
- A12. The proxy gives 120,000 ms to `accounts/<id>/storage/remove-older` only. Each other POST keeps its budget.
- A13. After a 502, a 504 or a network error, the dialog follows the removal (D1). It reads the preview every 5 seconds, confirms at 0 messages, and says that it cannot confirm after 3 minutes. It never reports a failure.
- A14. After a removal, the store carries the `stored_bytes` of the answer, and the page reads the accounts again.
- A15. A visual review on the local stack shows the notice, the dialog, the switcher mark and the step. It uses light mode, compact density and a changed accent.

**Files.** Under `workbench/control_plane/src/app/email/`, these change: `lib/types.ts`, `lib/api.ts`, `lib/onboarding.ts`, `lib/onboarding.test.ts`, `lib/mailbox.ts`, `page.tsx` and `components/AccountSidebar.tsx`. These are new: `lib/storage.ts` with its test, `components/StorageNotice.tsx`, `components/RemoveOlderMailDialog.tsx`, and a step component or a variant of the notice. Also `src/app/api/email/[...path]/route.ts`, and a new module for the rule of the proxy, with its test.

**Verify with.**

```bash
cd workbench/control_plane
npx tsc --noEmit
npx vitest run src/app/email src/app/api/email src/components src/lib/theme src/lib/nav.test.ts
npx vitest run
node ../../.claude/hooks/ste-lint.mjs --staged
```

**Open points.**

- The notice has no close button. The owner can reverse this.
- **(Visual review) The app shell remounts the page at the mobile width.** A change of the window across that width closes an open dialog. Every dialog of the page does the same, so this belongs to the shell, not to EM-T6e.
- **(Visual review) The compact switcher cuts a label short.** At compact density, a separate mailbox at the limit shows "Cl…" beside the "Separate" badge and the mark.
- **(Visual review) No capture beside Calendar.** The done-when of EM-T6d named a view beside Calendar. This review did not capture one.
- **(Review round 1) Cmd+K opens over a dialog.** The command palette opens above any dialog of the page, and it offers "Delete" for the open mail. This is older than EM-T6e. A follow-up for the page shortcuts owns it.
- **EM-T6f, a resume path (backend, recorded).** Nothing resumes the import of a mailbox in the phase `limit` under the limit (EM-T6c, review round 1, item 3). The candidate of the auditor: a route sets `initial_sync_done = false` and `import_phase = 'importing'` for such a mailbox, so `_run_import` resumes at `import_reached_at`. It needs R8 and its own audit.

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
- **R-10. An open at the limit (answered, Q4).** The open path stores the body that it loads (`transport/messages.py:674-687`). The owner decided on 2026-10-02 that at the limit an open shows the body and stores nothing. EM-T6c adds that check to the open path. A reopen at the limit loads the body live again.

#### 10.4.8 EM-T5b in full

**Status.** ✅ EM-T5b-1 and EM-T5b-2 (narrowed) MERGED (#576, 2026-10-02), as ONE PR. ✅ EM-T5b-2 in full MERGED (#593, 2026-10-03), with review fix round 3. Its three features stay OFF in production until the owner's go. EM-T5b-3 and EM-T5b-4 are SPEC ONLY. The audit read each anchor at `01d760e6`. EM-T5b has four parts, and each part is one PR. D-EM-7 to D-EM-9 are the decisions. The "Question conventions" of `customer_console.md` §6A.14 are the contract for each question.

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

Not built in this narrowing: the thread status, the cold check and the sender pin in `on`, the startup check (item 9 below), the `· auto` change (item 7 below) and the docstrings of `acb_llm/decide.py` (item 10 below). EM-T5b-2 in full builds them (below).

The fences are `tests/unit/test_email_decide_on.py` (R8 for the runner, Process past and the floor), `tests/unit/test_email_assistant_settings.py` (R8 for the stored `rule_model`) and `workbench/control_plane/src/app/email/lib/noRuleModel.test.ts`.

**Production state (orchestrator report, 2026-10-02).**

- `DECIDE_ENABLED=true` since 12:16 UTC.
- `DECIDE_FEATURE_MODES=email.rule_match=on` and `DECIDE_FEATURE_ORGS=*` since 16:31 UTC.
- The first live `decide.decided` line came at 16:50:47 UTC. It was for account `21e57cfd`, with 11 rules, 1 request and 9 questions, in 940 ms.
- In that line, `best` was `r5` at 0.91, and `member_verified` was 1.
- The orchestrator saw 0 refusals at the time of its report.

**EM-T5b-2 in full, as built (2026-10-03).** The owner's direction is that the decide tier does email triage, rules and cleanup (D-EM-7, D-EM-8). This part builds what the narrowing left out:

1. `ON_FEATURES` holds the four email features. `on` for any other name resolves to `off` and logs `decide.mode_refused`.
2. **The thread status.** In `on`, `replyzero._decide_thread_status` asks the `status` choice, and the choice decides. An answer outside the options is no decision.
3. With no status, `_decide_thread_status` raises `DecisionUnavailable`. `resolve_conversation_status_matches` passes it through its broad handler, so the runner skips the row. `recompute_thread_status` writes nothing and returns None, so `_mark_thread_replied` leaves the labels.
4. **The cold check.** An email is cold at 0.5 or above when the blocker labels. When the blocker archives, the bar is 0.7 (fix round 3). With no decision it is not cold: no row, no label and no archive. The rule outcome stands, and the runner stamps the message.
5. **The sender pin.** A pin needs 0.9 or above. With no decision there is no pin. The rule still applies, and the runner stamps the message.
6. **The member.** `engine._decide_member(db, account_id, feature)` reads the owner for each feature in `on`. Each site gives it to `ask` as a proven member.
7. **The cool-down** of fix round 2 is for the organization, so it covers all four features. A 402 from the cold check also stops the status and the pin calls.
8. **`· auto`.** A decided status is confident, so it never gets the tag. The backfill checks an old `· auto` row once more, and then leaves it. With no decision the row keeps the tag, and the next cycle asks again.
9. **The startup check.** `register_email_post_sync_hooks` runs `scheduler_hooks.check_decide_wiring()` once. It logs `email.decide_not_wired` at error level when a feature is `on` and `decide_enabled` or `router_is_wired()` is false. It reads the wiring through a new probe, `acb_llm.routed.router_wired()`. The dependency fence admits eight importers of `console_resolve`, and it pins the `decide` facade to two names, so the gateway may not import it here.
10. With no organization in `DECIDE_FEATURE_ORGS`, no feature is `on`, so the check of item 9 logs nothing.
11. The docstrings of `acb_llm/decide.py` say that email leaves an email undecided, with no LLM call.
12. `decide.decided` logs our own keys and numbers only. The status logs `answer`, `confidence`, `margin`, `options`, `p_answer`, `moves` and `move_bar_met`. The cold check logs `p_cold`, `cold` and `threshold`, and the pin logs `p_always` and `pin`.
13. The sent rows of the Reply Zero backfill keep the new-mail floor too (fix round 3, item 5 below). Item 7 of the narrowed record gave them no floor.

The fences are the R8 classes `TestTheThreadStatusOnJev`, `TestTheColdCheckOnJev` and `TestTheSenderPinOnJev`, and the hermetic cases, in `tests/unit/test_email_decide_on.py`.

**To turn the three features on, after the owner's "go".** The orchestrator sets these two values on the box and restarts the gateway:

```text
DECIDE_FEATURE_MODES=email.rule_match=on,email.thread_status=on,email.cold_check=on,email.sender_pin=on
DECIDE_FEATURE_ORGS=*
```

⚠️ Use the names in `decide_features.FEATURES`. `email.cold_sender` and `email.pin` are not feature names. A wrong name logs `decide.mode_refused` and stays `off`.

**Fix round 3 (review, 2026-10-03).** The house rule for a bar is 0.5, 0.7 for a decision that moves mail, and 0.9 for a pin. Items 1 and 2 build the two findings that the first build left open.

1. **A cold check that archives needs 0.7.** `senders._cold_threshold` gives 0.7 when the blocker is `ARCHIVE`, and 0.5 when the blocker labels. `_cold_blocker_moves` is the one test, so the archive and the bar cannot disagree. The `shadow` line uses the same bar.
2. **A thread status that moves mail needs 0.7.** The status selects a conversation rule, and the runner runs its actions. When that rule moves mail, the status needs 0.7 on its own option (`replyzero._STATUS_MOVE_THRESHOLD`).
   - Under the bar the rule does not run, and the per-message matches stand, as for a status with no enabled rule. The log line is `email.thread_status_under_move_bar`.
   - A status whose rule only labels keeps the plurality of the choice. `recompute_thread_status` runs no rule, so it has no bar.
3. **The status comes first when it is sure.** `classify_matches` calls `replyzero.status_before_match` before the rule match. The status is sure when the mailbox has an enabled conversation rule and the thread is already a conversation.
   - Then a missing status raises `DecisionUnavailable` before the rule match is paid. The email stays undecided, and the next cycle asks again (D-EM-8).
   - For another thread, the resolver asks only after the rule match picks a conversation rule. An earlier ask would pay for each email that matches no conversation rule.
4. **No conversation rule, no status call.** With no enabled conversation rule (`replyzero._enabled_conversation_rules`), the resolver asks no status.
5. **The sent rows keep the floor.** The Reply Zero backfill selects a sent gap thread only when its latest message arrived at or after `NEW_MAIL_FLOOR_SQL`. With no enabled rule it selects none.
   - The filed rows keep no floor. They get a fixed FYI, with no model call and no provider write.
   - "Reclassify" reuses the backfill, so it no longer reaches older sent threads either.
6. `off` and `shadow` keep the old resolver path and the old order. In those modes `status_before_match` reads nothing.

The fences are in `tests/unit/test_email_decide_on.py`. The hermetic cases are `test_an_archiving_cold_blocker_needs_the_move_bar`, `test_the_cold_shadow_line_uses_the_bar_of_the_blocker`, `test_a_status_whose_rule_moves_mail_needs_the_move_bar`, `test_the_status_is_asked_before_the_rule_match`, `test_a_missing_status_costs_no_rule_match` and `test_no_enabled_conversation_rule_means_no_status_call`. The R8 cases are in `TestTheThreadStatusOnJev`, `TestTheColdCheckOnJev` and `TestTheReplyZeroBackfillOnJev`. A temporary mutation of each fix made its fences fail.

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
| Cold check, the blocker labels | 0.5 | The old prompt asked for no margin |
| Cold check, the blocker archives | 0.7 | An archive moves mail (fix round 3) |
| Sender pin | 0.9 | The old prompt asked for 90% sure, and a pin is permanent |
| Thread status, its rule labels | none | The choice decides. The log keeps the confidence |
| Thread status, its rule moves mail | 0.7 | The status runs the move of its rule (fix round 3) |

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
- The EM-T4b cap wraps the `decide` await in `_ask_all` (`decide_features.py:512-518`), with one permit for each call (B8).

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
  tests/unit/test_email_decide_on.py tests/unit/test_email_assistant_settings.py \
  tests/unit/test_email_decide_shadow.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/email src/components/email
```

The changed automation files carry old ruff findings, so compare them with the base for each file and code, as §10.4.4 says. EM-T5b-2 in full also changes `scheduler_hooks.py`, `packages/acb_llm/acb_llm/decide.py` and `packages/acb_llm/acb_llm/routed.py`, so compare those three as well. The 8 old RUF100 findings of `scheduler_hooks.py` must stay 8.

The R8 cases must show PASSED, not SKIPPED.

**After the merge.** ⚠️ **The three features stay off in production until the owner says "go".** Decision (c) of §10.2 kept the thread status, the cold check and the sender pin on the old path for the demo, and no later decision of the owner turns them on for all organizations. The verifier of fix round 3 found that gap on 2026-10-03. Each `on` adds paid `decide` calls for each mailbox. When the owner says "go", record it as a dated decision in §10.2. Then set the value of "To turn the three features on" above, and report the act, the box and the evidence in the same message. The evidence is one `decide.decided` line for each feature, each with a `request_id`.

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

#### 10.4.10 EM-T9 — a file on an Outlook draft (a LIVE defect)

**Status.** ✅ MERGED (#643, 2026-10-05). The live check of the owner is still open. The audit of
2026-10-05 gave GO-NARROWED (B1 to B7, N1 to N9), and it checked each anchor against the code on
that day. "As built" below records the build.

**Why it went first.** The audit of EM-G3c found the defect (its C21). Outlook is the live
provider, so this slice went before the Gmail work that waits for customers.

**Gate.** 🟢 AGENT-SAFE build. No migration, no flag and no change to SQL text, so R8 does not bind
it. The fix changes the live Outlook path, so it takes the full review loop for data. The live check
after the merge sends real mail, so it is 🔴 OWNER-GATE (CLAUDE.md §3a rule 3).

**Size.** M.

**The defect (LIVE on Outlook).** `_attach_files` (`outlook.py:944-965`) adds each file in one
POST of a `fileAttachment`. It never reads the answer, and it drops each error with `continue`.
Graph takes a file under 3 MB in one POST only. So a larger file fails at Graph, and the draft
keeps no copy of it.

**Why the member sees nothing.** The composer sends a mail with files through a draft. The save
before the send adds the files (`drafting.py:2167-2171`), then `drafts/send` sends the draft. So
a member can send a mail with no file, and nothing tells the member. The three tests of
`test_email_draft_attachments.py:43-84` reach `_attach_files` with fakes that answer 200, so none
of them sees a failed file.

**What Microsoft documents (`learn.microsoft.com/graph/outlook-large-attachments`).**

- A file under 3 MB goes in one POST to the `attachments` collection, and Graph answers 201. A file
  of 3 MB to 150 MB needs an upload session.
- An upload session refuses a file under 3 MB, with the code
  `ErrorAttachmentSizeShouldNotBeLessThanMinimumSize`.
- The `uploadUrl` is pre-authenticated, with a token in its query. Each PUT to it must carry no
  `Authorization` header.
- Each PUT carries `Content-Range: bytes {start}-{end}/{total}`, `Content-Length` and
  `Content-Type: application/octet-stream`. Each range stays under 4 MB, in order.
- An earlier PUT answers 200 with `nextExpectedRanges`. The last PUT answers 201.

**The callers (B4).** Each caller passes `{filename, content: bytes, mime_type}`, so `len(content)`
is the true size.

- `PUT /email/drafts` (`upsert_draft`, `drafting.py:2174-2285`): the update branch at
  `:2216-2240`, and the reply and new branches at `:2265-2275`.
- The rule actions REPLY and DRAFT_EMAIL (`actions.py:545`) and FORWARD (`actions.py:589`). Their
  `except Exception` at `actions.py:609-612` records `str(exc)` in `errors_out`.
- `drafts/send` adds no file (`drafting.py:2361-2369`). The composer awaits the save before the
  send in one `try` (`ComposePanel.tsx:323-335`, `EmailDetail.tsx:648-660`), and `gatewayFetch`
  throws on a status that is not 2xx (`api.ts:41-48`). So a failed save stops the send in the
  composer. `fromRow.test.ts:196-198` already pins that order.

**Scope.**

1. **A small file (B5).** A file under 3,000,000 bytes goes in one POST, as today. The code then
   reads `status_code`, and each status that is not 2xx raises. Graph answers 201, so a check for
   `== 200` is wrong.
2. **A large file.** A file of 3,000,000 bytes or more goes through
   `POST /me/messages/{id}/attachments/createUploadSession`. The body is
   `{"AttachmentItem": {"attachmentType": "file", "name", "size", "contentType"}}`. Then PUTs of
   2 MiB ranges go to the `uploadUrl`, in order, until the last PUT answers 201.
3. **The minimum size (B6).** "3 MB" can mean 3,000,000 or 3,145,728 bytes. So a session that
   Graph refuses with `ErrorAttachmentSizeShouldNotBeLessThanMinimumSize` falls back to one POST.
   The code reads that refusal from `error.code` in the JSON body.
4. **The upload loop (B6).** The PUTs go through `httpx.AsyncClient(timeout=30.0)`. The code makes
   that client at the time of the call, inside `async with`. It has no auth and no
   `Authorization` header. The client of
   Graph applies its auth to each host (`outlook.py:466`), so it must not send the PUTs.
5. **The checks of the loop (B6).** A 200 whose `nextExpectedRanges` does not start at the next
   offset raises. A 201 before the last range raises, and a 200 after it raises. A 429 or a 5xx on
   a PUT raises, with no retry. The two POSTs of Graph keep `client.post` with no retry. They do
   not go through `_graph_send`, because its wait can reach 30 seconds.
6. **The token stays out of each log and error (B2).** httpx logs each request at INFO, with its
   full URL. The gateway logs at INFO. So a filter on the `httpx` logger
   removes the query of an upload URL from each record. The code never calls `raise_for_status()`
   on an upload answer, and it raises `from None`.
7. **The error.** A failed file raises `ProviderAttachmentFailed`, a new class in `base.py` beside
   `ProviderRateLimited` (`base.py:24`). It subclasses `Exception` only, never
   `httpx.HTTPStatusError`, and it carries the name of the file and no URL.
8. **The route (B1).** `upsert_draft` answers 502 with a string detail, as `assistant.py:671-672`
   does, through a mapper of the shape of `drafting.py:2129-2139`. The text is "The file <name>
   could not be attached. The mail was not sent." The route writes no local row.
9. **A new draft with a failed file (B3).** `create_draft` deletes the new draft before it raises,
   at the two sites of `outlook.py:873` and `:884` only. The delete never goes in `_attach_files`
   or `update_draft`, which share the helper with a draft that the member already has. A failed
   delete is dropped, and the first error raises.
10. **An empty draft id (B3).** `create_draft` raises before any file when Graph returns no id
    (`.get("id", "")` at `outlook.py:868` and `:883`).
11. **A rule action (B4).** A rule draft with a failed file is deleted, and the action records the
    error in `errors_out`. Before the fix, the rule made a draft with no file.
12. **A file of 0 bytes (N7).** It goes in one POST, and a refusal raises with its name. A file with
    `content=None` raises, in place of the `or b""` at `outlook.py:953`.

**Non-goals.** No change to Gmail or IMAP. No change to the composer or to `drafts/send`. No change
to the cut of 10 MiB in the Next proxy, which EM-G3c owns (§12.3.12 item 3). No change to
`send_message`.

**Files (N5).** `providers/outlook.py`, `providers/base.py`, `routes/email/automation/drafting.py`,
`apps/services/gateway/AGENTS.md:63` (the rules of the draft path get the new 502), and the new
test file. The build also added contract 13 to `apps/services/email_ingestion/AGENTS.md`, because
the root DOX rule asks for it.

**Known limit EM-T9-f1.** A retry after a failed file adds again each file that the first try
added. The composer sends the files again at each send (`ComposePanel.tsx:332`,
`EmailDetail.tsx:657`). A later slice owns the dedupe, with EM-G3c-f2.

**Known limit EM-T9-f2.** A direct send (`POST /email/send`, `send_message` at
`outlook.py:804-817`) puts its files inline in `sendMail`. Graph limits that request to 4 MB. So
a direct send with 3 MB of files or more fails, and the error reaches the caller. It is not silent.
The chat tool `send_email` takes this path (`agents.py:1784`).

**Known limit EM-T9-f3.** The Next proxy cuts a body over 10 MiB. So a file over about 7.4 MiB
never reaches the gateway, for each provider. EM-G3c-2 adds a pick limit for it.

**Known limit EM-T9-f4.** The proxy stops a draft save after 30 seconds (`route.ts:156`). A slow
upload of large files can pass that limit. Then the composer sends nothing, and the gateway still
finishes the save. On the create path, a retry then makes a second draft.

**Known limit EM-T9-f5 (N6).** A failed save of a draft that exists leaves the new text and each
file added before the failure on the Outlook draft. ConversationView, or the chat tool
`send_draft` (`agents.py:1815-1834`), can send that draft later.

**Known limit EM-T9-f6 (N6).** One POST of a file near 3 MB can get a 413, because base64 makes
it about 4 MB. The code raises, so the failure is not silent.

**Known limit EM-T9-f7 (B3).** A deleted new draft goes to Deleted Items, as `trash_message` does
(`outlook.py:1062`). So the sync shows it in Trash. The slice keeps the normal DELETE, because it
can be undone.

**Advisory EM-T9-a1 (N8).** A raise inside `provider_session` skips `_persist_rotated_creds`
(`core.py:332-340`). A token refresh during a failed save then loses the new token. The risk is
low, because Microsoft keeps the old refresh token valid.

**Fences (R7).** A new `tests/unit/test_outlook_attachments.py`. The auth and log fences reuse
`wire` (`test_email_provider_401_retry.py:236-255`). The body fences reuse the pattern of
`test_email_draft_attachments.py:23-40`, and the route fence reuses `_route_patches`
(`test_gmail_send_and_drafts.py:691-705`).

- `test_a_small_file_goes_in_one_post_and_201_passes`
- `test_a_large_file_goes_through_an_upload_session_in_ranges`
- `test_the_upload_puts_carry_no_authorization_header`
- `test_a_session_refused_for_the_minimum_size_falls_back_to_one_post`
- `test_a_range_that_graph_did_not_expect_raises`
- `test_a_failed_file_raises_with_its_name`
- `test_a_failed_file_on_a_new_draft_deletes_the_draft`
- `test_a_failed_file_on_an_existing_draft_keeps_the_draft`
- `test_the_upload_token_never_reaches_a_log_or_a_traceback`. It runs caplog at DEBUG on each
  logger, and it reads `traceback.format_exception(exc)`.
- `test_a_failed_save_answers_502_and_writes_no_local_row`
- `test_the_signed_send_adds_no_file`
- In `test_email_rule_action_failures.py`: `test_a_rule_draft_with_a_failed_file_is_deleted`

**Mutations.** M1 restores `continue`, and the failed-file test fails. M2 sends each file in one
POST, and the session test fails. M3 sends the PUTs through the Graph client, and the auth test
fails. M4 drops the fallback, and the minimum-size test fails.

**More mutations.** M5 puts the URL into the error text, and the token test fails. M6 removes the
log filter, and the token test fails. M7 moves the delete into `_attach_files`, and the test of
the existing draft fails. M8 checks `== 200`, and the small-file test fails.

**Verify with (B7).**

```bash
uv run pytest tests/unit/test_outlook_attachments.py tests/unit/test_email_draft_attachments.py \
  tests/unit/test_outlook_drafts.py tests/unit/test_outlook_draft_cc.py \
  tests/unit/test_email_provider_401_retry.py tests/unit/test_email_rule_action_failures.py \
  tests/unit/test_gmail_send_and_drafts.py -v -rs
uv run ruff check apps/services/email_ingestion/email_ingestion/providers/outlook.py \
  apps/services/email_ingestion/email_ingestion/providers/base.py \
  apps/services/gateway/gateway/routes/email/automation/drafting.py \
  tests/unit/test_outlook_attachments.py
cd workbench/control_plane && npx vitest run src/app/email/lib/fromRow.test.ts
```

**The live check after the merge (🔴 OWNER-GATE, N9).** From a connected Outlook mailbox, the owner
sends three mails to the owner's own address. One has a file of 5 MB, one has a file of 1 MB, and
one has no file. Each mail must arrive, with its file. An agent must not send real mail
(CLAUDE.md §3a rule 3).

**As built (2026-10-05).** Branch `email-outlook-t9`. The slice takes no migration, no flag and no
change to SQL text.

- `base.py` holds `ProviderAttachmentFailed(filename)`. Its text is "The file <name> could not be
  attached." It subclasses `Exception` only.
- `outlook.py` holds the path of a file in three module functions: `_post_file`, `_upload_file`
  and `_put_ranges`. `_attach_files` calls them, and it never deletes a draft.
- `create_draft` calls `_require_new_draft_id` after each `.get("id", "")`. It calls
  `_attach_to_new_draft` at its two sites, and only that helper deletes a draft.
- A draft with no file sends the same requests as before. The POST of a small file keeps its body,
  byte for byte. The fakes of `test_email_draft_attachments.py` pass with no change.
- `drafting.py` holds the mapper `_file_not_attached` and the text `file_not_attached_detail`.
  `upsert_draft` puts the mapper on the update, the reply and the new branch.
- `actions.py` has no change. Its `except` records the text of the error, and that text holds the
  file name only.
- Two new log lines name no URL and no file. `outlook.attachment_failed` gives the stage, the
  reason and the size. `outlook.new_draft_delete_failed` gives the reason.

**Four readings of the spec.** The spec did not decide these, so the build took the safe side.

1. The PUT answers come from an Outlook REST URL. The page of Microsoft spells their key
   `NextExpectedRanges`, and Graph spells it `nextExpectedRanges`. The code reads the key in any
   case. A 200 with no ranges raises.
2. An empty draft id raises only when the draft has a file. A draft with no file keeps the
   behaviour of before.
3. A transport error on any request of a file raises `ProviderAttachmentFailed` too. The code
   raises it outside the `except` block, so no error of httpx chains to it.
4. A `content` that is not bytes raises, for `None` and for text. Before, the code sent `None` as
   an empty file and dropped text with no error.

**A finding outside the plan (B2).** litellm loads in the gateway through `acb_llm`. At load, it
sets the `httpx` logger to WARNING and adds a filter that writes `?REDACTED` over each query. So
litellm hides the leak of B2 today. The filter of `outlook.py` does not depend on that, because an
upgrade of litellm or `LITELLM_DISABLE_REDACT_SECRETS=true` removes it. The token fence keeps
only the filter of `outlook.py` on the logger, because the filter of litellm first hid M6.

**Mutations (2026-10-05).** A script changed `outlook.py` and ran the named fence and both test
files. Then it put the file back with `git checkout --`, and `git diff` was empty each time. All 8
mutations turned the named fence red. The count in brackets is the count of both files.

| # | Mutation | Fence | Result |
|---|---|---|---|
| M1 | `_attach_files` drops each failure with `continue` | `test_a_failed_file_raises_with_its_name` | red, 9 failed (29) |
| M2 | Each file goes in one POST, with no session | `test_a_large_file_goes_through_an_upload_session_in_ranges` | red, 4 failed (19) |
| M3 | The PUTs go through the Graph client | `test_the_upload_puts_carry_no_authorization_header` | red, 1 failed (1) |
| M4 | No fallback for the minimum size | `test_a_session_refused_for_the_minimum_size_falls_back_to_one_post` | red, 1 failed (1) |
| M5 | The upload URL goes into the text of the error | `test_the_upload_token_never_reaches_a_log_or_a_traceback` | red, 3 failed (13) |
| M6 | No log filter at load | `test_the_upload_token_never_reaches_a_log_or_a_traceback` | red, 3 failed (4) |
| M7 | The delete moves into `_attach_files` | `test_a_failed_file_on_an_existing_draft_keeps_the_draft` | red, 1 failed (4) |
| M8 | Only a 200 counts as a success | `test_a_small_file_goes_in_one_post_and_201_passes` | red, 2 failed (18) |
| M9 | The end of a range loses its `- 1` | `test_a_large_file_goes_through_an_upload_session_in_ranges` | red, 8 failed (verifier P2-1) |

**The verification (2026-10-05).** The verifier passed the slice, with no P0 and no P1. It recorded
each Graph call of `create_draft` and `update_draft` on main and on the branch. It used 18 cases
with no file or with small files, and all 18 were the same. It also ran the 13 R8 cases on the scratch
database, and 184 passed. Its P2-1 added the edges of 4 MiB and 4 MiB + 1 byte to the range fence.

**Verified (2026-10-05).** The pytest command of B7 ran 171 passed and 13 skipped. The 13 skips
are R8 cases of `test_email_provider_401_retry.py` and `test_gmail_send_and_drafts.py`, and R8
does not bind this slice. The ruff count of the four files of B7 fell from 69 to 67, with no
finding on an added line. `fromRow.test.ts` ran 32 passed. The run of `test_email_*`,
`test_outlook_*` and `test_gmail_*` ran 2606 passed and 390 skipped, each skip an R8 case.

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

---

## 11. Several mailboxes for one member — multi-inbox (2026-10-03)

> **Owner request, 2026-10-03.** Plan the connection of several mailboxes in the Email app of
> one member, as a product manager would. Find the use cases and the edge cases. Document the
> feature, then start to build it.

> **The two questions of the owner.** How does the AI manage context between the mailboxes? How
> does the member always know which mailbox a mail is shown in, received in and sent from?

> **This section wins over §1** where §1 says "Multi-account / multi-provider parity is not a
> near-term goal". It does not change D-EM-5: Outlook stays the only provider in the connect flow.
> The design is the same for Gmail when D-EM-5 changes.
> **2026-10-04:** D-EM-5 changed. §12 brings Gmail into this design.

### 11.0 The answer, in five rules

1. **A mailbox is the boundary of the AI context.** Each AI act reads and writes one mailbox. The
   one exception is the All inboxes scope of the list, of search and of chat. That scope reads
   the mailboxes of the member, and it names the mailbox of each result (§11.3).
2. **A mail belongs to one mailbox, and each act on that mail runs in that mailbox.** The selected
   view never decides it. The mailbox comes from the mail itself, in the UI and on the server.
3. **A reply goes out from the mailbox that received the mail.** Each composer shows a From row
   when the member has two or more mailboxes. A change of From shows a warning before the send.
4. **Each mailbox has a name and a colour, and each surface that can show two mailboxes shows that
   chip.** The colour is never the only signal. The name of the mailbox is always next to it.
5. **A setting or a rule always names one mailbox.** No setting applies to all mailboxes by
   surprise. A copy from one mailbox to another is an act that the member starts.

A member with ONE mailbox sees no change. The All inboxes scope, the chips and the From row show
only when the member has two or more mailboxes.

### 11.1 Measured state (2026-10-03)

**Production.** Two mailboxes are connected. Each one belongs to a different member in a
different organization. No member owns two mailboxes yet, so no defect below has fired in
production. The address of each mailbox is the sign-in address of its member. That fact makes
defect MB-1 certain for the next second connect.

**The backend is mostly ready.** Each child table of a mailbox has `account_id`, with
`ON DELETE CASCADE`. The rules, the assistant settings, the voice profile, the learned patterns,
the sender lists, the knowledge, the contacts and the drafting memory are per mailbox.
`GET /email/messages`, `/email/search`, `/senders` and `/analytics/overview` read all the
mailboxes of the member when the request has no `account_id`. A member can own several rows.
`is_default` marks one of them (migration 223), and a delete moves the default to the oldest
mailbox that is left (`transport/accounts.py:612-628`).

**The UI shows one mailbox at a time.** The selected mailbox is `selectedAccountId` in
`emailStore.ts:258`, kept in the URL `?account=` and in local storage. The left rail lists the
mailboxes, with a default star and a `⋯` menu. There is no All inboxes scope and no From row.

**The defects.** Each one is real in the code of `f0264ce8`. The slice that fixes it is in the
last column.

| Id | Defect | Evidence | Slice |
|---|---|---|---|
| **MB-1** | **A second Outlook connect signs in the first mailbox again.** The authorize leg sends the sign-in address of the member as `login_hint`, and it sends no `prompt=select_account`. The callback then takes the reconnect path for the first mailbox. | `transport/oauth.py:169`, `:192-195`, `:462-484` | EM-T8a |
| **MB-2** | **An inline reply or forward sends from the selected mailbox, not from the mailbox of the mail.** The signature, the draft save, the AI draft, the thread load and the send all use `selectedAccountId`. | `EmailDetail.tsx:74`, `:213`, `:279-313`, `:546-627` | EM-T8a |
| **MB-3** | **"Open in inbox" on a chat card does not switch the mailbox.** A mail of mailbox B opens while A is selected. A reply then goes out from A. | `EmailToolCards.tsx:468`, `emailStore.ts:1006-1025` | EM-T8a |
| **MB-4** | **The chat send card does not show the From address**, and the model picks `account_id` from text. A chat reply is not tied to the mailbox of the mail. | `agents.py:1378-1386`, `instructions.md:32-33` | EM-T8a |
| **MB-5** | **A chat reply passes the local message id where `/send` expects the provider id.** The reply does not thread, and it can fail. This is true with one mailbox too. | `agents.py:1366-1367`, `send.py:163-170` | EM-T8a |
| **MB-6** | **`/send` accepts a `reply_to_message_id` that is not in the sending mailbox.** It asks the provider to reply to it. | `send.py:163-182` | EM-T8a |
| **MB-7** | **Reply-all leaves out only the address of the selected mailbox.** A member who is on the thread under two addresses sends a copy to themself. | `EmailDetail.tsx:410-411` | EM-T8a |
| **MB-8** | **Two Outlook mailboxes look the same.** Each new row gets the label "Outlook" and the colour `#6366f1`. The API cannot change the colour. The UI draws the hex value, which breaks design rule 1. | `core.py:785-787`, `oauth.py:510`, `accounts.py:74-78`, `AccountSidebar.tsx:133-141` | EM-T8b |
| **MB-9** | **After a second connect, Email shows the old mailbox.** The return URL keeps `?account=<old>`, and the callback page ignores the `account_id` it gets. The setup steps of the new mailbox stay hidden. | `page.tsx:329`, `oauth/callback/page.tsx:187` | EM-T8c |
| **MB-10** | **A switch of mailbox keeps the "load older" state of the old mailbox.** The backfill cursor is keyed by folder only. | `emailStore.ts:244-246`, `:951-965` | EM-T8c |
| **MB-11** | **The AI settings never name the mailbox they change.** The mobile automation drawer has no mailbox picker. | `AutomationView.tsx:95-110`, `page.tsx:467-480` | EM-T8f |
| **MB-12** | **The all-mailbox list merges conversations across mailboxes.** It groups by `thread_id` with no `account_id`. The thread count has no owner predicate. | `messages.py:192-196`, `:283`, `:364-377` | EM-T8d |
| **MB-13** | **The unread badge of a mailbox counts junk and deleted mail.** | `accounts.py:150-158` | EM-T8d |
| **MB-14** | **The AI drafts "as" the sign-in address of the member, not as the mailbox.** "Self" covers only the current mailbox, so mail between two mailboxes of one member counts as external. | `drafting.py:816`, `automation/identity.py:64-91` | EM-T8e |
| **MB-15** | **The chat tags a result with the label of its mailbox, and two Outlook mailboxes share the label "Outlook".** | `agents.py:197-215`, `core.py:785-787` | EM-T8e |
| **MB-16** | **The Integrations page connect leg sends no import range and still offers IMAP.** D-EM-5 hides IMAP. | `integrations/page.tsx:1019-1039` | EM-T8c |
| **MB-17** | **A disconnect leaves the Mem0 drafting memories of the mailbox (`#acct:<id>`).** | no purge code, `core.py:790-809` | EM-T8f |

### 11.2 Decisions (product design, 2026-10-03)

The owner asked the agent to design this feature as a product manager. The decisions below are
that design. The owner can reverse any of them. Q-MB-1 is the one open owner question (§11.8).

| Id | Decision |
|---|---|
| **D-EM-17** | **A member can connect several mailboxes in one organization.** D-EM-4 applies to each one: a mailbox is private to the member who connects it. Two members can connect the same address, and each one gets a private copy. The same address in two organizations is two copies. |
| **D-EM-18** | **A mailbox is the boundary of the AI context.** The rule match, the thread status, the cold check, the sender pin, automatic drafts, learning, the voice profile, Process past emails, the digest and the drafting memory each read and write one mailbox. Nothing that the AI learns in one mailbox changes another. |
| **D-EM-19** | **An act on a mail runs in the mailbox of that mail.** Reply, forward, AI draft, summary, rule from this mail, archive, move, label, snooze and block sender all take the mailbox from the mail. The server refuses an act that names a mail of another mailbox. |
| **D-EM-20** | **The sending mailbox is visible and never silent.** A reply or a forward goes out from the mailbox of the mail. New mail goes out from the mailbox in view. In All inboxes, new mail goes out from the default mailbox. The From row shows when the member has two or more mailboxes. A change of From on a reply shows a warning. |
| **D-EM-21** | **Each mailbox has a label and a colour slot.** The default label comes from the address (§11.4). The colour slot is the lowest slot of the `--cat-1…12` ramp that no other mailbox of the member uses. The member can change both. Each chip shows the colour and the label together. |
| **D-EM-22** | **All inboxes is a view, never a merge.** It lists the mail of each mailbox of the member, newest first, with a chip on each row. Each row keeps its mailbox. A conversation never spans two mailboxes. The same mail in two mailboxes shows twice, and each row says "Also in" the other mailbox. |
| **D-EM-23** | **Chat has a visible scope: one mailbox, or All inboxes.** In All inboxes, the chat reads across the mailboxes of the member and names the mailbox of each result. Each write act binds to exactly one mailbox, by the order in §11.3. The confirmation card names the From address. |
| **D-EM-24** | **Rules and AI settings are per mailbox, and a copy is explicit.** A new mailbox starts with no rules. Its setup step offers "Copy the rules of <label>" beside the presets. A copy is made once. After that, the two sets change apart. |
| **D-EM-25** | **The default mailbox.** The first mailbox is the default, and the member can change it. The default sends new mail from All inboxes and from the other apps, for example Notes. When the default disconnects, the oldest mailbox that is left becomes the default, and Email says so. |
| **D-EM-26** | **A connect of one more mailbox always asks Microsoft which account to use.** The authorize leg sends `prompt=select_account` and no `login_hint`. A connect that returns an address the member already connected in this organization is a reconnect of that mailbox, and Email says so. After a connect, Email opens the new mailbox and its setup. |
| **D-EM-27** | **The member identity covers each mailbox of the member.** "Self" means any address of a mailbox of the member in this organization. Mail between two of them is not a cold sender and is not "awaiting reply". Reply-all leaves out each of those addresses. |
| **D-EM-28** | **A member can keep a mailbox separate.** A separate mailbox stays out of All inboxes, out of search in All inboxes and out of chat in All inboxes. Use it for a mailbox under a confidentiality agreement. It is off by default. |
| **D-EM-29** | **A copy of rules leaves out a forward to an own address.** Until the loop guard of §11.6 edge case 12 ships, `POST /email/rules/copy` leaves out each WHOLE rule that has a FORWARD action whose recipient is an address of a mailbox of the member. The answer names each rule that it left out. The agent recorded this on 2026-10-03, and the owner can reverse it. The reason for the whole rule is in the As-built notes of EM-T8f-1 (§11.7.6). |
| **D-EM-30** | **What "separate" covers.** A separate mailbox leaves each read of more than one mailbox: the list, the facets, search, `/senders`, the sums and the chat in All inboxes. It stays in the identity of the member, so D-EM-27 self and the Sent-copy proof still read it. A read that names it still works, by its `account_id`, a mail id, a thread load or a bulk act by ids. "Also in" and the draft dedupe pair two mailboxes only when neither one is separate. All inboxes shows only when two or more mailboxes are not separate. The agent recorded this on 2026-10-03, and the owner can reverse it. |

### 11.3 How the AI keeps the mailboxes apart

**The model.** The context of a mailbox is the data that the AI may read when it acts for that
mailbox. That data is the mail, the threads, the contacts and the sender lists of the mailbox.
It is also the rules, the learned patterns, the voice profile, the signature, the knowledge, the
embeddings and the drafting memory of the mailbox. Each item has `account_id`. The tables and the routes in §11.1 already carry it.

**Three kinds of AI act, and the mailbox of each one.**

| Kind | Acts | The mailbox it reads | How the mailbox is chosen |
|---|---|---|---|
| **Background** | The rule match (Jev), the thread status, the cold check, the sender pin, automatic drafts, learning, the voice profile, Process past emails, the morning brief | One mailbox only | The `account_id` of the mail or of the job. The loop runs once for each mailbox. |
| **Bound to a mail** | Draft a reply, summarize a thread, make a rule from this mail, suggest a label | The mailbox of the mail, and for a draft also the voice and the signature of the sending mailbox | The mail. Never the selected view. |
| **Chat** | Questions, search, triage, drafts, sends, rule changes | The scope: one mailbox, or each mailbox of the member that is not separate | The scope picker in the chat. A write act resolves to one mailbox by the order below. |

**The order by which chat chooses the mailbox of a write act.**

1. **An act on a mail that exists** (reply, forward, archive, move, label, unsubscribe): the
   mailbox of that mail. The tool takes it from the mail. When the model names another mailbox,
   the tool refuses and names the right one.
2. **New mail, in the scope of one mailbox:** that mailbox.
3. **New mail, in All inboxes:** the mailbox that the member names. If the member names none, the
   mailbox that last wrote to that recipient. If no mailbox wrote to that recipient, the agent asks
   "Send from which mailbox?" and lists the labels.
4. **A rule or a setting:** one mailbox. In All inboxes, the agent asks which one. "All of them"
   makes one copy for each mailbox, and the agent names each copy.

Each confirmation card shows "From <label> · <address>" above the recipients. The server checks
the mailbox again (D-EM-19), so a wrong choice by the model fails closed.

**What the AI reads across mailboxes, and nothing else.** Three surfaces read more than one
mailbox. They are the All inboxes list, search in All inboxes, and chat in All inboxes. Each one
reads only the mailboxes of the member in the current organization. Row level security binds the
organization, and the owner predicate binds the member. A separate mailbox (D-EM-28) is never in
the three.

**What stays per member, and why.**

- **The chat memory** holds how the member likes to work, for example "keep replies short". It
  holds no mail. It stays per member (`automation/chat.py:160-171`).
- **The calendar context of the drafter** is the calendar of the member, who is one person in each
  mailbox. It stays per member (`drafting.py:251-287`).
- **The identity of the member** covers each mailbox (D-EM-27).

**The drafter speaks as the mailbox.** A draft names the address and the display name of the
sending mailbox, not the sign-in address (MB-14). The voice profile and the signature come from
the sending mailbox. A reply from mailbox B to a mail in mailbox A reads the thread of the mail.
It uses the voice and the signature of B.

**The cost.** Each mailbox runs its own sync loop and its own Jev calls. The budget of EM-T4b
counts for each mailbox. A 402 or a 403 from the Router starts a cool-down for the organization.
The cool-down stops the triage of each mailbox in that organization.

### 11.4 How the member always knows the mailbox

**The mailbox chip.** A chip is a dot of the categorical ramp and the label of the mailbox. It
uses `accentForSlot(color_slot)` from `src/lib/categorical.ts`, never a hex value. A narrow screen
shows the dot and the first letter of the label. The full address is in the tooltip and in the
reading pane.

**The default label.** The member can rename a mailbox. Until then, Email makes the label from the
address, in this order:

1. A consumer domain (`outlook.com`, `hotmail.com`, `live.com`, `msn.com`, `gmail.com`,
   `yahoo.com`, `icloud.com` and others) gives "Personal". The list of record is
   `CONSUMER_DOMAINS` in `routes/email/mailbox_identity.py`.
2. Any other domain gives its first part, with a capital letter. `vj@fracktal.in` gives
   "Fracktal".
3. When two mailboxes of the member get the same label, each one takes its local part instead.
   `sales@fracktal.in` gives "Sales".

A stored label equal to the provider name ("Outlook", "Gmail" or "Email") counts as no label,
because the connect wrote it, not the member.

**Where the mailbox shows.**

| Surface | What it shows | When |
|---|---|---|
| **Mailbox switcher**, top of the left rail | "All inboxes", then each mailbox: chip, label, address, unread count of its Inbox, a status mark (importing, reconnect, at the limit). "Add a mailbox" at the end. | Two or more mailboxes. One mailbox shows the address, as today. |
| **View header** | "All inboxes · 3 mailboxes", or the chip and the address of the one mailbox | Two or more mailboxes |
| **List row** | The chip, after the sender | All inboxes only. One mailbox in view needs no chip. |
| **Reading pane** | "In <chip> · to <address>" under the subject | Two or more mailboxes |
| **Composer** | A From row with a picker. A reply starts on the mailbox of the mail. | Two or more mailboxes |
| **Composer warnings** | "This conversation is in Fracktal. The recipients will see a new address." "You usually write to this person from Fracktal." "You are writing to a fracktal.in address from Personal." Each warning has a one-click switch. | When it applies |
| **Banners and progress** | The label and the address in each banner: reconnect, import, storage limit, first sync | Always |
| **AI settings** | "AI settings for <chip> · <address>" with a mailbox picker. There is no All option. | Always, on desktop and on mobile |
| **Process past emails** | The dialog title names the mailbox | Always |
| **Chat** | The scope picker, a tag on each result, and "From" on each send card | Two or more mailboxes |
| **Disconnect dialog** | The label, the address, the counts that go, and the new default when this one is the default | Always |

**Unread counts.** The count of a mailbox is the unread mail in its Inbox, not in junk or deleted
mail (MB-13). The count of All inboxes is the sum, without separate mailboxes.

**Folders.** One mailbox in view shows its own folder tree. All inboxes shows the well-known
folders: Inbox, Drafts, Sent, Archive, Junk and Deleted. Each count is the sum. A custom folder
shows only under its own mailbox. The label filter shows only for one mailbox, because each
mailbox has its own Outlook categories.

**The scope in the URL.** `?account=all` or `?account=<id>`. An id that no longer exists falls back
to All inboxes, or to the only mailbox, with no error. The first visit with two or more mailboxes
opens All inboxes. After that, Email opens the last scope.

### 11.5 Use cases

| Id | The member wants to | What happens |
|---|---|---|
| **UC-1** | Connect a second mailbox | "Add a mailbox" in the switcher opens the range step. Microsoft asks which account (D-EM-26). Email opens the new mailbox, shows its import progress, then its rules step with "Copy the rules of <label>". |
| **UC-2** | Triage the mail of all mailboxes in one place | All inboxes lists each mailbox, newest first, with chips. Archive, move and snooze act in the mailbox of each row. A bulk act across mailboxes groups the provider calls by mailbox. |
| **UC-3** | Reply to a mail of mailbox B from All inboxes | The composer opens with From = B. The draft is saved in B and sent from B. |
| **UC-4** | Write new mail from a chosen address | The From row starts on the mailbox in view, or on the default in All inboxes. The member picks another one before the send. |
| **UC-5** | Ask the chat "what did Acme send me this week?" | In All inboxes, the chat searches each mailbox and tags each result with its chip. |
| **UC-6** | Ask the chat to reply to one of those mails | The reply binds to the mailbox of that mail. The card shows "From B". |
| **UC-7** | Set up the rules of the new mailbox | The rules step offers presets and "Copy the rules of <label>". Process past emails runs for that mailbox only. |
| **UC-8** | See why a mailbox stopped | The switcher shows a status mark on its chip. The banner names the mailbox and offers "Reconnect <label>". The other mailboxes run on. |
| **UC-9** | Remove one mailbox | The dialog names the mailbox and the counts. The other mailboxes, their rules and their mail stay. If it was the default, the dialog names the new default. |
| **UC-10** | Keep a client mailbox apart | "Keep separate" in the mailbox menu. It leaves All inboxes and the All inboxes chat. Its own view still works. |
| **UC-11** | Tell two mailboxes apart at a glance | Each one has its own label and colour. The member can rename and recolour it in the mailbox menu. |
| **UC-12** | Reach the storage limit in one mailbox | Only that mailbox stops its import. Its chip shows the mark, and its banner names it. EM-T6e owns the mark and the banner (§10.4.7, D3). |

### 11.6 Edge cases

| # | Case | Behaviour | Slice |
|---|---|---|---|
| 1 | The browser is signed in to Microsoft as mailbox A, and the member adds mailbox B | `prompt=select_account` shows the account picker (D-EM-26) | EM-T8a |
| 2 | The member picks A again in the Microsoft picker | A reconnect of A. Email says "<address> is already connected" and shows A. No new row. | EM-T8c |
| 3 | Two members of one organization connect the same address | Two private copies, two syncs, two sets of rules (D-EM-17). Neither member sees the other copy. | — |
| 4 | One member connects the same address in two organizations | Two copies, one in each organization. Row level security keeps them apart. | — |
| 5 | Two mailboxes get the same default label | The local part becomes the label (§11.4). The address always shows in the switcher and the pane. | EM-T8b |
| 6 | More than 12 mailboxes | The colour slots repeat. The labels still differ. Q-MB-1 can make this case impossible. | EM-T8b |
| 7 | The member changes From on a saved reply draft | Email saves a new draft in the new mailbox, then deletes the draft in the old one. A provider draft cannot move between mailboxes. | EM-T8c |
| 8 | The sending mailbox needs a reconnect | The send stops with "Reconnect <label> to send". The draft stays. | EM-T8c |
| 9 | The sending mailbox is at the storage limit | The send runs. The limit is on the copy in Metorite only (D-EM-14). | — |
| 10 | The same mail is in two mailboxes, for example a mail sent to both addresses | Two rows, each with its chip, each with "Also in <label>". The match is `internet_message_id` (migration 89). Outlook stores `internet_message_id`, and Gmail does too since EM-G2 (#626). EM-G9 proves a Gmail and Outlook pair. Known limit: IMAP stores none, so an IMAP mailbox pairs with nothing. | EM-T8g-3, EM-G9 |
| 11 | Both mailboxes have automatic drafts on, and the same mail is in both | The second draft does not start when the other mailbox already has a draft or a reply for that `internet_message_id`. Outlook stores `internet_message_id`, and Gmail does too since EM-G2 (#626). EM-G9 proves the dedupe across a Gmail and Outlook pair. Known limit: IMAP stores none. | EM-T8g-3, EM-G9 |
| 12 | A rule forwards mail from A to B, and a rule in B forwards it back | A forward rule does not fire on mail that a Metorite rule forwarded. The forward carries the header `X-Metorite-Forwarded`. Today a FORWARD makes a draft, so a loop needs a send by the member at each hop. | deferred (§11.7.7) |
| 13 | Two Outlook mailboxes return the same conversation id | The conversation key includes `account_id`. A conversation never spans two mailboxes (MB-12). | EM-T8d |
| 14 | Mail between two mailboxes of the member | Not a cold sender, not "awaiting reply" (D-EM-27). A thread with no participant outside the member's mailboxes is FYI. It is never NEEDS_REPLY and never AWAITING. A mail with no recipient keeps its status. The cold check skips such a mail only when a Sent copy proves the send (edge case 26). | EM-T8e |
| 15 | Reply-all where the member is on the thread under two addresses | Each address of the member leaves the recipients (MB-7) | EM-T8a |
| 16 | The URL names a mailbox that was removed | All inboxes, or the only mailbox, with no error | EM-T8d |
| 17 | The chat scope is a mailbox that the member removes | The scope goes back to All inboxes, and the chat says so | EM-T8e (the note: EM-T8f) |
| 18 | The default mailbox is removed | The oldest mailbox that is left becomes the default. The dialog names it before the removal. | EM-T8f |
| 19 | Two mailboxes import at the same time | Each one has its own sync lock (EM-T4f) and its own progress panel, named | EM-T8f |
| 20 | The member switches mailbox during "load older" | The backfill state belongs to the mailbox and the folder (MB-10) | EM-T8c |
| 21 | A label filter in All inboxes | Not shown. A label belongs to one mailbox. | EM-T8d |
| 22 | The recipient is known only from another mailbox | The composer shows "You usually write to this person from <label>" with a switch | EM-T8c |
| 23 | Another app sends mail for the member, for example Notes | It uses the default mailbox and names it in its own confirm step | EM-T8f-2 |
| 24 | A disconnect | The Mem0 drafting memories of the mailbox go too (MB-17) | EM-T8f |
| 25 | A keyboard reply (`r`) in All inboxes | The mailbox of the focused mail, as for a click | EM-T8a |
| 26 | An outside sender forges From as another mailbox of the member | The cold check runs. Only a Sent copy that proves the send stops it. The copy has the same Message-ID, and that ID is not empty. It sits in the `sent` folder of another mailbox of the member. It names this mailbox in To, Cc or Bcc, so a replayed Message-ID proves nothing. **Known limit:** IMAP stores no Message-ID, so a pair with an IMAP mailbox gets the cold check, as before EM-T8e-1. Gmail stores it since EM-G2 (#626), and EM-G9 proves the proof from Gmail to Outlook and back. **Accepted risk:** the classifier payload, Reply Zero, the digest and the cleanup still read a forged From as "self". The single address had the same exposure before EM-T8e-1. | EM-T8e-1 |

**Gmail (2026-10-05).** Edge cases 10, 11 and 26 hold for a Gmail and Outlook pair too. EM-G2 (#626) gives Gmail the Message-ID in the form of Graph, and EM-G9 proves each case (§12.3.11). EM-G9-f1 to EM-G9-f3 are the known limits of the pair.

### 11.7 Slices

Each slice is one PR, or the pull requests that its section names. Each slice ships behind no flag, because each one is a fix or a view that
shows only with two or more mailboxes. The order is the order of risk: EM-T8a fixes the live
wrong-sender defects first.

| Slice | Gate | Scope | Done when |
|---|---|---|---|
| **EM-T8a** | 🟢 AGENT-SAFE · security review | ✅ **MERGED #587 (2026-10-03).** **Send from the right mailbox.** MB-1 to MB-7. No migration. | §11.7.1 |
| **EM-T8b** | 🟢 AGENT-SAFE | ✅ **MERGED #588 (2026-10-03, migration 227).** **The mailbox identity.** A migration adds `color_slot`. The default label, the chip, rename and recolour. MB-8. | §11.7.2 |
| **EM-T8c** | 🟢 AGENT-SAFE | ✅ **MERGED #592 (2026-10-03).** **The From row and the second connect.** The From picker, the warnings, the move of a draft, the block on a broken mailbox, the return to the new mailbox, the Integrations connect leg. MB-9, MB-10, MB-16. | §11.7.3 |
| **EM-T8d** | 🟢 AGENT-SAFE · R8 | ✅ **MERGED #596 (2026-10-03).** **All inboxes.** The scope, the chips on rows, the well-known folders, the counts. MB-12, MB-13. | §11.7.4 |
| **EM-T8e** | 🟢 AGENT-SAFE · security review | **The AI context.** The fences of D-EM-18, the chat scope, the binding order of §11.3, the drafter identity. MB-14, MB-15. Three pull requests. T8e-1 is self, the drafter and the server checks. T8e-2 is the chat tools. T8e-3 is the chat scope. | §11.7.5 |
| **EM-T8f** | 🟢 AGENT-SAFE | **Settings for each mailbox.** The AI settings header and picker, the copy of rules, the disconnect dialog, the Mem0 purge. MB-11, MB-17. | §11.7.6 |
| **EM-T8g** | 🟢 AGENT-SAFE · R8 · security review | ✅ **T8g-1 MERGED #608 (2026-10-04, migration 229). T8g-2 MERGED #610 (2026-10-04).** ✅ **T8g-3 MERGED #611 (2026-10-04), with review fix round 1.** **Duplicates and separation.** Three pull requests: T8g-1 "Keep separate" on the server (migration), T8g-2 "Keep separate" in the UI, T8g-3 "Also in" and the draft dedupe. The forward loop guard waits for a later slice. | §11.7.7 |

#### 11.7.1 EM-T8a — send from the right mailbox

**Status.** ✅ MERGED (#587, 2026-10-03), with review fix round 1.

**Scope.**

1. **The authorize leg (MB-1).** When the member already has a mailbox of that provider in this
   organization, the leg sends `prompt=select_account` and no `login_hint`. The first connect keeps
   the hint, as EM-T3a item 3 specified. A `login_hint` that the client sends still wins. When
   the read of the mailboxes fails, the leg shows the picker, which is right for a first connect
   too. Reconnect on the Integrations page sends the mailbox address as its hint, as the banner
   in Email does.
2. **The inline composer (MB-2).** `EmailDetail` takes the mailbox from `email.accountId`. Each
   of these uses it: the signature, the draft save, the AI draft and the thread load. The send,
   the draft send, the optimistic sent row and the sync after the send use it too. `selectedAccountId` is only the fallback for
   a mail with no `accountId`.
3. **Open in inbox (MB-3).** `openEmailById` opens the mail in its own mailbox. When the mailbox
   differs from the selected one, the store selects the mailbox of the mail first.
4. **The chat send (MB-4, MB-5).** In reply mode, `send_email` reads the original mail and uses its
   `account_id`. It passes the local id of the mail, and `/send` maps it to the provider id. When
   the model passes another `account_id`, the tool uses the mailbox of the mail and says so in
   its result. The confirmation card shows "From <address>" for each send, new or reply.
   `send_draft` shows the same line. `draft_reply` drafts in the mailbox of the mail, and the
   first line of its result names that mailbox. The chat draft card reads the mailbox from that
   line, so its Save and Send act there, and its confirm names the From mailbox.
5. **The server check (MB-6).** `/send` answers 404 when `reply_to_message_id` names no mail of a
   sending mailbox of the member. The check runs before the provider session, so a refused reply
   makes no provider call, the sign-in included. Each arm of the match uses an index.
6. **Reply and reply-all (MB-7).** The recipients leave out the address of each mailbox of the
   member. The in-thread draft card uses the same rule. Two cases keep an own address:
   - A reply to mail from another mailbox of the member answers that mailbox.
   - A reply to mail that the sending mailbox sent answers its recipients, as Outlook does. When
     those recipients are only own mailboxes, they stay.
7. **On a phone,** a mail that Open in inbox opened in its own mailbox keeps the detail view.

**Fences (R7).**

- A unit test proves that a second connect of a member sends `prompt=select_account` and no
  `login_hint`, and that a first connect keeps the hint.
- An R8 test proves that `/send` with a reply id of another mailbox answers 404, and that the
  route opens no provider session. The same holds for the mailbox of another member.
- A unit test proves that the chat reply sends from the mailbox of the original mail. It also
  proves that the reply uses the provider id, and that the card names the From address.
- A vitest source fence proves that `EmailDetail.tsx` passes no bare `selectedAccountId` to a send,
  draft, signature or thread call.
- The fix round of the review adds five fences, each named in its test file:
  `email-authorize-check-fails-open-to-picker`, `email-chat-draft-card-mailbox`,
  `email-draftcard-own-addresses`, `email-open-by-id-mobile` and
  `email-integrations-reconnect-hint`.

**Verification.** `uv run pytest tests/unit/test_email_multi_inbox.py tests/unit/test_email_oauth_state.py tests/unit/test_email_tool_consolidation.py -v -rs`.
The R8 tests must show PASSED, not SKIPPED. In `workbench/control_plane`, run
`npx tsc --noEmit && npx vitest run src/app/email src/components/email`.

#### 11.7.2 EM-T8b — the mailbox identity

**Status.** ✅ MERGED (#588, 2026-10-03, migration 227), with review fix round 1.

1. **A migration** (R1: take the number at build time, and check it again at merge). It adds `color_slot SMALLINT NULL`,
   with a check of 1 to 12. It sets a slot for each existing row, in the order of `created_at`
   for each member and organization. The column stays nullable (R6).
2. **The connect** writes the lowest slot that no other mailbox of the member uses.
3. **The API** returns `color_slot` and `display_label`, the label of §11.4. `PATCH` accepts
   `label` and `color_slot`.
4. **The UI.** One `MailboxChip` component, through `accentForSlot`. A NULL slot falls back to
   `categoricalAccent(account.id)`. The switcher, the mobile top bar and the reading pane use it.
   The hex avatar goes, and the `COLOR_DEBT` entry for `#6366f1` in `conformance.test.ts` goes
   with it.
5. **The mailbox menu** gains "Rename" and "Colour".

**As built (2026-10-03).**

- Migration 227 is `227_email_mailbox_color_slot.sql`. A rerun keeps a slot that the member chose.
- `routes/email/mailbox_identity.py` is the one place that decides a label and a slot. A new
  mailbox takes the least-used slot of the member, then the lowest, in SQL inside its INSERT.
- The API also returns `default_label`: the label of the mailbox with its name cleared. The
  rename dialog shows it for a blank name, so the UI derives no label of its own.
- A stored label equal to the address is no choice, as a stored provider name is not. A `PATCH`
  to "Outlook", "Gmail" or "Email" answers 400. `color_slot` is a strict integer.
- A rename or a colour does not restart the sync loop. A sync toggle still does.
- The menu has one item, "Name and colour", which opens one dialog for both.
- `src/components/ui/SlotPicker.tsx` is the one colour picker. Space settings draw it too.
- The reading pane shows "In <chip> · to <address>" under the subject, for two or more mailboxes.

**Fences.** An R8 test for the backfill and for the slot of a new connect. A unit test for the
label rule of §11.4. The conformance test with the debt entry removed. The fence ids are in
`tests/unit/test_email_mailbox_identity.py` and `src/app/email/lib/mailboxIdentity.test.ts`.

**Verification.** `uv run pytest tests/unit/test_email_mailbox_identity.py tests/unit/test_email_n_plus_one.py -v -rs`.
The R8 tests must show PASSED, not SKIPPED. In `workbench/control_plane`, run
`npx tsc --noEmit && npx vitest run src/app/email src/lib/theme src/components`.

#### 11.7.3 EM-T8c — the From row and the second connect

**Status.** ✅ MERGED (#592, 2026-10-03, no migration), with review fix round 1.

1. **The From row** in `ComposePanel` and in the inline composer, for two or more mailboxes. It
   lists each mailbox with its chip and its address. A mailbox that needs a reconnect shows the
   mark and cannot send.
2. **The warnings** of §11.4, each with a one-click switch. "You usually write to this person
   from <label>" reads `/contacts/suggest` with no `account_id`.
3. **The move of a draft** (edge case 7). The signature changes with the From, when the body still
   holds the old signature unchanged.
4. **The return after a connect (MB-9).** The callback page selects the `account_id` it gets.
   The connect modal leaves `?account=` out of `redirect_after`.
5. **A connect of an address already present** shows "<address> is already connected" (edge
   case 2). The callback returns a `reconnected` flag.
6. **The backfill state (MB-10)** is keyed by mailbox and folder.
7. **The Integrations connect leg (MB-16)** sends the import range and hides IMAP. As an
   alternative, it links to the connect flow inside Email.

**As built (2026-10-03).**

- **A reply from another mailbox goes as new mail.** A provider cannot answer a mail of another
  mailbox, and `/send` refuses it (MB-6). So the composer drops the reply target, and the
  warning says that the reply starts a new conversation. The thread of the mail shows no
  optimistic copy, because the reply lands in the other mailbox.
- **The usual sender** comes from `GET /email/contacts/sent-from`, not from `/contacts/suggest`.
  It maps each address to the mailbox of the member that last wrote to it, with the owner
  predicate in the SQL. A failure answers `{}`.
- **The domain warning** reads `work_domain` from the accounts API, so the UI keeps no list of
  consumer domains.
- **A mailbox that cannot send** is one whose sign-in failed: a live call answered 401, or the
  accounts API returns `needs_reconnect`. The gateway sets that flag only for a sync error of the
  sign-in. A 429 or a 503 during an import also marks the sync as failed. A send still works
  then, so that error does not block a send. The reconnect banner still shows for any sync error.
- **A change of From during a save** makes the draft of that save stale. The composer keeps a
  list of stale drafts and deletes them only once the new mailbox holds the message. While a
  stale draft exists, a send takes the draft path, which waits for the real send.
- **The pop-out keeps the From** of the inline reply. The full composer opens on the mailbox of
  the mail and starts on the chosen From, so it drops the reply target itself.
- **The picker** is the house dropdown (`SelectButton`), with the label of each mailbox and its
  address as the hint. A mailbox that cannot send stays in the list, disabled, with the reason.
- **A reply that leaves its conversation** always shows the warning. When the mailbox of the
  conversation cannot send, the warning offers no switch back.
- **An address already present** is found in the browser. Before an ADD, the page keeps the
  ids of the mailboxes of the member in the session storage of the tab. A reconnect names its
  mailbox and stores none. The callback page reads the ids once, removes them, and shows the
  notice when the id that returns is one of them. The gateway needs no `reconnected` flag.
- **The return** sets `?account=<id>` on the target of the callback, so an old selection in
  `redirect_after` does not win. It also removes `connect=1`, or the add dialog opens again.
- **"Load older"** that ends after a switch of mailbox writes no rows into the new view.
- **Integrations** sends Add to `/email?connect=1`. Its own leg, with Gmail and IMAP, is gone.
  Reconnect stays on Integrations, with the mailbox as its hint.

**Fences.** `tests/unit/test_email_from_row.py` (R8 for `sent-from`) and
`src/app/email/lib/fromRow.test.ts` name their fence ids.

**Verification.** `uv run pytest tests/unit/test_email_from_row.py -v -rs` (R8 PASSED, not SKIPPED).
In `workbench/control_plane`, run
`npx tsc --noEmit && npx vitest run src/app/email src/app/integrations src/components/email src/lib/theme`.
The theme suite holds the design-system fences, so leave it in.

#### 11.7.4 EM-T8d — All inboxes

**Status.** ✅ MERGED #596 (2026-10-03, no migration).

1. **The store scope** is `"all"` or a mailbox id. The URL holds it (§11.4).
2. **The list and search** call the backend with no `account_id` in All inboxes. Each row draws
   its chip. The thread load passes the `account_id` of the mail.
3. **The backend (MB-12).** The conversation key is `(account_id, thread_id)`. The thread filter
   and the thread count take `account_id`, and the count takes the owner predicate. R8 proves
   that two mailboxes with the same `thread_id` give two conversations.
4. **The well-known folders** and their summed counts. Custom folders and label filters show only
   for one mailbox.
5. **The unread count (MB-13)** is the Inbox only.
6. **The import panels** show one panel for each importing mailbox, each one named.

**As built (2026-10-03).**

- **The scope is a flag beside the mailbox.** The store keeps `viewAll` and a real
  `selectedAccountId`. All inboxes reads the list, search, paging, the refresh and the facets
  with no `account_id`. Settings, automation and new mail keep a real mailbox, and new mail in
  All inboxes goes from the default mailbox. The URL and the local storage hold `all`.
- **The first view.** Two or more mailboxes and no stored choice open All inboxes. A stored
  mailbox that is gone falls back to All inboxes, or to the only mailbox. One mailbox never shows
  the scope.
- **The switcher** draws "All inboxes" above the mailboxes, with the sum of the Inbox counts. A
  mailbox row is never selected while All inboxes is.
- **Folders (item 4, narrowed).** All inboxes draws only the folders that each mailbox has,
  with no count, because a count belongs to one mailbox. A custom folder that is open when the
  member picks All inboxes becomes the Inbox. A summed count for each folder is not built.
- **"Load older"** pages one mailbox at the provider, so All inboxes does not offer it.
- **"Open in inbox"** keeps All inboxes: the mail shows there with its chip.
- **The import panels (item 6) are not built in this slice.** The page still shows one panel,
  and it names its mailbox.

**The review round (2026-10-03).** The review and the verifier found that some acts still used
the hidden selected mailbox in All inboxes. Each act now uses the mailbox of the row, or acts on
each mailbox, or is absent.

- **A row act uses the mailbox of the row.** The rule test, the snooze filter and the label
  colour take the `accountId` of the mail. A label colour for another mailbox goes to the
  provider and leaves the chips of the view unchanged.
- **Acts that belong to one mailbox are absent.** The label filter, the custom folders in a move
  menu, the search scope and the palette, and the resync are absent in All inboxes. A label chip
  on the dashboard opens its mailbox first.
- **Sync and the busy mark cover each mailbox.** `syncScope()` syncs each mailbox, and
  `scopeBusy()` reads the status of each one.
- **A failure cannot hide.** The reconnect banner names the first mailbox that needs it, and the
  switcher marks a mailbox whose sync failed.
- **The unread count** counts the Inbox only, and a snoozed mail does not count.
- **The scope survives change.** A refresh for another scope does not land. A disconnect in All
  inboxes reads the list again, and fewer than two mailboxes end the scope.

**The second review round (2026-10-03).** The verifier passed the first round with no P0 and no
P1. This round fixes its P2 findings.

- **Automation and the chat name their mailbox (F4, F5).** Both act on one mailbox, and All
  inboxes names none. So opening one leaves All inboxes for the mailbox of the open mail, else
  the default mailbox, and the switcher marks it. The open mail stays open. EM-T8e-3 gives the
  chat its own All inboxes scope.
- **A removed mailbox leaves at once (F8).** Its rows leave the list, and a mail of it leaves the
  reading pane. This is true for a disconnect in this tab and for a removal in another tab.
- **The rows of All inboxes add no label (F6).** A label belongs to one mailbox.
- **The bulk label menu keeps its colours for one mailbox (F2).** All inboxes shows none.

**Moved to EM-T8f (F7).** This slice does not build two items. They are the summed count of each
folder in All inboxes (item 4), and one import panel for each mailbox (item 6, edge case 19).
§11.7.6 now owns them.

**Fences.** `tests/unit/test_email_all_inboxes.py` (R8), `src/app/email/lib/allInboxes.test.ts`
and `src/app/email/lib/allInboxesStore.test.ts` name their fence ids. The store tests drive the
real store with a mocked gateway. They killed 7 of 7 store mutants in round 1 and 6 of 6 in
round 2. Round 2 includes the 3 mutants that survived the verifier.

**Verification.** `uv run pytest tests/unit/test_email_all_inboxes.py tests/unit/test_email_conversation_collapse.py -v -rs`
(R8 PASSED, not SKIPPED). In `workbench/control_plane`, run
`npx tsc --noEmit && npx vitest run src/app/email src/lib/theme src/components`.

#### 11.7.5 EM-T8e — the AI context

1. **The fences of D-EM-18.** Each test puts a second mailbox of the same member in the
   database (R8). One test for each background act proves that it reads one `account_id` only.
2. **The chat scope.** The picker in `AgentChat` offers "All inboxes". The persona names the scope
   and lists each mailbox as label and address. Each tool result carries "label · address", not
   the label alone (MB-15).
3. **The binding order of §11.3**, on the tool side. A tool that acts on a mail derives the mailbox
   from the mail. A new send in All inboxes with no mailbox named answers with the question.
4. **The drafter identity (MB-14).** The prompt names the address and the display name of the
   sending mailbox. "Self" in `automation/identity.py` covers each mailbox of the member.
5. **Removed mailbox** (edge case 17): the chat scope falls back to All inboxes.

**What the code does today (measured 2026-10-03, origin/main 3ee0396b).**

- **No background act reads across mailboxes.** Each background loader has `account_id = :aid` in
  its `WHERE`, or `account_id` in its Mem0 key. The loaders serve the rule match, the thread
  status, the cold check, the sender pin and the automatic drafts. They also serve learning, the
  voice profile, Process past emails, the digest, the embeddings and the drafting memory. No R8
  test proves it with two mailboxes of one member.
- **"Self" is one address.** `automation/identity.py:64-91` matches the address of the current
  mailbox only. So mail from another mailbox of the member is external when the domains differ.
  The cold check, the thread status, the digest and the sender pin then act on it.
- **The drafter names the sign-in address.** `drafting.py:816` and `drafting.py:1005` say "You
  are drafting as: <sign-in address>". The local draft copies store the sign-in address as the
  From (`drafting.py:1765`, `actions.py:499`, `actions.py:538`).
- **A reply from mailbox B to a mail in A loses the thread.** Compose-assist reads the mail with
  `em.account_id = :aid` of B (`drafting.py:1634`), finds nothing, and drafts with no thread.
- **The live chat is the `email-assistant` agent** (`apps/agents/agent-email-assistant/agents.py`),
  not `automation/chat.py`. The scope reaches the agent only as persona text. Each tool takes
  `account_id` as an argument from the model.
- **Three tools do not refuse a wrong mailbox.** `send_email` re-binds a reply to the mailbox of
  the mail and does not refuse. `manage_inbox` archive, trash, read and star with a wrong
  `account_id` change 0 rows and report no error. `read_thread` lets the id from the model win
  over the id of the mail.
- **Three server writes trust the pair.** `mark_thread_done` writes a status row for a thread
  that has no mail in the mailbox (`replyzero.py:2076-2091`). `test_rules` tests a mail of B
  against the rules of A (`engine.py:1460`). `_upsert_rule_pattern` takes a `rule_id` of
  another mailbox (`rules.py:866-868`).
- **The chat tags a result with the raw label** (`agents.py:209-213`), often "Outlook", and the
  picker shows the address only.

**The narrowed slices.** EM-T8e ships as three pull requests.

##### EM-T8e-1 — self, the drafter and the server checks (gateway)

**Status.** ✅ MERGED #604 (2026-10-03). No migration.

**As built.**

- **The helper.** `automation/identity.py` holds `SELF_ADDRESSES_SQL`, a subquery over `:aid`,
  and `resolve_self`. One read of `resolve_self` gives the address, the label and the set. The
  label comes from `mailbox_identity.display_labels`. The SQL also compares the organization of
  each row. Row level security does the same, so the copy guards a session with no bind.
- **The callers.** The rule match payload, the thread status, the conversation check, the
  digest, the pin guard, the cleanup scope and the sender categories use the set. The recipient
  role uses it too, so a member in To under mailbox B is a direct recipient in mailbox A.
- **The cold check** reads `sender_scope` from the payload. Since review round 1, a value of
  `self` stops the check only when a Sent copy proves the send.
- **The drafter.** The prompt names "label <address>" of the sending mailbox. With no address,
  the prompt names nobody. A rule draft that has no `self` in its payload reads the mailbox row.
  That applies to approve and to retry.
- **Only compose-assist reads across mailboxes (item 3).** `/draft-reply` still answers 404 for
  a mail of another mailbox (D-EM-19).
- **Not changed.** `cleanup._internal_domains` reads the domain of the current mailbox, as item
  1 says. `_draft_direction_note` still compares one address for its Cc note, because item 1
  does not name it. A reply across mailboxes stores the AI draft under the thread id of A. So
  the learning on send can miss it.
- **Tests.** Four hermetic fakes answer the new read now: `test_email_digest.py`,
  `test_email_rule_pattern_guards.py`, `test_email_categorization.py` and
  `test_email_request_jobs_tenancy.py`. `email-self-each-mailbox` has one more case, as the
  owner role, for the organization compare.
- **A false claim, corrected.** The first build said that each fix had a fence that failed
  without it. That was false for eight sites. Review round 1 below gives the true result.

**Review round 1 (2026-10-03).** An adversarial reviewer and an independent verifier found no
P0. The self set never reaches another member or another organization. This round fixes the
other findings.

- **The pair of a Fix (P1).** `POST /email/rules/feedback` answers 404 for a mail or a thread of
  another mailbox. Before, a LABEL rule of mailbox A put its label on a mail that mailbox B
  holds. An FYI rule of mailbox A wrote a status row for a thread that only mailbox B holds.
  `POST /email/rules/guidance` refuses a rule of another mailbox. `core._assert_thread_in_mailbox`
  holds the one query, and `resolve_thread` uses it too.
- **The live rule paths (P1).** Five R8 cases drive the real paths. Each case uses a mail in
  mailbox A that mailbox B sent. Three paths are the automatic run with its cold check, the run
  of one message and Process past emails. The preview on recent mail and the Reply Zero backfill
  are the other two.
- **A forged From (P2).** The cold check skips a mail from another mailbox only with proof. The
  proof is a mail with the same `internet_message_id` in the `sent` folder of another mailbox of
  the member (`identity.proven_own_send`). Edge case 26 records the accepted risk. Review
  round 2 adds one more test: the copy names this mailbox.
- **Mail between own mailboxes is never open (P2, D-EM-27).** `replyzero._thread_is_self_only`
  asks whether the thread has a participant outside the member's mailboxes. A participant is the
  sender of a mail that is not in Sent, and each To and Cc address. Without one, the thread is
  FYI with the reason "Only your own mailboxes". The status authority, the resolver and the
  projection each check it, and no model call runs.
- **Why not `has_external`.** It reads senders only, and a colleague is `internal`. So it would
  close each thread with a colleague, and each thread that the member sent to an outside party.
- **Why FYI and not "no row".** With no row, the backfill selects the thread again in each cycle.
  It then spends a rule match on it. FYI is the "nothing to do" status of the backfill, so the
  thread stays out of the Reply view and out of the next cycle.
- **The nudge and the saved draft (P2).** The follow-up nudge names the sending mailbox.
  `/drafts/save` stores the mailbox address as the From of its local copy.
- **The pin guard (P2).** This mailbox keeps the substring rule for its address and its domain.
  For another mailbox of the member, the guard refuses an exact address only. So the guard no
  longer refuses `gmail.com` in a work mailbox when another mailbox is a Gmail address.
- **Mutation result.** A mutation run took out each fix of the first build and of this round,
  one at a time. It killed 42 of 42 mutants, among them the eight sites with no fence before.
  Review round 2 names each mutant with its test. One more mutant cannot fail, because it
  changes nothing. `_determine_status_of` can drop the set it passes, and
  `build_thread_context` then reads the same set itself.
- **Tests.** Two more hermetic suites answer the new reads: `test_email_reply_zero.py` and
  `test_email_thread_single_classification.py`.

**Review round 2 (2026-10-03).** The re-verifier passed round 1 with no P0 and no P1. This round
fixes its five small findings.

- **Rebase.** Main moved: #602 (EM-T8f-3) changed `work_plan.md` and this spec. The WS-17 row
  keeps the text of main and adds only the EM-T8e-1 entry.
- **A replayed Message-ID (F1, P2).** An outsider who got a real mail of B can forge `From: B`
  to A with the same Message-ID. The Sent copy of B named the outsider, and the cold check still
  stopped. Now the copy must name this mailbox in To, Cc or Bcc.
- **The provider in the Fix fence (F3).** The 404 rolls back the local rows, so a label write
  that ran before the check left no local trace. A recording provider now counts the writes.
  It sees none for the mail of B, and one for the control mail of A.
- **Bcc and unknown recipients (F4, P3).** The participant rule reads To, Cc and Bcc. A mail with
  no recipient in any list keeps its status, because its recipients are unknown.
  `identity.recipient_lists_sql` is the one reader of the three lists, for the proof and the rule.
- **Four more fences (F5).** They cover an empty Message-ID, a NULL Cc, the DONE row on the
  self-only path and the folder test of the proof. The folder test matters most. One forged mail
  to both A and B puts a copy with the same Message-ID in the inbox of mailbox B as well. Only
  the folder test refuses that copy.
- **Known limit (F2), closed for Gmail by EM-G2 and EM-G9.** Outlook stores
  `internet_message_id`, and Gmail does too since EM-G2 (#626, §12.3.2). EM-G9 proves the Sent-copy
  proof from Gmail to Outlook and back (§12.3.11). IMAP never sets it. So a pair with an IMAP
  mailbox gets the cold check, which is as safe as before EM-T8e-1.
- **Mutation result.** The run killed 50 of 50 mutants: 42 from the first build and round 1,
  and 8 from round 2. Each fence below is in `tests/unit/test_email_ai_context.py`.

| Mutant | What the mutant breaks | The test that kills it |
|---|---|---|
| `b_self_set` | the set holds the current mailbox only | `test_the_set_is_the_mailboxes_of_the_member_in_this_org` |
| `b_org_pred` | the organization compare in the SQL | `test_the_org_predicate_holds_where_rls_does_not_bind` |
| `b_payload` | the set in the `/rules/test` payload | `test_the_rule_match_payload_reads_another_mailbox_as_self` |
| `b_recipient_role` | the set in the recipient role | `test_the_rule_match_payload_reads_another_mailbox_as_self` |
| `b_thread_scopes` | the set in the thread scopes | `test_the_thread_and_the_conversation_read_another_mailbox_as_ours` |
| `b_conversation` | the set in the conversation check | `test_the_thread_and_the_conversation_read_another_mailbox_as_ours` |
| `b_digest` | the set in the digest window | `test_the_digest_leaves_another_mailbox_out` |
| `b_digest_ours` | the set in the digest counterparty | `test_the_digest_leaves_another_mailbox_out` |
| `b_cleanup` | the set in the cleanup scope | `test_the_cleanup_scope_leaves_another_mailbox_out` |
| `b_sender_cats` | the set in the sender categories | `test_the_sender_categories_leave_another_mailbox_out` |
| `b_resolve` | the pair check of `resolve_thread` | `test_resolve_refuses_a_thread_of_another_mailbox` |
| `b_rule_test` | the mailbox check of `/rules/test` | `test_rule_test_refuses_a_mail_of_another_mailbox` |
| `b_feedback_rules` | the rule check of `/rules/feedback` | `test_feedback_refuses_a_rule_of_another_mailbox` |
| `b_drafter` | the sending mailbox in the reply prompt | `test_the_reply_prompt_names_the_sending_mailbox` |
| `b_rule_copy` | the mailbox From of a rule draft copy | `test_a_rule_draft_copy_stores_the_mailbox_as_from` |
| `b_reply_copy` | the mailbox From of the `/draft-reply` copy | `test_the_draft_reply_copy_stores_the_mailbox_as_from` |
| `b_compose` | the sending mailbox in compose-assist | `test_new_mail_in_compose_assist_names_the_sending_mailbox` |
| `b_thread_box` | the thread read from the mailbox of the mail | `test_it_reads_the_thread_of_a_and_the_voice_of_b` |
| `b_voice_box` | the sent examples from the sending mailbox | `test_it_reads_the_thread_of_a_and_the_voice_of_b` |
| `r1_runner_recent` | the set in `test_rules_recent` | `test_the_preview_on_recent_mail` |
| `r1_runner_one` | the set in `run_rules_on_message` | `test_the_run_of_one_message` |
| `r1_runner_past` | the set in Process past emails | `test_process_past_emails` |
| `r1_runner_job` | the set in the automatic run | `test_the_automatic_run_and_its_cold_check` |
| `r1_backfill` | the set in the Reply Zero backfill | `test_the_reply_zero_backfill` |
| `r1_proj_scope` | the set in `_PROJ_SCOPE` | `test_the_digest_categories_leave_another_mailbox_out` |
| `r1_decide_facts` | the set in the decide facts | `test_the_decide_facts_and_the_cc_note_read_the_set` |
| `r1_cc_note` | the set in the Cc note | `test_the_decide_facts_and_the_cc_note_read_the_set` |
| `r1_fb_mail` | the mail check of `/rules/feedback` | `test_feedback_refuses_a_mail_of_another_mailbox` |
| `r1_fb_thread` | the thread check of `/rules/feedback` | `test_feedback_refuses_a_thread_of_another_mailbox` |
| `r1_guidance` | the rule check of `/rules/guidance` | `test_guidance_refuses_a_rule_of_another_mailbox` |
| `r1_cold_no_proof` | the proof for a self sender | `test_the_cold_check_skips_only_a_proven_own_send[forged]` |
| `r1_proof_any_box` | the test "another mailbox" of the proof | `test_the_cold_check_skips_only_a_proven_own_send[own_copy]` |
| `r1_proof_any_member` | the test "a mailbox of the member" | `test_a_sent_copy_of_another_member_proves_nothing` |
| `r1_selfonly_recompute` | the self-only check of the status authority | `test_the_status_authority_files_it_as_fyi` |
| `r1_selfonly_determine` | the self-only check of the resolver | `test_the_resolver_asks_no_model_for_it` |
| `r1_selfonly_project` | the self-only check of the projection | `test_the_projection_never_opens_it` |
| `r1_selfonly_no_recipients` | the recipients in the participant rule | `test_the_participant_rule` |
| `r1_nudge` | the mailbox in the nudge payload | `test_the_follow_up_nudge_names_the_mailbox` |
| `r1_save_copy` | the mailbox From of the `/drafts/save` copy | `test_a_saved_draft_copy_stores_the_mailbox_as_from` |
| `r1_pin_wide` | the exact test for another mailbox, with a substring test in its place | `test_the_pin_guard_refuses_own_mailboxes_only[gmail.com]` |
| `r1_pin_no_exact` | the exact rule for another mailbox | `test_the_pin_guard_refuses_own_mailboxes_only[SELF-B]` |
| `r1_pin_no_substring` | the substring rule for this mailbox | `test_the_pin_guard_refuses_own_mailboxes_only[fracktal-t8e.test]` |
| `r2_proof_no_recipient` | the test "names this mailbox" of the proof | `test_the_cold_check_skips_only_a_proven_own_send[replayed]` |
| `r2_proof_empty_id` | the test "not empty" of the Message-ID | `test_the_cold_check_skips_only_a_proven_own_send[empty_id]` |
| `r2_proof_no_folder` | the test "`sent` folder" of the proof | `test_the_cold_check_skips_only_a_proven_own_send[inbox_copy]` |
| `r2_cc_no_case` | the `CASE` around the Cc list | `test_the_participant_rule_reads_bcc_and_unknown_lists[outsider-null-cc]` |
| `r2_no_bcc` | the Bcc list | `test_the_cold_check_skips_only_a_proven_own_send[proven_bcc]` |
| `r2_no_empty_rule` | the rule for a mail with no recipient | `test_the_participant_rule_reads_bcc_and_unknown_lists[no-recipients]` |
| `r2_preserve_done` | `preserve_done` on the self-only path | `test_a_done_row_stays_done` |
| `r2_feedback_check_late` | the mail check before the label write | `test_feedback_refuses_a_mail_of_another_mailbox` |

**Scope.** `apps/services/gateway/gateway/routes/email/**` and new tests. Not in scope: the
agent, the UI, `routes/crm/**`, and the three files that EM-T8d edits (`transport/messages.py`,
`transport/accounts.py`, `mailbox_identity.py`).

1. **Self covers each mailbox of the member (D-EM-27).** One SQL helper in
   `automation/identity.py` returns the lower-case addresses of each mailbox of the member who
   owns `:aid`. Row level security binds the organization. `identity.py` takes that set, and an
   address in the set is `self`. The internal domain stays the domain of the current mailbox.
   Each caller that compares with one address today uses the set. The callers are the rule match
   payload, the thread status, the conversation check, the cold check and the digest. The sender
   pin guard, the cleanup scope and the sender categories are callers too.
   `sender_scope(from_email, self_email)` keeps working with one address, because
   `routes/crm/auto_lead.py` calls it that way.
2. **The drafter speaks as the sending mailbox (MB-14).** The prompt names the address of the
   sending mailbox and its label. The local draft copies store that address as the From.
3. **A reply from another mailbox keeps the thread.** Compose-assist can answer a mail that is not
   in the sending mailbox. It then reads the mail and its thread from the mailbox of the mail,
   under the owner predicate. Each item that is the voice of the writer comes from the sending
   mailbox:
   - the voice profile and the signature
   - the sent examples to that sender
   - the reply memories and the few-shot examples
4. **The server refuses a pair that does not match.**
   - `POST /email/reply-zero/resolve` (`resolve_thread`, which the chat tool `mark_thread_done`
     calls) returns 404 when no mail has that `(account_id, thread_id)`. This covers the done, the
     reopen and the dismiss branches.
   - `POST /email/rules/test` returns 404 when `email_id` is not a mail of `account_id`.
   - `POST /email/rules/feedback` returns 404 when `expected`, or a value of
     `matched_rule_ids`, is not a rule of `account_id`. It also returns 404 when `message_id` is
     not a mail of `account_id`. It does the same when no mail of `account_id` has `thread_id`
     (review round 1).
   - `POST /email/rules/guidance` returns 404 when `rule_id` is not a rule of `account_id`
     (review round 1).
5. **The fences of D-EM-18 (item 1).** One R8 suite seeds two mailboxes of one member, each with
   its own marker. For each background loader, the read for A holds no marker of B. These are the
   loaders:
   - the rules, the rule patterns, the rule guidance and the sender history
   - the thread context, the prior contact of the cold check and the sender pin evidence
   - the voice samples, the sent few-shot and the assistant context
   - the Process past emails range, the digest window and the pending embeddings

**Not built in EM-T8e-1.** The display name of the mailbox. `email_accounts` has no column for
it, so the prompt names the address and the label. A later slice can store the name that the
provider returns at connect.

**Fences (R7).** A new file, `tests/unit/test_email_ai_context.py`, names its fence ids.

- `email-self-each-mailbox` (R8): a mail from another mailbox of the member is `self`. The same
  address in a mailbox of a second organization is not. The second case runs as the app role,
  because the owner role bypasses row level security.
- `email-drafter-sending-mailbox`: the prompt and the local draft copy name the sending mailbox,
  never the sign-in address.
- `email-reply-other-mailbox-thread` (R8): a reply from B to a mail of A reads the thread of A,
  and the voice items of B.
- `email-pair-refused` (R8): each route and each input in item 4 returns 404, and writes nothing.
- `email-ai-context-one-mailbox` (R8): item 5, one case for each loader.

**Verification.**

```
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_ai_context.py tests/unit/test_email_multi_inbox.py tests/unit/test_email_draft_direction.py tests/unit/test_email_sender_direction.py tests/unit/test_email_draft_context.py tests/unit/test_email_reply_zero.py tests/unit/test_email_thread_resolve.py tests/unit/test_email_digest.py tests/unit/test_email_cold_gate_case.py tests/unit/test_email_fix_feedback.py tests/unit/test_email_rule_pattern_guards.py tests/unit/test_email_recipient_and_pattern_guard.py tests/unit/test_email_rules_engine.py tests/unit/test_email_cleaner_category_scope.py tests/unit/test_email_sender_category_projection.py tests/unit/test_email_owner_scope_fence.py tests/unit/test_email_automation_tenancy.py tests/unit/test_email_rulepath_draft_parity.py tests/unit/test_email_sent_fewshot.py tests/unit/test_crm_auto_lead.py -v -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit/test_email_ai_context.py --select F821,F601,F602,F502,F7,B006
```

The R8 cases must show PASSED, not SKIPPED. Point `DATABASE_URL` and
`TENANT_LADDER_DATABASE_URL` at a private base database.

##### EM-T8e-2 — the chat tools bind to one mailbox (agent)

**Status.** ✅ MERGED #597 (2026-10-03). No migration.

**As built (2026-10-03).**

- **A reply that names another mailbox stops.** `send_email` reads the mail first and sends
  nothing. The answer names the mailbox of the mail as "label · address (account_id <id>)". The
  EM-T8a fence is now `test_a_chat_reply_that_names_another_mailbox_is_refused`. A reply that
  names no mailbox goes out from the mailbox of the mail.
- **`read_thread` and `manage_inbox` take no `account_id`.** MAF drops an argument that a tool
  does not declare, so an id from the model cannot reach the request. When two mailboxes hold
  one `thread_id`, `read_thread` merges nothing and asks for an `email_id` (edge case 13).
- **New mail.** `send_email` asks `sent-from` for the bare address of the first recipient, in
  lower case. An answer that names no mailbox of the member counts as no answer.
- **Item 3.** `account_id` is the first argument, and it is optional. A required argument after
  it is keyword-only, so the tool schema still marks that argument as required.
- **A question never holds `id=`.** It lists each mailbox as "label · address (account_id <id>)",
  because `RULE_ID_RE` in `EmailToolCards.tsx` reads `id=` as the id of a rule.
- **One more fence.** `email-chat-thread-mailbox` fences the `read_thread` rule of item 1.
- **Two UI readers changed, outside the stated scope (2026-10-03).** The new answers of the agent
  made two cards wrong, so this slice fixes them.
  - A refused send, a question and a cancel get the no-action card ("Not sent", "Needs your
    answer", "Cancelled"). Before, the generic card said "Email sent" over "Not sent.", which
    told the member that mail went out. Fence: `email-chat-no-action-card` in
    `src/components/email/noAction.test.ts`. It reads the lead words from `agents.py`.
  - The thread card reads the thread in the mailbox of the mail, as `read_thread` now does.
    Fence: `email-chat-thread-card-mailbox`.

**Review round 1 (2026-10-03).** The verifier and the reviewer found no P0, and the binding held.
They found live answers that still drew a done card. This round fixes them.

- **One lead for each answer that did not act.** The send, bulk and item 3 tools start such an
  answer with "Not sent.", "Nothing changed.", a question, or "Cancelled". Before, "No connected
  mailbox has the id …" drew "Email sent". Fence: `email-chat-no-action-lead`. It reads every
  `return` of those tools, and it checks that `noActionOf` knows each lead.
- **The card loop checks first.** `noActionOf` runs before the list, thread, info and rule cards,
  not only in `renderCard`.
- **A thread read that refused fetches nothing.** With no `email_id` and no "Thread:" head, the
  thread card shows the text. A fetch with no mailbox would merge the two mailboxes.
- **The send card shows each bcc address and each attachment.** A mail body can ask the model
  to add a hidden recipient or a file, so the member must see both before the send. The card
  keeps 500 characters, and the sender of a mail sets the subject of a reply. So both come
  before the subject, and the subject is clipped. Fence: `email-chat-send-card-shows-hidden`.
- **No mailbox connected.** A send and each item 3 tool change nothing and say so. Fence:
  `email-chat-no-mailbox`.
- **`instructions.md`** no longer says that unsubscribe takes the mailbox from the mail.

**Found, and not fixed in this slice.**

- `own_tool_scope` in the `config.json` of the agent holds none of the eight tools of item 3. The
  executor keeps only the tools that it names, so the live chat cannot call them yet.
- `RuleResultCard` and `SettingsUpdatedCard` read `args.account_id`. The live chat cannot call
  their tools yet (the item above), so this has no live effect.
- `unsubscribe_sender`, `learn_rule_pattern` and `mark_thread_done` act in the mailbox that the
  model names. None is live. EM-T8e-1 makes the server refuse a thread or rule of another
  mailbox.
- `ManageInboxCard` counts the ids that the model sent, not the `affected` count of the answer.
- Three sentences of the EM-T8e narrowing are too long for STE. All three EM-T8e branches share
  that text, so the last of them to merge fixes it.

**Scope.** `apps/agents/agent-email-assistant/agents.py`, `instructions.md` and tests. No gateway
file. No UI file.

1. **An act on a mail takes the mailbox of the mail (§11.3 rule 1).**
   - `send_email` in reply mode reads the mail first. When the model names another mailbox, the
     tool refuses, sends nothing, and names the mailbox of the mail as "label · address". This
     replaces the re-bind of EM-T8a (§11.7.1 item 4) for a send, because a send cannot be undone.
     It rewrites the EM-T8a fence `test_a_chat_reply_goes_out_from_the_mailbox_of_the_mail`.
   - `draft_reply` keeps the re-bind of EM-T8a. A draft can be changed and is not sent, its first
     line names the mailbox, and the server refuses a wrong pair. The EM-T8a fence
     `test_a_chat_draft_is_made_in_the_mailbox_of_the_mail` stays as it is.
   - `read_thread` reads with the mailbox of the mail, never the id from the model.
   - `manage_inbox` sends the mail ids with no `account_id`, so each mail acts in its own mailbox.
   - `mark_thread_done` has no mail id. It stays as it is and fails closed on the 404 of
     EM-T8e-1.
2. **New mail with no mailbox named (rule 3).** `send_email` with no `account_id` uses the only
   mailbox when there is one. With two or more, it asks `GET /email/contacts/sent-from` for the
   first recipient, in lower case. It uses the mailbox that the answer names. An empty answer
   gives the question "Send from which mailbox?" and lists each mailbox as "label · address". It
   never guesses.
3. **A rule or a setting (rule 4).** These tools take `account_id` as optional: `create_rule`,
   `create_rules_from_prompt`, `install_default_rules`, `update_assistant_settings`,
   `save_knowledge`, `generate_writing_style`, `learn_rule_pattern` and `run_rules`. With no
   `account_id` and two or more mailboxes, each one asks "Which mailbox?" and lists them. With
   one mailbox, each uses it. `update_rule`, `delete_rule` and `delete_knowledge` take an object
   id, and bind through that object.
4. **"label · address" everywhere (MB-15).** `list_accounts`, the result tags and the From line of
   each card use `display_label` and the address. `instructions.md` says the same, and its rule
   3 takes the `sent-from` step.

**The shapes that the UI reads stay.** The row is `id=<id> [<tag>] | …`. The first line of a
draft is `Draft from <from> (mailbox <id>)`. The fence `email-chat-draft-card-mailbox` depends on
that line.

**Fences (R7).** A new file, `tests/unit/test_email_chat_binding.py`, names its fence ids. It
uses fakes of the gateway, so it needs no database.

- `email-chat-reply-refuses-other-mailbox`: a reply with a wrong `account_id` sends nothing and
  names the right mailbox.
- `email-chat-new-mail-binding`: one mailbox, a `sent-from` match, and an empty answer that gives
  the question.
- `email-chat-rule-asks`: each tool of item 3 asks with two mailboxes and no `account_id`.
- `email-chat-bulk-no-account`: `manage_inbox` sends no `account_id`.
- `email-chat-label-address`: each tag and each From line carries "label · address".
- `email-chat-ui-shapes`: the two shapes above.

**Verification.**

```
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_chat_binding.py tests/unit/test_email_multi_inbox.py tests/unit/test_email_tool_consolidation.py tests/unit/test_email_present_groups.py tests/unit/test_email_assistant_settings.py tests/unit/test_email_auto_draft_defaults.py tests/unit/test_agent_gateway_identity.py tests/unit/test_agent_manifest.py tests/unit/test_email_from_row.py -v -rs
uv run ruff check apps/agents/agent-email-assistant tests/unit/test_email_chat_binding.py --select F821,F601,F602,F502,F7,B006
```

Do not smoke-test `send_email` on production. It sends real mail (CLAUDE.md §3a rule 3).

**Build order.** EM-T8e-1 and EM-T8e-2 touch disjoint source files, and each can merge first.
Each slice writes its status under its own heading. The slice that merges second rebases and
merges the shared status lines in place. EM-T8e-3 merges after EM-T8e-2. Its All inboxes persona
tells the model to leave `account_id` out, and only EM-T8e-2 makes that argument optional.

##### EM-T8e-3 — the chat scope (UI)

**Status.** ✅ MERGED #599 (2026-10-03), after EM-T8e-2 #597. No migration.

**As built (2026-10-03).**

- **The decisions are pure functions.** `lib/chatScope.ts` holds the scope of the chat, the
  options of the picker and the settings read. Vitest runs in node and cannot render
  `EmailAssistantChat`, so a source scan checks the wiring.
- **The picker (item 1).** "All inboxes" comes first, for two or more mailboxes. Each mailbox
  shows as "label · address". When the label is the address, the address shows once. A pick
  stays while the page scope stays. When the page scope changes, the chat follows the page.
- **The persona (item 2).** `chatMailboxName()` makes "label · address" from `mailboxLabel()`.
  It reads the store shape and the gateway shape, so `chat/page.tsx` gets the MB-15 fix with no
  change. The list writes each mailbox as "label · address (account_id <id>)" in both scopes.
  All inboxes ignores `selectedAccountId` and `settings`. An open mail names its mailbox.
- **The fallback (item 3)** calls `pickInitialView`. The note "the chat says so" is not built.
- **The page (item 4)** passes the prop `pageScope`, which is `ALL_INBOXES` or a mailbox id. The
  prop `selectedAccountId` is gone.
- **Item 5** is a test only. No card file changed.

**Fences.** `src/app/email/lib/chatScope.test.ts` names the five fence ids. A mutation run killed
13 of 13 mutants. `allInboxes.test.ts` keeps the automation half of
`email-all-automation-names-mailbox`.

**Review round 1 (2026-10-03).** The verifier found no P0 and no P1. This round fixes its P2
findings.

- **The settings of the last mailbox clear first (F1).** A switch from A to B clears the
  settings and the chat model before the read of mailbox B starts. Before, the standing orders of
  A stood under the name of B while the read was out, and after a failed read.
- **A read tool that needs one mailbox runs for each mailbox (F4).** The All inboxes persona
  says so. Without it, the model could answer "what needs a reply?" for one mailbox as if for
  all.
- **Three fences got tighter (F2, F3, F1).** The picker mark in All inboxes, the page scope that
  a pick holds, and the clear before the read. A mutation run killed 5 of 5.
- **The deferred items have an owner (F5).** The colour dot in the picker and the note "the chat
  says so" moved to EM-T8f.

**Not checked.** This session had no browser. Nobody looked at the chat in light mode, at compact
density or under a changed accent.

**Scope.** `workbench/control_plane/src/app/email/` (`EmailAssistantChat.tsx`,
`lib/emailAssistantPersona.ts` and `page.tsx`) and tests. No agent file and no gateway file. Not
in scope: `src/components/AgentChat.tsx`, which draws the picker, `src/components/email/
EmailToolCards.tsx`, and `src/app/chat/page.tsx`, which stays on one mailbox.

1. **The picker.** `EmailAssistantChat` gives the picker an "All inboxes" option when the member
   has two or more mailboxes. It names each mailbox as "label · address", from `mailboxLabel()`.
   It starts on the scope of the page. The member can change it, and the change holds only for
   that chat. A pick calls neither `selectAccount` nor `selectAll`. A colour dot in the picker needs
   `AgentChat.tsx`, so it is deferred.
   The chat model in All inboxes is "tier-powerful", the current default with no mailbox.
2. **The persona** names the scope.
   - In one mailbox, it gives that `account_id` and the settings of that mailbox: the standing
     instructions and the writing style.
   - In All inboxes, it gives no default `account_id`, and it lists each mailbox as
     "label · address (account_id <id>)". It holds the settings of no mailbox, because each
     mailbox has its own (D-EM-24). It tells the model to leave `account_id` out of a write act,
     so the tool binds it or asks (§11.3).
   - An open mail names its mailbox in the persona, in both scopes.
3. **Removed mailbox (edge case 17).** A chat scope on a mailbox that is gone falls back to All
   inboxes, or to the only mailbox. The rule of `pickInitialView` decides it. The note "the chat
   says so" of edge case 17 moved to EM-T8f.
4. **The chat keeps All inboxes.** EM-T8d moved the page out of All inboxes when automation or
   the chat opened. At that time the chat had no All inboxes scope. The chat now has one, so that
   move skips the chat. Automation still moves. The page passes the scope of the chat to
   `EmailAssistantChat`, never the hidden `selectedAccountId`. This rewrites the chat half of the
   EM-T8d fence `email-all-automation-names-mailbox` (`allInboxes.test.ts`), and keeps its
   automation half.
5. **No hidden mailbox.** In All inboxes, `emailContext.accountId` is null, and no settings read
   runs. Each card already reads the mailbox from the tool result or the mail, or fails closed.
   EM-T8e-2 owns the one card fix, the thread card.

**Fences (R7).** The vitest files name their fence ids.

- `email-chat-scope-picker`: the picker offers All inboxes only with two or more mailboxes, and
  it starts on the scope of the page. A pick changes the chat, not the page.
- `email-chat-scope-persona`: a pure test of `buildEmailAssistantPersona` for both scopes. In All
  inboxes, the text holds no "Active account", no default `account_id` and no settings of a
  mailbox. It lists each mailbox as "label · address".
- `email-chat-scope-fallback`: a removed mailbox gives All inboxes, or the only mailbox.
- `email-chat-keeps-all-inboxes`: the move of EM-T8d skips the chat.
- `email-chat-scope-no-hidden-mailbox`: in All inboxes, `emailContext.accountId` is null, and no
  `getAssistantSettings` call runs. An open mail names its mailbox in the persona.

**Verification.** In `workbench/control_plane`, run
`npx tsc --noEmit && npx vitest run src/app/email src/components src/lib/theme`. Look at the
chat in light mode, at compact density, and under a changed accent (CLAUDE.md §4).

#### 11.7.6 EM-T8f — settings for each mailbox

**Status.** 📝 Narrowed 2026-10-03, verified against the code at a5085fec. Three pull
requests, T8f-1 to T8f-3. No migration.

**Not in scope.** These acts are OWNER-GATE:
- a sweep of the `#acct:<id>` memories that earlier disconnects left behind (a production one-off)
- a test disconnect of a real production mailbox
- the `MEM0_ENABLED` flip
"The counts that go" in the disconnect dialog (§11.4) is not built in EM-T8f.

##### EM-T8f-1 — copy rules, the memory purge and `created_at` (backend, R8)

**Status.** ✅ MERGED #605 (2026-10-03). No migration.

**As-built notes.**
- **The forward rule (D-EM-29).** The copy leaves out the whole rule, and not only its FORWARD.
  Without the FORWARD, the other actions of the rule stay. An ARCHIVE would then hide mail that
  the member meant to forward. The answer names the rule. The owner can reverse this.
- **The address scan.** The guard finds each `x@y` token in the To, Cc and Bcc of a FORWARD. The
  strict `getaddresses` of Python 3.12 gives no address for a field that it cannot parse. The
  guard would then let a loop through, so it does not use that parser.
- **The answer.** `copied` holds the names in the target. `renamed` holds `{name, copied_as}`.
  `left_out` holds `{name, reason}`. The reason is `disabled`, `forward_to_own_address` or
  `reply_rule_exists`.
- **The names.** The compare of names ignores case. The INSERT has
  `ON CONFLICT (account_id, name) DO NOTHING`, so a name that another writer takes moves the copy
  to the next name. After 50 tries the route answers 409, and the copy writes nothing.
- **One reply rule (D-EM-6).** The target keeps one reply rule at most (`rules._is_reply_rule`).
  The "Auto draft replies" switch edits only the first reply rule. A second one would go on
  drafting while the switch shows OFF. So a source reply rule is left out as `reply_rule_exists`
  when the target holds a reply rule, enabled or not. It is also left out when the copy already
  took a reply rule.
- **Drafting (D-EM-6).** The copied reply rule keeps DRAFT_EMAIL only when the target drafts. A
  DRAFT_EMAIL on a rule that is not a reply rule stays, because the switch does not govern it.
- **The columns.** Three tuples in `rule_copy.py` name each column of `email_rules` and
  `email_actions`. The fence reads `information_schema` and fails on a column in no tuple.
- **The purge.** `MemoryClient.delete_scope` reads pages of 100 rows with `show_expired=True`.
  Unlike `delete`, it raises on a Mem0 error. A page that comes back twice raises, and so do more
  than 1000 pages. So a fault fails a run and does not hang it. With Mem0 off the count is 0.
- **The refusals.** `delete_scope` refuses `"*"`, a value that is not a `str` and a blank value,
  before any read. The pgvector store of mem0ai 2.2.1 turns `"*"` into `payload ? 'user_id'`,
  which matches each memory of each tenant. The purge also refuses an account id that is not a
  UUID. Since round 2, `core.email_memory_scope` builds the key from `str(UUID(id))` for each
  writer and for the purge. Some writers take the id from the request, and Postgres finds the
  row for an id in capitals.
- **Two passes.** Three writers fill the key. The third is the precedent of each draft, which a
  background task adds, and no lock holds it. So the purge runs `delete_scope` twice, 120 seconds
  apart. An add that lands after the second pass stays.
- **The log.** A purge logs the account id and the counts `first` and `second`. A failed pass
  logs the pass and the error class only. No purge log holds an address or the text of a memory.
- **`created_at`.** Each account read returns ISO text with six digits of microseconds. Two values
  then sort as text in the order of time.
- **Findings, not built here.** `MemoryClient.get_all` passes no `top_k`, and mem0ai 2.2.1 then
  returns 20 rows at most. The memory panel can show only 20. `routes/admin/members.py` deletes
  the mailboxes of a purged member and starts no Mem0 purge.
- **Deferred: a durable sweeper.** The purge task lives in one gateway process. A restart during
  the 120 seconds loses the second pass, and an add after the second pass stays. A durable sweep
  of `#acct:<id>` keys with no mailbox row closes both. It is not built here. A run of it against
  production is OWNER-GATE, as the sweep of older disconnects is.
- **Verification (2026-10-03).** With `DATABASE_URL` unset, the block below gave 278 passed and 2
  skipped. The two skips are the WS-29 gates of `test_tenant_coverage.py`, which read
  `DATABASE_URL`. With `DATABASE_URL` set to the base database as well, those two gates failed
  and the other 278 passed. `test_app_role_cannot_bypass_rls` reuses an engine of a stopped event
  loop. `test_live_catalog_has_column_force_and_policy` reads a ladder that an earlier suite
  replayed with no FORCE RLS phase. Both fail the same way on the base tree. Each of 16
  mutations of the fences went red.

**Review fix round 1 (2026-10-03).** An adversarial reviewer and a verifier checked 41c9c0b4.
They found no P0, and no leak across members or organizations. The round fixed one P1, four P2s,
four fence gaps and two doc defects. The agent rebased the branch on `a6b5b3ae` (#602). The
rebase dropped the two narrowing commits, because #602 carries them.

| Finding | Fix | Mutant | The test that goes red |
|---|---|---|---|
| P1: a renamed copy of a reply rule drafts after the switch goes OFF | The target keeps one reply rule (`reply_rule_exists`) | the check off | `TestTheTargetKeepsOneReplyRule` |
| P1, second half | The copy counts the reply rule that it took | `reply_held` not set | `test_a_second_reply_rule_of_the_source_is_left_out` |
| P2: a late Mem0 add lands after the purge | A second pass after 120 seconds, logged as `first` and `second` | no second pass | `TestTheSecondPass` |
| P2: `delete_scope("*")` deletes each memory of each tenant | Refuse `"*"`, a non-`str` and a blank value | each check off | `test_delete_scope_refuses_before_any_read` |
| P2: the page loop has no cap | 1000 pages, then `RuntimeError` | `while True` | `TestThePageCap` |
| P2: a path id in capitals misses the Mem0 key | The key holds `str(UUID(id))` | the raw id | `test_a_path_id_in_another_form_purges_the_canonical_key` |
| M5: no fence for a rule that is not a reply rule | That rule keeps DRAFT_EMAIL | `keep_draft=target_drafts` | `TestDraftingFollowsTheTarget` |
| M6: no fence for zero microseconds | A seed with a whole second | plain `isoformat()` | `test_each_account_read_returns_created_at` |
| M7: no fence for the 409 | A copy that cannot land | the 409 path answers a name | `TestACopyThatCannotLand` |
| M8: no fence for the strong reference | The set holds the running task | no `_PURGES.add` | `TestTheStrongReference` |

- **Docs.** The D-EM-29 row in §11.2 now says that the copy leaves out the whole rule (F1). The
  docstring of `memory_purge.py` names the three writers (F9).
- **Mutation check.** 27 mutations went red: the 11 of the table and the 16 of the build, with
  their round 1 anchors. The script restored each file and checked its hash.
- **Verification.** With only `TENANT_LADDER_DATABASE_URL` set, the block below gave 307 passed and
  2 skipped (the two WS-29 gates of `DATABASE_URL`). All 127 email suites, with the memory and
  seam suites, gave 2291 passed. The ruff gate passed.

**Review fix round 2 (2026-10-03).** The verifier passed round 1 with no code blocker. Round 2
fixed two P3s and two P4s. The agent rebased the branch on `ceba07cb` (#604, EM-T8e-1). In
`apps/services/gateway/AGENTS.md` both sides added a bullet after the EM-T4e bullet, and the
agent kept both.

| Finding | Fix | Mutant | The test that goes red |
|---|---|---|---|
| F1 (P3): a writer that takes the id from the request keys Mem0 on capitals | `core.email_memory_scope` builds `str(UUID(id))` when the id parses | the key keeps the request id | `TestTheKeyIsCanonical` and `test_a_writer_given_capitals_writes_the_key_that_the_purge_deletes` |
| F2 (P3): a writer commits "Needs Reply" between the read and the INSERT | A reply rule is never renamed. When the target holds its name, the copy leaves it out as `reply_rule_exists` | rename allowed, or the check gone | `test_a_reply_rule_that_another_writer_commits_during_the_copy` |

- **F1, the writers.** The writing-style route, the draft routes and the send route take the id
  from the request. A canonical id does not change, so a key from a database id stays the same.
- **F2, the residual.** A writer that commits a reply rule under ANOTHER name in the same window
  still gives two reply rules. Only a lock that "Add defaults" also takes can close it. It is not
  built here.
- **Docs.** The docstring of `delete_scope` gives the real reason to refuse a value that is not a
  `str` (F4). Version 2.2.1 of mem0ai changes such a value with `str()`. The Fences list and scope item 2
  carry the fences and the log of rounds 1 and 2 (F5).
- **Mutation check.** The 4 mutants of the table went red. The script restored each file and
  checked its hash.
- **Verification.** With only `TENANT_LADDER_DATABASE_URL` set, the block below gave 315 passed and
  2 skipped (the two WS-29 gates of `DATABASE_URL`). All 128 email suites, with the memory and
  seam suites, gave 2373 passed. The ruff gate passed.

**Scope.** A new `routes/email/automation/rule_copy.py`, a new `routes/email/memory_purge.py`,
`automation/__init__.py`, `transport/accounts.py`, `packages/acb_memory/acb_memory/mem0_client.py`
and tests. It edits neither `automation/rules.py` nor `core.py`, because EM-T8e-1 edits both.
After EM-T8e-1 merged (#604), review round 2 edits `core.email_memory_scope` only.

1. **Copy rules (item 2).**
   - `POST /email/rules/copy` takes `from_account_id` and `to_account_id`. Each must be a mailbox
     of the member, else 404. The same id twice gives 422.
   - It copies each enabled rule with each column except `id`, `account_id`, `organization_id`
     and the timestamps. It copies each action of that rule.
   - It copies no disabled rule, rule pattern, rule guidance, learned pattern, assistant setting,
     voice profile or knowledge (D-EM-18).
   - A name that the target holds gets " (copy)", then " (copy 2)". A copy never fails on the
     unique name.
   - Each copied rule gets `created_at = now()`, so the new-mail floor of the target never moves
     back into the imported mail.
   - A reply rule keeps DRAFT_EMAIL only when the stored `draft_replies` of the target is true
     (D-EM-6). D-EM-29 governs a FORWARD to an own address.
   - The answer lists the copied names, the renamed names and the rules that it left out.
2. **The memory purge (item 4, MB-17).**
   - After the DELETE of a disconnect commits, a task with a strong reference deletes each Mem0
     memory under `email_memory_scope(owner, account_id)`. It makes two passes, 120 seconds
     apart. It logs `email.disconnect.memory_purged` with the counts `first` and `second`.
   - The helper refuses a scope that has no `#acct:`. A purge of the bare member scope would
     delete every personal memory of the member.
   - A 404, a 409 or a failed DELETE makes no purge call.
   - A failed purge still answers 204 and logs `email.disconnect.memory_purge_failed`. The log
     never holds the text of a memory.
   - `mem0_client.py` gains `delete_scope(user_id) -> int`. It pages until the scope is empty.
3. **`created_at` on the account model.** Each account read returns it, so the UI can name the
   next default (item 3).

**Fences (R7).**
- `tests/unit/test_email_rule_copy.py` (R8, the app role):
  - `email-rule-copy-owner`: a mailbox of another member, or of a second organization, gives
    404 and writes no row. The same id twice gives 422.
  - `email-rule-copy-enabled-only`: each column and each action of an enabled rule matches its
    source. No disabled rule, pattern, guidance or learned pattern arrives.
  - `email-rule-copy-names`: "X", then "X (copy)", then "X (copy 2)", with no error.
  - `email-rule-copy-floor`: the new-mail floor of the target is the time of the copy.
  - `email-rule-copy-drafting`: with `draft_replies` false or absent, no copied reply rule holds
    DRAFT_EMAIL. With it true, the rule keeps it.
  - `email-rule-copy-forward-loop`: a FORWARD to an own address is left out and named.
  - `email-rule-copy-one-reply-rule` (round 1): the target keeps one reply rule. After a copy,
    the switch OFF stops each reply draft.
  - `email-rule-copy-reply-race` (round 2): another writer commits "Needs Reply" between the read
    and the INSERT. The reply rule is left out, and never lands under another name.
  - `email-rule-copy-409` (round 1): a copy that cannot land answers 409 and writes nothing.
- `tests/unit/test_email_disconnect_memory_purge.py` (a fake Mem0 client):
  - `email-disconnect-purges-memory`: a 204 deletes each memory under `<owner>#acct:<id>`, over
    more than one page. It deletes no memory of the bare scope or of another mailbox.
  - `email-memory-key-canonical` (round 2): each form of one id gives one key. A writer given an
    id in capitals writes the key that the purge deletes (R8).
  - `email-purge-second-pass` (round 1): the second pass deletes an add that lands between the
    passes.
  - `email-purge-refuses-bare-scope`: an empty account id, or one that is not a UUID, deletes
    nothing. `delete_scope` refuses `"*"`, a value that is not a `str` and a blank value.
  - `email-purge-page-cap` (round 1): a scope that never empties raises after 1000 pages.
  - `email-purge-strong-reference` (round 1): `_PURGES` holds the task while it runs.
  - `email-purge-after-delete`: a 404, a 409 or a failed DELETE makes no purge call. A failed
    purge still gives 204, and its log holds no memory text.
  - `email-account-created-at`: each account read returns `created_at`.

**Verification.**

```
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_rule_copy.py tests/unit/test_email_disconnect_memory_purge.py tests/unit/test_email_disconnect_order.py tests/unit/test_email_multi_account.py tests/unit/test_email_presets.py tests/unit/test_email_rules_admin.py tests/unit/test_email_auto_draft_defaults.py tests/unit/test_email_owner_scope_fence.py tests/unit/test_email_automation_tenancy.py tests/unit/test_email_all_inboxes.py tests/unit/test_email_mailbox_identity.py tests/unit/test_tenant_coverage.py -v -rs
uv run ruff check apps/services/gateway/gateway/routes/email packages/acb_memory tests/unit/test_email_rule_copy.py tests/unit/test_email_disconnect_memory_purge.py --select F821,F601,F602,F502,F7,B006
```

The R8 cases must show PASSED, not SKIPPED. Do not run `test_memory_integration.py` or
`test_memory_e2e.py`.

##### EM-T8f-2 — the settings UI (after EM-T8f-1)

**Status.** ✅ MERGED #606 (2026-10-03), after EM-T8f-1 #605, whose copy route and `created_at` it
calls. No migration and no backend change.

**As-built notes.**

- **The header (item 1).** `AutomationHeader` in `AutomationView.tsx` is the one header of the four
  automation views. The page renders one `AutomationView` for desktop and for mobile, so the header
  shows on both. Its second line names the mailbox as label and address.
- **The picker.** With two or more mailboxes the header draws the chip and a `SelectButton` of each
  mailbox. The picker has no All inboxes option. With one mailbox the header shows the name as
  text, with no chip and no picker (§11.0). §11.4 says "Always", so the name shows for one mailbox.
- **The pick.** `pickSettingsMailbox` clears the Process past date and calls the `selectAccount` of
  the store. A pick of the mailbox in view, or of an id that is not a mailbox, does nothing.
- **The copy (item 2).** For each other mailbox, the rules step shows a "Copy the rules of <label>"
  button beside the presets. It shows them only before an enabled rule exists, as D-EM-24 says.
  With two or more mailboxes the step shows the chip and the address of its own mailbox.
- **No second copy.** `ruleCopier` refuses a start while a copy runs, and a pair that copied
  before. The check and the mark are one synchronous call, so a double click starts one copy. A
  failed copy does not count. The step then reads the rules again, and a copy that landed moves the
  step on.
- **The answer.** `copyReport` names the copied rules, each new name, and each rule that the copy
  left out, with its reason in plain words. The test reads the `LEFT_OUT_*` reasons of
  `rule_copy.py`, and it fails on a reason with no words.
- **The disconnect (item 3).** `nextDefaultAfter` orders by `createdAt` as text, then by `id`. A
  missing `createdAt` sorts last, as NULL does in an ascending order of Postgres. The dialog names
  the mailbox as "label · address". When that mailbox is the default, the dialog names the new
  default.
- **The store.** The store marks the same mailbox as the default after the removal. This fixes the
  STATUS DRIFT of the audit. The dialog holds its names while the disconnect runs. Without the
  hold, the store drops the mailbox first, and the sentence on the new default disappears during
  the close.
- **Notes (item 4).** `notesFromOptions` names each mailbox through `chatMailboxName`, the one rule
  for "label · address". The From picker is now `SelectButton`. This slice removes the native
  `<select>` and its `SELECT_DEBT` entry in `conformance.test.ts`.

**Outside the stated scope.**

- `onboardingRules.test.ts`: the fake `RulesStepApi` gets `copyRules`.
- `src/lib/theme/conformance.test.ts`: this slice removes the `SELECT_DEBT` entry of
  `FollowupEmailModal.tsx`. The ratchet fails a file that improved until its entry changes.
- `src/app/notes/lib/types.ts`: the account type of Notes gets the optional `display_label`.

**Narrowed.**

- The automation drawer on mobile keeps `showMailbox={false}`. The header picker is the picker on
  mobile (MB-11).
- `FollowupEmailModal.tsx` keeps its own overlay. Only its From picker moved to the house control.
- Nobody did the look check of CLAUDE.md §4 (light mode, compact density, a changed accent). The
  agent had no browser. A reviewer must do it.

**Fences.** `src/app/email/lib/mailboxSettings.test.ts` names the four fences of this slice.

**Mutation run.**

- The first run found one gap. The header test read the markup, and the chip holds the address in
  its `title`, so a header with no visible address passed. The fence now reads the visible text.
- The final run killed 33 of 33 mutants: 8 for the header, 11 for the copy, 11 for the disconnect
  and 3 for Notes. Three of them edited `transport/accounts.py`. The script restored each file and
  checked its hash.

**Verification (2026-10-03).** In `workbench/control_plane`, `npx tsc --noEmit` passed.
`npx vitest run src/app/email src/app/notes src/components src/lib/theme` gave 49 files and 917
tests passed. The suites of Integrations, Organization and Chat import `app/email/lib`, and they
gave 8 files and 92 tests passed.

**Review fix round 1 (2026-10-03).** An independent verifier checked `5d82548a`. It found no P0
and no P1. This round fixes its four P2 findings. The branch still sits on `7b77980d`.

| Finding | Fix | Mutants | The test that goes red |
|---|---|---|---|
| F1: an answer for A that lands after a pick of B shows under "for B". A toggle there then changes A. | `key={accountId}` on the view body. A `guardedLoad` in the loads of `RulesTab`, `DashboardView` and `BulkUnsubscribeView`. | R1 to R8 | `an answer for A that lands after a pick of B is never shown`, `the view body has the key of the mailbox`, `each load of the three views goes through guardedLoad` |
| F2: no fence on the pick in Notes | `FollowupFromField` holds no hook. `followupSendRequest` makes the body of Send. | N4 to N7 | `a pick in the From field changes the account_id that Send posts` |
| F3: no fence on the hold of the dialog | `holdNames` decides the names. | D12, D13 | `the names hold while busy, when the list changes under the dialog` |
| F4: no fence on the end of a copy | `runRuleCopy` gives the answer, the failure and the read of the rules to the step. | C12 to C17 | the four tests of `the end of a copy (review F4)` |

- **The key has a cost.** A pick resets the tab of AI Settings to Rules, and it closes a dialog that
  is open in the view. The state of the views belongs to one mailbox, so the remount loses nothing
  else.
- **The key alone covers two loads.** `loadPatterns` of `RulesTab` and `loadMore` of the Email
  Cleaner have no guard of their own.
- **The fences.** The tests call each hook-free component as a function, so the handlers on its
  elements are the real ones. The links from a container to its own state stay source scans,
  because vitest here has no DOM.
- **Found, and not fixed: Integrations.** `integrations/page.tsx:1046-1057` disconnects through a
  native `confirm("Remove this email account?")`. It names no mailbox and no new default. This is
  edge case 18 on a second surface. The tab reads its own shape of an account, with no
  `isDefault`, `displayLabel` or `createdAt`, and `emailRemove.test.ts` pins its `handleDelete`. The
  fix is more than 30 lines, so it needs a slice of its own.
- **Mutation run.** The run killed 53 of 53 mutants: the 33 of the build and 20 new ones. D11 has
  a new anchor, because the hold moved into `holdNames`. N4, D12, C12 and C14 match the four
  survivors that the verifier named, and the fences now kill all four. The script restored each
  file and checked its hash.
- **Verification.** In `workbench/control_plane`, `npx tsc --noEmit` passed. `npx vitest run` over
  `src/app/email`, `src/app/notes`, `src/components`, `src/lib/theme`, `src/app/integrations`,
  `src/app/settings/organization` and `src/app/chat` gave 57 files and 1020 tests passed.

**Scope.** `components/automation/AutomationView.tsx`, `page.tsx`, `OnboardingRulesStep.tsx`,
`lib/onboarding.ts`, `DisconnectDialog.tsx`, `lib/connect.ts`, `lib/emailStore.ts`, `lib/api.ts`,
`lib/types.ts`, a new `lib/mailboxSettings.ts`, and
`src/app/notes/components/FollowupEmailModal.tsx`.

1. **The AI settings header (item 1, MB-11)** names the mailbox as label and address. It has a
   picker on desktop and on mobile. The picker has no All inboxes option. A pick calls
   `selectAccount`, because `RulesTab` reads the folders of the selected mailbox.
2. **The copy step (item 2).** The rules step of a new mailbox offers "Copy the rules of
   <label>" for each other mailbox of the member. The step names its own mailbox when the member
   has two or more.
3. **The disconnect dialog (item 3, edge case 18).** One pure function, `nextDefaultAfter`,
   orders by `createdAt`, then `id`, as `transport/accounts.py:612-628` does. It compares the ISO
   text, so microseconds count. The dialog shows the label and the address. When the removed
   mailbox is the default, it names the new default. The store mirror uses the same function.
4. **Notes (edge case 23).** The From picker of Notes shows "label · address".

**Fences (R7).** `src/app/email/lib/mailboxSettings.test.ts`:
- `email-settings-header-names-mailbox`: each automation header names the label and the
  address. The picker has no All option, and a pick calls `selectAccount`.
- `email-rules-step-copy`: one copy for each other mailbox, and none with one mailbox.
- `email-disconnect-names-default`: `nextDefaultAfter` orders by `createdAt`, then `id`. The test
  reads the ORDER BY in `transport/accounts.py` and fails when the two differ.
- `email-notes-from-label`: the Notes options show "label · address".

##### EM-T8f-3 — the views of each mailbox and the chat picker

**Status.** ✅ MERGED #602 (2026-10-03), with review fix round 1. No migration and no backend
change.

**As built (2026-10-03).**

- **Summed counts (item 1).** In All inboxes the store reads the folders of each mailbox. It sums
  the provider `message_count` of the six well-known folders. `sumFolderCounts` and
  `allInboxesFolders` in `lib/emailStore.ts` hold the rule. A failed read adds 0. A custom folder
  shows no count. Before the sums land, no folder shows a count, so the count of one mailbox never
  reads as the sum. The sums go when the member leaves All inboxes and when a mailbox leaves.
- **The store bounds the reads.** At most 4 reads run at one time (`FOLDER_SUM_CONCURRENCY`),
  because Q-MB-1 sets no limit on the mailboxes. The store runs one round of reads at a time. A
  request while a round is out asks for one more round. Each read gives up after 15 s
  (`FOLDER_SUM_TIMEOUT_MS`) and adds 0. Nothing awaits the reads, and the list never waits on
  them.
- **When the store reads the sums.** It reads them when All inboxes opens, and when a mailbox
  leaves. A Refresh in All inboxes reads them once, 6 s after its last sync ends
  (`FOLDER_SUMS_AFTER_SYNC_MS`). A second Refresh moves that read. A sync alone reads no sums.
  `folders` stays the tree of one mailbox.
- **Import panels (item 2).** `firstSyncPanels` in `lib/onboarding.ts` gives one panel for each
  mailbox whose first sync runs. The mailbox in view comes first. With two or more mailboxes, each
  panel draws the chip of its mailbox beside the address. With one mailbox the panel does not
  change (§11.0).
- **The chat picker (item 3).** `chatMailboxOptions` gives each mailbox option the dot of
  `mailboxAccent()`, as a `bg-cat-*` class. All inboxes has none. `AgentChat` takes the dot as an
  optional `accent` on each option, and draws it beside the label. The trigger shows the dot of
  the mailbox in force. `/chat` sends no `accent`, so its picker draws no dot.
- **Two changes reach `/chat` on purpose.** The mark of the option in force is `text-primary`, not
  `text-emerald-400`, because one look has one token for "on". The label of each option sits in a
  flex span, beside the place of the dot.
- **The removed-mailbox note (item 4).** `rememberChatScope` holds the scope of the render before.
  When the mailbox of that scope leaves, it gives one note. The note names that mailbox and the
  new scope. `AgentChat` takes the note as an optional `notice` prop, and draws it above the
  composer with a dismiss button. A pick clears the note. All inboxes that ends because one
  mailbox is left gives no note.

**Narrowed.**

- One mailbox gets no dot in the chat picker, because the chips show only for two or more
  mailboxes (§11.0).
- All, Starred and Snoozed show no count in All inboxes. Item 1 names six folders. The All count
  of one mailbox also counts its custom folders, so a sum of it would be wrong.
- A failed read of the sums does not show the reconnect banner. `fetchFolders` marks a 401 for the
  mailbox in view, and the read of the sums does not.

**Outside the stated scope.**

- `lib/onboarding.ts` holds `firstSyncPanels`, beside `firstSyncSurface`.
- `src/lib/theme/conformance.test.ts` lowers the `PALETTE_DEBT` of `AgentChat.tsx` from 14 to 13.
  The ratchet fails a file that got better until its number goes down.
- Three older fences changed shape. `connect.test.ts`, `onboarding.test.ts` and
  `onboardingRules.test.ts` read `firstSyncPanels` now, not one `pendingAccount`.
- `mailboxAccent` moved from `components/MailboxChip.tsx` into `lib/mailbox.ts` (review F8).
  `MailboxChip.tsx` re-exports it, so its callers did not change.

**Fences.**

- `allInboxes.test.ts` names `email-all-folder-sums` and `email-import-panel-each`.
- `allInboxesStore.test.ts` holds the store half of `email-all-folder-sums`.
- `chatScope.test.ts` names `email-chat-picker-dot` and `email-chat-removed-note`. It also holds
  the scan that keeps `lib/` free of imports from `components/`.

**Mutation runs.**

- The build: the first run killed 34 of 35 mutants. A new case kills the one that survived, a move
  away from a mailbox that stays connected. The second run killed 3 of 3.
- Review round 1: the run killed 17 of 17.

**Review round 1 (2026-10-03).** The verifier passed the build with no P0 and no P1. This round
fixes its eight P2 findings.

- **F1.** Three reads of the sums had no fence: the first load into All inboxes, a removal in
  another tab, and the read after a sync. Each one now has a store test. The fallback when the
  selected mailbox is gone has one too.
- **F2.** A Refresh read O(N²) folders, because each catch-up of each sync asked for a round. A
  probe gave 216 live folder reads for one Refresh with 12 mailboxes. Now the catch-up asks for
  none, and `syncScope` asks once. A Refresh of 8 mailboxes whose syncs end 3 s apart reads 1
  round. The fence allows at most 2.
- **F3.** The dot in the trigger and the note of `AgentChat` had no fence. `AgentChat` renders to
  markup in node, so the tests read what it draws. The dismiss handler does not show in markup,
  so a source scan holds it.
- **F4.** The sums were never cleared, so an old sum could show for up to 120 seconds after a return.
  They now go when the member leaves All inboxes and when a mailbox leaves.
- **F5.** One hung read held every later request of the sums for 120 seconds. Each read now gives up
  after 15 s, and a fake-timer test proves it.
- **F6.** The paragraph on the fences had too many sentences, so it is now a list.
- **F7.** The note on `/chat` said that its picker does not change. Two changes reach it on
  purpose, and the list above names them.
- **F8.** `lib/chatScope.ts` imported from a component. `mailboxAccent` now lives in
  `lib/mailbox.ts`.

**Found, and not fixed in this round.** In All inboxes each catch-up still reads the tree of the
hidden selected mailbox, so a Refresh of N mailboxes reads that tree 2N times. A sync of one
mailbox from the reading pane does not read the sums.

**Not checked.** This session had no browser. Nobody looked at the sidebar, the panels or the chat
picker in light mode, at compact density or under a changed accent.

**Scope.** `components/AccountSidebar.tsx`, `lib/emailStore.ts`, `page.tsx`,
`OnboardingPanel.tsx`, `FirstSyncBanner.tsx`, `src/components/AgentChat.tsx`,
`EmailAssistantChat.tsx` and `lib/chatScope.ts`. It needs no backend change.

1. **Summed counts (item 5).** All inboxes sums the provider `message_count` of Inbox, Drafts,
   Sent, Archive, Junk and Deleted over each mailbox. A mailbox whose read fails adds nothing.
   A custom folder shows no count.
2. **Import panels (item 5, edge case 19).** One panel for each importing mailbox, each one
   named.
3. **The chat picker (item 6).** Each mailbox option carries the dot of `mailboxAccent()`, never
   a hex value or a raw palette class. All inboxes carries none. The prop stays optional, because
   `src/app/chat/page.tsx` also draws the picker.
4. **The removed-mailbox note (item 6, edge case 17).** When the scope mailbox leaves, the chat
   shows one note that names it and the new scope.

**Fences (R7).** In `allInboxes.test.ts` and `chatScope.test.ts`:
- `email-all-folder-sums`: each well-known folder shows the sum, and a failed read adds 0.
- `email-import-panel-each`: one panel for each importing mailbox, each named.
- `email-chat-picker-dot`: each mailbox option carries `mailboxAccent()`, and All inboxes none.
  The picker in `AgentChat.tsx` uses no raw palette class.
- `email-chat-removed-note`: one note that names the removed mailbox and the new scope.

**Verification for T8f-2 and T8f-3.** In `workbench/control_plane`, run this command:

```
npx tsc --noEmit && npx vitest run src/app/email src/app/notes src/components src/lib/theme
```

Then look at the header, the dialog, the sidebar and the chat picker. Look in light mode, at
compact density and under a changed accent (CLAUDE.md §4).

#### 11.7.7 EM-T8g — duplicates and separation

**Status.** 📝 Narrowed 2026-10-03, verified against the code at 30eebe6c. Three pull requests,
T8g-1 to T8g-3. T8g-1 adds one migration. Item 3, the forward loop guard, is deferred.
✅ T8g-1 MERGED #608 (2026-10-04, migration 229). ✅ T8g-2 MERGED #610 (2026-10-04), with review fix rounds 1 and 2.
✅ T8g-3 MERGED #611 (2026-10-04), with review fix round 1. It adds no migration.

**Order.** T8g-1 merges first. T8g-3 follows it, because both edit `transport/messages.py`,
`transport/search.py` and `core.py`. T8g-2 follows T8g-1 and EM-T8f-2, because it edits the same
store and page files.

**Not in scope.**
- **The forward loop guard (item 3).** A FORWARD action makes a provider draft and never sends
  (`automation/actions.py:532-563`). So a loop needs a send by the member at each hop. The guard
  ships with the first rule action that sends with no review. D-EM-29 stays until then.
- **`internet_message_id` for Gmail and IMAP.** Only `providers/outlook.py:1674` sets it. D-EM-5
  keeps Outlook the only provider in the connect flow. A fill also turns on the re-key reclaim of
  `persist.py:232-246` for Gmail and IMAP. That needs its own slice and its own R8 test.
  **2026-10-04:** D-EM-5 changed. EM-G1 gates the reclaim, then EM-G2 fills the Message-ID for Gmail (§12).
- **`/analytics/overview`.** It reads each mailbox with no `account_id`, but no caller sends it so.
- **The contacts reads and the CRM timeline.** D-EM-28 does not name them.
- **OWNER-GATE.** The build has none. The production migration is agent-safe under the `deploy`
  grant until 2026-11-30. Confirm the pre-migration backup, then report the ledger line. No
  production member has two mailboxes (§11.1), so R8 and vitest are the acceptance. Do not
  connect a real mailbox or send real mail to test it.

##### EM-T8g-1 — "Keep separate", the server half (migration, gateway, agent, R8)

**Status.** ✅ MERGED #608 (2026-10-04). Migration 229. Review fix round 1 (2026-10-04) is below
the mutation table of the build.

**As-built notes.**
- **The number (R1).** The build found 229 free. 227 is the last migration on main, and only the
  parked branch `ws43t2-sessions` holds 228. No open pull request adds a migration. Check it again
  at merge.
- **The migration.** `229_email_keep_separate.sql` adds `in_all_inboxes BOOLEAN NOT NULL DEFAULT
  true` to `email_accounts`. It adds no table, so `infra/postgres/generated/` does not change.
- **One scope helper.** `core._owned_accounts_sql` gives the ids of the mailboxes of `:uid`, and
  `core._account_scope` wraps it. Both take the keyword-only flag `pooled_only`. With the flag and
  no `account_id`, the subquery adds `AND in_all_inboxes`. A named mailbox is never left out. With
  no flag, the text does not change.
- **The list and the facets.** `transport/messages._mailbox_clause` holds one rule for both. A
  named mailbox gives its rows. A thread load with no `account_id` keeps the owner scope only. Any
  other read adds `ea.in_all_inboxes`.
- **`/senders`.** The mail, the dispositions that keep archived mail in the list, and the status
  of each sender read only the mailboxes in All inboxes. So a disposition of a separate mailbox
  never shows in All inboxes. The three "never list the member" subqueries keep each mailbox,
  because a separate mailbox is still the member (D-EM-27).
- **The API.** Each account read returns `in_all_inboxes`, and the create reads it from
  `RETURNING`. `AccountUpdateModel.in_all_inboxes` is `StrictBool`, so `"yes"`, `"true"`, `0` and
  `1` answer 422. The `PATCH` does not restart the sync loop.
- **The chat binding (as review round 1 left it).** `agents._pooled` gives the mailboxes in All
  inboxes. A row with no `in_all_inboxes` is in All inboxes. Only a member with one mailbox in
  total binds with no question. `agents._choices` shortens the list of a question to the mailboxes
  in All inboxes when two or more are there. A tool that names a separate mailbox still acts in
  it.
- **Narrowed: the `sent-from` bind is strict.** A `sent-from` answer binds only a mailbox in All
  inboxes, and only when two or more are there. When each mailbox is separate, the question lists
  them all, and no answer binds. Item 5 does not say which rule wins in that case, so the build
  uses the rule that asks.
- **Not changed.** `_unread_counts` counts each mailbox, because the switcher shows the count of a
  separate mailbox too. `identity.py`, `/contacts/sent-from`, the contacts reads and the CRM
  timeline do not change.
- **Found, not fixed.** `read_thread` with a bare `thread_id` is a thread load. When a separate
  mailbox holds the same thread id as a mailbox in All inboxes, the answer names both mailboxes.
  Item 4 keeps the owner scope for a thread load, so this slice does not change it.
- **Verification (2026-10-04, on the final tree).** Only `TENANT_LADDER_DATABASE_URL` was set, on a private
  database. The block below gave 514 passed and 2 skipped. The two skips are the WS-29 gates of
  `test_tenant_coverage.py`, which read `DATABASE_URL`. With `DATABASE_URL` set to the local
  scratch database, both gates fail. One reads a ladder with no FORCE RLS phase, and the other
  connects as a superuser. The two tests, the generator and `generated/` are the same as on main,
  so the base tree fails the same way. Each R8 case of `test_email_keep_separate.py` passed, and
  none skipped. All 129 email suites with `-k "not calendar"` gave 2329 passed. The ruff gate
  passed, and full ruff on the changed files shows no new finding.
- **Mutation check.** 27 of 27 mutants went red. The first run left `g_patch_restarts_sync` alive,
  because the test cleared the log of restarts before the assert. The fixed test kills it. The
  script restored each file and checked its hash. One more mutant cannot fail: the create can
  drop the field, and the model default and the column default are both true.

| Mutant | What the mutant breaks | The test that goes red |
|---|---|---|
| `g_core_no_pool` | the `in_all_inboxes` term of `_owned_accounts_sql` | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `g_core_pool_named` | a named mailbox is left out when it is separate | `test_its_own_account_id_still_reads_it` |
| `g_core_default_text` | the text of `_account_scope` with no flag | `test_with_no_flag_the_text_does_not_change` |
| `g_msg_no_pool` | the clause of the list and the facets | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `g_msg_thread_pooled` | a thread load leaves out a separate mailbox | `test_a_thread_load_a_read_by_id_and_a_bulk_act_reach_it` |
| `g_facets_skip` | the facets do not leave it out | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `g_search_no_pool` | search does not leave it out | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `g_senders_scope` | the mail of `/senders` | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `g_senders_disposition` | the dispositions that keep archived mail | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `g_senders_status` | the status of each sender | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `g_senders_self_pooled` | "never list the member" leaves out a separate mailbox | `test_a_separate_mailbox_is_still_self` |
| `g_bulk_pooled` | the bulk act leaves out a separate mailbox | `test_a_thread_load_a_read_by_id_and_a_bulk_act_reach_it` |
| `g_patch_no_write` | the `PATCH` does not write the field | `test_each_account_read_returns_the_field` |
| `g_patch_restarts_sync` | the `PATCH` restarts the sync loop | `test_each_account_read_returns_the_field` |
| `g_model_not_strict` | `"yes"` turns into a boolean | `TestTheUpdateModel` |
| `g_list_field` | the list does not return the field | `test_each_account_read_returns_the_field` |
| `g_default_field` | the default does not return the field | `test_each_account_read_returns_the_field` |
| `g_update_field` | the `PATCH` does not return the field | `test_each_account_read_returns_the_field` |
| `g_patch_owner` | the owner predicate of the `PATCH` | `test_a_patch_of_the_mailbox_of_another_member_is_404` |
| `i_self_pooled` | the self set leaves out a separate mailbox | `test_a_separate_mailbox_is_still_self` |
| `m_default_false` | the column default is false | `TestTheColumn` |
| `m_nullable` | the column is nullable | `TestTheColumn` |
| `a_one_no_pool` | `_one_mailbox` lists a separate mailbox | `test_the_rule_question_leaves_out_a_separate_mailbox` |
| `a_new_no_pool` | `_new_mail_mailbox` lists a separate mailbox | `test_a_sent_from_answer_that_names_a_separate_mailbox_is_no_answer` |
| `a_sent_from_lenient` | `sent-from` binds a separate mailbox | `test_with_no_pooled_mailbox_sent_from_still_binds_no_separate_one` |
| `a_no_fallback` | no list when each mailbox is separate | `test_with_no_pooled_mailbox_the_full_list_stays` |
| `a_missing_is_separate` | a row with no field counts as separate | `test_a_row_with_no_field_is_in_all_inboxes` |

Review round 1 replaced the agent code of the five `a_` rows. Its own table below holds the agent
mutants that apply now.

**Review fix round 1 (2026-10-04).** An adversarial reviewer and an independent verifier checked
73a21890. They found no P0. The SQL scoping, the `PATCH` and migration 229 were clean. The agent
rebased the branch on origin/main `0711c8b3` (#606, #607 and #598) with no conflict.
`work_plan.md` is the same as on main.

- **P1: the binding of the chat failed open.** Take a member with Work in All inboxes and a
  separate NDA mailbox. That member has no All inboxes (D-EM-30), so the chat is always in the
  scope of one mailbox. In the
  scope of NDA, the model sends no `account_id`. `_pooled` then gave Work only, and each item 3
  tool bound Work with no question. `save_knowledge` wrote an NDA fact into Work. The reset card
  named no mailbox, and then the reset deleted the rules of Work.
- **The fix.** A tool that gets no `account_id` binds with no question only when the member has
  one mailbox in total. `agents._choices` only shortens the list of a question, to the mailboxes
  in All inboxes when two or more are there. `sent-from` binds a mailbox in All inboxes only when
  two or more are there. Otherwise new mail asks and reads no `sent-from`.
- **The mailbox in each answer.** The reset card names the mailbox in its title and its detail.
  An id of no mailbox of the member stops before the card with "Nothing changed.". Each answer of
  an item 3 tool names its mailbox as "label · address" (`agents._named`, which reads
  `_mailbox_name`). A tool that names a mailbox now reads the list for that name, and the list
  never chooses the mailbox.
- **P2: `list_accounts`.** It marks a separate mailbox "(separate)". Its unread mail is not in the
  total, and the answer says so. Its own line keeps its own count.
- **P2 (F7): a bulk act by filter.** With no `account_id`, a bulk act by `sender_email`,
  `folder`, `older_than_days` or `only_read` leaves out a separate mailbox. A bulk act by ids
  keeps the owner scope. Item 4 says so.
- **P2: five fence gaps.** F1 fences the unread count of each mailbox. F2 fences
  `drafting._reply_target` for a mail of a separate mailbox. F3 fences the folder clause of
  `/senders` with `include_archived`. F4 fences the label chips of a sender in both mailboxes. F5
  fences a thread load with an `account_id`, which stays in that mailbox (D-EM-22).
- **Changed fences.** `test_one_pooled_mailbox_binds_and_never_the_separate_one` is now
  `test_one_pooled_mailbox_and_a_separate_one_asks`. The new mail test
  `test_new_mail_with_one_pooled_mailbox_sends_from_it` is now
  `test_new_mail_with_one_pooled_mailbox_and_a_separate_one_asks`.
  `test_a_named_mailbox_is_used_with_no_question` no longer refuses a read of the list, because
  the answer names the mailbox.
- **Verification (2026-10-04).** Only `TENANT_LADDER_DATABASE_URL` was set, on a private
  database. The block below gave 528 passed and 2 skipped, the same two WS-29 gates. Each of 21
  R8 cases of `test_email_keep_separate.py` passed, and none skipped. All 129 email suites with
  `-k "not calendar"` gave 2343 passed. The ruff gate passed, and full ruff on the changed files
  shows no new finding.
- **Mutation check.** 41 of 41 mutants went red. 20 are in the table below. The other 21 are the
  gateway and migration mutants of the build, run against the changed tests. The script restored each source
  file and checked its hash.

| Mutant | What the mutant breaks | The test that goes red |
|---|---|---|
| `r1_one_binds_pooled` | `_one_mailbox` binds the one pooled mailbox (the P1 probe) | `test_one_pooled_mailbox_and_a_separate_one_asks` |
| `r1_new_binds_pooled` | new mail binds the one pooled mailbox | `test_new_mail_with_one_pooled_mailbox_and_a_separate_one_asks` |
| `r1_sent_from_one_pooled` | `sent-from` binds with one pooled mailbox | `test_new_mail_with_one_pooled_mailbox_and_a_separate_one_asks[pooled]` |
| `r1_sent_from_any` | `sent-from` binds a separate mailbox | `test_a_sent_from_answer_that_names_a_separate_mailbox_is_no_answer` |
| `r1_choices_shorten_to_one` | the question lists one pooled mailbox only | `test_one_pooled_mailbox_and_a_separate_one_asks` |
| `r1_results_unnamed` | no answer names its mailbox | `test_each_write_result_names_the_mailbox` |
| `r1_save_knowledge_unnamed` | `save_knowledge` names no mailbox | `test_each_write_result_names_the_mailbox[save_knowledge]` |
| `r1_reset_title` | the reset card title names no mailbox | `test_the_reset_card_names_the_mailbox` |
| `r1_reset_detail` | the reset card detail names no mailbox | `test_the_reset_card_names_the_mailbox` |
| `r1_reset_unknown_id` | a reset of an id of no mailbox shows its card | `test_a_reset_of_an_id_of_no_mailbox_changes_nothing` |
| `r1_list_total` | the total counts a separate mailbox | `test_list_accounts_marks_a_separate_mailbox_and_leaves_it_out_of_the_total` |
| `r1_list_mark` | a separate mailbox has no mark | `test_list_accounts_marks_a_separate_mailbox_and_leaves_it_out_of_the_total` |
| `r1_missing_is_separate` | a row with no field counts as separate | `test_a_row_with_no_field_is_in_all_inboxes` |
| `f1_unread_pooled` | the unread count of a separate mailbox is 0 | `test_each_account_read_returns_the_field` |
| `f2_reply_target_pooled` | compose-assist loses a mail of a separate mailbox | `test_a_separate_mailbox_is_still_self` |
| `f3_nl_sub_unpooled` | the folder clause of `/senders` reads every disposition | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `f4_tally_unpooled` | the label chips read the separate mailbox | `test_all_inboxes_leaves_out_a_separate_mailbox` |
| `f5_thread_first` | a thread load with an `account_id` reads each mailbox | `test_a_thread_load_a_read_by_id_and_a_bulk_act_reach_it` |
| `f7_bulk_filter_unpooled` | a bulk act by filter reaches a separate mailbox | `test_a_bulk_act_by_filter_leaves_out_a_separate_mailbox` |
| `f7_bulk_ids_pooled` | a bulk act by ids leaves out a separate mailbox | `test_a_thread_load_a_read_by_id_and_a_bulk_act_reach_it` |

**Gate.** 🟢 AGENT-SAFE · R8 · security review, because it changes `core._account_scope` (D-EM-4).

**Scope.** A new migration, `transport/accounts.py`, `core.py`, `transport/messages.py`,
`transport/search.py`, `automation/senders.py` (`list_senders` only),
`apps/agents/agent-email-assistant/agents.py` and tests. No UI file.

1. **The migration.** R1 applies: take the next free number at build time, and check it again at
   merge. The audit found 229 free, because a parked branch holds 228. The migration is
   `ALTER TABLE email_accounts ADD COLUMN IF NOT EXISTS in_all_inboxes BOOLEAN NOT NULL DEFAULT
   true`. Its header gives the R6 reason: a constant default fills each row, and no code fills the
   column. It creates no table, so `infra/postgres/generated/` does not change.
2. **The API.** Each account read returns `in_all_inboxes`. `AccountUpdateModel` takes
   `in_all_inboxes: StrictBool | None`. The `PATCH` writes under the owner predicate and does not
   restart the sync loop.
3. **The reads of more than one mailbox.** With no `account_id`, these reads leave out a separate
   mailbox: `GET /email/messages` with no `thread_id`, the facets, `GET /email/search` and
   `GET /email/senders`. With the `account_id` of a separate mailbox, each read gets its rows.
   `core._account_scope` takes a keyword-only flag. With no flag, its text does not change.
4. **The reads that keep the owner scope only.** These still reach a separate mailbox: a thread
   load, each act by a mail id, and `POST /email/messages/bulk` by ids. The reason is that
   `manage_inbox` sends mail ids with no `account_id`. Compose-assist and `/contacts/sent-from` do
   not change. A bulk act by filter with no `account_id` leaves out a separate mailbox, as a read
   of All inboxes does (review round 1).
5. **The chat binding.** `_one_mailbox` and `_new_mail_mailbox` list only the mailboxes in All
   inboxes when two or more are there, else each mailbox. A `sent-from` answer that names a
   separate mailbox counts as no answer. Only a member with one mailbox in total binds with no
   question. Take one mailbox in All inboxes and one separate mailbox. A tool with no
   `account_id` then asks (review round 1).
6. **Self does not change** (D-EM-27, D-EM-30). `identity.py` keeps each mailbox of the member.

**Fences (R7).** `tests/unit/test_email_keep_separate.py` (R8, the app role):
- `email-keep-separate-column`: the column is NOT NULL with a default of true, and each old row
  reads true.
- `email-keep-separate-api`: each account read returns the field. A `PATCH` by another member
  answers 404 and writes nothing. `"yes"` answers 422.
- `email-keep-separate-reads`: with no `account_id`, the four reads of item 3 hold no row of a
  separate mailbox. With its `account_id`, each one holds its rows.
- `email-keep-separate-owner-scope`: a thread load, a read by id and a bulk act by ids reach a
  separate mailbox. A bulk act by filter does not (review round 1).
- `email-keep-separate-self`: a mail from a separate mailbox is still `self` in another mailbox.
- `email-chat-binding-skips-separate` (`test_email_chat_binding.py`, fakes): the question and the
  `sent-from` bind leave out a separate mailbox. One pooled mailbox and a separate one ask.
- `email-chat-write-names-mailbox` and `email-chat-list-accounts-separate` (review round 1): each
  answer of an item 3 tool and the reset card name the mailbox. `list_accounts` marks a separate
  mailbox and leaves it out of the total.

**Verification.**

```
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_keep_separate.py tests/unit/test_email_all_inboxes.py tests/unit/test_email_conversation_collapse.py tests/unit/test_email_facets.py tests/unit/test_email_search_scope.py tests/unit/test_email_bulk_apply.py tests/unit/test_email_mailbox_identity.py tests/unit/test_email_multi_account.py tests/unit/test_email_n_plus_one.py tests/unit/test_email_from_row.py tests/unit/test_email_ai_context.py tests/unit/test_email_chat_binding.py tests/unit/test_email_multi_inbox.py tests/unit/test_email_owner_scope_fence.py tests/unit/test_email_rule_copy.py tests/unit/test_crm_email_timeline.py tests/unit/test_email_contact_card.py tests/unit/test_tenant_coverage.py -v -rs
uv run ruff check apps/services/gateway/gateway/routes/email apps/agents/agent-email-assistant tests/unit/test_email_keep_separate.py --select F821,F601,F602,F502,F7,B006
```

The R8 cases must show PASSED, not SKIPPED. With `DATABASE_URL` set, the two WS-29 gates of
`test_tenant_coverage.py` also fail on the base tree, so compare those two with the base tree.

##### EM-T8g-2 — "Keep separate", the UI half (after T8g-1 and EM-T8f-2)

**Status.** ✅ MERGED #610 (2026-10-04), after EM-T8g-1 #608. No migration and no backend change.

**As-built notes.**

- **One rule.** `isSeparate` and `pooledMailboxes` in `lib/mailbox.ts` decide the pool.
  `hasAllInboxes` is true for two or more pooled mailboxes. A source scan of `src/` fails when a
  file other than `lib/api.ts`, `lib/mailbox.ts` or `lib/types.ts` names the flag (review F2).
- **The field.** `mapAccount` reads `in_all_inboxes`. Only an explicit `false` makes a mailbox
  separate, so a gateway before EM-T8g-1 keeps each mailbox in All inboxes.
- **The menu (item 1).** `separateToggle` gives "Keep separate" or "Show in All inboxes".
  `accountMenuItems` is the menu as a function with no hook, so a test runs each pick. The
  switcher row draws the word "Separate" in the house `Badge`, after the label of the mailbox.
- **The pool (item 2).** The All inboxes row, its unread sum, the header count, `scopeBusy`,
  `syncScope`, the folder sums and `pickInitialView` read `pooledMailboxes`.
- **The toggle (item 3).** `setInAllInboxes` in the store waits for the `PATCH`, then moves only
  the flag, because the answer holds no default flag. In All inboxes, the rows, the checks and the
  open mail of a mailbox kept separate leave at once. Then the store reads the list and the sums
  again. Fewer than two pooled mailboxes end All inboxes for the default mailbox.
- **Another tab.** `fetchAccounts` and `refreshAccounts` call `applyPoolChange`, the one
  reconciliation of the pool (review F7). A mailbox that left the pool leaves as a removed
  mailbox does. A mailbox that joined the pool makes the store read the list and the sums again.
- **Open in inbox (item 4).** `mailboxToOpen` in `lib/mailbox.ts` holds the rule of MB-3 and of
  item 4. In All inboxes, a mail of a separate mailbox opens in that mailbox.
- **The chat (item 5).** `chatMailboxOptions` offers All inboxes for two or more pooled
  mailboxes, and a separate mailbox stays an option. The All inboxes persona lists only the pooled
  mailboxes. `chatScope` falls back through `pickInitialView`.
- **No change (item 6).** The chips, the From row, "In <chip>", the import panels and the
  reconnect banner still count each mailbox. `attentionMailbox` in `lib/mailbox.ts` names the
  mailbox of the reconnect banner (review F6). New mail in All inboxes starts on the default
  mailbox, also when the default is separate.

**Narrowed.**

- With one mailbox, the menu offers neither label and the row shows no word (§11.0).
- A Refresh in All inboxes does not sync a separate mailbox. Its own view and its own loop sync it.
- Each end of All inboxes goes to the default mailbox: a toggle, a disconnect and a re-read. Before
  review round 1, a re-read stayed on the selected mailbox.
- Nobody did the look check of CLAUDE.md §4: light mode, compact density and a changed accent. The
  agent had no browser, so a reviewer must do it.

**Outside the stated scope.**

- `lib/mailbox.test.ts`: two cases of `email-open-by-id-switches` read the inline condition that
  moved into `mailboxToOpen`. They now test the function and scan for its call.
- `allInboxes.test.ts`: two EM-T8d cases, the header count and "keeps an opened mail", read the
  new code.
- `workbench/AGENTS.md`: one line for this slice.

**Fences.**

- `email-all-skips-separate`: `allInboxes.test.ts`, and its store half in `allInboxesStore.test.ts`.
- `email-separate-menu`: `allInboxes.test.ts`.
- `email-separate-leaves-at-once`: `allInboxesStore.test.ts`, and its pure half in
  `allInboxes.test.ts`.
- `email-chat-separate`: `chatScope.test.ts`.

**Mutation run.**

- The final run killed 46 of 46 mutants. They are 14 for `email-all-skips-separate`, 11 for
  `email-separate-menu`, 16 for `email-separate-leaves-at-once` and 5 for `email-chat-separate`.
- The script restored each file and checked its hash.
- The first run also killed 46 of 46. A failed store case then held a folder read open, so the
  later cases failed too. An `afterEach` now settles that read, and each kill names its own case.

**Verification (2026-10-04).** In `workbench/control_plane`, `npx tsc --noEmit` passed.
`npx vitest run src/app/email src/components src/lib/theme src/app/notes src/app/chat` gave 49
files and 961 tests passed. The suites of Integrations and Organization gave 8 files and 92 tests
passed.

**Review fix round 1 (2026-10-04).** An independent verifier failed `9c02baed2` on one P1. This
round fixes the P1 and the P2 findings. The contract with EM-T8g-1 did not change.

| Finding | Fix | The test that goes red |
|---|---|---|
| F1 (P1): a disconnect counted the mailboxes, so All inboxes stayed open with one pooled mailbox. | `applyPoolChange` in the store is the one reconciliation of the pool. It reads `hasAllInboxes`, and the hidden mailbox takes `poolHome`. | `F1: a disconnect that leaves one pooled mailbox ends All inboxes`, `F1: a disconnect of the hidden mailbox moves it to a pooled mailbox` |
| F2: the scan missed a destructure, a bracket read and the other apps. | The scan reads each source file of `src/` for the bare token. The store calls `setMailboxPooled` and `withPoolFlag`. | `is the one rule: no other file names the flag, in any app` |
| F3: a re-read from another tab kept the checks of a mailbox that left. | `applyPoolChange` drops the checks with the rows and the open mail. | `drops the rows, the checks and the open mail of a mailbox that another tab kept separate` |
| F4: a list read that started before a toggle could land after it. | `fetchEmails` takes a number, and only the newest read lands. `softRefresh` and `loadMoreEmails` drop an answer after a newer read or a change of the pool. | the five `F4:` cases |
| F5: the hidden mailbox of All inboxes could be separate. | `poolHome` gives the pooled default, else the first pooled mailbox. `pickInitialView`, `selectAll` and the reconciliation use it. | the two `F5:` cases, `keeps a pooled mailbox selected out of view in All inboxes` |
| F6: four rules had no fence. | `attentionMailbox` names the mailbox of the reconnect banner. New cases cover the menu of a pair, the server answer and the re-read after a refusal. | `names a separate mailbox in the reconnect banner`, `offers the way back with one pooled and one separate mailbox`, `takes the answer of the server over the request`, `changes nothing on a refusal, says so, and reads the accounts again` |
| F7: a quiet re-read did not reconcile the pool. | `refreshAccounts` calls `applyPoolChange`. | the two `F7:` cases |

- **Who calls the reconciliation.** A toggle, a disconnect, a re-read and a quiet re-read call
  `applyPoolChange`. Fewer than two pooled mailboxes end All inboxes for the default mailbox.
- **F8, a note.** A toggle that ends All inboxes moves the page scope. A chat pick of the
  separated mailbox was made against All inboxes, so it drops with the page, and no note shows.
  That is the pick rule of EM-T8e-3. The mailbox is still connected, so §11.6 case 17 does not
  apply.
- **A new mailbox joins the pool.** The verifier's M4 made a mailbox that another tab connects
  count as joined. This round makes that the rule, so its rows and its sums show in All inboxes.
  The opposite mutant is now killed.
- **The F1 sweep.** Each other count of `accounts` in `src/app/email/` and
  `src/components/email/` serves the chips, the From row, the settings picker or an empty list
  (§11.0, item 6). None of them decides All inboxes.
- **Also changed.** `loadMoreEmails` drops its page after a newer read, because F4 applies to it
  too. A disconnect of a separate mailbox in All inboxes reads nothing again, because the pool
  does not change.
- **Known limit.** A quiet re-read that started before a toggle can land after it. It then shows
  the old flag until the next re-read. The list stays correct, because the server leaves the
  separate rows out.
- **The EM-T8f-2 fence stays.** `deleteAccount` keeps `nextDefaultAfter(get().accounts, id)`
  word for word, so the source scan of `email-disconnect-names-default` holds.

**Round 1 mutation run.** The run killed 33 of 33 mutants. The script restored each file and
checked its hash. V-M1 to V-M9 are the mutants of the verifier, moved to the new anchors. M6 of
the verifier, the import panels, is not in the list. The EM-T8f-3 case `draws each panel on the
page, keyed and named by its mailbox` holds its line.

| Mutant | What it breaks | The case that goes red first |
|---|---|---|
| V-M1 | a destructured read of the flag in `page.tsx` | `is the one rule: no other file names the flag, in any app` |
| V-M2 | a bracket read of the flag in `AccountSidebar.tsx` | the same scan |
| V-M3 | the request wins over the answer of the server | `takes the answer of the server over the request` |
| V-M4 | a new mailbox does not join the pool (the opposite of M4) | `reads the list and the sums again when another tab connects a mailbox` |
| V-M5a, V-M5b | the reconnect banner skips a separate mailbox, in the rule and on the page | `names a separate mailbox in the reconnect banner (item 6)` |
| V-M7, V-M8 | the menu and the word ask for two pooled mailboxes | `offers the way back with one pooled and one separate mailbox (review F6)` |
| V-M9 | a refusal does not read the accounts again | `changes nothing on a refusal, says so, and reads the accounts again` |
| F1-a | the end of All inboxes counts the mailboxes | `F1: a disconnect that leaves one pooled mailbox ends All inboxes` |
| F1-b | a disconnect skips the reconciliation | `re-reads All inboxes when another mailbox goes` (EM-T8d) |
| F1-c | the hidden mailbox takes the default, pooled or not | `F1: a disconnect of the hidden mailbox moves it to a pooled mailbox` |
| F3-a, F3-b, F3-c | the checks, the rows or the open mail of a mailbox that left stay | `drops the rows, the checks and the open mail of a mailbox that another tab kept separate` |
| F4-a | `fetchEmails` lands out of order | `F4: a list read that started before the toggle never lands after it` |
| F4-b | `softRefresh` misses a change of the pool | `F4: a background read that started under another pool drops its answer` |
| F4-c | `softRefresh` misses a newer read | `F4: a background read drops its answer when a newer read started` |
| F4-d | `loadMoreEmails` adds to a list that was read again | `F4: a page of older mail drops when the toggle read the list again` |
| F4-e | a stale read that fails sets the error | `F4: a stale read that fails leaves the error and the list to the newer read` |
| F5-a | `pickInitialView` keeps a separate default | `keeps a pooled mailbox selected out of view in All inboxes (review F5)` |
| F5-b, F5-e | `selectAll` keeps a separate hidden mailbox, or reads no labels | `F5: All inboxes, opened from a separate mailbox, keeps a pooled one` |
| F5-c, F5-d | the reconciliation keeps a separate hidden mailbox, or reads no labels | `F5: a toggle of the hidden mailbox moves it to the default pooled mailbox` |
| F5-f | `poolHome` skips the pooled default | `opens All inboxes for two or more mailboxes and no stored choice` (EM-T8d) |
| F7-a | a quiet re-read skips the reconciliation | `F7: a quiet re-read with one pooled mailbox ends All inboxes` |
| F7-b | a full re-read skips the reconciliation | `ends All inboxes when a re-read finds one mailbox` (EM-T8d) |
| R-a | the toggle skips the reconciliation | `ends All inboxes for the default mailbox when fewer than two are pooled` |
| R-b | the end of All inboxes stays on the hidden mailbox | the same case |
| R-c | a change of the pool reads no list | `puts a mailbox back: the list and the sums are read again with it` |
| R-d | a change of the pool keeps the old sums | `F4: a disconnect clears the sums before the new round lands` (EM-T8f-3) |
| F2-c | a read of the flag in `src/components/email/` | `is the one rule: no other file names the flag, in any app` |

The 46 mutants of the build ran again on the code of this round. 31 found their anchor, and the
run killed 31 of 31. The other 15 edited code that this round moved into `applyPoolChange`,
`poolHome` and `setMailboxPooled`, and the round mutants above cover it.

**Round 1 verification (2026-10-04).** In `workbench/control_plane`, `npx tsc --noEmit` passed.
`npx vitest run src/app/email src/components src/lib/theme src/app/notes src/app/chat
src/app/integrations src/app/settings/organization` gave 57 files and 1069 tests passed, with no
unhandled error.

**Review fix round 2 (2026-10-04).** The verifier checked `6deaed077` again. It confirmed F1, F4,
F5, F6, F7 and M4, and it found no regression in EM-T8d, EM-T8f-2 or EM-T8f-3. It failed F3: a
bulk delete could still reach a mail of a separate mailbox.

- **F3, the probe.** A member checks c-9 in All inboxes. A refresh in the background takes c-9 out
  of the list, and its check stays. The member keeps c separate and clicks Delete. Before this round,
  `deleteEmail` then got `["a-1", "c-9"]`.
- **F3, the fix of the class.** Three layers close it, and each one has its own fence.
  - (a) `applyPoolChange` keeps only the checks of the rows that stay in the list.
  - (b) `checkedRows` in `lib/emailStore.ts` gives the checked ids that are rows of the list on
    screen. `bulkUpdateSelected`, `bulkDeleteSelected`, each bulk act of `EmailList.tsx` and
    each "N selected" count read it. No reader of the raw `selectedIds` acts.
  - (c) `fetchEmails` and `softRefresh` prune the checks to the rows that they land, so a check
    never outlives its row.
- **P3, a removed selected mailbox.** In All inboxes, `fetchAccounts` now sends that case to
  `applyPoolChange`. With one mailbox in view, the branch clears the open mail and the checks.
  `replaceAccount` calls `applyPoolChange` too.
- **P3, the three survivors of round 1.** New cases kill each one. V-F5b holds the end of All
  inboxes on the default mailbox. V-F3c holds the open mail kept as an override. V-ALL0 holds the
  end with no mailbox left. With no mailbox left, the store now also clears the rows and the
  checks.
- **P3, the scan.** `stripComments` in `allInboxes.test.ts` keeps each string and drops only a
  real comment. A `//` in a string no longer hides a read after it. The case `finds a read after
  a // in a string, and skips a real comment` holds the stripper.
- **Outside the stated scope.** `components/EmailToolbar.tsx` and `components/EmailList.tsx`
  read `checkedRows`, because they hold the bulk acts.

**Round 2 mutation run.** The run killed 17 of 17 mutants. The script restored each file and
checked its hash. V-M1 to V-M3 are the three survivors of round 1 at the verifier.

| Mutant | What it breaks | The case that goes red first |
|---|---|---|
| R2-a | (a): a toggle drops only the checks of the rows that leave now | `F3 (a): a toggle keeps only the checks of the rows that stay` |
| R2-b1, R2-b2 | (b): a bulk delete or a bulk update reads the raw checks | `F3 (b): a bulk act with a stale check reaches only the rows on screen` |
| R2-b3 | (b): `checkedRows` gives the raw checks | `acts only on the checked rows of the list on screen` |
| R2-b4, R2-b5 | (b): the toolbar count, or a bulk label of the list, reads the raw checks | `reads checkedRows for each bulk act and each count, in the store and in both bars` |
| R2-c1 | (c): `fetchEmails` keeps the check of a row that left | `F3 (c): a list read that drops a checked row drops its check` |
| R2-c2 | (c): `softRefresh` keeps the check of a row that left | `F3 (c): a background refresh that drops a checked row drops its check` |
| R2-c3 | `prunedChecks` never prunes | `drops the rows, the checks and the open mail of a mailbox that another tab kept separate` |
| P3-a | the branch of round 1 for a removed selected mailbox | `P3: a re-read that removes the hidden mailbox and keeps c separate drops the checks` |
| P3-b | a removed mailbox in view keeps its checks and its open mail | `P3: a re-read that removes the mailbox in view drops its checks and its open mail` |
| P3-c | `replaceAccount` skips the reconciliation | `P3: replaceAccount reconciles the pool` |
| V-M1 | All inboxes ends on `poolHome`, not on the default | `V-F5b: a toggle that ends All inboxes goes to the default, also a separate one` |
| V-M2 | the check of the open mail ignores `selectedEmailOverride` | `V-F3c: the open mail of a mailbox that left goes, also when it is not a row` |
| V-M3, V-M3b | with no mailbox left, `viewAll` stays true, or the rows and the checks stay | `V-ALL0: with no mailbox left, All inboxes ends and nothing of the list stays` |
| S-a | a read of the flag after a `//` in a string, on one line | `is the one rule: no other file names the flag, in any app` |

The 33 mutants of round 1 ran again on the code of round 2. 30 found their anchor, and the run
killed 30 of 30. F3-a and F3-b edited the code that round 2 replaced, and R2-a and R2-c3 cover
it. F7-b found its line twice, so F7-b2 ran on its own with a unique anchor, and the run killed it.

**Round 2 verification (2026-10-04).** In `workbench/control_plane`, `npx tsc --noEmit` passed.
`npx vitest run src/app/email src/components src/lib/theme src/app/notes src/app/chat
src/app/integrations src/app/settings/organization` gave 57 files and 1083 tests passed, with no
unhandled error.

**Gate.** 🟢 AGENT-SAFE.

**Scope.** `components/AccountSidebar.tsx`, `lib/emailStore.ts`, `lib/mailbox.ts`,
`lib/chatScope.ts`, `lib/emailAssistantPersona.ts`, `lib/api.ts`, `lib/types.ts`, `page.tsx` and
tests. No backend file.

1. **The menu.** The mailbox menu offers "Keep separate", or "Show in All inboxes" for a separate
   mailbox. A pick sends the `PATCH`. The switcher row of a separate mailbox shows the word
   "Separate" beside its chip.
2. **One helper.** `pooledMailboxes(accounts)` in `lib/mailbox.ts` gives the mailboxes in All
   inboxes. The All inboxes row, its unread sum, the header count, the folder sums, `scopeBusy`,
   `syncScope` and `pickInitialView` read it.
3. **When All inboxes shows.** It shows for two or more pooled mailboxes. A toggle that leaves
   fewer ends All inboxes for the default mailbox, as a disconnect does. A toggle in All inboxes
   reads the list again, so the rows leave at once.
4. **Open in inbox.** A mail of a separate mailbox opens in that mailbox, never in All inboxes.
5. **The chat.** The All inboxes option needs two pooled mailboxes. The All inboxes persona lists
   only pooled mailboxes. The picker still offers a separate mailbox as its own scope.
6. **What does not change.** The chips, the From row and "In <chip>" count each mailbox (§11.0).
   New mail in All inboxes starts on the default mailbox (D-EM-20), and the From row names it.

**Fences (R7).**
- `email-all-skips-separate` (`allInboxes.test.ts`): items 2 and 3, with one separate mailbox
  among three.
- `email-separate-menu` (`allInboxes.test.ts`): the two labels, the `PATCH` and the word
  "Separate".
- `email-separate-leaves-at-once` (`allInboxesStore.test.ts`): the toggle, the end of the scope,
  and item 4.
- `email-chat-separate` (`chatScope.test.ts`): item 5.

**Verification.** In `workbench/control_plane`, run this command:

```
npx tsc --noEmit && npx vitest run src/app/email src/components src/lib/theme
```

Then look at the switcher and the menu. Look in light mode, at compact density and under a
changed accent.

**Follow-ups from the re-verify of EM-T8g-1 (2026-10-04).** All are P3, and none blocked #608.

- **The rule question in a separate chat.** A member with two pooled mailboxes and one separate
  mailbox opens the chat of the separate one. An item 3 tool with no `account_id` then asks, and
  the question lists only the two pooled mailboxes. It fails closed, because nothing binds. The
  better question lists each mailbox and marks the separate one "(separate)".
- **Two fence gaps.** The bulk scope `pooled_only=not req.message_ids` has a fence for the sender
  filter only, not for `folder` or `older_than_days`. Four answers of the item 3 tools have no
  fence for the mailbox name:
  - `run_rules` with scope "new"
  - the install without a reset, and "already installed"
  - the update path of `save_knowledge`
- **Doc slips.** The T8g-1 notes count 21 R8 cases, but 9 are R8 and 12 are hermetic. The T8g-1
  Scope line names `list_senders` only, but round 1 also changes `bulk_action`.

##### EM-T8g-3 — "Also in" and the draft dedupe (after T8g-1, R8)

**Status.** ✅ MERGED #611 (2026-10-04), with review fix round 1. It adds no migration and no
index. The notes of round 1 are below the mutation table of
the build, and the notes below say what round 1 changed.

**As-built notes.**

- **The pair set (item 4).** `identity.PAIRED_MAILBOX_IDS_SQL` is the self set of `:aid` without
  `:aid`, and both mailboxes are in All inboxes. `paired_mailbox_ids_sql(anchor)` gives the same
  rule over a column, for a page of mail. Any anchor that is not a bind name or a column raises.
  So do `a.` and `o.`, the aliases of the set itself (review round 1). The text of the self set
  over `:aid` did not change.
- **The copy.** A copy is a mail of a paired mailbox with the same `internet_message_id`, not
  empty, and the same sender address (review round 1). It is outside drafts, junk and trash.
  `core.NOT_A_COPY_FOLDERS` holds the three folders, and "Also in" and the dedupe both read it.
  A row in those three folders names no copy either (review round 1).
- **"Also in" (item 1).** `identity.also_in_by_message` reads `also_in` for one page in one
  statement, `ALSO_IN_SQL`. A LATERAL pair set takes the mailbox of each row. The read binds
  `:uid` too, so a mail id of another member gives no row. An empty page runs no read.
- **The lazy import.** The list and search reach the read through
  `messages._also_in_by_message`. The automation layer imports `transport.send` at load time, so
  a load-time import from `transport` would be a cycle.
- **The index (R6).** The agent measured the plans on 40,000 rows after ANALYZE. Migration 89's
  `idx_email_messages_internet_message_id` serves each lookup of a copy as an index condition on
  `(account_id, internet_message_id)`. That index is partial (`WHERE internet_message_id IS NOT
  NULL`), and the equality of the lookup lets the planner use it. `idx_email_messages_thread`
  (migration 17) serves the thread read of the dedupe. So the slice needs no migration.
- **The dedupe (items 3 and 5).** `actions._skip_for_paired_mailbox` runs in the REPLY and
  DRAFT_EMAIL branch, before the thread check and the model call (review round 1). It calls
  `identity.draft_skip_in_pair`, which runs two statements.
  - The first takes `pg_try_advisory_xact_lock` on a hash of four parts: the organization, the
    member, the sender address and the Message-ID. A lock that another run holds gives `busy`.
  - The second asks if the thread of a copy holds a draft or a sent mail, newer than the copy.
    A yes gives `answered`. Before round 1, any draft counted.
- **Why two statements.** READ COMMITTED takes a new snapshot for each statement. So the check
  after the lock sees each draft that a run committed before it released the lock.
- **No pair, no lock.** A mail with an empty Message-ID, a member with no paired mailbox and a
  separate mailbox take no lock. So a pooled run never holds off the draft of a separate mailbox
  (D-EM-30).
- **The log.** A skip logs `email.draft_skipped_other_mailbox` with `account_id` and `reason`, and
  no address. The read runs in a savepoint. A failed read logs `email.draft_dedupe_failed` and
  drafts as before, as the thread check does.
- **The callers.** The dedupe is in `_apply_rule_actions`, so the automatic run, the approval and
  Process past get it. The retry never reaches the branch, because `_RETRY_SKIPPED_ACTIONS` holds
  REPLY and DRAFT_EMAIL.
- **The row (item 2).** `alsoInMailboxes` and `AlsoInLine` in `components/EmailList.tsx` draw
  "Also in" and a `MailboxChip` for each mailbox, in the order of the switcher. A member with one
  mailbox sees none. The row shows it in each view, not only in All inboxes, because D-EM-22 says
  "each row".
- **The API.** `mapEmail` reads `also_in` into `Email.alsoIn`. A gateway before EM-T8g-3 sends no
  field, and the row then names no other mailbox.
- **The fence files.** The four server fences are in `tests/unit/test_email_duplicates.py`: 33
  R8 cases and 20 hermetic cases after round 1 (18 and 16 at the build). `email-also-in-row` is in
  `workbench/control_plane/src/app/email/lib/alsoIn.test.ts`: 9 cases.

**Narrowed.**

- At the build, only Outlook mailboxes paired, and this slice filled `internet_message_id` for no
  other provider. Since EM-G2 (#626) Gmail fills it too. EM-G9 proves a Gmail and Outlook pair
  (§12.3.11).
- The dedupe runs before the thread check (review round 1). So a run that skips trashes no draft
  of this mailbox. The build ran the thread check first, and a run that then met the lock left the
  pair with no draft.
- In a race, the run that meets the lock makes no draft. The run that holds the lock can then make
  none, for low confidence or for a 401 or a 429 from the provider. Then the mail has no draft.
  Item 5 asks for one draft at most, so that is in scope.
- A copy in trash does not stop a draft, also when its thread holds a draft. The member threw that
  copy away.

**Outside the stated scope.**

- `tests/unit/test_email_ai_context.py`: `test_a_rule_draft_copy_stores_the_mailbox_as_from` now
  patches `draft_skip_in_pair`, as it patches the thread check. A bare `AsyncMock` session warns
  about a coroutine that nothing awaits.
- `apps/services/gateway/AGENTS.md` and `workbench/AGENTS.md`: one line each for this slice.
- The §11.7 table row now names T8g-2 MERGED #610 too.

**Not checked.** This session had no browser. Nobody looked at the row in light mode, at compact
density or under a changed accent (CLAUDE.md §4). The security review is still due.

**Verification (2026-10-04, on the final tree).**

- Only `TENANT_LADDER_DATABASE_URL` was set, on a private database for each run.
- The block below gave 227 passed and 0 skipped. All 34 cases of `test_email_duplicates.py`
  passed, and none skipped.
- All 130 email suites with `-k "not calendar"` gave 2377 passed and 6 deselected.
- One earlier run on the same tree failed one case:
  `test_email_otp_token.py::test_an_expired_code_does_not_verify`. That case gives a code one
  second of margin before `expires > now()`. The suite then passed alone twice, and a full run
  passed. This slice changes no file on that path.
- The ruff gate passed. Full ruff on the changed files shows no new finding. The C901 finding of
  `_apply_rule_actions` was on the base tree, and the slice moves it from 32 to 33.
- In `workbench/control_plane`, `npx tsc --noEmit` passed. `npx vitest run src/app/email
  src/components src/lib/theme` gave 47 files and 961 tests passed.

**Mutation check.**

- The final run killed 33 of 33 mutants: 25 on the server and 8 in the UI. The script restored
  each file and checked its hash.
- A first run reported each server mutant as killed, but a bare `bash` found WSL, so no test ran.
  The script now needs a passing run with no mutant. It counts a kill only when pytest names a red
  case.
- A rerun with only the R8 cases found two gaps. The "Also in" test took its folders from the
  constant under test. Only a hermetic case fenced the order of the two statements.
- The test now names the three folders. `test_the_lock_comes_before_the_check_on_a_real_database`
  puts the other run in the gap between the two statements. Both mutants now go red on R8 cases.

| Mutant | What the mutant breaks | A case that goes red |
|---|---|---|
| `i_pair_this_separate` | a separate `:aid` pairs | `test_b_drafts_when_either_mailbox_is_separate[this]` |
| `i_pair_other_separate` | a separate mailbox pairs with `:aid` | `test_each_row_names_each_paired_mailbox_that_holds_a_copy` |
| `i_pair_self_included` | the pair set holds `:aid` | `test_each_row_names_each_paired_mailbox_that_holds_a_copy` |
| `i_org_predicate` | no organization predicate | `test_the_org_predicate_holds_where_rls_does_not_bind` |
| `i_anchor_any` | any anchor passes | `test_a_column_anchors_it_for_a_page_and_nothing_else_does` |
| `i_also_folder` | a copy in junk, drafts or trash counts | `test_each_row_names_each_paired_mailbox_that_holds_a_copy` |
| `i_also_empty_id` | an empty Message-ID pairs | `test_each_row_names_each_paired_mailbox_that_holds_a_copy` |
| `i_also_owner` | the read binds no `:uid` | `test_the_org_predicate_holds_where_rls_does_not_bind` |
| `c_junk_is_a_copy` | junk holds a copy | `test_a_copy_in_junk_is_no_copy` |
| `m_read_per_row` | the list reads once for each row | `test_one_page_makes_one_read` |
| `m_no_field` | the list sends no `also_in` | `test_each_row_names_each_paired_mailbox_that_holds_a_copy` |
| `s_no_field` | search sends no `also_in` | `test_each_row_names_each_paired_mailbox_that_holds_a_copy` |
| `d_no_lock` | the guard takes no lock | `test_two_overlapping_runs_make_one_draft` |
| `d_session_lock` | the lock outlives the transaction | `test_two_overlapping_runs_make_one_draft` |
| `d_busy_drafts` | a held lock drafts | `test_two_overlapping_runs_make_one_draft` |
| `d_check_before_lock` | the check runs before the lock | `test_the_lock_comes_before_the_check_on_a_real_database` |
| `d_no_lock_pair` | a mail with no pair takes the lock | `test_a_separate_mailbox_is_never_held_off` |
| `d_no_draft_arm` | a draft in the thread of the copy stops nothing | `test_a_draft_in_the_thread_of_the_copy_stops_the_second_draft` |
| `d_no_sent_arm` | a sent reply stops nothing | `test_a_newer_sent_mail_in_the_thread_of_the_copy_stops_it` |
| `d_no_newer` | an older sent mail stops the draft | `test_an_older_sent_mail_does_not_stop_it` |
| `d_no_copy_folder` | a copy in junk stops the draft | `test_a_copy_in_junk_is_no_copy` |
| `d_no_pair_check` | any other mailbox stops the draft | `test_b_drafts_when_either_mailbox_is_separate[other]` |
| `a_no_call` | the rule path skips the dedupe | `test_a_draft_in_the_thread_of_the_copy_stops_the_second_draft` |
| `a_fail_closed` | a failed read stops the draft | `test_a_failed_read_drafts_as_before` |
| `a_no_log` | a skip writes no log | `test_a_draft_in_the_thread_of_the_copy_stops_the_second_draft` |
| `f_one_mailbox` | a member with one mailbox sees a label | `names none for a member with one mailbox` |
| `f_own_row` | the row names its own mailbox | `skips the mailbox of the row and a mailbox that left the list` |
| `f_order` | the labels follow the order of `also_in` | `names each mailbox of also_in, in the order of the switcher` |
| `f_no_row` | the list draws no "Also in" | `is in each row of the list, beside the other mailboxes of the member` |
| `f_no_chip` | a label with no `MailboxChip` | `draws 'Also in' and the chip of each mailbox` |
| `f_no_words` | the chips with no words "Also in" | `draws 'Also in' and the chip of each mailbox` |
| `f_api_no_field` | `mapEmail` drops `also_in` | `maps also_in from the list and from search` |
| `f_api_no_filter` | `mapEmail` keeps a value that is not an id | `maps also_in from the list and from search` |

**Review fix round 1 (2026-10-04).** An adversarial reviewer and an independent verifier checked
`2b59abcb9`. Neither found a P0, or a leak across members, organizations or separate mailboxes.
This round fixes one P1 and five P2 findings. It adds no migration, and the UI does not change.

| Finding | Fix | The case that goes red |
|---|---|---|
| P1 (verifier F3): a draft OLDER than the copy stopped B. The member can send a draft from Outlook desktop, and its local row stays, so B never drafted for that thread again. | The draft arm has the time bound of the sent arm: `t.received_at > c.received_at`. `_upsert_local_draft` writes `received_at = now()`, so a new draft is newer. | `test_a_draft_older_than_the_copy_does_not_stop_it` |
| F1: a race left no draft. B skipped on A's old draft. A's thread check trashed that draft, and then A met B's lock. | The dedupe runs before the thread check. A run that skips changes nothing. | `test_a_run_that_meets_the_lock_trashes_nothing` (the probe of the verifier) |
| Reviewer: a forged Message-ID paired two different mails. "Also in" showed beside a forged mail, also in junk, and a forged mail could hold off the real draft. | A copy has the same sender address, in "Also in", in the check and in the lock key. A row in junk, drafts or trash names no copy. | `test_a_copy_from_another_sender_does_not_pair`, `test_a_copy_from_another_sender_does_not_stop_it`, `test_the_lock_key_holds_each_part[sender]`, `test_a_row_in_junk_drafts_or_trash_names_no_copy` |
| F2: six rules had no fence. | New R8 cases, in the mutation table below. | `test_the_lock_key_holds_each_part`, `test_an_empty_message_id_takes_no_lock`, `test_the_thread_read_stays_in_the_mailbox_of_the_copy`, `test_a_copy_only_in_a_mailbox_that_does_not_pair_names_nothing` |
| F4: `_ANCHOR` took `a.` and `o.`, so `paired_mailbox_ids_sql("a.id")` gave the mailboxes of each member. | The anchor refuses both aliases. | `test_the_aliases_of_the_clause_are_no_anchor` |
| F5: docs. | The gateway contract names the four parts of the lock key and the partial index. | none |

- **The lock key.** It holds four parts in a JSON array: the organization, the member, the
  sender address and the Message-ID. The array keeps the parts apart, so no two keys run together.
  The sender is new in this round. Without it, a forged mail with the same Message-ID could hold
  off the real draft through the lock.
- **The accepted risk.** A forged mail from the SAME sender address still pairs, in "Also in" and
  in the dedupe. The sender sets the Message-ID. Edge case 26 accepts the same risk.
- **Recorded, not fixed.**
  - A `busy` skip is final. It stays final also when the run that holds the lock then fails in
    `create_draft` with a 401 or a 429. The mail then has no draft.
  - `run_rules_on_message` (Apply on the Test tab) reaches the dedupe. Its answer lists the
    actions of the rule, so it still names the draft action after a skip.
- **Outside the stated fix.** The sender address in the lock key. The finding named the lock, and
  the stated fix named only "Also in" and the check.

**Round 1 verification (2026-10-04, on the final tree).**

- Only `TENANT_LADDER_DATABASE_URL` was set, on a private database for each run.
- The block below gave 246 passed and 0 skipped. All 53 cases of `test_email_duplicates.py`
  passed: 33 R8 and 20 hermetic.
- All 130 email suites with `-k "not calendar"` gave 2396 passed, 6 deselected and 0 skipped.
- The ruff gate passed. Full ruff on the changed files shows no new finding.
- In `workbench/control_plane`, `npx tsc --noEmit` passed. `npx vitest run src/app/email
  src/components src/lib/theme` gave 47 files and 961 tests passed. Round 1 changes no UI file.

**Round 1 mutation run.**

- The run killed 46 of 46 mutants: 38 on the server and 8 in the UI.
- The script needed a passing run with no mutant first, and it counted a kill only when pytest
  named a red case. It restored each file and checked its hash.
- The 38 server mutants are the 25 of the build and the 13 below. The 25 ran on the code of
  round 1, four of them at a moved anchor.
- The six `v_` mutants are the survivors that the verifier found, at the new anchors.

| Mutant | What the mutant breaks | The case that goes red |
|---|---|---|
| `r1_draft_any_age` | a draft of any age stops B (the P1) | `test_a_draft_older_than_the_copy_does_not_stop_it` |
| `r1_thread_check_first` | the thread check runs before the dedupe (F1) | `test_a_run_that_meets_the_lock_trashes_nothing` |
| `r1_also_any_sender` | "Also in" pairs a copy from another sender | `test_a_copy_from_another_sender_does_not_pair` |
| `r1_answer_any_sender` | a copy from another sender stops the draft | `test_a_copy_from_another_sender_does_not_stop_it` |
| `r1_also_row_any_folder` | a row in junk, drafts or trash names a copy | `test_a_row_in_junk_drafts_or_trash_names_no_copy` |
| `r1_lock_no_sender` | the lock key has no sender | `test_the_lock_key_holds_each_part[sender]` |
| `r1_anchor_aliases` | `a.` and `o.` anchor the pair set (F4) | `test_the_aliases_of_the_clause_are_no_anchor` |
| `v_lock_no_org` | the lock key has no organization | `test_the_lock_key_holds_each_part[organization]` |
| `v_lock_no_member` | the lock key has no member | `test_the_lock_key_holds_each_part[member]` |
| `v_lock_no_msgid` | the lock key has no Message-ID | `test_the_lock_key_holds_each_part[message_id]` |
| `v_lock_empty_id` | an empty Message-ID takes the lock | `test_an_empty_message_id_takes_no_lock[None]` and `[]` |
| `v_thread_any_mailbox` | the thread read leaves the mailbox of the copy | `test_the_thread_read_stays_in_the_mailbox_of_the_copy` |
| `v_also_copy_anywhere` | "Also in" finds a copy outside the paired mailbox | `test_a_copy_only_in_a_mailbox_that_does_not_pair_names_nothing` |

The four moved anchors are `i_anchor_any`, `d_no_draft_arm`, `d_no_sent_arm` and `d_no_newer`.
`d_no_newer` now removes the one time bound, so the P1 case goes red with it too.

**Gate.** 🟢 AGENT-SAFE · R8 · security review, because both read across the mailboxes of the
member.

**Scope.** `core.py`, `transport/messages.py`, `transport/search.py`, `automation/actions.py`,
`automation/identity.py`, `components/EmailList.tsx`, `lib/api.ts`, `lib/types.ts` and tests.

**Known limit.** IMAP stores no `internet_message_id`, so items 1 and 3 never pair an IMAP
mailbox. Edge case 26 has the same limit. Outlook stores it, and Gmail does too since EM-G2 (#626).
EM-G9 proves a Gmail and Outlook pair (§12.3.11). A mail with an empty id pairs with nothing.

1. **"Also in" (item 1, edge case 10).** `GET /email/messages` and `GET /email/search` give each
   row `also_in`, a list of mailbox ids. Each id is a paired mailbox that holds a copy. A copy has
   the same non-empty `internet_message_id` and the same sender address. It is outside drafts,
   junk and trash. A row in those three folders has no `also_in` (review round 1). One read serves
   each page.
2. **The row.** `EmailList.tsx` shows "Also in <label>" with the chip of each mailbox in
   `also_in`, when the member has two or more mailboxes.
3. **The draft dedupe (item 2, edge case 11).** This step is in the REPLY and DRAFT_EMAIL branch,
   before the thread check (review round 1). The run looks for a copy of the mail in a paired
   mailbox. When the thread of that copy holds a newer draft or a newer sent mail, the run skips
   the draft. A skip changes nothing. It logs `email.draft_skipped_other_mailbox` with no address.
4. **The pair set.** One SQL constant in `identity.py`, beside `SELF_MAILBOX_IDS_SQL`, gives the
   paired mailboxes. It leaves out each separate mailbox, and it is empty when `:aid` is separate.
5. **Two runs at once.** Two overlapping runs for one mail make one draft at most. The proposal is
   a transaction-scoped try-lock on the member and the Message-ID.
6. **Not in scope.** The drafts that the member starts (`/draft-reply`, `/drafts`, `/drafts/save`,
   compose-assist) and the nudge drafts (`followups.py`).

**Fences (R7).** `tests/unit/test_email_duplicates.py` (R8, the app role):
- `email-also-in`: item 1. A copy in junk does not count. A mailbox of another member, of a
  second organization, or a separate mailbox never counts. A copy from another sender does not
  count, and a row in junk names no copy (review round 1).
- `email-also-in-one-read`: one page makes one read for `also_in`.
- `email-draft-dedupe`: B makes no draft when A holds a newer draft or a newer sent reply in the
  thread of its copy. B drafts when A holds neither, and when either mailbox is separate.
- `email-draft-dedupe-race`: two overlapping runs for one mail make one draft. A run that meets
  the lock trashes nothing, and a run that differs in one part of the lock key drafts (review
  round 1).
- `email-also-in-row` (vitest): the row shows each label of `also_in`, and a member with one
  mailbox sees none.

**Verification.**

```
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_duplicates.py tests/unit/test_email_keep_separate.py tests/unit/test_email_all_inboxes.py tests/unit/test_email_search_scope.py tests/unit/test_email_n_plus_one.py tests/unit/test_email_rulepath_draft_parity.py tests/unit/test_email_auto_draft_defaults.py tests/unit/test_email_draft_replies_action.py tests/unit/test_email_rules_engine.py tests/unit/test_email_ai_context.py -v -rs
uv run ruff check apps/services/gateway/gateway/routes/email tests/unit/test_email_duplicates.py --select F821,F601,F602,F502,F7,B006
```

In `workbench/control_plane`, also run `npx tsc --noEmit` and `npx vitest run src/app/email src/components src/lib/theme`.

##### The forward loop guard (item 3) — deferred

**What the guard must do before D-EM-29 can change.**
1. Each mail that a rule FORWARD sends carries a mark that the receiving mailbox can read. The
   proposal is the header `X-Metorite-Forwarded`.
2. The sync keeps the mark for each provider that can carry it. Today no provider write path
   takes a header (`providers/base.py:434-446`, `:662-673`), and no column holds one.
3. A FORWARD never runs on a mail that carries the mark. The other actions of the rule still run.
4. An R8 fence runs A to B to A for two mailboxes of one member. The second FORWARD does not run.

Then `POST /email/rules/copy` can keep a rule with a FORWARD to an own address, and D-EM-29 can
change.

### 11.8 Open owner question

- **Q-MB-1. How many mailboxes can one member connect?** Each mailbox adds a sync loop, Jev calls
  and up to 500 MB of storage. The proposal is 5 for each member at launch, with an admin view of
  the count. Until the owner answers, the code sets no limit. The limit is a commercial choice, so
  the agent does not make it.

---

## 12. Gmail beside Outlook (2026-10-04)

> **Owner decision, 2026-10-04.** The owner wants to work with Outlook mailboxes and with Gmail or
> Google Workspace mailboxes together. This reverses D-EM-5, which kept Outlook the only provider
> in the connect flow. §10.2 records the change on the row of D-EM-5.

> **The setup stays one click for a customer.** Metorite owns ONE Google OAuth app, as D-EM-1 to
> D-EM-4 say for Microsoft. The flow is Connect, then consent, then done. A member never
> configures an OAuth client, never pastes a key and never opens Integrations.

> **Status (2026-10-05).** 📝 SPECIFIED by EM-G0. EM-G1 (#625, §12.3.1), EM-G2 (#626,
> §12.3.2) and EM-G4b (#632, §12.3.5.2) are ✅ MERGED, and EM-G7 is ✅ MERGED
> (#637, §12.3.9). Each anchor below is
> verified against the code at `d0627789a` on 2026-10-04. Re-verify each anchor at dispatch,
> because the code is the fact. This section wins over §1 and §11 where they say that Outlook is
> the only provider.

> **The split of EM-G4 (2026-10-05).** EM-G4a and EM-G4b take the place of EM-G4 (§12.3.5).
> EM-G4a is ✅ MERGED (#629, §12.3.5.1). EM-G4b is ✅ MERGED (#632, §12.3.5.2).

> **EM-G3a (2026-10-05).** ✅ MERGED (#634, §12.3.3).

> **EM-G8 (2026-10-05).** ✅ MERGED (#638, §12.3.10), after
> EM-G7. The connect UI reads the capability read, and Gmail stays "Coming soon" while the read
> says `gmail: false`.

> **EM-G7b (2026-10-05).** ✅ MERGED (#639, §12.3.9b). A member
> allowlist narrows the Gmail connect, so the live test of EM-G10 reaches the listed members only.

> **EM-G9 (2026-10-05).** ✅ MERGED (#640, §12.3.11).
> Tests and docs only, with no SQL change. Four R8 fences prove "Also in", the draft dedupe and
> the Sent-copy proof for a Gmail and Outlook pair.

> **EM-G3b (2026-10-05).** ✅ MERGED (#645, 2026-10-05)
> (§12.3.4). A Gmail move to a user label files the message as `archive`, and the Gmail filters
> show on the rules screen. No migration, no flag and no change to SQL text.

> **D-EM-36 amended (orchestrator, 2026-10-05).** The flag flips for the owner's test after
> EM-G5a, EM-G9 and EM-G7b merge. EM-G5b, EM-G3b and EM-G3c move to "before customers" (§12.2,
> §12.3.12).

> **The split of EM-G5 (2026-10-05).** EM-G5a and EM-G5b take the place of EM-G5 (§12.3.6).
> EM-G5a is ✅ MERGED (#641, 2026-10-05) (§12.3.6.1). EM-G5b is
> ✅ MERGED (#647, 2026-10-05) (§12.3.6.2). The second audit
> cleared it as GO-NARROWED.

> **The slice names.** The slices are EM-G1 to EM-G10. §10.4.7 already uses the bare names G1 to
> G5 for the gaps of EM-T6c, so the prefix keeps the two sets apart (R2).

**Short paths in this section.**

- `providers/` is `apps/services/email_ingestion/email_ingestion/providers/`. `gmail.py`,
  `outlook.py` and `base.py` live there.
- `persist.py`, `scheduler.py` and `body_backfill.py` live in
  `apps/services/email_ingestion/email_ingestion/`.
- `transport/`, `automation/` and `core.py` live in `apps/services/gateway/gateway/routes/email/`.
- `lib/`, `components/`, `oauth/callback/page.tsx` and `page.tsx` live in
  `workbench/control_plane/src/app/email/`.

### 12.0 The answer, in six rules

1. **A Gmail mailbox does what an Outlook mailbox does.** Connect, import, sync in both directions,
   send, reply and drafts work the same. Each feature of §11 works the same too.
2. **The setup is one click.** Connect, consent, done. A Google Workspace admin acts only when the
   company blocks new apps.
3. **A change goes both ways.** A change in Metorite reaches Gmail at once. A change made in Gmail
   reaches Metorite at the next sync. That covers an archive, a read mark, a star, a label and a
   delete.
4. **A Gmail mailbox sends real mail.** The body has a text part and an HTML part. A reply threads
   at the recipient. An attachment keeps its type. One draft is one row in Metorite and one draft
   in Gmail.
5. **The import rules of §10.2 hold for Gmail.** The import goes newest first, shows an estimate
   and resumes (D-EM-10 to D-EM-16). A pause loses no mail (D-EM-13).
6. **Gmail shows only when Metorite can serve it.** The choice is live when the gateway reports the
   Google app as installed (D-EM-35). Until then it stays "Coming soon".

**What "Gmail works" means.** EM-G10 passes on production. One member connects a Gmail mailbox
beside an Outlook mailbox, and each check of EM-G10 passes for both.

**What does not change.** A mailbox stays private to its member (D-EM-4). The rules, the settings
and the AI context stay per mailbox (D-EM-18, D-EM-24). Each triage decision still goes to the
`decide` task (D-EM-7). The storage limit of 500 MB holds for each Gmail mailbox (D-EM-14).

### 12.1 Measured state (2026-10-04)

**Production.** D-EM-5 hid Gmail from the connect flow since 2026-10-01. EM-T8c removed the Gmail
leg of Integrations (§11.7.3). No deploy template sets `GMAIL_OAUTH_CLIENT_ID` or
`GMAIL_OAUTH_CLIENT_SECRET`, and `.env.example:98-99` leaves both empty. So this plan assumes no
Gmail mailbox in production, and EM-G10 checks it first.

⚠️ **Measured again on 2026-10-05, by name only.** The orchestrator found `GMAIL_OAUTH_CLIENT_ID`
and `GMAIL_OAUTH_CLIENT_SECRET` in the box `.env`. Nobody in this plan set them (Q-GM-6). So
`oauth_app("gmail")` is configured in production, and a call to the authorize route by URL could
connect Gmail. Production holds 0 Gmail mailboxes. D-EM-36 keeps the Gmail connect dark until
EM-G10.

**The provider exists, and much of it works.** `gmail.py` has 1169 lines. These parts work today:

- Sign-in and refresh, with one refresh on a 401 (`gmail.py:198-249`, `RefreshingBearer` at
  `base.py:51`).
- Labels: list, create, colour and read back (`gmail.py:670`, `:779`, `:762`, `:309`, `:837`, and
  `SUPPORTS_LABEL_READBACK` at `:145`).
- Archive, trash, read, star, flag and the bulk acts (`gmail.py:645-660`, `:557`, `:564`, `:602`).
- Filters: create and delete (`gmail.py:791`, `:828`). Attachments: list and download (`:82`,
  `:1026`).
- The multi-inbox features that need no Message-ID. These are the sending mailbox, the colour
  slots, All inboxes, Keep separate and the settings of each mailbox. The account picker for one
  more mailbox works too (`transport/oauth.py:179-207`).

**The defects.** Each one is real at `d0627789a`. The last column names the slice that fixes it.

| Id | Defect | Evidence | Slice |
|---|---|---|---|
| **GM-1** | **No Message-ID.** The parser sets no `internet_message_id`. The header read is case-sensitive, so it misses `Message-Id`. "Also in", the draft dedupe of a pair and the Sent-copy proof never see a Gmail mailbox. | `gmail.py:1093-1123`, `:1135-1143`, `automation/identity.py:173`, `:224`, `:247`, `:328` | EM-G2, proved by EM-G9 |
| **GM-2** | **A Message-ID would turn on the re-key reclaim for Gmail.** The reclaim moves a row to a new provider id when one row holds the Message-ID. Gmail never re-keys an id. So two Gmail messages with one Message-ID would fold into one row, and that row would swap ids at each sync. | `persist.py:219-246`, `gmail.py:616` | EM-G1 |
| **GM-3** | **A nested body is empty.** The parser reads the top-level parts only. In `multipart/mixed`, which is any mail with an attachment, the nested `multipart/alternative` stays unread. A single-part HTML mail lands in `body_text`, and `body_html` stays None. The rules, Reply Zero, the drafter and the embeddings then read an empty body. | `gmail.py:1043-1061`, read through `body_backfill.py:141`, `transport/messages.py:691`, `:1026` and `core.py:410` | EM-G2 |
| **GM-4** | **The address parse splits on each comma.** `"Doe, John" <j@x.com>` becomes two broken addresses. | `gmail.py:1098-1104`, `:1145-1169` | EM-G2 |
| **GM-5** | **Archived mail files as `inbox`.** The parse files a message with no system label as `inbox`. Each path that keeps the folder of the parse then shows archived mail in the Inbox, and the history branch is one of them. | `gmail.py:38-52` | EM-G2 (D-EM-33) |
| **GM-6** | **The Archive folder cannot page.** Gmail has no `archive` label. "Load older" in Archive sends `labelIds=["archive"]`, and Gmail refuses it. | `transport/folders.py:375-398`, `gmail.py:282-285` | EM-G2 (D-EM-33) |
| **GM-7** | **"Load older" in a user-label view moves Inbox mail out of the Inbox.** The page sets the folder of each message to the label name through `canonical_override`, also when the message has `INBOX`. | `transport/folders.py:391-397`, `gmail.py:295-302` | EM-G2 |
| **GM-8** | **The import misses archived mail with no user label.** The deep sync and the sweep list the user labels and five system labels only. The sweep fetches a message with two user labels twice. | `gmail.py:1884-1923`, `:1925-1984`, `:1967` (moved from `:903-933` and `:986-1018`, checked at `a9a08deb9`) | EM-G5a (the import). EM-G5-f1 records the sweep half |
| **GM-9** | **The provider fetches each message alone, and a failed fetch leaves no record.** No call handles a 429 or a rate-limit 403. | `gmail.py:296-304`, `:358-366` | EM-G4a (rate limits and the fetch record), EM-G5a (fetch in parallel) |
| **GM-10** | **The send loses the HTML body.** `MIMEText(body_text, "html" if body_html)` sends the text with an HTML label and drops `body_html`. The HTML signature and the line breaks go. `create_draft` has the same defect. `update_draft` is correct. | `gmail.py:387`, `:398`, `:444`, `:455`, `:491-492` | EM-G3a |
| **GM-11** | **A reply does not thread at the recipient.** The send sets `threadId` only. It sets no `In-Reply-To` and no `References`. | `gmail.py:399-415`, `:456-466` | EM-G3a |
| **GM-12** | **Each attachment goes as `application/octet-stream`.** | `gmail.py:389`, `:446`, `:500` | EM-G3a |
| **GM-13** | **One draft gets two rows, and a draft made in Gmail cannot send.** `create_draft` returns the draft id, and the local row stores it. The sync stores the message id of the same draft as a second row. A draft made in Gmail has only the message id, so `update_draft` and `send_draft` fail with an HTTP error. The fallback catches only `NotImplementedError`, so it never runs. | `gmail.py:470`, `:531-536`, `automation/drafting.py:2126-2153`, `:2191-2196`, `:2265-2279` | EM-G3a |
| **GM-14** | **A move to a user label does nothing.** `move_to_folder` knows trash, archive, inbox and junk only. Outlook creates the folder and moves the mail. A rule move then logs `email.move_folder_noop`. | `gmail.py:645-660`, `outlook.py:1096-1120`, `automation/actions.py:430-448` | EM-G3b |
| **GM-15** | **The list of upstream filters is empty.** Gmail has no `list_filters`, so the base returns `[]`. | `base.py:618-629`, `outlook.py:1173`, `automation/rules.py:721` | EM-G3b |
| **GM-16** | **No history cursor is ever stored.** The first sync and the deep sync return `new_history_id=None`, and nothing calls `getProfile`. The scheduler keeps the NULL. So each poll runs the branch for no cursor: the first page of each user label and of five system labels, with each message fetched alone. | `gmail.py:930-933`, `:1020-1024`, `scheduler.py:1418-1419` (moved from `:1413-1414`, checked at `ba723e0f1`) | EM-G4b |
| **GM-17** | **The history branch has four defects, and it never runs today.** It reads one page and ignores `nextPageToken`. It asks for the label events and drops them, so an archive, a read mark, a star or a label made in Gmail never syncs. It stores the current id of the mailbox after that one page, so the changes on the later pages are lost. A stale cursor answers 404, and `raise_for_status` fails each cycle until a Resync. | `gmail.py:935-979`, `:940`, `:943`, `:946`, `transport/sync.py:283-325` | EM-G4b |
| **GM-18** | **A pause loses mail.** The provider ignores `catch_up`, so it never reads the mail past the first page of a label after a pause (against D-EM-13). `email_ingestion/AGENTS.md:40` said that the Gmail cursor reads each change. EM-G0 corrects that line. | `gmail.py:898-900` | EM-G4b |
| **GM-19** | **The import has no stream, no estimate and no resume point.** Gmail uses the default of the base class, which fetches all, then sorts and cuts. The progress shows a count with no estimate (against D-EM-16). | `base.py:830-858` (moved from `:785-813`, checked at `a9a08deb9`) | EM-G5a |
| **GM-20** | **No reconcile.** `import_full_snapshot` is False, so Resync and Process past emails trash no row that Gmail deleted. Gmail also has no confirm by its own id. The confirm of Outlook, `message_exists`, asks by the Message-ID, and that key cannot prove a Gmail delete (C1). ✅ EM-G5b fixes it, merged as #647 (2026-10-05). `import_full_snapshot` is True, and `message_gone` confirms by the provider id. | `base.py:432`, `scheduler.py:771`, `:881-883`, `:924-929`, `outlook.py:1361`, `:1850-1864` (moved from `base.py:407`, `scheduler.py:669`, `:780`, `outlook.py:1357` and `:1846`, checked at `a9a08deb9` and again at `3c1a27012`). The fix: `gmail.py:1764`, `:2209`, `scheduler.py:761`, `:959-961` on branch `email-gmail-g5b` | EM-G5b |
| **GM-21** | **No push.** Gmail has no watch and no stop. `_ensure_subscription` returns early for a provider that is not Microsoft. `/webhooks/gmail` is the Phase-1 receiver. It never finds an `email_accounts` row and never calls `_webhook_sync`. Gmail polls only. | `transport/sync.py:438-508`, `:469`, `apps/services/ingestion/ingestion/sources/gmail/webhook.py:41-149`, `gateway/main.py:597`, `:1200-1205` | EM-G6a, EM-G6b |
| **GM-22** | **The scope is the widest restricted scope.** The authorize leg asks `https://mail.google.com/`. The callback never reads the granted scope, and Google lets a member clear a scope on the consent page. | `gmail.py:138`, `transport/oauth.py:201-207`, `:273-385` | EM-G7 (D-EM-31) |
| **GM-23** | **Google errors map to a generic code.** Only `access_denied` maps, to `consent_declined`. | `transport/oauth.py:388-415` | EM-G7 |
| **GM-24** | **A bounce never names the provider.** `_bounce` adds `error` only, so the callback page takes Microsoft for each failure. | `transport/oauth.py:303-306`, `oauth/callback/page.tsx:191-193` | EM-G7, EM-G8 |
| **GM-25** | **A member reads an operator instruction.** With no Google app on the box, the authorize leg tells the member to paste a Google client into Integrations. | `transport/oauth.py:84-96`, `:193-194` | EM-G7 (D-EM-35) |
| **GM-26** | **The UI says "Coming soon" and blocks each Gmail path.** `available` is false, and the retry and the range step refuse a provider that is not live. | `lib/connect.ts:46-53`, `:248-286`, `lib/connect.test.ts:116-122`, `:561-562`, `:722` | EM-G8 |
| **GM-27** | **The copy names Microsoft only.** The empty state, the decline text, the generic failure and the callback page do so. | `components/ConnectEmptyState.tsx:75`, `lib/connect.ts:441`, `:469`, `oauth/callback/page.tsx:240` | EM-G8 |
| **GM-28** | **No test reads real Gmail behaviour.** The Gmail cases in `tests/unit` use fakes. None covers the body parse, the history branch, the Message-ID, the HTML send, the two draft ids, a move to a label or a push. `test_gmail_normaliser.py` tests the Phase-1 normaliser, not this provider. | `tests/unit/test_email_*.py` | each slice adds its fence |

### 12.2 Decisions (2026-10-04)

D-EM-5 changed by owner decision, and §10.2 holds the amended row. The orchestrator took D-EM-31 to
D-EM-35 for this plan, and D-EM-36 on 2026-10-05. Each one is an agent decision, and the owner can
reverse it.

| Id | Decision |
|---|---|
| **D-EM-31** | **The Google scopes are `gmail.modify` and `gmail.settings.basic`.** They replace `https://mail.google.com/`. The pair is the narrowest set that covers read, send, drafts, labels and filters. Both are restricted scopes, but a narrower request makes the review of Google easier. `gmail.modify` covers the profile, the history, the watch, the messages, the drafts and the labels. `gmail.settings.basic` covers the filters. No code path deletes a message for good, so `gmail.modify` loses nothing. The callback reads the granted scope. A grant that lacks either scope saves no mailbox, and the member sees a clear message. *Agent decision (orchestrator, 2026-10-04). The owner can reverse it.* |
| **D-EM-32** | **Polling first, push later.** EM-G4 makes a poll cheap with the history cursor, so an idle mailbox costs one call. Push (EM-G6a, EM-G6b) ships dark behind `EMAIL_GMAIL_PUSH`, which is `off` by default. Push needs a design for how a push finds its tenant, and that design gets its own audit (O-GM-4). *Agent decision (orchestrator, 2026-10-04). The owner can reverse it.* |
| **D-EM-33** | **The folder model of Gmail.** The system labels decide the folder, in this order: `TRASH`, `SPAM`, `DRAFT`, `SENT`, `INBOX`. A Gmail message with no system label is in `archive`. A move to a user label adds that label and removes `INBOX`, `TRASH` and `SPAM`. The audit of EM-G3b added `TRASH` and `SPAM` on 2026-10-05 (E-M5), so a move out of Trash or Spam leaves `archive`. The Archive folder pages with a query, never with `labelIds=["archive"]`. The orchestrator decided O-GM-1 on 2026-10-04: a user label is a label and never a folder. *Agent decision (orchestrator, 2026-10-04). The owner can reverse it.* |
| **D-EM-34** | **The re-key reclaim runs only for a provider that re-keys its ids.** That is Outlook, whose ids change on a move. Gmail never re-keys an id, so Gmail never folds two rows on one Message-ID. The provider attribute is false by default. IMAP stores no Message-ID today, so IMAP sees no change. *Agent decision (orchestrator, 2026-10-04). The owner can reverse it.* |
| **D-EM-35** | **Gmail shows only when the Google app is installed.** The UI reads a capability from the gateway: for each provider, whether its app credentials are set. Gmail is a live choice only when the answer is true. No member ever sees "configure Integrations". *Agent decision (orchestrator, 2026-10-04). The owner can reverse it.* |
| **D-EM-36** | **The Gmail connect ships dark behind `EMAIL_GMAIL_CONNECT`, which is off by default.** The box in production holds a Google client today (§12.1), so the app alone cannot keep Gmail hidden. While the flag is off, the capability read answers `gmail: false`, and each Gmail leg of EM-G7 refuses. ~~The flag flips at EM-G10 only, after EM-G2 to EM-G5 and EM-G9 merge, under gate `enforcement-flip`.~~ **Amended (orchestrator, 2026-10-05): the flag flips for the owner's test after EM-G5a, EM-G9 and EM-G7b merge, under gate `enforcement-flip`.** The flip for the test of the owner also sets `EMAIL_GMAIL_CONNECT_MEMBERS` to the member address of the owner (EM-G7b, §12.3.9b). With the flag on and the list empty, every member sees Gmail, and that state is for the time after Google verifies the app. The text below this table holds the reason. *Agent decision (orchestrator, 2026-10-05). The owner can reverse it.* |

**The amendment of D-EM-36 (orchestrator, 2026-10-05).** The flag flips for the owner's test when
three slices merge:

- EM-G5a, the import half of EM-G5 (§12.3.6).
- EM-G9, the parity of a Gmail and Outlook pair (§12.3.11).
- EM-G7b, a member allowlist beside the flag. Its variable is
  `EMAIL_GMAIL_CONNECT_MEMBERS` (#639).

Three items move from "before the flip" to "before customers" (§12.3.12):

- EM-G5b, the reconcile half of EM-G5. It needs a confirm by message id (E-G5-9).
- EM-G3b, a move to a user label, and the filter list (§12.3.4).
- EM-G3a-f8, the size limit of Google for a plain `drafts.update`, and the autosave of a draft
  with files (§12.3.3). A new slice, EM-G3c, owns it.

**Why.** The owner's test is one mailbox of one member, in the Testing mode of Google. In that
mode a refresh token ends after 7 days. The history cursor of EM-G4b already reports each delete
within a week. So the owner's test needs none of the three items, and each one binds a customer
mailbox only.

**Open questions for the orchestrator.** Each one blocks the slice in its last column. Each has a
recommendation, and the slice scope below follows the recommendation. If the orchestrator decides
otherwise, the scope of that slice changes before dispatch.

| Id | Question | Recommendation | Blocks |
|---|---|---|---|
| **O-GM-1** | **Is a Gmail user label also a folder?** Today the sweep files a message under the name of a user label when the message has no system label. The `folder` column holds one value, and a Gmail message can carry many labels. So each path can give a different folder (GM-7). | **No. A user label is a label, as an Outlook category is.** The folder comes from the system labels only, and `archive` when there is none (D-EM-33 as written). The user labels stay in `categories`, where `list_labels` and `set_labels` already put them. The folder tree of a Gmail mailbox shows the well-known folders, and its user labels show in the label filter. The other answer files a message under its first user label by name. Then a message with two labels shows under one only, and a label view misses the labelled mail in the Inbox. | EM-G2, EM-G3b, EM-G8 item 5 |
| **O-GM-2** | **Which id does the local row of a Gmail draft hold?** Gmail has a draft id and a message id for one draft. It also gives the draft a new message id at each update. | **The message id, which is the id that the sync finds.** `create_draft` and `update_draft` return the message id of the draft. `update_draft` and `send_draft` take a message id and find the draft id through `users.drafts.list`. The save then moves the local row to the new message id. This needs no column and no migration. The other answer stores the draft id. The sync must then read `drafts.list` at each cycle, and a column must hold the second id. | EM-G3a |
| **O-GM-3** | **Does a disconnect revoke the Google grant?** | **No.** A revoke at Google can end each token that the same Google account gave to the Metorite client. Another member or another organization can hold a copy of the same address (D-EM-17, §11.6 edge cases 3 and 4), and a revoke would break that copy. The disconnect dialog tells the member where to remove access in the Google account. | nothing (EM-G7 lists it as a non-goal) |
| **O-GM-4** | **How does a Gmail push find its tenant?** A push carries the address and a history id only. One address can be a mailbox in two organizations, and of two members (D-EM-17). | **Candidate (a).** (a) One unbound discovery read, in a `SECURITY DEFINER` function, maps an address to its pairs of organization and mailbox id, and returns nothing else. The route then queues one sync for each pair, inside its own tenant. (b) One Pub/Sub topic for each organization, each with a push subscription whose URL carries a signed `org`, as the Graph webhook does (EM-T1a item 5). (a) adds no cloud resource and no secret, and a push only starts a sync that each mailbox runs with its own token. (b) keeps the signed-org seam, but the box then needs a Pub/Sub admin credential to make a topic for each organization. The audit of EM-G6a decides, with a security review. | EM-G6a, EM-G6b |

**Decided (orchestrator, 2026-10-04).** O-GM-1, O-GM-2 and O-GM-3 follow the recommendation, so the slice scopes stand as written. The EM-G6a audit decides O-GM-4. Candidate (a) is the lean, and the audit checks its cross-tenant read before any build.

| Id | Question | Decision | Slice |
|---|---|---|---|
| **O-GM-5** | **May an Integrations write set the key of a mail app?** `PUT /integrations/keys` and `POST /integrations/configure` write `os.environ` and the box `.env`. `oauth_app` reads that environment. So a member of one organization could aim the mail connect of every organization at an OAuth client of their own. | **No.** The configure, put and delete writes refuse each `GMAIL_OAUTH_*`, `MSFT_OAUTH_*` and `AUTH_MICROSOFT_ENTRA_ID_*` key with a 403, and write nothing. The `gmail-oauth` tile leaves Integrations. Metorite owns the mail apps (D-EM-1), and an operator sets them on the box under gate `env-write`. | EM-G7 |

**Decided (orchestrator, 2026-10-05).** O-GM-5 takes the answer in the table, and EM-G7 builds it. The reason is the reach of one write. One member of one organization changes the mail app of every organization on the box, and no connect log shows the change. This answer reverses the EM-G7 non-goal that kept the `gmail-oauth` tile. The wider fix, for every key that is not a mail key, stays owner gate §6 (f).

### 12.3 Slices

Each slice is one PR. Each slice that changes code ships dark: Gmail stays "Coming soon" until
the capability read answers `gmail: true` (D-EM-35). That needs the Google app on the box and the
flag `EMAIL_GMAIL_CONNECT` on (D-EM-36, E-D1). A slice that merges updates the status of this
section and the WS-17 row in the same PR (R4).

| Slice | Gate | Scope | Order | Size | Section |
|---|---|---|---|---|---|
| **EM-G1** | 🟢 AGENT-SAFE · R8 | The reclaim gate (D-EM-34, GM-2) | First | S | §12.3.1 |
| **EM-G2** | 🟢 AGENT-SAFE | The body, the headers, the Message-ID, the addresses and the folder model (GM-1, GM-3 to GM-7) | After EM-G1 and O-GM-1 | M | §12.3.2 |
| **EM-G3a** | 🟢 AGENT-SAFE · R8 | Send and drafts: the MIME body, threading, attachment types, the draft ids (GM-10 to GM-13) | After EM-G4a and O-GM-2 | M | §12.3.3 |
| **EM-G3b** | 🟢 AGENT-SAFE | A move to a user label, and the filter list (GM-14, GM-15) | After EM-G2 and O-GM-1 | M | §12.3.4 |
| **EM-G4a** | 🟢 AGENT-SAFE | The rate-limit helper at the client seam, and the record of a failed fetch (GM-9, items 8 to 10) | After EM-G2 | S | §12.3.5.1 |
| **EM-G4b** | 🟢 AGENT-SAFE · R8 | The history cursor, the label events, the stale cursor and its recovery (GM-16 to GM-18, items 1 to 7 and 11) | After EM-G4a | L | §12.3.5.2 |
| **EM-G5a** | 🟢 AGENT-SAFE · R8 | The import, the estimate and the resume (GM-8, GM-19, items 1 to 5) | After EM-G4a and EM-G4b | L | §12.3.6.1 |
| **EM-G5b** | 🟢 AGENT-SAFE · R8 · ✅ MERGED #647 | The reconcile (GM-20, items 6 to 12, E-G5-9) | After EM-G5a | M | §12.3.6.2 |
| **EM-G6a** | 🟢 AGENT-SAFE build · security review · R8 · 🔴 flip (`enforcement-flip`) | The push route and its tenant, dark (GM-21) | After EM-G4b and O-GM-4 | M | §12.3.7 |
| **EM-G6b** | 🟢 AGENT-SAFE build · 🔴 flip (`enforcement-flip`) | The watch, its renewal and its stop, dark (GM-21) | After EM-G6a | M | §12.3.8 |
| **EM-G7** | 🟢 AGENT-SAFE · security review | The connect backend: scopes, granted scope, errors, bounces, the capability read (GM-22 to GM-25) | Any time. Merges dark | M | §12.3.9 |
| **EM-G7b** | 🟢 AGENT-SAFE · security review | A member allowlist for the Gmail connect, `EMAIL_GMAIL_CONNECT_MEMBERS` (go-live item A7) | After EM-G7 and EM-G8, before EM-G10. Merges dark | S | §12.3.9b |
| **EM-G8** | 🟢 AGENT-SAFE · visual review | The connect UI: availability, copy, the Workspace admin help (GM-24, GM-26, GM-27) | After EM-G7 (E-D1). Merges dark | M | §12.3.10 |
| **EM-G9** | 🟢 AGENT-SAFE · R8 | Parity of a Gmail and Outlook pair, and the known limits | After EM-G2 and EM-G3a | S | §12.3.11 |
| **EM-G10** | 🔴 OWNER-GATE | Live acceptance with a test Gmail user | Last | S | §12.3.12 |

**The order, in one line.** EM-G1, then EM-G2, then EM-G4a. EM-G3a and EM-G4b then go in any
order, and EM-G3b can go at any time after EM-G2. EM-G5a follows, then EM-G5b after its new
audit, and EM-G7 can go at any time. EM-G8 and EM-G9 follow, then EM-G10. EM-G6a and EM-G6b wait for O-GM-4, and EM-G10 does not need
them (D-EM-32).

**Sizes.** S is under 150 changed lines with tests. M is 150 to 500. L is more than 500. Each
estimate counts code and tests.

**Live tests.** EM-G7 and EM-G8 need the Google app of the owner (§12.4) for a live test. Each one
can merge dark before that. The box holds a Google client today (§12.1), so the flag of D-EM-36
keeps Gmail hidden, not the app (E-D1).

#### 12.3.1 EM-G1 — the reclaim gate (D-EM-34)

**Status.** ✅ MERGED #625 (2026-10-05). The as-built notes, the
mutation table and the known limit EM-G1-f1 are at the end of this section.

**Gate.** 🟢 AGENT-SAFE · R8. No migration.

**Order.** First. EM-G2 fills the Message-ID, so EM-G2 must not merge before EM-G1.

**Size.** S. About 30 lines of code and one test file.

**Scope.**

1. `BaseEmailProvider` gains the class attribute `REKEYS_MESSAGE_IDS = False`. `OutlookProvider`
   sets it to `True`.
2. `persist.upsert_message` gains the keyword `reclaim: bool = False`. The reclaim block
   (`persist.py:232-246`) runs only when `reclaim` is true.
3. `scheduler._write_messages` gains the same keyword. Its two calls pass
   `provider.REKEYS_MESSAGE_IDS` (`scheduler.py:804`, `:1360`).
4. `core._upsert_message` gains the same keyword. Its one caller passes the attribute of its
   provider (`transport/folders.py:404`).
5. `inbound.py:282` keeps `on_conflict="nothing"`, which never reclaims.
6. The comment at `persist.py:219-231` names D-EM-34.

**Non-goals.** No change to the SQL of the reclaim. No merge of rows that already share a
Message-ID. No change to IMAP.

**Fences (R7).** A new `tests/unit/test_email_rekey_reclaim.py` runs on a real Postgres as the app
role, in the harness of `test_email_duplicates.py`.

- `test_two_gmail_messages_with_one_message_id_keep_two_rows`
- `test_an_outlook_rekey_still_reclaims_its_row`
- `test_the_default_is_no_reclaim`
- `test_each_upsert_caller_names_reclaim`, an AST fence over `apps/services`. Each call of
  `upsert_message`, `_write_messages` or `_upsert_message` with the update path passes `reclaim=`.

`test_email_persist_upsert.py::test_a_rekeyed_message_reclaims_its_row_instead_of_ghosting` passes
`reclaim=True`, and a twin case proves `reclaim=False`.

**Mutations.** Each one must turn a named test red.

- M1. Remove the gate. `test_two_gmail_messages_with_one_message_id_keep_two_rows` fails.
- M2. Make the default `True`. `test_the_default_is_no_reclaim` fails.
- M3. Set `OutlookProvider.REKEYS_MESSAGE_IDS = False`. `test_an_outlook_rekey_still_reclaims_its_row`
  fails, because it reads the attribute.
- M4. Drop `reclaim=` from `scheduler.py:1360`. The AST fence fails.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_rekey_reclaim.py tests/unit/test_email_persist_upsert.py \
  tests/unit/test_email_import_batches.py tests/unit/test_email_deep_sync.py -v -rs
uv run ruff check apps/services/email_ingestion apps/services/gateway/gateway/routes/email \
  tests/unit/test_email_rekey_reclaim.py
```

The R8 tests must show PASSED, with 0 skips.

**As built (2026-10-04, branch `email-gmail-g1`).** The build follows the four rules E1 to E4 of
the audit. The SQL of the reclaim did not change, and the slice needs no migration.

- **The gate.** `persist.upsert_message` takes the keyword-only `reclaim: bool = False`. The
  reclaim runs only when `reclaim` is true, on the update path, for a message with a Message-ID.
  The comment above the reclaim names D-EM-34, GM-2 and EM-G1-f1.
- **The attribute.** `BaseEmailProvider.REKEYS_MESSAGE_IDS` is false. `OutlookProvider` sets it
  to true. Gmail and IMAP inherit false.
- **The callers (E3).** `scheduler._write_messages` and `core._upsert_message` take the same
  keyword and pass it on. Three calls pass `getattr(provider, "REKEYS_MESSAGE_IDS", False)`.
  These are the import batch in `_run_import`, the sweep in `_sync_cycle` and "Load older" in
  `transport/folders.py`. The `getattr` form is deliberate, because five test fakes do not
  subclass the base.
- **Inbound.** `inbound.py` keeps `on_conflict="nothing"` and passes no `reclaim`.
- **The test fakes (E1).** Seven upsert fakes had the fixed signature `(db, account_id, msg)`.
  Each one now takes `*, reclaim=False`. The fake of `test_email_sync_one_at_a_time.py` passes
  `reclaim` on to the real upsert. No expected value changed.
- **The scope of the AST fence (E2).** The fence reads `apps/services/email_ingestion` and
  `apps/services/gateway/gateway/routes/email` only. `whatsapp_ingestion/persist.py` has an
  `upsert_message` of its own, with no reclaim.
- **`test_email_persist_upsert.py`.** The reclaim test passes `reclaim=True`. Its twin
  `test_a_rekeyed_message_without_reclaim_runs_no_update` proves `reclaim=False` and the default.
  The two older guards (no Message-ID, the insert-only path) now pass `reclaim=True`, so each
  one still proves its own condition.

**The fences, as built.** `tests/unit/test_email_rekey_reclaim.py` holds 11 cases. The five R8
cases run on a real Postgres as `acb_app_h3rls`.

| Test | Kind | What it proves |
|---|---|---|
| `test_only_outlook_rekeys_its_ids` | hermetic | The base, Gmail and IMAP read false. Outlook reads true. |
| `test_each_reclaim_keyword_is_keyword_only_and_false_by_default` (3 cases) | hermetic | Each of the three keywords is keyword-only, with false as its default. |
| `test_each_upsert_caller_names_reclaim` | hermetic, AST | Each update-path call names `reclaim=`. The scan finds the five known calls and the one insert-only call. |
| `test_the_caller_fence_can_fail` | hermetic | The fence finds each planted call with no `reclaim=`, also a call through `**kwargs`. |
| `test_two_gmail_messages_with_one_message_id_keep_two_rows` | R8 | Two Gmail messages with one Message-ID keep two rows. No row swaps its id on a second sync. |
| `test_an_outlook_rekey_still_reclaims_its_row` | R8 | An Outlook move keeps its one row and its row id. |
| `test_the_default_is_no_reclaim` | R8 | An upsert with no keyword moves no row. |
| `test_each_caller_passes_the_attribute_of_its_provider` (2 cases) | R8 (E4) | `_sync_account` (the first import, then the sweep) and "Load older" pass the flag of the provider, for true and for false. |

**Mutations, as run (2026-10-04).** Each mutation ran against `test_email_rekey_reclaim.py` and
`test_email_persist_upsert.py` on a real Postgres. A script then wrote back the original bytes of
the file and checked its SHA-256.

| Id | Mutation | Red tests |
|---|---|---|
| M1 | The reclaim ignores the flag (`persist.py`) | `test_two_gmail_messages_with_one_message_id_keep_two_rows`, `test_the_default_is_no_reclaim`, `test_each_caller_passes_the_attribute_of_its_provider[keeps_ids]`, and both cases of the twin in `test_email_persist_upsert.py` |
| M2 | The base attribute is true (`base.py`) | `test_only_outlook_rekeys_its_ids`, `test_two_gmail_messages_with_one_message_id_keep_two_rows` |
| M2b | The default of `upsert_message` is true (`persist.py`) | `test_the_default_is_no_reclaim`, `test_each_reclaim_keyword_is_keyword_only_and_false_by_default[persist.upsert_message]`, the twin `[no_keyword]` |
| M3 | The Outlook attribute is false (`outlook.py`) | `test_only_outlook_rekeys_its_ids`, `test_an_outlook_rekey_still_reclaims_its_row` |
| M4 | The `_sync_cycle` call drops `reclaim=` (`scheduler.py`) | `test_each_upsert_caller_names_reclaim`, `test_each_caller_passes_the_attribute_of_its_provider[rekeys]` |
| M5 | The `_sync_cycle` call passes `reclaim=False` (`scheduler.py`) | `test_each_caller_passes_the_attribute_of_its_provider[rekeys]` |
| M6 | The `_run_import` call reads `"REKEYS_MESSAGE_ID"` (`scheduler.py`) | `test_each_caller_passes_the_attribute_of_its_provider[rekeys]` |
| M7 | The "Load older" call passes `reclaim=True` (`folders.py`) | `test_each_caller_passes_the_attribute_of_its_provider[keeps_ids]` |
| M8 | `core._upsert_message` drops the keyword when it calls the upsert (`core.py`) | `test_each_upsert_caller_names_reclaim`, `test_each_caller_passes_the_attribute_of_its_provider[rekeys]` |
| X2 (review) | The deep path passes `reclaim=... and progress`, so a deep sync gets False (`scheduler.py:808`) | `test_every_outlook_path_passes_reclaim_true` |

M5 is the reason for the value fence (E4). The AST fence stays green for M5, M6 and M7, because
each of those calls names `reclaim=`. Without the value fence, M5 gives each production Outlook
mailbox a second row at each folder move.

**Review (2026-10-05).** The verifier passed EM-G1 with four P3 findings. Two were gaps in the value fence: it ran no deep sync, and it patched the factory. The new fence `email-reclaim-every-outlook-path` closes both. The real factory builds each Outlook provider, and the first import, a sweep, a deep sync, a Resync and Load older each send `reclaim=True`. Mutation X2 survived before this fence and is red now.

**Known limit EM-G1-f1 (a follow-up, not fixed here).** The Outlook reclaim folds a mail that a
member sends to their own address. The Sent Items copy and the Inbox copy have two Graph ids and
one Message-ID. So each sweep moves the one row between `sent` and `inbox`. EM-G1 keeps this fold,
because Outlook keeps `REKEYS_MESSAGE_IDS = True`, and no fence treats the fold as correct. The
follow-up EM-G1-f1 owns the fix, and it needs its own audit before a build.

#### 12.3.2 EM-G2 — the parse and the folder model

**Status.** ✅ MERGED #626 (2026-10-05). The as-built notes and the
mutation table are at the end of this section.

**Gate.** 🟢 AGENT-SAFE. No migration.

**Order.** After EM-G1, which merged as #625. The orchestrator decided O-GM-1 on 2026-10-04 (§12.2):
a user label is a label. The scope follows that answer.

**Size.** M. About 200 lines in `gmail.py`, with fixtures and tests of about 300 lines.

**Scope.**

1. **The body walk (GM-3).** A recursive walk reads the payload at any depth. It takes the first
   `text/plain` part and the first `text/html` part that is not an attachment.
2. **A single-part HTML mail** fills `body_html` and leaves `body_text` empty, as Outlook does
   (`outlook.py:1955-1956`). `body_backfill._html_to_text` fills the text later.
3. **The charset.** The walk decodes each part with the `charset` of its `Content-Type`, and
   UTF-8 when it names none.
4. **The headers (GM-1).** `_parse_headers` reads each name in any case. `Message-ID` and
   `Message-Id` both fill `internet_message_id`.
5. **The form of the Message-ID.** The parse trims the value and keeps its angle brackets, the form in
   which Graph gives `internetMessageId` (`outlook.py:1971`). One mail in a Gmail and an Outlook
   mailbox then has one value.
6. **The addresses (GM-4).** `From`, `To`, `Cc` and `Bcc` parse with `email.utils.getaddresses`.
   An encoded word decodes.
7. **The folder (GM-5, D-EM-33).** `TRASH`, `SPAM`, `DRAFT`, `SENT` and `INBOX` decide, in that
   order, else `archive`. A user label never sets the folder (O-GM-1). The user labels stay in
   `categories`.
8. **One rule for the folder (GM-7).** Gmail `list_messages` keeps the folder of the parse and
   ignores `canonical_override`. A page of any label view then files each message by its labels.
9. **The Archive folder (GM-6).** `list_messages` maps the folder key `archive` to the query
   `-in:inbox -in:sent -in:drafts`. It joins the query of the caller, and it sends no `labelIds`.
   `messages.list` leaves out spam and trash by default.
10. **A user label named Archive.** `transport/folders.py:380-386` maps the key `archive` to a
    label whose name is Archive. So the Gmail provider also takes the query when
    `canonical_override` is `archive`, and such a label never takes the place of the folder.
11. **Fixtures.** `tests/unit/fixtures/gmail/` holds message JSON in the shape of
    `users.messages.get` with `format=full`. Build each fixture from a real shape, with no real
    address and no real content. The set is (a) `multipart/mixed` with a nested
    `multipart/alternative` and a PDF, (b) single-part HTML, (c) `multipart/related` with an inline
    image, (d) a lower-case `Message-Id`, (e) a name with a quoted comma, (f) an archived message
    with a user label, (g) an ISO-8859-1 body, (h) an RFC 2047 display name (E6), and (i) a forward
    as an inline `message/rfc822` part (review round 1).

**Non-goals.** No send change (EM-G3a). No cursor (EM-G4). No new column and no migration. The
folder tree of the UI is EM-G8 item 5.

**No backfill of old Gmail rows (R6, E11).** A fetch of a row writes the new parse. But until EM-G4
and EM-G5 land, the sweep reads only the first page of each label, so an older row keeps its old
folder. That is acceptable, because production holds no Gmail mailbox (two Microsoft mailboxes
only, checked 2026-10-04).

**Fences (R7).** A new `tests/unit/test_gmail_parse.py` reads the fixtures.

- `test_a_nested_alternative_gives_text_and_html`
- `test_single_part_html_fills_body_html`
- `test_the_charset_of_the_part_decodes_the_body`
- `test_message_id_reads_in_any_case`
- `test_message_id_keeps_the_form_of_graph`
- `test_a_quoted_comma_is_one_address`
- `test_no_system_label_files_as_archive`
- `test_a_user_label_never_sets_the_folder`
- `test_a_label_page_keeps_the_folder_of_the_parse`
- `test_the_archive_page_sends_a_query_and_no_label`
- `test_a_user_label_named_archive_does_not_replace_the_query`
- `test_an_encoded_display_name_decodes` (E6, fixture (h))

`test_message_id_reads_in_any_case` runs on `Message-ID`, `Message-Id` and `message-id` (E7).

One more R8 case in `test_email_rekey_reclaim.py`,
`test_two_parsed_gmail_fixtures_with_one_message_id_write_two_rows`, runs the real parse through
`_write_messages`.

**Mutations.** Each one must turn a named test red. E8 added M6 to M9.

| Id | Mutation | The test that fails |
|---|---|---|
| M1 | Read the top level only | the nested test |
| M2 | Make the header read case-sensitive | the `Message-Id` test |
| M3 | Put back the `inbox` fallback | the archive test |
| M4 | Honour `canonical_override` | the label-page test |
| M5 | Send `labelIds=["archive"]` | the archive-page test |
| M6 | Strip the angle brackets, or lower the case, of the Message-ID | `test_message_id_keeps_the_form_of_graph` |
| M7 | Split the address list on each comma | `test_a_quoted_comma_is_one_address` |
| M8 | Decode each part as UTF-8 only | `test_the_charset_of_the_part_decodes_the_body` |
| M9 | Put single-part HTML into `body_text` | `test_single_part_html_fills_body_html` |

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_gmail_parse.py tests/unit/test_email_rekey_reclaim.py \
  tests/unit/test_email_attachment_inline.py tests/unit/test_email_unsubscribe.py \
  tests/unit/test_email_folders.py tests/unit/test_email_deep_sync.py -v -rs
uv run ruff check apps/services/email_ingestion tests/unit/test_gmail_parse.py
```

The R8 tests must show PASSED, with 0 skips.

**As built (2026-10-05, branch `email-gmail-g2`).** The build follows items 1 to 11 and the rules
E5 to E12 of the audit. It adds no migration, no flag and no column. Production holds no Gmail
mailbox, so no live row changes.

- **One walk (items 1 and 2).** `_iter_gmail_parts` is the one walk of the MIME tree.
  `_collect_gmail_attachments` uses it too, with the same order and the same result.
  `_gmail_bodies` takes the first `text/plain` part and the first `text/html` part that hold data.
- **What the body walk skips.** It skips a part with a file name, a part with the disposition
  `attachment` and a `message/*` part, and each part below them. So the body of an attached mail
  never becomes the body of the mail that carries it.
- **The charset (item 3).** `_decode_gmail_part` decodes with the charset of the part. An unknown
  charset, or a codec that raises `UnicodeError`, falls back to UTF-8 (review round 1). It adds the
  base64 padding when Gmail sends none.
- **The headers (items 4 and 5).** `_parse_headers` keys each name in lower case.
  `_gmail_message_id` trims the value, and it keeps the angle brackets and the case.
- **The addresses (item 6, E6).** `_split_addresses` runs `getaddresses` with `strict=False` on the
  raw header, and `_decode_display_name` decodes each name after the split. A name that does not
  decode stays as it came. An entry with no `@` is left out (review round 1).
- **The sender.** When the first entry of `From` has no `@`, the old reading holds. The address is
  the text inside the angle brackets, and the name is the text before them (review round 1).
- **The folder (items 7 to 10).** `_gmail_folder_from_labels` returns `archive` when no system
  label matches. `list_messages` no longer writes `msg.folder`. The folder key `archive`, or a
  `canonical_override` of `archive`, sends `GMAIL_ARCHIVE_QUERY` with the query of the caller and
  no `labelIds`.
- **The sweep.** The code of the sweep and the deep sync did not change. Their comments said that
  the system labels come last to win the upsert. Now they say that the order sets no folder.

**The decisions of the audit, as built.**

- **E5.** `tests/unit/test_email_folders.py` expected `inbox` for a message with no system label.
  It now expects `archive`, ON PURPOSE. A comment on that assert and a comment in
  `_gmail_folder_from_labels` say so. Do not change it back.
- **E9.** From this merge, "Also in", the draft dedupe and the Sent-copy proof compare Gmail rows
  too, because a Gmail row now holds a Message-ID. Gmail stays hidden (D-EM-35), so this has no
  live effect. EM-G2 corrected the comments that said only Outlook stores it, and EM-G9 lost its
  comment item.
- **E11.** The paragraph "No backfill of old Gmail rows" above records the limit of the sweep.
- **E12.** From EM-G2 to EM-G8, "Load older" in a Gmail user-label view files each row as `inbox`
  or `archive`. So that folder view gets no new rows. This is accepted while Gmail stays hidden.
  EM-G8 item 5 fixes the tree, because the user labels then show in the label filter.
- **The clock flake.** `test_email_storage_limit.py::test_a_before_that_is_not_a_past_date_answers_400`
  computed "now + 5 minutes" at collection. On a slow run that date is in the past, and the test
  fails. A sentinel parameter now names the case, and the body of the test computes the date. No
  other line of that file changed.
- **Anchors.** Each anchor of the brief matched at `b0e09c23a`. The Outlook `internetMessageId`
  is at `outlook.py:1975` and its body at `:1960-1961`, a few lines below `:1971` and `:1955-1956`.

**The fences, as built.** `tests/unit/test_gmail_parse.py` holds 30 cases, and all are hermetic.
They drive `get_message` or `list_messages` through a fake HTTP client that answers from the
fixtures. The R8 case runs on a real Postgres as `acb_app_h3rls`.

| Test | Kind | What it proves |
|---|---|---|
| `test_a_nested_alternative_gives_text_and_html` (2 cases) | hermetic | Fixtures (a) and (c) give both bodies. (a) keeps its PDF, and (c) lists no inline image. |
| `test_single_part_html_fills_body_html` | hermetic | Fixture (b) fills `body_html`, and `body_text` is empty. |
| `test_the_charset_of_the_part_decodes_the_body` | hermetic | Fixture (g) decodes with no U+FFFD. No charset, or an unknown one, decodes as UTF-8. |
| `test_message_id_reads_in_any_case` (3 cases, E7) | hermetic | `Message-ID`, `Message-Id` and `message-id` each fill `internet_message_id`. |
| `test_message_id_keeps_the_form_of_graph` | hermetic | The value equals what the Outlook parse gives for the same Graph value. No header gives None. |
| `test_a_quoted_comma_is_one_address` | hermetic | Fixture (e): `"Doe, John"` and `"Rao, Asha"` stay one address each. |
| `test_an_encoded_display_name_decodes` (E6) | hermetic | Fixture (h): `Müller, Jürgen` and `Rao, Asha` decode after the split. |
| `test_an_address_header_with_no_address_gives_no_entry` | hermetic | A group with no member gives no address. |
| `test_no_system_label_files_as_archive` | hermetic | Fixture (f) files as `archive`. |
| `test_the_system_labels_decide_in_order` | hermetic | The order of D-EM-33. |
| `test_a_user_label_never_sets_the_folder` | hermetic | A user label named Archive goes to `categories` only. |
| `test_a_label_page_keeps_the_folder_of_the_parse` | hermetic | A user-label page files its rows as `inbox` and `archive`. |
| `test_the_archive_page_sends_a_query_and_no_label` | hermetic | The Archive page sends `GMAIL_ARCHIVE_QUERY` and the query of the caller. |
| `test_a_user_label_named_archive_does_not_replace_the_query` | hermetic | The arguments of `transport/folders.py` still send the query. |
| `test_a_codec_that_raises_decodes_as_utf8` (3 cases, round 1) | hermetic | `idna`, `punycode` and `undefined` decode as UTF-8, and `list_messages` keeps the mail. |
| `test_an_attached_mail_never_becomes_the_body` (round 1) | hermetic | Fixture (i): the inner body of an inline `message/rfc822` part is not the body. |
| `test_a_text_part_with_attachment_disposition_is_not_the_body` (round 1) | hermetic | A `text/plain` part with the disposition `attachment` is not the text body. |
| `test_a_bare_comma_in_from_keeps_the_real_sender` (2 cases, round 1) | hermetic | `Doe, John <…>` and `Müller, Jürgen <…>` keep the name and the real address. |
| `test_a_bare_comma_in_to_keeps_each_real_address` (2 cases, round 1) | hermetic | The same forms in `To` keep each real address and no fragment. |
| `test_a_loose_address_list_keeps_each_real_address` (3 cases, round 1) | hermetic | A trailing comma, a `;` and an empty element keep both addresses. |
| `test_each_fixture_is_a_gmail_message_with_no_real_address` | hermetic | Nine fixtures, the shape of `format=full`, and only RFC 2606 domains. |
| `test_two_parsed_gmail_fixtures_with_one_message_id_write_two_rows` | R8 | Two parses of fixture (a), each with its own Gmail id, keep two rows over two syncs through `_write_messages`. |

**Mutations, as run (2026-10-05).** Each mutation ran against `test_gmail_parse.py`,
`test_email_folders.py` and the R8 class of `test_email_rekey_reclaim.py`, on a real Postgres with
0 skips. A script then wrote back the original bytes of `gmail.py` and checked its SHA-256. Each
mutation turned a named test red.

| Id | Mutation (`gmail.py`) | Red tests |
|---|---|---|
| M1 | The body walk reads the top level only | `test_a_nested_alternative_gives_text_and_html`, both cases |
| M2 | `_parse_headers` keeps the case of each name | the `Message-ID` and `Message-Id` cases of `test_message_id_reads_in_any_case`, `test_message_id_keeps_the_form_of_graph`, the two address tests and the R8 case |
| M3 | The fallback is `inbox` | `test_no_system_label_files_as_archive`, `test_a_user_label_never_sets_the_folder`, the three page tests and `test_email_folders.py::test_gmail_folder_from_labels_priority` |
| M4 | `list_messages` writes `canonical_override` into the folder | `test_a_label_page_keeps_the_folder_of_the_parse` |
| M5 | The Archive branch never runs, so `labelIds` goes out | `test_the_archive_page_sends_a_query_and_no_label`, `test_a_user_label_named_archive_does_not_replace_the_query` |
| M6a | The Message-ID loses its angle brackets | `test_message_id_keeps_the_form_of_graph`, the three cases of `test_message_id_reads_in_any_case`, the R8 case |
| M6b | The Message-ID is lower-cased | the same five tests as M6a |
| M7 | The address list splits on each comma | `test_a_quoted_comma_is_one_address` |
| M8 | Each part decodes as UTF-8 only | `test_the_charset_of_the_part_decodes_the_body` |
| M9 | A single-part body goes into `body_text` | `test_single_part_html_fills_body_html` |
| E6 | The names decode before the split | `test_an_encoded_display_name_decodes` |
| MX1 | A `message/*` part is not a file | `test_an_attached_mail_never_becomes_the_body` |
| MX2 | The disposition `attachment` is not a file | `test_a_text_part_with_attachment_disposition_is_not_the_body` |
| MX3 | `From` takes its first entry with no check for `@` | both cases of `test_a_bare_comma_in_from_keeps_the_real_sender` |
| R1 | A list keeps an entry with no `@` | both cases of `test_a_bare_comma_in_to_keeps_each_real_address` |
| R2 | Only `LookupError` falls back to UTF-8 | the three cases of `test_a_codec_that_raises_decodes_as_utf8` |
| R3 | `getaddresses` is strict | the trailing-comma and `;` cases of `test_a_loose_address_list_keeps_each_real_address` |

Review round 1 ran all 17 rows again on the new code, against 34 tests with 0 skips. Each row was
red, and each restore matched the SHA-256 of `gmail.py`. M2 then turned 13 tests red, because the
new address tests read `From`, `To` and `Cc` too.

**Review round 1 (2026-10-05).** The verifier found one P2 regression and three P3 defects. Each fix
has a fence.

- **P2, a comma outside quotes in `From`.** `From: Doe, John <john@example.org>` gave the sender
  `Doe` with no address, and main gave the real one. The fix: when the first entry has no `@`,
  `_parse_from` uses the old reading. A list leaves out each entry with no `@`, so a fragment never
  becomes a recipient. Fences: `test_a_bare_comma_in_from_keeps_the_real_sender` and
  `test_a_bare_comma_in_to_keeps_each_real_address`, each in ASCII and in non-ASCII.
- **P3a, a codec that raises.** `charset=idna`, `punycode` or `undefined` raised `UnicodeError`.
  The parse failed, and `list_messages` then skipped the mail at each sync, so a sender could hide a
  mail. The fix: `_decode_gmail_part` falls back to UTF-8 on `UnicodeError` too. Fence:
  `test_a_codec_that_raises_decodes_as_utf8`.
- **P3b, a strict address parse.** On Python 3.12.12, `getaddresses` refuses a whole header for one
  defect, so `a@example.org, b@example.org,` gave no recipient. The fix: `_split_addresses` passes
  `strict=False`, and it falls back to the plain call on a Python with no such keyword. The lenient
  parser splits on `;` too. Fence: `test_a_loose_address_list_keeps_each_real_address`.
- **P3c, three rules with no fence.** The verifier removed the rule for a `message/*` part, the rule
  for the disposition `attachment` and the `From` fallback, and each mutation survived. Fixture (i)
  and two new tests now hold them (MX1 to MX3 above).

**Known limit EM-G2-f1 (P3d, on main before EM-G2).** Gmail can send a large text part with
`body.attachmentId` and no `body.data`. The body walk then reads nothing for that part, and the body
stays empty. The fix needs a call of `users.messages.attachments.get` for that part. A later slice
owns it, and it needs its own audit.

#### 12.3.3 EM-G3a — send and drafts

**Status.** ✅ MERGED #634 (2026-10-05), with review rounds 1 and 2. The
as-built notes, the fences, the mutation table and review round 1 are at the end of this section.

**Gate.** 🟢 AGENT-SAFE · R8, because the draft row is SQL. No migration.

**Order.** After EM-G4a, and after the orchestrator answers O-GM-2. The scope follows the
recommendation of O-GM-2. EM-G4a puts the rate-limit helper on the client, so each new call of
EM-G3a gets it with no change. The helper never sends `messages.send`, `drafts.create` or
`drafts.send` twice (§12.3.5.1). It sends a new POST once, unless EM-G3a adds it to the list of
POST actions that a second try cannot change.

**Size.** M. About 250 lines in `gmail.py` and `drafting.py`, with tests of about 250 lines.

**Scope.**

1. **One MIME builder (GM-10).** One private builder serves `send_message`, `create_draft` and
   `update_draft`. With `body_html`, it builds `multipart/alternative`, with the text part first.
2. **Attachments (GM-12).** With attachments, the body part sits inside `multipart/mixed`. Each
   attachment takes the type that the caller gives, else `mimetypes.guess_type` of its name, else
   `application/octet-stream`.
3. **The file name.** The builder writes it with `add_header(..., filename=...)`, so a name with a
   quote or a letter outside ASCII survives (RFC 2231).
4. **Threading (GM-11).** For a reply, the provider reads the parent with `format=metadata` and the
   headers `Message-ID` and `References`. It sets `In-Reply-To` and `References` from them, and it
   keeps `threadId`.
5. **The parent of a thread.** Sometimes the caller gives only `thread_id`. Then the parent is the
   newest message of the thread that is not a draft (`users.threads.get`, `format=metadata`). An
   update rebuilds the whole draft, so it sets the two headers again.
6. **The draft ids (GM-13, O-GM-2).** `create_draft` and `update_draft` return the message id of
   the draft (`message.id` in the answer of Gmail).
7. **The draft lookup.** `update_draft` and `send_draft` take a message id. They find the draft id
   through `users.drafts.list`, matched on `message.id`, and cache it for the life of the provider
   instance. A message id that is no draft raises a clear error, not an HTTP 400.
8. **The callers.** The save path (`automation/drafting.py:2126-2153`) writes the message id that
   `update_draft` returns into the same local row, by its local id. The sync then finds that row.
9. **The fallback.** The `NotImplementedError` fallback (`drafting.py:2144`, `:2274`, `:2279`)
   stays for IMAP. A draft made in Gmail web syncs as one row, and it now sends.

**The dispatch points (E-A1 to E-A5, 2026-10-05).** The audit said GO after EM-G2 merged, on five
points. The orchestrator decided E-A2, E-A3 and the trash rule of E-A5. They bind the build. The
anchors are at `241dddccf`.

- **E-A1, the send path.** Item 8 names the send of a signed draft too (`drafting.py:2264-2280`).
  The route signs the draft with `update_draft`. Then it gives `send_draft` the id that
  `update_draft` returns, and never the old id. Gmail gives the draft a new message id at each
  update, so the old id is no draft after the update. Fence:
  `test_a_signed_send_sends_the_id_that_the_update_returns` (hermetic).
- **E-A2, the collision rule (decided).** The sync can write the new message id as its own row
  before the save moves the local row. So the save makes two writes in ONE transaction. First it
  deletes the row of the same mailbox that holds the new provider id. That row is the copy that
  the sync made of this same draft. Then it moves the local row to the new id. The local row keeps
  its row id, because other tables point at it. Fence:
  `test_a_sync_copy_of_the_new_id_folds_into_the_local_row` (R8, as the role that owns no table).
- **E-A3, a failed parent read (decided).** When the read of the parent fails, the mail goes with
  no `In-Reply-To` and no `References`. The provider then writes one line to the log, with no
  subject and no address. A failed parent read never fails the send. Fence: `test_a_failed_parent_read_still_sends`.
- **E-A4, the tests that exist and change.** Each one changes only where this slice changes the
  behaviour.
  - `tests/unit/test_email_reply_threading.py:86-103`,
    `test_gmail_update_draft_keeps_thread_and_html`. It asserts `text/html`, and it gives an answer
    to `client.put` only. Item 1 makes the body `multipart/alternative`, and item 7 adds a read of
    `drafts.list`. The case keeps its intent: the thread id and the HTML body survive the update.
  - `tests/unit/test_email_reply_threading.py:34-83`, the four send cases. They give no answer to
    `client.get`, so the parent read of item 4 meets an `AsyncMock`. Each case gets an answer for
    `client.get`, and it keeps its asserts.
  - `tests/unit/test_gmail_rate_limits.py:337-338`, the `drafts.send` case of
    `test_a_send_is_not_retried_after_the_request_was_sent`. It sends the draft id `r-draft-1`.
    Item 7 makes `send_draft` take a message id and read `drafts.list` first. The case gets a
    `drafts.list` answer, and it passes a message id.
- **E-A5, three more fences, the R8 harness, the cache and the trash rule.**
  - Item 5: `test_a_reply_with_only_a_thread_id_reads_the_newest_message_that_is_no_draft`.
  - Item 7: `test_a_message_id_that_is_no_draft_raises_a_clear_error`, and
    `test_the_draft_lookup_reads_each_page` for a `drafts.list` of more than one page.
  - The R8 tests use the harness of `tests/unit/test_email_rekey_reclaim.py` and
    `tests/unit/test_email_duplicates.py`. That is the two-org catalog of
    `test_h3_rls_promotion_rehearsal` under FORCE RLS, with the role `acb_app_h3rls` (NOSUPERUSER,
    NOBYPASSRLS). The admin engine seeds the rows and reads them.
  - The cache of item 7 lives for one provider instance. `provider_session` builds one provider
    for each request, so the cache never spans two saves.
  - **The trash rule (decided).** `trash_message` on a Gmail draft finds the draft id through
    `drafts.list` and calls `drafts.delete`. That is "Discard draft" in Gmail. The local row is
    deleted. `drafts.delete` removes the draft for good, as Gmail itself does, and the scope
    `gmail.modify` of D-EM-31 allows it. Fence: `test_trash_of_a_draft_discards_it`.

**Non-goals.** No schedule-send. No migration. No change to the signature rules. Outlook drafts
change in one way only, the live fix EM-G3a-F2 of review round 1. A signed send now reaches each To
address, and it carries the Cc and the Bcc of the row.

**Fences (R7).** A new `tests/unit/test_gmail_send_and_drafts.py`.

- `test_send_has_a_text_part_and_an_html_part`
- `test_create_draft_has_a_text_part_and_an_html_part`
- `test_a_reply_sets_in_reply_to_and_references`
- `test_an_update_sets_in_reply_to_again`
- `test_an_attachment_keeps_its_mime_type`
- `test_a_file_name_outside_ascii_survives`
- `test_update_and_send_resolve_a_message_id_to_its_draft`
- The fences of E-A1, E-A3 and E-A5 above.
- R8: `test_a_draft_saved_here_is_one_row_after_the_sync`
- R8: `test_an_update_moves_the_row_to_the_new_message_id`
- R8: `test_a_sync_copy_of_the_new_id_folds_into_the_local_row` (E-A2)

`tests/unit/test_email_reply_threading.py` gains a case for the two headers.

**Mutations.** M1 puts back `MIMEText(body_text, "html")`, and the two MIME tests fail. M2 drops
`In-Reply-To`, and the reply test fails. M3 sends each attachment as `application/octet-stream`,
and the type test fails. M4 returns the draft id from `create_draft`, and the one-row R8 test
fails. M5 skips the row move on update, and the second R8 test fails.

The dispatch adds three. M6 makes the signed send pass the old id, and the E-A1 test fails. M7
makes a failed parent read fail the send, and the E-A3 test fails. M8 makes a collision insert a
second row, and the E-A2 test fails. Each run restores each file to its SHA-256.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_gmail_send_and_drafts.py tests/unit/test_email_reply_threading.py \
  tests/unit/test_outlook_drafts.py tests/unit/test_email_signature_placement.py \
  tests/unit/test_email_multi_inbox.py tests/unit/test_email_ai_context.py \
  tests/unit/test_gmail_parse.py tests/unit/test_gmail_rate_limits.py -v -rs
uv run ruff check apps/services/email_ingestion apps/services/gateway/gateway/routes/email \
  tests/unit/test_gmail_send_and_drafts.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

The R8 tests must show PASSED, with 0 skips. Run each other test that patches
`_upsert_local_draft`, or that calls `send_draft`, `update_draft` or `create_draft`, too.

**As built (2026-10-05, branch `email-gmail-g3a`).** The build follows items 1 to 9 and E-A1 to
E-A5. It adds no migration, no column and no flag. Production holds no Gmail mailbox, so no live
Gmail mailbox changes. One live Outlook change is in it: EM-G3a-F2 (review round 1). A signed send
of an Outlook draft now reaches each To address, and it carries the Cc and the Bcc of the row.

- **The builder.** `_build_gmail_raw` in `gmail.py` is the one builder. Each part is UTF-8. A
  header goes out only when the caller gives its value, so an update with no `Cc` writes no `Cc`.
- **The type of a file.** `application/octet-stream` counts as no type, because the send route
  gives it when the browser gives none. Then `mimetypes.guess_type` of the name decides. A type
  with parameters keeps its main type and its subtype only.
- **The parent.** `_reply_headers` reads `messages/{id}` or `threads/{id}` with `format=metadata`
  and the two `metadataHeaders`. The header read has no case, as in EM-G2. A parent with no
  Message-ID gives no header. `References` is the `References` of the parent, then its Message-ID.
- **The failed read (E-A3).** Each error of the read gives no header, a rate limit too. The line
  `gmail.parent_read_failed` names the kind, the id, the class of the error and the status.
- **The draft ids (O-GM-2).** `_remember_draft` keeps each pair from the answer of `drafts.create`
  and `drafts.update`, and an update drops the old message id. An answer with no draft id or no
  message id raises `ValueError`, so no row stores a wrong id. `_draft_id_for` reads
  `drafts.list` with 500 drafts on a page and 20 pages at most. It stops at the page that holds
  the id.
- **The cache.** A miss of `update_draft` or `send_draft` reads the list again. A miss of
  `trash_message` after one full read answers "no draft", so the trashes of one provider cost one
  read.
- **The trash rule (E-A5).** `trash_message` sends a draft to `drafts.delete`, and it adds the
  message id to `discarded_drafts`. A lookup that fails for a reason other than a rate limit
  trashes the message as before, and it logs `gmail.draft_lookup_failed`. `move_to_folder` calls
  `trash_message` for `trash`, so it discards a draft too.
- **The Discard button.** It calls `DELETE /email/messages/{id}` (`transport/messages.py`). That
  route deletes the local row of a discarded draft. Each other row goes to the folder `trash`, as
  before.
- **The save path (item 8, E-A2).** `_move_local_draft` (`drafting.py`) runs when the id that
  comes back differs from the id of the row. That is the Gmail update and the IMAP fallback.
  Outlook returns the same id, so its save sends no statement of the move. `_upsert_local_draft`
  keeps its key.
- **The send path (E-A1).** `send_draft_endpoint` sends the id that `update_draft` returns. It
  sends the old id only when the update returns none.
- **D-EM-31.** `drafts.delete` removes a draft for good, and D-EM-31 says that no code path
  deletes a message for good. Gmail itself puts no discarded draft in Trash. The scope
  `gmail.modify` allows `drafts.delete`.
- **Anchors at `241dddccf`.** `send_message` was at `gmail.py:712`, `create_draft` at `:767`,
  `update_draft` at `:816`, `send_draft` at `:875` and `trash_message` at `:901`. The save path
  was at `drafting.py:2126-2196`, and the send path at `:2264-2280`.

**The tests that changed (E-A4), as built.** The update case of
`test_email_reply_threading.py` answers `drafts.list` with the draft `r-1`, and it asserts the PUT
to `/users/me/drafts/r-1`. Its body is `multipart/alternative`, with the HTML part second. The
four send cases there use one client that answers `client.get` with a parent with no Message-ID.
The `drafts.send` case of `test_gmail_rate_limits.py` answers `drafts.list`, and it sends the
message id `m-draft-1`.

**The fences, as built.** `tests/unit/test_gmail_send_and_drafts.py` holds 49 cases after review
round 2. 40 are hermetic, on a fake Gmail on `httpx.MockTransport` that keeps its state. 9 are R8.

| Test | What it proves |
|---|---|
| `test_send_has_a_text_part_and_an_html_part` | The send body is `multipart/alternative`, text first, and each part keeps its own body. |
| `test_a_body_with_no_html_is_one_text_part` | A body with no HTML stays one `text/plain` part. |
| `test_create_draft_has_a_text_part_and_an_html_part` | `create_draft` uses the same builder. |
| `test_an_update_has_a_text_part_and_an_html_part` | `update_draft` uses the same builder. |
| `test_an_attachment_keeps_its_mime_type` (5 cases) | The given type, the given type over the name, the name over `application/octet-stream`, the name alone, and an unknown name. |
| `test_a_file_name_outside_ascii_survives` (3 cases) | A quote, an umlaut and CJK letters survive, through RFC 2231. |
| `test_a_reply_sets_in_reply_to_and_references` (2 cases) | The send and the draft read the parent with `format=metadata`, set both headers and keep `threadId`. |
| `test_a_reply_with_only_a_thread_id_reads_the_newest_message_that_is_no_draft` | Item 5. A newer draft in the thread is not the parent. |
| `test_an_update_sets_in_reply_to_again` | An update sets both headers again, from the thread. |
| `test_a_mail_that_is_no_reply_reads_no_parent` | A new mail makes no parent read and gets no header. |
| `test_a_failed_parent_read_still_sends` (4 cases) | E-A3: a 500, a 404, a rate limit and a thread 503. One line, with no subject and no address. |
| `test_create_and_update_return_the_message_id` | Item 6. |
| `test_update_and_send_resolve_a_message_id_to_its_draft` | Item 7. A draft with only a message id updates and sends, with one `drafts.list`. |
| `test_a_message_id_that_is_no_draft_raises_a_clear_error` | Item 7. `GmailDraftNotFound`, not an HTTP error, and no write. |
| `test_the_draft_lookup_reads_each_page` | Item 7. Three pages, and the cache serves the next lookup. |
| `test_the_lookup_after_an_update_drops_the_old_id` | After an update, the old id is no draft. |
| `test_trash_of_a_draft_discards_it` | E-A5. `drafts.delete` for a draft, `messages.trash` for a mail, one `drafts.list`. |
| `test_a_failed_draft_lookup_still_trashes_a_mail` | A failed `drafts.list` still trashes a mail, and logs one line. |
| `test_a_signed_send_sends_the_id_that_the_update_returns` | E-A1. The route signs, then sends the new message id. |
| `test_a_signed_send_of_a_provider_that_keeps_its_id_is_unchanged` | Outlook sends the same id as before. |
| `test_an_update_keeps_the_files_of_the_draft` | Review round 1, F1. An update keeps each file of the draft, and adds the given files. |
| `test_a_failed_file_read_fails_the_update` | F1. An update that cannot read the files writes nothing. |
| `test_a_draft_that_changed_in_gmail_answers_409` | Item 5 of review round 1. The send, with and with no signature, and the save answer 409. |
| `test_an_outlook_signed_send_patches_only_the_lists_the_row_holds` (2 cases) | Review round 2. A row with no Cc and no Bcc sends neither key. A row with both sends both lists. |
| `test_a_signed_gmail_send_keeps_the_bcc` | Review round 2. The signing `drafts.update` carries the Bcc of the row. |
| `test_the_imap_fallback_sends_each_to_cc_and_bcc` (2 cases) | Review round 2. The new mail of the IMAP fallback, signed and not signed, goes to each address of the row. |
| `test_an_attached_mail_stays_one_file_after_an_update` (2 cases) | Review round 2. A forwarded mail, with no name and as a named `.eml` that holds `inner.pdf`, stays one `message/rfc822` part, and no inner file comes to the top. |
| R8 `test_a_draft_saved_here_is_one_row_after_the_sync` | A saved draft and its sync copy are one row. |
| R8 `test_an_update_moves_the_row_to_the_new_message_id` | The row keeps its row id, and each request builds its own provider. |
| R8 `test_a_sync_copy_of_the_new_id_folds_into_the_local_row` | E-A2. A sync copy that commits before the move goes, and one row stays. |
| R8 `test_an_update_that_keeps_its_id_moves_nothing` | Outlook: no statement of the move reaches Postgres. |
| R8 `test_a_discarded_gmail_draft_leaves_no_row` | E-A5. The route deletes the row of a discarded draft, and a mail goes to `trash`. |
| R8 `test_an_outlook_draft_to_two_people_and_a_cc_sends_to_all_three` | F2. The real Outlook provider PATCHes two To addresses and one Cc, and uploads no file again. |
| R8 `test_a_signed_gmail_send_keeps_the_cc_and_the_file` | F1. The signing `drafts.update` carries the Cc of the row and the file of the draft. |
| R8 `test_a_failed_send_leaves_the_row_on_the_live_id` | F3. After a failed `drafts.send`, the row holds the live id, and a second Send works. |
| R8 `test_the_move_never_touches_another_mailbox` | F4. The same new id in another mailbox of the organization and in another organization stays. |

`test_email_reply_threading.py::test_gmail_reply_sets_in_reply_to_and_references` is the case for
the two headers there.

**Mutations, as run (2026-10-05, review round 2).** A script ran each mutation against
`test_gmail_send_and_drafts.py`, `test_email_reply_threading.py` and `test_gmail_rate_limits.py`,
on a real Postgres with 0 skips. It then wrote back the original bytes, and the SHA-256 of
`gmail.py`, `drafting.py` and `messages.py` matched each time. Each mutation turned a named test
red. The counts are from the run of review round 2, which ran each row again on the new code.

| Id | Mutation | Red tests |
|---|---|---|
| M1 | The builder puts back `MIMEText(body_text, "html")` | 11, with the three MIME tests and the update case of `test_email_reply_threading.py` |
| M2 | No `In-Reply-To` | 5, with both cases of the reply test and the new case of `test_email_reply_threading.py` |
| M3 | Each file goes as `application/octet-stream` | 8, with the 4 cases of the type test that name a type |
| M4 | `create_draft` returns the draft id | 9, with `test_create_and_update_return_the_message_id` and each R8 test of the Gmail save |
| M5 | The save skips the move | the R8 move, collision and two-mailbox tests |
| M6 | The signed send passes the old id | 4, with the E-A1 test, the F1 test and the F3 test |
| M7 | A failed parent read fails the send | the 4 cases of `test_a_failed_parent_read_still_sends` |
| M8 | The move keeps the sync copy and gives way to it, so a second row stays | the R8 collision test |
| M8a | The move keeps the sync copy, so the move meets the unique key | the R8 collision test |
| M9 | A draft goes to `messages.trash` | 3, with `test_trash_of_a_draft_discards_it` and the R8 discard test |
| M10 | The delete route keeps the row of a discarded draft | the R8 discard test |
| M11 | The parent of a thread can be a draft | the thread-only test and the update test |
| M12 | The lookup reads the first page only | `test_the_draft_lookup_reads_each_page` |
| R1 | The row stores the first To address only (F2) | the R8 Outlook test |
| R2a | The signing update drops the Cc (F1) | 3, with the R8 Gmail F1 test and the R8 Outlook test |
| R2b | The Gmail update drops the files of the draft (F1) | 5, with the R8 Gmail F1 test, `test_an_update_keeps_the_files_of_the_draft` and both cases of the attached-mail test |
| R3 | The move runs in the block of the route, so a failed send rolls it back (F3) | the R8 F3 test |
| V2 | `_DROP_SYNC_COPY_SQL` has no account bind (F4) | the R8 two-mailbox test |
| R5 | `GmailDraftNotFound` is not mapped to 409 | `test_a_draft_that_changed_in_gmail_answers_409` |
| X1 | An empty Cc goes out as an empty list (P3-1) | the no-Cc case of the Outlook list test |
| X2 | The signing update drops the Bcc (P3-2) | `test_a_signed_gmail_send_keeps_the_bcc` and the Cc-and-Bcc case of the Outlook list test |
| X6 | The IMAP fallback drops the Cc and the Bcc (P3-3) | both cases of `test_the_imap_fallback_sends_each_to_cc_and_bcc` |
| Y1 | The file read walks into an attached mail (P3-4) | both cases of `test_an_attached_mail_stays_one_file_after_an_update` |

**Review round 1 (2026-10-05).** An independent verifier passed the branch with two P1 and two P3
findings. It also confirmed that the branch does not change how Outlook drafts. Each fix has a
fence above.

- **F2, P1, a LIVE Outlook defect on main, fixed here.** The save stored the first To address of a
  draft only. The signed send read the row back and PATCHed `toRecipients` with it. So a draft to
  two people, sent with a signature, reached the first person only. Now `_upsert_local_draft`
  takes `to_addresses`, and `PUT /email/drafts` passes each To address. The ON CONFLICT key does
  not change. This changes live Outlook behaviour, and the change is correct.
- **F1, P1 for Gmail.** The signing update gave no Cc, no Bcc and no file, and Gmail replaces the
  whole draft. So the mail went out with no Cc and no file. Now the route passes each To, Cc and
  Bcc address of the row. Outlook PATCHes the Cc list and the Bcc list only when the row holds an
  address.
- **F1, the files.** Metorite keeps no bytes of a draft file. So the Gmail `update_draft` reads the
  files from the draft in Gmail (`_draft_files`), and it builds them in again. That binds each
  Gmail update, an autosave too, because each one replaces the whole draft. Outlook gets no file
  again, because its draft keeps its files.
- **F3, P3.** A `drafts.send` that failed after the signing update rolled back the route, and the
  row kept the old message id. Now `_commit_draft_move` moves the row in a block of its own, and
  that block commits before `drafts.send`. A commit inside the block of the route would end its
  tenant.
- **F4, P3, a fence gap.** No test failed when the account bind left `_DROP_SYNC_COPY_SQL`. The
  two-mailbox R8 test now fails.
- **Item 5.** `_draft_changed_upstream` answers 409 with "This draft changed in Gmail. Refresh and
  try again." on the save and on the send, for `GmailDraftNotFound`.
- **The IMAP fallback.** The fallback of the send now passes the Cc and the Bcc of the row too.

**Review round 2 (2026-10-05).** The re-verify passed review round 1 with six P3 findings and no
P0, P1 or P2. It confirmed the live Outlook fix: the PATCH carries each To, the Cc and the Bcc. An
empty list sends no key, so Outlook keeps its own, and nothing goes twice.

- **P3-1, P3-2 and P3-3, three fence gaps.** Three mutations survived round 1. X1 sends an empty
  Cc as an empty list. X2 drops the Bcc from the signing update. X6 drops the Cc and the Bcc from
  the IMAP fallback. Each one now turns a fence above red.
- **P3-4, data loss on the dark Gmail path.** `_draft_files` read `format=full` and walked into an
  attached mail. Gmail opens a `message/rfc822` part into its parts and gives no bytes for it. So
  an update lost a forwarded mail, or put its inner files at the top. Now `_draft_files` reads the
  draft once as a raw mail (`format=raw`), and the standard `iter_attachments` gives each file of
  the top level. An attached mail stays one file, with its name, else `attached.eml`. The builder
  puts a `message/*` file in as a mail part (`MIMEMessage`), not as base64.
- **The cost of the read.** The raw read takes the place of `messages.get` with `format=full` and
  each `attachments.get`. So an update of a draft with files now makes one read of the draft.
- **P3-5 and P3-6.** EM-G3a-f8 below records the cost of an autosave. The non-goals, the as-built
  notes and the WS-17 row name the one live Outlook change.

**Fixed defect EM-G3a-F2 (LIVE on Outlook before this branch).** A signed send of an Outlook draft
to more than one To address reached the first address only. Review round 1 fixes it, with the R8
fence `test_an_outlook_draft_to_two_people_and_a_cc_sends_to_all_three`.

**Known limit EM-G3a-f1.** A member edits a draft in Gmail web, then in Metorite before the next
sync. Gmail gave the draft a new message id, so the local row holds an old id. `update_draft` then
raises `GmailDraftNotFound`, and the route answers 409 since review round 1.

The sync writes the new id as a second row. The old row stays until the history of EM-G4b reports the delete of
the old id, and E-B2 deletes the row. No reconcile removes it, because EM-G5b leaves drafts
out (item 9, the second audit of 2026-10-05).

**Known limit EM-G3a-f2.** A sync can hold its copy of the new id in an open transaction while the
save moves its row. The move then waits for that transaction, and it fails on the unique key. The
draft then meets EM-G3a-f1. The window is the time between the answer of Gmail and the move.

**Known limit EM-G3a-f3.** Three paths keep the local row of a discarded Gmail draft in the folder
`trash`. They are the PATCH of a message to `trash`, the rule action `TRASH` and a bulk trash by
sender. Gmail holds no such draft, so the row stays in `trash`. The Discard button uses the delete
route, which deletes the row.

⚠️ **Corrected by the audit of EM-G5 (E-G5-10, 2026-10-05).** This text said that the row stays
"until a reconcile (EM-G5)". That is false. No reconcile reads a row in `trash`: both reads of
`reconcile.py` filter on `LOWER(folder) <> 'trash'` (`reconcile.py:45`, `:144`). So no reconcile
removes this row, and no slice owns it yet.

**Finding EM-G3a-f4 (on main before EM-G3a).** IMAP `create_draft` returns `draft-<folder>`, so
each IMAP draft that Metorite makes writes onto one shared row. Before EM-G3a, an IMAP save wrote
over that shared row. Now the move of item 8 deletes it. So the other draft leaves Metorite in both
cases, and the move adds no loss. A later slice owns the fix of the IMAP id, with its own audit.

**Known limit EM-G3a-f5.** A Gmail draft row from before EM-G3a holds a draft id, so its update and
its send raise `GmailDraftNotFound`. Production holds no Gmail mailbox, so no such row exists
there.

**Known limit EM-G3a-f6 (on main before EM-G3a).** The IMAP fallback of the send builds a new mail,
and it sends no file of the draft.

**Known limit EM-G3a-f7.** Each Gmail update reads each file of the draft and sends it again, and
an autosave is an update. An inline image (`cid:`) of a draft made in Gmail web is no file in that
read, so an update drops it.

**Known limit EM-G3a-f8, a precondition of customers.** ~~A precondition of EM-G10 and of the Gmail
go-live.~~ The amendment of D-EM-36 (§12.2, 2026-10-05) moved it, and EM-G3c owns it.

The re-verify measured one autosave of a Gmail draft with one 5 MB file. It made 4 Gmail calls: `drafts.list`,
`messages.get` with `format=full`, `attachments.get` and `drafts.update`. It sent 9.44 MB up and
took 6.99 MB down, and the composer autosaves after each pause of 1.2 seconds. Review round 2 puts
one `messages.get` with `format=raw` in the place of the second and the third call. The bytes stay
about the same.

A file read that fails gives a 500, and the `catch` of the autosave hides it from the member.
Before customers, EM-G3c must check the size limit of Google on a plain `drafts.update`. It must
also decide how often an autosave of a draft with files may run (§12.3.12).

#### 12.3.4 EM-G3b — a move to a user label, and the filter list

**Status.** ✅ MERGED (#645, 2026-10-05). The audit of 2026-10-05
gave GO-NARROWED. Its corrections are E-M1 to E-M13, E-F1 to E-F5 and E-V1 to E-V3, and the scope
below holds each one. The audit checked each anchor against the code on 2026-10-05. The as-built
notes, review round 1 and the mutation table are at the end of this section.

**Gate.** 🟢 AGENT-SAFE. No migration, no flag and no change to SQL text.

**Order.** After EM-G2 (#626). The orchestrator decided O-GM-1 on 2026-10-04 (§12.2).

**Size.** M. About 300 lines with tests. The audit changed it from S.

**Scope — the move.**

1. **The name of the target (E-M1, E-M2, E-M3).** `GmailProvider.move_to_folder`
   (`gmail.py:1506-1521`) resolves the name with `canonical_folder()`. So "Bin", "Deleted Items"
   and "Junk Email" reach the system branch. The move refuses `sent` and `drafts` with a
   `ValueError`. It also refuses a name that resolves to a reserved system label or a
   `CATEGORY_*` label. Without that rule, "Starred" stars the message and archives it.
2. **A move to a user label (GM-14, D-EM-33, E-M5).** For any other name, the move finds or
   creates the user label (`_ensure_label_id`, `gmail.py:1583-1621`). One `modify` call adds the
   label and removes `INBOX`, `TRASH` and `SPAM`. The move returns `None`, because Gmail keeps the
   id. The name goes to Google unchanged, also with a "/" in it.
3. **A failed label (E-M4).** When `_ensure_label_id` returns `None`, the move raises. A quiet
   return lets the caller store `archive` for a move that did not occur. Outlook raises in the
   same case (`outlook.py:687`).
4. **The folder after a move (E-M6, E-M7).** `BaseEmailProvider.folder_after_move(name)` returns
   the folder key that a move to `name` leaves. The base returns `canonical_folder(name)`.
5. **The Gmail folder after a move.** Gmail returns the key for inbox, archive, trash and junk. It
   returns `None` for sent and drafts, and `archive` for a user label.
6. **The helper (E-M6).** A module helper calls `folder_after_move`. It falls back to
   `canonical_folder(name)` when the provider is no `BaseEmailProvider`, because some fakes do not
   subclass the base (`base.py:439-440`).
7. **The rule move (`actions.py:439-440`, E-M12).** It stores the folder from the helper. After a
   Gmail label move it calls `mirror_label` (`actions.py:100-111`). So `categories` also matches
   the next parse.
8. **The PATCH of a message (`messages.py:795-797`, E-M8, E-M9).** The route builds the provider
   before it writes the folder. `_provider_for_message` makes no network call
   (`core.py:164-186`). The route stores the folder from the helper. When the helper gives
   `None`, the route answers 400 and writes nothing. The push stays best-effort.
9. **The case of a new name (E-M9).** The PATCH sends the name with `.strip()`, not `.lower()`
   (`messages.py:852`). So a new label or Outlook folder keeps its case, as a rule move does.
10. **The restore of Reply Zero (`replyzero.py:1063-1067`, E-M10).** It compares the folder of the
    row with the helper, not with `canonical_folder` of the rule label. So the restore finds a
    Gmail rule move.
11. **The no-op log (`actions.py:445-448`, E-M11).** `email.move_folder_noop` fires only for a
    provider that rekeys its ids (`REKEYS_MESSAGE_IDS`), or that keeps the move of the base. IMAP
    has the one real no-op left, so it still logs. The docstring at `base.py:557` says that IMAP
    does COPY and EXPUNGE. That is false, and the slice corrects it.

**Scope — the filter list.**

12. **The read (GM-15, E-F1).** Gmail `list_filters` reads `users.settings.filters.list`. Google
    answers with the key `filter`, which is singular. It answers `{}` for a mailbox with no filter.
    Each filter maps to `{id, name, enabled, from_addresses, summary}`, as `outlook.py:1177-1221`
    does.
13. **The name and the senders (E-F4).** A Gmail filter is always enabled. The name is
    `From <from>`, else the query, else the subject. `from_addresses` holds `criteria.from` as
    one whole string, because it is a Gmail query such as "a OR b".
14. **The summary (E-F2, E-F3).** The summary is a list of tokens in this order:
    `subject contains “x”`, `to contains “x”`, `matches “q”`, `has attachment`, `label: <name>`,
    `skip inbox`, `mark read`, `star`, `forward` and `trash`. A label id maps to its name through
    `_label_name_id_map()` (`gmail.py:1551-1581`). An unknown id shows as it is. The map ignores
    each other action.
15. **The errors (E-F5).** A plain 403 gives `[]`. `GmailRateLimited` passes up, because
    `rules.py:724-732` already answers `provider_rules_supported: false` for it.

**Non-goals.** No edit of a Gmail filter. No change to `create_filter`. No parent label for a name
with a "/".

**Known limit EM-G3b-f1.** After a label move, a message in `SENT` or `DRAFT` keeps that folder at
Gmail, because `SENT` ranks before `INBOX` (`gmail.py:55-61`). The row reads `archive` until the
next sync files it again.

**Known limit EM-G3b-f2 (E-M13).** The undo of a rule move (`runner.py:611-619`) adds `INBOX`
again. The user label stays on the Gmail message.

**Known limit EM-G3b-f3 (E-M10).** On Gmail, the restore of Reply Zero cannot tell a rule move
from a later archive by the member. Both leave the row in `archive`.

**Fences (R7).** A new `tests/unit/test_gmail_move_and_filters.py`.

- `test_a_move_to_a_label_adds_it_and_removes_inbox`
- `test_a_move_to_a_new_label_creates_it_first`
- `test_the_folder_after_a_gmail_label_move_is_archive`
- `test_a_rule_move_stores_the_folder_of_the_provider`
- `test_list_filters_maps_criteria_and_actions`
- `test_list_filters_answers_empty_on_a_403`
- `test_a_gmail_move_to_an_alias_hits_the_system_branch`
- `test_a_gmail_move_to_sent_or_drafts_is_refused`
- `test_a_move_to_a_system_label_name_is_refused`
- `test_a_failed_label_create_raises`
- `test_a_move_from_trash_to_a_label_removes_trash`
- `test_a_label_create_409_resolves_by_reread`
- `test_a_patch_move_stores_the_folder_of_the_provider`
- `test_a_patch_move_keeps_the_case_of_the_name`
- `test_the_restore_finds_a_gmail_label_move`
- `test_the_noop_log_still_fires_for_imap`

**Mutations.** M1 drops `removeLabelIds`, and the move test fails. M2 makes Gmail return the label
name from `folder_after_move`, and the folder test fails. M3 makes `list_filters` return `[]`, and
the map test fails. The as-built notes name one mutation for each new fence.

**R8 (E-V1).** The slice changes no SQL text, so R8 does not bind it. An implementer that changes
SQL text adds a test on a real database.

**Verify with (E-V2).**

```bash
uv run pytest tests/unit/test_gmail_move_and_filters.py tests/unit/test_email_unsubscribe.py \
  tests/unit/test_email_provider_rules_listing.py tests/unit/test_email_labels_upstream.py \
  tests/unit/test_email_two_way_sync.py tests/unit/test_email_thread_single_classification.py \
  tests/unit/test_email_rule_action_failures.py tests/unit/test_email_rules_engine.py \
  tests/unit/test_email_rekey_reclaim.py tests/unit/test_gmail_send_and_drafts.py -v -rs
uv run ruff check apps/services/email_ingestion apps/services/gateway/gateway/routes/email \
  tests/unit/test_gmail_move_and_filters.py
```

**As built (2026-10-05, branch `email-gmail-g3b`).** The build follows items 1 to 15 and the
corrections of the audit. It adds no migration, no flag, no column and no SQL text. Production
holds no Gmail mailbox, so no live mailbox changes.

- **The move.** `GmailProvider.move_to_folder` reads the name with `canonical_folder`. Sent and
  drafts raise `ValueError` before any request. Each other name that is no system folder goes to
  `_user_label_id_for_move`.
- **The system labels (E-M2, E-M3).** `_is_system_label_name` refuses a name in `_GMAIL_RESERVED`
  and a name that starts with `CATEGORY_`, before any request. After `_ensure_label_id`, the move
  also checks the id against the user labels. So a system label that Google adds later raises too.
- **A failed label (E-M4).** When `_ensure_label_id` gives no id, the move raises `ValueError`.
  `create_folder` raises the same class for the same case.
- **The folder after a move.** Gmail also gives `None` for a system label name, because the move
  refuses it. Item 5 names only sent and drafts, so this note makes item 5 complete.
- **The helper (E-M6).** `local_folder_after_move` sits at the end of `base.py`. It calls the
  method only on a `BaseEmailProvider`, so an `AsyncMock` fake makes no coroutine.
- **The rule move (E-M12).** `_move_folder_action` in `actions.py` now holds the `MOVE_FOLDER`
  branch. It asks the helper before the move, and a `None` answer raises before any provider call.
  It calls `mirror_label` when the folder of the provider differs from `canonical_folder` of the
  name. Only a Gmail user label does that. The complexity of `_apply_rule_actions` falls from 33
  to 31.
- **The PATCH (E-M8, E-M9).** `_folder_for_move` in `messages.py` builds the provider, asks the
  helper and answers 400 for `None`. A provider that fails to build gives `canonical_folder(name)`.
  The push then builds it again and logs the failure, as before. The push reuses the provider of
  the folder step, and the complexity of `update_message` stays at 18.
- **The no-op log (E-M11).** `_move_can_be_a_noop` is true when `REKEYS_MESSAGE_IDS` is `True`,
  or when the class of the provider keeps `BaseEmailProvider.move_to_folder`. A test fake logs
  nothing now.
- **The filter list.** `list_filters` reads the labels only when a filter adds a user label. A
  label id that is a system label name is no user label. The label token joins the names with
  ", ", as Outlook does.
- **The SQL (E-V1).** No statement changes its text. Only the values of `:f` and `:folder`
  change.

**Review round 1 (2026-10-05).** The verifier passed the slice, and the reviewer approved it. Both
found P2 items, and this round closes five of them. No SQL text changes.

1. **A blank name (reviewer P2-2, verifier P2-1).** `_move_target` in `messages.py` strips the
   name once. A name that is empty after the strip answers 400 before any read or write, for each
   provider. The helper and the provider get the same stripped string.
2. **Archive and Junk from Trash (reviewer P2-3).** A Gmail move to Archive removes `INBOX`,
   `TRASH` and `SPAM` (`_LABEL_MOVE_REMOVES`). A move to Junk adds `SPAM` and removes `INBOX` and
   `TRASH` (`_JUNK_MOVE_REMOVES`). Gmail ranks `TRASH` first, so the old request left the message
   in Trash. The Inbox and Trash moves do not change. The `ARCHIVE` and `MARK_SPAM` rule actions
   and the cold sender archive now match their rows at Gmail. The batch archive of `bulk_apply`
   (`_BULK_LABEL_OPS`) still removes `INBOX` only, and this round does not change it.
3. **The reserved guard of the mirror (verifier P2-2).** `_writable_label` in `actions.py` is the
   one check of an empty or reserved label. `push_label` and the mirror of a rule move both call
   it. So a rule move to "Uncategorized" writes no mirror.
4. **Known limit EM-G3b-f7 (reviewer P2-1)** is new, below. §12.3.12 lists it.
5. **The mutation table (verifier P2-3).** The commit of the build said 24 mutations, and the
   table held 23 rows. The table now states its count, with the rows of this round.

**The fences, as built.** `tests/unit/test_gmail_move_and_filters.py` holds 21 fences and 51
cases, and all are hermetic. Review round 1 added five fences and nine cases. A fake Gmail on
`httpx.MockTransport` keeps the labels of the mailbox and of each message.

| Test | What it proves |
|---|---|
| `test_a_move_to_a_label_adds_it_and_removes_inbox` | One `modify` adds the user label and removes `INBOX`, `TRASH` and `SPAM`. The parse then files the message as `archive`. |
| `test_a_move_to_a_new_label_creates_it_first` | A new name becomes a label before the `modify`, with its case and its "/". |
| `test_the_folder_after_a_gmail_label_move_is_archive` (14 cases) | Gmail, the base and the helper give the folder of each name. The helper calls nothing on an `AsyncMock` fake or on a plain fake. |
| `test_a_rule_move_stores_the_folder_of_the_provider` | The rule move stores `archive` and mirrors the label. A fake keeps the canonical key. A refused move makes no call and no write. |
| `test_list_filters_maps_criteria_and_actions` | Three filters map to their name, their senders and their tokens, in order. An unknown label id shows as it is. A mailbox with no filter gives `[]` with no label read. |
| `test_list_filters_answers_empty_on_a_403` | A plain 403 gives `[]` after one try. A 429 past the tries raises `GmailRateLimited`. |
| `test_a_gmail_move_to_an_alias_hits_the_system_branch` (6 cases) | Each alias sends the request of its system folder and reads no label. |
| `test_a_gmail_move_to_sent_or_drafts_is_refused` (4 cases) | Each name raises `ValueError` before any request. |
| `test_a_move_to_a_system_label_name_is_refused` (6 cases) | A reserved name raises before any request. A system label outside the reserved set raises with no create and no `modify`. |
| `test_a_failed_label_create_raises` | A create that Gmail refuses raises, and no `modify` goes out. |
| `test_a_move_from_trash_to_a_label_removes_trash` | A message in Trash or Spam leaves for `archive`. |
| `test_a_label_create_409_resolves_by_reread` | A 409 reads the labels again, and the move uses that label. |
| `test_a_patch_move_stores_the_folder_of_the_provider` | The route builds the provider after the ownership read only, and stores `archive`. "Sent" answers 400 with no write and no request. A provider that fails to build stores the canonical key. |
| `test_a_patch_move_keeps_the_case_of_the_name` | The provider gets "Cold Email", and a new Gmail label keeps that case. |
| `test_the_restore_finds_a_gmail_label_move` | The restore moves the `archive` row of a rule label back to the Inbox. A refused rule label matches no row. |
| `test_the_noop_log_still_fires_for_imap` | IMAP and a provider that re-keys log the no-op. A Gmail label move logs nothing. |
| `test_a_patch_move_to_a_blank_name_is_refused` (4 cases) | Round 1. For an Outlook provider and for Gmail, `""` and `"   "` answer 400. No statement runs, and the provider gets no call. |
| `test_a_patch_with_no_folder_is_no_move` | Round 1. A PATCH with `is_read` only writes the flag and no folder, and calls no move. |
| `test_a_gmail_move_from_trash_to_archive_removes_trash` | Round 1. Archive removes `INBOX`, `TRASH` and `SPAM`, and a message from Trash or Spam reaches `archive`. |
| `test_a_gmail_move_from_trash_to_junk_removes_trash` | Round 1. Junk removes `INBOX` and `TRASH`, and a message from Trash reaches `junk`. The Inbox move keeps its request. |
| `test_a_gmail_rule_move_to_a_reserved_name_writes_no_mirror` (2 cases) | Round 1. A Gmail rule move to "Uncategorized" stores `archive` and writes no mirror. |

**Mutations, as run (2026-10-05).** A script replaced one anchor for each mutation and ran the
fence file. Then it restored the file with `git checkout`. Each time `git diff` was clean and the
SHA-256 matched. Each mutation turned its fence red. The table holds 29 mutations: 23 of the build
(M1 to M17) and 6 of review round 1 (M18 to M21).

| Id | Mutation | Red tests |
|---|---|---|
| M1 | The label move drops `removeLabelIds` | 5 tests, with the label move test |
| M2 | Gmail gives the label name from `folder_after_move` | 5 cases, with the folder test |
| M3 | `list_filters` returns `[]` | the map test |
| M4 | The move sends a new label name in lower case | 3 tests, with the new label test |
| M5 | The rule move stores `canonical_folder` of the name again | the rule move test |
| M5b | The rule move writes no mirror | the rule move test |
| M6 | A plain 403 raises | the 403 test |
| M6b | `list_filters` swallows `GmailRateLimited` | the 403 test |
| M7 | The move reads the name with `.lower()` | 5 cases, with the alias test |
| M8 | The move does not refuse sent and drafts | 2 cases of the sent and drafts test |
| M9 | The move skips the check of a system label name | the 6 cases of the system label test |
| M9b | The move skips the check against the user labels | the 6 cases of the system label test |
| M10 | A failed label create returns with no error | the failed create test |
| M11 | The label move removes `INBOX` only | 5 tests, with the Trash test |
| M12 | A failed create reads no labels again | the 409 test |
| M13 | The PATCH stores the name of the request | 2 tests, with the PATCH folder test |
| M13b | The PATCH does not refuse a refused move | the PATCH folder test |
| M13c | A provider that fails to build fails the PATCH | the PATCH folder test |
| M14 | The PATCH sends the name with `.lower()` | the case test |
| M15 | The restore compares with `canonical_folder` of the rule label | the restore test |
| M16 | The no-op log fires only for a provider that re-keys | the IMAP test |
| M16b | The no-op log fires for each provider, Gmail too | the IMAP test |
| M17 | The helper calls `folder_after_move` on any provider | 17 cases, with the folder test |
| M18 | `_move_target` lets a blank name through | the 4 cases of the blank name test |
| M18b | `_move_target` reads no `folder` as a blank name | the test of a PATCH with no folder |
| M18c | The push sends the name of the request, not the stripped name | the case test |
| M19 | The Archive move removes `INBOX` only | the Archive test, and the Archive case of the alias test |
| M20 | The Junk move removes `INBOX` only | the Junk test, and 2 cases of the alias test |
| M21 | The mirror of a rule move skips `_writable_label` | the 2 cases of the reserved name test |

**Known limit EM-G3b-f4 (found in the build).** A PATCH move to a Gmail user label puts the label
on the message in Gmail, and the row stores `archive`. The PATCH writes no mirror into
`categories`, so the chip shows after the next sync. Item 8 does not ask for the mirror.

**Known limit EM-G3b-f5.** The mirror of a rule move writes the name of the rule. When Gmail holds
that label in another case, the next parse writes the name that Gmail holds.

**Known limit EM-G3b-f6.** `canonical_folder` reads an empty name as `inbox`, so a Gmail move with
an empty name now goes to the Inbox. Before the slice, Gmail did nothing for it. Since review round
1, the PATCH refuses a blank name with a 400. A rule action with a label of spaces still reads it
as `inbox`, because `_apply_rule_actions` checks only that the label is not empty.

**Known limit EM-G3b-f7 (review round 1, reviewer P2-1).** `DAMAGED_CONVERSATION_THREADS_SQL`
(`replyzero.py:211-212`) still compares `LOWER(TRIM(ea.label))` with the folder of the row. The
live restore now asks the helper (`replyzero.py:1068`). A Gmail rule move to a custom label leaves
the row in `archive`, so the SQL finds no match for it. So for a Gmail mailbox with a custom
`MOVE_FOLDER` rule on a `NEEDS_REPLY` thread, the health metric counts 0. And
`scripts/repair_conversation_threads.py` skips the thread.

Only a custom Gmail rule hits it, because each Gmail preset uses `LABEL`
(`rules.py::_actions_for_preset`). The repair changes SQL text, so it needs an R8 test on a real
database. A later slice owns it, before Gmail goes live. §12.3.12 lists it under "Before
customers".

#### 12.3.5 EM-G4 — the history cursor, in two slices

**The split (E-B4, 2026-10-05).** The audit of EM-G4 found it too large for one PR, and the
orchestrator accepted its split. EM-G4a is the rate-limit helper and the record of a failed fetch,
items 8 to 10. EM-G4b is the cursor and its recovery after a 404, items 1 to 7 and 11. The recovery
stays with the cursor. Each item keeps its number, so "EM-G4 item 4" still names one item.

**The order.** EM-G4a goes first. It puts the helper at the client seam, so EM-G3a and EM-G4b get
it with no change. EM-G3a and EM-G4b then go in any order.

##### 12.3.5.1 EM-G4a — the rate limits and the fetch record

**Status.** ✅ MERGED #629 (2026-10-05). The as-built notes and
the mutation table are at the end of this section.

**Gate.** 🟢 AGENT-SAFE. No SQL, no migration, no column and no flag. Gmail stays dark. Production
holds no Gmail mailbox, and D-EM-35 keeps the Gmail connect hidden on a box with no Google app.

**Order.** After EM-G2, which merged as #626. Before EM-G3a and EM-G4b.

**Size.** S. About 120 lines in `gmail.py`, with tests of about 300 lines.

**Scope.**

8. **Rate limits (GM-9).** One helper wraps each Gmail call. A 429, or a 403 with the reason
   `rateLimitExceeded` or `userRateLimitExceeded`, waits for `Retry-After` or a back-off.
9. **The limit of tries.** The helper tries 3 times, then raises a typed error. The scheduler
   counts that error as a failed sync, backs off, and keeps the cursor.
10. **No silent skip.** A fetch that still fails logs `gmail.fetch_failed` with the message id. It
    goes into `SyncResult.errors` on the history path.

**The rules of the helper (the dispatch of 2026-10-05).**

- **One seam.** The helper sits at the client seam, `_get_client` in `gmail.py`, beside
  `RefreshingBearer`. Each call through the client gets it, and no call site changes.
- **A 401 still refreshes once.** The helper wraps the flow of `RefreshingBearer`. A rate limit and
  a 401 in one call cost one refresh.
- **A plain 403 is a refusal.** A 403 with any other reason, or with no reason, goes back to the
  caller at once. A second try cannot help a missing scope or a policy.
- **A 5xx is not a rate limit.** Items 8 to 10 name only the 429 and the two reasons of a 403. So a
  5xx goes back to the caller, as it does today.
- **No send goes out twice.** Each answer of Google proves that the request reached Google. So the
  helper sends a request again only when a second try cannot change the mailbox. That is a GET, a
  PUT or a DELETE, or a POST to `modify`, `batchModify`, `trash` or `untrash`. Each other POST goes
  once, and that includes `messages.send`, `drafts.create` and `drafts.send`.
- **The wait has a bound.** One wait is 30 seconds at most. A `Retry-After` that asks more ends the
  tries at once, because a shorter wait gets one more refusal. The waits of one client add up to 60
  seconds at most. One provider serves one sync cycle, so a cycle waits 60 seconds at most for rate
  limits.
- **A rate limit is not a skipped fetch.** A fetch whose tries are spent raises, and the sync fails.
  So the history path never moves the cursor past a message that it did not read.
- **The record names no content.** `gmail.fetch_failed` names the message id, the class of the
  error and the HTTP status. It never names a subject or an address.
- **Two calls use their own client.** The `authenticate` probe and the token post do not go
  through `_get_client`, so the helper does not wrap them. A probe that Google refuses for a rate
  limit fails the cycle, and the loop backs off.

**Non-goals.** No cursor (EM-G4b). No fetch in parallel (EM-G5). No change to Outlook or IMAP.

**Fences (R7).** A new `tests/unit/test_gmail_rate_limits.py`, with an `httpx.MockTransport` that
answers as Gmail does. Each test drives the real `_get_client`, and no test sleeps.

- `test_get_client_puts_the_helper_on_each_call`
- `test_a_429_waits_for_retry_after_and_succeeds`
- `test_a_403_with_a_rate_limit_reason_retries`
- `test_a_plain_403_is_not_retried`
- `test_a_5xx_is_not_retried`
- `test_the_tries_are_bounded_and_end_in_a_typed_error`
- `test_a_retry_after_past_the_cap_ends_the_tries`
- `test_the_total_wait_of_a_client_is_bounded`
- `test_a_send_is_not_retried_after_the_request_was_sent`
- `test_a_post_that_sets_a_state_retries`
- `test_the_401_refresh_still_runs_once`
- `test_a_failed_fetch_is_recorded_and_the_other_messages_parse`
- `test_a_failed_fetch_on_the_history_path_goes_into_errors`
- `test_the_sweep_puts_the_fetch_records_of_its_call_into_errors`
- `test_a_rate_limited_fetch_fails_the_page`
- `test_a_rate_limit_past_the_tries_fails_the_sync`

The fences of EM-G4 named `test_a_429_backs_off_and_keeps_the_cursor` and
`test_a_failed_fetch_leaves_a_record`. The tests above take their place. The cursor stays because
the sync raises, so phase (d) never runs. The R8 test of EM-G4b proves the cursor write.

**Mutations.** Each one must turn a named test red. M5 comes from the list of EM-G4, and the
dispatch added M8. M9 to M17 cover the rules above. EM-G4b keeps M1 to M4, M6 and M7.

| Id | Mutation | The test that fails |
|---|---|---|
| M5 | Remove the back-off, so a 429 goes back at once | the 429 test |
| M8 | Retry a plain 403 | the plain-403 test |
| M9 | Remove the limit of tries | the tries test |
| M10 | Remove the wait budget of the client | the total-wait test |
| M11 | Send each POST again | the send test |
| M12 | Put back the silent skip | the fetch-record test |
| M13 | Record and skip a fetch whose tries are spent | the rate-limited fetch test |
| M14 | Swallow a rate limit in the label loop of the sweep | the failed-sync test |
| M15 | `_get_client` uses the plain `RefreshingBearer` | the seam test |
| M16 | Ignore `Retry-After` | the 429 test |
| M17 | Wait for a `Retry-After` past the cap | the cap test |

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_gmail_rate_limits.py tests/unit/test_gmail_parse.py \
  tests/unit/test_email_provider_401_retry.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_email_import_batches.py tests/unit/test_email_folders.py -v -rs
uv run ruff check apps/services/email_ingestion tests/unit/test_gmail_rate_limits.py
```

The run must show 0 skips. `test_email_provider_401_retry.py` holds R8 cases, so the database
must be up.

**As built (2026-10-05, branch `email-gmail-g4a`).** The build follows items 8 to 10 and the rules
above. It adds no migration, no flag, no column and no SQL. Production holds no Gmail mailbox, so
no live mailbox changes.

- **The seam.** `GmailBearer` is a subclass of `RefreshingBearer`, and `_get_client` sets it. So
  the fence of EM-T4c, `isinstance(client.auth, RefreshingBearer)`, still holds. Each try runs the
  flow of the parent class, so its one refresh on a 401 does not change.
- **What is a rate limit.** `_is_rate_limit` is true for a 429. It is true for a 403 when one
  `error.errors[].reason` is one of the two names. It reads the body of a 403 only.
- **What goes again.** `_repeatable` is true for GET, HEAD, PUT and DELETE. It is true for a POST
  whose path ends in `/modify`, `/batchModify`, `/trash` or `/untrash`. Each other POST goes once.
- **The wait.** `Retry-After` sets the wait, in seconds or as an HTTP date. With no value, the
  back-off is 1 second, then 2 seconds, and each gets up to 1 second more at random.
- **The bounds.** `GMAIL_MAX_TRIES` is 3, `GMAIL_MAX_WAIT_SECS` is 30 and
  `GMAIL_WAIT_BUDGET_SECS` is 60. A wait past either bound ends the tries at once. So one request
  waits 60 seconds at most, and one client waits 60 seconds at most in all.
- **The typed error.** `GmailRateLimited` is an `httpx.HTTPStatusError`, so each caller that
  handles an HTTP failure handles it too. Its message names the method and the path, and no query.
- **The logs.** `gmail.rate_limited` logs each wait, and `gmail.rate_limit_gave_up` logs the end of
  the tries. Both name the method, the path and the status only.
- **The fetch record.** `_record_fetch_failure` logs `gmail.fetch_failed` with the message id, the
  class of the error and the status. It adds the record to `fetch_failures` on the provider. The
  history path puts the record into `SyncResult.errors`, as item 10 says. The sweep with no cursor
  and the deep sync put the records of their own call there too.
- **A rate limit fails the sync.** `list_messages` raises a `GmailRateLimited` again. Each loop of
  `sync_messages` that skips a failed label calls `_raise_rate_limit` first. One named helper keeps
  the complexity of `sync_messages` at 25, the value on main.
- **The size.** The build is larger than its estimate of S. It changes 240 lines of `gmail.py`,
  and most of them are comments. The new test file has 582 lines.
- **Anchors.** At `ba723e0f1`, `_get_client` is at `gmail.py:326-337`, not at `:185-196`, because
  EM-G2 moved it. `RefreshingBearer` is at `base.py:51`.

**The fences, as built.** `tests/unit/test_gmail_rate_limits.py` holds 36 cases, and all are
hermetic. A fake Gmail on `httpx.MockTransport` answers the client, the `authenticate` probe and
the token post.

| Test | What it proves |
|---|---|
| `test_get_client_puts_the_helper_on_each_call` | The client of `_get_client` uses `GmailBearer`, which is still a `RefreshingBearer` of the provider. |
| `test_a_429_waits_for_retry_after_and_succeeds` (2 cases) | `Retry-After` in seconds and as an HTTP date sets the one wait, and the second try succeeds. |
| `test_a_403_with_a_rate_limit_reason_retries` (2 cases) | Each of the two reasons gets a back-off from 1 to 2 seconds and a second try. |
| `test_a_plain_403_is_not_retried` (5 cases) | A scope, a daily limit, a policy, no reason and a body that is not JSON each go back after one try. |
| `test_a_5xx_is_not_retried` (2 cases) | A 500 and a 503 go back after one try, with no wait. |
| `test_the_tries_are_bounded_and_end_in_a_typed_error` | 3 tries and 2 waits, then `GmailRateLimited` with no query in its message. |
| `test_a_retry_after_past_the_cap_ends_the_tries` | A `Retry-After` of 31 seconds ends the tries after one try. |
| `test_the_total_wait_of_a_client_is_bounded` | Four calls wait 50 seconds in all. Each call past the budget stops at its first refusal. |
| `test_a_send_is_not_retried_after_the_request_was_sent` (3 cases) | `messages.send`, `drafts.create` and `drafts.send` each go once. |
| `test_a_post_that_sets_a_state_retries` (3 cases) | `modify`, `trash` and `batchModify` get a second try. |
| `test_the_401_refresh_still_runs_once` (2 cases) | A 429 and a 401 in either order cost one refresh. |
| `test_a_failed_fetch_is_recorded_and_the_other_messages_parse` (3 cases) | A 500, a 404 and a failed parse each leave one record and one log line, with no subject and no address. |
| `test_a_failed_fetch_on_the_history_path_goes_into_errors` | The history path puts the record into `SyncResult.errors`. |
| `test_the_sweep_puts_the_fetch_records_of_its_call_into_errors` | The sweep returns the records of its own call, and a later call starts empty. |
| `test_a_rate_limited_fetch_fails_the_page` | A fetch whose tries are spent raises, and it leaves no record. |
| `test_a_rate_limit_past_the_tries_fails_the_sync` (7 cases) | Each loop of `sync_messages` raises the rate limit: the label list, a user label and a system label of the sweep and of the deep sync, and the history fetch. |

**Mutations, as run (2026-10-05).** A script ran each mutation against
`test_gmail_rate_limits.py`. It then wrote back the original bytes of `gmail.py`, and the SHA-256
matched each time. Each mutation turned a named test red.

| Id | Mutation (`gmail.py`) | Red tests |
|---|---|---|
| M5 | A 429 is no rate limit | 18 tests, with both cases of the 429 test |
| M8 | Each 403 is a rate limit | the 5 cases of `test_a_plain_403_is_not_retried` |
| M9 | No limit of tries | `test_the_tries_are_bounded_and_end_in_a_typed_error`, the page test and the 7 cases of the failed-sync test |
| M10 | No wait budget | `test_the_total_wait_of_a_client_is_bounded` |
| M11 | Each POST goes again | the 3 cases of `test_a_send_is_not_retried_after_the_request_was_sent` |
| M12 | `list_messages` skips a failed fetch with no record | the 3 cases of the fetch-record test, and the sweep-record test |
| M12b | The history path drops the record | `test_a_failed_fetch_on_the_history_path_goes_into_errors` |
| M12c | The sweep returns no record | `test_the_sweep_puts_the_fetch_records_of_its_call_into_errors` |
| M13 | `list_messages` records and skips a fetch whose tries are spent | `test_a_rate_limited_fetch_fails_the_page` |
| M14a to M14g | One loop of `sync_messages` swallows the rate limit, for each of the 7 loops | the matching case of `test_a_rate_limit_past_the_tries_fails_the_sync`, one for each |
| M15 | `_get_client` uses the plain `RefreshingBearer` | 21 tests, with `test_get_client_puts_the_helper_on_each_call` |
| M16 | `_next_wait` ignores `Retry-After` | 10 tests, with both cases of the 429 test |
| M17 | A wait past the cap goes ahead | `test_a_retry_after_past_the_cap_ends_the_tries` |

Under M9, httpx stopped the loop itself after 20 requests with `TooManyRedirects`. So the tests
went red on the missing typed error, not on a hang.

**Known limit EM-G4a-f1.** When a `batchModify` stops on a rate limit, `bulk_apply` falls back to
one `modify` for each message, as it did before EM-G4a. With the budget spent, each `modify` stops
at its first refusal. So a large bulk act can send many quick requests to a mailbox that Gmail
limits. EM-G5 does not own the fix (E-G5-8, 2026-10-05). A later slice owns it, with its own
audit, and §12.4 lists it before customers.

**Known limit EM-G4a-f2.** On the history path, a fetch that fails with a 5xx leaves a record, and
the cursor still moves past that message. Only a rate limit holds the cursor. EM-G4b decides
whether a 5xx holds the cursor too. EM-G4b decided it: a 5xx holds the cursor for 3 cycles at
most (§12.3.5.2).

##### 12.3.5.2 EM-G4b — the history cursor and its recovery

**Status.** ✅ MERGED #632 (2026-10-05). The as-built notes and
the mutation table are at the end of this section.

**Gate.** 🟢 AGENT-SAFE · R8, because the cursor write is SQL. No migration:
`email_accounts.last_history_id` holds the cursor.

**Order.** After EM-G4a, so each call of the cursor gets the rate-limit helper. A label event uses
the folder rule of EM-G2. EM-G4b and EM-G3a go in any order.

**Size.** L. About 250 lines in `gmail.py`, with tests of about 300 lines.

**Scope.**

1. **The seed (GM-16).** With no cursor, the sweep reads `users.getProfile` FIRST and keeps its
   `historyId`. It then runs the sweep and returns that id as `new_history_id`. The next history
   call reads each change made during the sweep.
2. **Every page (GM-17).** The history call follows `nextPageToken` to the last page. The new
   cursor is the `historyId` of the last answer.
3. **The label events (GM-17).** `labelsAdded` and `labelsRemoved` add the message id to the fetch
   set. The provider fetches such a message in full, as it fetches an added message. A fetch costs
   the same quota in each format, and a full fetch keeps `has_attachments` right.
4. **A deleted message.** `messagesDeleted` keeps the `[DELETED]` marker, which moves the row to
   trash (`scheduler.py:522-526`). E-B2 below changes this for a row in `drafts`.
5. **A stale cursor (GM-17).** A 404 from `history.list` is no failure. The provider seeds a new
   cursor from `getProfile`, then sweeps all mail back to the catch-up watermark.
6. **The sweep after a 404.** It lists with `q=after:<epoch seconds>` and `includeSpamTrash=true`,
   and no `labelIds`. With no watermark, it uses the floor. It logs `gmail.history_reset` with the
   mailbox id.
7. **The catch-up (GM-18, D-EM-13).** With a cursor, `catch_up` stays unused, because the history
   reads each change since the cursor. Gmail keeps its history for about one week, often longer.
   Item 5 is the catch-up for a longer pause. The docstring at `gmail.py:898-900` says so.
11. **Resync** still clears the cursor (`transport/sync.py:283-325`), and item 1 seeds a new one.

**The rules of the audit (E-B1 to E-B3, 2026-10-05).**

- **E-B1, a short sweep after a 404 keeps the stale cursor.** The sweep of item 6 reads at most
  `DEEP_SYNC_MAX_PAGES` pages, the ceiling that `_sweep_label` has today. That is 50 pages of 100,
  so 5,000 messages. When the sweep stops at that cap, or a page fails, it returns no new cursor.
  The stale id then stays, and the next cycle gets the 404 and sweeps again.
- **Open for the audit of EM-G4b.** A gap of more than 5,000 messages never completes, so each
  cycle sweeps the same newest pages. The recommendation is the abandon rule of the Outlook sweep.
  After `CATCH_UP_MAX_MISSES` (6) short cycles, the seed moves on, and the log names the gap.
  The orchestrator took the recommendation on 2026-10-05. The as-built notes below record it.
- **E-B2, a deleted draft. EM-G4b fixes it.** Each edit of a draft in Gmail web gives the draft a
  new message id and deletes the old one. `messagesDeleted` then moves the old row to Trash through
  `_TRASH_DELETED`, so each edit leaves one ghost in Trash. With the fix, a `[DELETED]` marker for
  a row in `drafts` deletes that row and moves nothing to Trash. The audit of EM-G4b checks the
  effect on IMAP, which sends the marker too.
- **E-B3, the seed point of the first import.** `_sync_cycle` reads the seed BEFORE
  `_import_in_batches`, when the mailbox has no cursor. A seed after the import loses each change
  made in Gmail during the import, such as a read mark, an archive or a delete. The sweep of that
  cycle then reads the history from that seed. A provider with no cursor gives no seed, so Outlook
  and IMAP see no change.

**Non-goals.** No push (EM-G6a, EM-G6b). No import change beyond the seed point of E-B3 (EM-G5).
No change to Outlook or IMAP, except the E-B2 rule for a deleted draft.

**Fences (R7).** A new `tests/unit/test_gmail_history_cursor.py`, with an `httpx.MockTransport`
that answers as Gmail does.

- `test_a_sweep_with_no_cursor_seeds_from_get_profile_first`
- `test_history_reads_every_page`
- `test_the_new_cursor_is_the_id_of_the_last_answer`
- `test_inbox_removed_in_gmail_files_the_row_as_archive`
- `test_unread_removed_in_gmail_marks_the_row_read`
- `test_a_user_label_added_in_gmail_reaches_categories`
- `test_a_stale_cursor_reseeds_and_sweeps_back_to_the_watermark`
- `test_a_short_reset_sweep_keeps_the_stale_cursor` (E-B1)
- `test_a_deleted_draft_leaves_no_row_in_trash` (E-B2)
- `test_the_first_import_seeds_before_it_imports` (E-B3)
- R8: `test_the_cursor_reaches_last_history_id_and_the_next_cycle_reads_it`. It drives
  `_sync_cycle` twice with a Gmail fake, in the harness of `test_email_scheduler_tenancy.py`.

**Mutations.** Each one must turn the named test red. M5 moved to EM-G4a, and EM-G4a uses M8 to
M17. So a new mutation of EM-G4b takes M18 or a later id.

- M1 removes the seed, and the seed test fails.
- M2 reads one page, and the page test fails.
- M3 drops the label events, and both label tests fail.
- M4 raises on a 404, and the stale test fails.
- M6 keeps the id of the first page, and the last-answer test fails.
- M7 returns the seed after a reset sweep that stopped short, and the short-sweep test fails.
- M18 moves a deleted draft to Trash, and the draft test fails.
- M19 seeds after the import, and the seed-point test fails.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_gmail_history_cursor.py tests/unit/test_email_scheduler_tenancy.py \
  tests/unit/test_email_deep_sync.py tests/unit/test_email_provider_401_retry.py -v -rs
uv run ruff check tests/unit/test_gmail_history_cursor.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

The R8 tests must show PASSED, with 0 skips. `ruff check apps/services/email_ingestion` exits 1 on
old findings in other files, so the block above leaves it out (review round 1 F8). The counts of
`gmail.py` and `scheduler.py` must stay at or below the counts on main.

**As built (2026-10-05, branch `email-gmail-g4b`).** The build follows items 1 to 7 and 11, the
rules E-B1 to E-B3, and the decisions of the orchestrator of 2026-10-05. It adds no migration, no
column, no table and no flag. Production holds no Gmail mailbox, so no live mailbox changes.

- **The seed (item 1, E-B3).** `GmailProvider.seed_cursor` reads `users.getProfile` through the
  client seam. `BaseEmailProvider.seed_cursor` returns None, so Outlook and IMAP get no seed.
  `scheduler._seed_before_import` calls it before `_import_in_batches` when the mailbox has no
  cursor. The sweep of that cycle then reads the history from the seed.
- **A failed seed.** A failed seed never stops new mail (owner answer Q2). The scheduler logs
  `sync.seed_failed`, and the import and the sweep still run. The sweep then seeds on its own. A
  second failure gives no cursor, and the next cycle seeds again. A spent rate limit on any seed
  fails the sync, as each rate limit does. `ProviderRateLimited` in `base.py` marks it, and
  `GmailRateLimited` adds that class (review round 1 F2).
- **Every page (item 2).** `_read_history` follows `nextPageToken` to the last page. The new cursor
  is the `historyId` of the last answer. One read has no page cap (known limit EM-G4b-f1).
- **The events (items 3 and 4).** `messagesAdded`, `labelsAdded` and `labelsRemoved` put the
  message id into one fetch set, so two events of one message cost one fetch. Each fetch is
  `format=full`. `messagesDeleted` gives a `[DELETED]` marker, and the provider does not fetch a
  deleted message.
- **A defect that never ran.** The old marker set no `thread_id`, and `EmailMessage` needs it. So
  the first delete on the old history path raised. The history path never ran, so production
  never met it. The new fence found it.
- **A stale cursor (items 5 and 6).** A 404 on any history page seeds again, then sweeps all
  mail. The list sends `q=after:<epoch seconds>`, `includeSpamTrash=true` and no `labelIds`. It
  reads back to the later of the catch-up watermark and the floor, because the floor binds each
  sync (EM-T6a). With neither, it sends no `after:`.
- **E-B1, a short sweep.** The sweep reads `DEEP_SYNC_MAX_PAGES` (50) pages at most. The page
  cap, a page that fails, a fetch that a later cycle can fix or a failed seed makes it short. A
  short sweep returns no new cursor. It sets `catch_up_incomplete` with the name `all mail`.
  Phase (d) then keeps the stale cursor and the watermark, and the next cycle sweeps again.
- **The abandon (the open point, decided).** The count of short sweeps is the count of the
  Outlook catch-up: `_catch_up_misses` in `scheduler.py`, through `_watermark_outcome`. So one
  policy and one constant serve both providers. The count lives in the process, only a loop cycle
  adds to it, and a restart clears it. No column holds it.
- **The write at the abandon.** After `CATCH_UP_MAX_MISSES` (6) short loop cycles,
  `scheduler._next_cursor` writes `SyncResult.reseed_history_id`. That is the seed that the sweep
  of the same cycle read first. The scheduler logs `email.gmail_history_gap_abandoned` with the
  mailbox id and the count. A Resync recovers the older gap.
- **The reset log.** `SyncResult.cursor_reset` marks each reset. The scheduler then logs
  `gmail.history_reset` with the mailbox id, and says whether the sweep read to the end and
  whether the seed worked. The provider does not know the mailbox id.
- **A failed reseed (review round 1 F6).** The scheduler also logs `gmail.reseed_failed`, and the
  cycle backs off, because the stale cursor stays. Before this rule, a sticky abandon swept all
  mail at each interval while `getProfile` failed.
- **A failed fetch (EM-G4a-f2, decided).** A fetch on the history path that fails with a 5xx or a
  transport error keeps the OLD position. A later cycle cannot fix a 404, another 4xx or a parse
  error, so they do not hold the cursor. The fetch record goes into `SyncResult.errors` each time.
- **Where that count lives.** The stored cursor holds it. The cursor is a plain history id, or
  JSON while a fetch holds it: `{"held_cycles": <n>, "history_id": "<id>", "v": 1}`. The count
  belongs to the cursor, not to a message (review round 1 F1). The cursor holds no message id, so
  its size never grows (F7). This form is the narrowest, with no migration and no second store,
  and a restart keeps it.
- **A history id.** A history id is ASCII digits only (F5). Text that is neither form is no
  cursor, and a JSON object with no `history_id` is none either (F4). The sync then seeds again.
- **The pass.** On the cycle that reaches `GMAIL_FETCH_HOLD_CYCLES` (3) held cycles, the cursor
  moves to the last answer. Each message that still fails gets the log `gmail.fetch_abandoned` and
  a record in `SyncResult.errors`, with its id only. A cycle with no failed fetch moves the cursor
  and clears the count. So one bad message, or a new failure on each cycle, holds the cursor for
  3 cycles at most. No line names a subject or an address.
- **The sync log (F7).** `email_sync_log.provider_history_id` keeps a plain history id, and NULL
  for a held JSON cursor (`scheduler._log_cursor`). The Outlook delta keeps its JSON out of the
  log for the same reason (EM-T4d review round 1 F5).
- **A rate limit.** A `GmailRateLimited` on a history page or on a fetch fails the cycle, as
  EM-G4a says. The loop backs off, and the cursor does not move.
- **E-B2, a deleted draft.** For a marker with `EmailMessage.deletion_marker`, `_write_messages`
  runs `_DELETE_DELETED_DRAFT` before `_TRASH_DELETED`. Only the Gmail history sets that flag, so
  a real subject cannot trigger the rule (review round 1 F3). A row in `drafts` or `draft`, in any
  case, goes. Any other row moves to TRASH. Each table that points at a message row cascades or
  sets NULL (the files 17, 19, 73 and 87 of `infra/postgres/`).
- **No change to IMAP or Outlook.** Only `gmail.py` sets `deletion_marker`, so the E-B2 rule never
  meets an IMAP or an Outlook row. Both keep the `seed_cursor` of the base class, so
  `_seed_before_import` passes their stored value on as it is. ⚠️ A real message with the subject
  "[DELETED]" still moves to TRASH, as on main. That defect of main stays out of this slice. ⚠️
  The docstring of `reconcile.py` says that IMAP sends the marker. The code does not.
- **Items 7 and 11.** The docstring of `sync_messages` says that a cursor makes `catch_up`
  unnecessary. It also says that the sweep of item 6 is the catch-up after a long pause. A Resync
  clears the cursor in phase (a), and the seed before the import gives the new one.
- **The size.** `sync_messages` now calls `_deep_sweep`, `_history_sync` and `_first_sweep`, so
  ruff finds no C901 in it. Ruff finds 18 issues in `gmail.py`, against 21 on main. The change to
  `gmail.py` has about 550 lines, and the new test file has 1,235 lines.

**Review round 1 (2026-10-05).** The verifier passed the branch with eight findings. The verifier
also found the Outlook and IMAP sync the same as main in 10 cases. The build fixed each finding,
and the bullets above give the rules that apply now.

- **F1 (P2).** A count for each message let a new failure on each cycle hold the cursor with no
  end. The count now belongs to the cursor.
- **F2.** `_seed_before_import` degraded a spent rate limit. It now raises a
  `ProviderRateLimited`, so the cycle fails before the import sends a request.
- **F3.** The draft rule keyed on the subject "[DELETED]", so a real Outlook or IMAP draft with
  that subject lost its row. It now keys on `deletion_marker`.
- **F4 and F5.** A JSON object with no `history_id`, and a history id of other digits, now have
  fences. A history id is ASCII digits only.
- **F6.** The scheduler now logs a reset and a failed reseed with the mailbox id. A failed reseed
  backs the loop off.
- **F7.** The sync log keeps no held JSON, and the held cursor holds no message id.
- **F8.** The "Verify with" block leaves out the ruff command that exits 1 on old findings.

**Known limit EM-G4b-f1.** The cursor moves only at the end of a read. A backlog that one cycle
cannot finish can fail on a rate limit before its end, such as a bulk label on 50,000 messages.
The next cycle then starts again at the old cursor. A later slice can move the cursor at each
history record.

**The fences, as built.** `tests/unit/test_gmail_history_cursor.py` holds 49 cases. A fake Gmail
on `httpx.MockTransport` answers the client. The provider sends no `authenticate` request, so the
fake sees the calls of the sync in order.

| Test | What it proves |
|---|---|
| `test_a_sweep_with_no_cursor_seeds_from_get_profile_first` | The first request is `getProfile`, and the sweep returns its id (item 1). |
| `test_a_failed_seed_still_sweeps_and_returns_no_cursor` | A failed seed lands the mail and returns no cursor. |
| `test_a_rate_limited_seed_fails_the_sync` | A rate limit on the seed raises. |
| `test_text_that_is_no_cursor_seeds_again` (8 cases) | Text that is no cursor seeds again, and sends nothing to `history.list`. JSON with no `history_id` and other digits are no cursor (F4, F5). |
| `test_history_reads_every_page` | Three pages, one fetch set, and no `messages.list` with a cursor (items 2 and 7). |
| `test_the_new_cursor_is_the_id_of_the_last_answer` | The cursor is the id of the last page. |
| `test_inbox_removed_in_gmail_files_the_row_as_archive` | A removed INBOX files the message as `archive`. |
| `test_unread_removed_in_gmail_marks_the_row_read` | A read mark and a star reach the message, with one full fetch. |
| `test_a_user_label_added_in_gmail_reaches_categories` | A user label reaches `categories`. |
| `test_a_deleted_message_is_a_marker_and_is_not_fetched` | A deleted message is a marker, with no fetch (item 4). |
| `test_a_rate_limit_on_a_later_history_page_fails_the_sync` | A rate limit on page 2 raises. |
| `test_a_stale_cursor_reseeds_and_sweeps_back_to_the_watermark` | A 404 seeds, then lists all mail after the watermark (items 5 and 6). |
| `test_the_reset_sweep_reads_back_to_the_later_of_watermark_and_floor` (3 cases) | The floor binds an older watermark, and with none the sweep sends no `after:`. |
| `test_a_short_reset_sweep_keeps_the_stale_cursor` | At the page cap the sweep returns no cursor (E-B1). |
| `test_a_reset_sweep_with_a_failure_is_short` (3 cases) | A failed page, fetch or seed makes the sweep short. |
| `test_a_5xx_fetch_keeps_the_old_cursor` | A 5xx keeps the old position, and the record goes into `errors`. |
| `test_a_transport_error_holds_the_cursor` | A transport error holds the cursor too. |
| `test_a_held_cursor_holds_no_message_id` | 150 failed fetches leave a held cursor with three keys (F7). |
| `test_a_failure_that_stays_does_not_hold_the_cursor` (3 cases) | A 404, a 400 and a failed parse move the cursor on. |
| `test_a_message_that_fails_three_cycles_is_passed_and_recorded` | The third failure passes the message and records it. |
| `test_a_new_failure_each_cycle_cannot_hold_the_cursor_longer` | With a new failure on each cycle, the cursor moves at cycle 3 and records both messages (F1). |
| `test_a_message_that_recovers_drops_out_of_the_count` | A message that succeeds leaves the count. |
| `test_the_first_import_seeds_before_it_imports` | The seed comes before the import, and an archive during the import lands (E-B3). |
| `test_a_failed_seed_before_the_import_still_imports` | A failed seed before the import stops nothing. |
| `test_a_resync_clears_the_cursor_and_seeds_a_new_one` | A Resync reads the history from the new seed (item 11). |
| `test_a_provider_with_no_cursor_gets_no_seed` (2 cases) | Outlook and IMAP get no seed, and their value goes on as it is. |
| `test_a_complete_reset_sweep_writes_the_seed` | Phase (d) writes the seed of a complete reset. |
| `test_six_short_loop_cycles_abandon_the_gap_and_write_the_fresh_seed` | Five short loop cycles keep the cursor, and the sixth writes the seed. |
| `test_a_short_cycle_of_a_member_act_does_not_count` | Six manual syncs abandon nothing. |
| `test_a_spent_rate_limit_on_the_seed_fails_the_cycle_before_the_import` | Three 429 answers to the seed fail the cycle, and the import sends nothing (F2). |
| `test_a_failed_reseed_logs_the_mailbox_and_backs_the_loop_off` | A sticky abandon with a failed reseed logs the mailbox and backs off (F6). |
| `test_the_sync_log_keeps_no_held_cursor` | The sync log gets NULL for a held cursor, then the plain id (F7). |
| R8 `test_the_cursor_reaches_last_history_id_and_the_next_cycle_reads_it` | Five cycles on Postgres: the seed, two pages, a held 5xx, a rate limit, then the move. The sync log holds no JSON. |
| R8 `test_a_deleted_draft_leaves_no_row_in_trash` | A deleted draft row goes, and a deleted inbox row moves to TRASH (E-B2). |
| R8 `test_a_real_subject_of_deleted_keeps_its_draft_row` | An Outlook draft and an IMAP draft with the subject "[DELETED]" keep their rows and attachments, as on main (F3). |

**Mutations, as run (2026-10-05).** A script ran each mutation against its named tests, with the
database up. It then wrote back the original bytes. The SHA-256 of `gmail.py` and `scheduler.py`
matched each time. Each mutation turned its named tests red. Review round 1 ran the whole table
again, with M23 to M30 added.

| Id | Mutation | Red tests |
|---|---|---|
| M1 | `_first_sweep` reads no seed | `test_a_sweep_with_no_cursor_seeds_from_get_profile_first` |
| M2 | The history read stops after one page | `test_history_reads_every_page` |
| M3 | The label events leave the fetch set | the 3 label tests |
| M4 | A 404 of `history.list` raises | `test_a_stale_cursor_reseeds_and_sweeps_back_to_the_watermark` |
| M6 | The cursor keeps the id of the first page | `test_the_new_cursor_is_the_id_of_the_last_answer` |
| M7 | A short reset sweep returns the seed | `test_a_short_reset_sweep_keeps_the_stale_cursor` |
| M18 | A deleted draft moves to Trash (`scheduler.py`) | R8 `test_a_deleted_draft_leaves_no_row_in_trash` |
| M19 | The seed comes after the import (`scheduler.py`) | `test_the_first_import_seeds_before_it_imports` |
| M20 | The abandon rule never fires (`scheduler.py`) | `test_six_short_loop_cycles_abandon_the_gap_and_write_the_fresh_seed` |
| M21 | A 5xx moves the cursor at once | `test_a_5xx_fetch_keeps_the_old_cursor` |
| M22 | A failed fetch never moves the cursor | `test_a_message_that_fails_three_cycles_is_passed_and_recorded` |
| M23 | F1: a count for each message again | `test_a_new_failure_each_cycle_cannot_hold_the_cursor_longer` |
| M24 | F7: the held cursor stores the failed ids | `test_a_held_cursor_holds_no_message_id` |
| M25 | F2: the seed degrades a spent rate limit (`scheduler.py`) | `test_a_spent_rate_limit_on_the_seed_fails_the_cycle_before_the_import` |
| M26 | F6: a failed reseed does not back off (`scheduler.py`) | `test_a_failed_reseed_logs_the_mailbox_and_backs_the_loop_off` |
| M27 | F7: the sync log keeps the held JSON (`scheduler.py`) | `test_the_sync_log_keeps_no_held_cursor` and the R8 cursor test |
| M28 | F3: the draft rule keys on the subject again (`scheduler.py`) | R8 `test_a_real_subject_of_deleted_keeps_its_draft_row` |
| M29 | F4: `KeyError` leaves the `except` of `_parse_cursor` | 2 cases of `test_text_that_is_no_cursor_seeds_again` |
| M30 | F5: a history id takes any digit | 2 cases of `test_text_that_is_no_cursor_seeds_again` |

#### 12.3.6 EM-G5 — the import and the reconcile, in two slices

**The split (the audit of 2026-10-05).** The audit of EM-G5 cleared the import and stopped the
reconcile. The confirm of the old item 6 can trash a live row, so the reconcile waits for a new
confirm. EM-G5a is the import, items 1 to 5. EM-G5b is the reconcile, items 6 to 12, with the rule
E-G5-9. Each item keeps its number, so "EM-G5 item 4" still names one item.

**The rules of the audit (E-G5-1 to E-G5-10).** E-G5-3 to E-G5-8 bind the build of EM-G5a, and
§12.3.6.1 holds them. E-G5-9 binds EM-G5b, and §12.3.6.2 holds it. The other three rules correct
this document.

- **E-G5-1, the anchors.** GM-8, GM-19, GM-20 and item 7 name the lines at `a9a08deb9` (§12.1).
- **E-G5-2, the verify block.** The ruff line names files only. The pytest line adds the four
  files that the import touches (§12.3.6.1).
- **E-G5-10, EM-G3a-f3.** No reconcile reads a row in `trash` (`reconcile.py:45`). So the text of
  EM-G3a-f3 was false, and §12.3.3 corrects it. No slice owns that row yet.

##### 12.3.6.1 EM-G5a — the import

**Status.** ✅ MERGED (#641, 2026-10-05). The audit of 2026-10-05
cleared it after E-G5-1 to E-G5-8 landed. The as-built notes and the mutation table follow the
verify block.

**Gate.** 🟢 AGENT-SAFE · R8. No migration, no column and no flag. Gmail stays dark (D-EM-36).

**Order.** After EM-G4a and EM-G4b. The import uses the rate-limit helper of EM-G4a. The seed
before the import (EM-G4b E-B3) stays as it is: `_sync_cycle` seeds at `scheduler.py:1421`,
before `_import_in_batches` at `:1426`.

**Size.** L. About 250 lines in `gmail.py`, with tests of about 300 lines.

**Scope.**

1. **One list for all mail (GM-8, GM-19).** Gmail overrides `import_batches`. It pages one
   `messages.list` with `q=after:<since> before:<until>` in epoch seconds, `includeSpamTrash=true`
   and no `labelIds`.
2. **The order.** Gmail lists the newest mail first, so each page is one batch. Each batch is
   sorted again by `received_key`, and a message newer than `until` drops, as in the base.
3. **The resume.** A resume passes `before:` the point that the import reached (EM-T6b, D-EM-13).
4. **The fetch.** The import fetches each page of ids in parallel, with at most 10 calls at once,
   through the rate-limit helper. It fetches a message with several labels once.
5. **The estimate (D-EM-16).** The import awaits `on_estimate` once, with the
   `resultSizeEstimate` of the first answer.

**The rules of the build (E-G5-3 to E-G5-8).**

- **E-G5-3, the resume bound.** `before:` is the whole second of `until`, plus 1 second, as in
  `outlook.py:118-133`. So a resume reads the second that it reached again. The upsert makes the
  overlap harmless. Fence: `test_a_resume_reads_the_second_it_reached_again`.
- **E-G5-4, a failed fetch.** A failure that a later cycle can fix, a 5xx or a transport error,
  fails the import. The resume then reads that message again. A failure that stays, a 404 or a
  parse error, keeps its record (EM-G4a item 10), and the batch goes on. Fence:
  `test_a_transient_fetch_failure_fails_the_import`.
- **E-G5-5, no fetch in flight at a yield.** Each page gathers all of its fetches before it
  yields. A rate limit cancels the other fetches of the page and raises. The import stops after
  the batch that reaches the storage limit (`scheduler.py:919-923`), and `aclosing` closes it.
  Fence: the storage limit stops the import with no fetch in flight.
- **E-G5-6, the page cap.** The import reads a set number of pages at most, far above the 50
  pages of the deep sweep. At the cap it ends and logs `gmail.import_capped`. The model is
  `IMPORT_MAX_PAGES` of Outlook (`outlook.py:1353`).
- **E-G5-7, the sweep half of GM-8.** EM-G5a fixes the import only. Known limit EM-G5-f1 below
  records the sweep, and the comment at `gmail.py:1967` must say so.
- **E-G5-8, the deep sweep stays.** `_deep_sweep` keeps its fences: M14 of
  `test_gmail_rate_limits.py`, and `test_email_deep_sync.py`. A new fence proves that
  `import_batches` never calls `sync_messages(deep=True)`. EM-G4a-f1 is not part of EM-G5, and
  §12.4 lists it before customers.

**Non-goals.** No `message_exists`, no change to `import_full_snapshot`, no change to
`reconcile.py` and no change to `_confirm_gone` (EM-G5b). No change to the default of the base
class, so IMAP keeps it. No change to the progress UI. No change to the storage limit.

**Fences (R7).** A new `tests/unit/test_gmail_import.py`.

- `test_the_import_lists_all_mail_once_newest_first`
- `test_the_import_reads_archived_mail_with_no_label`
- `test_a_message_with_two_labels_is_fetched_once`
- `test_the_estimate_comes_from_result_size_estimate`
- `test_a_resume_passes_before`
- `test_no_message_older_than_the_floor`
- The fences of E-G5-3 to E-G5-6 and E-G5-8.
- R8, in `TestTheImportOnARealDatabase` of `tests/unit/test_email_import_batches.py`: a Gmail
  first import through `_sync_cycle` writes the estimate, then resumes below
  `import_reached_at`.

**Mutations.** Each one must turn a named test red. M3 and M4 move to EM-G5b.

| Id | Mutation | The test that fails |
|---|---|---|
| M1 | Put back `labelIds` | the archived-mail test |
| M2 | Drop the estimate | the estimate test |
| M5 | Drop the 1 second of E-G5-3 | `test_a_resume_reads_the_second_it_reached_again` |
| M6 | Skip a fetch failure that a later cycle can fix | `test_a_transient_fetch_failure_fails_the_import` |
| M7 | Yield with a fetch task open | the storage-limit fence of E-G5-5 |
| M8 | Remove `includeSpamTrash` | the all-mail test |

**Verify with (E-G5-2).**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_gmail_import.py tests/unit/test_email_import_batches.py \
  tests/unit/test_email_storage_limit.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_gmail_history_cursor.py tests/unit/test_gmail_rate_limits.py \
  tests/unit/test_email_rekey_reclaim.py tests/unit/test_email_scheduler_tenancy.py -v -rs
uv run ruff check apps/services/email_ingestion/email_ingestion/providers/gmail.py \
  tests/unit/test_gmail_import.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

The R8 tests must show PASSED, with 0 skips. The count of `gmail.py` must stay at or below the
count on main.

**As built (2026-10-05, branch `email-gmail-g5a`).** The build follows items 1 to 5 and the rules
E-G5-3 to E-G5-8. It adds no migration, no column, no table and no flag. Production holds no Gmail
mailbox, and D-EM-36 keeps the Gmail connect dark.

- **The list.** `GmailProvider.import_batches` sends `includeSpamTrash=true`, no `labelIds`, the
  batch size as `maxResults` (500 at most), and `q=after:<floor> before:<bound>`. With no floor
  and no bound, it sends no `q`.
- **The window.** `_in_import_window` drops a message newer than `until` and a message older than
  the floor. A message with no date stays, as in the base, and the core decides on it (EM-T6a).
- **One fetch for each id.** `_fresh_ids` keeps the ids of the whole import in one set. So an id
  that the list gives again at a page edge gets one fetch and one row.
- **The fetch.** `_fetch_page` starts one task for each id, and a semaphore lets 10 run at once.
  It gathers each task before it returns. When one task raises, it cancels the others and waits
  for them. Each fetch goes through `get_message`, so the parse of EM-G2 and the rate limits of
  EM-G4a apply.
- **A failed fetch.** `_import_fetch` raises a spent rate limit at once. Each other failure leaves
  its record through `_record_fetch_failure`. A 5xx or a transport error then raises
  (`_transient`), and any other failure gives None.
- **The estimate.** `_estimate_of` reads `resultSizeEstimate` as a whole number, or gives None.
  The import awaits `on_estimate` after the first list answer and before the first fetch.
- **The cap.** `IMPORT_MAX_PAGES` is 5000, 100 times the 50 pages of the deep sweep. At the cap
  the import ends and logs `gmail.import_capped` with the pages and the count of messages.
- **The label names.** The import loads the label names once, before the first fetch. So the
  fetches of a page do not each read the label list.
- **E-G5-7 and E-G5-8.** The comment of `_first_sweep` names known limit EM-G5-f1. The
  docstrings of `sync_messages` and `_deep_sweep` say that the import does not call the deep sync.
  The docstring of the default import in `base.py` says that only IMAP uses it.
- **Two test fakes changed.** Two hermetic tests and two R8 tests of EM-G4b imported through the
  label lists of the default import. `_by_label` in `test_gmail_history_cursor.py` now answers the
  list of all mail with each id once, as Gmail does. The default-import test of
  `test_email_import_batches.py` now uses IMAP, because Gmail does not use the default.
- **The size.** The change to `gmail.py` has about 190 lines, and most of them are docstrings.
  The new test file has 609 lines. Ruff finds 7 issues in `gmail.py`, the count on main.

**The fences, as built.** `tests/unit/test_gmail_import.py` holds 21 cases. A fake Gmail on
`httpx.MockTransport` answers the real `_get_client`. Its list honours `labelIds`,
`includeSpamTrash`, `after:` and `before:`, so a mutation of the query changes what it gives.

| Test | What it proves |
|---|---|
| `test_the_import_lists_all_mail_once_newest_first` (2 cases) | Eight messages of six kinds come once each and newest first, also when a page comes oldest first. Each list sends `includeSpamTrash=true` and no `labelIds`. |
| `test_the_import_reads_archived_mail_with_no_label` | A message with no label comes, with the folder `archive` (GM-8). |
| `test_a_message_with_two_labels_is_fetched_once` | A message with two user labels comes once, with one fetch. |
| `test_an_id_listed_again_at_a_page_edge_is_fetched_once` | An id that the list gives again comes once, with one fetch. |
| `test_the_import_fetches_in_parallel_at_most_ten_at_once` | A page of 30 ids runs 10 fetches at once, never more. |
| `test_the_estimate_comes_from_result_size_estimate` (2 cases) | One call before the first fetch, with the value of the first answer, or None. |
| `test_a_resume_passes_before` | The list sends `before:` the second after `until`. A message of that second that is newer than `until` drops. |
| `test_a_resume_reads_the_second_it_reached_again` | A message 400 ms older than `until`, in the same second, comes (E-G5-3). |
| `test_no_message_older_than_the_floor` | The list sends `after:` the floor. An older message drops, also when the list gives it. |
| `test_a_transient_fetch_failure_fails_the_import` (2 cases) | A 503 and a transport error fail the import before the page yields (E-G5-4). |
| `test_a_fetch_failure_that_stays_is_recorded_and_the_batch_goes_on` (3 cases) | The batch goes on after a 404, a 400 or a parse error, and each one leaves a record (E-G5-4). |
| `test_a_rate_limit_cancels_the_other_fetches_and_raises` | A spent rate limit cancels the 4 other fetches and raises, with no fetch in flight (E-G5-5). |
| `test_the_storage_limit_stops_the_import_with_no_fetch_in_flight` | `_run_import` writes the first batch with no fetch in flight, stops at the limit and reads no next page (E-G5-5). |
| `test_the_import_cap_is_far_above_the_deep_sweep` | The cap is at least 25 times the 50 pages of the deep sweep (E-G5-6). |
| `test_the_import_stops_at_the_page_cap_and_logs_it` | A list that never ends stops at the cap and logs `gmail.import_capped` (E-G5-6). |
| `test_the_import_never_calls_the_deep_sync` | The import runs while `sync_messages` and `_deep_sweep` refuse each call (E-G5-8). |
| R8 `test_a_gmail_first_import_writes_the_estimate_and_resumes_below_it` | On Postgres, through `_sync_account`, the estimate is 250. A 503 on page 3 fails the import at 200 rows. The resume sends `before:` the second after `import_reached_at`, and it ends `done` with 250 rows, a count of 251 and an estimate of 251. |

**Mutations, as run (2026-10-05).** A script ran each mutation against its named tests, with the
database up. It then wrote back the original bytes of `gmail.py`, and the SHA-256 matched each
time. Each mutation turned its named tests red. M9 to M12 are extra.

| Id | Mutation (`gmail.py`) | Red tests |
|---|---|---|
| M1 | The list sends `labelIds` of `INBOX` | `test_the_import_reads_archived_mail_with_no_label` |
| M2 | No call of `on_estimate` | both cases of the estimate test, and the R8 test |
| M5 | `before:` with no 1 second | `test_a_resume_reads_the_second_it_reached_again`, and the R8 test |
| M6 | A 5xx or a transport error gives None | both cases of `test_a_transient_fetch_failure_fails_the_import` |
| M7 | `_fetch_page` returns at the first fetch that ends, and the others stay open | `test_the_storage_limit_stops_the_import_with_no_fetch_in_flight` |
| M8 | The list sends no `includeSpamTrash` | both cases of `test_the_import_lists_all_mail_once_newest_first` |
| M9 | A raise does not cancel the other fetches | `test_a_rate_limit_cancels_the_other_fetches_and_raises` |
| M10 | No bound on the fetches at once | `test_the_import_fetches_in_parallel_at_most_ten_at_once` |
| M11 | Gmail falls back to the default import | `test_the_import_never_calls_the_deep_sync` |
| M12 | `IMPORT_MAX_PAGES` is 50 | `test_the_import_cap_is_far_above_the_deep_sweep` |

M7 survived the first run. Each fetch of the fake took the same time, so all ten ended in one turn
of the loop. The fake now gives each message its own time, as Gmail does, and M7 is red.

**Verification (2026-10-05, a private database).** The pytest block above gave 317 passed and 0
skipped. Each file whose name starts with `test_email_`, `test_gmail_` or `test_outlook_`, 145
files, gave 2914 passed and 0 skipped. The first ruff line finds the 7 findings of `gmail.py`
that main has, and none in the new test file. The second line of ruff passes.

**Known limit EM-G5-f1 (E-G5-7).** `_first_sweep` still misses archived mail with no user label.
It reads the user labels and five system labels, and no label reaches that mail. It runs only when
no cursor exists after a failed seed. The import of EM-G5a reads that mail, and the history cursor
of EM-G4b reads each later change.

##### 12.3.6.2 EM-G5b — the reconcile

**Status.** ✅ MERGED (#647, 2026-10-05). The second audit of
2026-10-05 cleared it as GO-NARROWED. The first audit stopped the confirm of the old item 6, and
E-G5-9 replaced it. The second audit found three more paths that can trash a row: the key of the
confirm, a stale draft row and a bare 404. C1 to C9 close them, and the scope below holds each
one.

The as-built notes and the mutation table follow the build-PR note.

**Gate.** 🟢 AGENT-SAFE · R8, because the reconcile trashes rows. No migration and no flag. The
slice changes shared code of the live Outlook reconcile. So it takes the full review loop for data
(C9).

**Order.** After EM-G5a (#641), because the reconcile reads the snapshot of the import.

**Size.** M.

**The candidates today (the second audit).** A candidate is a row of the account in a folder that
the snapshot holds. The row is not in `trash`, and its `received_at` is at or after the oldest
written message of that folder. Its id is not in the snapshot. Its `updated_at` is set and older
than `db_now`. A folder with more than max(50, 2%) candidates is skipped, and a folder sends at
most 50 lookups (`reconcile.py:40-47`, `:78-103`).

**Scope.**

6. **The confirm (GM-20, E-G5-9, C1).** Gmail confirms each candidate by its provider id, with
   `GET messages/{id}?format=minimal`. A new method of the provider holds that read, with its own
   name. Outlook keeps `message_exists(internet_message_id)`, and its behaviour does not change.
   - `import_reconcile_candidates` returns `(row id, provider_message_id, internet_message_id)`
     (`reconcile.py:101-102`).
   - `_confirm_gone` (`scheduler.py:761-786`) picks the key for each provider.
   - A Gmail message keeps its id. A draft does not (O-GM-2), and item 9 covers drafts.
7. **`import_full_snapshot = True`.** The import reads all mail back to the floor. So a deep sync
   that a member starts reconciles the deletions (`scheduler.py:924-929`). Three member acts start
   it: Resync (`sync.py:323-324`), "Clean older mail" (`cleanup.py:923-924`) and "Process past
   emails" (`runner.py:1431`). The first import collects no snapshot (`scheduler.py:881-883`).
8. **The recurring reconcile** of `reconcile_full_snapshot` stays for Outlook only. It has no
   confirm (`reconcile.py:115-160`). The Gmail history reports each delete (EM-G4b item 4). No
   Gmail sync result sets `full_snapshot`, and a fence now pins that.
9. **Drafts stay out (C3).** The Gmail reconcile leaves out each row in drafts. It uses the filter
   of `storage.py:88`: `LOWER(COALESCE(folder,'')) NOT IN ('drafts','draft')`. Gmail gives a draft
   a new message id at each update, so a missing draft id proves no delete. The history of EM-G4b
   removes an old draft row through E-B2 (`scheduler.py:343-347`).
10. **The 404 rule (C2).** "Gone" means a 404 whose body gives the reason `notFound`. Each other
    answer raises, and that includes a bare 404. A raise keeps the row. A 200 keeps the row, also
    for a message in `TRASH` or `SPAM`, because the import read both. The confirm goes through
    `_get_client()`, so the rules of EM-G4a apply. It sends `format=minimal` only.
11. **A rate limit stops the confirm (C4).** `_confirm_gone` stops at the first
    `ProviderRateLimited`. Each row that it did not confirm keeps its folder
    (`scheduler.py:797-801`). Outlook raises no `ProviderRateLimited`, so no live path changes.
12. **A capped import runs no reconcile (C5).** Gmail records when its import reaches
    `IMPORT_MAX_PAGES` (`gmail.py:2167`). `_run_import` then skips the reconcile, as it does
    at the storage limit (`scheduler.py:956-961`). It logs `sync.import_reconcile_skipped` with
    `reason=capped`, the event of the storage-limit skip. The audit text named
    `email.import.reconcile_skipped`, and no code logs that name.

**Non-goals (C8).** No recurring reconcile for Gmail. No delete path for a draft row. No change to
rows in `trash` (E-G5-10). No change to the `[DELETED]` rule of `scheduler.py:539-549`. No flag.

**Fences (R7, C6).**

- R8, in `tests/unit/test_email_import_batches.py`, with a real `GmailProvider` on the fake of
  `tests/unit/test_gmail_import.py`:
  - `test_a_gmail_resync_trashes_a_row_that_gmail_deleted`
  - `test_a_row_that_the_import_missed_and_gmail_holds_keeps_its_row`. Its row has a Message-ID
    that differs from its id, and the fake answers 404 to each id that it does not hold.
  - `test_a_stale_gmail_draft_row_stays_in_drafts_with_no_lookup`
  - `test_a_capped_gmail_import_runs_no_reconcile`
- Hermetic, in `tests/unit/test_gmail_import.py`:
  - `test_the_gmail_confirm_reads_by_the_provider_id`. A 404 with `notFound` gives gone. A 200 for
    `INBOX`, `TRASH` and `SPAM` keeps the row. A bare 404, a 400, a 403, a spent 429, a 500 and a
    transport error each raise. The path holds the provider id.
  - `test_a_spent_rate_limit_stops_the_confirm`
  - `test_no_gmail_sync_result_sets_full_snapshot` (item 8)
- The four Outlook R8 fences of `test_email_import_batches.py` and its hermetic fence of the
  Outlook confirm stay green.

**Mutations (C7).** M3 sets `import_full_snapshot = False`, and the resync test fails. M4 makes the
confirm keep each row, and the resync test fails. M9 trashes each candidate with no confirm, and
the negative fence fails.

**The mutations of the second audit.** M10 sends the Message-ID to the confirm, and the negative
fence fails. M11 counts a bare 404 as gone, and the confirm fence fails. M12 keeps drafts in the
candidates, and the draft fence fails. M13 lets the confirm go on after a rate limit, and the rate-limit fence fails.
M14 runs the reconcile after a capped import, and the cap fence fails.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_gmail_import.py tests/unit/test_email_import_batches.py \
  tests/unit/test_email_storage_limit.py tests/unit/test_email_deep_sync.py \
  tests/unit/test_gmail_history_cursor.py tests/unit/test_gmail_rate_limits.py \
  tests/unit/test_email_rekey_reclaim.py tests/unit/test_email_scheduler_tenancy.py -v -rs
uv run ruff check apps/services/email_ingestion/email_ingestion/providers/gmail.py \
  apps/services/email_ingestion/email_ingestion/reconcile.py \
  apps/services/email_ingestion/email_ingestion/scheduler.py tests/unit/test_gmail_import.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

The R8 tests must show PASSED, with 0 skips. The ruff count of each file must stay at or below its
count on `main`.

**In the build PR (R4, C10).** Correct GM-20 (§12.1) and the status of this section. Correct
`AGENTS.md:283-284` and `:421-432`, and the docstrings at `scheduler.py:764-770`, `:873-880` and
`reconcile.py:61-77`.

**As built (2026-10-05, branch `email-gmail-g5b`).** The build follows items 6 to 12. It adds no
migration, no column, no table and no flag. Production holds no Gmail mailbox, and D-EM-36 keeps
the Gmail connect dark.

The same branch holds the C10 corrections. They are GM-20, the status lines and the three
docstrings. In `email_ingestion/AGENTS.md`, they are contracts 12 and 13 and the paragraph of the
three guards.

- **The key (item 6, C1).** Each candidate is a triple: the row id, `provider_message_id` and
  `internet_message_id` (`reconcile.py:67-123`). The confirm, `_confirm_gone`
  (`scheduler.py:761`), asks `message_gone` by the provider id when the provider has that method.
  Else it asks `message_exists` by the Message-ID, as before.
- **The confirm (item 10, C2).** The new method `GmailProvider.message_gone` (`gmail.py:2209`)
  reads `messages/{id}` with `format=minimal` through `_get_client`. Only a 404 whose body gives
  the reason `notFound` gives True (`_says_not_found`). A 2xx gives False, and each other answer
  raises.
- **The reason, not the status.** A 404 that gives only the status `NOT_FOUND` raises. Google
  sends the reason and the status together for a deleted message. So the narrower test loses no
  delete, and a changed body of Google keeps the row.
- **An id guard (an addition to the scope).** The method `message_gone` refuses an id that holds
  a character other than a letter, a digit, `-` or `_` (`_GMAIL_MESSAGE_ID`). It refuses before
  any request. A stored id with a `/`, a `?` or a `#` could send the read to another resource.
  The refusal raises, so the row stays.
- **Item 7.** The attribute `GmailProvider.import_full_snapshot` is True (`gmail.py:1764`).
- **Item 9 (C3), for Gmail only.** Gmail sets `import_reconcile_skips_drafts`, a new attribute of
  the base class that is False by default (`base.py:438`). The scheduler then passes
  `skip_drafts=True` in `_reconcile_import`, and the query adds the filter of
  `storage.KEPT_FOLDERS_SQL`. The statement of Outlook keeps the text of main.
- **Why the filter binds Gmail only.** The Outlook sweep reads `drafts` (`outlook.py:74`). Its
  parse keeps the `internetMessageId` of Graph for each message (`outlook.py:1975`). So an Outlook
  draft row can be a candidate today, and a filter for both providers would change Outlook.
- **Item 11 (C4).** The confirm stops at the first `ProviderRateLimited`. It logs
  `sync.import_reconcile_rate_limited` with the mailbox, the rows gone and the rows not confirmed.
  The rows that it confirmed before the stop go to trash. Another failure lets the confirm go on.
- **Item 12 (C5).** The import sets `import_capped` at the cap, and clears it when an import
  starts. Then `_run_import` logs `sync.import_reconcile_skipped account=<id> reason=capped` and
  runs no reconcile.
- **Item 8.** No line of `gmail.py` sets `full_snapshot`. A fence reads the source and each sync
  path.
- **One Outlook fence changed, and it is stronger.** The fake of
  `test_a_resync_reconciles_once_against_its_whole_import` takes the place of
  `import_reconcile_candidates`. It now takes `skip_drafts`, and it fails when the Outlook resync
  passes True. The four Outlook R8 fences did not change, and they pass.
- **The test fake.** The fake of `test_gmail_import.py` gives the body of Google to an id that it
  does not hold. That body is a 404 with the reason `notFound` and the status `NOT_FOUND`. A path
  that the fake does not know still gets a bare 404. The fake records each `format=minimal` read in
  `confirms`. The set `unlisted` holds a message that the list leaves out, and the flag
  `stale_history` makes the history answer 404.

**The fences, as built.** The R8 cases run as `acb_app_h3rls` and call `_assert_non_priv`.

| Test | What it proves |
|---|---|
| R8 `test_a_gmail_resync_trashes_a_row_that_gmail_deleted` | Through `_sync_account(deep=True)`, a row that Gmail deleted goes to trash in org B. The one confirm sends its provider id with `format=minimal`. The 30 imported rows stay in the Inbox. Org A reads no trash row. |
| R8 `test_a_row_that_the_import_missed_and_gmail_holds_keeps_its_row` | The list leaves out a message that Gmail holds, and its Message-ID differs from its id. The confirm asks by the provider id. Gmail answers 200, and the row stays. |
| R8 `test_a_stale_gmail_draft_row_stays_in_drafts_with_no_lookup` | Two drafts came in the import. An old draft row gets no lookup and stays in drafts. A deleted Inbox row of the same Resync goes to trash. |
| R8 `test_a_capped_gmail_import_runs_no_reconcile` | The cap is 2 pages. A deleted row inside the pages that the import read gets no lookup and stays. The log says `reason=capped`. |
| R8 `test_only_the_gmail_reconcile_leaves_out_drafts` (extra) | On real SQL, each candidate is the triple of C1. With `skip_drafts`, a draft row is no candidate. Without it, as for Outlook, the same row is a candidate. |
| `test_the_gmail_confirm_reads_by_the_provider_id` (12 cases) | A 404 with `notFound` is gone. A 200 for `INBOX`, `TRASH` and `SPAM` keeps the row. A bare 404, an empty 404, a 404 with the status only, a 400, a 403, a spent 429, a 500 and a transport error raise. Each try is one `format=minimal` read of the provider id. |
| `test_the_gmail_confirm_refuses_an_id_that_is_not_a_gmail_id` (8 cases, extra) | An empty id, a `/`, a `../`, a `?`, a `#`, a `%`, a Message-ID and a space each raise. No request goes out. |
| `test_a_spent_rate_limit_stops_the_confirm` | A 500 lets the confirm go on, and a spent 429 stops it. The row that it confirmed before stays confirmed. The two rows after the stop get no lookup. |
| `test_no_gmail_sync_result_sets_full_snapshot` | No cursor, the history, a stale cursor and the deep sweep each return `full_snapshot` False. No line of `gmail.py` sets the field. |
| `test_the_import_stops_at_the_page_cap_and_logs_it` (EM-G5a, extended) | The provider records the cap. The next import that ends clears the record. |

**Mutations, as run (2026-10-05).** A script ran each mutation against the ten fences above, with
the database up. It then ran `git checkout --` on the file, and `git diff` was empty each time.
Each mutation turned its named fence red. M12b and M15 to M17 are extra.

| Id | Mutation | Red tests |
|---|---|---|
| M3 | `GmailProvider.import_full_snapshot = False` | the resync fence, and the negative, draft and cap fences |
| M4 | `message_gone` gives False for a 404 with `notFound` | the resync fence, and the draft, confirm and rate-limit fences |
| M9 | `_confirm_gone` gives each candidate with no lookup | the negative fence, and the resync, draft and rate-limit fences |
| M10 | The Gmail confirm sends the Message-ID | the negative fence, and the resync, draft and rate-limit fences |
| M11 | A 404 with no `notFound` is gone | the confirm fence: the bare, empty and status-only 404 |
| M12 | The query has no filter for drafts | the draft fence, and `test_only_the_gmail_reconcile_leaves_out_drafts` |
| M12b | Gmail sets `import_reconcile_skips_drafts = False` | the draft fence |
| M13 | The confirm goes on after a spent rate limit | the rate-limit fence |
| M14 | The reconcile runs after a capped import | the cap fence |
| M15 | `message_gone` has no id guard | the id-guard fence, 8 cases |
| M16 | The import never sets `import_capped` | the cap fence, and the cap test of EM-G5a |
| M17 | The first sweep sets `full_snapshot=True` | `test_no_gmail_sync_result_sets_full_snapshot` |

Under M10 the id guard refuses the Message-ID, so no read goes out. The negative fence fails on
its check of the confirm read, and the resync fence keeps a deleted row. Without the guard, the
fake answers `notFound` to the Message-ID, and the reconcile trashes the live row of the negative
fence.

**Verification (2026-10-05, the shared scratch database).** The pytest block above gave 344
passed and 0 skipped. The 140 files whose names start with `test_email_` or `test_gmail_` gave
2899 passed and 0 skipped, with the memory and calendar tests left out. The first ruff line finds
16 findings: the 7 of `gmail.py` and the 9 of `scheduler.py` that main has. It finds none in
`reconcile.py` or in the test file. The second line of ruff passes.

The shared database gave `AdminShutdown` to some runs, while a second session used it. The build
ran each of those runs again, and each count above comes from a clean run.

#### 12.3.7 EM-G6a — the push route and its tenant (dark)

**Gate.** 🟢 AGENT-SAFE build · security review · R8. `EMAIL_GMAIL_PUSH` stays `off`. To change it
on a box is gate `enforcement-flip`.

**Order.** After EM-G4b, and after the orchestrator answers O-GM-4. The audit of EM-G6a reads the
chosen design first. The scope follows candidate (a) of O-GM-4.

**Size.** M. About 200 lines with tests.

**Scope.**

1. **The route.** A public route `POST /email/webhook/gmail`. It is the one Gmail entry in
   `PUBLIC_ROUTES` (`gateway/main.py:558`). It joins the exemption from the feature gate in
   `core.py:58-62`, as `/email/webhook/microsoft` does. The Phase-1 `/webhooks/gmail` stays.
2. **The OIDC check.** The route verifies the Pub/Sub token. It checks the signature against the
   Google keys, the issuer and the audience `GMAIL_PUSH_AUDIENCE`. It checks the `email` claim
   against `GMAIL_PUSH_SERVICE_ACCOUNT`, with `email_verified`. A failure answers 401 and queues
   nothing.
3. **The flag.** With `EMAIL_GMAIL_PUSH=off`, the route answers 204 after the check and queues
   nothing.
4. **The tenant (O-GM-4).** The route decodes `message.data` to `emailAddress` and `historyId`. One
   discovery read gives the pairs of organization and mailbox id for that address, and nothing else.
5. **The queue.** For each pair, the route queues `_webhook_sync(account_id, organization_id)`,
   which binds that tenant. An address with no match queues nothing.
6. **The answer.** The route answers 2xx fast, so Pub/Sub does not send again. A push for a mailbox
   that syncs already joins the next rerun (EM-T4f).
7. **Settings.** `GMAIL_PUSH_AUDIENCE` and `GMAIL_PUSH_SERVICE_ACCOUNT` go into `settings.py` and
   `.env.example`, empty by default. An empty value refuses each push.

**Non-goals.** No watch (EM-G6b). No change to the Phase-1 receiver.

**Fences (R7).** A new `tests/unit/test_email_gmail_push.py`.

- `test_a_push_with_no_token_queues_nothing`
- `test_a_token_with_the_wrong_audience_queues_nothing`
- `test_the_flag_off_queues_nothing`
- `test_an_unknown_address_queues_nothing`
- R8: `test_a_push_queues_one_sync_for_each_mailbox_in_its_own_tenant`, with one address in two
  organizations
- `tests/unit/test_org_access_enforcement.py` names the new public route, and no other Gmail route.

**Mutations.** M1 skips the audience check, and the audience test fails. M2 ignores the flag, and
the flag test fails. M3 queues with no tenant bound, and the R8 test fails.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_gmail_push.py tests/unit/test_email_webhook.py \
  tests/unit/test_org_access_enforcement.py tests/unit/test_email_tenant_bind_rls.py -v -rs
uv run ruff check apps/services/gateway/gateway tests/unit/test_email_gmail_push.py
```

The R8 tests must show PASSED, with 0 skips.

#### 12.3.8 EM-G6b — the watch (dark)

**Gate.** 🟢 AGENT-SAFE build. To set `EMAIL_GMAIL_PUSH` on a box is gate `enforcement-flip`.

**Order.** After EM-G6a.

**Size.** M. About 150 lines with tests.

**Scope.**

1. **The watch.** With the flag on, `_ensure_subscription` (`transport/sync.py:438-508`) handles a
   Gmail row. It calls `users.watch` with `topicName` set to `GMAIL_PUSH_TOPIC`, and no label
   filter.
2. **The row.** It writes `webhook_subscription_id = 'gmail-watch'`, because a Gmail watch has no
   id. It writes `webhook_expires_at` from the `expiration` of the answer.
3. **The renewal.** Google asks for a watch call at least once in 7 days, and advises one each day.
   The loop renews a Gmail watch when less than 6 days remain.
4. **The stop.** A disconnect calls `users.stop`, best effort, where it drops the Graph
   subscription (`transport/accounts.py:695`, `:773-800`). It does so for each row that holds a
   watch, with the flag on or off.
5. **The flag off.** No Gmail row makes a watch call.
6. **The register.** `work_plan.md` §6.0 D4 lists `EMAIL_GMAIL_PUSH` with the dark flags, gate
   `enforcement-flip`. `GMAIL_PUSH_TOPIC` goes into `settings.py` and `.env.example`, empty.

**Non-goals.** No Pub/Sub setup from code. The owner makes the topic (§12.4 step 16).

**Fences (R7).** A new `tests/unit/test_gmail_watch.py`.

- `test_the_flag_on_watches_a_gmail_mailbox`
- `test_the_flag_off_makes_no_watch_call`
- `test_a_watch_renews_when_six_days_remain`
- `test_a_disconnect_stops_the_watch`
- R8: `test_a_gmail_watch_writes_its_own_row_only`

**Mutations.** M1 ignores the flag, and the flag test fails. M2 renews at 1 day, and the renewal
test fails. M3 drops the stop, and the disconnect test fails.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_gmail_watch.py tests/unit/test_email_disconnect_order.py \
  tests/unit/test_email_webhook.py -v -rs
uv run ruff check apps/services tests/unit/test_gmail_watch.py
```

The R8 tests must show PASSED, with 0 skips.

#### 12.3.9 EM-G7 — the connect backend

**Status.** ✅ MERGED #637 (2026-10-05). The as-built notes and
the mutation table are at the end of this section.

**Gate.** 🟢 AGENT-SAFE · security review. It merges dark. With no Google app on the box, a member
sees no change.

**Order.** Any time. It needs no other EM-G slice. A live test waits for §12.4 and EM-G10.

**Size.** M. About 150 lines of code, with tests of about 250 lines.

**Scope.**

1. **The scopes (D-EM-31, GM-22).** `GMAIL_SCOPES` (`gmail.py:138`) holds `gmail.modify` and
   `gmail.settings.basic`. The authorize leg keeps `access_type=offline` and `prompt=consent`.
2. **The granted scope.** The callback reads `scope` in the token answer of Google. When either
   scope is absent, it saves no mailbox and bounces `scope_missing`.
3. **The refresh token.** A Google token answer with no `refresh_token` saves nothing and bounces
   `token_exchange_failed`.
4. **The Google errors (GM-23).** For Gmail, `access_denied` maps to `consent_declined`, and
   `admin_policy_enforced` maps to `workspace_admin_blocked`. Each other plain code passes through
   `_provider_error_reason`.
5. **Errors that never return.** Google shows some errors on its own page, for example
   `org_internal`. So EM-G8 adds the help for a Workspace admin before the click.
6. **The provider on each bounce (GM-24).** `_bounce` adds `provider` from the path, so each
   failure names its provider.
7. **No operator text for a member (GM-25, D-EM-35).** Both entries of `_NOT_CONFIGURED` say only
   that the provider is not available yet. Neither names Integrations or a client ID.
8. **The capability read (D-EM-35).** A new gated route `GET /email/oauth/providers` answers one
   boolean for each provider, keyed by its id: `{"microsoft": bool, "gmail": bool}`. A value is
   true when that app is configured (`oauth_app(provider).configured`). Gmail also needs the flag
   of D-EM-36. The answer holds booleans only, never a client ID, a secret or a URL. EM-G8 reads
   this shape (review round 1).
9. **The app facts for a Workspace admin.** `GET /email/oauth/gmail/app` returns the client ID and
   the redirect URI, as it does for Microsoft (`transport/oauth.py:238-270`). An admin needs the
   client ID to trust the app. Google shows both values in each authorize URL.

**Non-goals.**

- No PKCE. EM-T1a signs the state, and the Microsoft leg has none.
- No `include_granted_scopes`. No `openid` scope, because `users/me/profile` gives the address.
- No revoke on disconnect (O-GM-3).
- ~~The Integrations entry `gmail-oauth` (`routes/integrations.py:276-295`) stays, as
  `microsoft-oauth` does.~~ O-GM-5 reversed this (orchestrator, 2026-10-05). The entry is gone,
  and the Integrations writes refuse the keys of the mail apps.
- No UI (EM-G8).
- A mailbox connected with `https://mail.google.com/` works until its next reconnect.

**Fences (R7).** A new `tests/unit/test_email_gmail_connect.py`. `test_email_connect_backend.py`,
`test_email_oauth_authorize_wiring.py`, `test_email_oauth_app_info.py` and
`test_email_oauth_state.py` change where they assert the old behaviour.

- `test_gmail_asks_the_two_scopes_of_d_em_31`
- `test_a_grant_without_settings_basic_saves_nothing`
- `test_a_token_without_refresh_token_saves_nothing`
- `test_each_bounce_names_the_provider`
- `test_google_errors_map_to_their_reasons`
- `test_not_configured_names_no_integrations`
- `test_the_providers_route_answers_booleans_only`
- `test_gmail_app_info_returns_the_client_id`

**Mutations.** M1 accepts a partial grant, and the scope test fails. M2 bounces with no provider,
and the bounce test fails. M3 adds the client ID to the providers route, and its test fails.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_gmail_connect.py tests/unit/test_email_connect_backend.py \
  tests/unit/test_email_oauth_authorize_wiring.py tests/unit/test_email_oauth_app_info.py \
  tests/unit/test_email_oauth_state.py tests/unit/test_email_owner_scope_fence.py \
  tests/unit/test_integrations_mail_app_keys.py tests/unit/test_integrations_env_hardening.py -v -rs
uv run ruff check apps/services/gateway/gateway/routes/email/transport/oauth.py \
  apps/services/gateway/gateway/routes/email/transport/accounts.py \
  tests/unit/test_email_gmail_connect.py tests/unit/test_integrations_mail_app_keys.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/api/email
```

The ruff line names the files of this slice, and it exits clean. A ruff check of the whole package
`routes/email` finds 278 errors that are older than this slice (review round 1, P3-2).

**As built (2026-10-05, branch `email-gmail-g7`).** The build covers items 1 to 4 and 6 to 9, the
BFF bounces (E-C1), the capability read (E-C2), O-GM-5 and D-EM-36. Item 5 is copy for EM-G8.
The slice adds no migration and changes no SQL.

- **The scopes and the grant (items 1 to 3).** `GMAIL_SCOPES` holds the two scopes of D-EM-31.
  The callback checks the token answer of Google before the profile read. `_gmail_grant_refusal`
  answers `scope_missing` when a scope is absent, and `token_exchange_failed` with no refresh
  token. A `scope` that is absent, or that is not a string, is a missing scope. The Microsoft leg
  does not read these rules.
- **The Google errors (item 4).** `_refusal_reason` sends a Gmail error through
  `_google_error_reason`, which never reads `error_description`. A Microsoft error still goes
  through `_consent_error_reason`. So `admin_policy_enforced` on Microsoft stays a plain code.
- **The provider on each bounce (item 6, E-C1).** The gateway `_bounce` adds `provider` only for
  `gmail` or `microsoft` (`CONNECT_PROVIDERS`). The two BFF routes build each failure with
  `bounceQuery` in `src/app/api/email/oauth/bounce.ts`. Each `failed()` call passes the path
  segment, and the one allowlist decides. No route echoes another segment.
- **No operator text (item 7).** `_NOT_CONFIGURED` says "Gmail is not available yet." and
  "Microsoft 365 / Outlook is not available yet." The BFF still hands that `detail` to the
  callback page as the error.
- **The capability read (item 8, E-C2).** `GET /email/oauth/providers` answers
  `{"microsoft": bool, "gmail": bool}`: one entry for each provider, keyed by its id, as the
  dispatch asked. The model `OAuthProviders` sets `extra="forbid"`. A provider is true when its app
  holds a client ID and a secret, and Gmail also needs the flag. A caller with no organization or
  no member gets 403.
- **The capability read, its fences.** The route has an `OWNER_SCOPE_EXEMPT` entry, because it
  reads no mailbox. The email catch-all of the BFF proxies it, so it needs no new BFF route.
- **The app facts (item 9).** `GET /email/oauth/gmail/app` returns the client ID and the redirect
  URI. Another provider is still a 404. While the flag is off, Gmail answers 503, as a box with no
  Google app does. That rule is an agent call on D-EM-36, so the dark state is one state.
- **The dark flag (D-EM-36).** `email_gmail_connect` in `acb_common/settings.py` is false by
  default, and `gmail_connect_enabled()` is its one reader. While it is off, the authorize leg
  refuses Gmail before it signs a state or reads a mailbox. The callback bounces each Gmail return
  with `provider_unavailable` before the token exchange, so it writes no row. It also refuses a
  reconnect of a Gmail mailbox, and production holds none. Only the env file of the box sets the
  flag, because layer B of `env_guard` refuses each `EMAIL_*` name on each Integrations write.
  Restart the gateway after each change. A cache clear can read the file again, but it never
  replaces a value that the process loaded at start (review round 1).
- **The Integrations refusal (O-GM-5).** Since the rebase on #633, layer B of
  `acb_common.env_guard` is the one refusal. It answers 403 for each `GMAIL_OAUTH_*`,
  `MSFT_OAUTH_*` and `AUTH_MICROSOFT_ENTRA_ID_*` key on configure, put and delete, in any case. It
  writes no store row, no env var and no file line, and a mixed request fails whole. The helper
  `_refuse_mail_app_keys` and its second list `is_mail_app_env` are gone (review round 1). The
  write of each other key does not change.
- **The tiles.** The `gmail-oauth` entry left `_SETUP_GUIDES` and `_GUIDE_CATEGORIES`, and
  `MAIL_APP_SERVICE_IDS` keeps the id reserved. The `microsoft-oauth` tile stays for its status.
  Since round 1 it has no setup step, no link and no form field. The status lists its three keys
  read-only. The banner names Outlook only, as plain text with no link. The palette debt of
  `integrations/page.tsx` drops from 83 to 73.
- **The manual route (review round 1).** `POST /email/accounts` answers 403 for `gmail` and
  `microsoft` before it encrypts, writes or starts a sync. It took tokens from the body, so it
  could write a Gmail row while the connect was dark. No caller sends either provider, so the route
  now adds an IMAP mailbox only.
- **The startup load (review round 1).** The `INTEGRATION_ENV_MAP` of `key_store` has no
  `gmail-oauth` and no `microsoft-oauth` entry. Layer B already skipped each of their rows, so the
  two entries were dead. A stored row under either id never reaches `os.environ`.

**The fences, as built.** `tests/unit/test_email_gmail_connect.py` holds 60 cases. They cover
the eight of the spec, the dark flag, its one reader, the mail-app names of `env_guard` and the
manual route. `tests/unit/test_integrations_mail_app_keys.py` holds 20 cases for O-GM-5, the tiles,
the banner and the startup load. `bounce.test.ts` and
the two `route.test.ts` files beside the BFF routes run each bounce for both providers. These
older suites changed:

- `test_email_oauth_app_info.py`: Gmail left the list of the 404 (item 9).
- `test_email_connect_backend.py`: the two consent-error cases expect `provider=microsoft` (item
  6), and its fixture turns the flag on.
- `test_email_multi_inbox.py`: its fixture turns the flag on, for the Gmail picker case.
- `test_email_owner_scope_fence.py`: one entry for `oauth_providers`, and the reason of
  `oauth_app_info` names Gmail.
- `test_email_oauth_state.py` and `test_email_oauth_authorize_wiring.py` did not change. They read
  `error` only, or a source shape that stays.
- `conformance.test.ts`: the palette debt of the Integrations page, 83 to 73.

**Mutations, as run (2026-10-05).** Each mutation ran against its named test and turned it red.
Each file then went back to its exact SHA-256.

| Id | Mutation | Red tests |
|---|---|---|
| M1 | The callback accepts a partial grant (`oauth.py`) | `test_a_grant_without_settings_basic_saves_nothing`, 6 of 6 |
| M2 | The gateway `_bounce` drops `provider` | `test_each_bounce_names_the_provider`, 12 of 12 |
| M3 | `OAuthProviders` gets a `client_id` field | `test_the_providers_route_answers_booleans_only` |
| M4 | The callback accepts a token with no `refresh_token` | `test_a_token_without_refresh_token_saves_nothing`, 3 of 3 |
| M5 | `admin_policy_enforced` maps to `provider_error` | `test_google_errors_map_to_their_reasons`, 2 of 7 |
| M6 | `bounceQuery` drops `provider` (`bounce.ts`) | 20 of the 58 vitest cases in `src/app/api/email/oauth` |
| M7 | `bounceQuery` echoes each provider value | 5 of the 58 vitest cases |
| M8 | Round 1: `PUT /integrations/keys` skips layer B for a mail-app key | `test_put_refuses_a_mail_app_key`, 2 of 2 |
| M9 | Round 1: `POST /integrations/configure` skips layer B for `AUTH_MICROSOFT_ENTRA_ID_SECRET` | `test_configure_refuses_each_mail_app_key[AUTH_MICROSOFT_ENTRA_ID_SECRET]` |
| M10 | The capability read returns the client IDs in a dict | `test_the_providers_route_answers_booleans_only` |
| M11 | The capability read ignores the flag | `test_flag_off_the_capability_says_gmail_false` |
| M12 | The authorize leg ignores the flag | `test_flag_off_the_authorize_leg_refuses_and_signs_nothing`, 2 of 2 |
| M13 | The callback ignores the flag | `test_flag_off_the_callback_refuses_and_writes_nothing`, 2 of 2 |
| M14 | Round 1: `POST /email/accounts` takes `gmail` and `microsoft` again | `test_the_manual_route_refuses_each_oauth_provider`, 4 of 6 |
| M14b | Round 1: `POST /email/accounts` drops only its 403 | `test_the_manual_route_refuses_each_oauth_provider`, 4 of 6 |
| M15 | Round 1: the startup map maps `gmail-oauth` again | `test_the_startup_map_names_no_mail_app_key` |

Round 1 ran M8, M9 and M11 to M15 again on the rebased code, and each one was red. Under M9,
layer C still answers 422, so the test reads the 403 of layer B alone.

**Known limits of EM-G7 (follow-ups, not fixed here).**

- Round 1 closed the two limits that stood here: the startup map and the Microsoft tile.
- The read-only probe `GET /integrations/test?service=gmail-oauth` stays. It returns a status, not
  a key.

**Review round 1 (2026-10-05).** The review failed EM-G7 on two P1s. The security fix #633 closed
both on main: a newline in a value, and a configure write of `EMAIL_GMAIL_CONNECT`. The branch is
rebased on #633, and these findings are closed:

- **One source for the refused names.** Layer B of `env_guard` refuses the mail-app keys. The
  second list in `app_credentials.py` and the helper in `integrations.py` are gone.
  `test_every_name_that_oauth_app_reads_is_a_platform_name` holds the two together.
- **P2, the tiles and the banner.** No setup step, no link and no form field for a mail app.
  Fences: `test_the_microsoft_tile_offers_no_field_and_no_setup_step` and
  `test_the_banner_gives_no_setup_step_and_no_link`.
- **P2 and P3-1, the manual route.** `POST /email/accounts` refuses both OAuth providers (M14).
- **P3, the startup map.** The two dead entries are gone (M15).
- **P3-2, the verify command.** It names the files of the slice.
- **The restart.** The settings comment, the as-built notes and §12.4 give the reason.
- **The M8 cases.** Each case reaches layer B. `test_the_gmail_oauth_service_is_gone` holds the
  400 of the removed service on its own.
- **The docstring of `oauth_app`.** It names the env file of the box, not Integrations.

#### 12.3.9b EM-G7b — a member allowlist for the Gmail connect

**Status.** ✅ MERGED #639 (2026-10-05). The as-built notes and
the mutation table are at the end of this section.

**Why (go-live item A7, orchestrator decision, 2026-10-05).** `EMAIL_GMAIL_CONNECT` (D-EM-36) is
one value for the whole box (`transport/oauth.py:119-132`). When it is on, each member of each
organization sees a live Gmail choice. While the Google app is in Testing, Google refuses each
user that is not a test user (§12.4 step 5). That member then reads the `consent_declined` copy.
So the live test of the owner (EM-G10) must reach the listed members only.

**Gate.** 🟢 AGENT-SAFE · security review. It merges dark. While the flag is off, no member sees
a change.

**Order.** After EM-G7 and EM-G8. Before EM-G10. It needs no other EM-G slice.

**Size.** S. About 40 lines of code, with tests of about 250 lines.

**Scope.**

1. **The setting.** `EMAIL_GMAIL_CONNECT_MEMBERS` (`email_gmail_connect_members` in
   `acb_common/settings.py`) holds member addresses, with a comma between addresses. The default
   is empty. It follows the pattern of `EMAIL_OUTLOOK_DELTA_ACCOUNTS` (EM-T4d).
2. **The list holds addresses, not member ids.** The gateway reads `UserContext.email` from the
   session, and the signed state of EM-T1a binds the same address. `UserContext.user_id` is an
   opaque token in two UUID spaces, and it is empty until the platform provisions the member
   (`acb_auth/roles.py`). Each entry is the sign-in address of a Metorite member. That address
   is not always the Gmail address that the member connects.
3. **The flag stays the master switch.** With the flag off, no member may connect Gmail. With the
   flag on and the list empty, every member may. "Every member" is for the time after Google
   verifies the app (§12.4 step 15). With the flag on and the list set, only a listed member may.
4. **The match.** The match ignores case. It removes the space around each entry and around the
   address of the session. It compares whole addresses, never a part of one.
5. **A value with no address.** An empty or blank value adds no limit. A value that holds only
   commas and spaces names no member, so no member passes. *Agent decision (EM-G7b, 2026-10-05):
   a value that an operator wrote fails closed.*
6. **One reader for each setting, and one check.** `gmail_connect_enabled()` stays the one reader
   of the flag. A new `gmail_connect_members()` is the one reader of the list. A new
   `gmail_connect_allowed(user)` takes the `UserContext` of the session and answers "may this
   member connect Gmail". It never reads a query, a body, a header or the state.
7. **Each Gmail gate calls the check.** The capability read, the authorize leg, the callback and
   the app facts call `gmail_connect_allowed(user)`. A listed member gets `gmail: true`, a 302 to
   Google and a saved mailbox.
8. **Each other member gets the dark answer of today.** The capability read says `gmail: false`.
   The authorize leg answers 400 before it signs a state. The callback bounces
   `provider_unavailable` before the token exchange. The app facts answer 503, because the dark
   state is one state (§12.3.9).
9. **A platform setting.** `env_guard.is_platform_env("EMAIL_GMAIL_CONNECT_MEMBERS")` is true,
   because of the prefix `EMAIL_`. So no Integrations write can set it. Only the env file of the
   box sets it. A change needs a restart of the gateway, as for the flag.

**Non-goals.**

- No UI change. EM-G8 reads the capability read already. A listed member sees Gmail, and each
  other member sees "Coming soon".
- No Microsoft change. No Microsoft leg reads the list.
- No list for each organization. The list is one value for the box, as the flag is.
- No address in a log line. A refusal logs `email.oauth_gmail_dark` with its leg, as today.
- No migration and no SQL change.

**Fences (R7).** `tests/unit/test_email_gmail_connect.py` gets these cases.

- `test_a_listed_member_connects_gmail`: the capability read says `gmail: true`, the authorize leg
  answers 302 to Google, and the callback saves the mailbox.
- `test_an_unlisted_member_reads_gmail_false`: the capability read says `gmail: false`.
- `test_an_unlisted_member_is_refused_at_authorize`: a 400, and the leg signs no state.
- `test_an_unlisted_member_is_refused_at_the_callback`: `provider_unavailable` before the token
  exchange, and no `email_accounts` row.
- `test_an_unlisted_member_gets_no_app_facts`: a 503.
- `test_the_list_ignores_case_and_space` and `test_the_list_matches_whole_addresses_only`.
- `test_an_empty_list_lets_every_member_through` and `test_a_list_of_only_commas_lets_nobody_through`.
- `test_the_flag_off_overrides_the_list`.
- `test_a_request_input_cannot_widen_the_list`: a `login_hint` or a signed state that names a
  listed member does not let an unlisted session through.
- `test_every_gmail_gate_asks_the_member_of_the_session`: each call of the check passes the
  `user` of `Depends(get_current_user)`, and only the check reads the flag.
- `test_gmail_connect_members_is_the_one_reader_of_the_list`.
- `test_the_member_list_is_a_platform_name`.
- `test_the_member_list_does_not_touch_microsoft`.

**Mutations.** Each one must turn its test red. Then the file goes back to its exact SHA-256.

- M1: the capability read ignores the list.
- M2: the authorize leg ignores the list.
- M3: the callback ignores the list.
- M4: the match is case-sensitive.
- M5: the check drops the flag-off override.

**Verify with.**

```bash
uv run pytest tests/unit/test_email_gmail_connect.py tests/unit/test_email_connect_backend.py \
  tests/unit/test_email_oauth_state.py tests/unit/test_email_oauth_app_info.py \
  tests/unit/test_email_owner_scope_fence.py tests/unit/test_integrations_env_hardening.py -v -rs
uv run ruff check apps/services/gateway/gateway/routes/email/transport/oauth.py \
  tests/unit/test_email_gmail_connect.py
uv run ruff check . --select F821,F601,F602,F502,F7,B006
```

`test_integrations_env_hardening.py` holds one R8 case. Run it against a private database, with
`TENANT_LADDER_DATABASE_URL` set, so that no case skips.

**As built (2026-10-05, branch `email-gmail-g7b`).** The build covers items 1 to 9. It adds no
migration and changes no SQL.

- **The setting.** `email_gmail_connect_members: str = ""` sits under `email_gmail_connect` in
  `acb_common/settings.py`. Its comment names the one reader, the gate and the fence.
- **The readers and the check** (`transport/oauth.py`). `_connect_members` parses the raw value
  and caches it, as `scheduler._delta_accounts` does. It answers `None` for an empty or blank
  value, and a set for each other value. `gmail_connect_members()` is the one reader of the field.
  `gmail_connect_allowed(user)` asks the flag first, and then the list.
- **The gates.** `provider_available` takes the `user` of the capability read. The authorize leg,
  the callback and the app facts call the check with the `user` of `Depends(get_current_user)`.
  `gmail_connect_enabled()` now has one caller, the check.
- **The form.** The list holds sign-in addresses (item 2), because the gateway can check
  `UserContext.email` from the session with no request input.
- **No UI change and no Microsoft change.** The log line `email.oauth_gmail_dark` does not change,
  and it carries no address.

**The fences, as built.** `tests/unit/test_email_gmail_connect.py` grows from 60 to 96 cases. The
36 new cases come from 16 tests: the 15 of the list above and `test_the_member_list_defaults_empty`.
The fixture of the file sets the list to empty, so the cases of EM-G7 run as before. No other
suite changed.

**Mutations, as run (2026-10-05).** Each mutation ran against its named test and turned it red.
`oauth.py` then went back to its exact SHA-256 (`7d66fb25`).

| Id | Mutation | Red in the named test | Red in the whole file |
|---|---|---|---|
| M1 | The capability read ignores the list | `test_an_unlisted_member_reads_gmail_false`, 2 of 2 | 8 |
| M2 | The authorize leg ignores the list | `test_an_unlisted_member_is_refused_at_authorize`, 2 of 2 | 7 |
| M3 | The callback ignores the list | `test_an_unlisted_member_is_refused_at_the_callback`, 2 of 2 | 4 |
| M4 | The match is case-sensitive | `test_the_list_ignores_case_and_space`, 3 of 5 | 4 |
| M5 | The check drops the flag-off override | `test_the_flag_off_overrides_the_list`, 3 of 3 | 10 |
| M6 | The app facts ignore the list | `test_an_unlisted_member_gets_no_app_facts`, 1 of 1 | 2 |
| M7 | A list of only commas lifts the limit | `test_a_list_of_only_commas_lets_nobody_through`, 3 of 3 | 3 |
| M8 | The match takes a part of an address | `test_the_list_matches_whole_addresses_only`, 5 of 6 | 5 |
| M9 | The parse keeps the space around an entry | `test_the_list_ignores_case_and_space`, 2 of 5 | 5 |

**Verify, as run.** The six suites of the verify command give 623 passed and 0 skipped, with
`TENANT_LADDER_DATABASE_URL` on a private database. The three ruff checks exit clean.

**Known limits (follow-ups, not fixed here).**

- `work_plan.md` §6 row D4 does not name the list yet. The EM-G9 branch adds both Gmail names
  there, so this branch leaves the row alone.
- The order line of §12.3.12 (EM-G10) does not name EM-G7b yet.

#### 12.3.10 EM-G8 — the connect UI

**Status.** ✅ MERGED #638 (2026-10-05), after EM-G7. The
as-built notes, the fences and the mutation table are at the end of this section.

**Gate.** 🟢 AGENT-SAFE · visual review. It merges dark.

**The guard is the flag (E-D1, orchestrator, 2026-10-05).** The guard is `EMAIL_GMAIL_CONNECT`
(D-EM-36), not a box with no Google app. The box holds `GMAIL_OAUTH_*` today (§12.1, measured on
2026-10-05, by name only). The UI offers Gmail only when the capability read answers
`gmail: true`. That answer needs the Google app on the box and the flag on. The flag flips at
EM-G10 only, under gate `enforcement-flip`, after the slices that D-EM-36 names.

**Order.** EM-G8 merges after EM-G7 (E-D1). EM-G7 adds the capability read, the flag and the
`provider` of each bounce. While the flag is off, Gmail stays "Coming soon". So EM-G8 can merge
before EM-G3b and EM-G5, and a provider that is half built never goes live.

**#633 closes O-GM-5 (E-D2).** #633 merged on 2026-10-05. Each Integrations and Settings write now
refuses a platform name with a 403 (`acb_common/env_guard.py`). `PLATFORM_ENV_PREFIXES` holds
`GMAIL_OAUTH_`, `MSFT_OAUTH_`, `AUTH_MICROSOFT_ENTRA_ID_` and `EMAIL_`. So no tenant can set the
key of a mail app, or `EMAIL_GMAIL_CONNECT`. Fence: `tests/unit/test_integrations_env_hardening.py`,
which names `EMAIL_GMAIL_CONNECT` and the mail-app keys.

**The check before the merge (E-D2).** The orchestrator does this check after EM-G7 deploys. The
box must answer `GET /email/oauth/providers` with `gmail: false`. If it answers `gmail: true`,
EM-G8 does not merge.

**Size.** M. About 200 lines of UI, with tests of about 200 lines.

**Scope.**

1. **Availability (D-EM-35, GM-26).** `CONNECT_PROVIDERS` no longer fixes `available`. The page
   reads `GET /email/oauth/providers` through `gatewayFetch`, as `lib/api.ts:524` reads the app
   facts. The answer is `{"microsoft": bool, "gmail": bool}` (EM-G7 item 8), and the store keeps
   it.
2. **A failed read** keeps Microsoft live and Gmail "Coming soon". A network error is a failed
   read. So are a 403, a 404 before EM-G7 deploys and an answer of another shape. `retryTarget` and
   `rangeStepProviderFrom` (`lib/connect.ts:248-286`) take the live set from the read.
   - **A provider that the read refuses (E-D4).** When the read answers `microsoft: false`, for
     example on a dev box with no Microsoft app, Microsoft also shows as not available. Its note
     is `Not available yet`, the words of `_NOT_CONFIGURED` in EM-G7. The UI never offers a
     provider whose app is missing.
3. **The copy (GM-27, E-D3).** The empty state names each provider that the read offers, because
   no try exists yet. With Microsoft only, it reads as today. The decline text, the generic
   failure and the callback page name the provider of the try. The callback page reads `provider`
   from its URL (EM-G7 item 6).
4. **The new reasons.** `scope_missing` says that Metorite needs both permissions, with a retry.
   `workspace_admin_blocked` shows the help of item 6.
5. **The folder tree of a Gmail mailbox** shows the well-known folders and Archive. Its user labels
   show in the label filter (O-GM-1). `mergeFolders` (`lib/emailStore.ts:139`) takes the provider
   of the mailbox.
6. **The help for a Workspace admin.** Under the Gmail choice, one line says that a company admin
   can trust Metorite once. It opens a short help with the client ID from
   `GET /email/oauth/gmail/app`. The help also gives the Admin console path of §12.4.
7. **The reconnect banner** already handles Gmail (`page.tsx:1085-1101`). Its copy stays, and the
   table below names the test that holds it (E-D3).
8. **Visual review.** Use the `visual-review` skill on the connect choices, the range step and the
   callback page for each reason. Look in light mode, at compact density, with a changed accent
   and at 390 px.

**The words, and the test that holds each (E-D3).** `{Name}` is `Microsoft` or `Google`, from the
provider of the try.

| Item | Where | The words | Test |
|---|---|---|---|
| 2 | The note of Gmail, not live | `Coming soon` | `gmail-coming-soon-when-the-read-fails` |
| 2 | The note of Microsoft, not live | `Not available yet` | `microsoft-unavailable-when-the-read-says-no` |
| 3 | The empty state, Microsoft only | `You sign in with Microsoft. Metorite never sees your password.` | `empty-state-names-each-live-provider` |
| 3 | The empty state, both | `You sign in with Microsoft or Google.` and the same second sentence | `empty-state-names-each-live-provider` |
| 3 | The decline text | `{Name} did not give Metorite access to your mailbox, so nothing was connected.` | `callback-copy-names-google` |
| 3 | The generic failure | `{Name} or Metorite stopped the connection.` | `callback-copy-names-google` |
| 3 | A mailbox that the member connected before | `To add a different mailbox, choose another account at {Name}.` | `callback-copy-names-google` |
| 4 | The title of `scope_missing` | `Metorite needs both permissions` | `scope-missing-copy` |
| 4 | The title of `workspace_admin_blocked` | `Your Google Workspace admin needs to trust Metorite` | `workspace-admin-help-shows-the-client-id` |
| 6 | The line under the Gmail choice | `Company Google account? Your admin can trust Metorite once for everyone.` | `workspace-admin-help-shows-the-client-id` |
| 6 | The Admin console path | `Security → Access and data control → API controls`, then `Manage third-party app access`, `Configure new app` and `Trusted` | `workspace-admin-help-shows-the-client-id` |
| 7 | The reconnect button | `Reconnect Outlook` and `Reconnect Gmail` | `reconnect-banner-names-the-provider` |

**Non-goals.** No change to the Microsoft paths beyond copy that names the provider. No change to
Integrations.

**Fences (R7).** `src/app/email/lib/connect.test.ts` inverts the cases at `:116-122`, `:561-562`
and `:722`. New cases carry these names:

- `gmail-available-from-capability`
- `gmail-coming-soon-when-the-read-fails`
- `microsoft-unavailable-when-the-read-says-no` (E-D4)
- `empty-state-names-each-live-provider` (E-D3)
- `callback-copy-names-google`
- `scope-missing-copy`
- `workspace-admin-help-shows-the-client-id`
- `gmail-folder-tree-shows-well-known-folders`
- `reconnect-banner-names-the-provider` (E-D3)

**Mutations (E-D4).** Each mutation must turn its named case red. Then the file goes back to its
exact SHA-256.

| Id | Mutation | The case that fails |
|---|---|---|
| M1 | The choices always offer Gmail as available | `gmail-coming-soon-when-the-read-fails` |
| M2 | `rangeStepProviderFrom` ignores the read | `gmail-available-from-capability` |
| M3 | The callback page ignores `provider` | `callback-copy-names-google` |
| M4 | `mergeFolders` keeps the Gmail user labels as folders | `gmail-folder-tree-shows-well-known-folders` |

**Verify with.**

```bash
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/email src/app/api/email src/lib/theme
cd ../.. && uv run pytest tests/unit/test_email_oauth_authorize_wiring.py -v
```

The theme suite holds the design-system fences, so keep it in the run.
`test_email_oauth_authorize_wiring.py` reads `email/page.tsx` and holds `handleConnect` (E-D4).

**As built (2026-10-05, branch `email-gmail-g8`).** The build covers items 1 to 8 and E-D1 to
E-D4. It changes UI files only, and `handleConnect` does not change.

- **The read (items 1 and 2).** `getConnectProviders` in `lib/api.ts` reads
  `GET /email/oauth/providers` through the email catch-all of the BFF. `mapProviderAvailability`
  takes an object with a boolean for each provider. Any other answer is a failed read.
- **The store.** `connectProviders` is `undefined` until the read settles, and `null` for a failed
  read. A read slower than `PROVIDERS_READ_TIMEOUT_MS` (8 seconds) is a failed read.
  `liveProviders` and `connectChoices` in `lib/connect.ts` turn the read into the live set and the
  choices.
- **No guess before the read.** Until the read settles, `ConnectChoices` draws two `Skeleton` rows
  and the sign-in line names no provider. The list mounts after the read. So a retry URL for Gmail
  opens the range step only when the read offers Gmail.
- **The retry (item 2).** `retryTarget` and `rangeStepProviderFrom` take the live set. The email
  page reads its query once into `rangeStepQuery`. The callback page reads the capability for its
  retry target only, and until that read settles, the live set is Microsoft only.
- **E-D4.** `UNAVAILABLE_NOTE` holds `Not available yet` for Microsoft and `Coming soon` for Gmail.
- **The copy (items 3 and 4).** `callbackView` now requires `provider`, and `callbackProvider`
  reads it from the URL. `alreadyConnectedCopy`, `offersTryAgain`, `offersConnectAgain` and
  `showsWorkspaceAdminHelp` move four decisions of the callback page into `lib/connect.ts`.
  `scope_missing` and `workspace_admin_blocked` are two new kinds of `callbackView`.
- **The folder tree (item 5).** `lib/emailStore.ts` now exports `mergeFolders`, and it takes the
  provider. For Gmail it adds no user label, and only a system label feeds the counts of the
  well-known folders. `fetchFolders`
  and `fetchAllFolderCounts` pass the provider of the mailbox.
- **The Workspace help (item 6).** `components/WorkspaceAdminHelp.tsx` holds
  `WorkspaceAdminSteps`, which only draws, and `WorkspaceAdminHelp`, which reads
  `getMailAppInfo("gmail")`. The line under a live Gmail choice opens it in place. The client ID
  read runs only when the help opens. The callback page shows the help for
  `workspace_admin_blocked`.
- **The reconnect banner (item 7).** `RECONNECT_LABEL` holds both labels, and the page reads it.
  The rest of the banner names no provider.
- **The visual review (item 8, 2026-10-05).** A rig that the build did not keep stubbed each `/api`
  call. It captured the empty state with the flag off, with both providers live, with the help
  open and with no app on the box. It also captured the range step for Gmail, and the callback
  page for four Gmail reasons. Each capture is in dark, light, compact, a violet accent and
  390 px. No capture showed a console or page error. At 390 px the mobile shell mounts the page
  again, so an open help closes, and that is the rig.

**The fences, as built.** `connect.test.ts` holds the nine named cases of the list above, with 31
new cases in all. The build inverts the cases at `:116-122`, `:561-562` and `:722`. The run of
`src/app/email`, `src/app/api/email` and `src/lib/theme` gives 850 passed, and main gave 819.

**Mutations, as run (2026-10-05).** A script ran each mutation against its named case. Each file
then went back to its exact SHA-256.

| Id | Mutation | Red cases |
|---|---|---|
| M1 | `connectChoices` offers Gmail as available for each read (`lib/connect.ts`) | `gmail-coming-soon-when-the-read-fails`, 4 of 8 |
| M2 | `rangeStepProviderFrom` takes `liveProviders(null)` in place of the read | `gmail-available-from-capability`, 1 of 4 |
| M3 | The callback page sets `provider` to `"microsoft"` (`oauth/callback/page.tsx`) | `callback-copy-names-google`, 1 of 4 |
| M4 | `mergeFolders` adds the user labels of a Gmail mailbox (`lib/emailStore.ts`) | `gmail-folder-tree-shows-well-known-folders`, 2 of 3 |

**Known limits of EM-G8 (follow-ups, not fixed here).**

- The folder suggestions of the rule editor (`MoveFolderField` in `RulesTab.tsx`) read the folder
  tree. For a Gmail mailbox they no longer list the user labels. A member can still type a label
  name. EM-G3b, which makes a move to a user label work, can take the suggestions from the labels.
- The reconnect banner does not read the capability read. While the flag is off, the reconnect of a
  Gmail mailbox bounces `provider_unavailable` (EM-G7), and production holds no Gmail mailbox.
- `provider_unavailable` shows the generic failure, with its code as the reference.

#### 12.3.11 EM-G9 — parity of a Gmail and Outlook pair

**Status.** ✅ MERGED #640 (2026-10-05). The as-built notes, the
mutation table and the known limits EM-G9-f1 to EM-G9-f3 are at the end of this section.

**Gate.** 🟢 AGENT-SAFE · R8. No migration.

**Order.** After EM-G2 and EM-G3a.

**Size.** S. Tests and docs, about 150 lines.

**Scope.** The EM-G9 build checked the anchors below at `a9a08deb9`.

1. **"Also in".** One member has a Gmail and an Outlook mailbox. A mail sent to both shows "Also
   in" on each row (`ALSO_IN_SQL`, `automation/identity.py:174`).
2. **The draft dedupe.** The second mailbox makes no draft when the first holds one
   (`_DRAFT_LOCK_SQL` at `automation/identity.py:225`, `_ANSWERED_IN_PAIR_SQL` at `:248`).
3. **The Sent-copy proof.** The proof holds from Gmail to Outlook and back (`_PROVEN_OWN_SEND_SQL`
   at `:330`). Mail between the two is not cold and not awaiting (D-EM-27).
4. **The known limits.** Update §11.6 edge cases 10, 11 and 26. Update the F2 note of EM-T8e-1
   (§11.7.5) and the note of EM-T8g-3 on `internet_message_id` (§11.7.7).

**Non-goals.** No change to the SQL of the four statements or to their callers. No change to
IMAP. No change to the cold check. No migration.

**The comments moved to EM-G2 (E9).** EM-G2 corrected each comment that said only Outlook stores
the Message-ID. They sit above `ALSO_IN_SQL` and `_PROVEN_OWN_SEND_SQL` in `automation/identity.py`,
in two lines of `apps/services/gateway/AGENTS.md`, and in the docstrings of
`test_email_duplicates.py` and `test_email_ai_context.py`. EM-G2 missed one comment. The doc
comment of `alsoIn` in `workbench/control_plane/src/app/email/lib/types.ts` still said that only
two Outlook mailboxes pair. EM-G9 corrects it.

**Fences (R7).** New R8 cases in `tests/unit/test_email_duplicates.py` and
`tests/unit/test_email_ai_context.py`. The Gmail rows come from the real parse of an EM-G2
fixture, not from a row built by hand.

- `test_also_in_pairs_a_gmail_and_an_outlook_copy`
- `test_draft_dedupe_spans_gmail_and_outlook`
- `test_the_sent_proof_holds_from_gmail_to_outlook`
- `test_the_sent_proof_holds_from_outlook_to_gmail`

**Mutations.** M1 drops the Message-ID in the Gmail parse, and each R8 case fails. M2 strips the
angle brackets in the Gmail parse only, and each R8 case fails.

**Verify with.**

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_email_duplicates.py tests/unit/test_email_ai_context.py \
  tests/unit/test_gmail_parse.py -v -rs
```

The R8 tests must show PASSED, with 0 skips.

**As built (2026-10-05, branch `email-gmail-g9`).** No SQL and no provider code changed.

- **The two reads.** `tests/unit/_email_pair_rows.py` gives each row from the real read of its
  provider. A Gmail row comes from `GmailProvider.get_message` over an EM-G2 fixture. An Outlook
  row comes from `OutlookProvider.list_messages` over one Graph message. `httpx.MockTransport`
  answers each request.
- **The Graph form is a literal.** The Outlook message carries the Message-ID as Graph gives it,
  with its angle brackets and its case. The test writes that value as a literal. A value taken
  from the Gmail parse would agree with each defect of that parse.
- **The write.** Each row goes through `persist.upsert_message`, with the `REKEYS_MESSAGE_IDS`
  flag of its provider (D-EM-34). Each read runs as `acb_app_h3rls`.
- **Fixture (d)** holds a Message-ID in mixed case. So a parse that changes the case also breaks
  the pair.

**The fences, as built.** 8 new R8 cases. The run of the three files gives 165 passed, with 0
skipped.

| Test | File | What it proves |
|---|---|---|
| `test_also_in_pairs_a_gmail_and_an_outlook_copy` (2 cases) | `test_email_duplicates.py` | Each copy names the other mailbox, in the list, in one mailbox and in search. Both rows hold the Graph form. |
| `test_draft_dedupe_spans_gmail_and_outlook` (3 cases) | `test_email_duplicates.py` | Gmail drafts first, or Outlook drafts first, or the member made the draft in Gmail web. The other mailbox makes no draft, and the skip reason is `answered`. The Gmail label `DRAFT` gives the folder `drafts`. |
| `test_the_try_lock_spans_gmail_and_outlook` | `test_email_duplicates.py` | The Outlook run meets the lock of the Gmail run, and the skip reason is `busy`. |
| `test_the_sent_proof_holds_from_gmail_to_outlook` | `test_email_ai_context.py` | Before the Gmail Sent copy syncs, the cold check runs (EM-G9-f1). After the sync, the proof holds and the check skips. The thread is self-only, so it is never awaiting. |
| `test_the_sent_proof_holds_from_outlook_to_gmail` | `test_email_ai_context.py` | The same, from an Outlook Sent copy to a Gmail Inbox copy. |

**Mutations, as run (2026-10-05).** A script ran each mutation against the 8 cases on a real
Postgres. Each file then went back to its exact SHA-256.

| Id | Mutation | Red cases |
|---|---|---|
| M1 | The Gmail parse sets no Message-ID (`gmail.py`) | 8 of 8 |
| M2 | The Gmail parse strips the angle brackets (`_gmail_message_id`) | 8 of 8 |
| M3 | The Outlook parse strips the angle brackets (`outlook.py`) | 8 of 8 |

Each red case failed on its behaviour: an empty "Also in", a second draft, or no proof.

**Known limit EM-G9-f1, the order of the syncs.** Gmail polls, and Outlook gets a push. So a mail
from Gmail to Outlook can meet the Outlook cold check before the Gmail Sent copy syncs. The check
then has no proof, and it runs as for any mail with no proof. This fails safe.
`test_the_sent_proof_holds_from_gmail_to_outlook` shows it.

**Known limit EM-G9-f2, the forms of a Gmail address.** Gmail can deliver mail to other forms of
one address: other dots, a `+tag`, or the domain `googlemail.com`. Each compare reads
`email_address` exactly. So a Sent copy to such a form proves nothing, and the self set does not
hold that form. The cold check then runs. This fails safe.

**Known limit EM-G9-f3, the raw Message-ID.** The fences prove that the two parses keep one value.
No test can prove that Gmail and Graph give the same raw value for one mail. EM-G10 check 6 tests
it live.

#### 12.3.12 EM-G10 — live acceptance (owner)

**Gate.** 🔴 OWNER-GATE. The owner registers the Google app (§12.4 steps 1 to 8), names the test
user (Q-GM-3) and gives the go. An agent writes the env under gate `env-write` and runs the checks.

**Order.** Last. ~~After EM-G1 to EM-G5, EM-G7, EM-G8 and EM-G9.~~ **Amended (D-EM-36,
orchestrator, 2026-10-05):** the owner's test runs after EM-G5a, EM-G9 and EM-G7b merge. The
other slices that it needs merged before: EM-G1, EM-G2, EM-G3a, EM-G4a, EM-G4b, EM-G7 and EM-G8.
EM-G6a and EM-G6b are not needed (D-EM-32). The list "Before customers" below holds what moved.

**Size.** S. A run of the checks, and a report.

**The checks,** on production, for one member with a Gmail mailbox and an Outlook mailbox.

1. Before the connect, count the `gmail` rows in `email_accounts`. Record the count, not the rows.
2. Open Email. The Gmail choice is live, and "Coming soon" is gone.
3. Connect Gmail. Google shows the two scopes of D-EM-31, and the callback opens the range step.
4. Clear one scope on the consent page in a second try. The callback shows `scope_missing` and
   saves no row.
5. Import 1 month. The progress shows a count and an estimate, newest first, and ends.
6. Send one mail to both addresses. Each row shows "Also in".
7. In Gmail web, archive, read, star and label one mail. Each change shows in Metorite within one
   poll.
8. Do the same acts in Metorite. Each change shows in Gmail at once.
9. Reply from Metorite with the HTML signature. The reply threads in Gmail and at an Outlook
   recipient.
10. Save a draft in Metorite. Gmail shows one draft. Send a draft made in Gmail web from Metorite.
11. Turn sync off for one hour, send mail, and turn it on. No mail is lost.
12. Disconnect Gmail. The copy in Metorite goes, and the mail stays in Gmail.
13. Read one mail with an RFC 2047 sender name through `format=full` (EM-G2 review round 1). Record
    whether Gmail sends the encoded word raw or decoded. A decoded name with a comma and no quotes,
    such as `Müller, Jürgen`, still splits in a `To` list.
14. Open the Archive folder and "Load older". Record that Gmail search takes `GMAIL_ARCHIVE_QUERY`
    (`-in:inbox -in:sent -in:drafts`) and gives the archived mail.
15. Read one mail with an ISO-8859-1 body. Record that `body.data` keeps the charset of its part,
    so the body shows with no U+FFFD.

**Evidence.** The log lines of each step, the row counts and the screenshots. Report each
production act in the same message (CLAUDE.md §3a rule 2).

**Non-goals.** Google verification and CASA (§12.4 steps 10 to 15). They come before customers,
not before this check.

**Before customers.** The amendment of D-EM-36 (§12.2) moves the first three items past the
owner's test. The other items are known limits of merged slices. Each item must close before a
customer connects Gmail, and no item has an owner yet.

1. **EM-G5b**, the reconcile half of EM-G5 (§12.3.6). It needs a confirm by message id (E-G5-9).
   🔨 Built, not merged (2026-10-05): the confirm asks Gmail by the provider id (§12.3.6.2).
2. **EM-G3b**, a move to a user label, and the filter list (§12.3.4).
3. **EM-G3c**, a new slice with no section yet. It owns EM-G3a-f8: the size limit of a plain
   `drafts.update`, and the autosave of a draft with files (§12.3.3).
4. **EM-G4b-f1.** The cursor moves only at the end of a read. So a large backlog can start again at
   the old cursor (§12.3.5.2).
5. **EM-G2-f1.** A large text part that Gmail sends by `attachmentId` gives an empty body
   (§12.3.2).
6. **EM-G3a-f7.** An update of a draft made in Gmail web drops its inline images (§12.3.3).
7. **EM-G4a-f1.** A `batchModify` that stops on a rate limit falls back to one `modify` for each
   message (§12.3.5.1).
8. **The known limits of EM-G8** (§12.3.10). The rule editor suggests no Gmail user label. The
   reconnect banner does not read the capability read. `provider_unavailable` shows the generic
   failure.
9. **EM-G3b-f7.** The health metric and the repair script miss a Gmail thread that a custom
   `MOVE_FOLDER` rule moved. `DAMAGED_CONVERSATION_THREADS_SQL` compares the rule label with the
   folder. The fix changes SQL text and needs an R8 test (§12.3.4).

### 12.4 Owner runbook — register the Metorite Google mail app (D-EM-5 amended, D-EM-31)

These are one-time owner acts. No customer ever repeats them. An agent cannot do them, because
each one needs a Google account that Metorite owns (`work_plan.md` §6.0 B).

**Now, for EM-G10 (Testing mode).**

1. Create a Google Cloud project for mail only, under a Google account or organization that
   Metorite owns. Do not use the project of the Google sign-in client (`AUTH_GOOGLE_ID`,
   `GOOGLE_SSO_CLIENT_ID`). A restricted-scope review of the mail app then cannot stop sign-in.
2. In **APIs & Services → Library**, enable the **Gmail API**. Enable the **Cloud Pub/Sub API**
   only when EM-G6a is next.
3. In **Google Auth Platform → Branding**, set the name `Metorite`, the logo and a support email.
   Set the home page, the privacy URL and the terms URL on `metorite.com`.
4. Add `metorite.com` as an authorized domain. Verify `metorite.com` in Google Search Console with
   an account that owns the project.
5. In **Audience**, set the user type to **External** and the status to **Testing**. Add each test
   Gmail address as a test user. Google allows 100 at most.
6. In **Data Access**, add `https://www.googleapis.com/auth/gmail.modify` and
   `https://www.googleapis.com/auth/gmail.settings.basic` (D-EM-31). Both are restricted scopes.
7. In **Clients**, create a client of the type **Web application**. Add the redirect URI
   `https://app.metorite.com/api/email/oauth/gmail/callback`. For local tests, also add
   `http://localhost:3001/api/email/oauth/gmail/callback`.
8. Give the client ID and the secret to an agent session through a one-time channel. You can also
   write them on the box yourself. Do not paste the secret into a chat (H-207 shows the cost).
9. The agent writes `GMAIL_OAUTH_CLIENT_ID` and `GMAIL_OAUTH_CLIENT_SECRET` to the box under gate
   `env-write`. It confirms `WORKBENCH_PUBLIC_URL`, `GATEWAY_PUBLIC_URL` and
   `GATEWAY_SESSION_SECRET`, which Outlook uses already.

**The dark flag (D-EM-36).** The box holds a Google client today, and nobody in this plan set it
(Q-GM-6). So `EMAIL_GMAIL_CONNECT` keeps the Gmail connect dark until EM-G10. Set it to `true`
only for the owner's test, under gate `enforcement-flip`. That test waits for EM-G5a, EM-G9 and
EM-G7b to merge (D-EM-36, amended 2026-10-05).

**The member list (EM-G7b, gate `enforcement-flip`).** While the app is in Testing, set
`EMAIL_GMAIL_CONNECT_MEMBERS` with the flag. It holds the Metorite sign-in address of each test
member, with a comma between addresses. Only a listed member then sees Gmail, and each other
member still sees "Coming soon". Each test member also needs a Gmail address on the test-user
list of step 5. Leave the list empty only after Google verifies the app (step 15).

**How to flip it.** Set both names in the env file of the box, because no Integrations write can
set an `EMAIL_*` name. Then restart the gateway. A later read of the file never replaces a value that
the process loaded at start.

**The limits of Testing.** Read these before EM-G10.

- Google issues a refresh token that ends after 7 days for an External app in Testing. So each
  test mailbox reconnects once a week, through the reconnect banner.
- Google shows "Google hasn't verified this app" to each test user. Expect that screen.
- **Optional, for a Workspace domain.** A Workspace admin can trust the app by its client ID. The
  path is Admin console → Security → Access and data control → API controls.
- Then open Manage third-party app access → Configure new app, and choose **Trusted**. Members of
  that domain can then connect, also when the domain blocks apps that Google did not verify.
- Trust does not remove the limit of 7 days.

**Before customers (Production).**

10. Submit the app for verification: the brand, and the restricted scopes. Give a reason for each
    scope, and say why a narrower scope does not serve. Record a demo video of the OAuth flow and
    of each feature that uses the scopes.
11. Update `site/privacy.html` and the terms. Today they name Google for sign-in only
    (`site/privacy.html:107`, `:149`).
12. The privacy text says how Metorite reads, uses, stores and shares Gmail data. It states that
    Metorite meets the Limited Use rules of the Google API Services User Data Policy.
13. The privacy text names the AI sub-processors of D-EM-9 (TypeSafe, AI/ML API). It states that
    no Gmail data trains a general AI model. Q-GM-5 asks the owner to confirm this.
14. Pass the CASA security assessment with a lab that Google authorizes, once each year. It costs
    a fee and takes weeks. 🔴 Money and a third party: an owner act only (CLAUDE.md §3a rule 3).
15. Publish the app (**Audience → Publish app**) only when verification passes. An app that Google
    did not verify shows the warning, and Google caps it at 100 new users.

**Engineering before customers.** These are not owner acts. An agent slice closes each one before
the app moves to Production.

- **Known limit EM-G4a-f1** (§12.3.5.1). A bulk act that falls back to one `modify` for each
  message can send many quick requests to a mailbox that Gmail limits. EM-G5 does not own it
  (E-G5-8). A later slice fixes it, with its own audit.

**For push (EM-G6a and EM-G6b), when push is next.**

16. In Pub/Sub, create a topic for Gmail. Grant `gmail-api-push@system.gserviceaccount.com` the
    role **Pub/Sub Publisher** on it.
17. Create a push subscription to `<GATEWAY_PUBLIC_URL>/email/webhook/gmail`, with
    authentication on. Choose a service account and an audience. The base is the base of the
    Graph webhook.
18. Give the topic name, the service account and the audience to an agent. It writes
    `GMAIL_PUSH_TOPIC`, `GMAIL_PUSH_SERVICE_ACCOUNT` and `GMAIL_PUSH_AUDIENCE` under gate
    `env-write`. To set `EMAIL_GMAIL_PUSH` is gate `enforcement-flip`.

**Effect on mailboxes connected today.** No member has connected a Gmail mailbox (§12.1). A test
mailbox that connects in Testing must reconnect once after the app moves to Production. Its
refresh token from Testing still ends after 7 days.

### 12.5 Owner questions

- **Q-GM-1. Which Google account or organization owns the Google Cloud project of the mail app?**
  The proposal is an organization that Metorite owns, apart from the project of Google sign-in.
- **Q-GM-2. Start Google verification now, or after EM-G10 in Testing?** The proposal is to verify
  the domain and the brand now, because that costs nothing and the review takes weeks. Start the
  restricted-scope review and CASA after EM-G10, because the demo video needs the finished flow.
- **Q-GM-3. Which Gmail account is the test user of EM-G10?** The proposal is one personal
  `@gmail.com` account that Metorite owns, and one Workspace mailbox if Q-GM-4 names a domain.
- **Q-GM-4. Which Google Workspace domain, if any, tests the app as Trusted?** A Workspace mailbox
  shows the admin paths of EM-G8 that a personal account cannot show.
- **Q-GM-5. Does D-EM-9 cover Gmail content?** Google's Limited Use rules bind each transfer of
  Gmail data, also to TypeSafe and AI/ML API, and the privacy text must name them. The proposal is
  yes, on the same terms as Outlook, with the text of §12.4 steps 12 and 13. This is a legal and
  third-party call, so the agent does not make it.
- **Q-GM-6. Whose Google client does the box hold today?** The box `.env` holds
  `GMAIL_OAUTH_CLIENT_ID` and `GMAIL_OAUTH_CLIENT_SECRET` (measured by name, 2026-10-05). Nobody in
  this plan set them. Name its Google Cloud project, its redirect URIs and the scopes it allows. If
  it is not the mail app of §12.4, replace it at step 9 before EM-G10 (D-EM-36).
