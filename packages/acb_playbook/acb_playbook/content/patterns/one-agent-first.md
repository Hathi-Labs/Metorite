---
slug: patterns/one-agent-first
version: 1
title: Pattern: one agent first
summary: Start with one agent and add another only for a named bottleneck.
audience: both
---

# One agent first

**Use it when** you start, or when a team asks for "more agents".

## The pattern

- One agent is the default. It has less to coordinate and less to go wrong.
- Add a second agent only when you can name the bottleneck. Write the bottleneck in one sentence. If you cannot, do not add the agent.
- Parallel agents fit work that does not share files. Two agents on two independent apps is a good fit. Two agents on one file is not.
- Inside one task, use a pipeline and not a crowd. One agent builds. A second agent verifies. A third reviews. Each stage has one evaluator.

## Do not use it when

The work is one small change. One agent with a clear brief is enough.

## Check yourself

- Can you name the bottleneck of each lane in one sentence?
- Does each lane own a part of the work that no other lane touches?
