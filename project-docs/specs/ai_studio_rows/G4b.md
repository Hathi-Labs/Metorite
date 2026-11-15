# G4b

Tier: STD

Gate: AGENT-SAFE

## What

🔨 **BUILT, dark (2026-11-12, task 1189).** The client half of G4. The Preview tab keeps writes in an in-memory preview store of the client (Option A). `makeDraftDoor` in `sampleRows.ts` creates, updates and deletes in that store. It checks each record with `validateRecord`, like `sampleDoor.ts`. It makes no network call and never reaches the live package. A reload drops the store. The Table and the Board of the tab are now editable. A draft object has no server preview, so the server `preview=1` path serves live objects only (F4 dry-run, G10).

## Done when

A create, an update or a delete in the Preview tab changes the rows in the frame, and a Board move and a Table edit show. No `fetch` runs. A reload drops the written rows, and no browser storage holds them. An invalid record gets the validator's sentence. One door instance never sees the store of another. Fences: `src/lib/studio/sampleRows.test.ts` and `src/app/studio/build/page.test.ts` (no `fetch` and no POST in the IDE). Verify: `cd workbench/control_plane && npx vitest run src/lib/studio src/app/studio`
