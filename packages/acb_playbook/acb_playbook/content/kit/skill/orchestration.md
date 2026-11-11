---
slug: kit/skill/orchestration
version: 1
title: Kit skill: orchestration
summary: Lanes, worktrees, sub-agents under one run, hand-off notes and queue order.
audience: both
---

# Orchestration

## Lanes

A lane is one session with one area of the product. A lane holds one claim at a time.
Keep the number of lanes within what your machine and your quota can carry.

## One worktree for each claim

Make a worktree for each claim. Put the task short id in the branch name.
Install the dependencies in each worktree. Do not link them from another one.
Remove the worktree after the merge.

## Sub-agents under one parent run

The coordinator holds the run. Each sub-agent works under it and makes no run.
The coordinator posts for the sub-agents. It posts at least once a minute while any of them works.

## Queue order

Take work in this order.

1. Runs that are already in flight.
2. Slices on the critical path, by least slack first.
3. Priority, from high to low.

## Hand-off notes

Call `release_task` with a note, or add a comment before you stop. The note has four parts.

- What is done.
- What is left.
- The next step.
- The evidence: the command and its real result.

## Waits

Park the run at a wait. State what you wait on and for how long. Never sleep in a loop.
Give every wait a deadline.

## Fleet review

Call the prompt `review_my_fleet` to find runs that stalled. After finished work, call `run_retro`.
