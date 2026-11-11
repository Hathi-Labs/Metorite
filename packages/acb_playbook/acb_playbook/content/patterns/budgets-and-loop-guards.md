---
slug: patterns/budgets-and-loop-guards
version: 1
title: Pattern: budgets and loop guards
summary: Set a budget and a loop guard before you add agents.
audience: both
---

# Budgets and loop guards

**Use it when** you plan to run more than one agent, or to run agents without a person watching.

## The pattern

- Orchestration multiplies cost. Several agents read the same context and repeat work. Measure your own cost. Do not copy a figure from an article.
- Set a **budget** for each run, each lane and each day. Use time first, because you can always measure time.
- Set a **loop guard**. Stop a run that repeats the same call many times, or that writes too fast.
- Start the guard in **warn** mode. Move to **stop** mode when the warnings are accurate.
- When a budget stops a run, the run leaves a note that says why.

## Check yourself

- What is the most one run may cost you in a day?
- What happens when an agent repeats one call twenty times?
