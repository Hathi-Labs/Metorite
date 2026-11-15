# WFC-4a-2b

Tier: STD

Gate: AGENT-SAFE. After WFC-4a-2a

## What

**Update a record from a workflow (task 3310).** Rung 3. The form adds a record picker for an update action. The picker reads the records of the package, so the member types no id. It follows WFC-4a-2a and keeps its rules: the owner only, a literal `record_id`, and no `{{`

## Done when

The step holds `record_id`, `package_id`, `action` and `data`, and the pair passes `validate_graph`. The record id comes from a picker
