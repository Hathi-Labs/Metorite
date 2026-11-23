# STU-Q15a

Tier: HIGH

Gate: AGENT-SAFE build. A value of `MODULE_RUN_ORGS` on a box is the owner's (`enforcement-flip`), after the answer to STU-Q15

## What

🔨 **BUILT, dark (2026-11-06).** **Module Studio code runs only for a listed org** (task 1428). It waits on the owner's answer to STU-Q15 (§12), and the branch does not merge before that answer. Module Studio runs member Python as a child process on the box, and its runner is not a sandbox. **As built:** `MODULE_RUN_ORGS` is a comma list of org ids, or `*`. It is empty by default, so the code of a module runs for no org. `gateway/module_runs.py::module_runs_allowed` is its one reader, and a SIGHUP reload applies a change. `run_module_code` takes a required `organization_id` and calls `refuse_unless_allowed` as its first statement, so it is the one choke point of the three run paths: `POST /workflows/modules/test`, `POST /workflows/modules/{module_id}/test` and the workflow module node. Each test route checks the switch first too, so a refused call opens no session. A refused route answers 403 `{code: "module_runs_off", message}`. The message says that code modules do not run in this organization yet, because they will run in a sandbox first, and that an admin of Metorite can turn them on. A refused node fails with the same words, and the run ends as failed. The org is the session's (`user.organization_id`) or the run's (`NodeServices.organization_id`, from `build_node_services`), never request input. The switch refuses a run with no org, such as a manual or a webhook run until H4 slice 6c. `GET /auth/me/shell` says `module_runs` for the session's org, and `READ_ONLY_ANSWER_KEYS` keeps it out of a save. `/studio` shows the Module Studio tab only when it is true (`studioLibraryView`, through `useModuleRunsOn`). Module CRUD, `/modules/generate`, Studio apps, skills and workflows with no module node do not change. No migration

## Done when

With the flag empty, each of the three paths starts no child process, and a listed org and `*` still run. A refused route answers 403 `module_runs_off` and opens no session. A refused node fails with the message, and the run ends. An org in the body, the query or a header changes nothing. The Module Studio tab hides with the switch off. Fences: `test_module_runs_off.py`, `test_auth_me_shell.py` (`TestTheModuleRunsFlag`), `library.test.ts`, `shellPrefs.test.ts`
