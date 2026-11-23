# J2b-4a

Tier: STD

Gate: AGENT-SAFE. Dark behind `STUDIO_ORGS` and `PACKAGE_HOST_ORGS`

## What

🔨 **BUILT, dark (2026-11-13, task 3091).** **Needs you, the backend row.** The needs source `studio` (`routes/shell/needs.py::_studio_approvals`) gives a holder of `studio:publish` one row for each pending version. The owner chose it on 2026-11-13 (answer 29514d69). The row reuses the kind `approval` with app `studio`, so `KIND_ORDER`, `feed_rank` and the frontend do not change. The row opens the Studio library, `/studio`.

**The read and the gates.** The source calls `list_approvals` of J2b-1 and writes no SQL. `Provider(permission="studio:publish")` makes a member without the right read `absent`. `STUDIO_ORGS` off reads `absent` too. Since J2d, `PACKAGE_HOST_ORGS` off drops the app rows only, and the source still gives the skill share rows of J2d.

**The row.** The provider drops each row that the member owns or published, because the store refuses that approval. The row holds the app name, the version and who asked, and no manifest. It has no act. The row leaves with the next read after an approve or a reject.

**What it does not do.** The source has no feed signal, so My Day shows no card, and the Home card is doc only. No migration and no new flag

## Done when

A holder sees one row for each pending version of another member's app. A member without the right sees none. `STUDIO_ORGS` off gives `absent`. `PACKAGE_HOST_ORGS` off drops the app rows (J2d). The owner's own rows and the rows that the member published never show. Fences: `test_shell_needs.py` and `test_shell_needs_r8.py` (R8, two orgs)
