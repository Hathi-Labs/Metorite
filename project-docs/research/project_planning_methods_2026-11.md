# Planning methods for the Metorite AI planner: operational brief

> **Research record for WS-PJP** (`project-docs/specs/project_planning.md`, D168). Written 2026-11-02 for the owner's ask that Metorite plans projects with the member. This file is evidence, not a contract. The spec owns what we build.

> **Source quality.** [W] marks a claim checked by a web search in the session of 2026-11-02, with its URL. [K] marks standard literature from background knowledge, not fetched again. Treat a [K] claim as a pointer to the named book, not as a verified quote.

> **Note on the store.** Metorite stores a dependency as a `pm_task_links` row with `link_type = 'blocks'`, not as a `blocked_by` field. Each task has one `estimate_mins` (a likely value), `start_date` and `due_at`. A project has no date of its own.

Bottom line: use a small stack. Decompose to deliverables and link dependencies. Get ranged estimates. Compute the critical chain with buffers in code. Name ONE constraint and run a premortem. Then monitor buffer and WIP weekly.

The AI applies the theory and asks at most 3-5 plain questions. Source tags: [W] = verified by web search this session. [K] = standard literature from background knowledge, not re-fetched.

Store assumptions: tasks have status, assignee, due date, `blocked_by`. Often missing: estimates, durations, capacity, start dates. Missing data is the main design problem. Rule: never ask the member for a number the AI can propose. Propose a range, member says "ok" or corrects.

---

## 1. CPM and PERT [K: Kelley/Walker 1959. PMBOK]
- **Idea:** longest dependency path sets the minimum duration. Tasks off that path have float.
- **Data:** duration per task + `blocked_by` DAG. Missing: durations (default from ranges, section 5).
- **Compute:**
  1. Topological sort. Reject cycles and report them.
  2. Forward pass: ES = max(EF of predecessors), EF = ES + d.
  3. Backward pass: LF = min(LS of successors), LS = LF - d.
  4. Float = LS - ES. Critical = float 0 (or below a small threshold).
  5. PERT: expected = (O + 4M + P)/6, sd = (P - O)/6. Path sd = sqrt(sum of variances).
- **Show:** "Longest path: A > C > F, 23 working days. Finish range P50 to P85. Tasks X, Y have 6 days of slack."
- **Ask:** only if a dependency looks missing. Two tasks have the same assignee or deliverable and no link. Ask: "Can B start before A is done?"
- **Fails:** no dependencies or durations (garbage in). Resource contention ignored (see CCPM). PERT's beta assumptions understate tail risk. Path-sum ignores merging paths (real finish later than computed).

## 2. Theory of Constraints [K: Goldratt, *The Goal* 1984]
- **Idea:** output of a system is set by one constraint. Improving elsewhere is an illusion.
- **Five steps:** identify, exploit (never idle, never starved, no rework), subordinate everything else, elevate (add capacity), repeat (watch for inertia).
- **Drum-buffer-rope:** drum = constraint's pace. Buffer = work queued ahead of it. Rope = release rule that stops upstream from over-feeding.
- **Data:** assignee and status per task. Open work per person. Queue ages. Missing: capacity.
- **Find the constraint (heuristic):**
  1. Per assignee: open assigned hours (or count) / available capacity. Highest load is a candidate.
  2. Per status: where do tasks wait longest? Pile-up directly before a person or step is a candidate.
  3. Check critical-chain tasks: who owns the most of them.
  4. Pick one. Name it in one sentence.
- **Show:** "Priya is the constraint: 9 of 14 critical tasks, 2.1x her capacity. Next: remove non-critical work from her, take review off her, then add help."
- **Ask:** "Is Priya really the only one who can do these?" (skill specificity), once.
- **Fails:** several constraints at once (name the first). Policy constraints (approvals) are not people. Tiny teams where "constraint" is just "the owner".

## 3. Critical Chain (CCPM) [K: Goldratt, *Critical Chain* 1997. Buffer zones W: https://success.sciforma.com/en/2026.08/topics/user/critical-chain-monitoring]
- **Idea:** the critical chain = longest path after resolving resource conflicts. Strip padding from tasks, pool it as buffers at the end.
- **Behaviors it counters:** Parkinson's law (work expands to the time given). Student syndrome (start at the last moment). Bad multitasking (switching adds delay to every item). Lost early finishes.
- **Data:** ranged estimates, `blocked_by`, assignee per task. Missing: resource calendars (assume full-time or set a default per member).
- **Compute:**
  1. Task estimate = aggressive median (about 50% confidence), roughly the PERT "M" or P50. If the member gave a padded date, use about 50-60% of it.
  2. Resource-level: if one assignee has two overlapping tasks, serialize them (order by chain priority). Re-run CPM. This yields the critical chain.
  3. Project buffer = about 50% of the chain's summed length. Or sqrt(sum of squared (safe - aggressive)) (root-sum-square) to avoid oversizing on long chains.
  4. Feeding buffer on every non-critical path that merges into the chain. Same sizing, shorter.
  5. Target date = chain end + project buffer.
  6. Fever chart: x = % of critical chain complete, y = % of project buffer consumed. Zones green / yellow / red. Example: 10 of 25 buffer days used = 40% penetration (yellow). [W: sciforma page above. Https://success.sciforma.com/en/node/71682] Rule: act when buffer use runs ahead of chain progress.
- **Show:** one number: "Planned finish Mar 14, buffer 8 days, 20% used, 40% done: green."
- **Ask:** "Is this date firm?" and "Who is on this full-time vs part-time?" (only if load data absent).
- **Fails:** members hear "your estimates are cut" (frame as "most likely time, protected by a shared buffer"). Projects under about 8 tasks (just use one buffer). Chain changes when resources change (recompute weekly). No resource data means CCPM degrades to CPM plus a buffer.

## 4. Toyota Production System, Lean, Last Planner
**Lean ideas for knowledge work** [K: Ohno 1988. Liker, *The Toyota Way*. Anderson, *Kanban*]
- Flow and pull: finish before start. Start new work only when capacity frees up.
- **WIP limit** and Little's law: avg cycle time = avg WIP / avg throughput. [K: Little 1961]. Compute from status history. If WIP is 3x throughput per week, items wait about 3 weeks.
- Wastes (muda), software mapping: partially done work, extra features, relearning, handoffs, task switching, delays/waiting, defects, unused talent.
- Takt: demand pace (deadline / remaining items) vs actual throughput.
- Heijunka: level the load: don't plan week 3 at 150% and week 4 at 20%.
- Jidoka: stop and fix: a blocked or failed item becomes the top item, not a background issue.
- Genchi genbutsu: ask the person doing it, not the dashboard. A3 / 5 Whys: use for a repeated slip. Obeya: one visible board.
- **Data:** status transitions with timestamps (cycle time, age), assignee, `blocked_by`. Missing: transition history if the store keeps only the current status (log it).
- **Compute:** WIP per assignee. Item age vs median cycle time (flag > P85). Blocked-time. Load per week vs capacity (level_load).
- **Show:** "Tom has 7 open tasks. Your median is 2 at a time. Finish or park 4." "3 tasks have not moved in 14 days."
- **Ask:** one question per stalled item: "What is it waiting on?" Maps straight to a `blocked_by` link or a reason code.
- **Fails:** WIP limits as dogma for people with legitimate waiting time. Flow metrics need 10+ finished items.

**Last Planner System** [W: https://leanconstruction.org/lean-construction-journal/doi-info-2019-131-146/. Make-ready: https://pure.au.dk/ws/files/51141782/Lindhard_and_Wandahl_2012_Improving_the_Making_Ready_Process.pdf]
- **Idea:** plan backwards from milestones with those who do the work (pull planning). Make every task "sound" before it enters a weekly plan. Measure promise reliability.
- **Levels:** master plan (milestones), phase pull plan, 6-week lookahead (remove constraints = "make ready"), weekly work plan (commitments), learning (PPC).
- **Task is sound if:** definition clear, prerequisites done, materials/info/access present, capacity exists, assignee can do it.
- **PPC** = tasks completed as promised / tasks promised for the week. Every miss gets one reason code (waiting on someone, unclear scope, rework, late input, capacity, priority change).
- **Studies:** tasks judged "sound" completed far more often than non-sound ones. Non-sound tasks often got into weekly plans anyway [W: Lindhard and Wandahl link above].
- **Compute:** lookahead = tasks due in the next 6 weeks. Check sound criteria in code (has assignee, due date, no open `blocked_by`, estimate exists). List failures as "to make ready". PPC from last week's commitments. Top 3 reasons.
- **Show:** "Next 6 weeks: 12 tasks, 7 ready, 5 need action (2 no owner, 3 blocked by X)."
- **Ask:** weekly, "Which of these will you commit to this week?" Then, on a miss, one pick-list reason.
- **Fails:** solo members (skip commitments ritual, keep sound check). Needs a weekly rhythm.

## 5. Work breakdown [K: PMI Practice Standard for WBS. Agile Alliance on DoD]
- **Idea:** decompose the outcome into deliverables (nouns), not activities. 100% rule: children cover exactly the parent's scope, nothing more.
- **Heuristics:** leaf = one owner, one outcome, 1-10 working days (the 8/80 rule means 8 hours to 80 hours). Each leaf has a one-line definition of done (DoD). Rolling wave: detail the next 4-6 weeks, keep far phases coarse. Milestone = a verifiable state, zero duration.
- **Data:** parent/child tree, titles, description. Missing: DoD, deliverable framing.
- **Compute (code):** flag leaves with no description or DoD. Leaves with no assignee. Parents with one child. Tasks with titles that are verbs only ("work on") and cannot be verified. Leaf too big (estimate > 10 days) or too small (< 2 hours: merge).
- **Show:** the tree with gaps highlighted, plus a proposed missing-branch list.
- **Ask:** "What does done look like, so someone else could check it?" (once per outcome). "Anything else that must exist at the end?" (100% check).
- **Fails:** over-decomposing far-future work. Decomposing by activity phase when deliverables differ. Treating a tree as a schedule.

## 6. Risk, bias, forecasting
- **Premortem** [W: Klein, `Performing a Project Premortem`, HBR Sept 2007. Summary https://glenbrook.com/?p=588]. Assume the project failed. List why. Use prospective hindsight. Run early. Output: top 3-5 risks, each with a mitigation task or trigger. AI proposes 8-10 likely causes from the plan's structure. Examples: single owner on chain, unlinked external dependency, no DoD, no estimate, far milestone. The member strikes or adds. Ask: "It is [date] and this failed. What is the most likely reason?" Fails: when it turns into a long generic risk register.
- **Planning fallacy and reference class** [K: Kahneman & Tversky 1979. Flyvbjerg 2006, "From Nobel Prize to project management"]. Inside view underestimates. Use the outside view: how long did similar past projects/tasks take? Compute from the org's history: ratio actual/estimate per task type or per assignee. Apply the ratio (P50 and P85) to new estimates. Needs 10+ closed tasks with both estimate and actual. Otherwise state "no history yet" and use default uplift ranges (for example P85 = 1.5-2x P50). Fails: tiny history, one-off projects.
- **Monte Carlo from throughput** [W: https://xebia.com/blog/accurate-forecasting-without-estimation/. ActionableAgile https://55degrees.atlassian.net/wiki/spaces/AAS/pages/2405826561/Monte+Carlo+How+Many]. No estimates needed. Throughput = count of tasks done per week (not points). "When": sample weekly throughput (with replacement) from the last 6-12 weeks until the remaining count is reached. Repeat 5-10k times. Report P50/P85/P95 dates. "How many": sum sampled weeks to a date. Quote P85 for outside promises. Needs 6+ weeks of done-history and tasks of broadly similar size (split outliers). Show: "85% chance all 24 remaining tasks are done by Dec 18." Fails: new team, size-varied tasks, scope growth (add a scope-growth rate = new tasks/week).
- **Cone of uncertainty** [K: Boehm 1981. McConnell]. Early estimates range 0.25x-4x. Narrow as decisions are made. So show ranges early, dates later, and never one date at intake.

## 7. Other high-value methods
- **Shape Up** [K: Singer, *Shape Up*, Basecamp]. Appetite = time you will spend (fixed), scope varies. Ask "How much time is this worth?" instead of "how long will it take?" Hill chart: uphill = figuring out, downhill = doing. Per-task state flag. Good fit for vague work.
- **Little's law** (see section 4): cycle time = WIP/throughput. Cheap, always computable.
- **Capacity planning:** capacity = days available x focus factor (about 0.6-0.7 default) minus absences. Needs a calendar. Ask once: "How many days a week can you give this?" Compare to load per week.
- **RACI/ownership:** each leaf has exactly one accountable assignee. Code check: no owner, owner with 3+ chain tasks, a task with a reviewer-less handoff. Ask only about gaps.
- **OKR alignment:** link a project to one outcome and 1-3 measurable key results. Flag orphan projects. Ask: "How will you know this worked?"
- **Dependency structure matrix (DSM):** an adjacency matrix of tasks. Code finds cycles and tightly coupled clusters. Suggest merging or an interface task. Use for 30+ tasks.
- **Eisenhower / value ordering:** when the chain forces nothing, order by value over effort. Low priority for this brief.

---

## Recommended planning workflow for an AI assistant
Legend: **[T]** deterministic tool, **[M]** model judgement, **[M+T]** model proposes, tool checks.

1. **Intake [M]:** read the existing project/tasks (`read_plan`). Ask at most: goal, deadline (firm or hoped), who works on it. Skip anything already in the store.
2. **Outcome and DoD [M]:** write a one-sentence outcome and 3-5 testable done criteria. Member approves. Ask "What does done look like?" once.
3. **Decompose [M+T]:** `decompose_outcome` drafts a deliverable tree. `lint_wbs` [T] checks leaf size, owners, DoD, single-child parents. Only the next 4-6 weeks in detail (rolling wave).
4. **Dependencies [M+T]:** model proposes `blocked_by` links. `check_graph` [T] finds cycles, orphans, missing links among same-owner tasks. Ask only about doubtful links.
5. **Estimates as ranges [M+T]:** model proposes O/M/P (or low/likely/high) from titles and history. `calibrate_estimates` [T] applies the member's own actual/estimate ratio when 10+ samples exist. Member edits only the few that look off. Fall back to Monte Carlo from throughput when estimates are absent.
6. **Critical chain and buffers [T]:** `compute_critical_path`, then `level_load` (resolve same-assignee overlaps), then `size_buffers`. Output: target date, buffer, float per task. Frame durations as "likely time", not padded.
7. **Constraint [T then M]:** `find_constraint` names one person, step, or approval. Model explains it and proposes exploit/subordinate/elevate actions.
8. **Premortem [M+T]:** `premortem` drafts risks from plan structure. Member strikes/adds. Each kept risk becomes a mitigation task or a watch trigger.
9. **Approval [M]:** show a one-screen plan: outcome, tree, dates (P50/P85), buffer, constraint, top 3 risks, what changes in the board. Member approves. Then the AI writes tasks/links/dates.
10. **Execution loop (weekly, and on any change) [T then M]:**
    - `buffer_status`: fever zone. Act only on yellow or red.
    - `wip_report` and Little's law: stalled tasks, overload.
    - `lookahead_ready`: next 6 weeks, what needs make-ready.
    - `forecast_completion`: refresh Monte Carlo.
    - Model summarises in 5 lines with one proposed action. Asks reason codes for misses (PPC). Re-runs steps 4-6 if scope or people changed.

## Tool list (function, purpose, inputs to outputs)
1. `read_plan(project_id)`: load tree, links, assignees, dates, status history. Out: normalised graph.
2. `lint_wbs(project_id)`: find WBS defects. Out: list of {task_id, issue}.
3. `check_graph(project_id)`: cycles, orphan tasks, suspect missing links. Out: issues + suggested links.
4. `calibrate_estimates(project_id, scope)`: actual/estimate ratios and default ranges. Out: ratio, sample size, P50/P85 multipliers.
5. `compute_critical_path(tasks, links, durations)`: CPM/PERT. Out: ES/EF/LS/LF, float, critical set, finish distribution.
6. `level_load(project_id, capacity)`: serialize overlaps, show load per person per week. Out: critical chain, overloads, move suggestions.
7. `size_buffers(chain, feeders, method)`: project and feeding buffers. Out: buffer lengths, target date.
8. `find_constraint(project_id)`: bottleneck ranking. Out: top candidate + evidence (load ratio, chain share, queue age).
9. `buffer_status(project_id)`: fever chart point. Out: % chain done, % buffer used, zone, trend.
10. `wip_report(project_id)`: WIP per person, item ages, Little's-law cycle time. Out: stalled and overloaded list.
11. `forecast_completion(project_id, runs)`: Monte Carlo from throughput with scope-growth. Out: P50/P85/P95 dates or "how many by date".
12. `lookahead_ready(project_id, weeks)`: soundness check. PPC and reason-code tally. Out: ready/not-ready list, PPC, top reasons.
13. `premortem(project_id)`: structural risk candidates. Out: ranked risks with suggested mitigation tasks.
14. `decompose_outcome(outcome, context)`: model-backed draft tree with DoD. Out: proposed tasks (not written until approved).

## Design cautions
- Never write to the board before approval (step 9).
- Show P50/P85 ranges, never a single false-precise date, until the cone narrows.
- A tool that lacks data returns "not enough data: need X", and the AI proposes a default or asks one question.
- Fences: CPM needs an acyclic graph. Monte Carlo needs 6+ weeks of history. calibration needs 10+ closed tasks with estimates. CCPM needs assignees.
