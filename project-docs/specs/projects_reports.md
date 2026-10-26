# Projects · the reporting system — WS-27bn

**Status: ACTIVE. R1 BUILT 2026-09-24** (the builder, `render_body` and
`POST /projects/reports/preview`). **R2 BUILT 2026-09-24** (the template
catalogue, `config.template`, the gallery and "Your reports"). **R2b BUILT
2026-09-24** (the visual report body: each section draws its Analytics panel,
with its table folded under it). **R3a BUILT 2026-09-25** (the `outlook`
section, the ageing bands in `stuck`, and T5 live).

**R3b BUILT 2026-09-25** (the `rebalance` section and `RebalancePanel`, read
only). **R3c BUILT 2026-09-26** (the `hygiene` section, `HygienePanel`, and
T13 live). **R3d BUILT 2026-09-28** (the `pulse` section, `PulsePanel`,
and T1 live).

**R5a BUILT 2026-09-28** (the role rule of §7.1 and `config.subject`, on the
server, in `report_scope.py`). **R5d BUILT 2026-09-28** (the role rule in the
chat's dataset read, and the rule for a delete or a change, §9 Q10 to Q13).

**R5b-1 BUILT 2026-09-29** (the subject chip in the builder, T2 and T6 live,
T1 takes a team, and the link contract). Repair round 1 on 2026-09-29 keeps a
T2 about its author on an edit. **R5b-2 BUILT 2026-09-29** (the entry
points "Report on this", "1:1 prep" and "My day"). **The UX pass BUILT
2026-09-29** (§6.5: the builder beside its preview, the header, the gallery,
the rail and Delete in edit mode). R5c is blocked. R4, R4b, R6 to R9 and
Phases 2 and 3 are not built.

**R5f BUILT 2026-09-30** (one Reports app: the Analytics app joins Reports,
and Overview is the landing screen, §9 Q14). **R5f repair round 1 BUILT
2026-09-30** (the pass-through fields and the builder redesign of §6.6).

**R5f repair round 2 BUILT 2026-09-30.** It gives each section one name,
makes each tile one line, and puts the HR grant into plain words. The email
now matches the app.

**CLOSE-OUT 2026-09-30 (§9 Q15).** The owner asked to conclude the feature
with only what is necessary. The last slice was **R-final**, in §8, the
chat render by name. R4, R4b and R6 are CUT. R8's send and R9 move to
Phase 2 (§9 Q16). R5c stays blocked and leaves this feature.

**R-final BUILT 2026-09-30** (the chat renders a report by name, for example
"team pulse for Design"). **Phase 1 is COMPLETE.** Phases 2 and 3 are not
built.

**R5g BUILT 2026-09-30** (the live Overview, full page, §6.7 and §9 Q17).
Overview draws its panels across the page. One toolbar holds the choices
behind "Filters", and Overview refreshes itself while the tab is visible.

**RA-1 BUILT 2026-10-25, not merged** (§6.8 F, §8 RA-1). One rule in
`analytics.py` labels each done task and each open task's live holder. The
Throughput and Finished reads carry `actor_split`, and the Load read carries
`holder_split`. No panel changes.

**RA-3 BUILT 2026-10-25, not merged** (§6.8 C, §8 RA-3). The `waiting`
section lists agent questions with no answer, agent work that waits for a
review, and blocked handoffs. It gives the answer time too. Overview shows
the panel by default, and hides it when the scope has no agent run.

**RA-2 BUILT 2026-10-25, not merged** (§6.8 B, §8 RA-2). The live Overview
draws a KPI strip of six tiles, and each tile carries its rule as its
tooltip. "Who did the work" replaces "What we finished" in the Overview of
one project. "How long it took" splits its median and p90 by people and
agents.

**RA-4 BUILT 2026-10-25, not merged** (§6.8 B, §8 RA-4). The `agent_runs`
section gives 14 days of run outcomes, the success rate and its trend, the
silent runs now and the run sizes. It flags a long run. Overview shows the
panel by default, and hides it when the scope has no agent run.

**RA-4b BUILT 2026-10-26, not merged** (§5 "Agent time", "Success rate" and "Run outcome"). Four follow-ups from the RA-4 review. A reopened run's idle gap is no agent time. A server close is no agent success, and it is its own outcome. With agent runs off, no section says clear. The chat prints the success rate as a percent.

**RA-6 BUILT 2026-10-25, not merged** (§6.8 B and F, §8 RA-6). The Forecast
runs the past weeks again 2,000 times and gives the 50% and 85% dates, with
no estimate. When work arrives as fast as it finishes, "Not converging"
says the finishes a week that would converge it. "Needs an owner" shows
first in Open work when it leads. Fix round 1 BUILT 2026-10-26. RA-7 and
RA-8 are not built.

**RA-5 BUILT 2026-10-26, not merged** (§6.8 B and F, §8 RA-5). The `quality`
section shows the agent pull request outcomes, with the merge rate and its
trend. It shows the tasks reopened after done, for agents and for people. It
shows the failed runs that got a new run, and the agent pull requests
with failing checks. Overview shows the panel by default. Overview hides it
when the scope has no agent pull request, no reopen and no failed run.

Written
2026-09-24. The R3 lockstep anchors were verified against the code on 2026-09-26. The owner answered
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
- `projects_ai_chat.md` §17 (S11). It owns the outlook's capacity figure.

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
| Open work for each person | `GET /projects/analytics/load` · `analytics.py:600`, body `load_body` · `:646` | `load` |
| Work that nobody touched, blocked work, overdue work | `GET /projects/analytics/stuck` · `analytics.py:235` | `stuck` (the bands since R3a) |
| Hours, absences, spare hours, the pill | `GET /projects/analytics/capacity` · `analytics_capacity.py:207`, body `capacity_body` · `:241` | `capacity` (opt-in) |
| Order, overlap and people conflicts | `GET /projects/analytics/conflicts` · `analytics_conflicts.py:179`, body `conflicts_body` · `:381` | `conflicts` (opt-in) |
| Helpers for at-risk work, work for idle people | `GET /projects/analytics/rebalance` · `analytics_rebalance.py:104`, body `rebalance_body` · `:128` | `rebalance` (opt-in, R3b) |
| The forecast | `GET /projects/analytics/outlook` · `analytics.py:1660`, body `outlook_body` · `:1710` | `outlook` (opt-in, R3a) |
| Who fits one task | `GET /projects/tasks/{id}/candidates` · `candidates.py` | none |

**The report routes** are in `routes/projects/reports.py`:
- `SECTIONS` (`:124`) is the vocabulary, in a fixed order. `DEFAULT_SECTIONS`
  (`:136`) is the four that a definition with no `sections` key renders.
- `_DEFAULTS` (`:142`): one week, the current week skipped, the subtree
  included.
- `TEMPLATES` (`:184`) is the template catalogue of §4 (R2).
- `SCHEDULES` (`:284`) holds only `"weekly"`.
- `delivery_armed()` (`:287`) reads `PROJECT_REPORT_EMAIL_ENABLED`.
- The routes: list is at `:567`, templates at `:615` and subjects at
  `:632` (R5a). Create is at `:652`. Get, patch and delete are at `:689` to
  `:739`. Render is at `:1094` and preview at `:1130`. Recipients are at
  `:1220` to `:1302`, and schedule is at `:1320`. These anchors were
  measured again on 2026-09-28, after the R5d change rule.
- `render_body` (`:753`) holds the section loop. Render and preview both
  call it.
- **Since R5a:** `routes/projects/report_scope.py` holds the rule of §7.1,
  the subject clause and the row filter.
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
  `RenderedBody` (`:273`). The create control is "New report" (`:1806`).
- `components/AnalyticsView.tsx` and `components/NodeDashboard.tsx`. They draw
  the same panels from `AnalyticsPanels.tsx`, for the portfolio and for one
  node.
- `lib/api.ts:1613` to `:1653`. The client can list, create, patch, preview,
  render and delete a report, and read the templates and the subjects. It has
  no call for recipients or schedule.
- **Since R1:** `ReportsView.tsx` has the builder (`ReportBuilder`, `:829`).
  `lib/reportBuilder.ts` holds the builder's choices as pure functions.
- **Since R2:** `ReportsView.tsx` has the home screen (`ReportsHome`, `:1406`)
  and the template card (`TemplateCard`, `:1352`). These anchors were
  measured again on 2026-09-30, after R5f.
- **Since R5b-1:** the builder has the subject chip. `lib/reportBuilder.ts`
  holds `subjectOptions`, `reportLink` and `parseReportLink`.
- **Since R5b-2:** `lib/reportEntry.ts` holds the three entry points. It
  decides the parts of each link, and `reportLink` builds the link.
- `src/lib/reportEmail.ts`. It builds an email body with no colour.
  `sendReportEmail` throws while the flag is off.
- Analytics and Reports are `live` in `lib/projectApps.ts`.
- **Since R5f:** Reports is `live`, and the Analytics app is gone. Its
  Overview shows what Analytics showed (§9 Q14).

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

**Status key:** ✅ live (T4 since R2, T5 since R3a, T13 since R3c, T1 since R3d, T2 and T6 since R5b). ◐ waits
for a section, a scope, a period or a filter that §8 adds. The gallery shows
a ◐ template as "coming soon".

| # | Key | Template | The question | Scope | Period | Sections | Status |
|---|---|---|---|---|---|---|---|
| T1 | `team_pulse` | **Team pulse** (the morning report) | How is each person on the team today, and who needs help? | team · project · org | today | `pulse`, `conflicts`, `rebalance` | ✅ (since R3d) |
| T2 | `my_day` | **My day** | What do I work on today, and what waits on me? | person (the reader) | today | `pulse` (its waiting items show on the reader's own row, Q6) | ✅ (since R5b) |
| T3 | `what_changed` | **What changed** | What happened since I last read this? | any | since the last run | `changes` | ◐ |
| T4 | `weekly_delivery` | **Weekly delivery** | What did we finish last week, and how fast? | project · org | last week | `finished`, `throughput`, `load`, `stuck` | ✅ (the default today) |
| T5 | `project_status` | **Project status** | Will this project finish on time, and what blocks it? | project | this week | `finished`, `outlook`, `stuck`, `conflicts` | ✅ (since R3a) |
| T6 | `one_on_one` | **1:1 prep** | How is this person doing over a month? | person | last 4 weeks | `finished`, `throughput`, `pulse` (its waiting items show on the reader's own row, Q6) | ✅ (since R5b, §7.1 limits who may open it) |
| T7 | `exceptions` | **Exceptions** | What is wrong right now, and nothing else? | any | today | `stuck`, `conflicts`, `hygiene` (only the high rows) | ◐ |
| T8 | `capacity_outlook` | **Capacity outlook** | Do we have the people for the next weeks? | team · org | next 2 to 6 weeks | `capacity` (it carries absences), `outlook` | ◐ |
| T9 | `stakeholder_update` | **Stakeholder update** | A short project summary to send outside the team | project | last 2 weeks | `finished`, `outlook`, with no per-person rows | ◐ |
| T10 | `portfolio_health` | **Portfolio health** | Which projects are healthy? | org | this month | `outlook` for each child project, `capacity` | ◐ |
| T11 | `focus_switching` | **Focus and switching** | Who is spread over too many projects? | team · org | this week | `conflicts` (`parallel_person` only), `load` | ◐ (it waits for a conflict-kind filter) |
| T12 | `retrospective` | **Retrospective** | What slipped in the period, and why? | project | a closed period | `finished`, `throughput`, `changes` (slips only) | ◐ |
| T13 | `data_hygiene` | **Data hygiene** | Which tasks make every other report wrong? | project · org | today | `hygiene` | ✅ (since R3c) |

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
| **Waiting** | A `pm_task_personal` row of the reader whose effective disposition is `WAITING` (`personal.effective_disposition`). It is past its date when `expected_by` is before today, or `due_at` when `expected_by` is null. This is the rule of My Tasks, `isWaitingOverdue` in `app/tasks/lib/waiting.ts`. | `analytics_pulse.py` `is_waiting` and `_WAITING_LINE` (R3d) |
| **Needs help** | NEW. A person with one or more of: a blocked task, a stale task in progress, a waiting item past its date, or the `behind` pill two runs in a row | The `pulse` section (R3) |
| **Has room** | The `idle` pill, or spare hours above a threshold in the window, and not on leave | `capacity` — exists |
| **Today's focus** | Tasks in progress, plus tasks due today or tomorrow, plus tasks that the person scheduled today (`scheduled_start`) | The `pulse` section (R3) |
| **Agent-finished** | A done task whose finish came from an agent: the activity carries `meta.via` = `mcp:…`, or the actor is `agent:<name>`. `system:workflow:<id>` is not an agent. The finish is the finish the window counts. | `analytics.py` `classify_finish` and `finish_label_sql` (RA-1) |
| **Agent-assisted** | A done task that an agent worked before its finish, and that a person, a workflow or GitHub finished. "Worked" is the rule of `agent_worked.AGENT_WORKED_SQL`: a run of any status, a queued run too, or an act through MCP, made before the finish. The finish is the finish the window counts. | `analytics.py` `classify_finish` and `finish_label_sql` (RA-1) |
| **Waiting on a person** | One of three things on an open task. A run in `awaiting` with an open question, which is an elicitation that no prompt answers. Agent work that waits for a review. A handoff blocker. | `analytics_waiting.py` `waiting_body` (RA-3) |
| **Waits for a review** | An agent moved the task into a review lane, or the task has an open pull request (not a draft) and an agent worked it. No person act came after that move or that pull request. | `analytics_waiting.py` `review_since` and `reviews_sql` (RA-3) |
| **Review lane** | A status of the `in_progress` category with a word of its name that starts with "review", with no regard to case. "In review" and "Reviewing" count, and "Preview" does not. The match knows English only, so a lane named in another language does not count. The MCP protocol tells an agent to move its task there when the pull request opens. | `analytics_waiting.py` `is_review_lane` and `review_lane_sql` (RA-3) |
| **Person act** | A timeline row that a person made: no agent act (`is_agent_act`), and no actor that starts with `system:`, such as a workflow or GitHub. | `analytics_waiting.py` `is_person_act` (RA-3) |
| **Handoff blocker** | An open `blocks` link where an agent holds one task and a person holds the other. "Holds" is RA-1's holder rule. | `analytics_waiting.py` `handoff_side` and `handoffs_sql` (RA-3) |
| **Answer time** | The time from an agent's question to the prompt that answers it, for the questions of the report period. A question with no answer stays out of the median and the p90, and the section counts it. | `analytics_waiting.py` `answer_time` and `answer_time_sql` (RA-3) |
| **Silent run** | An open run in `in_progress` with no agent activity for 15 minutes or more. A run with no agent activity yet counts from its start. | `analytics_agents.py` `SILENT_RUN_AFTER_S` and `silent_run_sql` (RA-2). RA-4 reads the same constant. It is not the idle state of the pill, `filters.AGENT_RUN_STALE_AFTER_S` (5 minutes), which reads every activity. |
| **Agents**, **People** | The two groups of a done task. Agents holds the agent-finished and the agent-assisted tasks. People holds the tasks that no agent worked before the finish. | `analytics_agents.py` `actor_group` and `actor_group_sql` (RA-2) |
| **Agents working now** | An open run in `in_progress` on an open task, that is not silent. "Working" is the rule of the board strip, `agent_summary.WORKING_STATES`. | `analytics_agents.py` `runs_now_sql` (RA-2) |
| **Agent PR merge rate** | The pull requests on tasks that an agent worked before the close, merged or closed in the last 14 days. The rate is the merged ones over the merged and the closed ones. A merge reads its `merged_at`. A close with no merge reads `pr_updated_at`, the last update of the pull request on GitHub. A pull request with no time from GitHub does not count, because the token path of TR-1 moves the row's own `updated_at` on each read. An open or draft pull request does not count. The trend compares the 14 days before. | `analytics_agents.py` `merge_sql`, `pr_closed_at` and `merge_rate` (RA-2) |
| **Agent time** | The time that agents worked on a finished task. Each run lasts from its start to the last act of its agent, and never past its end. A takeover or a server close stamps its end later than that. Each wait on a person comes out. A wait lasts from a question to its answer, or to the next act of the agent if that comes first. Each idle gap of a reopened run comes out too. A member prompt moves a completed run back to `in_progress` in the SAME run. The time from the end it reopened to that prompt belongs to the member. Only a run made at or before the finish counts. The tile gives the median over the finished tasks with a measured run. | `analytics_agents.py` `run_hours` and `run_time_sql` (RA-2). The answer of a question is `analytics_waiting.answered_at_sql`. The gaps are `_REOPEN_GAPS_JOIN` in SQL and `reopen_gaps` in Python (RA-4b). A gap exists only before a prompt that carries `content.reopens.ended_at`. `agent_runs._write_activity` stamps that key when a prompt reopens a terminal run. A prompt to an open run makes no gap, and a row before RA-4b has no key, so it makes none. When the prompt answers a question, the gap starts after the wait of that question, so no second leaves twice. |
| **Run outcome** | How an agent run ended: completed, failed, stopped, abandoned, released or closed by Metorite. The first five are the terminal statuses of a run. A run closed by Metorite has the status `completed`, and the server wrote that end: the sweep of a task that is done, cancelled or archived (`task_close`), or an approved archive (`archive_close`). The marker `server_close` is on the final system line of the run. A member prompt after it reopens the run, and the later end is the agent's. A run counts on the UTC day of its `ended_at`, which each terminal move stamps. The window is the last 14 UTC days, today included. | `analytics_agent_runs.py` `RUN_OUTCOMES`, `SERVER_CLOSED_SQL` and `outcome_series_sql` (RA-4, RA-4b) |
| **Success rate** | The completed runs divided by the completed, failed and abandoned runs of the window. A stop, a release and a run closed by Metorite stay out of both sides, because a person chose the stop, a release gives the task back with no outcome, and a server close says nothing of the agent's work. With no such run, there is no rate. The trend compares the 14 days before. No text calls 100% the goal. | `analytics_agent_runs.py` `success_rate` (RA-4) |
| **Run size** | The agent time of a completed run of the window, in one of four sizes: under 15 minutes, under 1 hour, under 4 hours, and 4 hours or more. "Agent time" is the §5 row above, for one run. A run with no agent time has no size, and the section counts it. | `analytics_agent_runs.py` `size_bucket` and `size_bucket_sql` (RA-4). The agent time is `analytics_agents.run_time_joins_sql`. |
| **Long run** | A completed run of 4 hours of agent time or more. The panel flags it as "maybe a poorly specified task", with the count, and names the longest five. | `analytics_agent_runs.py` `is_long_run` and `long_line` (RA-4) |
| **Agent PR outcome** | One of three states of a pull request on a task that an agent worked. Merged and closed with no merge are the two outcomes of the last 14 days, counted back from now. Open means a state of `open` or `draft`, on an open task, now. The merge rate and its trend are the §5 row "Agent PR merge rate", read from the same query. So the panel and the tile show one rate. | `analytics_quality.py` `quality_body`, which runs `analytics_agents.merge_sql` (RA-5) |
| **Rework** | Two kinds, over the last 14 days and the 14 days before. **Reopened**: a status move from a done status to a status that is not done and not cancelled. A move to cancelled is a relabel, and it is no rework. "Tasks reopened" counts each task once. A task belongs to the group of the finish that its latest reopen in the window undid. The groups are Agents (agent-finished or agent-assisted) and People, by the labels of RA-1. Never one person. **Reopen rate**: a cohort. It takes the finishes of the window, by group. It counts the share that a later reopen undid, at any time up to now. A reopen undoes a finish when it comes after the finish and before the next finish of the task. One finish counts once, so the rate never passes 100%. A recent finish has had less time to be reopened, so the newest window reads low. The 14 days before use the same rule. **Retried after failure**: a run that ended `failed`, and a later run on the same task. The later run is created after the failed run ended. While `agent_runs_enabled` is off, no run is read. | `analytics_quality.py` `is_reopen_move`, `finish_undone`, `reopened_tasks`, `reopened_sql`, `is_retried` and `retried_sql` (RA-5) |
| **CI red** | An open or draft pull request on an agent task, whose `checks_state` is `failing` now. The panel counts them and names five. The oldest comes first, by the time the task linked the pull request. **Merged while red**: an agent pull request that merged in the window. Its `checks_state` is `failing`, read at or before `merged_at`. A read after the merge does not count. The checks write does not look at the state of the pull request (`github_sync.py` `_CHECKS_WRITE_SQL` and `_HEAD_HELD_SQL`). So a late check can rewrite the state after the merge. The count can miss a pull request, and it never adds one. A new head resets the state to `pending`. | `analytics_quality.py` `failing_prs_sql`, `merged_red` and `merged_red_sql` (RA-5) |
| **Forecast by count** | 2,000 runs play the history weeks again. Each simulated week is one past week, taken at random. Its finished tasks come off the open count, and its new tasks go back on. The 50% date is the week by which half the runs empty the open count. The 85% date is the week by which 85% of them do. Each date is today plus whole weeks. The same seed gives the same dates. The seed is the scope and the last week of the history. | `analytics_forecast.py` `count_forecast`, `simulate_weeks`, `percentile_weeks` and `forecast_seed` (RA-6). The weeks are `analytics.velocity_sql`, the spine of Throughput. |
| **Not converging** | One of two things gives no date. In the first, new work arrives, and it arrives at least as fast as work finishes. That is the rule of 2026-09-17. The panel then gives the finished tasks a week that would converge it: the new tasks of a week, plus the open count divided by 52, rounded up to one decimal. In the second, fewer than 85% of the runs empty the open count within 52 weeks. The panel then says that the 85% date is more than 52 weeks away. It says that a steadier flow of finished tasks, or smaller tasks, brings that date closer. It names no rate, because an 85% test has no mean rate. | `analytics_forecast.py` `converge_per_week`, `REASON_ARRIVALS` and `REASON_CAP` (RA-6, fix round 1) |
| **Not enough history** | Fewer than 3 finished tasks in the history weeks, or fewer than 2 weeks with a finish. The forecast gives no date. It says how many tasks finished, and in how many weeks. A project that grows is "Not converging" before this rule runs. | `analytics_forecast.py` `MIN_FINISHED_FOR_FORECAST` and `MIN_FINISH_WEEKS` (RA-6, fix round 1) |
| **Needs an owner** | An open task with no assignee and no open run. An open run has the status `created`, `in_progress`, `awaiting` or `stopping`, with no regard to its lease. Each such task is in the `none` count of RA-1's `holder_split`. `none` also holds an unassigned task whose open run lost its lease, and that task has an owner. | `analytics.py` `needs_owner_sql` and `is_needs_owner` (RA-6). Load's `needs_owner` carries the total and `leads`. |

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
(R4). Pins wait for a later slice.

**Since the UX pass (§6.5)**, the empty state is one line: "You have not
saved a report yet. Pick a question below." A card shows the name, then the
subject and the scope. It shows the template name only when the name differs
from it.

**The rail** lists the saved reports in two groups, "Yours" and "Shared with
you", by the server's `mine`. A shared row names its author. On a phone the
rail is absent on the home screen, because the cards list your reports.

**In R2, the saved list has no filter.** The filter for scope, template and
owner waits for a later slice.

**Since R5f (§9 Q14)**, the home screen has three areas, top to bottom.
1. **Overview.** The builder with no name field, over the whole organization. It shows the KPI strip, the space table and seven sections. It saves nothing, and "Save as report" keeps its choices.
2. **Start from a question.** The template gallery from §4.
3. **Your reports.** The saved reports, as cards.

The rail is absent on Overview. It shows when a member opens a saved report.

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
  Order stays fixed by `SECTIONS` (`reports.py:100` to `:123` gives the reason).
- **The preview renders from the server** on each change, with a short delay.
  It uses `POST /projects/reports/preview` with an unsaved config. The
  browser computes no figure (§9.12.7).
- **No schedule control in Phase 1.** A report renders when a member opens
  it. Phase 2 adds the schedule chip: "and send it to [person] every
  [weekday at 09:00]".

On a phone, the preview moves under the sentence, and each chip takes the full
width.

**Since the UX pass (§6.5).** At `xl` and wider, the chips and the other
controls are a sticky column on the left, and the preview is on the right.
Below `xl` the two stack. The rail leaves while a member builds a report.

When every chosen section ignores the period, "As of today" replaces the
period chip. The sections sit under three labels. The name follows the chips
until the member types one. Edit mode offers "Delete report".

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

**The line under the title** (the UX pass, §6.5) names the subject, the scope
and the period, in the words of the chips. An example is "About Meera Iyer ·
In Printer X2 · 31 Aug – 27 Sep 2026". A report whose sections all ignore the
period says "As of 29 Sep 2026" in place of the period. The word for the
organization is "Whole organization" on every surface, the email too.

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

### 6.5 The UX pass (2026-09-29)

The owner asked for report creation that is intuitive, easy and pleasant to
look at (directive, 2026-09-29). A design review ranked 18 changes, and this
pass applies all of them. Each rule below changes what the UI shows or says.
The one look binds each rule (`DESIGN_SYSTEM.md`).

**The builder.**
1. **The preview sits beside the controls.** At `xl`, the controls are a
   sticky column of 18rem, and the preview is on the right. Below `xl` the
   two stack. The saved-reports rail leaves while the member builds or edits.
2. **A project-only template asks for a project.** T5, "Project status", has
   the `scope_kinds` `["project"]` and nothing else. For a new report, its
   scope chip says "choose a project" and offers no "Whole organization".
   Save is off. The preview says "Choose a project in the In chip to see this
   report." An edit does not ask, because PATCH cannot change the scope. So a
   saved org-wide T5 keeps its scope, and Save stays on.
3. **The period chip says "As of today" when no section reads the period.**
   `render_body` reads the period only in `finished` and `throughput`. When
   every chosen section is one of the other eight, "As of today" replaces
   the chip.
4. **The sections sit under three labels**, each in `SECTIONS` order.
   "What happened" holds `finished` and `throughput`. "Where we stand" holds
   `outlook`, `load`, `capacity`, `stuck` and `hygiene`. "Who needs help"
   holds `pulse`, `conflicts` and `rebalance`. A checkbox's tooltip is its
   panel's own sentence.
5. **The name follows the chips** until the member types one. Examples are
   "1:1 prep: Meera Iyer" and "Team pulse: Hardware team". "My day" stays "My
   day". A saved report keeps its saved name.
6. **The subject menu** shows "Everyone" with no heading, then "Teams", then
   "People". The address shows only on "Me", and on two people with one name.
   The filter box also matches each address, so a search by name or by
   address finds a person.
7. **Team pulse starts on the reader's team** when the reader is not an admin
   and leads exactly one team.
8. **One Retry.** When the subjects read fails, the subject chip is off and
   says "People did not load". Retry sits beside it. The preview line says
   what failed, with no second Retry.
9. **The preview does not blank.** On a change, the last preview stays,
   dimmed, with "Updating…". A skeleton shows only on the first load. A failed
   preview says "The preview could not be drawn." with "Try again".
10. **Edit mode** has the title "Edit: [name]", and no "Started from a
    template" line. A reader whose row has `can_delete` sees "Delete report".
    It asks through the shared `ConfirmDialog`, and the words say that the
    delete is for good, for every reader.

**The home and the report.**
11. **The gallery** shows the live templates first, in one grid. The
    coming-soon templates fold under "Coming later (N)", and their cards
    carry no badge. What a template waits for is a tooltip, not a line on
    the card.
12. **The rail and the cards** follow §6.1: "Yours" and "Shared with you",
    the author on a shared row, and one empty line. Each rail row says its
    scope on a line under the name, in the chips' words: "Whole
    organization" or the project's name.
    With no saved report the rail is absent, and Home shows the one empty
    line. The line adds the finished count only when the count is a number.
13. **The header line** follows §6.4: the subject and the scope in the chips'
    words, then the period or "As of [day]". The email and the downloaded
    file print the same "As of [day]", because `reportLayout` reads
    `periodFree` and `asOfDay` from `reportBuilder.ts`.
14. **A failed render stays in the pane**, with its sentence, "Try again",
    and Home and Edit. The page has no error line above the pane.
15. **Text follows the density.** `ReportsView.tsx` uses rem sizes only, so
    `--ui-scale` reaches every line. A card title is never smaller than its
    question.
16. **The hidden line says what to do** where a team choice changes the
    report. A lead who reports on "Everyone" reads "This view hides N other
    people. Choose a team in About to report on that team only." §7.1 hides
    those people on any subject, so the words do not promise to show them.
    The line shows once, under the `pulse` panel. The table under it does
    not count the same people again. A template with no About chip shows no
    hint, because the words would name a control that is absent.

**The panels and the entry points.**
17. The `pulse` note is the tooltip of the panel's sentence. The conflicts
    window reads as dates ("29 Sep 2026"). "Who is overloaded" shows a name
    when the payload carries one.
18. The person header wraps at 390 px, so the name keeps its width. "1:1
    prep" is absent on the reader's own page, because "My day" covers it. The
    chat's report card links through `reportLink`.

**As built (2026-09-29).**
- `lib/reportBuilder.ts` holds each rule as a pure function: `scopePrompt`,
  `scopeChoices`, `startingTeam`, `hiddenTeamHint`, `PERIOD_FREE_SECTIONS`,
  `periodFree`, `asOfDay`, `reportHeaderLine`, `scopePhrase`, `subjectLabel`,
  `sectionGroups`, `builderName`, `reportCardLine`, `railGroups`,
  `deleteShown` and `editTitle`. `BuilderState` gains `nameTouched` and
  `subjectTouched`.
- `lib/panelHints.ts` is the one source of each panel's sentence. The panels
  and the builder's checkboxes read it.
- `hiddenPeopleLine` in `reportEmail.ts` takes an optional `hint`. The email
  and the chat pass none, so their words stay the same.
- `deleteReportCopy` in `lib/deleteCopy.ts` holds the delete words.
- Fences: `reportBuilderUx.test.ts`, `reportsUx.test.ts`,
  `panelHints.test.ts`, `reportEntry.test.ts` (item 18) and
  `deleteCopy.test.ts`. Each test title and code comment names its item by
  the number in this list. In `test_projects_report_sections_lockstep.py`, a
  test reads the branches of `render_body` and pins `PERIOD_FREE_SECTIONS`
  to them. A mutation that drops `stuck` from the list turns it red.
- ⚠️ **The review's list of period-free sections was short.** It named five.
  `render_body` shows eight: `load`, `outlook` and `stuck` read the state now
  too. The lockstep test holds the eight.
- ⚠️ **No server sends a name on a `load` row yet.** `LoadRow.name` is
  optional, and "Who is overloaded" shows the address until one does.
- ⚠️ **The "As of" day.** The server sends a day only in `pulse` (`today`).
  Another period-free report reads the reader's own date, because the render
  ran now.

---

### 6.6 Builder redesign (owner, 2026-09-30)

The owner looked at the Team pulse builder and asked for a UI that is "easier to use and non-threatening". The builder and the report must feel calm, friendly and obvious. They use fewer words, less chrome and more visual guidance. The data, the seams and the one look do not change (`DESIGN_SYSTEM.md`).

**A. One header row.**
1. The row holds a back button "Reports", the report name as an inline title, Cancel and the Save button.
2. The title looks like a heading, and it shows a pencil on hover and on focus. It is still a labelled input with `MAX_REPORT_NAME`, and the `nameTouched` rule stays.
3. "Delete report" stays in the row as a quiet button. It shows only when the server's `can_delete` is true.
4. In Overview the title is "Overview", and it does not change. The row holds "Save as report".
5. The pane has no heading "Reports", because the page header names the app. Home keeps "New report".

**B. Three steps.** The left column is one card, "Build your report", with three numbered steps. A plain label sits above each control.
1. "Who is it about?" holds the subject chip. A locked "My day" shows "You", a lock icon and a short note.
2. "Which work?" holds the scope chip, and "Include the projects under it" sits under it.
3. "What time?" holds the period chip. With no period, a soft pill says "Today's snapshot", with a clock icon. Its tooltip gives the reason.

A report from a template shows a small badge "From template: [name]" under the header. The line "Started from a template" goes.

**C. What to include.** Section tiles replace the checkbox grid.
1. Each tile is one line: the icon of its section, its name and a check. The short line is the tooltip, and a screen reader reads it through `aria-describedby` (round 2).
2. A chosen tile has an accent border, a soft accent tint and a check mark.
3. A tile that a subject turns off is muted, and it says why.
4. The groups are "What happened", "Where things stand" and "Who needs help". A report still needs one section. The last chosen tile is `aria-disabled`, and it says why.
5. One map in `lib/sectionIcons.ts` names the icon of each section.
6. Each section has one name, in `REPORT_SECTIONS` (round 2). The tile, the card title, the clear row and the email heading all say it. So a member can join a tile to its card.

**D. The preview.**
1. The preview sits on a soft surface, so the report reads like a page. A small "Live preview" label and a status dot replace the server sentence. A spinner shows while it updates.
2. The report header shows the name. Under it, one line with small icons names the subject, the scope and the period. `reportHeaderLine` stays the one source of that text.
3. An empty section is one compact row: a check mark, the section title and one friendly line. When every section is empty, one calm card says "All clear" and lists the sections.
4. The friendly lines are short and active. `lib/sectionEmpty.ts` holds them, and the app, the email and the download read them.
5. The table of a section opens from a small table button in the section header. An empty section has no table button.
6. A footnote, such as the conflicts horizon, is one short line. It shows only when the section has rows.

**E. Home.**
1. One heading "Reports" shows.
2. The space table takes the full width of the preview.
3. A compact strip of the live templates sits above the Overview panels. The full gallery stays below.
4. "Save as report" sits in the Overview header row.
5. At 390 px a number shows before the controls. The controls sit behind "Change what you see".

**F. Motion and density.** Only subtle transitions, such as a colour change. The type follows the density tokens. The review covers light, dark, compact, a changed accent and 390 px.

§6.7 replaces items 3, 4 and 5 of E for Overview.

### 6.7 The live Overview (owner, 2026-09-30)

The owner asked to read the live analytics at once, on the full page, with no extra menu (§9 Q17). Overview is still the builder in `reportsOverview` mode. This section changes its layout only. The data, the seams and the one look do not change.

**A. Full-page panels.**
1. The controls card is not on the page by default.
2. The KPI strip and the space table take the full width.
3. The panels flow in a grid. It has one column below `xl` and two columns at `xl` and up. The two cards of a row have one height (round 1).
4. "Forecast" and "How long it took" take both columns when they start a row. A clear row always takes both columns. When a row would hold one panel alone, that panel takes both columns. So no row is half empty.
5. A saved report and the builder keep one readable column.

**B. One slim toolbar.**
1. On the left: the title "Overview" and one summary line. An example is "Whole organization · The last 12 weeks · Updated 2 min ago".
2. On the right: "Filters", "Refresh", "Save as report" and "New report". There is no menu and no dialog.
3. "Filters" shows a small count when a choice differs from the Overview defaults.
4. "Save as report" is optional. A member can read Overview and save nothing.

**C. Filters open in place.**
1. The panel opens under the toolbar, and its button labels it.
2. The three choices sit in one row that wraps on a small screen. The sections are small chips under them, in their three groups.
3. A chip keeps the rules of a tile (§6.6 C).
4. "Reset" returns to the Overview defaults. It shows only when a choice differs.
5. Filters is closed by default, on each screen size. The browser remembers the state. The page renders the same when the storage fails.

**D. Live.**
1. Overview asks the server again when the tab becomes visible and the last answer is more than 60 seconds old.
2. While the tab is visible, Overview asks again every 5 minutes. A hidden tab stops the timer.
3. The last body stays on screen during a refresh. Only the Refresh spinner and the "Updated" time change.
4. A refresh sends the key on screen. So Home still keeps the Overview state.

**E. Home.** The gallery and "Your reports" stay under the dashboard. The template strip above the panels goes. The gallery under the dashboard has the same heading and the same templates. The "Change what you see" button goes, because "Filters" replaces it.

### 6.8 People and agents in the Overview (product decision, 2026-10-25)

The owner delegated this design on 2026-10-25: "think of yourself as the product manager". External AI agents now claim, work and finish tasks over MCP (`external_agent_access.md` §12.1 to §12.4). The Overview must show how work gets done by people AND by agents: speed, cost, quality, and how the two coordinate. The decisions below rest on a survey of Paperclip, Linear, Jira, GitHub Copilot, Swarmia, Jellyfish, Devin, Factory, LangSmith and Langfuse (2026-10-25).

**A. The five questions.** The Overview answers five questions, in this order. A panel that answers none of them does not belong on it.

1. Will this finish, and when?
2. Who is carrying the work: which people, which agents?
3. What is stuck or waiting, and on whom?
4. Is the output good?
5. What does it cost?

**B. What we keep, change and add.**

| Panel | Decision | Why |
|---|---|---|
| **KPI strip** | CHANGE. Six tiles: finished this week (people and agents), median cycle time (people and agents), waiting on a person (count and oldest), agents working now (working, silent, failed today), agent PR merge rate (14 days), cost for each finished task | Each tile is one of the five questions at a glance |
| **Forecast** | CHANGE. Forecast by COUNT with a Monte Carlo of weekly throughput, at 50% and 85%. Keep "Not converging", and add the finishes a week that would converge it | Estimates are rare. The screenshot of 2026-10-25 shows 26 open tasks and none sized, so "Nothing sized" answered nothing. A count forecast needs no estimate |
| **Who did the work** | NEW. Replaces "What we finished" when the scope is one project. Weekly finishes, stacked by people and by agents. Each agent rolls up under its member | A bar of one project, inside that same project, says nothing |
| **How long it took** | CHANGE. Split the median and p90 by people and agents | "Are agent tasks really faster, end to end?" |
| **Waiting on people** | NEW. The coordination panel (C below) | No surveyed product does this well. Our agent runs already hold the data |
| **Agent runs** | NEW. 14 days of run outcomes (completed, failed, stopped, abandoned, released), the success rate, silent runs now, and run sizes | "Can I trust agents with this work?" Paperclip's Run Activity and Success Rate |
| **Quality** | NEW. Agent PR outcomes (merged, closed unmerged, open), work reopened after done, CI red on agent PRs | Speed without quality is the trap every survey names |
| **Cost** | NEW. Cost by project and for each finished task, with its source on every figure (D below) | "What does this throughput cost?" |
| **Stuck work** | CHANGE. Add silent agent runs. Mark a task older than this project's own p85 age | One place for "what is dying quietly" |
| **Open work** | CHANGE. Add the open runs of each agent. Show "Needs an owner" first when unassigned work leads | 25 of the 26 open tasks in that screenshot had no owner. That is the finding |

**C. The coordination panel: "Waiting on people".** This is the panel no competitor has, and we build it first. It lists:

1. **Agent questions with no answer.** Each row shows the task, the agent, the member it asks and the age. The oldest question shows first. The source is runs in `awaiting`, with an open elicitation.
2. **Agent work waiting for review.** Tasks that an agent moved to a review lane, and agent PRs that are open with no review yet.
3. **Handoff blockers.** Open `blocks` links where a person's task blocks an agent's task, or the reverse.
4. **Answer time.** The median and p90 from an agent's question to the person's answer, over the window.

Each row opens the task. A reader sees only the rows of tasks they can open (§7 rule 1).

**D. Cost, and how sure each figure is.** Cost has three sources, decided in the swarm plan (Metorite #330, SW-20). Each figure prints its source:

1. **Measured.** In-app agents spend our AI credits, and the credits ledger joins to the task and the run.
2. **Reported.** External agents may send their own `usage` (tokens, model, an estimate). It shows as "reported by the agent", never as billed.
3. **Time.** Agent run time (start to end, minus waits on a person) and person wait time. Always measured. It shows as hours, never as money.

A figure with no known usage shows "unknown", never $0. The panel shows the share of finished tasks with any cost data, so a reader knows how much the number covers. Budgets and prices wait for H-42.

**E. Rules that bind every panel.**

1. **No leaderboard and no rank of people.** Show load and who is waiting, never "the top performer". Per-person rows follow §7.1 exactly. SPACE and Swarmia both warn that individual output scores are counter-productive.
2. **An agent never hides its person.** Each agent figure names the member it works for, as Linear does with delegation.
3. **Adoption is not productivity.** Do not show agent sessions, messages or tool calls as output. Count finished tasks, merged PRs and time.
4. **No 100% target.** The merge rate shows its trend. The text never calls 100% the goal, because Swarmia names "close to, never at" as healthy.
5. **Summary first.** Each panel opens with one plain sentence, as Forecast does. The run log stays one click away, never on the Overview (Paperclip's "no log worship").
6. **Every word is a rule.** §5 Signals gains the rows below. The Overview prints the reason beside each status.
7. **Chat first.** The assistant answers "how are the agents doing on this project?" from the same sections (owner rule, 2026-10-20). It is a read, so it asks nothing.

**F. New signal words for §5.**

| Word | Rule |
|---|---|
| **Agent-finished** | A done task whose move to done came from an agent: the activity carries `meta.via` = `mcp:…`, or the actor is `agent:<name>`. The move is the finish the window counts. |
| **Agent-assisted** | A done task that an agent worked before its finish (a run, or an act through MCP, by the rule of `agent_worked.py`), and that a person, a workflow or GitHub finished. The finish is the finish the window counts. Changed 2026-10-25 in the RA-1 review: the PM replaced "held at least one agent run". |
| **Waiting on a person** | An open run in `awaiting` with an open elicitation, or agent work in a review lane with no human action since. Sharpened 2026-10-25 in RA-3: an open pull request on a task that an agent worked also waits for a review, and a handoff blocker waits on a person too. §5 holds the exact rule. |
| **Silent run** | An open run in `in_progress` with no agent activity for 15 minutes or more |
| **Rework** | A task that went back from done to an open status, or a failed run followed by a new run on the same task |
| **Needs an owner** | An open task with no assignee and no open run |

**G. What we do not build.** No per-agent token charts beyond the cost panel. That is observability, a LangSmith job. No lines-of-code counts and no acceptance rates. No DORA deploy metrics, because no deploy data lives here.

**H. Slices.** RA-1 to RA-8 in §8, after R-final. Order: RA-1, then RA-3 (coordination), then RA-2, RA-4, RA-6, RA-5, then RA-7 when SW-20 captures usage, and RA-8 last.

*Verified against code: 2026-10-25, at `origin/main`. `pm_task_refs.state` is `open`, `draft`, `merged` or `closed` (migration 255). The run statuses and elicitations are in migrations 267 and 289. `meta.via` comes from `record_activity`.*

## 7. Data and privacy rules

1. **The viewer's scope.** Every section uses `task_visibility_clause` and
   `reportable_with_ancestors_clause`, as `analytics.py` does. A report about
   a team shows only the tasks that the reader can open.
2. **The HR tier is gated.** Hours, absences, skills and fit need
   `admin:members:read`. Without it, a section returns its task half and
   `hr_visible: false`, as S7 does. The UI says that an admin can see
   capacity. It does not guess. Two exceptions apply.
   **The `outlook` section** (the owner, 2026-09-25,
   `projects_ai_chat.md` §17.3 rule 9). Its hours come from each holder's
   working schedule for every reader, because `working_hours` is directory
   tier. Absences stay HR tier. They reduce the hours only for a reader with
   `admin:members:read`, and the section says `absences_applied: false`
   for every other reader.
   **The reader's own card in `pulse`.** `people_center_app.md` §4.2 opens
   the HR half of a person to that person (R3d, edit E4).
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
   in `report_scope.py` answers for a person and for a team. It is not in
   `reports.py`, because `reports.py` imports `analytics.py` and the analytics
   routes call the check too. Every path calls it:
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
   The Center groups (`sales`, `people`, `company` and the others in
   `routes/admin/groups.py:41`) are `org_group` rows too. So a lead of a
   company-wide group may report on everybody in it.
5. **The member setting shows the team, and not each person.** With
   `reports.members_see_own_team` on, a member sees the team's totals and
   their own row. They cannot open a report on one teammate.
6. **The HR tier stays separate.** Hours, absences and skills still need
   `admin:members:read` (§7 rule 2). A lead without that grant sees the task
   half of their team. The capacity section tells the lead that an admin can
   see the hours.
7. **Rebalance rows need the HR grant, and only the admin row holds it.** So
   rule 3 holds for `rebalance` with no row filter. The rebalance lists need
   the HR grant, and §7.1's admin row is the same grant. So R5 adds no reader
   filter to rebalance.

⚠️ **The `load` section is older than this rule.** It lists open work for each
person, and today any member who can see the tasks can read it. Slice R5
applies rule 3 to `load` too, so the Analytics app and a report agree. The
Load panel in the Analytics app then changes in the same way. The PR for R5
must say this. The Capacity panel and the `parallel_person` rows of the
Conflicts panel change in the same way.

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

1. `reports.py` `SECTIONS` (`:124`).
2. `skill_projects/reads.py` `_REPORT_SECTIONS` (`:1447`). This is the
   chat's list, not `views.py`.
3. `skill_projects/writes.py` `REPORT_SECTIONS` (`:854`).
4. `ReportsView.tsx` `RenderedBody` (`:219`).
5. `src/lib/reportEmail.ts`.
6. `lib/reportBuilder.ts` `REPORT_SECTIONS` (`:34`).
7. `lib/api.ts` `RenderedReportBody` (`:820`).

The chat card map is `skill_projects/views.py` `REPORT_CARD_SECTIONS`
(`:318`). `test_projects_report_sections_lockstep.py` holds the seven lists.
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

**H-169 is closed by `projects_ai_chat.md` §17 (S11).** The section calls
`outlook_body`, and it does need the reader's grant. `render_body` passes
`hr_visible=can_read_hr_fields(user)`, as the `capacity` section does. So
absences reduce the report's hours only for a reader with
`admin:members:read`.

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

#### R3c — `hygiene` and T13 · AGENT-SAFE · BUILT 2026-09-26

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

**As built:** `test_projects_report_sections_r3c.py` seeds one project and a
stopped child project on a real database. It proves (a) to (g), and a
mutation of each of (b), (d), (e) and (f) turns its test red.
`hygiene_body` has no route yet, so `test_projects_analytics.py` names it
as the one body with no route. `reportVisuals.test.ts` draws nine panels,
and `hygiene.test.ts` holds the email words equal to the panel words.
The chat card and the chat text show five hygiene rows of each kind, then a count of the rest.
The card cuts a timestamp to its date only in a date column, so a title prints whole.

#### R3d — `pulse` and T1 · AGENT-SAFE · BUILT 2026-09-28

**What:** the `pulse` section. It draws one card for each person: a load
bar, the status, the top focus tasks and a "Needs help" mark. T1
`team_pulse` goes live.

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

**The body.** `pulse_body` in a new module, `analytics_pulse.py`, returns the
section. It takes the reader, the scope and one `today`. It passes that
`today` to `capacity_body`, and its own queries use the same UTC day. The
render awaits `pulse_body`, as it awaits `hygiene_body`.

**Edit E3, the rows.** The rows are the `kind == "person"` rows of
`capacity_body`. So only a person who holds open work in the scope gets a
card. An agent and the unassigned row get no card. The `pill` of a card is
the `pill` of its capacity row, so the two cannot disagree.

**The fields of a row:**
- `status` is `on_leave` when `absent_on(today)` finds a full absence.
  Otherwise `status` is the pill. A partial absence keeps the pill.
- `blocked_count` counts the open tasks of the person with an open blocker.
  It calls `blocked_clause()`. R3d moves that predicate out of the `stuck`
  route into `analytics.py`, and the route calls it too.
- `stale_count` counts the tasks in progress of the person with no change for
  `STALE_DAYS`. It reads the `stale_in_progress` predicate of
  `HYGIENE_KINDS`, so pulse and hygiene have one rule.
- `focus` holds up to five tasks: tasks in progress, and tasks due today or
  tomorrow. `focus_total` counts them all.
- `has_room` is true when the row has the `idle` flag and `status` is not
  `on_leave`. The `idle` flag applies `IDLE_FRACTION`. So R3d adds no second
  threshold.
- `needs_help` is true when `help_reasons` is not empty. The reasons are
  `blocked`, `stale` and `waiting_overdue`.

**Behind two runs in a row.** This reason needs the stored runs of R4. The section
carries `help_note`, which says that the report does not check it yet.

**Edit E4, the HR split.** The HR half of a row needs `admin:members:read`,
or the row must be the reader's own row. This is the self door of
`people_center_app.md` §4.2. Any other row carries only the task half:
`open_tasks`, `overdue`, `blocked_count`, `stale_count` and `focus`.

**A row with no HR half** has no `pill`, no `status`, no `has_room` and no
hours key. Each key is absent, never null. Its `help_reasons` use only
`blocked` and `stale`. Before R5, only an admin sees the row of another
person. So this rule binds first in R5.

**Private notes stay on the reader's own row (owner Q6).** The section reads
`pm_task_personal` for the reader's own address only. A task with a
`scheduled_start` of today joins `focus` on the reader's own row. Every other
row has no `waiting_count` and no `waiting` key. This rule holds for an admin
too.

**Waiting past its date (repair round 1, 2026-09-28).** A waiting item counts
when two things are true. First, `personal.effective_disposition` gives
`WAITING`, so a task that the reader moved to NEXT does not count. Second,
its line is before today. The
line is `expected_by` when a person stated a promise, and else `due_at`. This
is the rule of My Tasks, `isWaitingOverdue` in `app/tasks/lib/waiting.ts`.

Every delegate site stores `expected_by` null, so the usual line is the due
date.

**Edit E5, who sees which card (owner Q7).** A reader with
`admin:members:read` sees every card, up to `MAX_PEOPLE` (20). Any other
reader sees only their own card. `hidden_people` counts the cards that the
reader cannot see. `people_total` counts every person row before the filter
and the cap. The filter runs in `pulse_body`, before the body leaves the
server.

**The hidden line.** When `hidden_people` is more than zero, the panel, the
email and the chat card show "This report hides N other people". E5 binds
the `pulse` section only. R5 applies §7.1 rule 3 to the other sections.

**T1 goes live.** `team_pulse` gets the sections `pulse`, `conflicts` and
`rebalance`, with `weeks` 1 and `skip_current_week` false. Its scopes are
`project` and `org`, and R5 adds `team`. The three sections ignore the
period. `pulse` reads today, and the other two read their own horizons.

**The visual.** `PulsePanel` in `AnalyticsPanels.tsx` draws one card for each
row, in a report only. A card shows the name or the address, a load bar and
the status. Up to three focus tasks follow, then the "Needs help" mark with
its reasons.

**The load bar** uses the private `Bar`. With `hours_basis`, it shows committed
hours of working hours this week. Without it, the bar shows `overdue` of
`open_tasks`.

**Colour.** The status takes `PILL_HUE` through `accentForHue`, with
`PILL_LABEL` beside it. `on_leave` takes the `gray` hue and the words "On
leave". The email and the download print one line for each card, with no
colour. The chat card shows one row for each card.

**The lockstep.** `pulse` goes between `capacity` and `stuck` in `SECTIONS`,
and in each of the seven lists. The self-test
`test_the_template_fence_fires_on_a_missing_section` uses the fake name
`not_a_section`.

**Non-goals:** a `/analytics/pulse` route, a panel in the Analytics app, a
summary tile, T2 and T6. "Behind two runs in a row" waits for R4.

**Done when:**
- (a) On a real database, each card of an admin reader has the `pill` of
  `capacity_body` for the same person, scope, reader and `today`.
- (b) A person with a full absence today has `status` `on_leave` and
  `has_room` false. The panel draws "On leave" and not "Idle". A partial
  absence keeps the pill.
- (c) Member Bea has a task in scope with disposition `WAITING` and
  `expected_by` yesterday. Bea also scheduled a second task today. An admin
  render has no `waiting` key on the card of Bea. That second task is not in
  the `focus` of Bea. The render for Bea shows both.
- (d) A reader with no grant renders an org report with three holders. The
  body has one row, the reader's own, with `hidden_people` 2 and
  `people_total` 3. The panel, the email and the chat card print the line.
- (e) An admin render has `hidden_people` 0. A reader who holds no work in
  the scope gets no row, and `hidden_people` equals `people_total`.
- (f) A unit test calls the row builder with no grant, for a row that is not
  the reader's own. The row has no key of `HR_KEYS`, no `status` and no
  `has_room`.
- (g) A source test finds `link_type = 'blocks'` once in `analytics.py`, in
  `blocked_clause`. The `stuck` route and `pulse_body` call it. A task that
  a closed task blocks is not in `blocked_count`.
- (h) A task in progress with `updated_at` 15 days old is in `stale_count`.
  A task with 13 days is not. A source test proves that pulse names no day
  number of its own.
- (i) Each reason fires alone: `blocked`, `stale` and `waiting_overdue`. A
  person with none has `needs_help` false. The body carries `help_note`.
- (j) Seven focus tasks give five rows and `focus_total` 7. A task due in
  three days, and not in progress, is not in `focus`.
- (k) T1 is live. The live list in the lockstep, R3b and R3c tests is
  `team_pulse`, `weekly_delivery`, `project_status`, `data_hygiene`.
- (l) The lockstep test and the card title test pass with the new name.
- (m) `reportVisuals.test.ts` draws ten panels, and fence (b) names
  `PulsePanel`.
- The visual review passes in light mode, compact density, a changed accent
  and at 390 px.

The `waiting` rows of `pulse` for one person serve T2 and T6. They are not a fifth section.

**As built:** `test_projects_report_sections_r3d.py` seeds two projects and
seven people on a real database. It proves (a) to (j). A mutation of each of
(a), (b), (c), (d) and (g) turns its test red.

Repair round 1 adds three Waiting tests. A delegation with no promise counts
on its due date. A task moved to NEXT does not count. A promise wins over the
due date. Test (a) now holds a pill that changes with `today`.

`lib/pulse.ts` holds the
words, and `reportEmail.ts` holds the status words, the reason words and
the hidden line. `pulse.test.ts` holds them equal to `PILL_LABEL` and to the
chat card. `reportVisuals.test.ts` draws ten panels. The panel names three
focus tasks, and the folded table names each focus task that the body
carries.

### R4 — Stored runs and "what changed" · CUT 2026-09-30 (§9 Q15)

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

### R4b — Act on a suggestion · CUT 2026-09-30 (§9 Q15)

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

### R5a — The rule and the subject, on the server · AGENT-SAFE

**What:** `config.subject` is `{kind: "person", email}` or `{kind: "team", slug}`. `pm_reports.project_id` stays the node scope, and the two combine.

**Rules:**

- `normalise_report_config` checks the shape of the subject only. It runs on every read, so it must never need the database.
- Create, patch, preview and render check the subject against the directory. A person must be an active `app_user` in the reader's organization. A team must be an `org_group` slug in that organization. Any other value gets 422.
- `report_scope.py` holds `may_report_on`, `reportable_people`, `subject_clause` and the row filter. `reports.py` and the analytics routes import it. A copy in a second module is a defect.
- A team expands to its active members at render time. The expansion calls the one helper in `routes/admin/groups.py`.
- `subject_clause` keeps the tasks that a subject person holds now. Each body ANDs it into its scope.
- `outlook` and `hygiene` with a subject get 422 with the reason "not available for a person or team scope".
- The row filter runs after each body, never inside `capacity_body`. It applies to `load`, `capacity`, `pulse` and `parallel_person` conflict rows. It replaces edit E5.
- `rebalance` gets no reader filter, because the HR grant gates it (§7.1 rule 7). A subject narrows its pickups.
- Totals count every person. `hidden_people` counts the people whose rows the filter removed.
- The unassigned row and `agent:` rows are not people, and they stay.
- Dependency conflict rows keep their holders. The reader can open each task and see its assignees.
- The list hides a report whose subject the reader may not report on. Get, patch, delete and render answer 403 with the reason.
- `GET /projects/reports/subjects` returns the people and teams that `may_report_on` allows. The route goes above `/reports/{report_id}`. Its manifest row is class X.
- The `/analytics/load`, `/analytics/capacity` and `/analytics/conflicts` routes call the same body and the same filter as the report. So the Analytics panels change too, and the PR says so.

**Non-goals:** the picker, the entry points, T2, T6, and the org setting.

**Done when** (each on a real database, in `test_projects_report_scope_r5.py`):

- (a) An admin gets 200 for every person and team.
- (b) Lead L of team A gets 200 for team A and for M, a member of that team. The lead gets 403 for team B and for N, a member of that team.
- (c) A member gets 200 for themselves and 403 for a colleague. The 403 names the role that would allow it.
- (d) A person outside the directory gets 422, and so does a slug from another organization.
- (e) On an org report, a member sees their own row in `load`, `capacity` and `pulse`, and `hidden_people` counts the others. Lead L sees the rows of team A and their own row.
- (f) For a restricted reader, the JSON body holds no address outside the allowed set. The one exception is the holders of dependency conflict rows.
- (g) `/analytics/load` for a member returns their own row, the unassigned row and `hidden_people`. `total_tasks` is the same as for an admin.
- (h) A team report lists each member once, and a member removed from the team is gone on the next render.
- (i) A saved report on N is not in L's list after L leaves team B, and its render gets 403.
- (j) A saved row with no `subject` renders exactly as before.
- (k) The panels, the email and the chat card print the hidden line for `load`, `capacity` and `conflicts`.

**As built (2026-09-28).** `report_scope.py` holds the rule, the subject clause and the row filter.
`routes/admin/groups.py` `active_memberships` is the one reader of team membership, and its SQL names the organization.
`load_body` is new in `analytics.py`, so the Load route and the `load` section share one body.
Only a restricted reader receives `hidden_people` in `load`, `capacity` and `conflicts`, so an admin's render is unchanged.

`test_projects_report_scope_r5.py` proves (a) to (k) on a real database.
A mutation of (b), (c), (d), (f), (g), (i) and (j) turns its test red.
The chat's workload answers read the Load, Capacity and Conflicts routes, so a member's chat answers change in the same way.

**As built, repair round 1 (2026-09-28).** The review found five faults, and each has a test in the same file.

- The chat tools `status_report`, `analytics_load` and `team_capacity` print the hidden line. `status_report` prints no "most open work" when the view hides people.
- The Load, Capacity and Conflicts panels print "You can see no rows here" when the server removed every row. They do not say that the scope is empty.
- `hiddenPeopleLine` in `reportEmail.ts` is the one source of the words. A panel says "This view hides", and the email says "This report hides". ⚠️ Since §6.5 item 16, the `pulse` table prints no hidden line, because its panel prints it.
- `hidden_people` counts people in `conflicts` too. Two rows of one person count as one person.
- An admin or the creator can delete a report whose stored subject has a bad shape. Get and render still answer 422.

### R5b — The picker, the entry points, T2 and T6 · AGENT-SAFE

**What:** the builder chooses a subject. T2 and T6 go live, and T1 takes a team. Four controls open the builder. R5b is two PRs, R5b-1 and R5b-2.

**Rules, R5b-1 (the server and the builder):**
- The builder has two chips: "about [subject]" and "in [scope]". R5a lets `config.subject` and `project_id` combine, and one picker cannot hold both. This replaces the one picker of §6.2.
- The subject chip lists "Everyone", then the people, then the teams. It reads `GET /projects/reports/subjects` only. The reader's own row reads "Me". ⚠️ Superseded by §6.5 item 6: "Everyone" has no heading, and the teams come before the people.
- The scope chip stays the project tree and "Whole organization". `scopeOptions` does not change.
- `builderStateFrom`, `configFor`, `createPayload` and `patchPayload` carry `subject`. PATCH replaces the config, so an edit that drops the subject is a defect.
- With a subject set, the builder removes `outlook` and `hygiene`. A template with no `person` or `team` in `scope_kinds` hides the subject chip.
- `my_day` (T2) goes live: `pulse`, `weeks` 1, `skip_current_week` false, `scope_kinds` `["person"]`, `requires_subject` `"self"`.
- `one_on_one` (T6) goes live: `finished`, `throughput`, `pulse`, `weeks` 4, `skip_current_week` true, `scope_kinds` `["person"]`, `requires_subject` `"person"`.
- `team_pulse` (T1) takes `scope_kinds` `["team", "project", "org"]`.
- `normalise_report_config` gives 422 to a T2 or T6 config with no person subject. This rule reads only the shape, so it needs no database.
- A T2 is always about its AUTHOR. Create and preview give 422 to a T2 config whose subject is not the reader. PATCH gives 422 to a T2 config whose subject is not the stored `created_by`, with the reason "My day is always about its author". So an admin can rename a member's T2 (Q13), and the report stays about the member. This check runs before the §7.1 check. A render does not run it.
- The builder derives the T2 subject from the reader only for a new report. An edit keeps the stored subject. The locked chip names the author, with the note "My day is always about its author".
- `pulse_body` does not change. The waiting items show only on the reader's own row (Q6).

**The link contract:** `/projects?app=reports&template=<key>&subject=<kind>:<value>&report_node=<id>`. Each key after `app` is optional. The key is `report_node` and not `project`, because the page consumes `?project=` and closes the app pane. `reportLink` in `lib/reportBuilder.ts` builds each link. A second builder of this address is a defect. `ReportsView` reads the keys once and then removes them from the address.

**Rules, R5b-2 (the entry points):**
- `NodeDashboard` shows "Report on this". It opens the builder with the node as the scope.
- `PersonPanel` in the People app shows "1:1 prep". It opens T6 with the person as the subject.
- "1:1 prep" shows only when the subjects route lists that person. When the read fails, the control is absent. It is never disabled.
- The top bar of My Tasks shows "My day". It opens T2 with the reader as the subject.
- "My day" shows only to a member who can open Projects (`canSeePath` on `/projects`, the check of `AccessGate`). For every other member the control is absent, never disabled, because the link would end in a refusal (repair round 1).

**Non-goals:** the chat (R8), the member setting (R5c), a "today" period, a `waiting` section, and a control on a task's assignee. The assignee chip has no menu, and a new menu is a design choice for a later slice.

**Done when, R5b-1:**
- (a) Vitest: a row with a subject goes through `builderStateFrom` and then `patchPayload`, and the subject stays the same.
- (b) Vitest: `subjectOptions` for a member lists "Everyone" and "Me" only. For a lead it lists the lead, the team members and the teams they lead. For an admin it lists every person and team in the answer.
- (c) Vitest: `configFor` of a state with a subject holds no `outlook` and no `hygiene`.
- (d) Vitest: `reportLink` and its parser keep the template, the subject and the node. An unknown template or a bad subject opens the home screen.
- (e) Real database: create, patch and preview of T2 or T6 with no subject each get 422. A team subject also gets 422.
- (f) Real database: a member creates T2 on themselves and gets 201. An admin creates T2 on another person and gets 422.
- (g) Real database: lead L creates T6 on M and gets 201. L creates T6 on N and gets 403 with the §7.1 reason.
- (h) Real database: T1 with the subject team A renders for L. A member gets 403 for team A.
- (i) The live templates are exactly `team_pulse`, `my_day`, `weekly_delivery`, `project_status`, `one_on_one` and `data_hygiene`.
- (j) Real database: the subject of a saved T2 renders it and sees their waiting items. An admin renders the same row and sees none.
- (n) The subject chip reads as words, for example "About: Me" or "About: Hardware team". The menu has the headings People and Teams, in the order Teams, then People. "Everyone" has no heading. This sentence is superseded by §6.5 item 6. A person shows the name, with the address muted beside it. A menu of more than 8 entries has a filter box. The chip is `SelectButton` from `src/components/ui`.
- (o) Vitest: T2 locks the chip to the reader, with the note "My day is always about you." T6 shows "About: choose a person". Until a person is set, the preview shows one line that says what to do, and no error.
- (p) Vitest: with a subject set, one muted line under the sections says why Forecast and Data hygiene are off. The two checkboxes stay in the list.
- (q) The sentence reads "[Template] About: [subject] In: [scope] Over: [period]". At 390 px each chip has the full width.
- (r) Vitest and review: the subject chip shows a skeleton while it loads. It shows a line when the list is empty, and a retry line when the read fails. A 403 or a 422 shows as one sentence under the chip that caused it.
- (s) A visual review in light mode, at compact density, with a changed accent and at 390 px finds no defect.
- (t) Real database: an admin renames member A's T2 and gets 200. The stored subject stays A, and A still lists and renders it. An admin PATCH that sets the subject to the admin gets 422. A PATCH by the author gets 200. Vitest: `builderSubject` keeps the stored subject of a T2 under edit.
- (u) Real database: a stored T2 row with no subject does not make `GET /projects/reports` answer 422. The list answers 200 without that row, and an admin can delete it.

**Done when, R5b-2:**
- (k) Vitest: the link of each control equals `reportLink` with the expected template, subject and node.
- (l) Vitest: `PersonPanel` shows no "1:1 prep" when the subjects answer omits the person, or when the read fails.
- (m) A review at 390 px shows that each link opens Reports with the builder filled in. If it does, the PR closes H-184 item 5.

**As built, R5b-1 (2026-09-29).** `test_projects_report_scope_r5b.py` proves (e) to (j) on a real database. `reportBuilderSubject.test.ts` proves (a) to (d), (o) and (p).
A mutation of (a), (c), (d), (e), (f) and (g) turns its test red.

⚠️ **Correction, repair round 1.** The first note said the tests proved (r). They did not. No test covered the skeleton, the empty line or the retry line. The (a) test also covered the pure functions and not the component. The component put the reader in as the subject of an edited T2.

- `normalise_report_config` calls `_config_template`, which gives 422 to T2 or T6 with no person subject. `require_template_subject` gives 422 to a T2 whose subject is not the reader. Create, patch and preview call it before the §7.1 check.
- The subjects answer carries `me`, the address of the reader. The chip names that row "Me", and T2 takes it as its subject.
- The builder derives the T2 subject from `me` and never stores a typed value. A template about one person asks the server for no preview until a person is set.
- `SelectButton` has four new optional props: `group` on an option, `prefix`, `prompt` and `filterAbove`. `filterOptions` is the filter, and `SelectButton.test.ts` holds it.
- `ReportsView` reads the link keys once, after the catalogue loads, and removes them from the address.
- `NO_SUBJECT_SECTIONS` in `reportBuilder.ts` mirrors `report_scope.py`. `test_projects_report_sections_lockstep.py` fails when the two differ.

⚠️ **The hazard.** `_report_dict` runs `normalise_report_config` on each read, and the list reads every row. So a stricter shape rule added later can make a saved row answer 422. Put a rule that needs the reader or the database in `require_template_subject`, which a read never calls.

**As built, repair round 1 (2026-09-29).** Each item names its fence.

1. **The T2 author rule.** `require_template_subject` takes `author` on a PATCH, and `update_report` passes the stored `created_by`. The builder calls `builderSubject`, which derives the reader only when `editing` is null. `subjectChipNote` names the author for an admin. Fence: (t), and a mutation that puts the override back turns the vitest red. Before the fix, the real-database test got 422 "must be you" for the admin's rename.
2. **The list hazard.** `list_reports` runs `_report_dict` inside the `try`, so a bad row is left out and the list answers 200. Fence: (u). Before the fix, the list answered 422.
3. **The link node.** `parseReportLink` keeps `report_node` only when it is a UUID in the reader's tree, and `ReportsView` waits for the tree. A link with only a bad node opens the home. Fence: vitest (3), and a mutation that keeps any node turns it red.
4. **The filter query.** `selectPanelReducer` holds the open state and the query together, and every close clears the query. Fence: vitest (4), and a mutation that keeps the query on a trigger close turns it red.
5. **The listbox.** `SelectList` puts the filter box above `role="listbox"`, and each group is `role="group"` with the group name as its label. A list with no filter and no group keeps the old markup, so the callers from before R5b render unchanged. Fence: vitest (5), with a server render of `SelectList`.
6. **The fallback sections.** `withSubject` writes the default sections into `state.sections` when the subject turns off every chosen section. So the checkboxes show what the preview shows. Fence: vitest (6), and a mutation that removes the fallback turns it red.
7. **The "as saved" hint.** `subjectOptions` takes `editing`. A subject from a link that the answer omits reads "not in your list". Fence: vitest (7).
8. **The builder text.** The template name, or "Custom report", is the title above the chips. A T2 does not show "Started from a template". A custom report starts as "Untitled report". When the subjects read fails, the T2 preview line says so and offers Retry. Fence: vitest (8).
9. **(r).** `subjectChipStatus` decides the skeleton, the retry line and the ready chip, and `subjectChipNote` decides the empty line. Vitest (9) pins the pure functions. No test renders the chip, because the vitest environment has no DOM.

**As built, R5b-2 (2026-09-29).** This PR touches the People app and the My Tasks app. `lib/reportEntry.ts` decides the parts of each link, and `reportLink` builds each link. `reportEntry.test.ts` holds the fences for (k) and (l).

- **"Report on this".** `NodeDashboard` takes `onReport`, and both dashboard renders in `page.tsx` pass it. The link names the node and nothing else.
- **"1:1 prep".** `PersonPanel` reads the subjects answer through `useCachedResource`, with the key that the builder reads. `oneOnOneLink` gives `null` when the read fails, before the answer arrives, or when the answer omits the person. Then the control is absent.
- **"My day".** The desktop bar and the phone bar of My Tasks show it. The link names T2 and no subject. The builder makes the reader the subject of a new T2 (`builderSubject`). An address in the link could name another member, and the server refuses that T2.
- **(m).** A visual review at 390 px opened each link. Each one opened Reports with the builder filled in: "About: Me", "About: Ana Shah" and "In: Hardware". So H-184 item 5 was stale, and this PR removes it.
- **The fences.** Five mutations each turn a test red:
  1. A node link with `project=`.
  2. T6 changed to T2.
  3. A failed read that shows "1:1 prep".
  4. An omitted person that shows "1:1 prep".
  5. A `PersonPanel` that ignores the read error.

**As built, the review repair (2026-09-29).** Each fix has a vitest that was red before the fix.

- `scopePrompt` and `scopeChoices` take `editing`. Fence: `reportBuilderUx.test.ts` (2).
- `hiddenTeamHint` takes `chipShown`. Fence: `reportBuilderUx.test.ts` (16).
- `SelectOption` gains `keywords`, which `filterOptions` matches and the list never draws. `subjectOptions` puts each address there. Fences: `SelectButton.test.ts` and `reportBuilderUx.test.ts` (6).
- `myDayShown` in `reportEntry.ts` decides "My day". Fence: `reportEntry.test.ts`.
- `renderAsOf` in `reportEmail.ts` decides "As of [day]" for the email and the file. Fences: `reportEmail.test.ts` and `reportFiles.test.ts`.
- `homeEmptyLine` holds the one empty line of Home. Fence: `reportsUx.test.ts` (11).

### R5c — The member setting · BLOCKED
`reports.members_see_own_team` waits for an organization-scoped settings store. `org_settings` has no `organization_id` today, so one key would change every organization. The rule without the setting is the strict rule, so R5a and R5b do not need it. The owner chose to wait for the WS-29 fix (§9, Q8).

### R5d — The role rule in the chat, and who may change a report · AGENT-SAFE

**What:** two server changes and their words in the chat and the UI. The dataset read obeys §7.1 rule 3 (Q10). The delete route obeys Q12. A change to a report obeys the same rule (Q13).

**Rules, the dataset** (`analytics_dataset.py`, the chat tool `task_dataset`):
- The route calls `reader_scope` once, in the tenant session of the request. An admin gets `None`, and the answer for an admin does not change.
- With `group_by=assignee`, the groups of people outside the allowed set go. The unassigned group and the `agent:` groups stay.
- `filter_person_rows` with `key="key"` removes them. A second filter in another module is a defect.
- The filter runs before the cap of `MAX_GROUPS`. The SQL limit must never remove an allowed person.
- `total` still counts every task. `groups_total` and `groups_truncated` count the groups that the reader receives.
- `hidden_people` counts the people whose groups went. Only a restricted reader receives it, as in R5a.
- An `assignee` or `assignees` filter that names a person outside the allowed set gets 403. The reason comes from `ReaderScope.allows`. This binds rows and groups, because `total` is then a count for that person.
- A row keeps its `assignees` column. The reader can open each task and see its assignees. This is the R5a rule for dependency conflict rows.
- The HR gate of O3 and the K rule (`MIN_GROUP_PEOPLE`) stay unchanged.
- `task_dataset` prints the hidden line through `hidden_people_line(payload, "view")`.

**Rules, the delete** (`reports.py` `delete_report`):
- A report on a node that the reader cannot see still gets 404.
- The author of the report may delete it. An admin (`can_read_hr_fields`) may delete it too.
- Every other reader gets 403 with the reason "Only the author of this report or an admin may delete it."
- The author may delete their report when the subject check refuses them. So a lead who left a team can still remove their own report.
- `_report_dict` carries `can_delete`, which the server computes, as it computes `mine`.
- The chat tool `report_delete` reads `can_delete` before the card. When the value is false, the tool gives the reason and shows no card.

**Rules, a change** (`reports.py`, §9 Q13, added 2026-09-28):
- The rule of the delete also binds `PATCH /reports/{id}`, the recipient add and remove routes, and `PATCH /reports/{id}/schedule`.
- The order is the same. A report on a node that the reader cannot see still gets 404. Then the author or an admin passes.
- Every other reader gets 403 with the reason "Only the author of this report or an admin may change it."
- The author path skips the subject check of the report, as the delete does. A new subject in a PATCH still passes `require_subject`.
- One function, `_may_change`, holds the rule for a delete and a change. A second copy of the rule is a defect.
- `_report_dict` carries `can_edit` beside `can_delete`. Both come from `_may_change`, so the two values are always equal.
- The chat tool `report_save` reads `can_edit` before the card of a change. When the value is false, the tool gives the reason and shows no card.
- The Reports app shows Edit only when `can_edit` is true. The control is absent, never disabled, as in `people_center_app.md` §3.2.
- A read does not change. Any reader who may open the report can still get it, render it and list its recipients.

**Out of R5d, with the reason:**
- `rebalance`, `fit_for_task` and `propose_plan`. The HR grant gates their per-person figures, and only the admin row holds it.
- `analytics_load`, `team_capacity`, `find_conflicts`, `status_report` and `render_report`. R5a filters them.
- `people_for`. Its load and skills are HR tier, and its names are directory tier.
- `list_tasks` and `find_tasks`. They list tasks that the reader can open.
- A count that the model makes over the rows. This limit is advisory, as O1 of `projects_ai_chat.md` §13.7 says.

**Non-goals:** `created_by` (Q11). Any UI change other than Edit.

**Done when** (each on a real database, in `test_projects_report_scope_r5.py`, which reuses the `seeded` fixture):
- (l) Member M reads the dataset with `group_by=assignee`. The groups hold M, the unassigned group and the `agent:` groups only. `hidden_people` counts the others. `total` equals the total for an admin.
- (m) An admin makes the same read. It holds a group for each person and no `hidden_people` key.
- (n) Lead L makes the same read. It holds the groups of L and team A, and no group from team B.
- (o) M reads with `assignee` set to N and gets 403. The reason names the role that would allow it. This holds with `group_by` and without it. L reads with `assignee` set to M and gets 200.
- (p) M reads rows with the `assignees` column. Each row keeps its assignees. The row count equals the admin's for the same filters.
- (q) With `MAX_GROUPS` set to 2 by the test, an allowed person who ranks third still receives a group.
- (r) The author deletes a report and gets 204. An admin deletes the report of another person and gets 204.
- (s) A member who can open the report of another person gets 403 with the reason. The row still exists.
- (t) L saves a report on N and then leaves team B. L deletes it and gets 204.
- (u) `report_delete` with `can_delete` false returns the reason. It never calls the card or DELETE. A fake gateway counts the calls.
- (v) If the dataset filter is removed, (l) turns red. If the delete check is removed, (s) turns red.
- (w) `report_save` with `report_id` and `can_edit` false returns the reason. It never calls the card or PATCH. A fake gateway counts the calls.
- (x) The author and an admin each get 200 from the PATCH, the recipient add and remove, and the schedule. `can_edit` is true for each.
- (y) A member who can open the report of another person gets 403 with the reason from each change route. The row, its recipients and its schedule do not change. The member can still render the report.
- (z) L saves a report on N and then leaves team B. L changes it, its recipients and its schedule, and gets 200 each time. The render still gets 403.
- (aa) A report on a node that the reader cannot see gets 404 from each change route, before the author path.
- (ab) The Reports app shows Edit to the author and to an admin, and shows no Edit to any other member. A vitest proves it.
- (ac) A mutation that removes the change check from the PATCH turns (y) red.

**As built (2026-09-28).** `analytics_dataset.py` reads `reader_scope` once in the route. `assignee_refusal` gives the 403, and `dataset_body` takes `allowed`.
For a restricted reader grouped by assignee, the SQL takes no limit, and `filter_person_rows` runs before the cap.

`reports.py` holds `DELETE_REFUSED`. The 2026-09-28 change below renamed `_may_delete` to `_may_change`.
`_visible_report` with `refusal` runs the project 404 and then the author-or-admin rule. It skips the subject check.

The delete rule also replaces the old 422 for a stored subject with a bad shape. Any other reader now gets the 403 of the delete rule.
Test (i) no longer expects 403 from the delete, because (t) replaces that half.

`test_projects_report_scope_r5.py` proves (l) to (t) on a real database, and `test_projects_agent_writes.py` proves (u).
Six mutations each turn a test red (v). They remove the filter, its 403, the author path, the delete check or the chat check, or they put back the SQL limit.

**As built, the change rule (2026-09-28, §9 Q13).** `reports.py` holds `CHANGE_REFUSED` and one function, `_may_change`, for the delete and for a change.
`_visible_report` takes `refusal`. The delete passes `DELETE_REFUSED`. `update_report`, `add_recipient`, `remove_recipient` and `set_schedule` pass `CHANGE_REFUSED`.

`_report_dict` sets `can_edit` and `can_delete` from one call to `_may_change`. The two flags keep the name of each act for the client that offers it.

`writes.py` holds `REPORT_CHANGE_REFUSED`, and `_report_change` is the update path of `report_save`. It reads `can_edit` before the card.
`ReportsView.tsx` exports `ReportActions`, which renders Edit only when `can_edit` is true. `reportActions.test.ts` proves (ab).

`test_projects_report_scope_r5.py` proves (w) to (aa) on a real database, and `test_projects_agent_writes.py` proves (w) for the chat.
Six mutations each turn a test red (ac). Three change the server: no PATCH check, a pass for every change, or no author path for a change.
Three change a client: `can_edit` always true, no chat check, or Edit always.

### R5f — One Reports app · AGENT-SAFE · BUILT 2026-09-30

**What:** the Analytics app joins the Reports app (§9 Q14). Reports opens on Overview. Overview is the builder with no name field and no Save. It starts with the seven sections that the Analytics app showed, for the whole organization. The gallery and the saved reports come under it. "Save as report" opens the builder with the same choices.

**Rules:**
1. `PROJECT_APP_SECTIONS` holds no `analytics` entry, and `ProjectAppId` loses `"analytics"`. The Reports entry keeps its id, its icon and `live`.
2. Overview renders through `POST /projects/reports/preview`, so `render_body` computes every section. Overview adds no second fetch of a section.
3. `overviewState()` in `lib/reportBuilder.ts` is the start of Overview. It holds the whole organization, no subject, `weeks` 12, `skip_current_week` false and the subtree.
4. Its sections are `finished`, `throughput`, `outlook`, `load`, `capacity`, `stuck` and `conflicts`. The Analytics app drew these seven with these defaults, so Overview shows the same numbers.
5. `PERIODS` gains "The last 12 weeks", with `weeks` 12 and `skip_current_week` false. So the period chip names the Overview period, and a saved report keeps it.
6. The KPI strip and the space table move out of `AnalyticsView.tsx` into one component. Overview draws them above the sections. Do not copy them.
7. The strip and the table read `GET /projects/summary` for the whole organization, and `GET /projects/nodes/{id}/summary` for a node.
8. They show only when the subject is "Everyone" and `include_subtree` is true. The summary routes take no subject, so their counts would disagree with the sections.
9. In Overview the strip has no Overdue tile. The report tiles show Overdue from `stuck`, and one screen shows one Overdue tile.
10. "Save as report" opens the builder with `saveAsReportState(state)`. It keeps the scope, the subject, the period, the subtree and the sections.
11. The template of that state is `null`, and `builderName` names it. Overview itself writes no row.
12. `resolveAppLink` in `lib/projectApps.ts` maps `analytics` to `reports`. The `?app=` link and the `projects.open_app` handler both call it. A second map is a defect.
13. The chat tool `open_in_app` accepts `analytics` and opens `reports`. Its link is `/projects?app=reports`, and its docstring names Reports only.
14. `OPENS_APP` in `ProjectToolCards.tsx` sends each analytics read to `reports`, with the label "Open Reports".
15. Each panel in `RenderedBody` is the only child of its own `LayoutBoundary`. The Analytics app had this rule (WS-27bm S12), and Overview must keep it.
16. The saved-reports rail is absent on Overview. It shows when a member opens a saved report.
17. The code names the mode `reportsOverview`. `page.tsx` already uses `overview` for the overview of a project.

**Non-goals:**
- No server change, except the pass-through of repair round 1 (below). If the slice needs another, stop and change this spec first.
- `NodeDashboard` does not change. Each dashboard keeps its own reads.
- The analytics routes stay. The dashboards and the chat read them.
- No new section and no new template. Overview is not a template, so the gallery does not list it.
- `pulse`, `hygiene` and `rebalance` start off in Overview. A reader turns them on in "More".
- No link to an Overview state. A link opens the builder, as R5b says.

**Done when:**
- (a) Vitest: `PROJECT_APP_SECTIONS` holds no `analytics` entry, and the Reports entry is `live`.
- (b) Vitest: `resolveAppLink` maps `analytics` and `reports` to `reports`. It maps a preview id and an unknown id to `null`.
- (c) Vitest: `overviewState()` holds no project, no subject, `weeks` 12, `skip_current_week` false and the seven sections in `SECTIONS` order. Its `periodKey` is `last_12_weeks`.
- (d) Vitest: `saveAsReportState` keeps the scope, the subject, the period, the subtree and the sections. Its template is `null`, and `nameTouched` is false.
- (e) Vitest: `overviewTableShown` is true for "Everyone" with the subtree, and false with a subject or without the subtree.
- (f) Vitest: `OPENS_APP` holds no `analytics` value. Pytest: `open_in_app` with `analytics` sends `reports`, and its link is `/projects?app=reports`.
- (g) Real database: for one reader and one scope, the preview of `overviewState()` equals the analytics routes. The throughput series and the finished total are the same.
- (h) Real database: a member previews `overviewState()`. The `load` rows hold the member only, and `hidden_people` counts the others (§7.1 rule 3).
- (i) `layoutBoundary.test.ts` finds each panel of `RenderedBody` in its own boundary. A mutation that removes one boundary turns it red.
- (j) A visual review in light mode, at compact density, with a changed accent and at 390 px finds no defect. Do it as an admin, a lead and a member.

**As built (2026-09-30).** Each rule has its fence. A mutation of each fence turns it red.

- `projectApps.ts` holds `resolveAppLink` and `APP_ALIASES`. The page passes its own resolved list, so the flagged chat entry still opens. Fences: `projectApps.test.ts` (a), (b) and rule 12.
- `reportBuilder.ts` holds `OVERVIEW_SECTIONS`, `overviewState`, `saveAsReportState` and `overviewTableShown`. Fence: `reportsOverview.test.ts` (c) to (e).
- `ReportBuilder` takes `mode="reportsOverview"`. Overview has no name field and no Save. "Save as report" opens the builder, and the builder names the report "Untitled report".
- `SpaceSummary.tsx` holds the KPI strip and the space table. For a node it adds the "Direct work" row, so the rows add up to the footer. `AnalyticsView.tsx` is deleted.
- `RenderedBody` takes `lead`, and each of its ten panels is the only child of its own `LayoutBoundary`. Fence: `layoutBoundary.test.ts` (i).
- `ReportsView` takes `onOpenNode` and no `finished`. The Home line takes its count from the Overview preview. The gallery is now above "Your reports", so the line says "Pick a question above".
- `OPENS_APP` is exported, and each value is `reports`. `nav.py` holds `APP_ALIASES`. Fences: `ProjectToolCards.test.ts` and `test_projects_agent.py` (f).
- `test_projects_report_overview_r5f.py` proves (g) and (h) on a real database. (g) runs for one project and for the whole organization. A wrong period and a missing row filter each turn it red.
- (j) ran on 2026-09-30 with mocked routes, as an admin, a lead and a member. It found one defect: the pane repeated the page subtitle. The pane now has no second subtitle.

⚠️ **Two differences from the Analytics app stay.** Repair round 1 removed the other two (the blocked list and the 20-row cap).
1. The Overdue column of the space table comes from `/summary`, and the Overdue tile comes from `stuck`. The two use different open-work rules, so they can differ.
2. `RenderedBody` draws Overdue before Open work. That order is older than R5f, and it is not the `SECTIONS` order.

**First paint (2026-09-30).** Three measurements:
- The server answers the Overview preview in 345 to 416 ms on the seeded scratch database.
- One section alone takes 190 to 270 ms. Each request pays that cost once.
- In `next dev` with a mocked server, the first panel draws 2900 to 3800 ms after navigation. A server delay of 1500 ms adds about 1000 ms.

The first preview asks at once. Only a later change waits for the delay.

#### R5f repair round 1 (2026-09-30) · BUILT 2026-09-30

The review of R5f found lost figures. The owner also asked for a calmer builder (§6.6). This round does both.

**Rules, the server pass-through.** `render_body` calls the functions of the routes and adds no arithmetic.
1. The `load` section carries `effort` and `people_total` from `load_body`. Each row carries `est_mins` and `estimated`. The rows keep the cap of 20, because the Load route caps them too.
2. Each week of the `throughput` section carries `cancelled`, `measured`, `no_start`, `median_hours` and `p90_hours`. The section carries `completed`, as the route summary does.
3. The `stuck` section carries `blocked` and `blocked_total`. `blocked_body` in `analytics.py` is the one read, and the route and the section both call it.
4. `capacity` and `conflicts` keep every row, as their routes do. The email cuts each list to `MAX_EMAIL_ROWS` in `reportEmail.ts`, so this adds no length to an email. The download keeps every row, because a file is the whole report. Round 2 corrected this rule, which said that the download cut its lists too.

**Rules, the client.**
5. The adapters in `reportPanels.ts` copy the new fields. An absent field stays absent.
6. Home keeps the Overview state when a member opens the builder or a saved report and comes back. Overview asks the server again only when a choice changed.
7. A builder link (`?template=`, `?subject=` or `?report_node=`) decides the pane before the first preview. Overview does not render first, and it sends no preview.
8. The builder follows §6.6.

**Done when:**
- (k) Real database: test (g) compares each section with its route row for row. It seeds 23 people, so a list of more than 20 rows shows a cap. The load effort, the throughput weeks and the blocked list are equal.
- (l) Vitest: each adapter copies the new fields, and the panel draws the effort line, the blocked list and the Finished cell.
- (m) Vitest: a return to Home keeps the Overview state and asks for no second preview.
- (n) Vitest: a builder link opens the builder, and Overview sends no preview.
- (o) Vitest: the header row, the section tiles, the all-clear state and the table toggle follow §6.6.
- (p) A mutation of each new test turns it red.
- (q) A visual review finds no defect. It covers Team pulse (all clear and with data), Project status, My day and Overview. Each screen is seen in light, dark, compact, a changed accent and 390 px.

**As built, round 1 (2026-09-30).** Each item names its fence. A mutation of each fence turns it red.

- `blocked_body` in `analytics.py` holds the blocked read. `render_body` passes `effort`, `people_total`, the row estimates, the weekly figures and `completed` through, and it keeps every row of `capacity` and `conflicts`. Fence: `test_projects_report_overview_r5f.py` (k), with six mutations.
- `reportPanels.ts` copies the new fields. Fence: `reportPassThrough.test.ts` (l).
- `previewNeeded` and `reportsPane` in `reportBuilder.ts` hold rules 6 and 7. `ReportsView` keeps `overviewDraft` and `overviewPreview`. Fence: `reportsRedesign.test.ts` (m) and (n).
- `lib/sectionIcons.ts` and `lib/sectionEmpty.ts` hold the icons and the clear lines. `reportEmail.ts` adds the same clear line as a note. Fence: `reportsRedesign.test.ts` (o).
- `PanelChromeContext` in `AnalyticsPanels.tsx` puts the section icon and the table button in the card header. A panel outside a report draws as before.
- `PANEL_HINTS` is short. Round 2 removed `PANEL_HINT_DETAILS`, because the tile's tooltip is now the short line.
- The review found five defects, and this round fixes each one:
  1. The tiles overflowed the card, because a fieldset keeps a min-content width.
  2. The hints were cut off.
  3. The title was cut off at 390 px.
  4. A table showed a "2 rows" line.
  5. The rebalance line said "nobody is idle" while one person was idle. `idleLine` now names the idle people.
- The shared panels also changed the empty lines on `NodeDashboard`. For example, "No open work in this scope." is now "Nobody holds open work here right now." The look of the dashboard did not change.

#### R5f repair round 2 (2026-09-30) · BUILT 2026-09-30

The review and the verifier found two defects, two weak fences and seven UX items. The owner's words bind this round too: "easier to use and non-threatening".

**Rules:**
1. A builder link never hangs. `linkStep` in `reportBuilder.ts` drops the link when the catalogue read fails, and Home shows the error.
2. `conflicts` is not clear when `hr_visible` is false. The server holds four kinds back, so the panel says "No conflicts in the kinds you can see."
3. The email prints the blocked list and `blocked_total` of `stuck`. A stuck section with blocked work and nothing overdue still prints.
4. Each of the ten sections gets its clear line in the email from `sectionEmpty.ts`, as in the app.
5. The email says "…and N more" under each list that it cuts. It says the same under the Load rows that the server cut.
6. `hrHints.ts` holds the three lines that replace the words "HR read access". The panels, the email and the chat read them.
7. Each section has one name (§6.6 C item 6).
8. A closed table carries `hidden="until-found"`, which an effect sets through a ref. React 19 writes `hidden` as a boolean.
9. The "Change what you see" toggle has `aria-controls`.

**Done when:**
- (r) Vitest: `linkStep` drops a link when the catalogue fails.
- (s) Vitest: conflicts without the grant are not clear, and the email does not say "No conflicts" beside the HR line.
- (t) Real database: the crowd seed blocks four tasks, and a cut of the blocked list turns test (g) red.
- (u) Vitest: `initialShownKey` seeds the key on screen, and `shownKey = useRef(null)` turns a test red.
- (v) Vitest: the email checks of rules 3 to 6, one test for each of the ten sections.
- (w) Vitest: the one-line tile, the last tile, the group labels, the all-clear rows on a phone, find-in-page and `aria-controls`.
- (x) A visual review finds no defect on four screens. They are Team pulse all clear at 1440 and 390 px, and Team pulse with data under a changed accent. The fourth is Overview for a member without the grant.

**As built, round 2 (2026-09-30).** `reportsRedesign.test.ts` and `reportEmailRound2.test.ts` hold the fences. A mutation of each new fence turns it red: 13 in the client and 1 on the real database.

### R5g — The live Overview, full page · AGENT-SAFE · BUILT 2026-09-30

**What:** the owner's directive of §9 Q17. Overview becomes a full-page dashboard with one toolbar, Filters in place and a live refresh (§6.7). The slice changes the client only.

**Rules:**
1. `RenderedBody` takes `layout`. Overview sends `grid`, and each other caller keeps `column`. `gridSpans` in `lib/overviewLive.ts` picks the wide panels. `BODY_ORDER` is the draw order of `RenderedBody`.
2. `OverviewHeader` is the toolbar. The Filters button carries `aria-expanded` and `aria-controls`. The Refresh button has the name "Refresh". `overviewFilterCount` gives the count.
3. `OverviewFilters` holds the builder's own controls. The builder's steps and the Filters use the same nodes, state and handlers, so the logic has one copy. `SectionTile` takes `variant="chip"`.
4. `readFiltersOpen` and `writeFiltersOpen` put each storage call in a `try`. The first render is closed, and an effect reads the stored state.
5. `refreshOnVisible`, `intervalRuns` and `LIVE_INTERVAL_MS` hold the refresh rule. `answerIsCurrent` guards each answer: the first preview, a change and a refresh. A late answer never overwrites a newer one.
6. A refresh sends the key on screen, through `previewRequest`. The key does not change, so `previewNeeded` and `initialShownKey` keep the rule of Home.
7. The effect clears both timers and the visibility listener on unmount.
8. Home keeps the time of the last answer with the preview. So "Updated …" stays true after a return.

**Non-goals:**
- No server change and no new route.
- No new section and no new template.
- The saved report and the builder keep their layout.
- No link to a Filters state.

**Done when:**
- (y) Vitest: the Overview body is a grid with one column below `xl` and two at `xl`, and it has no reading measure. A wide panel spans both columns. No row holds one narrow panel alone.
- (z) Vitest: the default body keeps `max-w-3xl`. The saved report and the builder send no grid.
- (aa) Vitest: the toolbar names Filters, Refresh and "Save as report". It holds no menu and no dialog, and Filters has `aria-expanded` and `aria-controls`.
- (ab) Vitest: Filters is closed by default. A failed storage read gives closed, and a failed write does not throw.
- (ac) Vitest: the count badge is absent at the defaults, and it counts each changed choice once.
- (ad) Vitest: the refresh rule. It waits 60 seconds on a return and 5 minutes on the timer. A hidden tab pauses it, a refresh keeps the key, and a late answer drops.
- (ae) Vitest: a chip keeps `aria-pressed` and the blocked reason. The last chip is `aria-disabled` and says why.
- (af) A mutation of each new fence turns it red.
- (ag) A visual review finds no defect. It covers Filters closed and open, in light, dark, compact, a violet accent and 390 px. It covers an admin and a member without the HR grant.

**As built (2026-09-30).** Each rule has its fence. A mutation of each fence turns it red: 27 mutations, 27 red.

- `lib/overviewLive.ts` holds the pure rules. Fence: `overviewLive.test.ts` (y), (ab), (ac) and (ad).
- `ReportsView.tsx` holds `OverviewHeader`, `OverviewFilters`, the chip variant of `SectionTile` and the `grid` layout of `RenderedBody`. Fence: `reportsLiveOverview.test.ts` (y), (z), (aa), (ae) and the refresh wiring.
- The builder puts its controls into three nodes (`workControl`, `timeControl` and `sectionToggles`). The builder's steps and the Overview Filters both use them.
- The template strip left Overview. The gallery under the dashboard has the same heading and the same live templates.
- (ag) ran on 2026-09-30 with mocked routes, as an admin and as a member without the HR grant. It found no defect in the new layout. The 390 px space table scrolls sideways, as it did before R5g.

⚠️ `OrgBrandLockup.tsx` reads `window.localStorage` with no `try`. When a browser blocks storage, the sidebar throws before Overview renders. R5g did not change that file. So the in-browser check of a blocked storage could not run, and (ab) is a unit fence only.

#### R5g repair round 1 (2026-09-30) · BUILT 2026-09-30

The reviewer approved R5g with three small items. The verifier failed it, because five fences did not catch a break and two added lines broke the STE rules. This round fixes each item.

**Rules:**
1. The two cards of a grid row have one height. The grid has no `items-start`, and `PanelChrome.fill` gives the card `h-full`. A clear row takes a full row, so a one-line row never stretches beside a chart.
2. Refresh is never disabled while it loads, so it keeps the focus. It shows `aria-busy` and a spinner. The controller ignores a second click. The Refresh spinner does not show a change of choices.
3. `createPreviewController` in `lib/overviewLive.ts` owns each request: the first preview, a change and a refresh. One guard protects each answer and each error.
4. A refresh does nothing while a change is in flight or waits for its delay. So a timer never drops the answer to a filter. A change supersedes a refresh, and the spinner then stops.
5. A failed refresh keeps the body, stops the spinner and dims nothing.
6. `startLive` refreshes on mount when the kept answer is more than 60 seconds old. So a return from a saved report refreshes an old Overview.
7. `UpdatedAgo` holds the 30-second clock of "Updated …". So the clock renders that text only, and not the panels.
8. `startTicker` takes a `LiveTimerEnv`: the clock, the timers and the visibility. The tests use a fake one.

**Done when:**
- (ah) Vitest drives the wiring with a fake clock, fake timers and a fake visibility. A hidden tab runs nothing and holds no timer. Unmount clears the timer and the listener.
- (ai) Vitest: a refresh keeps the key and the body. It writes the same key on screen and to Home, and it never dims the body.
- (aj) Vitest: a tick during a filter request asks nothing, and the filter answer lands. A late answer or error of an older request drops.
- (ak) Vitest: items 1, 2, 4 and 5 of the rules above.
- (al) Each surviving mutation of the verifier turns red: M2b, M2c, M2d, M6 and M7.
- (am) New captures of the admin and the member show no defect.

**As built, round 1 (2026-09-30).** `overviewLiveWiring.test.ts` holds (ah) to (aj). `reportsLiveOverview.test.ts` holds the markup of items 1, 2 and 5. 26 mutations of this round each turn a test red, and the verifier's five are among them. The captures are the admin in dark and light at 1440, the member at 390 px, and a clear row in the grid.

### R6 — The AI summary, on request · CUT 2026-09-30 (§9 Q15)

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

### R8 — The chat: list, create, render and email · SPLIT 2026-09-30: R-final built the render by name, and `send_report` moved to Phase 2 (§9 Q16)

**What:** the controls of §6.3, and four chat tools over the report routes.

| Tool | Route | Class |
|---|---|---|
| `report_list` | `GET /projects/reports` | A — exists |
| `report_save` | `POST` and `PATCH /projects/reports` | B — exists. It gains `template` and `subject`. |
| `render_report` | `GET /projects/reports/{id}/render`, or the preview of R1 | A — exists. It gains a template name and a subject, so "the morning report for Design" works without a saved report. R8 maps `POST /projects/reports/preview` to this tool and changes that row from class X to class A. |
| ~~`summarize_report`~~ | CUT with R6 (§9 Q15). The chat summarizes the output of `render_report` itself. | — |
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

### R9 — Email this report, on request · MOVED TO PHASE 2 on 2026-09-30 (§9 Q16)

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
- ~~A send writes a stored run (R4)~~ CUT with R4 (§9 Q15). The email links
  to the live report. The original rule read: a send writes a run so "Open in Metorite"
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

**Phase 1, on request, COMPLETE 2026-09-30:** R1 → R2 → R2b → R3 (R3a →
R3b → R3c → R3d) → R5 → R-final. Inside R5, R5f comes after R5b. The
original order ended R4 → R4b → R6 → R8 → R9. §9 Q15 and Q16 cut or moved
those slices.

- R1 and R2 give a member control of what exists.
- R2b draws each section as a chart or a progress bar.
- R3 makes the morning report real.
- R5 adds the person and team scope and the permission rule.
- R-final lets the chat render a report by name.

### R-final — The chat renders a report by name (owner, 2026-09-30) · BUILT 2026-09-30

The owner asked to conclude the feature and keep only what is necessary
(§9 Q15). This slice replaces the rest of Phase 1.

**What:** the chat tool `render_report` (`skill_projects/views.py:render_report`) takes a template key and a
subject, and `report_id` becomes optional. So "team pulse for Design"
works with no saved report. With no id, the tool calls
`POST /projects/reports/preview`, so the server runs
`require_template_subject` and the §7.1 check of `render_body`.

**Manifest (`skill_projects/manifest.py`), in the same PR:**
- The preview row changes from class X (`_PREVIEW_REASON`) to
  `render_report`, class A. It stays in `READ_ONLY_POSTS`.
- `GET /projects/reports/subjects` maps to `render_report`, class A, so the
  tool can resolve "Design" to a subject. That route lists only what the
  reader may report on.
- `GET /projects/reports/templates` maps to `render_report`, class A, if
  the tool resolves a template by name.
- Every `_DELIVERY_REASON` row stays unchanged. *(Superseded for the four routes by WS-64 OP-6d, 2026-10-24: they now have chat tools, and the send switch stays the owner's.)*

**Cut, and why (§9 Q15, Q16):**
- **R4 and R4b.** Stored runs, "what changed", the change arrows and the
  Assign and Dismiss controls were additions, not owner requirements.
- **R6.** The chat already reads a rendered report and summarizes it on
  request, with the credits of the member who asks.
- **R8's `send_report` and R9** move to Phase 2. The gateway has no system
  mail sender, and `sendReportEmail` lives in the Next tier with no caller.
  Phase 2 needs the same seam for the timed email, so the seam gets built
  once, there. Until then, a member downloads a report and sends the file.
- **R5c** waits for WS-29 and leaves this feature.

**Done when:**
- The chat renders T1 for a team by name, with no saved report.
- A member who names a team they do not lead gets the 403 reason, not a
  render. A test proves this.
- `test_projects_chat_coverage.py` passes with the new map.

**As built (2026-09-30).** `render_report` reads `/templates` and
`/subjects`. It matches a key, a slug, an address or a name with no regard
to case. With no exact match, a first name finds a full name, so "Asha"
finds "Asha Rao". When two entries match, the tool lists them and renders
nothing.

A name that matches none of the reader's subjects goes to the preview as
it is. The server then gives its 422 or its 403 reason.

A saved report and a preview use one formatter, `_draw_report`.
`test_projects_report_chat_rfinal.py` proves the done-when on a real
database. Five mutations each turn a test red. They break the §7.1 team
rule, the fallback to the server, the case rule, the choice list and the
first-name match.

---

### RA-1 — Who did it: people, agents and agent-assisted · AGENT-SAFE · BUILT 2026-10-25, not merged

**What:** one server rule in `analytics.py` that labels each done task as agent-finished, agent-assisted or person, and each open task's live holder (§6.8 F). Every later RA slice reads this rule. Nothing reads `meta.via` twice.

**Done when:** a pure test pins each label, and an R8 test pins the SQL on a real ladder. No panel changes yet.

**Built.** The rule has two twins in `analytics.py`. The SQL twin is `finish_actor_join` with `finish_label_sql`, and `holder_label_sql`. The Python twin is `classify_finish` and `classify_holder`.

1. The done label reads the latest move into the done category AT OR BEFORE the finish that the window counts (`cyc.finished_at` of `cycle_cte_sql`). So a saved report keeps its label when somebody reopens the task later. A move between two done lanes is not a move into done. A tie of time goes to the higher activity id, in both twins.
2. `agent_assisted` reads `agent_worked.AGENT_WORKED_SQL` itself, through `agent_worked_sql(task, before)`, with the time of that done move. A run of any status counts, a queued run that never started too, and so does an act through MCP. Work at or after the done move does not count. So a task that GitHub moved to done after an agent worked it through MCP only is `agent_assisted`. A task with no move into done is `person`.
3. An agent holds an open task when a live run keeps it (`NOT filters.unheld_clause`) or when an assignee is `agent:<name>`. Else a person holds it when it has an assignee that is not blank. Else nobody holds it.
4. `actor_split` (`agent_finished`, `agent_assisted`, `person`) is on `GET /projects/analytics/throughput`, `GET /projects/analytics/finished` and the `finished` and `throughput` report sections. Its counts add up to the `completed` figure of the same window. A render runs the split query once for the two sections.
5. `holder_split` (`agent`, `person`, `none`) is on `GET /projects/analytics/load` and the `load` report section. Its counts add up to `total_tasks`.
6. **`none` is not "Needs an owner" (RA-6).** `none` reads the lease, so a run whose lease ended no longer holds its task. "Needs an owner" reads every open run, with no regard to the lease. So each task that needs an owner is in `none`. `none` also holds an unassigned task whose open run lost its lease. That task has an owner that went quiet, and Needs attention shows it. The two rules share one assignee text, `any_assignee_sql`.

Fences: `tests/unit/test_projects_analytics_actor.py` (hermetic) and `tests/unit/test_projects_analytics_actor_r8.py` (R8).

### RA-2 — The KPI strip and "Who did the work" · AGENT-SAFE · BUILT 2026-10-25, not merged

**What:** the six tiles of §6.8 B, and weekly finishes stacked by people and agents, with each agent under its member. "How long it took" splits its median and p90 by people and agents. "What we finished" stays for a scope of more than one project.

**Done when:** `visual-review` passes at four widths, in light mode and at compact density, and each tile's rule is in its tooltip.

**Built.** The server half lives in `routes/projects/analytics_agents.py`. It joins rules that exist, and §5 holds each new word.

1. **Agents and people.** Agents holds `agent_finished` AND `agent_assisted`. §6.8 B asks if agent tasks are faster end to end. Merge-to-Done gives `agent_assisted` to each task that it moves, so that label is agent work.
2. **The `finished` section** gains `actor_series`, each week of the window by label. The weeks add up to `actor_split`. It gains `agent_members` too: each agent finish under its member, by the rule of `analytics_waiting.agent_of`. The members sort by name, never by count (E1). §7.1 rule 3 hides the rows of other people from a reader who is not an admin, and `hidden_people` counts them.
3. **The `throughput` section** gains `actor_cycle`, the median and the p90 of each group, over the `cyc` hours of Throughput.
4. **Summary first (E5).** Both sections gain `actor_summary`. The server writes the sentence, and the panel prints it first.
5. **The `waiting` section** gains `waiting_total` and `oldest_hours`, from its own three lists. A task that waits in two ways counts two times.
6. **The KPI strip.** `POST /projects/reports/preview` with `kpis: true` adds a `kpis` block. Only the Overview sends it, so a saved report, the email and the chat run none of its queries. The block holds the runs now, the merge rate of 14 days, the run time and the cost.
7. **The six tiles.** Finished this week reads the last week of `actor_series`. Median cycle time reads `actor_cycle`. Waiting on a person reads `waiting_total` and `oldest_hours`. The other three read `kpis`. Each tile names its split of people and agents. Its rule is its tooltip, and an `InfoTip` beside its label gives the same rule to a keyboard and a touch.
8. **The silent rule.** §5 says 15 minutes, and the tile uses 15. `SILENT_RUN_AFTER_S` is the one constant, and `analytics_agents.py` owns it. The 5-minute idle state of the pill (`filters.AGENT_RUN_STALE_AFTER_S`) stays as it was.
9. **Cost.** SW-20 captures no usage yet. So the tile reads "Unknown", never $0, beside "0% of finished tasks have cost data". The measured fallback is the agent time of §5, with the waits on a person taken out (§6.8 D item 3). `basis` is `agent_time_less_waits`.
10. **"Who did the work"** replaces "What we finished" in the live Overview when the scope is a node and its finished work sits in one project or none. The organization and a scope of many projects keep "What we finished". A saved report keeps it too.
11. **The flag.** While `agent_runs_enabled` is off, the runs tile reads "Off" and counts no run. The pull requests do not depend on the flag.
12. **Live, with no flag.** Every API change is a new key. RA-2 adds no table and no migration.
13. **Review fix round (2026-10-25).** The merge rate reads only a time from GitHub (P1). A run ends at the last act of its agent (P2). The waits come out of the agent time. Each tile has an `InfoTip`. A subject hides the waiting tile, because `waiting` refuses a subject and `sectionsFor` drops it.
14. **The merge rate and the board strip.** Both read `merged_at` for a merge, so both skip the token path. They still differ on purpose. The strip of the board counts every merged pull request of 7 days, in one project. The tile counts the pull requests of agent work, merged or closed, in 14 days, in the scope of the Overview.
15. **Known cost, filed as a follow-up.** `actor_series`, `agent_members`, `actor_cycle` and the run time each build the window of `cycle_cte_sql` again, so a render runs that walk four times. One shared statement needs the four answers from one query, and that is not a small change.

Fences: `tests/unit/test_projects_report_kpis.py` (hermetic) and `tests/unit/test_projects_report_kpis_r8.py` (R8), and in the client `lib/kpiStrip.test.ts` and `components/kpiStrip.test.ts`.

### RA-3 — Waiting on people · AGENT-SAFE · BUILT 2026-10-25, not merged

**What:** the coordination panel of §6.8 C. It lists open agent questions (oldest first), agent work waiting for review and handoff blockers. It shows the answer time at the median and p90.

**Done when:** each row opens its task, a reader sees only tasks they can open, and an R8 test covers the answer time.

**Built.** The report section `waiting` lives in `routes/projects/analytics_waiting.py`. It joins rules that exist, and it holds no second copy of them. §5 holds each rule.

1. **Questions.** A run whose state is `awaiting` by `filters.AGENT_RUN_STATE_SQL`, on an open task of the scope. `agent_runs._OPEN_ELICITATIONS_SQL` finds its open question, as it is. The oldest question comes first.
2. **Reviews.** The rule "Waits for a review" of §5. "Agent work" is `agent_worked.AGENT_WORKED_SQL`, and "an agent act" is `analytics.agent_act_sql`, the SQL twin of `is_agent_act`.
3. **Handoffs.** RA-1's `holder_labels_sql` labels both tasks of an open `blocks` link. The blocking task can be in any project that the reader can see.
4. **Answer time.** `percentile_cont` over the questions of the report period. A question with no answer stays out of both figures, and `unanswered` counts it.
5. **§7 rule 1.** The lists read Load's open-work predicate, and the answer time reads Throughput's history predicate. Each list holds 20 rows at most, beside a total.
6. While `agent_runs_enabled` is off, the questions and the answer time read nothing, as `agent_summary.py` does.
7. The section refuses a subject, as `outlook` and `hygiene` do. It reads the period for the answer time, so the builder keeps the period chip.
8. **Overview.** The section is on by default. Overview hides the panel when the scope has no agent run and nothing waits. A saved report shows one clear row in its place.
9. **Each row opens its task.** A row is a `ControlLink` to `/projects?task=<id>`. A plain click goes to the page through `OpenTaskContext`, and the page opens the task as a deep link does.
10. **One sentence.** The server writes the summary, and the panel, the email and the chat print it (§6.8 E5). The chat reads the section in a saved report, through `report_render` and `render_report`. A direct question to the assistant about who waits on whom is RA-8.

`analytics.py` gains `activity_via_sql` and `agent_act_sql`. So the RA-1 fence still finds one read of `meta.via` in the analytics modules.

Fences: `tests/unit/test_projects_report_waiting.py` (hermetic), `tests/unit/test_projects_report_waiting_r8.py` (R8), and in the client `lib/waiting.test.ts` and `components/waitingPanel.test.ts`.

### RA-4 — Agent runs · AGENT-SAFE · BUILT 2026-10-25, not merged

**What:** 14 days of run outcomes, the success rate and silent runs now (the §6.8 F rule). Run sizes: under 15 minutes, under 1 hour, under 4 hours, and longer. A long run is flagged as "maybe a poorly specified task".

**Done when:** the panel hides itself when the scope has no agent runs.

**Built.** The report section `agent_runs` lives in `routes/projects/analytics_agent_runs.py`. It joins rules that exist, and §5 holds each new word.

1. **Outcomes.** The runs that ended in the last 14 UTC days, today included, by outcome. "Ended" is `ended_at`, the time of the terminal move. RA-2's run end is the end of the agent's time, so only the sizes read it. A daily series feeds a small stacked chart, and its days add up to the outcomes.
2. **The success rate.** Completed over completed, failed and abandoned. A failure is the agent's error, and an abandoned run is an agent that went quiet past its lease. A stop and a release stay out. The trend is RA-2's `merge_trend`, against the 14 days before.
3. **Silent runs now.** RA-2's `silent_run_sql` and its one constant, on Load's open-work predicate. So the total equals the silent count of the KPI tile. The list names 20 runs at most, the longest silence first. Each row names its task, the agent and its member (E2).
4. **Run sizes.** The completed runs of the window, by RA-2's agent time of a run. `analytics_agents.run_time_joins_sql` is now the one text of that time, and the KPI tile reads it too. A long run is 4 hours or more. The panel names the longest five.
5. **Summary first (E5).** The server writes the sentence and the flag of the long runs. The panel, the email, the download and the chat print them.
6. **§7 rule 1.** The outcomes and the sizes read Throughput's history predicate. A run on a task that is done or archived now still counts in its window.
7. **The period.** The section reads its own window and never the report period, as the merge rate does. The builder lists it as period-free. It refuses a subject, as `waiting` does.
8. **Overview.** The section is on by default. Overview hides the panel when the scope has no agent run and nothing is in it, by RA-3's rule. A saved report shows one clear row in its place.
9. **UI.** The chart takes Throughput's style. Each outcome takes an explicit slot of the categorical ramp, beside its label. Each figure has an `InfoTip` with its rule, and a keyboard or a touch opens it.
10. **The chat.** A saved report prints the section through `report_render`. The text fences each title, agent name and member name with `data()`. A direct question to the assistant is RA-8.
11. While `agent_runs_enabled` is off, the section reads no run. Live, with no flag. Every API change is a new key, with no table and no migration.
12. **RA-4b (2026-10-26).** The run sizes and the KPI median read one agent time text. It now takes out each idle gap of a reopened run (`_REOPEN_GAPS_JOIN`). The outcomes gain `server_closed` in the counts and the series. It has its own ramp slot and the legend words "Closed by Metorite". The success rate and the sizes leave it out. The server marks it on a system line, so no column is new. The `on` key of `agent_runs` and of `waiting` decides the clear rule. `agentRunsIsClear` and `waitingIsClear` answer false when `on` is false. Then the panel, the clear row and the email print the server's summary. The chat prints `success.rate` as a percent (`_REPORT_PERCENT` in `skill_projects/reads.py`).

Fences: `tests/unit/test_projects_report_agent_runs.py` (hermetic) and `tests/unit/test_projects_report_agent_runs_r8.py` (R8), and in the client `lib/agentRunsReport.test.ts` and `components/agentRunsPanel.test.ts`.

### RA-5 — Quality · AGENT-SAFE · BUILT 2026-10-26, not merged

**What:** agent PR outcomes from `pm_task_refs.state`, rework (§6.8 F) and CI red on agent PRs.

**Done when:** the merge rate shows a trend, with no 100% target in its text.

**Built.** The report section `quality` lives in `routes/projects/analytics_quality.py`. It joins rules that exist, and §5 holds each new word.

1. **Agent PR outcomes.** The section runs RA-2's `merge_sql`, `merge_rate` and `merge_trend`, as they are. So the merged count, the closed count, the rate and the trend equal the KPI tile for the same scope. The R8 suite pins that. It adds the open and draft agent pull requests on open tasks. `analytics_agents.PR_CLOSED_AT_SQL` is the close time of RA-2, under a public name.
2. **The window.** The last 14 days counted back from now, and the 14 days before. These are the bounds of `merge_sql`, and not 14 UTC calendar days. The tile counts back from now, and the two must agree. Every count of the section uses the same two bounds. The section reads its own window and never the report period. It refuses a subject, as `waiting` does.
3. **Reopened.** `reopened_sql` reads the status moves of `pm_activities`. A reopen is a move from the done category to a category outside `CLOSING_CATEGORIES`. A finish is a move into done from a status that is not done. RA-1's `finish_actor_join` and `finish_label_sql` label each move. RA-2's `actor_group_sql` puts the label in Agents or People.
4. **Two counts, on purpose (fix round 1).** "Tasks reopened" counts the distinct tasks with a reopen in the window. Each task takes the group of the finish that its latest reopen undid. The reopen rate is a cohort of the finishes of the window. A finish is undone by a reopen of the same task after it, with no other finish between the two. Any time up to now counts. So the rate never passes 100%. The first version divided reopen moves by finish moves of one window. Three tasks that finished 20 days ago and reopened today read 3 of 1. The cohort reads 0 of 1, and the three tasks show in "Tasks reopened". A task finished twice in the window and reopened once after the first finish gives 1 of 2.
5. **The newest window reads low.** A recent finish has had less time to be reopened. The `InfoTip` and the §5 row say so. The trend compares the same cohort rule for the 14 days before.
6. **Retried.** A failed run counts as retried when a later run exists on the same task. The later run has a `created_at` after the `ended_at` of the failed run. The window is the one of that `ended_at`.
7. **CI red.** The open list is `failing_prs_sql`. Each row opens its task. Each row names the agent and its member (E2), through RA-3's `agent_of` and RA-1's `agent_act_sql`.
8. **Merged while red: the decision.** The count is in. The stored `checks_state` of a merged pull request is not always the state at the merge. `_CHECKS_WRITE_SQL` in `github_sync.py` matches the head SHA and nothing else. `_HEAD_HELD_SQL` also lets the webhook of a check event pass for a merged reference. So a check that completes after the merge rewrites the state. The count therefore requires `checks_fetched_at <= merged_at`. Then the state was read before the merge, and no later read replaced it. A pull request whose last read came after the merge stays out. The count can miss a pull request, and it never adds one. If the checks write ever looks at the state of the pull request, the guard can go. `test_the_checks_write_has_no_gate_on_the_state_of_the_pull_request` fails then.
9. **Summary first (E5).** The server writes the sentence. The panel, the email, the download and the chat print it first. No text, no tooltip and no test fixture calls 100% the goal (E4). A pure test scans every copy string of the module for a goal word and for a 100% target. It also runs over a merge rate of 100%.
10. **§7 rule 1.** The history reads use Throughput's history predicate. The open pull requests use Load's open-work predicate.
11. **The runs flag.** While `agent_runs_enabled` is off, the retried count reads no run. The failing list reads no run either. It names the agent from the agent acts only. RA-2's test of an agent pull request still sees a run that exists, as the KPI tile does. The pull requests and the reopened tasks do not depend on the flag. While the flag is off, the section is never clear. A failed run cannot show.
12. **Overview.** The section is on by default. Overview hides the panel when the scope has no agent pull request, no reopen and no failed run. That is RA-3's rule (`quality_seen`). A saved report shows one clear row in its place.
13. **UI.** Each figure has an `InfoTip` with its rule. Each pull request outcome, and each group, takes an explicit slot of the categorical ramp. Agents and People keep the slots of RA-2. Each failing row opens its task.
14. **The chat.** A saved report prints the section through `report_render`. The text fences each title, agent name and member name with `data()`.
15. Live, with no flag. RA-5 adds no table and no migration.

Fences: `tests/unit/test_projects_report_quality.py` (hermetic) and `tests/unit/test_projects_report_quality_r8.py` (R8), and in the client `lib/qualityReport.test.ts` and `components/qualityPanel.test.ts`.

### RA-6 — The forecast by count · AGENT-SAFE · BUILT 2026-10-25, not merged

**What:** a Monte Carlo of weekly finishes. It gives the 50% and 85% dates for the open count, with no estimates needed. It keeps "Not converging" and adds the finishes a week that would converge it. "Needs an owner" shows first in Open work when unassigned tasks lead.

**Done when:** the same seed gives the same dates, and a pure test pins the converge line.

**Built.** The forecast lives in `routes/projects/analytics_forecast.py`, and it is pure. `analytics.project_forecast` calls it. §5 holds each new word.

1. **One series.** The forecast reads the weeks of `analytics.velocity_sql`, which is the spine of Throughput. The R8 suite pins its six weeks to the closed weeks of the `throughput` section. No second count of a finish exists.
2. **The runs.** Each of 2,000 runs samples one past week at a time, with replacement. The finishes and the arrivals of that week go together. The rule of 2026-09-17 binds: scope growth is part of the answer. A run that read finishes only would print the date that the mean net rate refused.
3. **The seed.** The seed is SHA-256 of the scope and the last week of the history. A reload gives the same dates, and a new week gives new ones. `hash()` changes in each process, so the seed does not use it. The generator is a local `random.Random`, never the global state.
4. **2,000 runs and a cap of 52 weeks.** At 2,000 runs the 85% figure has a standard error under one point. So two seeds differ by a week at most. A run stops at 52 weeks, because a date past a year is no forecast. The cap also bounds the work to 104,000 draws, near 12 ms.
5. **The verdicts, in order.** First comes no open task, then "Not converging" for arrivals, then "Not enough history". Then comes "Not converging" for the cap, and last the two dates. The arrivals rule runs before the history gate, so a project that grows stays red with a thin history. Only the arrivals verdict carries `converge_per_week`. Each `not_converging` answer carries `reason`.
6. **The estimates.** The simpler choice: the estimates add a second line only when they exist. "Nothing sized" no longer draws. "No capacity on file" still draws, because it says that estimates exist and hours do not. The server still sends the `capacity` block.
7. **The API.** Every change is a new key. `velocity` gains the two dates, the weeks, the runs, the cap, the converge line, `reason` and `summary`. `finish_date` and `weeks_remaining` carry the 50% figure, so the slip, the email and the chat keep working. No key goes. Load gains `needs_owner`.
8. **Summary first (E5).** The server writes the sentence. The panel, the email and the chat print it first.
9. **Needs an owner.** `analytics.needs_owner_sql` over Load's open-work predicate, so a reader sees only the tasks they can open. The server names 20, the soonest due first, beside the total. `leads` is true when the total is at least the open count of each holder. It reads every row before the reader's row filter, because it compares totals only. RA-1's item 6 says how it differs from `none`.
10. **UI.** The Forecast draws one band from today. It is solid to the 50% date and an outline to the 85% date, with a tick at the plan. The band takes the explicit ramp slot 9. An `InfoTip` beside the headline gives the rule of the verdict. "Needs an owner" is the first group of Open work when it leads. It is the last group when it does not lead. Each row opens its task.
11. **The chat.** `analytics_outlook` prints the sentence first. `analytics_load` and a report's `load` section print the tasks that need an owner, each title through `data()`. The text prints each row the server sent, so the paging fence holds. The card gains "85% finish" and "Need an owner".
12. Live, with no flag. RA-6 adds no table and no migration.

**Fix round 1 (2026-10-26), after the review.**

1. **No rate for the cap.** The converge line is a mean rate, and the cap verdict is an 85% test. In about one cap verdict in five, the line asked a team for a rate it already had. So only the arrivals verdict names a rate. The cap verdict says what helps.
2. **The history gate.** It needs 3 finished tasks, in 2 weeks or more. So a team that finishes work every second week gets a date. The arrivals rule runs before the gate.
3. **Three weeks.** `GET /analytics/outlook` takes 2 to 26 weeks. Three weeks of history can now give a date.
4. **The chat.** A figure that the server sends as null prints nothing. `analytics_load` prints "Needs an owner" on the first page only.
5. **The band.** The 85% part is an outline in the full hue, with a fill of 20%. The outline is 3.96:1 against the dark track and 6.90:1 against the light one. The solid part is 3.08:1 against the fill in dark mode and 4.91:1 in light mode.
6. **The table.** The table of Open work puts "Needs an owner" first when it leads.

Fences: `tests/unit/test_projects_report_forecast.py` (hermetic) and `tests/unit/test_projects_report_forecast_r8.py` (R8), and in the client `lib/outlook.test.ts`, `lib/needsOwner.test.ts` and `components/forecastPanel.test.ts`.

### RA-7 — Cost · BLOCKED on SW-20 usage capture

**What:** cost by project and for each finished task (§6.8 D). Every figure prints its source. The panel shows the coverage share, and "unknown" in place of $0.

**Done when:** a task with no usage reads "unknown", and the coverage share counts it.

### RA-8 — The chat · AGENT-SAFE

**What:** the assistant reads the same sections for "how are the agents doing on this project?". It is a class A read with no card.

**Done when:** the manifest maps the tool, and an answer quotes the panel's own summary sentence.

## 8a. Later phases — recorded, not scheduled

### Phase 2 — R7, a report that renders and emails itself · OWNER-GATE to arm

⚠️ **Phase 2 now owns R9 and R8's `send_report` too (§9 Q16).** Before it is
dispatched, the spec must name five things that the R-final audit found
missing (2026-09-30):
1. The send seam. The gateway has no system mail sender. The only one is
   `sendReportEmail` in `src/lib/reportEmail.ts`, in the Next tier.
2. One lock of record, because `delivery_armed()` reads the gateway env
   and `isReportEmailEnabled` reads the Next env.
3. The directory check: `report_scope.check_in_directory`, not
   `reports.py:_known_member`, which matches across organizations.
4. How the context of the recipient is built, and a shared store for the
   rate limit.
5. What a send returns when the recipient may not open the subject.

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
| Q8 (2026-09-28) | Where does the member setting of R5c live? | R5c waits for the WS-29 fix that scopes `org_settings` to each organization. The setting stays off until then. |
| Q9 (2026-09-28) | May a lead of a Center group report on its members? | Yes. A lead of any group reports on the members of that group, whatever its size. Admins control who holds the lead role. |
| Q10 (2026-09-28) | Does the chat's task data read obey the role rule? | The owner: "run role rule in chat". The dataset read obeys §7.1 rule 3. A reader cannot group or count tasks by a person outside the people they may report on. This amends O3 of `projects_ai_chat.md` §13.7 (R5d). |
| Q11 (2026-09-28) | May a report show the address of its author? | The owner: "author email address is fine for people from the same org". Every reader is in the organization of the report. So `created_by` stays in the report, the list and the chat. Nothing changes. |
| Q12 (2026-09-28) | Who may delete a report? | The owner: "report deleting should be based on role permissions". The author of a report may delete it, and so may an admin. Every other reader gets 403 with the reason (R5d). |
| Q13 (2026-09-28) | Does the delete rule also bind a change to a report? | The owner: yes. Only the author or an admin may change a report, its recipients or its schedule. Every other reader gets 403 with the reason (R5d). |
| Q14 (2026-09-29) | Do Analytics and Reports stay two apps? | The owner: "i think we should combine ANalytics and reports? … its similar contect from a UI/UX point of view right?", then "go ahead" to one app. Reports is now the one app. It opens on Overview, which shows what the Analytics app showed and saves nothing. The gallery and the saved reports come under it. `?app=analytics` opens Overview. This answer changes the presentation only. The line of §9.12.8 (2026-08-31) stays: Overview is what you look at, and a saved report is what you deliver (R5f). |
| Q15 (2026-09-30) | How much of Phase 1 is left to build? | The owner: "just conclude this feature soon, keep only what is absolutally necessary". One slice remains, R-final. R4, R4b and R6 are cut. Q16 then narrowed R-final to the chat render by name. |
| Q16 (2026-09-30) | Does the email send stay in the close-out? | The owner: "Move email to later". The gateway has no system mail sender, and the timed email of Phase 2 needs the same seam. R9 and `send_report` move to Phase 2. R-final keeps only the chat render by name. |
| Q17 (2026-09-30) | May Overview show the live analytics with no extra menu? | The owner, in these words: `Since reports and analytics share the same option, live analytics should be viewable there without extra menus. Settings, options, and filters can be hidden to display full-page analytics. Exporting to a report is optional; the analytics can be viewed directly. Ensure the live view maintains proper UI/UX.` Overview is now a full-page, live dashboard (§6.7, R5g). |

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

Server, in two calls. One call with every file is slow, and a hang then
hides the file that hung.

```bash
uv run pytest tests/unit/test_projects_report_scope_r5.py \
  tests/unit/test_projects_report_scope_r5b.py \
  tests/unit/test_projects_report_overview_r5f.py \
  tests/unit/test_projects_report_sections_r3d.py \
  tests/unit/test_projects_report_sections_r3.py \
  tests/unit/test_projects_report_sections_r3b.py \
  tests/unit/test_projects_report_sections_r3c.py \
  tests/unit/test_projects_analytics_load.py \
  tests/unit/test_projects_analytics_capacity.py \
  tests/unit/test_projects_analytics_rebalance.py \
  tests/unit/test_projects_analytics_conflicts.py   tests/unit/test_projects_analytics_dataset.py \
  tests/unit/test_projects_analytics_actor.py tests/unit/test_projects_analytics_actor_r8.py \
  tests/unit/test_projects_report_waiting.py tests/unit/test_projects_report_waiting_r8.py \
  tests/unit/test_projects_report_kpis.py tests/unit/test_projects_report_kpis_r8.py \
  tests/unit/test_projects_report_agent_runs.py \
  tests/unit/test_projects_report_agent_runs_r8.py \
  tests/unit/test_projects_report_forecast.py \
  tests/unit/test_projects_report_forecast_r8.py -q -rs

uv run pytest tests/unit/test_projects_reports.py \
  tests/unit/test_projects_report_recipients.py \
  tests/unit/test_projects_report_sections_lockstep.py \
  tests/unit/test_projects_reportable_reports.py \
  tests/unit/test_projects_chat_coverage.py \
  tests/unit/test_projects_agent.py \
  tests/unit/test_projects_report_builder.py \
  tests/unit/test_projects_report_templates.py \
  tests/unit/test_projects_report_visuals.py \
  tests/unit/test_projects_analytics_outlook.py \
  tests/unit/test_tenant_coverage.py   tests/unit/test_projects_agent_writes.py   tests/unit/test_projects_report_chat_rfinal.py -q -rs
```

`test_projects_agent.py` holds the class-A reach fence. A manifest row that
names a built tool which never calls the route fails there.

- `test_projects_report_builder.py` is R1's file.
- `test_projects_report_templates.py` is R2's file.
- `test_projects_report_sections_r3.py` is R3a's file.
- `test_projects_report_sections_r3b.py` is R3b's file.
- `test_projects_report_sections_r3c.py` is R3c's file.
- `test_projects_report_sections_r3d.py` is R3d's file.
- `test_projects_analytics_load.py` holds the Load route and its body.
- `test_projects_report_scope_r5.py` is R5a's file.
- `test_projects_report_scope_r5b.py` is R5b's file.
- `test_projects_report_chat_rfinal.py` is R-final's file.
- `test_projects_analytics_actor.py` and `test_projects_analytics_actor_r8.py`
  are RA-1's files.
- `test_projects_report_waiting.py` and `test_projects_report_waiting_r8.py`
  are RA-3's files. Its client files are `waiting.test.ts` and
  `waitingPanel.test.ts`.
- `test_projects_report_kpis.py` and `test_projects_report_kpis_r8.py` are
  RA-2's files. Its client files are `lib/kpiStrip.test.ts` and
  `components/kpiStrip.test.ts`.
- `test_projects_report_agent_runs.py` and
  `test_projects_report_agent_runs_r8.py` are RA-4's files. Its client files
  are `lib/agentRunsReport.test.ts` and `components/agentRunsPanel.test.ts`.
- `test_projects_report_forecast.py` and `test_projects_report_forecast_r8.py`
  are RA-6's files. Its client files are `lib/needsOwner.test.ts` and
  `components/forecastPanel.test.ts`, and it changed `lib/outlook.test.ts`.
- `test_projects_report_overview_r5f.py` is R5f's file. Its client half is
  `reportsOverview.test.ts`.
- R5g has no server file. Its files are `overviewLive.test.ts` and
  `reportsLiveOverview.test.ts`, in the client.

Client, in `workbench/control_plane`:

```bash
npx tsc --noEmit && npx vitest run src/app/projects src/lib/reportEmail.test.ts src/lib/theme src/lib/sourceHygiene.test.ts src/components/genUITemplates.test.ts src/components/ui/SelectButton.test.ts
```

Each slice adds its own test file and names it in its PR. A UI slice also runs
the `visual-review` skill: light mode, compact density, a changed accent, and
the phone width.
