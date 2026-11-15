# AF-11a

Tier: STD

Gate: Built dark

## What

**The layout model of a page** (task 3159). Rung 8. **Built dark 2026-11-14 (STU-AF11a, task 3159).** A pure model of the layout edits over the manifest `pages`, with no screen, no route and no flag. `src/lib/studio/pageLayout.ts` holds `moveCell`, `moveCellToTab`, `setCellSpan`, `addCell`, `removeCell`, `clampSpans` and `cellCapRoom`, and the type `Loc`. Each function is pure and never throws. For a bad request it returns the input manifest, the same reference. The caps come from `PAGE_CAPS` in `pageModel.ts`. A span over the page `cols` is refused, and it is never clamped. The server rule for an empty list (`_check_pages`): a page needs `tabs` or `cells`, so an emptied page list is refused. A tab with no cells passes the server, so a move may empty a tab. `pageLayout.test.ts` holds 34 tests, with a deterministic run of 200 edits for three seeds. `tests/unit/test_studio_page_layout_parity.py` checks the fixture of that run with `validate_manifest`. Verify: `npx vitest run src/lib/studio/pageLayout.test.ts` and `uv run pytest tests/unit/test_studio_page_layout_parity.py`. **Owner questions, taken on their defaults and reversible.** (1) A drop marks the draft dirty and the member saves, and G5 undo covers the saved change. (2) The strip of views is the `views` of the draft. (3) The canvas makes no first tab. (4) `side` and `cols` belong to the form of AF-8. (5) `clampSpans` is exported, so AF-8 can call it when it lowers `cols`. (6) Name the demo of rung 8 when AF-11b dispatches

## Done when

The tests and the parity test pass. A mutation that drops the span refusal, one that allows the 13th cell, one that changes the input and one that throws each turn a test red
