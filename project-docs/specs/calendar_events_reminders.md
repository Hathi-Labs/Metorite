# Calendar — events, meetings and reminders

<!-- ste-tier: strict -->

- **Status:** PROPOSED, 2026-10-15. No decision taken. Nothing built.
- **Board row:** none yet. CE-0 asks the owner to mint one.
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
The Calendar then reads one list from two sources. A reminder is a time on a
row that exists, so it adds no store. Every reminder reaches the member only
as a My Day card with a reach class, so Reach (D103) stays the one way out.

The new table amends D54.5, and a meeting with colleagues amends D54.4. Both
are the owner's call in CE-0. Four tickets need no decision change and can
start now: CE-1 (lead time), CE-2 (reminders on tasks), CE-3 (the composer)
and CE-7a (the reminder tool).

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
| **Reminder** | A moment when the member wants to hear about something | Only when it is on a task | A column on a row that exists |

**An event** may have attendees, a location, a call link and a repeat rule.
It may link to a task, a CRM deal, a CRM contact or a Notes meeting. A link
does not make it a task. A member does not complete an event. The event
ends.

**A reminder** has three forms:

1. **On a task.** "Remind me about this on Friday at 9." It is `remind_at`
   on my overlay of the task.
2. **On an event, as a lead time.** "Tell me 10 minutes before." It is
   `lead_minutes` on my attendee row of the event.
3. **Standalone.** "Remind me at 5 to call Ravi." It is a private task in my
   personal project, with a `remind_at` and no block. So "remind me" stays in
   the one task store (D53).

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

## 5. Reminders

### 5.1 The data

| Reminder | Column | Default |
|---|---|---|
| On a task | `pm_task_personal.remind_at` TIMESTAMPTZ | NULL |
| Before a task block | `pm_task_personal.lead_minutes` INT | NULL reads `user_settings.block_lead_minutes`, which reads 30 when NULL |
| Before an event | `calendar_entry_attendees.lead_minutes` INT | NULL reads `user_settings.event_lead_minutes`, which reads 10 when NULL |
| Standalone | A private task in my personal project with `remind_at` | — |

**The fixed 30 minutes goes.** `SOON` in `routes/shell/feed.py` becomes the
lead time of each block. With the default of 30, nothing changes for a
member who sets nothing.

### 5.2 The delivery

A reminder sends nothing. It makes a My Day card, and Reach plans the
card like any other (D103.1). The member sees it in three places:

1. **In the app:** the feed card and the bell count. The shell owns the bell
   (D89).
2. **On WhatsApp:** Reach sends it, under the link, opt-in and STOP rules.
3. **By web push,** when RE-9 lands.

| Card kind | When | Reach class | Ticket |
|---|---|---|---|
| `block_soon` | A block starts within its lead time | `interrupt` (exists) | CE-1 |
| `reminder_due` | The `remind_at` of an open task is now | `urgent`, as the member's own rule | CE-2 |
| `event_soon` | An event I attend starts within its lead time | `interrupt` | CE-6 |
| `event_invite` | A colleague adds me to an event | `brief` | CE-9 |
| `meeting_overlap` | MD-11's kind | MD-11 sets it | CE-10 |

`event_soon` takes the place of the illustrative `meeting_starts_soon` of
`proactive_reach.md` §4.3. Each kind enters `REACH_CLASS` in the same PR
that adds it (Reach W2).

### 5.3 Quiet hours and the budget

A reminder at a time is the member's own rule. D103.3 lets only the member's
own rule break quiet hours. So `reminder_due` has class `urgent`.

D103.3 also allows 1 urgent message each day. A member who sets 4 reminders
for one day loses 3 of them to the feed. The owner must decide whether a
member's reminder is outside the budget (Q3). That amends D103.3 and
D-RE-3. Until the owner answers, CE-2 keeps the budget as it is. A reminder
that does not fit shows in the feed and the bell only.

### 5.4 A request to WS-59

A reminder must go at its minute, not at the next member pass. The ask:
the planner plans a card with a fixed time ahead, with `not_before` set to
that time. The worker reads the task again at send time. A task done before
the time sends nothing. `proactive_reach.md` owns the planner, so WS-59
decides how.

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
| Reminder | Title, time, task | The time is the start of the range. No task makes a new private task |

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
| Reminder | A pin. A 2 px line across the day column at its minute, and a bell glyph in the gutter. No height | Drag moves the time. A click opens the task |

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
- **A task with a `remind_at`** shows a bell glyph and the time. It shows in
  Tasks, and on the Projects card for the member who set it. Only that
  member sees it, because the column is on the overlay.
- **A task that an event links to** shows a calendar glyph and the event
  time.
- **A standalone reminder** is a private task, so Tasks lists it under my
  personal project.

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
| `set_reminder` | Sets `remind_at` on a task, a lead time on an event, or makes a standalone reminder | Same |

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
| `CALENDAR_REMINDERS` | Gateway | CE-2, CE-7a | No `reminder_due` card |
| `NEXT_PUBLIC_CALENDAR_COMPOSER` | Control plane | CE-3 | No drag to draw. The Gap Filler as today |
| `CALENDAR_EVENTS` and `NEXT_PUBLIC_CALENDAR_EVENTS` | Both | CE-4 to CE-9, CE-12 | No events. `/version` reports a mismatch, as for `TASKS_LENS` |
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
| **CE-2** Reminders on tasks, and standalone | AGENT-SAFE. No change | `pm_task_personal.remind_at`. The kind `reminder_due`, with class `urgent` in `REACH_CLASS`. The bell glyph in Tasks and Projects. The pin on the grid. The ask of §5.4 | A `remind_at` of 17:00 gives a card at 17:00, not before. A done task gives none. Org B never sees a reminder of org A. A colleague on the same task never sees mine | `tests/unit/test_calendar_reminders.py` (R8, new, two orgs and two members) and `test_reach_plan.py` |
| **CE-3** The composer and drag to draw | AGENT-SAFE. No change | The composer of §6.1 with Task and Reminder. Event shows only with `NEXT_PUBLIC_CALENDAR_EVENTS`. Drag to draw in `TimeGrid.tsx`. The **New at…** row in the Gap Filler | A drag from 14:00 to 15:00 makes a private task with that block in one save. Esc saves nothing. The keyboard alone can do it | `components/Composer.test.tsx` (new), the conformance suite, `src/lib/shell/seams.test.ts`, and a visual review |
| **CE-4** The event store | AGENT-SAFE after CE-0 Q1 | The two tables of §3.3. `POST`, `PATCH` and `DELETE` on `/calendar/entries`. The read of §3.4 | I see my entries. A colleague who is not an attendee sees none. Org B sees no row of org A. The flag off gives today's read | `tests/unit/test_calendar_entries_r8.py` (new) and `test_db_engine_seam.py` |
| **CE-5** Events in the UI | AGENT-SAFE after CE-3, CE-4 | The Event kind, the looks of §6.2, the sheet of §6.3, the all-day row and Month of §6.4 | No event shows in Tasks or Projects. An all-day event keeps its date in every zone | Vitest for the composer, `MonthGrid` and the all-day row, and `themes.test.ts` |
| **CE-6** Event lead time and conflicts | AGENT-SAFE after CE-1, CE-4 | `event_soon` with class `interrupt`. `block_overlap` reads busy entries | A lead of 10 gives `event_soon` 10 minutes before. A free entry gives no overlap | `test_calendar_lead_time.py` and `test_shell_feed_r8.py` |
| **CE-7a** The reminder tool and job | AGENT-SAFE after CE-2. No change | `set_reminder` for a task and a standalone reminder. The `set-reminder` job. `ALLOWED_ACTS` | "Remind me at 5 to call Ravi" asks one confirm and makes one private task with `remind_at` 17:00 in my zone | `test_projects_chat_coverage.py`, `test_shell_intent.py`, `test_wac_writes.py` |
| **CE-7b** The event tools and job | AGENT-SAFE after CE-4 | `create_event`, `update_event`, `set_reminder` on an event. The `new-event` job | "Meet Sharath 8:30-10:30 tomorrow" opens a filled composer. The chat makes the event after one confirm | The three tests of CE-7a |
| **CE-8** Repeat rules | AGENT-SAFE after CE-4 | `recurrence`, `series_id`, `original_start`, expanded in the window. "This event" or "all events" on a change | A weekly 09:00 event in `Asia/Kolkata` stays at 09:00 in that zone. A cancelled instance does not show | `tests/unit/test_calendar_recurrence.py` (new), with a zone that changes its clock |
| **CE-9** Colleagues on an event | AGENT-SAFE after CE-0 Q2, CE-4 | Attendee rows with `member_email`. The rule of §4.1. `event_invite` with RSVP in place (D101) | An attendee sees the event. A colleague who is not one does not. Decline writes `rsvp` and mutes the entry | `test_calendar_entries_r8.py`, the visibility cases |
| **CE-10** Read Google and Outlook | 🔴 OWNER-GATE for §8.3. The code is AGENT-SAFE after CE-0 Q1, Q6, CE-4 | §8.1, and the provider of MD-11 | With recorded replies, a sync writes each instance once, and a second sync adds nothing. A disconnect deletes the rows of that connection | `tests/unit/test_calendar_sync_read.py` (R8, new) and the loop in `test_db_engine_seam.py` |
| **CE-11** Write events to the provider | 🔴 OWNER-GATE (§8.3). After CE-10 | §8.2 | The flag off sends no request. One event makes one provider event, and the next sync makes no copy | `tests/unit/test_calendar_sync_write.py` (new) |
| **CE-12** Notes meetings | AGENT-SAFE after CE-4 | `meeting_id`. **Prepare notes** and **Send notetaker**. The "Coming up" band reads entries | Prepare notes twice opens one `meeting` row. A moved event moves the band | `tests/unit/test_calendar_meeting_link.py` (new) |

**The order.** CE-1, CE-2, CE-3 and CE-7a now, and CE-0 in parallel. Then
CE-4, CE-5, CE-6, CE-7b, CE-8, CE-9 and CE-12. Then CE-10 and CE-11.

## 11. Owner questions

| # | Question | Default when the owner says nothing |
|---|---|---|
| Q1 | Accept option C, one `calendar_entries` table? It amends D54.5 | Yes. A task block stays on the overlay |
| Q2 | May a colleague see an event when they are an attendee, and nobody else? It amends D54.4 narrowly | Yes. No team calendar, no free and busy view |
| Q3 | May a member's own reminder use class `urgent`, and sit outside the daily budget? It amends D103.3 and D-RE-3 | Yes, up to 10 reminders a day. It may break quiet hours, because the member set it |
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
