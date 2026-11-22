---
slug: patterns/sub-agent-contract
version: 1
title: Pattern: the sub-agent contract
summary: Give a sub-agent a brief, ask for a summary, never a transcript.
audience: both
---

# The sub-agent contract

**Use it when** one agent hands work to another.

## The pattern

A sub-agent needs three things.

1. **Its own instructions.** What it is, and what it must not do.
2. **A structured brief.** The objective, the output, the tools and the limits (see `brief-contract`).
3. **A summary back.** The sub-agent returns what it did, what is left and the evidence. It does not return its full transcript.

The parent reads the summary and checks the evidence. The parent does not trust a claim that has no evidence.

## Why

A transcript is long and hides the result. A summary with evidence is short and can be checked.

## Check yourself

- Does the brief fit on one screen?
- Does the summary name the evidence for each claim?
