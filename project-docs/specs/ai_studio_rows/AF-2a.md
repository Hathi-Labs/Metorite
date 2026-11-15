# AF-2a

Tier: STD

Gate: AGENT-SAFE. After AF-1. **Status: Built dark 2026-11-14 (STU-AF2a, task 3185).** No UI and no flag

## What

**The `nav` of the manifest** (task 3185). Rung 4. `nav` joins the A2 model, the schema and `lib/studio/manifest.ts`, the way `pages` did (D11a). It is an optional top-level list that defaults to empty, so each stored manifest still validates. Up to 4 sections (`MAX_NAV_SECTIONS`), each with a `title` and up to 12 rows (`MAX_NAV_ROWS`). A row is `{page, icon?, label?}`. The `page` names a declared page id, and `icon` is a name of the icon list. **The icon list** is `NAV_ICONS` in `studio_manifest.py`: 42 Lucide names that `lib/icons.tsx` draws. They come from the icons of the built-in rails (`lib/nav.ts`, the CRM rail, the Knowledge rail and the Projects tree) and the common names of a business app. `NAV_ICONS` in `lib/studio/manifest.ts` is the twin, and the parity test holds them equal. A name outside the list gives a plain sentence. A colour key in a row meets the D12 scan. **A `nav` needs `pages`.** The default pages of §5.5 rule 3 are drawn in the client from the objects, so a row cannot name them. Two sections may not share a title. The `nav` of this slice is not the `contributes.nav` point of `package_host.md`, which adds a row to the shell sidebar. **Non-goals:** the UI (AF-2b), a count and a view in a row, and a row that the reads of the viewer hide (AF-3), and `studio_describe`, the skeleton and the IDE (AF-8). Today `studio_describe` and the skeleton know no part of `nav` or `pages` (a grep of `studio_tools.py` finds none)

## Done when

`test_studio_manifest.py` holds the `nav` cases: a valid nav, 4 sections and a fifth refused, 12 rows and a thirteenth refused, an unknown page, a bad icon, a colour key, no `nav`, and each first-party template. `test_studio_manifest_ts_parity.py` and the stale-schema test pass, with the icon twin. `navCaps.test.ts` holds the TS caps equal to the `maxItems` of the schema. `realDoor.test.ts` holds `nav` in `getPackage`. `test_studio_package_routes_r8.py` publishes a manifest with `nav`. Verify: `uv run pytest tests/unit/test_studio_manifest.py tests/unit/test_studio_manifest_ts_parity.py tests/unit/test_studio_package_routes.py`, `cd workbench/control_plane && npx vitest run src/lib/studio`, `npx tsc --noEmit` **Deploy order:** the server that holds this change must be live before a client or the IDE writes `nav`, because an older server refuses an unknown key.
