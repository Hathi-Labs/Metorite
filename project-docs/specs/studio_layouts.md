# Studio layouts: standard page layouts by use case

**Status: DRAFT (2026-11-14, task 3312). The owner answered the three questions of §9 on 2026-11-14, and the spec is not yet ACTIVE. LY-0 is BUILT, dark (2026-11-15, task 3313, PR 2354): the model, the validator and the TS twin, with no renderer.** Board: WS-STU, Metorite task 3312, slices 3313 to 3322. Parent spec: `ai_studio.md` (§5.5 is the app frame, D219). Research note: the lane file `studio_layouts.md` of 2026-11-14. This spec holds the result, and the note is not a source of record.

**Bottom line.** Studio ships 12 page presets and 2 patterns (record 360 and form wizard). A page preset is one enum value plus named regions. The author and Mira pick a preset, and the app draws it. A page with no preset keeps today's grid, so stored manifests stay valid. Build six first: table workspace, record 360, board, form wizard, work queue with approval, and dashboard. Record 360 and form wizard build as the record page and a Form mode (§6).

## 1. What exists on main (checked against origin/main, 2026-11-14)

| Part | State | Where |
|---|---|---|
| Page model (`pages`, `tabs`, `cells`, `side`, `cols`) | Built | D11a, `lib/studio/pageModel.ts`, `studio_manifest.py` |
| Page renderer | Built, preview only | D11b, `components/studio/StudioPage.tsx` |
| App page on live data | Built dark | AF-1 |
| `nav` and the rail | Built dark | AF-2a, AF-2b, `StudioAppFrame.tsx` |
| Filter bar and layout switch | Built dark | AF-4 (`StudioFilterBar.tsx`, `lib/filterModel.ts`) |
| Record page (Details, Related, Files) | Built dark | AF-5 (`StudioRecord.tsx`) |
| Dialogs to add, edit, delete | Built dark | AF-6 (`StudioFormDialog.tsx`) |
| Saved views, store only | Built dark | AF-10a |
| Page layout edit model | Built dark | AF-11a (`lib/studio/pageLayout.ts`) |
| Pages tab of the IDE | Built dark | AF-8b-2 |
| Table, Form, Detail, Board | Built | D2, D3, D3b |
| Chart, Stat | Built dark | D4, D4b, D4c |
| Sheet of line items | Built dark | D17a |
| Calendar grids, Timeline core | Built, part | D5, D6 |
| Rail counts and views in the rail | **Not built** | AF-3 |
| `header` and `sections` | **Not built** | AF-7 |
| Checklist, Map, Documents, Gallery, Activity feed, Queue board | **Not built** | D9, D10, D13, D19, D22, D30 |

Each frame slice sits behind `NEXT_PUBLIC_STUDIO_APP_FRAME`. The page keys `layout` and `regions` do not exist yet.

## 2. Rules that bind this spec

1. **D12.** A manifest sets no colour and no pixel size. A layout is a preset of regions and blocks. It is never free styling.
2. **One look.** Tokens only. Status colour comes from `statusAccent.ts`. Light and dark, three densities, member accent. A layout adds no palette and no hand-built control.
3. **§5.5 caps.** 8 pages, 4 rail sections, 12 rows in a section, 8 tabs on a page, 12 cells on a page, 6 tabs on a record, 4 related lists, 8 rail counts.
4. **D205.** A vertical is a preset over core blocks. A layout preset follows the same rule.
5. **R6.** A page with no `layout` validates and draws as before. The new keys are optional.
6. **R7.** Each rule below names its fence. A rule with no fence is advisory.

## 3. Shared rules for every layout

These apply to all 14 layouts. The catalog does not repeat them.

| Topic | Rule |
|---|---|
| States | Loading is a skeleton in the shape of the block. Empty says what the page is and gives one next action. Error says what failed, says whether data is safe, and has Retry. No-permission says what the member lacks and who to ask. A filtered-empty differs from a true-empty. No page is ever blank. |
| Header | One title, one sentence, one count, one primary action at top right. |
| Keyboard | Tab order follows reading order. Each pointer act has a key act. Esc closes a panel or dialog and returns focus to the opener. |
| URL | Page, tab, record, filter and layout live in the URL (§5.5 rule 4). The page preset uses the key `preset`, because `layout` is the AF-4 switch. |
| Phone (390 px) | One column. Touch targets of 44 px. No act that needs hover. No horizontal page scroll. A wide table scrolls inside its own strip with the first column pinned. |
| Accessibility | Status never shows by colour alone. A live region announces a save and a failure. Drag has a menu or key alternative. |

Fence: LY-1 builds one component for the four states. A test refuses a local copy in a preset.

## 4. The catalog

Each entry gives the purpose, example apps, the regions, what is missing today, the layout by width, and a quality bar. The bar numbers are this spec's own proposals. They are not quotes from a source.

### L1. List and detail
- **Purpose:** pick one item and read it beside the list. **Apps:** support desk, contacts, shipment tracker.
- **Slots:** header, `list` (Table in compact row mode, or card list), `detail` (Detail block with tabs).
- **Missing:** a compact row mode in Table. A split pane with a selection state. AF-5 gives a side panel only.
- **Widths:** desktop 35/65 split. Tablet: the list, with detail as an overlay. Phone: the list, then detail full screen with Back.
- **Bar:** the selected row shows by more than colour. The detail title equals the row title. Back restores list scroll and filter. The list shows a count. A widen keeps the selection. No selection shows a placeholder with a hint.

### L2. Table workspace
- **Purpose:** find, compare, edit and bulk-act on many records. **Apps:** inventory, purchase requests, expense claims, candidates.
- **Slots:** header, filter bar with layout switch, Table, optional side panel, bulk bar.
- **Missing:** a bulk bar. Column hide and reorder. (The filter bar exists, AF-4.)
- **Widths:** desktop: full table, sticky header, record in side panel. Tablet: fewer columns, panel overlays. Phone: a card list of the first 3 fields, with filters in a sheet.
- **Bar:** the first column is a name, not an id. Active filters show as removable chips. At most two inline row actions, and the rest in a menu. The bulk bar appears on the first selection and shows the count. Editing opens a non-modal panel. The count shows total and filtered. A filtered-empty offers "Clear filters". A partial bulk failure lists the failed rows.

### L3. Board
- **Purpose:** move records through stages. **Apps:** hiring pipeline, sales, claim stages.
- **Slots:** header, filter bar, Board, record panel.
- **Missing:** shared filters between Table and Board (AF-4 gives the switch). A phone stage switcher.
- **Widths:** desktop: all columns, scroll past 6. Tablet: 3 columns. Phone: one column at a time with a stage switcher. Drag becomes a "Move to" menu.
- **Bar:** each column shows its name and count. Stage colour comes from `statusAccent`. A card shows at most 4 fields. Drag has a key path. A refused move snaps back and says why. An empty column shows a drop hint.

### L4. Dashboard
- **Purpose:** know the state of the work at a glance and jump into it. **Apps:** field-visit overview, clinic day.
- **Slots:** header with period control, `kpis` (Stat), `charts` (Chart), one `worklist` (short Table).
- **Missing:** a period control. A chart-to-filter link. Caps on tiles.
- **Widths:** desktop: 4 stats, 2 to 4 charts. Tablet: 2 columns. Phone: stats in 2 columns, charts stack by importance.
- **Bar:** the most important tile sits top-left. At most 6 stats and 4 charts. No pie, donut or gauge for an exact comparison. A tile click opens the filtered list. Each chart has a title with its unit and a text alternative. One colour per series across the page. Each tile has its own loading and error state.

### L5. Calendar and schedule
- **Purpose:** see and book things by time. **Apps:** field visits, appointments, shift roster.
- **Slots:** header with date navigation and Month, Week, Day and Agenda switch, Calendar, event panel, an "Unscheduled" list.
- **Missing:** the Calendar block on live records (D5 is part built). A conflict check on write.
- **Widths:** desktop: week grid. Tablet: day or week. Phone: agenda list by default, month as dots.
- **Bar:** Today is one click. An event shows time and title. Status shows by label and colour. The current period is in the title. Drag-to-reschedule has a key path. A conflict names the clash.

### L6. Record 360
- **Purpose:** everything about one thing, with its related records. **Apps:** customer, patient, vendor, asset.
- **Slots:** header (title, status, key facts, one primary action), tabs (Details, Related, Files, Activity), optional side panel.
- **Missing:** the `record` manifest key (task 3254, AF5b). The Activity tab (D22).
- **Widths:** desktop: header, tab body, 30% side column. Tablet: the side column sits under the tabs. Phone: one column, scrolling tabs, key facts first.
- **Bar:** the header shows the 3 to 5 facts a member needs first. One primary action. Related lists sit on one tab, at most 4. A tab shows a count. An edit never loses a draft. A permalink works. A missing record says "Not found or no access" and leaks nothing.

### L7. Work queue
- **Purpose:** work items one by one until the queue is empty. **Apps:** support desk, inbound requests, safety reports.
- **Slots:** header with queue counts, list, the open item, action bar.
- **Missing:** sort by age or priority. A "next item" act. Assign and snooze acts. (D30 covers token queues only.)
- **Widths:** desktop: list and item. Tablet: list overlay. Phone: item full screen with Next and Previous.
- **Bar:** the oldest or highest priority item comes first by default. Each item shows its age. After an act, focus moves to the next item. Undo lasts 5 seconds after a close. Counts show open and mine. An empty queue says "All clear" with the time of the last item. That is the success state.

### L8. Approval queue
- **Purpose:** decide yes or no on requests, with the facts to decide. **Apps:** purchase requests, leave, expense claims.
- **Slots:** list of pending, request detail (facts, lines, files), decision bar (Approve, Reject, Ask).
- **Missing:** a decision bar bound to a typed action with a reason field. A link from "Needs you".
- **Widths:** like L7. Phone: a card, then detail with a sticky decision bar.
- **Bar:** amount, requester and date are in the first view. Approve and Reject differ in label and place, not only colour. Reject needs a reason. No single-key Approve or Reject. The result shows who decided and when. A member cannot approve their own request. A request that changed since opening asks for a reload before a decision. Empty says "Nothing waits on you".

### L9. Form and step wizard
- **Purpose:** create a record, short (one page) or long (steps). **Apps:** new visit, expense claim, candidate intake.
- **Slots:** header, Form (one section or steps), progress, footer actions.
- **Missing:** a step model in Form. Draft save. A review step.
- **Widths:** desktop: a centred column with a max width. Phone: one step per screen with a sticky footer.
- **Bar:** use steps only for four or more groups. Progress reads "Step 2 of 5". Back never loses input. Required fields are marked in text. An error shows beside the field, with a summary at top, linked by `aria-describedby`. A final review step lists the answers with Edit links. The success screen shows the next action and a link to the record.

### L10. Gallery and catalog
- **Purpose:** browse by picture. **Apps:** inventory with photos, site photos, products.
- **Slots:** header, filter bar, Gallery grid, record panel.
- **Missing:** the Gallery block (D19).
- **Widths:** desktop 4 to 6 columns. Tablet 3. Phone 2.
- **Bar:** a fixed aspect ratio. Thumbnails load when they come into view. A card shows a title and at most 2 fields. Alt text comes from the title. A failed image shows a labelled placeholder. Empty offers Upload.

### L11. Timeline
- **Purpose:** see work over time with dates. **Apps:** installations, projects, maintenance.
- **Slots:** header with zoom, row list, time grid.
- **Missing:** live records and drag (D6 is part built). Dependency lines.
- **Widths:** desktop: list and grid. Tablet: a narrower list. Phone: a read-only dated list, with no grid.
- **Bar:** a Today line is always visible. Three zoom levels. A bar shows label and status. Overdue shows by icon and text. A record with no dates sits in "Unscheduled". The phone does not fake a Gantt chart.

### L12. Map and list
- **Purpose:** see where things are and pick one. **Apps:** field visits, shipments, sites.
- **Slots:** list, Map, record panel.
- **Missing:** the Map block (D10, a paid tile key is an owner gate, STU-Q8). A `geo` field. List and map sync.
- **Widths:** desktop 40/60. Tablet: the map with a list sheet. Phone: a List and Map toggle, never both.
- **Bar:** list and map select together. The map shows only filtered records. Pins cluster at scale. A count of unmapped records shows. The list works with no map, and it is the keyboard path.

### L13. Checklist and runbook
- **Purpose:** do steps in order and sign off. **Apps:** inspection, onboarding, SOP.
- **Slots:** header with progress, Checklist or step player, side panel for notes and files.
- **Missing:** Checklist (D9), Step player (D14), Capture (D24).
- **Widths:** desktop: the list with the current step expanded. Phone: one step per screen with large targets.
- **Bar:** progress reads "6 of 10". The current step stands out by more than colour. A step can hold a photo or a note. Leaving keeps progress. A blocked step names why. Sign-off names who and when. A required step cannot be skipped.

### L14. Document and settings
- **Purpose:** read a page of content, or set options. **Apps:** SOP page, policy, app settings.
- **Slots:** header, outline (optional), body (Document or sectioned Form), save bar.
- **Missing:** the Documents block (D13). A sticky save bar for Form sections.
- **Widths:** desktop: outline and body. Phone: the outline becomes a menu.
- **Bar:** each option has a one-line help. The save bar shows unsaved changes. A destructive option sits last and asks once. A change says when it takes effect. A save error keeps the edits. Body line length stays readable.

## 5. Build order

1. **L2 Table workspace.** Almost every app starts with a list. Table and the filter bar exist.
2. **L6 Record 360.** Every object needs a record page. AF-5 and D2 cover most of it.
3. **L3 Board.** The block is built. It gives the clearest demo of a pipeline.
4. **L9 Form wizard.** Every app needs entry. Only the step model is new.
5. **L7 Work queue, with L8 as a variant.** Ties to "Needs you".
6. **L4 Dashboard.** Stat and Chart are built. The preset arranges them and adds tile states.

Second wave: L5, L1, L10, L12, L11, L13, L14. Each waits for its missing block. A layout never ships with a "not built" card inside it.

## 6. The manifest model (LY-0)

A page gains two optional keys, `layout` and `regions`. The word `slots` is not used, because the top-level manifest key `slots` belongs to `package_host.md` (PKG-14 to PKG-16). The page keys `header` (AF-7) and `side` (D11a) already exist. A layout adds no second `header` and no second `side`.

```
page: { id, title,
  layout: "list_detail" | "table_workspace" | "board" | "dashboard" | "calendar"
        | "work_queue" | "approval_queue" | "gallery" | "timeline"
        | "map_list" | "checklist" | "document_settings",
  regions: { list?, main?, detail?, kpis?, charts?, worklist?, outline?, body? } }
```

**Two patterns are not page layouts.** Record 360 (L6) is the quality bar of the AF-5 record page and the `record` key (task 3254). A page in `pages` has no record context. Form wizard (L9) is a step mode of the Form block that the AF-6 dialog opens. LY-3 and LY-5 build them there. So the enum holds 12 values.

**Rules.**
1. A region holds view ids. The table below gives the block type and the cap of each region. A bar (filter, bulk, decision, progress, save) is a frame part or a block mode, not a region.
2. A page with `layout` has no `tabs`, no `cells` and no `side`. A page with no `layout` is the D11a page, unchanged (R6). The validator names the key to remove.
3. A region name outside the preset is refused. A required region that is missing is refused. A view of the wrong block type is refused.
4. The D12 scan walks the whole document, so it covers `regions` with no new code.
5. The AF-4 layout switch (`layouts` of a view, `layout` in the URL) is a different thing from the page `layout`. The page layout takes the URL key `preset`. Fence: `pageState.test.ts`.
6. **Deploy order.** The server that knows the keys goes live before a client or the IDE writes them, as for AF-2a.
7. Regions count against the 12 cells of a page (`MAX_CELLS_PER_PAGE`).

| Preset | Required regions (block type, cap) | Optional regions |
|---|---|---|
| `table_workspace` | `main` (Table, 1) | none. The side panel is the record page of AF-5 |
| `list_detail` | `list` (Table, 1), `detail` (Detail, 1) | none |
| `board` | `main` (Board, 1) | none |
| `dashboard` | `kpis` (Stat, 6) | `charts` (Chart, 4), `worklist` (Table, 1) |
| `calendar` | `main` (Calendar, 1) | `list` (Table, 1, the "Unscheduled" list) |
| `work_queue` | `list` (Table, 1), `detail` (Detail, 1) | none |
| `approval_queue` | `list` (Table, 1), `detail` (Detail, 1) | none |
| `gallery` | `main` (Gallery, 1) | none |
| `timeline` | `main` (Timeline, 1) | none |
| `map_list` | `list` (Table, 1), `main` (Map, 1) | none |
| `checklist` | `main` (Checklist, 1) | none |
| `document_settings` | `body` (Document or Form, 4) | `outline` (derived from the body ids, no view) |

A preset whose block is not built (calendar, gallery, timeline, map, checklist, document) stays out of the validator enum until its block lands. So LY-0 ships the first six values only: `table_workspace`, `list_detail`, `board`, `dashboard`, `work_queue` and `approval_queue`. Each later value joins in the slice of its block.

**What LY-0 changes (the validators that refuse a layout page today).**
- `_check_pages` in `studio_manifest.py` says "A page needs tabs or cells". It accepts a page with `layout` and `regions` instead.
- `readPage` in `pageModel.ts` returns null when `hasTabs === hasCells`, so the client drops the page with no message. It reads a layout page.
- `dropViews` in `draftEdit.ts` strips views from cells and `side` only. It strips them from `regions` too, so a deleted view leaves no dangling region.
- `frameEdit.ts` and `pageLayout.ts` (AF-11a) work on cells. They refuse an edit on a layout page, with a plain sentence, until a later slice teaches them regions.

**Fences (R7).** `tests/unit/test_studio_manifest.py` (valid, each bad region, a missing region, a wrong block type, an old manifest, a colour key, a layout page with cells). `test_studio_manifest_ts_parity.py` and the golden schema `tests/unit/golden/studio_manifest.schema.json`. `pageModel.test.ts` (a layout page reads, caps equal the schema). The `draftEdit` and `frameEdit` tests (a deleted view, a refused edit).

**Tier.** LY-0 changes the manifest validator, so it is HIGH. It needs an independent verifier and an Opus diff review.

## 7. How Mira picks a layout

`studio_describe` gets the presets, the regions of each and this table in its existing `frame` part (AF-8a). It adds no second part.

| Words in the description | Layout |
|---|---|
| list of, browse, directory, details beside | L1 |
| track, manage, spreadsheet, bulk, inventory | L2 |
| pipeline, stages, status columns, hiring, kanban | L3 |
| overview, KPIs, summary, how are we doing | L4 |
| schedule, appointments, visits by date, roster | L5 |
| customer, patient, profile, everything about | L6 |
| inbox, tickets, triage, requests to handle | L7 |
| approve, sign off, needs approval | L8 |
| submit, apply, intake, claim, new request | L9 |
| photos, catalog, products, cards | L10 |
| plan over time, Gantt, deadlines | L11 |
| where, route, sites, on a map | L12 |
| checklist, inspection, SOP steps, procedure | L13 |
| policy, wiki, settings, configure | L14 |

One primary layout for each page. When two match, the second gets its own page. Mira states her choice in one sentence, and the member may change it. Fence: the LY-8 eval of 14 prompts.

## 8. Slices

Each layout slice has four parts: the preset component built on `StudioPage` and the existing blocks, its states from LY-1, Mira and IDE support from LY-8, and captures. The UI gate binds each one: four captures (light mode, compact density, a changed accent, 390 px) and the app beside a built-in app (§5.5 rule 9). Owner preview per D145.

| Id | Task | Slice | Done when |
|---|---|---|---|
| LY-0 | 3313 | Preset model: `layout`, `regions`, validator, TS twin, golden, the four validator changes of §6 | An old manifest validates. A bad region fails to publish. First six values only. HIGH, dark **Status: BUILT, dark (2026-11-15, PR 2354).** No renderer yet: a layout page draws "This page has no views." until LY-2. Nothing writes `layout` before LY-8. |
| LY-1 | 3314 | Shared states component | Every preset uses it. A test refuses a local copy. |
| LY-2 | 3315 | Table workspace | Meets the L2 bar, with bulk bar. |
| LY-3 | 3316 | Record 360, as the AF-5 record page | Meets the L6 bar. Needs `record` (task 3254). |
| LY-4 | 3317 | Board | Shared filters with L2, phone stage switcher. |
| LY-5 | 3318 | Form wizard, as a step mode of Form | Steps, progress, review step, draft keep. |
| LY-6 | 3319 | Work queue and approval | Next item, decision bar, success empty state. |
| LY-7 | 3320 | Dashboard | Tile states, caps, tile-to-list link. |
| LY-8 | 3321 | Mira and IDE | `studio_describe` knows the presets. The Pages tab has a picker. Eval passes. **Status: BUILT (PR 2372): `studio_describe` and the eval of 14 prompts on `layout_hint`.** The Pages-tab picker is LY-8b (task 3352, PR 2381). |
| LY-9 to LY-15 | 3322 | Calendar, list and detail, gallery, map, timeline, checklist, document and settings | One slice each, after its block. |

## 9. Owner decisions (2026-11-14, task 3312)

The owner took the recommendation on all three questions.

1. **The preset replaces the grid for a page.** A page has one source of layout (rule 6.2).
2. **The `layout` enum is closed.** A spec change adds a value, in the slice of its block.
3. **D9 (Checklist) and D22 (Activity feed) come first** among the missing blocks, because L13 and L6 need them.

## 10. Limits of the research

The note read Android canonical layouts, NN/g on data tables, empty states and dashboards, and IBM Carbon dashboards in full. It read Airtable, Salesforce and Visa wizard pages only as search snippets. Material 3, Apple HIG, NN/g master-detail, and Polaris pages failed to load. The note read no Atlassian, Carbon data table, Fluent, Linear, Retool, Glide or Softr page, so this spec makes no claim about them. Keyboard shortcuts and bar numbers are this spec's proposals.
