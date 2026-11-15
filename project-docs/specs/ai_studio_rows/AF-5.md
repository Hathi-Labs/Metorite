# AF-5

Tier: STD

Gate: AGENT-SAFE. After AF-1. **Status: BUILT, dark 2026-11-14 (STU-AF5).** The flag is `NEXT_PUBLIC_STUDIO_APP_FRAME`. The model is `lib/studio/recordPageModel.ts`. The body is `components/studio/StudioRecord.tsx`. `StudioPage` shows a record in the side panel, or in a sheet below `lg`. "Open full page" gives one column at each width. The URL keys are `rec` (the id), `ro` (its object), `rtab` (the tab) and `full=1`. `rtab` is not `tab`, because `tab` belongs to the tabs of the page. A table row and a board card link to the record when the app declares `object:<name>.get`. The frame opens that link in the panel. It makes no page load. Details is always a tab. Related joins when another object has a `relation` field that points here, with one table for each field. Files joins when the object has a `file` field. An object `record.tabs` list wins. The manifest schema has no `record` key yet, so no app sets one. A related table reads `object:<child>.list` through the door. The filter is `<field> eq <record id>`. The door holds the row rules, so the viewer sees only the rows that the viewer reads. A field that is not hot filters only on an object of at most 5,000 records (`hot_or_small`). "New" opens the Form block over `object:<child>.create`. `linkedDoor` fills the link. The write goes through `door.run`, so it adds no write path. A viewer sees no "New". An app with no declared create shows it off, with the reason. **Not built:** the Activity tab (D22) and a `record` key in the manifest schema. The Files tab and the Detail say "File attached" or a count of files. No Studio route turns an attachment id into a name or a link. A later slice adds them. AF-6 gives "New" a dialog. Fences: `recordPageModel.test.ts`, `StudioRecord.test.ts`, and the new cases of `pageState.test.ts`

## What

**The record page** (task 3153). Rung 4. A record opens in the side panel. "Open full page" gives it its own URL (`?rec=`). Its tabs come from `record`, or from §5.5 rule 3: Details (the Detail block), Related (a table for each `relation` field that points to the object, with a "New" that fills the link), and Files (the `file` fields). The Activity tab joins when D22 is built. `RecordShell.tsx` and `RelatedLists.tsx` of the CRM are the model, and no CRM file changes

## Done when

A record link opens the same record and tab after a reload. A related table shows only the children that the viewer can read. "New" on a related table makes a child with the link filled. At 390 px the record page is one column. Visual review in four contexts
