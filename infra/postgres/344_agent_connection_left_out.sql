-- ============================================================================
-- 344_agent_connection_left_out.sql - the left-out list and the requested
-- scopes of an outside agent's connection (WS-55 EA-17a, D-EA-12, D-EA-13)
-- ============================================================================
-- Spec: project-docs/specs/external_agent_access.md §10.8 and the EA-17a row
-- of §15 · Metorite task 1586 · R5 · R6 · R8.
-- R1: origin/main held 340 as its highest file on 2026-11-04. Branch
-- crm-m1-change-history holds 341. The local branches of the worktrees
-- mcp-p1b1-store and aln5a-rules hold 342 and 343. So this file is 344.
-- Check the number again at merge.
--
-- What: two columns on `agent_connection`.
--   left_out_projects  UUID[] NOT NULL DEFAULT '{}'. The left-out list of
--                      D-EA-12. The reach of the connection stops at each of
--                      these projects, so the project and every project
--                      beneath it stay out, unless a pin beneath it starts a
--                      reach of its own. Empty leaves nothing out. The cap is
--                      the cap of `pinned_projects` (50), in the approve body.
--   requested_scopes   TEXT[], nullable. The scopes that the agent asked for
--                      at the connect, before the member cleared a box. It
--                      is the ceiling of Change access (D-EA-13, EA-17c).
--                      NULL on each connection from before this file.
--
-- Who writes them: only `acb_auth.agent_tokens.create_connection`, from the
--   approve of `gateway/routes/agent_access.py`. Who reads the list: the
--   live read of the connection (`_CONNECTION_LIVE_SQL`), for the edge and
--   for branch 0. Nothing reads `requested_scopes` until EA-17c.
--
-- The list can only narrow. `gateway/routes/projects/core.py` walks the
-- reach from the pins, or from the roots with no pin, and stops at a
-- left-out project. The member's live access, the seal and the personal
-- switch still bound the result.
--
-- Expand only (R6): two new columns, no rename, no backfill. The old code
--   names each column by name, so it never reads the new ones, and its
--   INSERT gets the default. A constant default changes only the catalog,
--   so the ALTER does not rewrite the table. Idempotent: ADD COLUMN IF NOT
--   EXISTS.
--
-- Tenancy (R5): `agent_connection` is tenant-scoped, with FORCE RLS and the
--   policy `agent_connection_tenant_isolation` (248). The columns change
--   neither. So this file adds no table, no policy and no
--   `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: `ALTER TABLE` takes ACCESS EXCLUSIVE on
--   `agent_connection`, and each agent call reads that table. So the file
--   sets `lock_timeout` itself, as 319 and 337 do. On a lock timeout the
--   runner tries the whole file again, and the file is idempotent.
--
-- Depends on: 248_agent_access_tokens.sql (the table).
-- Pinned by: tests/unit/test_agent_left_out_projects.py (R8).
-- ============================================================================

SET lock_timeout = '5s';

-- One statement, so the table takes its ACCESS EXCLUSIVE lock once (review P3).
ALTER TABLE agent_connection
    ADD COLUMN IF NOT EXISTS left_out_projects UUID[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS requested_scopes TEXT[];

COMMENT ON COLUMN agent_connection.left_out_projects IS
    'The left-out list of D-EA-12 (WS-55 EA-17a). The reach stops at each id: the project and every project beneath it stay out, unless a pin beneath starts again. Empty leaves nothing out.';

COMMENT ON COLUMN agent_connection.requested_scopes IS
    'The scopes that the agent asked for at the connect, before the member cleared a box (D-EA-13). The ceiling of Change access. NULL on a connection from before EA-17a.';
