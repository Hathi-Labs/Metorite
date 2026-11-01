# Calendar — events, meetings and reminders

<!-- ste-tier: strict -->

- **Status:** **Events (§3, §4, §8) are DECIDED, 2026-10-29 (D138).** **Reminders
  (§5) are DECIDED, 2026-10-25 (D114).** **PN-1 (the web push platform) is
  BUILT, 2026-10-25, dark behind `WEB_PUSH_ENABLED`** (migration 306,
  `gateway/push/`, `public/sw.js`). **RM-1 (the reminder store and API) is
  BUILT, 2026-10-25, dark behind `CALENDAR_REMINDERS`** (migration 307,
  `routes/projects/reminders.py`). **RM-2 (the loop and the smart slot) is
  BUILT, 2026-10-26, dark** (`gateway/reminders/`). **RM-3 (the chat tools) is BUILT,
  2026-10-26, dark**: the tools refuse before a card while
  `CALENDAR_REMINDERS` is off. **RM-4 (a tap opens the chat) is BUILT,
  2026-10-29, dark** (`routes/chat_reminder.py`, `reminders/context.py`,
  `lib/shell/reminderOpen.ts`). **RM-5 (the Calendar UI and the New reminder form) is
  BUILT, 2026-10-30** (`app/calendar/lib/reminders.ts`,
  `components/RemindersPanel.tsx`, `components/ReminderFormModal.tsx`). It is not
  dark. It draws wherever `CALENDAR_REMINDERS` is on, and production has it on.
  The client has no flag of its own (§9). The owner
  moved RM-5 ahead of RE-9b on 2026-10-30 and added the form. RM-5b (the bell glyph
  on Tasks and Projects) is split out and not built. RM-6 to RM-10 are not built. Verified
  against code on 2026-10-25 (`origin/main` `894ddad27`): no `reminders`
  table and no `remind_at` existed then.
  **RM-2b (the reminder bell) is BUILT, 2026-10-30, dark** (`routes/shell/needs.py`
  holds the one `reminders` source). **RE-9 (the reminder push) is SPECIFIED,
  2026-10-30.** RE-9 splits into RE-9a and RE-9b. See §5.6 and §10.1.
  **RE-9a (the push message builder and the "Hide details on the lock
  screen" setting) is BUILT, 2026-10-30, dark.** It sends nothing.
  **RE-9b (a due reminder sends a real web push) is BUILT 2026-10-31.** It is
  not dark. It sends wherever `WEB_PUSH_ENABLED` and `CALENDAR_REMINDERS`
  are on, and production has both on. Its code is in migration 318 and in
  `reach/push_channel.py`, and its rules are the rulings a to k of §5.6.
  Repair round 1 (2026-10-31) added the rulings i to k to that list.
  Repair round 2 widened ruling k on the same day.
  **OB-8 (the chat asks a phone member to turn on notifications) is
  BUILT, 2026-10-30, dark** behind `PUSH_PROMPT_ORGS` (§10.4). No box
  has the flag on.
- **Board row:** **WS-CAL**. WS-66 for reminders and web push sits under
  it.
- **Asked by:** the owner, 2026-10-15. The Calendar must hold more than
  tasks. Add events, meetings and reminders, and fit them into the app.
- **Reads:** D12, D49, D52, D53, D54, D89, D96, D99, D101, D102, D103.
- **Owns:** the event store, the reminder rules, and the Calendar composer.
- **Does not own:** the task store (`project_management_app.md` §12), the
  cards (`my_day_feed.md`), delivery (`proactive_reach.md`), the shell
  (`navigation_shell.md`), or timeboxing behaviour (WS-21).

## 0. The answer

Add one new table, `calendar_entries`, for time that is not a to-do. It
holds events, meetings and read-only copies of Google and Outlook events.
Tasks stay the one to-do store, and a task block stays on `pm_task_personal`.
The Calendar then reads one list from two sources.

**A reminder has its own private store, `reminders` (D114).** It is a nudge,
not work, so it stays out of the task store. "Make it a task" promotes one
when it is work. Every reminder reaches the member only as a My Day card with
a reach class, so Reach (D103) stays the one way out. A reminder stays open
until the member closes it, and a tap on it opens the chat (§5).

The new table amends D54.5, and a meeting with colleagues amends D54.4. Both
are the owner's call in CE-0. CE-1 (lead time) and CE-3 (the composer) need
no decision change and can start now. WS-66 (§10.1) replaces CE-2 and CE-7a.

## 1. What exists today (measured 2026-10-15)

| Part | State | Where |
|---|---|---|
| A task block | One block per task per member, with `flexible` and `is_hard_date` | `pm_task_personal`, migration `187_projects_personal_timeboxing.sql`. The key is `(task_id, member_email)` |
| The read | `GET /projects/my/calendar?start&end` | `routes/projects/personal.py`, `my_calendar` |
| The grid | Day, Week and Month. Drag and resize a block | `src/app/calendar/CalendarView.tsx`, `components/TimeGrid.tsx`, `components/MonthGrid.tsx` |
| A tap on an empty slot | Opens `ScheduleSheet`, the Gap Filler. It picks a task that exists | `components/ScheduleSheet.tsx` |
| A new entry from the grid | None. No drag to draw, no free-form entry | — |
| A meeting | A pinned task block (`flexible = false`) | — |
| External sync | Not built. `/calendar/accounts` returns `[]`. `/calendar/sync` returns 501 | `routes/tasks/calendar.py` |
| Reminders | Four feed cards: `block_soon` at a fixed 30 minutes, `block_overlap`, `day_overbooked`, `no_plan_yet`. `block_soon` has reach class `interrupt`, the others `brief` | `routes/shell/feed.py` (`SOON`), `gateway/reach/classes.py` (`REACH_CLASS`) |
| "Remind me to…" | Becomes a plain task. The capture job removes the words | `routes/shell/intent.py`, job `capture` |
| WhatsApp | WAC-4b (branch `wa-calendar-blocks`): `create_personal_task` takes `block_start` and `block_end`, and `set_my_overlay` is a WhatsApp act | `acb_skills/whatsapp_acts.py`, `ALLOWED_ACTS` |
| Notes meetings | The `meeting` table holds a time that a member types | `infra/postgres/95_note_taker.sql`, `note_taker_app.md` §7.1 |

`calendar_focus_os.md` §5 sketches `gtd_time_blocks` with a nullable item and
the kinds `task`, `batch`, `break`, `ritual` and `external`. That table does
not exist. The table of §3.3 is that sketch with a name that is not `gtd_`.
It is the same design, so it is not a second seam.

## 2. Three things, and how they differ

| Thing | What it is | Has a to-do | Store |
|---|---|---|---|
| **Task block** | Time that a member gives to a task | Yes. Check it off | `pm_task_personal` (exists) |
| **Event** | A time with no to-do: a meeting, a call, a visit, a holiday, an all-day entry | No | `calendar_entries` (new) |
| **Reminder** | A moment when the member wants to hear about something | No. It may point at a task | `reminders` (new, D114) |

**An event** may have attendees, a location, a call link and a repeat rule.
It may link to a task, a CRM deal, a CRM contact or a Notes meeting. A link
does not make it a task. A member does not complete an event. The event
ends.

**A reminder** has three forms:

1. **On an object.** "Remind me about this on Friday at 9." It is a
   `reminders` row that points at one object. The object is a task, an
   email thread, a contact, a WhatsApp chat or a project.
2. **On an event, as a lead time.** "Tell me 10 minutes before." It is
   `lead_minutes` on my attendee row of the event. A lead time is not a
   `reminders` row, because it moves with the event.
3. **Standalone.** "Remind me at 5 to call Ravi." It is a `reminders` row
   with no link.

⚠️ **D114 changed forms 1 and 3 on 2026-10-25, before either was built.**
They were `pm_task_personal.remind_at` and a private task. The owner chose an
own store, because a reminder is not work.

## 3. Where the data lives

### 3.1 The options

| Option | Shape | Decision it touches | For | Against |
|---|---|---|---|---|
| **A. Everything stays a task** | An event is a pinned private task | None | No migration | A meeting shows in Tasks and Projects, counts in reports, and can be "done". A sync writes hundreds of task rows. A task has no attendee or RSVP |
| **B. A separate event store** | An `events` table and an `external_events` table, each with its own read | D54.5 | Clean events | Two new stores, two reads, two conflict checks. The Calendar merges three sources |
| **C. One entry table of kinds (recommended)** | `calendar_entries` with `kind` `event` or `external`. A task block stays on the overlay | D54.5 | One shape for every non-task time. One conflict check. Room for the WS-21 kinds later | One new store. The read joins two sources |

### 3.2 The recommendation

**Take C.** D54.5 says the Calendar has no store of its own and is a third
lens on the same rows. That stays true for tasks. C adds rows that are not
tasks, and no task lens reads them. The owner must amend D54.5
to allow it. This spec does not assume the amendment.

**A task block does not move in this spec.** A later decision may move the
block into `calendar_entries` as kind `task`, for several blocks per task.
The nullable `task_id` keeps that door open. That move is expand and
contract (R6) and is out of scope here.

### 3.3 The table sketch

`calendar_entries`. Tenant-scoped, FORCE ROW LEVEL SECURITY at the
`get_db()` seam (R5). Every column is nullable or has a default (R6).

| Column | Meaning |
|---|---|
| `id`, `organization_id` | The row and the tenant. `organization_id` defaults to `app.tenant_id` |
| `owner_email` | The member who owns the entry. For `external`, the member whose account it came from |
| `kind` | `event` or `external`. A CHECK holds the list |
| `title`, `notes`, `location` | Text |
| `starts_at`, `ends_at` | TIMESTAMPTZ. NULL for an all-day entry |
| `start_date`, `end_date` | DATE, for an all-day entry only. A date never moves with a time zone |
| `tz` | The IANA zone of the entry. A repeat rule expands in this zone |
| `recurrence` | An RFC 5545 RRULE, or NULL |
| `series_id`, `original_start` | One changed or cancelled instance of a series |
| `status` | `confirmed`, `tentative` or `cancelled` |
| `busy` | Default true. False for a holiday, or for an entry that blocks no time |
| `conference_url` | The join link of a call |
| `task_id`, `crm_deal_id`, `crm_contact_id`, `meeting_id` | Links. All nullable |
| `source` | `metorite`, `google` or `outlook` |
| `connection_id`, `external_id`, `etag` | For a synced entry only. `connection_id` names the `integration_connections` row |
| `created_at`, `updated_at` | Times |

`calendar_entry_attendees`. One row for each person on an entry.

| Column | Meaning |
|---|---|
| `entry_id`, `organization_id` | The entry and the tenant |
| `member_email` | Set when the person is a member of this organization. NULL for a guest |
| `email`, `display_name` | The guest's address and name |
| `role` | `organizer` or `attendee`. The owner has an `organizer` row |
| `rsvp` | `needs_action`, `accepted`, `tentative` or `declined` |
| `lead_minutes` | This member's own lead time. NULL reads the member default |

The lead time is on the attendee row for the reason that migration 187 put
the block on the overlay. Two people at one meeting want two lead times.

### 3.4 The read

`GET /projects/my/calendar` stays the one read. With `CALENDAR_EVENTS` on, it
adds the entries in the window that I own or attend, each with `kind`. With
the flag off, it returns what it returns today. The client draws by `kind`.
No lens other than the Calendar reads `calendar_entries`.

## 4. Meetings with invitees

### 4.1 Colleagues

D54.4 sets the Calendar scope to personal. A meeting with colleagues puts
one entry on several calendars, so the owner must decide this (CE-0, Q2).

**The proposed rule, after D12's private level:** the owner and each
attendee with a `member_email` see the entry. Nobody else sees it. There is
no free and busy view of a colleague, and no team calendar. Those stay out
of scope until the owner takes the team decision that D54.4 names.

A colleague whom the owner adds gets an `event_invite` card in My Day.
**Accept**, **Maybe** and **Decline** act in place (D101) and write my
`rsvp`. After I decline, the grid mutes the entry until I hide it.

### 4.2 Guests outside the organization

Metorite sends no mail to a guest itself (CLAUDE.md §3a rule 3, and Reach
sends only to the member, D103). A guest gets an invite only through the
member's own Google or Outlook calendar, after CE-11. The provider sends the
invite, from the member's own account.

Before CE-11, a guest is a name and an address on the entry, and Metorite
sends nothing. The composer says so, and offers "Connect Google Calendar".
After CE-11, the provider reports each guest's RSVP, and the sync of CE-10
writes it to the attendee row.

### 4.3 Notes meetings and the meeting bot

`note_taker_app.md` §7.1 lists four needs from the Calendar. This spec meets
each one:

| Need of the Note Taker | Answer here |
|---|---|
| Upcoming events with attendees and join links | `calendar_entries` with `conference_url` and attendee rows |
| A stable event id to a `meeting` link | `calendar_entries.meeting_id` |
| A change or a cancel | `status` and the sync of CE-10 |
| A "has a video link" signal | `conference_url IS NOT NULL` |

The event sheet offers **Prepare notes**, which makes or opens the linked
`meeting` row. It offers **Send notetaker** for a call link, through the
on-demand join that exists. Auto-join stays deferred and needs consent
(Note Taker D10). This spec does not change that.

## 5. Reminders (D114, decided 2026-10-25)

**Board: WS-66.** This section owns the reminder store, its states, the
loop that fires and checks back, the smart reschedule, the open-in-chat flow
and the reminder tools. It does not own the push transport
(`chat_run_continuity.md` §S7), the cards (`my_day_feed.md`) or delivery
(`proactive_reach.md`). §5.10 says what each of those docs gains.

### 5.0 The research record (2026-10-25)

The owner asked for product research first. These findings set the design.

| Finding | Source products | What we take |
|---|---|---|
| **No assistant checks back and reschedules.** ChatGPT Tasks, Gemini, Siri and Google Tasks fire once and forget | ChatGPT Tasks, Gemini Scheduled Actions, Apple Reminders | The loop of §5.4 is the gap in the market |
| **Nag apps repeat on a fixed timer**, from every minute to every hour, and never reschedule with judgement | Due, TickTick, Alexa follow-up reminders, Medisafe | Reschedule, do not repeat. Stop after a known limit |
| **Volume kills response.** Acceptance fell about 30% with each extra reminder in one clinical encounter | BMC Med Inform, PMC5387195 | One check-back a day. Batch the rest into the brief |
| **Give up openly.** Due stops after 5 repeats. Alexa never stops. Duolingo's give-up message reads as a guilt trip | Due, Alexa, Duolingo | A neutral "I stopped checking" with three exits |
| **A reminder in a task store loses its shape.** Google moved Assistant reminders into Google Tasks and lost the time of day and the location triggers | Google Tasks | An own store (D114.1) |
| **When, deadline and reminder are three fields.** Apps that blur them confuse users | Things 3, Planify | A reminder is not a due date and not a block |
| **A reminder stored in a chat thread breaks with the thread**, and a regenerated reply duplicates it | ChatGPT Tasks | The row lives in `reminders`. The chat only points at it |
| **Time zones break reminders.** Slack keeps the old zone after travel. Teams ignores working hours | Slack, Microsoft Teams | Store UTC and the zone. Fire in the member's current zone. Respect quiet hours |
| **Conditional follow-ups that cancel themselves** exist only for email | Superhuman, Boomerang | RM-7, across email, WhatsApp, the CRM and tasks |
| **Fake confirmation.** Early Gemini said "done" and saved nothing | Gemini | The reply quotes the saved row: its time and its zone |

### 5.1 What a reminder is

A reminder is a private nudge to one member. It has a time, one line of
text, a kind, and an optional link to one object. It holds no work of its
own.

- **Kind `action`.** "Get a haircut", "call the bank". It needs the member to
  confirm. The model picks the kind from the verb. The member can change it.
- **Kind `notice`.** "The office closes early today", "Priya's birthday". It
  closes when the member opens it.
- **The link.** `subject_kind` is `task`, `email_thread`, `crm_contact`,
  `wa_chat` or `project`, with `subject_id`. A link is a pointer. It copies
  nothing from the object, and the object's own visibility still binds when
  the chat reads it.
- **Private.** Only the member who owns it reads or writes it. A colleague
  on a linked task never sees it. An admin does not see it either.

### 5.2 The tables

Take the migration number at build time and check it again at merge (R1).
Both tables are tenant-scoped under FORCE ROW LEVEL SECURITY at the
`get_db()` seam, and `organization_id` defaults to `app.tenant_id` (R5).
As built (migration 307), five columns are NOT NULL with no default:
`member_email`, `title` and `fire_at` of `reminders`, and `reminder_id` and
`event` of `reminder_events`. R6 binds a column added to a live table, and
no running code wrote these new tables, so this is safe. Migrations 290 and
296 use the same shape.

`reminders`

| Column | Meaning |
|---|---|
| `id`, `organization_id`, `member_email` | The row, the tenant and the owner |
| `title`, `notes` | The text. `title` is at most 200 characters |
| `kind` | `action` or `notice`. Default `action` |
| `subject_kind`, `subject_id` | The optional link of §5.1 |
| `fire_at` | TIMESTAMPTZ. The next time the reminder fires |
| `tz` | The IANA zone that the member was in when the time was set |
| `state` | §5.3. A CHECK holds the list. Default `scheduled` |
| `next_check_at` | TIMESTAMPTZ. When the loop next looks at a `due` row |
| `first_fired_at` | The first fire. The 72-hour limit counts from it |
| `cycles`, `snoozes` | Counts. A reschedule that Metorite makes adds to `cycles`. A snooze that the member asks for adds to `snoozes` |
| `estimate_minutes` | The model's guess of how long the act takes. It sizes the grace of §5.4 |
| `important` | Default false. True gives the tighter ladder of §5.4 |
| `resolution` | `done`, `dropped`, `evidence`, `promoted` or `gave_up` |
| `resolved_at`, `promoted_task_id` | The end, and the task that a promotion made |
| `source` | `chat`, `app`, `whatsapp` or `api` |
| `source_session_id` | The chat session that made it, for the open-in-chat flow. The reminder does not depend on it |
| `created_at`, `updated_at` | Times |

`reminder_events` is the ledger. One row for each change and each delivery.

| Column | Meaning |
|---|---|
| `id`, `organization_id`, `reminder_id` | The row, the tenant, the reminder |
| `at` | TIMESTAMPTZ, default `now()` |
| `event` | 16 values: `created`, `fired`, `check_back`, `briefed`, `rescheduled`, `snoozed`, `opened`, `done`, `dropped`, `promoted`, `gave_up`, `evidence`, `delivered`, `failed`, `edited` and `reopened` |
| `channel` | `feed`, `push`, `chat` or `whatsapp`, for a delivery |
| `detail` | JSONB. For example the old and the new `fire_at`, or `from_state` |

- **`edited`** (added in review, 2026-10-25). A PATCH of any field except
  `fire_at` writes one `edited` row. Its `detail` names the changed fields,
  never their values. A PATCH of `fire_at` writes `rescheduled`. A field
  sent with its stored value is not a change, so a PATCH with no real change
  writes nothing.
- **`reopened`** (added in review, 2026-10-25). It records the Undo of
  §5.3.

Index `reminders (organization_id, state, fire_at)` and
`reminders (organization_id, state, next_check_at)` for the loop, and
`reminders (organization_id, member_email, state)` for the lists. As built,
a fourth index `reminder_events (reminder_id, at)` serves the ledger read and
the cascade.

### 5.3 The states

```
scheduled ──fire──▶ due ──answer──▶ done | dropped
    ▲                │
    │                ├─check back (once)─▶ due (next_check_at = the brief)
    └──reschedule────┤
                     └─limit reached───▶ unconfirmed ──answer──▶ done | dropped | scheduled
```

A promote is an answer. It ends in state `done` with `resolution` set to
`promoted`. So the state CHECK holds six values: `scheduled`, `due`,
`unconfirmed`, `done`, `dropped` and `cancelled`.

| State | Meaning | Open? |
|---|---|---|
| `scheduled` | Waits for `fire_at` | Yes |
| `due` | Fired, and waits for the member | Yes |
| `unconfirmed` | Metorite stopped chasing it. It shows in the Unconfirmed list | Yes |
| `done`, `dropped` | The member closed it, or evidence did. `resolution` says which: `done`, `evidence` or `promoted` for state `done`, and `dropped` for state `dropped` | No |
| `cancelled` | The condition of a conditional reminder cleared (RM-7) | No |

A `notice` reminder goes from `due` to `done` when the member opens it. Every
change writes one `reminder_events` row in the same transaction.

**Undo of an answer (reopen).** A reminder in state `done` with resolution
`done`, or in state `dropped`, can reopen. It goes back to the state that it
had before the answer, which the `done` or `dropped` ledger row records as
`from_state`. A reopen writes `reopened`. A promote cannot reopen, because it
made a task.

### 5.4 The loop: fire, check back, reschedule, give up

**The worker.** Copy `routes/email/automation/send_later.py`. It has a
60-second tick and the one tenant-discovery read for the orgs. Then it opens
`tenant_session(org)` for each org and claims with `FOR UPDATE SKIP LOCKED`.
Do not copy the
workflows scheduler, which is still on the unbound `get_db()`. The loop
claims two sets of rows:

- `state = 'scheduled' AND fire_at <= now()`: fire.
- `state = 'due' AND next_check_at <= now()`: the next step of the ladder.

**The ladder for an `action` reminder.**

| Step | When | What the member gets | Reach class |
|---|---|---|---|
| 1. Fire | `fire_at`, the time the member asked for | The card `reminder_due`: "Get a haircut · 15:00" | `urgent` (D114.3) |
| 2. Check back | `fire_at` plus the grace | The card turns to `reminder_check`: "Did you get the haircut?" | `interrupt` |
| 3. The brief | The next `day_start_hour` | One line in the morning brief, under "Still open from yesterday" | `brief` |
| 4. Reschedule | Right after the brief, when the member did not answer | Metorite picks a slot (§5.5), sets `fire_at`, adds 1 to `cycles`, and says so in the brief line: "Moved to Sat 10:00" | — |
| 5. Give up | `cycles` reaches 2, or 72 hours pass after `first_fired_at` | State `unconfirmed`. One brief line: "I stopped checking on 'haircut'. Make it a task, pick a time, or drop it?" Then nothing more | `brief` |

**The defaults.**

| Parameter | Default | Why |
|---|---|---|
| Grace before the check-back | `estimate_minutes` plus 30 minutes, kept between 30 minutes and 3 hours. 60 minutes when there is no estimate | A check-back before the act can end reads as nagging. Habitify checks at +30 minutes, Medisafe at +1 hour |
| Check-backs in one day | 1 for each reminder. No second ping that day | The clinical data on volume |
| Quiet hours | Outside `day_start_hour` to `day_end_hour`, in the member's `user_settings.timezone`. This is the Reach rule (`reach.plan.in_quiet_hours`). Reach and the loop read the same function (decision of 2026-10-25) | Steps 2 to 5 never land there. A step that would land there waits for the next `day_start_hour` |
| Weekends | No weekend rule in RM-2. The work and personal rule moves to RM-2c (decision of 2026-10-25) | Work and personal time |
| The member's snoozes | They do not count toward the limit. After 3 snoozes, the chat offers "Make it a task, or drop it?" once | The member is engaged, but the item keeps moving |
| `important` | Check back after 15 minutes, then every hour, at most 4 times, inside the cap | For the few items that need it |
| Escalating to another person | Off. Not in WS-66 | It reaches a real person (CLAUDE.md §3a rule 3) |

A `notice` reminder has step 1 only.

**The member's answer, at any step.** Done ends the reminder. Drop ends it.
"Not yet" with no time reschedules it with §5.5 and says the new time.

"Later" or a named time snoozes it to that time. It adds 1 to `snoozes`.
"Make it a task" makes a private task in the member's personal project. It
sets `promoted_task_id`, and ends the reminder in state `done` with
`resolution` `promoted`.

**Who counts what.** Every move that the member asks for adds 1 to
`snoozes`: a snooze, a named time and "Not yet". Only a move that Metorite
makes with no answer adds 1 to `cycles`. The give-up limit reads `cycles`.

**Every member move restarts the 72-hour window** (decision of
2026-10-26). A snooze, a named time, "Not yet" and a PATCH of `fire_at`
each clear `first_fired_at`, so the limit counts from the next fire. A move
out of `unconfirmed` also sets `cycles` to 0. A move that Metorite makes
keeps both. The ledger row of the move records the old `first_fired_at`.

**Two rules for RM-2, decided in the RM-1 review (2026-10-25).**

- **A member move out of `unconfirmed` is a fresh start.** It sets `cycles`
  to 0 and clears `first_fired_at`, because the member is engaged again.
  Otherwise the give-up limit trips again at once.
- **The loop skips a member who is not active in the org.** A removed member
  who is not purged keeps their rows, but no step fires and no push goes.

**As built (RM-2, 2026-10-25).** The worker is `gateway/reminders/loop.py`.
`gateway/main.py` starts it beside the send-later job, and it starts no task
while `CALENDAR_REMINDERS` is off.

- **One transaction for each step.** The loop claims one row by its id and
  its due predicate, with `FOR UPDATE SKIP LOCKED`. In the same transaction
  it moves the row and writes the ledger row. So two workers never fire one
  row twice.
- **Step 1** sets `first_fired_at` on the first fire only. The fire time is
  the tick, so a late tick does not move the check-back into the past.
- **Step 2.** The check time is the fire plus the grace. A check time in
  quiet hours moves to the next `day_start_hour`. A step that comes due in
  quiet hours writes nothing and waits.
- **Steps 3 and 4 are one transaction.** At the next `day_start_hour` after
  the check-back, the loop writes `briefed`. Then it calls `next_slot`, sets
  the new `fire_at`, adds 1 to `cycles`, and writes `rescheduled` with
  `{"by": "metorite"}`. The brief line says the new time, so the two rows
  belong together.
- **Step 5.** At the brief, a reminder that Metorite moved twice gives up. At
  any step of the ladder, a reminder that first fired 72 hours ago or more
  gives up. The `gave_up` row names the reason, `cycles` or `72h`. A member
  move clears `first_fired_at`, so the 72 hours start again at the next fire.
- **`important`.** The loop checks back after 15 minutes, then every hour, 4
  times in all. The brief comes after the fourth check-back.
- **The member check** is `my_day_loop.departed`, read once for each member
  in a tick. A failed read skips the member for that tick only.
- **The member's day** is `tasks.settings.member_settings`, the read of
  `GET /tasks/settings` with an address in any case, through
  `state.settings_day`, the rule of the feed. The loop has the lower-case
  address of the row, and the settings row has the spelling of the session.

**Words.** The check-back names the thing and gives every exit in one line.
Each try uses different words. No guilt, and no "you missed".

### 5.5 The smart slot

`reminders/slot.py` holds one function, `next_slot(member, reminder, now)`.
It is code, with no model call, so a test can hold it. It picks, in order:

1. A time that the member named.
2. The same time of day on the next suitable day.
3. The next free gap in the member's calendar that is long enough for
   `estimate_minutes`. The read holds task blocks today, and busy events
   after CE-4.

It never returns a time in quiet hours. The chat may propose another slot,
and the member's answer wins.

**Moved to RM-2c (decision of 2026-10-25).** The rules for work, personal
time, weekends and pinned blocks wait for RM-2c. RM-2 treats every reminder
the same.

The RM-2c rules are these. For a reminder linked to work, the slot keeps
inside working hours and skips the weekend. For a personal reminder, it
prefers time outside working hours. An errand never lands inside a block that
the member pinned.

**As built (RM-2, 2026-10-25).** `gateway/reminders/slot.py` is pure. The
caller gives the member's day, the busy blocks and `now`.

- **The order.** A named time wins when it is after now, outside quiet hours
  and outside every busy block. Then the time of day of `fire_at` on the
  first later day that is free at that time, up to 14 days. Then the first
  free gap of the member's day hours that is long enough for
  `estimate_minutes`, or for 15 minutes with no estimate. The gaps come from
  `tasks.calendar._free_intervals`, the rule of the planner.
- **Never now.** A gap that opens at now, such as a day start, gives the
  next quarter of an hour.
- **The last resort.** With no gap in 14 days, the slot is the next day
  start after them, moved past each busy block that covers it.
- **One zone.** The slot reads the member's current zone in
  `user_settings.timezone`, for the time of day and for quiet hours.
- **The busy blocks** are the member's task blocks, from
  `planning.LENS_SOURCE.busy_window`, the read of a window of the planner. It
  covers the days after now and the days after the reminder's own time. CE-4
  adds busy events. `my_calendar` is a route with a session, so the loop
  cannot call it.
- **`/reschedule` takes `"smart"`** as its `fire_at` (RM-2). It is a member
  move, so it adds 1 to `snoozes`, and its `rescheduled` row holds
  `{"slot": "smart", "by": "member"}`. A snooze and a PATCH still refuse
  `smart` with a 422.

### 5.6 Delivery

A reminder sends nothing itself (D103.1). The loop changes the row, and the
row makes a card. Reach plans the card like any other.

| Card kind | When | Reach class | Actions in place (D101) |
|---|---|---|---|
| `reminder_due` | Step 1 | `urgent`, outside the daily cap, at most 10 each day, may break quiet hours (D114.3) | Done · Later · Open |
| `reminder_check` | Step 2 | `interrupt`, inside the cap, never in quiet hours. An overflow goes to the brief | Done · Not yet · Drop · Open |
| `reminder_unconfirmed` | Step 5 | `brief` | Make it a task · Pick a time · Drop |

The Reach class column is the target of RE-9. In RM-2 each of the three
kinds had the class `none` in `reach/classes.py` (decision B1 of
2026-10-25). **Since RE-9b (2026-10-31) the three kinds are `urgent`,
`interrupt` and `brief`**, and they reach the member by web push only
(`reach/push_channel.py`). The Reach loop still sends only on WhatsApp, to
members on its allowlist, and D114.7 keeps reminders off WhatsApp. The
D114.3 exception is the own cap of `reminder_due` on web push.

**A class does not keep a card out of the WhatsApp brief** (review of
RM-2, 2026-10-26). The class gates an interrupt only, and the brief takes
every open card of Now and Needs you. So `reach/classes.NOT_ON_WHATSAPP`
holds the three kinds, and `reach/plan.on_whatsapp` keeps them out of each
WhatsApp brief, its template parameters, each interrupt and the preview.
The brief in the chat still shows them, because D114.7 allows the chat.
RM-9 lifts the rule.

- **The bell does not show reminders yet** (decision B2 of 2026-10-25). That
  is ticket RM-2b. The bell reads `/api/shell/needs`, so RM-2b needs a needs
  provider and a `KIND_ORDER` row for each kind. The paragraph "Settled
  2026-10-30 (RM-2b)" below holds the decisions.
- **The feed in RM-2.** Steps 1, 2 and 5 each give their card. Steps 3 and 4
  write their ledger rows, and the feed keeps the card until step 4 moves the
  row (decision B4 of 2026-10-25). The words of a Reach brief line belong to
  RE-9.
- **Web push.** Reach sends the card through RE-9 on the S7 transport. The
  payload holds the title and the time (D114.4), unless the member turned on
  "Hide details on the lock screen". Then it reads "Metorite: a reminder".
  The `url` is `/chat?reminder=<id>`. The paragraph "Settled 2026-10-30
  (RE-9)" below holds the decisions.
- **On the minute.** A reminder must go at its minute, not at the next
  member pass. RE-9 owns the test that accepts a push within 90 seconds of
  `fire_at`. If Reach is slower, RE-9 adds the `not_before` of the old ask to
  WS-59. `proactive_reach.md` owns how.
- **Closed before it goes.** Reach reads the row again at send time. A
  reminder that is done, dropped or moved sends nothing.

**Settled 2026-10-30 (RM-2b).** The supervisor settled these five points
after two spec audits. The one bell is the shell bell: `ShellBell.tsx` reads
`/api/shell/needs`. `NotificationBell.tsx` in Projects stays as it is.

1. **One source, one seam (option A).** `reminders` becomes ONE ungated needs
   provider in `routes/shell/needs.py`, with its feed `signals` thunk. It
   registers before `approvals`, so approvals still runs last. The feed-only
   registration at `routes/shell/feed.py:1218-1219` goes. A second source id
   is a second seam, and D87 to D89 ban it. The registry rules are at
   `needs.py:970-978`: a needs provider may not be gated, and it registers
   before every feed-only one. The feed runs the needs providers first, with
   no gate (`feed.py:1739-1740`).
2. **The flag off.** With `CALENDAR_REMINDERS` off, the provider raises
   `SourceAbsent`. Then `sources.reminders` reads `absent`. The pinned
   needs-source set in `test_shell_feed.py` gains the `reminders` key. The pins
   are the registry list (`:350-353`), the needs subset (`:360-363`) and the
   sources (`:394`). The MD-3b golden fixture gains that key too. The RM-2b
   PR updates both.
3. **The flags.** The bell row AND the My Day card need `CALENDAR_REMINDERS`
   only. `MY_DAY_PROVIDERS` no longer gates reminders. A `reminders` entry
   that stays in `MY_DAY_PROVIDERS` does no harm.
4. **The order.** The three kinds go at the END of `needs.KIND_ORDER`
   (`needs.py:204-205`). They sit below every existing needs kind and above
   every feed-only kind, in the order `reminder_due`, `reminder_check`,
   `reminder_unconfirmed`. Their `feed_rank.BASE` ranks rise to sit just below
   the lowest needs kind, so the rule of `test_feed_rank.py:62` holds
   unchanged. No whole number sits between `needs_reply` (44) and
   `agent_asks` (43). So the PR shifts the five needs bases up by k = 3.
   `overdue`, `approval`, `due_today`, `notification` and `needs_reply`
   become 63, 59, 55, 51 and 47 (`feed_rank.py:60-64`). `reminder_due`,
   `reminder_check` and `reminder_unconfirmed` become 46, 45 and 44, above
   `agent_asks` (43). The PR updates `test_feed_rank.py`: the `BASE` literal
   (`:41-58`), the score sum (`:148`) and the seed expectations (`:312-319`).
   The fence line: the three reminder bases sit between `agent_asks` and the
   lowest shifted needs base.
   `NEWEST_FIRST` (`needs.py:210`) needs a key for each kind, because
   `needs.py:1062` reads it. The three keys are `False`: the oldest reminder
   shows first, like `overdue`.
   The three reminder kinds share one signal id in
   `test_feed_fold_fingerprint.py`. Their pairs leave `FEED_ONLY_PAIRS`
   (`:185`), because the folded kind is now in `KIND_ORDER`. They land in
   `test_only_these_folds_carry_a_needs_kind` (`:206`). The PR adds the three
   pairs to that set. The comment says that a real reminder row yields one
   kind and never folds. The check `len(FEED_ONLY_PAIRS) > 40` (`:198`) still holds.
   The count goes from 53 to 50.
5. **The row is a link only.** The `href` is `reminder_href(id)`
   (`feed.py:1161`), which gives `/chat?do=new-chat&reminder=<id>`. The gate
   is `feature:projects`. The client gets the app `calendar` in `NeedsApp`,
   `NEEDS_APP_HREF.calendar = "/calendar"` and the three kinds in `NeedsKind`
   and `KIND_ORDER` (`src/lib/shell/needs.ts:26-27, 85, 174`). Done, Later
   and Open acts on the bell row wait (D101). RM-2b shows NO Calendar count in
   the sidebar. The server keys `sources` and `totals` by the source id
   `reminders`, and the client reads them by `NeedsApp`. The bell rows still
   show, because they filter on `item.app`. A Calendar count waits for a later
   ticket.
6. **The code moves.** `_reminder_signal`, `reminder_href` and
   `REMINDER_KINDS` move from `feed.py` into `needs.py`, because `feed.py`
   imports `needs.py`. `feed.py` does not re-export them. The PR updates the
   importers: `test_reminder_loop.py` (`:236-240`, and the calls to
   `feed._reminders` at `:527, :707, :749`), `test_feed_fold_fingerprint.py`
   (`:144, :150`) and `test_reach_loop.py` (`:1162, :1263`). The check at
   `test_reminder_loop.py:236-240` now asserts `needs` True and `gated` False.
7. **Member-visible changes (intended).**
   - My Day reminder cards rank above the MD-3 feed-only cards.
   - A feed "drop" from `decide` on a reminder card moves it to Later,
     because `feed_decide.is_needs_card` reads `KIND_ORDER`
     (`feed_decide.py:122, 201-204`). It does not delete or resolve the
     reminder. The resolve of the reminder is the way to close it.
   - The bell poll runs one more provider.
   - `sources` in the needs answer gains `reminders`.

**Settled 2026-10-30 (RE-9).** The supervisor settled these six points. The
sender is `push.sender.send_to_member` (`push/sender.py:189`) with
`PushMessage` (`push/sender.py:73`). No code outside `push/` calls it today,
except `POST /push/test`. WS-51 S7 (H-289) reuses the same send step later.

1. **The gate.** The push channel runs when `WEB_PUSH_ENABLED` and
   `CALENDAR_REMINDERS` are on. It does NOT need `REACH_ENABLED`,
   `REACH_ORGS` or `REACH_MEMBERS` (`reach/flags.py`). Those flags gate
   WhatsApp only, and D115 keeps WhatsApp off. This is how the approval of
   both push flags for every org (D114.8) takes effect. D-RE-1 still holds:
   the reminder reaches the member as a card kind with a reach class, built
   in `gateway/reach/`.
2. **The trigger and the send.**
   - **One transaction.** `_fire`, `_check_back` and `_give_up` in
     `reminders/loop.py` (`:240, :271, :259`) each call ONE function in
     `gateway/reach/`, for example `reach.push_channel.plan_reminder_push(db,
     ...)`. It takes the own session `db` of the reminder loop. It writes the
     `reach_outbox` row on that session, through a new SQL helper in
     `reach/outbox.py` that takes a session. `outbox.plan` opens its own
     `tenant_session`, and the loop does NOT use it. RE-9b adds no new
     `tenant_session` or `get_db` site (R5). The function picks the card kind
     and the class, and it plans one row with the channel `web_push`.
   - **The dedupe key.** The key is `reminder:<reminder id>:<reminder_events
     id>`. The event row is the ledger row that the step wrote: `fired`,
     `check_back` or `gave_up`. So each step of each cycle gets its own key.
     The ledger helper `routes/projects/reminders.py:405` returns nothing
     today, and RE-9b makes it return the id of the row.
   - **The send step.** A function in `gateway/reach/` sends each planned
     `web_push` row through `send_to_member`. `run_org` in
     `reminders/loop.py:340` calls it after the commit of the steps. (Ruling
     k moves this call to one send phase of `run_tick`.) It needs
     no `REACH_*` flag, and `reach/loop.py` never runs it. It marks the row
     `sent` or `failed`. It writes the `reminder_events` row `delivered` or
     `failed` (migration 307 allows both, and the channel `push`). The loop
     never calls the sender itself.
   - **The retry.** A crash leaves a planned row, and the next tick retries
     it, so delivery is at least once. The push `tag` is the reminder id, so
     a duplicate replaces the first on the device. At send time, the step
     reads the reminder row again. A done, dropped or moved reminder sends
     nothing. The claim of `reminders/loop.py:149-152` (`FOR UPDATE SKIP
     LOCKED`) stops two workers from firing one row.
   - **The body.** The outbox `body` of a `web_push` row holds the fixed text
     "reminder", and never the title. The send step builds the payload from
     the reminder row with the RE-9a builder.
3. **The classes.** `reach/classes.py:73-75` changes the three kinds from
   `none`. `reminder_due` is `urgent`. It is outside the WhatsApp and
   interrupt cap. It sends at most 10 each day for each member, and it may
   break quiet hours. `reminder_check` is `interrupt`. It is inside its cap
   and never in quiet hours. An overflow sends nothing, and the card stays in
   My Day. `reminder_unconfirmed` is `brief`, so it sends no push in quiet
   hours. `reach.plan.in_quiet_hours` (`reach/plan.py:137`) decides quiet
   hours. `NOT_ON_WHATSAPP` (`reach/classes.py:88`) stays.

   **The caps.** Each cap counts for one member and one member local date.
   It counts outbox rows with the state `sent` on the channel `web_push`
   only. `reminder_due` has its own counter, with a limit of 10. The eleventh
   sends nothing. `reminder_check` uses the interrupt daily cap of Reach: the
   setting `reach_daily_cap` (`packages/acb_common/acb_common/settings.py:560`,
   default 3), read by `flags.daily_cap()` (`reach/flags.py:64`). It counts
   `web_push` rows of kind `reminder_check` only. As built, `_SENT_SINCE_SQL`
   and `_URGENT_SINCE_SQL` (`reach/outbox.py`) count `channel = 'whatsapp'`
   only, so a push row never counts in the WhatsApp cap.
4. **The setting.** The key `hide_push_details` joins `PREF_KEYS`
   (`routes/shell/prefs.py:62`) in `user_settings.my_day_prefs`. The existing
   prefs route writes it. The route needs four code points, not only
   `PREF_KEYS`. Widen `PrefPut.key` (`:135`). Add a boolean arm to `_CLEAN`
   (`:127`). Add the key to `stored_prefs` (`:144`). `stored_prefs` drops
   unknown keys, so without it the toggle never reads back. The toggle sits in
   `NotificationsSection.tsx` (`src/app/people/components/`). With the key on,
   the payload title reads "Metorite: a reminder". No title or time text is in
   the payload. When one device holds several accounts, the payload names the
   org. The org name goes in `body`, because `sw.js` reads only `title`,
   `body`, `url` and `tag`. With the key on, `body` holds the org name and no
   reminder text.
5. **The migration.** It takes the next free number at build time. RE-9b
   took 318 on 2026-10-31 (`infra/postgres/318_reach_outbox_web_push.sql`).
   Check it again at merge (R1). It widens
   `reach_outbox_channel_known` to add `web_push`, and `reach_outbox_kind_known`
   to add the three reminder kinds (migration 266, `:51-52`). It is expand
   only (R6). `outbox._PLAN_SQL` fixes the channel `whatsapp`
   (`reach/outbox.py:40`). Before RE-9b, `_SENT_SINCE_SQL` counted interrupt
   rows of any channel. As built, `_SENT_SINCE_SQL` and `_URGENT_SINCE_SQL`
   count `channel = 'whatsapp'` only. So the budget reads count by channel,
   and `reminder_due` has its own counter. `proactive_reach.md` §7.1 states
   the same.
6. **The 90 seconds.** The acceptance measures from `fire_at` to the
   `send_to_member` call, with a frozen clock. Delivery to the device is the
   owner's device check, the PN-1 HANDOFF item.

**Rulings of RE-9b, 2026-10-31.**

Rulings a to d come from the spec-auditor, e to g from the builder, and h
from the supervisor. Rulings i to k come from repair round 1 (2026-10-31),
after the review of the Opus reviewer and the verifier. The code is
`gateway/reach/push_channel.py` and the push helpers of
`gateway/reach/outbox.py`. The fence is `tests/unit/test_reach_push.py`.

- **a. The gate.** The plan step and the send step run only while
  `push.flags.enabled()` and `reminders_enabled()` are both true. With push
  off, the plan step writes no row. So a later flip sends no burst of old
  pushes.
- **b. The bound of a retry.** A planned row older than the TTL of its kind
  becomes `skipped`, reason `expired`. The TTLs are 3,600 s, 14,400 s and
  43,200 s (`push_message.ttl_of`). Missing VAPID keys keep the row
  `planned`, reason `no_keys`. A later tick tries it again, never past the
  TTL.
- **c. No device.** A member with no device gives `skipped`, reason
  `no_device`, and no ledger row. One device or more that took the push
  gives `sent` and a `delivered` ledger row. Every device failing gives
  `failed` and a `failed` ledger row. Both ledger rows have the channel
  `push`.
- **d. The caps at send time.** The send step counts each cap again just
  before each send, and it sends the rows of one member in order. The count
  holds the `sent` rows and each row that another send step holds now. A
  transaction advisory lock on the org and the member serialises two claims
  of one member, so a second worker cannot pass the cap. A cap counts the
  member's own date (`Day.feed_day` in the zone of the member), never the
  UTC date. Each rule has an R8 fence. In the first, two claims wait for
  each other after the count. In the second, a member in Kolkata has two
  local dates on one UTC date, and the tick reads a UTC clock.
- **e. The claim.** A send step claims a planned row before it sends: it sets
  `reason = 'sending'` and `sent_at` to the claim time. The claim holds for
  `outbox.PUSH_LEASE_S` (120 s). No session stays open during the send. A
  second worker skips a row under a live claim. The claim of a step that
  crashed runs out, and a later tick sends the row again. Migration 318 adds no column for
  this.
- **f. A stale row.** The send step reads the reminder again. The state must
  still be `due` (for `reminder_due` and `reminder_check`) or `unconfirmed`.
  No newer step may be in the ledger: `fired`, `check_back`, `briefed`,
  `rescheduled`, `snoozed`, `done`, `dropped`, `promoted`, `gave_up` or
  `reopened`. Otherwise the row becomes `skipped`, reason `closed` or
  `moved`. A retry of a `reminder_check` or `reminder_unconfirmed` row in
  quiet hours becomes `skipped`, reason `quiet`.
- **g. A failed plan.** The plan insert runs in a SAVEPOINT. An error logs
  `reach.push.plan_failed` and plans nothing. The step and its ledger row
  stand, so the card stays in My Day.
- **h. Staging.** Staging sends no reminder push to a copied device: the
  export and the staging refresh both remove push_subscriptions.
  (`infra/staging/minimise.sql` and `scripts/staging_refresh.sh`. Fences:
  `tests/unit/test_staging_export.py` and `tests/unit/test_staging_box.py`.)
  The rows on staging at merge time are deleted once by hand at merge, and
  the refresh deletes them each night after that.
- **i. The hide setting reads every spelling.** A `user_settings` row keys
  on the spelling of the session that wrote it, for example
  `Ravi.K@acme.com`. The send step knows only the lower case of
  `reminders.member_email`. So `prefs.hide_push_details` reads the key over
  every spelling of the address, and one `true` hides the details. A
  lower-case row with no key never outvotes it. The read fails closed, as
  the agent switch of EA-6i does.
- **j. A late step sends no push.** A step that runs more than the TTL of
  its kind after its due time plans no push. An outage or a flag flip must
  not send a burst of old reminders, and `reminder_due` may break quiet
  hours. The fire, its ledger row and the My Day card stay as they are. The
  plan step writes no row, so the step uses no slot of a cap.
  - **The due time.** `reminder_due` counts from `fire_at`.
    `reminder_check` and `reminder_unconfirmed` count from `next_check_at`,
    the time that their step came due. A check-back that waited for quiet
    hours counts as late too.
  - **At the send.** The send step also ends a `reminder_due` row more than
    its TTL after `fire_at` (`skipped`, reason `expired`). The other two
    kinds keep the TTL from the plan time of ruling b.
- **k. Fires first, then one send phase.** `run_tick` takes the steps of
  every org first, and a step makes no network call. Then one send phase
  sends the planned pushes of each org, under one deadline for the whole
  tick: no row starts after `loop.SEND_PHASE_S` (30 s). A row that started
  runs to its end, inside `push.sender.SEND_BUDGET_S` (20 s). A row that did
  not start stays `planned`, and the next tick sends it.
  - **The clock.** The quiet hours of a row read the clock at the start of
    that row, not the start of the tick.
  - **No cancel.** No `asyncio.wait_for` cancels a send. A cancel in the
    middle of `send_to_member` would leave its device sends running and its
    row claimed.
  - **An error.** A send that raises an error other than `RuntimeError`
    releases its row at once, with the reason `error`. An error in the send
    phase never stops the loop.
  - **No org starves** (repair round 2). Each tick starts the send phase at
    the next org (`loop._send_turn`). In the first round, each org starts
    rows only in its fair share of the time left. The share is that time
    over the count of orgs still to go, and the first row of each org
    always starts. A second round gives the time still left to the orgs
    that still hold rows. So each org goes first once in N ticks, for N
    orgs, and no org waits longer than that. An org that fills the phase
    on every tick can delay the next org by up to N - 1 ticks.
  - **A stop.** A stop of the loop ends the send phase after the row in
    progress. The rows left stay `planned`.
  - **The 90 s.** A fire comes within one tick of `fire_at`: `TICK_SECONDS`
    (60 s), plus the short work of the tick before. Its `send_to_member`
    call starts inside the send phase of that tick, so at most
    `SEND_PHASE_S` (30 s) after the phase starts. A healthy push service
    sends each row in well under a second, so the call starts a few
    seconds after the fire, inside the 90 s.

**As built (RM-2, 2026-10-25).** The cards come from the feed source
`reminders` in `routes/shell/feed.py`. Code computes each card from the row
when the feed reads it, and nothing stores a card.

- **The kind.** A `due` row gives `reminder_due` until its check-back, and
  `reminder_check` after it. An `unconfirmed` row gives
  `reminder_unconfirmed`. A `scheduled` row gives no card.
- **The read.** `reminders.my_open_cards` reads the member's rows and the
  last ladder step of each, in one tenant session. The source needs
  `feature:projects`. With `CALENDAR_REMINDERS` off, the source is `absent`
  before any read.
- **The cut.** The read puts `due` rows first, then the newest step. So old
  `unconfirmed` cards never push out a reminder that just fired.
- **The gate of the feed.** In RM-2 the source ran only while
  `MY_DAY_PROVIDERS` listed `reminders`, as each feed source since MD-3b
  does. **RM-2b changes this** (decision 3 above). `reminders` becomes a
  needs provider, and a needs provider has no gate. So the bell row AND the
  My Day card need `CALENDAR_REMINDERS` only, and the flip of reminders sets
  that one flag. `MY_DAY_PROVIDERS` no longer gates reminders. A `reminders`
  entry that stays in it does no harm. The MD-3b golden fixture gains the key
  `sources.reminders` (decision 2).
- **The card.** The app is the Calendar, and the client calls the three kinds
  "Reminder", "Check-back" and "Unconfirmed". The actions in place of the
  table wait for the client (D101).
- **The link.** The one act opens `/chat?do=new-chat&reminder=<id>`. Before
  RM-4 the chat reads only `do`, so the link opens a new chat in the one
  assistant. A bare `/chat?reminder=<id>` would open the chat that was open
  last. RM-4 makes the chat read `reminder` and write the first message
  (§5.7).
- **The order.** `reminder_due` is in the group `now`. The other two are in
  `needs_you`. The ranks in `feed_rank.BASE` are 31, 29 and 25, below every
  needs kind. `reminder_unconfirmed` sits just above `digest_ready` (24).
- **The refresh.** After each write, the loop bumps the member's My Day
  version (`cache.bump_member_safe`). After its commit, each answer and each
  move of the member does the same, with the session's org. The next
  `fire_at` or `next_check_at` is the edge of the read cache.

### 5.7 A tap opens the chat

The owner's words: "When I open the reminder on mobile, it should open the
chat interface and remind me what the reminder is about. I then chat with the
AI about what to do next."

1. **One link for every surface.** The push, the bell card, the feed card
   and the Calendar pin all open `/chat?reminder=<id>`. In the installed web
   app on a phone, the chat opens full screen. On a desktop, the one
   assistant opens in the top bar (D99).
2. **Code builds the first message.** It makes no model call, so it is
   instant and free. An example: "Reminder · Get a haircut · 15:00 today.
   Did you do it?" It shows the linked object in one line. For a task, that
   is the title and the status. For an email thread, it is the subject and
   the last sender.
3. **The chips.** Done · Not yet · Later · Drop · Make it a task. Done, Not
   yet, Later and Drop act at once with Undo (D-CF-5), because each is a
   reversible write on the member's own record. The Undo of Done and Drop is
   the reopen of §5.3. "Make it a task" makes a task, so it cannot reopen, and
   it asks one confirm. The chat says the result: "Moved to Sat 10:00".
4. **A typed answer.** It goes to the model with a `reminder` context block.
   The block holds the row and its ledger. It also holds a summary of the
   linked object, when the member can read that object. The tools of §5.8 are in scope. The model can then help
   with the act itself, for example draft the follow-up email. A draft that
   sends still asks a confirm.
5. **The open counts.** It writes an `opened` ledger row. A `notice`
   reminder closes on open. An `action` reminder stays `due`.
6. **No answer after the open.** The ladder of §5.4 goes on. An open is not
   an answer.

**As built (RM-4, 2026-10-29).** Dark behind `CALENDAR_REMINDERS`. The
server builds every word, and no model runs.

- **The link.** `ChatFullView` reads `?reminder=<id>` and opens a fresh
  chat. `/chat?reminder=<id>` and `/chat?do=new-chat&reminder=<id>` both
  work. The page takes only a UUID. The old page (`ChatPageInner`, the
  `NEXT_PUBLIC_SHELL_CHAT` flag off) does not read the link.
- **One call.** The dock waits for the fresh chat, then calls
  `POST /chat/sessions/{id}/reminder-open` once, with `{reminder_id}`. It
  holds the chat as for the brief, with the same 4 s cap. A reminder's chat
  gets no brief.
- **The route** is `routes/chat_reminder.py`. The flag off gives 404 before
  the body is read. The member needs `feature:projects`. The route keeps
  each session check of `post_brief`. A room shared with a second person
  gets 204, with no row and no ledger write.
- **The ONE open** is `reminders.open_reminder`. It loads the member's own
  row, or answers 404. When the reminder is open, it writes one `opened`
  row. A `notice` in state `due` then closes as `done`. Its `done` row
  records `from_state` and `via: open`, so `/reopen` undoes it. An `action`
  reminder stays `due`. A closed reminder writes nothing and answers with
  its state, not 409.
- **The first row** goes in through the brief's mint path
  (`chat_brief._post_row`). Its id is `reminder-<id>`, so the same chat never
  gets a second row and never writes a second `opened` row. A new tap opens
  a new chat and writes one more `opened` row.
- **The words** come from `gateway/reminders/context.py`. The time is in the
  row's `tz`, else the member's saved zone. A task shows its title and its
  status, and a project its name, through the member's own visible read. An
  object that the member cannot open shows its kind only. RM-4 reads no
  email thread, CRM contact or WhatsApp chat.
- **The context block** is a list of facts. The dock publishes them while
  that chat is on screen. Each title, note and status sits in «marks». The
  facts name the reminder id and carry at most 8 ledger rows. They leave when
  the member opens another chat, or when a chip closes the reminder. Undo
  brings them back. A typed answer goes to the model with them (item 4), and
  the run path did not change.
- **The chips** are the `reminderCard` template, drawn with the brief's pill
  row. Done and Drop call `/resolve`. Not yet calls `/reschedule` with
  `smart` and says the new time, for example "Moved to Sat 10:00". Later
  sends "Snooze this reminder" to the model, which asks for a time. Make it
  a task sends "Make it a task", so the model shows the one confirm of
  `promote_reminder`. No chip calls `/promote`.
- **Undo** (decision of 2026-10-29). After Done or Drop, the card offers one
  Undo chip, which calls `/reopen`. `UndoToast.tsx` binds the task store, so
  it does not fit. The chip is the pill row that the card already draws. A
  reminder that arrived closed, such as a `notice` that the open closed,
  offers no chip.
- **Not in RM-4.** Not yet and Later have no Undo, and Later has no time
  picker. After a reload, the card draws the state of its row and reads no
  newer state. RE-9 sends the real push that a member taps.
- **Fences.** `tests/unit/test_reminder_context.py`,
  `tests/unit/test_reminders_r8.py` (the open cases) and
  `src/app/chat/reminderOpen.test.ts`. The vitest config runs
  `*.test.ts` only, so the fence is not the `.tsx` file of §10.1.

### 5.8 The chat tools

**The routes** live in a new module, `routes/projects/reminders.py`, under
`/projects/my/reminders`. The Calendar already reads `/projects/my/calendar`,
and the promote helpers are in `routes/projects/personal.py`. The
`/projects` router gives the gate, `feature:projects`, as it does for the
Calendar read. No second gate applies.

The tools go into `skill_projects` beside `create_personal_task`.
`test_projects_chat_coverage.py` walks the `/projects` router, so each new
route needs a manifest row in `skill_projects/manifest.py` in the PR that
adds the route. RM-1 added each row as a planned tool. RM-3 made it real.

| Tool | Does | Confirm |
|---|---|---|
| `set_reminder` | Makes a reminder: `title`, `at` or `in_minutes`, `kind`, `subject_kind` and `subject_id`, `estimate_minutes`, `important` | One card, until CF-6 |
| `list_reminders` | Lists my reminders by state and window | A read |
| `update_reminder` | Changes the text, the time, the kind, the link, the estimate or `important` (PATCH) | One card, until CF-6 |
| `resolve_reminder` | Done or drop | One card, until CF-6. Its Undo is `reopen_reminder` |
| `reopen_reminder` | The Undo of a done or a drop (§5.3) | One card, until CF-6 |
| `promote_reminder` | Makes a private task in my personal project, and ends the reminder | One card. It cannot reopen |
| `reschedule_reminder` | A snooze, a named time, or `smart` (§5.5) | One card, until CF-6 |

**As built (RM-3, 2026-10-25).** RM-1 built eight routes, and RM-3 built
the seven tools over them. `reschedule_reminder` reaches two routes:
`/snooze` with `snooze=yes`, and `/reschedule` for a named time. The read is
in `reads.py`, the five class B writes are in `writes.py`, and
`promote_reminder` is class C in `guarded.py`. Each request field is now in
`SENDS`, so RM-3 closes gap G24 of `projects_agent_parity.md` §3.3. The fence
is `tests/unit/test_reminder_tools.py`.

- **Confirm.** One card for each write, until CF-6 exists. Then set,
  update, resolve, reopen and reschedule act at once with Undo. RM-3 shows
  each card through `writes._confirm`, the one door. It adds no second card
  door and no act-at-once path (`chat_first_surface.md`, CF-6).
- **Two gates before the card** (review round 1). Each write asks
  `writes._reminder_gate` first. With `CALENDAR_REMINDERS` off, the routes
  answer 404, so the tool refuses and says that reminders are not on. A
  WhatsApp run, or any run with no card channel, cannot confirm a reminder
  until RM-9 adds the acts to `whatsapp_acts.ALLOWED_ACTS`. So the tool
  refuses there too. Both refusals tell the member plainly, and the tool
  tells the model to make no task in place of a reminder (owner, 2026-11-01,
  task 1228). `list_reminders` says the same when the flag is off.
- **Time.** The tool resolves the time itself. `at` is ISO 8601 with an
  offset, or a wall time with no offset, such as `2026-10-31 17:00`. The tool
  reads a wall time in the member's saved zone, `user_settings.timezone`,
  through `GET /projects/my/today`. `in_minutes` sets a time from now.
- **A time that does not exist.** A date or an hour that does not exist,
  such as `2099-02-30 09:00` or `24:00`, gets a refusal and no card. A wall
  time in a gap of the clock, such as 02:30 on the morning the clock jumps to
  03:00, becomes the real time. The card and the receipt then show the same
  time.
- **A saved zone of UTC.** `user_settings.timezone` defaults to UTC, so a
  member who never chose a zone looks like a UTC member. When the saved zone
  is UTC, the card shows one more line: "Your saved time zone is UTC. If that is
  wrong, change it in Calendar settings." The tool still saves.
- **No zone, no guess.** When the member has no saved zone, or the saved
  zone is not valid, the tool refuses a wall time and says why. It never
  reads a wall time as UTC. A time with an offset still saves, and the
  reply then shows UTC and says that the member has no saved zone.
- **The route still refuses a time with no offset.** The tool attaches the
  zone before it calls the route. So "remind me at 5" saves one row at
  17:00 in the member's zone.
- **A time in the past.** The tool refuses it, and the refusal states the
  member's time now. The model then takes the next day and says so.
- **The context of the model.** The executor gives the model the date only
  (`today_line`). RM-3 did not change the executor. `list_reminders` and
  each time refusal state the member's time now and the zone.
- **"Tomorrow morning"** reads `day_start_hour`. Since review round 1,
  `GET /projects/my/today` sends `day_start_hour` by the rule of
  `personal.member_day`, with 7 when the member saved no hour. The model passes
  `at="YYYY-MM-DD morning"`, and the tool takes that hour. "Later today"
  reads 3 hours on, inside working hours.
- **`smart`.** `reschedule_reminder` sends `smart` to `/reschedule` only.
  The create, the PATCH and the snooze refuse it, so the tool refuses it
  for each of them before the card. RM-2 adds the slot to the route. Until
  then the route answers 422, and the reply says that nothing moved.
- **A snooze is at most 366 days.** The tool checks the bound of the route
  (`SNOOZE_MAX`) before the card.
- **An end date includes its whole day.** For `list_reminders`, a date
  alone as `end` is the midnight that starts the next day, as the `calendar`
  tool reads it. So a start and an end of one date read that one day.
- **Answer first.** On an unclear time, the model saves the best guess, and
  the reply states it with one correction: "09:00 tomorrow, or did you mean
  tonight?" (the CRM rule of answering first). Each tool description says
  this.
- **The reply quotes the saved row.** It states the absolute time and the
  zone, for example "Fri 31 Oct, 09:00 IST". `reads.saved_when` reads the
  route's `fire_at` and `tz`, and never the argument of the tool. A reply
  with no reminder id tells the model not to say that the reminder is
  saved.
- **A line for the model only.** Guidance for the model, such as "quote this
  time exactly" or "reopen_reminder undoes this answer", goes on a line that
  starts with the data legend (`reads.for_the_model`). The receipt card of
  the member drops that line, as it drops the legend of a read.
- **A reminder is not a task.** Each tool description says so, and
  `create_personal_task` sends "remind me to" to `set_reminder`. A reminder
  row prints `reminder_id:`, never `full_id:`, because a `full_id:` line opens
  a task in the chat cards. Only the promote prints a `full_id:`, the one of
  the task that it made.
- **"Remind me to…" in the command bar.** The `capture` job stops taking it.
  A new `set-reminder` job opens the composer of §6.1 filled. This waits for
  the composer (CE-3 and RM-5). RM-3 did not build the job or the `capture`
  change.
- **Not in RM-3.** Act at once and Undo (CF-6), the command-bar job and the
  `capture` change (CE-3, RM-5), and the WhatsApp acts (RM-9).

### 5.9 Web push on iOS and Android

The transport is S7's. WS-66 builds it as PN-1, because the reminders need
it first. The rules here add to S7 and change only its item 4 (the events)
and its item 5 (for reminder pushes, D114.4).

- **iOS.** Push works only in a web app on the Home Screen, on iOS 16.4 or
  later. In a Safari tab, Settings shows no toggle. It shows the three steps
  to add Metorite to the Home Screen. The permission prompt shows only after
  the member taps the toggle (S7 item 3). iOS shows no action buttons on a
  web notification. So the tap that opens the chat is the one path.
- **Android.** Push works in Chrome and in the installed app. A notification
  may carry two actions, Done and Later, as a shortcut. The tap still opens
  the chat.
- **Desktop.** Chrome, Edge, Firefox and Safari, from the same toggle.
- **The service worker.** One file, `public/sw.js`, registered at scope `/`.
  On `push` it shows the notification with a `tag` of the reminder id, so a
  check-back replaces the first notification and does not stack. On
  `notificationclick` it focuses an open Metorite window and navigates it,
  or opens a new one at the payload's `url`.
- **Every notification.** Push is the channel for every card that Reach
  delivers (RE-9), and for S7's two chat events.
- **Where the member turns it on.** A Notifications section on My Profile
  (`/people/me`), beside the Reach section. It adds no pane and no nav
  entry. RE-7 later adds the choice of the main channel to that section.
  On a phone, the web chat also asks once, with snooze and "Don't ask
  again" (OB-8, §10.4).
- **One browser, several accounts.** The account switcher lets one browser
  hold several accounts, and all of them share one push subscription. PN-1
  settles the device rows: each account has its own row for the endpoint. A
  sign-out or a toggle-off deletes that account's row only. The browser
  unsubscribes only when no other account stays. A failed read of the
  account list keeps the subscription.
- **What RE-9 still settles.** A reminder title of account A can show while
  account B is in use. The default: the payload names the org, and "Hide
  details" covers a shared device. Settled 2026-10-30: RE-9 decision 4 of
  §5.6 holds the rule, and RE-9a builds it.

### 5.10 What the other docs gain

| Doc | Gains |
|---|---|
| `chat_run_continuity.md` §S7 | A note: built under WS-66 PN-1. Item 4 grows to every Reach card. Item 5 has the reminder exception of D114.4 |
| `proactive_reach.md` | `reminder_due`, `reminder_check` and `reminder_unconfirmed` in §4. The D114.3 exception in the budget. RE-9 points at PN-1 |
| `my_day_feed.md` | The three card kinds |

## 6. UI and UX

Everything here is inside the one look (`DESIGN_SYSTEM.md`). Colour comes
from the `--cat-1…12` ramp, `src/lib/categorical.ts` and
`src/lib/statusAccent.ts`. No raw palette class.

### 6.1 One composer

- **Drag on an empty slot** draws a range and opens the composer. A drag
  shorter than 15 minutes snaps to 15.
- **A tap on an empty slot** still opens the Gap Filler. It gains one row on
  top, **New at 14:30**, which opens the composer.
- **New** in the app's title bar opens it at the next free half hour.
- The composer is a `Modal.tsx` dialog. A switch at the top reads
  **Task · Event · Reminder**.

| Kind | Fields | Defaults |
|---|---|---|
| Task | Title, project, block, flexible, due date | My personal project, private. The block is the drawn range. Flexible on |
| Event | Title, all day, start and end, repeat, people, location or call link, link to a task, deal or contact, lead time, busy, notes | The drawn range. Busy on. My event lead time |
| Reminder | Title, time, kind, link | The time is the start of the range. Kind `action`. No link makes a standalone reminder (§5.1) |

**Smart defaults.**

- A typed time sets the range.
- A colleague's name, or "with", "meet" or "call", switches to Event and
  adds the person. "Remind" switches to Reminder.
- After the member touches the switch, the text never moves it.
- The title stays when the kind changes.
- The first kind is the kind the member used last. For a new member it is
  Task (Q5).

### 6.2 How each kind looks on the grid

| Kind | Look | What the member can do |
|---|---|---|
| Task block | Filled, with the `--cat-n` of its context, as today | Drag, resize, check off |
| Event | Outlined. A border in the `--cat-n` of its link, or the accent. A surface fill. A people glyph when it has attendees | Drag and resize when I own it. Opens the event sheet |
| External | Hatched. A stripe token on a muted surface, and the provider glyph. No handles | Read only. Opens the sheet with **Open in Google** or **Open in Outlook** |
| Reminder | A pin. A 2 px line across the day column at its minute, and a bell glyph in the gutter. No height. A `due` reminder has the accent. An `unconfirmed` one is muted | Drag moves the time. A click opens the chat with the reminder (§5.7) |

A tentative event has a dashed border. The grid mutes a declined event and
strikes its title. The stripe token is new, so it goes into `src/app/globals.css`
and its mirror `lib/theme/themes.ts`, and `themes.test.ts` holds the two.
Look at each kind in light mode, at compact density, under a changed
accent, and beside the Tasks app (CLAUDE.md §4).

### 6.3 The event sheet

- **Fields:** the title, the time and zone, the repeat rule, the people with
  their RSVP, and my RSVP buttons.
- **More fields:** the location, and the call link with **Join**. The links
  to a task, a deal, a contact and the notes. My lead time, busy, and notes.
- **The source line,** for example "From Google Calendar, read only".
- **Actions:** **Prepare notes**, **Send notetaker**, **Make a task from
  this** (it sets `task_id` on the entry), and **Delete** for the owner.
- A change to the guests asks a confirm when the provider will send mail.

### 6.4 The all-day row and Month

- **All-day row.** A strip above the time grid. It holds all-day events,
  holidays from a provider, and a span of days as one bar. More than 2 in a
  day fold to "+3". Task due dates stay as the deadline markers of today.
- **Month.** A day lists its chips: all-day first, then by start. An event
  chip has an outline, a task chip a fill, an external chip a hatch. A
  reminder chip is a bell, a time and a title. "+4 more" opens the Day view.

### 6.5 The other lenses

- **An event never shows in Tasks or Projects.** It is not a task.
- **A task that an open reminder points at** shows a bell glyph and the
  time. It shows in Tasks, and on the Projects card for the member who set
  it. Only that member sees it, because the reminder is private.
- **A task that an event links to** shows a calendar glyph and the event
  time.
- **A standalone reminder** is not a task, so Tasks and Projects do not list
  it. The Calendar lists it, in the grid and in its Reminders panel (RM-5).

### 6.6 The command bar

The bar (D88, D102) picks a job and fills its fields. A job never writes when
it opens (`navigation_shell.md` §5.1).

| Job | Opens | Fields |
|---|---|---|
| `new-event` (new) | `/calendar?do=new-event` | `title`, `start`, `end`, `people` |
| `set-reminder` (new) | `/calendar?do=new-reminder` | `title`, `at`, `task` |
| `capture` (exists) | `/tasks?do=capture` | Its description stops at tasks. "Reminder" moves to `set-reminder` |

"Meet Sharath 8:30-10:30 tomorrow" picks `new-event` with the title "Meet
Sharath", the times and the person. The composer opens filled. The member
presses **Save**. Both job lists change in one PR:
`routes/shell/intent.py` and `src/lib/shell/registry.ts`.

### 6.7 The web chat and WhatsApp

One assistant (D99), and one tool for each act:

| Tool | Does | Confirm |
|---|---|---|
| `create_event` | Makes an event, with people and links | A web card. On WhatsApp, the WAC-4 buttons |
| `update_event` | Moves, renames or changes the people of an event I own | Same |
| `set_reminder` | Makes a `reminders` row (§5.8). A lead time on an event is `update_event` | §5.8: acts at once, with Undo |

`create_personal_task` (with `block_start` and `block_end`) and
`set_my_overlay` stay the tools for a task block. The new tools go into
`skill_projects` beside them, so `test_projects_chat_coverage.py` maps each
route. Each joins `ALLOWED_ACTS` for WhatsApp. A delete stays in the web
app (D-WAC-4). A confirm for an event with guests shows the guest list.

## 7. Shell manifest (R9)

| Field | Value |
|---|---|
| `team` | `personal` |
| `blurb` | "Your time: tasks, events and reminders" |
| `jobs` | `plan-day` (exists), `new-event`, `set-reminder` |
| `search` | None in v1. Later: event titles, as a `calendar` provider |
| `needs` | The calendar kinds of §5.2, through the one feed |
| `cards` | None new. Home keeps what it shows |
| `agent` | None. The one assistant serves it (D99) |
| Its own palette, bell, rail or ⌘K handler | None. `src/lib/shell/seams.test.ts` holds this |
| `feature` | `feature:tasks`, as today (D54.1) |

## 8. External calendars

The owner said on 2026-10-13 that Metorite will read Google and Outlook
calendars (`my_day_feed.md` Q3). D54.6 keeps the OAuth credentials an owner
gate.

### 8.1 Read first (CE-10)

1. **Connect in the Calendar app.** `CalendarSettings.tsx` gains **Connect
   Google Calendar** and **Connect Outlook**. The Integrations console lists
   the row and links back (D96.1).
2. **One click for the customer.** Metorite owns the OAuth app. The member
   presses Connect, consents, and is done. No customer configures an app or
   a key.
3. **The row.** `integration_connections` with `owning_app = 'calendar'`,
   `scope_kind = 'member'` and `member_email`. The token is in
   `credentials_encrypted`, never in the environment (D96.3). The email
   app's row stays its own, so a disconnect in one app breaks nothing in
   the other.
4. **The loop.** A 60-second tick with a due check and a lock, after
   `gateway/crm_sync/scheduler.py`. Google uses a sync token. Outlook uses a
   Graph delta query. The window is 30 days back and 180 days ahead.
5. **The rows.** Each instance is an `external` entry, read only. A
   provider's "free" entry has `busy = false`. A cancel sets `status`.
6. **Conflicts.** `block_overlap` and MD-11's `meeting_overlap` read the busy
   external entries. The day planner treats them as fixed.

### 8.2 Write with read (CE-11)

D138 (2026-10-29): CE-11 ships beside CE-10, and both providers ship
together. The owner gates of §8.3 still bind the live flips.

- An event made in Metorite, with a connected account, has the switch
  **Also in Google Calendar**. It is on by default when connected.
- Metorite writes the event to the provider. The provider sends the invites.
  The provider's id goes into `external_id`, and the sync of CE-10 keeps the
  row current.
- Task blocks as busy time in the provider is a later choice (Q9).

### 8.3 Owner gates

| Gate | Who | Why |
|---|---|---|
| Add `calendar.readonly` to the Google consent screen, and pass Google's review of a sensitive scope | Owner | Our OAuth app, and our name on the review |
| Add `Calendars.Read` to the Azure app | Owner | Same |
| Put the client secrets on the box with `scripts/secrets.sh` | Owner | Live credentials |
| Turn `CALENDAR_SYNC_READ` on for a real member | Owner | D54.6 |
| Add the write scopes, and turn `CALENDAR_SYNC_WRITE` on | Owner | The provider sends mail to real people (CLAUDE.md §3a rule 3) |

## 9. Flags

Every flag is OFF by default. Off gives today's behaviour.

| Flag | Where | Ticket | Off means |
|---|---|---|---|
| `CALENDAR_LEAD_TIME` | Gateway | CE-1, CE-6 | The fixed 30 minutes |
| `CALENDAR_REMINDERS` | Gateway. As for web push, the client has no flag of its own: RM-5 draws reminders only when the reminder routes answer | RM-1 to RM-5 | The reminder routes answer 404. No loop, no cards, no tools, no pins. `/version` carries `calendar_reminders` |
| `WEB_PUSH_ENABLED` | Gateway. The client has no flag of its own: the Notifications section shows only when `/push/public-key` answers | PN-1, RE-9 | Every `/push` route answers 404, so the section draws nothing, and nothing registers or sends. ✅ ON for every org is approved (D114.8) |
| `PUSH_PROMPT_ORGS` | Gateway, a per-org list. The client reads `prompt` in the `/push/public-key` answer | OB-8 (§10.4) | The chat shows no sheet that asks a phone member to turn on notifications |
| `NEXT_PUBLIC_CALENDAR_COMPOSER` | Control plane | CE-3 | No drag to draw. The Gap Filler as today |
| `CALENDAR_EVENTS` and `NEXT_PUBLIC_CALENDAR_EVENTS` | Both | CE-4 to CE-9, CE-12 | No events. `/version` carries `calendar_events` |
| `CALENDAR_SYNC_READ` | Gateway | CE-10 | No sync. 🔴 Owner flip |
| `CALENDAR_SYNC_WRITE` | Gateway | CE-11 | No write-out. 🔴 Owner flip |

## 10. Tickets

Take each migration number at build time and check it again at merge (R1).
Each ticket ships dark behind its flag (§9). "No change" means the ticket
needs no decision change.

| Ticket | Gate and order | Scope | Accept | Fence (R7) |
|---|---|---|---|---|
| **CE-0** The owner's decisions — ✅ **DECIDED 2026-10-29 (D138)** | 🔴 OWNER | Answer Q1 to Q10 of §11. Mint the board row. Record the amendments the owner accepts | `work_plan.md` §3 holds the decision | — |
| **CE-1** Lead time on a task block | AGENT-SAFE. No change | `pm_task_personal.lead_minutes`. `user_settings.block_lead_minutes` and `event_lead_minutes`. `feed.py` reads the lead, not `SOON`. The block menu sets it | A lead of 10 gives `block_soon` 10 minutes before the start. No lead gives 30. The flag off gives 30 for every block | `tests/unit/test_calendar_lead_time.py` (R8, new) and `test_shell_feed.py` |
| ~~**CE-2**~~ | **REPLACED by WS-66 (§10.1), D114** | — | — | — |
| **CE-3** The composer and drag to draw | AGENT-SAFE. No change | The composer of §6.1 with Task and Reminder. Event shows only with `NEXT_PUBLIC_CALENDAR_EVENTS`. Drag to draw in `TimeGrid.tsx`. The **New at…** row in the Gap Filler | A drag from 14:00 to 15:00 makes a private task with that block in one save. Esc saves nothing. The keyboard alone can do it | `components/Composer.test.tsx` (new), the conformance suite, `src/lib/shell/seams.test.ts`, and a visual review |
| **CE-4** The event store | AGENT-SAFE after CE-0 Q1 | The two tables of §3.3. `POST`, `PATCH` and `DELETE` on `/calendar/entries`. The read of §3.4 | I see my entries. A colleague who is not an attendee sees none. Org B sees no row of org A. The flag off gives today's read | `tests/unit/test_calendar_entries_r8.py` (new) and `test_db_engine_seam.py` |
| **CE-5** Events in the UI | AGENT-SAFE after CE-3, CE-4 | The Event kind, the looks of §6.2, the sheet of §6.3, the all-day row and Month of §6.4 | No event shows in Tasks or Projects. An all-day event keeps its date in every zone | Vitest for the composer, `MonthGrid` and the all-day row, and `themes.test.ts` |
| **CE-6** Event lead time and conflicts | AGENT-SAFE after CE-1, CE-4 | `event_soon` with class `interrupt`. `block_overlap` reads busy entries | A lead of 10 gives `event_soon` 10 minutes before. A free entry gives no overlap | `test_calendar_lead_time.py` and `test_shell_feed_r8.py` |
| ~~**CE-7a**~~ | **REPLACED by RM-3 and RM-9 (§10.1), D114** | — | — | — |
| **CE-7b** The event tools and job | AGENT-SAFE after CE-4 | `create_event`, `update_event`, `set_reminder` on an event. The `new-event` job | "Meet Sharath 8:30-10:30 tomorrow" opens a filled composer. The chat makes the event after one confirm | The three tests of CE-7a |
| **CE-8** Repeat rules | AGENT-SAFE after CE-4 | `recurrence`, `series_id`, `original_start`, expanded in the window. "This event" or "all events" on a change | A weekly 09:00 event in `Asia/Kolkata` stays at 09:00 in that zone. A cancelled instance does not show | `tests/unit/test_calendar_recurrence.py` (new), with a zone that changes its clock |
| **CE-9** Colleagues on an event | AGENT-SAFE after CE-0 Q2, CE-4 | Attendee rows with `member_email`. The rule of §4.1. `event_invite` with RSVP in place (D101) | An attendee sees the event. A colleague who is not one does not. Decline writes `rsvp` and mutes the entry | `test_calendar_entries_r8.py`, the visibility cases |
| **CE-10** Read Google and Outlook | 🔴 OWNER-GATE for §8.3. The code is AGENT-SAFE after CE-0 Q1, Q6, CE-4 | §8.1, and the provider of MD-11 | With recorded replies, a sync writes each instance once, and a second sync adds nothing. A disconnect deletes the rows of that connection | `tests/unit/test_calendar_sync_read.py` (R8, new) and the loop in `test_db_engine_seam.py` |
| **CE-11** Write events to the provider | 🔴 OWNER-GATE (§8.3). After CE-10 | §8.2 | The flag off sends no request. One event makes one provider event, and the next sync makes no copy | `tests/unit/test_calendar_sync_write.py` (new) |
| **CE-12** Notes meetings | AGENT-SAFE after CE-4 | `meeting_id`. **Prepare notes** and **Send notetaker**. The "Coming up" band reads entries | Prepare notes twice opens one `meeting` row. A moved event moves the band | `tests/unit/test_calendar_meeting_link.py` (new) |

**The order.** CE-1 and CE-3 now. CE-0 is decided (D138). Then CE-4, CE-5,
CE-6, CE-7b, CE-8, CE-9 and CE-12. Then CE-10 and CE-11 together, for Google
and Outlook at once (D138).

### 10.1 WS-66 — reminders and web push (D114)

Every ticket is AGENT-SAFE and ships dark (§9). The owner approved both flags
ON for every org in production on 2026-10-25 (D114.8). An agent turns each one
on after its slice passes on dev.metorite.com. Each R8 suite needs `pytestmark =
_DB_GATE`, because `deploy.yml` has no ladder database.

| Ticket | Order | Scope | Done when | Fence (R7) |
|---|---|---|---|---|
| **RM-0** The spec, D114 and the board row | First | This section, §5, D114, WS-66, INDEX | The spec-auditor gives GO for PN-1 and RM-1 | — |
| **PN-1** The push platform (S7's transport) — ✅ **BUILT 2026-10-25, dark** (migration 306) | Now, beside RM-1 | §10.2 | The agent proves: no prompt shows without a tap, org B reads no subscription of org A, a 410 deletes the row, and a sign-out deletes the row of that device. 🔴 The owner checks, through a HANDOFF entry on dev.metorite.com: a test push reaches an installed iPhone app and Android Chrome | `tests/unit/test_push_subscriptions.py` (R8, two orgs), `src/lib/push.test.ts`, `src/proxy.test.ts` |
| **RM-1** The store and the API — ✅ **BUILT 2026-10-25, dark** (migration 307) | Now, beside PN-1 | §10.3 | Member B never reads, changes or counts a reminder of member A. Org B reads no row of org A. A promote makes one private task and ends the reminder | `tests/unit/test_reminders_r8.py` (R8, two orgs, two members), `test_projects_chat_coverage.py` |
| **RM-2** The loop and the smart slot — ✅ **BUILT 2026-10-26, dark** | After RM-1 | §5.4 and §5.5. The three card kinds in `feed.py` and in `REACH_CLASS`. The D114.3 exception in the Reach budget | A reminder for 15:00 fires at 15:00, and its check-back comes at 16:00. No answer moves it to the brief, then to one new slot. After 2 cycles it is `unconfirmed`. No step 2 to 5 lands in quiet hours. Two workers never fire one row twice | `tests/unit/test_reminder_loop.py` (R8, frozen clock), `tests/unit/test_reminder_slot.py`, `test_reach_plan.py` |
| **RM-3** The chat tools — ✅ **BUILT 2026-10-26, dark** | After RM-1 | §5.8 in `skill_projects`: seven tools on the one confirm card, until CF-6. The `set-reminder` command-bar job and the `capture` change wait for CE-3 and RM-5 (§5.8) | "Remind me at 5 to get a haircut" saves one row at 17:00 in my zone, and the reply quotes that time and zone. A time with no offset is refused | `test_projects_chat_coverage.py`, `tests/unit/test_reminder_tools.py`, `test_projects_agent_writes.py`, `test_wac_writes.py` |
| **RM-2b** The reminder bell (Metorite task 443) — **BUILT 2026-10-30, dark** — AGENT-SAFE, tier MEDIUM, ships dark | After RM-2 | §5.6 "Settled 2026-10-30 (RM-2b)". Move `reminders` to one ungated needs provider in `routes/shell/needs.py` with its feed `signals` thunk, and remove the feed-only registration. Add the three kinds to `KIND_ORDER`, `NEWEST_FIRST` and `feed_rank.BASE`. Client: `NeedsApp`, `NeedsKind`, `KIND_ORDER` and `NEEDS_APP_HREF.calendar` in `src/lib/shell/needs.ts`. The row is a link to `reminder_href(id)`. Shift the five needs bases of `feed_rank.BASE` up by 3 (63, 59, 55, 51, 47), and give the reminder kinds 46, 45 and 44. Move `_reminder_signal`, `reminder_href` and `REMINDER_KINDS` into `needs.py`, with no re-export from `feed.py`, and update every importer (§5.6 RM-2b decision 6). The UI gate of `DESIGN_SYSTEM.md` §11 applies. **Non-goals:** a second bell, a second source id, any change to `NotificationBell.tsx` in Projects, Done, Later or Open acts on the row (D101), a Calendar count in the sidebar (the server keys `sources` and `totals` by `reminders`, and the client reads them by `NeedsApp`), and the push (RE-9) | With the flag on and a due reminder, `GET /shell/needs` returns one item of kind `reminder_due`, app `calendar`, with the href `/chat?do=new-chat&reminder=<id>`. A check-back row gives `reminder_check`. An unconfirmed row gives `reminder_unconfirmed`. A scheduled row gives none. Member B and org B never see the reminder of member A (R8). With the flag off, no reminder item shows and `sources.reminders` reads `absent`. The feed still gives one card for each reminder, with no duplicate. The shell bell draws the row with the one look, and no second bell exists. My Day reminder cards rank above the MD-3 feed-only cards. A feed `drop` from `decide` on a reminder card moves it to Later, and does not delete or resolve the reminder | New cases in `tests/unit/test_shell_needs.py` and `tests/unit/test_shell_needs_r8.py` (`pytestmark = _DB_GATE`). The updated pins in `test_shell_feed.py`, `test_feed_rank.py` (a new line: the three reminder bases sit between `agent_asks` and the lowest shifted needs base) and `test_feed_fold_fingerprint.py`. A new case in `tests/unit/test_feed_decide.py`: a `drop` of a reminder card gives Later. A client case in `src/lib/shell/needs.test.ts`. **Verify:** `uv run pytest tests/unit/test_shell_needs.py tests/unit/test_shell_needs_r8.py tests/unit/test_shell_feed.py tests/unit/test_feed_rank.py tests/unit/test_feed_fold_fingerprint.py tests/unit/test_reminder_loop.py tests/unit/test_reminders_r8.py tests/unit/test_reach_loop.py tests/unit/test_feed_decide.py tests/unit/test_feed_task_kinds.py tests/unit/test_crm_digest.py`, then in `workbench/control_plane`: `npx tsc --noEmit && npx vitest run src/lib/shell/needs.test.ts src/lib/shell/shellBell.test.ts src/lib/shell/seams.test.ts` |
| **RE-9a** The push message and the hide setting (Metorite task 431) — ✅ **BUILT 2026-10-30, dark** (AGENT-SAFE, tier STANDARD) | After PN-1, RM-2 | §5.6 "Settled 2026-10-30 (RE-9)", decision 4. A pure `PushMessage` builder in `gateway/reach/` for the three reminder kinds. The key `hide_push_details` in `PREF_KEYS` (`routes/shell/prefs.py:62`), and its toggle in `NotificationsSection.tsx`. The prefs route needs four code points: `PREF_KEYS`, `PrefPut.key` (`:135`), a boolean arm in `_CLEAN` (`:127`) and `stored_prefs` (`:144`). Without the last, the toggle never reads back. The org name goes in the payload `body`, because `sw.js` reads only `title`, `body`, `url` and `tag`. The UI gate applies. **Non-goals:** no send, no trigger, no migration, no class change | The builder gives the title and time with the url `/chat?reminder=<id>` and the tag of the reminder id. With `hide_push_details` on, the title is "Metorite: a reminder", and no title or time text is in the payload. The `body` holds the org name and no reminder text. The prefs route writes the key, and org B and member B read nothing of it (R8) | New cases in `tests/unit/test_reach_push_message.py` (new), `tests/unit/test_my_day_prefs.py` and `tests/unit/test_my_day_prefs_r8.py`, and a client case for the toggle. **Verify:** `uv run pytest tests/unit/test_reach_push_message.py tests/unit/test_my_day_prefs.py tests/unit/test_my_day_prefs_r8.py tests/unit/test_reach_plan.py`, then `npx tsc --noEmit && npx vitest run src/app/people` in `workbench/control_plane` |
| **RE-9b** A due reminder sends a real web push — ✅ **BUILT 2026-10-31. Not dark: it sends wherever `WEB_PUSH_ENABLED` and `CALENDAR_REMINDERS` are on (production: both on)** (AGENT-SAFE, tier HIGH: a migration, and a send to real devices) | After RE-9a and RM-2. It does not wait for RM-2b | §5.6 "Settled 2026-10-30 (RE-9)", decisions 1 to 3, 5 and 6, and the rulings a to k of RE-9b. The migration (`infra/postgres/318_reach_outbox_web_push.sql`, the next free number on 2026-10-31), the classes in `apps/services/gateway/gateway/reach/classes.py`, the plan step and the send step in `apps/services/gateway/gateway/reach/push_channel.py`, the calls from `_fire`, `_check_back` and `_give_up` in `reminders/loop.py`, the per-channel budget, and the R8 acceptance. The plan step takes the session `db` of the loop and writes the row through a new helper in `reach/outbox.py`, with no new `tenant_session` or `get_db` site (R5). The dedupe key is `reminder:<reminder id>:<reminder_events id>`. The send phase of `run_tick` calls the send step after the steps of every org, with no `REACH_*` flag (ruling k). The margin of the 90 s is one tick (`TICK_SECONDS` = 60) plus the send phase (`SEND_PHASE_S` = 30, ruling k). The outbox `body` holds the fixed text "reminder". The caps count `sent` rows on `web_push` for one member and one local date: 10 for `reminder_due`, and `reach_daily_cap` (default 3) for `reminder_check`. **Non-goals:** WhatsApp (D115), the S7 chat events, a new channel setting (RE-7), and any new flag | A frozen-clock R8 test fires a `reminder_due` row and sends one push through a faked `outbound_guard.request_https` within 90 s of `fire_at`. The decrypted payload holds the url `/chat?reminder=<id>`. A hidden-details member gets no title. A replay of the same tick sends once, and two workers send once. The push of the second cycle is sent, because each ledger row gives its own key. The push is sent with `REACH_ENABLED`, `REACH_ORGS` and `REACH_MEMBERS` all off. A done, dropped or moved reminder sends nothing. `reminder_check` and `reminder_unconfirmed` send nothing in quiet hours. The eleventh `reminder_due` of a day sends nothing. A `reminder_check` over the interrupt cap sends nothing, and its card stays in My Day. Push rows do not count in the WhatsApp cap, and `_SENT_SINCE_SQL` counts by channel. The Reach loop and `whatsapp.deliver` never touch a `web_push` row. `plan.on_whatsapp` still removes the three reminder kinds after their class changes. Org B reads nothing of org A | `tests/unit/test_reach_push.py` (new, `pytestmark = _DB_GATE`), `tests/unit/test_reach_plan.py` and `tests/unit/test_reach_loop.py` (the WhatsApp fence). **Verify:** `bash scripts/dev_db.sh`, then `uv run pytest tests/unit/test_reach_push.py tests/unit/test_reach_plan.py tests/unit/test_reach_loop.py tests/unit/test_reach_r8.py tests/unit/test_reminder_loop.py tests/unit/test_reminders_r8.py tests/unit/test_push_subscriptions.py tests/unit/test_reach_push_message.py tests/unit/test_tenancy_boundary.py`. The production flip needs no new owner act (D114.8): an agent flips both flags once each slice passes on dev.metorite.com. The owner checks a real device through the PN-1 HANDOFF item |
| **RM-4** A tap opens the chat — ✅ **BUILT 2026-10-29, dark** (AGENT-SAFE) | After RM-3 | §5.7: `/chat?reminder=<id>`, the code-built first message, the chips, the `reminder` context block. As built: §5.7 | A tap on a push opens the chat with the reminder message and no model call. Done from a chip ends the reminder and offers Undo | `src/app/chat/reminderOpen.test.ts` (new, `.ts` because vitest runs `*.test.ts` only), `tests/unit/test_reminder_context.py` and `tests/unit/test_reminders_r8.py` |
| **RM-5** The Calendar UI and the New reminder form — ✅ **BUILT 2026-10-30. It draws wherever `CALENDAR_REMINDERS` is on (production: on). The client has no flag of its own (§9)** (AGENT-SAFE) | After RM-1 | The pin and the Month chip of §6.2. The Reminders panel: Upcoming, Waiting on you, Unconfirmed. **The New reminder form** (owner decision, 2026-10-30, no D-number): a member makes a reminder in the Calendar with no chat. A button in the title bar tools and a button in the panel header open one `Modal` (`src/components/ui/Modal.tsx`) with a title (200 characters at most), a date and a time (the next half hour by default), and a kind (action or notice). The form makes a standalone reminder and sends no subject. The time is read in the browser zone, which is the zone of the grid, and goes out as ISO with the offset beside `tz`. The form does not read `user_settings.timezone`, because it holds "UTC" until the settings load. The form refuses a time at or before now, under its field. A drag of a pin, or Alt+ArrowUp and Alt+ArrowDown (15 minutes of real time), calls `/reschedule` once. The Calendar refuses a drag or a key move to a time at or before now, and a toast says why. A move of a scheduled reminder offers Undo. A "Move to…" item opens the same date and time controls. A move starts the reminder again, so an unconfirmed pin goes back to cycle 0. A tap on a pin or a panel row opens `/chat?do=new-chat&reminder=<id>`. The panel shows in every mode, and a phone opens it as a sheet from the title bar. With the routes at 404 or 403 the Calendar draws no pin, chip, panel or button and shows no error. | A reminder draws as a line with no height and takes no capacity. Drag moves `fire_at`. With the flag off the Calendar draws nothing. The form sends `fire_at` with an offset and `tz`, and the pin shows at that minute. A reminder changes no `leverageStats` | `reminders.test.ts` beside `lib/reminders.ts` (zone to ISO across a clock change, the split by state, the pin, the absent state on a 404, the one-call `/reschedule`), the conformance suite, `uiGates.test.ts`, and a visual review. Non-goals: events, the full Task, Event and Reminder composer (CE-3), the link picker, the `set-reminder` and `new-event` jobs, push (RE-9b), and the bell glyph (RM-5b) |
| **RM-5b** The bell glyph on Tasks and Projects | After RM-5 | The bell glyph of §6.5 on a Tasks or Projects card, for the member who set the reminder. Split from RM-5 on 2026-10-30 | A linked card shows the glyph to its own member only | A vitest for the card chip, and the conformance suite |
| **RM-6** Closed by evidence | Later | The linked task goes to done, or the member sends an email on the linked thread. The reminder closes and says so | A done task closes its reminder, with an `evidence` ledger row | `test_reminder_evidence.py` |
| **RM-7** Conditional follow-ups | Later | "Remind me if Ravi has not replied by Friday". A reply cancels it | An inbound reply before Friday cancels the reminder | `test_reminder_conditions.py` |
| **RM-8** Repeats | Later | An RRULE on a reminder, in its own zone | A daily 09:00 reminder stays at 09:00 across a clock change | `test_reminder_repeat.py` |
| **RM-9** WhatsApp | Later | Reminder acts in `ALLOWED_ACTS`, and the reminder as a Reach WhatsApp card | — | `test_wac_writes.py` |
| **RM-10** Learning | Later | Ask once to stop check-backs for one kind. Learn the times that the member acts on | — | — |

**Verification commands.** Start the database first:
`bash scripts/dev_db.sh`, then `eval "$(bash scripts/dev_db.sh --export)"`.

```
uv run pytest tests/unit/test_push_subscriptions.py tests/unit/test_reminders_r8.py tests/unit/test_reminder_loop.py tests/unit/test_reminder_slot.py tests/unit/test_reminder_tools.py tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/push.test.ts src/app/manifest.test.ts src/proxy.test.ts src/lib/publicFlags.test.ts
```

Add `test_tenancy_boundary.py`, `test_migration_prefixes.py`,
`test_secrets_drop.py` and `test_projects_chat_coverage.py` to the pytest
line for the ticket that touches each.

**Migration numbers (R1).** PN-1 and RM-1 each add one migration. Take the
next free number at build time and scan the origin branches again before
merge. The second ticket to merge renumbers. A test finds its migration by
content, as `test_email_send_later.py` does. The fence is
`test_migration_prefixes.py`.

### 10.2 PN-1 scope (narrowed by the spec-auditor, 2026-10-25)

1. **The migration.** `push_subscriptions` with the W3C fields: `endpoint`,
   `p256dh`, `auth`, `expiration_time`, plus `member_email`, `user_agent`,
   `created_at` and `last_success_at`. Copy the RLS block of
   `290_email_scheduled_sends.sql`. The unique key is `(organization_id,
   member_email, endpoint)`. Never key on `endpoint` alone, because an
   insert in org B then fails on a row that org B cannot see. Regenerate
   `infra/postgres/generated/` with `scripts/gen_tenant_migration.py`, and add
   the table to `test_tenant_coverage.py`.
2. **The settings.** `web_push_enabled`, `vapid_public_key`,
   `vapid_private_key` and `vapid_subject` in
   `packages/acb_common/acb_common/settings.py`. One module reads them, as
   `gateway/reach/flags.py` does. `/version` carries `web_push`.
3. **The router.** `/push`, gated on `feature:chat`, as `/reach` is. The
   routes are `GET /push/public-key`, `POST` and `DELETE /push/subscriptions`
   and `POST /push/test`. The member and the org come from the session only.
   With the flag off, each route answers 404.
4. **The sender.** One module on `pywebpush`, which is a new dependency. Its
   send blocks, so it runs in `asyncio.to_thread`. On a 404 or a 410, it
   deletes the row in the same tenant session. The test payload holds a
   fixed title and no content (S7 item 5).
   - **As built (review, 2026-10-25).** The sender encrypts and signs with
     pywebpush, and sends only through `outbound_guard.request_https`, after
     the push-service allowlist of `push/endpoints.py`. So push adds no
     second outbound seam. `/push/test` sends to every device at once, under
     a budget of 20 s.
   - **The cap.** A member holds at most 10 devices. A new device first
     evicts the stale ones (expired, or 5 failures), then the oldest by its
     last success. It never refuses a device.
   - **The purge.** The admin member purge deletes the member's devices.
5. **The service worker.** `public/sw.js` at scope `/` (§5.9). Add `/sw.js`
   to `PUBLIC_PAGES` in `src/proxy.ts`, with a `proxy.test.ts` case.
   Otherwise a signed-out update fetch of the script gets a redirect.
6. **The BFF.** `src/app/api/push/[...path]/route.ts`, a copy of
   `api/tasks/[...path]/route.ts` with `refuseUnsafePath`.
7. **The UI.** `src/lib/push.ts` and a `NotificationsSection` on
   `app/people/me/page.tsx`, beside `ReachSection`. The prompt shows only
   after a tap on the toggle. In a Safari tab on iOS, the section shows the
   three Home Screen steps.
8. **Sign-out.** Delete the row before the session ends, on all six paths:
   `signOutThis` and `signOutAll` in `lib/accountSwitch.ts`,
   `components/Sidebar.tsx`, `app/signup/SignUpForm.tsx`, "Remove from this
   browser", and a stash that overwrites a full slot. The last two delete as
   the account that leaves, through its slot session. The browser
   unsubscribes only when no other account stays (§5.9). A read
   `POST /push/subscriptions/status` tells whether this member has a row for
   this endpoint, so the toggle shows the member's own state.
9. **The keys.** The agent makes one P-256 pair. Add `VAPID_*` to
   `app-env.allowed_keys` in `deploy/secrets/manifest.json`. Push to staging
   first (`metorite-dev`), and report the act with its evidence (CLAUDE.md
   §3a rule 2).
10. **Out of scope.** The device check (the owner's HANDOFF entry), the
    production flip, S7's two chat events, and RE-9.

### 10.3 RM-1 scope (narrowed by the spec-auditor, 2026-10-25)

1. **The migration.** `reminders` and `reminder_events` of §5.2, with the
   CHECK lists of §5.2 and §5.3 and the three indexes. Copy the RLS block of
   migration 290, and regenerate `generated/`.
2. **The module.** `routes/projects/reminders.py`, mounted at
   `/projects/my/reminders` (§5.8). The routes are `GET` (the list, by state,
   which also serves Unconfirmed), `POST`, `PATCH /{id}`, `POST
   /{id}/resolve` (done, drop), `POST /{id}/promote`, `POST /{id}/reopen`,
   `POST /{id}/snooze` and `POST /{id}/reschedule`. As built, a snooze
   target must be after now and within 366 days.
3. **The member.** `member_email` comes from `actor(user).lower()`, as in
   `routes/projects/core.py`. Every query also filters on `member_email`,
   because RLS covers the org only.
4. **One transaction.** Each change writes its ledger row in the same
   `async with _tenant_session() as db:` block.
5. **Time.** Refuse a time with no offset. `/reschedule` takes a named time
   only. The `smart` slot is RM-2.
6. **Promote.** Call `ensure_personal_project`, `load_default_status` and
   `create_personal_task` from `personal.py` in that one session. The
   `capture` route is the model, so after the commit a promote also calls
   `link_from_text`.
7. **The manifest.** One row for each new route in
   `skill_projects/manifest.py`, as a planned tool, in this PR.
8. **The flag.** With `CALENDAR_REMINDERS` off, each route answers 404.
   `/version` carries `calendar_reminders`.

### 10.4 OB-8 — the chat asks a phone member to turn on notifications

Metorite task 600, workstream WS-OB. Owner requests, 2026-10-27 (the task
rules) and 2026-10-30 ("a chat should also prompt the user"). No D-number.
Tier STANDARD: product UI and one key of the member's own prefs. No
migration. It ships dark behind `PUSH_PROMPT_ORGS` (§9).

**BUILT, 2026-10-30, dark.** The sheet is `components/PushPromptSheet.tsx`.
The show rule is `lib/pushPrompt.ts`.

`lib/push.ts` holds `isPhone` and `prompt`.
`components/AgentChat.tsx` holds one mount. `routes/shell/prefs.py` holds the
`push_prompt` key. `push/flags.py` and `push/routes.py` hold `PUSH_PROMPT_ORGS`.

**The build is AGENT-SAFE. The flag flip is OWNER-GATE**, or an agent act under a dated
`enforcement-flip` grant. Staging comes first, and the act names the flag
and the box.

**Why.** The morning brief, reminders and proactive messages reach a phone
only through web push. Today a member turns push on only in My Profile, and
few members find it.

1. **Where.** The web chat of the member's own assistant
   (`components/AgentChat.tsx`, above the message box, by the `notice` block).
   That block already docks a note there. The guard is
   `isOrchestrator && !isRoom`. It is one inline sheet, docked above the
   composer. It is not a modal. It never
   covers the messages and never blocks the composer. It never shows in a
   room, in an app agent's chat, on the sign-in page, or on WhatsApp.
2. **A phone.** One pure helper, `isPhone(facts)` in `src/lib/push.ts`. It
   is true for an iOS or Android device, and for a coarse pointer under
   768 px. A desktop never shows the sheet.
3. **When it shows.** Every rule must hold:
   - The flag holds for the org. `GET /push/public-key` answers
     `prompt: true` (item 7).
   - `pushSupport()` gives `supported`. In a Safari tab on iOS it gives
     `ios-tab`, and the sheet does not show (item 6).
   - The permission is not `granted`, or this device has no row for this
     member (`registeredHere`).
   - The chat holds one message or more, and no run streams. So the sheet
     never shows in the first-run chat, and never shows beside the
     interview offer, which shows only in an empty chat.
   - The stored state allows it (item 5): no `never`, no `snoozed_until`
     in the future, and no `last_shown_at` in the last 24 hours.
4. **The three choices.**
   - **Turn on.** The tap handler calls `enablePush()` first, before any
     other await, because a browser refuses a permission request that no
     tap started. On success, the sheet says "Notifications are on" and
     closes. On `denied`, the sheet shows the phone-settings steps (item 6).
   - **Remind me later.** It writes `snoozed_until` = now + 7 days, and
     closes the sheet.
   - **Don't ask again.** It writes `never: true`, and closes the sheet.
     A toast offers Undo and names the way back: My Profile → Notifications.
   - The close button and Escape close the sheet. The 24-hour rule then
     holds it back until tomorrow.
5. **The state.** One key, `push_prompt`, in `PREF_KEYS` of
   `routes/shell/prefs.py`, stored in `user_settings.my_day_prefs`. Add it at
   the four code points that RE-9a names: `PREF_KEYS`, `PrefPut.key`, an arm
   in `_CLEAN` and `stored_prefs`. Its value is an object:
   `snoozed_until`, `last_shown_at` and `denied_seen_at` (ISO times with an
   offset), and `never` (a boolean). `_CLEAN` drops an unknown field,
   refuses a time with no offset, and refuses a `snoozed_until` more than
   366 days ahead. The state is per member and org, so every phone of the
   member obeys it. The client writes `last_shown_at` once each time the
   sheet shows. `_CLEAN` holds list and boolean arms only, so this key
   needs a new object validator and an object arm in `stored_prefs`. The
   client reuses `loadFeedPrefs` and `saveFeedPref` of `lib/shell/feed.ts`.
   A PUT replaces the whole value of one key, so the client reads, merges,
   then writes.
6. **iPhone and a refused permission.**
   - In a Safari tab (`ios-tab`), push works only from the Home Screen app.
     OB-8 shows nothing there. `InstallNudge` owns the Home Screen ask.
     OB-8 asks once the member opens Metorite from the Home Screen.
   - When the permission is `denied`, a browser allows no second request.
     The sheet shows how to turn notifications on in the phone's settings,
     once. It then writes `denied_seen_at` and does not show again while
     the permission stays `denied`.
7. **The flag.** `PUSH_PROMPT_ORGS`, a per-org list on the existing `*_ORGS`
   seam, with the reload rule of that seam. `*` opens every org. The field
   `push_prompt_orgs` goes in `settings.py` beside `web_push_enabled`, and
   the reader goes in `gateway/push/flags.py`. It reads the settings at call
   time, never at import (`test_flag_reload.py`). Add the prefix
   `PUSH_PROMPT_` to `env_guard.py`, as `BRIEF_` is, so that a tenant env
   cannot open the flag. The staging flag tool (`acb-staging-flag`) does not
   take this name, because its `HELD_RE` holds every `PUSH` key off on
   staging. An agent with a dated `env-write` grant sets it in the staging
   `.env` instead, and reports the act. `GET /push/public-key` adds `prompt`, true only when the flag
   holds for the session's org. With the
   flag off, the chat draws no sheet and writes nothing. The Notifications
   section of My Profile does not change.
8. **The copy.** Title "Get notified on this phone". Body: "Turn on
   notifications so reminders, your morning brief and replies that need you
   reach this phone, even when Metorite is closed." It never names the model
   or a tool.
9. **The UI gate.** `DESIGN_SYSTEM.md` §11 applies: the one look, the four
   captures, a 390 px check, Tab reaches each button, Escape closes, and the
   sheet is a labelled region.

**Non-goals.** A desktop chat that says no phone gets notifications. A
prompt on WhatsApp. Any change to `enablePush`, the service worker or the
sender. A new table or migration.

**Done when.** On a phone with the flag on and push off, the chat shows the
sheet once. Each choice does what it says. With the flag off, nothing
shows. Member B and org B read no `push_prompt` of member A (R8).

**Fence (R7).** `src/lib/pushPrompt.test.ts` (new) pins the show rule: the
flag, each support state, the permission, an empty chat, a stream, the
snooze, `never`, the 24 hours and `denied_seen_at`. A component test pins
the three buttons and Escape. New cases in `tests/unit/test_my_day_prefs.py`
and `tests/unit/test_my_day_prefs_r8.py` (`pytestmark = _DB_GATE`) pin
`_CLEAN` and the tenant read. A case in `tests/unit/test_push_subscriptions.py`
pins `prompt` and the flag.

**Verify.** `bash scripts/dev_db.sh`, then
`uv run pytest tests/unit/test_my_day_prefs.py tests/unit/test_my_day_prefs_r8.py tests/unit/test_push_subscriptions.py`,
then in `workbench/control_plane`:
`npx tsc --noEmit && npx vitest run src/lib/push.test.ts src/lib/pushPrompt.test.ts src/components src/app/people`.

## 11. Owner questions

✅ **The owner answered on 2026-10-29 (D138).** Q1, Q2, Q4, Q5, Q8 and Q10
take the default. Q6: Google and Outlook ship together. Q7: write-out ships
with read. Q9 keeps the default, because the owner did not answer it
directly.

| # | Question | Default when the owner says nothing |
|---|---|---|
| Q1 | Accept option C, one `calendar_entries` table? It amends D54.5 | Yes. A task block stays on the overlay |
| Q2 | May a colleague see an event when they are an attendee, and nobody else? It amends D54.4 narrowly | Yes. No team calendar, no free and busy view |
| Q3 | May a member's own reminder use class `urgent`, and sit outside the daily budget? It amends D103.3 and D-RE-3 | ✅ **ANSWERED 2026-10-25 (D114.3): yes**, up to 10 reminders a day. It may break quiet hours. A check-back is `interrupt`, inside the cap |
| Q4 | The default lead times | 30 minutes for a task block, as today. 10 minutes for an event |
| Q5 | The first kind in the composer for a new member | Task |
| Q6 | Google or Outlook first? | Google. The Gmail connect already runs on our Google project |
| Q7 | When does write-out start? | After read is live for 2 weeks, and only for events made in Metorite |
| Q8 | A guest with no connected account | Store the name and address. Send nothing. Offer Connect |
| Q9 | Do task blocks go to the provider as busy time? | No. An opt-in for each member, after CE-11 |
| Q10 | The gate of the new acts | `feature:tasks`, as D54.1 |

## 12. Out of scope

- A team or company calendar, and free and busy of a colleague (D54.4).
- Several blocks for one task, and the WS-21 kinds `break`, `batch` and
  `ritual`. The table leaves room for them (§3.2).
- Room and resource booking.
- Any mail that Metorite sends itself.
- Auto-join of the meeting bot (Note Taker D10).
