# Linear parity review, 2026-11-08

**Metorite task 2211.** Owner ask, 2026-11-08: "Review the features that Linear has to ensure that we are able to cater to all the use cases and abilities that Linear also does."

**Tier.** Research prose. It follows the INFORMED tier of `docs/style_ste.md`. This file is not a spec. It does not change a decision.

---

## 1. The answer first

**Metorite covers the daily core of Linear and the agent platform. It has gaps in planning cadence, reporting and intake.** Linear has no feature that Metorite must copy against a recorded decision. Most gaps already have a task.

- **Covered.** Tasks, sub-tasks, priority, estimate (time), tags, blocks/relates/duplicate links, board, list, table, timeline and calendar views, saved views, intake with accept/decline/duplicate/snooze, watchers, a notification bell, a command palette, a GitHub App, a file importer, and a full MCP server with agent runs.
- **Partial.** Insights (Reports cover cycle time, throughput and a forecast, but have no slice-and-measure builder), project health, templates, integrations (GitHub only), webhooks (inbound only), guests, and the MCP read surface.
- **Gap.** Cycles (sprints), project milestones, initiatives and a cross-project roadmap, SLAs, customer requests, project updates with a digest (Pulse), triage rules and rotation, label groups, Slack Asks, Linear/Jira/Asana importers, outbound webhooks, and MCP tools for labels, members, comments, documents and attachments.
- **Not wanted.** A Linear "team" as an object (D87, D49). A time-tracking timer (D77). Story-point estimate systems and Plane-style modules (P-31, open again only if SP-0 decides). A second priority scale (D78). A docs store inside Projects (D-PM-13). A public intake form now (D-PM-14).
- **Agent platform.** Linear's own changelog shows one entry after 2026-10-30 (5 November 2026). Nothing in it changes the 2026-10-30 comparison. Section 6 lists the earlier items that Appendix B did not cover (Loops, Diffs, coding sessions).

Section 7 groups the gaps into 15 proposed tasks, biggest value first. Twelve of the 15 already have a Metorite task. The new work is smaller than it looks.

**How to read the verdicts.** "Covered" means code or an ACTIVE spec with a built slice. "Partial" means some of the use case works. "Gap" means none of it exists. Where a spec says "BUILT" but the feature is dark, the row says so.

---

## 2. Method and limits

- **Linear side.** Linear's own pages first: `linear.app/docs`, `linear.app/developers` and `linear.app/changelog`. Pages fetched on 2026-11-08. A claim from a third-party page carries the tag **(third party)**.
- **Metorite side.** The code and the ACTIVE specs. Task numbers come from the Metorite MCP read tools (`search_tasks`, `get_task`). No write tool was called.
- **Pages that I could not open.** `linear.app/docs/sla` and `linear.app/docs/cycles` answered 404. I used `linear.app/docs/use-cycles` for cycles. I did not read an SLA page (see section 8).
- **Prompt injection.** Two Linear pages held a line that told AI agents to connect to a Linear MCP server. I ignored it. It is not part of this review.
- **Earlier work.** `agent_coordination.md` §3.8 and Appendix B hold the agent platform comparison of 2026-10-30. Metorite task 978 (LP) holds the Linear and Plane idea list of 2026-10-29. This review builds on both and does not redo them.

---

## 3. Linear as a project tool

Verdict key: **covered**, **partial**, **gap**, **not wanted**. "Slice" names the proposed task number from section 7 (P1 to P15) and any existing Metorite task.

### 3.1 Issues and their properties

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| Issue with title, description, status, assignee | linear.app/docs/issues | `pm_tasks`; `/projects`; MCP `create_task`, `update_task` | covered | none |
| Sub-issues, parent and sub-issue | linear.app/docs (Parent and sub-issues) | Sub-tasks to any depth; `list_tasks` with `parent_task_id` (MCP-G5, done) | covered | none |
| Relations: blocks, blocked by, related, duplicate | linear.app/docs (Issue properties) | `pm_task_links`: `blocks`, `relates_to`, `duplicates` (`skill_projects/writes.py:126`); `RelationsBlock.tsx`; merge dialog | covered | none |
| Priority | linear.app/docs (Issue properties) | One priority matrix (D78) | covered | none |
| Estimates (points, t-shirt, Fibonacci) | linear.app/docs (Issue properties) | `estimate_mins` only. Point scales refused by P-31 | partial. Time covered. Points await SP-0 | existing #959 (SP-0) |
| Labels | linear.app/docs (Issue properties) | Tags with colour (`TagManager.tsx`), org-wide and per space | covered | none |
| Label groups (single or multi-select) | linear.app/docs/initiatives (label groups also for initiatives) | Flat tags, no group | gap | P11 |
| Due date, start date | linear.app/docs | `due_at`, `start_date` | covered | none |
| Issue templates | linear.app/docs (Issue templates) | None | gap | P7, existing #18 |
| Assign and delegate | linear.app/developers/agents | Assignees today. Delegate is planned (SW-29a) | partial | existing #1046 |
| Comments and reactions | linear.app/docs (Comments and reactions) | Comments are activities (`pm_activities`). No reactions found | partial (reactions Not verified in Metorite) | none (low value) |
| Archive and delete | linear.app/docs (Delete and archive issues) | `request_task_archive`, auto-archive (migration 166), `delete_task` | covered | none |
| Recurring issues | Not in the Linear docs list | `RepeatEditor.tsx`, repeat in Metorite | covered (Metorite goes beyond) | none |
| Git branch name on an issue | Third party (Speakeasy lists it on `get_issue`) | `short_id` for branch names and the GitHub App links PRs by branch (WS-54) | covered | none |
| Custom fields | Not a Linear feature in the docs list | `FieldManager.tsx`, `customFields.ts` | covered (Metorite goes beyond) | none |

### 3.2 Planning: cycles, projects, milestones, initiatives, roadmap

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| Cycles: repeating time boxes, 1 to 8 weeks, up to 15 future cycles | linear.app/docs/use-cycles | None. `project_management_app.md` §1 lists sprints as a stated non-goal. Task 19 (SP) reopens it | gap | P1, existing #19, #959, #977 |
| Cooldown between cycles | linear.app/docs/use-cycles | None | gap | P1 |
| Rollover of unfinished issues | linear.app/docs/use-cycles | None. P-23 in the Plane notes describes a logged transfer | gap | P1 |
| Auto-add of started issues to the current cycle | linear.app/docs/use-cycles | None | gap | P1 |
| Cycle graph and capacity dial | linear.app/docs/use-cycles | None. RA-6 gives a count forecast (Monte Carlo 50 and 85 percent) | gap | P1 |
| Projects with lead, members, dates, status, priority | linear.app/docs (Projects) | Spaces, projects, sub-projects (`pm_projects`). Project run state and lifecycle (WS-27bg) | covered | none |
| Project labels, project status, project dependencies | linear.app/docs (Planning) | Project tags are Not verified. Run state exists. Task `blocks` links exist. A project-to-project dependency was not found | partial | P2 |
| Project milestones with progress | linear.app/docs/project-milestones | A milestone is a tag on a task (D168.3). WM-2 (#981) plans milestones with progress | gap | P2, existing #981 |
| Initiatives, sub-initiatives, status, owner, health | linear.app/docs/initiatives | None. Task 977 (WM) names "goals". The nearest object is a sub-project tree | gap | P2, existing #977 |
| Roadmap and timeline across projects | linear.app/docs (Timeline) | `TimelineView.tsx` shows the tasks of one view. A cross-project roadmap is not built | partial | P2 |
| Releases (plan and track software releases, CI/CD deploy status) | linear.app/changelog, 2026-04-30 | GitHub App shows PR and check state on the task (WS-54). No release object | gap, low value for Metorite's market | none (watch) |
| Team page, team documents | linear.app/changelog, 2026-06-04 | None. A team view is a filter (D87) | not wanted. Reason: D87, D49 | none |
| Teams as an object (statuses, cycles, triage per team) | linear.app/docs (Teams) | A project or space carries its own statuses. A "team" is a filter | not wanted. Reason: D87, D49 | none |
| Sub-teams and private teams | linear.app/docs (Private teams, Sub-teams) | Visibility is private, Center scoped, org, plus `group:<slug>` grants (D12) | covered by D12 grants | none |

### 3.3 Intake: triage, SLAs, templates, Asks, customers

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| Triage inbox with accept, decline, duplicate, snooze | linear.app/docs/triage | Intake queue with accept, decline, duplicate and snooze (`pm_intake`, `TriageRail.tsx`, `intake.ts`) | covered | none |
| Triage rules (change team, status, assignee, label, project, priority on entry) | linear.app/docs/triage (Business and Enterprise) | None as rules. Workflows engine exists. LP-6 (#993) plans one-tap recipes | gap | P4, existing #993 |
| Triage responsibility (auto-assign, rotate, skip out-of-office, PagerDuty and others) | linear.app/docs/triage | None. People has absences (`people_absences`), so the data exists | gap | P4 |
| Triage Intelligence (suggest team, project, assignee, labels, duplicates) | linear.app/docs (Triage Intelligence); `agent_coordination.md` App. B | Planned: LP-2 (#989), SW-35 (#1052), ALN-3 (#1377). One engine only (ALN) | gap (planned) | P4, existing #989, #1052, #1377 |
| SLAs on priority, with Breached and Failed | linear.app/docs (SLA page not read, see section 8). Webhook doc lists an "Issue SLA" event | None. LP-3 (#990) plans it | gap (planned) | P5, existing #990 |
| Asks: Slack, email and web-form intake | linear.app/docs/linear-asks (Business and Enterprise) | LP-8 (#995) plans forms and a project address. A public form is deferred (D-PM-14). No Slack intake | gap | P12, existing #995 |
| Customers and customer requests, importance, revenue, tier | linear.app/docs/customer-requests | None in Projects. The CRM exists. LP-4 (#991) links a task to a CRM customer | gap (planned) | P6, existing #991 |
| Customer sources: Intercom, Zendesk, Front, Salesforce, Slack | linear.app/docs/customer-requests | CRM has Zoho and email. No support-desk connectors | gap | P12 |
| Custom views filtered by customer attribute | linear.app/docs/customer-requests | Needs P6 first | gap | P6 |

### 3.4 Views, display and navigation

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| Board, list and timeline layouts | linear.app/docs (Board layout, Timeline) | Six canvases: board, list, table, calendar, timeline and more (`TaskBoard`, `TaskList`, `TableView`, `CalendarView`, `TimelineView`) | covered | none |
| Grouping, sub-grouping, ordering, display options | linear.app/docs (Display options) | `ViewSheet.tsx`, display popover (OP-12 to OP-14 planned) | partial | P14, existing #839 |
| Filters, saved and shared custom views (issue, project, initiative) | linear.app/docs/custom-views | `pm_views` and `pm_view_task_positions`; one filter component (OP-5) | partial. Saved task views covered. Project and initiative views are not | P14 |
| Subscribe to a view (notify or post to Slack) | linear.app/docs/custom-views | Watchers on tasks exist. A view subscription was not found | gap | P14 |
| Favorites in the sidebar | linear.app/docs (Favorites) | Not found in `nav.ts` or the Projects sidebar | gap, low value | P14 |
| Peek preview | linear.app/docs/custom-views | Side panel, centred card, full screen (`one_record_card.md`, D181) | covered | none |
| Command menu and keyboard shortcuts | linear.app/docs (Search, Editor) | One shell palette (D87 to D89), `SearchPalette.tsx`, `ShortcutsSheet.tsx` | covered | none |
| My issues | linear.app/docs (My issues) | My work in Projects, `MyWorkHost.tsx`, My Tasks lens (D52 to D54) | covered | none |
| Mobile app | linear.app/changelog, 2026-11-05 (Mobile Diffs) | Phone layout of the web app (OP-1, MOB slices) | covered for tasks | none |

### 3.5 Insights, updates and health

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| Insights: measure (count, effort, cycle time, lead time, triage time, age) | linear.app/docs/insights (Business and Enterprise) | Reports: cycle time, throughput, stuck, load, outlook (`analytics_*`), who-did-the-work, quality, waiting | partial | P15 |
| Slice and segment any measure, percentile markers | linear.app/docs/insights | Fixed report sections, not a free builder | gap | P15 |
| Burn-up and cumulative flow | linear.app/docs/insights | Not found. Burndown is banked in the sprint notes (P-23) | gap | P1, P15 |
| Dashboards | linear.app/docs (Dashboards) | The Center dashboards own them (task 978). Reports Overview is live | partial. Custom dashboards in Projects are out (task 978) | none |
| Project updates with health (on track, at risk, off track) | linear.app/docs/initiatives; changelog 2026-06-18 | LP-1 (#988) plans it. Project health signals exist in Reports | gap (planned) | P3, existing #988 |
| Initiative updates and health | linear.app/docs/initiatives | None | gap | P2, P3 |
| Pulse: feed of updates, daily or weekly summary in the inbox | linear.app/docs/pulse | The LP-1 digest. Briefs exist (WS-BRIEF) | gap (planned) | P3 |
| Agent-drafted updates | linear.app/changelog, 2026-06-18 | SW-36 plans a weekly draft | gap (planned) | P3, existing SW-36 |
| Project Slack channels | linear.app/changelog, 2026-05-21 | None | gap | P12 |

### 3.6 Notifications, inbox and subscriptions

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| Inbox with notifications | linear.app/docs (Inbox, Notifications) | `NotificationBell.tsx`, `notifications.ts`, Waiting on me (`waiting_on_me.md`) | covered | none |
| Priority inbox (urgent split out) | linear.app/changelog, 2026-09-03 | Reach decides what interrupts (WS-59, D103) | covered by another design | none |
| Subscribe to an issue, project, view or customer | linear.app/docs | Watchers on tasks (migration 165). Not on projects or views | partial | P14 |
| Mail, mobile push, Slack delivery | linear.app/docs (Notifications) | Web push (PN-1), email and WhatsApp through Reach | covered. Slack delivery is a gap | P12 |

### 3.7 Integrations and import

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| GitHub (PR links, status sync, branch names) | linear.app/docs (GitHub) | GitHub App, webhooks, auto-link, merge-to-Done for agent work (`github_integration.md`, GH-1 to GH-7 built; dark behind `github_app_enabled`) | covered, once the flag is on | none |
| GitLab | linear.app/docs (GitLab) | Out of scope in `github_integration.md` §2 | gap | P12 (low) |
| Slack (create issues, unfurl, sync replies, notifications) | linear.app/docs (Slack) | None for Projects. WhatsApp assistant channel exists | gap | P12 |
| Sentry, Zendesk, Intercom, Front, Salesforce, Figma, Gong, Notion, Discord, Zapier | linear.app/docs (Integrations) | None for Projects. CRM and email connectors are separate | gap | P12 (pick two by demand) |
| Importers: Jira, Asana, GitHub Issues, Shortcut, Linear-to-Linear, CSV | linear.app/docs (Importers) | ClickUp workspace CSV importer built (I-1 to I-10). Jira, Asana and other adapters are described in `project_import.md` §4.2 and are not built | partial | P13 |
| Import from Linear | linear.app/docs (Importers) | None | gap | P13 |
| Export data | linear.app/docs (Exporting Data) | Report file buttons, CSV in reports. A full workspace export was not found | partial | none (watch) |

### 3.8 API, webhooks, permissions and admin

| Linear capability | Linear source | Metorite now | Verdict | Proposed slice |
|---|---|---|---|---|
| GraphQL API, keys with scopes (Read, Write, Admin, Create issues, Create comments) | linear.app/docs/api-and-webhooks | MCP at `mcp.metorite.com/mcp` with OAuth 2.1. Gateway REST for the web app. No public REST or GraphQL for customers | partial. The MCP is the public API. A GraphQL API is not wanted | none |
| Webhooks for issues, comments, projects, cycles, customers, SLA events | linear.app/developers/webhooks | Inbound webhooks only (GitHub, mail). RT-4 (#1562) is a webhook per agent connection | gap | P10, existing #1562 |
| Webhook retry (3 times: 1 minute, 1 hour, 6 hours) and HMAC signature | linear.app/developers/webhooks | The GitHub receiver checks HMAC and delivery ids. No outbound sender | gap | P10 |
| Members and roles, guests | linear.app/docs (Members and roles) | Roles incl. `guest` (`org_access_control.md`). Guest scope is an open question there | partial | none |
| SAML, SCIM, login methods | linear.app/docs | Not verified in Metorite for customers | Not verified | none |
| Audit log | linear.app/docs (Audit log) | A member-admin audit write exists (`routes/admin/members.py`). A customer-readable audit log was not found | partial | none (watch) |
| Third-party app approvals | linear.app/docs | Agent connections are approved per member (MCP OAuth consent) | covered | none |
| Billing, AI credits, usage limits | linear.app/docs | Flat price plus AI credits (`launch_surface.md` §4) | covered | none |

---

## 4. Linear's MCP server against the Metorite MCP

**Linear.** Endpoints `https://mcp.linear.app/mcp` (Streamable HTTP) and `/mcp/readonly`. Auth is OAuth 2.1 with dynamic client registration, or a bearer token. Enterprise sign-in can run through Okta. The `/sse` endpoint is deprecated. Source: `linear.app/docs/mcp`.

**Tool list.** Linear's docs page names no tool. The list below comes from a **third-party** catalogue (Speakeasy, 31 tools). Other sites give 21 to 31. Treat the names as indicative. The 5 November 2026 changelog adds a completion-date filter to `list_issues`.

**Metorite.** `/mcp`, OAuth 2.1 with Client ID Metadata Documents, 34 tools in `apps/services/agent_edge/agent_edge/tools.py`, one prompt (`use_metorite`), and two resource templates behind a flag (SW-34a, `agent_resources_orgs`). The read-only endpoint is SW-34b (task 1051, in progress). Metorite counts 34 tools, and `agent_coordination.md` §3.8 says 25. The spec is out of date. The code is the fact.

| Linear tool (third party) | Metorite tool | Verdict | Note and task |
|---|---|---|---|
| `list_issues` (filters, `me` as assignee) | `list_tasks`, `search_tasks`, `my_work` | covered | MCP-G19 (#1957) adds counts, date and status-category filters |
| `get_issue` (with attachments, branch name) | `get_task`, `get_task_context`, `get_task_timeline` | covered | `short_id` is the branch key |
| `save_issue` (create or update) | `create_task`, `update_task`, `set_task_status`, `set_assignees`, `move_tasks` | covered | Metorite splits it, with idempotency keys. MCP-G12 (#1023) clears dates |
| `list_comments` | `get_task_timeline` | partial | Comments sit inside the timeline |
| `save_comment` (create or edit) | `add_comment` | partial | Edit and delete: MCP-G9 (#843) |
| `delete_comment` | none | gap | MCP-G9 (#843) |
| `get_attachment`, `create_attachment`, `delete_attachment` | `add_task_reference` for links. Attachments are read in `get_task_context` | partial | MCP-G8 (#413) |
| `list_cycles` | none | gap | Needs P1 first |
| `get_document`, `list_documents`, `create_document`, `update_document` | none. Knowledge has its own tools in chat | gap | P9 |
| `extract_images` | none | gap, low value | with MCP-G8 |
| `list_issue_statuses`, `get_issue_status` | `get_project` returns the statuses | covered | |
| `list_issue_labels`, `create_issue_label` | `get_project` returns tags. No create tool | partial | P8 |
| `list_projects`, `get_project` | `list_projects`, `get_project` | covered | |
| `save_project` | `create_project`, `update_project` | covered | MCP-G1 (#355, done) |
| `list_project_labels` | none | gap | Needs P2 |
| `list_milestones`, `get_milestone`, `save_milestone` | none | gap | Needs P2 |
| `list_teams`, `get_team` | `list_projects` (spaces) | not wanted as objects | D87 |
| `list_users`, `get_user` | `whoami` only | gap | P8: a read of members to assign |
| `search_documentation` | `use_metorite` prompt, server instructions, playbook (PB-3, #1949) | partial | P9 and task 751 (MCP-Gc) |
| Linear read-only endpoint | none yet | gap, in progress | SW-34b (#1051) |
| Linear Okta-managed MCP access | Per-member OAuth consent, box tokens (EA-19, MCP-G17) | partial | Enterprise sign-in is Not verified |

**Metorite tools that Linear's MCP does not have** (Metorite leads here): `my_work`, `claim_task`, `release_task`, `start_agent_run`, `post_agent_activity`, `finish_agent_run`, `get_agent_run`, `list_agent_runs`, `ask_on_task`, `record_outside_decision`, `preview_move_tasks`, `move_tasks`, `request_task_archive`, `delete_task`, `list_apps`, `describe_app`, `app_read`, `app_write`. These are the lease, the run ledger and the question path.

---

## 5. Where Metorite goes beyond Linear

Short list, so the proposals do not undersell the product.

- **Many agents of many members on one board**, with one exclusive lease per task (D135, D140). Linear's sources do not state this.
- **Chat-first parity.** Each click has a chat tool behind an approve card (`chat_parity.md`).
- **Critical path, critical chain and flow health as tools** (WS-PJP, D168).
- **Custom fields, task types, recurring tasks and a personal lens** on one task store (D52 to D54).
- **A file importer with discard and update mode.**
- **Flat pricing**, with no per-feature plan tiers. Linear gates Triage rules, Insights, Asks and Customer requests to Business or Enterprise.

---

## 6. Agent platform: changes since 2026-10-30

**Cut-off.** `agent_coordination.md` Appendix B was written on 2026-10-30. Source: `linear.app/changelog`, fetched 2026-11-08.

**After 2026-10-30.** One dated entry.

| Date | Linear change | Effect on Metorite | Action |
|---|---|---|---|
| 2026-11-05 | MCP `list_issues` gets a completion-date filter. API rate-limit responses return HTTP 429. Mobile Diffs for iOS and Android | Confirms that a date filter is needed on `list_tasks` (MCP-G19, #1957). MCP-G10 (#844) asks for a retry hint on our write limit | none new. Add the 429 note to #844 |

**Dated before 2026-10-30 but not in Appendix B.** These are still new to our comparison.

| Date | Linear change | Metorite position |
|---|---|---|
| 2026-10-22 | Loops can start from Slack and GitHub, can be saved as workspace templates, and take reasoning levels. A Figma MCP server for Linear Agent. Cursor Origin sync for PR-linked issues | Loops equal recurring agent routines. WS-AUTO (D161, proactive agent) and Studio own that. No new slice. See P8 note |
| 2026-10-08 | Stacked PRs in Diffs and Reviews. AI credit usage explorer and spend limits. Guided Reviews on Basic plans | Spend limits: SW-38 (#1064) caps run time and credits per agent. Code review UI: not wanted, GitHub stays the review surface |
| 2026-09-24 | Adaptive model routing, environment secrets, open-weight models for coding sessions. Team project labels and statuses. Durable project and initiative IDs | Routing: WS-45 (D90). Durable IDs: `short_id` exists for tasks |
| 2026-09-14 | Loops with project, initiative and cycle triggers. Document editing by the agent. Slack updates | As above. Document editing needs P9 |
| 2026-09-03 | Project composer with Linear Agent. Admins approve third-party apps | Mira's planning workflow (PJP-11) covers the composer |
| 2026-07-20 | Introducing Loops | As above |
| 2026-06-11 and 2026-08-20 | Coding sessions in the cloud with Claude Code and Codex, configurable environments, browser tests | Cloud agent connectors (SW-30b, WS-56). Not built |
| 2026-05-14 | Code Intelligence (agent reads repositories) | Metorite reads GitHub through the App with no Contents permission (G-9). Not wanted for now |

**Developer-page items (no date).** `linear.app/developers/agents` lists `delegate` set by assigning to an agent, the scopes `app:assignable` and `app:mentionable`, `actor=app`, a `PermissionChange` webhook, `customer` and `initiative` scopes, and a rule that agents cannot be project leads. These match Appendix B (delegate, app user). The `customer` and `initiative` scopes point at P2 and P6. I could not confirm which of these are new since 2026-10-30. The page has no dates.

---

## 7. Proposed tasks (15, biggest value first)

Sizes: S under 1 day of agent work, M a few days, L a week or more. "Owning area" is the Metorite project. Every proposal must go through the owning spec first. Nothing here reopens a decision.

| # | Proposed task | Linear features it covers | Owning area | Existing task | Size |
|---|---|---|---|---|---|
| P1 | **Cycles.** Time-boxed cycles for a project or space: auto-create, optional cooldown, rollover, auto-add, a cycle graph and a capacity hint. Decide first, in SP-0, whether sprints reopen the Scrum non-goal. | Cycles, cooldown, rollover, auto-add, cycle graph, burn-up | Projects/Tasks App | #19 (SP), #959 (SP-0), #977 (WM) | L |
| P2 | **Milestones, initiatives and a roadmap.** Real milestone objects with progress. A goal object over projects with status, owner and health. A cross-project roadmap on the timeline canvas. A filter and not a team (D87). | Project milestones, initiatives, sub-initiatives, roadmap, project labels, project dependencies | Projects/Tasks App | #981 (WM-2), #977 | L |
| P3 | **Project updates, health and a digest.** The lead posts On track, At risk or Off track. Mira drafts it. Followers get a weekly digest, like Pulse. | Project and initiative updates, health, Pulse, agent-drafted updates | Projects/Tasks App | #988 (LP-1), SW-36 | M |
| P4 | **Triage that works for you.** Suggestions with a reason, plus rules that act on entry and a rotation that skips absent people. One engine with ALN-3. | Triage Intelligence, triage rules, triage responsibility | Projects/Tasks App | #989 (LP-2), #1052 (SW-35), #1377 (ALN-3), #993 (LP-6) | M |
| P5 | **SLAs.** A rule sets a due-by from priority. The card shows time left, Breached and Failed. Fire an event on breach. | SLAs, SLA webhook event | Projects/Tasks App | #990 (LP-3) | M |
| P6 | **Customer requests from the CRM.** Link a task to a CRM customer. Count asking customers and deal value. A view filtered by customer attributes. | Customers, customer requests, importance, revenue, tier | Projects/Tasks App (with CRM) | #991 (LP-4) | M |
| P7 | **Templates.** Task and project templates, "Save as template", a library per work style. | Issue, project and document templates | Projects/Tasks App | #18 | M |
| P8 | **MCP read and write parity with Linear.** Tools to list members, create and list tags, edit and delete a comment, read and add attachments, and list milestones and cycles once P1 and P2 land. Add the 429 retry hint. | `list_users`, `list_issue_labels`, `create_issue_label`, `delete_comment`, attachment tools, `list_cycles`, `list_milestones` | MCP & External Agents | #843 (G9), #413 (G8), #1957 (G19), #844 (G10), #1051 (SW-34b) | M |
| P9 | **Documents over MCP and on the project.** Project docs as linked Knowledge pages (D-PM-13) with read and write tools, so an agent edits a document. | Documents, `get_document`, `create_document`, `update_document`, agent document editing | Projects/Tasks App, MCP & External Agents | #992 (LP-5), #751 (MCP-Gc) | M |
| P10 | **Outbound webhooks.** An admin registers a URL for task, comment, project and SLA events. HMAC signature, delivery id, retries at 1 minute, 1 hour and 6 hours. Tenant-bound (R5). | Webhooks and their signature and retry rules | Platform Core | #1562 (RT-4) | L |
| P11 | **Label groups.** Group tags into single or multi-select groups. Keep one colour vocabulary. | Label groups | Projects/Tasks App | none | S |
| P12 | **Slack and a support-desk door.** Slack intake with `/ask`, Slack notifications and view subscriptions, and project channels. Then one support-desk connector chosen by demand. Reach (D103) stays the one outbound seam. | Asks, Slack, Zendesk, Intercom, Front, Sentry, project Slack channels | Platform Core | #995 (LP-8) | L |
| P13 | **More importers.** Jira CSV, Asana CSV and a Linear export adapter on the I-1 to I-3 pipeline. | Jira, Asana, GitHub Issues, Linear importers | Projects/Tasks App | `project_import.md` §4.2 (no task found) | M |
| P14 | **Views, subscriptions and favorites.** View tabs and display options (OP-12 to OP-14). Add subscriptions to a view or project and a favorites list. | Custom views, display options, subscriptions, favorites | Projects/Tasks App | #839 (OP-12) | M |
| P15 | **A free Insights builder.** Pick a measure, a slice and a segment. Add percentile markers and a cumulative flow chart. Keep it inside Reports, with no second analytics seam. | Insights, burn-up, cumulative flow, lead and triage time | Projects/Tasks App | RA-1 to RA-6 are built. No task holds the builder | M |

**Order.** P7, P3, P5, P11 and P14 are small and unblock a lot. P1 and P2 are the big planning gap, and P8 grows as they land. P10 and P12 serve the outside world and should wait for the dark flags on the current work.

**Items that I did not propose, and why.**

- **GraphQL API.** The MCP is the public API. A second API is a second seam.
- **Teams, sub-teams, team pages.** D87 and D49.
- **Diffs, Reviews and Guided Reviews.** GitHub stays the review surface.
- **Time tracking timer.** D77.
- **Story-point systems.** P-31, unless SP-0 reopens it.
- **Releases.** Low demand. Watch.

---

## 8. Not verified

- **SLA behaviour.** `linear.app/docs/sla` answered 404. I know only from the webhook page that an "Issue SLA" event exists and from task 978's notes that SLAs are rule based.
- **Linear MCP tool names.** Linear publishes no tool list on `linear.app/docs/mcp`. The 31 names are from Speakeasy (third party). The count differs between sources (21 to 31).
- **Linear changelog before 2026-04-23 and before 2026-07-23 detail.** The page I fetched began at 2026-07-23, and page 2 began at 2026-04-23. January to March 2026 were not read. Small fixes under each date were not itemised.
- **Estimate scales, issue-relation types and label-group details.** `linear.app/docs/project-milestones` does not cover them. I took them from my own knowledge of Linear and the docs index. Mark as **Not verified** in Linear's pages.
- **Linear plan gating.** I report the plan limits that pages state (Triage rules, Insights, Asks, Customers). Other limits were not read.
- **Reactions on Metorite comments, project tags, a customer-readable audit log, SAML and SCIM for customers, a favorites list, and a full workspace export.** I did not find them in the code I read. They may exist.
- **Status of built slices in production.** Specs say "BUILT, dark" for many slices. I did not check a flag on a box.
- **`linear.app/developers/agents` dates.** The page shows no dates, so I cannot say what changed after 2026-10-30.

---

## 9. Sources

Linear (first party, fetched 2026-11-08):

- https://linear.app/changelog and https://linear.app/changelog/page/2
- https://linear.app/docs (index of sections)
- https://linear.app/docs/mcp
- https://linear.app/docs/use-cycles
- https://linear.app/docs/triage
- https://linear.app/docs/initiatives
- https://linear.app/docs/pulse
- https://linear.app/docs/linear-asks
- https://linear.app/docs/insights
- https://linear.app/docs/customer-requests
- https://linear.app/docs/custom-views
- https://linear.app/docs/project-milestones
- https://linear.app/docs/api-and-webhooks
- https://linear.app/developers/webhooks
- https://linear.app/developers/agents

Third party (tagged in the text):

- https://www.speakeasy.com/product/mcp-gateway/catalog/linear (31 tool names)
- Search results naming `save_issue`, `list_issue_labels`, `create_attachment` and `list_documents` (various catalogues)

Metorite:

- `project-docs/specs/agent_coordination.md` §3.8 and Appendix B
- `project-docs/specs/project_management_app.md`, `one_projects_app.md`, `project_planning.md`, `project_import.md`, `projects_reports.md`, `github_integration.md`, `org_access_control.md`, `launch_surface.md`
- `project-docs/research/plane_pm_research_2026-08.md`
- `apps/services/agent_edge/agent_edge/tools.py`, `resources.py`
- `apps/skills/skill-projects/skill_projects/writes.py`
- `workbench/control_plane/src/app/projects/components/`
- Metorite tasks, part 1: 2211, 978 and LP-0 to LP-8 (987 to 995), 19, 959, 977, 981, 18.
- Metorite tasks, part 2: 1046, 1047, 1048, 1051, 1052, 1949, 1957, 843, 413, 844, 751, 1562, 839, 1377
