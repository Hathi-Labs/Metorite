# My Day as a live feed — the chief of staff

**Status: ACTIVE, 2026-10-14.** These tickets are BUILT and merged:

- MD-1a, the gateway half (#964)
- MD-1b, the frontend half (#967)
- MD-2a, the card state on the server, migration 258 (#983)
- MD-2b, the card menu and the seen observer (#988)
- MD-2d, the feed preferences, migration 259 (#980)

Each is dark behind `NEXT_PUBLIC_MY_DAY_FEED`, or by the absence of a caller.

**Live in production since 2026-10-14.** The owner turned the feed on. So
`NEXT_PUBLIC_MY_DAY_FEED`, `MY_DAY_CACHE` and `MY_DAY_LOOP_ENABLED` are each
on, on the production box. Each `my_day.*` decide mode stays `off`, and
`MY_DAY_DECIDE_ORGS` stays empty. A new provider now reaches each member when
it deploys, so MD-3 adds `MY_DAY_PROVIDERS` (§5.8).

**MD-2c is BUILT, 2026-10-14.** The server half puts Handled in
`GET /shell/feed`, with no migration. The client half draws the Handled
group and the honest summary, dark behind `NEXT_PUBLIC_MY_DAY_FEED`. So MD-2
is complete.

**MD-5a is BUILT, 2026-10-14** (the read cache, no migration, dark behind
`MY_DAY_CACHE`, default off).

**MD-5b is BUILT, 2026-10-14** (the `my_day` loop, no migration, dark
behind `MY_DAY_LOOP_ENABLED`).

**MD-6a-1 is BUILT, 2026-10-14** (the features, the helpers and the table,
migration 262). Each `my_day.*` mode is `off` and `MY_DAY_DECIDE_ORGS` is
empty, so nothing asks `decide`.

**MD-6a-2 is BUILT, 2026-10-14** (the member pass of the loop, no
migration). The loop now calls the pass. With each `my_day.*` mode `off`,
the pass stops before it reads Redis, an identity or a row.

**MD-6a-3 is BUILT, 2026-10-14** (the measure read, no migration). It is an
admin read, and nothing calls it until MD-6b.

**MD-3a is BUILT, 2026-10-14** (the links, no migration and no flag). It
is live when it deploys, because the feed is live. A URL with no new
parameter opens as before.

**MD-3b is BUILT, 2026-10-14** (the seam of the new sources, no migration).
`MY_DAY_PROVIDERS` is empty by default, so the feed answer is the same as
before, byte for byte, except for one new field, `source_apps`. The list
gates no provider yet, and MD-3b adds no kind.

**MD-3c is BUILT, 2026-10-15** (the task kinds, no migration). The source
`tasks_followups` is gated, and `MY_DAY_PROVIDERS` is empty in production.
So the answer is the same as before, byte for byte. MD-3c also adds the
shared rows of all eleven MD-3 kinds, so MD-3d to MD-3f add only their
providers and reads.

**MD-3d is BUILT, 2026-10-15** (the mail kinds, no migration). The source
`email_followups` is gated, and `MY_DAY_PROVIDERS` is empty in production.
So the answer is the same as before, byte for byte, and so is the email
digest.

**MD-3e is BUILT, 2026-10-15** (the CRM kinds, no migration).
`MY_DAY_PROVIDERS` gates the source `crm`, and that list is empty in
production. So the answer is the same as before, byte for byte.

**MD-3f is BUILT, 2026-10-15** (people and chat, no migration).
`MY_DAY_PROVIDERS` gates the sources `people` and `chat`, and the list is
empty in production. So the answer is the same as before, byte for byte.

The rest of this file is not built. The open AGENT-SAFE tickets are MD-4,
MD-6b, MD-6c (the build only), MD-7, MD-8, MD-9 and MD-12.
Each ships dark.

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
| A member's access outside a request | `member_context(email)` resolves the same context a signed-in request gets. The WhatsApp channel calls it, and refuses a context in another org | `packages/acb_auth/acb_auth/deps.py:405` · `routes/whatsapp_channel/views.py:142-155` |
| Change events | An in-process bus. Projects emits `pm.*` and awaits it inline, and two sinks listen | `apps/services/ingestion/ingestion/event_hooks.py:32`, `:43` · `gateway/main.py:1623`, `:1630` |
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
| `unblocked` | Ready for you |
| `waiting_quiet` | Needs you |
| `stale_next` | For your information |
| `absent_on_due` | Needs you |
| `promise_due` | Now when the due time is 0 to 2 hours away, else Needs you |
| `no_answer` | Needs you |
| `crm_followup_due` | Now when the due time is 0 to 2 hours away, else Needs you |
| `deal_quiet` | For your information |
| `deal_close_passed` | Needs you |
| `colleague_out` | For your information |
| `agent_asks` | Needs you |

MD-3c added the last eleven rows (§5.9 item 3). Fence:
`test_feed_rank.py::TestTheGroupTable`.

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
  `/calendar?date=&at=` at the block's start (MD-3a). A slot act waits for
  MD-8.
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
| `unblocked` | A task I own was blocked, and its last blocker closed since my day start | Template: "Ravi closed the API task you waited on." | Open | None | MD-3c, built |
| `waiting_quiet` | A Waiting task whose `delegated_at` is more than 5 calendar days ago (§5.9 item 10.2). A nudge does not reset it | Template: "You have waited 6 days on Ravi." | "Nudge Ravi" (B), Snooze | None | MD-3c, built |
| `stale_next` | A Next task with no change for more than 14 days | Template | "Move to Someday" (A\*), Open | None | MD-3c, built |
| `due_at_risk` | The outlook read says a deadline will slip at the current load | Template from the outlook facts | Open, "Ask to move the date" (B) | None | MD-3 |
| `absent_on_due` | A due date of my task falls on a day **I** am absent | Template: "Due Friday. You are on leave that day." | "Move to <day>" (A\*) | None | MD-3c, built. It reads **my own** absences, `GET /people/me/absences` (`routes/people/selfservice.py:272`), never the HR conflicts read |

### 5.3 Email

| Kind | Signal | Why line | Body | Acts | AI | Phase |
|---|---|---|---|---|---|---|
| `needs_reply` | Today's provider | Template: "Asked yesterday at 4:12 PM." | The last message, 3 lines | Open thread, Snooze | None | MD-1 |
| `reply_drafted` | `needs_reply` and a draft in `email_ai_drafts` (card B) | Template, plus the promise when the email app holds one for the thread | The full draft and its attachments. For a thread of 6 messages or more, the gist above it | "Review and send" (B), "Edit draft" | The draft is the email app's. The gist is a text composition (MD-6c) | MD-4 |
| `promise_due` | A promise I made in sent mail falls due (card B's "You said you would send it by Friday") | Template from the promise | — | Done (A), Open thread | **None in My Day.** The email app's own detector found the promise and filed it as a task (`capture_email.py:1007`, `:1176`). The provider reads the open ones through `digest.promise_tasks`, which reads only a commitment task (§5.9 rule 10.1) | MD-3d, built |
| `no_answer` | A mail I sent has waited longer than the mailbox's follow-up wait, 3 business days by default, and less than 30 days | Template | — | "Follow up" (opens the thread), Snooze | None. It reads the email app's AWAITING threads through `digest.awaiting_threads` | MD-3d, built |

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
| `crm_followup_due` | An activity I own has `due_at` today or earlier | Template | Open deal | None | MD-3e, BUILT. §5.9 item 13 holds the rule. Feed only, until a §7.2 amendment |
| `deal_quiet` | A deal I own passed its stage's `max_dwell_days` | Template: "No activity for 12 days in Proposal." | "Log a call" (opens the CRM form), Snooze | None | MD-3e, BUILT. §5.9 item 13 holds the rule and the text |
| `deal_close_passed` | A deal I own passed its close date and is open | Template | "Move the date" (opens the CRM form) | None | MD-3e, BUILT. §5.9 item 13 holds the rule |
| `lead_replied` | A lead I own replied by mail | Template | Open thread | None | MD-9 |

### 5.6 People, approvals, agents and WhatsApp

| Kind | Signal | Why line | Acts | AI | Phase |
|---|---|---|---|---|---|
| `approval` | Today's provider, org admin only | Template from the action name | Open Approvals | None | MD-1 |
| `colleague_out` | A person I have a block with today is absent | Template | Open the day | None | MD-3, **only for a member who passes `can_read_hr_fields`** (`routes/tasks/core.py:75`). The provider calls the People app's own gated read. A plain member gets no row, and a negative test proves it. BUILT in MD-3f, source `people` (§5.9 item 14) |
| `agent_done` | A chat run that I started finished while I was away | Template from the run | Open chat | None | MD-3. It reads WS-51's run state |
| `agent_asks` | A run waits for my answer (`chat_pending_ask`) | Template | Open chat | None | MD-3. Feed only, until a §7.2 amendment. BUILT in MD-3f, source `chat` (§5.9 item 14) |
| `whatsapp_waiting` | A WhatsApp thread waits for me | Template | Open thread | None | MD-9 |

### 5.7 The cards about the day itself

| Kind | When | What it says | AI | Phase |
|---|---|---|---|---|
| `brief` | The first visit of the day | The header line of card D. "3 things need you. The rest is handled." Code writes it from the counts. `my_day.reason` picks one lead line from the templates, for example "Dana waits on the Q3 proposal" | None beyond the common pass | MD-7 |
| `while_away` | The first visit after 4 hours or more away | The counts of what came in and what closed, and the top three | None | MD-7 |
| `day_end` | Within 60 minutes of `day_end_hour` | "4 open items left today." Links to Calendar's `EndOfDayReview` and to rollover | None | MD-7 |

### 5.8 How MD-3 runs (decided 2026-10-14)

The MD-3 audit gave NO-GO, with eleven gaps. These answers close them.
Where this section and an earlier one differ, this section wins.

**1. A flag for each new source.** The feed is live, so a new provider
reaches each member when it deploys. `MY_DAY_PROVIDERS` is a settings list
of source ids, empty by default. It is read in one place and is in
`env_guard.py`. It filters `feed_only_providers()` when the feed runs.

The kinds stay registered, so the prefs and the state POST accept them.

With the list empty, the answer is the same as today, byte for byte, except
for the new `source_apps` field of item 6.

The gateway caches its Settings for the life of the process. So a change of
the list takes effect when the gateway restarts, and the restart is the act
of the flip. The list is part of `features_hash`, so the cache serves no
answer from before the restart after it.

**2. The cache.** These rows join §8.4:

- `features_hash` also covers `admin:members:read`. A member who loses the
  grant then loses the HR cards at the next read.
- `PATCH /crm/settings` bumps the org version, because CRM access is a row.
- The member's own absence writes bump the member version. An admin's
  absence write bumps the org version.
- A `chat_pending_ask` insert bumps the version of the member it asks.
- These wait for the edge, or 600 seconds at most: CRM data, the Zoho
  mirror sync, the AWAITING status, and the quiet thresholds.

**3. The budget.** The feed-only providers run two at a time at most, under
a semaphore (`FEED_CONCURRENCY = 2`). So each read of the feed holds two
connections of the pool at most for them. Each provider has its own budget
of 2 seconds, and the budget starts when the provider gets its slot. A slow
provider then fails alone.

For the My Day pass of the loop, only one provider runs at a time
(`FEED_PASS_CONCURRENCY = 1`). The loop runs the pass for three members at
once, so two slots for each would hold six of the nine connections of the
pool.

One ceiling bounds the whole run: `FEED_TOTAL_S = 2.3`, from the start. At
the ceiling, each provider that still waits for a slot or still runs reads
`failed`. The cache keeps no answer with a failed source, so the next poll
tries again.

A cancel cancels each provider. Then it waits 0.1 seconds at most
(`CANCEL_WAIT_S`), so that each provider closes its session first. The
wait also runs when the ceiling fires.

The worst case of a read is 4.0 + 2.3 + 0.1 + 1.5 = 7.9 seconds: the needs
run, this run, its cancel wait and the state join. That is under the 8 seconds of the BFF route,
so a slow database gives a partial answer and not an error. A ceiling of 2.4 with a
wait of 0.5 made 8.4, past the limit. Fence:
`test_shell_feed.py::TestTheBudgetSum`.

The MD-3b review decided these rules on 2026-10-14. Pool exhaustion caused
EMAXCONNSESSION on 2026-09-20.

**4. Absent, not failed.** A refusal is not a failure. These each read as
`absent`: CRM off, CRM access refused, and no People row. A provider raises a
sentinel, `SourceAbsent`, to say so. `Provider` gains `permission: str |
None` for a grant such as `admin:members:read`.

**5. The rows of each kind.** Each new kind gets these:

- a `BASE` number, and an old group (§4.3)
- a why template with its `why_key`, and its `STABLE_FACTS`
- a `signal_id`, a label in `FEED_KIND_LABELS`, and a `CLOSES` entry

The ids:

- every task kind: `tasks:<id>`, `promise_due` and `absent_on_due` too
- CRM: `crm:<deal|lead|contact|organization>:<id>`
- chat: `chat:<thread_id>`
- `colleague_out`: `calendar:<date>`

`STATE_FACTS` does not grow in MD-3.

**6. The app ids.** §14.1a gains `crm`, `people` and `chat`. Each source has
an id of its own, for example `email_followups`. So the answer gains
`source_apps`, the app of each source, and the client maps a failed or
capped source to its app through it. A needs provider cannot
give a new kind (`needs.py:868`).

**7. The sources, defined:**

- `crm_followup_due`: an open activity (`completed_at IS NULL`) due by the
  end of the member's today, where `lower(created_by)` is the member. The
  card links to the record the activity is on.
- `deal_quiet`: the template reads "In Proposal for 12 days", because the
  data is the time in the stage. It is empty in Mirror mode.
- `colleague_out`: a co-assignee of a task that has a block today.
- `unblocked`: a blocker closed since the member's day start. The card
  names the blocker only when the member can read it.
- `absent_on_due`: a task due on one of the member's absence days, in the
  next 14 days.
- `promise_due` and `no_answer` read through owner-checked wrappers in
  `digest.py`, as `needs_reply_threads` does. Their errors raise.

**8. Deferred to MD-9.** `agent_done` has no source, because no table
records a run that ended. `due_at_risk` has no source for one task.

**9. The links.**

- `/calendar?date=YYYY-MM-DD&at=HH:MM` opens the day at that date and
  scrolls to that time. A bad value is ignored.
- `/approvals?action=<id>` scrolls to that row and marks it. When the row
  is not pending, the page says so above the queue.
- The feed's signal links change, and the needs row keeps `/approvals`
  (§4.4).
- The acts that change: `block_overlap`, `day_overbooked` and
  `no_plan_yet`.
- "Block time" on a `due_today` card opens the member's today too (MD-3a
  review). A card keeps one link for each place, and the main kind's link
  wins. The time of a Calendar link is not part of its place. So a folded
  overlap on the same day adds no second Calendar link. An overlap on
  another day keeps its own link.

**10. The pills move to MD-4,** which draws the bodies that use them. MD-4
also fixes `pillLinkName`, which says "in Projects" for each kind.

### 5.9 How MD-3c to MD-3f run (decided 2026-10-15)

Where this section and an earlier one differ, this section wins.

The audit of 2026-10-15 checked MD-3c to MD-3f against the code at
`57fa805a1`. These answers close each gap that it found. The owner and the
coordinator accepted them on 2026-10-15.

#### 1. The source ids, and the carry

| Slice | Source id | `Provider.app` | Carry gap |
|---|---|---|---|
| MD-3c | `tasks_followups` | `tasks` | Yes. The needs provider owns the id `tasks` |
| MD-3d | `email_followups` | `email` | Yes. The needs provider owns the id `email` |
| MD-3e | `crm` | none, the id is the app | No |
| MD-3f | `people` for `colleague_out`, `chat` for `agent_asks` | none | No |

The carry of §6.1 keys on the source id and filters cards by `card.app`
(`noteOk`, `feed.ts:442-453`). So a source whose id is not its app id
carries no card. MD-3c closes this gap:

- **The gateway.** A card gains `source`, the source id of its main signal.
  The gateway sends `source` only when it differs from `app`. So with an
  empty `MY_DAY_PROVIDERS`, the body stays the same, byte for byte.
- **Where.** The loop over `found.items()` (`feed.py:630-639`) tags each
  signal with its source. `build_cards` copies the source of the main signal
  to the card.
- **The client.** The filter of `noteOk` becomes
  `(c.source ?? c.app) === source`.
- **§14.1a** names the new optional card field `source`.
- **Fence.** `feed.test.ts`: a failed `tasks_followups` carries its own cards
  for 5 minutes, and it carries no card of the `tasks` source.

#### 2. `BASE` (`feed_rank.py:56`)

Each new kind goes into a gap of the table. Each one stays under 44, so each
needs kind still starts above it.

| Kind | `BASE` |
|---|---|
| `overdue` | 60 |
| `approval` | 56 |
| `due_today` | 52 |
| `notification` | 48 |
| `needs_reply` | 44 |
| `agent_asks` | 43 |
| `promise_due` | 42 |
| `crm_followup_due` | 41 |
| `block_overlap` | 40 |
| `absent_on_due` | 39 |
| `unblocked` | 38 |
| `waiting_quiet` | 37 |
| `block_soon` | 36 |
| `no_answer` | 35 |
| `deal_close_passed` | 34 |
| `deal_quiet` | 33 |
| `day_overbooked` | 32 |
| `stale_next` | 30 |
| `no_plan_yet` | 28 |
| `colleague_out` | 26 |

The examples of §4.5 stay true. An overdue task that falls on my absence is
one `overdue` card. `colleague_out` at 26 folds under each day card.

#### 3. The old-path group of each new kind

These rows join the table of §4.3 and `feed_rank.group`.

| Kind | Old-path group |
|---|---|
| `unblocked` | Ready for you |
| `waiting_quiet` | Needs you |
| `stale_next` | For your information |
| `absent_on_due` | Needs you |
| `promise_due` | Now when the due time is 0 to 2 hours away, else Needs you |
| `no_answer` | Needs you |
| `crm_followup_due` | Now when the due time is 0 to 2 hours away, else Needs you |
| `deal_quiet` | For your information |
| `deal_close_passed` | Needs you |
| `colleague_out` | For your information |
| `agent_asks` | Needs you |

#### 4. The state facts

`STATE_FACTS` (`state.py:89`) does not grow. Each kind uses only the names
below. A fold must not change the state fingerprint (§6.8 item 1), so each
task kind keeps to `due_at`.

**The rule in code** (MD-3d fix round 1, 2026-10-16). A folded signal of a
feed-only kind adds no state fact to its card (`feed._states_entity_facts`).
The main signal keeps its own state facts, and a folded signal still adds
its other facts. A folded needs kind keeps its state facts, because a new
note is a change of the task. The fence covers every kind of `BASE`
(`test_feed_fold_fingerprint.py`).

| Kind | Its state facts |
|---|---|
| The task kinds and `promise_due` | `due_at` only, the due time of the task |
| `no_answer` | `waiting_since`, the time of the last message, and `message_id` |
| `crm_followup_due` | `due_at`, the earliest open due time on the record |
| `deal_quiet` | `waiting_since`, set to `status_changed_at` |
| `deal_close_passed` | `due_at`, the close date at 00:00 in the zone of the member |
| `agent_asks` | `waiting_since`, the newest `asked_at` on the thread |
| `colleague_out` | None. The id is the day, so a dismiss holds for that day |

Two names are not allowed:

- `waiting_quiet` names the start of its wait `delegated_at`. It never uses
  `waiting_since`.
- `promise_due` keeps the mail id in the link of its act only. It never puts
  it in `message_id`.

`STABLE_FACTS` (`feed.py:447`) gains `delegated_at`, `blocker_ids`,
`absence_day`, `out` and `request_ids`.

#### 5. `CLOSES` (`feed.ts:733`)

- `done` also closes `waiting_quiet`, `stale_next`, `unblocked`,
  `absent_on_due` and `promise_due`.
- The other new kinds have no tap act. So they get no entry.

#### 6. `FEED_KIND_LABELS` (`feed.ts:875`)

| Kind | Label |
|---|---|
| `waiting_quiet` | Waiting too long |
| `stale_next` | No change |
| `unblocked` | Unblocked |
| `absent_on_due` | Due while you are away |
| `promise_due` | You promised |
| `no_answer` | No answer yet |
| `crm_followup_due` | Follow-up due |
| `deal_quiet` | Quiet deal |
| `deal_close_passed` | Close date passed |
| `colleague_out` | Colleague away |
| `agent_asks` | Waiting for your answer |

#### 7. The budget, and the rule for the flip

When each source is listed, the feed has 6 feed-only providers. They are
Calendar and the 5 new sources. They run in 2 slots under the ceiling of 2.3
seconds, so 3 rounds get about 0.76 seconds each.

**The rule for the flip.** Before a source joins `MY_DAY_PROVIDERS` on a box,
measure it on that box:

- The p95 of each new provider is 0.5 seconds or less.
- The sum of the p95 of all the feed-only providers is 3.6 seconds or less.

The message of the flip records the measure. MD-3c adds `elapsed_ms` to the
provider log line in `_attempt` (`needs.py:1068`), so the measure can come
from the log.

**The fence in a test** counts the sessions that each provider opens, with a
counting fake:

| Source | Sessions at most |
|---|---|
| `tasks_followups` | 4 |
| `email_followups` | 1, plus 2 for each mailbox |
| `crm` | 2 |
| `people` | 3 |
| `chat` | 1, plus 1 for each thread, and 15 threads at most |

**A note for MD-6b.** The loop pass runs one provider at a time under the
same ceiling of 2.3 seconds (`feed.py:620-624`). So the pass sees each source
only while the sum of the p95 is 2.3 seconds or less. Today the pass stops at
its gate, because each `my_day.*` mode is `off`. MD-6b must solve this before
it turns `decide` on.

#### 8. The old-path weight

§7 says that the old path adds 10 for a promise and 15 for a CRM customer.
`feed_rank.weight` (`feed_rank.py:121`) reads only `priority` today.

- MD-3d adds 10 when `promise_due` is on the card, as the main kind or
  folded in.
- The 15 for a CRM customer waits for MD-9, because the word customer has no
  definition yet.

#### 9. Anchors, corrected on 2026-10-15

| The spec cites | The code is at |
|---|---|
| `needs.py:868` (§5.8 item 6) | The refusal of a new kind, `needs.py:939-942` |
| `needs.py:159`, `KIND_ORDER` | `needs.py:203` |
| `needs.py:175`, `YIELDING_KINDS` | `needs.py:207` |
| `needs.py:150`, `DEFAULT_LIMIT` | `needs.py:182` |
| `needs.py:845-847` | The import of `ADMIN_PERMISSION` at `needs.py:174`, and `held_by` at `needs.py:918-925` |
| `_run_bounded` at `needs.py:1112` | `needs.py:1113` |
| `FEED_TOTAL_S` at `feed.py:125` | `feed.py:126` |
| `source_apps` at `feed.py:675` | `feed.py:676` |
| `get_user_settings` at `tasks/settings.py:393` | `tasks/settings.py:402` |
| `my_calendar` at `personal.py:2028` | `personal.py:2104` |
| `capture_email.py:1176` | The function starts at `capture_email.py:1178`, after its decorator |

#### 10. Rules that the code check added

1. **A promise is a commitment task.** `_digest_commitments` (`digest.py:293`)
   calls `items_by_origin` with no `commitment=True` (`digest.py:321-322`). So
   it returns each task captured from the mailbox. The `promise_due` read
   must pass `commitment=True`. A promise is a task with
   `origin.commitment = true` (`capture_email.py:1278`, `item_lens.py:392-393`).
2. **`waiting_quiet` uses the one rule of the app.** That rule is
   `item_lens.STALE_WAITING_DAYS = 5` (`item_lens.py:98-104`). A task is quiet
   when more than 5 calendar days passed since `delegated_at`. A nudge does
   not reset it. The client keeps the same rule in `app/tasks/lib/waiting.ts`.
   The words "5 working days" of §5.2 no longer apply.
3. **The link of `agent_asks` exists.** It is
   `/chat?do=open-chat&fill.session=<thread id>` (`runActivity.ts:299-304`,
   `app/chat/page.tsx:1383-1390`).
4. **The link of a CRM record exists.** It is `/crm?tab=<tab>&<param>=<id>`
   (`shell/search.py:125-130`, `:163`). MD-3e moves it into one helper,
   `crm_href`, and copies it nowhere.
5. **The HR gate of `colleague_out` stays.** The assignee picker and the
   directory show "Away" to members with no HR grant
   (`projects/assignees.py:194`, `people/directory.py:180`). The feed keeps
   its stricter gate. The owner decided this on 2026-10-15.
6. **The cap of the preferences is full.** After MD-3 the registry holds 20
   kinds, and `prefs.MAX_KINDS` is 20 (`prefs.py:61`). MD-4 adds more kinds,
   so MD-4 raises the cap.

#### 11. MD-3c, the task kinds

Each kind keys on `tasks:<task id>` and links to `task_href`. None has an
`urgent_at`. The provider gives 15 signals at most, by `BASE` and then by
age, and it sets `capped`.

| Kind | It fires when | `why_key` and text | Act |
|---|---|---|---|
| `waiting_quiet` | The effective disposition is `WAITING`, and `p.delegated_at` is more than `item_lens.STALE_WAITING_DAYS` days ago. A task with no `delegated_at` never fires | `waited`: "You have waited {n} days on {who}." With no name, `waited_anon`: "You have waited {n} days for this." | "Nudge {who}", class B, a link to the task |
| `stale_next` | The effective disposition is `NEXT` (`effective_disposition`, `personal.py:298`), and `greatest(t.updated_at, p.updated_at)` is older than `analytics.STALE_DAYS`, 14 days (`analytics.py:116`) | `no_change`: "No change for {n} days." | "Move to Someday", class A\*, a link to the task |
| `unblocked` | An open task of my lens has an incoming `blocks` link whose source closed at or after my day start. No incoming `blocks` link comes from a task that is still open (`relations.py:61`) | `unblocked`: "{who} closed {blocker}, which you waited on." When I cannot read the blocker, `unblocked_hidden`: "A task that blocked this is closed." | "Open" |
| `absent_on_due` | An actionable task of my lens has its local due date on a day of my full absence, `away` or `holiday`, from today to today + 14. A `partial` absence never fires | `absent_on_due`: "Due {when}. You are away that day." The folded clause: "You are away on its due day." | "Move the date", class A\*, a link to the task |

`{n}` counts whole local days. `{who}` of `waiting_quiet` is
`_who(p.waiting_on)`. `{who}` of `unblocked` is the actor of the newest
`status_change` of the blocker (`_CLOSED_BY_SQL`, `personal.py:1776`). A
blocker is readable when `task_visibility_clause` passes with the own grants
of the member, as `_CHILD_VISIBLE` does (`personal.py:1255`). The facts of an
unreadable blocker hold no title and no id.

The sources:

- `personal.my_quiet_tasks(user, *, limit)`, new. It is `_MY_TASKS_SQL` with
  `DEFERRED_CLAUSE`, `ACTIONABLE_CLAUSE` and the time filters. The rule of
  `effective_disposition` then narrows the rows. `my_due_tasks`
  (`personal.py:1723`) is the model.
- `personal.my_unblocked_since(user, since, *, limit)`, new. It adds an
  `EXISTS` over `pm_task_links` through `idx_pm_task_links_target_task_id`
  (`146_projects.sql:290`).
- `personal.my_due_ahead(user, start, end, *, limit)`, new. It runs only when
  an absence falls in the window. It uses `idx_pm_tasks_due_at`.
- `selfservice.my_absences(user=)` (`selfservice.py:272`) gives my absences.
  A 404 means that I have no People row (`selfservice.py:137-154`). The
  provider reads it as no absence. It is not `SourceAbsent`, because the other
  three kinds still hold. Any other error raises.

Each read binds the tenant through `MY_TASKS_FROM` and `my_tasks_binds`, under
FORCE RLS. Each one reads only the lens of the member.

The gate: `features=("projects",)`, `gated=True`, `app="tasks"`, and no
Handled read.

The cache:

- A task write bumps a version already, through `pm.*` and §8.4 item 5.
- `POST` and `DELETE /people/me/absences` (`selfservice.py:280`, `:299`) call
  `bump_org`. So the card of `colleague_out` of a colleague changes at once.
- The time thresholds wait for the edge, or 600 seconds at most.

The carry: MD-3c builds item 1 of this section.

**The facts of each template** (MD-3c, 2026-10-15). MD-3c writes the why
template of each of the eleven kinds (`feed.py`, `_md3_why`). So a later
slice gives these facts and no others:

| Kind | Facts that its template reads |
|---|---|
| `waiting_quiet` | `waited_days`, `who`. Also `delegated_at` |
| `stale_next` | `stale_days`. Also `changed_at` |
| `unblocked` | `blocker`, `who` and `blocker_ids`, only for a blocker that I can read. Also `unblocked_at` |
| `absent_on_due` | `absence_day`, the local date |
| `promise_due` | `due_at` |
| `no_answer` | `waiting_since`, `who` |
| `crm_followup_due` | `due_at`, `subject`, and `all_day` for a task |
| `deal_quiet` | `stage`, `stage_days`, `stage_limit` |
| `deal_close_passed` | `due_at` |
| `colleague_out` | `out`, a list of names, and `task`, a title |
| `agent_asks` | `waiting_since` |

⚠️ **No task kind of MD-3c carries a state fact, also not `due_at`.** A
notification card has no `due_at`. A task kind that carried one would change
the state fingerprint of that card when it folds in (done-when 11). So
`absent_on_due` names its day in `absence_day`, and the other three kinds
name no due time. This narrows the table of item 4, which allows `due_at`.

#### 12. MD-3d, the mail kinds

| Kind | It fires when | `why_key` and text | Acts | `signal_id` |
|---|---|---|---|---|
| `promise_due` | An open commitment task (rule 10.1) of a mailbox that I own is due in the next 3 days, or its due time passed. The window is `horizon_days=3` of the digest (`digest.py:294`). A promise with no due date never fires. The task is in my lens, and I hold `feature:projects` | `promised`: "You promised this by mail. It is due {when}." The folded clause: "You promised this by mail." | "Done", a tap. "Open thread", a link to `_email_href(message id, account id)` | `tasks:<task id>`. Its `urgent_at` is `due_at` |
| `no_answer` | A live thread in `AWAITING` has `last_message_at` before the cutoff and after now − 30 days (`_FOLLOW_UP_MAX_AGE_DAYS`, `followups.py:69`) | `no_answer`: "You wrote {when}. {who} has not answered." | "Follow up", a link to `_email_href(message id, account id)` | `email:<account id>:<thread id>` |

The cutoff is `_business_days_cutoff(n)` (`replyzero.py:2825`). `n` is the
`follow_up_awaiting_days` of the mailbox when it is more than 0, and else 3.

The sources are two new wrappers in `digest.py`. Each one calls
`_assert_account_owner` (`email/core.py:861`) first, as `needs_reply_threads`
does (`digest.py:238`). Each one raises on an error.

- `awaiting_threads(user, account_id, limit, *, before, after)`.
  `_digest_thread_list` (`digest.py:174`) gains the optional bounds `before`
  and `after`, in SQL. With no bound, the digest does not change. The bounds
  must be in SQL, because the list is the oldest first and stops at 15.
- `promise_tasks(user, account_id, *, horizon_days, limit)`. The body of
  `_digest_commitments` moves into a core that raises, with
  `commitment=True`. `_digest_commitments` keeps its best-effort wrapper for
  the digest.
- The mailboxes come from `feed_mailboxes` (`accounts.py:361`), which reads
  only the mailboxes of the member. The provider skips a mailbox with
  `in_all_inboxes` false. Each mailbox gets `MAILBOX_TIMEOUT_S`. A failed
  mailbox costs only its own rows, as in `_email`.

The gate: `features=("email",)`, `gated=True`, `app="email"`. The provider
gives `promise_due` only to a member who holds `feature:projects`.

The cache: no new bump. A new mail bumps the member version (§8.4 item 19).
The `AWAITING` status and the thresholds wait for the edge, or 600 seconds
at most.

The carry: MD-3d uses the `source` field of item 1.

The weight: MD-3d adds 10 when `promise_due` is on the card (item 8).

#### 13. MD-3e, the CRM kinds

| Kind | It fires when | `why_key` and text | Act |
|---|---|---|---|
| `crm_followup_due` | An activity has `completed_at IS NULL`. Its type is not `status_change` or `system`. `lower(created_by)` is the member. It is due before the tomorrow of the member, by the one CRM day rule below | `followup_due`: "{Subject} is due {when} at {clock}." With no subject: "A follow-up is due {when} at {clock}." A task has no clock: "{Subject} is due {when}." and "A follow-up is due {when}." | "Open {noun}" |
| `deal_quiet` | An open deal that I own passed the `max_dwell_days` of its own stage. A stage with no limit never fires. No override applies | `in_stage`: "In {stage} for {n} days. The stage limit is {limit} days." | "Log a call", a link to the record |
| `deal_close_passed` | An open deal that I own has `expected_close_date` before the local today of the member | `close_passed`: "The close date was {when}, and the deal is still open." | "Move the date", a link to the record |

- An open deal has a status type outside `CLOSING_TYPES` (`crm/core.py:99`).
  "I own" means that `lower(owner_email)` is the member.
- One record gives one card. An activity on more than one record takes the
  first of deal, lead, contact and organization.
- `signal_id` is `crm:<deal|lead|contact|organization>:<id>`. The link is
  `crm_href` (rule 10.4).
- **The one CRM day rule** (`crm_platform.md` §19.7, fix round 1). A task
  holds a DATE at 00:00 UTC, because the Zoho sync and the composer both
  write it so. So the read compares the UTC date of a task with the local
  date of the member. A call or a meeting keeps its real time, and the read
  compares it with the local midnight of the member.
- A task card has `all_day`, and its `due_at` is 00:00 of its date in the
  zone of the member, as `deal_close_passed` does. It has no `urgent_at`,
  so a midnight never lifts it to Now. For a call or a meeting, `urgent_at`
  is its `due_at`. The zone comes from `personal.stored_zone`.
- The read keeps the follow-ups due from the local today first, and then
  the older ones. So many stale open tasks cannot cut a follow-up of today.
- `stage_days` of `deal_quiet` rounds the time in the stage up, the rule of
  the SQL. So on its first day past a limit of 10, the text says 11 days.
- In Mirror mode (`is_mirror_mode`, `crm/core.py:1818`), `deal_quiet` is
  empty and the source still reads `ok`. A follow-up still shows, because the
  sync writes the mapped member into `created_by` (`crm_sync/engine.py:1097`).

The sources:

- `crm/activities.py::my_due_activities(db, user, today, tz, limit)`, new.
  It reads through `idx_crm_activities_due_at` (`144_crm.sql:452`).
- `crm/aggregate.py::my_overdue_deals(user, limit)`, new. It uses
  `_OVERDUE_FROM` (`aggregate.py:323`) with an owner clause, on
  `idx_crm_deals_owner_email`.
- `crm/aggregate.py::my_close_passed_deals(user, today, limit)`, new.
- The three reads share one `tenant_session`, and each statement also names
  the tenant. The provider never calls `stage_dwell` (`aggregate.py:349`),
  because that read covers the whole organization.

The gate: `features=("crm",)`. The provider raises `SourceAbsent` when
`flags.crm_org_allowed` is false. It also raises `SourceAbsent` on the 403 of
`access.require_crm_access` (`access.py:98`).

The cache: `PATCH /crm/settings` (`crm/settings.py:320`) calls `bump_org`.
CRM data and the Zoho mirror sync wait for the edge, or 600 seconds at most.

The carry: none, because the source id is the app id.

#### 14. MD-3f, people and chat

| Kind | It fires when | `why_key` and text | Act |
|---|---|---|---|
| `colleague_out` | A co-assignee of a task with a block today has a full absence on the local today of the member. The co-assignee is not the member and not `agent:*` | `colleague_out`: "{name} is away today. You share {task} with them." The folded clause is the same sentence | "Open the day", `day_href(today)` |
| `agent_asks` | The member has a question that waits, in a room where the member can still send | `asks`: "Asked {when} at {clock}. The run waits for your answer." | "Open chat", `/chat?do=open-chat&fill.session=<thread id>` |

`colleague_out`:

- The title is "{name} is away today", or "{n} colleagues are away today".
- `signal_id` is `calendar:<date>`, and `app` is `people`. `expires_at` is
  the next local midnight.
- The blocks and the assignees come from `my_calendar` for today
  (`personal.py:2104`). It attaches `assignees` (`personal.py:2185`). The
  zone comes from `get_user_settings`.
- The absences come from `people/absences.py::away_on(user, emails, day)`,
  new. It raises 403 unless `can_read_hr_fields` (`tasks/core.py:75`) is
  true. It is one statement over `people` and `people_absences`, for the
  given day. A `partial` absence does not count.
- `away_today` (`absences.py:242`) is not the source. It uses `CURRENT_DATE`
  of the database, which gives the wrong day near midnight for a member in
  India.

`agent_asks`:

- The title is "{agent} asked you a question".
- The card shows no question text. A model wrote that text, and §4.1 allows
  no such text in a signal.
- `signal_id` is `chat:<thread id>`. The provider skips a thread whose id
  fails `SIGNAL_ID_PATTERN`. `expires_at` is the `expires_at` of the row.
- The rows come from `_my_waiting_asks(org, me)` (`chat.py:1217`), one for
  each thread. That function reads `pending_ask.waiting_asks`
  (`orchestrator/pending_ask.py:286`) through `acb_graph.tenant_session`.
- The provider keeps a thread only when `_resolve_room(...).can_send` is true
  (`agent.py:1110`). That is the rule of `list_pending_asks` (`chat.py:1246`).
  It reads 15 threads at most. A `None` answer is a database error, and the
  provider raises.

The gates:

- `people`: `features=("people", "tasks", "projects")` and
  `permission="admin:members:read"`. The features hash covers that grant
  already.
- `chat`: `features=("chat",)`. With `CHAT_DURABLE_ASKS` off, the provider
  raises `SourceAbsent`.

The cache:

- `POST` and `DELETE /people/{id}/absences` (`absences.py:219`, `:230`) call
  `bump_org`.
- `_record` (`pending_ask.py:371-383`) calls
  `cache.bump_member(org, actor_email)` after its insert.
- `settle` (`pending_ask.py:433`) makes the same call. So an answered
  question leaves the feed at once.

The carry: none, because each source id is its app id. A `colleague_out`
that folds into a Calendar card carries with the `calendar` source.

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
`gateway/main.py:1623` and `:1630`.

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
   (`deps.py:405`). That is the request path's own resolve, so the features,
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

### 8.4 How MD-5 runs these parts (decided 2026-10-14)

The MD-5 audit found fifteen gaps in §8.1 to §8.3. These answers close them.
Where this section and an earlier one differ, this section wins.

**1. Two versions, not members from the payload.** Of the `pm.*`
events, only `pm.task.assigned` names its members. So the sink cannot find the
members of an edit. There are two versions instead:

- An org version, `key("my_day", "ver")`. The sink bumps it on each `pm.*`
  event. It takes the org from `acb_common.db.current_tenant()`, or else from
  `payload["organization_id"]`. One edit then refreshes each member of the
  org once. That is the cost, and it is bounded by the poll.
- A member version, `key("my_day", "ver", <member>)`. These bump it: the
  member's own `POST /shell/feed/state` and `PUT /shell/feed/prefs`, and the
  post-sync hook of each mailbox the member owns.

**2. The edges.** The next time edge is the earliest of these:

- each block start minus 30 minutes, for the member's blocks of today, also
  outside the 30-minute window. The Calendar provider hands it back in a new
  field, `Found.next_edge`.
- each `urgent_at`, each `expires_at`, and each due time minus 2 hours
- each `snoozed_until` of the member's state rows
- the day start, the day start plus 60 minutes (the grace of `no_plan_yet`),
  and the day end minus 60 minutes

**3. Countdowns.** A card can count down in its why line, as "Starts in 20
min" does. While the answer holds one, it is valid for 60 seconds at most.
The why keys that count down are `starts_in` and `due_soon`.

**4. The keys,** all through `tenant_redis.key()`. The active set holds the
session's exact address. Each key that names a member uses the lower case.
(Corrected 2026-10-14 by MD-6a-2 fix round 1. The member tables key on the
exact address, so a lower-case set lost a member's rows.)

- `key("my_day", "answer", <member>, <limit>, <features hash>)`: one JSON
  value that holds the answer, its edge and the two versions. The TTL is the
  edge minus now, and 600 seconds at most. The features hash is of the
  member's effective feature set, so a revoked app stops its cards at once.
- `key("my_day", "active")`: a sorted set of members, scored by the time of
  their last feed read.

**5. The ETag.** The gateway sends an ETag, and item 16 says what it holds.
The BFF route passes `If-None-Match` on, and passes a 304 back as a 304.
`fetchFeed` keeps the tag. On a 304 it keeps its cached answer, stamps
`fetchedAt` again, and writes the needs view again.

A 304 says that the body is the same as the body the client holds. It does
not say that the body is new. The writes of this file bump a version, and
item 8 names the writes that wait. The new stamp also fixes the 5-second
carried needs.

These writes of a member's own view emit no `pm.*` event, so each one bumps
a version after its commit (MD-5a review):

- `PATCH /projects/tasks/{id}/personal` (a disposition, a defer, a block) and
  `POST /projects/notifications/read` bump the member's version.
- `POST /projects/tasks/{id}/nudge` bumps the version of the person whom the
  nudge reaches.
- `PUT /projects/tasks/{id}/assignees` with removals and no additions bumps
  the org version. An addition already emits `pm.task.assigned`.

None of them emits a new event, so the workflow triggers do not change.

**6. Redis fails open.** With Redis down, the read runs the providers as
today. The sink and the active-set write log the failure and go on. The sink
waits 50 ms at most, because Projects awaits `emit` inline in each write.

**7. Dark by a server flag.** `MY_DAY_CACHE`, default false, is a field in
`packages/acb_common/acb_common/settings.py`. It gates the cache,
the sink and the bumps. It is read in one place, and a test fences it. With
it off, every read runs the providers, as today.

**8. What waits.** These change the answer, and bump no version. Each waits
for the edge, or 600 seconds at most:

- an approval
- a mailbox the member reads but does not own
- a Calendar change that is not a task edit
- a Handled entry of another member

**The settle time.** Projects emits some `pm.*` events inside the
transaction of the write: `create_personal_task`, `complete_for_member` and
`reopen_if_closed`. A read between the event and the commit reads the old
rows. So the org version is a time stamp, and a read stores no answer while
the org version is less than 2 seconds old (`SETTLE_S`). The read still
serves its answer.

A transaction slower than 2 seconds can still store a stale answer, until the
edge or 600 seconds at most. The fix for the long term is to emit after the
commit. #493 put the emit of `complete_for_member` inside the transaction on
purpose, so that change needs its own decision.

**9. Paging is not in MD-5.** The column keeps `limit=40`. The cache keys on
`limit`, so a later ticket can page with no change to the cache.

**10. The loop's switch.** `MY_DAY_LOOP_ENABLED`, default false. It differs
from the default-on rule of `gateway/main.py:36-50`, which was written for
loops that already ran. `CRM_MIRROR` is the default-off precedent.

**11. What the loop does before MD-6a.** It trims the active set, and it runs
the sweep. MD-5 builds no member pass. MD-6a builds it with its first judge,
and items 12 and 24 bind it. The morning pass and the "dirty" rule move to
MD-6a too.

**12. The member pass, when it runs.** Each member runs in a task of its own,
because `member_context` binds a tenant and never releases it. Before
`member_context`, the loop checks the member by item 24. A member who left is removed from the active set and
skipped. So `member_context` never files a sign-in request for a departed
address.

**13. Orgs and members.** The loop reads `organization`, as a new entry in
`H2_TENANT_DISCOVERY_SITES` modelled on the CRM loop. For each org, inside
`organization_scope(org)`, it trims the active set to the last 3 days and
reads it. Three members run at once at most.

**14. The sweep.** One statement for each org each day, inside
`tenant_session(org)`:
`DELETE FROM my_day_card_state WHERE updated_at < now() - interval '30 days'`.
It needs no migration. MD-6a adds the same sweep of `my_day_judgments`.

**15. `TenantRedis` gains sorted sets.** `zadd`, `zrangebyscore`, `zrem` and
`zremrangebyscore`, each with a typed key first.

**16. The ETag names the body.** When the gateway stores an answer, it hashes
the body. The hash is SHA-256 of the JSON with sorted keys, cut to 16 hex
characters. The ETag is `W/"<hash>"`.

A hit answers 304 only when
`If-None-Match` equals the stored tag. A recompute answers 304 only when its
own body has the same hash. So a 304 never stands for a different body.

**17. Read the versions first.** The read takes the two versions before it
runs the providers, and stores those values with the answer. A write during
the compute then bumps past the stored values, so the next read misses.

**18. A failed source is not cached.** An answer with a source of `failed`,
or with `handled_ok` false, is served and not stored. The next poll runs the
providers again.

**19. The email bump.** The `on_new_mail` hook bumps the member version of
the mailbox owner. A change from `classify_threads` waits for the edge, or
600 seconds at most. So does a change from a path that skips `on_new_mail`.

**20. The Redis binding.** `tenant_redis` binds its own tenant, apart from
`acb_common.db`. So the read does its Redis work inside
`organization_scope(user.organization_id)`. The sink does its work inside
`organization_scope(current_tenant())`. The precedent is
`routes/shell/intent.py:285`.

An emit with no bound tenant bumps nothing, unless its payload names
`organization_id`. Only `pm.task.assigned` does. Such a change waits for the
edge, or 600 seconds at most. The sink gets its own `try` in `main.py`, apart
from the workflows block, so a failed workflows import does not drop it.

**21. The hash and the snooze edge.** The features hash covers each grant
that gates a provider: the member's `feature:*` grants, and
`ADMIN_PERMISSION` (`needs.py:845-847`), sorted. `state.apply_state` also returns the earliest
`snoozed_until` of the rows it hides, for item 2.

**22. The active-set write.** The feed read writes the active set only while
`MY_DAY_LOOP_ENABLED` is on. A Redis failure logs, and the read serves the
answer.

**23. The sweep, exactly.** It runs once a day for each org. The guard is
`SET key("my_day", "swept", <date>) NX EX 90000`. It deletes rows older than
30 days in these states:

- `seen` and `later`
- a time snooze that ended: `snoozed`, with `snooze_on_change` false and
  `snoozed_until` in the past

It keeps `dismissed`, `acted` and the snoozes until a change. To delete one
of those brings its card back as new, against §6.3 rule 2 and §6.4.

**24. Departed, for MD-6a.** The member pass treats a member as departed when
one of these holds:

- `resolve_identity` gives no org, and `identity_read_failed()` is false.
- `resolve_identity` gives an org that is not the loop's org.
- `app_user.status` is not `active`.

With `IDENTITY_CUTOVER` off, `resolve_identity` reads `app_user` with no
status filter. That is why the third check exists. A departed member leaves
the active set. A failed identity read skips the member, and keeps it in the
set.

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

MD-6a-1 tests the reader only, because in `shadow` the caller always gets
the old value. An off-list key reads as no answer. A valid key reads as its
own option key. The two card cases above move to MD-6b.

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

1. **Register.** MD-6a adds the eight `my_day.*` features to the one
   registry in `gateway/decide_features.py`, as `MY_DAY_FEATURES`. They are
   not in the tuple `FEATURES`. The email suites pin that tuple as the
   email set (MD-6a-1 done-when 7). Every default is `off`. MD-6b adds them to
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

### 10.9 How MD-6a runs (decided 2026-10-14)

The MD-6a audit gave NO-GO, with ten gaps. These answers close them. Where
this section and an earlier one differ, this section wins.

**1. One compute.** `feed_answer` gives up a function, `feed_cards(user, now)`.
It runs `needs_run`, then `build_cards`, then `apply_state`. The read and the
pass both call it, so there is no second compute path. The pass does not run
`handled_run` or the needs view.

**2. The old values.** The judge compares each answer with the value that the
feed shows today:

- `triage`: the card's `group`, after the state
- `weight`: the level of `feed_rank.LEVEL_BY_CELL`, and 0 for a card that is
  not a task
- `next_act`: the key of `acts[0]`
- `resurface`: false

**3. The features MD-6a asks.** It registers all eight, and asks four:
`triage`, `weight`, `next_act` and `resurface`. `reason` waits for MD-4,
`slot` for MD-8, `compose` for MD-6c and `nudge` for MD-10. A registered
feature that nothing asks stays `off`.

**4. Two more helper changes.** §10.6 item 2 lists four. These are the fifth
and sixth:

- `shadow` takes `record`, an async callable that gets the comparisons. A
  `compare` may give a list. With no `record`, the helper behaves as today.
- `shadow` honours `_cooling`, as `ask` does. Today a shadow keeps calling
  after a 402.

Email runs `on` in production, so the email decide suites are regression
fences for both changes (§18).

**5. The org list.** `MY_DAY_DECIDE_ORGS` is a settings field. `mode_for`
reads it, and not `DECIDE_FEATURE_ORGS`, for a feature that starts with
`my_day.`. Its default is empty, so no org is in it. The owner can then start
`shadow` for one org, with no change to email's list of `*`.

**6. The org ceiling.** `my_day_decide_org_daily_calls`, a settings field,
default 0. A value of 0 sets no org ceiling. The member cap of 60 calls each
day still binds (§9.1).

**7. The source of "dirty".** `cache.py` gains a public `versions(member)`.
The pass stores the versions it judged under `key("my_day", "judged",
<member>)`, with a TTL of 26 hours. A member is dirty when the versions
differ.

The bumps write only while `MY_DAY_CACHE` is on. With it off, only
the morning pass runs.

**8. The pass bound.** The member pass runs after `run_org`, outside
`PER_ORG_TIMEOUT_S`. Each member has a bound of 30 seconds, and three run at
once at most (§8.4 item 13).

**9. Each mode off means no spend.** With each `my_day.*` mode `off`, the pass
makes no facade call and writes no row. A test fences it. That keeps
`MY_DAY_LOOP_ENABLED` and `MY_DAY_CACHE` under the `enforcement-flip` grant.

**10. The measure read.** `GET /shell/feed/decide-measure` needs
`ADMIN_PERMISSION`, and reads the bound org only. For each feature it answers
the count of rows and the rate of agreement. It also answers the acted rate
of the top five cards of each path.

The judged top five is `feed_rank.order`,
with the judged group and the judged level times six. The pass stores each
place in `old_rank` and `judged_rank` (§13.2).

A card counts as acted when its `my_day_card_state` row has an `acted_at` at
or after the judgment's `created_at`, on the same day for the member.

Decided at the MD-6a-3 review, 2026-10-14:

- **The mode.** The read splits every number by `mode`. In `on` the member
  sees the judged order, so the judged path wins its acted rate by
  construction. A gate reads the `shadow` numbers only.
- **The day** is the local calendar date in `user_settings.timezone`, and
  not `state.Day.feed_day`. The morning pass runs 15 minutes before
  `day_start_hour`, so `feed_day` puts its rows on the day before. An act
  after midnight by a member who works at night does not count. That lowers
  both paths alike.
- **Resurface.** Its old value is always `false`, so its `agree_rate` is
  only the share of `false` answers. The read also gives the count of each
  pair of old and judged values, and a gate for resurface reads the pairs.

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
| `old_rank`, `judged_rank` | `SMALLINT NULL` | The card's place on each path. The pass writes them on the `my_day.triage` row |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

The primary key is `(organization_id, member_email, signal_id, feature,
created_at)`. An index on `(organization_id, created_at)` serves the sweep.

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
        {"id": "open-day", "label": "Open the day", "class": "A", "way": "open", "href": "/calendar?date=2026-10-14&at=14:00"}
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
- **The app ids** are `tasks`, `projects`, `email`, `approvals`,
  `calendar`, `crm`, `people` and `chat`. MD-3b added the last three. The
  `sources` key of the Calendar provider is `calendar`. A `sources` key is
  the id of a source, and an MD-3 source has an id of its own (§5.8 item 6).
- **MD-3c adds the card field `source`** (§5.9 item 1). It is the source id
  of the card's main signal. The gateway sends it only when it differs from
  `app`, so a card of an MD-1 source has no such field. The carry of §6.1
  keys a card on `source`, or else on `app`. So a failed `tasks_followups`
  carries only its own cards, and never a card of the `tasks` source.
- **MD-3b adds `source_apps`**, `{source id: app id}`, with one entry for
  each key of `sources`. The client maps a failed source to its app for the
  failed line, and a capped source to its app for "Show all in <app>". A
  source that the map does not name is its own app, so an older answer reads
  as before. Two failed sources of one app give one line.
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
MD-3. Server paging is dropped by MD-5 (§8.4 item 9). The keys of §14.3 go to
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
is 7.9 seconds since MD-3b (4.0 + 2.3 + 0.1 + 1.5, §5.8 item 3), under the 8
seconds of the BFF route. A read past its
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
  no title, or with a `by` kind it does not know. It also drops an entry whose
  link is not a path of this app, and a card in the group `handled`.
- **Only an in-app path is a link** (fix round 1). `isInAppPath` in
  `src/lib/inAppLink.ts` is the one rule, and the chat and the entity pill
  use it too. It refuses `//`, a backslash, a control mark, and a path that
  resolves to another origin. A card with another link keeps its title and
  draws no link. The reader drops an `open` act with another link.
- **The group.** `FeedColumn` draws Handled folded after Later, as "Handled
  today" and its count. Each row is the app's icon, the title as a link, and
  who closed it with the time, for example "You · 09:30". `handledBy` writes
  the words of the `by` table. Handled never enters `cards`, the cut, the
  pill or the visit.
- **The summary.** With the flag on, `feedNeedsCount` counts the open cards
  of Now and Needs you, after the column's own hides. `handledClaim` holds
  the claim. With the flag off, the summary is the line of NS-3.
- **The fences.** `feedHandled.test.ts` (21 tests), four tests in
  `myDayPage.test.ts`, one test in `inAppLink.test.ts`, and two checks in
  `e2e/my-day-feed.spec.ts`.

The build made these choices, which this file did not fix:

- **The claim follows a count of needs only.** When nothing needs the
  member, the line stays "Nothing needs you right now.", because "The rest"
  then has nothing to follow.
- **A failed Calendar silences the claim too.** A calendar card can sit in
  Now, so the page cannot back "The rest is handled." without it.
- **A capped source silences the claim too** (fix round 1). Its count is a
  floor. With 20 overdue tasks the feed holds 15, and 5 more wait.
- **A stale answer silences the claim too** (fix round 1). When a refresh
  fails, the column keeps the last answer and says "Could not refresh your
  day." The summary keeps the count and drops the claim, because the claim
  can be from the day before.
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

### MD-3 · More signals, and the three link gaps — AGENT-SAFE, in six slices

The MD-3 audit of 2026-10-14 gave NO-GO, and split the ticket. §5.8 holds
the answers. MD-3a needs no flag. Each other slice is dark behind
`MY_DAY_PROVIDERS` until its source id is in the list. A change to that
list is an `enforcement-flip`. Name the flag and the box in the same message.

### MD-3a · The links — AGENT-SAFE

Files: `src/app/calendar/CalendarView.tsx`, `src/app/approvals/page.tsx`,
and `routes/shell/feed.py` for the links of three acts and of the approval
signal.

Done when:

1. `/calendar?date=&at=` opens the day at that date and time. A bad value
   leaves the page as it is today. A vitest covers the parser.
2. `/approvals?action=<id>` marks that row. A row that is not pending shows
   a line above the queue. A vitest covers the parser.
3. `block_overlap`, `day_overbooked`, `no_plan_yet` and the approval card
   link to these paths. The needs row keeps `/approvals`.

**BUILT, 2026-10-14.** No migration and no flag. These are the parts:

- The gateway builds the links. `day_href` in `routes/shell/needs.py` gives
  `/calendar?date=YYYY-MM-DD`, and adds `&at=HH:MM` for an overlap. The time
  is the block's start in the member's zone. `approval_href` in
  `routes/shell/needs.py` gives `/approvals?action=<id>`. Only the feed
  signal uses it, and the needs row keeps `/approvals`.
- `app/calendar/lib/calendarLink.ts` reads the Calendar link.
  `components/CalendarDeepLink.tsx` hands it to `CalendarView.tsx`, which
  opens the day view on that day. The grid scrolls to one hour above the
  link's time, as it scrolls above the current hour today.
- `app/approvals/lib/approvalLink.ts` reads the Approvals link. The page
  scrolls to the row and marks it with the cursor look of `InboxCard.tsx`.
- The fences are `calendarLink.test.ts`, `approvalLink.test.ts`,
  `api/actions/pending/route.test.ts` and `test_shell_feed.py::TestTheLinks`.

Review round 1 fixed four findings:

- `GET /api/actions/pending` answers 502 when the gateway fails, never an
  empty list. The page says "no longer waiting" only after a good load. A
  failed reload keeps the rows that the member sees.
- "Block time" opens today, and the merge keeps one link for each place
  (§5.8 item 9).
- A change of mode or day by hand ends the link's time. Day, then week,
  then day scrolls to the current hour again.
- The status line of Approvals stays mounted, and it is empty when there
  is nothing to say.

The build made three choices that the ticket did not record:

- Each part of the Calendar link is read on its own. A bad date opens
  today, and keeps a good time. A bad time keeps a good date.
- The Approvals link takes a UUID only, the key of `pending_actions`.
- The card's own `href` changes too, for `day_overbooked`, `no_plan_yet`
  and the approval card. The `block_overlap` card keeps the task link, and
  only its act changes.

### MD-3b · The seam — AGENT-SAFE

Files: `packages/acb_common/acb_common/settings.py`, `env_guard.py`,
`routes/shell/needs.py`, `feed.py` and `cache.py`, and `src/lib/shell/feed.ts`.

Done when:

1. With `MY_DAY_PROVIDERS` empty, the answer is the same as before.
2. The feed-only providers run at the same time. A slow one fails alone.
3. `SourceAbsent` reads as `absent`, and `Provider.permission` gates a
   provider.
4. The features hash covers `admin:members:read` and `MY_DAY_PROVIDERS`.
5. The client knows `crm`, `people` and `chat`, with a name and an icon.

**BUILT, 2026-10-14.** No migration, and no flag changed. These are the
parts:

- `MY_DAY_PROVIDERS` is a Settings field and a name in `env_guard.py`.
  `needs.listed_sources` is its one reader. `needs.feed_only_providers`
  keeps a provider with `gated=True` only when the list holds its source
  id. The register refuses a gated needs provider.
- `needs.run_providers(..., slots=, ceiling_s=)` runs the feed-only
  providers two at a time at most for a read (`FEED_CONCURRENCY`), and one
  at a time for the loop's pass (`FEED_PASS_CONCURRENCY`). Each limit of 2
  seconds starts when the provider gets its slot. At `FEED_TOTAL_S`, 2.3
  seconds, the rest read `failed`. The needs providers still run one after
  another.
- The register refuses a gated provider that carries a Handled read, until
  a slice filters `handled.handled_providers()` by the list.
- `needs.SourceAbsent` makes a source read `absent`, in the provider run and
  in the Handled read (`handled.py`). `Provider.permission` is part of
  `held_by`, so it gates the Handled read too.
- The answer gains `source_apps` (`needs.source_app`, and `Provider.app`
  for a source whose id is not its app id). `feedFailedLines`,
  `cappedLinks` and `handledClaim` read it. The body changes once at the
  deploy, so each stored answer misses once on its ETag.
- `cache.features_hash` also covers each `Provider.permission` that the
  member holds, and the list. With an empty list, the hash does not change,
  so the keys of the live cache stay valid.
- `feed.ts` names the three apps, and takes each icon from its pane in
  `nav.ts`.
- The anchors, at the fix round of 2026-10-14: `_run_bounded` is at
  `needs.py:1112`. `FEED_CONCURRENCY`, `FEED_PASS_CONCURRENCY` and
  `FEED_TOTAL_S` are at `feed.py:115`, `:118` and `:125`. The answer builds
  `source_apps` at `feed.py:675`.

**What the build found, for the slices after it.**

- `ADMIN_PERMISSION` (`routes/shell/intent.py`) is `admin:members:read`. So
  the hash covered that grant before MD-3b. Done-when 4 adds a test. The
  general rule stands: the hash covers each `Provider.permission`.
- `handled.handled_providers()` reads the whole `REGISTRY`. So the register
  refuses a gated provider with a Handled read. The first slice that needs
  one must filter that read by the list, and then lift the refusal.
- The carry of §6.1 (`noteOk` and `carries` in `feed.ts`) keys on the
  source id and filters cards by `card.app`. A source whose id is not its
  app id carries no cards when it fails. The slice that adds such a source
  must give the card its source, or accept that its cards do not carry.

### MD-3c · The task kinds — AGENT-SAFE, after MD-3b

`waiting_quiet`, `stale_next`, `unblocked` and `absent_on_due`, from the
source `tasks_followups`. §5.9 items 1 to 7 and 11 hold every rule, number,
text and anchor. This ticket also builds the `source` field of the carry
(§5.9 item 1) and the `elapsed_ms` log line (§5.9 item 7).

Files:

- `apps/services/gateway/gateway/routes/shell/feed.py`: the provider, its
  templates, its folded clauses, `STABLE_FACTS`, and the `source` of a card
- `apps/services/gateway/gateway/routes/shell/feed_rank.py`: `BASE` and
  `group`
- `apps/services/gateway/gateway/routes/shell/needs.py`: `elapsed_ms` in
  `_attempt`
- `apps/services/gateway/gateway/routes/projects/personal.py`:
  `my_quiet_tasks`, `my_unblocked_since` and `my_due_ahead`
- `apps/services/gateway/gateway/routes/people/selfservice.py`: `bump_org`
  on the absence `POST` and `DELETE`
- `workbench/control_plane/src/lib/shell/feed.ts`: `noteOk`, `CLOSES`,
  `FEED_KIND_LABELS` and the optional card field `source`
- `project-docs/specs/my_day_feed.md`: §4.3, §5.2, §14.1a and §18

Done when:

1. With `tasks_followups` not in `MY_DAY_PROVIDERS`, the feed answer is the
   same as before, byte for byte.
2. With the source listed, each of the four kinds gives its card on the seed.
   Each card has the `BASE`, group, `why_key`, text, act and label of §5.9.
3. `waiting_quiet`: a `WAITING` task with `delegated_at` exactly 5 days ago
   gives no row. A `WAITING` task with no `delegated_at` gives no row.
4. `stale_next`: a `NEXT` task that changed 13 days ago gives no row. A
   `SOMEDAY` task with no change for 30 days gives no row.
5. `unblocked`: a blocker that closed before the day start gives no row.
6. `unblocked`: a task with one closed blocker and one open blocker gives no
   row.
7. `unblocked`: a blocker that the member cannot read gives a card. Its
   facts hold no blocker title and no blocker id (R8).
8. `absent_on_due`: the absence of a colleague on the due day gives no row
   (R8, two People rows).
9. `absent_on_due`: a `partial` absence gives no row.
10. A member with no People row gets the other three kinds, and the source
    reads `ok`, not `absent`.
11. A folded task kind does not change the state fingerprint of a card.
12. A failed `tasks_followups` carries its own cards for 5 minutes. It
    carries no card of the `tasks` source.
13. The provider opens 4 sessions at most, by a counting fake.
14. A self absence write bumps the org version, while `MY_DAY_CACHE` is on.

Fences: `tests/unit/test_feed_task_kinds.py`,
`tests/unit/test_feed_task_kinds_r8.py` (R8, with `pytestmark = _DB_GATE`),
`tests/unit/test_feed_rank.py`, and `src/lib/shell/feed.test.ts`.

Verify:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_feed_task_kinds.py tests/unit/test_feed_task_kinds_r8.py tests/unit/test_shell_feed.py tests/unit/test_shell_feed_r8.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py tests/unit/test_shell_needs_r8.py tests/unit/test_projects_relations.py tests/unit/test_people_absences.py -q
uv run ruff check apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/routes/projects/personal.py apps/services/gateway/gateway/routes/people/selfservice.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/shell/feed.test.ts src/lib/shell/feedApps.test.ts
node .claude/hooks/ste-lint.mjs project-docs/specs/my_day_feed.md
```

**BUILT, 2026-10-15.** No migration, and no flag changed. The source
`tasks_followups` is gated, so it runs only when `MY_DAY_PROVIDERS` lists
it. These are the parts:

- `feed.py`: the provider `_tasks_followups` and its four kinds. It also
  holds the why templates of all eleven MD-3 kinds (`_md3_why`), the folded
  clauses, `STABLE_FACTS`, and the card field `source`. The feed tags each
  signal with its source after the provider runs.
- `feed_rank.py`: `BASE` and `group` for all eleven kinds (§5.9 items 2 and
  3). `BASE` is also the vocabulary of `prefs.feed_kinds`, so a member may
  mute an MD-3 kind before its provider is listed. 20 kinds, which is
  `prefs.MAX_KINDS`, so the cap did not change.
- `needs.py`: each provider run logs `elapsed_ms`. A run that ends well
  writes the line `provider answered`, with its `outcome`. A run that the
  ceiling cuts writes `provider passed the ceiling`, with its time from its
  slot to the cut.
- `personal.py`: `my_quiet_tasks`, `my_unblocked_since` and `my_due_ahead`.
  `my_quiet_tasks` also gives the day start hour by the rule of
  `state._hours` (`personal.member_day`), so the provider learns the
  member's day start with no fifth session.
- `selfservice.py`: the self absence `POST` and `DELETE` call
  `cache.bump_org_safe` after the commit.
- `feed.ts`: the field `source`, `noteOk` on `(c.source ?? c.app)`, `CLOSES`
  and the eleven labels.

**What the build found, for the slices after it.**

- `unblocked` reads an "open task" as an actionable one
  (`ACTIONABLE_CLAUSE`). So a Someday task never gets the card. A deferred
  task stays out too (`DEFERRED_CLAUSE`).
- An act link of a folded kind is often to the task. `_merge_acts` keeps one
  link for each place, so the "Nudge" link of a folded `waiting_quiet` does
  not show on an overdue card. MD-8 makes these acts taps.
- The measure (§5.9 item 7) needs the line `provider answered`. Before
  MD-3c, a run that ended well wrote no line.

**Mutations, run by hand on 2026-10-15.** Each one failed a fence:

1. The gated flag removed.
2. `waiting_quiet` at 5 days or more, not more than 5.
3. `unblocked` names a blocker that the member cannot read.
4. A due day that is not my day away counts, as a colleague's day would.
5. A `partial` absence counts.
6. The 404 of the absence read raises `SourceAbsent`.
7. The card sends `source` also when it is its app.
8. The `POST` bump dropped, and then the `DELETE` bump dropped.
9. A task kind carries `due_at`.
10. `elapsed_ms` dropped from the line `provider answered`.
11. The carry filter of `noteOk` uses the app, not the source.

**Fix round 1, 2026-10-15.** The verifier and the reviewer found these, and
each one is fixed:

- **The log fields did not reach the journal.** The gateway prints
  `%(message)s` alone (`acb_common/_log.py`), so a field in `extra` was
  lost. Each provider line now holds `provider=`, `outcome=` and
  `elapsed_ms=` in its text. A feed-only provider logs its answer at INFO,
  and a needs provider at DEBUG, because the bell polls the needs view.
- **Three reads of `user_settings` named no tenant.** Since migration 239 a
  member has one row for each organization. RLS holds the gateway role
  `acb_app` (measured on the box, 2026-10-16), so the predicate is defence
  in depth. `personal.stored_zone` and `tasks.settings._load`
  now name the bound tenant (the GUC predicate of `prefs.py`). The day start
  read is gone: `personal.member_day` calls `_load`.
- **The day start uses the one rule.** A member with the Calendar pane
  reads `_load`: a stored 0 reads 7, and a member with no row gets the
  People seed. Any other member reads 7, as `state._hours` does.
- **`my_due_ahead` asks the days away in SQL.** The read used one window,
  from the first day away to the last. So 16 tasks of the work days between
  filled the LIMIT, and a task due on the last day away was lost.
- **The absence bump is guarded.** `cache.bump_org_safe` waits 0.5 seconds
  at most and never raises. MD-3f reuses it.
- **A fence under the owner role.** `test_feed_task_kinds_r8.py`
  `TestTheTenantUnderTheOwnerRole` seeds a member in two organizations.
  The mutations of round 1 that it kills: the org predicate dropped from the
  quiet read, from `_load`, and from `stored_zone`. The fields moved back to
  `extra`, the days of the due read back to the window, and the bump
  unguarded fail their fences too.

### MD-3d · The mail kinds — AGENT-SAFE, after MD-3c merges (the A1 `source` field)

`promise_due` and `no_answer`, from the source `email_followups`. §5.9 items
2 to 8, 10.1 and 12 hold every rule, number, text and anchor. The carry of
this source needs the `source` field that MD-3c builds (§5.9 item 1).

Files:

- `apps/services/gateway/gateway/routes/shell/feed.py`: the provider, its
  templates and its folded clauses
- `apps/services/gateway/gateway/routes/shell/feed_rank.py`: `BASE`, `group`,
  and the weight of 10 for a promise
- `apps/services/gateway/gateway/routes/email/digest.py`: `awaiting_threads`,
  `promise_tasks`, the bounds of `_digest_thread_list`, and the core of
  `_digest_commitments` that raises
- `workbench/control_plane/src/lib/shell/feed.ts`: `CLOSES` and
  `FEED_KIND_LABELS`
- `project-docs/specs/my_day_feed.md`: §4.3, §5.3 and §18

Done when:

1. With `email_followups` not listed, the feed answer is the same as before.
2. With the source listed, each kind gives its card on the seed. Each card
   has the `BASE`, group, `why_key`, text, acts and label of §5.9.
3. The wrapper of member A, called with the mailbox of member B, raises 404.
   The feed of A shows no thread of B (R8).
4. A captured task that is not a commitment gives no `promise_due` row.
5. A database error in the promise read makes the source `failed`, not an
   empty `ok`.
6. A thread younger than the cutoff gives no `no_answer` row.
7. A thread older than 30 days gives no `no_answer` row.
8. With no bound, the output of the digest does not change.
9. A card with `promise_due` gets a weight of 10 more than the same card
   without it.
10. The provider opens 1 session, plus 2 for each mailbox, at most.

Fences: `tests/unit/test_feed_mail_kinds.py`,
`tests/unit/test_feed_mail_kinds_r8.py` (R8, with `pytestmark = _DB_GATE`),
`tests/unit/test_email_digest.py` and `tests/unit/test_feed_rank.py`.

Verify:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_feed_mail_kinds.py tests/unit/test_feed_mail_kinds_r8.py tests/unit/test_email_digest.py tests/unit/test_shell_feed.py tests/unit/test_shell_needs.py tests/unit/test_shell_needs_r8.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py -q
uv run ruff check apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/routes/email/digest.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/shell/feed.test.ts src/lib/shell/feedApps.test.ts
node .claude/hooks/ste-lint.mjs project-docs/specs/my_day_feed.md
```

**BUILT, 2026-10-15.** No migration, and no flag changed. The source
`email_followups` is gated, so it runs only when `MY_DAY_PROVIDERS` lists
it. These are the parts:

- `feed.py`: the provider `_email_followups` and its two signals. It skips a
  separate mailbox, and a mailbox that answers 404. Each mailbox gets one
  `MAILBOX_TIMEOUT_S` for its two reads.
- `feed_rank.py`: `weight(facts, kinds)` adds `PROMISE_WEIGHT`, 10, when
  `promise_due` is on the card. `feed_decide.old_level` passes no kinds, so
  its level stays 0 to 4.
- `digest.py`: `awaiting_threads`, `promise_tasks`, the bounds of
  `_digest_thread_list` and the core `_commitments_core`. With no bound, the
  digest sends the same statements, byte for byte.
- `email/core.py`: `_assert_account_owner` takes `this_tenant`, off by
  default. The two wrappers set it.
- `feed.test.ts`: case (d1d), the carry of a failed `email_followups`.

**What the build found, for the slices after it.**

- The cutoff needs the mailbox's `follow_up_awaiting_days`, and the spec
  gave no session for that read. `awaiting_threads` reads it in its own
  session, when the caller gives no `before`.
- `feed_mailboxes` names no tenant. RLS holds for `acb_app`, the role of
  production, so this is defence in depth (R5). Read past RLS, the list would
  hold the member's mailbox of another organization too. The wrappers refuse
  it with a 404, and the provider skips it. HANDOFF H-334 holds the rest.

**Fix round 1, 2026-10-16.** The review found that a fold moved the state
fingerprint. A `promise_due` folded under a notification card added
`due_at`, so a dismissed card came back when the promise came or went.
`build_cards` now keeps the state facts of a folded feed-only kind out of the
card (§5.9 item 4). A folded needs kind keeps them, so a new note still
brings a task back (§6.8 item 1). The fence is
`tests/unit/test_feed_fold_fingerprint.py`, for every kind of `BASE`.

The same round reuses the owner check's row in `_commitments_core`, and builds
the tenant line of the latest-message statement explicitly.

**Mutations, run by hand on 2026-10-15.** Each one failed a fence:

1. `commitment=True` dropped from the promise read.
2. The owner check dropped from `awaiting_threads`.
3. The error of the promise read swallowed in the provider.
4. The 30-day bound dropped from the SQL.
5. The digest default changed (`this_tenant` on by default).
6. The promise weight set to 0.
7. The tenant predicate dropped from the owner check.
8. A 404 mailbox counted as a failure.
9. The default wait set to 2 days.
10. The promise read run without `feature:projects`.
11. The text of the latest-message statement changed.

**The measure.** The provider ran 40 times on the R8 seed, with two
mailboxes, against a local Docker Postgres. The p50 was 136 ms, and the p95
was 165 ms. The rule of §5.9 item 7 still needs the measure on the
production box.

### MD-3e · The CRM kinds — AGENT-SAFE, after MD-3b

`crm_followup_due`, `deal_quiet` and `deal_close_passed`, from the source
`crm`. §5.9 items 2 to 7, 10.4 and 13 hold every rule, number, text and
anchor. The source id is the app id, so the carry needs no change.

Files:

- `apps/services/gateway/gateway/routes/shell/feed.py`: the provider, its
  templates and its folded clauses
- `apps/services/gateway/gateway/routes/shell/feed_rank.py`: `BASE` and
  `group`
- `apps/services/gateway/gateway/routes/shell/search.py`: the one helper
  `crm_href`, and its use at `:163`
- `apps/services/gateway/gateway/routes/crm/activities.py`:
  `my_due_activities`
- `apps/services/gateway/gateway/routes/crm/aggregate.py`:
  `my_overdue_deals` and `my_close_passed_deals`
- `apps/services/gateway/gateway/routes/crm/settings.py`: `bump_org` on
  `PATCH /crm/settings`
- `workbench/control_plane/src/lib/shell/feed.ts`: `FEED_KIND_LABELS`
- `project-docs/specs/my_day_feed.md`: §4.3, §5.5 and §18

Done when:

1. With `crm` not listed, the feed answer is the same as before.
2. With the source listed, each kind gives its card on the seed. Each card
   has the `BASE`, group, `why_key`, text, act and label of §5.9.
3. With the kill switch off for the organization, the source reads `absent`.
4. For a plain member under `access = 'admins'`, the source reads `absent`.
   The provider makes no CRM read.
5. In Mirror mode, no `deal_quiet` row shows, and the follow-ups still show.
6. A deal of another owner gives no row.
7. An activity of another member gives no row.
8. A won or lost deal gives no `deal_close_passed` row.
9. With two organizations under FORCE RLS, each read returns the rows of
   one tenant only (R8).
10. `PATCH /crm/settings` bumps the org version, while `MY_DAY_CACHE` is on.
11. The provider opens 2 sessions at most.

Fences: `tests/unit/test_feed_crm_kinds.py`,
`tests/unit/test_feed_crm_kinds_r8.py` (R8, with `pytestmark = _DB_GATE`),
`tests/unit/test_crm_access.py` and `tests/unit/test_crm_aggregate_r8.py`.

Verify:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_feed_crm_kinds.py tests/unit/test_feed_crm_kinds_r8.py tests/unit/test_crm_access.py tests/unit/test_crm_access_r8.py tests/unit/test_crm_aggregate.py tests/unit/test_crm_aggregate_r8.py tests/unit/test_shell_feed.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py -q
uv run ruff check apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/routes/crm
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/shell/feed.test.ts src/lib/shell/feedApps.test.ts
node .claude/hooks/ste-lint.mjs project-docs/specs/my_day_feed.md
```

**BUILT, 2026-10-15.** No migration, and no flag changed.
`MY_DAY_PROVIDERS` gates the source `crm`, so it runs only when the list
names it. These are the parts:

- `feed.py`: the provider `_crm`, its gate `_crm_gate` and its three
  signals. This file opens no session.
- `crm/aggregate.py`: `my_overdue_deals`, `my_close_passed_deals` and
  `my_day_rows`. `my_day_rows` opens the one session of the source. It
  reads the zone, and then it runs the three reads.
- `crm/activities.py`: `my_due_activities`. It gives one row for each
  record, with the earliest open due time.
- `retrieval/sources.py`: `crm_href`, the one link to a CRM record. The
  search sources call it too. It moved there with the search refactor on main.
- `crm/settings.py`: `PATCH /crm/settings` calls `bump_org_safe` after
  the commit, the MD-3c rule for a write route of another app.
- `shell/cache.py`: while `crm` is listed, `features_hash` holds the CRM
  kill switch of the org. So an org that leaves `CRM_ORGS` gets no stored
  CRM card.
- `feed.ts` did not change. MD-3c added the three labels.

**What the build found, for the slices after it.**

- The three reads take the session from their caller, so they can share
  one session. §5.9 item 13 named `(user, limit)`. `my_day_rows` opens the
  session, because `test_shell_feed.py` refuses a session in `feed.py`.
- `_OVERDUE_FROM` reads the clock of the database (`now()`), not
  `needs._now()`. So the edge of `deal_quiet` follows the database time.
  The cache waits 600 seconds at most for it (§5.8 item 2).
- Each act of the three kinds links to the record. So `_merge_acts` keeps
  only the act of the main kind on a folded card. MD-8 makes these acts
  taps.
- Rule 4 of the state join can move a call to Now. It does so when the
  due time, minus 2 hours, came after the start of the member's day. A task
  has no `urgent_at`, so the rule never moves it.
- RLS holds the gateway role `acb_app` in production (measured on the box,
  2026-10-16). So each org predicate of the three reads is defence in
  depth. The R8 suite still proves each one on a role that RLS does not
  hold.
- `stage_dwell` (`aggregate.py`) runs its reads with no org predicate. RLS
  holds it in production, so this is defence in depth only. MD-3e does not
  change it.
- A folded `deal_quiet` adds `waiting_since` to a `deal_close_passed` card.
  That moves the state fingerprint, so a dismissed card comes back. MD-3d
  fixes the fold for each kind. Until then, `test_feed_crm_kinds.py` holds
  the case as a strict `xfail`, and the rebase on MD-3d removes the mark.
- `test_shell_feed.py` counts `needs._now()` in `feed.py`, and it lists the
  registry in order. Each MD-3 provider changes both, so MD-3f must change
  them again.

**The measure on the R8 seed** (§5.9 item 7). The provider ran 40 times on
a local database. The p50 was 37 ms and the p95 was 49 ms for the app role.
The p50 was 45 ms and the p95 was 56 ms for the owner role. A box measures
again before the flip.

**Mutations, run by hand on 2026-10-15.** Each one failed a fence:

1. The owner clause dropped from the two deal reads.
2. The access rule runs after the reads.
3. Mirror mode raises `SourceAbsent`, and does not give an empty `ok`.
4. The follow-up read does not leave out `status_change`.
5. The `bump_org` of `PATCH /crm/settings` dropped.
6. The org clause dropped from the two deal reads (the owner role).
7. The org clause dropped from the follow-up read (the owner role).
8. The member clause dropped from the follow-up read.
9. The close read lets a won or a lost deal through.
10. A 403 of the access rule raises, and does not give `absent`.
11. The search bar writes the CRM link itself, not through `crm_href`.
12. A record takes its latest follow-up, not its earliest.

**Fix round 1, 2026-10-16.** Each of these mutations failed a fence:

1. The read compares a task with the local midnight, as a time.
2. The card gives a task its UTC time and a clock.
3. The LIMIT keeps the oldest rows first again.
4. `stage_days` rounds down, so the first day reads the limit.
5. The CRM kill switch leaves the cache key.
6. The provider cuts a follow-up of today for an older one.

### MD-3f · People and chat — AGENT-SAFE, after MD-3b

`colleague_out` from the source `people`, and `agent_asks` from the source
`chat`. §5.9 items 2 to 7, 10.3, 10.5 and 14 hold every rule, number, text
and anchor. Each source id is its app id, so the carry needs no change.
`CHAT_DURABLE_ASKS` belongs to WS-51, so `agent_asks` stays empty until WS-51
turns it on.

Files:

- `apps/services/gateway/gateway/routes/shell/feed.py`: the two providers,
  their templates and their folded clauses
- `apps/services/gateway/gateway/routes/shell/feed_rank.py`: `BASE` and
  `group`
- `apps/services/gateway/gateway/routes/people/absences.py`: `away_on`, and
  `bump_org` on the admin absence `POST` and `DELETE`
- `apps/services/orchestrator/orchestrator/pending_ask.py`: `bump_member` in
  `_record` and in `settle`
- `workbench/control_plane/src/lib/shell/feed.ts`: `FEED_KIND_LABELS`
- `project-docs/specs/my_day_feed.md`: §4.3, §5.6 and §18

Done when:

1. With `people` and `chat` not listed, the feed answer is the same as
   before.
2. With the sources listed, each kind gives its card on the seed. Each card
   has the `BASE`, group, `why_key`, text, act and label of §5.9.
3. A member who fails `can_read_hr_fields` gets the source `people` as
   `absent`. A spy shows that the provider never ran.
4. `away_on` raises 403 for a member who fails `can_read_hr_fields`.
5. A `partial` absence of a co-assignee gives no row.
6. The absence of the member gives no `colleague_out` row.
7. `away_on` reads one tenant, and it uses the day that it gets (R8).
8. A question with another `actor_email` gives no `agent_asks` row.
9. A question in a room where the member cannot send gives no row.
10. With `CHAT_DURABLE_ASKS` off, the source `chat` reads `absent`.
11. The card of `agent_asks` holds no question text.
12. An insert and a settle of a question bump the version of the member,
    while `MY_DAY_CACHE` is on.
13. The provider `people` opens 3 sessions at most. The provider `chat` opens
    1 session, plus 1 for each thread, at most.

Fences: `tests/unit/test_feed_people_chat_kinds.py`,
`tests/unit/test_feed_people_chat_kinds_r8.py` (R8, with
`pytestmark = _DB_GATE`), `tests/unit/test_people_absences.py` and
`tests/unit/test_pending_ask_flow.py`.

Verify:

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_feed_people_chat_kinds.py tests/unit/test_feed_people_chat_kinds_r8.py tests/unit/test_people_absences.py tests/unit/test_pending_ask_flow.py tests/unit/test_pending_ask_store.py tests/unit/test_shell_feed.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py -q
uv run ruff check apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/routes/people/absences.py apps/services/orchestrator/orchestrator/pending_ask.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/shell/feed.test.ts src/lib/shell/feedApps.test.ts
node .claude/hooks/ste-lint.mjs project-docs/specs/my_day_feed.md
```

**BUILT, 2026-10-15.** No migration, and no flag changed. `MY_DAY_PROVIDERS`
gates the two sources, so each runs only when the list names it. These are
the parts:

- `feed.py`: the providers `_people` and `_chat`, and their two `register`
  calls. `people` needs `admin:members:read`, so a member without the grant
  gets `absent`, and the provider never runs.
- `people/absences.py`: `away_on`, one statement that names the bound
  tenant beside RLS. It raises 403 without the HR grant, and it opens no
  session for no address. The admin absence `POST` and `DELETE` call
  `bump_org` after the commit.
- `orchestrator/pending_ask.py`: `_record` and `settle` call `bump_member`
  for the member that the question asks. Each bump does nothing with
  `MY_DAY_CACHE` off. It never raises, and it stops after 0.5 seconds
  (`MY_DAY_BUMP_TIMEOUT_S`). So a bump never breaks an ask or an absence
  write.
- `feed_rank.py` and `feed.ts` do not change. MD-3c added the `BASE`, the
  group, the template and the label of both kinds.

**What the build found, for the slices after it.**

- §5.9 item 4 gives `agent_asks` the newest `asked_at` of the thread. The
  source of item 14, `_my_waiting_asks`, kept only the oldest row of each
  thread. Fix round 1 adds a summary of every waiting row of the thread to
  that entry: `request_ids`, `newest_asked_at`, `newest_agent_name` and
  `latest_expires_at`. The entry stays the oldest row, so `kind` keeps its
  meaning for `list_active_sessions`. The card takes the newest time, so a
  new question brings back a dismissed card.
- The template table of item 11 names only `waiting_since` for
  `agent_asks`. The card also carries `request_ids`, all the waiting ids of
  the thread, sorted. Item 4 adds that name to `STABLE_FACTS`.
- Two more writes end a question, and each bumps My Day (fix round 1). The
  late answer to a parked question (`POST /agent/respond-input`) bumps the
  member that it asked. The delete of a chat closes its questions, so it
  bumps the org while `CHAT_DURABLE_ASKS` is on.
- `cache.bump_member_safe` joins `cache.bump_org_safe`, so each bump of
  MD-3f goes through `cache.py`. Each waits 0.5 seconds at most and never
  raises.
- `pending_ask.waiting_asks` names the org beside RLS (fix round 1).
  Production connects as `acb_app`, which FORCE RLS holds, so each org
  predicate of MD-3f is defence in depth (R5), not a fix of a leak.
- `rooms._load_room` reads `chat_session` with no org predicate. The fix
  is not cheap: a fresh replay of the ladder gives `chat_session` no
  `organization_id` until the generated phases run. So it stays open, and
  RLS holds it in production.
- The chat provider checks the room of 15 threads at most, one session
  each (§5.9 item 7). So `capped` says that some threads were not checked,
  also when the first 15 fail `can_send`.
- The anchors moved. `my_calendar` is at `personal.py:2344`, after the
  reads of MD-3c. The admin absence routes are at `absences.py:224` and
  `:237`.
- The people provider checks the absence kind again after `away_on`. So a
  change of that read can only narrow the card.

**Mutations, run by hand on 2026-10-15.** Each one failed a fence:

1. The HR permission of `people` removed.
2. A `partial` absence counted, in the SQL of `away_on` and then in the
   provider.
3. My own absence counted.
4. `can_send` ignored.
5. The bump of a question raises into the write.
6. The org predicate removed from `away_on` (R8, a session that bypasses
   RLS).
7. The check of `actor_email` removed from the chat provider.
8. The bump of the admin absence `POST` removed.
9. `away_on` reads `CURRENT_DATE`, not the day that it gets.
10. The bump of `settle` removed.
11. The time bound of the absence bump removed.
12. The HR check of `away_on` removed.
13. `CHAT_DURABLE_ASKS` ignored.

**The measure** (§5.9 item 7). Each provider ran 40 times on the R8 seed,
on a local Postgres. `people` gave p50 50 ms and p95 81 ms. `chat` gave p50
153 ms and p95 271 ms, with a new connection for each session. Measure
again on the box before the flip.

Each slice adds its pytest and vitest lines to §18 in the same PR.

**The flip of a source.** To list a source id in `MY_DAY_PROVIDERS` is an
`enforcement-flip`. Do it after the merge, and restart the gateway. The
message names the flag, the box, the p95 measure of §5.9 item 7, and the log
line.

### MD-4 · Card bodies — AGENT-SAFE

`CalendarStrip`, `DraftPreview` (read only, from `email_ai_drafts`),
`ReportTile` and `TaskRows`, the `feedKinds` field and its row in
`navigation_shell.md` §5.1. Done when each body renders in the `visual-review`
contexts, and `conformance.test.ts` passes with no new budget.

### MD-5 · The cache and the loop — AGENT-SAFE, in two slices

The MD-5 audit of 2026-10-14 gave NO-GO, and split the ticket in two. §8.4
holds the answers. MD-5a and MD-5b may build at the same time. MD-5b adds one
call to `feed.py`, and MD-5a owns the rest of that file.

### MD-5a · The read cache — AGENT-SAFE

**BUILT, 2026-10-14.** The rules live in `routes/shell/cache.py`, and
`MY_DAY_CACHE` keeps them dark. The build adds three edges to item 2: the due
time itself, the urgent time minus 2 hours, and the next local midnight. A
folded `block_soon` counts as a countdown. An answer whose state read failed
is not stored. Fences: the two suites below and the vitest of `feed.test.ts`
and `route.test.ts`.

The review added four rules. The four own writes of item 5 bump a version. The
org version is a time stamp with the settle time of item 8. The sink takes the
bound tenant before the payload's org. A request of an outside agent
(`user.agent`) reads and stores nothing.

Files:

- `routes/shell/feed.py`, and a new `routes/shell/cache.py`
- `needs.py`, for `Found.next_edge`
- `state.py` and `prefs.py`, for the member bumps
- the sink in `gateway/main.py`
- `routes/email/scheduler_hooks.py`, for the `on_new_mail` bump
- the BFF route `src/app/api/shell/feed/route.ts`
- `fetchFeed` in `src/lib/shell/feed.ts`

Done when:

1. A hit runs no provider, no `state.apply_state` and no
   `handled.handled_run`. A test patches each one and asserts no call.
2. On a hit, the route body runs 0 SQL statements. The identity resolve of
   each request is counted apart.
3. A `block_soon` card appears 30 minutes before its block, with no event in
   between.
4. A task edit bumps the org version, and the sink runs no SQL. A test counts
   the statements of one edit with the sink on and off.
5. A state POST and a prefs PUT bump the member version.
6. A 304 keeps the client's answer and writes the needs view again.
7. A Redis outage serves the uncached answer.
8. With `MY_DAY_CACHE` off, every read runs the providers, and the sink and
   the bumps do nothing.
9. A revoked feature stops its cards at the next read, through the hash. A
   revoked admin permission stops the approval cards the same way.
10. A recompute whose body differs never answers 304 (§8.4 item 16).
11. A write during a compute makes the next read miss (item 17).
12. An answer with a failed source, or with `handled_ok` false, is not
    stored (item 18).

Fences: `tests/unit/test_my_day_cache.py`, and
`tests/unit/test_my_day_cache_r8.py` for the statement count. The Redis
tests run a fake under the real `TenantRedis`, as
`tests/unit/test_mcp_edge_writes.py` does. CI has no Redis, so a test that
needs a real one skips, and that skip is not a pass.

### MD-5b · The loop — AGENT-SAFE

Files:

- a new `gateway/my_day_loop.py`
- `gateway/main.py`, to start and stop it beside the CRM mirror
- `packages/acb_common/acb_common/settings.py`, for `MY_DAY_LOOP_ENABLED`,
  beside `crm_mirror` at line 261
- `packages/acb_common/acb_common/tenant_redis.py`, for the sorted sets
- the active-set write in `routes/shell/feed.py`
- `tests/unit/test_db_engine_seam.py`, for the discovery site

Done when:

1. `TenantRedis` has `zadd`, `zrangebyscore`, `zrem` and
   `zremrangebyscore`, each with a typed key first.
   `tests/unit/test_tenant_redis.py` fences them.
2. `tests/unit/test_my_day_loop.py` shows that the loop reads members only
   from the active set. The test seeds a fake under the real `TenantRedis`.
   It also shows that the read writes the set only from the session's
   identity.
3. With `MY_DAY_LOOP_ENABLED` off, the loop starts no task, and the read
   writes no active set.
4. The loop runs no member pass. MD-6a builds the pass, with the two other
   fences of §8.3 and with §8.4 item 24.
5. The sweep follows §8.4 item 23. `tests/unit/test_my_day_loop_r8.py`, with
   `_DB_GATE`, shows that it deletes only the bound org's rows, and that a
   `dismissed` row survives.

**BUILT, 2026-10-14.** The loop is `gateway/my_day_loop.py`, and it ships
dark behind `MY_DAY_LOOP_ENABLED`. Two choices differ from the file list:

- The sweep statement lives in `routes/shell/state.py`, as
  `sweep_old_rows`. That module stays the one writer of
  `my_day_card_state`, as migration 258 says. The loop calls it.
- The active-set write waits 0.25 seconds at most. A slow Redis cannot hold
  the feed read.

### MD-6a · The `decide` pass, in shadow — AGENT-SAFE (build), OWNER-GATE (turn on)

The MD-6a audit of 2026-10-14 gave NO-GO, and split the ticket in three.
§10.9 holds the answers. Build them in order: 6a-1, then 6a-2, then 6a-3.

**Turning on any `my_day.*` mode, `shadow` included, is the owner's flip**
(§17.1). Each shadow call is a real Router call, billed to the org. Residency
is answered (D100).

### MD-6a-1 · The features, the helpers and the table — AGENT-SAFE

Files:

- `gateway/decide_features.py`: the eight features, the six helper
  changes, and the `my_day.` org list
- `packages/acb_common/acb_common/settings.py`: `my_day_decide_orgs` and
  `my_day_decide_org_daily_calls`
- a new `gateway/routes/shell/feed_decide.py`: the questions, the state
  builder, the caps and the answer cache
- `gateway/routes/shell/feed.py`: `feed_cards` out of `feed_answer`
- migration `<next free>_my_day_judgments.sql`, 262 at audit, with the
  rank columns of §13.2. Check the number again at merge (R1).

Build it after MD-5a (#993) merges, because `feed_answer` comes from it.

Done when:

1. The eight features are in the one registry, `off` by default. No second
   registry and no second helper exist.
2. `mode_for` reads `MY_DAY_DECIDE_ORGS` for a `my_day.` feature, and the
   email features keep `DECIDE_FEATURE_ORGS`.
3. In `shadow`, the caller gets the old value, and each comparison writes
   one `my_day_judgments` row. `TestNoTenantText` passes.
4. `TestKeysOnly` (§10.2) and `TestCaps` (§9.1) pass. `TestCaps` covers the
   member cap and the org ceiling. The §7 rule 2 test runs on a pure
   function, `apply_triage`.
5. A pass with no new card makes no call. A repeat of the same facts makes
   no call. A change to an unhashed field makes no call. A test counts them.
6. A 402 starts the cool-down. A `shadow` then makes no call, and every card
   keeps its old value.
7. The email decide suites pass with no change to their tests.

Fences: `tests/unit/test_feed_decide.py`, and
`tests/unit/test_feed_decide_r8.py` with `_DB_GATE` for the table.

**BUILT, 2026-10-14, on branch `my-day-feed-md6a`.** Migration 262 adds
`my_day_judgments`. `feed_decide.judge(user, now)` is the pass for one
member, and MD-6a-2 calls it. The build made these choices:

- **The registry.** The eight names are `MY_DAY_FEATURES`, beside
  `FEATURES`, and `REGISTERED` holds both. The parser and `mode_for` are
  the same. `FEATURES` stays the email set, because four email tests pin it
  and done-when 7 forbids a change to them.
- **The call name.** A pass calls `shadow` as `my_day.automation`, with
  `features` set to the asked features. The agent name needs a dot
  (`AUTOMATION_AGENT_RE`), so `my_day` alone binds no agent.
- **The caps.** A pass reserves its calls before it asks. Past a cap it
  sends fewer requests, or none. A Redis failure asks nothing.
- **A key that is not ours** is stored as no value in `judged_value`.
- **The state.** `read_context` reads the role, the local time, the day
  hours, the One Thing, the corrections and the habit counts. No app read
  gives the goals, the projects, the 7-day deadlines or today's blocks to
  the module yet, so those fields stay empty.
- **The cool-down.** It is per org and shared with email, because both
  spend the same credits. A My Day call starts it on a 402 only. A 403 can
  refuse My Day alone, so it must not pause email's `on`.
- **One shadow for each request** (fix round 1). The helper gives no answer
  for a call with one failed request. So the pass runs one `shadow` for each
  packed request, at once, and a failed request costs only itself.
- **The member** is the session's address, exactly as `my_day_card_state`
  writes it, so the measure read of MD-6a-3 joins on equal text.
- **The org bucket** keys on the UTC date. The member bucket keeps the
  member's local date (§9.1 rule 8).

Known limits, for the next tickets:

- `configured_on()` reads `FEATURES` only. MD-6b must widen it before a
  `my_day.*` feature can run `on`.
- The pass writes the rows before it caches the answers. A row write that
  keeps failing asks again on each pass, until the member cap.
- The context is thin. The goals, the projects the member leads, the
  deadlines of the next 7 days and today's blocks have no reader yet. This
  is open for MD-6b, because a gate set from thin shadow data is weak.

### MD-6a-2 · The member pass — AGENT-SAFE

Files: `apps/services/gateway/gateway/my_day_loop.py`, and
`apps/services/gateway/gateway/routes/shell/cache.py` for `versions(member)`.

Done when:

1. A member whose context resolves to another org is skipped.
2. A member without `feature:email` gets no email signal.
3. Each of the three checks of §8.4 item 24 removes a departed member from
   the active set. The pass never calls `member_context` for that member.
4. A failed identity read, or `IdentityUnavailable`, skips the member and
   keeps it in the set.
5. The morning pass runs once, 15 minutes before `day_start_hour`.
6. A dirty member gets one pass in 2 minutes at most.
7. With each `my_day.*` mode `off`, the pass makes no facade call and writes
   no row (§10.9 item 9).
8. A member pass that runs past 30 seconds is cancelled, and the next member
   runs.
9. The sweep deletes `my_day_judgments` rows older than 30 days, of the
   bound org only.

Fences: `tests/unit/test_my_day_loop.py`, and
`tests/unit/test_my_day_loop_r8.py` for the departed checks.

**BUILT, 2026-10-14, on branch `my-day-feed-md6a2`.** The loop runs the
member pass after `run_org`, and it adds no migration and no flag. Each
`my_day.*` mode is `off` in production, so the pass stops at its first
line. The build made these choices:

- **The cheap gate.** `judging_on(org)` binds the org, asks
  `feed_decide.active_features`, and releases the org. It reads settings
  only. With it false, the pass reads no Redis, makes no identity read,
  sends no SQL and calls no `member_context`.
- **The sweep file.** `feed_decide.py` holds the judgments DELETE, beside
  `write_rows`, so one module writes the table. The loop calls it in its
  own `tenant_session(org)`. A failed judgments sweep keeps the card-state
  sweep.
- **The status read.** `_STATUS_SQL` reads `app_user.status` inside
  `tenant_session(org)`, with the org predicate beside RLS. No row counts
  as departed. A failed status read keeps the member, as a failed identity
  read does.
- **The judged key** holds the versions and the member's zone and day
  hours. So the loop finds that a member is not due from Redis alone. A
  pass reads the day again through `state._hours`.
- **The morning window** opens 15 minutes before the day start and closes
  at the day end. A loop that was down at the opening runs the pass later
  that day. The guard keys on the local date, so the pass runs once.
- **The dirty guard** is `SET key("my_day", "dirty", <member>) NX EX 120`.
  A guard in Redis also holds across two gateway processes.
- **Three members at once** across the tick, not for each org. The member
  pass runs outside the org's slot.

**Fix round 1, 2026-10-14.** The review found one P1 and three P2s:

- **The address case (P1).** The active set held a lower-case copy. The
  card states, the settings and the judgments key on the session's exact
  address, so a member with a capital letter lost their rows. Their zone
  also fell back to UTC. The set now holds the exact address, and
  `note_active` removes an old lower-case copy. The status read compares
  `lower(email)` on both sides. The loop's own guards key on the lower
  case, as the cache does.
- **One connection for the day read (P2-1).** A member who holds
  `feature:tasks` reads the day with no session around the read, as
  `state.apply_state` does.
- **A throttle after a failure (P2-2).** After a failed identity read, or
  a pass that failed, timed out or judged nothing, the guard
  `key("my_day", "hold", <member>)` holds the member for 120 seconds. An
  org in the `decide` cool-down skips the slow path. A morning pass that
  ends in `cooldown`, `cap` or `failed` gives its guard back.
- **The isolation test (P2-3)** now runs two members in one task, so it
  fails without the task of each member.
- **The morning pass takes the 2-minute guard too.** So a member who is
  dirty gets no second pass a tick later.

Known limits, for the next tickets:

- The R8 status case with the cutover off runs as the database owner. As
  the app role, the unbound `resolve_identity` read sees no `app_user` row,
  so every member reads as departed. That is the brick of H6, and the
  cutover is its fix.
- With `IDENTITY_CUTOVER` on, a member with two active memberships
  resolves to no org. The pass then treats them as departed (§8.4 item 24)
  and removes them from the set. Their next feed read adds them again.
- `app_user` holds one row for each address (`app_user_email_lower_key`),
  so no test can seed one address in two orgs. The R8 case of the org
  predicate reads org A's row while it is bound to org B.
- **For MD-6b.** With judging on, `cache.on_event` sets the org version on
  each `pm.*` event. So each active member becomes dirty, and gets a
  `feed_cards` pass each 2 minutes. MD-6b should limit the dirty pass to a
  member seen recently, for example in the last hour.

### MD-6a-3 · The measure read — AGENT-SAFE

Files: `apps/services/gateway/gateway/routes/shell/feed_decide.py`, and
`apps/services/gateway/gateway/routes/shell/__init__.py`.

Done when:

1. `GET /shell/feed/decide-measure` answers §10.9 item 10's shape, with the
   acted window of that item.
2. A member without `ADMIN_PERMISSION` gets 403.
3. An R8 test with two orgs shows that the read counts the bound org only.

MD-6b needs this read, so MD-6b does not start before it.

**BUILT, 2026-10-14, on branch `my-day-feed-md6a3`.** The route is in a new
module, `routes/shell/feed_measure.py`, because `feed_decide.py` holds 806
lines. The build made these choices:

- **The answer.** It is `{days, since, acts, features, top_five}`.
  `features` maps each `my_day.*` feature, and each mode with rows, to
  `{rows, agree_rate, pairs}`. `pairs` is a list of `{old, judged, rows}`.
  `top_five` maps `shadow` and `on`, and in each `old` and `judged`, to
  `{cards, acted, acted_rate}`. A rate with no rows is `null`.
- **No act at all.** `acts` counts the org's `my_day_card_state` rows with
  an `acted_at` in the span. At 0, each `acted_rate` is `null`. With
  `NEXT_PUBLIC_MY_DAY_FEED` off, no member can act, and MD-6b must not read
  0.0 against 0.0 as a tie.
- **Agreement.** A row agrees when `judged_value` equals `old_value`. A row
  with no judged value counts as a row, and never as an agreement.
- **The unit.** One `my_day.triage` row is one card in one pass. A card
  that two passes judge counts two times.
- **The day.** See §10.9 item 10. The SQL reads the zone through
  `pg_timezone_names`, with the rule of `state.zone_of`: no zone and an
  unknown zone are UTC.
- **The cost.** The database does the counting, so the read returns a few
  rows for any span. Each statement runs under a limit of 5 seconds
  (`statement_timeout`, local to the read). A read past it answers 503.
- **One zone reader.** `state.zone_of` is now public. `feed.py` and
  `needs.py` call it, and their copies are gone. A fence fails on a new
  copy.
- **The span.** `days` is 7 by default, from 1 to 30. The loop keeps 30
  days of rows (§13.2).
- **The gate.** `require_permission(ADMIN_PERMISSION)`, the value of
  `intent.py`. The SQL names the bound org, and RLS also binds it.
- **No BFF route.** MD-6b builds the UI, and adds the route beside
  `src/app/api/shell/feed/`.

Known limits, for MD-6b:

- `my_day_card_state` holds only the last `acted_at` of a card. A second
  act moves it, so an earlier act that was in the window can drop out.
- The read gives no agreement for each confidence band. A gate on
  confidence (§10.6 item 5) needs that split, so MD-6b must add it.
- A gate reads `shadow`, the `pairs` for resurface, and a `null` acted rate
  as "no data".

Fences: `tests/unit/test_feed_measure.py`, and
`tests/unit/test_feed_measure_r8.py` with `_DB_GATE`. The R8 file also runs
the read on a session that bypasses RLS.

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

**Owner: `proactive_reach.md` (WS-59), since 2026-10-14.** That spec builds
step 4 as RE-1 to RE-4, with the outbox, the budget and the channels. This
spec keeps the signals, the cards and the `my_day.nudge` question.

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
| Turn on `MY_DAY_COMPOSE`. Or set any `my_day.*` mode other than `off` in `DECIDE_FEATURE_MODES` while `MY_DAY_DECIDE_ORGS` names an org | It spends customer credits | Owner only. CLAUDE.md §3a rule 3. Residency is answered (D100) |
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
uv run pytest tests/unit/test_my_day_cache.py tests/unit/test_my_day_cache_r8.py tests/unit/test_my_day_loop.py tests/unit/test_my_day_loop_r8.py tests/unit/test_tenant_redis.py tests/unit/test_db_engine_seam.py -q  # MD-5
uv run ruff check apps/services/gateway/gateway/my_day_loop.py apps/services/gateway/gateway/main.py apps/services/gateway/gateway/routes/email/scheduler_hooks.py packages/acb_common/acb_common/tenant_redis.py  # MD-5
uv run pytest tests/unit/test_feed_decide.py tests/unit/test_feed_decide_r8.py tests/unit/test_feed_rank.py tests/unit/test_my_day_loop.py tests/unit/test_my_day_loop_r8.py -q  # MD-6a
uv run pytest tests/unit/test_email_decide_shadow.py tests/unit/test_email_decide_questions.py tests/unit/test_email_llm_cap.py tests/unit/test_background_ai_member.py -q  # MD-6a, email regression
uv run ruff check apps/services/gateway/gateway/decide_features.py apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/my_day_loop.py  # MD-6a
uv run pytest tests/unit/test_feed_measure.py tests/unit/test_feed_measure_r8.py tests/unit/test_org_access_enforcement.py -q  # MD-6a-3
uv run pytest tests/unit/test_shell_feed.py tests/unit/test_my_day_cache.py tests/unit/test_integrations_env_hardening.py -q  # MD-3b
npx vitest run src/lib/shell/feedApps.test.ts  # MD-3b
uv run pytest tests/unit/test_feed_task_kinds.py tests/unit/test_feed_task_kinds_r8.py tests/unit/test_shell_feed.py tests/unit/test_shell_feed_r8.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py tests/unit/test_shell_needs_r8.py tests/unit/test_projects_relations.py tests/unit/test_people_absences.py tests/unit/test_my_day_prefs.py tests/unit/test_my_day_state.py tests/unit/test_feed_decide.py tests/unit/test_no_new_clock_flakes.py -q  # MD-3c
uv run ruff check apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/routes/projects/personal.py apps/services/gateway/gateway/routes/people/selfservice.py  # MD-3c
npx vitest run src/lib/shell/feed.test.ts src/lib/shell/feedApps.test.ts  # MD-3c
uv run pytest tests/unit/test_feed_mail_kinds.py tests/unit/test_feed_mail_kinds_r8.py tests/unit/test_email_digest.py tests/unit/test_shell_feed.py tests/unit/test_shell_needs.py tests/unit/test_shell_needs_r8.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py tests/unit/test_email_follow_up_scan.py tests/unit/test_email_reply_zero.py -q  # MD-3d
uv run ruff check apps/services/gateway/gateway/routes/shell  # MD-3d
npx vitest run src/lib/shell/feed.test.ts src/lib/shell/feedApps.test.ts  # MD-3d
uv run pytest tests/unit/test_feed_crm_kinds.py tests/unit/test_feed_crm_kinds_r8.py tests/unit/test_crm_access.py tests/unit/test_crm_access_r8.py tests/unit/test_crm_aggregate.py tests/unit/test_crm_aggregate_r8.py tests/unit/test_shell_feed.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py tests/unit/test_no_new_clock_flakes.py -q  # MD-3e
uv run ruff check apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/routes/crm  # MD-3e
uv run pytest tests/unit/test_feed_people_chat_kinds.py tests/unit/test_feed_people_chat_kinds_r8.py tests/unit/test_people_absences.py tests/unit/test_pending_ask_flow.py tests/unit/test_pending_ask_store.py tests/unit/test_shell_feed.py tests/unit/test_feed_rank.py tests/unit/test_my_day_cache.py tests/unit/test_no_new_clock_flakes.py -q  # MD-3f
uv run ruff check apps/services/gateway/gateway/routes/shell apps/services/gateway/gateway/routes/people/absences.py apps/services/orchestrator/orchestrator/pending_ask.py  # MD-3f
npx vitest run src/lib/shell/feed.test.ts src/lib/shell/feedApps.test.ts  # MD-3f
uv run pytest tests/unit/test_email_decide_shadow.py tests/unit/test_email_decide_questions.py -q
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py -q
uv run pytest tests/unit/test_shell_feed.py tests/unit/test_shell_needs.py tests/unit/test_my_day_cache.py -q -rs  # MD-3a
uv run ruff check apps/services/gateway/gateway/routes/shell tests/unit/test_shell_feed.py  # MD-3a
npx vitest run src/app/calendar src/app/approvals src/lib/shell/  # MD-3a, in workbench/control_plane

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
