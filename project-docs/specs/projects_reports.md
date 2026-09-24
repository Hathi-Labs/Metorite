# Projects · the reporting system — WS-27bn

**Status: ACTIVE, SPEC ONLY. Nothing in this file is built.** Written
2026-09-24 and verified against the code on 2026-09-24. Each anchor carries a
file name and a line number. Check them again at dispatch, because this tree
moves every day.

**Owner directive, 2026-09-24:** "I want to improve the reports feature". The
owner named the morning report as the first report. It tells a lead what each
person works on and where they are. It says who falls behind, who needs help,
and which tasks can move to another person. The owner also asked for reports on one
person, one team, one project, or the whole organization.

**Board row:** WS-27 · **ticket WS-27bn** · **owning spec:** this file.

**Parent spec:** `project_management_app.md` §9.12.7 (analytics) and §9.12.8
(reporting). Those sections stay the decision of record for what they decide.
This spec extends them and does not reopen them.

**Companions:**
- `projects_ai_chat.md` §13 (S7). It built the capacity, fit, rebalance and
  conflicts reads that this spec puts into reports.
- `people_center_app.md` §4.2. It owns the HR-tier gate.
- HANDOFF **H-111**. It owns the send job and the owner's email flag.
- HANDOFF **H-169**. It owns the outlook's capacity figure.

---

## 1. The answer, in one screen

**The numbers exist. The way to ask for them does not.** The server already
computes stuck work, load, capacity with absences subtracted, throughput,
cycle time, the forecast, conflicts and rebalancing suggestions. Every one of
those is a route, and six of them are report sections. But the Reports app has
one button, "New weekly report", which saves the server defaults. A member
cannot choose a scope, a period, a section or a schedule, and nothing sends a
report.

**So this spec adds three things, and no second arithmetic:**

1. **A report builder** over the API that exists (§6, slice R1).
2. **Templates.** A template is a named question with a preset of sections,
   scope and period. The morning report is the template "Team pulse" (§4).
3. **Four new sections, a person and team scope, stored runs and a daily
   schedule.** Each is a slice in §8.

**The rule that binds every slice: the server computes every number, and the
LLM only writes words about them.** A headline that the LLM writes links each
figure to the section that holds it. A report with a figure from nowhere is a
defect.

---

## 2. Scope and non-goals

**In scope:**
- The Reports app in the Projects app: its home screen, its builder and its
  reading layout.
- Report templates, and the sections that they need.
- A report about one person, one team, one project subtree, or the whole
  organization.
- Stored runs of a report, and the change since the last run.
- A daily schedule next to the weekly one, and the job that sends both.
- Entry points into a report from the node dashboard, from a person, from
  Tasks and from the chat.

**Non-goals:**
- **No second arithmetic.** Hours come from `gateway/work_schedule.py` and
  `gateway/capacity.py`. Pills come from `gateway/workload.py` `classify`. Fit
  comes from `rank_candidates`. `projects_ai_chat.md` §13.2 rule 1 binds this
  spec too.
- **No auto-assign.** A suggestion opens the assignment flow that exists. The
  member confirms each change.
- **No time tracking.** §9, question Q3, holds that decision for the owner.
- **No new chart library.** Charts use the categorical ramp through
  `src/lib/categorical.ts`, as §9.12.7 says.
- **No public share link.** A stakeholder report (§4, T9) reaches a person
  outside the organization only through email, and only after H-111 is armed.
- **No PDF export** in this spec. The email body and the in-app page are the
  two formats.

---

## 3. What exists today (measured 2026-09-24)

### 3.1 The server

| Read | Route | Report section |
|---|---|---|
| What we finished | `GET /projects/analytics/finished` · `analytics.py` | `finished` |
| Cycle time and weekly completions | `GET /projects/analytics/throughput` · `analytics.py` | `throughput` |
| Open work for each person | `GET /projects/analytics/load` · `analytics.py:517` | `load` |
| Work that nobody touched, blocked work, overdue work | `GET /projects/analytics/stuck` · `analytics.py:151` | `stuck` |
| Hours, absences, spare hours, the pill | `GET /projects/analytics/capacity` · `analytics_capacity.py:201` | `capacity` (opt-in) |
| Order, overlap and people conflicts | `GET /projects/analytics/conflicts` · `analytics_conflicts.py:173` | `conflicts` (opt-in) |
| Helpers for at-risk work, work for idle people | `GET /projects/analytics/rebalance` · `analytics_rebalance.py:103` | none |
| The forecast | `GET /projects/analytics/outlook` · `analytics.py:1431` | none |
| Who fits one task | `GET /projects/tasks/{id}/candidates` · `candidates.py` | none |

**The report routes** are in `routes/projects/reports.py`:
- `SECTIONS` (`:90`) is the vocabulary, in a fixed order. `DEFAULT_SECTIONS`
  (`:101`) is the four that a definition with no `sections` key renders.
- `_DEFAULTS` (`:107`): one week, the current week skipped, the subtree
  included.
- `SCHEDULES` (`:117`) holds only `"weekly"`.
- `delivery_armed()` (`:120`) reads `PROJECT_REPORT_EMAIL_ENABLED`.
- The routes: list, create, get, patch, delete (`:273` to `:369`), render
  (`:383`), recipients (`:649` to `:730`) and schedule (`:748`).
- **A render resolves visibility from the caller.** A send renders once for
  each recipient, with that recipient's own visibility (H-111). So a report
  never shows a person more than they can open in the app.

**The table** is `pm_reports` (`infra/postgres/204_projects_reports.sql:47`).
Its scope is `project_id`, and NULL means the portfolio. `config` is JSONB,
validated in the route. `pm_report_recipients` is migration 205.

**The pills** are `workload.PILLS` (`workload.py:41`): `behind`, `at_risk`,
`overloaded`, `idle`, `on_track`. `classify` (`:166`) returns one pill, every
flag that applies, and a reason for each.

### 3.2 The client

- `components/ReportsView.tsx`. It lists saved reports and draws a render with
  `RenderedBody` (`:115`). The only create control is "New weekly report"
  (`:380`). It posts no config.
- `components/AnalyticsView.tsx` and `components/NodeDashboard.tsx`. They draw
  the same panels from `AnalyticsPanels.tsx`, for the portfolio and for one
  node.
- `lib/api.ts:1153` to `:1163`. The client can list, create, render and delete
  a report. It has no call for patch, recipients or schedule.
- `src/lib/reportEmail.ts`. It builds an email body with no colour.
  `sendReportEmail` throws while the flag is off.
- Analytics and Reports are `live` in `lib/projectApps.ts`.

### 3.3 The chat

`apps/skills/skill-projects/skill_projects/views.py` has `render_report`
(`:299`) and `status_report` (`:366`). The chat saves a report through
`writes.py` `REPORT_SECTIONS`. It can never send one.

### 3.4 The gaps

1. **No builder.** The API takes scope, period and sections. The UI sends
   none of them.
2. **No person or team scope.** `pm_reports` scopes by project node only.
3. **No daily report.** `SCHEDULES` is weekly only, and no job sends anything
   (H-111).
4. **No memory.** A render computes again and stores nothing. A report cannot
   say what changed since the last time you read it.
5. **Three reads have no section:** rebalance, outlook and fit.
6. **No "needs help" signal.** The pills say who is behind or overloaded. No
   read joins blocked work, stale work and overdue waiting-on items for one
   person.
7. **No time range picker** on the Analytics surfaces. They use the server
   defaults.

### 3.5 The reference agent, and what not to copy

The owner named `FracktalWorks/agent-project-manager` as a starting point. It
was read on 2026-09-24. Its morning report is one Python script with fixed
rules and no LLM. A person asks for it in chat, and it writes a Markdown file.
It reads ClickUp only.

**What it does well, and this spec keeps:**
- One status for each person, from rules a reader can check.
- Three parts: the work of each person, a table of people, and suggestions.
- Suggestions in two tiers: help a colleague who is behind, or take unassigned
  work.
- The LLM adds a short summary and does not change the numbers.

**What it gets wrong, and this spec must not copy:**
- It compares weekly capacity with a two-day task window. So almost everyone
  shows a light load. **Here, both sides of a comparison use one named
  window** (`projects_ai_chat.md` §13.3 rule 3).
- A person on leave shows as idle. **Here, the absence shows first.**
- "Behind" means one overdue task. **Here, behind is the `classify` pill, and
  "needs help" is a separate signal (§5).**
- A task with two assignees counts in full for each. **Here, the rebalance
  join lists a shared task once (§13.4, as built).**
- Due dates are UTC and "today" is local. **Here, the server reads the UTC
  day (§13.5 rule 3).**
- Skill match is a substring search. **Here, fit is `rank_candidates`, with
  skill level, recency and spare hours.**
- Nothing sends and nothing runs on a timer.

---

## 4. The catalogue — templates

A **template** is a named question. It sets the sections, the scope kind and
the default period. A member picks a template, then changes any of it. The
saved report keeps the template name in `config.template`, so the home screen
can group reports and the chat can name them.

**Status key:** ✅ every section exists · ◐ one or more sections are new in §8.

| # | Template | The question | Scope | Period | Sections | Status |
|---|---|---|---|---|---|---|
| T1 | **Team pulse** (the morning report) | How is each person on the team today, and who needs help? | team · project · org | today | `pulse`, `conflicts`, `rebalance` | ◐ |
| T2 | **My day** | What do I work on today, and what waits on me? | me | today | `pulse` for one person, `waiting` | ◐ |
| T3 | **What changed** | What happened since I last read this? | any | since the last run | `changes` | ◐ |
| T4 | **Weekly delivery** | What did we finish last week, and how fast? | project · org | last week | `finished`, `throughput`, `load`, `stuck` | ✅ (the default today) |
| T5 | **Project status** | Will this project finish on time, and what blocks it? | project | this week | `outlook`, `stuck`, `conflicts`, `finished` | ◐ |
| T6 | **1:1 prep** | How is this person doing over a month? | person | last 4 weeks | `finished`, `throughput`, `pulse`, `waiting` | ◐ ⚠️ Q1 |
| T7 | **Exceptions** | What is wrong right now, and nothing else? | any | today | `stuck`, `conflicts`, `hygiene` (only the high rows) | ◐ |
| T8 | **Capacity outlook** | Do we have the people for the next weeks? | team · org | next 2 to 6 weeks | `capacity`, `outlook`, `on_leave` | ◐ |
| T9 | **Stakeholder update** | A short project summary to send outside the team | project | last 2 weeks | `finished`, `outlook`, with no per-person rows | ◐ |
| T10 | **Portfolio health** | Which projects are healthy? | org | this month | `outlook` for each child project, `capacity` | ◐ |
| T11 | **Focus and switching** | Who is spread over too many projects? | team · org | this week | `conflicts` (`parallel_person` only), `load` | ✅ |
| T12 | **Retrospective** | What slipped in the period, and why? | project | a closed period | `finished`, `throughput`, `changes` (slips only) | ◐ |
| T13 | **Data hygiene** | Which tasks make every other report wrong? | project · org | today | `hygiene` | ◐ |

**Templates live on the server,** as a `TEMPLATES` map in `reports.py` next to
`SECTIONS`. The builder, the chat and the email read the one map.
`test_projects_report_sections_lockstep.py` already holds five lists of section
names together, and it gains the template map.

---

## 5. Signals — what each word in a report means

Every word below is a rule a reader can check. The report prints the reason
next to each status, as `classify` already does.

| Word | Rule | Where it comes from |
|---|---|---|
| **Behind**, **At risk**, **Overloaded**, **Idle**, **On track** | The `classify` pill, over the named window | `workload.py:166` — exists |
| **On leave** | An absence today, from `people_absences`. It shows before every pill. | `work_schedule.py` — exists |
| **Stale** | An open task with no activity for N days. N comes from the `stuck` bands. | `analytics.py` `stuck` — exists |
| **Blocked** | An open task with an open blocker (`pm_task_links`) | `stuck` and `conflicts` — exist |
| **Waiting** | A `pm_task_personal` row with `waiting_on`, and `expected_by` before today | migration 187/188 — the column exists, no read uses it in a report |
| **Needs help** | NEW. A person with one or more of: a blocked task, a stale task in progress, a waiting item past its date, or the `behind` pill two runs in a row | The `pulse` section (R3) |
| **Has room** | The `idle` pill, or spare hours above a threshold in the window, and not on leave | `capacity` — exists |
| **Today's focus** | Tasks in progress, plus tasks due today or tomorrow, plus tasks that the person scheduled today (`scheduled_start`) | The `pulse` section (R3) |

⚠️ **"Needs help" is a flag, not a sixth pill.** `PILLS` is a closed vocabulary
that the People dashboard also draws. A sixth pill changes two apps. So the
`pulse` section adds `needs_help: bool` and a `help_reasons` list beside the
pill.

⚠️ **"Behind two runs in a row" needs stored runs (R4).** Until R4 lands, the
`pulse` section leaves that clause out and says so in its response.

---

## 6. The UI and UX

### 6.1 The Reports home

The home screen has three areas, top to bottom.

1. **For you.** The latest run of each report that you receive or pinned, as
   cards. A card shows the template, the scope, the headline and the change
   since the last run. An empty state offers T1 and T2.
2. **Start from a question.** The template gallery from §4. Each card carries
   the question in plain words, not the template's internal name.
3. **Saved reports.** The list that exists today, with a filter for scope,
   template and owner.

### 6.2 The builder — one sentence

The builder is one sentence of chips, with a live preview beside it:

> Show **[Team pulse ▾]** for **[Hardware team ▾]** over **[Today ▾]**, and
> send it to **[me ▾]** every **[weekday at 09:00 ▾]**.

- **The scope chip** opens one picker with four groups: people, teams
  (`org_group`), the project tree, and "Whole organization".
- **The period chip** offers today, this week, last week, the last 4 weeks and
  a custom range. A forward template (T8) offers the next 2, 4 or 6 weeks.
- **"More"** opens the section list, so a member can add or remove a section.
  Order stays fixed by `SECTIONS` (`reports.py:80` to `:89` gives the reason).
- **The preview renders from the server** on each change, with a short delay.
  It uses `/render` with an unsaved config. No figure is computed in the
  browser (§9.12.7).
- **The delivery half** is hidden until the member opens it. While
  `delivery_armed()` is false, the builder says that email is off for this
  organization and saves the schedule anyway.

On a phone, the preview moves under the sentence, and each chip takes the full
width.

### 6.3 Entry points

A member reaches a report from where they already are, with the scope filled
in.

| From | Control | Opens |
|---|---|---|
| A space, folder or project dashboard (`NodeDashboard.tsx`) | "Report on this" | The builder, scope set to the node |
| A person in the People app or a task's assignee | "1:1 prep" | T6, scope set to the person. Shown only when Q1 allows it. |
| My Tasks | "My day" | T2 for me |
| The Projects chat | "Give me the morning report for Design" | `render_report` with T1. The chat draws the card that exists. |
| An email | "Open in Metorite" | The stored run (R4), or a live render before R4 |

### 6.4 The reading layout

One layout for every template. The top answers the question, and the detail
comes after.

1. **The headline.** Two or three sentences. With R6 on, the LLM writes them.
   Without R6, a fixed sentence built from the counts. Each figure links to its
   section.
2. **Attention cards.** The exceptions, sorted by severity: needs help,
   behind, conflicts at `high`, then the rest. Each card names the person or
   the task, and the reason.
3. **The body.** A people report shows one row for each person, grouped by
   status. The groups are needs help, behind, overloaded, at risk, on track,
   has room and on leave. For a project report, one row for each child project. A row opens to
   show its tasks.
4. **Suggestions.** Each rebalance row carries an **Assign** control. It opens
   the confirm card that the task panel and the chat already use. A
   **Dismiss** control hides that suggestion for this report until the facts
   change.
5. **The change mark.** With R4 on, each figure carries an arrow and the value
   of the last run.

**Every figure opens a filtered task list.** A count that you cannot open is a
count that you cannot check.

**Look and feel.** The report uses the one look (`DESIGN_SYSTEM.md`). Status
uses `src/lib/statusAccent.ts`. Severity uses the destructive and warning
tokens, as the Conflicts panel does (`projects_ai_chat.md` §13.5, as built).
An email carries no colour (§9.12.8).

---

## 7. Data and privacy rules

1. **The viewer's scope.** Every section uses `task_visibility_clause` and
   `reportable_with_ancestors_clause`, as `analytics.py` does. A report about
   a team shows only the tasks that the reader can open.
2. **The HR tier is gated.** Hours, absences, skills and fit need
   `admin:members:read`. Without it, a section returns its task half and
   `hr_visible: false`, as S7 does. The UI says that an admin can see
   capacity. It does not guess.
3. **One render for each recipient.** A send renders with the recipient's
   visibility (H-111). This rule already exists and binds R7.
4. **A person scope is not a licence.** A report about one person shows that
   person's work that the reader can see. Q1 decides who may open T6 at all.
5. **D-PM-32 applies.** A stopped project's open work leaves every report. Its
   finished history stays. `reports.py` applies this in the render.
6. **R5 binds every new table.** A stored run (R4) carries `organization_id`
   and passes `test_tenant_coverage.py`.

---

## 8. Slices

Each slice is one PR. Take the migration number at build time and check it
again at merge (R1). Build each behind the report vocabulary, so an old report
renders as it did.

### R1 — The builder over the existing API · AGENT-SAFE

**What:** the sentence builder of §6.2 for scope (project node or portfolio),
period, sections and name. Add `patch` to `lib/api.ts`. Add an unsaved-config
render: `POST /projects/reports/preview` takes a config and returns the render
body. It writes nothing, so it goes into `READ_ONLY_POSTS`.

**Files:** `components/ReportsView.tsx` · `lib/api.ts` ·
`routes/projects/reports.py` · the chat manifest, which maps the new route or
excludes it (D-PM-37).

**Done when:**
- A member creates a report with a chosen scope, period and sections, and the
  saved `config` holds exactly those values.
- A member edits a saved report, and the render changes to match.
- The preview renders a config that is not saved, and writes no row.
- A config with a section outside `SECTIONS` gets 422.
- `test_projects_chat_coverage.py` passes.

### R2 — Templates and the home screen · AGENT-SAFE

**What:** the `TEMPLATES` map (§4), `config.template`, the gallery and the
"For you" area of §6.1. Only the templates whose sections exist go live in
this slice: T4 and T11. The others show in the gallery as "coming soon" and
cannot be chosen.

**Done when:**
- `GET /projects/reports/templates` returns the map, and the gallery draws it.
- Choosing a template fills the builder, and the member can still change each
  chip.
- The lockstep test holds `TEMPLATES` and fails if a template names a section
  outside `SECTIONS`.

### R3 — Four new sections · AGENT-SAFE

Each section is one server read and one entry in the five lockstep lists
(`reports.py` `SECTIONS`, `RenderedBody`, `reportEmail.ts`, the chat's
`_REPORT_SECTIONS`, `writes.py` `REPORT_SECTIONS`). Each is opt-in, and none
goes into `DEFAULT_SECTIONS`.

| Section | What it returns | It calls |
|---|---|---|
| `pulse` | One row for each person in scope: pill, flags, reasons, `needs_help`, `help_reasons`, on leave, today's focus (up to 5 tasks), stale count, blocked count, waiting count | `person_capacity`, `classify`, the `stuck` predicates, `pm_task_personal` |
| `rebalance` | The rebalance route's body, capped like `load` | `analytics_rebalance.py` |
| `outlook` | The forecast for the scope, and for each child project on a portfolio | `analytics.py` `outlook` |
| `hygiene` | Open tasks with no assignee, no due date or no estimate, and tasks in progress with no activity. Counts, and up to 20 rows of each kind. | new SQL over `load_open_where` |

**Done when:**
- Each section renders in the app, in the email body with no colour, and in
  the chat card.
- `pulse` gives the same pill as the capacity route for the same person and
  window. One test pins this, so the two cannot drift.
- A person on leave today shows "on leave" and no `idle` pill.
- A shared task counts once in `rebalance`.
- Each new query runs against a real database (R8).

⚠️ `waiting` for T2 and T6 is the `pulse` waiting rows for one person. It is
not a fifth section.

### R4 — Stored runs and "what changed" · AGENT-SAFE

**What:** a table `pm_report_runs` (report, organization, rendered for, period,
body JSONB, created). The render writes a run when a schedule fires or when a
member presses "Save this run". The home screen reads the last run. The
`changes` section compares `pm_activities` since the reader's last run of the
report: completed, slipped due date, reassigned, blocked, created.

**Rules:**
- Expand only (R6). The table is new, nullable where it can be, and tenant
  scoped (R5).
- A run stores the body that the reader saw, with the reader's visibility. A
  run is never shown to a second person, because their visibility differs.
- Keep 90 days of runs for each report. A cleanup deletes older runs.

**Done when:** a second run shows an arrow and the last value on each figure.
`changes` lists each event type with a link to its task.

### R5 — Person and team scope · AGENT-SAFE after Q1

**What:** `config.subject` with `kind` `person` or `team`, and an address or
an `org_group` slug. The sections filter their task half to that subject's
assignments. `pm_reports.project_id` stays the node scope, so the two combine:
"the Hardware team, in the Printer project".

**Rules:**
- The subject is validated against the directory, as recipients are. Free
  text gets 422.
- A team expands to its members at render time, not at save time. A new
  member joins the report by joining the team.
- Q1 decides whether T6 needs the HR grant or a lead role.

**Done when:** a report on one person shows only that person's rows. A report
on a team shows each member once. A person outside the directory gets 422.

### R6 — The headline · AGENT-SAFE to build, OWNER-GATE to turn on

**What:** an LLM writes two or three sentences from the render body. The body
is the only input. Each figure in the text must match a figure in the body. A
check compares them and drops the headline if one does not match. The
fallback is the fixed sentence of §6.4.

**Rules:**
- The call goes through the Router, and the org pays for it in AI credits.
  So it is off by default for each report, behind the flag
  `PROJECT_REPORT_HEADLINE_ENABLED`. Q4 decides who turns it on.
- The prompt fences task titles as data, as the chat does (§13.5, as built).

**Done when:** a headline with an invented figure is dropped by the check. A
test proves this with a fake model that returns a wrong number.

### R7 — The daily schedule and the send job · AGENT-SAFE to build, OWNER-GATE to arm

**What:** `SCHEDULES` gains `weekday_daily`. A schedule carries a local time
and the recipient's timezone decides the hour. The job is the one H-111 names
as missing. It copies the claim pattern of `routes/workflows/scheduler.py`: it
claims `last_sent_at` with compare-and-set, so two workers cannot send twice.
It renders once for each recipient (§7 rule 3) and writes a stored run (R4).

**Rules:**
- The job runs dark. `delivery_armed()` stays the second lock, and
  `PROJECT_REPORT_EMAIL_ENABLED` stays the owner's (CLAUDE.md §3a rule 3).
- In-app delivery needs no email. With the flag off, a scheduled report still
  writes its run, and "For you" shows it. This is the first channel (Q2).
- A send that fails is logged with the report id and the recipient, and it
  does not advance `last_sent_at`.

**Done when:** with the flag off, a due schedule writes one run for each
recipient and sends nothing. With a fake sender and the flag on, it sends
once, even when two workers claim the same report.

### R8 — Entry points and the chat · AGENT-SAFE

**What:** the controls of §6.3. The chat's `render_report` accepts a template
name and a subject, so "morning report for Design" works. The chat still
cannot send (D-PM-37 class X).

**Done when:** each control opens the builder with the scope filled in, and
the chat renders T1 for a team by name.

### Order

**R1 → R2 → R3 → R4 → R7**, with R5 after Q1 and R6 after Q4. R1 and R2 give a
member control of what exists. R3 makes the morning report real. R4 gives it a
memory. R7 delivers it.

---

## 9. Questions for the owner

| # | Question | Recommendation |
|---|---|---|
| **Q1** | Who may open a report about one person (T6, 1:1 prep)? It is performance data. | The person, and a member with `admin:members:read`. The task half follows visibility for everyone else. |
| **Q2** | Which channel comes first? | In-app first, because it needs no gate (R7). Email second, when H-111 is armed. WhatsApp later, through the digest that exists. |
| **Q3** | Do we add time tracking? | Not now. Estimates and `actual_start`/`actual_end` carry the reports. Look again when a customer asks for billable hours. |
| **Q4** | Who turns on the LLM headline, which spends credits? | A report owner, for each report, after the owner prices the AI rate card (H-42). |
| **Q5** | When does the morning report send? | 09:00 on weekdays, in each recipient's timezone. The report owner can change the time. |

**One question is already answered:** whose view a scheduled report uses. The
send renders once for each recipient with that recipient's visibility (H-111,
answered 2026-09-17).

---

## 10. Verification commands

Run the database first, or 843 tests skip while the run reads green:

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
```

Server:

```bash
uv run pytest tests/unit/test_projects_reports.py \
  tests/unit/test_projects_report_recipients.py \
  tests/unit/test_projects_report_sections_lockstep.py \
  tests/unit/test_projects_reportable_reports.py \
  tests/unit/test_projects_analytics_capacity.py \
  tests/unit/test_projects_analytics_rebalance.py \
  tests/unit/test_projects_analytics_conflicts.py \
  tests/unit/test_projects_chat_coverage.py \
  tests/unit/test_tenant_coverage.py -q
```

Client, in `workbench/control_plane`:

```bash
npx tsc --noEmit && npx vitest run src/app/projects src/lib/reportEmail.test.ts
```

Each slice adds its own test file and names it in its PR. A UI slice also runs
the `visual-review` skill: light mode, compact density, a changed accent, and
the phone width.
