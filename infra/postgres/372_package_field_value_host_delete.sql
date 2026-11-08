-- ============================================================================
-- 372_package_field_value_host_delete.sql — a host delete takes its package
-- field values (WS-PKG PKG-14a-3, Metorite task 2320).
--
-- What: one trigger function and five AFTER DELETE triggers. When a row of a
--       host table goes, the function deletes the rows of
--       `package_field_value` that point at it.
-- Why:  package_host.md §8.3 and PKG-Q30. `package_field_value.record_id`
--       holds the ids of five tables, so it can have no foreign key to a
--       host table. Before this file, a host delete left its values behind
--       as orphans. A trigger catches each kind of delete: a route, a bulk
--       delete, an import discard, a sync delete and a foreign key cascade
--       (a project delete cascades to its tasks).
-- Depends on: 366_package_field_value.sql (the table and its index),
--             144_crm.sql (the four CRM tables), the pm_tasks migrations.
--
-- The host tables and their kinds. The list must equal HOST_RECORD_KINDS in
-- apps/services/gateway/gateway/package_host/field_values.py.
-- tests/unit/test_package_field_value_host_delete.py fences the parity.
--     pm_tasks       → 'task'
--     crm_leads      → 'crm.lead'
--     crm_contacts   → 'crm.contact'
--     crm_companies  → 'crm.company'
--     crm_deals      → 'crm.deal'
--
-- Each host id and `record_id` are UUID, so the function needs no cast.
--
-- Tenancy (R5). The function deletes only rows WHERE organization_id =
-- OLD.organization_id. That filter is the control, not RLS. RLS does not
-- bind a delete by a role that bypasses it, such as the superuser of the
-- migration runner or of a production one-off. Such a delete sees the
-- values of each org. For a member session, RLS on `package_field_value`
-- binds the delete too, and the bound tenant equals OLD.organization_id,
-- because RLS on the host table let the session see the row. The R8 suite
-- measured a cascade from a member session: the trigger ran under the RLS
-- of that session.
--
-- Not SECURITY DEFINER. The function runs as the role that deletes the host
-- row, so it adds no privilege. search_path is not pinned, because the
-- function is not SECURITY DEFINER, and the repo idiom (168, 341) pins none.
--
-- No EXCEPTION block. A plain DELETE on a valid session can not fail. If it
-- fails, the host delete must fail too, and not leave orphans in silence.
--
-- The cost. The index package_field_value_record_idx of 366 is
-- (organization_id, record_kind, record_id), the exact key of the lookup.
-- So each host row costs one call of the function and one index probe.
-- While PACKAGE_HOST_ORGS is empty the table is empty for each org, and the
-- probe reads one empty index page. An org delete also cascades to the table
-- through its own foreign key, and the trigger then deletes rows that the
-- cascade deletes too. Postgres skips a row that is already gone.
--
-- Expand only (R6): one new function and five new triggers. No table, no
-- column, no rename, no drop of an object that old code names, and no change
-- to a row. Old code deletes host rows as before, and the trigger only adds
-- the delete of their values.
-- Idempotent per infra/postgres/README.md: CREATE OR REPLACE FUNCTION and
-- CREATE OR REPLACE TRIGGER. The repo idiom of 168 takes CREATE OR REPLACE
-- TRIGGER for a hot table. It takes a SHARE ROW EXCLUSIVE lock. A DROP
-- TRIGGER IF EXISTS takes an ACCESS EXCLUSIVE lock, which blocks each read.
-- lock_timeout stops the file from waiting long on a busy table.
--
-- The number was 372 at build time: main ended at 366, and open pull
-- requests held 361, 367, 370 and 371. Check it again at merge (R1).
-- tests/unit/test_package_field_value_host_delete_r8.py finds this file by
-- CONTENT, never by number.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION package_field_value_host_delete() RETURNS trigger
LANGUAGE plpgsql AS $pfv_host_delete$
BEGIN
    -- TG_ARGV[0] is the record kind of the host table (see the header).
    DELETE FROM package_field_value
     WHERE organization_id = OLD.organization_id
       AND record_kind = TG_ARGV[0]
       AND record_id = OLD.id;
    RETURN OLD;
END;
$pfv_host_delete$;

COMMENT ON FUNCTION package_field_value_host_delete() IS
    'Deletes the package field values of a deleted host record (WS-PKG PKG-14a-3). '
    'project-docs/specs/package_host.md §8.3.';

CREATE OR REPLACE TRIGGER trg_pm_tasks_package_field_value_delete
    AFTER DELETE ON pm_tasks
    FOR EACH ROW EXECUTE FUNCTION package_field_value_host_delete('task');

CREATE OR REPLACE TRIGGER trg_crm_leads_package_field_value_delete
    AFTER DELETE ON crm_leads
    FOR EACH ROW EXECUTE FUNCTION package_field_value_host_delete('crm.lead');

CREATE OR REPLACE TRIGGER trg_crm_contacts_package_field_value_delete
    AFTER DELETE ON crm_contacts
    FOR EACH ROW EXECUTE FUNCTION package_field_value_host_delete('crm.contact');

CREATE OR REPLACE TRIGGER trg_crm_companies_package_field_value_delete
    AFTER DELETE ON crm_companies
    FOR EACH ROW EXECUTE FUNCTION package_field_value_host_delete('crm.company');

CREATE OR REPLACE TRIGGER trg_crm_deals_package_field_value_delete
    AFTER DELETE ON crm_deals
    FOR EACH ROW EXECUTE FUNCTION package_field_value_host_delete('crm.deal');

COMMIT;
