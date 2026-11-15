# AF-11b

Tier: STD

Gate: AGENT-SAFE. AF-11b-2 comes after AF-8b-3b.

## What

**Place blocks on a page by drag** (task 3159). Rung 8. Split in two slices. **AF-11b-1 (task 3326, built dark 2026-11-14):** the pure `lib/studio/dropModel.ts`: `dropIndex` (the `to.index` that `moveCell` reads after the removal), `positionOf`, `dropVerdict`, `canDrop` and `applyDrop` (they call `moveCell` and `addCell` through `layoutAct`). A full list refuses a drop with the sentence of the button. A no-op drop changes nothing. A cross move needs two tabs of one page. **AF-11b-2 (task 3327, not built, after AF-8b-3b, PR 2348):** a drag layer ON the rows of `CellsEditor.tsx` in the Pages tab, with no second component for the same cells. A member drags a cell row to reorder it inside a list, drops it on another tab block, or drags a view chip from a strip of the draft views into a list. The drag is native HTML5, as `FieldStudio.tsx` and `KanbanBoard.tsx` do. The keyboard and phone path is the buttons of AF-8b-3b (Move up, Move down, Move to tab, Add cell, the span select), because HTML5 drag does not fire on touch. A drop is `act((m) => applyDrop(...))`. So it is one `editor.edit`, one Save sends one `PUT /draft` with `based_on`, Revert drops it, and G5 Undo reverts the saved change. A refusal shows the sentence of the button. A `role="status"` line says each move. The drop highlight is `ring-2 ring-primary/40`. **Non-goals:** a new view, page or tab (AF-8), `cols` or `side`, a row-and-column grid, a tile preview (a later AF-11c, read-only), a drag between pages, touch drag, a palette outside the Pages tab, a new flag. Verify AF-11b-1: `cd workbench/control_plane && npx vitest run src/lib/studio/dropModel.test.ts src/lib/studio/pageLayout.test.ts`, `npx tsc --noEmit`, `uv run pytest tests/unit/test_studio_drop_parity.py tests/unit/test_studio_page_layout_parity.py`

## Done when

AF-11b-1: `dropModel.test.ts` holds the `dropIndex` table, all 16 combinations of a 4-cell list against an independent oracle, a full list refused, and a 200-drop run valid for the server validator. AF-11b-2: a drop writes one draft change, every drag act has a button, visual review in four contexts and at 390 px
