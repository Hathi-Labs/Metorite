-- ============================================================================
-- 412_studio_package_pause.sql - the kill switch of a Studio package
-- (WS-STU J3a, Metorite task 1201)
-- ============================================================================
-- Spec: project-docs/specs/ai_studio.md §9 item 7 and §14 J3a,
--   package_host.md §5.3 (PKG-9). R1 · R5 · R6.
-- R1: origin/main held 404 as its highest file on 2026-11-11. Open branches
--   held 405 to 410, and the PR of C8a took 411. So this file is 412. Check
--   the number again at merge (scripts/premerge.sh).
--
-- Why: an admin must stop a package in one step, and new actions must stop
--   within 10 seconds (§9 item 7). A pause is not a status. `apps.status`
--   keeps its CHECK of 114 (`draft`, `live`, `archived`), so a pause keeps
--   the draft, the live version and each publish as they are.
--
-- What: two nullable columns on `apps`.
--   paused_at  TIMESTAMPTZ. When the package was paused. NULL means the
--              package runs. The action door refuses each action of a
--              package with a value (423 `package_paused`).
--   paused_by  TEXT. The address of the session member who paused it.
--
-- Who writes them: only `gateway/studio/packages_store.py`
--   (`pause_package` and `resume_package`), in the transaction that moves
--   each install of the package (`package_host/store.py`) and writes the
--   `app_audit` row. A publish and a rollback do not name the columns, so
--   a publish keeps the pause.
-- Who reads them: `packages_store._GET_SQL` (the door reads the package),
--   and `package_host/resolve.INSTALLS_SQL`, so the sidebar and a viewer
--   see no install of a paused package.
--
-- Expand only (R6): two new nullable columns with no default and no
--   backfill. A row from before this file runs, as it did. Old code names
--   each column of its INSERT and UPDATE, so it never writes these. The
--   ALTER changes only the catalog. Idempotent: ADD COLUMN IF NOT EXISTS.
--   A second run changes nothing. The file adds no CHECK.
--
-- Tenancy (R5): `apps` is tenant-scoped, with FORCE RLS and the generated
--   policies. A new column changes neither. So this file adds no table, no
--   policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE, and each
--   Studio request reads this table. So the file sets `lock_timeout`, as
--   390 does. On a lock timeout the runner tries the whole file again.
--
-- Depends on: 114_custom_apps.sql (the table).
-- Pinned by: tests/unit/test_studio_package_pause.py (the file, by
--   CONTENT, never by number) and tests/unit/test_studio_package_pause_r8.py.
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE apps
    ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS paused_by TEXT;

COMMENT ON COLUMN apps.paused_at IS
    'WS-STU J3a: when the Studio package was paused. NULL means it runs. Only studio/packages_store.py writes it.';

COMMENT ON COLUMN apps.paused_by IS
    'WS-STU J3a: the address of the member who paused the Studio package. Only studio/packages_store.py writes it.';
