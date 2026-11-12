---
slug: kit/skill/review-and-verify
version: 1
title: Kit skill: review and verify
summary: The verifier and the reviewer: who they are and what each one checks.
audience: both
---

# Review and verify

## The verifier

- The verifier is never the implementer of the slice.
- It reads the brief, then re-derives each fact from the code. It does not trust the write-up.
- It runs the checks of the brief and quotes the real output.
- A check that fails for a cause the change does not explain stops the work. Report it.

## The reviewer

- The reviewer hunts for the case where the change is wrong. A question such as "is it correct?" gets a yes.
- It checks the edges: empty input, a second user, a retry and a failure halfway.
- It reports each finding with the file, the line and the reason.
- It does not fix the change. The implementer fixes it, and the reviewer looks again.

## The report

Both roles end with one report. It says what passed, what failed and what they could not check.
The coordinator posts the report to the task. A sub-agent never posts it.
