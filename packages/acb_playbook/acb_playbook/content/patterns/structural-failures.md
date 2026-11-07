---
slug: patterns/structural-failures
version: 1
title: Pattern: failures are mostly structural
summary: Name the cause of a stall from a fixed list and fix the structure.
audience: both
---

# Failures are mostly structural

**Use it when** an agent run stalls, loops or ends with no result.

## The pattern

- Most failures come from the structure around the agent. A missing owner, a missing check, an unclear brief or a full queue cause more stalls than a weak prompt.
- Name the cause from a fixed list (see `failures`). Do not write a new reason each time.
- Count the causes each week. Fix the cause that occurs most.
- Change the structure first: add a gate, add an owner, narrow the brief. Change the prompt last.

## Check yourself

- Which cause did your last five stalled runs share?
- What one structural change would remove it?
