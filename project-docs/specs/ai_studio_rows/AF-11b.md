# AF-11b

Tier: STD

Gate: AGENT-SAFE. After AF-8b-3b. Needs AF-11a

## What

**Place blocks on a page by drag** (task 3159). Rung 8. The canvas `app/studio/build/components/PagesCanvas.tsx` in the Pages branch of the Explorer. The Pages tab exists on main (AF-8b-2). A member drags a cell inside a list or to another tab, and sets its span. The drag is native HTML5, as `FieldStudio.tsx` and `KanbanBoard.tsx` do, because `package.json` holds no drag library. Each move is also a button (Move up, Move down, Move left, Move right, Move to tab), and a span `SelectButton` sets the span. A live-region sentence says each move. A drop calls `editor.edit` with a function of `pageLayout.ts`, so one Save sends one `PUT /draft` with `based_on`, and G5 Undo reverts it. Non-goals: a new view, a new page or tab (AF-8), a change of `cols` or `side` (AF-8), a row-column grid, a palette outside the Pages branch, a new flag. Chat parity: no new route, because `PUT /draft` is covered by `studio_save`

## Done when

A drag writes one draft change, with the one-step undo of G5. The keyboard does each move too. Visual review in four contexts
