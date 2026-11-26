# C2d-a

Tier: HIGH

Gate: AGENT-SAFE. After C2b, C8a and J2a. **Status: Built dark 2026-11-14 (STU-C2d-a, task 3311).** No flag

## What

🔨 **BUILT, dark (2026-11-14, task 3311).** **The aggregate of a viewer.** No migration and no flag. `aggregate_records` takes a REQUIRED keyword `viewer`. It reads what `query_records` reads for that viewer. The scope comes from `_view`. The row rule joins the statement as one `EXISTS` in each part (`g`, `m` and the ids), through `access` in `build_aggregate`. A filter, a group or a measure on a hidden key is refused (`_refuse_hidden_aggregate`). A viewer whose roles read no record gets a total of 0 and no group. With no `access`, the statement is the one of the owner, byte for byte. The door lets a viewer of an install (J2a) aggregate. It runs `parse_aggregate` on the `project()` fields first, so a field of a newer version is refused. **Known gap:** the J2a formula gap (the values of the newest version) applies to a sum too. **Non-goals:** a second group axis for the matrix (C2d-b). It needs its own row for the cell cap, the fold rule for two keys and the answer shape

## Done when

A viewer who owns only its own rows counts, sums and averages only those rows. The cold path (`g` and `m`) leaks no row. A hidden key is refused in the filter, the group and each measure. A viewer with no read gets a total of 0. The owner reads all. Org B and a non-member get 404. A field of a newer version does not aggregate. Verify: `uv run pytest tests/unit/test_studio_row_rules.py tests/unit/test_studio_row_rules_r8.py tests/unit/test_studio_query_r8.py tests/unit/test_studio_action_door.py tests/unit/test_studio_action_door_r8.py tests/unit/test_studio_hot_num_f8.py`
