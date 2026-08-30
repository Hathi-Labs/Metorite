-- ============================================================================
-- 194_projects_space_identity.sql — a space carries an icon and a hue.
--
-- What: `pm_projects.icon` (a themed icon NAME) and `pm_projects.icon_slot`
--   (1..8, a slot on the categorical ramp).
--
-- Why: owner directive 2026-08-31 — the sidebar could not tell a space from a
--   folder from a project from a subproject, because every row drew the same
--   glyph. Level now decides the SHAPE of the marker, and a space additionally
--   picks its own icon and hue through Space Settings.
--
-- ⚠️ A NAME and a SLOT, never a colour. `icon` is a key into the themed icon
--   registry (`workbench/control_plane/src/lib/theme/icon-registry.ts`), so the
--   active theme decides which pack draws it. `icon_slot` is an index into the
--   `--cat-1..8` ramp (`src/lib/categorical.ts`), so the active theme decides
--   what the hue actually is, in both light and dark. Storing `#7c3aed` here
--   would be a hardcoded colour in a database column — the one thing
--   DESIGN_SYSTEM.md rule 1 refuses, and unreachable by any later re-theme.
--
-- ⚠️ Both NULLABLE with no default, per R6. NULL means "not chosen", and the
--   client falls back to the level's default glyph and a hue hashed from the
--   space's own name — so an unconfigured space still looks deliberate, and
--   every existing row reads correctly without a rewrite.
--
-- ⚠️ Set on ANY node, meaningful on a SPACE. The write path refuses the two
--   fields on a non-root today. The column is not root-only in SQL, because a
--   later release may let a folder carry a glyph too, and widening a CHECK is
--   the migration this one would otherwise force.
--
-- 🔭 FUTURE, recorded here so it is not re-derived: when departments, teams and
--   groups are defined, a space gains an OWNING team, and it then appears in
--   that team's Center. The seam already exists — `pm_project_grants` with a
--   `group:<slug>` subject (D12) is how a Center gets its slice today, so the
--   future column is an owner, not a second grant mechanism. See
--   `project-docs/specs/project_management_app.md` §5.1.
--
-- Idempotent per infra/postgres/README.md.
--
-- Depends on: 146_projects.sql (pm_projects), 193_projects_node_kind.sql.
-- Pinned by: tests/unit/test_projects_space_identity.py.
-- ============================================================================

ALTER TABLE pm_projects
    ADD COLUMN IF NOT EXISTS icon TEXT;

ALTER TABLE pm_projects
    ADD COLUMN IF NOT EXISTS icon_slot SMALLINT;

DO $$ BEGIN
    ALTER TABLE pm_projects
        ADD CONSTRAINT pm_projects_icon_slot_check
        CHECK (icon_slot IS NULL OR (icon_slot >= 1 AND icon_slot <= 8));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN pm_projects.icon IS
    'A themed icon NAME (src/lib/theme/icon-registry.ts), never an image and '
    'never a glyph: the active theme decides which pack draws it. NULL = use '
    'the default for the node level.';

COMMENT ON COLUMN pm_projects.icon_slot IS
    'A slot 1..8 on the categorical ramp (--cat-1..8, src/lib/categorical.ts). '
    'A SLOT, never a colour, so the hue follows the theme in both light and '
    'dark. NULL = hash the space name for a stable default.';
