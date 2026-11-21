# AF-3a

Tier: STD

Gate: AGENT-SAFE. After AF-2a, and before AF-8, because Mira learns `count` in AF-8. **Status: Built dark 2026-11-14 (STU-AF3a, task 3252).** No UI and no flag

## What

**The `count` of a rail row, in the manifest** (task 3252, the same task as AF-3b until the board splits it). Rung 4. `NavRow` gains one optional field, `count: bool | None = None`. TypeScript has `count?: boolean`. The filter and the sort of the view stay on the view of the page (`source.args`). **Caps:** `MAX_NAV_COUNTS = 8` in `studio_manifest.py` and in `lib/studio/manifest.ts`. `_check_nav` refuses 9 or more `count: true` rows across all sections, with one error at `$.nav` ("A rail shows at most 8 counts."). **The page rule:** `_check_nav` refuses `count: true` on a row whose page holds no view with the block `table` or `board`. It looks in the cells, the tabs and the side of the page. The error is at `$.nav[i].rows[j].count`. A `count` of `false` or none is never checked. `count` has no default, so each stored manifest still validates, and the contract version does not change. The schema file and its golden copy change. **Non-goals:** no count is computed here, no route, no migration and no flag. The `me` token is not handled here. A row has no `view`, `filter` or `sort` key. AF-10b adds the saved-view rows later, and it extends the row shape with a row id

## Done when

`test_studio_manifest.py` holds the count cases: a table or board page is ok, a page with other views is refused, 8 counts across two sections are ok and 9 are refused, `count: false` is never checked, a manifest with no count validates, a `view`, `filter` or `sort` key is refused, and each first-party template validates. `test_studio_manifest_ts_parity.py` holds `MAX_NAV_COUNTS` and the `count` key equal to the twin. `realDoor.test.ts` holds `count` in `getPackage`. `test_studio_package_routes_r8.py` publishes a counted row and refuses 9 counts in a draft. Verify: `uv run pytest tests/unit/test_studio_manifest.py tests/unit/test_studio_manifest_ts_parity.py tests/unit/test_studio_contract.py`, `cd workbench/control_plane && npx vitest run src/lib/studio`, `npx tsc --noEmit`. **Deploy order:** the server with this change must be live before a client or the IDE writes `count`, because an older server refuses an unknown key (the rule of `nav`).
