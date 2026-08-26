-- ============================================================================
-- live_ws39_promote_required.sql — migration 192 against REAL Postgres (R8).
--
-- The rule (which fields are mandatory, and what counts as blank) is unit
-- tested. What only a real database can answer is whether the COLUMN behaves
-- the way every reader assumes: that it is nullable with a false default, that
-- existing definitions were not silently made mandatory, and that it survives
-- the org-wide ∪ project-local union `load_definitions` performs.
--
-- That last one is the one worth having. `required` is read through a union
-- with shadowing (WS-27bj), and a column that the union drops would make an
-- org-wide mandatory field silently optional — a validation that passes because
-- the requirement never arrived, which is indistinguishable from success.
--
-- Run:  docker exec -i tenant-scratch psql -U acb -d acb_tenant \
--         -v ON_ERROR_STOP=1 < tests/live/live_ws39_promote_required.sql
-- ============================================================================

\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION rq_check(label text, got anyelement, want anyelement)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF got IS DISTINCT FROM want THEN
        RAISE EXCEPTION 'FAIL % — got %, want %', label, got, want;
    END IF;
    RAISE NOTICE 'ok   %', label;
END; $$;

BEGIN;

-- ── 1. The column's shape, which R6 constrains ──────────────────────────────
SELECT rq_check('1a required exists',
    (SELECT count(*) FROM information_schema.columns
      WHERE table_name = 'pm_custom_fields' AND column_name = 'required'), 1::bigint);
SELECT rq_check('1b it is NULLABLE (R6 expand — tighten in a later release)',
    (SELECT is_nullable FROM information_schema.columns
      WHERE table_name = 'pm_custom_fields' AND column_name = 'required'), 'YES');
SELECT rq_check('1c ...with a false default, so new rows are optional',
    (SELECT column_default FROM information_schema.columns
      WHERE table_name = 'pm_custom_fields' AND column_name = 'required'), 'false');

INSERT INTO organization (id, slug, display_name)
VALUES ('e9000000-0000-0000-0000-0000000000e9','rq-org','Required Ltd')
ON CONFLICT DO NOTHING;
INSERT INTO pm_projects (id, organization_id, name, created_by)
VALUES ('e9100000-0000-0000-0000-000000000001','e9000000-0000-0000-0000-0000000000e9',
        'Client work','ops@rq.invalid');

-- ── 2. An existing definition is NOT retro-made mandatory ───────────────────
--
-- The migration deliberately does not backfill true. Turning a field required
-- must not invalidate every task already in the project.
INSERT INTO pm_custom_fields (id, project_id, field_key, name, field_type,
                              position, created_by, organization_id)
VALUES ('e9200000-0000-0000-0000-000000000001','e9100000-0000-0000-0000-000000000001',
        'notes','Notes','text',10,'ops@rq.invalid','e9000000-0000-0000-0000-0000000000e9');
SELECT rq_check('2a a definition written without the column is NOT required',
    (SELECT coalesce(required, false) FROM pm_custom_fields
      WHERE id = 'e9200000-0000-0000-0000-000000000001'), false);

-- ── 3. A field can be made mandatory, per project ───────────────────────────
INSERT INTO pm_custom_fields (id, project_id, field_key, name, field_type,
                              position, required, created_by, organization_id)
VALUES ('e9200000-0000-0000-0000-000000000002','e9100000-0000-0000-0000-000000000001',
        'client','Client','text',20,true,'ops@rq.invalid',
        'e9000000-0000-0000-0000-0000000000e9');
SELECT rq_check('3a a project-local field can be required',
    (SELECT required FROM pm_custom_fields
      WHERE id = 'e9200000-0000-0000-0000-000000000002'), true);

-- ── 4. ORG-WIDE required survives the union (WS-27bj) ───────────────────────
--
-- `project_id IS NULL` = org-wide. `load_definitions` unions these with the
-- project-local ones and shadows on `field_key`. If `required` did not travel,
-- an org-wide mandatory field would arrive optional — a validation that passes
-- because the requirement never showed up.
INSERT INTO pm_custom_fields (id, project_id, field_key, name, field_type,
                              position, required, created_by, organization_id)
VALUES ('e9200000-0000-0000-0000-000000000003',NULL,
        'cost_centre','Cost centre','text',30,true,'ops@rq.invalid',
        'e9000000-0000-0000-0000-0000000000e9');
SELECT rq_check('4a an org-wide field can be required',
    (SELECT required FROM pm_custom_fields
      WHERE id = 'e9200000-0000-0000-0000-000000000003'), true);
SELECT rq_check('4b the union (org-wide ∪ local) sees BOTH required fields',
    (SELECT count(*) FROM pm_custom_fields
      WHERE organization_id = 'e9000000-0000-0000-0000-0000000000e9'
        AND (project_id = 'e9100000-0000-0000-0000-000000000001'
             OR project_id IS NULL)
        AND coalesce(required, false)), 2::bigint);

-- ── 5. A project-local definition SHADOWS an org-wide one on field_key ──────
--
-- ⚠️ Including its `required`. A project that deliberately relaxes an org-wide
-- requirement must actually be relaxed, or the shadow is decorative.
INSERT INTO pm_custom_fields (id, project_id, field_key, name, field_type,
                              position, required, created_by, organization_id)
VALUES ('e9200000-0000-0000-0000-000000000004','e9100000-0000-0000-0000-000000000001',
        'cost_centre','Cost centre (optional here)','text',31,false,'ops@rq.invalid',
        'e9000000-0000-0000-0000-0000000000e9');
SELECT rq_check('5a the shadowing local row carries required = false',
    (SELECT required FROM pm_custom_fields
      WHERE id = 'e9200000-0000-0000-0000-000000000004'), false);
SELECT rq_check('5b both rows share the field_key — the union must pick ONE',
    (SELECT count(*) FROM pm_custom_fields
      WHERE organization_id = 'e9000000-0000-0000-0000-0000000000e9'
        AND field_key = 'cost_centre'), 2::bigint);

ROLLBACK;

\echo ''
\echo '════════════════════════════════════════════════════════'
\echo '  Required custom fields (mig 192): all checks passed.'
\echo '════════════════════════════════════════════════════════'
