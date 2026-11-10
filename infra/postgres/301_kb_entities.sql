-- ============================================================================
-- 301_kb_entities.sql — the entity registry of Knowledge.
--
-- What: one new table and five indexes.
--       `kb_entities` — one row for each thing the brain knows about. It is a
--                   pointer, not a copy. `ref_table` and `ref_id` name the
--                   owner record: `organization`, `people`, `crm_companies`,
--                   `pm_projects` or `workflows`. A Knowledge-owned entity (a
--                   product, a topic, a written workflow) names
--                   `ref_table = 'kb_entities'` and its own id, and it must
--                   hold a `space_id` and a `name`. `name` is a cache for the
--                   Knowledge-owned types only. The name of every other type
--                   comes from the owner row by a live join. `aliases` holds
--                   the other names that documents use. `page_id` names the
--                   entity page, made lazily. The key is SET NULL, so a page
--                   delete leaves the entity.
--       `kb_entities_org_ref_key` — one entity for each owner record, as a
--                   partial unique index on (organization_id, ref_table,
--                   ref_id) WHERE ref_id IS NOT NULL.
--       `idx_kb_entities_aliases` — a GIN index on `aliases`.
--       `idx_kb_entities_type` — the directory of the Knowledge-owned types.
--       `idx_kb_entities_page`, `idx_kb_entities_space` — the two foreign keys.
-- Why:  WS-61 KC-8a, spec project-docs/specs/knowledge_curation.md §6.1 to
--       §6.4 and §16. The type list in the CHECK mirrors
--       `gateway/kb_entities/registry.ENTITY_TYPES`, and
--       tests/unit/test_kc_entities.py fails when the two differ.
-- Depends on: 130_org_access_control.sql (organization),
--       272_knowledge_pages.sql (kb_spaces, kb_pages).
--
-- Expand only (R6): one new table and its indexes. No rename, no drop, and
-- no row changes. The old gateway never names the table, so it meets no
-- refusal during the deploy.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 293_kb_gaps.sql. `REFERENCES` comes before `DEFAULT`, because the tenancy
-- ratchet in tests/unit/test_tenancy_boundary.py matches the two with no
-- comma between. The foreign key is inline, never
-- `ADD CONSTRAINT <table>_org_fk`, because generated phase 3 adds that name.
-- `ref_id` has no foreign key, because it names a row of one of six tables.
-- `gateway/kb_entities/registry.ensure_entity` is the only writer, and it
-- checks that the owner row is in the bound tenant before it inserts.
-- The number is taken at build time (R1). On 2026-10-25 main held 298, open
-- PR #1288 held 299 and the KC-1a branch held 300. Check it again at merge.
-- The tests find this file by CONTENT, never by number.
-- ============================================================================

SET lock_timeout = '5s';

BEGIN;

CREATE TABLE IF NOT EXISTS kb_entities (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    type              TEXT NOT NULL
                      CONSTRAINT kb_entities_type_check
                      CHECK (type IN ('company', 'product', 'person', 'customer',
                                      'workstream', 'workflow', 'topic')),
    ref_table         TEXT NOT NULL,
    ref_id            UUID NULL,
    name              TEXT NULL
                      CONSTRAINT kb_entities_name_length
                      CHECK (name IS NULL OR char_length(name) BETWEEN 1 AND 200),
    aliases           TEXT[] NOT NULL DEFAULT '{}'
                      CONSTRAINT kb_entities_aliases_cap
                      CHECK (coalesce(array_length(aliases, 1), 0) <= 50),
    page_id           UUID NULL REFERENCES kb_pages (id) ON DELETE SET NULL,
    space_id          UUID NULL REFERENCES kb_spaces (id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Each type names its one owner table. A workflow is a row of
    -- `workflows`, or a written procedure that Knowledge owns.
    CONSTRAINT kb_entities_type_ref_check CHECK (
        (type = 'company'    AND ref_table = 'organization')
     OR (type = 'person'     AND ref_table = 'people')
     OR (type = 'customer'   AND ref_table = 'crm_companies')
     OR (type = 'workstream' AND ref_table = 'pm_projects')
     OR (type = 'workflow'   AND ref_table IN ('workflows', 'kb_entities'))
     OR (type = 'product'    AND ref_table = 'kb_entities')
     OR (type = 'topic'      AND ref_table = 'kb_entities')
    ),
    -- A Knowledge-owned entity points at itself, so the unique index covers
    -- it too. It holds its space and its name (§16, two rules).
    CONSTRAINT kb_entities_owned_check CHECK (
        ref_table <> 'kb_entities'
        OR (ref_id = id AND space_id IS NOT NULL AND name IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS kb_entities_org_ref_key
    ON kb_entities (organization_id, ref_table, ref_id)
    WHERE ref_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kb_entities_aliases
    ON kb_entities USING GIN (aliases);

CREATE INDEX IF NOT EXISTS idx_kb_entities_type
    ON kb_entities (organization_id, type);

-- The SET NULL of a page delete.
CREATE INDEX IF NOT EXISTS idx_kb_entities_page
    ON kb_entities (page_id) WHERE page_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kb_entities_space
    ON kb_entities (space_id) WHERE space_id IS NOT NULL;

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kb_entities'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class
             WHERE oid = to_regclass(t)
               AND relrowsecurity
               AND relforcerowsecurity
        ) THEN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
            EXECUTE format(
                'CREATE POLICY %I ON %I '
                'USING      (organization_id = current_setting(''app.tenant_id'', true)::uuid) '
                'WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)',
                t || '_tenant_isolation', t);
        END IF;
    END LOOP;
END
$rls$;

COMMENT ON TABLE kb_entities IS
    'WS-61 KC-8a: one pointer for each thing the brain knows about. The owner '
    'app keeps the record. A read joins the owner table under the owner rule '
    '(knowledge_curation.md §6.4). gateway/kb_entities/registry.ensure_entity '
    'is the only writer.';

COMMENT ON COLUMN kb_entities.name IS
    'WS-61 KC-8a: a cache for the Knowledge-owned types only (product, topic, '
    'a written workflow). Every other type reads its name from the owner row.';

COMMIT;
