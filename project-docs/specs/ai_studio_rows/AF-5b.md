# AF-5b

Tier: STD

Gate: AGENT-SAFE. After AF-5. No migration and no flag. **Status: BUILT 2026-11-14 (STU-AF5b, task 3254).** Fences: `test_studio_manifest.py` (record cases), `test_studio_manifest_ts_parity.py`, `recordCaps.test.ts`. **Not built:** the frame still reads its objects from the object rows of the API, and those rows do not carry `record`. A later slice must pass the manifest `record` to `recordTabs`. AF-8 owns `studio_describe` and the skeleton, so they do not write `record` yet.

## What

**The `record` key of the manifest** (task 3254). Rung 4. An object gets an optional `record: { tabs: [...] }`. The tab ids are `details`, `lines`, `related` and `files`. The list holds 1 to 6 ids (`MAX_RECORD_TABS`), and a duplicate is refused. The key takes no colour or style key. It joins `studio_manifest.py` (`RecordDef`), the schema file, its golden copy and `lib/studio/manifest.ts`, the way `nav` did (AF-2a). Every stored manifest still validates, because the key is optional

## Done when

A manifest with `record.tabs` validates. An empty list, 7 tabs, a duplicate and an unknown id are refused with a plain sentence. The TS cap equals the `maxItems` of the schema. The parity tests pass
