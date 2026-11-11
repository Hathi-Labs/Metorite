---
slug: gates
version: 1
title: Gates
summary: What must be true before a task moves to the next stage.
audience: both
---

# Gates

A gate is a condition that must be true before work moves on. A gate needs evidence, not a promise.

## A starting set of stages

1. **Audit.** The task is clear and the brief is complete.
2. **Build.** The change exists and runs.
3. **Verify.** A check that the builder did not write passes.
4. **Review.** A reviewer who looks for the case where the work is wrong approves.
5. **Preview.** A person sees the change and approves it, when the change is visible.
6. **Checks.** The automatic checks pass on the final version.
7. **Merge.** The change joins the main line.
8. **Release.** The change goes to the live service.
9. **Live.** The live service shows the change.

## Rules

- Match the number of stages to the risk. A small, low-risk change needs fewer stages than a change to security or money.
- The builder never verifies its own work. A claim by the builder is a claim, not evidence.
- A person sets the risk level. An agent does not set it for itself.
- Show the state of each stage on the task, with a link to the evidence.

## Check yourself

- Which stages does your highest-risk work need?
- Who sets the risk level of a task?
