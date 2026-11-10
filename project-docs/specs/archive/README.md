# Archived specs

These specs record design reasons and history. They are not plans. The
feature that each spec describes is live in the code, or a living spec holds
what is left.

Do not use these specs as the current status of anything.
Metorite holds the current state (D118). `../../work_plan.md` §2 is the
workstream index.

On 2026-10-27, the cleanup deleted seven specs from this folder. Only code
comments named them, and git history keeps them.

On 2026-10-29, task #956 brought back three superseded specs that the cleanup
PR #1386 had deleted. Live specs still quote their sections, so each one is
here as a record. The last three rows of the table name them.

| Archived spec | Why archived | Residual open work (carried forward) |
|---|---|---|
| `artifact_viewer.md` | SHIPPED (`ArtifactViewerModal` + sidebar/card + `write_artifact` backend; superset of spec incl. DOCX). | — |
| `chat_implementation_review_2026-07.md` | An audit at one point in time. Its P0 fixes and its refactors are in the code. | Each P1 or P2 that was left went to `chat_ux.md` §12, which is in this folder again. |
| `email_ai_assistant.md` | v2.0 feature inventory (2026-06-29); superseded 2026-07-22 by the consolidated `../email_app_master_plan.md`. Kept for architecture detail + provider matrix. | all residual work → `../email_app_master_plan.md` |
| `email_app_review.md` | Pure M0→M9 build changelog; subsumed by `email_ai_assistant.md` §6 (also archived). | — |
| `email_inbox_zero_parity_plan.md` | Parity audit; core shipped. | open items absorbed into `../email_app_master_plan.md` §5-§7 |
| `email_tool_consolidation.md` | Goal met — tool surface consolidated 63→42 (`agent-email-assistant/agents.py`); plan closed at 42 (master plan §6 decision). | fossil card-key cleanup → `../email_app_master_plan.md` §6 |
| `pixel_art_office_pipeline.md` | Real Pixel-Lab art SHIPPED; described artifacts (`sprites.generated.ts`/`spriteFor()`/Avatar Studio) SUPERSEDED by the top-down office (`office-topdown.tsx` + `character-library.generated.ts`). | — asset-anchor reference only |
| `multi_tenancy.md` | Superseded for architecture on 2026-08-09 by `../saas_multitenancy.md` (D15). `../multi_tenancy_leak_audit.md` and `../crm_app.md` quote its sections. | Cite D15, never its D-MT-* rules. |
| `core_module_map.md` | A module checklist of 2026-07. Specs still use its module ids, such as B4, B6 and E2. | — module-id reference only |
| `chat_ux.md` | Superseded on 2026-08-10 (D26). `../generative_ui_2.md` §2 owns the approval model. | §12 VII to XI are open and not scheduled. |
