# WhatsApp Message Manager — plan & feature brainstorm

> **Product:** Metorite · **Feature:** WhatsApp channel vertical (channel #2, after email)
> **Created:** 2026-07-23 · **Revised:** 2026-07-23 (v3 — calm-UI pass grounded in a prior-art
> study; v2 — official WhatsApp Business Platform only, per founder decision; unofficial
> linked-device routes dropped)
> **Status:** ✅ **BUILT W0–W14 per §11** (2026-07-24) — routes in
> `apps/services/gateway/gateway/routes/whatsapp/`, migrations 102–111, 227 backend unit
> tests; activation gated on env config (`WHATSAPP_ENRICHMENT`, `WHATSAPP_APP_SECRET`,
> `WHATSAPP_PUBLIC_URL`, …) and Meta app review (Embedded Signup). §1–§10 are the design
> record; §11 is the build log and current state. · sibling surface: `whatsapp_calls_note_taker.md` Surface C (calls + recording) SHIPPED 2026-08-02 on this stack *(cross-ref added 2026-08-09)*
> *(Update 2026-08-01, doc-truth pass: header previously said "PLANNING — no code yet",
> contradicting §11's own build log; verified against the repo.)*
> **Mockups:** `mockups/whatsapp_message_manager.html` (7 screens + build notes, control-plane shell,
> rebuilt around a single organizing spine — see §7)
> **Anchors:** ADR-007 (WhatsApp via Meta Cloud API), `email_app_master_plan.md` (the vertical
> to mirror), `agent_registry.json` → `agent-triage` ("email / WhatsApp / meeting triage").

This doc is the "what and why" for managing WhatsApp the way the email app manages
mail: see what needs a reply, auto-handle the routine, digest the rest, categorize
senders — plus the things WhatsApp uniquely needs (groups, labels, voice notes,
multilingual threads) and an AI companion to run it hands-free. The audience is a
founder-CEO whose *actual* inbox is WhatsApp: in this business, customers, dealers,
vendors, service escalations, payments, the team, investors and family all arrive
on the same thread list, with no Cc, no folders and no off switch.

---

## 1. The problem, stated honestly

Email got fixed: the email vertical ships a ranked reply queue, sender categories,
learned rules, drafts in the user's voice, a daily digest-dashboard and an
assistant agent with 42 tools. WhatsApp — the channel where an Indian hardware
company actually transacts — still runs raw on a phone:

1. **It's an interrupt stream, not an inbox.** 200+ messages/day across 200+
   chats; the one ₹11 L purchase order sits between a dealer-group meme and a
   school parents' group. There is no triage surface at all.
2. **Groups bury the signal.** 18 dealer/team groups × dozens of messages; the
   one @mention that needed the founder dies above the fold.
3. **Promises evaporate.** "Installation by month-end" typed at a red light never
   becomes a task. Nobody chases what the *other* side promised either.
4. **Everything needs the founder because nothing carries context.** Only the
   founder knows this sender owes ₹3.2 L — the phone can't say so. So the phone
   *is* the CRM, and the CRM is a human.
5. **The routine eats the day.** Order-status asks, price-list requests,
   "location?" — identical answers, typed by hand, 40× a day.
6. **It never closes.** Family and VIP customers share one notification channel;
   the only options are "always on" or "missing things".

### The high-level value we sell (to ourselves)

| # | Value | One-line proof |
|---|---|---|
| V1 | **An obligation queue, not a chat list** | Open to 7 ranked "needs reply" items, not 200 unreads |
| V2 | **Hours back daily** | Routine asks answered from Odoo/price-list automatically, with an audit log |
| V3 | **No dropped promises — either direction** | Commitments extracted from both sides' messages, tracked into tasks, nudged |
| V4 | **Groups become one paragraph** | Per-group daily summaries; @mentions surface instantly |
| V5 | **The whole company standing behind every reply** | Zoho deal, Odoo ledger, ClickUp tasks, email history beside the thread |
| V6 | **Your voice at scale** | Drafts in the founder's WhatsApp register (short, warm, emoji-tolerant, Hinglish-aware) — never auto-sent to people who matter |
| V7 | **Calm by policy** | Per-category notification/escalation policy; digest catches the rest; Focus Shield holds pings |
| V8 | **Trust through visibility** | Every automated action listed, reviewable, revocable — same doctrine as email rules |

---

## 2. What already exists to build on (ecosystem fit)

The email vertical is the template; almost every hard part has a proven home.
This is the single strongest argument for building WhatsApp *inside*
Metorite rather than buying a WhatsApp inbox SaaS.

| WhatsApp need | Existing machinery to mirror/reuse | Where |
|---|---|---|
| Channel connector abstraction | `BaseEmailProvider` ABC + normalized message dataclasses + `factory.build_provider` | `apps/services/email_ingestion/email_ingestion/providers/` |
| Continuous sync per account | Per-account asyncio scheduler, incremental cursor, backoff, hot add/remove | `email_ingestion/scheduler.py` |
| One write path | Shared `upsert_message()` ON CONFLICT upsert | `email_ingestion/persist.py` |
| New-message pipeline | Post-sync hook registry (classify → thread status → digest → follow-ups), layering-inverted | `email_ingestion/post_sync.py` + `gateway/routes/email/scheduler_hooks.py` |
| Classifier | Deterministic learned patterns first, then tier-fast LLM rule pick with guidance/hints | `gateway/routes/email/automation/engine.py` |
| Needs-reply model | Reply Zero thread status (NEEDS_REPLY / AWAITING / FYI / DONE) | `automation/replyzero.py`, `email_thread_status` |
| Drafts in the user's voice | 5-layer voice priority (explicit style > voice profile > learned style), sender examples, quote-stripped learning | `automation/drafting.py`, `voice_profile.py` |
| Digest + dashboard | One `_generate_digest()` computation, two projections (mail + in-app dashboard) | `routes/email/digest.py` |
| Rules with nested learnings | `email_rules` + `_actions` + `_executed_rules` + `_rule_patterns` + `_rule_guidance`, one Rules screen | migrations 19/21/31/85/87 + `RulesTab.tsx` |
| Sender rollups | `email_senders` category projection + auto-learn with consent | `automation/senders.py` |
| Message → task | `TaskCaptureModal` + `routes/tasks/capture_email.py` (`origin.emailId` → `origin.waMessageId`) | tasks routes |
| Assistant with tools + HITL | `agent-email-assistant` (42 tools, `request_confirmation` fail-closed) | `apps/agents/agent-email-assistant/agents.py` |
| Outward-write approval | Action Broker approval queue — a WhatsApp broadcast to 18 groups is exactly what it exists for | `apps/services/action_broker/` |
| Entity linking phone ↔ CRM | graphiti/mem0 + `skills/triage/entity_link` | `packages/acb_memory/`, skills |
| Credentials | Integration Registry + BYOK encrypted key store (ADR-022) | `packages/acb_llm/key_store` |
| Model tiering | `rule_model` tier-fast / `draft_model` tier-powerful / `chat_model` tier-balanced per account | `email_assistant_settings` pattern |

**What does *not* exist:** any messaging transport. The Slack/Telegram "coming
soon" UI in `RulesTab.tsx` is flagged dead code in the email master plan. WhatsApp
is greenfield channel #2 — we mirror the email vertical's *shape*, not its code
paths, and we resist inventing a premature generic "channels framework" until the
second messenger proves the abstraction (same doctrine the email app used:
single-provider first, generalize later).

---

## 3. Integration — the official WhatsApp Business Platform, only

**Decision (2026-07-23):** build exclusively on Meta's **WhatsApp Business Cloud
API** — the only supported path for new integrations since Oct 2025 — using
**coexistence** so the founder's existing WhatsApp Business app keeps working on
the phone. This confirms and extends ADR-007. Unofficial protocol bridges
(wacli / whatsmeow / Baileys / whatsapp-web.js) are **rejected**: they violate
WhatsApp's ToS and risk a permanent ban of the number — unacceptable for the
company line, and not worth maintaining a second transport for. Should Meta ever
open an official path for personal numbers, that becomes a new decision.

### 3a. What the official platform gives us

| Capability | How |
|---|---|
| Connect without losing the phone app | **Embedded Signup + coexistence**: scan a QR from the WhatsApp Business app; the number becomes API-enabled while the app keeps working |
| History | ~6 months of chat history synced at onboarding (delivered in phases — the Connect screen shows progress honestly) |
| Real-time, both directions | Webhooks mirror inbound *and* outbound — including messages the founder types on the phone — so the store is complete from day one |
| Labels | Business-app labels import at onboarding; mirrored two-way where the API exposes label events, `sync_state` per label keeps the UI honest (🏷 synced vs local) |
| Sends | Free-form replies inside each chat's **24 h customer-service window**; outside it, pre-approved **template messages** (per-message pricing) |
| Media | Documents, images, voice notes in and out via the media API |
| Trust | ToS-compliant, no ban risk, quality rating visible; business verification badge |

### 3b. Constraints we design around (not against)

| Constraint | Design response |
|---|---|
| 24 h service window | Window state is ambient UI: a header pill on every thread ("session open · replies free · 21 h left") and the composer switches to template mode automatically. Nudges/chases outside the window use an approved template set, with cost shown before send |
| Template approval latency | The standing-rules that need templates (payment chase, follow-up nudge) ship with a small pre-approved template library created at onboarding |
| Business numbers only — no personal SIMs | Personal WhatsApp is **out of scope for v1**. The business line is where the business runs; family/personal contacts who message it get a hands-off category (surfaced, never AI-touched) |
| Label API coverage is partial/evolving | Import-once is the floor; two-way sync where events exist; per-label `sync_state` so the UI never lies about what's mirrored |
| Webhook delivery is at-least-once and can burst | Ingestion lands webhooks on a queue → the shared upsert path (idempotent on `wa_message_id`); a periodic reconcile job backfills gaps |
| Meta platform churn (coexistence is new) | All Meta specifics live behind the provider seam; worst case we degrade to standard Cloud API behavior (no history import) without touching triage/AI layers |

### 3c. Rejected alternatives (for the record)

wacli's architecture (QR-paired linked device via whatsmeow, local SQLite+FTS
mirror, webhook fan-out) remains a useful *design reference* for the sync/store
shape — but as a transport it is third-party protocol emulation: WhatsApp
actively bans numbers using it, bans are permanent, and no triage feature is
worth the founder's number. Baileys and whatsapp-web.js share the same
disqualifier. WhatsApp MCP bridges validate the "agent runs your WhatsApp" UX we
want, but with none of the store, triage or HITL discipline this plan requires.

---

## 4. Feature set (ranked brainstorm)

### 4.1 The Reply Queue — WhatsApp Reply Zero ⭐ the core

- Every chat carries a status: **NEEDS_REPLY / AWAITING / FYI / DONE**
  (replyzero ported; clamps to AWAITING when we spoke last). Groups get a
  variant: **needs-you** only when @mentioned, directly addressed, or a
  question lands in a group where the founder is the obvious answerer.
- Dashboard ranks by `sender importance × intent × age × deadline language`
  ("by today", "client demo tomorrow") — same priority spirit as the email
  reply queue. Every row carries its prepared next action: ✍️ draft ready,
  → task hand-off, or open-thread.
- The five dashboard numbers: needs reply · commitments due · waiting on them ·
  auto-handled · muted. Unread count is deliberately absent (V1).

### 4.2 Categories = WhatsApp Business labels, upgraded to policy carriers ⭐

- **Label import + mirror.** Business-app labels import at onboarding and
  mirror two-way where the coexistence API exposes label events; `sync_state`
  per label (🏷 synced vs local) keeps the UI honest, and field staff using
  the phone's Business app keep seeing the same labels either way.
- A category answers four questions (this is the upgrade — labels become
  behavior): **notify** (instant / digest / @mention-only / never),
  **auto-reply** (never / holding reply / answer-from-system), **AI drafts**
  (always-ready / on-intent / never), **escalate if unanswered** (2 h → push…).
- Ships with defaults mapping the Business app's stock labels (New customer,
  Order, Pending payment, Paid) + ours (★ VIP, Team, Vendors, Dealer groups,
  Family & personal, 🔇 Noise). `Uncategorized` is a state, not a label
  (email doctrine).
- **Auto-learn with consent:** classifier proposes homes for new senders
  (message content + phone-number ↔ Zoho/Odoo contact match); acceptances
  become visible learned patterns under the Rules screen.
- **AI stays out of Family & personal by default.** Surfaced, never drafted,
  never auto-handled. This line buys more founder trust than any feature.

### 4.3 Drafts in your WhatsApp voice ⭐

- Reuses the 5-layer voice stack, but learns a **separate register per
  channel and per relationship**: WhatsApp ≠ email (shorter, warmer, emoji,
  "🙏", Hinglish code-switching); dealer ≠ investor ≠ service engineer.
- **Reply in the sender's language.** Detect Hindi/Kannada/Tamil/English (or
  mixed), draft in kind, one-tap switch. This is table stakes for the dealer
  network and something no US-built inbox tool does well.
- Variant chips on every draft: shorter · warmer · drop-the-ask · translate.
- Never auto-sent on VIP/Family; elsewhere per the category's autonomy setting.

### 4.4 Standing rules + the autonomy ladder

- Office-hours autoresponder with an URGENT escape hatch that pages the founder.
- **Answer-from-system rules:** order status from Odoo (live status + AWB),
  price-list PDF on request, catalog/spec-sheet sends. Read-only answers may
  fully automate; anything committing money/dates/reputation is drafts-only.
  The ladder is per-rule and visible.
- **Payment-chase cadence** on 🏷 Pending payment: 7d polite → 14d firmer +
  accounts CC'd via email (cross-channel!) → 21d escalation pack to founder.
  Outside the 24 h window the cadence uses the approved `payment_reminder`
  template — cost surfaced at the rule level.
- **Plain-language rule compiler** ("when a dealer asks for the edu deck, send
  the latest PDF and label them Edu-pipeline") → compiled rule shown for
  approval — same `email_rules.instructions` pattern.
- **Hard guardrails (not AI-editable):** never auto-send to ★ VIP or Family;
  no prices outside the published list; never promise delivery dates; ≤20
  auto-replies/hour; templates only from the approved set; every send
  audit-logged. Rules show honest stats ("41 handled, 0 corrections") —
  trust is earned.

### 4.5 Digest + dashboard (projection pattern)

- WhatsApp section joins the existing morning digest email and in-app
  dashboard: ≤3 "needs you first" items with prepared actions, commitment
  watch, group roll-up, the trust ledger (handled + muted, sample-checked).
- Same `_generate_digest`-style single computation, two projections.

### 4.6 Commitment & waiting-on tracking (both directions) ⭐

- Extract commitments from **our outbound** ("installation by month-end") →
  reconcile against `gtd_items`/calendar → digest flags the promise that never
  became work → one tap creates the task (`origin.waMessageId`).
- Extract **their** promises ("will send AWB tomorrow") → waiting-on strip with
  aging + one-tap ✍️ nudge drafts (email's per-thread Nudge, ported).
- Silence triggers: "remind me if no reply in 2 days" armed from any thread.

### 4.7 Group intelligence

- Per-group daily/weekly AI summaries (dealer sentiment, questions asked,
  decisions made); quiet groups report as "nothing for you".
- @mention/direct-address detection promotes a group into the reply queue.
- **Broadcast composer:** summarize-then-respond ("draft a monsoon delay update
  to all dealer groups") — always through the Action Broker approval gate.

### 4.8 The person, cross-channel ⭐ the moat

- Context rail beside every thread: Zoho deal stage & lifetime value, Odoo
  invoices (overdue!), ClickUp open loops, recent email, past commitments.
  Phone-number ↔ contact entity linking via graphiti + `entity_link` skill.
- The killer save: AI reads the incoming PO (4.10), sees the overdue invoice,
  and the draft confirms specs *and* nudges payment — before the founder
  extended more credit by reflex.
- One-tap: chat → ClickUp task, → CRM note, → email thread continuation.

### 4.9 AI companion — run WhatsApp by talking ⭐

- Not a new app: the existing Control-Plane chat (and its mobile PWA) gains a
  `wa_*` toolset on `agent-triage` / a new `agent-whatsapp-assistant`,
  mirroring the email agent's tool families: read/triage (`wa_find_needs_reply`,
  `wa_summarize_group`, `wa_query`), actions (`wa_draft_reply`, `wa_send` —
  HITL-gated), categories (`wa_categorize`), rules, digest.
- The scenarios that matter: *"What did I miss during the board meeting?"* ·
  *"Reply to Rajesh — confirm specs, keep the payment ask warm"* · *"What is
  the dealer network saying about monsoon delays? Draft a broadcast."* ·
  *"Who am I ignoring?"* · *"Mute Dealer North till Monday."*
- Voice-first works because the agent holds the queue + drafts + context; the
  founder supplies one sentence of judgment from a cab.

### 4.10 Media understanding

- POs/invoices/screenshots → OCR + structured extraction (amount, items,
  delivery terms) feeding drafts and CRM notes.
- Voice notes → transcription (acb_stt exists for the Note Taker) → searchable,
  summarized, triaged like text. Dealer voice notes are a way of life.

### 4.11 Search & memory

- FTS + semantic search across the synced WhatsApp history, landing in
  Postgres + pgvector exactly like email.
- "What did we quote Meher in March?" answers across WhatsApp *and* email.

### 4.12 Focus integration

- WhatsApp joins the Focus Shield: pings held during focus blocks, released at
  breaks. **WhatsApp windows** (like Email windows) become schedulable calendar
  blocks — the digest's "clear in 10 min ▸" deep-links into one.

---

## 5. AI implementation notes

| Concern | Approach |
|---|---|
| Intent classification | Mirror `engine.py`: learned deterministic patterns short-circuit → tier-fast LLM picks rule/intent with guidance + history hints. Intent taxonomy: `order_status · quote_request · payment · service_issue · scheduling · social · spam/promo` |
| Chat status | replyzero port; per-chat not per-thread (WhatsApp has no threads); group needs-you detection adds @mention/direct-address/answerable-question signals |
| Drafting | `drafting.py` pattern: thread history + sender examples + semantically-near past sends + system context (Odoo/Zoho blocks when intent warrants) on `draft_model` (tier-powerful); LLM failure returns sentinel, never a fake draft |
| Voice | `voice_profile.py` extended with `channel` + `audience` dimensions; learned from the founder's own past WhatsApp sends (quote-stripped, their side only) — the coexistence history import is what makes this possible on day one |
| Language | Detect per-message; draft in the thread's language; translation layer for the founder's reading pane (auto-translate toggle) |
| Commitments | Extraction prompt over outbound+inbound at classify time; writes candidate commitments; reconciler-style nightly diff against tasks/calendar |
| Cost control | Groups classified as digest-tier by default (batch summarization, not per-message LLM); Noise category exits the pipeline after cheap pattern checks; per-role model overrides as in `email_assistant_settings` |
| Send regimes | Every outbound decorated with its regime: `session` (free, inside 24 h window) vs `template` (₹, approved set only). Rules and the composer both surface this; templates are never improvised by the LLM |
| HITL | All sends via `request_confirmation` fail-closed; broadcast + bulk actions via Action Broker queue; autonomy ladder only ever widened by the human |
| Auditability | `wa_executed_rules` mirror + the dashboard trust ledger; every auto-send links to the rule that caused it |

---

## 6. Data model (mirrors email migrations 17→94)

```
wa_accounts        id, user_id, phone_number, display_name, waba_id, phone_number_id,
                   credentials via Integration Registry (BYOK), webhook_verify_token,
                   sync_status, history_import_phase, initial_sync_done, quality_rating
wa_chats           id, account_id, wa_chat_id (JID), kind('dm'|'group'|'broadcast'),
                   name, participants jsonb, category_id, service_window_expires_at,
                   UNIQUE(account_id, wa_chat_id)
wa_messages        id, account_id, chat_id, wa_message_id, direction, sender jsonb, kind
                   ('text'|'image'|'video'|'audio'|'voice'|'document'|'sticker'|'location'|
                    'contact'|'reaction'|'system'), body_text, transcript_text, media_ref,
                   quoted_wa_message_id, mentions text[], categories text[], intent,
                   template_name nullable (outbound template sends), send_regime,
                   sent_at, synced_at, rules_processed_at, UNIQUE(account_id, wa_message_id)
                   + FTS index + embeddings (pgvector)
wa_media           message_id, mime_type, size_bytes, storage_path, ocr_text, transcription_status
wa_contacts        account_id, phone_number, display_name, category_id, category_source,
                   entity_ref (graphiti/Zoho link), UNIQUE(account_id, phone_number)
wa_labels          account_id, wa_label_id, name, color, sync_state('synced'|'local'|'import_only')
wa_templates       account_id, name, language, body, meta_status('approved'|'pending'|'rejected'),
                   cost_hint — the approved template library rules draw from
wa_chat_status     PK(account_id, chat_id), status(NEEDS_REPLY|AWAITING|FYI|DONE),
                   reason, last_message_at, follow_up_reminded_at
wa_commitments     id, account_id, chat_id, message_id, direction('ours'|'theirs'), text,
                   due_hint, status(open|done|dismissed), gtd_item_id nullable
wa_rules / wa_actions / wa_executed_rules / wa_rule_patterns / wa_rule_guidance
                   — structural mirrors of the email quintet
wa_ai_drafts       (account_id, chat_id), draft_text, language, register
wa_assistant_settings
                   per-account: autonomy defaults, office_hours, guardrails, digest config,
                   rule_model/draft_model/chat_model, family_hands_off bool (default true)
```

Categories live as first-class rows (not just `text[]`) because they carry
policy: `wa_categories(id, account_id, name, icon, wa_label_id nullable,
notify_policy, auto_reply_policy, draft_policy, escalate_after_mins)`.

---

## 7. UI/UX — the calm design (see mockups v3)

### 7a. Prior-art study (what the mockups are grounded in)

Before v3 the mockups were feature-complete but cluttered — five stat cards, a
dense chip row, 3–4 pills + two buttons on every queue row, an always-on
multi-card context rail. A prior-art pass across paid and open-source WhatsApp
inboxes and the "calm/fast inbox" tradition showed that clutter is not a polish
problem, it's an information-architecture one, and that the fix is well-trodden.

**The tools reviewers call "clean, near-zero training":** Cooby (tabs +
inbox-zero over WhatsApp Web), Rasayel and WATI (clean three-pane), SleekFlow and
Kommo ("clutter-free", single spine), plus the calm-inbox canon — Superhuman
(one accent, keyboard-first, almost no visible buttons), HEY (Imbox/Feed/Paper
Trail — categories are *destinations*, not filters), Missive/Front/Shortwave
(context and AI summoned, not resident), and Chatwoot (the OSS reference: a
four-zone shell, Copilot-as-a-thread).

**The tools reviewers call "cluttered / steep learning curve":** Periskope,
Interakt, Gallabox, Trengo, Respond.io — and the specific complaints (too many
pills per row, a busy always-on rail, a KPI dashboard bolted onto the workspace,
multiple organizers stacked on one screen) were an exact description of our v2.

The convergent lesson — **one organizing spine, not five** — drove the rebuild.

### 7b. The six calm principles (now the house rules for this vertical)

1. **One spine.** Triage streams live in the nav; there is no stat wall and no
   chip cloud. The five numbers that were stat cards became stream counts —
   same data, clickable, one active at a time (HEY / Cooby / Rasayel).
2. **One triage number.** It's just the active stream's count. Volume, response
   time and other metrics live in a separate analytics view you visit on purpose.
3. **Quiet rows, revealed actions.** A row is avatar · name · one line · time ·
   at most one red dot ("needs you"); hover or keys surface reply/snooze/done.
   Inside a stream, rows don't re-state the category (Superhuman / WATI / Cooby).
4. **Two panes by default; context on demand.** List → thread; the CRM/ERP
   context is a "Details ▸" drawer (accordion'd, one section open), not a
   permanent rail (Missive / Front / Chatwoot).
5. **AI at the point of writing.** One "✦ Suggested reply" chip above the
   composer, expandable to a copilot thread — never a standing AI column
   (SleekFlow / Shortwave / WATI).
6. **One accent, greyscale rest.** Cyan = active/primary/AI; WhatsApp green =
   send & outbound only; a single red dot = needs you; gold ★ = VIP (a glyph,
   not a fill); amber only for real payment aging, in context. Everything else
   stays grey until it earns attention (Superhuman).

**The governing constraint for every future feature:** it lands as a new stream
in the nav, on a settings surface, or as a hover/drawer affordance — *never* as
another always-on element on the queue or the thread. That single rule is what
the cluttered tools violated, per their own reviews.

### 7c. The screens

Every screen renders inside the real control-plane shell (icon rail → streams nav
→ main) so layout, tokens and components translate 1:1 into
`workbench/control_plane/src/app/whatsapp/`. Screen 8 is a build-notes sheet:
the principle→screen→prior-art map, color-as-signal legend, and a component map
(mockup element → existing email/control-plane component to reuse → net-new).

| # | Screen | What it proves |
|---|---|---|
| 1 | **Connect** | Embedded Signup + coexistence stepper; observable phased import; one quiet capability card stating the 24 h window, template pricing and label caveats up front |
| 2 | **The queue** | The whole home is two panes: triage-streams nav (the single spine, replacing v2's stat cards + chip row) and quiet near-textual rows with a single "needs you" dot and hover-revealed actions |
| 3 | **Conversation** | List + thread; 24 h window as a quiet header chip + `/` template picker; AI as one "Suggested reply" chip in the composer; the CRM/ERP moat in an on-demand accordion drawer (the overdue-invoice interception) |
| 4 | **Categories** | A settings surface (depth allowed): labels as policy rows (notify / auto-reply / drafts / escalation), only the meaningful cell bright, Family "AI hands off", uncategorized drained by evidence-backed suggestions |
| 5 | **Rules** | Rule cards with honest stats + nested learnings, the autonomy ladder visible per rule, template costs surfaced, an un-editable hard-guardrails card, plain-language compiler |
| 6 | **Digest** | ≤3 "needs you first" with prepared actions; commitment watch both ways; sections divided by whitespace + one label color, not six filled cards |
| 7 | **Companion** | Mobile PWA chat with quiet genUI queue/draft cards; approve-&-send as the only send |
| 8 | **Build notes** | Principles→screens→prior-art map, color-as-signal legend, component reuse map — the implementation checklist |

---

## 8. Competitive positioning (why not buy?)

| Tool | What it is | Why it doesn't solve this |
|---|---|---|
| Periskope, TimelinesAI, Cooby | WhatsApp shared inboxes / CRM syncers for teams | Team-inbox economics, generic AI, no founder-personal triage, and none can see your ERP ledger, tasks, calendar or email while drafting |
| WATI / Interakt / BSP suites | Cloud-API marketing + support bots | Campaign/bot-first, not inbox-management; nothing for the founder's own thread list |
| WhatsApp MCP bridges | Agent access to WhatsApp | Right instinct, wrong transport (unofficial) and no store, no triage discipline, no HITL, no trust ledger |
| **Us** | The founder's whole company (CRM/ERP/tasks/email/calendar/memory) standing behind every WhatsApp reply, with email-grade triage doctrine, on the official API | The moat is the context graph + the already-earned trust patterns, not any single feature |

---

## 9. Risks & open questions

| # | Risk / question | Position |
|---|---|---|
| R1 | **Label API coverage** in coexistence is partial/evolving | Import-once + local ownership is the floor; two-way sync where the API allows; `sync_state` per label keeps the UI honest |
| R2 | **24 h window + template pricing** | Auto-replies are session messages (free, inside window); proactive nudges outside the window use the approved template library; regime + cost are ambient UI (thread pill, rule cards, draft cards) |
| R3 | **Privacy: business chats pipe through LLMs** | Family & personal category is hands-off by default (surface, never draft, never auto-handle); per-category AI opt-out; all processing through the gateway's existing BYOK/routing; audit log |
| R4 | **India DPDP / consent** for storing counterparty messages | Same posture as email (we store our own correspondence); document retention policy; deletion honored via reconcile pass |
| R5 | **Group consent optics** (summarizing groups) | Summaries are private to the founder — no content leaves; broadcast sends always human-approved |
| R6 | **Meta platform churn** (coexistence is new; history/label sync phases may change) | All Meta specifics behind the provider seam; worst case degrade to standard Cloud API (no history import) without touching triage/AI layers |
| R7 | **The personal SIM stays unmanaged in v1** | Accepted consequence of official-only. Practical mitigation: business traffic migrates to the business number over time (the Business app makes this natural); revisit only if Meta opens an official personal-number path |
| R8 | **Coexistence availability** (region/tier gating by Meta/BSP) | Verify eligibility for our WABA early in W0 — it's the plan's only hard external dependency; fallback is standard Cloud API onboarding with forward-only history |

---

## 10. Suggested phasing

> **Update 2026-08-01 (doc-truth pass):** this W0–W4 phasing is the original proposal and
> is superseded by §11, which records what was actually built as W0–W14 (the build split
> the work into finer slices and went well past W4). Read §11 for current state.

- **W0 — Pipe + store (no AI):** Cloud API onboarding via Embedded Signup +
  coexistence; webhook ingestion → queue → idempotent upsert into
  `wa_accounts/chats/messages/media`; history + label import with observable
  progress; FTS; read-only chat UI + Connect screen. *Exit: the founder reads
  and searches the company number's WhatsApp in the control plane.*
- **W1 — Send + hand-offs:** session-window-aware sends with HITL; template
  library bootstrap + approval tracking; message → task capture; contact ↔
  Zoho/Odoo entity linking. *Exit: replies sent from the dashboard; every chat
  shows its CRM context.*
- **W2 — Triage brain:** classifier + chat status + categories-as-policy +
  reply queue dashboard + digest section + auto-handled ledger. *Exit: the
  founder stops opening the phone app to find out what matters.*
- **W3 — Voice + automation:** drafting with WhatsApp voice profile +
  multilingual replies; standing rules (office hours, order-status
  answer-from-Odoo, payment cadence via templates); commitment/waiting-on
  extraction + nudges. *Exit: ≥50% of routine asks auto-handled with 0
  corrections/wk.*
- **W4 — Companion + groups:** `wa_*` agent tools in control-plane chat + mobile
  PWA; group summaries + broadcast-with-approval; media OCR + voice-note
  transcription; Focus Shield / WhatsApp windows. *Exit: a full day managed
  from the companion without opening WhatsApp.*

Each phase lands behind the existing patterns (provider seam, hook registry,
rules quintet, HITL) so nothing here forks the architecture — WhatsApp is the
proof that the email vertical's shape was a *channel* shape all along.

---

## 11. Build status (2026-07-23)

**W0 — pipe + store — BUILT.**
- `infra/postgres/102_whatsapp.sql`: `wa_accounts / wa_chats / wa_messages /
  wa_media / wa_contacts / wa_labels / wa_chat_status / wa_sync_log` + FTS.
- `apps/services/whatsapp_ingestion/`: provider seam (normalized dataclasses,
  `WhatsAppCloudProvider`, `parse_webhook` total parser, factory), the
  idempotent `persist` path, and the `post_sync` hook registry.
- `apps/services/gateway/gateway/routes/whatsapp/`: accounts, chats + `/streams`
  counts, messages + FTS search, window-aware send, and the public webhook
  (verify + receive → persist → hooks); registered in `main.py`.
- `workbench/control_plane/src/app/whatsapp/`: the calm read surface (streams
  nav, quiet queue, conversation) + the `/api/whatsapp` proxy + nav entry.

**W1 — send + hand-offs — BUILT (send/templates/capture/context).**
- `infra/postgres/103_whatsapp_templates.sql` + `transport/templates.py`: the
  approved template library (list / upsert / bootstrap default set).
- `transport/capture.py`: `/whatsapp/capture-task` — message → GTD inbox item,
  idempotent on `origin.wa_message_id`.
- `transport/context.py`: `/whatsapp/chats/{id}/context` — contact + entity ref
  + open loops + stats (live Zoho/Odoo fields deferred, degrades honestly).
- Frontend: window-aware composer (text / template picker), Details drawer,
  per-message capture affordance.

**W2 — triage brain — BUILT (backend, wired end-to-end).**
- `automation/replyzero.py`: the Reply Zero chat-status classifier
  (NEEDS_REPLY/AWAITING/FYI/DONE, DM vs group @mention) + `classify_chats` hook.
- `automation/intent.py`: a deterministic Hinglish-aware intent classifier
  (order_status/quote/payment/service/scheduling/social/spam) + the
  `on_new_messages` hook (idempotent via the `rules_processed_at` watermark).
- `104_whatsapp_categories.sql` + `automation/categories.py`: categories as
  policy carriers (notify/auto-reply/draft/escalate) with the default set
  (Family hands-off, Noise silent) + list/bootstrap/patch route.
- `digest.py`: the `/whatsapp/digest` projection (≤3 needs-you + calm counts).
- `scheduler_hooks.py` + `main.py`: the hooks are registered at startup and the
  webhook fires them, so an inbound message now flows webhook → persist → intent
  → chat-status → surfaces in `/streams`, the queue and the digest. The frontend
  stream counts light up from this automatically (no UI change needed).

**W3 — automation engine — BUILT (deterministic core, backend).**
- `automation/rules.py`: `decide_action` — the pure auto-reply autonomy ladder
  (answer_from_system / holding_reply / draft / none) with `requires_approval` +
  `via_template`, enforcing the hard guardrails first (VIP + Family never
  auto-send; Family hands off; social/spam muted). `/whatsapp/rules/preview`
  dry-runs it over needs-reply chats — what WOULD happen, no sends.
- `105_whatsapp_commitments.sql` + `automation/commitments.py`: promises tracked
  both ways. `extract_commitment` is pure + conservative (a promise verb is
  required), Hinglish/curly-quote tolerant, with a verbatim due hint;
  `apply_commitments` runs in the on_new_messages pipeline (watermarked on
  `commitment_checked_at`), tagging ours (digest watch) vs theirs (chase).
  `/whatsapp/commitments` lists them.
- `digest.py`: the brief now carries the commitment watch (our open promises,
  with a "never became a task" flag) + a waiting-on count.
- `106_whatsapp_ai_drafts.sql` + `automation/drafting.py`: AI drafting in the
  founder's WhatsApp voice (short/warm/emoji-tolerant, reply in the thread's own
  language via a pure Devanagari-vs-Latin detector), with the email drafter's
  two doctrines — conversation-as-DATA and sentinel-on-failure (NO_DRAFT / LLM
  error → no fabricated draft). Cached in wa_ai_drafts; generate/get routes. The
  composer gains a "✦ Suggest reply" chip (AI at the point of writing).
- `automation/outbound.py`: the approval-gated broadcast composer.
  `/whatsapp/broadcast` never sends directly — it routes ONE Action Broker
  proposal at SUGGEST authority (→ NEEDS_APPROVAL) and asserts the gating held;
  the real sends live only in the registered `whatsapp.broadcast` handler
  (broker non-negotiable #4). The founder's own explicit reply stays direct
  (that tap is the human in the loop); automation and one-to-many go through
  approval.

**W4 — companion + groups — BUILT (group intelligence + waiting-on nudge).**
- `108_whatsapp_group_summaries.sql` + `automation/groups.py`: "groups become one
  paragraph" (value V4). One cached AI summary per group chat — what was
  discussed, sentiment, whether the founder was addressed, and the ≤5 points
  worth their eye. Pure builder + parser (validates the sentiment enum, clamps
  key-points, rejects an empty summary); `summarize_group` OR-s the model's
  `mentions_you` with a deterministic @mention check so a direct address is never
  missed, and carries the drafting doctrines (transcript-as-DATA,
  sentinel-on-failure). `summarize_stale_groups` is a bounded (20/pass),
  watermarked (`covered_through`) schedule/digest trigger, never the hot webhook
  path. Routes: `POST /groups/{id}/summarize`, `GET /groups/summaries`.
- **Waiting-on nudge drafts (W4.2) — completes the "no dropped promises" loop.**
  The theirs-direction commitments (what they owe us) become one-tap chases.
  `commitments.py` gains a pure `build_nudge_messages` (gentle/warm register,
  conversation-as-DATA, NO_DRAFT sentinel) and `draft_nudge` (loads the 'theirs'
  commitment + a short thread excerpt, reuses the Devanagari language detector,
  sentinel-on-failure); `POST /commitments/{id}/nudge` returns a DRAFT the
  founder reviews and sends through the existing composer (which owns the 24h
  window / template logic — the nudge seam never sends). The `digest` now
  surfaces the waiting-on *list* (each with its nudge id), and the per-chat
  `context` rail carries the chat's open waiting-on commitments. The Details
  drawer renders a "Waiting on them" section with a "✦ Nudge" chip that drafts
  and drops the text into the composer.
- **Voice-note transcription (W4.3) — voice notes join the brain.** Dealers live
  on voice notes; they used to land as a bare `[voice]` with empty `body_text`,
  invisible to triage/intent/commitments/search. `persist.py` now marks
  voice/audio media `transcription_status='pending'` at ingest (canonical
  `is_transcribable` predicate lives in the lower layer). `automation/
  transcription.py` downloads the audio via the account's Cloud API provider,
  transcribes it through the platform STT tier (`acb_stt` → LiteLLM), writes
  `wa_messages.transcript_text`, and RESETS the intent/commitment watermarks so
  the transcript flows through the SAME deterministic classifiers — a spoken
  "kal AWB bhej dunga" becomes a real waiting-on commitment. The classifiers now
  read an effective-text (`COALESCE(NULLIF(body_text,''), transcript_text)`).
  STT is a network call, so it runs on demand (`POST /messages/{id}/transcribe`)
  or on a bounded schedule (`transcribe_pending`, 25/pass), never the hot webhook
  path; download/STT failure marks the media `failed` and returns a sentinel,
  never a fabricated transcript. The transcript feeds the existing FTS index
  (already covers `transcript_text`) and shows in the thread bubble, with a
  one-tap "Transcribe" chip on untranscribed inbound voice notes.

**W5 — companion agent — BUILT (the "companion AI" headline).**
`apps/agents/agent-whatsapp-assistant` is a MAF agent (structure mirrors
agent-email-assistant: `agents.py` `build_agents()` + `config.json`
`runtime: "maf"`, registered in the gateway allowlist + `_AGENT_REGISTRY`). It
gives the founder conversational command of the vertical — every tool is a thin,
user-scoped wrapper over the `/whatsapp/*` routes, so it inherits their
guarantees. 13 tools: read/triage (`list_whatsapp_accounts`, `whatsapp_brief`,
`list_whatsapp_chats`, `read_whatsapp_chat`, `search_whatsapp`,
`whatsapp_waiting_on`, `whatsapp_my_commitments`, `whatsapp_chat_context`),
understanding (`summarize_whatsapp_group`, `list_whatsapp_group_summaries`,
`transcribe_whatsapp_voice_note`), and drafting
(`draft_whatsapp_reply`, `draft_waiting_on_nudge`). **DOCTRINE: the companion
DRAFTS, the founder SENDS** — there is deliberately NO send tool, so the worst it
can do is prepare words the founder reviews (a free-form reply is the founder's
own tap; one-to-many goes through the broker). Tools carry `chat_id` /
`message_id` / `commitment_id` forward so a `commitment_id` from `whatsapp_brief`
feeds `draft_waiting_on_nudge` in one flow. Runs on the BYOK LiteLLM tier
(`tier-balanced`), never native Copilot.

**W6 — chat snooze / remind-me — BUILT (an inbox staple).** Defer a conversation
out of the triage queue until a chosen time, then let it resurface on its own.
`109_whatsapp_chat_snooze.sql` adds `wa_chat_status.snoozed_until` (a partial
index for the handful of snoozed chats). Snooze is an ORTHOGONAL overlay on Reply
Zero — the chat keeps its real status; the queue reads just filter
`snoozed_until IS NULL OR snoozed_until <= now()`, so a snooze **auto-expires with
no batch**. `transport/snooze.py` exposes `POST /chats/{id}/snooze` (pure
`parse_snooze_until` validates the client-supplied ISO instant — future, sane
horizon; the browser computes the absolute time in the founder's own tz) and
`POST /chats/{id}/unsnooze`. A **new inbound message wakes a snoozed chat**: the
`recompute_chat_status` upsert clears `snoozed_until` via a CASE that fires ONLY
when *that* chat's last message actually changed and the new one is inbound (a
blanket clear would defeat snooze, since `classify_chats` sweeps every chat). The
streams/list/digest all hide snoozed chats and a new **"Snoozed" nav stream**
surfaces them; the conversation header gains a Snooze menu (Later today / This
evening / Tomorrow 9am / Next week) and a one-tap "wake". An OUTBOUND reply never
wakes a snooze.

**W7 — Pulse (analytics / insights) — BUILT (the founder-CEO's "am I keeping
up?").** A read-only projection (`pulse.py`, alongside `digest.py`) over the
classified store: `GET /whatsapp/pulse?days=` returns the typical reply time
(median + p90 minutes from each answered inbound to the founder's next outbound),
how many they replied to, in/out volume + active chats, **who has waited longest**
(open NEEDS_REPLY, not snoozed, oldest first), inbound load **by intent**, and the
**busiest chats** over the window. The aggregation maths (`median`, `percentile`
nearest-rank, `summarize_response_times` — drops negatives/None, robust to
outliers) is pure and unit-tested; the reply-latency pull is a bounded LATERAL
next-outbound join, all validated on real Postgres 16. A calm `/whatsapp/insights`
screen (headline tiles + waited-longest list + intent bars + busiest list, with a
7/30-day toggle) hangs off a new "Pulse" nav entry. No migration, no LLM — honest
projection of what the pipeline already wrote.

**W8 — saved replies / quick snippets — BUILT (an inbox staple).** The answers a
founder types ten times a day (price list, address, GST number, catalogue link).
`110_whatsapp_saved_replies.sql` adds `wa_saved_replies` (title, body, optional
`/shortcut` with a partial unique index per account). `transport/saved_replies.py`
is plain account-scoped CRUD (`GET/POST/PATCH/DELETE /whatsapp/saved-replies`);
`normalize_shortcut` is pure (lowercased, single leading `/`, `[a-z0-9_]`, empty →
None) and the shortcut-collision surfaces as a 409. **Distinct from templates** —
a saved reply is a free-form snippet dropped into the composer INSIDE the 24h
window, where a template is the Meta-approved message required once it closes. A
`/whatsapp/settings/replies` CRUD screen manages them; the composer gains a
"⚡ Saved" picker (appends the snippet to the draft) beside "Suggest reply". The
partial unique index (NULLs coexist, duplicate shortcut rejected) was verified on
real Postgres 16.

**W9 — background enrichment scheduler — BUILT (makes W4.1 + W4.3 autonomous).**
Group summaries and voice transcription were built as bounded, watermarked batch
passes but only ran on-demand — WhatsApp had no background loop (it's
webhook-driven). `whatsapp/scheduler.py` adds ONE lightweight loop that
periodically sweeps every live account through `summarize_stale_groups` +
`transcribe_pending`, wired into the gateway lifespan (start on boot, cancel on
shutdown) alongside the email/tasks schedulers. **Cost-gated OFF unless
`WHATSAPP_ENRICHMENT=1`** (each cycle can call the LLM and STT); interval via
`WHATSAPP_ENRICHMENT_INTERVAL_SECS` (default 900s, min 120s). Both passes are
per-pass bounded and only touch stale/pending rows, so a caught-up account does
no work, and a per-account failure is logged, never fatal to the sweep. The pure
gate (`enrichment_enabled`) + interval clamp (`resolve_interval`) + the
single-cycle sweep (`run_enrichment_cycle`, with per-account error resilience)
are unit-tested; the on-demand routes keep working regardless of the flag.

**W10 — semantic search over history — BUILT (reuses the proven email/mem0
embedder).** Find a chat by MEANING, not just keywords — and because the embedded
text is the message body PLUS its voice-note transcript, a spoken "kal AWB bhej
dunga" is findable too. Faithfully mirrors the email vertical's Phase-2 embeddings
(migration 73): `111_whatsapp_embeddings.sql` adds `wa_message_embeddings`
(pgvector `vector(1536)`, ivfflat cosine, per-row model + content_hash);
`whatsapp_ingestion/wa_embeddings.py` embeds a bounded batch through the LiteLLM
gateway's `/v1/embeddings` (the SAME path mem0 + email use — **no new infra**, the
concern that deferred OCR does not apply here), gated by
`whatsapp_semantic_search_enabled` (reusing `email_embedding_model`). `/whatsapp/
search?hybrid=true` keeps recall LEXICAL (every FTS match returned) and only
RE-ORDERS by `0.5·ts_rank + 0.5·cosine`, so a keyword hit is never dropped and an
unembedded message still ranks on its lexical score. The enrichment sweep (W9)
also backfills embeddings per account (no-op when the flag is off), and the
companion agent's `search_whatsapp` passes `hybrid=true` so it benefits
automatically. The Python `content_hash` was verified byte-for-byte against the
SQL predicate on real Postgres 16 — including the trailing-newline case that once
thrashed the email version — so the sweep never re-embeds a settled message.
(pgvector isn't installed on the sandbox cluster, so the vector DDL itself rides
on migration 73's prod-proven equivalence.)

**W11 — Connect-a-number onboarding wizard — BUILT (the app becomes usable).**
Everything above assumes a connected WhatsApp Business number; the connect flow
was a DISABLED "Embedded Signup" button, so registration meant a hand-rolled API
call. W11 replaces it with a guided, VERIFIABLE wizard at `/whatsapp/connect`:
(1) prerequisites with links to the exact Meta pages, (2) the webhook Callback URL
+ Verify token to paste into Meta → Configuration (copy buttons; the URL from
`WHATSAPP_PUBLIC_URL` or a domain field), (3) a credentials form with a **"Test
connection"** that calls Meta's Graph API for real — a new provider
`get_phone_number_profile()` GETs the phone-number id, so a 200 proves the token
works and shows the verified name / number / quality rating, and **Connect is
gated on a successful test** so a broken token is never saved — and (4) a done
screen. `POST /whatsapp/accounts/verify` (never writes; returns Meta's own error
cleaned up via the pure `friendly_meta_error`) and `GET /whatsapp/connection/info`
back it. Honest by design: it names what Meta actually requires rather than faking
a one-click flow the platform can't deliver without app review. **To go live in
production, set three env vars:** `WHATSAPP_APP_SECRET` (webhook HMAC),
`WHATSAPP_VERIFY_TOKEN` (or use the wizard's per-account token), and
`WHATSAPP_PUBLIC_URL` (so the wizard shows the real Callback URL). Historical
backfill still awaits coexistence/Embedded-Signup; the inbox starts fresh from
connection.

**W12 — Embedded Signup one-click connect — BUILT (the top-tier UX).** The
genuine best onboarding: **"Continue with Facebook"** → pick your number in
Meta's popup → done, with **zero copy-pasting**. The connect page now fetches
`connection/info` and, when the Meta app is Embedded-Signup-configured
(`WHATSAPP_APP_ID` + `WHATSAPP_ES_CONFIG_ID`), leads with the one-click card and
offers the W11 manual wizard as a secondary path; otherwise it goes straight to
the wizard (which hints how to unlock one-click). The button loads the Facebook
JS SDK, calls `FB.login` with the ES `config_id` (`response_type: code`), and a
`message`-event listener captures the `phone_number_id`/`waba_id` the user picks;
those go to `POST /whatsapp/connect/embedded`, which **exchanges the code for a
token server-side** (the App Secret never touches the browser), verifies the
number, **subscribes the app to the WABA so webhooks flow**, and stores the
account via a `persist_account` helper shared with the manual path. Graceful
degradation is the design: it's honest one-click when Meta allows it, honest
guided-manual when it doesn't. This does need the Meta app to have Embedded
Signup access (Tech-Provider / app review) to actually run — so it's wired
correctly and unit-tested, but its live end-to-end path can only be exercised
against a configured app.

**W13 — multi-number management — BUILT (parity with the email app).** The queue
was hardwired to the first account; now an **account switcher** in the nav footer
lists every connected number (avatar + name + count), switches the active one,
and links to "Connect another number". The active number scopes the streams,
chat list, and conversation (`account_id` threaded through
`fetchStreams`/`fetchChats`/`Conversation`); switching resets the open chat +
stream and reloads. The default number is selected on load.

**W14 — WhatsApp in the Integrations settings page — BUILT (managed centrally).**
The integration now appears on `/integrations` as a first-class card, faithfully
mirroring the **ClickUp** multi-account precedent (both are `wa_accounts`/
`task_accounts` per-account tokens, not process-wide env keys). Backend: a
`whatsapp` entry in `_SETUP_GUIDES` (label, docs, no env_vars) + the
`communication` category + `_is_configured → False` (its connected state is the
number count). Frontend: `/integrations` overrides `configured` from the real
`/api/whatsapp/accounts` count (like ClickUp), and a dedicated `WhatsAppConnector`
side-panel LISTS the connected numbers, DISCONNECTS them
(`DELETE /whatsapp/accounts/{id}`, which already existed), and links out to the
in-app connect wizard — so the integration is managed centrally while initiating
a connection stays in the app (as requested). No new backend lint/test debt (the
integrations.py registry edit adds zero ruff errors); frontend `next build`
compiles.

**Tests:** 227 backend unit tests (`pytest -k whatsapp`) — webhook parser,
persist (incl. voice→pending), post-sync registry, route helpers (signature/
window/regime), templates, capture, context, Reply Zero, intent (20 cases),
categories, digest + hook wiring, the auto-reply ladder (12), commitment
extraction + nudge drafting (24), voice-note transcription (11 — predicate,
status lifecycle, watermark reset, sentinel-on-failure), group intelligence
(13 — builder/parser/summarize orchestration), the companion agent (9 — tool
surface, drafts-only doctrine, config/tool drift guard, mocked-gateway
formatting; `build_agents()` constructs the MAF agent with all 13 tools), chat
snooze (12 — the pure wake-time validator + route registration), Pulse
(8 — median/percentile/response-time folds + route registration), saved
replies (15 — the pure shortcut normalizer + route registration), the
enrichment scheduler (6 — the pure gate/interval helpers + cycle sweep with
per-account error resilience), semantic search (7 — the pure embed-text /
content_hash helpers + the disabled-by-default query gate), and the connect
wizard (10 — the pure Meta-error extractor, the verify route with a mocked
provider, and the connection-info route). All new code `ruff`-clean; the frontend
`next build` compiles the snooze + Pulse + saved-reply + connect surfaces, the
gateway app imports cleanly with the scheduler wired into the lifespan, and
`uv sync` installs the new agent workspace member cleanly. The connect wizard
count grows to 24 with W12's Embedded Signup (the ES config flags on
`connection/info`, the "not configured" guard, and a fully-mocked happy path
through code-exchange → verify → subscribe → persist).

**Deploy validation (2026-07-24, this sandbox).** The production deploy runs on
the Hostinger VPS via `deploy/hostinger/deploy.sh` (`git pull → docker compose →
apply_migrations.sh → smoke`); the sandbox can't reach that VPS, so the branch is
the deployable artifact. What WAS validated here:
- **Migrations 102–110 against a real Postgres 16** (initdb'd local cluster, not
  Docker — the daemon is unavailable here). Fresh apply is clean; re-apply is
  fully idempotent (all `IF NOT EXISTS`, zero errors) — safe for
  `apply_migrations.sh` on every deploy. A functional smoke test seeded a full
  object graph and ran the actual route SQL (the FTS search, the LATERAL
  last-message join, the digest aggregation, the commitment partial-index query);
  `EXPLAIN` confirms the FTS query uses `idx_wa_messages_fts` (Bitmap Index Scan,
  not a seq scan) — the tsvector expression matches the index byte-for-byte. The
  `credentials_encrypted` NOT NULL fired as designed. The **W6 snooze SQL** was
  functionally verified on the same cluster: the queue filter hides a snoozed chat
  and the "Snoozed" stream surfaces it; a new INBOUND message clears the snooze
  via the `recompute_chat_status` CASE while an OUTBOUND reply does not. The **W8
  saved-replies** partial unique index was verified too: two NULL shortcuts
  coexist on one account while a duplicate `/shortcut` is rejected. For **W10**,
  the Python `content_hash` was proven byte-for-byte equal to the SQL predicate
  (`encode(sha256(convert_to(...)))`) on the same cluster, including a body with a
  trailing newline — the exact case that once thrashed the email embedder. The
  vector DDL (migration 111) could not run locally (pgvector isn't installed on
  the sandbox cluster) and rides on migration 73's prod-proven equivalence — the
  one open item. **Caveat closed** (bar the pgvector DDL, noted).
- **Frontend `next build` PASSES.** `npm ci` FAILS on a PRE-EXISTING lockfile
  drift (package-lock is missing `@emnapi/*` platform deps — unrelated to
  WhatsApp; nothing here touches package.json/lock), so I validated via
  `npm install` + `npm run build`: the production build compiles the whole app
  including `/whatsapp` (prerendered static) and `/api/whatsapp/[...path]`
  (dynamic) — my TSX/TS typechecks and builds under real Next 16. **Caveat
  closed.** The lockfile drift is a separate deploy blocker worth fixing on its
  own (it would break CI's `npm ci`), but it is out of scope for this branch.

**Frontend settings screens — BUILT (build-validated).** `/whatsapp/settings/
categories` (the policy table — editable notify/auto-reply/draft dropdowns that
PATCH optimistically, seed-defaults fallback, guardrail note) and
`/whatsapp/settings/rules` (the dry-run of the auto-reply engine over the
needs-reply queue — summary tiles + per-chat "would do / why", no sends). Both
reachable from an AUTOMATION nav section and confirmed by a real `next build`.
A PATCH verb was added to the `/api/whatsapp` proxy.

**Deferred — CRM/ERP integrations (Zoho CRM + Odoo), noted for later.**
Neither is a first-class platform integration yet (no Odoo client exists; the
`odoo_id`/`zoho_id` columns in `acb_graph` are reserved slots, not live sync).
The WhatsApp seams that would consume them are already in place and degrade
honestly without them:
- **Chat context rail** (`transport/context.py`) resolves the contact's CRM/ERP
  entity via a stable `<system>:<kind>:<id>` `entity_ref`; the live deal/invoice
  fetch is the deferred half.
- **`answer_from_system`** (the auto-reply ladder in `automation/rules.py`)
  already *decides* to answer order-status from Odoo (`system_source=
  "odoo_order_status"`); only the fetch is stubbed.
The correct sequencing is **platform Zoho/Odoo integration first** (an ingestion
source/agent alongside the existing ClickUp/Zoho references), *then* WhatsApp
consumes it through the shared layer — never a bespoke WhatsApp-only client.
Until then the vertical is fully functional; these rules simply fall through to a
normal draft.

**Next (buildable now, no CRM/ERP dep):** document/image OCR — the media-
understanding sibling of voice transcription (`wa_media.ocr_text` is already
provisioned) — deferred pending a vision-model tier, since the codebase has no
LLM-vision precedent yet; semantic search over history (pgvector embeddings on
`wa_messages`, already indexed for FTS); scheduler wiring for the bounded batch
~~group intelligence~~, ~~waiting-on nudge drafts~~, ~~voice-note transcription~~,
the ~~`wa_*` AI companion toolset~~, ~~chat snooze~~, ~~Pulse analytics~~,
~~saved replies~~, the ~~batch-trigger scheduler wiring~~, and ~~semantic search~~
are now BUILT (W4.1–W10). To activate in production: `WHATSAPP_ENRICHMENT=1`
turns on the autonomous group/voice/embedding sweep, and
`whatsapp_semantic_search_enabled=true` turns on hybrid semantic ranking — both
cost-gated off by default. A frontend WhatsApp search UI (the hybrid route is
consumed by the companion agent today) is the remaining surface.

**Next (integration-bound, later):** wire `answer_from_system` to live Odoo
order-status; the Embedded Signup onboarding flow + real coexistence history
import; an LLM refinement layered onto the deterministic intent/status
classifiers for the ambiguous tail; the single-reply auto-send executor
(reusing the broker handler seam). These need the Zoho/Odoo/Meta integrations or
a running stack to validate end-to-end.
