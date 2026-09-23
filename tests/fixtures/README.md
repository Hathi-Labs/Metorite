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
| `deferred_parity.json` | `tests/unit/test_projects_personal_s6f.py` · `tests/live/live_ws39_s6f.py` · `workbench/control_plane/src/app/tasks/lib/sharedFields.test.ts` | "Not yet" — `(defer_days, start_days) → hidden`. Pins `personal.DEFERRED_CLAUSE` (on Postgres), `personal.not_yet` and `utils.ts::isTickled` to one rule: hidden until the later of my defer and the shared start date (D76, `specs/my_tasks_cutover.md` §4.10). |
