# People intelligence: goals with measures, evidence of work, scores, and decisions about people

**Status: 🆕 PROPOSED (2026-11-22, owner ask in chat). Nothing is built. No slice dispatches until the owner answers §11.**

**Task:** PPL-0, Metorite task 3867 (project Organisation). **Id prefix:** PPL-n, and PPL-Qn for the owner questions (unused on `main` at 2026-11-22, R2).

**Verified against code on 2026-11-22** at `origin/main` `29c32d611`. Every path in §3 was read on that commit.

**Research record:** `project-docs/research/people_intelligence_2026-11.md`. Part 1 covers HR products and the law. Part 2 covers workplace games and fair scores.

**Tier:** INFORMED (`docs/style_ste.md` §2).

**The owner's words (2026-11-22, in chat, shortened):**

```text
"an HR management solution that keeps track of all the work that all the
people are doing across email, projects, and ... all the other surfaces ...
a particular team is not performing, but instead of removing the entire team,
we retain the people who are actually good in that ...
connect to their personal GitHubs ... judge their performance based on the
CRM and emails that they write ...
somebody from some other team might be useful in some other team ...
The context from this should be able to be used by external agents ...
appraisals, moving people across teams, hiring and firing ...
set up a company OKR for a week, for a month, for a quarter ...
know if somebody is misaligned and need to align them or change them."

"a system where it can be gamified to some extent, where people can see
scores, there's a leaderboard, and understand how they can improve their own
scores ... it should not be with a global perspective because sales,
marketing, and software development are all very different kinds of roles
... those things can be scored fairly."
```

---

## 0. Read this first: the ask meets four recorded rules

**Bottom line.** Metorite can build most of the ask on seams that exist. Three parts reverse recorded decisions, and one part needs a new kind of connection. So this spec splits the work into four tiers. The owner can accept each tier alone.

| # | The part of the ask | The rule it meets | Where the rule lives | Its fence today |
|---|---|---|---|---|
| C1 | Key results, KPIs, a weekly or monthly goal, a check-in | "There is no team goal, no key result and no check-in record." D199.2 refuses those words | `project_management_app.md` §13.6a (owner, 2026-11-09), D199 | None |
| C2 | Judge a person, keep the strong people of a weak team, find who is misaligned, score people, a leaderboard | "No score for any person. No table, no report and no view ranks or counts people" | D192.5 (ALN-Q5), D-PC-14, `people_center_app.md` §3.6 and §10. Also `proactive_loop.md` §8.3, `waiting_on_me.md`, `crm_conversational.md`, `project_planning.md` | Planned only: the ALN-7 schema scan in `test_align_findings.py` (not built). `proactive_loop.md` plans a check that refuses the entity type `person` |
| C3 | Judge a person by the emails they write | Only the mailbox owner and the members with a grant on a shared mailbox read a mailbox | `mailbox_predicate` (`routes/email/mailbox_access.py:103`), D12 | The R8 suites of the mailbox seam |
| C4 | Read a person's personal GitHub | The GitHub connection is one App that an admin installs on the company's organization | `github_integration.md` (G-1 to G-9) | None for personal accounts |

**The four tiers.**

| Tier | What it gives | Which conflict it needs the owner to settle |
|---|---|---|
| **A. Goals with measures** | Company, team and project goals at week, month, quarter and year. Each goal has 1 to 5 numbers with a target. Metorite shows how much of the work serves each goal, and which work serves none | C1 only. Two parts need no change at all (§4.7, rung 1) |
| **B. Evidence of each person's work** | A person's work, skills and reviews, built from the work itself. A lead and HR read it. AI drafts a review from the evidence, and a person writes the rating | C2, C3 and C4. Skills from evidence and "who knows X" need none (§5.4, §5.5) |
| **S. Scores that people can see and improve** | A scorecard for each person, fair inside their role. Each person sees how to raise their score. Teams compete on boards, and people of one role may opt into a role board | C2, in its strongest form. It is a separate choice (§5a, PPL-Q12) |
| **C. Decisions about people** | A team move, a role change, a hire and an exit become decision records with a named person who decides. An outside agent reads the evidence and proposes. It never applies | C2, plus the legal rows of §7 |

**Recommendation in one line.** Accept tier A now. Accept tier B with the bounded change of §5.1 (option B-2). Accept tier S as a pilot with one team, behind a wall that keeps the score out of reviews, pay and exits. Accept tier C after a lawyer reads §7.

---

## 0a. The plan in one page

**Three rules hold the whole plan.**

1. **Measure output against goals, never activity.** A task done, a deal won and a pull request merged count. Typing, time online and message counts never count.
2. **Compare like with like.** A person is compared with their own target and their own past. A team is compared with teams. People of unlike roles never share one scale.
3. **A person decides.** A named person writes each rating and makes each move. The game score decides nothing.

| Phase | What people get | Rungs | Owner answers | Why this order |
|---|---|---|---|---|
| **1. Everyone works to the goals** | Goals at week, month and quarter, with measures. The share of work under each goal, and the work under none | 1, 2 | PPL-Q1 to PPL-Q4 | Scores and reviews measure against goals, so goals come first |
| **2. Know your people** | Skills from evidence, "who knows X", my work page, personal GitHub by choice | 3, 5 | PPL-Q7 | Most of it reverses no decision |
| **3. Scores that people improve** | My scorecard with next steps and team boards. Then a pilot of role boards, seasons and badges with one team | 4, 6 | PPL-Q12 to PPL-Q14 | It motivates people, and the pilot measures whether it works for us |
| **4. Fair reviews** | Reviews from cited evidence, and the team view that keeps the strong people of a weak team | 7, 8 | PPL-Q5, PPL-Q8 | It uses the evidence and the measures of phases 1 to 3 |
| **5. Decisions and agents** | Decision records, outside agents that propose, and hiring gaps | 9, 10, 11 | PPL-Q9 to PPL-Q11 | The highest legal weight, so it waits for a legal review |

---

## 1. The ask as use cases

| Id | Use case | Who acts | Tier |
|---|---|---|---|
| UC-1 | The company sets goals for a week, a month and a quarter, and each team and project links its work to them | Founder, leads | A |
| UC-2 | Each goal shows numbers that move by themselves: revenue from the CRM, tasks done from Projects, a figure from the books | Founder, leads | A |
| UC-3 | The founder sees the work that serves no goal, and the goals that no work serves | Founder | A |
| UC-4 | A member sees how their own work maps to the goals | Every member | A |
| UC-5 | The founder asks "who has done firmware work?" and gets people with the tasks and pull requests that show it | Founder, leads | B (fits today) |
| UC-6 | A team misses its goals. The founder sees each member's work side by side, keeps the strong people, and moves or lets go the rest | Founder, HR | B, then C |
| UC-7 | A lead writes an appraisal. AI drafts it from the evidence with a citation for each claim, and the lead writes the rating | Lead | B |
| UC-8 | A person in team A has the skills that team B lacks. Metorite shows the match with its evidence | Founder, leads | B |
| UC-9 | A member joins a personal GitHub account, so their outside work counts as evidence of skill | The member, by choice | B |
| UC-10 | The goals need skills that nobody has. Metorite drafts a role brief for a hire | Founder | C |
| UC-11 | An outside agent (Claude, ChatGPT) reads the evidence of one person and drafts a proposal: a move, a raise or an exit | Founder, HR, through their own agent | C |
| UC-12 | Each person sees a score that is fair to their role and learns how to raise it. Teams compete on a board | Every member | S |

---

## 2. The idea: evidence of work, not watching people

**Metorite already holds the work.** The task store holds what each person did. The GitHub App holds the pull requests. The CRM change history holds who moved each deal. Knowledge holds who wrote each page.

Most HR tools ask people to type this in. Metorite can read it.

**So the unit is an evidence item, not a score.** An evidence item points to one work artifact. It names the person's role in it, the goal it served and the date. It is a pointer, never a copy. A reader opens it through the seam of the app that owns it, so a reader sees only what they could already open.

**Four rules make this safe to build.** The research of §13 supports each one.

1. **Output, never activity.** Evidence is a finished artifact: a task done, a pull request merged, a deal won, a page written. It is never typing, time online, message counts, idle time, or a screenshot.
2. **AI drafts and cites. A person decides.** AI writes a summary with a link for each claim. A named person writes each rating and makes each decision.
3. **The subject sees it first.** A person sees every evidence item that a reviewer sees about them. The person can add context and dispute an item.
4. **No new read right.** The evidence view adds no way to see a row that the reader cannot already see. It arranges what the reader can see by person.

---

## 3. What exists, and the seam each part extends

`building_blocks.md` §6.1 binds this spec. Each part extends a block of §2 of that spec. It adds no parallel seam.

| Need | The seam that exists | Path on `main` | How this spec extends it |
|---|---|---|---|
| Goals | The Goal task type with a rank, an owner and a target date. One epic serves one Goal (D199) | `project_management_app.md` §13.6a, WM-5 (task 984, in progress), RM-1 (task 2528) | Tier A adds measures, a period and a parent goal |
| Roll-up of progress | SP-9 roll-up over linked epics | `project_management_app.md` §13 | Tier A adds the share of work by goal |
| Numbers from data | Projects analytics reads, the semantic model, the number check | `projects_ai_chat.md` §13 (S7 reads), `semantic_model.md` (D226), `packages/acb_common/acb_common/number_check.py` | A measure reads one of these. It adds no second arithmetic |
| The person record | `people`, `people_skills`, `people_resumes` | `infra/postgres/149_people.sql`, `176_people_skills.sql`, `people_center_app.md` §3 | Tier B adds a source and evidence to a skill |
| Who may report on whom | `may_report_on`: an admin reports on all, a lead on their team, a member on self | `routes/projects/report_scope.py:170`, `projects_reports.md` §7.1 | Tier B uses it for the evidence view. No new grant |
| The HR read tier | `can_read_hr_fields`, the `admin:members:read` grant | `routes/tasks/core.py:103`, `people_center_app.md` §4 | Tier B uses it for reviews |
| Teams | A team is an `org_group` (D204.8), with `org_group_member.role = 'lead'` | `projects_reports.md` §7.1 rule 4 | Team goals, team boards and team views read it |
| Code work | The Metorite GitHub App and the task references | `infra/postgres/282_github_app.sql`, `255_pm_task_refs.sql`, `github_integration.md` | Tier B reads pull requests of company repositories |
| Deal work | CRM change history | `routes/crm/change_log.py`, `crm_conversational.md` §4 | Tier B reads who moved a deal |
| Targets and games in the CRM | Zoho parity means "targets and KPIs" (Zoho Motivator) | `crm_platform.md` (the Gamescope note) | Tier S is the one place for targets and games. The CRM builds no second one |
| Mail | The one mailbox seam | `routes/email/mailbox_access.py:103` (`mailbox_predicate`) | Tier B reads only mail that a record or a task links. See §5.9 |
| Waits and approvals | "Needs you" | `routes/shell/waiting.py`, `waiting_on_me.md` | A review to write and a decision to sign are kinds of the seam |
| Cards in My Day | The feed registry | `routes/shell/feed.py`, `my_day_feed.md` | A number to enter, a skill to confirm and a next step are card kinds |
| Chat twins | The chat parity inventory | `packages/acb_skills/acb_skills/chat_parity.py`, `tool_packs.py` | Each act gets a chat tool |
| Outside agents | The four door tools and the app manifest (D136, D205.9) | `external_agent_access.md` §19, `acb_common/app_manifests/` | Tier C adds door ops and a `people://` resource |

---

## 4. Tier A: goals with measures, a cadence and alignment

### 4.1 A Goal gains measures

**Today a Goal has a text field, "how we know it is met"** (`project_management_app.md` §13.3). Tier A turns that text into 1 to 5 numbers. Each number is a **measure**.

A measure has these fields:

| Field | Meaning | Example |
|---|---|---|
| Name | What it counts | New revenue booked |
| Unit | ₹, %, count, days | ₹ |
| Direction | Up is good, or down is good | Up |
| Start value | The value when the goal starts | 0 |
| Target | The value that meets the goal | 40,00,000 |
| Source | Where the value comes from (§4.3) | CRM, deals won in the period |
| Owner | The member who answers for it | The sales lead |

**A KPI is a measure with no end.** A key result is a measure with a target in a period. One record holds both, so the product has one word for a member: **measure**. The words "key result" and "OKR" stay in help text only, to keep D199.2's rule of few terms. PPL-Q1 asks the owner.

### 4.2 A period and a parent

**Each Goal has a period:** week, month, quarter or year. A goal of a week starts on Monday. The other periods start on the first day of the calendar period. An org setting can move the quarter start (for example, April for an Indian financial year).

**A Goal may support one parent Goal.** A company goal for the quarter has team goals for the month under it. A team goal may have project goals for the week under it. The tree is at most 3 levels deep. A goal of a shorter period may support a goal of a longer period, never the reverse.

**A Goal may belong to a team.** Its owner is a member, and it may name one `org_group` as its team. D199.2 refused team goals, so PPL-Q3 asks the owner.

### 4.3 Where a measure gets its value

| Source | How it reads | Example |
|---|---|---|
| **Projects** | One S7 read of `projects_ai_chat.md` §13 over the epics under the goal | Tasks done, median cycle time |
| **CRM** | A verified query of the semantic model (`semantic_model.md`) | Deals won, pipeline added |
| **Connector** | A verified query on a profiled connection (SEM rung 3) | Collections from Zoho Books |
| **Manual** | The owner types a number | Customer NPS from a survey |

Rules:

1. A sourced value passes the one number check (`number_check.py`) before it is saved.
2. A refresh runs on the workflow engine (`building_blocks.md` §2). It adds no scheduler.
3. A manual measure asks its owner for one number per period, with one My Day card. The card holds one field. There is no check-in form. PPL-Q4 asks the owner.
4. Each value keeps its history, so the goal shows a trend line.

### 4.4 Alignment: the work under each goal, and the work under none

**Alignment is a fact about work, not about a person.** It reads the task store only.

- **Share of work by goal.** For a period, it takes the tasks done and groups them by the goal above their epic. A task with an estimate counts its estimate. A task with no estimate counts 1.
- **Work under no goal.** It lists the done and open tasks of the period whose epic serves no goal, or that sit under no epic. Personal tasks never count (D192.5).
- **Goals with no work.** It lists the goals of the period with no open epic.
- **Suggest the link.** Mira suggests the epic of an unlinked task, and one tap accepts. It extends the one project suggester (LP-2, task 989, ALN-3). It never moves a task by itself (D192.3).

**Visibility.** Each list reads through the task store predicate of D12. A reader who cannot open a task sees a count, "N tasks you cannot open", and never the title.

### 4.5 A member sees their own alignment

My Day and My Profile show one line for the member: "This month, 14 of your 20 done tasks served a company goal." It also lists the member's own unlinked tasks with a suggested epic.

**Only the member sees this line in tier A.** A lead sees the team total and never the line of one person. A per-person line for a lead is tier B (§5.7).

### 4.6 The data shape

R1: the builder takes the next free migration number at build time. R5: each new table is tenant-scoped under FORCE row level security. R6: each column is nullable, with a default.

- **The Goal task** gains `goal_period` (`week`, `month`, `quarter`, `year`), `goal_period_start` (a date), `goal_parent_id` (a Goal task id) and `goal_team_id` (an `org_group` id). WM-5 decides whether these are columns of `pm_tasks` or a side table. This spec follows WM-5.
- **`pm_goal_measures`** (new): `id`, `organization_id`, `goal_task_id`, `name`, `unit`, `direction`, `start_value`, `target`, `source_kind`, `source_ref`, `owner_email`, `created_at`. A measure takes the visibility of its goal task. It has no grant of its own. Tier S adds `subject_email` for a measure of one person (§5a.2).
- **`pm_goal_measure_points`** (new): `measure_id`, `organization_id`, `at`, `value`, `run_id` (sourced) or `entered_by` (manual). `entered_by` is a record of the act. No query groups by it (ALN-Q5).
- **Alignment** is a read, not a table. It has no cache in v1.

### 4.7 Tier A slices

Rung 1 needs no owner decision beyond WM-5. Rung 2 needs PPL-Q1 to PPL-Q4.

| Row | Rung | Scope | Done when | Risk | Gate | Depends on |
|---|---|---|---|---|---|---|
| **PPL-A1** | 1 | The alignment read (§4.4): share of done work by goal, work under no goal, goals with no work. Server only, behind `GOAL_ALIGN_ORGS` (default empty) | A space with 2 goals, 3 linked epics and 4 unlinked tasks gives the right shares and lists. A hidden task counts and is never named. `tests/unit/test_goal_alignment_r8.py` (new) passes | STANDARD | AGENT-SAFE | WM-5 |
| **PPL-A2** | 1 | The Alignment panel in the Roadmap view: one bar per goal, the "work under no goal" list, and the one-tap link from the suggester. Chat twin `goal_alignment` | A member links an unlinked task in one tap, and the bar moves. Captures in light mode, compact density and at 390 px. Chat parity row passes | STANDARD | AGENT-SAFE | PPL-A1, RM-1 |
| **PPL-A3** | 1 | The member's own line (§4.5) in My Day and My Profile | A member sees their own share and unlinked tasks. A second member never sees it. `tests/unit/test_goal_alignment_r8.py` covers the self rule | STANDARD | AGENT-SAFE | PPL-A1 |
| **PPL-A4** | 2 | Period, parent and team on a Goal (§4.2). The migration and the Goal form | A quarter goal takes two month goals. A fourth level is refused. A week goal cannot parent a quarter goal. R8 test passes | HIGH (migration) | AGENT-SAFE after PPL-Q2, PPL-Q3 | WM-5 |
| **PPL-A5** | 2 | Measures with a manual source (§4.1, §4.3 rule 3), the two tables, the card for a number, and the chat twin `set_measure` | A goal with 2 measures shows progress against target. The card saves a number in one tap. R8 test passes | HIGH (migration) | AGENT-SAFE after PPL-Q1, PPL-Q4 | PPL-A4 |
| **PPL-A6** | 2 | Sourced measures: Projects and CRM, refreshed on the workflow engine, each value through the number check | A CRM measure moves when a deal is won, within one refresh. A value that fails the number check is not saved, and the goal says why | HIGH (new job) | AGENT-SAFE after PPL-Q1 | PPL-A5, SEM-1a |
| **PPL-A7** | 2 | The goal tree view: company to team to project goals, each with its measures and its alignment bar | The owner sees a quarter goal, its month goals and their measures on one page at 390 px and at desktop width | STANDARD | AGENT-SAFE | PPL-A4, PPL-A5 |

**Demo of rung 1.** The owner opens Roadmap. Each goal shows its share of the last 30 days of done work. A list shows 12 tasks under no goal. The owner links 3 of them in 3 taps.

**Demo of rung 2.** The owner sets "₹40 lakh new revenue this quarter" with a CRM measure, and two team goals for November under it. A won deal moves the bar the same hour.

---

## 5. Tier B: evidence of each person's work

### 5.1 The score line: three options

**C2 is the main decision of this spec.** The owner wants to judge people. Five specs and one decision say Metorite keeps no score of a person. The owner can change that line. These are the options for reviews and decisions. Tier S (§5a) is a separate choice about a game.

| Option | What it allows | What it costs |
|---|---|---|
| **B-1. Keep D192.5 as it is** | Skills from evidence (§5.4), "who knows X" (§5.5), and a member's own evidence page. A review lists evidence by task, never by person | It does not meet UC-6, UC-7 or UC-8. A lead still builds an appraisal by hand |
| **B-2. A bounded change (recommended)** | One new surface, People Review, may show the work of one person to the readers that `may_report_on` allows. It shows facts about the work and the ratings that people write. It shows no score that a machine makes, and it never orders people by a number | It amends D192.5, D-PC-14 and `people_center_app.md` §3.6 for one surface. Every other surface keeps the rule, and its fence |
| **B-3. Full people analytics** | AI scores each person, ranks the people of a team, and the score feeds reviews, pay and exits | High legal weight: high-risk under the EU AI Act from 2 Dec 2027, automated decisions under GDPR Article 22, bias audits under NYC Local Law 144. A score that decides pay gets gamed (§5a.1) |

**What B-2 allows and refuses, exactly.**

| Allowed in People Review | Refused in People Review and in decisions |
|---|---|
| A count of facts about one person's work: "18 tasks done, 14 of them under a goal", "6 pull requests merged" | A composite score made by a machine ("performance 7.2"). The game score of §5a stays behind its wall |
| The attainment of each measure of a person's scorecard (§5a.2), as a fact | The scorecard total, the band and the board place |
| A rating that a named person writes in a review cycle | A rating that AI picks |
| Members of one team side by side, in name order | Any list of people sorted by a number |
| A team total, when the team has 5 or more members | A team total for a team of fewer than 5, shown to anybody but the members |
| An AI summary of one person's evidence, with a link for each claim | An AI summary with a claim that has no link |

**Every other surface keeps D192.5 as it is.** My Day, Projects, Waiting, the alignment checker, the CRM and the proactive loop keep "no score for any person". Their fences stay. The People Review tables and the tier S tables go on one allowlist, with a reason, in the fence of §9.

### 5.2 The evidence item and its sources

An evidence item is a pointer: `{person, kind, ref, role, at, goal_task_id?}`. A read resolves `ref` through the seam of the owning app, as the reader.

| Source | Evidence kind | The person's role | Status |
|---|---|---|---|
| Task store | A task done, a task reviewed, an epic accepted | Assignee, reviewer, lead | Allowed |
| GitHub App | A pull request merged, a review given | Author, reviewer | Allowed, company repositories only |
| CRM change history | A deal moved or won, an account created | Actor | Allowed |
| Knowledge | A page written or kept current | Author | Allowed |
| Mail linked to a record | A thread on a CRM record or a task reference | Sender | Allowed. See §5.9 |
| Personal GitHub | A public repository, a pull request to a public project | Author | Opt-in. See §5.8 |
| Reviews | A review written by a lead or a peer | Subject | Allowed |
| Chat with Mira | Private | none | **Refused** |
| A mailbox, as a whole | Private (C3) | none | **Refused** |
| Time online, typing, idle time, screenshots, app use | Activity, not output | none | **Refused** |
| Message counts, mail counts, hours in meetings | Activity, not output | none | **Refused** |
| Sentiment, emotion, tone | Banned at work in the EU (AI Act Art. 5(1)(f)) | none | **Refused** |

### 5.3 Who sees what

| Reader | Sees | The check |
|---|---|---|
| The subject | Every evidence item that any reviewer sees about them, the AI summaries, and each released review | The self predicate (`people_center_app.md` §4.1) |
| A team lead | The evidence of each member of the teams they lead | `may_report_on` (`report_scope.py:170`) |
| An admin with `admin:members:read` | The evidence of every person | `may_report_on` and `can_read_hr_fields` |
| A peer | Nothing, unless a review cycle asks them to write a peer review of one person | The review cycle |

**Each item resolves as the reader.** A lead who cannot open a task sees "1 item you cannot open" and no title. So the view adds no read right (§2 rule 4).

**A review draft is private until release.** The subject sees the evidence at all times, and sees a review when the cycle releases it.

### 5.4 Skills from evidence (fits today)

Metorite reads a person's evidence and proposes skills. Each proposed skill names its evidence items and a confidence (low, medium, high). The subject gets one card: "Add 'firmware' to your skills? From 6 tasks and 2 pull requests." One tap accepts, and the subject can edit or refuse it.

- `people_skills` gains `source` (`self`, `admin`, `resume`, `evidence`), `evidence_refs` and `confirmed_at`.
- A skill that the subject refused is never proposed again for 90 days.
- This fits `people_center_app.md` §3.3 (capability). It needs no change of D192.5, because a skill is a capability, not a score.

### 5.5 Find a person for a team (fits today)

**"Who knows X?"** The founder asks in the chat or in the People app. Metorite answers with the people whose confirmed skills or evidence match, each with the 3 strongest evidence items. The list sorts by how well the evidence matches the need. That is an assignment question, which `people_center_app.md` §1 already owns. D-PC-14 allows it: "ranking TASKS by risk is the product".

It answers UC-5 and most of UC-8. "Team B lacks X" becomes "find people with X outside team B".

### 5.6 Reviews (appraisals)

**A review cycle** has a name, a period, the people in scope, the reviewers, a rating scale or no scale, and a release date. The org picks its scale. PPL-Q8 asks the owner for the default.

**The steps of a cycle:**

1. HR starts a cycle. Each reviewer gets a "Needs you" item for each review to write.
2. The subject writes a self-review first. Mira drafts it from the subject's own evidence and their own mail, and the subject edits it.
3. The reviewer opens the review. Mira drafts a summary from the evidence. Each claim links to its item. The reviewer edits it and writes the rating.
4. The reviewer submits. HR releases the cycle on the release date. The subject reads the review and may add a reply.

**AI never picks a rating.** The rating field is empty until the reviewer fills it. The draft never holds a number. Lattice ships the same rule (research Part 1 §2.2).

### 5.7 The team view: keep the strong people of a weak team

**This is UC-6, and it needs option B-2.** People Review shows one team. Each member is one column, in name order. Each column holds:

- the goals that the member's work served this period, with the share of their work under a goal
- the attainment of each measure of the member's scorecard (§5a.2)
- the counts of facts: tasks done, pull requests merged, deals won, pages written
- the confirmed skills
- the last released rating and its date
- a link to the full evidence

There is no total score and no sort by number. A founder reads the columns and decides. The decision becomes a record (tier C).

**The same view answers "who is misaligned".** The share of a member's work under a goal is a fact. A low share starts a talk, and it never moves a person by itself.

### 5.8 Personal GitHub (C4)

**The member connects, never the company.** The rules:

1. Only the member starts the connection, from My Profile. No admin can ask for it, and no flow needs it.
2. Metorite reads public data only: public repositories and pull requests to public projects.
3. The member picks which repositories count. Nothing else is read.
4. The evidence shows on the member's own page first. A reviewer sees it only when the member marks it shared.
5. The member can disconnect. Metorite then deletes every item from that account.
6. A refusal writes nothing and changes nothing. No view shows who did not connect.

**Why so strict.** India's DPDP Act allows work data for "purposes of employment" with no consent (s.7(i)). A personal account sits outside that purpose, so it needs free consent that the member can take back. 27 US states bar an employer from asking for a personal account (research Part 1 §3).

### 5.9 Mail (C3)

**Recommendation: no person reads another member's mailbox.** `mailbox_predicate` stays as it is.

Mail still counts as evidence in two ways:

1. **Mail on a record.** A thread that the CRM timeline or a task reference links is evidence. A reader sees it when the CRM or the task lets them.
2. **The subject's own mail.** In a self-review, Mira reads the subject's own mailbox for the subject, and the subject picks what to share.

**Why.** A third-party read of a mailbox is monitoring of communication. New York needs a written notice for it. India's DPDP Act asks for the least data that the purpose needs. It also breaks D12, and every member would write less in mail. PPL-Q6 asks the owner.

### 5.10 Tier B rungs

Tier B gets slice rows when the owner answers PPL-Q5. Until then each rung is a plan.

| Rung | What it gives | Owner answer needed | Demo |
|---|---|---|---|
| 3 | Skills from evidence (§5.4) and "who knows X" (§5.5) | None | The owner asks "who has done firmware work?" and gets 3 people with the tasks and pull requests that show it |
| 5 | My evidence page (§5.2, self only) and personal GitHub (§5.8) | PPL-Q7 | A member opens My Profile, sees their work of the quarter by goal, and connects a GitHub account |
| 7 | Review cycles with AI drafts (§5.6) | PPL-Q5 (B-2), PPL-Q8 | A lead writes a review from a cited draft. The member reads it on release |
| 8 | The team view (§5.7) | PPL-Q5 (B-2) | The owner opens one team and sees each member's goals, measures, facts and skills side by side |

---

## 5a. Tier S: scores that people can see and improve

**The owner's second message asks for a game:** scores that people see, a leaderboard, a way to improve, and fair scores across unlike roles. Research Part 2 holds the evidence. This tier follows it.

### 5a.1 What the evidence says

**Bottom line.** A game helps a little when people choose to play, compare with themselves and win as a team. It hurts when a public rank is forced on people, when unlike roles share one scale, and when the score decides pay or a job.

| Finding | Source | Design rule here |
|---|---|---|
| A game raised mood only for the people who agreed to play. For the others, it lowered mood | Mollick and Rothbard 2014, field experiment, 200+ salespeople (peer-reviewed) | Opt-in (§5a.5) |
| Removing rank feedback raised sales by about 11%, because the leaders relaxed | Barankay, randomized, 1,754 furniture salespeople | Private view first. Seasons that reset |
| Public rank cut output in a factory. Private rank did nothing | A sweater-factory study (exact citation UNVERIFIED) | No full public ranking |
| Rank for status raised effort. Some people sabotaged others or cheated. A strong team identity cut the sabotage | Charness, Masclet and Villeval 2014 (peer-reviewed) | Team boards first |
| Hard sales targets tied to pay led to about 3.5 million false accounts and a USD 185M fine | Wells Fargo, 2016 | The wall (§5a.6) |
| Forced ranking ended after engineers described sabotage of colleagues | Microsoft, 2013 | No forced bottom |
| OKRs are graded 0.0 to 1.0 and kept out of reviews, after an early pay link led to gaming | Google | The wall |
| "One metric tells everything" is a myth | The SPACE authors, 2021 | No single count per engineer |
| Streak counters rewarded empty one-commit days, and GitHub removed them | GitHub, 2016, and an arXiv study | No streaks |
| A regulator fined fine-grained speed tracking of each worker EUR 32M | CNIL v Amazon France Logistique, 2023 | No speed tracking |
| Sales game vendors report gains, but nobody found an independent test | Ambition, Spinify, SalesScreen, Zoho Motivator | A pilot that we measure (§5a.7) |

### 5a.2 The scorecard: fair inside a role

**Each person has one scorecard for each season.** It holds 3 to 5 measures of tier A that belong to that person (PPL-Q13). Each measure serves a team goal.

1. A role template gives the measures. The lead and the person agree on each target at the start of the season.
2. The attainment of a measure is the actual value divided by the person's own target. The cap is 120%, so volume cannot win alone.
3. Each volume measure has a quality pair. A drop in quality cuts the score more than volume adds.
4. The score is the weighted attainment, from 0 to 100. The page shows each weight.
5. The score is plain arithmetic, and no model sets it. AI explains the score and suggests a next step. AI never sets or changes a score.

**Why attainment makes roles fair.** A sales rep at 90% of quota and an engineer at 90% of the work they committed each stand at 90% of their own plan. So the fairness moves into the targets, and the lead and the person set those together.

**The role templates.** An org edits a template, or adds its own.

| Role family | Outcome measures | Quality pair | Source |
|---|---|---|---|
| Sales | Revenue won against target, pipeline added | Revenue kept after 90 days, discount inside policy | CRM |
| Marketing | Qualified leads sourced, pipeline influenced | Lead-to-deal rate | CRM |
| Engineering | Committed work delivered in the cycle: the tasks and epics done, of those committed | Changes reverted or reopened, reviews given to others | Projects, GitHub App |
| Support | Tickets resolved inside the target time | Tickets reopened, customer rating | Projects (the ticket type) |
| Operations and custom | The measures of the team's goals | A pair that the lead names | Any source of §4.3 |

**Engineering gets no count of commits, lines or pull requests.** Its measure is the work that the person committed to in the cycle. Lead time and change failure stay team measures (SPACE, DORA).

### 5a.3 How a person improves their score

The scorecard page is a coach, not a judge. It shows:

- the score, and its trend against the last season
- each measure with its target and its attainment
- the 2 largest gaps, each with the evidence behind it
- one next step for each gap, which Mira suggests. For example: "2 deals sit in Proposal for 20 days. Book a call."

A next step is a suggestion. The person may make it a task with one tap.

### 5a.4 Boards: who sees which score

| Board | Who is on it | Who sees it | Rules |
|---|---|---|---|
| **My scorecard** | Me | Me only | Always on in an org that turned tier S on |
| **Team board** | Teams | Every member | Team goal attainment. It is fair across roles, because each team has its own targets |
| **Role board** | People of one role family who opted in | The people on that board | At least 8 people opted in, and an admin may raise the floor. It shows the top 3 and "your band": top third, middle or rest. It never shows a full list or the names at the bottom |
| **Recognition** | Anybody | Every member | Badges for effort and quality: "closed every review", "helped 3 people on their tasks", "kept the docs current". No rank and no streak |
| **Lead view** | The members of the lead's teams | The lead | The attainment of each measure, as in §5.7. Never the score total, the band or the board place |

**A season is a month or a quarter**, as the org sets. Each season starts at zero. Old seasons stay in the person's own history only.

**No board compares unlike roles.** People of different role families meet only on the team board.

### 5a.5 Opt-in, and leaving

1. An org turns tier S on with `PEOPLE_GAME_ORGS`. The switch starts empty, so it is off.
2. In an org that turned it on, each person gets a private scorecard.
3. Each person chooses to join a role board, and may leave at any time.
4. A person who leaves goes off the board at once. Nobody sees who left.
5. Where a works council exists (Germany), the admin confirms a works agreement before the switch turns on.

### 5a.6 The wall between the game and decisions

**The score, the band, the badges and the board places never feed a review rating, pay, a decision record or an outside agent.** Wells Fargo, Microsoft and Google each show what happens when they do.

- The review and decision surfaces of tiers B and C read the measures and the evidence. They never read the score tables.
- No door op for an outside agent returns a score, a band or a board place.
- The fence `tests/unit/test_score_wall.py` makes this fail in CI (§9).
- The opt-in page says the rule in plain words, so people trust it.

PPL-Q12 (c) lets the owner remove the wall. This spec recommends against it.

### 5a.7 A pilot first

One willing team plays one season before any other team. The pilot measures four things:

1. The share of the team that opts in, and that stays in at the end.
2. The change of the team's goal attainment against the season before.
3. The engagement of the bottom third: do they stay on the board, and do their measures rise?
4. Gaming: does any quality pair fall while its volume measure rises?

The owner reads the four numbers and decides the next step. PPL-Q14 asks which team.

### 5a.8 The data shape

R1, R5 and R6 bind as in §4.6.

- **A measure of one person** is a `pm_goal_measures` row with `subject_email` set. It links to the team goal that it serves. The subject and the readers that `may_report_on` allows see it.
- **`people_scorecards`** (new): `organization_id`, `subject_email`, `season_start`, `season_end`, `template`, `weights`, `agreed_at`.
- **`people_score_seasons`** (new): the score of a closed season, for the subject's own history. The live score is a read, not a row.
- **`people_badges`** (new): `subject_email`, `kind`, `season_start`, `evidence_refs`.
- **`people_board_optins`** (new): `subject_email`, `role_family`, `joined_at`. A leave deletes the row.

### 5a.9 Tier S rungs

| Rung | What it gives | Owner answer needed | Demo |
|---|---|---|---|
| 4 | My scorecard with next steps (§5a.2, §5a.3) and the team board | PPL-Q12, PPL-Q13 | A member sees their score, its 2 gaps and a next step. Every member sees the team board |
| 6 | Role boards, seasons and badges, as a pilot with one team (§5a.4, §5a.7) | PPL-Q12, PPL-Q14 | The pilot team plays one season. The owner reads the four pilot numbers |

---

## 6. Tier C: decisions about people, and outside agents

### 6.1 The decision record

**A decision about a person** is a team move, a role change, a pay change, a hire request or an exit. Each one becomes one record.

| Field | Meaning |
|---|---|
| Subject | The person |
| Kind | Move, role, pay, hire request, exit |
| Proposed by | A member, or a member's agent |
| Decided by | A named member. Never an agent (§6.2) |
| Evidence shown | The evidence items, measures and reviews that the decider saw. Never a game score (§5a.6) |
| AI part | None, summary, or proposal |
| Reason | Free text by the decider |
| Told to the subject | The date the subject was told |

**The write stays an ask.** A member or role write always asks a person (`building_blocks.md` §4.9). A decision record moves no membership and no role. A team move needs a person to change the `org_group` after the record. Pay stays outside Metorite (`people_center_app.md` §3.6).

**Why a record.** GDPR Article 22 and the CJEU SCHUFA ruling need a real person with the facts and the power to decide. California's ADMT rules (from 1 Jan 2027) and Colorado SB 26-189 need a way for the subject to ask again. The record holds the proof.

### 6.2 Outside agents through the door

An outside agent acts as its member, cut down to the member's scopes (`external_agent_access.md` §19, D136). Tier C adds:

1. **Door ops** in the People manifest: `people.evidence` (class A), `people.review_draft` (class B) and `people.decision_propose` (class C). A class C op makes a card that the member approves.
2. **One person per call.** No op returns the evidence of many people. A bulk export does not exist.
3. **A purpose on each read.** The agent names the purpose (`review`, `move`, `hire`, `exit`). The audit row keeps it.
4. **A scope that starts off.** The MCP scope `people:review` is off for each org until an admin turns it on.
5. **Propose, never apply.** An agent drafts a decision record. A named member decides on the card in Metorite.

### 6.3 Hiring: from the goals to the skills that nobody has

Each goal may name the skills it needs. Metorite compares them with the confirmed skills of the people in the company. A gap with no person becomes a draft role brief. A hire pipeline stays a later step. `people_center_app.md` §10 says it reuses the CRM shape, and this spec keeps that rule.

### 6.4 Tier C rungs

| Rung | What it gives | Owner answer needed | Demo |
|---|---|---|---|
| 9 | Decision records with evidence (§6.1) | PPL-Q5, PPL-Q10 | The owner records a team move with the evidence shown. The subject sees the record |
| 10 | Outside agents through the door (§6.2) | PPL-Q9 | The owner asks Claude, through the MCP, "draft a move for Priya to the CRM team". A card waits in Metorite |
| 11 | Hiring gaps (§6.3) | PPL-Q11 | The owner sees "No one has PCB layout. Goal: ship v3 board" and a drafted role brief |

---

## 7. The law, and the design rules that follow

**A customer may still need legal advice.** This table is a first pass from secondary sources (research record). Nobody verified it with counsel.

| Rule | Where | What it means for Metorite | From | Design rule here |
|---|---|---|---|---|
| DPDP Act s.7(i) | India (first customers) | Work data for employment purposes needs no consent, only the necessary data | About 13 May 2027 | Output only (§2). Personal sources need consent (§5.8) |
| EU AI Act, Annex III point 4 | EU staff | Evaluation, promotion, exit and task allocation are high-risk AI | 2 Dec 2027 | Tiers B, S and C stay off for an org with EU staff until a conformity review |
| EU AI Act, Art. 5(1)(f) | EU | No emotion inference at work | Now | No sentiment source (§5.2) |
| GDPR Art. 22, SCHUFA | EU | A deciding score is an automated decision | Now | No machine score in decisions (§5.1, §5a.6). A named decider (§6.1) |
| BetrVG §87(1) no. 6 | Germany | A works council co-decides a system that can monitor behaviour or performance | Now | A works agreement before tier S or B turns on (§5a.5) |
| CNIL v Amazon France Logistique | France | EUR 32M fine for fine-grained performance tracking | 2023 | No speed or activity tracking (§5.2) |
| NYC Local Law 144 | New York City | A tool for hire or promotion needs a yearly bias audit | Now | A "who knows X" list used for promotion counts. PPL-Q10 |
| California ADMT rules | California | Notice, access and a human appeal for a significant decision | 1 Jan 2027 | The subject sees all (§5.3). The decision record (§6.1) |
| Colorado SB 26-189 | Colorado | Disclose the AI part of an adverse decision within 30 days | 1 Jan 2027 | The "AI part" field (§6.1) |
| Illinois HB 3773 | Illinois | Notice of AI use, and no AI bias in hire, promotion or exit | Now | Notice text in the org setting. PPL-Q10 |
| NY Civil Rights Law s.52-c | New York | Written notice of email monitoring | Now | No mailbox read (§5.9) |
| State laws on personal accounts | 27 US states | No demand for a personal account | Now | Member-started, optional (§5.8) |

**Two org switches hold tiers B, S and C.** `PEOPLE_REVIEW_ORGS` holds tiers B and C, and `PEOPLE_GAME_ORGS` holds tier S. Both start empty. Before an admin turns one on, the admin confirms the staff notice and the regions of the staff. A region in a "wait for review" row keeps the switch off.

---

## 8. Shell manifest (R9)

**Tier A joins Projects.** Goals and measures live in the Roadmap view and on the Goal task. Tier A adds no app.

**Tiers B, S and C join the People app** (`/people`), with the sections "Reviews" and "Boards". My Profile gets "My work", "My scorecard" and "My reviews".

| Manifest part | Value |
|---|---|
| Team | `people` |
| Purpose | See the work of people, see and raise your score, write reviews, and record decisions about people |
| Jobs | "Find a person with a skill" (`/people?skill=`), "See my scorecard" (`/people/me/scorecard`), "Start a review cycle" (`/people/reviews/new`), "Write a review" (`/people/reviews/<id>`), "Record a decision" (`/people/decisions/new`) |
| Search provider | People by skill and by evidence (§5.5) |
| Needs you | A review to write, a skill to confirm, a scorecard target to agree, a decision to sign, a number to enter (tier A, from Projects) |
| Home cards | "My scorecard" (week altitude), "Reviews due" (week altitude), "Skills to confirm" (day altitude) |
| Agent | Mira, through a tool pack `people-review`, with a chat twin for each act |
| Not owned | No palette, bell, assistant rail or ⌘K listener |

---

## 9. Fences (R7)

| Rule | Fence | State |
|---|---|---|
| Alignment and measures read through the task predicate | `tests/unit/test_goal_alignment_r8.py`, `tests/unit/test_goal_measures_r8.py` | New with PPL-A1 and PPL-A5 |
| No evidence source of a refused kind (§5.2) | `tests/unit/test_people_evidence_kinds.py`: the source registry refuses a kind on a deny list | New with rung 5 |
| An evidence read resolves as the reader | `tests/unit/test_people_evidence_visibility_r8.py` | New with rung 5 |
| The subject sees every item a reviewer sees | `tests/unit/test_people_subject_sees_all_r8.py` | New with rung 5 |
| No machine rating in a review, and no sort of people by a number in People Review | `tests/unit/test_people_no_machine_score.py`: the review draft schema has no number field, and no People Review query orders people by an aggregate | New with rung 7 |
| The wall: no review, decision, pay or door code reads a score table | `tests/unit/test_score_wall.py`: a scan of the review, decision and door modules for the names of the tier S tables | New with rung 4 |
| A role board holds one role family, meets its floor and shows no bottom names | `tests/unit/test_role_board_r8.py` | New with rung 6 |
| No streak counter | `tests/unit/test_score_wall.py` also refuses a `streak` field in the tier S tables | New with rung 4 |
| A personal source is optional | `tests/unit/test_personal_source_optional.py`: no route or flow needs it, and a refusal writes no row | New with rung 5 |
| A decision has a named human decider | `tests/unit/test_people_decision_human.py`: an `agent:` decider is refused | New with rung 9 |
| No score for a person outside People Review and tier S | The ALN-7 schema scan (`test_align_findings.py`, planned) gains an allowlist with the People Review and tier S tables and their reason | Advisory until ALN-7 lands |

---

## 10. The ladder (R10)

| Rung | Tier | Slices | Owner answers | Demo |
|---|---|---|---|---|
| 1 | A | PPL-A1 to PPL-A3 | None (needs WM-5) | §4.7 |
| 2 | A | PPL-A4 to PPL-A7 | PPL-Q1 to PPL-Q4 | §4.7 |
| 3 | B | Rows after PPL-Q5 | None | §5.10 |
| 4 | S | Rows after PPL-Q12 | PPL-Q12, PPL-Q13 | §5a.9 |
| 5 | B | Rows after PPL-Q5 | PPL-Q7 | §5.10 |
| 6 | S | Rows after PPL-Q12 | PPL-Q12, PPL-Q14 | §5a.9 |
| 7 | B | Rows after PPL-Q5 | PPL-Q5, PPL-Q8 | §5.10 |
| 8 | B | Rows after PPL-Q5 | PPL-Q5 | §5.10 |
| 9 | C | Rows after PPL-Q5 | PPL-Q10 | §6.4 |
| 10 | C | Rows after PPL-Q9 | PPL-Q9 | §6.4 |
| 11 | C | Rows after PPL-Q11 | PPL-Q11 | §6.4 |

**When the tasks come.** Tier A gets its tasks when the owner accepts tier A.

That is one task for each tier A row and one demo task for each rung. The other rungs get rows and tasks when the owner answers their questions. So no task waits on an open reversal.

---

## 11. Questions for the owner

| Id | Question | Options | Recommendation | Why |
|---|---|---|---|---|
| **PPL-Q1** | May a Goal hold numbers with a target? (amends D199.2 and §13.6a) | (a) Yes, 1 to 5 "measures". (b) Yes, and a member sees the words "Objective" and "Key result". (c) No | **(a)** | It meets the ask and keeps three words for a member. "Measure" also covers a KPI |
| **PPL-Q2** | May a Goal have a period and support a parent Goal? | (a) Yes, week, month, quarter, year, 3 levels. (b) Period only. (c) No | **(a)** | A company goal of a quarter needs team goals of a month under it |
| **PPL-Q3** | May a Goal belong to a team? (amends D199.2) | (a) Yes, one `org_group`. (b) No, a member owns each goal | **(a)** | A team is a group (D204.8). It adds no new word |
| **PPL-Q4** | How does a manual measure get its number? | (a) One My Day card per period, one field. (b) A weekly check-in form. (c) Only by hand | **(a)** | It keeps D199.2's "no check-in record", and a number still arrives |
| **PPL-Q5** | The score line for reviews and decisions (§5.1) | B-1, B-2, B-3 | **B-2** | Facts about work and human ratings meet UC-6 to UC-8. A machine score adds legal weight and invites gaming |
| **PPL-Q6** | May anybody read another member's mailbox for evidence? | (a) No. Mail on a record, and the subject's own mail in a self-review. (b) Yes, for HR | **(a)** | Monitoring notice laws, DPDP's least data, and trust (§5.9) |
| **PPL-Q7** | Personal GitHub on the terms of §5.8? | (a) Yes. (b) No personal sources | **(a)** | It shows skill that company work hides, and the terms keep consent free |
| **PPL-Q8** | The default review scale | (a) No scale, written reviews only. (b) A 5-point scale. (c) The org picks, with (a) as the default | **(c)** | A company keeps its own scale, and a new company starts with words |
| **PPL-Q9** | Outside agents on the terms of §6.2? | (a) Yes, scope off by default. (b) Read only, no proposals. (c) No | **(a)** | It meets UC-11, and a person still decides each case |
| **PPL-Q10** | Which staff regions at launch? | (a) India first. EU, California and New York staff wait for a legal review. (b) All regions | **(a)** | The first customers are Indian, and EU high-risk rules start 2 Dec 2027 |
| **PPL-Q11** | Does Metorite pay for a legal review before tier B, S or C reaches a customer? | (a) Yes, before rung 4. (b) Later | **(a)** | The research is secondary sources only. This is a money call, so the owner decides |
| **PPL-Q12** | Scores and boards (§5a) | (a) My scorecard, team boards and opt-in role boards (top 3 and "your band"), with seasons, behind the wall. (b) My scorecard and team boards only, with no board of people. (c) Full role leaderboards that everybody sees, and the score feeds reviews | **(a), as a pilot** | It gives the game the owner asked for, in the form the evidence supports. (b) is the lowest risk. (c) is the form that failed at Wells Fargo and Microsoft |
| **PPL-Q13** | May a measure belong to one person, as a personal target? | (a) Yes, inside a scorecard that the person and the lead agree. (b) No, measures belong to goals only | **(a)** | A fair score needs a target of one's own. The agreement keeps it fair |
| **PPL-Q14** | Which team plays the pilot season? | The owner names a team | **A sales team** | Sales already works to targets, and its measures come from the CRM |

---

## 12. Non-goals

- Payroll, pay bands and leave balances (`people_center_app.md` §3.6 and §10).
- An applicant tracking system. A hire pipeline reuses the CRM shape when it comes (§6.3).
- Any activity signal of §5.2: typing, time online, idle time, screenshots, app use, message counts, meeting hours.
- Sentiment, emotion or tone of a person.
- A composite score of a person in a review, a decision or an outside agent, unless the owner picks B-3 or PPL-Q12 (c).
- A ranking of people of unlike roles on one scale.
- A full public ranking of people, a forced bottom, or a streak.
- A single count per engineer: commits, lines or pull requests.
- A move, a role change or an exit that applies by itself.
- A read of another member's mailbox, unless the owner picks PPL-Q6 (b).
- A bulk export of people data to any agent.

---

## 13. What other products and studies show

The full record is `project-docs/research/people_intelligence_2026-11.md`. The lessons that shaped this spec:

| Product or study | What it does or found | We take | We do not take |
|---|---|---|---|
| Lattice (Evidence-based Review Drafts, July 2026, and an MCP) | Drafts a review from goals, 1:1s and feedback. Its agent cannot pick a rating. A goal can follow a Jira query | AI drafts, a person rates (§5.6). A measure follows a live query (§4.3) | Self-typed check-ins as the main evidence |
| Windmill | A Slack agent drafts reviews from GitHub, Jira, Asana and Salesforce | Evidence from many work tools (§5.2) | Slack as the home of the review |
| Microsoft Viva Insights | Personal insights for each person. Manager views are group-level, with a floor of 5 | The floor of 5 (§5.1). The subject sees their own data first (§5.3) | Collaboration hours and meeting load as signals |
| Swarmia, DX | Team flow metrics, beside surveys. They warn against ranking people | Team-level totals (§5.1, §5a.4) | none |
| McKinsey (2023) developer productivity paper | Proposed person-level contribution scores. Wide criticism followed | The lesson: no single count per engineer (§5a.2) | The score |
| Workday Skills Cloud, Eightfold | Infer skills from documents and job history. Eightfold faces a 2026 class action over hidden scores | Skills with their source, confirmed by the person (§5.4) | Opaque match scores |
| ActivTrak, Hubstaff, Teramind | Track app use, idle time, screenshots, keystrokes | Nothing | Every activity signal (§5.2) |
| Mollick and Rothbard 2014 | A game helps only the people who agreed to play | Opt-in (§5a.5) | A forced game |
| Barankay, and the sweater-factory study | Public rank made leaders relax and cut output | A private scorecard first, seasons (§5a.4) | A full public ranking |
| Charness, Masclet and Villeval 2014 | Rank for status raised effort and some sabotage. Team identity cut the sabotage | Team boards (§5a.4) | Boards of single people by default |
| Wells Fargo, Microsoft, Google | Targets or ranks tied to pay led to fraud, sabotage and gaming | The wall (§5a.6) | A score that decides pay |
| Google OKR grading | 0.0 to 1.0, with 0.6 to 0.7 a good result for a stretch goal | Attainment against one's own target (§5a.2) | none |
| GitHub, 2016 | Removed streak counters | No streaks (§12) | none |
| Zoho Motivator | Targets, KPIs, games and trophies inside a CRM | Tier S serves the CRM targets too (§3) | A second game store in the CRM |

**Not verified:** whether Workday bought Confirm, the skills method of Gloat, Beamery and SuccessFactors, Peoplelogic, and any independent test of a sales game vendor. Quantive is now part of WorkBoard (May 2025).

---

## 14. Verification of this spec

This pull request changes docs only. Its checks:

```bash
node .claude/hooks/ste-lint.mjs project-docs/specs/people_intelligence.md project-docs/research/people_intelligence_2026-11.md
PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py -q
```

**R4.** A pull request that ships a PPL slice updates the status header of this spec and its INDEX row in the same pull request.
