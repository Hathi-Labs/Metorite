# Plan with Metorite — the AI plans projects with the member (WS-PJP)

> **Product:** Metorite · **Feature:** planning in the Projects chat · **Updated:** 2026-11-02 · **Owner:** vjvarada

> **Status:** 📋 **SPEC, NOTHING BUILT.** PJP-0 (this spec) is the first slice. PJP-1 and PJP-2 are next, in parallel.

> **Verified against code on 2026-11-02 at `c8b31dcbf`.** Each path in §8 was read on that commit. The production facts in §2 come from the gateway journal and the database of 2026-11-02.

> **Board:** WS-PJP in `work_plan.md` §2.1. **Decision:** D168. D53, D87 to D89, D99, D109, D145 and D-PM-12 bind it.

> **Metorite:** project "Projects/Tasks App", epic 1476, this spec task 1477. The slices PJP-1 to PJP-11 are tasks 1478 to 1488, in order.

> **Research:** `project-docs/research/project_planning_methods_2026-11.md`.

> **Gate summary:** every slice is AGENT-SAFE. Two acts stay the owner's: the `TOOL_PACKS` line on production (PJP-2) and any eval over USD 0.50 (D168.4). Each slice that changes what a member sees follows the D145 loop.

---

## 0. Thesis

**The member does not need to know project management.** Metorite knows it. The member says what they want. Metorite finds the critical path, the constraint and the risks, and it proposes a plan. The member corrects it and approves it.

**The tools do the arithmetic, and the model does the judgement.** A critical path, a buffer, a load per person and a forecast come from code that a test can check. The model writes the breakdown, reads the result and explains it in plain words.

**Many changes, one approval.** Planning moves many things at once. Three or more related changes come on ONE card with a tick box for each change (D168.1).

**One assistant.** The member talks to Metorite. No Projects work goes to a second agent (D99).

**Never ask for a number that Metorite can propose.** Metorite proposes an estimate, a date or a stage list. The member says "yes" or corrects it.

---

## 1. Scope and non-goals

### 1.1 In scope

1. **Card fixes** (PJP-1): a status is a pill and a colour is a swatch. A colour is one of six hues.
2. **One assistant for Projects** (PJP-2): the main chat stops its hand-off. The Projects page opens the one assistant.
3. **One card for many changes** (PJP-3, PJP-4): batch tools for statuses and task edits. A rule tells the model when to use them.
4. **Stages that fit the work** (PJP-5): a catalogue of status sets by kind of work.
5. **Planning methods as tools** (PJP-6 to PJP-10): critical path, critical chain, constraint, flow health, premortem and forecast.
6. **The planning workflow** (PJP-11): the method that the AI follows from the first message to one approved plan.

### 1.2 Non-goals

1. **No migration in this spec.** The buffer and the finish line are tasks (D168.3). A project date, a baseline and a buffer chart wait for a later owner decision.
2. **No second analytics seam.** Flow health and the forecast extend `analytics_stuck`, `analytics_load` and `analytics_outlook`. They do not add a parallel module.
3. **No auto-schedule on the board.** D-PM-12 holds: a `blocks` arrow on the timeline warns, and it never moves a task. A date moves only when the member approves a card.
4. **No new colour.** D168.2 keeps the six hues of `statusAccent.ts`.
5. **No score per person.** The constraint names a person only to protect their time. No view ranks people.
6. **No sprint machinery.** Sprints belong to SP (task 19). The coach belongs to PC (task 958). Work styles belong to WM (task 977). §3.7 says how this spec feeds them.
7. **No proactive nudge.** A weekly replan prompt belongs to WS-AUTO (D161). This spec gives it the reads.

---

## 2. What happened on 2026-11-02, and what exists today

### 2.1 The owner's session, 04:14 to 05:06 UTC

The owner made a project and its stages in the main chat (thread `434ca331…`, org Hathi Labs). The gateway journal and the database show four faults.

1. **The hand-off.** Five turns ran. Four of them loaded the `projects` pack and still called `projects_assistant`. The line `executor.packs_loaded` reads `replaced=['crm-assistant','email-assistant']`, so the Projects specialist stayed on offer. The model took it, as OA-8e run 1 found that it does.
2. **One approval at a time.** Inside the sub-agent, `create_status` ran 3 times and `update_status` ran 8 times. Each call came in its own model round. Each card held the run until the click. The gaps were 83 s to 3.5 min. The stack (`cardStack.ts`) never had two cards. No two cards waited at the same time.
3. **`[in_progress]` on the card.** `create_status` writes `detail=f"{name} [{cat}]"` (`writes.py:3720`). `save_view` does the same with the view kind (`inbox.py:587`).
4. **Colours as text.** The tool accepts any string. The model saved seven hex codes (`#EAB308`, `#6366F1`, `#F97316`, `#94A3B8`, `#CBD5E1`, `#16A34A`, `#EC4899`) in `pm_task_statuses.color`. The card drew each code as words (`cardFields.ts:80`, kind `words`). The board knows six hue names (`statusAccent.ts:79`). `resolveHue` finds no alias for a hex code, so each lane falls back to its category colour. The member's choice never shows.

### 2.2 Why the hand-off stays

These counts come from the production line of each turn and from `_tool_count`.

| Item | Tools |
|---|---|
| The base of the orchestrator on production (`base_tools`) | 56 |
| The base and the core pack `projects` (`current_tools`) | 79 |
| `projects-plan` | 35 |
| `projects-structure` | 34 |
| `projects-bulk` | 21 |
| `projects-reminders` | 7 |
| `MAX_REQUEST_TOOLS` | 110 |

`plan_loader` (`tool_packs.py:2124`) reaches an owner only when EVERY pack of the owner that the run does not hold fits. With the specialist tool gone, 79 − 1 + 35 = 113, and 79 − 1 + 34 = 112. Both exceed 110. So `projects-assistant` is never in `via_loader`. `SPECIALIST_STAYS_UNTIL_REACHED` (`tool_packs.py:1679`) then keeps the specialist. `one_assistant_context.md` records this gap (§4.3.2, "As built, OA-8b").

A second path exists in code. The Projects page has its own "AI chat" entry and a docked rail (`AssistantRail.tsx`). Both are pinned to `projects-assistant`. Production has `NEXT_PUBLIC_SHELL_CHAT`, `NEXT_PUBLIC_SHELL_BAR` and `NEXT_PUBLIC_SHELL_NAV` on. So `shellChatOn()` is true, `CHAT_LIVE` is false (`page.tsx:300` to `:303`), and production shows neither. The owner's session used the main chat.

### 2.3 Seven days of production use (2026-10-26 to 2026-11-02)

The journal's `permission.decision` lines count each tool call. In `projects-plan`, the 11 report tools had 0 calls. `create_project` had 15, `update_project` 5 and `task_dataset` 5. In `projects-structure`, `update_status` had 16 and `create_status` 3. The inbox, area, notification and access tools had 0. §3.3 uses these counts.

### 2.4 What exists

- **The batch seam.** `_BatchRow`, `_ask_batch` and `_apply_batch` (`writes.py:2177`, `:2316`, `:2341`) draw one card with a tick box per row and apply the ticked rows. `create_tasks`, `file_tasks`, `create_tags`, `create_types` and `link_tasks_batch` use it. No status tool and no per-task edit tool uses it.
- **Card rows.** `clean_card_rows` (`ask_tools.py:742`) takes `{id, label, hint, checked}` and the optional keys `kind`, `class` and `warning`. `ConfirmationCard.tsx:418` draws the label and the hint through `CardText`, so a «fenced» name draws as a pill.
- **Planning data.** `pm_tasks` has `estimate_mins`, `start_date`, `due_at`, `completed_at` and the assignees. `pm_task_links` has `link_type` `blocks`. `pm_projects` has no date. No code computes a critical path. "critical path" is only a load word (`tool_packs.py:584`).
- **Planning tools.** `propose_plan` (`forms.py:1000`) makes a NEW project with keys, effort, dates, `after` links and phases. `analytics_outlook` gives a velocity forecast. `team_capacity`, `fit_for_task`, `rebalance`, `find_conflicts`, `analytics_load` and `analytics_stuck` exist. The gateway route `POST /projects/plan/preview` gives fit and hours per owner.

---

## 3. The design

### 3.1 One card for many changes (D168.1, PJP-3, PJP-4)

**The rule.** The model counts the changes of one request.

| Changes | What the member sees |
|---|---|
| 1 | One card |
| 2 | Two cards that wait together, as a stack |
| 3 or more, related | ONE card with a tick box per change, from a batch tool |

"Related" means one request of the member: set up stages, move these tasks, plan this project. Two unrelated requests in one message are two sets.

**The batch tools.** Each one uses the batch seam of §2.4. Each validates every row before the card, the same way the single tool validates one change. A row that fails validation does not reach the card. The card's note names it.

| Tool | Pack | Rows |
|---|---|---|
| `create_tasks`, `file_tasks` | `projects-bulk` | exists |
| `link_tasks_batch` | `projects-plan` | exists |
| `create_tags`, `create_types` | `projects-structure` | exists |
| **`change_statuses`** (PJP-3) | `projects-structure` | add, rename, recategorise, recolour or reorder a lane |
| **`update_tasks`** (PJP-4) | `projects-bulk` | a different date, start, estimate, status, title, priority, tags or type on each task. Owners change through `assign` (see PJP-8) |

A delete stays a single guarded act (class C). No batch tool deletes.

**`update_tasks` starts with an extraction.** `update_task` (`writes.py:1430` to `:1533`) mixes its checks with `_confirm` and the patch. It has no validator that a row can call. So step 1 of PJP-4 extracts a pure `_plan_task_edit(task, args)`. It returns the payload, the before values and the card text, or a refusal string.

`update_task` and the new row class (a `_BatchRow`, as `_LinkRow` and `_FileRow` are) both call it. Each row then patches `/projects/tasks/{tid}` with the same params. Three more rules bind the rows:

- A row that would ask a question (the `include_subtasks` ask of `_edit_task_fields`) is a refused row. A batch never asks in the middle.
- The priority fields keep the `@takes_priority` rules of `update_task`.
- A call takes at most 50 rows, as `bulk_update` does. `bulk_update` stays the tool for ONE change across many tasks. `update_tasks` is the tool for a different change on each task.

**A row shows its values.** A row may carry two new optional keys, `hue` and `category`. The client draws them with the same `CardFieldValue` kinds as a card field: a swatch and a category chip. `clean_card_rows` accepts them like `kind`, `class` and `warning`.

**The rule text.** The pack rules (`tool_packs.py`, rules block) and `apps/agents/agent-projects/instructions.md` state the table above, in one place each. Two writes in one step must reach the member as a stack. PJP-3 checks that the executor runs two `request_confirmation` calls of one step at the same time. If it does not, PJP-3 fixes that first.

### 3.2 One colour vocabulary (D168.2, PJP-1)

**The six hues** of `ACCENT_HUES` stay the only status colours: gray, red, amber, green, blue and violet.

**Any other colour maps to the nearest hue.** One rule covers statuses, tags and types. Tags already use the six hues (`TAG_COLORS` in `app/projects/lib/tags.ts:51`). The rule lives in two places that a parity test holds together:

1. **The server** (`skill_projects/hues.py`, a new helper `hue_of`): a known name or alias maps through the alias table. A hex code maps by its HSL value. A saturation under 0.30 is gray. The hue angle decides the rest. Red runs from 345° to 15°, amber to 65°, green to 170°, blue to 250° and violet to 345°. The status, tag and type tools store the hue name. The gateway routes that take a colour apply the same map by import of `skill_projects.hues`. The gateway imports `skill_projects` today (`authority.py`, `delta.py`). It must not copy the map. So the app and the chat store the same value. These routes take a colour:
   - the status routes in `admin.py`: create (:195 to :221), `PATCH` (:229) and the copy of a set (:779 to :785)
   - the type routes in `admin.py`, about :991 to :1000
   - the tag routes in `tags.py`: :192, :419, :454 and :507.
2. **The client** (`statusAccent.ts`, `resolveHue`): a stored hex code maps by the same rule. So the seven lanes of §2.1 draw in their chosen colours with no data change. `statusAccent.ts` exports `COLOR_ALIASES` and the hue bands, so the parity test reads values and does not parse source.

**The card shows a swatch.** `cardFields.ts` gains the kind `color`. `CardFieldValue` draws a dot in the hue and the hue's name. A change draws as the old swatch, an arrow and the new swatch.

**The pill.** A card detail line never puts a category in brackets. The category is a field of the card, so it draws as the category chip. A tool result that the model reads names the category in words: "category: in progress".

### 3.3 One assistant for Projects (PJP-2)

**The fit rule.** With the core pack held, each other Projects pack must fit with the specialist gone: 79 − 1 + n ≤ 110, so n ≤ 32. This spec keeps one tool of slack: **each other Projects pack holds 31 tools or fewer**, including the tools that later slices add.

**The split** follows §2.3.

| Pack | Before | Moves | After PJP-2 | Room for later slices |
|---|---|---|---|---|
| `projects` (core) | 23 | none | 23 | — |
| `projects-plan` | 35 | the 11 report tools leave | 24 | PJP-6 to PJP-10 add up to 6 |
| `projects-reports` (new) | — | `add_report_recipient`, `remove_report_recipient`, `render_report`, `report_delete`, `report_list`, `report_recipients`, `report_render`, `report_save`, `set_report_schedule`, `status_report`, `export_tasks` | 11 | — |
| `projects-structure` | 34 | the task inbox, areas, contexts and notifications leave (10) | 24 | PJP-3 and PJP-5 add 2 |
| `projects-reminders` | 7 | gains `capture_intake`, `intake_queue`, `triage_intake`, `create_area`, `delete_area`, `rename_area`, `my_areas`, `my_contexts`, `notifications`, `mark_notifications_read` | 17 | — |
| `projects-bulk` | 21 | none | 21 | PJP-4 adds 1 |

**A side effect, intended.** At base 56, the core and `projects-plan` now fit together (79 + 24 = 103). So a pack word that loads `projects-plan` no longer drops the core pack (`replaces_core`). The owner is also reached while `email` is held (87 − 1 + 24 = 110). The tests that pin the old premise change in PJP-2:

- `test_projects_packs.py`: `SLUGS` and `SIZES` (:69 to :71), the 120-tool union (:119) and the "35 tools" answer (:716).
- `test_projects_packs.py`: "not reached with email held" (:811), the access request (:939) and the compound words (:887 to :915).
- `test_projects_packs.py`: the reminders size (:1069 to :1082) and the core rules text (:525).
- `test_pack_cues.py:45`, `test_pack_guards.py:1200`, `test_pack_no_double_write.py:52`, `test_pack_rules_oa8g.py:13` and `:46`, `test_tool_packs_choice.py:1413`, `test_tool_packs_registry.py:104`, `test_one_assistant_measure_oa8e.py:333` and `evals/one_assistant_measure/scenarios_work.py`.

The rule text changes in `tool_packs.py`. The core rules (about :421 to :431) and `_PROJECTS_PLAN_RULES` (:440 to :460) change. The structure rules (:492 to :500), `_PROJECTS_STRUCTURE_WORDS` (:611 to :615) and the plan words (:583 to :588) change. The `when` lines of plan, structure and reminders change. The new pack needs a `Pack` and a `SKILL_FAMILIES` entry with its `owner` and `replaces_core`. The rules-length cap of the test is 3450 characters.

`projects-reminders` becomes the pack of "my own organising": reminders, the task inbox, areas and contexts, and notifications. Its `when` line and its load words change to match. The slug stays, because `TOOL_PACKS` names it.

**The flag line.** `projects-reports` is a new slug. Production's `TOOL_PACKS` is an explicit list. `plan_loader` needs EVERY pack of the owner in the allow list.

So the slug must join `TOOL_PACKS` on production BEFORE the release that ships PJP-2. That line is owner-only (`work_plan.md` §6 D4). The old code drops an unknown slug, so the order is safe. Until the line exists, the hand-off stays as it is today. Nothing gets worse.

**The rail.** With the three shell flags on, production already withdraws the Projects page's "AI chat" entry and its docked rail (§2.2). The page publishes its place to the shell assistant (`projectsAssistantFacts`, through `useAssistantFacts` in `lib/shell/assistant.ts`). So PJP-2b removes the dead `CHAT_LIVE` paths. These are `page.tsx` :4621 to :4628, :5791, :3022, :5220 and :5545 to :5613, `AssistantRail.tsx` and its use in `lib/chatDock.ts`.

`buildProjectsAssistantPersona` stays, because the facts use it. PJP-2b is low in priority. It removes a second door to the specialist (D99, D87 to D89).

**The measure.** One OA-8e Projects run after PJP-2, with `--budget-usd 0.50` (D168.4). Done when the pack arm has 0 hand-offs to `projects-assistant` and does not score below the delegation arm. If the measure cannot fit in USD 0.50, the slice ships on its fences. Then the check is the production journal after the release. It must show 0 `agent=projects-assistant` runs from the main chat for a day of real use.

### 3.4 The planning engine (PJP-6 to PJP-10)

**One pure module.** `apps/skills/skill-projects/skill_projects/planning.py` holds the arithmetic. It does no I/O. It takes plain data (tasks, links, people, history) and returns plain data. The tools in `reads.py` fetch the data through the existing gateway reads and call the module. A test feeds the module fixed graphs and checks each number.

**Units.** A working day is 6 focus hours (360 min), a named constant. Saturday and Sunday are not working days. `estimate_mins` is the likely (P50) estimate.

**Missing data never stops a read.** Each result names what it lacked: "6 tasks have no estimate". The model then proposes estimates and writes them with `update_tasks` on one card. Each tool has a minimum. Below it, the tool says "not enough data: need X" and computes nothing:

| Computation | Minimum |
|---|---|
| Critical path | 2 linked tasks, no cycle |
| Critical chain | the critical path, and an owner on each chain task |
| Constraint | 5 open tasks with owners |
| Flow health | 10 finished tasks in the project or its root |
| Forecast | 6 weeks of finished-task history |

**PJP-6 `analyze_plan(project_id)`, a read.**
1. The critical path method: a topological sort over `blocks` links, a forward and a backward pass, and slack per task. A cycle is reported with its tasks. Then no path is computed.
2. Gaps in the breakdown: a task with no owner, estimate or description. Also an estimate over 10 working days, a parent with one child, and a vague title ("work on X").
3. Readiness (the Last Planner System) covers each task due in the next 6 weeks. A ready task has an owner, an estimate and no open blocker. The result lists the tasks that are not ready, with the reason.
4. The finish: the critical path's end against the latest due date of the project.

**PJP-7 the critical chain.**
1. `plan_chain(project_id)`, a read: start from PJP-6. When one person owns two tasks that overlap, serialize them in chain order and run the passes again. That gives the critical chain. The project buffer is 50% of the chain's length, Goldratt's simple rule. A feeding buffer is 50% of each path that joins the chain. The target date is the chain's end plus the project buffer.
2. `set_chain(project_id)` is a write on ONE card. It creates the "Project buffer" task and the "Finish" milestone (D168.3). It links the chain's last task to the buffer, and the buffer to the finish. It moves the dates that the chain changed. The buffer task has the tag `Buffer`. The finish task has the tag `Milestone`, an estimate of 0 and the target date as its due date. The tool finds them again by tag.
3. `buffer_status(project_id)` is a read. It gives the share of chain work done, by estimate. It gives the share of the buffer used: the chain's projected end past its planned end, against the buffer. The zone uses the burn ratio, used share ÷ done share. Green is up to 1.0, yellow up to 1.5, and red above 1.5. Under 10% done, the zone uses the used share: green to 15%, yellow to 33%, red above. These thresholds are named constants and working defaults.
4. Estimates are likely values, so the tool never cuts the member's estimates. The rules tell the model to ask for the likely time, and to put the safety time in the buffer.

**PJP-8 `find_constraint(project_id)`, a read.** For each owner, it reads open work against capacity (`team_capacity`). It also reads the owner's share of chain work and the age of their queue. For each status, it reads the median age of the tasks that wait in it. The tool names ONE constraint with its evidence.

The rules then give the model the five focusing steps in plain words. Protect the constraint's time. Move other work away from it. Then add help. Each proposed move comes on one `update_tasks` card.

A move of work to another person changes the owner. `update_task` has no owner field, because `assign` owns that write. So PJP-8 adds owner rows to the batch: an `assignees` key on `update_tasks` rows through the same check as `assign`, or a batch `assign`. The choice is PJP-8's, and it adds no second check.

**PJP-9 flow health (lean).** It extends the existing reads.
- `analytics_load` gains work in progress per person and per status.
- `analytics_stuck` gains each task's age against the project's 85th-percentile cycle time.
- `analytics_throughput` gains Little's law: cycle time = work in progress ÷ throughput.
- The rules add stop-and-fix: a blocked task on the chain is the first item of any answer. For a stalled task, the model asks one question, "what is it waiting on?", and offers a `blocks` link.
- Levelling uses `rebalance`. It does not get a new tool.

**PJP-10 premortem and forecast.**
1. `premortem(project_id)` is a read. It finds risk candidates from the plan's shape. Each rule below is one candidate:
   - one person owns more than half of the chain
   - a chain task has no owner
   - more than 30% of tasks have no estimate
   - a task waits on another project
   - the chain has no buffer
   - the finish is after the due date
   - a task is longer than 10 working days
   - a milestone is more than 6 weeks away, with none before it.

    The model adds its judgement and offers a mitigation task for each kept risk, on one `create_tasks` card.
2. The forecast extends `analytics_outlook`. It is a Monte Carlo run over weekly throughput of the last 12 weeks. It makes 5,000 runs with a fixed seed. It gives P50 and P85 dates for the open tasks. The answer quotes a range, never one date, until the chain has a buffer.

### 3.5 The tool budget

| Pack | After PJP-2 | Added | Final | Cap (§3.3) |
|---|---|---|---|---|
| `projects-plan` | 24 | `analyze_plan`, `plan_chain`, `set_chain`, `buffer_status`, `find_constraint`, `premortem` | 30 | 31 |
| `projects-structure` | 24 | `change_statuses`, `suggest_stages` | 26 | 31 |
| `projects-bulk` | 21 | `update_tasks` | 22 | 31 |

A slice that would pass the cap moves a tool with no calls out first, or it stops and says so.

### 3.6 Stages that fit the work (PJP-5)

**A catalogue in code.** `skill_projects/stage_sets.py` holds one status set for each kind of work. Each stage has a name, a category and a hue.

| Kind | Stages |
|---|---|
| Software | Backlog, Ready, In progress, In review, Testing, Done |
| Hardware and product development | Idea, Design, Prototype, Testing, Ready for production, Done |
| Manufacturing order | Order received, Materials, In production, Quality check, Ready to ship, Delivered |
| Marketing campaign | Idea, Brief, Creating, Review, Scheduled, Live, Done |
| Content | Idea, Writing, Editing, Review, Scheduled, Published |
| Client delivery | Scoping, Planned, In delivery, Client review, Delivered |
| Hiring | Sourcing, Screening, Interview, Offer, Hired, Not hired |
| Event | Planning, Booking, Promotion, Ready, Done |
| Research | Question, Investigating, Analysis, Write-up, Done |
| Support and operations | New, Triage, In progress, Waiting, Resolved |
| General | To do, In progress, Waiting, Done |

The builder sets the category and the hue of each stage, with these limits:

- Each set has one done stage. A "Not hired" or similar end stage is cancelled.
- Each set has a waiting stage, or a review stage, where work queues. PJP-8 reads queue age there.
- Hues follow the category, as `CATEGORY_HUES` does. A review or a waiting stage takes violet or amber, so the queue is visible.

**`suggest_stages(project_id, kind)`, a read.** It returns the set for the kind, and the project's current set beside it. The model infers the kind from the project's name and goal. It asks one question only when it cannot tell. Then it calls `change_statuses` once. The card shows each new or changed lane, with its swatch and category chip.

### 3.7 How this feeds PC, WM and SP

- PC-3 (task 972) sets up a team's way of working on one card. It uses `change_statuses` and `suggest_stages`. It does not add a second card.
- WM (task 977) defines work styles: flow, sprints, cycles and plan-driven phases. A work style may name a stage set from §3.6. WM must not add a second catalogue.
- The coach of PC teaches the methods of this spec with the member's own data. It reads the results of PJP-6 to PJP-10. It does not compute them again.

### 3.8 The planning workflow (PJP-11)

The rules of `projects-plan` and W1 of the agent instructions hold these ten steps. [T] is a tool, [M] is the model, and [M+T] means that the model proposes and a tool checks.

1. **Intake [M].** Read the project. Ask at most three things that the store does not hold. These are the goal, the date and who works on it.
2. **Outcome [M].** Write one sentence for the outcome and three to five done criteria.
3. **Stages [M+T].** `suggest_stages`, then one `change_statuses` card when the project has no fitting set.
4. **Breakdown [M+T].** Deliverables, not activities. Each leaf has one owner, one outcome, 1 to 10 working days and a done line. Detail the next six weeks only.
5. **Links [M+T].** Propose `blocks` links. `analyze_plan` finds cycles and gaps.
6. **Estimates [M+T].** Propose the likely time for each leaf. The member corrects only the ones that look wrong.
7. **Chain and buffer [T].** `plan_chain`, then `set_chain` on one card.
8. **Constraint [T then M].** `find_constraint`, then one sentence and one proposed move.
9. **Premortem [M+T].** `premortem`, then mitigation tasks on one card.
10. **One plan to approve [M].** `propose_plan` shows the whole plan on one card. After the approval, a weekly check reads `buffer_status`, `analytics_stuck` and readiness.

`propose_plan` gains `into_project_id`, so a plan can fill a project that exists. Today it refuses that case and sends the model to `create_tasks` (§29.2 of `projects_ai_chat.md`).

---

## 4. Data

No migration. No new table. Each read uses an existing gateway route. A slice may need a new read route, for example the links of a whole tree in one call. That route runs against a real database first (R8).

The buffer and the finish are ordinary tasks with the tags `Buffer` and `Milestone` (D168.3). The member can see them, move them and delete them. `set_chain` finds them again by tag. When the member deletes one, the next `set_chain` proposes it again on its card.

---

## 5. Slices

| Slice | Task | What | Done when | Tier | Gate | Needs |
|---|---|---|---|---|---|---|
| PJP-0 | 1477 | This spec, D168, the board row, the INDEX entry and the research file | It merges with no `ste-lint` error in the new lines | LOW | AGENT-SAFE | — |
| PJP-1 | 1478 | §3.2: the hue map for statuses, tags and types, on the server, the routes and the client. The `color` card kind, tested on status, tag and type cards. No brackets on a card (`writes.py:3720`, `inbox.py:587`) | The fences of §10 for PJP-1 pass. A card for `update_status` with `#EAB308` shows an amber swatch and stores `amber`. The seven lanes of §2.1 draw in amber, blue, amber, gray, gray, green and violet on the board. The visual review gate passes (four captures) | LOW | AGENT-SAFE | — |
| PJP-2 | 1479 | §3.3: the pack split, its fence, the `when` lines and load words | `plan_loader` with the production allow list and the core held puts `projects-assistant` in `via_loader`. The fence of §10 passes. The measure or the journal check of §3.3 passes | MEDIUM | AGENT-SAFE. The `TOOL_PACKS` line is OWNER-GATE | — |
| PJP-2b | 1479 | §3.3: remove the dead `CHAT_LIVE` rail paths | No `AgentChat` or `useAgentSessions` call with `PROJECTS_AGENT` stays outside the shell. `seams.test.ts:139` counts one fewer rail wrapper. The Projects page looks the same with the shell flags on. The visual review gate passes | LOW | AGENT-SAFE | — |
| PJP-3 | 1480 | §3.1: `change_statuses`, the row keys `hue` and `category`, and the rule text | One call with 8 rows draws ONE card with 8 tick boxes, each with its swatch and chip. Ticking 6 writes 6. Two writes in one step show as a stack of 2 | MEDIUM | AGENT-SAFE | PJP-1 |
| PJP-4 | 1481 | §3.1: step 1 extracts `_plan_task_edit` from `update_task` (`writes.py:1430`). Step 2 adds `update_tasks` | `update_task` and each row call `_plan_task_edit`, and the existing `update_task` tests pass unchanged. One call with 12 rows draws ONE card. Each row shows before and after. Each ticked row writes through the same route as `update_task`. A failed row does not stop the next one, and the receipt names it | MEDIUM | AGENT-SAFE | — |
| PJP-5 | 1482 | §3.6: `stage_sets.py` and `suggest_stages` | Each set passes the limits of §3.6 in a test. "Set up stages for a hardware project" in a live turn gives one `change_statuses` card | LOW | AGENT-SAFE | PJP-2, PJP-3 |
| PJP-6 | 1483 | §3.4: `planning.py` passes and `analyze_plan` | The module tests of §10 pass for a chain, a diamond, a cycle, a task with no estimate and a graph with no links | LOW | AGENT-SAFE | PJP-2 |
| PJP-7 | 1484 | §3.4: `plan_chain`, `set_chain`, `buffer_status` | A fixed project with two people and 9 tasks gives the chain, the buffer and the target date in a test. `set_chain` makes the buffer and finish tasks on one card. A second call finds them and proposes no copy | MEDIUM | AGENT-SAFE | PJP-4, PJP-6 |
| PJP-8 | 1485 | §3.4: `find_constraint` | A fixed team where one person owns 6 of 9 chain tasks names that person, with the evidence | LOW | AGENT-SAFE | PJP-6 |
| PJP-9 | 1486 | §3.4: flow health in the existing analytics reads | Each extended read returns the new lines, and its old lines do not change | LOW | AGENT-SAFE | — |
| PJP-10 | 1487 | §3.4: `premortem` and the Monte Carlo forecast | Each rule of the premortem fires on its own fixture. The forecast with a fixed seed gives the same P50 and P85 twice | LOW | AGENT-SAFE | PJP-6 |
| PJP-11 | 1488 | §3.8: the workflow in the rules and instructions, and `into_project_id` | A scripted turn "plan the launch of X" calls the tools in the order of §3.8 and shows one plan card. A live check stays inside USD 0.50 | MEDIUM | AGENT-SAFE | PJP-5 to PJP-8 |

### 5.1 Build order and parallel work

1. **Now, in parallel:** PJP-1, PJP-2 and PJP-4. They touch different files. PJP-2 owns the pack lists in `skill_families.py`. PJP-4 adds its tool to `projects-bulk` after PJP-2 merges, or in a rebase.
2. **Then:** PJP-3 after PJP-1 (the row keys need the `color` kind). PJP-2b and PJP-9 at any time.
3. **Then:** PJP-6, then PJP-7, PJP-8 and PJP-10 in parallel.
4. **Last:** PJP-5 and PJP-11.

Each slice that changes what a member sees follows D145. That is a preview on this PC, the owner's Approve, the local gate and the merge. Production follows at the 21:30 UTC release.

---

## 6. Safety

1. **Every write keeps its card.** A batch tool asks once for many rows. It never skips the card. The rows answer names ticked ids only, and `_ask_batch` refuses an id that it did not offer.
2. **A batch row is validated like a single call.** A batch tool must call the same validation as its single tool. It must not copy it. A fence checks this for each batch tool (§10).
3. **The held chat.** A chat that read outside mail is held (`egress.py`). Each batch tool is a first-party write with a server-built card. RX-1 (task 1402) decides whether such a write stays in a held chat. This spec adds no exception.
4. **A planning read shows only what the member can read.** Each read goes through the gateway routes, so the member's grants apply. A hidden task appears as "a hidden task", as `link_tasks_batch` does today.
5. **Text from members is data.** Titles and descriptions go to the model fenced, as today.

---

## 7. What other tools do

The research file holds the methods and their sources. A survey of competing products was not part of this work. A later slice may add one. Until then, nothing in this spec copies a product.

---

## 8. Current file paths

| Area | Path |
|---|---|
| Status tools | `apps/skills/skill-projects/skill_projects/writes.py` (`create_status` :3695, `update_status` :3732) |
| Batch seam | `writes.py` (`_BatchRow` :2177, `_ask_batch` :2316, `_apply_batch` :2341) |
| Batch tools today | `forms.py` (`create_tasks` :1671, `create_tags` :2271, `create_types` :2290), `writes.py` (`link_tasks_batch` :2987), `guarded.py` (`bulk_update` :1135) |
| View card | `inbox.py:587` |
| Plan tool | `forms.py` (`propose_plan` :1000) |
| Analytics reads | `reads.py` (`analytics_outlook` :1073, `team_capacity` :1193, `find_conflicts` :1418) |
| Card rows | `packages/acb_skills/acb_skills/ask_tools.py` (`clean_card_rows` :742, `_ROW_EXTRA_KEYS`) |
| Packs | `packages/acb_skills/acb_skills/tool_packs.py` (`MAX_PACK_TOOLS` :117, `PACKS` :708, `MAX_REQUEST_TOOLS` :992, `SPECIALIST_STAYS_UNTIL_REACHED` :1679, `plan_loader` :2124) and `skill_families.py` (tool lists) |
| Agent | `apps/agents/agent-projects/agents.py`, `instructions.md` (W1) |
| Status routes | `apps/services/gateway/gateway/routes/projects/admin.py` |
| Hues | `workbench/control_plane/src/lib/statusAccent.ts` (`ACCENT_HUES` :79, `COLOR_ALIASES` :181, `resolveHue` :309) |
| Card fields | `workbench/control_plane/src/lib/cardFields.ts`, `src/components/CardFieldValue.tsx`, `src/components/ConfirmationCard.tsx` |
| Projects chat | `workbench/control_plane/src/app/projects/components/AssistantRail.tsx`, `src/app/projects/page.tsx` (:4628, :5791) |
| New in this spec | `skill_projects/planning.py`, `skill_projects/stage_sets.py`, `skill_projects/hues.py` |

---

## 9. Verification commands

```bash
# Python: the packs, the card rows, the Projects skill
uv run pytest tests/unit/test_projects_packs.py tests/unit/test_tool_packs_choice.py \
  tests/unit/test_confirmation_rows.py -q
uv run pytest tests/unit/test_card_words.py tests/unit/test_pack_cues.py tests/unit/test_pack_guards.py \n  tests/unit/test_pack_no_double_write.py tests/unit/test_pack_rules_oa8g.py tests/unit/test_tool_packs_registry.py -q
uv run pytest tests/unit -k "projects_ and not calendar" -q   # the skill's own tests live in tests/unit
uv run python scripts/affected_tests.py --run

# Frontend
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/statusAccent.test.ts src/lib/cardFields.test.ts

# The whole local gate before a push (D145)
bash scripts/ci_local.sh
```

The live measure of PJP-2 runs the OA-8e harness with `--budget-usd 0.50`.

---

## 10. Fences (R7)

| Rule | Fence | Slice |
|---|---|---|
| A status colour is one of six hues | A test feeds `hue_of` every alias and the seven hex codes of §2.1, and checks each result | PJP-1 |
| Server and client map a colour the same way | A parity test compares the exported `COLOR_ALIASES` and hue bands of `statusAccent.ts` with `hues.py`, through a JSON fixture that a vitest writes and pytest reads, or the reverse | PJP-1 |
| A card detail has no bracketed category | A test calls each status and view tool with a stubbed `_confirm` and checks that no detail matches `\[[a-z_]+\]` | PJP-1 |
| Each other Projects pack fits beside the core | `test_projects_packs.py`: with base 56 and the core held, each other pack is 31 tools or fewer, and `plan_loader` puts `projects-assistant` in `via_loader` | PJP-2 |
| No `AgentChat` or `useAgentSessions` call pins `PROJECTS_AGENT` outside the shell | A source scan of `workbench/control_plane/src`. Other names of the agent (`nav.ts`, `agentName.ts`, `ProjectToolCards`, `spend.ts`) are legal | PJP-2b |
| A batch tool validates like its single tool | For each batch tool, a test sends one bad row and checks the same refusal text as the single tool | PJP-3, PJP-4 |
| The planning arithmetic | Module tests of `planning.py` on fixed graphs | PJP-6 to PJP-10 |
| Each stage set keeps its limits | A test over `stage_sets.py` | PJP-5 |

The rule text of §3.1 is advisory until PJP-11's scripted turn passes.

---

## 11. Owner gates

1. **`TOOL_PACKS` on production** gains `projects-reports` before the release that ships PJP-2. Owner-only (`work_plan.md` §6 D4).
2. **An eval over USD 0.50** needs the owner's OK (D168.4).
3. **A flag that spends credits** stays the owner's flip. No slice here adds one.

---

## 12. Change log

- 2026-11-02 — PJP-0. The owner reported four faults from a morning session and asked for planning methods. The agent read the production journal and the database, and wrote this spec. The owner answered four questions in chat, recorded as D168.
