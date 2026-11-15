# B1c

Tier: STD

Gate: AGENT-SAFE

## What

🔨 **BUILT, dark (2026-11-02).** Source 5 of `GET /studio/actions`: the object actions and the named actions of the caller's own packages. **Scope: per caller.** Rows come from the packages that the member owns in the bound org, read from the draft manifest as the door of B2a reads it. Another member and another org see none. **Not in `catalog_rows`.** That function feeds publish, skills and the door as `{id: class}`, and two packages with `object:items.list` would collide. A new function, `actions.package_rows`, feeds this route only. **The row:** source `package`, `id` the `uses` string of an object action or the A2 action id of a named action, `app` the package uuid. A named action also has `uses`. An object gives five rows, one for each verb of `OBJECT_OP_CLASSES`: list and get are A, create and update are B, delete is C. A named action takes the class of its target. A named action leaves out when its target is class X, or when the catalog does not serve it. **The speed:** `packages_store.list_package_actions` reads the object names and the action ids with jsonb path reads in one statement, and it loads no whole manifest. It also reads the `title` of each named action, because the row shows it. It reads only packages with `format = 'manifest'`, so an App Workshop app gives no row. No route holds SQL. The statement count does not grow with the packages. `?app=<uuid>` keeps only the rows of that package. A value that is not a uuid is 422. `validate_manifest` does not change. Its `object:` check returns before the catalog check, and the publish catalog stays the org one. No migration

## Done when

`test_studio_catalog.py` (no database: its `db` fixture fakes the store read, and the R8 suite runs the real SQL) and `test_studio_catalog_r8.py` (`pytestmark = _DB_GATE`, now in the `--must-run` list). The owner sees five rows for each object and one for each named action. Another member and org B see none. Two packages with one object name give two rows with different `app`. A class X target and an action outside the catalog leave out. `?app=` narrows to one package. The statement count is constant. A fence holds `catalog_rows` free of source `package`. Each mutation in the suite docstring turns a named test red
