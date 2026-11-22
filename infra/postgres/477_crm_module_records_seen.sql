-- ============================================================================
-- 477_crm_module_records_seen.sql - the seen stamp of a generic mirror record
-- (WS-53 CRM-Q6a, Metorite task 3870)
-- ============================================================================
-- Spec: project-docs/specs/crm_platform.md §18.7 (the delete walk of a
--   generic module) · R1 · R5 · R6.
--
-- R1: the number 477 was booked for task 3870 on 2026-11-22. Main then held
--   472 as its highest file. Check the number again at merge
--   (scripts/premerge.sh).
--
-- Why: a generic module of the Zoho mirror (Invoices, Quotes, a custom
--   module) got its deletes only from the deleted list of Zoho. Zoho keeps a
--   delete for 60 days, and a refused read of that list only logs. So a
--   record that Zoho deleted could stay in the mirror for ever (task 3729,
--   cause 1).
--
-- What: one nullable column on `crm_module_records`:
--   * `ext_seen_at`: the start of the last complete full read of the module
--     that listed the record. NULL means that no such read listed it yet.
--   The weekly full read of task 3869 writes it page by page, through the
--   door `core.mark_module_records_seen_from_source`. At the end of a
--   complete read, the engine deletes a row that the two last complete reads
--   both missed (`engine._drop_stale`, the rule of the core walk). The core
--   mirror tables got the same column in 246.
--
-- RLS (R5): the table keeps its FORCE row level security and its policy. A
--   new column of a table changes no generated phase, so this file adds no
--   table, no policy and no `gen_tenant_migration.EXEMPT` row. The column
--   holds no member address, so the member purge needs no change.
--
-- Expand only (R6): a new nullable column with no default and no backfill,
--   so the ALTER changes only the catalog and rewrites no row. Old code names
--   each column that it writes, so it never writes this one. NULL is the
--   correct value of each old row: no complete read listed it yet.
--   Idempotent: ADD COLUMN IF NOT EXISTS. A second run changes nothing.
--
-- Never wait for the lock: ALTER TABLE takes a strong lock, and each page of
--   the sync writes this table. So the file sets `lock_timeout` first, as 470
--   and 472 do. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 251_crm_module_records.sql (the table).
-- Pinned by: tests/unit/test_crm_module_delete_walk.py (static) and
--   tests/unit/test_crm_module_delete_walk_r8.py (R8). Both find this file
--   by CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE crm_module_records ADD COLUMN IF NOT EXISTS ext_seen_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN crm_module_records.ext_seen_at IS
    'WS-53 task 3870. The start of the last complete full read of the module that listed this record. NULL: no such read listed it yet. Only the sync writes it, through core.mark_module_records_seen_from_source. A row that the two last complete reads missed is deleted. crm_platform.md §18.7.';
