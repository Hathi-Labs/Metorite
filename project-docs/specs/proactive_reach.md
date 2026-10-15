# Reach — the chief of staff speaks first

**Status: ACTIVE, 2026-10-14.** Owner directive of 2026-10-14. The owner
accepted every proposal of §12 the same day, recorded as **D103**. Board row
**WS-59**.

**RE-1a is BUILT, 2026-10-14** (branch `ws59-proactive-reach`, migration
266, dark). `gateway/reach/` holds the classes, the planner, the outbox, the
WhatsApp adapter, the loop and two routes. My Profile shows "Messages
Metorite starts", with a preview and "Send my brief now". `REACH_ENABLED` is
off, and `REACH_ORGS` and `REACH_MEMBERS` are empty.

**RE-1a is LIVE, 2026-10-15,** for the two members with a WhatsApp link. The
first morning sent one brief and lost one brief and one interrupt to a closed
window. **RE-2a is LIVE, 2026-10-15** (#1042, no migration). A message in a closed
window waits for the member's next message. It goes as a template instead
when `REACH_BRIEF_TEMPLATE` or `REACH_NEEDS_YOU_TEMPLATE` names an approved
one (§8.1a, H-315). **RE-5 is BUILT, 2026-10-15** (branch `ws59-tell-me`,
migration 269). `tell_me()`, `reach_requests`, the email rule action
`NOTIFY_ME` and the workflow step `notify.whatsapp` are wired.
`POST /reach/tell-me` is built. The assistant tool that calls it is RE-5b,
because it lives in the orchestrator's tool manifests (§11.4).

**What this spec owns.** How Metorite decides to contact a member outside the
app, and how the message reaches them. That covers the outbound path, the
attention budget, the delivery ledger, the channel adapters, the reply loop
and the scale plan for all of them.

**What this spec does not own.**

- **What needs the member.** `my_day_feed.md` (WS-58) owns the signals, the
  cards, the `decide` questions and their cost. Reach reads its cards, and
  adds no second feed and no second registry.
- **The WhatsApp conversation.** `whatsapp_assistant_channel.md` (WS-47) owns
  the bot, the link, the inbound run and the reply. Reach adds an outbound
  door to the same bot.
- **How Metorite writes.** `agent_writing_voice.md` (WS-52) owns the voice and
  the chief-of-staff role (§11 of that spec).
- **Web push transport.** `chat_run_continuity.md` §S7 (WS-51, H-289) owns
  the push subscription. Reach uses it as a second channel.

---

## 0. The answer in one screen

1. **One brain, one mouth.** My Day already judges what needs the member, as
   a chief of staff would. Reach is the mouth. It takes a judged card and
   decides whether, when and where to say it. It never finds a signal of its
   own.
2. **Every app joins through one contract.** An app registers a My Day
   provider. It declares a reach class for each kind. Meetings, tickets, notes, a knowledge base and company insights join
   the same way. No app sends a message itself.
3. **A funnel keeps it cheap.** Code finds signals for free. Code drops most
   of them. `decide` judges the few that are left, inside the calls My Day
   already makes. A text model writes only the daily brief. So the cost of a
   member grows with the messages they get, not with the data they hold.
4. **An attention budget keeps it useful.** Each member has a daily cap,
   quiet hours from their own schedule, and a batch window. A message that
   does not fit waits for the next brief. Fewer, better messages also keep
   the bot number's quality rating high at Meta.
5. **A durable outbox makes it scale.** Each planned message is a row in
   Postgres. Workers drain it with `FOR UPDATE SKIP LOCKED`, so any number of
   processes can deliver, and a crash loses nothing.
6. **The member trains it with a tap.** Each message carries three buttons:
   Useful, Not for WhatsApp, and Mute. The answer becomes a correction that
   the next judgment reads. The member sets three things at most: the channel,
   the cap and the brief time.

---

## 1. What the owner asked for (2026-10-14)

- The WhatsApp bot sends messages first, about what the member should know:
  important email, the day's work, and context from projects. Later apps add
  more.
- An important email reaches the member on WhatsApp, without a rule for each
  case.
- An app may offer a control, for example an email rule that escalates to
  WhatsApp. The default needs no low-level setup.
- The same items show in My Day.
- **Scale.** The design must hold for many members and many organizations.
  It must balance a useful experience against the cost to deliver it.
- **Growth.** Meetings, operations, ticketing, notes and a knowledge base
  join later. They fit the same design.
- **Persona.** The bot acts as a chief of staff or an executive assistant.
  That is the persona of the chat (`agent_writing_voice.md` §11), now on
  WhatsApp too.

---

## 2. Scope and non-goals

### 2.1 In scope

- The reach class that each card kind declares (§4.2).
- The reach judgment, which is the `my_day.nudge` question that already
  exists and that nothing asks (§5).
- The attention budget, quiet hours and batching (§6).
- The outbox table and the delivery workers (§7).
- The WhatsApp adapter: the 24-hour window, templates, opt-in, STOP and
  START (§8.1).
- The daily brief, the interrupt and the end-of-day note (§9).
- The reply loop: buttons, card acts and corrections (§10).
- The email override: one rule action, "Tell me on WhatsApp" (§11).
- The scale plan (§13).

### 2.2 Non-goals

- **No second feed, registry or judge.** My Day owns them (§0 item 1).
- **No message to anyone but the member.** Reach never writes to a customer,
  a colleague or a group. An outward message to another person stays an
  Action Broker act with its own approval.
- **No marketing template.** Every template is in the utility category.
- **No SMS and no email channel in v1.** The member already has their own
  email digest (`email/digest.py`).
- **No open-domain chat.** Meta's 2026 policy binds
  (`whatsapp_assistant_channel.md` §2.3). A proactive message is always
  about the member's own company data.

---

## 3. What exists today — measured 2026-10-14

| Part | State | Where |
|---|---|---|
| Signals and cards | Live. Providers for tasks, projects, email, approvals and calendar | `routes/shell/needs.py`, `routes/shell/feed.py` |
| New sources | MD-3b adds `MY_DAY_PROVIDERS`, a gated list (PR #1024, open) | `my_day_feed.md` §5.8 |
| The judge | The `my_day` loop is live. Each `my_day.*` decide mode is `off` | `gateway/my_day_loop.py` |
| `my_day.nudge` | Registered. Nothing asks it | `gateway/decide_features.py:176` |
| Loop throughput | One gateway process. 60 s tick. 3 orgs and 3 members at once | `my_day_loop.py:106-122` |
| The change bus | In-process only | `ingestion/event_hooks.py` |
| WhatsApp sends | Replies only. No template send, no window check, no proactive path | `routes/whatsapp_channel/bot_run.py:1652` |
| Template primitive | `send_template` exists in the provider, and nothing in the bot calls it | `whatsapp_ingestion/providers/cloud_api.py:114` |
| Opt-in | `whatsapp_member_links.opted_in_at` exists. No code reads or writes it | `infra/postgres/235_whatsapp_member_links.sql` |
| Notify seam | None. The bell, the feed, the activity stream and the email digest are separate | — |
| Quiet hours | None. `day_start_hour`, `day_end_hour` and `work_schedule` can serve | `gateway/work_schedule.py` |
| Web push | None. Spec only | `chat_run_continuity.md` §S7 |
| Email rule actions | Ten types. No notify action | `routes/email/automation/actions.py:448` |

---

## 4. The model

```
 producers (apps)          My Day (WS-58)                 Reach (WS-59)                         channels
 ─────────────────         ───────────────────            ─────────────────────────────         ──────────────
 Tasks, Projects  ─┐                                       ┌─ gate (code): reach class,
 Email, Calendar  ─┤  signals ─▶ cards ─▶ judged by  ─────▶│  state, opt-in, mutes        ─┐
 CRM, Approvals   ─┤  (free)    (free)   decide in the     ├─ judge: my_day.nudge          │    ┌▶ WhatsApp
 Meetings*        ─┤                     member pass       │  (packed into the same pass)  ├─▶ outbox ─▶ worker ┼▶ web push
 Tickets*, Notes* ─┤                                       └─ plan (code): budget, quiet  ─┘    └▶ (later)
 KB*, Insights*   ─┘                                          hours, batch, channel
                                                                                ▲
 * = later apps, same contract                     reply, tap, STOP ─────────────┘ (feedback and acts)
```

### 4.1 The contract for an app

An app reaches a member through My Day and through nothing else. To join, an
app does three things.

1. **It registers a provider** under `MY_DAY_PROVIDERS` (MD-3b). The provider
   returns signals with the fields of `my_day_feed.md` §4.1.
2. **It declares a reach class for each kind** in the same registry (§4.2).
3. **It emits a bus event on change** that names the members it affects, so
   the feed goes dirty (`my_day_feed.md` §8.2).

That is all. The app writes no message, opens no channel and holds no
preference. A new app costs one provider, one card template and one row in
the reach-class table.

**Fence (RE-1a, built).** `tests/unit/test_reach_plan.py::TestTheClasses`
fails when a kind that a provider can give has no reach class.
**Fence (RE-1, to build).** `TestNoDirectSend` fails when a module outside
`gateway/reach/` imports a channel adapter.

### 4.2 Reach classes

A reach class is the most a kind may ever do outside the app. Code sets it,
and `decide` can lower it, never raise it.

| Class | Meaning | Examples |
|---|---|---|
| `none` | Feed only. Never leaves the app | `comment`, `day_overbooked` for a later day, a report that moved |
| `brief` | May appear in the daily brief or the day-end note | `due_today`, `no_plan_yet`, a deal that went quiet |
| `interrupt` | May interrupt within the budget, when the judge says yes | `needs_reply` from an important sender, `approval`, `block_soon`, `mention`, `agent_asks` |
| `urgent` | May interrupt and may use the urgent allowance (§6.1) | Only by an explicit member rule (§11), or a later kind that the owner names |

**The default is `none`.** A kind without a reviewed class never leaves the
app.

### 4.3 Kinds that later apps add — illustrative

This table shows that later apps fit. Each row is decided by that app's own
spec, not here.

| App | Kind | Class |
|---|---|---|
| Meetings | `meeting_prep_ready`, `meeting_starts_soon`, `action_item_assigned` | `interrupt`, `interrupt`, `brief` |
| Ticketing | `ticket_breaching_sla`, `ticket_assigned` | `interrupt`, `brief` |
| Operations | `stock_below_minimum`, `order_late` | `interrupt`, `brief` |
| Notes and knowledge base | `doc_shared_with_me`, `doc_needs_review` | `brief`, `brief` |
| Insights (project "Proactive Intelligence") | `metric_moved`, `opportunity_found`, `decision_needed` | `brief`, `brief`, `interrupt` |

### 4.4 The apps of a company, and what makes each item matter

An item matters because of what it touches in other apps. An email alone is
one more email. An email from the buyer on an open deal that closes this
week is the item of the day. So each row below names the join that lifts it.

| App | Kinds (class) | The join that makes it matter | Source of the kind |
|---|---|---|---|
| **Email** | `needs_reply` (interrupt), `no_answer` (brief), `promise_due` (brief) | The sender is a CRM contact on an open deal, a person on my project, or my manager | Live, and MD-3 for the other two |
| **CRM and sales** | `lead_replied` (interrupt), `crm_followup_due` (interrupt), `deal_close_passed` (brief), `deal_quiet` (brief), `deal_stage_changed` (brief) | The deal's value and close date. A meeting today with the contact | MD-3 and MD-9 |
| **Sales follow-ups** | `no_answer` on a deal thread (interrupt), `crm_followup_due` (interrupt) | A quote I sent waits for an answer, and the deal is open | MD-3 |
| **Calendar** | `block_soon` (interrupt), `block_overlap` (brief), `meeting_prep_ready` (interrupt, later) | The attendees' open deals, open threads and tasks | Live, and MD-11 for meetings |
| **Projects and tasks** | `notification` assigned or mention (interrupt), `overdue` (brief), `due_today` (brief), `unblocked` (brief) | A task blocks someone else, or the project has a date this week | Live, and MD-3 |
| **Finance** | `invoice_overdue`, `payment_received`, `bill_due` (each brief, later) | The customer is a CRM organization with an open deal | No app yet. Mail about invoices reaches the member as email until then |
| **Approvals and people** | `approval` (interrupt), `colleague_out` (brief) | The action waits on me and blocks a person | Live, and MD-3 |
| **Agents** | `agent_asks` (interrupt), `agent_done` (brief) | I started the run | MD-3 and MD-9 |

### 4.5 The context join — what Reach asks of My Day

A judge is only as good as the facts it reads. So Reach asks My Day for one
new step, after the providers and before the fold: **the context join**.

- **What it adds.** Each card gets a `related` fact. It is a short list of
  `{app, type, id, label, facts}` for the entities that the card touches in
  other apps. Examples: the open deal of an email's sender, the project of a
  task, the attendees of a block.
- **How it runs.** Code does the join with bounded, indexed reads, and no
  model. Each lookup goes through the owning app's own authorized read, with
  the member's `UserContext`. So a member never sees a deal that they cannot
  open in the CRM.
- **The first three joins.** Email sender to CRM contact to open deals.
  Email sender to a person in the People app. A task to its project and
  that project's next date.
- **What it gives.** The judge reads `related` in its state, so "the buyer
  on a large deal wrote" outranks a newsletter with no rule. The message
  names the join in one clause: "Priya Shah (Acme, Proposal, closes Fri)".
- **The owner of the join is WS-58,** because it changes the cards. §17
  lists it as a request to that workstream.

---

## 5. The judgment — is this worth an interrupt?

### 5.1 The gate in code comes first

The loop asks the judge only about a card that passes every test below. Each
test is free.

1. Its class is `brief`, `interrupt` or `urgent`.
2. It is new, or its fingerprint changed (`my_day_feed.md` §6.3).
3. The member is linked, opted in and not stopped (§8.1).
4. No mute in `my_day_prefs` covers its kind, sender or project.
5. The ledger holds no delivery of this `signal_id` with this fingerprint.

On a normal day this gate drops about nine cards in ten.

### 5.2 One typed question, in a pass that already runs

The member pass of MD-6a already sends up to 16 questions in one `decide`
request. Reach adds `my_day.nudge` to that request, for the cards that passed
§5.1. So reach adds questions, and almost never a call.

- **The question.** "Would this member want to hear about this now, at the
  next brief, or not outside the app?" The options are `now`, `brief` and
  `feed`.
- **The state.** The card's facts, the member's corrections (§10.3), their
  focus for today, and the context hash of `my_day_feed.md` §10.4. For mail,
  the facts include the sender's history with the member and whether a
  project names the sender.
- **The gate.** The loop takes `now` only above a confidence of 0.7. Below
  it, the card falls to `brief`.
- **The cap.** Each answer counts against the member's 60 daily calls of
  `my_day_feed.md` §9.1 rule 8. Past the cap, the judge answers `brief` by
  rule.

### 5.3 The persona

The chief of staff of `agent_writing_voice.md` §11 prepares a decision and
never makes it. On WhatsApp that means three rules.

- **Lead with what the member must do,** then why, in one line.
- **Offer the act, never take it.** A button runs a card act that code
  built. The member taps, and the confirmation of WAC-4 still applies to a
  write.
- **Say less, more rarely.** One message holds every item of the same hour.
  The brief holds what can wait.

---

## 6. The attention budget

### 6.1 The rules

| Rule | Default | Source |
|---|---|---|
| Interrupts per day | 3 | `my_day_feed.md` §6.5 |
| Urgent allowance | 1 more each day, for class `urgent` only | Proposed, D-RE-3 |
| Quiet hours | Outside `work_schedule`, and outside `day_start_hour` to `day_end_hour` | `my_day_feed.md` §6.5 |
| Batch window | 15 minutes. A `now` waits up to 15 minutes for a partner, then goes | Proposed, D-RE-4 |
| Daily brief | 1, at `day_start_hour`, on a working day | §9.1 |
| Day-end note | 0 by default. The member can turn it on | §9.3 |
| Per-organization ceiling | Set by the operator. Default 10 interrupts for each seat each day | §13.4 |

**An interrupt that does not fit** goes to the next brief. It never drops in
silence, and the feed always shows the card.

**The urgent allowance ignores quiet hours only when the member's own rule
says so** (§11). The judge alone can never wake a member.

### 6.2 Where the budget lives

The counters are Redis keys through `acb_common.tenant_redis`:
`reach:sent:<member>:<local date>` and `reach:sent:org:<date>`, with a 26 h
TTL. The ledger (§7) is the truth. A lost Redis key is rebuilt from the
ledger on the next plan, so a Redis restart can never double the budget.

---

## 7. The outbox — `reach_outbox`

### 7.1 The table

One row is one planned message to one member on one channel. Tenant-scoped,
with FORCE ROW LEVEL SECURITY at the `get_db()` seam (R5).

| Column | Meaning |
|---|---|
| `id` | UUID |
| `organization_id` | The tenant. The RLS key |
| `member_email` | The recipient. Always the member, never a third person |
| `channel` | `whatsapp` or `web_push` |
| `kind` | `brief`, `interrupt`, `day_end` or `system` (opt-in, STOP reply) |
| `signal_ids` | The cards this message carries, as text[] |
| `dedupe_key` | Unique. A hash of the member, the channel, the kind, and the sorted pairs of signal and fingerprint |
| `not_before` | The earliest send time. The batch window and quiet hours set it |
| `state` | `planned`, `sending`, `sent`, `delivered`, `read`, `failed`, `skipped` |
| `regime` | `free_text` or `template`, chosen at send time (§8.1) |
| `template_name` | The template, when the regime is `template` |
| `provider_message_id` | Meta's id, for the status webhook and for the reply loop |
| `attempts`, `last_error` | Retry state |
| `cost_class` | `free`, `utility` or `push`. Feeds the cost report (§13.5) |
| `created_at`, `sent_at`, `outcome_at` | Times |
| `feedback` | `useful`, `not_here`, `muted` or null (§10) |

**Indexes.** `(state, not_before)` for the workers. `(organization_id,
member_email, created_at)` for the budget and the report. A unique index on
`dedupe_key`.

**Retention.** A sweep deletes rows older than 30 days. A monthly summary row
keeps the counts for the cost report.

### 7.2 The planner

The planner runs inside the member pass, after the judge. It is pure code.

1. Take the cards with `now` and the cards with `brief`.
2. For `now`, check the budget and quiet hours. A fit gets a row with
   `not_before` at the end of the batch window. When a message is already
   planned in the window, the card joins that row instead. The update runs
   only while the row's state is `planned`.
3. A `now` that does not fit, and every `brief`, joins the member's next
   brief row.
4. Insert with `ON CONFLICT (dedupe_key) DO NOTHING`. So a pass that runs
   twice plans once.

### 7.3 The workers

- A worker claims up to 50 due rows with `FOR UPDATE SKIP LOCKED`, in the
  order of `not_before`. It sets each claimed row to `sending`.
- **Discovery across tenants.** The claim runs as a tenant-discovery site,
  registered in `tests/unit/test_db_engine_seam.py`, the same way the
  `my_day` loop finds organizations. The send of each row then runs inside
  `tenant_session` for that row's organization.
- **The checks at send time.** The worker reads the link, the opt-in, the
  STOP state, the window and the budget again. A row that fails one becomes
  `skipped` with a reason. So a member who sent STOP a second ago gets
  nothing.
- **Retries.** A transient error, a 429 or a 5xx, goes back to `planned` with
  backoff of 1, 5 and 30 minutes. After three tries the row is `failed`.
  A brief that fails past its day is `skipped`.
- **A row stuck in `sending`** for 10 minutes goes back to `planned`. The
  dedupe key and Meta's own message id stop a double send.
- **Status webhooks** from Meta move a row to `delivered` or `read`. The
  webhook route exists for the bot (`routes/whatsapp_channel/inbound.py`).

**Fence (RE-1a, built).** `tests/unit/test_reach_r8.py`, on a real database
(R8). A replayed plan inserts once. A row of org A is never visible in a
session of org B. Only an inbound message opens the window. Only an active,
current link is used.

**Fence (RE-1, to build).** Two workers claim one row, and one wins. A STOP
between plan and send gives `skipped`.

---

## 8. Channels

### 8.1 WhatsApp

**Who may get a message.** Only a member with a current, active link in the
row's organization, `opted_in_at` set and no STOP. The lookup is a
tenant-bound select on `whatsapp_member_links`. ⚠️ `is_current` can point at
another organization, so the adapter checks that the current link is in this
row's organization. Otherwise it skips.

**Opt-in.** The link flow of WAC-6 asks once: "Can Metorite message you
first, at most 3 times a day?" A yes sets `opted_in_at`. "STOP" clears it
and records the time. "START" sets it again. Meta's own opt-in rules need
this record.

**The 24-hour window.** The adapter reads `last_inbound_at` of the link. The
inbound path must start to write it (RE-2). Then:

| Window | Regime | Cost |
|---|---|---|
| Open | `free_text`, with interactive buttons | Free |
| Closed | `template`, a utility template with a quick-reply button | One utility charge. A tap opens the window |

**The brief opens the day.** The brief goes as a template when the window is
closed. Most members tap a button or reply. That opens the window, and each
interrupt of the next 24 hours goes as free text. So most members cost about
one template a working day.

⚠️ **Meta's pricing changes. Read the live rate card at build time.** Since
1 July 2025 Meta charges per template message, by category and country. A
utility template sent inside an open window is free under the 2025 rules.
RE-2 confirms both facts against Meta's current page, and writes the date
it read them.

**Templates.** Three, each in the utility category, each in English first.

| Name | When | Body (category UTILITY, language `en`) | Button (quick reply) |
|---|---|---|---|
| `metorite_daily_brief` | The brief, window closed, in the first 3 hours of the day | Hi {{1}}, your Metorite brief for {{2}} is ready. {{3}} items need you today, starting with: {{4}}. Tap below to see the full brief. | Show my brief |
| `metorite_needs_you` | An interrupt of `approval` cards only, window closed | Hi {{1}}, your approval is pending in Metorite: "{{2}}". Tap below to review it. | Show me |
| `metorite_opt_in` | The link flow, when Metorite asks for opt-in | Later, with RE-2 | Yes · No |

**The owner submits them at Meta** (H-315). The auto-mode classifier refused
an agent's submission through the Graph API on 2026-10-15. Then the operator
sets `REACH_BRIEF_TEMPLATE=metorite_daily_brief` and
`REACH_NEEDS_YOU_TEMPLATE=metorite_needs_you`.

**Measured 2026-10-15.** On the test account, `metorite_task_reminder` and
`metorite_meeting_reminder` are approved utility templates. Their words do
not fit a brief, and they have no button, so Reach does not use them.

**Measured 2026-10-15, at submission.** Meta's pre-check refused three general
bodies for `metorite_needs_you` as utility, and it recommended marketing:

1. `Hi {{1}}, something in Metorite needs you now: {{2}}. Tap below to see it.`
2. `Hi {{1}}, an item in your Metorite account needs your action: {{2}}. ...`
3. `Hi {{1}}, an update from Metorite: "{{2}}" is waiting for your response. ...`

The approval body in the table passed the pre-check. The owner chose to narrow
the template, not to make it marketing. So the template carries an approval
only, and every other interrupt in a closed window waits (§8.1a).

### 8.1a Hold, then catch up (RE-2a)

The window is closed for most members at the brief time. On 2026-10-15 the
first live morning sent one brief and dropped one brief and one interrupt
for that reason. So Reach holds a message instead of dropping it.

- **An interrupt in a closed window** goes as `metorite_needs_you` when that
  template is set and every card in it is of kind `approval`. Otherwise Reach
  holds it: it plans nothing and logs nothing. The first tick after the member writes to the bot sends it as
  free text, inside the cap and outside quiet hours.
- **A brief in a closed window** goes as `metorite_daily_brief` in the first
  3 hours of the day, when that template is set. Otherwise Reach holds it
  until the member writes to the bot that day.
- **After a template brief,** the first open window that day gets the full
  brief once, as free text.
- **A held message never jumps the rules.** The cap, quiet hours and the
  12-hour freshness of a thread apply when it goes, not when it was held.

### 8.2 Web push

Web push is the second channel. It reuses the subscription table and the
VAPID key of `chat_run_continuity.md` §S7. A push payload holds no content,
only a count and a link, as §S7 decides. It costs nothing per message. RE-9
builds it after S7 lands.

### 8.3 The order of channels

A member who has both picks one main channel in Settings. Reach sends on that
channel only. A second channel is a fallback only when the first fails.

---

## 9. The three message shapes

### 9.1 The daily brief

- **When.** At `day_start_hour`, on a working day, after the morning pass of
  the `my_day` loop.
- **What.** The top items of Now and Needs you, and what is due today. Then
  every `brief` item since the last brief. At most 7 lines. Then the act
  buttons.
- **Who writes it.** A template writes each line from the card's own words.
  A text model writes one lead sentence when there are 4 items or more, and
  only after `my_day.compose` says yes. That is one composition for each
  member each day.
- **Skip rule.** A day with nothing to say sends nothing. "Nothing needs you"
  is not a message.
- **The same brief in the app.** The brief is also the MD-7 card at the top
  of My Day. One composition serves both.

### 9.2 The interrupt

- **What.** One line for each card: the headline and the why line, which
  code already wrote. At most 3 cards. Then up to 3 buttons.
- **No model writes an interrupt.** The words are the card's own templates.
  So an interrupt costs no text model.

### 9.3 The day-end note

Off by default. When on, it lists what was handled today and what moved to
tomorrow. It reads the Handled group of MD-2c. It sends only on a day with
at least one item.

---

## 10. The reply loop

### 10.1 Buttons

Each interactive message carries up to three reply buttons. The button id
holds the outbox row id and an act key. The bot's inbound path routes it.

| Button | Effect |
|---|---|
| A card act, for example "Reply", "Approve", "Block 11:00" | Runs the card act of `my_day_feed.md` §11. A write still asks for the WAC-4 confirmation |
| Useful | `feedback = useful` on the row. A positive correction for that kind |
| Not for WhatsApp | `feedback = not_here`. A correction: this kind or sender is `brief` from now on |
| Mute | Opens a list: mute this sender, this project or this kind. Writes the mute to `my_day_prefs` |

### 10.2 Free replies

A free reply to a reach message runs the normal bot turn of WAC. The bot gets
the cards of that message in its context, so "draft a reply to the second
one" works. The channel changes the delivery, never the function.

### 10.3 Corrections

The feedback goes into the corrections list of `my_day_prefs`
(`my_day_feed.md` §10.5). The judge reads them in its state. Three
`not_here` on one kind in 30 days lower that kind to `brief` for this member
by rule, with no model.

**What the member sees.** Settings → Notifications lists each learned rule in
words, for example "Mail from newsletters: brief only". The member can remove
one.

---

## 11. "Tell me on WhatsApp" — one primitive for every app

**Owner, 2026-10-15:** "Tell me on WhatsApp can be used across to send any
sort of information to WhatsApp." So it is not an email feature. It is one
call that every app, rule, workflow and the assistant can make. RE-5 builds
it.

### 11.1 The call

`gateway.reach.requests.tell_me(org, member, *, source, title, body=None,
href=None, entity=None, urgency="normal", requested_by, key=None)` records
one request. It is the ONLY way for an app to ask Reach to say something.
It sends nothing itself.

| Argument | Meaning |
|---|---|
| `source` | The app or the rule that asks: `email.rule`, `chat`, `workflow`, and later more |
| `title` | One line the member reads first. Code or the member wrote it, never a model alone |
| `body` | Optional text, at most 1,000 characters |
| `href` | Optional link into Metorite, built by code |
| `entity` | Optional `{type, id}` of the thing it is about |
| `urgency` | `normal` or `urgent` |
| `requested_by` | The member who asked, or the rule's owner |
| `key` | Optional dedupe key. The same key twice records one request |

**The member is always the recipient.** A request names one member, and
`member` comes from a session or from the rule's owner, never from request
input. No request can address another person (§2.2).

### 11.2 The table — `reach_requests`

Tenant-scoped, FORCE RLS. One row for each request. The columns are `id`,
`organization_id`, `member_email`, `source`, `title`, `body`, `href`,
`entity_type`, `entity_id`, `urgency` and `requested_by`. Then `dedupe_key`
(unique for each org), `created_at` and `expires_at` (48 hours by default). The outbox row that
delivered it keeps the link, through its `signal_ids`
(`request:<id>`).

### 11.3 How it reaches the member

The Reach loop reads the member's open requests and makes one card for
each, kind `notify_me`, signal `request:<id>`. From there the card takes
the normal path: the log, the window hold, templates, the thread record.

| Who asked | Class | Cap | Quiet hours |
|---|---|---|---|
| The member, now (the assistant, "Send my brief now") | `urgent` | Not counted | Ignored. The member is awake, because they just asked |
| A rule or a workflow, `normal` | `interrupt` | Counted | Respected |
| A rule or a workflow, `urgent` | `urgent` | The urgent allowance of §6.1 | Ignored only for `urgent` |

A request that expires before it can go is logged `expired`. The feed shows
nothing for it in RE-5. My Day may show requests later (§17, W6).

### 11.4 The first three callers (RE-5)

1. **Email rules.** A new action type `NOTIFY_ME` with one option,
   `normal` or `urgent`. It joins `_apply_rule_actions`
   (`routes/email/automation/actions.py`), the rule generator's list
   (`_GEN_ACTION_TYPES`, `rules.py`) and the rule UI (`actionFormat.tsx`,
   `RulesTab.tsx`). The title is "Email from <sender>: <subject>". The link
   opens the thread. Example rules: "Tell me on WhatsApp when a customer
   reports a defect". "Tell me on WhatsApp, urgent, when the bank writes".
2. **The assistant.** A tool `tell_me_on_whatsapp(title, body?)` in the chat
   of the web app. The member asks "send this to my WhatsApp", and the tool
   records a request with `source=chat`. The tool answers with what Reach
   will do: "sent", or "it waits for your next WhatsApp message".
3. **Workflows.** A step action `notify.whatsapp` that records a request
   for the workflow's owner.

**Decisions taken at build (RE-5, 2026-10-15).**

- A request is the member's own only when `source` is `chat` and the
  member asked. A rule's owner is the member too, but a rule request keeps
  the budget.
- A request that may not go now waits, and it is not logged. A later pass
  sends it before it expires. The brief never carries a request.
- One message holds 3 requests at most, the member's own first. A message
  of the member's own requests only is outbox kind `request`, so the cap
  does not count it.
- The urgency of `NOTIFY_ME` lives in the action row's `label`, so no
  column is added.

**RE-5b, the assistant tool.** `acb_skills/reach_tools.py` posts to
`POST /reach/tell-me` with the run's identity. It needs an entry in
`skill_families.py` and `tool_annotations.TOOL_ANNOTATIONS`, an injection
line in `orchestrator/_tool_injection.py`, and the name in
`whatsapp_ui.WITHHELD_TOOLS`, so a WhatsApp run does not carry it.

**Fence (RE-5).** `tests/unit/test_reach_requests.py`. No caller imports a
channel adapter. A request for one member never reaches another. A request
of org A is invisible in org B (R8). The same key records one request.

## 12. Decisions and the owner's answers

**The owner accepted every proposal below on 2026-10-14**: "I am fine with
your suggestions, go ahead with your recommendations." `work_plan.md` §3
records them as **D103**.

### 12.1 Decisions (accepted 2026-10-14)

| Id | Decision |
|---|---|
| D-RE-1 | Reach is one delivery seam for every app. An app reaches a member only by a My Day card with a reach class. No app sends to a member channel itself |
| D-RE-2 | The judge is `my_day.nudge`, packed into the member pass. Reach adds no model call of its own, except the one brief composition |
| D-RE-3 | The budget is 3 interrupts and 1 urgent each day, inside working hours. The urgent allowance needs the member's own rule to break quiet hours |
| D-RE-4 | A 15-minute batch window. One message holds every interrupt of the window |
| D-RE-5 | The outbox in Postgres is the delivery truth. Redis holds only counters that the outbox can rebuild |
| D-RE-6 | Every proactive WhatsApp message is a utility template or free text in an open window. Never marketing |

### 12.2 Questions, answered with their defaults (2026-10-14)

| # | Question | Answer |
|---|---|---|
| Q1 | Who pays Meta's template charge? It is about one utility message for each member each working day | Metorite absorbs it inside the ₹500 seat. Commercial call, so the owner decides |
| Q2 | Is opt-in on by default for a member who links a phone? | Off. The member says yes once in the link flow |
| Q3 | Does the brief go on a weekend or a holiday? | No. It follows `work_schedule` |
| Q4 | Is the daily cap of 3 right for a founder? | 3 for every member. The member may raise it to 6 in Settings |
| Q5 | Does an organization admin control reach for their org? | Yes. One switch for each org, under WAC-8 org controls |

---

## 13. Scale

### 13.1 What grows, and with what

| Cost | Grows with | Control |
|---|---|---|
| Signals and cards | Data and reads | Free. Bounded SQL. The read cache of `my_day_feed.md` §8.1 |
| `decide` questions | Changed cards of active members | The gate of §5.1, the fingerprint cache, the 60-call cap |
| Text model | Members who get a brief with 4 items or more | One composition each day, behind `my_day.compose` |
| Meta charges | Members whose window is closed at brief time | The brief opens the window. A skip rule for an empty day |
| Database writes | Messages sent | At most about 5 outbox rows for each member each day |
| Worker time | Messages sent | Workers scale out with `SKIP LOCKED` |

**A dormant member costs nothing.** The loop reads only the active set of
`my_day_feed.md` §8.3. ⚠️ Reach changes one rule there: a member with an
opted-in WhatsApp link stays active while their link is active, even with no
web read. Otherwise a member who lives on WhatsApp would drop out after 3
days. The cost of that member is the brief pass, once a day.

### 13.2 The estimate for one active member, one working day

| Item | Count | Note |
|---|---|---|
| Cards that pass the gate | about 5 | Of about 50 cards |
| Extra `decide` questions | about 5 | In requests the pass already makes |
| Extra `decide` calls | 0 to 1 | Only when a request is already full at 16 |
| Text compositions | 0 or 1 | The brief lead sentence |
| WhatsApp messages | 1 brief and 0 to 3 interrupts | |
| Template charges | 0 or 1 | Only when the window is closed at brief time |

So reach adds less than one model call and at most one Meta charge for each
member each working day. The live rates go in the build PR of RE-2, not
here.

### 13.3 The loop must scale out — the limit of today

**Measured.** The `my_day` loop runs in the one gateway process, with a 60 s
tick and 3 members at once (`my_day_loop.py:106-122`). At about 2 s for each
member pass, it judges about 90 members each minute.

**What breaks first: the morning.** Most members of one organization share a
`day_start_hour`. 2,000 members who start at 9:00 need about 22 minutes of
passes, so the last brief is late.

**The fix, in RE-8.**

1. **Spread the morning.** The morning pass of a member runs 60 to 15
   minutes before their `day_start_hour`. A hash of the member sets the
   offset in that window. The brief is planned with `not_before` at the start
   hour, so every brief still goes on time.
2. **Move the loop out of the gateway.** A worker unit, `acb-reach`, runs the
   member passes and the delivery workers. The gateway keeps the reads and
   the webhooks. A slow pass can then never slow a page read.
3. **Shard by lease.** A hash of the org id puts each organization in one
   of 64 buckets. Each worker takes a lease on some buckets through a
   Postgres advisory lock. Add workers to take more buckets. No coordinator is needed.
4. **The bus crosses processes.** The dirty bump moves to
   `TenantRedis.publish`, as `my_day_feed.md` §8.2 already names. The worker
   subscribes. One Redis increment for each change stays the cost.

### 13.4 Limits that are not ours

- **Meta's throughput** for one number is about 80 messages a second by
  default. The outbox paces sends with a token bucket in Redis, one for each
  bot number.
- **Meta's messaging tier** limits how many members a number can contact
  first in 24 hours. A new number starts low, and the tier grows with volume
  and quality. ⚠️ All organizations share one bot number (WS-47 §5.1). So the
  tier is a platform limit. RE-8 adds a check that alerts at 70 percent of
  the tier. A pool of numbers is an owner decision, not a v1 build.
- **Quality rating.** Meta lowers the rating, and then the tier, when members
  block or report the number. The budget, the skip rule and the "Not for
  WhatsApp" button protect the rating. The cost report (§13.5) shows the
  block rate.
- **The per-organization ceiling** stops one org from using the whole tier.

### 13.5 The cost report

An operator read, `GET /reach/report`, shows each organization by day. It
shows messages by kind and regime, and template charges. It also shows the
share of `useful` and `not_here`, and the block rate. It reads only the outbox and its monthly
summary. It is the number that tells us whether the balance of §1 holds.

---

## 14. Data and settings

- **`reach_outbox`** (§7). New, tenant-scoped, FORCE RLS. Migration number
  taken at build time (R1).
- **`user_settings.reach_prefs`**, a new nullable jsonb column: the main
  channel, the cap, the brief on or off, the day-end note on or off. A null
  reads as the defaults of §6.1. Expand only (R6).
- **`whatsapp_member_links`**: the inbound path starts to write
  `last_inbound_at`. The link flow starts to write `opted_in_at`. A new
  nullable column `stopped_at`. Expand only.
- **The mutes and corrections** stay in `user_settings.my_day_prefs`. Reach
  adds no second preference store for them.
- **Settings → Notifications**, one pane in the Personal Center. Three
  controls: the channel, the cap and the brief. Below them, the list of
  learned rules in words (§10.3). It follows `DESIGN_SYSTEM.md`.

---

## 15. Flags

| Flag | Default | Effect |
|---|---|---|
| `REACH_ENABLED` | off | The kill switch. Off: no plan and no send |
| `REACH_ORGS` | empty | The orgs that may plan. No `*` until the owner says so |
| `REACH_CHANNELS` | empty | `whatsapp`, `web_push` |
| `my_day.nudge` mode | `off` | `shadow` records the judgment and sends nothing. `on` lets it plan |

**Shadow first.** RE-4 runs `my_day.nudge` in `shadow` for a week on the
owner's org. The report of §13.5 then shows what reach would have sent. The
owner reads it before the first real send.

---

## 16. Tickets

Each ticket ships dark. Each names its fence. Turning a flag on for a member
is an owner gate (MD-10, CLAUDE.md §3a rule 3).

| Ticket | What | Depends on | Gate |
|---|---|---|---|
| **RE-0** | This spec, the board row WS-59, the INDEX row, and pointers in `my_day_feed.md` MD-10 and `whatsapp_assistant_channel.md` §5.8 | — | AGENT-SAFE |
| **RE-1** | The reach seam: the reach-class registry, `reach_outbox` (migration), the planner, the budget, quiet hours, the batch window, the worker loop with `SKIP LOCKED`. A fake channel only | MD-3b | AGENT-SAFE |
| **RE-2** | The WhatsApp adapter: the member lookup in this org, opt-in, STOP and START, `last_inbound_at`, the window check, free text and template sends, the status webhook, the token bucket | RE-1 | AGENT-SAFE to build. 🔴 Templates at Meta are owner work (H-251) |
| **RE-3** | The daily brief: the morning plan, the lines from card templates, one composition, the skip rule, the MD-7 card from the same brief | RE-2, MD-7 | AGENT-SAFE |
| **RE-4** | The interrupt: `my_day.nudge` in the member pass, the gate of §5.1, `shadow` mode, and the cost report of §13.5 | RE-1, MD-6b | AGENT-SAFE. 🔴 Mode `on` is an owner flip |
| **RE-5** | "Tell me on WhatsApp" for every app (§11): `reach_requests`, `tell_me()`, the email rule action, the assistant tool and the workflow step | RE-2a | AGENT-SAFE |
| **RE-6** | The reply loop: buttons, card acts from WhatsApp, the feedback and the corrections | RE-2 | AGENT-SAFE |
| **RE-7** | Settings → Notifications, and the "Sent to WhatsApp" mark on a feed card | RE-1 | AGENT-SAFE |
| **RE-8** | Scale out: the `acb-reach` unit, the spread morning, the lease shards, the bus on Redis, the tier alert, and a load test of 5,000 members | RE-4 | AGENT-SAFE to build. 🔴 A new unit on the box is a deploy act |
| **RE-9** | Web push as a second channel | RE-1, WS-51 S7 | AGENT-SAFE |
| **RE-10** | The producer guide: one page for a new app, and the fences of §4.1 in CI | RE-1 | AGENT-SAFE |

**The first useful slice for the owner** is RE-1, RE-2 and RE-3 on the
owner's org: a daily brief on WhatsApp. RE-4 and RE-5 then add the important
email.

### 16.1 RE-1a — the demo slice (owner, 2026-10-14)

The owner asked for a basic demo in 2 to 3 hours. RE-1a cuts RE-1, RE-2 and
RE-3 to what a demo needs. Each later ticket builds on it, and none of it is
thrown away.

| Part | In RE-1a | Left for later |
|---|---|---|
| Members | An allowlist, `REACH_MEMBERS`, inside `REACH_ORGS` | Opt-in in the link flow (RE-2) |
| The judge | The reach class and code rules only. A `needs_reply` card interrupts only when it waited less than 12 hours | `my_day.nudge` (RE-4), the context join (§4.5) |
| The first run | Each card that exists is logged as `baseline`, so nothing old interrupts | — |
| Budget | The daily cap and quiet hours | The batch window, the urgent allowance |
| Channel | WhatsApp free text in an open window. A template only when `REACH_BRIEF_TEMPLATE` names an approved one | The other templates, STOP and START (RE-2) |
| The loop | Its own 60 s loop in the gateway, one member at a time | The worker unit and the shards (RE-8) |
| The thread | Each message is also an assistant turn in the member's WhatsApp chat thread, so a reply has context | The buttons (RE-6) |
| Routes | `GET /reach/preview` and `POST /reach/brief`, for the signed-in member only. My Profile shows both, under "Chat on WhatsApp" | Settings → Notifications (RE-7) |

**Data.** Migration 266 adds `reach_outbox` and `reach_signal_log`. The log
holds one row for each signal and fingerprint that Reach considered, with its
outcome. It is the dedupe of an interrupt, and the baseline of the first
run.

**Fences.** `tests/unit/test_reach_plan.py`, `test_reach_loop.py` and
`test_reach_r8.py` (a real database, with the DB gate).

---

## 17. Requests to WS-58 (My Day)

Reach is as good as the cards it reads. The owner noted on 2026-10-14 that
the My Day intelligence is built in a parallel session. These are the asks
of this spec to that work. None of them blocks RE-1a.

| # | Ask | Why Reach needs it |
|---|---|---|
| W1 | The context join of §4.5, the `related` fact | The judge cannot tell an important email from a loud one without it |
| W2 | Each new kind of MD-3 and MD-9 ships with its reach class in `gateway/reach/classes.py`, in the same PR | §4.1. A kind without a class never leaves the app |
| W3 | `my_day.nudge` reads `related`, the corrections of §10.3, and the member's focus for today | §5.2 |
| W4 | A member with an active, opted-in WhatsApp link stays in the active set | §13.1. A member who lives on WhatsApp would drop out after 3 days |
| W5 | The brief card of MD-7 and the WhatsApp brief come from one composition | §9.1 |
| W6 | My Day shows an open "Tell me" request as a card, and marks it sent | §11.3 |

---

## 18. Verification

Each ticket's PR runs, with the dev database up (R8):

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_reach_plan.py tests/unit/test_reach_loop.py tests/unit/test_reach_r8.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

A run that reports skipped tests fails the check. A reach test must not skip
for a missing database.
