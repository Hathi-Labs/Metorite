# C8c

Tier: HIGH

Gate: AGENT-SAFE. After C5 and C8b

## What

**Search binds the row rules.** The `Source` of C5 reads records through `query_records` with the viewer of the run. So `search_company` never shows a record or a hidden key that the viewer cannot open

## Done when

R8: an employee finds only their own records, and no snippet holds a hidden key. The fence of C8a lists the new reader path. `test_studio_row_rules_r8.py` and the C5 suite
