# Waiting — one list of what waits on you, and of what you wait on (WS-AP)

**Product:** Metorite · **Feature:** one list of what waits on a member, and of what the member waits on · **Updated:** 2026-11-06 · **Owner:** vjvarada

**Status:** 📋 **SPEC, ACCEPTED.** Nothing in this spec is built, except the parts that §2 names. On 2026-11-06 the owner accepted the recommended answers to AP-Q1 to AP-Q8 (§13). AP-Q9 has a working default. AP-Q10 is answered, so AP-16 is open to build after AP-15.

**Verified against code on 2026-11-06 at `97da742ae`.** Each path in §2 and §9 was read on that commit. The open branch `l6s-waiting-list` (task 1750) was read at its head on the same day.

**Board:** WS-AP in `work_plan.md` §2.1. **Decisions that bind it:** D12, D52 to D54, D87, D89, D99, D103, D124, D145, D171, and ALN-Q5 of `project_alignment.md` §14.

**Metorite:** project "Proactive Intelligence", epic 1615. This spec is task 1855 (AP-0). The slices are listed in §8 with their task numbers.

**Gate summary:** every slice is AGENT-SAFE to build dark. The owner accepted the new sidebar row (AP-Q1), so AP-2 adds the live pane to `launch_surface.md` §2 in its PR. Each production flag flip is named in its PR with the box and the evidence.

---

## 0. The ask, and the answer in one screen

The owner, 2026-11-06, in chat:

```text
"Currently I have to open every single project to see the agents that are waiting for me,
instead be able to see it all in one place."
"Should we show everybody all the agents waiting? Should it be dependent on whether you see
the agents only waiting on the persons whose task that is? Should the admin see all of the
agents that are waiting and be able to filter on the basis of the person?"
"Agents waiting for you could be one of the surfaces that we see on the left-hand side."
"It's not only agents, but other human beings are also waiting for our inputs. A human being
should be able to specify that one particular task that they are working on is waiting for
another person on Metorite."
```

Later the same day, in chat:

```text
"Even when a task is not assigned to anybody and an external agent is working on it, we need
to figure out a way for 'Awaiting Attention' to show up to the right persons, or all relevant
people."
```

**The answer.**

1. **One sidebar row, "Waiting", opens one page.** The page has two lists. "For you" holds every agent question, approval and person ask that waits on you, in every project. "On others" holds every person you wait on.
2. **A wait has one addressee.** Your list shows only the waits addressed to you. Other people see a wait on its task, as "Waiting for Priya", and never in their own list.
3. **An admin gets a Team lens, not a second app.** It shows every wait on the tasks that the admin can open, with a filter by one person. It shows no count for each person and no ranking (ALN-Q5).
4. **A person waits on a person with one verb: Ask.** On a task, the member picks "Wait on someone" and names the person. Then the member writes the need and picks an optional date. The person gets the ask in "For you" and answers it in place. The answer posts on the task.
5. **No new store for asks.** A person ask is a row of `pm_decisions` (migration 332) with the new source `person`. Agent questions stay in `pm_agent_runs`. The page reads each store through one read seam (AP-1).
6. **A wait that no person owns still reaches people.** The owner added this later the same day (§4.5). A ladder names the addressee. It tries the run's member first, then the assignees, the watchers, the project lead and the admins. The people near the task (the circle) see the wait at once. They also get it in their count when the member cannot act, or after 24 hours with no answer.

---

## 1. Scope and non-goals

### 1.1 In scope

1. The read seam AP-1, over every store where a wait lives (§7.3).
2. The sidebar row "Waiting", its count, and the page `/waiting` with "For you", "On others" and the Team lens (§5).
3. The person ask: create, answer, decline, withdraw, nudge and expire (§6, AP-8).
4. The entry points of the ask: the task card, the comment box and the chat (§6.5, §6.6).
5. The task card chip "Waiting for Priya", and the Projects filter "Waiting on me" for person asks (AP-9).
6. My Day: the top waits in "Needs you", with "See all" to the page (AP-3).
7. Reach: when an ask is loud enough to push (AP-5).
8. A wait that no person owns: the addressee ladder, the circle and the open wait (§4.5, AP-15 and AP-16).

### 1.2 Non-goals

1. **No score for any person.** No view counts, ranks or times a person's answers. ALN-Q5 binds this spec as it binds `pm_decisions`. The Team lens filters by one person. It never groups by person.
2. **No change of the task's status.** A wait is not a status. The status is the team's, and a wait is one person's need (`project_management_app.md` §9.12.9).
3. **No auto-share.** An ask never opens a task to a person who cannot open it. The member shares the project first (D12).
4. **No ask to a person outside the organization.** The private "Waiting for" of the overlay keeps that case (§3.3).
5. **No new approval stack.** A held write keeps its own store (`pending_actions`) and its own exact-payload card (D124.2).
6. **No ask from an agent to a new person in v1.** An agent asks its own member, as `ask_on_task` does today. AP-16 (AP-Q10) lets it name one person of the circle, who already gets the bell of its questions. AP-14 lets the member forward a question, later (AP-Q7).

---

## 2. What exists today

Each fact was read on `97da742ae`.

### 2.1 The waits, by store

| Kind of wait | Store | Addressed to | Where it shows | Who else sees it |
|---|---|---|---|---|
| Agent question on a task | `pm_agent_runs`, state `awaiting` (`267_pm_agent_runs.sql:60`) | The run's member. `ask_on_task` has no recipient field (`agent_edge/tools.py:5748-5781`) | The task pill, the Projects filter "Waiting on me", the strip count, the bell `agent_question`, the chat tool `waiting_agent_requests` (`skill_projects/reads.py:1834`), the project panel "Waiting on people" | The bell goes to assignees and watchers too (`agent_runs.py:1256-1263`). Any person who opens the task can answer a free question (`agent_runs.py:1117-1118`) |
| Archive request | The same run, a typed question | The run's member | The task, the bell of the member only (`agent_runs.py:1230-1231`) | Only the member answers (`agent_runs.py:973-982`) |
| App request | `pm_agent_runs` with no task (migration 342) | The member | Integrations, AI agents, "Waiting for you" (`WaitingRequests.tsx`). Dark behind `APP_REQUESTS_ORGS` | Nobody |
| Held write | `pending_actions` | No approver column (`needs.py:46-47`) | `/approvals`. My Day for admins only (`needs.py:40-47`) | ⚠️ The gateway returns the whole tenant queue to any member with `feature:approvals` (`routes/actions.py:33-56`). Every member has it (migration 207) |
| Chat card | `chat_pending_ask` (migration 237) | `actor_email` | The open chat. My Day `agent_asks` and `AiWaitingCard`, both dark | Nobody |
| Decision | `pm_decisions` (migration 332) | The named `decider` | The bell `decision`, the task panel, My Day. Dark behind `DECISIONS_ORGS` | Others see `{decider, expires_at}` and the answer comment (`decisions.py:428-441`) |
| I wait on a person | `pm_task_personal.waiting_on` (migration 188) | Nobody. It is the waiter's private overlay | The waiter's "Waiting" chip in My work (`myTasks.ts:142`), `WaitingForView.tsx` (unreachable while `NEXT_PUBLIC_PROJECTS_MY_WORK` is on), the waiter's My Day `waiting_quiet` after 5 days (`feed.py:390-409`) | Nobody. The waited-on person has no list |

### 2.2 Facts that shape the design

1. **No list crosses projects for agent questions.** `GET /projects/agent-runs` is scoped to `member_email = :me`, newest first (`agent_runs.py:1835-1891`). The project panel lists one scope at a time (`analytics_waiting.py:341-349`).
2. **Task 1750 (PR 1741, branch `l6s-waiting-list`, not merged)** adds `order=oldest` and `with_task=true` to that route. It draws "Waiting for you (N)" above the held writes on `/approvals`. Each question answers in place with `ElicitationCard`. It lists the caller's own runs only.
3. **The Projects filter "Waiting on me"** keeps a task when a run is `awaiting`. The caller must be the run's member or an assignee (`filters.py:1478-1503`). It covers agent runs only.
4. **A person cannot mark "waiting on X" and keep the task.** Delegate replaces every assignee (`lens.ts:941-944`, `tasks.py:2221-2227`). The overlay route takes `waiting_on` alone (`personal.py:175-189`), but the read side names the current assignees minus me (D77, `personal.py:1180-1208`).
5. **The nudge** writes one bell row of kind `nudge` (`personal.py:3147-3276`). It has no rate limit, because nothing reads `last_nudged_at` before a send.
6. **⚠️ Defect.** The Projects bell draws every `nudge` as "{A} has gone quiet on {task}. Resume or release it." (`notifications.ts:79,97`). That text belongs to the stranded-run nudge of `agent_run_sweep.py:100-102`. So a person's nudge reads wrong. Task 1857 (AP-BUG-1).
7. **`pm_decisions` has no ask route on purpose** (`decisions.py:16-19`). An HTTP ask lets a member, or an agent with a member's connection, put a card on another member's bell. AP-8 adds a route, and §7.2 holds its guards.
8. **The no-score fence.** `tests/unit/test_decisions.py:180-200` refuses an index that names `decider`, `answered_by` or `asked_by`. It refuses `group by`, `count(`, `rank` and `score` in the service too.
9. **The sidebar has a count already.** The My Day row shows the feed total and the AI runs that wait (`Sidebar.tsx:1322-1381`). Since GL-12, "Needs you" is folded into that row (`navigation_shell.md:288-294`). `NavPane.badge` exists and no pane sets it (`nav.ts:89`).
10. **Roles.** `is_admin` is `admin:members:read` (`routes/admin/me.py:243`). The seeded `manager` role holds it too. The read-all right is `data:org:read`. A task in a personal space stays hidden from it (D-OP-15, `one_projects_app.md:395`).
11. **A team view is a filter**, `?scope=` on a page, that reads `reader_scope`. It shows nothing that the reader cannot open (`navigation_shell.md:789-808`, D87).

---

## 3. The model

### 3.1 One wait

A wait is one fact with seven parts.

| Part | Meaning |
|---|---|
| **Waiter** | Who cannot go on: an agent run, a chat run, a held write, or a member |
| **Addressee** | The ONE member who must act. The ladder of §4.5 names that member. An open wait has more than one (§4.5) |
| **Circle** | The people near the subject: the assignees, the watchers and the project lead. They see the wait, and they do not count it (§4.5) |
| **Subject** | The task, the chat or the held write. The row opens it |
| **The ask** | A question, an approval, or a short request. It is text that another person wrote, so it shows fenced as data |
| **Since** | When the wait started |
| **By when** | Optional. A person ask may carry a date. A decision carries `expires_at` |

A wait ends in one of five ways: **answered**, **declined** ("Not me"), **withdrawn** by the waiter, **expired**, or **closed** with its subject.

### 3.2 The kinds

| Kind | Store | Waiter | Addressee | The answer act | State |
|---|---|---|---|---|---|
| `agent_question` | `pm_agent_runs` | An external agent | The run's member, by the ladder of §4.5 | The options, or free text | Built |
| `archive_request` | `pm_agent_runs` | An external agent | The run's member only. The circle can nudge (§4.5) | Approve or Keep | Built |
| `app_request` | `pm_agent_runs`, no task | A Studio app | The member | Approve or Decline | Built, dark |
| `chat_ask` | `chat_pending_ask` | Mira, in a chat | The chat's member | Opens the chat at its card | Built, dark (WAIT-1) |
| `held_write` | `pending_actions` | A workflow or an agent | An admin, until AP-7 names an approver | "Review" opens the exact payload | Built |
| `decision` | `pm_decisions`, source `align` or `auto` | The alignment check, or an unattended run | The named decider | The options | Built, dark |
| `person_ask` | `pm_decisions`, source `person` | A member | Another member | Reply, a choice, or Not me | **New, AP-8** |
| `waiting_for` | `pm_task_personal.waiting_on` | The member | A person, private | Nudge, or Got it | Built, private |

The first seven kinds fill "For you". `person_ask` (as the waiter) and `waiting_for` fill "On others".

### 3.3 Two verbs for a person: Ask and Hand off

A member who needs another person has two different needs. The product keeps them apart.

| | **Ask** (new) | **Hand off** (exists: Delegate) |
|---|---|---|
| The need | "I keep the task. I need your input." | "You do the task now. I track it." |
| The assignee | Does not change | Becomes the other person |
| Who sees the wait | Both people, and the task's readers as a chip | The waiter only (private overlay) |
| The other person's act | Reply, a choice, or Not me | Work the task |
| Store | `pm_decisions`, source `person` | `pm_task_personal.waiting_on` |
| Can go outside the org | No | Yes, by a typed name |

AP-13 (AP-Q6) lets a hand-off to a member also send an ask "Tell me when it is done". The other person then sees it in "For you".

---

## 4. Who sees what — the owner's three questions

### 4.1 "Should we show everybody all the agents waiting?"

**Recommended: no.** A personal list shows only the waits addressed to you. A list of every wait in the company turns into noise, and nobody owns its rows.

A wait stays visible where it lives. Each person who can open the task sees its chip: "Needs you" for the addressee, and "Waiting for Priya" for everyone else. That is today's EA3-50 rule, and it stays.

### 4.2 "Only the persons whose task that is?"

**Recommended: the addressee, and only the addressee.** The addressee of an agent question is the run's member, the person the agent works for. The assignee is often a different person.

Today the Projects filter "Waiting on me" also matches the assignees (§2.2 item 3). The "For you" list does not, because its count must mean "only you can unblock these". The filter keeps its rule, because it answers a different question: which of my tasks are stuck. The owner accepted this (AP-Q2). §4.5 adds what happens when the run's member cannot act, or does not answer.

### 4.3 "Should the admin see all, and filter by person?"

**Recommended: yes, as a Team lens of the same page, with three limits.**

1. **It is a filter** (`/waiting?scope=team`), never a new app or route (D87, `navigation_shell.md:789-808`).
2. **It shows only what the reader can open.** It reads `reader_scope`. A task in a personal space never shows (D-OP-15).
3. **No count for each person, and no ranking.** The lens lists waits, oldest first. A filter shows one person's waits. It never draws "Priya: 9 waiting" or "slowest to answer" (ALN-Q5).

**Who gets the lens.** Members with `data:org:read`, and project leads for their own projects (`pm_projects.lead`, with the ancestor walk of `authority.py`). The owner accepted this (AP-Q3).

**What the lens can do.** It reads, and it nudges. It never answers for another person. The answer of a decision is that person's word (`pm_decisions_answered_by_decider`), and the same rule binds every kind on the lens.

### 4.4 The rules

| # | Rule | Fence |
|---|---|---|
| V1 | The count of "For you" holds a wait only when the caller is an addressee. The group "Near you" lists the circle's waits, with no count | AP-1 R8 test, two members, one org. AP-15 adds the circle cases |
| V2 | No list shows a wait on a task the reader cannot open | AP-1 R8 test, `deliverable` of `notifications.py` |
| V3 | No list shows a wait of another org | AP-1 R8 test, two orgs |
| V4 | An ask goes only to a member who can open the task. Else it is refused, with the next step | AP-8 R8 test, extends `test_a_decider_who_cannot_open_the_task_gets_no_row_and_no_bell` |
| V5 | Only the addressee answers. The lens never answers | `pm_decisions_answered_by_decider`, and an AP-12 test |
| V6 | No count, group or rank by person, in the seam or the lens | `test_decisions.py:180-200`, extended to `routes/shell/waiting.py` |
| V7 | A task in a personal space never shows on the Team lens | AP-12 R8 test |
| V8 | The text of an ask shows fenced as data, never as markup or an instruction | AP-4 test, the WAIT-2 rule |
| V9 | A wait on a task always has a person who can open the task in its addressees. The ladder of §4.5 ends at the `data:org:read` holders | AP-15 R8 test: the member left the org, and the task has no assignee, no watcher and no lead |
| V10 | The first answer closes an open wait for every addressee | AP-15 R8 test, two addressees |
| V11 | An agent can name an addressee only from the circle. Else the tool refuses, and writes no row and no bell | AP-16 R8 test |

### 4.5 A wait that no person owns

The owner asked this later on 2026-11-06 (§0). An external agent works on a task with no assignee. Its question must still reach the right people.

**The facts, read on `97da742ae`.**

1. An external agent always runs as one member, `pm_agent_runs.member_email`. A claim makes that member an assignee (`claim_task`). But `ask_on_task` and `start_agent_run` open a run with no claim. So a task can have a waiting run and no person assignee.
2. The bell of a question reaches more people than the list does. `notify` sends `agent_question` to the run's member and to `task_audience()` (`agent_runs.py:1256-1263`). The audience is the task watchers, the assignees and the watchers of the project chain (`notifications.py:348-399`). Migration 165 makes each task's author a watcher.
3. No list shows the circle's waits. A bell that a person missed is lost.
4. The member can stop being able to act. When they leave the org or lose the project's grant, `notify` drops them (`resolve_visibility_for`). Then nobody may hear. When they are away, `away_today` (`routes/people/absences.py:323`) knows it, and nothing reads it for a wait.
5. An author can be an agent or a workflow: `created_by` is `agent:<name>` or `system:workflow:<id>` (`analytics.py:1141`). Then the task can have no watcher who is a person.
6. Any person who opens the task can answer a free question. Only an agent is held to its own run (`agent_runs.py:1117`). A typed archive request takes an answer from the run's member only (`agent_runs.py:960-982`). An assignee or an org admin can release a run (`agent_runs.py:2163-2170`).

**The ladder: who is the addressee.** The read seam resolves it each time it reads, from these facts. It stores no new column. The first step that names a person wins. Each step keeps only the people who can open the task (V2), and drops each `agent:<name>`.

| Step | The addressee | It applies when |
|---|---|---|
| 0 | The member that the agent names in `ask_on_task` (AP-16) | The agent names a person of the circle. Needs AP-Q10 |
| 1 | The run's member | They can open the task, and `away_today` does not hold them |
| 2 | The person assignees of the task | Step 1 fails |
| 3 | The watchers of the task and of its project chain (`task_audience()`) | Steps 1 and 2 name nobody |
| 4 | The lead of the project, with the ancestor walk of `authority.py` | Steps 1 to 3 name nobody |
| 5 | The members with `data:org:read` | Steps 1 to 4 name nobody. So no wait lists for nobody (V9) |

**The circle** is the people of steps 1 to 4, minus the addressees. It is the audience that gets the bell today (fact 2), plus the project lead.

**Two states.**

- **Owned.** Step 0 or step 1 names one addressee. That person gets the row in "For you", the count and the bell. The circle sees the row in the group "Near you", with no count and no new bell.
- **Open.** Steps 2 to 5 name the addressees. Each one gets the row in "For you", with the count and the bell. The row reads "Open · any one of you". The first answer closes it for all of them (V10). Each row then reads "Answered by Priya" and leaves.

**An owned wait opens after 24 hours.** An owned wait with no answer after 24 hours also opens to its circle. The circle gets one bell: "Claude Code has waited 1 day on Firmware OTA". The first addressee keeps the row. The 24 hours is the working default of AP-Q9.

**What the circle can do on a row.**

| Kind | A person of the circle can |
|---|---|
| A free question, or one with options | Answer it. The answer is that person's word, and the timeline names them (fact 6) |
| A typed archive request | Nudge the member only. The row reads "Only Vijay can answer this". When the member can no longer open the task, an assignee or an admin sees "Stop the run" (the release rule of fact 6) |
| Any kind | Open the task, and nudge the addressee once in 24 hours (the AP-8 rate limit) |

**The other kinds.** The ladder binds `agent_question` and `archive_request`. A `decision` and a `person_ask` name their person, and §6.5 holds the case where that person leaves. A `chat_ask` and an `app_request` have no task, so they have no circle. A `held_write` goes to the admins until AP-7.

**The Team lens** gets the filter "Open". It lists the open waits, oldest first, with no count for each person (V6).

**The story.** Claude Code works on "Firmware OTA" for Vijay. The task has no assignee. Raj wrote the task, so Raj is a watcher. Priya leads the project.

| # | What happens | Vijay | Raj | Priya |
|---|---|---|---|---|
| 1 | The agent asks "Use the A/B partition?" | "For you", counted, bell | "Near you", bell (today's bell) | "Near you" |
| 2 | 24 hours pass with no answer | Still "For you" | "For you", counted, one bell "waited 1 day" | The same as Raj |
| 3 | Raj answers "Yes, A/B" | The row reads "Answered by Raj", and the agent goes on | The row leaves | The row leaves |

If Vijay had left the org at step 1, step 1 of the ladder fails. The task has no assignee, so step 3 names Raj, and the wait is open at once.

---

## 5. The surfaces

### 5.1 The sidebar row

A new row **"Waiting"** in Personal Center, right under My Day. Its count is the number of open waits in "For you". The count never shows 0, and it shows "99+" past 99, as the needs pill does.

```text
┌──────────────────────────┐
│  ◉ Metorite              │
│                          │
│  PERSONAL                │
│  ☀ My Day            12  │   ← the feed total, minus the waits (AP-3)
│  ⏳ Waiting           4  │   ← NEW. Open waits "For you". Amber pill
│  🗓 Calendar          2  │
│  ✉ My Email              │
│  …                       │
│  APPS                    │
│  ▦ Projects              │
│  …                       │
└──────────────────────────┘
```

**One count for one thing.** Today the My Day row counts the AI runs that wait. With the Waiting row on, the waits leave the My Day count. So no wait is counted twice (AP-3).

### 5.2 The page `/waiting`

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Waiting                                                                     │
│ [ For you  4 ]  [ On others  3 ]  [ Team ]          Kind ▾   Project ▾      │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▍ Claude Code asks · CIV-3 local CI lane · Developer Workflow       2 d     │
│ ▍ "Run the home lane first, or the PC lane?"                                │
│ ▍ ( Home lane ★ )  ( PC lane )  ( Answer in words… )        Open task →     │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▍ Priya waits on you · Enclosure v2 · Hardware              by Fri · 5 h    │
│ ▍ "Can you confirm supplier X for the gasket?"                              │
│ ▍ [ Reply… ]  ( Not me )                                    Open task →     │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▍ Workflow "Invoice reminders" holds a write · Admin               1 d     │
│ ▍ Send 14 reminder emails                                                   │
│ ▍ ( Review )                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▍ Mira asks in "Q4 plan" chat                                      3 h     │
│ ▍ "Approve the draft to Contoso?"                                           │
│ ▍ ( Open the chat at the card )                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▍ Open · any one of you · Claude Code asks · Firmware OTA          1 d     │
│ ▍ "Use the A/B partition?"            Nobody owns this task                 │
│ ▍ ( Yes, A/B ★ )  ( Single slot )  ( Answer in words… )     Open task →     │
├─────────────────────────────────────────────────────────────────────────────┤
│ NEAR YOU · not counted                                                      │
│   Claude Code asks Vijay · Gasket drawing · Hardware               3 h     │
│   "Rev C or rev D?"            You watch this task    ( Answer )  ( Nudge ) │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **The order.** Overdue asks first, then oldest first. A wait that waits longest costs most.
- **The row.** Who waits, on what, in which project, how long, and the ask. The acts sit on the row.
- **The acts follow the kind.** A question with options shows its options. A person ask shows Reply and Not me. A held write shows only Review, because it needs its exact payload (D124.2). A chat card opens its chat.
- **An answered row** fades and leaves after 5 seconds, with Undo while a person ask is not yet read.
- **An open wait** (§4.5) reads "Open · any one of you", and says why, such as "Nobody owns this task". It counts like an owned wait.
- **Near you** lists, under the counted rows, the waits of the tasks where you are in the circle (§4.5). Each row says why it is there: "You watch this task", "You lead this project" or "You are an assignee". It draws no count. It folds by default when it holds more than 5 rows.
- **The empty state:** "Nothing waits on you." Under it, one line names the oldest wait in "On others", when one exists.
- **A source that fails** shows "Could not load agent questions. Try again." It never shows an empty list in its place (the `Found` and `Signal` rule of `needs.py`).

**On others:**

```text
├─────────────────────────────────────────────────────────────────────────────┤
│ You wait on Raj · Gasket drawing · Hardware              by Wed · overdue  │
│ "Send the rev C drawing"                       ( Nudge )  ( Withdraw )  ⋯   │
├─────────────────────────────────────────────────────────────────────────────┤
│ You wait on Contoso (outside) · Site visit · Sales             since 6 d   │
│ Handed off. Private to you                     ( Got it )  ⋯               │
└─────────────────────────────────────────────────────────────────────────────┘
```

- Person asks that you sent, and your private hand-offs.
- **Nudge** sends one bell row. It is off for 24 hours after a nudge, and the button says "Nudged 3 h ago".
- **Withdraw** ends the ask. The other person's row leaves with "Priya withdrew this".
- **Got it** ends a private hand-off wait.
- The menu holds "Change the person" and "Change the date".

**The Team lens** (AP-12):

```text
│ Waiting · Team                       Person: [ Anyone ▾ ]  Kind ▾  Project ▾ │
├─────────────────────────────────────────────────────────────────────────────┤
│ Waiting for Priya · Claude Code asks · Firmware OTA             4 d  Nudge  │
│ Waiting for Raj · Vijay waits · Gasket drawing        by Wed · overdue Nudge│
│ Waiting for Priya · Mira asks · Q4 plan chat                    1 d  Nudge  │
```

It shows no answer control. "Person" filters by one addressee. The lens draws no total for each person.

### 5.3 The task card and the board

```text
Task card (side panel)                     Board card
┌──────────────────────────────────┐       ┌────────────────────────────┐
│ Enclosure v2          In progress│       │ Enclosure v2               │
│ ⏳ Waiting for Priya · by Fri  ⋯ │       │ ⏳ Priya · 5 h      ◷ Fri  │
│ Assignee  Vijay                  │       └────────────────────────────┘
│ Due       Mon 10 Nov             │
│ …                                │
│ [ ⏳ Wait on someone ]           │   ← in the facts row, and in the ⋯ menu
└──────────────────────────────────┘
```

- The chip reads "Needs you" for the addressee, and "Waiting for Priya" for every other reader. Two asks read "Waiting for 2". An open wait (§4.5) reads "Needs one of you" for its addressees, and "Waiting · open" for the other readers. It uses the amber accent of `AGENT_STATES.awaiting` (`statusAccent.ts:498`), with no new colour.
- The chip opens the ask: its text, its age, and the acts of the reader.
- Shortcut: `W` on a selected task opens "Wait on someone".

### 5.4 My Day

"Needs you" keeps the top 5 waits of "For you", oldest first (D171.1). "See all 9" opens `/waiting`. The private `waiting_quiet` card stays, and its Nudge uses the AP-8 rate limit.

### 5.5 Projects

- The filter "Waiting on me" adds person asks addressed to the caller (AP-9).
- The panel "Waiting on people" adds a section "Asks with no answer" for the scope (AP-9). It lists, oldest first, with no count for each person.

### 5.6 The chat

Every act on the page works in the chat (chat-first parity). AP-10 holds the tools.

```text
You:   what's waiting on me?
Mira:  4 things wait on you. The oldest is from Claude Code on CIV-3, 2 days ago:
       "Run the home lane first, or the PC lane?"   [Home lane] [PC lane]
You:   home lane. And ask Priya to confirm supplier X on the enclosure task by Friday
Mira:  ┌ Ask Priya · Enclosure v2 ─────────────────┐
       │ "Confirm supplier X for the gasket"       │
       │ By Fri 7 Nov                              │
       │              [ Send ]  [ Change ]         │
       └───────────────────────────────────────────┘
```

The ask shows a card, because it reaches another person (D124). It never sends from an unattended run.

### 5.7 The phone (390 px)

The row stacks: who and age on line 1, the ask on line 2, the acts as full-width buttons on line 3. The tabs become a segmented control. The Team lens filter becomes a sheet.

---

## 6. The workflow: a person waits on a person

### 6.1 The story

Vijay works on "Enclosure v2". He needs Priya to confirm a supplier before he orders parts. He keeps the task.

| Step | Vijay (the waiter) | Priya (the addressee) | Everyone who opens the task |
|---|---|---|---|
| 1. Ask | Task card → "Wait on someone" → Priya → "Confirm supplier X for the gasket" → by Fri → Send | — | — |
| 2. Told | The chip reads "Waiting for Priya · by Fri". "On others" gains a row | The bell: "Vijay waits on you: Confirm supplier X". The Waiting row count +1. My Day "Needs you" | The chip "Waiting for Priya". A comment on the task holds the ask |
| 3. Quiet | After 2 days with no answer, or on the date, My Day "Needs you" shows "No answer from Priya" with Nudge | The row turns "overdue" on the date | — |
| 4. Nudge | Nudge, once in 24 h | One bell row: "Vijay nudged you about Enclosure v2" | — |
| 5. Answer | The bell: "Priya answered: Yes, supplier X." The task returns to his Next actions | Reply: "Yes, supplier X." The row leaves her list | The answer posts as a comment. The chip leaves |

### 6.2 The form "Wait on someone"

```text
┌ Wait on someone ─────────────────────────────────────────┐
│ Who      [ Priya Nair                       ▾ ]          │
│          ⓘ Priya is out until Mon 10 Nov                 │
│ Need     [ Confirm supplier X for the gasket       ]     │
│          140 characters. It posts as a comment            │
│ Choices  + Add choices (optional, 2 or 3)                │
│ By when  ( None ) ( Today ) ( Tomorrow ) ( Fri ) ( 📅 )  │
│                                  [ Cancel ]  [ Send ]    │
└──────────────────────────────────────────────────────────┘
```

- **Who** lists only members who can open the task. A member who cannot open it shows greyed, with "Priya cannot open this task. Share the project with her first."
- **Absence.** When `people_absences` holds the person on that day, one line says so. The ask still sends.
- **Need** holds at most 140 characters, the `pm_decisions` limit. The comment carries the full text when the ask came from the comment box (§6.5).
- **Choices** are optional. With choices, Priya taps one. With none, she replies in words.
- **By when** sets `due_at`. With no date, the ask expires after 14 days (§7.1).

### 6.3 The answers of the addressee

| Act | What it writes | What the waiter sees |
|---|---|---|
| **Reply** (words, at most 500 characters) | `answer_key = 'reply'`, the text in `answer_reason`, one comment on the task | "Priya answered: …" |
| **A choice** | `answer_key` = the choice, and an optional reason | "Priya chose: Supplier X" |
| **Not me** | `answer_key = 'not_me'`, an optional "Try Raj" | "Priya says not her: Try Raj", with one tap "Ask Raj" |

### 6.4 The states

```text
                ┌────────── Withdraw (waiter) ──────────► withdrawn
                │
  Send ──► open ┼────────── Reply / choice / Not me ────► answered
                │
                ├────────── the task is done or archived ► closed
                │
                └────────── the expiry passes ───────────► expired
   (overdue is not a state: it is open, and past due_at)
```

### 6.5 The edge cases

| Case | What happens |
|---|---|
| Priya cannot open the task | The form refuses her, and names the next step: share the project (V4) |
| The task is in Vijay's personal space | Nobody else can open it. The form says "Move it to a project to ask someone". It offers the move |
| A second ask to the same person on the same task | Refused: "You already wait on Priya here. Nudge her?" One open ask for each task, waiter and addressee |
| Asks to two people | Allowed. The chip reads "Waiting for 2" |
| The task closes with an open ask | The ask closes with the reason `task_closed`. Priya's row leaves with "The task is done" |
| Priya loses access, or leaves the org | Her answer is refused. Vijay's row reads "Priya can no longer open this task", with "Ask someone else" |
| Vijay asks himself | Refused |
| An agent connection calls the ask route | Refused. An ask is a person's act (§7.2) |
| An unattended run tries to ask | Refused by `no_person_present`, before any read |
| The ask expires | Vijay's row reads "No answer in 14 days", with "Ask again". Priya's row leaves |
| Nudge twice in 24 hours | The button is off, and the route answers 429 |

### 6.6 The comment box: the fastest way in

Most asks start as a comment today: "@Priya can you confirm supplier X?" AP-11 adds one control. When a comment names exactly one member, the box shows **"Wait for Priya's reply"**. With it on, Send writes the comment and a person ask in one step. The question is the first 140 characters of the comment. A reply in the thread from Priya answers the ask.

### 6.7 The chat

`ask_person`, `my_waits` and `answer_wait` (AP-10). The ask shows a card before it sends. A reply to a nudge, such as "tell her yes", answers the ask at its source (AP-6).

---

## 7. Data and seams

### 7.1 `pm_decisions` takes the source `person` (AP-8)

One migration, the next free number at build time (R1). Expand only (R6).

1. **Widen** `pm_decisions_source` to `('align', 'auto', 'person')`.
2. **Add** `due_at TIMESTAMPTZ NULL`, `closed_at TIMESTAMPTZ NULL` and `closed_reason TEXT NULL`, with a CHECK on `closed_reason IN ('withdrawn', 'task_closed')`. The pair is whole or absent.
3. **Change the guard trigger.** It lets `closed_at` and `closed_reason` be set once, while `answered_at IS NULL`. It refuses an answer when `closed_at IS NOT NULL`.
4. **The options of a person ask.** With no choices, the service writes `[{reply}, {not_me}]`. With 2 or 3 choices, it adds `not_me`. That stays inside the CHECK of 2 to 4 options.
5. **The expiry.** `due_at + 7 days`, or 14 days with no date. `MAX_LIFE` stays 30 days.
6. **No index that names a person** (V6). The open list for one person is a filter on a small table. If the table grows, AP-8b asks the owner for an index on `(organization_id, subject_id)` only.

**The read rule changes for `person` only.** The asker reads their own asks. Each reader of the task reads an open ask's addressee, text and age, because the ask is also a comment. A decision of source `align` or `auto` keeps today's rule.

### 7.2 The ask route and its guards (AP-8)

`POST /projects/tasks/{task_id}/asks` with `{addressee, question, choices?, due_at?}`. It refuses:

1. A caller that is not a person: an agent connection, or `no_person_present`.
2. An addressee who cannot open the task (`deliverable`).
3. The caller as the addressee.
4. A second open ask for the same task, caller and addressee.
5. More than 30 asks from one caller in one day.

The author comes from the session, never from the body. The bell row goes through `notify`, the one bell writer, with the kind `decision`. Its text names the person: "Vijay waits on you".

Three more routes: `POST /asks/{id}/withdraw` (the asker), `POST /asks/{id}/nudge` (the asker or the Team lens, once in 24 hours), and the answer, which stays `answer_decision`.

**Why this route is safe now.** `decisions.py:16-19` refused an HTTP ask because a member's agent could put cards on bells. Guard 1 refuses every agent connection. A member can already @mention any reader of a task, and the mention reaches the bell. The ask adds one tracked state to that same act.

### 7.3 The read seam (AP-1)

`GET /shell/waiting?direction=for_you|on_others[&scope=team&member=<email>]` and `GET /shell/waiting/count`.

- One provider for each store, in the provider registry of `routes/shell/needs.py`. A provider that fails returns `failed`, not empty.
- `for_you`: agent runs where `member_email = :me`, `pm_decisions` where `decider = :me`, `chat_pending_ask` where `actor_email = :me`, app requests of the member, and held writes for admins (until AP-7).
- AP-15 widens the agent run provider. It reads the waiting runs on the tasks that the caller can open, then applies the ladder of §4.5 to each one. Each row carries `role`: `addressee`, `open` or `circle`. The count reads `addressee` and `open` only. Few runs wait at one time, so the ladder runs in the service, with no index that names a person (V6).
- `on_others`: `pm_decisions` of source `person` where `asked_by = :me`, and the caller's overlay rows with `waiting_on`.
- `scope=team`: every open wait of the first five kinds, on a task inside `reader_scope`. `member` filters by the addressee. With no `data:org:read` and no lead role, the route answers 403.
- My Day "Needs you" and the sidebar count read the same providers. No second list exists.

### 7.4 What stays

- The agent run stores do not change. AP-16 adds one optional field, `for`, to `ask_on_task` (AP-Q10). AP-14 waits on AP-Q7, which the owner answered "not now".
- The bell of a question keeps today's audience. AP-15 adds one bell to the circle when an owned wait opens after 24 hours.
- `pending_actions` and `/approvals` stay the admin queue of held writes (D171.3).
- The private overlay `waiting_on` stays for a hand-off and for a person outside the org.

---

## 8. Slices

Each slice ships dark behind `WAITING_ORGS`, empty by default, unless the row says other. Each UI slice follows the D145 loop and the UI gate.

| Slice | Task | What | Done when | Tier |
|---|---|---|---|---|
| **AP-0** | 1855 | This spec | It is on `main`, in `INDEX.md` and in `work_plan.md` §2.1 | LOW |
| **AP-1** | 1616 | The read seam of §7.3, with its count | The 8 kinds come from one route, each with kind, title (fenced), waiter, subject link, age and acts. A failed source reads `failed`. R8 tests hold V1, V2, V3 and V6 | HIGH (tenancy, identity) |
| **AP-2** | 1617 | The page `/waiting` and the sidebar row. The PR 1741 section on `/approvals` moves here, and `/approvals` links to it | The row shows the count of "For you". The page lists both directions and answers in place. `nav.test.ts` holds the new live pane, and `launch_surface.md` §2 lists it. Captures at 1440 and 390, light and dark, compact, a changed accent | MEDIUM |
| **AP-3** | 1618 | My Day "Needs you" reads AP-1, top 5, "See all". The My Day row stops counting waits | No wait counts in two rows. A test reads both counts from one fixture | STANDARD |
| **AP-4** | 1623 | One `WaitCard` for every kind, with the acts of its kind | The card draws in My Day, on the page and in the chat. The text is fenced (V8) | MEDIUM |
| **AP-5** | 1624 | Reach for waits (= BUD-7). An agent question and a person ask that is due today or overdue are interrupts. Other person asks are brief | The class table of `reach/classes.py` holds `waiting_on_you`. The 3-a-day budget holds | HIGH |
| **AP-6** | 1625 | Answer in the chat: "approve the first two" answers at the source | Each answer goes through the source's own route. No new write path | STANDARD |
| **AP-7** | 1626 | Later: an approver on each held write | Unchanged | — |
| **AP-8** | 1858 | The person ask: the migration of §7.1, the routes of §7.2, the notify of the asker on an answer | R8 tests: guards 1 to 5, one answer, withdraw, nudge once in 24 h, the close with the task, V4 and V5. `test_decisions.py` still passes | HIGH (migration, identity) |
| **AP-9** | 1859 | "Wait on someone" on the task card, the chip, the board chip, the filter "Waiting on me" and the panel section | The chip reads right for the addressee and for another reader. The filter lists a person ask. UI gate | MEDIUM |
| **AP-10** | 1860 | The chat tools `ask_person`, `my_waits` and `answer_wait` | Each tool calls the AP-8 or AP-1 route. `ask_person` raises a card. The new-chat-tool fence set passes | STANDARD |
| **AP-11** | 1861 | "Wait for Priya's reply" in the comment box | One Send writes the comment and the ask. A reply from Priya in the thread answers it | STANDARD |
| **AP-12** | 1862 | The Team lens of §4.3 | R8 tests hold V7 and V6. The lens has no answer control. A reader without the right gets 403 | HIGH (visibility) |
| **AP-13** | 1863 | A hand-off to a member can also send an ask "Tell me when it is done" | The delegate form shows the choice and the line "Priya will see that you wait". The ask closes when the task closes | STANDARD |
| **AP-14** | 1864 | Forward an agent question to a person | The forward makes a person ask that quotes the question. The answer returns to the run as the member's answer | HIGH. Deferred: the owner answered AP-Q7 "not now" |
| **AP-15** | 1875 | A wait that no person owns: the ladder, the circle, the open wait, the opening after 24 hours, and the Team lens filter "Open" (§4.5) | R8 tests hold V9, V10 and V2 for the circle. A typed archive request stays the member's. The group "Near you" draws no count. UI gate for the open row and "Near you" | HIGH (visibility). Needs AP-1 |
| **AP-16** | 1876 | An agent names the person its question is for: the field `for` on `ask_on_task`, inside the circle only | An R8 test holds V11. The MCP schema, `agent_edge/tools.py` and the route take the field. The answer still reaches the run's session | HIGH (identity). Needs AP-15. AP-Q10 is answered |
| **AP-BUG-1** | 1857 | The Projects bell draws a person's `nudge` as "has gone quiet … Resume or release it" | A person's nudge reads "{A} nudged you about {task}". A stranded-run nudge keeps its text. A vitest holds both | LOW |

**Build order.**

1. AP-BUG-1 at once.
2. AP-1 and AP-8, in parallel.
3. AP-2, AP-4 and AP-9.
4. AP-3, AP-10 and AP-11.
5. AP-15, then AP-12, AP-5 and AP-6.
6. AP-13. Then AP-16, after AP-15 (AP-Q10 is answered). AP-14 waits (AP-Q7).

**Task 1750 and PR 1741.** The PR fixes the bug that the owner hit on 2026-11-05. Merge it as it is. AP-2 then moves its section to `/waiting` (AP-Q8).

---

## 9. Current file paths

| Piece | Path |
|---|---|
| Agent runs, the list and the answer | `apps/services/gateway/gateway/routes/projects/agent_runs.py` (list `:1835`, answer `:1117`, notify `:1256`) |
| The ask of an agent | `apps/services/agent_edge/agent_edge/tools.py:5748` |
| Decisions | `apps/services/gateway/gateway/routes/projects/decisions.py`, `infra/postgres/332_pm_decisions.sql` |
| The needs providers | `apps/services/gateway/gateway/routes/shell/needs.py` (registry `:1173`, admin rule `:40`), `routes/shell/feed.py` (`agent_asks` `:957`, `waiting_quiet` `:390`) |
| Held writes | `apps/services/gateway/gateway/routes/actions.py:33`, `workbench/control_plane/src/app/approvals/page.tsx` |
| App requests | `apps/services/gateway/gateway/routes/agent_requests.py`, `workbench/control_plane/src/app/integrations/components/WaitingRequests.tsx` |
| Chat cards | `infra/postgres/237_chat_pending_ask.sql`, `orchestrator/pending_ask.py` |
| The overlay wait and the nudge | `apps/services/gateway/gateway/routes/projects/personal.py` (`set_personal` `:1009`, `nudge_task` `:3147`), `infra/postgres/188_projects_personal_overlay_fields.sql` |
| The filter and the panel | `routes/projects/filters.py:1478`, `routes/projects/analytics_waiting.py`, `workbench/control_plane/src/app/projects/lib/waiting.ts` |
| The bell | `routes/projects/notifications.py` (`NOTIFICATION_KINDS` `:75`, `notify`, `deliverable`), `workbench/control_plane/src/app/projects/lib/notifications.ts:79` |
| The sidebar | `workbench/control_plane/src/lib/nav.ts`, `nav.test.ts`, `src/lib/shell/Sidebar.tsx` (`MyDayRow` `:1322`, `paneBadge` `:1053`) |
| The chat tools | `apps/skills/skill-projects/skill_projects/reads.py:1834`, `writes.py`, `guarded.py`, `manifest.py` |
| Reach classes | `apps/services/gateway/gateway/reach/classes.py` |

---

## 10. Verification commands

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_decisions.py tests/unit/test_waiting_seam_r8.py tests/unit/test_person_asks_r8.py -q
uv run pytest tests/unit/test_agent_runs_r8.py -q
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/nav.test.ts src/app/waiting src/lib/shell
uv run python scripts/affected_tests.py --run
```

A run that SKIPS the R8 suites proves nothing (843 tests skip with no database).

---

## 11. Fences (R7)

| Rule | Fence | State |
|---|---|---|
| V1 to V3, V6 | `tests/unit/test_waiting_seam_r8.py` (new, AP-1) | Advisory until AP-1 |
| V4, V5, the guards of §7.2 | `tests/unit/test_person_asks_r8.py` (new, AP-8) and `tests/unit/test_decisions.py` | Advisory until AP-8 |
| V6 in the new files | `test_decisions.py:180-200`, extended to scan `routes/shell/waiting.py` | Advisory until AP-1 |
| V7 | `tests/unit/test_waiting_seam_r8.py` (AP-12 cases) | Advisory until AP-12 |
| V8 | `src/app/waiting/lib/waitCard.test.ts` (new, AP-4) | Advisory until AP-4 |
| V9, V10, the circle half of V1 | `tests/unit/test_waiting_seam_r8.py` (AP-15 cases) | Advisory until AP-15 |
| V11 | `tests/unit/test_agent_ask_for_r8.py` (new, AP-16) | Advisory until AP-16 |
| The live pane | `src/lib/nav.test.ts` `LIVE_SET` | Binds when AP-2 merges |
| No app-local bell or palette | `src/lib/shell/seams.test.ts` | Binds now |

---

## 12. Shell manifest (R9)

| Field | Value |
|---|---|
| Team | `across` |
| Purpose | "Everything that waits on you, and everyone you wait on." |
| Jobs | `ask-person`: verb "Ask", fields who, need and by when, form on the task card. `answer-wait`: verb "Answer", from the row |
| Search provider | None. ⌘K finds the task |
| Needs | The kinds of §3.2 that fill "For you". They feed the one needs registry (§7.3) |
| Home cards | My Day "Needs you", altitude `me` |
| Agent | Mira, through the AP-10 tools |

No palette, bell, assistant rail or ⌘K listener of its own.

---

## 13. Owner questions

**Answered.** On 2026-11-06 at 09:43 UTC, the owner chose "Accept all 8 recommendations" on task 1855 (run `ce35d443`). So AP-Q1 to AP-Q8 hold their recommended answers below. AP-Q1 amends D171.1 (`work_plan.md` §3). Later the same day, in chat, the owner asked for §4.5. AP-Q9 and AP-Q10 come from it.

At 10:33 UTC the owner answered AP-Q10: "Yes, a person of the circle only". AP-Q9 keeps its working default.

Each open question has a recommended answer. The build uses it as a working default only where the row says so.

| # | Question | Options | Recommended | Working default? |
|---|---|---|---|---|
| **AP-Q1** | Where does the full list live? | (a) A new sidebar row "Waiting" and the page `/waiting`. My Day keeps the top 5. (b) The My Day row only, as D171.1 says. (c) The Approvals app for every member, as AP-2 was filed | **(a).** It is what the owner asked for. It amends D171.1: My Day shows the top waits, and "Waiting" holds the full list | No. It adds a live pane |
| **AP-Q2** | Whose list holds an agent question? | (a) The run's member only. (b) Also the task's assignees, as the filter "Waiting on me" does | **(a).** The count then means "only you can unblock these". Assignees see the chip | Yes |
| **AP-Q3** | Who gets the Team lens, and what does it show? | (a) `data:org:read` and project leads. A filter by one person. No count for each person, no ranking. Read and nudge only. (b) The same, with a count for each person. This reverses ALN-Q5. (c) No Team lens | **(a)** | No |
| **AP-Q4** | May a member ask another member directly? | (a) Yes, a person in the app or the chat, with a card in the chat. Agents may not. (b) Also an agent, with a card each time | **(a).** §7.2 holds the guards | No |
| **AP-Q5** | Who reads the text of a person ask? | (a) Each reader of the task, because the ask is also a comment. (b) The two people only | **(a).** The task is the shared record | Yes |
| **AP-Q6** | A hand-off to a member: does that member see that you wait? | (a) Yes, by an opt-in ask, with the line "Priya will see that you wait". (b) No, it stays private | **(a)** | Yes, opt-in |
| **AP-Q7** | May a member forward an agent question to another person? | (a) Yes, AP-14. (b) Not now | **(b).** Forward needs the answer to go back to the run. Build it after AP-8 shows use | Yes |
| **AP-Q8** | PR 1741 (task 1750)? | (a) Merge it now, and AP-2 moves its section. (b) Hold it for AP-2 | **(a).** It fixes the bug that the owner hit today | Yes |
| **AP-Q9** | When does an owned wait with no answer open to its circle? | (a) After 24 hours. (b) Only when the member cannot act. (c) At once: the circle counts every wait | **(a).** The circle sees the wait at once in "Near you". The count and one bell come after a day, so the member gets the first chance | Yes |
| **AP-Q10** | May an agent name the person its question is for? | (a) Yes, one person of the circle only (AP-16). (b) No. The question stays with the run's member | **(a).** The circle gets the bell of each question already, so the field reaches nobody new. It changes part of AP-Q4, so the owner decided. **ANSWERED 2026-11-06 10:33 UTC: "Yes, a person of the circle only" (a).** | No |

---

## 14. Changelog

- 2026-11-06 — AP-0 written (task 1855). The research read `97da742ae` and the branch `l6s-waiting-list`. It found the store for person asks (`pm_decisions`), the nudge text defect (AP-BUG-1), and the open `/approvals` gateway read (§2.1).
- 2026-11-06 — The owner accepted AP-Q1 to AP-Q8 on task 1855. The owner then asked, in chat, that a wait on a task with no assignee reach the right people. Added §4.5 (the ladder, the circle and the open wait), V9 to V11, AP-15 (task 1875), AP-16 (task 1876), AP-Q9 and AP-Q10.
