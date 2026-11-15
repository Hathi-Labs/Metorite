# AF-4

Tier: STD

Gate: AGENT-SAFE. After AF-1. **Status: BUILT, dark 2026-11-14 (STU-AF4), behind `NEXT_PUBLIC_STUDIO_APP_FRAME`. The gallery and calendar layouts wait on D19 and D5**

## What

**The filter bar and the layout switch** (task 3152). Rung 4. Over a table or a board: search, filter chips by field type, sort and group. A switch moves the same rows between the layouts that a view allows: table and board now, gallery and calendar when D19 and D5 are built. AF-4 lifts the pure model of the CRM filter bar (`app/crm/lib/filterBar.ts`) to a shared module, and the CRM imports it from there. It adds no third filter bar. As for D5 and D6, the lift changes no behaviour of the CRM. Lift built (task 3152, PR 1 of AF-4): the shared module is `src/lib/filterModel.ts`. Moved: `ANY`, `SEARCH_DELAY_MS`, the search box state machine, `searchCommitter`, `setCustom` and `setRange`. Left in the CRM: the owner, source and date options, the field options and custom filters, `activeCount` and `pruneFilters`. Each one needs a CRM type or a CRM key list. **Bar built (task 3152, PR 2, dark):** `components/studio/StudioFilterBar.tsx` and `lib/studio/filterBarModel.ts`. The app frame mounts the bar behind its flag, over the first table or board of a page. The search box uses `filterModel.ts` and filters the title field. A hot field gets one chip by type, and any other field gets none. A sort takes a hot field, and a group picks the lane field of a board. The URL holds each choice, and the door gets `filter` and `sort` of `list`. Fences: `filterBarModel.test.ts` and `StudioFilterBar.test.ts`

## Done when

The CRM filter tests pass with no change, and a visual review of the CRM list shows no change. A filter, a sort and a layout survive a reload through the URL. A field type with no filter shows no chip. Visual review in four contexts
