---
slug: kit/plan-the-work
version: 5
title: "Kit: plan the work on the board"
summary: How an agent turns a goal into epics and slices on a Metorite board, with the approval of a person at each gate, using the tools of today.
audience: agent
---

# Kit: plan the work on the board

This page tells an agent how to plan a goal on a Metorite board with the tools that exist today. `start-here` defines the words. Where a tool is not built, this page says so and gives the step to use until it ships.

## The rules

1. **The plan lives on the board.** A plan that lives only in a file is invisible to the people who decide. You may draft in a file, but the board holds the result.
2. **A person approves each gate.** Make the records of a gate only after its approval.
3. **One writer for each slice.** Cut the work so that two slices do not change the same files.
4. **Do not guess dates or paths.** Read the order with `get_plan`, and use its answer and not your own. Where it cannot answer yet, write the order down and show your sums.
5. **The person decides.** Ask with `ask_on_task`. Do not ask only in the terminal.
6. **Plan a ladder of rungs.** Each rung ends in a demo that a person can see and use. Gate 1 draws the ladder.

## Before you start

1. Search for an existing plan with `search_tasks` and the words of the goal. Do not make a second plan.
2. Find or make a planning task in the project, and claim it (`claim_task`). Its run holds your questions and your progress lines.
3. Call `get_project` for the project. Keep three things: the status ids, the type whose `is_epic` is true, and the tags. Call `list_tags` for the tags of every project, and reuse a name that exists. Call `list_members` for the people you may assign.
4. Check that your connection can write. `whoami` shows the scopes.

## Gate 1: what

1. Ask the person at most five questions. Ask about the result, the people who will use it, the date, the budget and what is out of scope.
2. Write the goal: the result in one sentence, how you know it is met, and the target date.
3. Draft two to six epics. Give each epic this contract:
   - **Outcome:** one sentence that is true when the epic is done.
   - **Acceptance:** three to seven checks that a reviewer can tick.
   - **Out of scope:** what this epic does not do.
   - **Risk tier:** LOW, STANDARD or HIGH. Security, money, access and data changes are HIGH.
   - **Appetite:** the review hours and the credits that the epic may spend.
4. Draw the ladder. A rung is one step that ends in a demo. A person sees and uses the result.
   - **Rung 0** holds urgent safety work: a leak, data loss, a broken live feature or an owner gate with a date. Keep it small. It has no demo.
   - **Rung 1** is the smallest path that works end to end. It goes thin through every layer. It fits in one week.
   - **Start from what is live.** The first rungs improve a live feature. A new area comes after them.
   - **Order the rungs by visible value for the effort.** Bring a hard unknown forward, but only inside a slice that a person can see. A later rung fits in one or two weeks.
   - **The foundation rides with the rung.** A store change, a refactor or a hardening slice joins the first rung whose demo uses it. No rung ships only plumbing.
   - Write rungs 1 to 3 in detail. Write each later rung in one line. Give each rung its demo in one sentence.
   - Plan the next rungs again after each demo, with what the demo taught.
5. Call `ask_on_task` on the planning task. Put the goal, the ladder and every contract in the question. Give two options: "Approve" and "Change it".
6. Wait for the answer with `get_agent_run` (`wait_seconds` 20). If the person answers in chat, call `record_outside_decision` at once.
7. After the approval, make each epic with `create_task`. Send the `type_id` of the epic type, the `status_id` of the backlog status, and an `idempotency_key`. Put the contract in the description under the five headings above.

**Until the Goal type ships:** write the goal in the description of each epic and of the planning task. Do not put an epic under another task. An epic cannot have a parent.

## Gate 2: how

1. For each epic, write the approach in five lines or less. Name the areas that it changes and the main risks. Then name the risk tier, the lead (a person) and the project that holds the work.
2. Ask for the approval of the approach, as in Gate 1.
3. After the approval, add the approach to each epic as a comment (`add_comment`).

**Until workstreams ship:** one project is one track of work. Its lead is the person who answers for its epics.

## Gate 3: slices

1. Cut each epic into slices. A slice is right when all of these are true:
   - One agent finishes it in one run.
   - A person checks it in 15 to 30 minutes.
   - No other slice changes the same files at the same time.
2. Write a brief for each slice (`brief-contract`): the goal, the files, what not to touch, and the proof of done.
3. Give each slice two sizes: the agent time (`estimate_mins`), and the review time of the person who checks it. Put the review time in the brief.
4. Decide the order. For each slice, list the slices that must be done before it starts.
   Cut the slices of rungs 1 to 3 only. Each later rung stays one line.
5. Draft the order for the question, because the slices do not exist yet:
   - **Wave 1** holds the slices with nothing before them.
   - **Wave 2** holds the slices whose blockers are all in wave 1, and so on.
   - **The critical path** is the longest chain. Count the review wait in each step, not only the build.
   - **The review load** is the sum of the review times for each person.
6. Ask for the approval of the slices. Put the waves, the critical path and the review load for each person in the question. Say that Metorite will check the order after the links exist.
7. After the approval, make each slice with `create_task`. Send `parent_task_id` (its epic), the backlog `status_id`, `estimate_mins`, an `idempotency_key`, and as assignee the person who answers for it. Take the address from `list_members`. Do not guess it.
8. Link the order. For each pair, call `add_task_link` with the blocking slice as `task_id` and the waiting slice as `target_task_id`. No person adds the links.
   Then add the rungs to the board:
   - Make one **demo task** for each rung. Use the title `<area> · Rung N demo · <what a person can do>` and the tag `Demo`. Write the demo script in its description: what to open, what to click and what to see.
   - Give each slice the tag `Rung N`. Give rung 0 work the tag `Rung 0` and no demo task. A slice with no rung tag is Later.
   - Link each slice to the demo task of its rung. The slice blocks the demo task.
   - Link the demo task of rung N to the demo task of rung N+1. The earlier demo blocks the later one.
9. Read the order that Metorite computes. Call `get_plan` with the epic as `root_task_id`. The answer holds these keys:
   - **`ready`** and **`next_slice`**: the slices that can start now, and the one to start.
   - **`waves`**: the slices in layers. It replaces your draft.
   - **`critical_path`** and **`slack`**: the longest chain, and the minutes that each other slice can slip. The review wait is in it.
   - **`finish`**: one date from that chain. It is not a forecast with a range.
   - **`hidden`**: the slices that the person cannot see. Say the count, and do not guess what they are.
   Comment the result on the epic. When it differs from your draft, say so and use the answer of Metorite.

**A `blocks` link:** `task_id` blocks `target_task_id`. `my_work` does not offer the second task until the first is done. Metorite does not refuse a claim of the second task, so an agent that takes a task by its id must read its links first. `get_task` shows each link with its `link_id`, and `remove_task_link` removes one. Metorite refuses a link that makes a loop. Each link matters today: `my_work` skips a task that an open task blocks.

## The first week, until cycles ship

1. **Pick the work.** Take the lowest open rung first. In it, take wave 1, then the next waves. Stop when the review load fills the hours that each reviewer has this week. Agents rarely limit a week. People's review time does.
2. **Mark the week.** Set the due date of each chosen slice to the last day of the week (`update_task`). Move it to the to-do status (`set_task_status`).
3. **Assign the builder.** `my_work` lists the tasks that are assigned to the member of the connection. It skips a task that another session holds, and a task that an open task blocks. Assign each slice to the person whose agent builds it. Take the address from `list_members`.
4. **Build.** Each agent follows the session loop of `kit/team-setup`: claim, brief, build, verify, review, merge.
5. **Close the week.** Comment on each epic: what merged, what is left, and what got stuck. For each unfinished slice, the person chooses next week or the backlog. Change the due date to match.
6. **Look back.** Name the cause of each stall from the `failures` page. Propose one rule change. The person approves it before you apply it.

## What changes when the plan model ships

| Today | Coming |
|---|---|
| The goal is written in each epic | A Goal type, with its epics under it |
| The contract is headings in the description | Contract fields on the epic |
| `get_task` of an epic gives `rollup`: slices and estimate done and total over every level, the waits, and `hidden` | The same roll-up with spend against the appetite, a forecast, health and hill position |
| An agent sets `blocks` with `add_task_link` and reads the ready set, the waves, the critical path and one finish date with `get_plan` | A forecast with a range, from the history of finished tasks |
| Due dates mark the week | A cycle, with its capacity in review hours and credits |
| A person assigns each slice | `my_work` gives the ready slices of the cycle in critical-path order |

When a tool ships, use it. Do not keep your own copy of the order.

## Checklist

- [ ] Each gate has an approval on the planning task.
- [ ] Each epic has an outcome, acceptance checks, out of scope, a risk tier and an appetite.
- [ ] Each slice has a brief, two sizes, an assignee and its `blocks` links.
- [ ] Each rung has a demo task with the tag `Demo`, and each slice has a `Rung N` tag.
- [ ] The question of Gate 3 showed the waves, the critical path and the review load.
- [ ] Nothing in the plan lives only in a file.
