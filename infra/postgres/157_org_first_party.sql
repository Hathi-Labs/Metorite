-- ============================================================================
-- 157_org_first_party.sql — MT-0b: self-mutation is first-party-only
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy.md §6.2 / MT-0b · board WS-29.
--
-- Root AGENTS.md non-negotiable 3 has said this since it was written:
--
--   "native MAF agents (local_path, no own remote) currently land approved
--    self-mutations by opening a PR against THIS Metorite monorepo …
--    It MUST be swapped for a tenant-isolated mechanism before any
--    multi-tenant/customer deployment — third parties must never push to the
--    shared monorepo."
--
-- A third party's agent failing in production must therefore not be able to
-- open a pull request against Fracktal's repository. The cheapest sufficient
-- containment is a flag that is FALSE by default, so a tenant created tomorrow
-- is contained by construction rather than by someone remembering.
--
-- `work_plan.md` WS-3 records that no `first_party` field existed anywhere —
-- "the phrase occurs only in comments and one test helper". This migration
-- creates it, as the single source of truth the gate reads.
--
-- DEFAULT false is the whole point. The existing 'default' org is backfilled to
-- true so today's behaviour is unchanged; every organization created after this
-- migration is contained until a human says otherwise.
--
-- Idempotent. Depends on: 130_org_access_control.sql (organization).
-- ============================================================================

ALTER TABLE organization
    ADD COLUMN IF NOT EXISTS first_party BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN organization.first_party IS
    'MT-0b: may this org''s agents land self-mutations as PRs against the '
    'Metorite monorepo? FALSE for every tenant except the operator''s own. '
    'Read by orchestrator.mutation._self_mutation_permitted, which fails CLOSED.';

-- The operator's own organization keeps the behaviour it has today. This is the
-- ONLY row that should ever carry true on a multi-tenant deployment.
UPDATE organization
   SET first_party = true
 WHERE slug = 'default';
