# Archived specs

These specs record design reasons and history. They are not plans. The
feature that each spec describes is live in the code, or a living spec holds
what is left.

Do not use these specs as the current status of anything.
`../../work_plan.md` §2 is the only current-state authority.

On 2026-10-27, the cleanup deleted seven specs from this folder. Only code
comments named them, and git history keeps them.

| Archived spec | Why archived | Residual open work (carried forward) |
|---|---|---|
| `artifact_viewer.md` | SHIPPED (`ArtifactViewerModal` + sidebar/card + `write_artifact` backend; superset of spec incl. DOCX). | — |
| `chat_implementation_review_2026-07.md` | An audit at one point in time. Its P0 fixes and its refactors are in the code. | Each P1 or P2 that was left went to `chat_ux.md` §12. The cleanup of 2026-10-27 deleted that spec. |
| `email_ai_assistant.md` | v2.0 feature inventory (2026-06-29); superseded 2026-07-22 by the consolidated `../email_app_master_plan.md`. Kept for architecture detail + provider matrix. | all residual work → `../email_app_master_plan.md` |
| `email_app_review.md` | Pure M0→M9 build changelog; subsumed by `email_ai_assistant.md` §6 (also archived). | — |
| `email_inbox_zero_parity_plan.md` | Parity audit; core shipped. | open items absorbed into `../email_app_master_plan.md` §5-§7 |
| `email_tool_consolidation.md` | Goal met — tool surface consolidated 63→42 (`agent-email-assistant/agents.py`); plan closed at 42 (master plan §6 decision). | fossil card-key cleanup → `../email_app_master_plan.md` §6 |
| `pixel_art_office_pipeline.md` | Real Pixel-Lab art SHIPPED; described artifacts (`sprites.generated.ts`/`spriteFor()`/Avatar Studio) SUPERSEDED by the top-down office (`office-topdown.tsx` + `character-library.generated.ts`). | — asset-anchor reference only |
