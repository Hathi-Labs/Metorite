---
slug: kit/skill/SKILL
version: 1
title: Kit skill: index
summary: The short index of the kit skill. It tells an agent which file to load for which need.
audience: both
---

# The kit skill

Load a file only when you need it. Each file works as a skill file and as a plain instruction file.
The rules and the protocol are in `kit/agents-md`. These files hold the detail.

| You need | Load |
|---|---|
| To read a plan and pick a slice | `kit/skill/planning` |
| To write a brief and start a sub-agent | `kit/skill/dispatch` |
| To run lanes, worktrees and hand-offs | `kit/skill/orchestration` |
| To change many tasks at once | `kit/skill/bulk-writes` |
| To verify or review a change | `kit/skill/review-and-verify` |

For live text, call `read_playbook`. The kit does not copy it.
