# Calendar — events, meetings and reminders

<!-- ste-tier: strict -->

- **Status:** PROPOSED, 2026-10-15, for events (§3, §4, §8). **Reminders
  (§5) are DECIDED, 2026-10-25 (D114).** **PN-1 (the web push platform) is
  BUILT, 2026-10-25, dark behind `WEB_PUSH_ENABLED`** (migration 306,
  `gateway/push/`, `public/sw.js`). **RM-1 (the reminder store and API) is
  BUILT, 2026-10-25, dark behind `CALENDAR_REMINDERS`** (migration 307,
  `routes/projects/reminders.py`). **RM-2 (the loop and the smart slot) is
  BUILT, 2026-10-26, dark** (`gateway/reminders/`). **RM-3 (the chat tools) is BUILT,
  2026-10-26, dark**: the tools refuse before a card while
  `CALENDAR_REMINDERS` is off. **RM-4 (a tap opens the chat) is BUILT,
  2026-10-29, dark** (`routes/chat_reminder.py`, `reminders/context.py`,
  `lib/shell/reminderOpen.ts`). RM-5 to RM-10 are not built. Verified
  against code on 2026-10-25 (`origin/main` `894ddad27`): no `reminders`
  table and no `remind_at` existed then.
- **Board row:** **WS-66** for reminders and web push. Events still wait on
  CE-0 for a row.
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

The Reach class column is the target of RE-9. **In RM-2 each of the three
kinds has the class `none`** in `reach/classes.py` (decision B1 of
2026-10-25). Today Reach sends only on WhatsApp, to members on its
allowlist, and D114.7 keeps reminders off WhatsApp. RE-9 raises the three
kinds to `urgent`, `interrupt` and `brief` on web push. RE-9 also adds the
D114.3 exception to the Reach budget.

**A class does not keep a card out of the WhatsApp brief** (review of
RM-2, 2026-10-26). The class gates an interrupt only, and the brief takes
every open card of Now and Needs you. So `reach/classes.NOT_ON_WHATSAPP`
holds the three kinds, and `reach/plan.on_whatsapp` keeps them out of each
WhatsApp brief, its template parameters, each interrupt and the preview.
The brief in the chat still shows them, because D114.7 allows the chat.
RM-9 lifts the rule.

- **The bell does not show reminders yet** (decision B2 of 2026-10-25). That
  is ticket RM-2b. The bell reads `/api/shell/needs`, so RM-2b needs a needs
  provider and a `KIND_ORDER` row for each kind.
- **The feed in RM-2.** Steps 1, 2 and 5 each give their card. Steps 3 and 4
  write their ledger rows, and the feed keeps the card until step 4 moves the
  row (decision B4 of 2026-10-25). The words of a Reach brief line belong to
  RE-9.
- **Web push.** Reach sends the card through RE-9 on the S7 transport. The
  payload holds the title and the time (D114.4), unless the member turned on
  "Hide details on the lock screen". Then it reads "Metorite: a reminder".
  The `url` is `/chat?reminder=<id>`.
- **On the minute.** A reminder must go at its minute, not at the next
  member pass. RE-9 owns the test that accepts a push within 90 seconds of
  `fire_at`. If Reach is slower, RE-9 adds the `not_before` of the old ask to
  WS-59. `proactive_reach.md` owns how.
- **Closed before it goes.** Reach reads the row again at send time. A
  reminder that is done, dropped or moved sends nothing.

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
- **The gate of the feed.** The source runs only while `MY_DAY_PROVIDERS`
  lists `reminders`, as each feed source since MD-3b does.
  `test_shell_feed.py` holds the feed answer the same while that list is
  empty. So the flip of reminders sets `CALENDAR_REMINDERS` AND adds
  `reminders` to `MY_DAY_PROVIDERS`.
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
  refuses there too. Both refusals send the model to a private task with a
  block (`create_personal_task`). `list_reminders` says the same when the
  flag is off.
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
- **One browser, several accounts.** The account switcher lets one browser
  hold several accounts, and all of them share one push subscription. PN-1
  settles the device rows: each account has its own row for the endpoint. A
  sign-out or a toggle-off deletes that account's row only. The browser
  unsubscribes only when no other account stays. A failed read of the
  account list keeps the subscription.
- **What RE-9 still settles.** A reminder title of account A can show while
  account B is in use. The default: the payload names the org, and "Hide
  details" covers a shared device.

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

### 8.2 Write second (CE-11)

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
| **CE-0** The owner's decisions | 🔴 OWNER | Answer Q1 to Q10 of §11. Mint the board row. Record the amendments the owner accepts | `work_plan.md` §3 holds the decision | — |
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

**The order.** CE-1 and CE-3 now, and CE-0 in parallel. Then CE-4, CE-5,
CE-6, CE-7b, CE-8, CE-9 and CE-12. Then CE-10 and CE-11.

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
| **RE-9** Push as a Reach channel | After PN-1, RM-2 | `proactive_reach.md` RE-9 on the PN-1 sender. The reminder payload of D114.4 and "Hide details on the lock screen". Settle the case of one browser with several accounts (§5.9) first | A `reminder_due` card reaches the device within 90 s of `fire_at`. A hidden-details member gets no title in the payload | `test_reach_plan.py` and `tests/unit/test_reach_push.py` |
| **RM-4** A tap opens the chat — ✅ **BUILT 2026-10-29, dark** (AGENT-SAFE) | After RM-3 | §5.7: `/chat?reminder=<id>`, the code-built first message, the chips, the `reminder` context block. As built: §5.7 | A tap on a push opens the chat with the reminder message and no model call. Done from a chip ends the reminder and offers Undo | `src/app/chat/reminderOpen.test.ts` (new, `.ts` because vitest runs `*.test.ts` only), `tests/unit/test_reminder_context.py` and `tests/unit/test_reminders_r8.py` |
| **RM-5** The Calendar UI | After RM-1 | The pin and the Month chip of §6.2. The Reminders panel: Upcoming, Waiting on you, Unconfirmed. The bell glyph of §6.5 | A reminder draws as a line with no height and takes no capacity. Drag moves `fire_at` | Vitest for the pin and the panel, the conformance suite, and a visual review |
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

## 11. Owner questions

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
