# B6a

Tier: HIGH

Gate: AGENT-SAFE. Dark behind `STUDIO_ORGS` and `PACKAGE_HOST_ORGS`

## What

🔨 **BUILT, dark (2026-11-13, task 1413).** **The reads of a run with no viewer.** Migration 439 (at build) adds `run_as`, `run_as_set_by` and `run_as_set_at` to `package_install`, each nullable with no default. NULL means no run with no viewer, so a run fails closed. `package_host/store.set_run_as` writes the three columns under the org lock, and `read_run_as` reads them. **The route.** `PUT /studio/packages/{id}/install/run-as` takes `{scope, run_as}`, and `null` clears the value. The order: the two switches (404), the id, the body cap, the owner read (404), the scope and the address (422), then `studio:publish` (403). **The right.** A holder of `studio:publish` names the member, because that right installs the app (D180.3). The member must be active in the org, hold `feature:studio` and see the install (`resolve.install_seen_by`). Each miss gives the same 422 `run_as_refused`, so the answer says nothing about another org. One `app_audit` row (op `package.run_as`) commits with the change. **The run.** `invoke.run_as_read` runs one class A object `get` or `list` of the install as that member, inside `no_person_scope`. It checks the member again at each call. It reads the manifest of the version that the install pins, never the draft. Each read takes the viewer of that member (`invoke._row_viewer(member)`), so the row rules and the hidden keys of C8a bind the run as they bind the member. Until C8b, a member who does not own the app holds no role, so an object with `access` gives the run nothing. A class B or C action gets `reads_only`, and each other source gets `source_not_open`. The audit row names `system:package:<id>` as `user_email`, and the member, the install and the run in `detail`. No production code calls the run yet: E3 is the first caller

## Done when

`tests/unit/test_studio_run_as_r8.py` (R8, two orgs) and `tests/unit/test_studio_run_as.py`. A run reads only what the member reads through this install, under the row rules of that member (C8a). The rules of another member never reach the run. A run stops at its next call when the member leaves the org, loses `feature:studio` or leaves the group of the install. The audit row names `system:package:<id>` and the member. The run writes nothing. **Not in B6a:** a write, a door op, a model call (task 2230), the consent of the member, the callers E3, O2, O7 and O14, the J6 screen and an admin screen
