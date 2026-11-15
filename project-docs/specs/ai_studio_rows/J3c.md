# J3c

Tier: HIGH

Gate: AGENT-SAFE. **The owner decided on 2026-11-11 (task 1201, the recommended defaults).** The engine may take one narrow stop check between nodes. The check reads the stop state, and it changes nothing else. `workflows_app.md` §10 must record that exception in the same pull request. Not built

## What

**The stop of a workflow and of a skill.** An admin stops a workflow or a skill in one step (§9 item 7). The engine rule is in `workflows_app.md` §10

## Done when

A stopped workflow or skill runs no new step within 10 seconds
