# My Day as a live feed — the chief of staff

**Status: PROPOSED, 2026-10-13.** Nothing in this file is built. MD-1 to
MD-5 are AGENT-SAFE and can start now, dark.

**Revised 2026-10-13, same day, after the owner's answers.** The owner
answered Q2 and Q3 (§17.2). The customer pays for every AI call with AI
credits. External calendars come later, and calendar clashes fold in then.

The owner also asked the feed to use `decide` as much as it can. So §10 is
now "Decide first". Q1 and the new Q6 (residency for My Day) are still open.

**Verified against code on 2026-10-13** at `origin/main` `1ba914c1c`. Each
anchor carries a file and a line. Check them again at dispatch, because this
tree moves every day.

**Board row:** **WS-57**, minted by this spec (`work_plan.md` §2).
**Owning spec:** this file. **Owner:** vjvarada.

**Owner directive, 2026-10-13.** The owner wants My Day to stop being a set of
static views of email and the calendar. It must change with "the context of my
current system". The owner asked for a
live feed that acts as a chief of staff. It must notice, prepare and nudge,
and it must not cost too much to run. The owner gave four reference cards,
which §1.1 describes.

**Single owner, and the line with WS-44.** `navigation_shell.md` (WS-44) keeps
owning the page frame of My Day, the three altitudes, the bell, the manifest
and the shell flags. This file owns what goes **into** the feed. That is the
signals, the card shapes, the order and the life of a card. It is also the AI
that prepares a card, and its cost. `navigation_shell.md` §4.3 and §7.2 link here, and add nothing.

---

## 0. The answer in one screen

My Day becomes one ranked column of cards that a member can scroll. Each card
says what needs the member, why now, and offers the next act in one click.

1. **Four layers. The two at the bottom are free, and the third is cheap.**
   - **Signals** are facts that code finds: a task is overdue, two blocks
     overlap, a thread waits for a reply. They are free and deterministic.
   - **Cards** turn a signal into a headline, a "why" line, a small view and
     the acts. Code writes every card from templates, so a card needs no
     model to exist.
   - **Decisions** are the judgment of a chief of staff, asked of `decide`
     as typed questions. Where does this card go? How much does it matter to
     what the member must get done? Which reason, which act, which slot? Each
     answer is a key from a list that code wrote (§10).
   - **Compositions** are the few words that only a text model can write: the
     gist of a long thread, a meeting prep list. A `decide` question gates
     each one, so a composition runs only when it saves the member time.
2. **One feed, not two.** `GET /shell/needs` already merges four providers
   with no model. The feed extends that provider registry. The bell and the
   sidebar counts keep reading the needs view of the same signals.
3. **A card has a life.** It is new, then seen, then acted, snoozed,
   dismissed, resolved or expired. Clear rules bring a card back when it
   matters again (§6). Nothing that needs the member drops away in silence.
4. **Decide picks, templates speak, and code acts.** A `decide` answer is a
   key from options that code built. So no model output can invent a target,
   a time, a recipient or a link (§10).
5. **The feed ends.** Below the last open card, a "Handled" group lists what
   closed today. Then the feed says "You are caught up".
6. **Cost stays small by design.** No model runs in the read path. Each
   answer is cached by content, so the same facts never pay twice. A dormant
   member costs nothing. `decide` costs USD 0.042 for each million input
   tokens, and its output is free. §9 estimates about 20 calls for each
   active member each working day. The customer pays in AI credits.

---

## 1. What the owner asked for

### 1.1 The four reference cards

The owner shared four screenshots of a feed. Each card has the same anatomy,
and this spec takes that anatomy as the card contract (§4.2).

| Card | Source and state | Headline | Why line | Body | Acts |
|---|---|---|---|---|---|
| A | Calendar · Needs you | "Your 2:00 PM with Priya overlaps the Contoso visit" | "Contoso moved their site visit to 1:30 PM. Priya is free Thursday at 2:00." | A two-day strip that shows the overlap and the proposed slot | "Move to Thu 2:00 PM", "See options" |
| B | Email · Reply drafted | "Dana Liu is waiting on the Q3 proposal" | "She asked yesterday at 4:12 PM. You said you'd send it by Friday." | The full draft, the recipient and the attachment | "Send", "Edit draft" |
| C | Reports · Summary ready | "Q3 West region results are in" | "Monday's sales review is on your calendar. Final numbers landed overnight." | One large number, the change, and three bars | "Share with team", "Open report" |
| D | (header) | "Good morning, Megan" | "3 things need you. The rest is handled." | — | — |

### 1.2 What the owner listed, and what this spec adds

The owner named tasks due, emails that need a reply, CRM and report
summaries, calendar clashes, and views of the calendar. The owner also asked
for these four behaviours:

- **Proactive.** The feed proposes what the member can do, not only what
  happened.
- **Live.** The feed changes during the day, and a member can scroll it.
- **Recoverable.** A card that the member scrolled past, or missed, comes back
  when it matters.
- **A door to every app.** Each card links to the exact task, thread, deal,
  report or day.

§5 is the full catalogue of cards, and it adds about twenty kinds beyond the
owner's list. §15 adds eight ideas that are not cards.

---

## 2. Scope and non-goals

### 2.1 In scope

- The signal contract, and the providers that each app adds (§4.1, §5).
- The card contract and the feed groups (§4.2, §4.3).
- The life of a card: seen, snooze, dismiss, resolve, and the rules that
  bring a card back (§6).
- The order of the feed (§7).
- When work runs, and what it costs (§8, §9).
- The `decide` questions, the state they read, and the rules that keep them
  safe (§10).
- The acts a card may run in place (§11).
- The deep links each card needs (§12).
- One new table, and the cache keys (§13).
- The frontend shape of the feed (§14).

### 2.2 Non-goals — named so nobody builds them from this file

- **No second task store.** The feed reads the one lens (D53).
- **No second feed.** `GET /shell/needs` stays, as a view of the same
  providers (§4.4).
- **No second ritual.** Calendar owns `StartupRitual` and `EndOfDayReview`.
  The feed links to them and never copies them (`navigation_shell.md` §4.3).
- **No second planner.** "Plan my day" stays in Calendar. A feed card may
  offer it as a link.
- **No second drafter.** A draft reply comes from the email app's own drafts
  (`email_ai_drafts`). My Day never drafts a reply of its own (§9.1).
- **No second arithmetic.** A report card draws a number that a report or an
  analytics read already computes (`navigation_shell.md` §4.5).
- **No external calendar sync.** Card A needs it, and calendar P4 owns it
  (`calendar_timeboxing.md` §8). §5.1 says what the feed can do without it.
- **No model picker, and no price on a card** (D32.7, D88).
- **No team feed in phase 1.** The team altitude follows NS-5 (MD-12).

---

## 3. What exists today — measured 2026-10-13

| Part | Today | Anchor |
|---|---|---|
| The page | My Day at `/`, ON in production since 2026-10-10. A greeting, one summary line, and three cards: Needs you, Today, Next actions | `workbench/control_plane/src/lib/shell/MyDayPage.tsx` · `src/app/page.tsx:20-33` · flag `myDayFlag.ts` |
| The page rules | `greetingLine`, `summaryLine`, `KIND_LABELS`, `NEEDS_SHOWN = 7`, `cardsFor` | `src/lib/shell/myDay.ts` |
| The card frame | `HomeCard`: title, icon, source, link, footer. `CardError` with Retry | `src/lib/shell/HomeCard.tsx` |
| The feed | `GET /shell/needs`. Four providers run in turn: `tasks`, `projects`, `email`, `approvals`. 2 s for each provider, 4 s in all, 15 rows for each app. **No model** | `apps/services/gateway/gateway/routes/shell/needs.py:136`, `:456`, `:482` |
| The item shape | `{id, app, kind, title, detail, href, at, act, act_ref}`, plus `sources`, `totals` and `capped` | `navigation_shell.md` §7.2 |
| The acts | `done` and `read` only, through the owning app's code. An email row and an approval row have no act | `src/lib/shell/needs.ts` · `app/tasks/lib/completeFromHome.ts` |
| The poll | One shared 60 s poll while the tab shows (the bell) | `src/lib/shell/ShellBell.tsx` |
| Act marks | A client store that hides a row for 30 s after an act. Nothing on the server | `src/lib/shell/needsActs.ts` |
| Order and presets | Per member, in `user_settings.shell_prefs` | `infra/postgres/240_user_settings_shell_prefs.sql:29` |
| The member's day | `day_start_hour` and `day_end_hour` | `infra/postgres/77_gtd_calendar_prefs.sql:18` |
| Calendar events | **Only task time blocks.** External sync answers 501 | `routes/tasks/calendar.py:53-75` |
| Clash detection | Visual only, in the grid. No server read | `app/calendar/components/shared.ts:218-220` |
| Work conflicts | Seven kinds, among them `absent_on_due` and `overcommitted` | `routes/projects/analytics_conflicts.py` |
| Needs reply | `needs_reply_threads`, and the `_LIVE_THREAD` rule | `routes/email/digest.py:137`, `:238` |
| Drafts | One draft for each thread. Drafting is OFF by default (EM-T7) | `email_ai_drafts` · `routes/email/automation/drafting.py` |
| A cached AI line | The email morning brief. `tier-fast`, a Redis key of mailbox, day and sha256 of model, prompt and input, 26 h TTL | `routes/email/digest.py:542-621` |
| Background loops | `asyncio` loops in the gateway lifespan. Each loop holds its own kill switch | `gateway/main.py:40-48` |
| The best loop to copy | CRM mirror sync: a 60 s tick, `is_due`, three at once, a run lock in a table | `gateway/crm_sync/scheduler.py:49`, `:71`, `:104` |
| Change events | An in-process bus. Projects emits `pm.*`, and two sinks listen | `apps/services/ingestion/ingestion/event_hooks.py:32`, `:43` · `gateway/main.py:1612-1621` |
| A job that calls a model as a member | `as_mailbox_owner` binds the member, so the Router bills the right one | `routes/email/scheduler_hooks.py:53` |
| Out-of-app delivery | WhatsApp templates are spec only (WAC-7). Web push is spec only (D-2) | `whatsapp_assistant_channel.md` §5.8 · `chat_run_continuity.md` |
| Deep links | Task, project, report, email and CRM have one. **Calendar has none.** Approvals opens the queue only | §12 |

**The gap in one line.** The feed already knows what is due. It does not know
why it matters, it cannot prepare the next step, and it forgets a row 30
seconds after an act.

---

## 4. The model — signals, cards and the feed

```
 apps                     signals (free)          cards (mostly free)        feed (per member)
 ─────────────────        ──────────────          ───────────────────        ─────────────────
 My Tasks  ──provider──▶  overdue, due_today  ─┐
 Calendar  ──provider──▶  overlap, overbooked ─┤   template words          rank (§7)
 Email     ──provider──▶  needs_reply, quiet  ─┼─▶ + small view       ─▶   + state (§6)   ─▶ GET /shell/feed
 Reports   ──provider──▶  run_ready, moved    ─┤   + acts (code)           + groups (§4.3)
 CRM       ──provider──▶  deal_quiet, due     ─┘   + composition (AI,
 Approvals ──provider──▶  approval                   top cards only)       GET /shell/needs = the needs view
```

### 4.1 A signal

A signal is one fact about one entity that a provider found with a bounded
query. It holds no prose that a model wrote.

| Field | Meaning |
|---|---|
| `signal_id` | `<app>:<kind>:<entity id>`. The same id across reads. Today's needs ids keep their form, for example `tasks:<task id>` |
| `app`, `kind` | The source app and the kind from §5 |
| `entity` | `{type, id, label}`. The thing the card is about |
| `facts` | A small dict of typed values, for example `{due_at, priority, waited_hours}`. The template and the composer read only this |
| `fingerprint` | A short hash of the facts that matter. A change in it means the signal changed in substance (§6.3) |
| `urgent_at` | The time after which the signal is urgent, or null |
| `expires_at` | The time after which the signal is no longer true, or null. A meeting that ended expires |
| `needs` | `true` when the signal asks the member to act. The needs view reads only these |
| `href` | The deep link (§12). Code builds it, never a model |
| `acts` | The acts code offers, each with its class from §11 |

**A provider follows the rules of `navigation_shell.md` §5.2.** It runs on
the request's session at the `get_db()` seam. It calls its app's own
authorized read, and it never writes a visibility rule of its own. It gives
15 signals at most.

### 4.2 A card

A card is a signal drawn for the member. The four reference cards set its
anatomy:

1. **The source line.** The app's name, a dot, and the state of the card, for
   example "Email · Reply drafted". The time at the right.
2. **The headline.** One sentence that states the fact about a person or a
   thing. It never states a feeling.
3. **The why line.** One sentence on why it is here now. It starts with a
   light-bulb icon. A template writes it from `facts` for most kinds (§5).
4. **The body.** A small view that the source app exports: a day strip, a
   draft, a number with bars, task rows. A card may have no body.
5. **The acts.** One main act, filled, and one second act, outlined. Each
   names its effect in words, for example "Move to Thu 2:00 PM".
6. **The menu.** Snooze, Dismiss, "Not useful", and "Why am I seeing this".
7. **The door.** A click on the headline opens the entity in its app.

The frame is `HomeCard` (`src/lib/shell/HomeCard.tsx`), widened by a
`FeedCard` that adds the source line, the why line and the menu. The body is
the app's component, found by kind in the manifest (§14.2). The design rules
of `DESIGN_SYSTEM.md` bind every part. The source line uses the app's icon
through `<Icon name>`, never an app-local colour.

### 4.3 The feed groups

The feed is one column. Groups give it a shape, so a member knows where they
are when they scroll.

| Group | What goes in it | Shown |
|---|---|---|
| **Now** | Due within 2 hours, a block that starts within 30 minutes, an overlap today | Always open. Hidden when empty |
| **Needs you** | Decisions and replies: overdue, approvals, needs reply, an overlap later this week | Always open |
| **Ready for you** | Work that something prepared: a draft, a plan proposal, a report run, a free slot for an overdue task | Open |
| **For your information** | Facts with no act due: a report moved, a colleague is out, a deal changed stage | Open, at most 10, then "Show more" |
| **Later** | Cards the member saw three times with no act (§6.5) | Folded |
| **Handled** | What closed today, by the member, by a colleague, by a rule or by an agent | Folded. Its count feeds the summary line |

Then the feed ends with "You are caught up". The feed never fills itself with
filler to keep a member scrolling.

### 4.4 One feed, two views

The needs feed of `navigation_shell.md` §7.2 stays the contract for the bell
and the sidebar counts. It becomes a **view** of the signal registry:

- One registry of providers serves both routes. A provider registers once.
- `GET /shell/needs` returns the signals with `needs = true`, in today's
  shape, today's order and today's caps. Nothing in the bell changes.
- `GET /shell/feed` returns every signal, with its card fields, its state and
  its group.

**Fence (MD-1).** `tests/unit/test_shell_feed.py::TestOneRegistry` fails if a
provider serves one route and not the other. It also fails if the needs view
of a fixed seed differs from today's `GET /shell/needs` answer.

---

## 5. The card catalogue

Each row is one signal kind. **Phase** says when it can ship. "Template"
means a fixed sentence with values from `facts`. **AI** names the model work
that the kind needs beyond the common pass.

**The common pass applies to every kind.** `my_day.triage` and
`my_day.weight` judge each new card, and `my_day.reason` and
`my_day.next_act` judge each card in the top 10 (§10.3). So a row that says
"None" still gets judged. It needs no detector and no text.

### 5.1 Calendar

| Kind | Signal | Why line | Body | Acts | AI | Phase |
|---|---|---|---|---|---|---|
| `block_overlap` | Two of my scheduled blocks overlap today or this week | Template: "Both are set for 2:00 PM on Tuesday." | A day strip of the two blocks | "Move X to <slot>" (class A), "Open the day" | None | MD-1 |
| `day_overbooked` | Planned hours exceed the working hours of the day | Template: "7 h planned in a 5 h day." | A bar of planned against free time | "Fit what's left" (opens Calendar's replan) | None | MD-1 |
| `no_plan_yet` | It is past the day start and nothing is scheduled today | Template | — | "Plan my day" (link to Calendar) | None | MD-1 |
| `free_slot_for_overdue` | A free slot of 30 minutes or more today, and an overdue task that fits it | Template: "You have 90 min free at 3:00 PM." | The slot and the task | "Block it" (class A) | None | MD-4 |
| `block_soon` | A block starts within 30 minutes | Template | The block and its task | "Open task" | None | MD-1 |
| `meeting_prep` | A Notes meeting starts within 60 minutes | Composition: the open tasks, last thread and last notes with the attendees | A short prep list | "Open notes" | Text, after `my_day.compose` says yes | MD-6c |
| `meeting_overlap` | A meeting overlaps a meeting or a block (card A) | Template from the two events | The two-day strip of card A | "Move to <slot>" (class B), "See options" | None | **MD-11, waits on calendar P4** |

⚠️ **Card A needs external calendar events, and Metorite has none today.**
`POST /calendar/sync` answers 501 (`routes/tasks/calendar.py:62-75`). Until
calendar P4 lands, the feed can see an overlap of the member's own blocks, and
of a block with a Notes meeting. It cannot see a client's site visit. §17 Q3
asks the owner to rank P4.

### 5.2 My Tasks and Projects

| Kind | Signal | Why line | Acts | AI | Phase |
|---|---|---|---|---|---|
| `overdue` | Today's provider | Template: "Due Monday. 2 days late." | Done (A), Snooze (A), "Move to tomorrow" (A) | None | MD-1 |
| `due_today` | Today's provider | Template | Done (A), "Block time" (A) | None | MD-1 |
| `notification` | Today's provider: assigned, mention, comment, nudge | Template from the actor and the excerpt | Mark read (A), Open | None | MD-1 |
| `unblocked` | A task I own was blocked, and its blocker closed | Template: "Ravi closed the API task you waited on." | Open | None | MD-3 |
| `waiting_quiet` | A Waiting task with no activity for 5 working days | Template: "You have waited 6 days on Ravi." | "Nudge Ravi" (B, it uses the Projects nudge), Snooze | None | MD-3 |
| `stale_next` | A Next task with no change for 14 days | Template | "Move to Someday" (A), Open | None | MD-3 |
| `due_at_risk` | The outlook read says a deadline will slip at the current load | Template from the outlook facts | Open, "Ask to move the date" (B) | None | MD-3 |
| `absent_on_due` | A due date falls on a day I am absent | Template from the conflicts read | "Move to <day>" (A) | None | MD-3 |
| `assigned_to_me` | A colleague gave me a task today | Template | Accept (A), Open | None | MD-1 |

### 5.3 Email

| Kind | Signal | Why line | Body | Acts | AI | Phase |
|---|---|---|---|---|---|---|
| `needs_reply` | Today's provider | Template: "Asked yesterday at 4:12 PM." | The last message, 3 lines | Open thread, Snooze | None | MD-1 |
| `reply_drafted` | `needs_reply` and a draft in `email_ai_drafts` (card B) | Template plus a promise, if the email app found one | The full draft and its attachments | "Send" (B), "Edit draft" | **Reuses** the email app's draft. My Day pays nothing | MD-4, send in MD-8 |
| `promise_due` | A promise in the member's sent mail falls due ("I will send it by Friday") | Template from the promise | — | "Add a task" (A), Open | `my_day.commitment` reads each sent message once. It reuses email insights and `wa_commitments` where they already hold the promise | MD-6b |
| `no_answer` | A mail I sent that asked a question had no answer for 3 working days | Template | — | "Follow up" (opens a draft), Snooze | None. The email app's follow-up scan finds it | MD-3 |
| `long_thread` | A needs-reply thread with 6 messages or more | **Composition**: the gist in two sentences | The gist | Open thread | Text, after `my_day.compose` says yes | MD-6c |
| `handled_by_rules` | The email rules filed mail today | — | Count only, in the Handled group | Open the rules history | None | MD-7 |

### 5.4 Reports and analytics

| Kind | Signal | Why line | Body | Acts | AI | Phase |
|---|---|---|---|---|---|---|
| `report_ready` | A report I receive has a new run (card C) | Template: names the meeting on my calendar that the report feeds, when one exists | One `Stat` tile and its bars, from the report's own panel | "Share with team" (B), "Open report" | The report's own AI summary, when one exists. My Day writes none | MD-4 |
| `metric_moved` | A number in a report I receive moved more than its threshold | Template: "Throughput fell 30% this week." | The tile | Open report | None | MD-9 |
| `team_pulse` | (lead only) The morning Team pulse | Template from `pulse` | The pulse panel | Open report | None | MD-12 |

**The number on a report card is the report's number.** The card renders the
report's own panel through `Stat` in `AnalyticsPanels.tsx`. It computes
nothing (`navigation_shell.md` §4.5, "No second arithmetic").

### 5.5 CRM

| Kind | Signal | Why line | Acts | AI | Phase |
|---|---|---|---|---|---|
| `crm_followup_due` | An activity I own has `due_at` today or earlier | Template | Done (A), Open deal | None | MD-3. It needs a new "my due activities" read in `routes/crm/activities.py` |
| `deal_quiet` | A deal I own passed its stage's `max_dwell_days` | Template: "No activity for 12 days in Proposal." | "Log a call" (opens the CRM form), Snooze | None | MD-3 |
| `deal_close_passed` | A deal I own passed its close date and is open | Template | "Move the date" (opens the CRM form) | None | MD-3 |
| `lead_replied` | A lead I own replied by mail | Template | Open thread | None | MD-9 |

### 5.6 People, approvals, agents and WhatsApp

| Kind | Signal | Why line | Acts | AI | Phase |
|---|---|---|---|---|---|
| `approval` | Today's provider, org admin only | Template from the action name | Open Approvals | None | MD-1 |
| `colleague_out` | A person I have a block or a meeting with today is absent | Template from `people_absences` | Open the day | None | MD-3 |
| `agent_done` | A chat run that I started finished while I was away | Template from the run | Open chat | None | MD-3. It reads WS-51's run state |
| `agent_asks` | A run waits for my answer (`chat_pending_ask`) | Template | Open chat | None | MD-3 |
| `whatsapp_waiting` | A WhatsApp thread waits for me | Template | Open thread | None | MD-9 |

### 5.7 The cards about the day itself

| Kind | When | What it says | AI | Phase |
|---|---|---|---|---|
| `brief` | The first visit of the day | The header line of card D. "3 things need you. The rest is handled." Code writes it from the counts. `my_day.reason` picks one lead line from the templates, for example "Dana waits on the Q3 proposal" | None beyond the common pass | MD-7 |
| `while_away` | The first visit after 4 hours or more away | The counts of what came in and what closed, and the top three | None | MD-7 |
| `day_end` | Within 60 minutes of `day_end_hour` | "4 open items left today." Links to Calendar's `EndOfDayReview` and to rollover | None | MD-7 |

---

## 6. The life of a card — how it leaves and how it comes back

The owner asked what happens when a member does not scroll far enough, or
misses a card. This section is the answer. Every rule here is deterministic.

### 6.1 States

| State | Meaning | Visible |
|---|---|---|
| `new` | The member has not seen it | Yes, with a dot |
| `seen` | The member saw it and did not act | Yes |
| `acted` | The member ran an act from the card | No. It goes to Handled when the signal resolves |
| `snoozed` | Hidden until a time or a change | No |
| `dismissed` | Hidden until its fingerprint changes | No |
| `resolved` | The signal is no longer true, by any route | No. It goes to Handled |
| `expired` | Its `expires_at` passed | No |

**`resolved` needs no write.** A provider that no longer returns a signal
resolves it. A task done in Projects leaves the feed with no extra work. This
is the "no sync" rule of D52, applied to the feed.

### 6.2 Seen

- A card is **seen** when half of it is in the viewport for 1.5 s, while the
  tab is visible. The client collects the ids and sends one batch each 10 s
  and on `pagehide`.
- A card in the bell panel is not seen by this rule. The bell is a glance.
- Each seen write counts a **visit**: two sightings within 30 minutes are one
  visit.

### 6.3 What brings a card back

A card that left comes back on the first of these:

1. **Its fingerprint changed.** A new message in a dismissed thread, a new due
   date, a new run of a report. The card returns as `new`, with "Updated" in
   its source line.
2. **It crossed its `urgent_at`.** The member saw a task at 9:00, and it is
   due at 15:00. It comes back at 13:00, in the Now group, with "Still open".
3. **A new day started.** At the member's `day_start_hour`, every `seen`
   card that is still true returns to the ranking, with its visit count kept.
   A dismissed card stays dismissed.
4. **Its snooze ended.** See §6.4.

### 6.4 Snooze

Snooze offers four choices:

- **Later today** — 3 hours, or the day end, whichever comes first.
- **Tomorrow** — the next working day's `day_start_hour`, from
  `work_schedule.effective_schedule`.
- **Next week** — the first working day of next week.
- **Until it changes** — the card returns only when its fingerprint changes,
  for example when Dana replies.

A snooze of an `overdue` task does not change its due date. That needs the
"Move to tomorrow" act, which writes the date through the lens.

### 6.5 The scroll-past rule — "Later"

A member who scrolls past a card three times on three visits, and never acts,
tells us something. The card moves to the folded **Later** group. Two
things still lift it out:

- It crosses `urgent_at` (§6.3 rule 2).
- A new day starts, and the card is `overdue` or `needs_reply` older than
  48 h. Those return to Needs you once each day, never more.

### 6.6 Missed — the escalation ladder

A card that matters and stays `new` climbs one step at a time. Each step is
quieter than a red badge storm (`navigation_shell.md` §3.2c).

| Step | When | What happens | Built |
|---|---|---|---|
| 1 | Always | The card sits at the top of its group, with a dot | MD-2 |
| 2 | Always | The bell badge and the sidebar row count include it | Yes, today |
| 3 | `urgent_at` passed, and the card is still `new` | The card moves to Now, and the bell row moves first | MD-2 |
| 4 | Step 3 for 60 minutes, inside working hours, and the member opted in | One message on the member's channel: WhatsApp or web push | **MD-10, owner gate** |

**The caps on step 4.** Three messages each day at most. None outside the
working hours of `work_schedule`. One message holds every card that reached
step 4 in the same hour. The channel changes the delivery and never the
function (`whatsapp_assistant_channel.md`).

### 6.7 Dismiss and "Not useful"

- **Dismiss** hides this card until its fingerprint changes.
- **Not useful** hides the card, and counts against its kind for this member
  (§7). After three "Not useful" on one kind in 30 days, the menu offers
  "Stop showing <kind>". That mute goes into `user_settings.shell_prefs`.
- **A mute never hides a `needs` kind from the bell.** Overdue tasks and
  approvals stay visible somewhere. A mute changes the feed only.

### 6.8 Handled, and the honest summary

- **Handled** lists the signals that resolved since the day start. The
  member, a colleague, an email rule or an agent closed each one. Each row
  names who closed it.
- **The summary line counts real rows.** "3 things need you" is the count of
  open cards in Now and Needs you. "The rest is handled" shows only when the
  Handled group is not empty and every source answered.
- **A failed source silences the claim.** The rule of NS-3 stays: when a
  source failed, the summary says nothing about needs, and the card names the
  gap.

---

## 7. The order

The order is a score. Code owns time, and `decide` owns judgment.

```
score = base(kind) + urgency + weight + affinity − fatigue
```

| Term | Who sets it | Rule |
|---|---|---|
| `base(kind)` | Code | A fixed number for each kind, in one table in `gateway/routes/shell/feed_rank.py`. Needs kinds start above information kinds |
| `urgency` | Code | Grows as `urgent_at` comes near: +40 inside 2 h, +60 when passed. Overdue adds 2 for each day late, to 20 at most. A model never does date arithmetic |
| `weight` | **`decide`**, `my_day.weight` (§10.3) | The level from 0 to 4, times 6. With no answer, or an answer under its confidence gate, the old path sets it: the task's priority from the D78 matrix, +15 for a CRM customer, +10 for a promise |
| `affinity` | Code | From the member's own acts on this kind over 30 days: `(acted − not_useful) / shown × 20`, between −20 and +20 |
| `fatigue` | Code | −8 for each visit with no act, to −24 at most |

**The group comes from `decide` too.** `my_day.triage` (§10.3) puts each card
in Now, Needs you, Ready for you, For your information, or Later. With no
answer, the group table of §4.3 decides.

Four rules sit on top of the score:

1. **The group comes first.** The score orders cards inside a group.
2. **`decide` may demote a needs card, and it may never hide one.** An
   overdue task, an approval or a needs-reply thread goes at worst to Later.
   Only a card that is not a needs kind may leave the feed on a `drop`
   answer.
3. **Today's rules stay.** 15 signals for each app at most. An approval takes
   only the room that the member's own work leaves (`YIELDING_KINDS`,
   `needs.py:163`).
4. **The order holds while the member reads.** The client keeps the order of
   the cards it shows during one visit. A new card shows a pill at the top,
   "2 new". It enters on a click, or on the next visit. A card never moves
   under the pointer.

**Fence (MD-1, MD-6a).** `tests/unit/test_feed_rank.py` holds the table, each
term, the four rules, and a fixed seed whose order may change only with the
table. It also holds rule 2: a `drop` answer for an overdue task leaves the
card in Later.

---

## 8. Refresh — when work runs

The feed is live without a model in the read path. Three triggers do the
work, and each has its own cost.

| Trigger | What runs | What it costs | Ticket |
|---|---|---|---|
| **A read** | The providers, the rank and the state join. Bounded SQL. The read uses the cached `decide` answers, and never waits for a new one | No model. One read each 60 s while the tab shows, shared with the bell | MD-1 |
| **A change** | An event marks the member's feed **dirty**: a `pm.*` event, an email sync cycle, a report run, a chat run end | A Redis write. No model | MD-5 |
| **A clock** | One gateway loop, `my_day`, with a 60 s tick. It asks `decide` about the cards that are new or changed, makes the morning pass, handles the `urgent_at` crossings and the day end, and sweeps old rows | `decide` calls for active members only, in batches (§10.4) | MD-5, MD-6a |

### 8.1 The read cache

- The server keeps the last answer for each member for 30 s, under a key that
  holds the member and a **feed version**. A change event bumps the version.
  So a quiet member's poll costs one Redis read, and a busy member's poll
  recomputes at once.
- The key goes through the tenant-prefix wrapper, `acb_common.tenant_redis`
  (R5c). The key holds the member, because two members hold different grants.
- The client sends the version it holds. An unchanged version answers 304.

### 8.2 The change sink

The gateway registers a third sink on the in-process bus, beside the two at
`gateway/main.py:1612-1621`. It maps an event to the members it touches, and
bumps their feed version. It never computes a card. The email app bumps the
version from its post-sync hooks (`routes/email/scheduler_hooks.py`).

⚠️ **The bus is in-process.** An event emitted in one gateway process does not
reach another. Today the gateway runs one process. A second process would
need Redis pub-sub through `TenantRedis.publish`. MD-5 names this as a limit,
and the 30 s cache bounds the damage: a missed event costs at most 30 s.

### 8.3 The loop

The `my_day` loop copies the CRM mirror loop, `gateway/crm_sync/scheduler.py`:

- A 60 s tick. An `is_due(member)` test. Three members at once at most.
- The kill switch lives inside the start function (`gateway/main.py:40-48`).
- It enumerates organizations on the RLS-exempt table, then binds each with
  `tenant_session`. That read is a tenant-discovery site, registered in
  `tests/unit/test_db_engine_seam.py`.
- A member is **active** when they opened My Day in the last 3 days. The loop
  skips every member who is not active. A dormant seat costs no model call.
- **A member is due** when their feed is dirty and the last pass was 2
  minutes ago or more, or when their `day_start_hour` arrives. So a burst of
  20 task edits makes one pass, not 20.
- **The morning pass** runs 15 minutes before `day_start_hour`. So the first
  visit of the day already has the judged order, and the order does not jump
  under the member's eyes.

---

## 9. Cost — the rules that keep it cheap

### 9.1 Ten rules

1. **No model in the read path.** A feed read never waits for a model. A card
   with no answer yet shows its old-path group, weight and template. The
   judged values fill in on a later read.
2. **`decide` first, text last.** Every judgment is a typed question (§10.3).
   A text model runs only for a gist or a prep list, and only after
   `my_day.compose` says yes.
3. **Reuse what an app already paid for.** Email's own `decide` answers say
   which thread needs a reply (`email.thread_status`). A draft reply is the
   email app's draft. A report sentence is the report's own AI summary. My
   Day asks only what no app has asked.
4. **Once for each fingerprint.** The loop caches each answer. The key holds
   the member, the signal and the card's fingerprint. It also holds the hash
   of the member's day context, and the question version. The same facts never pay
   twice. This is the email brief pattern (`routes/email/digest.py:553-621`).
5. **Only what changed.** A pass asks about the cards that are new, or whose
   fingerprint changed. A pass with nothing new makes no call.
6. **Sixteen questions in one request.** One request carries up to 16
   questions over one shared state (`MAX_QUESTIONS`,
   `customer_console/decide.py:52`). The charge for each call has a floor
   (H-42), so the loop packs questions and counts calls, not only tokens.
7. **Only near the top.** A text composition runs for the top 5 open cards of
   an active member. It also runs when the member scrolls to a card. Never
   before.
8. **A cap for each member and each organization.** 60 `decide` calls and
   10 compositions for each member each day. The operator sets a ceiling
   for each organization. Past a cap, cards keep their old-path values.
9. **The balance gate stops it, and the feed keeps working.** A 402 or a 403
   starts the 15-minute cool-down of `decide_features.py`
   (`REFUSAL_COOLDOWN_S`). The feed then runs on the old path. That is D88's
   rule for the command bar.
10. **Off is one switch for each feature.** Each `my_day.*` feature has its
    own mode in `DECIDE_FEATURE_MODES`. `MY_DAY_COMPOSE` stops every text
    composition. An operator can also unbind a task in the Router (H-178).

### 9.2 The estimate

For one active member on one working day:

| Call | Calls | Input tokens each | Input tokens |
|---|---|---|---|
| Morning pass: triage and weight for about 30 cards, 2 questions each, 8 cards in a request | 4 | 6,000 | 24,000 |
| Passes on a change: about 8 new cards each time | 10 | 5,000 | 50,000 |
| Reason, next act and slot, packed into the same passes | 0 | — | — |
| Day start: resurface the seen cards | 1 | 5,000 | 5,000 |
| Commitments in sent mail, one request for each sync cycle that sent mail | 4 | 1,500 | 6,000 |
| **`decide`, total** | **19** | | **85,000** |
| Text: thread gist and meeting prep, when `my_day.compose` says yes | 4 | 2,500 | 10,000 |

**The vendor cost of `decide` is close to nothing.** At USD 0.042 for each
million input tokens, with free output, 85,000 tokens cost about USD 0.004
each day. That is about USD 0.08, or ₹7, each month of 22 working days.

**The text model is the larger cost.** Assume a price near US$0.60 for each
million input tokens, the range of the default `tier-fast` model. Then the 4
compositions cost about USD 0.13 each month, or ₹11.

**The customer's charge is not this number.** The customer pays in AI
credits, at the rate card of H-42, and the floor on each call sets most of
a `decide` charge. So the design counts calls: about 23 calls for each active
member each day, 19 of them `decide`.

**The real cost risk is the database, not the model.** One poll each 60 s from
each open tab runs four providers. §8.1's cache keeps that flat, and MD-5
must measure it.

### 9.3 Who pays — answered by the owner, 2026-10-13

**The customer pays for every AI call in AI credits.** That covers each
`decide` call and each text composition. The feed with no AI is free: the
signals, the templates, the order of the old path and every act. The card
shows no price (D32.7, D88).

**A customer with no credit keeps a working feed.** The CP-6 balance gate
refuses, the cool-down starts, and every card falls back to its old path. The
feed shows one line in the header: "AI suggestions are paused. Ask an admin
to add credits." It is the command bar's line (`navigation_shell.md` §6.5).

⚠️ **The text half waits on two things outside this spec**, as NS-4b does.
The first is **H-171**: the product's own text AI is not metered today. The
second is **H-69**: `ROUTER_SERVING_ENABLED` is an owner flip. The `decide`
half does not wait on them. `decide` already bills through the Console, and
its first call is a `usage_event` row (`acb_llm/decide.py`, `request_id`).

---

## 10. Decide first — how My Day thinks

### 10.1 Why `decide` fits this feed

A chief of staff makes many small calls on each item. Does this matter today?
Is it urgent or only loud? Is it the member's to do, or a colleague's? Which
reason will make the member act? Each call is a choice from a short list,
not an essay. That is the shape of `decide` (D75).

- **It is cheap.** USD 0.042 for each million input tokens, and the output
  is free (D75).
- **It is typed.** An answer is a probability, an option key or a level. It
  is never text, so it can never hold a link, a time or a name of its own.
- **It says how sure it is.** Each answer carries a probability or a
  confidence. The feed acts on a judged answer only above a gate, and takes
  the old path below it.
- **It is already live.** `DECIDE_ENABLED` is ON in production since
  2026-10-02, and email asks it for the rule match and the thread status
  (HANDOFF H-166).
- **It packs.** One request holds up to 16 questions over one state.

### 10.2 The rule: decide picks, templates speak, code acts

- **Code builds every option.** The list of groups, the reason templates, the
  acts and the slots all come from code, out of `facts`. `decide` answers
  with one of their keys.
- **A template writes every sentence on a card.** The why line is the
  template that `decide` picked, filled with values from `facts`. A judged
  card holds no word that a model wrote.
- **Code runs every act.** An act's target, time and recipient come from the
  option that code built, and `decide` only chose its key.
- **An answer that is not one of our keys is no answer.** The feed reads it
  as missing and takes the old path. `compare_choice` already logs such an
  answer as `"unknown"` (`gateway/decide_features.py:416-446`).

**Fence (MD-6a).** `tests/unit/test_feed_decide.py::TestKeysOnly` answers
each question with a key that is not in its options, and with a valid key.
It asserts that the card's `href`, `acts`, `act_ref` and why line are
byte-equal to an old-path card in the first case. In the second case, they
must come from the chosen option.

### 10.3 The questions

Each row is one feature in the one mode registry,
`gateway/decide_features.py` (§10.6).

| Feature | Type | The question, in short | Options or levels | What code does with the answer | Old path |
|---|---|---|---|---|---|
| `my_day.triage` | choice | "Where does this belong in the member's day?" | `now`, `needs_you`, `ready`, `fyi`, `later`, `drop` | Sets the group. §7 rule 2 limits `drop` | The group table of §4.3 |
| `my_day.weight` | score, 5 levels | "How much does acting on this today move what the member must get done?" | `none`, `low`, `some`, `high`, `critical` | Sets `weight` (§7) | The priority matrix term |
| `my_day.reason` | choice | "Which fact best explains why this matters now?" | 2 to 6 reason templates of the kind, each shown filled | Picks the why line | The first template of the kind |
| `my_day.next_act` | choice | "Which next step fits best?" | The acts that code built for this card | Picks the main act. The rest go to the menu | The kind's default act |
| `my_day.slot` | choice | "Which slot suits this work best?" | Up to 8 free slots that the calendar packer found | Fills "Block it" or "Move to" | The earliest slot |
| `my_day.resurface` | boolean | "Should this seen item come back to the member today?" | — | Back in its group when the probability is 0.6 or more, else Later | §6.3 and §6.5 alone |
| `my_day.commitment` | boolean, then choice | "Does this sent message promise a deliverable?" then "By when?" | `today`, `tomorrow`, `this_week`, `next_week`, `none` | Makes a `promise_due` signal (§5.3) | No signal |
| `my_day.compose` | boolean | "Would a short summary save the member time here?" | — | A text composition runs only on yes | No composition |
| `my_day.nudge` | boolean | "Is this worth a message outside the app now?" | — | Step 4 of §6.6, at 0.8 or more, inside the caps | No message |

**One pass asks several questions about one card.** Triage and weight go
together for every new card. Reason, next act and slot join them only for a
card that reaches the top 10. So the loop spends the most on the cards the
member will see.

### 10.4 The state — what the chief of staff knows

One request carries one state. Code builds it, and clips every text with
`clip_fact` (`gateway/decide_features.py:224`). It holds no mail body.

- **The member.** The role preset, the local time, the working hours of
  today, and today's One Thing (`calendar_day_state`).
- **What the member must get done.** The active goals and areas
  (`my_tasks_horizons`), the projects the member owns or leads, and the
  deadlines of the next 7 days. At most 10 of each.
- **The day.** Today's blocks and meetings, with their titles and times.
- **The member's habits.** For each kind, how often the member acted and how
  often they said "Not useful", over 30 days. Counts only.
- **The cards.** Up to 8 cards, each as `{id, app, kind, facts}`. A mail
  subject or a task title is a clipped fact. A body never goes in.

This state is how the feed follows what the member must get done across
projects and email (owner, 2026-10-13). The same overdue task weighs more on
the day its project is the One Thing.

**The day's part is cached.** The member, the goals and the day change a few
times each day. Their hash is part of each cache key (§9.1 rule 4). So a
change to the One Thing judges the open cards again, once.

### 10.5 The member's corrections

`decide_features.instructions` takes a block, "Corrections from the user",
under each question (`gateway/decide_features.py:254-272`). Email uses it for
the rule match. My Day uses it the same way:

- "Not useful" on a card offers an optional line: "Tell My Day why".
- The line goes into `user_settings.shell_prefs`, newest first, clipped to
  1,500 characters (`CORRECTIONS_CLIP`).
- It rides under `my_day.triage` and `my_day.weight` on every later pass.

So "Newsletters from Acme never need me" changes the next morning's feed
with no training, no rule editor and no setting screen.

### 10.6 Adoption — off, shadow, on

The feed follows the adoption rule of D75 item 7 and `customer_console.md`
§6A.14.

1. **Register.** MD-6a adds the `my_day.*` features to `FEATURES` in
   `gateway/decide_features.py`. Every default is `off`. It adds no second
   registry, and no second helper. H-44 absorbs the registry later.
2. **Widen `ask`, do not copy it.** `ask` names its log ids `account_id` and
   `message_id`, and its `module_slug` falls back to `email`. MD-6a gives it
   one generic map of ids, and keeps the two email keywords working. My Day
   passes `module_slug="my_day"`.
3. **Shadow.** In `shadow`, the feed always shows the old path, asks `decide`
   beside it, and logs both answers with `request_id`. The log holds keys and
   numbers only, never tenant text.
4. **Measure.** For each feature, the owner sees how often the two paths
   agree. The owner also sees how often the member acted on the top 5 cards
   of each path. MD-6a writes the read for it.
5. **On, with a gate.** In `on`, the judged answer sets the value when its
   confidence or probability passes the feature's gate. The gate comes from
   the shadow data. Below it, the old path sets the value.

⚠️ **`on` does not need `shadow` first for a feature with no old answer.**
`my_day.commitment`, `my_day.compose` and `my_day.nudge` decide something
that the old path never decides. `email.insights_screen` set this precedent
(`decide_features.py:102-104`).

### 10.7 Whose call it is

A `decide` call names the member it serves. In the loop, the member is bound
through `job_member_scope`, the way `as_mailbox_owner` binds a mailbox owner
(`routes/email/scheduler_hooks.py:53`). The member is a stored fact, so the
call passes `member_proven=True`, as `ask` does for email. So the Router
bills the right member, RLS binds the right tenant, and the state holds only
what the member may read.

### 10.8 Mail is data

A mail subject is input that a stranger wrote. It enters the state as a
clipped fact, and never as an instruction. A subject that tries to steer
`decide` can move a probability on one card. It cannot add an option, so it
cannot add a link, a time or a recipient (§10.2). The thread gist is the one
text composition that reads mail. It fences the thread as DATA in its prompt,
as the day planner fences its task list (`calendar_ai_review.md` §2, item 1).

---

## 11. Acts — what a card may do in place

`navigation_shell.md` §4.3 allows My Day three one-click acts: done, approve
and snooze. Cards A, B and C need more. This spec proposes three classes,
**D-MD-1**, for the owner to take (§17 Q1).

| Class | What it is | Examples | In the card |
|---|---|---|---|
| **A** | It changes only the member's own data, and Undo reverses it | Done, snooze, mark read, move my own block, move my own due date, block time, accept a plan proposal | One click, then the shared Undo toast |
| **B** | It reaches another person | Send a drafted reply, move a meeting with attendees, nudge a colleague, share a report, ask to move a date | **Default today: it opens the owning app, filled in.** If the owner takes D-MD-1, one click with the full effect on the card and a 10 s Undo before it leaves |
| **C** | It is destructive, moves money, or approves an agent's act | Delete, bulk acts, approve a pending action | Never from a card. It opens the owning app |

**The class B rule, if the owner takes it.** All four conditions bind:

1. The card shows the **whole** effect. For a reply, that is the full draft
   and every recipient. For a meeting, it is both times and every attendee.
2. The act calls the owning app's own write route. My Day adds no write route
   (`navigation_shell.md` NS-3).
3. The send waits 10 s with "Sending… Undo" before it leaves.
4. The act writes `meta.via = "my_day"`, the rule of D-PM-36.

**Fence (MD-8).** `src/lib/shell/feedActs.test.ts` fails if an act of class B
or C runs from a card with no confirm step. It also fails if a card act calls
a route that its owning app does not own.

---

## 12. Deep links

Every card opens its entity. These links exist today, and the feed uses them
as they are.

| Entity | Link | Anchor |
|---|---|---|
| Task | `/projects?task=<id>` | `app/projects/lib/card.ts:212-218` |
| Project | `/projects?project=<id>` | `app/projects/page.tsx:2502-2520` |
| Report | `/projects?app=reports&template=…&subject=…` | `app/projects/lib/reportBuilder.ts:759-765` |
| Email | `/email?email=<message id>&account=<account id>` | `app/email/lib/emailLink.ts:35-42` |
| CRM | `/crm?deal=<id>` (or `lead`, `contact`, `organization`) | `app/crm/lib/urlState.ts:27` |
| Meeting notes | `/notes/meeting/<id>` | `app/notes/` |
| Chat | the session link of `/chat` | WS-51 |

**Three gaps, and MD-3 closes them:**

- **Calendar has no link to a day or a time.** MD-3 adds `/calendar?date=
  YYYY-MM-DD&at=HH:MM`. It opens the day view and scrolls to the time.
- **Approvals opens only the queue.** MD-3 adds `/approvals?action=<id>`,
  which opens that one proposal.
- **`EntityPill` has no email, event or deal kind.** MD-3 adds three kinds to
  `src/components/ui/EntityPill.tsx`, so a card body names them as pills.

---

## 13. Data

### 13.1 One new table

`my_day_card_state` holds the state of a card for one member. It holds a row
only for a signal the member saw or touched. The signals themselves are never
stored, because the providers recompute them.

| Column | Type | Note |
|---|---|---|
| `organization_id` | `UUID NOT NULL DEFAULT current_setting('app.tenant_id', true)::uuid` | The tenant key |
| `member_email` | `TEXT NOT NULL` | From the session, never from the request body |
| `signal_id` | `TEXT NOT NULL` | §4.1 |
| `kind` | `TEXT NOT NULL` | For affinity (§7) |
| `state` | `TEXT NOT NULL` | `seen`, `acted`, `snoozed`, `dismissed`, `not_useful` |
| `fingerprint` | `TEXT` | The fingerprint at the last state change |
| `visits` | `INT NOT NULL DEFAULT 0` | §6.2 |
| `first_seen_at`, `last_seen_at`, `acted_at` | `TIMESTAMPTZ` | |
| `snoozed_until` | `TIMESTAMPTZ` | Null with `snooze_on_change` true means "until it changes" |
| `snooze_on_change` | `BOOLEAN NOT NULL DEFAULT false` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

- Primary key `(organization_id, member_email, signal_id)`.
- ENABLE and FORCE ROW LEVEL SECURITY, with one policy that holds USING and
  WITH CHECK. The template is `infra/postgres/250_crm_views.sql:36-77`.
- **R5a.** It must pass `tests/unit/test_tenant_coverage.py`.
- **R1.** The migration takes the next free number at build time. Today that
  is 253. Check it again at merge.
- **R6.** A new table is additive. Nothing reads it before MD-2 ships.
- **Retention.** The `my_day` loop deletes rows with no change for 30 days.

### 13.2 No other table

- **Mutes and feed preferences** go into `user_settings.shell_prefs`, the
  JSONB column of migration 240. No new column.
- **`decide` answers and text compositions** live in Redis, with a 26 h TTL,
  the email brief pattern. A lost key costs one new call, and it never loses
  state.
- **The member's corrections** (§10.5) go into `user_settings.shell_prefs`,
  beside the mutes.
- **The feed version** lives in Redis, under the tenant-prefix wrapper.
- **The memory service is not the state store.** Mem0 has no organization key
  and is off by default (`packages/acb_memory`).

---

## 14. The frontend

### 14.1 The page

- `/` stays `MyDayPage`, behind a second flag, `NEXT_PUBLIC_MY_DAY_FEED`.
  With it off, the page is today's page.
- **One column, about 720 px wide**, the measure of the reference cards. At
  `xl` the right rail keeps `TodayCard`, so the day's timeline stays in view
  while the member scrolls. Next actions becomes the `due_today` and
  `stale_next` cards, so it leaves the grid.
- The header keeps `greetingLine` and `summaryLine`, with §6.8's honest
  counts.
- The feed reads `GET /api/shell/feed` through `useCachedResource`. A true
  miss shows `Skeleton` rows. A revisit never blanks (`AGENTS.md` rule 9).
- **One poll.** The feed and the bell share the 60 s poll of `ShellBell.tsx`.
  The feed adds no second timer.
- The first 20 cards load first. Scrolling loads the next 20, to the end of
  the feed.

### 14.2 The card registry

- An app declares its kinds in its manifest, in a new `feedKinds` field of
  `NavPane` (`src/lib/nav.ts`): `{ kind, body }`. `navigation_shell.md` §5.1
  adds one row for it.
- `FeedCard` in `src/lib/shell/` draws the frame. It finds the body by kind.
  A kind with no body draws the frame alone.
- The first bodies:
  - `CalendarStrip` from Calendar, for card A
  - `DraftPreview` from Email, for card B
  - `ReportTile` from Reports, for card C, built on `Stat`
  - `TaskRows` from My Tasks

### 14.3 Keys and the phone

- `j` and `k` move between cards. `e` runs the main act if it is class A.
  `s` opens snooze. `Enter` opens the entity. The keys never collide with the
  one `⌘K` listener.
- On a phone the feed is the page. The acts sit at the foot of each card, in
  reach of a thumb. The right rail goes away, and the Now group shows the
  next block instead.

### 14.4 The look

The look is the house look. `DESIGN_SYSTEM.md` binds every part, and the
reference screenshots set the anatomy, never the colours. The `visual-review`
skill checks the feed in light mode, at compact density, under a changed
accent, and beside My Tasks.

---

## 15. Eight ideas beyond the cards

1. **"Why am I seeing this".** The card menu shows the score terms in words:
   "Due in 2 hours. You act on most overdue cards." It builds trust, and it
   is the first thing a member asks of a chief of staff.
2. **Ask about this card.** One menu row opens the assistant dock with the
   card as context. The dock owns the talk (`navigation_shell.md` §7.1).
3. **Teach My Day.** "Not useful" and mutes are the training. No survey and
   no setting screen.
4. **Delegate from a card.** "Give this to…" on a task card opens the assign
   form of Projects. The People rebalance read fills in a suggestion.
5. **Focus.** During a focus block in Calendar, the feed shows only Now. The
   rest waits until the block ends.
6. **The week ahead.** On the last working day, a card lists next week's
   deadlines, absences and heavy days. It reads the outlook section of
   reports.
7. **What agents did.** The Handled group names each agent and each email
   rule that closed something, with a link to its history. A member sees the
   work that was done for them.
8. **The lead's morning.** At the team altitude, a lead's feed adds Team
   pulse, at-risk work and a person who is overloaded. This is MD-12, after
   NS-5.

---

## 16. Tickets

Every ticket ships dark. MD-1 to MD-5 call no model.

### MD-1 · The signal registry and `GET /shell/feed`, with no AI — AGENT-SAFE (build), OWNER-GATE (turn on)

Files: `gateway/routes/shell/needs.py` (split into a registry),
`gateway/routes/shell/feed.py`, `gateway/routes/shell/feed_rank.py`,
`src/app/api/shell/feed/route.ts`, `src/lib/shell/FeedCard.tsx`,
`src/lib/shell/MyDayPage.tsx`. Flag `NEXT_PUBLIC_MY_DAY_FEED`.

Done when:

1. One provider registry serves `/shell/needs` and `/shell/feed`. The needs
   answer for a fixed seed is unchanged.
2. The feed returns the MD-1 kinds of §5. Each has a template why line, a
   group, a score and its acts.
3. `block_overlap` and `day_overbooked` read the member's own blocks and
   `work_schedule`. Each has a negative test: another member's block never
   reaches the card.
4. With the flag on, `/` renders the feed column. With it off, `/` renders
   today's page.
5. The feed SQL ran against a real Postgres (R8), and the run is in the PR.

### MD-2 · The life of a card — AGENT-SAFE

Files: migration `<next free>_my_day_card_state.sql`, `POST /shell/feed/state`,
the seen observer in `src/lib/shell/`.

Done when:

1. The table passes `test_tenant_coverage.py`, and another org's row is
   invisible under FORCE RLS (R8).
2. Seen, snooze (four choices), dismiss and "Not useful" write the table, and
   the feed honours each.
3. Each return rule of §6.3, and the Later rule of §6.5, has its own test.
   The test fails when someone removes its rule.
4. The client keeps the order during a visit, and shows the "N new" pill.
5. The Handled group and the honest summary line of §6.8 render, and a failed
   source silences "The rest is handled".

### MD-3 · More signals, and the three link gaps — AGENT-SAFE

The MD-3 kinds of §5, the CRM "my due activities" read, `/calendar?date=&at=`,
`/approvals?action=`, and three `EntityPill` kinds. Each provider has its own
negative test, as NS-3 done-when 3 has.

### MD-4 · Card bodies — AGENT-SAFE

`CalendarStrip`, `DraftPreview` (read only, from `email_ai_drafts`),
`ReportTile` and `TaskRows`, and the `feedKinds` manifest field. Done when each
body renders in the `visual-review` contexts, and `conformance.test.ts` passes
with no new budget.

### MD-5 · The loop, the change sink and the read cache — AGENT-SAFE

The `my_day` loop with its kill switch, the third event sink, the feed version
and the 304 path. Done when a measured run shows that a quiet member's poll
makes no provider query, and the run is in the PR.

### MD-6a · The `decide` pass, in shadow — AGENT-SAFE (build), OWNER-GATE (turn on)

Files:

- `gateway/decide_features.py`: the `my_day.*` features, and `ask` with a
  generic map of ids.
- `gateway/routes/shell/feed_decide.py`: the questions, the state builder and
  the reader.
- The `my_day` loop of MD-5.

Done when:

1. The six judgment features of §10.3 (`triage`, `weight`, `reason`,
   `next_act`, `slot`, `resurface`) are in the one registry, `off` by
   default. No second registry and no second helper exist.
2. In `shadow`, the feed shows the old path, and logs both answers with keys,
   numbers and `request_id` only. A test asserts that no log line holds a
   title, a subject or a name.
3. `TestKeysOnly` (§10.2) passes, and so does the §7 rule 2 test.
4. A pass with no new card makes no call. A repeat of the same facts makes
   no call. A test counts the calls.
5. A 402 starts the cool-down, and every card stays on its old path.
6. The read for §10.6 item 4 exists: agreement and act rate, for each
   feature.

**Turning on `shadow` for a real organization waits on Q6.** Shadow mode
sends tenant content too.

### MD-6b · `decide` on, with gates, and the commitment detector — AGENT-SAFE (build), OWNER-GATE (turn on)

`on` for each judgment feature, at the gate the shadow data sets.
`my_day.commitment` in the email post-sync hook, and the corrections of
§10.5. Done when a judged answer under its gate leaves the old-path value, and
a test proves it for each feature.

### MD-6c · Text compositions — AGENT-SAFE (build), OWNER-GATE (turn on)

The thread gist and the meeting prep, behind `my_day.compose` and the flag
`MY_DAY_COMPOSE`. Done when a composition runs only after a yes, a repeat
makes no call, and the gist fences the thread as DATA. **Waits on H-171 and
H-69 before the flag goes on.**

### MD-7 · The day cards — AGENT-SAFE

`brief`, `while_away` and `day_end`. `day_end` links to `EndOfDayReview` and
copies nothing of it.

### MD-8 · Class B acts in the card — OWNER-GATE until Q1

Send a drafted reply, move a meeting with attendees, nudge, share a report.
The four conditions of §11 and the `feedActs.test.ts` fence.

### MD-9 · Learning and the second wave of signals — AGENT-SAFE

Affinity, mutes, "Why am I seeing this", `metric_moved`, `lead_replied`,
`whatsapp_waiting`. "Why am I seeing this" names the judged group and weight
in words, for example "Judged high: it moves the Q3 launch".

### MD-10 · Nudges outside the app — OWNER-GATE

Step 4 of §6.6, on WhatsApp templates (WAC-7) or web push (D-2). Opt-in for
each member. It sends a message to a real person through a third party, so
CLAUDE.md §3a rule 3 binds.

### MD-11 · Meeting overlaps — BLOCKED on calendar P4

Card A in full. It waits on external calendar sync, which
`calendar_timeboxing.md` §8 owns and whose OAuth credentials are an owner gate.
The owner confirmed on 2026-10-13 that Metorite will read Google and Outlook
calendars, and that clashes fold into the feed then.

When sync lands, MD-11 adds one provider, `meeting_overlap`, and asks
`my_day.slot` to choose among the free slots of every attendee that Metorite
can see. No other part of this spec changes.

⚠️ **One shortcut to check at that time.** The email app already holds a
Microsoft Graph connection for each Outlook mailbox. A calendar read on the
same connection needs one more consent scope. The calendar spec owns that
choice, not this one.

### MD-12 · The team altitude — AGENT-SAFE, after NS-5

The lead's cards of §15 item 8, under `reader_scope` and the five rules of
`navigation_shell.md` §4.2.

### Order

MD-1 → MD-2 → MD-5 → MD-6a → MD-3 → MD-4 → MD-6b → MD-7 → MD-9 → MD-6c →
MD-8 → MD-12 → MD-10 → MD-11.

`decide` comes early, because it is cheap and it is the brain of the feed.
MD-6a can start in shadow as soon as the loop of MD-5 exists. So the shadow
data is ready by the time the owner wants to turn it on. The text model comes
late, because it costs the most and waits on H-171 and H-69.

---

## 17. Owner gates and open questions

### 17.1 Gates — refuse these by name

| Act | Why it is the owner's | Gate |
|---|---|---|
| Turn on `NEXT_PUBLIC_MY_DAY_FEED` in production | It changes the first page of every member in every organization at once | Owner only, as `navigation_shell.md` §13.1. The dev-phase window does not open it |
| Turn on `MY_DAY_COMPOSE`, or any `my_day.*` mode other than `off` in `DECIDE_FEATURE_MODES` | It spends customer credits, and it sends tenant content to a model vendor | Owner only. CLAUDE.md §3a rule 3. `shadow` also waits on Q6 |
| MD-10, a message outside the app | It reaches a real person through a third party | Owner only. CLAUDE.md §3a rule 3 |

⚠️ These bind by prose only. `plan-guard.mjs` names none of these flags (R7,
advisory), the same gap as `navigation_shell.md` §13.1.

### 17.2 Open — each has a default an agent builds to

| # | Question | Default |
|---|---|---|
| Q1 | May a card send a drafted reply, or move a meeting with attendees, in one click (class B, D-MD-1)? | No. Class B opens the owning app, filled in. MD-8 waits |
| Q2 | Who pays for the AI? | **Answered by the owner, 2026-10-13.** The customer pays for every AI call in AI credits, `decide` and text alike. The feed with no AI is free (§9.3) |
| Q3 | Does external calendar sync move up, so card A can ship? | **Answered by the owner, 2026-10-13.** Metorite will read Google and Outlook calendars. Clashes fold in then (MD-11). Until then, the feed ships the overlaps it can see |
| Q4 | Are nudges outside the app opt-in, with three a day at most? | Yes, opt-in, and off for everyone until MD-10 |
| Q5 | The Projects report template T2 is also called "My day". Rename it? | Rename T2 to "My work today" in MD-4, so one name means one page |
| Q6 | Does the `decide` residency answer cover My Day? D-EM-9 approves email triage "and for no other app". My Day sends task titles, deal names and mail subjects as clipped facts. It never sends a mail body | No, until the owner says so. MD-6a builds, and every `my_day.*` mode stays `off` on a live box. Shadow sends content too |

---

## 18. Verification commands

```bash
# Frontend — the whole gate
cd workbench/control_plane && npx tsc --noEmit && npx vitest run

# The fences this spec adds, by name
npx vitest run src/lib/shell/ src/lib/theme/ src/lib/nav.test.ts

# Gateway — needs a real database (R8)
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_shell_needs.py tests/unit/test_shell_needs_r8.py tests/unit/test_shell_feed.py tests/unit/test_feed_rank.py tests/unit/test_feed_decide.py tests/unit/test_feed_compose.py -q
uv run pytest tests/unit/test_email_decide_shadow.py tests/unit/test_email_decide_questions.py -q
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py -q

# This spec's prose
node .claude/hooks/ste-lint.mjs project-docs/specs/my_day_feed.md
```

The visual check runs through the `visual-review` skill, in light mode, at
compact density, under a changed accent, and beside My Tasks.

---

## 19. Where the rest of the documentation points here

Each document below links here, and adds nothing of its own.

- `work_plan.md` §2, row **WS-57**.
- `INDEX.md`, ACTIVE.
- `navigation_shell.md` §4.3, §4.5 and §7.2: the feed's content, order and
  life live here. The page frame, the bell and the flags stay there.
