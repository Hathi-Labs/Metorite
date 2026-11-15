# AF-10b

Tier: STD

Gate: AGENT-SAFE, with the UI gate. After AF-3, AF-4 and AF-10a

## What

**The front end of a saved view** (task 3188). Rung 6. AF-10b is split. **AF-10b-1 (task 3290, STD, no UI, built dark 2026-11-14):** `lib/studio/savedViewRows.ts` maps the page state of AF-4 to a spec `{filter, sort, layout}` and back (`stateToSpec`, `specToState`), maps a wire view to the seam `SavedView`, and builds the dropped note. A condition with no chip is dropped and counted. `group`, `me` and `columns` are not stored. Done when the round trip holds for each chip kind, a hostile spec gives a valid state, and `lib/savedViews/index.test.ts` passes with no change. **AF-10b-2 (task 3291, STD, UI gate, after AF-3a and AF-3b merge, not built):** the chip strip above the list through `ViewChips` and `useScopedViews`, a door for GET, POST, PATCH and DELETE on `/api/studio/packages/{id}/views`, cache prefix `studio-views:`. A private view shows to its owner, a shared view to every viewer, only the package owner sees Share, with the warning that every viewer reads the name. **AF-10b-3 (task 3292, STD, built dark 2026-11-14, see its row below):** one chat tool in a dark pack. The four COVERED parity rows land with AF-10b-2, in the PR of its first UI call, so `DEBT_CEILING['studio']` stays 3. **Decisions (defaults, open on task 3188):** chips above the list, not rail rows (the CRM moved them out of its rail in CRM-U8). A view is stored per object, not per page. **Non-goals:** a rail row of a view (AF-3 and a later slice), `group`, `me`, `columns`, a delete-preview count. AF-10a (the store and routes) is built.

## Done when

Verify: `cd workbench/control_plane && npx vitest run src/lib/studio src/lib/savedViews`, `npx tsc --noEmit`, `uv run pytest tests/unit/test_studio_saved_view_rows_parity.py`.
