-- ============================================================================
-- 287_crm_views_module_entity.sql — saved views on the lists of generic modules.
--
-- What: `crm_views.entity` also accepts `module:<api_name>`, such as
--       `module:Products`. The check `crm_views_entity_known` is replaced by
--       a wider one. The four known entities stay valid.
-- Why:  WS-53 CRM-U8. A member saves a view of a Zoho module list the way
--       they save a view of Deals. The shared view chips need one table.
-- Depends on: 250_crm_views.sql.
--
-- Expand only (R6): the new check admits every old value, so old code meets
-- the new schema. Idempotent: DROP CONSTRAINT IF EXISTS, then ADD. A second
-- run changes nothing.
-- The number is taken at build time (R1). Origin/main held 284 on 2026-10-20
-- and branches held 285 and 286. Check it again at merge.
-- Pinned by tests/unit/test_crm_views_entity.py, which finds this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

ALTER TABLE crm_views DROP CONSTRAINT IF EXISTS crm_views_entity_known;
ALTER TABLE crm_views ADD CONSTRAINT crm_views_entity_known CHECK (
    entity IN ('leads', 'deals', 'contacts', 'organizations')
    OR entity ~ '^module:[A-Za-z][A-Za-z0-9_]{0,99}$'
);

COMMIT;
