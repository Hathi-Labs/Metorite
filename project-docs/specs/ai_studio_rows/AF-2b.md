# AF-2b

Tier: STD

Gate: AGENT-SAFE. After AF-2a. Behind `NEXT_PUBLIC_STUDIO_APP_FRAME`. **Status: Built dark 2026-11-14 (STU-AF2b, task 3186).** No new flag

## What

**The rail of the app** (task 3186). Rung 4. `lib/studio/railModel.ts` builds the rail from `nav`, or from §5.5 rule 3 when there is none. `components/studio/StudioAppFrame.tsx` draws it as `RailRow`s under `AppTopBar`, with the toggle in the bar. A phone opens the rail in the drawer of `useMobileDrawer`. **Built decisions:** (1) an app with one page draws no rail and no toggle (`hasRail`). (2) The AF-1 page picker goes when the rail draws, and a one-page app has nothing to pick. (3) `StudioPage` takes `bare` (default false), so it draws no app bar inside the frame and the screen keeps one `<h1>`. `/studio/page-preview` is unchanged. The owner verbs ("Open in Studio IDE", "Delete app") moved into the `actions` of the app bar on a desktop. On a phone the owner verbs and the READ_ONLY sentence sit in the page toolbar, so the title stays readable. The full-page record view of AF-5 draws no toolbar, so on a phone it shows none of them until "Back". **Files:** `railModel.ts`, `StudioAppFrame.tsx`, `StudioPage.tsx` (`bare`), `package-preview/AppFrame.tsx`, `railRows.test.ts` (`RAILS`), `e2e/studio-app-rail.spec.ts`

## Done when

`railModel.test.ts` (default, declared, unknown page, empty section, caps, one page). `StudioAppFrame.test.ts` (RailRow, no `<button`, no colour, no HTML, no page model, `bare`). `page.test.ts` pins the frame free of the page model. `railRows.test.ts` lists the new rail. `appBar.test.ts` and `seams.test.ts` pass with no edit. The e2e spec opens a stubbed app at 1440 and 390 px and skips without the flag. Verify: `cd workbench/control_plane && npx vitest run src/lib/studio src/components/studio src/app/studio src/lib/railRows.test.ts src/lib/shell`, `npx tsc --noEmit`, and `NEXT_PUBLIC_STUDIO_APP_FRAME=1 npx playwright test e2e/studio-app-rail.spec.ts`. Visual review beside the CRM
