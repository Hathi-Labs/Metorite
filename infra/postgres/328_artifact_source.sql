-- ============================================================================
-- 328_artifact_source.sql — a file keeps the chat it came from, after the
-- chat is deleted (WS-60 AM-10).
--
-- What: two nullable columns on `artifacts`.
--       `source_title`      — the title of the chat at the time of its delete.
--       `source_deleted_at` — the time of that delete. NULL means that the
--                             chat of `session_id` was not deleted.
--       The row keeps `session_id`, so each file still names its chat.
--       acb_memory/artifacts.py stays the one writer (`keep_in_session`).
-- Why:  D162.7 and D162.8, artifact_management.md §7.3 and §11 AM-10. Owner,
--       2026-11-02: "we should be able to delete a session chat without
--       deleting its generated files". With the registry on, a chat delete
--       moves the chat's files to `outputs/.kept/<kept id>/` and keeps their
--       rows. `GET /artifacts?session_id=` leaves out a row with
--       `source_deleted_at`, so a new chat that reuses the id lists none of
--       them (H-227).
-- Depends on: 268_artifacts.sql (artifacts).
--
-- Expand only (R6): two nullable columns with NO default. No rename, no
-- drop, no backfill and no change to a row. A column with no default changes
-- only the catalog, so the ALTER rewrites no row. The old code names neither
-- column: it reads `artifacts` with a named column list. NULL is the right
-- value for each old row, because no chat was deleted under the keep rule.
--
-- Tenancy (R5): `artifacts` already has the tenant `organization_id` and
-- FORCE RLS with its policy (268). This file changes neither, so it adds no
-- table, no policy and no `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: ALTER TABLE takes ACCESS EXCLUSIVE on
-- `artifacts`, and each blob write registers a row in it. So the file sets
-- lock_timeout first, as 322 does. On a lock timeout the runner tries the
-- whole file again, and the file is idempotent.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS. A second
-- run changes nothing.
--
-- The number is taken at build time (R1). On 2026-11-02 origin/main held 325.
-- Open pull requests claim 326 (WS-CAL CE-4) and 327 (WS-STU C1b). Check it
-- again at merge. tests/unit/test_am10_kept_chat_files.py finds this file by
-- CONTENT, never by number.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS source_title TEXT NULL;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS source_deleted_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN artifacts.source_title IS
    'WS-60 AM-10: the title of the chat of session_id at its delete. '
    'artifact_management.md §7.3.';
COMMENT ON COLUMN artifacts.source_deleted_at IS
    'WS-60 AM-10: when the chat of session_id was deleted. NULL means it was '
    'not. GET /artifacts?session_id= leaves such a row out. '
    'artifact_management.md §7.3.';

COMMIT;
