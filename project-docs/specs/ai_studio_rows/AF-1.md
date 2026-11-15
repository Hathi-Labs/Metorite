# AF-1

Tier: STD

Gate: AGENT-SAFE. Behind `NEXT_PUBLIC_STUDIO_APP_FRAME`, default off (D219.6). After D4c (task 2955). **Status: Built dark 2026-11-14 (STU-AF1), behind `NEXT_PUBLIC_STUDIO_APP_FRAME`, default off.** The flag is named in `package-preview/page.tsx` and in `publicFlags.test.ts`

## What

**The frame on the live app page** (task 3149). Rung 4. The app page (`/apps/<id>`, which opens `/studio/package-preview?pkg=<id>` today) draws the manifest pages through `StudioPage`, on live data. A mapper turns each view into its block for the blocks that are built: table, form, detail, board, chart and stat. The mapper is `lib/studio/viewBlocks.ts`. It reuses `tableBlock` and `objectBoardBlock` (`blocks.ts`, `packagePreview.ts`), and `chartViewBlock` and `statViewBlock` of D4c (`aggregateModel.ts`, task 2955). `packagePreview.ts` has no chart or stat builder. §5.5 rule 3 gives the default pages. Before this slice `defaultPage()` made one page for all views, and now `defaultPages()` makes one page for each object and a `dashboard` page. The page id lives in the URL as `?page=<id>`, and a page change clears `tab` and `sel`. An unknown page id shows the first page. A form with an update submit shows the not-built card until a later slice. **Non-goals:** the rail (AF-2), a block that is not built (it shows the "not built yet" card of D11b)

## Done when

A template app opens each of its pages from the URL. A manifest with no `pages` shows one page for each object, and a dashboard page when it has a chart or a stat view. A viewer sees the read-only page of J2a. `pageModel.test.ts` (default pages), `viewBlocks.test.ts` (chart, stat, the undeclared action), `pageState.test.ts` and `usePageState.test.ts` (unknown page id, the reset on a page change, Back), `package-preview/page.test.ts` (the narrowed fence and the viewer) and `realDoor.test.ts` hold the new cases. The fence of `package-preview/page.test.ts` keeps its ban on `pageModel`, `buildPage`, `renderView` and `manifest.views` for `page.tsx`. Only `package-preview/AppFrame.tsx` holds them, and `page.tsx` hands the app to it under the flag. The `nav`, `seams` and conformance tests pass with no edit. Visual review in four contexts. Owner preview (D145). Verify: `cd workbench/control_plane && npx vitest run src/lib/studio src/components/studio src/app/studio src/lib/nav.test.ts src/lib/shell/seams.test.ts src/lib/studio/viewerSurfaces.test.ts src/lib/gatewayPath.test.ts src/lib/theme`, `npx tsc --noEmit`, and `uv run pytest tests/unit/test_studio_manifest.py tests/unit/test_studio_manifest_ts_parity.py`
