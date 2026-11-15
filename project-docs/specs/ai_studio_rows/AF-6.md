# AF-6

Tier: STD

Gate: AGENT-SAFE. After AF-1. **Status: BUILT, dark 2026-11-14 (STU-AF6), behind `NEXT_PUBLIC_STUDIO_APP_FRAME`.** The pure model is `lib/studio/dialogModel.ts`. It reads the declared actions. `object:<o>.create` gives "New <Object>" in the page toolbar, one button for each object that the page lists. `object:<o>.update` gives Edit, and `object:<o>.delete` gives Delete, both on the record page. A viewer gets no button, and an action with a `set` gives none. `components/studio/StudioFormDialog.tsx` holds the Form block in the shared `Modal`, a sheet below `sm`. `StudioNewButton.tsx` opens it. Delete reuses `DeleteRecordButton`, which asks once through `ConfirmDialog`, and the record closes after the delete. The inline "New" under Related is now the same dialog, with the link filled by `linkedDoor`. Each write goes through `door.run`, so no second write path exists. The Form block already holds one send on a double press and shows a 422 under its field. The `Modal` closes on Esc and gives the focus back to the button. **Not built:** an action that is not a plain object create, bulk delete, and an optimistic update. The list refreshes after a save by the cache invalidation of the door. Fences: `dialogModel.test.ts` and `StudioFormDialog.test.ts`

## What

**Dialogs to add, edit and delete** (task 3154). Rung 4. A typed action (D1) that opens a form puts a button in the app bar or on a page. The button opens the Form block in a `Modal`, which is a sheet on a phone. Edit opens the same form from the record page. A delete asks once through `ConfirmDialog`. Each write goes through the door as the viewer (B2)

## Done when

"New visit" makes one record on a double press. A 422 shows under its field. Esc closes the dialog, and the focus goes back to the button. A viewer with no write right sees no button (J2a). Visual review in four contexts
