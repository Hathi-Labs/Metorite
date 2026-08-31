-- ============================================================================
-- 195_projects_icon_slot_widen.sql — the ramp grew, the CHECK follows.
--
-- What: `pm_projects_icon_slot_check` widens from 1..8 to 1..12.
--
-- Why: owner ask 2026-08-31 — more colour choices for a space's marker. The
--   categorical ramp gained slots 9..12 (`--cat-9..12`, lime / indigo /
--   purple / amber, `src/lib/categorical.ts`), reachable ONLY by an explicit
--   choice: the hash that auto-colours contexts and tags keeps its modulus
--   at 8, so nothing already assigned repaints.
--
-- ⚠️ WIDENING only, which is the R6-safe direction: every value the old
--   constraint accepted, the new one accepts. Old code writes 1..8 and stays
--   valid. Never tighten this back in the same release that narrows the
--   ramp — drain the 9..12 values first.
--
-- Idempotent per infra/postgres/README.md: DROP IF EXISTS, then ADD.
--
-- Depends on: 194_projects_space_identity.sql.
-- Pinned by: tests/unit/test_projects_space_identity.py.
-- ============================================================================

ALTER TABLE pm_projects
    DROP CONSTRAINT IF EXISTS pm_projects_icon_slot_check;

DO $$ BEGIN
    ALTER TABLE pm_projects
        ADD CONSTRAINT pm_projects_icon_slot_check
        CHECK (icon_slot IS NULL OR (icon_slot >= 1 AND icon_slot <= 12));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN pm_projects.icon_slot IS
    'A slot 1..12 on the categorical ramp (--cat-1..12, '
    'src/lib/categorical.ts). A SLOT, never a colour, so the hue follows the '
    'theme in both light and dark. Slots 9..12 are choice-only: the hash '
    'that colours unset spaces stays modulo 8. NULL = hash the space name '
    'for a stable default.';
