---
slug: kit/skill/dispatch
version: 1
title: Kit skill: dispatch
summary: How to write a brief and give a slice to a sub-agent.
audience: both
---

# Dispatch

## The brief

A brief holds six parts. Use the prompt `write_brief` for the live form.

1. The scope: what to build and what not to build.
2. The acceptance: each criterion as a check that passes or fails.
3. The anchors: files and symbols that you checked.
4. The checks: the exact commands, and the result that means pass.
5. The rules: the limits of the work and the files that stay closed.
6. The report: the form of the answer.

## Start a sub-agent

1. Give it one slice, one worktree and one brief.
2. Tell it that it never posts to Metorite.
3. Set a time limit for each wait and each test command.
4. Read its result. Check the files it changed against the brief.
5. Post the activity, the comment and the status change yourself.

## Choose the model

Use a small model for a lookup and for work with clear checks. Use a strong model for a risky slice or a second try after a failure.

## Stop a sub-agent

Stop each sub-agent that you no longer need. Read the diff of its worktree before you build on it. A stopped agent can leave a half change.
