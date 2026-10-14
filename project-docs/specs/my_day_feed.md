# My Day as a live feed — the chief of staff

**Status: ACTIVE, 2026-10-14. MD-1a is BUILT (gateway half, merged as
#964, dark by the absence of a caller). MD-1b is BUILT (the frontend half,
dark behind `NEXT_PUBLIC_MY_DAY_FEED`). MD-2d is BUILT (the feed preferences,
migration 259, dark by the absence of a caller). MD-2a is BUILT (the card
state on the server, migration 258). MD-2b is BUILT (the card menu and the
seen observer, dark behind `NEXT_PUBLIC_MY_DAY_FEED`).**

**MD-2c is BUILT, 2026-10-14.** The server half puts Handled in
`GET /shell/feed`, with no migration. The client half draws the Handled
group and the honest summary, dark behind `NEXT_PUBLIC_MY_DAY_FEED`. So MD-2
is complete.

The rest of this file is not built. The open AGENT-SAFE tickets are MD-3 to
MD-5, MD-6a to MD-6c (the build only), MD-7, MD-8, MD-9 and MD-12. Each
ships dark.

**Revised 2026-10-13, same day, after the owner's answers.** The owner
answered Q2 and Q3 (§17.2). The customer pays for every AI call with AI
credits. External calendars come later, and calendar clashes fold in then.

The owner also asked the feed to use `decide` as much as it can. So §10 is
now "Decide first".

**The owner answered Q1 and Q6 the same day.** D100: `decide` may serve every
app, so residency is no longer a gate for My Day. D101: a card acts in place,
unless the act needs more context. §11 is the design of when to do which.

**Repair round 1, 2026-10-13.** The adversarial review of PR #939 found 14 P1
and 5 P2 defects. This version fixes each one. These are the largest
changes:

- The read cache knows the next time edge (§8.1).
- The loop builds the member's real access (§8.3).
- Handled reads app history, and never infers it (§6.7).
- One entity gives one card (§4.5).
- My Day adds no commitment detector of its own (§5.3).

**Verified against code on 2026-10-14** at `origin/main` `b0a45a34e`. Each
anchor carries a file and a line. Check them again at dispatch, because this
tree moves every day.

**Board row:** **WS-58**, minted by this spec (`work_plan.md` §2).
**Owning spec:** this file. **Owner:** vjvarada.
**Design mockup:** `mockups/my_day_feed.html` (§14.4).

**Owner directive, 2026-10-13.** The owner wants My Day to stop being a set of
static views of email and the calendar. It must change with "the context of my
current system". The owner asked for a live feed that acts as a chief of
staff. It must notice, prepare and nudge, and it must not cost too much to
run. The owner gave four reference cards, which §1.1 describes.

**Single owner, and the line with WS-44.** `navigation_shell.md` (WS-44) keeps
owning the page frame of My Day, the three altitudes and the bell. It also
keeps the needs contract of its §7.2, the manifest and the shell flags.

This file owns what
goes **into** the feed. That is the signals, the card shapes, the order and
the life of a card. It is also the AI that judges a card, and its cost.
`navigation_shell.md` §4.3 and §7.2 link here, and add nothing.

---

## 0. The answer in one screen

My Day becomes one ranked column of cards that a member can scroll. Each card
says what needs the member, why now, and offers the next act.

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
   - **Compositions** are the few words that only a text model can write:
     the gist of a long thread. A `decide` question gates each one, so a
     composition runs only when it saves the member time.
2. **One feed, not two.** The feed extends the provider registry of
   `GET /shell/needs`. The needs view keeps its five kinds, so the bell and
   the sidebar counts do not change (§4.4).
3. **One entity, one card.** A thread, a task or a deal gets one card. Its
   other signals become facts, a body or an act on that card (§4.5).
4. **A card has a life.** It is new, seen, later, acted, snoozed, dismissed
   or expired. Fixed rules, in a fixed order, bring a card back when it
   matters again (§6).
5. **Decide picks, templates speak, and code acts.** A `decide` answer is a
   key from options that code built. So no model output can invent a target,
   a time, a recipient or a link (§10.2).
6. **The feed ends.** Below the last open card, a "Handled" group lists what
   the apps record as closed today. Then the feed says "You are caught up".
7. **Cost stays small by design.** No model runs in the read path. Each
   answer is cached by content. A dormant member costs nothing. `decide`
   costs USD 0.042 for each million input tokens, and its output is free. §9
   estimates about 40 calls for each active member each working day. The
   customer pays in AI credits.

---

## 1. What the owner asked for

### 1.1 The four reference cards

The owner shared four screenshots of a feed. Each card has the same anatomy,
and this spec takes that anatomy as the card contract (§4.2).

| Card | Source and state | Headline | Why line | Body | Acts |
|---|---|---|---|---|---|
| A | Calendar · Needs you | "Your 2:00 PM with Priya overlaps the Contoso visit" | "Contoso moved their site visit to 1:30 PM. Priya is free Thursday at 2:00." | A two-day strip that shows the overlap and the proposed slot | "Move to Thu 2:00 PM", "See options" |
| B | Email · Reply drafted | "Dana Liu is waiting on the Q3 proposal" | "She asked yesterday at 4:12 PM. You said you would send it by Friday." | The full draft, the recipient and the attachment | "Send", "Edit draft" |
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
- The card contract, the feed groups and the one-card rule (§4.2 to §4.5).
- The life of a card, and the rules that bring a card back (§6).
- The order of the feed (§7).
- When work runs, and what it costs (§8, §9).
- The `decide` questions, the state they read, and the rules that keep them
  safe (§10).
- The acts a card may run in place (§11).
- The deep links each card needs (§12).
- Two new tables, one new column, and the cache keys (§13).
- The frontend shape of the feed (§14).

### 2.2 Non-goals — named so nobody builds them from this file

- **No second task store.** The feed reads the one lens (D53).
- **No second feed.** `GET /shell/needs` stays, as the needs view of the
  same providers (§4.4).
- **No second ritual.** Calendar owns `StartupRitual` and `EndOfDayReview`.
  The feed links to them and never copies them (`navigation_shell.md` §4.3).
- **No second planner.** "Plan my day" stays in Calendar. A feed card may
  offer it as a link.
- **No second drafter.** A draft reply comes from the email app's own drafts
  (`email_ai_drafts`). My Day never drafts a reply of its own.
- **No second commitment detector.** A promise in sent mail comes from the
  email app's own detector (§5.3).
- **No second arithmetic.** A report card draws a number that a report or an
  analytics read already computes (`navigation_shell.md` §4.5).
- **No external calendar sync.** Card A needs it, and calendar P4 owns it
  (`calendar_timeboxing.md` §8). §5.1 says what the feed can do without it.
- **No card for a `preview` app.** A card from Notes, for example, waits
  until the owner promotes the app (H-21, `navigation_shell.md` §4.5).
- **No model picker, and no price on a card** (D32.7, D88).
- **No team feed in phase 1.** The team altitude follows NS-5 (MD-12).

---

## 3. What exists today — measured 2026-10-13

| Part | Today | Anchor |
|---|---|---|
| The page | My Day at `/`, ON in production since 2026-10-10. A greeting, one summary line, and three cards: Needs you, Today, Next actions | `workbench/control_plane/src/lib/shell/MyDayPage.tsx` · `src/app/page.tsx:20-33` · flag `myDayFlag.ts` |
| The page rules | `greetingLine`, `summaryLine`, `KIND_LABELS`, `NEEDS_SHOWN = 7`, `cardsFor` | `src/lib/shell/myDay.ts` (`KIND_LABELS` at `:94`) |
| The card frame | `HomeCard`: title, icon, source, link, footer. `CardError` with Retry | `src/lib/shell/HomeCard.tsx` |
| The feed | `GET /shell/needs`. Four providers run in turn: `tasks`, `projects`, `email`, `approvals`. 2 s for each provider, 4 s in all, 15 rows for each app. **No model** | `apps/services/gateway/gateway/routes/shell/needs.py:136`, `:456`, `:482` |
| The five needs kinds | `KIND_ORDER`: overdue, approval, due_today, notification, needs_reply. The sort indexes it, so an unknown kind raises | `needs.py:159`, `:474` |
| The item shape | `{id, app, kind, title, detail, href, at, act, act_ref}`, plus `sources`, `totals` and `capped` | `navigation_shell.md` §7.2 |
| The acts | `done` and `read` only, through the owning app's code. An email row and an approval row have no act. Mark read has no Undo | `src/lib/shell/needs.ts` · `app/tasks/lib/completeFromHome.ts` |
| The poll | One shared 60 s poll. Its tick calls the **first** poller only | `src/lib/shell/ShellBell.tsx:239` |
| Act marks | A client store that hides a row for 30 s after an act. Nothing on the server | `src/lib/shell/needsActs.ts` |
| Layout preferences | `user_settings.shell_prefs`. `PUT /auth/me/shell` writes the whole blob, and the model refuses an unknown key | `infra/postgres/240_user_settings_shell_prefs.sql:29` · `routes/admin/me.py:329`, `:372`, `:459` |
| The member's day | `day_start_hour` and `day_end_hour` | `infra/postgres/77_gtd_calendar_prefs.sql:18` |
| Calendar events | **Only task time blocks.** External sync answers 501 | `routes/tasks/calendar.py:53-75` |
| Clash detection | Visual only, in the grid. No server read | `app/calendar/components/shared.ts:218-220` |
| Work conflicts | Seven kinds. The HR kinds, `absent_on_due` among them, need `admin:members:read` | `routes/projects/analytics_conflicts.py` · `gateway/conflicts.py:46-48` |
| My own absences | No HR gate | `routes/people/selfservice.py:272` |
| Who may read HR fields | `can_read_hr_fields` | `routes/tasks/core.py:75` |
| Needs reply | `needs_reply_threads`, and the `_LIVE_THREAD` rule it reads | `routes/email/digest.py:238`, `:137` |
| Promises in sent mail | The email app detects a promise and files it as a task. The digest reads the open ones | `routes/tasks/capture_email.py:1007`, `:1176` · `routes/email/digest.py:293` |
| Drafts | One draft for each thread. Drafting is OFF by default (EM-T7) | `email_ai_drafts` · `routes/email/automation/drafting.py` |
| A cached AI line | The email morning brief. `tier-fast`, a Redis key of mailbox, day and sha256 of model, prompt and input, 26 h TTL | `routes/email/digest.py:542-621` |
| `decide` | ON in production since 2026-10-02. Email asks it for the rule match and the thread status | HANDOFF H-166 · `packages/acb_llm/acb_llm/decide.py` |
| The `decide` mode registry | `off`, `shadow`, `on` for each feature. `on` only for a feature in `ON_FEATURES`. `shadow` logs one comparison for each call | `apps/services/gateway/gateway/decide_features.py:105`, `:122`, `:716-801` |
| Background loops | `asyncio` loops in the gateway lifespan. Each loop holds its own kill switch | `gateway/main.py:40-48` |
| The best loop to copy | CRM mirror sync: a 60 s tick, `is_due`, three at once, a run lock in a table | `gateway/crm_sync/scheduler.py:49`, `:71`, `:104` |
| A member's access outside a request | `member_context(email)` resolves the same context a signed-in request gets. The WhatsApp channel calls it, and refuses a context in another org | `packages/acb_auth/acb_auth/deps.py:388` · `routes/whatsapp_channel/views.py:142-155` |
| Change events | An in-process bus. Projects emits `pm.*` and awaits it inline, and two sinks listen | `apps/services/ingestion/ingestion/event_hooks.py:32`, `:43` · `gateway/main.py:1612-1621` |
| A job that calls a model as a member | `as_mailbox_owner` binds the member, so the Router bills the right one | `routes/email/scheduler_hooks.py:53` |
| Out-of-app delivery | WhatsApp templates are spec only (WAC-7). Web push is spec only (D-2) | `whatsapp_assistant_channel.md` §5.8 · `chat_run_continuity.md` |
| Deep links | Task, project, report, email and CRM have one. **Calendar has none.** Approvals opens the queue only | §12 |

**The gap in one line.** The feed already knows what is due. It does not know
why it matters, it cannot prepare the next step, and it forgets a row 30
seconds after an act.

---

## 4. The model — signals, cards and the feed

```
 apps                     signals (free)          cards (free)               feed (per member)
 ─────────────────        ──────────────          ────────────               ─────────────────
 My Tasks  ──provider──▶  overdue, due_today  ─┐
 Calendar  ──provider──▶  overlap, overbooked ─┤   one card per entity     group + weight (decide, §10)
 Email     ──provider──▶  needs_reply, quiet  ─┼─▶ template words     ─▶   + urgency (code, §7)  ─▶ GET /shell/feed
 Reports   ──provider──▶  run_ready, moved    ─┤   small view              + state (§6)
 CRM       ──provider──▶  deal_quiet, due     ─┤   acts (code)
 Approvals ──provider──▶  approval            ─┘                           needs view, 5 kinds ─▶ GET /shell/needs
```

### 4.1 A signal

A signal is one fact about one entity that a provider found with a bounded
query. It holds no prose that a model wrote.

| Field | Meaning |
|---|---|
| `signal_id` | **The identity of the entity, never of the kind.** `<app>:<entity ref>`, the grammar of today's needs ids: `tasks:<task id>`, `email:<account id>:<thread id>`, `approvals:<id>`. A reader splits on the first colon only. So a task that moves from due today to overdue keeps its id, and keeps its snooze. See the three rules below the table |
| `app`, `kind` | The source app and the kind from §5. The kind is a field, and it may change |
| `entity` | `{type, id, label}`. The thing the card is about |
| `facts` | A small dict of typed values, for example `{due_at, priority, waited_hours}`. The templates and the `decide` state read only this |
| `fingerprint` | A short hash of the facts that matter. A change in it means the signal changed in substance (§6.3) |
| `urgent_at` | The time after which the signal is urgent, or null |
| `expires_at` | The time after which the signal is no longer true, or null. A meeting that ended expires |
| `href` | The deep link (§12). Code builds it, never a model |
| `acts` | The acts code offers, each with its class from §11 |

**Three identity rules.**

- **A block belongs to its task.** A calendar block is the scheduled time of
  a task, so `block_soon` and `block_overlap` key on `tasks:<task id>`.
- **A signal about a whole day keys on the day**: `calendar:<local date>`.
  `day_overbooked` and `no_plan_yet` are such signals.
- **In the feed, a Projects notification keys on its task**,
  `tasks:<task id>`, so it folds into the task's one card. Its `read` act
  still carries the notification id. The needs view keeps its own id,
  `projects:<notification id>` (§4.4).

**A provider follows the rules of `navigation_shell.md` §5.2.** It runs on
the session at the `get_db()` seam. It calls its app's own authorized read
with the member's `UserContext`, and it never writes a visibility rule of its
own. It gives 15 signals at most, and it says when it stopped at the cap.

### 4.2 A card

A card is one entity drawn for the member. The four reference cards set its
anatomy:

1. **The source line.** The app's name, a dot, and the state of the card, for
   example "Email · Reply drafted". The time at the right.
2. **The headline.** One sentence that states the fact about a person or a
   thing. It never states a feeling.
3. **The why line.** One sentence on why it is here now, after a light-bulb
   icon. A template writes it from `facts`. `decide` may pick which template
   (§10.3).
4. **The body.** A small view that the source app exports: a day strip, a
   draft, a number with bars, task rows. A card may have no body.
5. **The acts.** One main act, filled, and one second act, outlined. Each
   names its effect in words, for example "Move to Thu 2:00 PM".
6. **The menu.** Snooze, Dismiss, "Not useful", and "Why am I seeing this".
7. **The door.** A click on the headline opens the entity in its app.

The frame is `HomeCard` (`src/lib/shell/HomeCard.tsx`), widened by a
`FeedCard` that adds the source line, the why line and the menu. The body is
the app's component, found by kind (§14.2). The design rules of
`DESIGN_SYSTEM.md` bind every part. The source line uses the app's icon
through `<Icon name>`, never an app-local colour.

### 4.3 The feed groups

The feed is one column. Groups give it a shape, so a member knows where they
are when they scroll.

| Group | What goes in it | Shown |
|---|---|---|
| **Now** | Due within 2 hours, a block that starts within 30 minutes, an overlap today | Always open. Hidden when empty |
| **Needs you** | Decisions and replies: overdue, approvals, needs reply, an overlap later this week | Always open |
| **Ready for you** | Work that something prepared: a draft, a plan proposal, a report run, a free slot for an overdue task | Open |
| **For your information** | Facts with no act due: a report moved, a deal changed stage | Open, at most 10, then "Show more" |
| **Later** | Cards in the state `later` (§6.5) | Folded |
| **Handled** | What the apps record as closed today (§6.7) | Folded. Its count feeds the summary line |

Then the feed ends with "You are caught up". The feed never fills itself with
filler to keep a member scrolling.

**The old-path group of each MD-1 kind.** With no `decide` answer, this table
sets the group. A ticket that adds a kind adds its row in the same PR.

| Kind | Old-path group |
|---|---|
| `overdue` | Needs you |
| `due_today` | Now when it is due within 2 hours, else Needs you |
| `notification` | Needs you for `assigned`, `mention` and `nudge`. For your information for `comment` |
| `approval` | Needs you |
| `needs_reply` | Needs you |
| `block_soon` | Now |
| `block_overlap` | Now when the overlap is today, else Needs you |
| `day_overbooked` | Needs you when the day is today, else For your information |
| `no_plan_yet` | Needs you |

### 4.4 One registry, and the needs view stays five kinds

The needs feed of `navigation_shell.md` §7.2 stays the contract for the bell
and the sidebar counts.

- **One registry.** A provider registers once, and serves the feed. The four
  providers of today serve the needs view too.
- **The needs view keeps its five kinds**: overdue, approval, due_today,
  notification and needs_reply (`KIND_ORDER`, `needs.py:159`). Its shape,
  order and caps do not change. Nothing in the bell changes.
- **A new kind joins the needs view only by an amendment** of
  `navigation_shell.md` §7.2. The ticket that adds the kind names the
  amendment, and adds the kind to `KIND_ORDER`, to `NeedsKind` and to
  `KIND_LABELS` in one PR. Until then, a kind such as `crm_followup_due` or
  `agent_asks` shows in the feed only.
- **`GET /shell/feed`** returns every card, with its group, its state and the
  needs view of the same provider run (§14.1).

**Fence (MD-1).** `tests/unit/test_shell_feed.py::TestOneRegistry` fails if a
provider registers twice. It fails if the needs view of a fixed seed differs
from today's `GET /shell/needs` answer. It also fails if a kind outside
`KIND_ORDER` reaches `GET /shell/needs`.

### 4.5 One entity, one card

`navigation_shell.md` §4.5 holds the rule for the needs card: "One thread in
two cards teaches the eye to skip both cards." The feed keeps it for every
entity.

- **The entity key is the `signal_id`.** One key gives one card.
- **The main kind wins.** When one entity gives several kinds, the kind with
  the highest `base(kind)` (§7) is the card's kind.
- **Every other kind becomes part of that card.** It is a fact in the why
  line, a body, or an act. Three examples:
  - A thread with a draft and many messages is one `reply_drafted` card. The
    gist of the thread is part of its body.
  - An overdue task with a free slot today is one `overdue` card. "Block
    11:00" is one of its acts.
  - An overdue task that falls on my absence is one `overdue` card. The
    absence is a fact in its why line.
  - The card takes the most urgent group of all its kinds, so a folded block
    that starts soon keeps the card in Now. The order is Now, Needs you,
    Ready for you, then For your information. Fence:
    `test_shell_feed.py::TestTheMostUrgentGroup`.
- **The NS-3 rule stays.** Next actions leaves out each task that a feed card
  already shows (`myDay.test.ts`).

**Fence (MD-1).** `tests/unit/test_shell_feed.py::TestOneCardPerEntity`
seeds one task that gives three kinds. It asserts one card, with the other
two kinds folded into it.

---

## 5. The card catalogue

Each row is one signal kind. **Phase** says when it can ship. "Template"
means a fixed sentence with values from `facts`. **AI** names the model work
that the kind needs beyond the common pass.

**The common pass applies to every card.** `my_day.triage` and
`my_day.weight` judge each new card. `my_day.reason` and `my_day.next_act`
judge the cards near the top (§10.3). So a row that says "None" still gets
judged. It needs no detector and no text.

**Acts marked A\* are class A acts beyond done, mark read and snooze.** D101
lets them run in the card as a tap (§11). MD-8 builds them. Until MD-8 lands,
each renders as a link that opens the owning app at the right place.

### 5.1 Calendar

| Kind | Signal | Why line | Body | Acts | AI | Phase |
|---|---|---|---|---|---|---|
| `block_overlap` | Two of my scheduled blocks overlap today or this week | Template: "They overlap from 14:00 on Tuesday." | A day strip of the two blocks | "Move X to <slot>" (A\*), "Open the day" | `my_day.slot` picks the slot | MD-1 |
| `day_overbooked` | Planned hours exceed the working hours of the day | Template: "7 h planned in a 5 h day." | A bar of planned against free time | "Fit what's left" (opens Calendar's replan) | None | MD-1 |
| `no_plan_yet` | It is past the day start and nothing is scheduled today | Template | — | "Plan my day" (link to Calendar) | None | MD-1 |
| `free_slot_for_overdue` | A free slot of 30 minutes or more today, and an overdue task that fits it | Template: "You have 90 min free at 15:00." | The slot and the task | "Block it" (A\*) | `my_day.slot` | MD-4. When the task has its own card, this folds into it (§4.5) |
| `block_soon` | A block starts within 30 minutes | Template | The block and its task | "Open task" | None | MD-1 |
| `meeting_overlap` | A meeting overlaps a meeting or a block (card A) | Template from the two events | The two-day strip of card A | "Move to <slot>" (class B), "See options" | `my_day.slot` | **MD-11, waits on calendar P4** |

**The reads and the gates of the Calendar kinds (MD-1a).**

- **Two gates.** The Calendar pane needs `feature:tasks`, and the lens read
  of blocks needs `feature:projects`. The provider checks both. A member who
  lacks either gets no Calendar signal, and the source reads `absent`.
- **Today comes from the Calendar app's own read**, `day_summary`
  (`routes/tasks/calendar.py:1388`). It gives `scheduled` and
  `capacity_mins`. `block_soon`, `day_overbooked` and `no_plan_yet` read it.
- **`day_overbooked` uses the planner's own budget.** It compares the planned
  minutes of today's open blocks with `capacity_mins`. That is the number
  Calendar plans against, so it is no second arithmetic.
- **`no_plan_yet`** fires on a working day when `scheduled` holds no open
  block, and the local time is 60 minutes past `day_start_hour`. The hour
  comes from the Calendar app's settings read, `get_user_settings`
  (`routes/tasks/settings.py:393`).
- **`block_overlap` reads the next 7 days** from the lens, `my_calendar`
  (`routes/projects/personal.py:2028`). That read is not bounded, so the
  provider stops at 15 signals and sets `capped`.
- **One overlap, one card.** An overlap of two blocks goes on the card of the
  task whose block starts first. The other task is a fact on it, "overlaps
  <title>". When the other task has a card of its own, that card carries the
  same fact.
- **Until MD-8**, the overlap card's act is "Open the day", a link to
  `/calendar`. A slot act waits for MD-8, and a link to the day waits for
  MD-3.
- **A provider opens no session of its own.** It calls the app functions,
  which open theirs, as `my_due_tasks` does (R5b).

⚠️ **Card A needs external calendar events, and Metorite has none today.**
`POST /calendar/sync` answers 501 (`routes/tasks/calendar.py:62-75`). Until
calendar sync lands, the feed sees an overlap of the member's own blocks
only. The owner confirmed on 2026-10-13 that Metorite will read Google and
Outlook calendars, and clashes fold in then (§17 Q3).

**Meeting prep waits on Notes.** A prep card for a Notes meeting needs the
Notes app, which is `preview` (`src/lib/nav.ts`, WS-19). It joins the
catalogue in the PR that promotes Notes (H-21). Its link is
`/notes/meeting/<id>` (`app/notes/meeting/[id]/page.tsx`).

### 5.2 My Tasks and Projects

| Kind | Signal | Why line | Acts | AI | Phase |
|---|---|---|---|---|---|
| `overdue` | Today's provider | Template: "Due Monday. 2 days late." | Done (A), Snooze (A), "Move to tomorrow" (A\*) | None | MD-1 |
| `due_today` | Today's provider | Template | Done (A), "Block time" (A\*) | None | MD-1 |
| `notification` | Today's provider: assigned, mention, comment, nudge. `assigned` is "a colleague gave me a task" | Template from the actor and the excerpt, for example "Ravi gave you this task today." | Mark read (A), Open | None | MD-1 |
| `unblocked` | A task I own was blocked, and its blocker closed | Template: "Ravi closed the API task you waited on." | Open | None | MD-3 |
| `waiting_quiet` | A Waiting task with no activity for 5 working days | Template: "You have waited 6 days on Ravi." | "Nudge Ravi" (B), Snooze | None | MD-3 |
| `stale_next` | A Next task with no change for 14 days | Template | "Move to Someday" (A\*), Open | None | MD-3 |
| `due_at_risk` | The outlook read says a deadline will slip at the current load | Template from the outlook facts | Open, "Ask to move the date" (B) | None | MD-3 |
| `absent_on_due` | A due date of my task falls on a day **I** am absent | Template: "Due Friday. You are on leave that day." | "Move to <day>" (A\*) | None | MD-3. It reads **my own** absences, `GET /people/me/absences` (`routes/people/selfservice.py:272`), never the HR conflicts read |

### 5.3 Email

| Kind | Signal | Why line | Body | Acts | AI | Phase |
|---|---|---|---|---|---|---|
| `needs_reply` | Today's provider | Template: "Asked yesterday at 4:12 PM." | The last message, 3 lines | Open thread, Snooze | None | MD-1 |
| `reply_drafted` | `needs_reply` and a draft in `email_ai_drafts` (card B) | Template, plus the promise when the email app holds one for the thread | The full draft and its attachments. For a thread of 6 messages or more, the gist above it | "Review and send" (B), "Edit draft" | The draft is the email app's. The gist is a text composition (MD-6c) | MD-4 |
| `promise_due` | A promise I made in sent mail falls due (card B's "You said you would send it by Friday") | Template from the promise | — | Done (A), Open | **None in My Day.** The email app's own detector found the promise and filed it as a task (`capture_email.py:1007`, `:1176`). The provider reads the open ones through `_digest_commitments` (`digest.py:293`) | MD-3 |
| `no_answer` | A mail I sent has waited 3 working days for the other side | Template | — | "Follow up" (opens a draft), Snooze | None. It reads the email app's AWAITING threads (`digest.py`, the read above `:293`) | MD-3 |

**The gist of a long thread is not a card.** It is part of the body of the
thread's one card (§4.5).

### 5.4 Reports and analytics

| Kind | Signal | Why line | Body | Acts | AI | Phase |
|---|---|---|---|---|---|---|
| `report_ready` | A report I receive has a new run (card C) | Template: names the meeting on my calendar that the report feeds, when one exists | One `Stat` tile and its bars, from the report's own panel | "Open report", "Share with team" (B) | The report's own AI summary, when one exists. My Day writes none | MD-4 |
| `metric_moved` | A number in a report I receive moved more than its threshold | Template: "Throughput fell 30% this week." | The tile | Open report | None | MD-9 |
| `team_pulse` | (lead only) The morning Team pulse | Template from `pulse` | The pulse panel | Open report | None | MD-12 |

**The number on a report card is the report's number.** The card renders the
report's own panel through `Stat` in `AnalyticsPanels.tsx`. It computes
nothing (`navigation_shell.md` §4.5, "No second arithmetic").

### 5.5 CRM

| Kind | Signal | Why line | Acts | AI | Phase |
|---|---|---|---|---|---|
| `crm_followup_due` | An activity I own has `due_at` today or earlier | Template | Open deal | None | MD-3. It needs a new "my due activities" read in `routes/crm/activities.py`. Feed only, until a §7.2 amendment |
| `deal_quiet` | A deal I own passed its stage's `max_dwell_days` | Template: "No activity for 12 days in Proposal." | "Log a call" (opens the CRM form), Snooze | None | MD-3 |
| `deal_close_passed` | A deal I own passed its close date and is open | Template | "Move the date" (opens the CRM form) | None | MD-3 |
| `lead_replied` | A lead I own replied by mail | Template | Open thread | None | MD-9 |

### 5.6 People, approvals, agents and WhatsApp

| Kind | Signal | Why line | Acts | AI | Phase |
|---|---|---|---|---|---|
| `approval` | Today's provider, org admin only | Template from the action name | Open Approvals | None | MD-1 |
| `colleague_out` | A person I have a block with today is absent | Template | Open the day | None | MD-3, **only for a member who passes `can_read_hr_fields`** (`routes/tasks/core.py:75`). The provider calls the People app's own gated read. A plain member gets no row, and a negative test proves it |
| `agent_done` | A chat run that I started finished while I was away | Template from the run | Open chat | None | MD-3. It reads WS-51's run state |
| `agent_asks` | A run waits for my answer (`chat_pending_ask`) | Template | Open chat | None | MD-3. Feed only, until a §7.2 amendment |
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
misses a card. This section is the answer. Every rule here is deterministic,
except `my_day.resurface`, which can only bring a card back.

### 6.1 States

| State | Meaning | Visible |
|---|---|---|
| `new` | No state row, or the fingerprint changed since the last row | Yes, with a dot |
| `seen` | The member saw it and did not act | Yes |
| `later` | The member saw it on three visits and did not act (§6.5) | In the folded Later group |
| `acted` | The member ran an act from the card | No |
| `snoozed` | Hidden until a time or a change | No |
| `dismissed` | Hidden until its fingerprint changes. `dismiss_reason` is `dismiss` or `not_useful` | No |
| `expired` | Its `expires_at` passed | No |

**A card leaves the feed when its source stops returning it.** Only a failed
source keeps its cards for a while. Decided in MD-2b fix round 1,
2026-10-14:

- **A capped source carries nothing.** Its answer is the truth for its top
  15. The rest sit behind "Show all in <app>", for example "Show all in My
  Tasks".
- **An absent source carries nothing.** The member no longer holds the app.
- **A failed source carries its cards from the last answer in which it read
  `ok`.** That is the last raw answer, and never a carried one. It carries
  for 5 minutes at most. After that the cards leave, and the caveat line of
  NS-3 stays.
- **A card that the member closed is never carried.** That is a snooze, a
  dismiss, or a tap act that takes the whole card away. Undo lets it back.

Leaving the feed never writes a state row, and it never puts a card in
Handled (§6.7).

### 6.2 Seen

- A card is **seen** when half of it is in the viewport for 1.5 s, while the
  tab is visible. The client collects the ids and sends one batch each 10 s
  and on `pagehide`.
- A card in the bell panel is not seen by this rule. The bell is a glance.
- Each seen write counts a **visit**: two sightings within 30 minutes are one
  visit. `visits` counts since the card last came back.

### 6.3 The order of the rules

The feed applies these rules in this order. The first rule that matches
decides.

1. **Snooze holds.** A `snoozed` card stays hidden until its time, or until
   its fingerprint changes for "until it changes".
2. **Dismiss holds.** A `dismissed` card stays hidden until its fingerprint
   changes.
3. **A changed fingerprint returns a card as `new`.** A new message in the
   thread, a new due date, a new run of a report. The source line says
   "Updated". `visits` goes back to 0.
4. **Urgency lifts.** A `seen` or `later` card that crosses its `urgent_at`
   goes to Now, with "Still open". The member saw a task at 9:00, and it is
   due at 15:00. It comes back at 13:00.
5. **The fixed daily return.** At the member's `day_start_hour`, some
   `later` cards return to Needs you once that day. They are each `overdue`
   card, and each `needs_reply` older than 48 h. `visits` goes back to 0. `decide` cannot stop
   this rule (§7 rule 2).
6. **The judged return.** At day start, `my_day.resurface` asks about each
   other `later` card. A yes returns it, with `visits` at 0. With no answer,
   the card stays in Later.
7. **Seen cards start the day fresh.** At day start, each `seen` card returns
   to the ranking with `visits` at 0.
8. **Three visits fold a card.** A `seen` card with 3 visits and no act
   becomes `later`.

**Snooze and dismiss beat urgency in the feed, and never in the bell.** The
member chose to hide the card. A needs kind still counts in the bell and the
sidebar, so nothing that needs the member drops away in silence.

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

### 6.5 Missed — the escalation ladder

A card that matters and stays `new` climbs one step at a time. Each step is
quieter than a red badge storm (`navigation_shell.md` §3.2c).

| Step | When | What happens | Built |
|---|---|---|---|
| 1 | Always | The card sits at the top of its group, with a dot | MD-2 |
| 2 | Always, for a needs kind | The bell badge and the sidebar row count include it | Yes, today |
| 3 | `urgent_at` passed, and the card is still `new` | The card moves to Now | MD-2 |
| 4 | Step 3 for 60 minutes, inside working hours, the member opted in, and `my_day.nudge` says yes | One message on the member's channel: WhatsApp or web push | **MD-10, owner gate** |

**The caps on step 4.** Three messages each day at most. None outside the
working hours of `work_schedule`. One message holds every card that reached
step 4 in the same hour. The channel changes the delivery and never the
function (`whatsapp_assistant_channel.md`).

### 6.6 Dismiss and "Not useful"

- **Dismiss** hides this card until its fingerprint changes.
- **Not useful** does the same, and counts against its kind for this member
  (§7). It also offers one line, "Tell My Day why" (§10.5).
- After three "Not useful" on one kind in 30 days, the menu offers "Stop
  showing <kind>". The mute goes into `user_settings.my_day_prefs` (§13.3).
- **A mute never hides a needs kind from the bell.** A mute changes the feed
  only.

### 6.7 Handled, and the honest summary

- **Handled reads app history. It never infers anything from a missing
  signal.** Each provider may give a `handled(member, since)` read, bounded at
  15 rows, through its app's own authorized read:
  - My Tasks and Projects: the member's tasks completed since the day start,
    with the actor from the task's activity.
  - Email: the rules applied today on the member's mailboxes
    (`email_executed_rules`, status `APPLIED`).
  - Approvals: the actions an admin decided today.

  The `acted` rows of `my_day_card_state` are not a source. They hold no
  title and no link (the MD-2c ticket, §16).
- **Each row names who closed it**: "You", a colleague, "Email rules", or the
  agent's name.
- **The summary line counts real rows.** "3 things need you" is the count of
  open cards in Now and Needs you. "The rest is handled" shows only when
  Handled is not empty and no Handled read failed (`handled_ok`, §14.1a).
- **A failed source silences the claim.** The rule of NS-3 stays: when a
  source failed, the summary says nothing about needs, and the card names the
  gap.

---

### 6.8 How MD-2 runs these rules (decided 2026-10-13)

The MD-2 audit found nine gaps in §6.1 to §6.7. These answers close them.
Where this section and an earlier one differ, this section wins.

**1. The state fingerprint.** The state compares a fingerprint of the
entity's own facts. It holds the entity, `due_at`, `note_ids`,
`waiting_since` and the last message id of a thread. It leaves out the kind,
the folded kinds and the block facts.

So a dismissed task stays dismissed
when a block folds in, when the block ends, and when the task turns overdue.
It comes back when its own facts change: a new due date, a new note or a new
message. The card answer carries it as `state_fp` (§14.1a).

**2. The rules run at read time.** Rules 5, 6 and 7 of §6.3 run when the
feed is read, against the member's most recent `day_start_hour`. The read
writes nothing.

To make that work, `visits` counts only the visits of one
local day, and the column `visits_day` holds that day. A row whose
`visits_day` is before today reads as 0 visits. The `my_day` loop of MD-5
changes nothing here.

**3. Rule 6 in MD-2.** `my_day.resurface` is MD-6a. Until then rule 6 has no
answer, so a `later` card that rule 5 does not return stays in Later. A test
asserts exactly this.

**4. `acted` is a record, never a hide.** A tap act that succeeds writes
`acted`, for the affinity term of §7. An `open` link writes nothing.

`acted` never hides a card. The client's own act marks hide it while the
write lands (MD-1b). So a task that someone reopens always shows again.

**5. `later` is stored, and it is sticky.** The POST that gives a card its
third visit of the day writes `later`. Rule 5 returns an overdue card, or a
`needs_reply` card older than 48 h, to Needs you for that day. The return
holds while the card's visits of that day stay under 3. Rule 8 then folds it
again.

**6. Escalation goes into the rules.** Rule 4 lifts a `new` card too, so §6.5
step 3 is rule 4. Inside a group, a `new` card sorts before a seen card, then
the score decides (§6.5 step 1). The fixed seed of `test_feed_rank.py`
changes with this rule only.

The lift instant is `urgent_at` less 2 hours. A card that the member never saw
lifts only when that instant is at or after the start of the member's current
day. An older crossing keeps its old-path group, so an overdue task from last
week stays in Needs you. A card that the member saw lifts when the instant
came after the last sighting. Decided in fix round 1 of MD-2a, 2026-10-14.

**7. Hidden cards take no slot.** The state join runs after the fold and
before the order. A snoozed, dismissed or expired card leaves the answer
before `limit`. A `later` card is in the answer, in the `later` group, and it
counts against `limit`. `PER_APP` binds the providers, before the join.

**8. The zone and the day hours.** The join reads them once for each feed
read. A member who holds `feature:tasks` gets them from `get_user_settings`
(`routes/tasks/settings.py:393`). Any other member gets the zone from the
lens (`stored_zone`, `routes/projects/personal.py`), and the column defaults
7 and 22 (`77_gtd_calendar_prefs.sql`).

**9. The needs view does not change.** The state never touches `needs`. A
dismissed or snoozed needs-kind card still counts in the bell.
`TestOneRegistry`'s equality must hold with state rows present.

#### The POST contract

`POST /shell/feed/state` takes this body, and nothing else:

```json
{"items": [{"signal_id": "tasks:9b2e…", "state_fp": "a41f09c2", "kind": "overdue",
            "action": "seen", "until": null}]}
```

- `action` is `seen`, `snooze`, `dismiss`, `not_useful`, `acted` or
  `restore`. `restore` clears a snooze or a dismiss, and leaves the row
  `seen` (MD-2b).
- `until` is `later_today`, `tomorrow`, `next_week` or `on_change`, and only
  a `snooze` carries it.
- One body holds 50 items at most. A `signal_id` matches
  `^[a-z_]+:[^\s]{1,200}$`. A `state_fp` is 8 to 64 hex characters.
- The body never names a member or an organization. The model refuses an
  unknown key with 422. The member comes from the session (R5e).
- **The server computes `snoozed_until`.** It uses the member's zone, day
  hours and working days (`work_schedule.effective_schedule`). It never takes
  a time from the client.
  - Later today: 3 hours from now, or the day end, whichever comes first. At
    or after the day end, it is the next working day's start.
  - Tomorrow: the next working day's `day_start_hour`.
  - Next week: the first working day of the next ISO week, at the day start.
  - On change: no time. The card returns when its `state_fp` changes.
- **The server applies the visit rule.** Two `seen` items for one card within
  30 minutes count as one visit.
- The answer is `{"written": n}`. A bad body gets 422, with no write.

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
| `weight` | **`decide`**, `my_day.weight` (§10.3) | The level from 0 to 4, times 6. With no answer, or an answer under its gate, the old path sets it: the task's priority from the D78 matrix, +15 for a CRM customer, +10 for a promise |
| `affinity` | Code | From the member's own acts on this kind over 30 days: `(acted − not_useful) / shown × 20`, between −20 and +20. It is computed once each day, at day start |
| `fatigue` | Code | −8 for each visit with no act, to −24 at most |

**The group comes from `decide` too.** `my_day.triage` (§10.3) puts each card
in Now, Needs you, Ready for you, For your information, or Later. With no
answer, the group table of §4.3 decides.

Four rules sit on top of the score:

1. **The group comes first.** The score orders cards inside a group.
2. **`decide` may demote a needs card, and it may never hide one.** An
   overdue task, an approval or a needs-reply thread goes at worst to Later.
   §6.3 rule 5 still brings it back each day. Only a card that is not a
   needs kind may leave the feed on a `drop` answer.
3. **Today's caps stay.** 15 signals for each app at most. An approval takes
   only the room that the member's own work leaves (`YIELDING_KINDS`,
   `needs.py:175`).
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
| **A read** | The providers, the one-card fold, the rank and the state join. Bounded SQL. The read uses the cached `decide` answers, and never waits for a new one | No model. At most one provider run for each time edge or change (§8.1) | MD-1, MD-5 |
| **A change** | An event marks the member's feed **dirty**: a `pm.*` event, an email sync cycle, a report run, a chat run end | One Redis write. No model, and no database query | MD-5 |
| **A clock** | One gateway loop, `my_day`, with a 60 s tick. It asks `decide` about new or changed cards, makes the morning pass, handles the day start rules of §6.3, and sweeps old rows | `decide` calls for active members only, in batches (§9.1 rule 6) | MD-5, MD-6a |

### 8.1 The read cache, and the next time edge

Some cards come from the clock, not from an event: `block_soon`, the 2-hour
edge of Now, `no_plan_yet`, `day_end` and every `expires_at`. So the cache
must know when the clock will change the answer.

- **Each answer carries its next time edge.** The server takes the earliest
  of these times, over the member's signals:
  - each block start, minus 30 minutes
  - each `urgent_at` and each `expires_at`
  - each due time, minus 2 hours
  - the next `day_start_hour`, and `day_end_hour` minus 60 minutes
- **An answer stays valid until the first of three things:** the feed version
  changes, the next time edge passes, or 10 minutes pass. The 10 minutes
  bound a missed event.
- **The key holds the member**, because two members hold different grants.
  It goes through the tenant-prefix wrapper, `acb_common.tenant_redis` (R5c).
- **The client sends an ETag** of the version and the edge. A valid answer
  with the same ETag answers 304.

So a quiet member's poll costs one Redis read until the next time edge. Then
one provider run makes the time-derived cards appear on time.

### 8.2 The change sink

The gateway registers a third sink on the in-process bus, beside the two at
`gateway/main.py:1612-1621`.

- **It reads the members from the event's payload only**, for example the
  assignee and the actor of a `pm.*` event. It bumps their feed version with
  one Redis increment.
- **It never queries the database.** Projects awaits `emit` inline in each
  write, so a query here would slow every task edit. An event that names no
  member bumps nothing, and §8.1's 10 minutes bound the gap.
- The email app bumps the version from its post-sync hooks
  (`routes/email/scheduler_hooks.py`).

⚠️ **The bus is in-process.** An event emitted in one gateway process does not
reach another. Today the gateway runs one process. A second process would
need Redis pub-sub through `TenantRedis.publish`. MD-5 names this as a limit.

### 8.3 The loop, and whose access it uses

The `my_day` loop copies the CRM mirror loop, `gateway/crm_sync/scheduler.py`:
a 60 s tick, an `is_due(member)` test, and three members at once at most. The
kill switch lives inside the start function (`gateway/main.py:40-48`).

**Which members.** Each feed read adds the signed-in member to a sorted set
of the organization, `my_day:active`, with the time as its score. The key
goes through the tenant-prefix wrapper. The loop reads members from that set
only. So each email it holds came from a session identity, never from
request input. A member with no read in 3 days is not active, and costs no
model call.

**Whose access.** The loop has no request. For each member it:

1. Binds the organization with `tenant_session`. The organization list comes
   from the RLS-exempt table, a tenant-discovery site registered in
   `tests/unit/test_db_engine_seam.py`.
2. Builds the member's context with `acb_auth.deps.member_context(email)`
   (`deps.py:388`). That is the request path's own resolve, so the features,
   the admin test and the visibility match a web request.
3. Refuses a context whose organization is not the bound one, the check of
   `routes/whatsapp_channel/views.py:142-155`. A member who left the
   organization is skipped, and the loop removes them from the set.
4. Runs the providers with that context, and judges what is new.

**Fence (MD-5).** `tests/unit/test_my_day_loop.py` holds four cases. A member
whose context resolves to another org is skipped. A member without
`feature:email` gets no email signal from the loop. The loop never reads a
member from anywhere but the active set. And the active set is written only
from the session's identity.

**When a member is due.**

- Their feed is dirty, and the last pass was 2 minutes ago or more. So a
  burst of 20 task edits makes one pass, not 20.
- 15 minutes before `day_start_hour`: **the morning pass**. So the first visit
  of the day already has the judged order, and the order does not jump under
  the member's eyes.

---

## 9. Cost — the rules that keep it cheap

### 9.1 Ten rules

1. **No model in the read path.** A feed read never waits for a model. A card
   with no answer yet shows its old-path group, weight and template. The
   judged values fill in on a later read.
2. **`decide` first, text last.** Every judgment is a typed question (§10.3).
   A text model runs only for the gist of a long thread, and only after
   `my_day.compose` says yes.
3. **Reuse what an app already paid for.** Email's own `decide` answers say
   which thread needs a reply (`email.thread_status`). The draft reply and
   the promise are the email app's. A report sentence is the report's own AI
   summary. My Day asks only what no app has asked.
4. **Once for each fingerprint.** The loop caches each answer. The key holds
   the member, the signal and the card's fingerprint. It also holds the
   context hash of §10.4, and the question version.
5. **Only what changed.** A pass asks about the cards that are new, or whose
   fingerprint changed. A pass with nothing new makes no call. A change of
   the context hash judges the open cards again, at most once each hour.
6. **Sixteen questions in one request.** One request carries up to 16
   questions over one shared state (`MAX_QUESTIONS`,
   `customer_console/decide.py:52`). The charge for each call has a floor
   (H-42), so the loop packs questions and counts calls, not only tokens.
7. **One round for each pass.** The cards near the top get their reason and
   next-act questions in the same round as triage and weight. "Near the top"
   means the top 6 by the old path, so the pass needs no second round. A card
   that the judged order lifts into the top 6 gets them on the next pass.
8. **A cap for each member and each organization.** 60 `decide` calls and
   10 compositions for each member each day. The operator sets a ceiling for
   each organization. Past a cap, cards keep their old-path values. The
   counters are Redis keys through the tenant wrapper:
   `my_day:calls:<member>:<local date>` and `my_day:calls:org:<date>`, with a
   26 h TTL. `gateway/routes/shell/feed_decide.py` checks them before each
   request. Fence: `tests/unit/test_feed_decide.py::TestCaps`.
9. **The balance gate stops it, and the feed keeps working.** A 402 or a 403
   starts the 15-minute cool-down of `decide_features.py`
   (`REFUSAL_COOLDOWN_S`). The feed then runs on the old path. That is D88's
   rule for the command bar.
10. **Off is one switch for each feature.** Each `my_day.*` feature has its
    own mode in `DECIDE_FEATURE_MODES`. `MY_DAY_COMPOSE` stops every text
    composition. An operator can also unbind a task in the Router (H-178).

### 9.2 The estimate

For one active member on one working day, with about 30 open cards:

| Pass | Questions | Requests | Input tokens each | Input tokens |
|---|---|---|---|---|
| Morning: triage and weight for 30 cards (60), reason and next act for the top 6 (12), slot for 2 (2) | 74 | 5 | 6,000 | 30,000 |
| On a change, 10 each day: about 8 new cards (16), and about 2 of them near the top (4) | 20 | 2 | 5,000 | 100,000 (10 passes) |
| A context change, 2 each day: the open cards again | 74 | 5 | 6,000 | 60,000 (2 changes) |
| Day start: resurface about 5 cards in Later | 5 | 1 | 5,000 | 5,000 |
| **`decide`, total** | | **36** | | **195,000** |
| Text: the gist of a long thread, when `my_day.compose` says yes | | 4 | 2,500 | 10,000 |

**The vendor cost of `decide` is small.** At USD 0.042 for each million input
tokens, with free output, 195,000 tokens cost about USD 0.008 each day. That
is about USD 0.18, or ₹15, each month of 22 working days.

**The text model is the other half.** Assume a price near US$0.60 for each
million input tokens, the range of the default `tier-fast` model. Then the 4
compositions cost about USD 0.13 each month, or ₹11.

**The customer's charge is not this number.** The customer pays in AI
credits, at the rate card of H-42, and the floor on each call sets most of
a `decide` charge. So the design counts calls: about 40 for each active
member each day, 36 of them `decide`. The member cap of 60 leaves room for a
busy day.

**The database is the other risk.** §8.1 bounds the provider runs to one for
each change or time edge. On My Day, one run serves both the feed and the
needs view (§14.1). MD-5 measures it.

### 9.3 Who pays — answered by the owner, 2026-10-13

**The customer pays for every AI call in AI credits.** That covers each
`decide` call and each text composition. The feed with no AI is free: the
signals, the templates, the order of the old path and every act. The card
shows no price (D32.7, D88).

**A customer with no credit keeps a working feed.** The CP-6 balance gate
refuses, the cool-down starts, and every card falls back to its old path. The
header shows one line: "AI suggestions are paused. Ask an admin to add
credits." It is the command bar's line (`navigation_shell.md` §6.5).

⚠️ **The text half waits on two things outside this spec**, as NS-4b does.
The first is **H-171**: the product's own text AI is not metered today. The
second is **H-69**: `ROUTER_SERVING_ENABLED` is an owner flip. The `decide`
half does not wait on them. `decide` already bills through the Console, and
each call has a `request_id` that joins it to its `usage_event` row.

---

## 10. Decide first — how My Day thinks

### 10.1 Why `decide` fits this feed

A chief of staff makes many small calls on each item. Does this matter today?
Is it urgent or only loud? Which reason will make the member act? Each call
is a choice from a short list, not an essay. That is the shape of `decide`
(D75).

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

- **Code builds every option.** The groups, the reason templates, the acts
  and the slots all come from code, out of `facts`. `decide` answers with one
  of their keys.
- **A template writes every sentence on a card.** The why line is the
  template that `decide` picked, filled with values from `facts`. A judged
  card holds no word that a model wrote.
- **Code runs every act.** An act's target, time and recipient come from the
  option that code built. `decide` only chose its key.
- **An answer that is not one of our keys is no answer.** The feed takes the
  old path. `compare_choice` already logs such an answer as `"unknown"`
  (`gateway/decide_features.py:416-446`).

**Fence (MD-6a).** `tests/unit/test_feed_decide.py::TestKeysOnly` answers
each question with a key that is not in its options, and with a valid key.
In the first case, the card's `href`, `acts`, `act_ref` and why line must be
byte-equal to an old-path card. In the second case, they must come from the
chosen option.

### 10.3 The questions

Each row is one feature in the one mode registry,
`gateway/decide_features.py` (§10.6). Every feature goes through `shadow`
before `on`. None claims an exemption from the adoption rule.

| Feature | Type | The question, in short | Options or levels | What code does with the answer | Old path |
|---|---|---|---|---|---|
| `my_day.triage` | choice | "Where does this belong in the member's day?" | `now`, `needs_you`, `ready`, `fyi`, `later`, `drop` | Sets the group. §7 rule 2 limits `drop` | The group table of §4.3 |
| `my_day.weight` | score, 5 levels | "How much does acting on this today move what the member must get done?" | `none`, `low`, `some`, `high`, `critical` | Sets `weight` (§7) | The priority matrix term |
| `my_day.reason` | choice | "Which fact best explains why this matters now?" | 2 to 6 reason templates of the kind, each shown filled | Picks the why line | The first template of the kind |
| `my_day.next_act` | choice | "Which next step fits best?" | The acts that code built for this card | Picks the main act. The rest go to the menu | The kind's default act |
| `my_day.slot` | choice | "Which slot suits this work best?" | Up to 8 free slots that the calendar packer found | Fills "Block it" or "Move to" | The earliest slot |
| `my_day.resurface` | boolean | "Should this item in Later come back to the member today?" | — | §6.3 rule 6, at a probability of 0.6 or more | The card stays in Later |
| `my_day.compose` | boolean | "Would a short summary save the member time here?" | — | The gist runs only on yes | No gist |
| `my_day.nudge` | boolean | "Is this worth a message outside the app now?" | — | Step 4 of §6.5, at 0.8 or more, inside the caps | No message |

### 10.4 The state — what the chief of staff knows

One request carries one state. Code builds it, and clips every text with
`clip_fact` (`gateway/decide_features.py:224`).

- **The member.** The role preset, the local time, the working hours of
  today, and today's One Thing (`calendar_day_state`).
- **What the member must get done.** The active goals and areas
  (`my_tasks_horizons`), the projects the member owns or leads, and the
  deadlines of the next 7 days. At most 10 of each.
- **The day.** Today's blocks, with their titles and times.
- **The member's habits.** For each kind, how often the member acted and how
  often they said "Not useful", over 30 days. Counts only, computed at day
  start.
- **The corrections** of §10.5.
- **The cards.** Up to 8 cards, each as `{id, app, kind, facts}`. A mail
  subject, a task title or a deal name is a clipped fact.

**The state holds no mail body.** The `facts` of an email card hold the
subject, the sender's name and the times. The gist of a long thread is the
one place a body goes to a model. It takes the text path of §9.1 rule 2, as
the email brief does (`digest.py:542-621`). It never goes to `decide`.

This state is how the feed follows what the member must get done across
projects and email (owner, 2026-10-13). The same overdue task weighs more on
the day its project is the One Thing.

**The context hash names its fields.** It holds these, and no others:

- the local date and the role preset
- the One Thing
- the active goal ids
- the ids of the projects the member owns or leads
- the corrections

It holds **no** clock, no blocks, no deadlines and no habit counts. Those
ride in the state, so a judgment can use them. A change to them never voids a
cached answer. A change to a
hashed field judges the open cards again, at most once each hour (§9.1
rule 5).

### 10.5 The member's corrections

`decide_features.instructions` takes a block, "Corrections from the user",
under each question (`gateway/decide_features.py:254-272`). Email uses it for
the rule match. My Day uses it the same way:

- "Not useful" on a card offers an optional line: "Tell My Day why".
- The line goes into `user_settings.my_day_prefs` (§13.3), newest first,
  clipped to 1,500 characters (`CORRECTIONS_CLIP`).
- It rides under `my_day.triage` and `my_day.weight` on every later pass.

So "Newsletters from Acme never need me" changes the next morning's feed
with no training, no rule editor and no setting screen.

### 10.6 Adoption — off, shadow, on

The feed follows the adoption rule of D75 item 7 and `customer_console.md`
§6A.14, with all four of its rules.

1. **Register.** MD-6a adds the eight `my_day.*` features to `FEATURES` in
   `gateway/decide_features.py`. Every default is `off`. MD-6b adds them to
   `ON_FEATURES`, or `on` resolves to `off` and logs `decide.mode_refused`
   (`decide_features.py:312-324`). No second registry, and no second helper.
2. **Widen the helpers, do not copy them.** Four changes, in MD-6a:
   - `ask` and `shadow` take one generic map of log ids, and keep the email
     keywords working. My Day passes `module_slug="my_day"`.
   - A packed request may mix features. The builder adds the questions of a
     feature only when that feature's own mode is `shadow` or `on`.
   - `shadow` logs one comparison for each card and feature in a packed
     request, not one for the call.
   - A new `compare_score` compares a score answer with the old-path level.
3. **Shadow.** In `shadow`, the feed shows the old path, and asks `decide`
   beside it. Each comparison goes to the table `my_day_judgments` (§13.2):
   keys, levels and numbers, never tenant text.
4. **Measure.** The measure read joins `my_day_judgments` with
   `my_day_card_state`. For each feature, the owner sees how often the two
   paths agree. The owner also sees how often the member acted on the top 5
   cards of each path.
5. **On, with a gate.** In `on`, the judged answer sets the value when its
   confidence or probability passes the feature's gate. The gate comes from
   the shadow data. Below it, the old path sets the value.

### 10.7 Whose call it is

A `decide` call names the member it serves. The loop binds the member
through `job_member_scope`, the way `as_mailbox_owner` binds a mailbox owner
(`routes/email/scheduler_hooks.py:53`). `run_attribution` then passes
`member_proven=True` (`acb_llm/routed.py:191`). So the Router bills the right
member and RLS binds the right tenant. The state holds only what the member
may read, because the providers ran with the member's own context (§8.3).

### 10.8 Mail is data

A mail subject is input that a stranger wrote. It enters the state as a
clipped fact, and never as an instruction. A subject that tries to steer
`decide` can move a probability on one card. It cannot add an option, so it
cannot add a link, a time or a recipient (§10.2). The thread gist is the one
text composition that reads mail. It fences the thread as DATA in its prompt,
as the day planner fences its task list (`calendar_ai_review.md` §2, item 1).

---

## 11. Acts — in the card, or in the app (D101)

**Decided by the owner, 2026-10-13 (D101).** In the owner's words, actions
"can happen inside cards, inside my day itself, unless the action needs more
context". Then "the user can prefer to open it in the app itself". This
amends the act rule of `navigation_shell.md` §4.3. The owner asked the design
to decide when to do which, and this section is that design.

### 11.1 Three ways an act runs

Code gives every act one **way**, when it builds the card. The way is a field
of the act: `tap`, `expand` or `open`.

| Way | What the member sees | When code gives it |
|---|---|---|
| **Tap** | One click. The act runs, the card leaves or changes, and the shared Undo toast shows | The act changes only the member's own data, Undo reverses it, and the card already shows its whole effect. Done, snooze, mark read. Move my own block to the slot on the card. Move my own due date to the day on the card. Block the slot on the card |
| **Expand** | The card opens a panel under its body, in place. The panel shows the whole effect and at most one small edit. One button names the effect, for example "Send to Dana Liu" | The act reaches another person, or it needs one small choice. Send the drafted reply. Nudge a colleague with a one-line note. Share a report with the team. Move a meeting with attendees. Pick another slot from up to 4 |
| **Open** | A link that opens the owning app at the entity, filled in | The act needs more context (§11.2), or it is class C |

**The member can always open the app instead.** Every card's menu holds
"Open in <app>", whatever the way of its main act.

### 11.2 "Needs more context" — the test, in code

Code runs this test for each act of class A or B. If any line is true, the
act's way is `open`:

1. **The member has not seen what the act depends on.** The thread holds
   more than one unread message, or the draft answers a message that the card
   does not show.
2. **The effect does not fit on the card.** The draft is longer than 1,200
   characters, or carries more than 2 attachments. The act reaches more than 6
   people. The choice has more than 4 options.
3. **The act needs a form.** It needs more than one field the member types,
   for example "Log a call" in the CRM.
4. **The member chose the app for this kind.** "Always open <kind> in the
   app" is set in `user_settings.my_day_prefs`. The member sets it from the
   card menu. After the member picks "Open in <app>" over an in-card act three
   times for one kind, the menu offers that setting.

Three more rules sit above the test:

- **Class C is always `open`.** Delete, bulk acts, money, and the approval of
  an agent's act never run in a card.
- **No write permission, no act.** A member who may read the entity and may
  not change it gets "Open in <app>" only.
- **A changed entity refuses the act.** The act sends the fingerprint the card
  showed. When the entity changed since, the owning route refuses, the card
  reloads, and it says "This changed. Look again."

**`decide` can make an act more careful, never less.** `my_day.next_act` may
pick the `open` act as the main act, for example for a sensitive customer
thread. It can never turn an `open` act into `tap` or `expand`, because the
test above runs in code after the answer.

### 11.3 The expand panel

- **It stays in the card.** It is not a dialog, and it moves no other card.
  Escape or "Cancel" closes it. On a phone it takes the card's full width.
- **It shows the whole effect.** For a reply: every recipient, the subject,
  the full text and the attachments. For a meeting: both times and every
  attendee. For a share: each recipient.
- **One small edit at most.** The reply text is a plain text box. The nudge
  is one line. Anything longer is "Edit in <app>", which opens the app with
  the edit kept.
- **Outward acts wait 10 seconds.** After "Send", the card says "Sending…
  Undo" for 10 seconds. Then the owning app's route sends it. Undo in that
  time sends nothing.
- **The act calls the owning app's own write route.** My Day adds no write
  route (`navigation_shell.md` NS-3). The write carries `meta.via = "my_day"`,
  the rule of D-PM-36.

### 11.4 Classes, for reference

| Class | What it is | Its way |
|---|---|---|
| **A** | It changes only the member's own data | `tap`, or `open` when §11.2 says so |
| **B** | It reaches another person | `expand`, or `open` when §11.2 says so |
| **C** | It is destructive, moves money, or approves an agent's act | Always `open` |

**Fence (MD-8).** `src/lib/shell/feedActs.test.ts` holds five rules:

- A class B act is never `tap`.
- A class C act is always `open`.
- Each line of §11.2 turns a fixture act into `open`.
- An act never calls a route that its owning app does not own.
- A `decide` answer never turns an `open` act into another way.

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
| Chat | the session link of `/chat` | WS-51 |

**Three gaps, and MD-3 closes them:**

- **Calendar has no link to a day or a time.** MD-3 adds `/calendar?date=
  YYYY-MM-DD&at=HH:MM`. It opens the day view and scrolls to the time. The
  A\* acts of §5.1 use it until MD-8.
- **Approvals opens only the queue.** MD-3 adds `/approvals?action=<id>`,
  which opens that one proposal.
- **`EntityPill` has no email, event or deal kind.** MD-3 adds three kinds to
  `src/components/ui/EntityPill.tsx`, so a card body names them as pills.

---

## 13. Data

Every new table follows R5: tenant-scoped, FORCE ROW LEVEL SECURITY, and the
member from the session, never from the request body. The template is
`infra/postgres/250_crm_views.sql:36-77`, with its
`organization_id UUID NOT NULL DEFAULT current_setting('app.tenant_id',
true)::uuid REFERENCES organization (id) ON DELETE CASCADE`. Each table must
pass `tests/unit/test_tenant_coverage.py`. Each migration takes the next free
number at build time (R1). It was 258 on 2026-10-13, so check it again at merge.

### 13.1 `my_day_card_state`

The state of a card for one member. It holds a row only for a signal the
member saw or touched. The signals themselves are never stored, because the
providers recompute them.

| Column | Type | Note |
|---|---|---|
| `organization_id` | the template's tenant key | |
| `member_email` | `TEXT NOT NULL` | From the session |
| `signal_id` | `TEXT NOT NULL` | The entity identity of §4.1 |
| `kind` | `TEXT NOT NULL` | The main kind when the row last changed. For affinity (§7) |
| `state` | `TEXT NOT NULL` | `seen`, `later`, `acted`, `snoozed`, `dismissed` |
| `dismiss_reason` | `TEXT` | `dismiss` or `not_useful` |
| `fingerprint` | `TEXT` | The state fingerprint (§6.8 item 1) the member saw at the last change |
| `visits` | `INT NOT NULL DEFAULT 0` | The visits of one local day (§6.8 item 2) |
| `visits_day` | `DATE` | The member's local day that `visits` counts |
| `first_seen_at`, `last_seen_at`, `acted_at` | `TIMESTAMPTZ` | |
| `not_useful_at` | `TIMESTAMPTZ` | The last "Not useful". A later sighting clears `dismiss_reason` and keeps this, so the count of §6.6 has its history |
| `snoozed_until` | `TIMESTAMPTZ` | Null with `snooze_on_change` true means "until it changes" |
| `snooze_on_change` | `BOOLEAN NOT NULL DEFAULT false` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

Primary key `(organization_id, member_email, signal_id)`. The `my_day` loop
deletes rows with no change for 30 days.

### 13.2 `my_day_judgments`

One row for each card, feature and pass, in `shadow` and in `on`. It feeds
the measure read of §10.6. It holds **no tenant text**: keys, levels and
numbers only.

| Column | Type | Note |
|---|---|---|
| `organization_id` | the template's tenant key | |
| `member_email` | `TEXT NOT NULL` | From the loop's active set (§8.3) |
| `signal_id` | `TEXT NOT NULL` | |
| `fingerprint` | `TEXT NOT NULL` | |
| `feature` | `TEXT NOT NULL` | For example `my_day.triage` |
| `mode` | `TEXT NOT NULL` | `shadow` or `on` |
| `old_value`, `judged_value` | `TEXT` | Option keys or levels only |
| `confidence` | `REAL` | The probability or confidence |
| `used` | `BOOLEAN NOT NULL` | Whether the judged value set the card |
| `request_id` | `TEXT` | Joins the call to its `usage_event` row |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

The loop deletes rows older than 30 days. A fence asserts that no column
holds a title, a subject or a name: `test_feed_decide.py::TestNoTenantText`.

### 13.3 `user_settings.my_day_prefs`

A new nullable `JSONB` column on `user_settings`, beside `shell_prefs`. It
holds the mutes of §6.6 and the corrections of §10.5.

- **Not inside `shell_prefs`.** `PUT /auth/me/shell` writes the whole
  `shell_prefs` blob, and its model refuses an unknown key
  (`routes/admin/me.py:329`, `:459`). So a layout save would delete every
  mute and correction.
- **Its own route.** `PUT /shell/feed/prefs` merges one key at a time on the
  server, with `jsonb_set`. It never replaces the column.
- **R6.** A nullable column with no default. Nothing reads it before MD-2.
- **Fence (MD-2).** `tests/unit/test_my_day_prefs_r8.py` saves a correction,
  then saves a layout through `PUT /auth/me/shell`, and asserts that the
  correction is still there. It also asserts the other way round.
  `tests/unit/test_my_day_prefs.py` is the hermetic half. It holds the keys,
  the limits and the SQL of the merge.

### 13.4 What lives in Redis

All keys go through the tenant-prefix wrapper (R5c):

- `decide` answers and text compositions, with a 26 h TTL, the email brief
  pattern. A lost key costs one new call, and it never loses state.
- The feed version, the cached answer and its next time edge (§8.1).
- The active set, `my_day:active` (§8.3).
- The call counters of §9.1 rule 8.

**The memory service is not the state store.** Mem0 has no organization key
and is off by default (`packages/acb_memory`).

---

## 14. The frontend

### 14.1 The page, and one poll

- `/` stays `MyDayPage`, behind a second flag, `NEXT_PUBLIC_MY_DAY_FEED`.
  With it off, the page is today's page.
- **One column, about 720 px wide**, the measure of the reference cards. At
  `xl` the right rail keeps `TodayCard`, so the day's timeline stays in view
  while the member scrolls. Next actions becomes the `due_today` and
  `stale_next` cards, so it leaves the grid.
- The header keeps `greetingLine` and `summaryLine`, with §6.7's honest
  counts.
- The feed reads `GET /api/shell/feed` through `useCachedResource`. A true
  miss shows `Skeleton` rows. A revisit never blanks (`AGENTS.md` rule 9).
- **One provider run for each poll on My Day.** The feed answer carries the
  needs view. The client writes it into the needs cache key, so the bell and
  the sidebar counts read it with no second request.
- **The shared poll calls each key.** Today its tick calls the first poller
  only (`ShellBell.tsx:239`). MD-1 changes the tick to call one refresh for
  each distinct key. On My Day the feed's refresh owns the needs key, so the
  needs poller does not run beside it. Fence: `shellBell.test.ts`, with two
  keys that both refresh, and a third poller of the needs key that stays
  quiet on My Day.
- The first 20 cards load first. Scrolling loads the next 20, to the end of
  the feed.

### 14.1a The answer of `GET /shell/feed`

`GET /shell/feed?limit=` gives 40 cards by default, and 80 at most.

```json
{
  "cards": [
    {
      "signal_id": "tasks:9b2e…",
      "app": "tasks",
      "kind": "overdue",
      "folded": ["block_soon"],
      "group": "needs_you",
      "score": 87,
      "entity": {"type": "task", "id": "9b2e…", "label": "Send the Q3 BOM"},
      "title": "Send the Q3 BOM to purchase",
      "why": "Due Monday. 2 days late.",
      "why_key": "days_late",
      "facts": {"due_at": "2026-10-12T17:00:00+05:30", "days_late": 2},
      "fingerprint": "a41f09c2",
      "urgent_at": null,
      "expires_at": null,
      "at": "2026-10-12T17:00:00+05:30",
      "href": "/projects?task=9b2e…",
      "acts": [
        {"id": "done", "label": "Done", "class": "A", "way": "tap", "act": "done", "act_ref": "9b2e…"},
        {"id": "open-day", "label": "Open the day", "class": "A", "way": "open", "href": "/calendar"}
      ]
    }
  ],
  "groups": [{"id": "now", "count": 1}, {"id": "needs_you", "count": 4}],
  "needs": {"count": 5, "total": 5, "items": [], "sources": {}, "totals": {}, "capped": []},
  "sources": {"tasks": "ok", "projects": "ok", "email": "absent", "approvals": "absent", "calendar": "ok"},
  "capped": [],
  "handled": [
    {"app": "tasks", "title": "File the VAT return", "href": "/projects?task=4c1d…",
     "by": {"kind": "person", "name": "ravi"}, "at": "2026-10-14T10:20:00+00:00"}
  ],
  "handled_ok": true
}
```

- `needs` is exactly the answer of `GET /shell/needs?limit=30`, from the
  same provider run, before the fold of §4.5. It always uses the needs
  route's own default limit, 30 (`DEFAULT_LIMIT`, `needs.py:150`), whatever
  `limit` the feed request carries. The client stores it under the needs
  cache key, so the bell's counts stay true.
- **The group ids** are `now`, `needs_you`, `ready`, `fyi`, `later` and
  `handled`, in that order. MD-1 emits only the first four.
- **The app ids** are `tasks`, `projects`, `email`, `approvals` and
  `calendar`. The `sources` key of the Calendar provider is `calendar`.
- `groups` lists only the groups that hold a card, in the order above.
- `folded` names the kinds that §4.5 folded into the card.
- An act with `way: "tap"` names its `act` and `act_ref`, as the needs items
  do. An act with `way: "open"` names its `href`.
- **A task's unread notifications are one signal, with one `read` act**
  (repair round 1 of MD-1a). The act's `act_refs` lists every unread
  notification id on the task, newest first, and `act_ref` is the first of
  them. The client marks each one read, and each needs row
  `projects:<id>` leaves. An asking reason (`assigned`, `mention`, `nudge`)
  sets the group over a `comment`.
- **The fingerprint hashes stable facts only.** They are the entity, the
  kind and the folded kinds. They are also the due time, the block times,
  the overlap ids, the notification ids and the waiting time. A countdown such as `minutes` or
  `days_late` never goes in it, so two polls a minute apart give the same
  fingerprint.
- **MD-2a adds five card fields.** `state` is `new`, `seen` or `later`.
  `updated` and `still_open` are booleans for the source line. `visits` is
  the count of the day. `state_fp` is the state fingerprint of §6.8 item 1.
- **MD-2c adds two answer fields.** `handled` is what the apps closed for the
  member since the start of the member's day, the newest first. Each entry is
  `{app, title, href, by, at}`, and `by` is `{kind, name}` (the `by` table of
  MD-2c). `handled_ok` is true when no Handled read that ran failed. A source
  that the member does not hold does not count as failed. `cards` and
  `groups` never hold `handled`, and it does not count against `limit`.
- A field that a later ticket adds is optional for the client.

### 14.2 The card registry

- MD-4 adds a `feedKinds` field to `NavPane` (`src/lib/nav.ts`):
  `{ kind, body }`. The same PR adds its row to `navigation_shell.md` §5.1.
- **Why not the `cards` field.** A card of `cards` is a Home card for an
  altitude, and it reads its own route. A feed kind is a body for one signal,
  and it reads nothing. It draws the `facts` it gets.
- `FeedCard` in `src/lib/shell/` draws the frame. It finds the body by kind.
  A kind with no body draws the frame alone.
- The first bodies:
  - `CalendarStrip` from Calendar, for card A
  - `DraftPreview` from Email, for card B
  - `ReportTile` from Reports, for card C, built on `Stat`
  - `TaskRows` from My Tasks

### 14.3 Keys and the phone

- `j` and `k` move between cards. `e` runs the main act if it is a class A
  act that runs in place. `s` opens snooze. `Enter` opens the entity. The keys
  never collide with the one `⌘K` listener.
- On a phone the feed is the page. The acts sit at the foot of each card, in
  reach of a thumb. The right rail goes away, and the Now group shows the
  next block instead.

### 14.4 The look

The look is the house look. `DESIGN_SYSTEM.md` binds every part, and the
reference screenshots set the anatomy, never the colours. The `visual-review`
skill checks the feed in light mode, at compact density, under a changed
accent, and beside My Tasks.

**The design mockup is `mockups/my_day_feed.html`** (2026-10-13). It draws the
groups, ten sample cards, the menu, "Why am I seeing this", the Later and
Handled groups, and the "2 new" pill. A switch shows the same cards with the
judged answers and on the old path. It draws the A\* acts as taps, as D101
allows. It does not draw the expand panel of §11.3 yet. Where the mockup and
this spec disagree, this spec wins.

---

## 15. Eight ideas beyond the cards

1. **"Why am I seeing this".** The card menu shows the score terms in words:
   "Due in 2 hours. You act on most overdue cards." It builds trust, and it
   is the first thing a member asks of a chief of staff.
2. **Ask about this card.** One menu row opens the assistant dock with the
   card as context. The dock owns the talk (`navigation_shell.md` §7.1).
3. **Teach My Day.** "Not useful" and its one line are the training. No
   survey and no setting screen.
4. **Delegate from a card.** "Give this to…" on a task card opens the assign
   form of Projects. The People rebalance read fills in a suggestion.
5. **Focus.** During a focus block in Calendar, the feed shows only Now. The
   rest waits until the block ends.
6. **The week ahead.** On the last working day, a card lists next week's
   deadlines and heavy days. It reads the outlook section of reports.
7. **What agents did.** The Handled group names each agent and each email
   rule that closed something, with a link to its history.
8. **The lead's morning.** At the team altitude, a lead's feed adds Team
   pulse, at-risk work and a person who is overloaded. This is MD-12, after
   NS-5.

---

## 16. Tickets

Every ticket ships dark. MD-1 to MD-5 call no model.

### MD-1a · The registry and `GET /shell/feed`, the gateway half — AGENT-SAFE

The spec-auditor cleared it on 2026-10-13 (GO-NARROWED), after B1 to B6 of
its report landed in §4.1, §4.3, §5.1 and §5.2. It adds no flag, no table,
no Redis key and no model call. `GET /shell/feed` serves on merge, and
nothing calls it until MD-1b. So it is dark by the absence of a caller.

Files: `gateway/routes/shell/needs.py` (split into one registry),
`gateway/routes/shell/feed.py`, `gateway/routes/shell/feed_rank.py`, and
`gateway/routes/shell/__init__.py`.

Done when:

1. `TestOneRegistry` (§4.4) and `TestOneCardPerEntity` (§4.5) pass. The fold
   seed is an overdue task with a block that starts in 20 minutes and
   overlaps a second block.
2. The feed returns the MD-1 kinds of §5 in the shape of §14.1a. Each card
   has a template why line, its old-path group (§4.3), a score and its acts.
   `done` and `read` are `tap`, and every other act is `open`.
3. Each Calendar kind has a negative test: another member's block never
   reaches the card. Each of the two feature gates of §5.1 has one too.
4. `GET /shell/needs` answers as before for the existing fixtures. Its
   `sources` keys stay exactly `tasks`, `projects`, `email`, `approvals`.
   `test_shell_needs.py` and `test_shell_needs_r8.py` pass with no edit.
5. The needs providers run first, in today's order and on today's budget. A
   slow feed-only provider never turns a needs source to `failed`.
6. A mount test proves the route registers. `gateway/main.py` hides an
   import error, so `test_shell_needs.py:913` is the model.
7. `test_feed_rank.py` holds the base table, each term, §7 rules 1 and 3, and
   a fixed seed. Rules 2 and 4 have no subject until MD-6a and MD-1b.
8. `test_shell_feed_r8.py` runs against a real Postgres (R8), with
   `pytestmark = _DB_GATE`. A second member's block in the same org reaches
   no card, and each source reads `ok`. The passing count is in the PR, and
   a run that skips is not a pass.

The old-path weight reads `packages/acb_common/acb_common/priority.py`
(`task_cell`, `priority_rank`). No second port of the D78 matrix.

**BUILT 2026-10-13, on branch `my-day-feed-md1`.** The route serves on merge.
Nothing calls it until MD-1b. The build made four choices that this file did
not fix:

- **The level of a D78 cell** is `round((7 - rank) * 4 / 6)`. So Critical is
  4 and Low Priority is 0 (`feed_rank.LEVEL_BY_CELL`).
- **The working day of `no_plan_yet`** comes from the People app's own self
  read, `get_me`. A member with no directory row gets the default policy's
  days. The read runs only when the other conditions hold.
- **A done block counts as a plan.** `no_plan_yet` stays quiet when the
  member has any block today, done or open.
- **The card's score** is the highest score of its folded signals, on the
  main kind's base.

**Known limits of MD-1a.** The review of round 1 found these two. They do not
block MD-1a, and a later ticket owns each one.

- **The Calendar window.** `day_summary` reads only today's window, from
  `day_start_hour` to `day_end_hour`. So a block before the day starts, or a
  block across midnight, gives no `block_soon`. The week read of `my_calendar`
  starts at the member's local midnight. `_working_day` falls back to the
  default policy, not to the org's own policy (`load_policy`).
- **The cost of a poll.** `day_summary` costs about 20 queries and 5 sessions
  on each feed poll. The read cache of MD-5 must cover it.
- **Mark read covers one page.** `act_refs` covers the unread notifications
  on the first page of the bell read (15). Older unread notes on the same
  task stay unread after Mark read, and the card comes back.

### MD-1b · The feed column and the one poll, the frontend half — AGENT-SAFE (build), OWNER-GATE (turn on)

Files: `src/app/api/shell/feed/route.ts`, `src/lib/shell/FeedCard.tsx`,
`src/lib/shell/MyDayPage.tsx`, `src/lib/shell/ShellBell.tsx`. Flag
`NEXT_PUBLIC_MY_DAY_FEED`, off by default.

The spec-auditor cleared it on 2026-10-13 (GO-NARROWED) for a build beside
MD-1a, against the shape of §14.1a.

Done when:

1. With the flag on, `/` renders the feed column. The groups come in the
   order of `groups`, and the cards keep the server order inside each group.
   `TodayCard` sits on the `xl` rail, and the Next actions card is gone. The
   page makes one request at the default limit, and shows 20 cards at a
   time. With the flag off, `/` renders today's page.
2. The feed answer fills the needs cache key, and the shared poll calls each
   key (§14.1). Fence: `shellBell.test.ts`.
3. Done and mark read run through the owning app's code, as NS-3 does. Every
   `open` act is a link.
4. The card draws through `HomeCard`, `Skeleton` and the house tokens, and
   `conformance.test.ts` passes with no new budget.

**Built 2026-10-13** on the branch `my-day-feed-md1b`. The client is
`src/lib/shell/feed.ts`, and its fence is `feed.test.ts`. The poll fence is
`shellBell.test.ts`, and the cache fence is `dataCache.test.ts`
(`guardedPut`).

**Two accepted limits of MD-1b.** The verifier accepted both on 2026-10-13.

- A feed read can land after `keep()` and before the PATCH answers. That
  read counts as the truth for one PATCH round trip. The re-read after the
  invalidate heals it.
- Suppose a feed answer shows a reopened task, and the bell then marks that
  task Done within the 30 s hold. The feed card stays until the hold ends.
- A row stays pending from the tap until the act calls `keep`, `show` or
  `fail`. If the store's first load never settles, the card stays hidden
  for that page visit. A reload shows it again.

**Not in MD-1b, and where each part went.** The honest counts of §6.7 and the
card menu go to MD-2, because each needs its state table. `stale_next` is
MD-3. Server paging with a cursor goes to MD-5. The keys of §14.3 go to
MD-9.

**Flag-on follow-ups of MD-1b.** Each one waits on a later ticket. None of
them shows while `NEXT_PUBLIC_MY_DAY_FEED` is off.

- **The why-line clock.** `feed.py` `_clock` writes a fixed 24-hour `HH:MM`.
  The card time and Today follow the browser's locale through `fmtClock`. In
  a 12-hour locale the two disagree. MD-4 fixes it: the client draws the
  times of the why line.
- **A carried needs read can be 5 seconds old.** `carryFeedNeeds` reads the
  feed key without `force`, and `FRESH_MS` is 5 seconds. MD-5 owns the fix,
  with the ETag.
- ~~**A slow feed holds the bell on My Day.**~~ **Fixed by MD-2b,
  2026-10-14.** `fetchNeeds` waits 4 seconds at most on the carrier
  (`CARRIER_WAIT_MS`). Then it reads `/shell/needs`.

### MD-2 · The life of a card — AGENT-SAFE, in four slices

The MD-2 audit of 2026-10-13 gave GO-NARROWED, and split the ticket in four.
§6.8 holds the answers that made MD-2a dispatchable.

### MD-2a · Card state on the server — AGENT-SAFE

Files:

- `infra/postgres/<next free>_my_day_card_state.sql`. The next free number
  was 258 on 2026-10-13. Check it again at merge (R1).
- The regenerated tenancy set: `uv run python scripts/gen_tenant_migration.py`.
- `POST /shell/feed/state` in `gateway/routes/shell/`, and its BFF route
  `src/app/api/shell/feed/state/route.ts`.
- The state join in `feed.py`, and the sort of §6.8 item 6 in
  `feed_rank.py`.

Done when:

1. The table passes `test_tenant_coverage.py`, with a per-table assertion in
   the model of `test_the_task_refs_table_is_tenant_scoped`. The R8 suite
   holds four negative tests:
   - Org B reads none of org A's rows.
   - WITH CHECK refuses org A's row from a session bound to B.
   - Member B cannot change member A's row for the same `signal_id`.
   - A body that names a member or an org gets 422.
2. Seen, snooze (four choices), dismiss, "Not useful" and `acted` write the
   table through the contract of §6.8. The feed honours each.
3. Each rule of §6.3, as §6.8 runs it, has its own test, and one test holds
   their order. Each test fails when someone removes its rule. Rule 6 asserts
   "no answer, stays in Later".
4. A dismissed task stays dismissed when a block folds in. It also stays
   dismissed when it turns overdue. A new due date brings it back (§6.8
   item 1).
5. A signal that leaves writes no row (§6.1). `needs` is unchanged with state
   rows present (§6.8 item 9).
6. Fatigue is −8 for each visit of the day, to −24 at most, in
   `test_feed_rank.py`.
7. The R8 suite passes with no skip, and the count is in the PR.

The `feed_rank.py` comment that gives Handled to MD-7 changes to MD-2c.

**BUILT 2026-10-14, on branch `my-day-feed-md2`.** The table is migration
258. `gateway/routes/shell/state.py` holds the POST, the rules in their
order (`RULES`) and the state join. `src/app/api/shell/feed/state/route.ts`
is the BFF route. The fences are `test_my_day_state.py`,
`test_my_day_state_r8.py`, `test_feed_rank.py` and the route test. The
build made these choices, which this file did not fix:

- **The lift instant of rule 4** is `urgent_at` less 2 hours, the start of
  the urgency term of §7. The example of §6.3 rule 4 fixes it. §6.8 item 6
  holds the bound for a card that the member never saw.
- **A snooze that ended** brings the card back as `new`, with a dot.
- **An `acted` row** shows its card as `seen`. It gets no fatigue, and rule
  8 does not fold it. The next sighting writes `seen` again.
- **The member's own act is no update.** Mark read takes ids out of
  `note_ids`, so the state fingerprint moves. An `acted` row whose act came
  at or after the newest event on the card takes the new fingerprint as
  seen. The newest event is the newest unread note (`last_note_at`), or the
  last message of a thread.
- **The member's day** starts at `day_start_hour`. So `visits_day` is the
  local date of the most recent day start. The snooze times count from that
  day too. At 00:30 the member's Monday still runs, so "Tomorrow" is
  Tuesday's start.
- **A failed or slow state read** keeps the feed. The read has 1.5 seconds
  (`STATE_TIMEOUT_S`). Each card then reads as new, and the failure goes to
  the log.
- **The first write of a card is never lost.** The POST claims a row for
  each card that has none, then locks the rows in signal-id order, then
  writes them in one statement. `test_my_day_state_r8.py` holds two sessions
  on one new card.
- **Do not run `gen_tenant_migration.py` in full.** It renames the FK, index
  and policy names of the tables that the gtd and CRM renames moved, and
  production keeps the old names. Migration 258 adds its table to the four
  phase files by hand, as 251 and 255 did. This holds until someone fixes
  that debt.
- **The thread signal** gains the fact `message_id`, for the state
  fingerprint.
- **§14.1a names five state fields**: `state`, `updated`, `still_open`,
  `visits` and `state_fp`.
- **A notification signal carries `last_note_at`**, the time of its newest
  note. The reason note of `at` can be older. The own-act rule reads it, and
  it is in no fingerprint and not in the needs view.

**Known limits of MD-2a.** A later ticket owns each one.

- **Mark read from another surface** changes the state fingerprint of a
  dismissed card, because it removes ids from `note_ids`. So the card comes
  back as "Updated". Only an `acted` row knows that the member read it.
- **Another person's change with no event time** reads as the member's own
  act on an `acted` card. A new due date is such a change.
- **A dismissed card still takes a provider slot.** `PER_APP` binds the
  providers before the join (§6.8 item 7).
- **State rows have no bound** until the 30-day sweep of MD-5.
- **The feed reads the day hours twice** for a member with the Calendar: once
  for the Calendar provider and once for the state join. The read cache of
  MD-5 can share them.

### MD-2b · The card menu and the seen observer — AGENT-SAFE, after MD-2a

The audit of 2026-10-14 gave GO-NARROWED. These answers close its three gaps.

**What is in MD-2b:**

- **The state fields reach the card.** `readCard` keeps `state`, `updated`,
  `still_open`, `visits` and `state_fp`. The card draws a dot for `new`,
  "Updated" for `updated`, and "Still open" for `still_open`.
- **The seen observer (§6.2).** Only the feed column observes, never the bell.
  A card is seen when half of it is in view for 1.5 s on a visible tab. One
  POST goes each 10 s, and one on `pagehide` with `keepalive`. A body holds 50
  items at most. A card with no `state_fp` is not sent. A 422 drops the batch.
  A 5xx keeps it for the next send.
- **The menu.** Snooze with its four choices, Dismiss and "Not useful", on the
  one menu primitive, `src/components/ContextMenu.tsx`. A second menu is a
  defect.
- **A menu act hides the card through an overlay of its own.** The overlay is
  keyed on `signal_id`, and it is never `needsActs`. So the bell and the
  sidebar counts do not change (§6.3, §6.8 item 9). The overlay ends when a
  feed answer fetched after the POST lands. A failed POST shows the card
  again, with an error line.
- **Undo for a menu act.** The shared toast (`src/lib/toast.ts`) offers Undo
  for 10 seconds. Undo posts the new action `restore`. It clears a snooze or a
  dismiss, and leaves the row `seen`. The POST contract of §6.8 gains it.
- **`acted` is written.** A tap act that succeeds also posts `acted` for its
  card (§6.8 item 4).
- **A failed source keeps its cards for 5 minutes**, from its last `ok`
  answer (§6.1, as fix round 1 decided it). Calendar is a source too. A
  capped or absent source carries nothing. A capped group ends with "Show all
  in <app>".
- **The order holds during a visit (§7 rule 4).** A visit is the life of one
  mounted feed column. It ends at a click on the "N new" pill, or at a
  remount. A card that a new answer would move stays in its place until the
  visit ends. A card that leaves goes at once.
- **Later draws folded**, with its count.
- **The carrier's limit.** `fetchNeeds` waits 4 s at most on the carrier, then
  reads `/api/shell/needs`.

**Not in MD-2b.** "Why am I seeing this", "Tell My Day why" and "Stop showing
<kind>" go to MD-9. "Open in <app>" goes to MD-8.

Done when:

1. Each item above has a test that fails when someone removes it.
2. A menu act leaves the bell's count and the sidebar counts unchanged.
3. `restore` passes the gateway's own POST tests, and the R8 state suite
   holds one `restore` case.
4. `e2e/my-day-feed.spec.ts` follows the pattern of `e2e/shell-bell.spec.ts`.
   It asserts the POST bodies and the batch on `pagehide`. It also asserts a
   menu act with no bell change, and the fallback after 4 seconds.
5. The `visual-review` skill runs in light mode, at compact density, under a
   changed accent and beside My Tasks. `conformance.test.ts` adds no budget.

**BUILT 2026-10-14, on branch `my-day-feed-md2b`.** The gateway adds
`restore` to `routes/shell/state.py`. The client is
`src/lib/shell/feedState.ts`, the one client of `POST /api/shell/feed/state`.
`feed.ts` keeps the state fields, the carry and the visit. `FeedCard.tsx`
draws the dot, the marks, the menu, the pill and the Later fold.
`needs.ts` holds the carrier's limit, `CARRIER_WAIT_MS`.

The fences are `test_my_day_state.py`, `test_my_day_state_r8.py`,
`feedState.test.ts`, `feed.test.ts`, `needs.test.ts`, `myDayPage.test.ts`
and `e2e/my-day-feed.spec.ts`.

The build made these choices, which this file did not fix:

- **The Undo of "Not useful" clears `not_useful_at`.** The Undo says that the
  act did not happen, so it must not count against the kind (§6.6, §7).
- **A `restore` claims no row.** A card with no row, or a row that is not
  snoozed or dismissed, gets no write. A `restore` counts no visit.
- **The client sends one sighting of a card in 30 minutes at most**, for each
  `state_fp`. The gateway counts two sightings in 30 minutes as one visit.
- **The queue also sends its items when the column unmounts**, with
  `keepalive`. A move to another app inside Metorite fires no `pagehide`.
- **`acted` goes into the queue of the seen observer.** It is a record, so it
  waits for the next send.
- **Only a 5xx, a 502 or no answer keeps a batch.** Each 4xx drops it,
  because the same body never passes.
- **The carry reads the last `ok` cards of each source, not the cache
  entry.** A Projects write drops the entry, and the read after a Done must
  still carry a failed source. `fetchFeed` keeps those cards in memory, from
  raw answers only, and a new member starts with none.
- **A card that a menu act hid is never carried.** It may come back from a
  sound answer that its fetch began after the write, or after Undo.
- **The overlay of a menu act ends** when an answer lands whose fetch began
  after the write landed.
- **Later is outside the cut of 20 cards.** It draws after the other groups,
  folded, with all its cards.
- **A card with no `state_fp` has no menu**, and the observer never sends it.
- **After a menu act, the focus goes back to the "···" button.** When the card
  left, the focus goes to the column's heading.

**Fix round 1, 2026-10-14.** The review and the verifier found these, and
this round fixed each one:

- **The carry rule of §6.1 is now decided.** Only a failed source carries,
  from its last raw `ok` answer, for 5 minutes at most (`CARRY_MS`). A capped
  or absent source carries nothing.
- **A Done is never carried back.** A tap act that takes its whole card away
  stops the carry of that card. The store's Undo lets it back.
- **A menu act drops the feed key** (`invalidate`). A read that began before
  the write then never stores its answer, so an Undo does not hide the card
  again.
- **The state queue belongs to one member.** It sends nothing once the cache
  names another member. A change of member gives the hook a new queue.
- **`columnView` in `feed.ts` is the one rule** of the visit, the cut and the
  pill. The pill counts only new cards that the next cut draws outside Later.
- **A POST gives up after 8 seconds** (`STATE_TIMEOUT_MS`), and the queue
  keeps its batch.

**Fix round 2, 2026-10-14.** A focused verification found two defects in
the account-switch change of round 1. This round fixed both:

- **An account switch clears no cache.** Round 1 called `bindIdentity(null)`
  before each navigation. That woke every reader of the leaving page, and
  each read went out under the new cookie. It also closed open panes, for
  every member and with the flag off. That call is gone.
- **One narrow signal takes its place.** `src/lib/accountLeave.ts` holds
  `onAccountLeave` and `leaveAccount`. The 4 exits call `leaveAccount` just
  before they navigate: `switchTo`, both exits of `signOutThis`, and the tab
  that hears another account. The signal touches no cache, wakes no reader
  and closes no pane. A listener that throws never blocks the navigation.
- **The state queue seals itself on the signal.** It drops its items, takes
  no new item, and sends nothing on `pagehide` or at the unmount until the
  page reloads. The seal also holds each queue that the page makes after it.
  The seal does not depend on `identity()`.
- **The fences.** `accountSwitchLeave.test.ts` proves with module mocks that
  each exit calls `leaveAccount` and never `bindIdentity`, `clearAll` or
  `invalidate`. `feedState.test.ts` holds the seal probe. The
  `shell-account.spec.ts` check holds the new document for 2.5 seconds, and
  the leaving page makes no read.

**Known limits of MD-2b.** A later ticket owns each one.

- **A visit starts with the first answer that the column draws.** On a
  revisit that is the cached answer, so the new cards of the read behind it
  wait behind the pill.
- **A sighting that a 4xx refused** is not sent again for 30 minutes.
- **A card more than twice as tall as the window** is never half in view, so
  it is never seen.
- **A batch that `pagehide` sends** gets no second try when it fails.

### MD-2c · Handled and the honest summary — AGENT-SAFE, after MD-2a

The audit of 2026-10-14 gave NO-GO for want of these answers. They close it.
The server half may build beside MD-2b. The client half follows MD-2b,
because it shares `feed.ts`, `FeedCard.tsx`, `MyDayPage.tsx` and `myDay.ts`.

**The sources.** There are three. The `acted` rows are not a source, because
they hold no title and no link, and the app histories already record what
the member closed.

- **Tasks.** A new read beside `my_due_tasks` in
  `routes/projects/personal.py`. It reads the tasks of the member's lens with
  `completed_at` since the day start. The actor is `created_by` of the newest
  `pm_activities` row of type `status_change` for that task. A tie of
  `created_at` goes to the higher `seq` (migration 213).
- **Email rules.** The rows of `email_executed_rules` with status `APPLIED`
  since the day start, on the mailboxes the needs email provider reads (owned,
  and `in_all_inboxes`). A row on a message in the trash counts, because the
  rule may have put it there. The title is the subject, or else the rule
  name. The link is the message, or else the email app (`/email`). The app
  has no link to its rules history.
- **Approvals.** A new read in `action_broker`, `read_decided(since)`, beside
  `read_pending`. It reads the statuses `approved`, `applied` and `rejected`.
  `failed` is not handled. Only the org admin who passes the approvals gate
  gets it. No new index: the read is bounded by the tenant, the status and 15
  rows.

**The shape.** Each provider may carry a `handled` callable on
`needs.Provider`. The answer gains `handled`, a list of
`{app, title, href, by, at}`, newest first, 15 for each source and one entry
for each entity. `by` is `{kind, name}`:

| `created_by` or `reviewed_by` | `kind` | `name` |
|---|---|---|
| The member's own address | `you` | null |
| Another member's address | `person` | the local part, as the bell's `_who` does |
| `agent:<name>` | `agent` | the agent's name |
| `system:sync`, `system:workflow:<id>`, `system`, `operator` | `system` | null |
| An email rule | `rules` | null |

The client writes the words, for example "You", "Ravi", "Email rules",
"Metorite".

**The budget.** The handled reads run at the same time as the state join,
after the providers, inside the same 1.5 seconds. So the feed's worst case
stays at 7.5 seconds, under the 8 seconds of the BFF route. A read past its
limit counts as failed.

**The render.** `cards` and `groups` never hold `handled`. The list does not
count against `limit`. The client draws it as the folded Handled group, after
Later.

**The summary.** "N things need you" counts the open cards of Now and Needs
you in the feed answer, and not the bell's `needs.count`. The two can differ,
because the bell still counts a snoozed card. "The rest is handled" shows
only when `handled` is not empty and no handled read failed. A source that
reads `absent` does not count as failed.

Done when:

1. `tests/unit/test_feed_handled.py` holds the shape, the `by` table, the
   dedup, the 15 cap, the budget and the summary rule.
2. `tests/unit/test_feed_handled_r8.py` has `pytestmark = _DB_GATE`. It holds
   four negatives:
   - Member B never sees member A's completed lens tasks.
   - Org B sees no rule rows of org A.
   - Org B's admin sees no approval of org A.
   - A member who is not an admin gets no approval row.
3. The R8 suite passes with no skip, and the count is in the PR.

**BUILT (server half), 2026-10-14, on branch `my-day-feed-md2c`.** No
migration. `gateway/routes/shell/handled.py` runs the three reads. Each read
is a new function in the app that owns its table:

- `personal.my_completed_since`, beside `my_due_tasks`.
- `runner.applied_rules_since`, beside `rules_history`.
- `action_broker.read_decided`, beside `read_pending`.

`needs.Provider` gains `handled`, and the tasks, email and approvals
providers carry one. The fences are `test_feed_handled.py` (51 tests) and
`test_feed_handled_r8.py` (13 tests, R8). The client half is still open,
and it follows MD-2b. The build made these choices, which this file did not
fix:

- **The claim field is `handled_ok`.** It is true when no Handled read that
  ran failed. §14.1a holds it.
- **The day** comes from `state._hours` and `Day.day_start`, the functions
  of the state join. A member with no Calendar pane gets the zone from the
  lens and the start hour 07:00 (§6.8 item 8).
- **The budget** is `state.STATE_TIMEOUT_S`. The day read counts inside it.
  A read that is not done at the limit reads as failed. A read that did not
  start before the limit reads as failed, and it never starts.
- **One read at a time** (fix round 1). The reads run as one chain beside
  the state join, so a feed read holds two pool connections at most. Three
  reads at once held four, and at a peak the join then missed its own limit.
  `test_a_feed_read_holds_two_connections_at_most` holds the rule.
- **One cut to 15.** Each read asks for 15 rows, and `handled_run` cuts each
  source to 15. The providers do not cut again.
- **A failed or slow read never fails the feed.** The cards and `needs` do
  not change, and `handled_ok` goes false.
- **One entry for each entity.** The email read keeps the newest rule row
  for each message in its SQL, before its `LIMIT`. So many runs on one
  message never push other messages out. The run also keeps the newest entry
  for each entity. A read that gives two rows for one entity gives one entry.
- **A rule row with no message** opens `/email`. The email app has no link
  to its rules history.
- **The `by` table.** An actor with no `@` that is not `agent:<name>` reads
  as `system`. This covers `system`, `system:*`, `operator` and an empty
  value.
- **Projects carries no Handled read.** A closed task is a row of My Tasks,
  and a notification that the member read closed nothing.

**Known limits of the server half.** A later ticket owns each one.

- **An entry does not say how an approval ended.** The shape of the ticket
  has no status, so a rejected action and an applied action look the same.
  The client half must decide if the words need it.
- **The feed reads the hours of the day three times** for a member with the
  Calendar pane. The Calendar provider, the state join and Handled each
  read them. The read cache of MD-5 can share them.
- **`completed_at` has no index.** The membership join of the lens bounds
  the read, as the ticket records.

**BUILT (client half), 2026-10-14, on branch `my-day-feed-md2c-client`.**
It is dark behind `NEXT_PUBLIC_MY_DAY_FEED`. The parts:

- **The reader.** `readFeedAnswer` in `src/lib/shell/feed.ts` reads
  `handled` and `handled_ok`. An older gateway sends neither, so the page
  shows no Handled group and makes no claim. The reader drops an entry with
  no title, a `by` kind it does not know, or a link that is not a path of
  this app. It also drops a card in the group `handled`.
- **The group.** `FeedColumn` draws Handled folded after Later, as "Handled
  today" and its count. Each row is the app's icon, the title as a link, and
  who closed it with the time, for example "You · 09:30". `handledBy` writes
  the words of the `by` table. Handled never enters `cards`, the cut, the
  pill or the visit.
- **The summary.** With the flag on, `feedNeedsCount` counts the open cards
  of Now and Needs you, after the column's own hides. `handledClaim` holds
  the claim. With the flag off, the summary is the line of NS-3.
- **The fences.** `feedHandled.test.ts` (18 tests), three tests in
  `myDayPage.test.ts`, and two checks in `e2e/my-day-feed.spec.ts`.

The build made these choices, which this file did not fix:

- **The claim follows a count of needs only.** When nothing needs the
  member, the line stays "Nothing needs you right now.", because "The rest"
  then has nothing to follow.
- **A failed Calendar silences the claim too.** A calendar card can sit in
  Now, so the page cannot back "The rest is handled." without it.
- **A name the server left out** reads "Somebody" for a person, as the bell
  writes it, and "An agent" for an agent.
- **The words do not say how an approval ended.** The `by` word names who
  decided, and the row opens Approvals. A later ticket can add a status.

### MD-2d · Feed preferences — AGENT-SAFE

The column `user_settings.my_day_prefs` (its own migration number, at build
time), `PUT /shell/feed/prefs`, and `test_my_day_prefs.py` (§13.3).

- **The keys.** There are three:
  - `mutes`: a list of kinds, 20 at most.
  - `open_in_app`: a list of kinds, for §11.2 rule 4.
  - `corrections`: 10 lines at most, 300 characters each, 1,500 in all.

  Any other key gets 422.
- **The body.** `{key, value}`. The server merges one key with `jsonb_set`,
  and never replaces the column.
- **A member with no settings row.** The PUT inserts the calendar seed too
  (`_seed_from_work_schedule`), as `me.py` `_write_shell` does (D-PC-16). A
  test holds this case.
- MD-9 reads `mutes` first. MD-8 reads `open_in_app`. MD-6b reads
  `corrections`.

**BUILT, 2026-10-13.** Migration 259 adds the column. The routes are in
`gateway/routes/shell/prefs.py`, and the BFF route is
`src/app/api/shell/feed/prefs/route.ts`. `GET /shell/feed/prefs` gives the
stored object, and a key the member never set is absent. The fences are
`tests/unit/test_my_day_prefs.py` and `tests/unit/test_my_day_prefs_r8.py`.
Nothing calls the routes yet, so they are dark by the absence of a caller.

The build made three choices that the ticket did not record:

- A kind is a kind that a provider of `needs.REGISTRY` gives. The write
  refuses another kind, and the read drops a kind that left the registry.
- `open_in_app` takes 20 kinds at most, the same cap as `mutes`.
- The server refuses a correction value over a limit with 422, and never
  clips it. The client clips the list, newest first, before it saves.

### MD-3 · More signals, and the three link gaps — AGENT-SAFE

The MD-3 kinds of §5, the CRM "my due activities" read, `/calendar?date=&at=`,
`/approvals?action=`, and three `EntityPill` kinds. Each provider has its own
negative test, as NS-3 done-when 3 has. Two more:

- `absent_on_due` reads the member's own absences only.
- `colleague_out` gives no row to a member who fails `can_read_hr_fields`.

### MD-4 · Card bodies — AGENT-SAFE

`CalendarStrip`, `DraftPreview` (read only, from `email_ai_drafts`),
`ReportTile` and `TaskRows`, the `feedKinds` field and its row in
`navigation_shell.md` §5.1. Done when each body renders in the `visual-review`
contexts, and `conformance.test.ts` passes with no new budget.

### MD-5 · The loop, the change sink and the read cache — AGENT-SAFE

The `my_day` loop with its kill switch, the active set, and the member's
context of §8.3. The third event sink, the feed version, the next time edge
and the 304 path.

Done when:

1. `test_my_day_loop.py` (§8.3) passes.
2. A measured run shows no provider query from a quiet member's poll
   before its next time edge. The run is in the PR.
3. A test shows that a `block_soon` card appears 30 minutes before its block
   with no event in between.
4. The sink makes no database query. A test counts the queries of one task
   edit with the sink on and off.

### MD-6a · The `decide` pass, in shadow — AGENT-SAFE (build), OWNER-GATE (turn on)

Files:

- `gateway/decide_features.py`: the eight `my_day.*` features and the four
  helper changes of §10.6 item 2.
- `gateway/routes/shell/feed_decide.py`: the questions, the state builder,
  the reader and the caps.
- Migration `<next free>_my_day_judgments.sql`.
- The `my_day` loop of MD-5.

Done when:

1. The eight features are in the one registry, `off` by default. No second
   registry and no second helper exist.
2. In `shadow`, the feed shows the old path, and each comparison writes one
   `my_day_judgments` row. `TestNoTenantText` passes.
3. `TestKeysOnly` (§10.2), `TestCaps` (§9.1) and the §7 rule 2 test pass.
4. A pass with no new card makes no call. A repeat of the same facts makes
   no call. A change to an unhashed field makes no call. A test counts them.
5. A 402 starts the cool-down, and every card stays on its old path.
6. The measure read of §10.6 item 4 exists.

**Turning on `shadow` for a real organization is the owner's flip** (§17.1),
because it spends credits. Residency is answered (D100).

### MD-6b · `decide` on, with gates — AGENT-SAFE (build), OWNER-GATE (turn on)

The `my_day.*` features join `ON_FEATURES`. `on` for each feature at the gate
the shadow data sets, and the corrections of §10.5. Done when a judged answer
under its gate leaves the old-path value, and a test proves it for each
feature.

### MD-6c · The thread gist — AGENT-SAFE (build), OWNER-GATE (turn on)

The gist of a long thread, behind `my_day.compose` and the flag
`MY_DAY_COMPOSE`. Done when a gist runs only after a yes, a repeat makes no
call, and the gist fences the thread as DATA. **Waits on H-171 and H-69
before the flag goes on.**

### MD-7 · The day cards — AGENT-SAFE

`brief`, `while_away` and `day_end`. `day_end` links to `EndOfDayReview` and
copies nothing of it.

### MD-8 · Acts in the card — AGENT-SAFE (D101)

The A\* acts as taps, and the class B acts in the expand panel of §11.3.
MD-9 also adds the keys of §14.3. The
"needs more context" test of §11.2, and "Always open <kind> in the app".
Fence: `feedActs.test.ts` (§11.4).

### MD-9 · Learning and the second wave of signals — AGENT-SAFE

Affinity, mutes, "Why am I seeing this", `metric_moved`, `lead_replied`,
`whatsapp_waiting`. "Why am I seeing this" names the judged group and weight
in words, for example "Judged high: it moves the Q3 launch".

### MD-10 · Nudges outside the app — OWNER-GATE

Step 4 of §6.5, on WhatsApp templates (WAC-7) or web push (D-2). Opt-in for
each member. It sends a message to a real person through a third party, so
CLAUDE.md §3a rule 3 binds.

### MD-11 · Meeting overlaps — BLOCKED on calendar sync

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

MD-1a → MD-1b → MD-2 → MD-5 → MD-6a → MD-3 → MD-4 → MD-6b → MD-7 → MD-9 → MD-6c →
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
| Turn on `MY_DAY_COMPOSE`, or set any `my_day.*` mode other than `off` in `DECIDE_FEATURE_MODES` | It spends customer credits | Owner only. CLAUDE.md §3a rule 3. Residency is answered (D100) |
| MD-10, a message outside the app | It reaches a real person through a third party | Owner only. CLAUDE.md §3a rule 3 |

⚠️ These bind by prose only. `plan-guard.mjs` names none of these flags (R7,
advisory), the same gap as `navigation_shell.md` §13.1.

### 17.2 Open — each has a default an agent builds to

| # | Question | Default |
|---|---|---|
| Q1 | May a card run, in place, the A\* acts and the class B acts? | **Answered by the owner, 2026-10-13 (D101).** Yes, unless the act needs more context. §11 holds the rule |
| Q2 | Who pays for the AI? | **Answered by the owner, 2026-10-13.** The customer pays for every AI call in AI credits, `decide` and text alike. The feed with no AI is free (§9.3) |
| Q3 | Does external calendar sync move up, so card A can ship? | **Answered by the owner, 2026-10-13.** Metorite will read Google and Outlook calendars. Clashes fold in then (MD-11). Until then, the feed ships the overlaps it can see |
| Q4 | Are nudges outside the app opt-in, with three a day at most? | Yes, opt-in, and off for everyone until MD-10 |
| Q5 | The Projects report template T2 is also called "My day". Rename it? | Rename T2 to "My work today" in MD-4, so one name means one page |
| Q6 | Does the `decide` residency answer cover My Day? | **Answered by the owner, 2026-10-13 (D100).** Yes, and every other app too. My Day's state sends titles, names and subjects as clipped facts, and no mail body (§10.4) |

---

## 18. Verification commands

```bash
# Frontend — the whole gate
cd workbench/control_plane && npx tsc --noEmit && npx vitest run

# The fences this spec adds, by name
npx vitest run src/lib/shell/ src/lib/theme/ src/lib/nav.test.ts

# Gateway — needs a real database (R8)
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_shell_needs.py tests/unit/test_shell_needs_r8.py tests/unit/test_shell_feed.py tests/unit/test_feed_rank.py tests/unit/test_shell_feed_r8.py -q
uv run ruff check apps/services/gateway/gateway/routes/shell
uv run pytest tests/unit/test_my_day_state.py tests/unit/test_my_day_state_r8.py -q
uv run pytest tests/unit/test_my_day_prefs.py tests/unit/test_my_day_prefs_r8.py -q
uv run pytest tests/unit/test_feed_handled.py tests/unit/test_feed_handled_r8.py -q  # MD-2c
# MD-5 adds tests/unit/test_my_day_loop.py, and MD-6a adds tests/unit/test_feed_decide.py
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

- `work_plan.md` §2, row **WS-58**.
- `INDEX.md`, ACTIVE.
- `navigation_shell.md` §4.3 and §7.2: the feed's content, order and life
  live here. The page frame, the bell, the needs contract and the flags stay
  there.
- `navigation_shell.md` §5.1 gets its `feedKinds` row in MD-4, not before.
