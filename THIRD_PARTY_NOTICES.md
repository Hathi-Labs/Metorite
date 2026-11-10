# Third-party notices

Code that Metorite took from other projects, and the licence of each.

## Docmost (AGPL-3.0)

- **Source.** https://github.com/docmost/docmost, core only.
- **Licence.** GNU Affero General Public License, version 3.
- **Decision.** `project-docs/work_plan.md` D107, `project-docs/specs/knowledge_base.md` §21.1.
- **Never ported.** Files under `apps/server/src/ee`, `apps/client/src/ee`,
  `packages/ee` and `packages/base-formula`. Docmost licenses those folders
  separately, with its enterprise licence.
- **Marker.** Each ported file carries the line `Adapted from Docmost` in its
  first lines, with the path in the Docmost repo.
- **Fence.** `tests/unit/test_third_party_notices.py` fails when a file with the
  marker is missing from the list below.

### Ported files

Each line is `path in this repo` and `path in Docmost`.

<!-- PORTED-FILES-START -->
- `workbench/control_plane/src/app/knowledge/lib/editor/footnotes.ts` <- `packages/editor-ext/src/lib/footnotes`
- `workbench/control_plane/src/app/knowledge/lib/editor/tables.ts` <- `apps/client/src/features/editor/components/table/handle/hooks/use-table-clear.ts`
- `workbench/control_plane/src/app/knowledge/lib/editor/searchAndReplace.ts` <- `packages/editor-ext/src/lib/search-and-replace/search-and-replace.ts`
- `workbench/control_plane/src/app/knowledge/lib/editor/tableMove.ts` <- `packages/editor-ext/src/lib/table/utils`
- `workbench/control_plane/src/app/knowledge/components/editor/tableDrag.ts` <- `packages/editor-ext/src/lib/table/dnd`
- `workbench/control_plane/src/app/knowledge/components/editor/TableHandles.tsx` <- `apps/client/src/features/editor/components/table/handle`
- `workbench/control_plane/src/app/knowledge/components/editor/FindBar.tsx` <- `apps/client/src/features/editor/components/search-and-replace/search-and-replace-dialog.tsx`
- `workbench/control_plane/src/app/knowledge/lib/editor/status.ts` <- `packages/editor-ext/src/lib/status.ts`
- `workbench/control_plane/src/app/knowledge/lib/editor/pageBreak.ts` <- `packages/editor-ext/src/lib/page-break/page-break.ts`
- `workbench/control_plane/src/app/knowledge/lib/editor/subpages.ts` <- `packages/editor-ext/src/lib/subpages/subpages.ts`
- `workbench/control_plane/src/app/knowledge/lib/editor/columns.ts` <- `packages/editor-ext/src/lib/columns/columns.ts`
- `workbench/control_plane/src/app/knowledge/components/editor/StatusView.tsx` <- `apps/client/src/features/editor/components/status/status-view.tsx`
- `workbench/control_plane/src/app/knowledge/components/editor/SubpagesView.tsx` <- `apps/client/src/features/editor/components/subpages/subpages-view.tsx`
<!-- PORTED-FILES-END -->
