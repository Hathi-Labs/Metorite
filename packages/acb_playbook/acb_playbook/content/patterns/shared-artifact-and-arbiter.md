---
slug: patterns/shared-artifact-and-arbiter
version: 1
title: Pattern: a shared artifact and an arbiter
summary: Agents that work as peers need one shared record, phase gates and a person who decides.
audience: both
---

# A shared artifact and an arbiter

**Use it when** two or more agents work on related tasks.

## The pattern

- Use the **task** as the shared artifact. Agents write claims, notes and evidence on the task. They do not send free messages to each other.
- Use **phase gates**. Work moves from one stage to the next only when the evidence for the stage exists.
- Name an **arbiter**. The arbiter is a person, or a rule that a person owns. When two agents disagree, the arbiter decides.
- A request from one agent to another goes through the task and has an owner and a reply.

## Why

A free chat between agents leaves no record. A person cannot see it, check it or stop it.

## Check yourself

- Can a person read the whole history of a task and see who did what?
- Who decides when two agents disagree?
