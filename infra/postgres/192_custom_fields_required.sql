-- ============================================================================
-- 192_custom_fields_required.sql — a custom field can be MANDATORY.
--
-- What: `pm_custom_fields.required`. A definition marked required must carry a
--   value before a task is allowed to enter the project that defines it.
--
-- Why: owner directive 2026-08-26 — *"ensure all mandatory fields are completed
--   when creating a task that will later move to the Projects app."* There was
--   no mechanism at all: `pm_custom_fields` had `field_key`, `name`,
--   `field_type`, `options`, `position` and nothing that could make one
--   obligatory.
--
-- ⚠️ WHERE IT IS ENFORCED, and this is the part worth reading twice. **At the
--   MOVE, not at capture.** Two reasons, and the second is the one that would
--   have been got wrong:
--
--     1. At capture time nobody knows which project the task will end up in, so
--        nobody can know which definitions apply. A required field is a fact
--        about a DESTINATION, and capture has no destination.
--     2. Enforcing at capture would break frictionless capture, which is GTD's
--        first discipline and something `personal.py::capture` commits to in
--        writing: "takes a title and nothing else is required: no project to
--        choose, no status to pick." A required-field prompt on quick capture
--        is the exact friction that stops people capturing at all, and a task
--        never written down is worse than one written down incompletely.
--
--   So the boundary does the asking. `POST /projects/tasks/{id}/move` refuses a
--   move into a project whose required fields are unanswered, and names them —
--   which is also precisely where a human is already thinking about that
--   project, so the question is expected rather than intrusive.
--
-- ⚠️ NULLABLE with a default rather than `NOT NULL DEFAULT false`, per R6. Every
--   existing definition therefore reads "not required" without a table rewrite,
--   and readers resolve it with `coalesce(required, false)` — "never stated" and
--   "stated false" mean the same thing to this column today, and a later release
--   may tighten it. Adding NOT NULL here would be the tightening, in the same
--   breath as the expansion, which is the shape R6 exists to prevent.
--
-- ⚠️ Deliberately NOT retro-applied. Turning a field required does not make
--   every existing task in that project invalid — the column governs what may
--   ENTER the project from now on, not what is already in it. A migration that
--   made a thousand existing tasks fail validation would be a data change
--   wearing a schema change's clothes.
--
-- Idempotent per infra/postgres/README.md.
--
-- Depends on: 155_projects_custom_fields.sql (pm_custom_fields),
--   175_projects_org_vocabularies.sql (the org-wide scope this column inherits).
-- Pinned by: tests/unit/test_promote_required.py,
--   tests/live/live_ws39_promote_required.sql.
-- ============================================================================

ALTER TABLE pm_custom_fields
    ADD COLUMN IF NOT EXISTS required BOOLEAN DEFAULT false;

COMMENT ON COLUMN pm_custom_fields.required IS
    'True = a task may not ENTER this project without a value for this field. '
    'Checked at POST /projects/tasks/{id}/move, never at capture — a required '
    'field is a fact about a destination and capture has none (GTD: capture '
    'must stay frictionless). NULL reads as false (R6 expand). Not retro-'
    'applied: it governs what enters, not what is already here.';
