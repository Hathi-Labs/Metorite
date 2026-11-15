# WFC-4a-2a

Tier: STD

Gate: AGENT-SAFE. D145 owner preview

## What

🔨 **BUILT (2026-11-15, task 3305), owner preview pending.** **The editor adds a record to an app: create only.** Rung 3. **S2 decision:** the editor offers the form to the OWNER of the workflow only, because the step runs as `workflows.owner_email`. The editor compares `owner_email` with `useAccess().access.email`, both trimmed and lower-cased as `crud._is_author` does. While access loads, the step is read-only. Any other member sees a plain note with the name of the owner, the stored values, and no form. **Sources, with no free text:** `listPackages()` gives the packages. One `getPackage(id)` gives the draft manifest. A viewer answer offers nothing. The editor offers a plain declared action (`verbMap` skips an action with a `set`) whose `uses` is `object:<name>.create` for an object of the package. `config.action` is the id of that action. The `data` form is the Form block (`FormView`) over the fields of the object, and `checkForm().value` is exactly the `data`. A value with `{{` writes nothing, and a value that `validateRecord` refuses writes nothing. **The pair:** the palette item "Add a record" drops a Human approval and the Studio write, joined by ONE edge. With an armed edge, the path is source, approval, write, target. The inspector shows a live shape check that mirrors `_validate_studio_writes`. **Notes in the form:** the step runs only when a schedule starts the workflow, and only after the owner approves the card. A Test run stops at this step. Each member who can open the workflow can read these values. A stored step that the form cannot edit (a delete, a `set`, an unknown package, a non-owner) shows a read-only summary. WFC-4a-2b-1 makes an update editable. **Non-goals:** no update, no delete, no record id, no route, no gateway change, no flag, no migration. `studio_write` stays in `catalog.UNOFFERED_NODE_TYPES`, so search and the Copilot do not offer it, and it has no `NODE_TYPE_META` entry

## Done when

The owner drops the pair, picks a package that they own, picks the create action, fills the form, and the step holds exactly `package_id`, `action` and `data`. The pair passes `validate_graph`. Another member, or the owner while access loads, sees no palette item and no form. A viewer package, an update, a delete and a `set` action are never offered. Fences: `tests/unit/test_workflows_node_offer.py`, and in `src/app/workflows/` the files `lib/studioWrite.test.ts`, `lib/humanStep.test.ts`, `components/StudioWriteForm.test.tsx` and `components/NodePalette.test.tsx`
