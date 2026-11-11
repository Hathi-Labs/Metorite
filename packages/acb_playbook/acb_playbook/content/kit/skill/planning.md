---
slug: kit/skill/planning
version: 2
title: Kit skill: planning
summary: How to read a plan with get_plan and pick the next slice.
audience: both
---

# Planning

## Read the plan

1. Call `get_plan` with the id of the goal or epic.
2. Read the ready set. These slices have no open blocker.
3. Read the critical path. It is the chain that sets the finish date.
4. Read the slack of each slice. A slice with no slack delays the finish if it slips.

## Pick a slice

- Take a slice from the ready set only.
- Take a ready slice of the lowest open rung first. The tag `Rung N` names the rung. A slice with no rung tag is Later.
- Inside that rung, take the slice with the least slack to the demo task of the rung.
- If slack ties, take the higher priority.
- Rung 0 is safety work with no demo task. It goes before the other rungs.
- If you find a loop, a hidden slice or a gap, report it on the task. Do not repair the plan alone.

## Split a goal

Call the MCP prompt `plan_slices`. A good slice has one owner, one app area, one set of checks and a size that fits one session.
Each slice states what it builds, what it does not build, the checks and the risk.

## Ask when the scope is open

If a brief does not settle a choice, call `ask_on_task`. State the choice and your advice. Do not guess.
