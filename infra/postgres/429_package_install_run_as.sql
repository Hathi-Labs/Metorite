-- ============================================================================
-- 429_package_install_run_as.sql - the member that a run with no viewer runs
-- as (WS-STU B6a, Metorite task 1413)
-- ============================================================================
-- Spec: project-docs/specs/ai_studio.md §14 B6 and B6a, §19.2 and §19.10 row
--   10 · package_host.md §5.1 · R1 · R5 · R6.
--
-- R1: this file took 429 on 2026-11-13, when main held 426 and open branches
--   held 423, 424, 427 and 428. Check the number again at merge
--   (scripts/premerge.sh).
--
-- Why: a scheduled or an event run of a Studio app has no viewer. B6 makes
--   the install name one member, and the run reads with the rights of that
--   member, cut down to the `uses` of the package. B6a builds the data, the
--   route that names the member, and the reads.
--
-- What: three nullable columns on `package_install`, with no default.
--   * `run_as`: the address of the member. NULL means no run with no viewer,
--     so such a run fails closed.
--   * `run_as_set_by` and `run_as_set_at`: the member who changed `run_as`
--     last, and the time. A clear sets them too.
--   Two named CHECKs:
--   * `package_install_run_as_member`: NULL, or a lower-case address with the
--     email shape of `package_install_scope_subject` (one `@`, no white
--     space), and no `system:` or `group:` prefix. So a run never runs as a
--     system actor or a group.
--   * `package_install_run_as_stamp`: the stamp is whole (both or neither),
--     `run_as_set_by` is lower case, and a set `run_as` has its stamp.
--
-- Who writes it: only `gateway/package_host/store.py` (`set_run_as`), under
--   the org lock. The route `PUT /studio/packages/{id}/install/run-as` calls
--   it. `store.read_run_as` and `routes/studio/invoke.run_as_read` read it.
--   Fence: tests/unit/test_package_host_store.py (the one writer).
--
-- Expand only (R6): three new nullable columns with no default and no
--   backfill, so the ALTER changes only the catalog. Old code names each
--   column of its INSERT and of its SELECT, so it never reads or writes these.
--   Each CHECK passes on NULL, so it goes in NOT VALID and the file then
--   validates it. Idempotent: ADD COLUMN IF NOT EXISTS, and each CHECK is
--   added only while `pg_constraint` lacks its name. A second run changes
--   nothing.
--
-- Tenancy (R5): `package_install` is tenant-scoped, with FORCE RLS and the
--   policy of 324. A new column changes neither. So this file adds no table,
--   no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- The member purge: `run_as` names a member, and the purge of
--   `routes/admin/members.py` keeps an org or a group install of a leaver.
--   So `run_as` can name a person who left. The run checks the member at
--   each call (active, `feature:studio`, sees the install), so such a run
--   fails closed. A cleanup of the column at the purge is a later slice.
--
-- Never wait for the lock: ALTER TABLE takes ACCESS EXCLUSIVE, and the
--   resolve reads this table on each shell load. So the file sets
--   `lock_timeout` first, as 418 does. On a lock timeout the runner tries the
--   whole file again.
--
-- Depends on: 324_package_registry.sql (the table).
-- Pinned by: tests/unit/test_studio_run_as_r8.py, which finds this file by
--   CONTENT, never by number (R1).
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE package_install ADD COLUMN IF NOT EXISTS run_as TEXT NULL;
ALTER TABLE package_install ADD COLUMN IF NOT EXISTS run_as_set_by TEXT NULL;
ALTER TABLE package_install ADD COLUMN IF NOT EXISTS run_as_set_at TIMESTAMPTZ NULL;

DO $run_as$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'package_install_run_as_member') THEN
        ALTER TABLE package_install ADD CONSTRAINT package_install_run_as_member
            CHECK (run_as IS NULL
                   OR (run_as = lower(run_as)
                       AND run_as !~ '^(system|group):'
                       AND run_as ~ '^[^@[:space:]]+@[^@[:space:]]+$'))
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'package_install_run_as_stamp') THEN
        ALTER TABLE package_install ADD CONSTRAINT package_install_run_as_stamp
            CHECK ((run_as_set_by IS NULL) = (run_as_set_at IS NULL)
                   AND (run_as_set_by IS NULL OR run_as_set_by = lower(run_as_set_by))
                   AND (run_as IS NULL OR run_as_set_by IS NOT NULL))
            NOT VALID;
    END IF;
END $run_as$;

ALTER TABLE package_install VALIDATE CONSTRAINT package_install_run_as_member;
ALTER TABLE package_install VALIDATE CONSTRAINT package_install_run_as_stamp;

COMMENT ON COLUMN package_install.run_as IS
    'WS-STU B6a. The member that a run with no viewer runs as, cut down to the uses of the package. NULL means no such run, so the run fails closed. Only gateway/package_host/store.py (set_run_as) writes it.';
COMMENT ON COLUMN package_install.run_as_set_by IS
    'WS-STU B6a. The member who changed run_as last. Only gateway/package_host/store.py (set_run_as) writes it.';
COMMENT ON COLUMN package_install.run_as_set_at IS
    'WS-STU B6a. The time of the last change of run_as. Only gateway/package_host/store.py (set_run_as) writes it.';
