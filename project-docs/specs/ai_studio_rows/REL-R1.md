# REL-R1

Tier: HIGH

Gate: AGENT-SAFE. After C8a, J2a and C1. **Status: Built dark 2026-11-15 (STU-REL-R1, task 3381).** No flag and no migration

## What

🔨 **BUILT, dark (2026-11-15, task 3381).** **The server names a relation to an object of the same app.** A relation cell, the record title and the form showed the raw id of the linked record. The door now answers a get and a list with `names`, as `{<record id>: <title or null>}`.

`names` covers the ids in the `relation` fields of the returned records. It covers a field only when `to` is a bare object name. A target of the form `<app>.<object>` gets no entry (REL-R4). A `null` means that the viewer may not read the record, or that it does not exist. The two look the same, so the answer is no oracle for an id. The id of a hidden record never appears in a name.

**The reader.** `objects_store.record_titles(db, object_id, ids, *, package_id, viewer)` reads the titles of a page. It runs the view, the fields and then one statement for the records. The count of statements does not grow with the ids.

It goes through `_view` and `_scoped`, as `get_record` does. So the role scope, the row rule (C8a) and `hide` of the TARGET object bind for that viewer. A title that `hide` hides gives `null`. The keyword `viewer` is required, and `record_titles` joins the `_SIX` fence list. The org is the bound tenant (R5).

A page names at most 200 ids (`RELATION_NAMES_CAP`), and the rest give `null`. An id that is no UUID gives `null` with no statement. A record with no title text is named by its id, as `recordTitle` does in the client.

**The title rule** is one pair. `studio_fields.title_key_of` and `record_title` hold the server side: the first text field of one value, else the first field, trimmed, and cut at 160 characters. `titleKeyOf` and `recordTitle` in `packagePreview.ts` hold the client side. Both read `tests/fixtures/studio_title_rule.json`, so a case that differs fails one side.

**The door.** `invoke._relation_names` finds the bare targets in the projected fields. It reads the target object by name in the same package, and it calls the reader once for each target. A viewer of an install (J2a) gets `null` for a target that the installed version does not declare (`_installed_object`). The title key then comes from the installed fields. A read that fails gives `null`, and the page still draws.

**The client.** `realDoor.ts` carries `names` through `withMeta` (`RowsMeta.names`) for a get and for a list. It draws nothing.

**Not built.** The render is REL-R2 (task 3383). The picker is REL-R3 (task 3384). A core target is REL-R4 (task 3385), and it waits for an owner answer. The shape of the chat tool does not change.

**Known gaps.** The chat tool `studio_record_list` still returns relation ids as ids. A later row changes it. A read with no viewer (`run_as_read`) computes `names` and drops them.

## Done when

The owner reads the names of a page of relation cells in a constant count of statements. A viewer under a row rule on the target gets `null` for the record that the rule hides. The same viewer gets the name for the one that it allows. A `hide` on the title field gives `null`.

A missing id, a hidden id and the id of another org give the same answer. A non-UUID gives `null` with no statement. The first 200 ids resolve and the rest give `null`. A viewer of an install whose version does not declare the target gets `null`. `names` is present only for a bare target.

Verify with `uv run pytest -rs` on `test_studio_relation_names.py`, `test_studio_relation_names_r8.py`, `test_studio_row_rules.py`, `test_studio_action_door.py` and `test_studio_action_door_r8.py`. Then run `npx vitest run src/lib/studio/realDoor.test.ts src/lib/studio/packagePreview.test.ts` in `workbench/control_plane`. Fence: `test_studio_relation_names_r8.py`, listed in `tests/fences.json`.
