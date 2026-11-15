# WFC-4a-2b-2

Tier: HIGH

Gate: AGENT-SAFE. After WFC-4a-2b-1. Opus builder, R8

## What

**The approval card names the record (task 3339).** Rung 3. The owner approval card of an update names the record by its title, from a read as the owner at the park, and not only by its id. A run whose record is gone ends with the code `record_gone` and plain words. `plan_workflow_write` refuses a step whose verb and record id disagree: an update with no record id, and a create that holds one. ⚠️ Until this row merges, do not list an org on all four flags of the unattended path (WFC-4a-2s) while a workflow of that org holds an update step

## Done when

The card shows the title of the record. A gone record ends the run with `record_gone`. A create with a record id and an update with none refuse at the park. R8 tests on a real database cover each case, with two orgs
