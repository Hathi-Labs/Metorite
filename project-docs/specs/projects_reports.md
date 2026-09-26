# Projects · the reporting system — WS-27bn

**Status: ACTIVE. R1 BUILT 2026-09-24** (the builder, `render_body` and
`POST /projects/reports/preview`). **R2 BUILT 2026-09-24** (the template
catalogue, `config.template`, the gallery and "Your reports"). **R2b BUILT
2026-09-24** (the visual report body: each section draws its Analytics panel,
with its table folded under it). **R3a BUILT 2026-09-25** (the `outlook`
section, the ageing bands in `stuck`, and T5 live).

**R3b BUILT 2026-09-25** (the `rebalance` section and `RebalancePanel`, read
only). R3c is next. R3d, R4, R4b, R5 to R9 and Phases 2 and 3 are not built.

Written
2026-09-24 and verified against the code on 2026-09-24. The owner answered
the open questions on 2026-09-24, and §9 records the answers. Each anchor carries a
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
3. **Four new sections, a person and team scope, stored runs, an AI summary
   and an "Email this report" action.** Each is a slice in §8.

**Phase 1 generates a report on request only** (owner, 2026-09-24). A member,
or the chat for a member, asks for a report, and the server renders it. Phase
2 adds the timer that renders a report by itself and emails it. Phase 3 lets
other apps, such as Workflows, use reports. All three phases call one render
function, so a report reads the same wherever it comes from.

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
- An AI summary that a member asks for, paid with AI credits.
- "Email this report": a member or the chat sends one render to one person
  in the directory, on request.
- Entry points into a report from the node dashboard, from a person, from
  Tasks and from the chat. The chat can list, create, render and email a
  report.
- **Phase 2 (later):** a report that renders on a timer and emails itself.
- **Phase 3 (later):** a report as a step that Workflows and other apps call.

**Non-goals:**
- **No second arithmetic.** Hours come from `gateway/work_schedule.py` and
  `gateway/capacity.py`. Pills come from `gateway/workload.py` `classify`. Fit
  comes from `rank_candidates`. `projects_ai_chat.md` §13.2 rule 1 binds this
  spec too.
- **No auto-assign.** A suggestion opens the assignment flow that exists. The
  member confirms each change.
- **No time tracking** (owner, 2026-09-24). Estimates, `actual_start` and
  `actual_end` carry every hours figure.
- **No automatic AI summary.** The AI summary runs only when a person asks
  for it (owner, 2026-09-24).
- **No new chart library.** A report draws with the Analytics panels (R2b).
  A categorical series uses the `--cat-*` ramp through `src/lib/categorical.ts`,
  as §9.12.7 says. State uses `src/lib/statusAccent.ts`.
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
| Work that nobody touched, blocked work, overdue work | `GET /projects/analytics/stuck` · `analytics.py:151` | `stuck` (the bands since R3a) |
| Hours, absences, spare hours, the pill | `GET /projects/analytics/capacity` · `analytics_capacity.py:201` | `capacity` (opt-in) |
| Order, overlap and people conflicts | `GET /projects/analytics/conflicts` · `analytics_conflicts.py:173` | `conflicts` (opt-in) |
| Helpers for at-risk work, work for idle people | `GET /projects/analytics/rebalance` · `analytics_rebalance.py:103`, body `rebalance_body` · `:127` | `rebalance` (opt-in, R3b) |
| The forecast | `GET /projects/analytics/outlook` · `analytics.py:1431`, body `outlook_body` · `:1474` | `outlook` (opt-in, R3a) |
| Who fits one task | `GET /projects/tasks/{id}/candidates` · `candidates.py` | none |

**The report routes** are in `routes/projects/reports.py`:
- `SECTIONS` (`:103`) is the vocabulary, in a fixed order. `DEFAULT_SECTIONS`
  (`:115`) is the four that a definition with no `sections` key renders.
- `_DEFAULTS` (`:121`): one week, the current week skipped, the subtree
  included.
- `TEMPLATES` (`:162`) is the template catalogue of §4 (R2).
- `SCHEDULES` (`:254`) holds only `"weekly"`.
- `delivery_armed()` (`:257`) reads `PROJECT_REPORT_EMAIL_ENABLED`.
- The routes: list is at `:440` and templates at `:474`. Create is at
  `:491`. Get, patch and delete are at `:525` to `:569`. Render is at `:840`
  and preview at `:876`. Recipients are at `:962` to `:1043`, and schedule is
  at `:1061`.
- `render_body` (`:583`) holds the section loop. Render and preview both
  call it.
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
  `RenderedBody` (`:201`). The create control is "New report" (`:946`).
- `components/AnalyticsView.tsx` and `components/NodeDashboard.tsx`. They draw
  the same panels from `AnalyticsPanels.tsx`, for the portfolio and for one
  node.
- `lib/api.ts:1275` to `:1313`. The client can list, create, patch, preview,
  render and delete a report, and read the templates. It has no call for
  recipients or schedule.
- **Since R1:** `ReportsView.tsx` has the builder (`ReportBuilder`, `:486`).
  `lib/reportBuilder.ts` holds the builder's choices as pure functions.
- **Since R2:** `ReportsView.tsx` has the home screen (`ReportsHome`, `:758`)
  and the template card (`TemplateCard`, `:701`).
- `src/lib/reportEmail.ts`. It builds an email body with no colour.
  `sendReportEmail` throws while the flag is off.
- Analytics and Reports are `live` in `lib/projectApps.ts`.

### 3.3 The chat

`apps/skills/skill-projects/skill_projects/views.py` has `render_report`
(`:433`) and `status_report` (`:491`). Its `REPORT_CARD_SECTIONS` (`:316`)
names the words of the chat card. The chat prints a section through
`reads.py` `_REPORT_SECTIONS` (`:1224`), and saves a report through
`writes.py` `REPORT_SECTIONS` (`:854`). It can never send one.

### 3.4 The gaps

1. **No builder.** The API takes scope, period and sections. The UI sends
   none of them.
2. **No person or team scope.** `pm_reports` scopes by project node only.
3. **No daily report.** `SCHEDULES` is weekly only, and no job sends anything
   (H-111).
4. **No memory.** A render computes again and stores nothing. A report cannot
   say what changed since the last time you read it.
5. **Two reads have no section:** rebalance and fit. Outlook became a
   section in R3a.
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
saved report keeps the template key in `config.template`, so the home screen
can group reports and the chat can name them.

**Status key:** ✅ live (T4 since R2, T5 since R3a) · ◐ it waits for a section, a scope, a period
or a filter that §8 adds. The gallery shows a ◐ template as "coming soon".

| # | Key | Template | The question | Scope | Period | Sections | Status |
|---|---|---|---|---|---|---|---|
| T1 | `team_pulse` | **Team pulse** (the morning report) | How is each person on the team today, and who needs help? | team · project · org | today | `pulse`, `conflicts`, `rebalance` | ◐ |
| T2 | `my_day` | **My day** | What do I work on today, and what waits on me? | me | today | `pulse` for one person, `waiting` | ◐ |
| T3 | `what_changed` | **What changed** | What happened since I last read this? | any | since the last run | `changes` | ◐ |
| T4 | `weekly_delivery` | **Weekly delivery** | What did we finish last week, and how fast? | project · org | last week | `finished`, `throughput`, `load`, `stuck` | ✅ (the default today) |
| T5 | `project_status` | **Project status** | Will this project finish on time, and what blocks it? | project | this week | `finished`, `outlook`, `stuck`, `conflicts` | ✅ (since R3a) |
| T6 | `one_on_one` | **1:1 prep** | How is this person doing over a month? | person | last 4 weeks | `finished`, `throughput`, `pulse`, `waiting` | ◐ (§7.1 limits who may open it) |
| T7 | `exceptions` | **Exceptions** | What is wrong right now, and nothing else? | any | today | `stuck`, `conflicts`, `hygiene` (only the high rows) | ◐ |
| T8 | `capacity_outlook` | **Capacity outlook** | Do we have the people for the next weeks? | team · org | next 2 to 6 weeks | `capacity` (it carries absences), `outlook` | ◐ |
| T9 | `stakeholder_update` | **Stakeholder update** | A short project summary to send outside the team | project | last 2 weeks | `finished`, `outlook`, with no per-person rows | ◐ |
| T10 | `portfolio_health` | **Portfolio health** | Which projects are healthy? | org | this month | `outlook` for each child project, `capacity` | ◐ |
| T11 | `focus_switching` | **Focus and switching** | Who is spread over too many projects? | team · org | this week | `conflicts` (`parallel_person` only), `load` | ◐ (it waits for a conflict-kind filter) |
| T12 | `retrospective` | **Retrospective** | What slipped in the period, and why? | project | a closed period | `finished`, `throughput`, `changes` (slips only) | ◐ |
| T13 | `data_hygiene` | **Data hygiene** | Which tasks make every other report wrong? | project · org | today | `hygiene` | ◐ |

**T11 waits for a filter.** `conflicts_body` (`analytics_conflicts.py:366`)
takes no argument for the conflict kind. So a T11 report would show every
conflict, and not only `parallel_person`.

**Templates live on the server,** as a `TEMPLATES` map in `reports.py` next to
`SECTIONS`. `GET /projects/reports/templates` serves it in catalogue order.
The builder, the chat and the email read the one map, and the client holds no
copy. `test_projects_report_sections_lockstep.py` holds seven lists of
section names together (§8 R3), and it holds the template map too.

**The keys are append-only.** A saved report keeps its key in
`config.template`, and the server checks each config again when it reads a
row. So a removed key makes each list and render of that report fail with
422. The lockstep test pins the 13 keys.

---

## 5. Signals — what each word in a report means

Every word below is a rule a reader can check. The report prints the reason
next to each status, as `classify` already does.

| Word | Rule | Where it comes from |
|---|---|---|
| **Behind**, **At risk**, **Overloaded**, **Idle**, **On track** | The `classify` pill, over the named window | `workload.py:166` — exists |
| **On leave** | An absence today, from `people_absences`. It shows before every pill. | `work_schedule.py` — exists |
| **Stale** | An open task in the `in_progress` category whose `updated_at` is `STALE_DAYS` (14) or more days old. `record_activity` and each satellite write bump `updated_at`, so a comment or a new assignee resets it. | `analytics.py` `STALE_DAYS`. The `hygiene` section uses it now, and `pulse` uses it later. |
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

**In R2, "For you" is "Your reports".** It shows the reports that you
created, newest first, at most six. A card shows the name, the template name
(or "Custom") and the scope. The server marks each row `mine`, from the
authenticated caller.

**No card renders when the home screen loads.** A click opens the report, and
the render runs then. The headline and the change mark wait for stored runs
(R4). Pins wait for a later slice. The empty state offers the live templates,
and in R2 that is T4 only.

**In R2, the saved list has no filter.** The filter for scope, template and
owner waits for a later slice.

### 6.2 The builder — one sentence

The builder is one sentence of chips, with a live preview beside it:

> Show **[Team pulse ▾]** for **[Hardware team ▾]** over **[Today ▾]**.

- **The scope chip** opens one picker with four groups: people, teams
  (`org_group`), the project tree, and "Whole organization". It lists only
  the people and teams that §7.1 lets this member report on. A member with no
  admin grant and no lead role sees "Me" in the people group, and nobody else.
- **The period chip** offers today, this week, last week, the last 4 weeks and
  a custom range. A forward template (T8) offers the next 2, 4 or 6 weeks.
- **In R1, the period chip offers three periods only**, because the config
  holds only `weeks` and `skip_current_week`:
  - "Last week" is `weeks` 1 with `skip_current_week` true.
  - "This week" is `weeks` 1 with `skip_current_week` false.
  - "The last 4 weeks" is `weeks` 4 with `skip_current_week` true.

  Today, a custom range and the forward periods need new config fields. A
  later slice adds those fields and those periods.
- **"More"** opens the section list, so a member can add or remove a section.
  Order stays fixed by `SECTIONS` (`reports.py:80` to `:92` gives the reason).
- **The preview renders from the server** on each change, with a short delay.
  It uses `POST /projects/reports/preview` with an unsaved config. The
  browser computes no figure (§9.12.7).
- **No schedule control in Phase 1.** A report renders when a member opens
  it. Phase 2 adds the schedule chip: "and send it to [person] every
  [weekday at 09:00]".

On a phone, the preview moves under the sentence, and each chip takes the full
width.

### 6.3 Entry points

A member reaches a report from where they already are, with the scope filled
in.

| From | Control | Opens |
|---|---|---|
| A space, folder or project dashboard (`NodeDashboard.tsx`) | "Report on this" | The builder, scope set to the node |
| A person in the People app or a task's assignee | "1:1 prep" | T6, scope set to the person. Shown only when §7.1 allows it. |
| My Tasks | "My day" | T2 for me |
| The Projects chat | "Give me the morning report for Design" | `render_report` with T1. The chat draws the card that exists. |
| The Projects chat | "Email it to Priya" | `send_report` (R9), behind a confirm card |
| An email | "Open in Metorite" | The stored run (R4), or a live render before R4 |

### 6.4 The reading layout

One layout for every template. The top answers the question, and the detail
comes after.

1. **The headline.** A fixed sentence built from the counts, which costs
   nothing. Beside it, a **Summarize with AI** control (R6). When the member
   presses it, the LLM writes two or three sentences, and the org pays in AI
   credits. Nothing calls the LLM before a person asks. Each figure links to
   its section.
2. **Attention cards.** The exceptions, sorted by severity: needs help,
   behind, conflicts at `high`, then the rest. Each card names the person or
   the task, and the reason.
3. **The body.** A people report shows one row for each person, grouped by
   status. The groups are needs help, behind, overloaded, at risk, on track,
   has room and on leave. For a project report, one row for each child project. A row opens to
   show its tasks.
4. **Suggestions.** Each rebalance row will carry an **Assign** control, a
   **Dismiss** control and a link to its task. Slice R4b builds all three.
   No shared confirm card exists today, so R4b must make one first. Dismiss
   hides a suggestion until the facts change, so it needs the stored runs of
   R4. R3b shows the rows and nothing to act on.
5. **The change mark.** With R4 on, each figure carries an arrow and the value
   of the last run.
6. **Email this report** (R9). It opens a picker of people in the directory,
   then a confirm card. While `delivery_armed()` is false, the control says
   that email is off for this organization, and it sends nothing.

**Every figure opens a filtered task list.** A count that you cannot open is a
count that you cannot check.

**Look and feel.** The report uses the one look (`DESIGN_SYSTEM.md`). Status
uses `src/lib/statusAccent.ts`. Severity uses the destructive and warning
tokens, as the Conflicts panel does (`projects_ai_chat.md` §13.5, as built).
An email carries no colour (§9.12.8).

**Pictures first.** Each section shows a chart, a progress bar or a tile
first, and its table second. Slice R2b sets the rules.

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
   visibility (H-111). This rule already exists and binds R9 and R7.
4. **A person scope is not a licence.** A report about one person shows only
   the work of that person that the reader can see. §7.1 decides who may open
   it at all.
5. **D-PM-32 applies.** A stopped project's open work leaves every report. Its
   finished history stays. `reports.py` applies this in the render.
6. **R5 binds every new table.** A stored run (R4) carries `organization_id`
   and passes `test_tenant_coverage.py`.

### 7.1 Who may report on whom (owner, 2026-09-24)

**The reader's own role decides whose report they may open.** An admin
reports on everybody. A member reports on themselves. A team lead also reports
on their team.

| Reader | May open a report on | The check |
|---|---|---|
| **Admin** | Every person, every team, the whole organization | `can_read_hr_fields` (`routes/tasks/core.py:75`), the `admin:members:read` grant |
| **Team lead** | Themselves, each team where they are `lead`, and each member of those teams | `org_group_member.role = 'lead'` (migration 138) |
| **Member** | Themselves | The subject is the reader's own address |
| **Member, with the org setting on** | Themselves, and each team they belong to, as a whole | A new org setting, `reports.members_see_own_team`, default off |

**Rules for the build.**
1. **One check, on the server.** A function `may_report_on(user, subject)`
   in `reports.py` answers for a person and for a team. Every path calls it:
   the builder's picker, the render, `send_report`, the chat and, later, the
   schedule and Workflows. The picker lists what the function allows. It
   never shows a subject and then refuses it.
2. **A refusal gives 403 and says why.** The chat tells the member which
   role would allow it. It does not guess.
3. **Per-person rows follow the same rule.** A report on a project or on the
   whole organization contains per-person sections (`pulse`, `rebalance`,
   the HR half of `capacity`). A reader who is not an admin sees only the
   rows of the people that they may report on. The totals still count every
   person. One line says "This report hides N other people".
4. **"The team" is an `org_group`.** It expands to its members at render
   time. A member who leaves the team leaves the report on the next render.
5. **The member setting shows the team, and not each person.** With
   `reports.members_see_own_team` on, a member sees the team's totals and
   their own row. They cannot open a report on one teammate.
6. **The HR tier stays separate.** Hours, absences and skills still need
   `admin:members:read` (§7 rule 2). A lead without that grant sees the task
   half of their team. The capacity section tells the lead that an admin can
   see the hours.
7. **Rebalance rows need the HR grant, and only the admin row holds it.** So
   rule 3 holds for `rebalance` with no row filter until R5. R5 must look at
   it again when it gives a lead the rows of their team.

⚠️ **The `load` section is older than this rule.** It lists open work for each
person, and today any member who can see the tasks can read it. Slice R5
applies rule 3 to `load` too, so the Analytics app and a report agree. The
Load panel in the Analytics app then changes in the same way. The PR for R5
must say this.

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

**The chat manifest row.** In R1 the preview row is class X. Its reason
says that the chat reaches the preview in R8. R8 maps the row to
`render_report` and changes its class to A. R1 must not map it to
`render_report`. That tool does not call the route yet, so
`test_projects_agent.py` fails.

**Files:** `components/ReportsView.tsx` · `lib/api.ts` ·
`lib/reportBuilder.ts` · `routes/projects/reports.py` · the chat manifest
(D-PM-37).

**Done when:**
- A member creates a report with a chosen scope, period and sections, and the
  saved `config` holds exactly those values.
- A member edits a saved report, and the render changes to match.
- The preview renders a config that is not saved, and writes no row.
- A config with a section outside `SECTIONS` gets 422.
- `test_projects_chat_coverage.py` passes.
- **One render function.** The render route and the preview call one server
  function, `render_body`. It takes a config and a user. A real-DB test
  calls both on one config and one caller, and gets equal sections and an
  equal period. A second render path is a defect (CLAUDE.md §4). The chat,
  the schedule and a workflow step call the same function later (§8a).

### R2 — Templates and the home screen · AGENT-SAFE · BUILT 2026-09-24

**What:** the `TEMPLATES` map (§4), `config.template`, the gallery and "Your
reports" (§6.1). T4 only goes live in this slice. Its sections, its scope and
its period exist. The others show in the gallery as "coming soon", and a
member cannot choose them. T11 waits for a filter on the conflict kind (§4).

**`config.template`.** No template, or `null`, adds no key. A live key is
kept. An unknown key and a coming-soon key get 422. The key is an origin
label, so a member can change the sections or the period and keep the key.

**Route order.** `reports.py` declares `GET /reports/templates` above
`GET /reports/{report_id}`. Otherwise FastAPI reads "templates" as an id.

**The chat manifest row.** The templates route is class X. Its reason says
that the chat reaches templates in R8. The row sits above the `{report_id}`
row, because `route_for` takes the first match.

**H-182, folded in.** The report list hides a report on a project that the
caller cannot see. It uses `Visibility.project_clause`, as
`load_visible_project` does. A portfolio report stays in the list.

**Files:** `routes/projects/reports.py` · the chat manifest ·
`components/ReportsView.tsx` · `lib/api.ts` · `lib/reportBuilder.ts`.

**Done when:**
- `GET /projects/reports/templates` returns all 13 templates, and exactly
  `weekly_delivery` has `available: true`. A test calls the route through the
  app router. It proves that `/reports/{report_id}` does not capture
  "templates".
- Choosing T4 fills the builder with the sections `finished`, `throughput`,
  `load` and `stuck`, with `weeks` 1 and `skip_current_week` true. The member
  can still change each chip. A vitest proves the state.
- A save with `config.template` `"weekly_delivery"` stores it, and PATCH and
  render return it. A builder edit that changes only the sections keeps the
  template. A vitest on `configFor` proves the round trip, and a real-DB test
  proves the row.
- An unknown template gets 422. A coming-soon template, such as
  `"focus_switching"`, gets 422. A config with no template saves exactly as
  in R1, and the R1 test stays green.
- A coming-soon card cannot open the builder. A vitest proves that the
  from-template function refuses an unavailable entry.
- The lockstep test holds `TEMPLATES`. It fails if a live template names a
  section outside `SECTIONS`, or out of `SECTIONS` order. It fails if a
  coming-soon template carries sections. It fails if a key of the pinned 13
  disappears. A self-test with a fake live template that names `pulse` proves
  that the fence fires.
- "Your reports" lists only the rows with `mine: true`, newest first, at most
  six. A real-DB test with two authors proves `mine`.
- `route_for("GET", "/projects/reports/templates").cls` is `"X"`, and
  `test_projects_chat_coverage.py` passes.
- The list hides a report on a hidden project from a caller who cannot see
  it, and shows it to a caller with a grant. A real-DB test proves both.
- A portfolio report, with a NULL `project_id`, stays in the list for every
  caller. The same real-DB test proves it for the outsider and the viewer.
- `template` is not a key of `_DEFAULTS`. A config with no template stores no
  `template` key. `test_no_template_adds_no_key` proves it.
- The server computes `mine`. It compares `created_by` with `actor(user)` and
  ignores case. The client does not compute it.
  `test_mine_is_true_only_for_the_author` proves it.
- The client holds no copy of the template map. It reads the catalogue from
  `GET /projects/reports/templates`. This item is advisory, and no test fences
  it. To check it, search `src/` for a template key outside the test files.
  The search must find nothing.

### R2b — The visual report body · AGENT-SAFE · BUILT 2026-09-24

**As built.** `lib/reportPanels.ts` maps each section to its panel.
`reportVisuals.test.ts` is the fence, and
`test_projects_report_visuals.py` proves the pass-through on a real
database. Each folded table carries the section's old heading as its
label, because the chat's report card borrows those words.

**Owner directive, 2026-09-24:** the reports must have as many good visual
elements as possible, such as progress bars and charts. A reader must see the
state of the organization, a project or a person at a glance.

**What:** a rendered report draws each section as a picture first and a table
second. The pictures are the panels that the Analytics app already draws, so a
report and the Analytics app look the same.

**The audit of 2026-09-24 measured the gap.** Three panels need figures that
the render body drops. The analytics SQL already selects each of them, and
`render_body` does not copy them into the body. So R2b passes them through
(the supervisor chose option B on 2026-09-24).

**Pass-through figures.** `render_body` copies these columns, which the SQL
that it calls already selects. It adds no query and no arithmetic.
- `load`: `due_next_7d` and `later` for each person, from `load_sql`.
- `throughput`: `p90_hours`, `no_start` and `cancelled`, from
  `_CYCLE_MEASURES`.
- `finished`: `median_hours` for each project, from `finished_sql`.

A real-database test proves each copied value equals the analytics route's
value for the same scope and period (R8).

**The visual for each section:**
- `finished`: `FinishedPanel`, a bar for each project.
- `throughput`: `ThroughputPanel`, weekly completions as bars, with the
  median and p90.
- `load`: `LoadPanel`, a stacked bar for each person: overdue, due in the
  next 7 days, and later.
- `capacity`: `CapacityPanel`, with a new progress bar for each person:
  committed hours of working hours in the horizon. It shows only with the HR
  half and `hours_basis`. The text states "X of Y h", because a bar clips
  above 100 percent.
- `stuck`: `StuckPanel`, overdue work for each project. The report body has
  no untouched bands, so the panel draws no band chart and no band legend for
  a report. It must not say "No open work in this scope". The bands wait for
  R3. (R3a added them.)
- `conflicts`: `ConflictsPanel`, the rows with the kind label and the
  severity dot, and the count line for each kind.

**Summary tiles.** Four tiles above the sections. Each takes one figure from
one section, and it hides when its section is not in the config.
- Finished: `finished.total_completed`.
- Open: `load.total_tasks`, or `capacity.total_tasks` when `load` is absent.
- Overdue: `stuck.overdue_total`. Never a sum of the rows for each person,
  because a shared task counts in each row.
- Median cycle time: `throughput.median_hours`. A null shows "not measured".

**The table stays** under each panel, in a `CollapsibleSection` from
`src/components/ui/Collapsible.tsx`, closed by default. The primitive gains a
`keepMounted` pass-through, so a closed table stays in the page.

**The email and the download** (`reportLayout` in `src/lib/reportEmail.ts`,
which `reportFiles.ts` also reads) draw a text bar with U+2588 and U+2591.
Each bar prints two server figures and computes neither:
- `finished`: each project, "completed of total_completed".
- `stuck`: each project, "overdue of overdue_total".
- `load`: each person, "overdue of open_tasks". Not "of total_tasks",
  because the rows for each person add up to more than the total.
- `capacity`: "committed of working h", only with the HR half and
  `hours_basis`.
- `throughput`: one bar for each week, scaled to the peak week, with no "of".

**Rules for the build:**
- **One chart seam.** A report section and an Analytics panel draw the same
  data with the same component. A pure adapter in `lib/reportPanels.ts` maps a
  section to its panel type. When a panel cannot take a report's shape, change
  the panel so that an absent field draws nothing. A second chart component
  for the same data is a defect (CLAUDE.md §4).
- **One tile.** `AnalyticsView.tsx` and `NodeDashboard.tsx` each hold a
  private `Stat`. Export one `Stat` from `AnalyticsPanels.tsx`, from the
  NodeDashboard copy, and use it in all three places. `Bar` stays private.
- **Colour follows the panels.** State uses `src/lib/statusAccent.ts`, and
  an ordinal scale uses `accentForHue`, as the panels do now. R2b adds no
  categorical series, so it adds no `--cat-*` colour. Each colour has a text
  label beside it.
- **An accessible name.** `Panel` gains `aria-labelledby` on its title. Each
  bar states its value in text, not only in a tooltip.
- **One heading.** `Panel` already carries a title, so the report drops its
  own section heading where a panel draws.
- **Keep the lockstep text.** `test_projects_report_sections_lockstep.py`
  needs the literal `sections.<name>` in `ReportsView.tsx`. Keep each access
  literal. Do not loop over a map.
- **The Analytics app does not change** except for the guards, the
  accessible name, the shared `Stat` and the capacity bar.

**Done when:**
- Each of the six sections renders its panel in the in-app report, and its
  table under the panel.
- **Agree:** a test gives each section a unique sentinel figure. The figure
  appears in the panel and in the table.
- **Degrade:** with report-shaped data, the markup contains no "undefined",
  no "NaN" and no "No open work in this scope".
- **A tile hides** when its section is not in the config.
- **The fence**, in `src/app/projects/components/reportVisuals.test.ts`:
  - (a) The render test above, with all six sections.
  - (b) A source test. `ReportsView.tsx` imports the six panels from
    `./AnalyticsPanels` and renders each one. It has no inline width or
    height style and no function named for a bar or a chart. A self-test
    proves that a hand-drawn bar string fires the fence.
  - (c) Unit tests for the adapters in `lib/reportPanels.ts`, with the
    arithmetic check of `capacity.test.ts` applied to that module.
- **The pass-through test** runs on a real database, as above.
- `reportEmail.test.ts` shows the text bars of the list above, and the colour
  checks stay green.
- The Analytics app and the node dashboard render as before. Their tests pass
  without change, except the tests for the four changes above.
- The visual review passes in light mode, compact density, a changed accent
  and at 390 px, on the report and on the Analytics app.

**From R2b on, a section ships with its visual.** R3 and every later slice add
the visual for each new section in the same PR. R3 also adds the untouched
bands to `stuck`.

### R3 — Four new sections, in four slices · AGENT-SAFE

Each section is one server read, one visual and one entry in each lockstep
list. Each is opt-in, and none goes into `DEFAULT_SECTIONS`. The
spec-auditor split R3 into four slices on 2026-09-24. Each slice is one PR.

**The lockstep list.** A section name lives in seven places, plus the chat
card map. A slice adds its name to each of them in the same PR:

1. `reports.py` `SECTIONS` (`:106`).
2. `skill_projects/reads.py` `_REPORT_SECTIONS` (`:1224`). This is the
   chat's list, not `views.py`.
3. `skill_projects/writes.py` `REPORT_SECTIONS` (`:854`).
4. `ReportsView.tsx` `RenderedBody` (`:210`).
5. `src/lib/reportEmail.ts`.
6. `lib/reportBuilder.ts` `REPORT_SECTIONS` (`:34`).
7. `lib/api.ts` `RenderedReportBody` (`:786`).

The chat card map is `skill_projects/views.py` `REPORT_CARD_SECTIONS`
(`:317`). `test_projects_report_sections_lockstep.py` holds the seven lists.
`test_projects_agent.py` holds each card title to `ReportsView.tsx`.

**The declared order of `SECTIONS`:** `finished`, `throughput`, `outlook`,
`load`, `capacity`, `pulse`, `stuck`, `hygiene`, `conflicts`, `rebalance`.
Each slice adds its name in that place. A report renders its sections in
this order, whatever order a member chose.

| Section | What it returns | It calls | Slice |
|---|---|---|---|
| `outlook` | The forecast for the report's scope | `analytics.py` `outlook_body` | R3a |
| `rebalance` | The rebalance route's body, capped like `load` | `analytics_rebalance.py` | R3b |
| `hygiene` | Open tasks with no assignee, no due date or no estimate, and tasks in progress with no activity. Counts, and up to 20 rows of each kind. | new SQL over `load_open_where` | R3c |
| `pulse` | One row for each person in scope: pill, flags, reasons, `needs_help`, `help_reasons`, on leave, today's focus (up to 5 tasks), stale count, blocked count, waiting count | `person_capacity`, `classify`, the `stuck` predicates, `pm_task_personal` | R3d |

**Done when, for each slice:**
- The section renders in the app, in the email body with no colour, and in
  the chat card.
- The section draws its visual, under the R2b rules.
- Each new query runs against a real database (R8).
- The visual review passes in light mode, compact density, a changed accent
  and at 390 px.

#### R3a — `outlook`, the ageing bands, and T5 · BUILT 2026-09-25

**What:**
- `outlook_body` (`analytics.py:1474`) holds the body of
  `GET /projects/analytics/outlook`. The route and the report section both
  call it, so the two are one computation.
- The `outlook` section covers the report's scope only. It reads
  `FORECAST_WEEKS` of history, not `config.weeks`. The route clamps a
  `weeks` of 1 to 2, so the report period would change the forecast. A
  forecast for each child project moves to the T10 slice.
- `OutlookPanel` draws a range bar from the planned finish to the forecast
  finish, with the slip in text. With no forecast date, it draws no bar and
  shows the verdict line. The Analytics app and the node dashboard draw the
  same panel.
- `stuck` carries the route's ageing bands as `stale: [{band, n}]`. The
  render's open-work predicate is the `stuck` route's, and the bands come
  from `stale_bands_sql`. The panel draws its band chart in a report.
- T5 `project_status` is live. Its sections are `finished`, `outlook`,
  `stuck` and `conflicts`, for one week with the current week kept.

**As built:** `test_projects_report_sections_r3.py` proves one computation on
a real database. The section and the route body are equal for one scope and
one caller. A report with `weeks` 1 samples `FORECAST_WEEKS`. Each band
equals `/analytics/stuck`. `reportVisuals.test.ts` draws seven panels, and
`outlook.test.ts` places the bar.

**H-169 stays open.** The outlook's capacity figure ignores absences. The
section calls `outlook_body`, so the fix reaches the report with no change
here.

#### R3b — `rebalance` · AGENT-SAFE · BUILT 2026-09-25

**What:**
- `render_body` awaits `rebalance_body` (`analytics_rebalance.py`), as the
  route does. The HR tier is the reader's grant, `can_read_hr_fields`. The
  section keeps the route's own 14-day horizon, not `config.weeks`.
- The section is a copy of the body. `at_risk` stays as the join serves it,
  capped at eight tasks. The render cuts `pickups` to `MAX_PEOPLE` (20),
  and `pickups_total` counts every idle person with a match.
- `rebalance` is opt-in and comes last in `SECTIONS`. No template goes live.
  T1 now waits for the pulse section and a today period only.
- `RebalancePanel` in `AnalyticsPanels.tsx` draws each task at risk, with its
  holder and up to three helpers. The idle people follow, then a line that
  names what the caps cut. `lib/rebalance.ts` holds the words.
- The email and the download print one line for each task and one line for
  each idle person. They carry no colour and no bar.
- The chat card shows the at-risk count, the idle count and one row for each
  task. `_dig` reads a numeric path part as a list index. The chat text uses
  plain labels and never prints a task id.

**Non-goals:** Assign, Dismiss and task links (slice R4b). The panel in the
Analytics app. A live template. A row filter under E5 or §7.1.

**HR gate:** without `admin:members:read`, the body has no `at_risk` and no
`pickups` key. The panel, the email and the card then show one line:
"Rebalancing needs HR read access. An admin can see it." §7.1 rule 7
records why R3b adds no row filter.

**Done when:**
- (a) On a real database, the section equals `rebalance_body` for one scope
  and one reader, apart from the pickups cap.
- (b) A reader without `admin:members:read` gets `hr_visible: false` and no
  lists. The panel, the email and the card show the HR line and no zero.
- (c) `pickups` holds 20 rows at most, and `pickups_total` counts them all.
- (d) `rebalance` is opt-in and last. `test_projects_report_sections_r3.py`
  passes with no change.
- (e) `reportVisuals.test.ts` draws eight panels, and fence (b) names
  `RebalancePanel`.
- (f) The lockstep test and the card title test pass with the new name.
- (g) Three tests close H-185. The card cuts a timestamp to its date. The
  outlook uses plain chat labels and says "Slip days" once. A test pins the
  forecast as today plus whole weeks.
- A task that two people hold counts once in `at_risk` and in
  `at_risk_total`.

**As built:** `test_projects_report_sections_r3b.py` seeds one task with two
holders and 23 idle people on a real database. It proves (a) to (d) and (g).

#### R3c — `hygiene` and T13 · AGENT-SAFE

**What:**
- `hygiene_body` in `analytics.py` returns the section. It reads open work
  through `load_open_where` and `load_params`, so hygiene and `load` agree
  about "open".
- The section counts four kinds of open task:
  - `no_assignee`: no row in `pm_task_assignees`. An `agent:<name>`
    assignee counts as an assignee.
  - `no_due_date`: `due_at IS NULL`.
  - `no_estimate`: `estimate_mins IS NULL`.
  - `stale_in_progress`: category `in_progress`, and
    `updated_at <= now() - make_interval(days => :stale_days)`.
- `STALE_DAYS = 14` is one named constant in `analytics.py`. It is the lower
  bound of the `days_14_to_30` band in `STALE_BANDS`.
- A task counts in each kind that it breaks. So the kinds do not add up to
  `open_total`, and the panel says so.
- Subtasks count, as they count in `load`. Triage, archived tasks, closed
  tasks and the work of a stopped project do not count (D-PM-32).
- The section never reads `pm_task_personal`. A private estimate of one
  member does not fill the shared field (D53, owner Q6).
- The body is `open_total`, `stale_days`, `by_kind` and `rows`. `rows` holds
  up to `MAX_NAMED` (20) tasks of each kind, and `by_kind` counts them all.
- `HygienePanel` in `AnalyticsPanels.tsx` draws one bar for each kind,
  "n of open_total". Up to five titles follow each bar, then "…and M more".
  It uses the private `Bar`.
- `lib/reportPanels.ts` gains `hygienePanelData`. The table folds under the
  panel, as R2b sets.
- The email and the download print one text bar for each kind,
  "n of open_total", then the rows up to `maxRows`.
- The chat card shows the four counts and one row for each task.
- T13 `data_hygiene` goes live with sections `hygiene`, `weeks` 1 and
  `skip_current_week` false. Its scopes are `project` and `org`. The section
  ignores the period, because it reads the state now.
- T7 stays ◐. Its `waits_for` changes to "A today period, and a rule for
  which hygiene rows are high".

**Non-goals:** a `/analytics/hygiene` route, a panel in the Analytics app, a
manifest row, a summary tile, and T7. The route is a later slice. The body
function makes it one route plus one manifest row.

**Done when:**
- (a) On a real database, the section equals `hygiene_body` for one scope and
  one reader.
- (b) A seeded task shows in each of the four kinds, and a task with only an
  `agent:` assignee is not in `no_assignee`.
- (c) Triage, archived, closed tasks and tasks of a stopped project are not in
  any kind.
- (d) A task in progress with `updated_at` 15 days old is stale. One with 13
  days is not.
- (e) A task with `estimate_mins` NULL and a
  `pm_task_personal.time_estimate_mins` value is in `no_estimate`. A source
  test proves that the SQL does not name `pm_task_personal`.
- (f) A task that the reader cannot see is in no count.
- (g) 23 undated tasks give 20 rows and `by_kind.no_due_date` 23.
- (h) `hygiene` sits between `stuck` and `conflicts` in `SECTIONS`, and it is
  opt-in. `test_projects_report_sections_r3.py` passes with no change.
- (i) T13 is live. The live list in the lockstep and template tests is
  `weekly_delivery`, `project_status`, `data_hygiene`.
- (j) `reportVisuals.test.ts` draws nine panels, and fence (b) names
  `HygienePanel`.
- (k) The lockstep test and the card title test pass with the new name.
- (l) The H-186 items pass their tests, and the slice deletes the H-186
  entry.

#### R3d — `pulse` and T1 · waits for spec edits E2 to E4

**What:** the `pulse` section. It draws one card for each person: a load
bar, the pill, the top focus tasks and a "needs help" mark. T1 `team_pulse`
goes live.

**Owner decisions (2026-09-25):**
- **Private notes stay on the reader's own row.** A report shows the
  `pm_task_personal` fields `waiting_on`, `expected_by` and `scheduled_start`
  only on the row of the person who wrote them, and only to that person. The
  cards of other people use shared task data only. D53.7 and D53.8 stay
  intact.
- **Before R5, an admin sees every card, and a reader who is not an admin
  sees only their own card.** The report then says "This report hides N
  other people". This rule is edit E5, and it is a strict subset of §7.1, so
  it can never show more than R5 allows.

**Open:**
- Spec edits E2 to E4 must land first.
- `test_the_template_fence_fires_on_a_missing_section` uses `pulse` as a
  fake missing name. R3d must give that self-test another name.

**Done when:**
- `pulse` gives the same pill as the capacity route for the same person and
  window. One test pins this, so the two cannot drift.
- A person on leave today shows "on leave" and no `idle` pill.

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

### R4b — Act on a suggestion · AGENT-SAFE

**What:** the controls of §6.4 item 4 on each rebalance row. **Assign** asks
the member to confirm, then sets the helper as an assignee. **Dismiss** hides
the suggestion for this report until the facts change. Each task title links
to its task.

**Rules:**
- No shared confirm card exists today. R4b builds one, and the chat and the
  task panel use it too.
- Dismiss stores its state beside the stored runs of R4, so R4b comes after
  R4.
- Assign goes through the ordinary assignees route. It adds no second write
  path.

**Done when:** an Assign on a row changes the task's assignees only after the
member confirms. A dismissed suggestion stays hidden on the next run of the
report, and it comes back when the facts change.

### R5 — Person and team scope · AGENT-SAFE

**What:** `config.subject` with `kind` `person` or `team`, and an address or
an `org_group` slug. The sections filter their task half to that subject's
assignments. `pm_reports.project_id` stays the node scope, so the two combine:
"the Hardware team, in the Printer project".

**Rules:**
- The subject is validated against the directory, as recipients are. Free
  text gets 422.
- A team expands to its members at render time, not at save time. A new
  member joins the report by joining the team.
- `may_report_on` (§7.1) gates the save, the render and the picker. The
  org setting `reports.members_see_own_team` lands in this slice, default off.

**Done when:**
- A report on one person shows only that person's rows. A report on a team
  shows each member once. A person outside the directory gets 422.
- A member without a grant gets 403 for a report on a colleague, and 200 for
  a report on themselves.
- A lead gets 200 for their own team and for each member of it, and 403 for
  another team.
- An admin gets 200 for every person and team.
- On a report of the whole organization, a member sees their own row and the
  line "This report hides N other people". One test per role pins this.

### R6 — The AI summary, on request · AGENT-SAFE

**What:** a **Summarize with AI** control on a rendered report. When a person
presses it, an LLM writes two or three sentences from the render body. The
body is the only input. Each figure in the text must match a figure in the
body. A check compares them and drops the summary if one does not match. The
fallback is the fixed sentence of §6.4.

**Rules:**
- **On request only** (owner, 2026-09-24). No render, no schedule and no
  page load calls the LLM. A person presses the control, or asks the chat.
- **Anybody with AI credits may use it** (owner, 2026-09-24). The call goes
  through the Router, like every other AI call, and spends the credits of the
  person who asked. With no credits, the control says why and sends nothing.
  It adds no second credit check. It reads the one that the chat uses.
- The prompt fences task titles as data, as the chat does
  (`projects_ai_chat.md` §13.5, as built).
- A stored run (R4) keeps the summary beside the body, so a second reader
  sees it without a second charge.

**Done when:**
- A summary with an invented figure is dropped by the check. A test proves
  this with a fake model that returns a wrong number.
- A render with no request makes zero LLM calls. A test counts them.
- A person with no credits gets a refusal that names the reason, and the
  Router records no call.

⚠️ **H-152 applies.** Today a self-serve customer can never be served AI.
Until H-152 closes, those customers see the refusal.

### R8 — The chat: list, create, render and email · AGENT-SAFE

**What:** the controls of §6.3, and four chat tools over the report routes.

| Tool | Route | Class |
|---|---|---|
| `report_list` | `GET /projects/reports` | A — exists |
| `report_save` | `POST` and `PATCH /projects/reports` | B — exists. It gains `template` and `subject`. |
| `render_report` | `GET /projects/reports/{id}/render`, or the preview of R1 | A — exists. It gains a template name and a subject, so "the morning report for Design" works without a saved report. R8 maps `POST /projects/reports/preview` to this tool and changes that row from class X to class A. |
| `summarize_report` | the R6 route | B — it spends credits, so a card shows the cost first |
| `send_report` | the R9 route | C — a guarded act, behind a confirm card |

**Rules:**
- The chat obeys `may_report_on` (§7.1). It never renders a report that the
  member cannot open.
- `send_report` changes D-PM-37's map. Today the manifest excludes every
  delivery route with `_DELIVERY_REASON`, "the chat renders a report, it
  never sends one" (`skill_projects/manifest.py:79`). The owner changed this
  on 2026-09-24: the chat may email a report to one person on request. The
  recipient, recipients-list and schedule routes stay class X. Only the R9
  route becomes class C. Update `projects_ai_chat.md` in the same PR.

**Done when:**
- Each control opens the builder with the scope filled in.
- The chat renders T1 for a team by name.
- The chat emails a report only after the member confirms the card.
  `test_projects_chat_coverage.py` passes with the new map.

### R9 — Email this report, on request · AGENT-SAFE to build, OWNER-GATE to arm

**What:** `POST /projects/reports/{id}/send` with one recipient. It renders
the report once, with the **recipient's** visibility (H-111), and sends it
through `sendReportEmail` (`src/lib/reportEmail.ts`). The Reports page (§6.4
item 6) and the chat's `send_report` both call it.

**Rules:**
- **The recipient is a person in the directory**, never free text. This is
  the H-111 rule, and it stops an open mail relay from our one sender.
- **The sender must also pass `may_report_on`** for the report's subject.
  A member cannot email a report that they cannot open.
- **`delivery_armed()` is the lock.** `PROJECT_REPORT_EMAIL_ENABLED` stays
  the owner's to turn on, because the send reaches a real person (CLAUDE.md
  §3a rule 3). While it is off, the route returns 409 with a clear reason.
  It never reports a send that did not happen.
- A send writes a stored run (R4) for the recipient, so "Open in Metorite"
  opens exactly what the email said.
- Rate limit: 20 sends for each member in each hour. A chat loop cannot mail
  the directory.

**Done when:**
- With the flag off, the route returns 409 and a fake sender sees no call.
- With the flag on and a fake sender, one call sends one email, and the body
  uses the recipient's visibility. One test shows a task that the sender can
  see and the recipient cannot, and the email leaves it out.
- An address outside the directory gets 422.

### Order

**Phase 1, on request:** R1 → R2 → R2b → R3 (R3a → R3b → R3c → R3d) → R5 →
R4 → R4b → R6 → R8 → R9.

- R1 and R2 give a member control of what exists.
- R2b draws each section as a chart or a progress bar.
- R3 makes the morning report real.
- R5 adds the person and team scope and the permission rule.
- R4 gives a report a memory, and R4b lets a member act on a suggestion.
- R6 adds the AI summary.
- R8 and R9 let the chat and the member send a report to one person.

---

## 8a. Later phases — recorded, not scheduled

### Phase 2 — R7, a report that renders and emails itself · OWNER-GATE to arm

**What:** a schedule on a saved report: a recipient, a day pattern and a
local time. `SCHEDULES` gains `weekday_daily` next to `weekly`. The job is
the one that H-111 names as missing. It copies the claim pattern of
`routes/workflows/scheduler.py`, which claims `last_sent_at` with
compare-and-set, so two workers cannot send twice. For each recipient, it
calls the R9 send path. It adds no second path.

**Rules:**
- The job runs dark, behind `delivery_armed()`.
- The recipient's timezone decides the hour. The default is 09:00 on
  weekdays.
- The job never calls the AI summary by itself. A schedule may carry an AI
  summary only after the owner decides who pays for an unattended AI call.
- A failed send is logged with the report id and the recipient, and it does
  not advance `last_sent_at`.

**Done when:** with the flag off, a due schedule writes one run for each
recipient and sends nothing. With a fake sender and the flag on, it sends
once, even when two workers claim the same report.

### Phase 3 — R10, reports as a step in other apps

**What:** a Workflows step "Render a report" and a step "Email a report".
Each step calls the one render function and the R9 send path, as the
workflow's owner. A step passes `may_report_on` for that owner.

**The seam that makes this cheap:** R1's done-when holds the rule. The render
lives in one server function, `render_body`, and the route, the chat, the
schedule and a workflow all call it. R1's preview route is the first proof
that the render does not need a saved row.

---

## 9. Owner answers (2026-09-24)

| # | Question | Answer |
|---|---|---|
| Q1 | Who may open a report about one person? | The reader's own role decides. An admin reports on everybody, a member on themselves, and a lead also on their team. §7.1 holds the rule. |
| Q2 | Which channel comes first? | In-app, on request. A member or the chat can email one render to one person (R9). A timer comes later (Phase 2). Other apps come after that (Phase 3). |
| Q3 | Do we add time tracking? | No. |
| Q4 | Who may use the AI summary? | Anybody with AI credits, and only when they ask for it (R6). |
| Q5 | When does the morning report send? | Nothing sends by itself in Phase 1. Phase 2 uses 09:00 on weekdays, in the recipient's timezone, as its default. |
| Q6 (2026-09-25) | May a report show one member's private notes (`waiting_on`, `expected_by`, `scheduled_start`) to another reader? | No. The notes show only on the reader's own row (R3d). |
| Q7 (2026-09-25) | Before R5, who sees the cards for each person? | An admin sees every card. Any other reader sees only their own card, and a line counts the hidden people (R3d, edit E5). |

**Answered before this spec:** whose view a sent report uses. The send renders
once for each recipient with that recipient's visibility (H-111, 2026-09-17).

**Still the owner's:** turning on `PROJECT_REPORT_EMAIL_ENABLED` (H-111). R9 is
built dark until then.

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
  tests/unit/test_projects_agent.py \
  tests/unit/test_projects_report_builder.py \
  tests/unit/test_projects_report_templates.py \
  tests/unit/test_projects_report_visuals.py \
  tests/unit/test_projects_report_sections_r3.py \
  tests/unit/test_projects_report_sections_r3b.py \
  tests/unit/test_projects_report_sections_r3c.py \
  tests/unit/test_projects_analytics_outlook.py \
  tests/unit/test_tenant_coverage.py -q
```

`test_projects_agent.py` holds the class-A reach fence. A manifest row that
names a built tool which never calls the route fails there.

`test_projects_report_builder.py` is R1's file.
`test_projects_report_templates.py` is R2's file.
`test_projects_report_sections_r3.py` is R3a's file.
`test_projects_report_sections_r3b.py` is R3b's file.
`test_projects_report_sections_r3c.py` is R3c's file.

Client, in `workbench/control_plane`:

```bash
npx tsc --noEmit && npx vitest run src/app/projects src/lib/reportEmail.test.ts src/lib/theme src/lib/sourceHygiene.test.ts src/components/genUITemplates.test.ts
```

Each slice adds its own test file and names it in its PR. A UI slice also runs
the `visual-review` skill: light mode, compact density, a changed accent, and
the phone width.
