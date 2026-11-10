-- 228_maf_agent_session.sql — one stored MAF session per organization,
-- thread and agent.
--
-- What: `maf_agent_session`. A row holds `AgentSession.to_dict()` of one
--       native MAF agent in one chat thread, after the compaction of §15.9.6.
-- Why:  spec `project-docs/specs/maf_coding_engine.md` §15.9 · decision D84
--       · board WS-43 slice WS-43t2 · fence WS43-F20. A native agent got
--       only TEXT history across turns, so a confirm turn lost the tool
--       output of the turn before (H-215). The store keeps the turns with
--       their tool calls and results. The one module that reads or writes
--       this table is `orchestrator/native_session_store.py`.
-- Depends on: 02_chat_history.sql (`chat_session`), 130_org_access_control.sql
--       (`organization`).
--
-- ── The key ─────────────────────────────────────────────────────────────────
--
-- (organization_id, thread_id, agent_name). The agent is part of the key, so
-- the session of agent X never loads for agent Y in the same thread.
--
-- ── The foreign key to chat_session ─────────────────────────────────────────
--
-- `thread_id` references `chat_session (id)` ON DELETE CASCADE. A deleted
-- chat takes the sessions of every agent of that thread with it. A save
-- after the delete fails on the key, and the store logs "chat gone" and
-- keeps no row. `idx_maf_agent_session_thread` serves the cascade, because
-- the primary key starts with the organization.
--
-- ── What the row never holds ────────────────────────────────────────────────
--
-- `system_context`, `memory_context` and the persona reach the model through
-- a per-run MAF context provider, as instructions (WS-43t1). They are never
-- input messages, so no stored session holds them (§15.9.5).
--
-- ── Tenancy ─────────────────────────────────────────────────────────────────
--
-- The guarded block below is the block of 219_pm_import_runs.sql: ENABLE and
-- FORCE ROW LEVEL SECURITY, then the policy
-- `maf_agent_session_tenant_isolation`. `scripts/gen_tenant_migration.py`
-- adds the table to `generated/`, and `tests/unit/test_tenant_coverage.py`
-- checks it (R5a). Every read and write goes through
-- `acb_graph.tenant_session(org)`, and the FORCE policy is the second lock.
--
-- R6 EXPAND only: a new table, two CHECKs and one index. Nothing that exists
-- changes, so old code meets this schema without noticing it.
--
-- Idempotent per infra/postgres/README.md. Pinned by
-- tests/unit/test_native_session_persistence.py (WS43-F20, R8 on the H3
-- phase-4 catalog as the NOBYPASSRLS app role).

BEGIN;

CREATE TABLE IF NOT EXISTS maf_agent_session (
    organization_id     UUID        NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    thread_id           TEXT        NOT NULL REFERENCES chat_session (id) ON DELETE CASCADE,
    agent_name          TEXT        NOT NULL,
    session_json        JSONB       NOT NULL,
    transcript_digest   TEXT        NOT NULL,
    session_fingerprint TEXT        NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, thread_id, agent_name),
    CONSTRAINT maf_agent_session_digest_is_sha256
        CHECK (transcript_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT maf_agent_session_fingerprint_is_sha256
        CHECK (session_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT maf_agent_session_json_is_object
        CHECK (jsonb_typeof(session_json) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_maf_agent_session_thread
    ON maf_agent_session (thread_id);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'maf_agent_session'::regclass
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        EXECUTE 'ALTER TABLE maf_agent_session ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE maf_agent_session FORCE  ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS maf_agent_session_tenant_isolation '
                'ON maf_agent_session';
        EXECUTE 'CREATE POLICY maf_agent_session_tenant_isolation '
                'ON maf_agent_session '
                '    USING      (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid) '
                '    WITH CHECK (organization_id = '
                '        current_setting(''app.tenant_id'', true)::uuid)';
    END IF;
END
$rls$;

COMMENT ON TABLE maf_agent_session IS
    'One stored MAF AgentSession per (organization, thread, agent), after '
    'compaction. Context and memory are never stored. '
    'maf_coding_engine.md §15.9 · WS-43t2 · fence WS43-F20.';

COMMIT;
