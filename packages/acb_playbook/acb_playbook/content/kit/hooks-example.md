---
slug: kit/hooks-example
version: 1
title: Kit: hook examples
summary: Small hooks that keep a session on its task.
audience: agent
---

# Kit: hook examples

A hook is a small script that your coding tool runs at a set moment. These examples keep a session on its task. Write each hook so that it never blocks forever and never reads a secret.

## Session start

Print the steps of your routine and the tasks that this member holds. If the service does not answer, print nothing and let the session start.

## Before a pull request is created

Check that the description has a task link, a task id, or a line that says "No task: reason". Warn when it has none.

## Before a push

Warn when the branch name has no task id.

## When a turn ends

Block the end of the turn once when the session holds a task and has no result. Name the four ways to end: review or done, a question, a park on the checks, or a release. A second end passes, so the hook never traps a session.

## Rules for every hook

- Read only files and the tool result. Never read a sign-in token.
- Use a short time limit. Pass when the hook cannot decide.
- Keep a test for each hook.
