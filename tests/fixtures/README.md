# tests/fixtures — cross-language fixtures

Data read by **more than one test runner**. Everything here exists because the
same rule is implemented in two languages and the two have to be held together
by a shared table rather than by a comment pointing one at the other.

Rules for anything added here:

1. **Both readers, or it does not belong here.** A fixture only pytest reads
   belongs beside its test; a fixture only vitest reads belongs beside its
   `.test.ts`. This directory is for the intersection.
2. **The fixture is the contract.** Neither side may restate the table in its
   own source. A test that types out its own copy "for readability" has
   recreated the two-hand-maintained-mirrors problem the file was minted to
   solve.
3. **Drive the real implementation, not a re-derivation.** The Python side must
   reach the production code path (for the CRM's weighted ₹ that means the
   emitted SQL, whose expression `tests/unit/_crm_fakes.py::_WEIGHTED_SUM_RE`
   reads out of the statement text), and the TS side the exported function. A
   fixture checked against a formula retyped in the test is a mirror agreeing
   with itself.

| File | Readers | Holds |
|---|---|---|
| `crm_weighted_parity.json` | `tests/unit/test_crm_reports.py` · `workbench/control_plane/src/app/crm/lib/board.test.ts` | Weighted ₹ — `(amount, deal_probability, stage_probability, stage_type) → weighted`. Pins `core.WEIGHTED_SQL` against `board.ts::weightedDeal`/`weightedRows`, including the NULL-inherits-the-stage-default rule and the open/ongoing-only filter (WS-26g, `specs/crm_app.md` §9). |
| `deferred_parity.json` | `tests/unit/test_projects_personal_s6f.py` · `tests/live/live_ws39_s6f.py` · `workbench/control_plane/src/app/tasks/lib/sharedFields.test.ts` | "Not yet" — `(defer_days, start_days) → hidden`. Pins `personal.DEFERRED_CLAUSE` (on Postgres), `personal.not_yet` and `utils.ts::isTickled` to one rule: hidden until the later of my defer and the shared start date (D77, `specs/my_tasks_cutover.md` §4.10). |
| `email_new_mailbox_settings.json` | `tests/unit/test_email_auto_draft_defaults.py` · `workbench/control_plane/src/app/email/lib/assistantSettings.test.ts` | The answer of `GET /email/assistant/settings` for a new mailbox, with no settings row and no rules. Pins the GET handler and `assistantSettings.ts::autoDraftRepliesOn` to one rule: reply drafting is OFF for a new mailbox (D-EM-6, `specs/email_app_master_plan.md` §10.4.9, EM-T7). The gateway test also pins `insights_enabled` OFF (D-EM-39, §13.9.1, EM-T14a). No UI reads that key yet. |
| `crm_field_check_cases.json` | `tests/unit/test_crm_field_input_parity.py` · `workbench/control_plane/src/app/crm/components/crmC4p.test.ts` | The client check of a custom field: each case is a value and whether the gateway takes it. Pins `field_studio._CHECKS` (the date, the date and time, and the phone) and `fieldLayout.ts::checkValue` to one rule: the real day, the year in 1..9999, the time and offset ranges, and the 4 digits of a phone (WS-53 CRM-C4p, `specs/crm_platform.md` §20.7). |
| `task_ref_cases.json` | `tests/unit/test_task_refs_text_links.py` · `workbench/control_plane/src/lib/taskRefs.test.ts` | The grammar of a task reference `#123`: each case is a text and the numbers in it, in order. Pins `text_links.find_task_refs` in the gateway and `taskRefs.ts::findTaskRefs` in the client to one set of rules (WS-57 TR-5, `specs/task_references.md` §6). The client draws the pill and the gateway saves the link, so the two must find the same numbers. `markdown_cases` hold the Markdown layer too: vitest renders each one through the card, and pytest reads it through `text_links.prose_of`. |
