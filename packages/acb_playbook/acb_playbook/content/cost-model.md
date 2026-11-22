---
slug: cost-model
version: 1
title: The cost model
summary: How to count what orchestration costs, without an invented number.
audience: both
---

# The cost model

Do not trust a figure that you did not measure. Count your own cost.

## What to count

- **Run time.** The minutes that each run is active. You can always measure this.
- **Waiting time.** The minutes that a run holds a task and does nothing. This is cost without work.
- **Repeat work.** The number of times agents read the same context.
- **Rework.** The number of times a change returns for repair.
- **Model use.** The tokens or credits, where your tools report them. If a tool does not report them, write "unknown". Do not write zero.

## A simple view

Cost per finished slice = (run time + waiting time + rework time) of all agents that touched the slice. Compare it with one agent working alone.

## Rules

- Set a budget before you scale (see `patterns/budgets-and-loop-guards`).
- Review the numbers each week. Remove an agent that adds cost and no speed.

## Check yourself

- What did your last finished slice cost in run time?
- How much of that time was waiting?
