-- ============================================================================
-- 502_tool_gaps.sql - the tool gap record: one row for each call of
-- find_tools that found no tool (agent_sandbox.md §7.4, SBX-1a, task 3800).
--
-- What: one new table. `tool_gaps` holds one row for each no-match call of
--       find_tools in a chat run: the member, the chat, the run, the need,
--       a category, the ids that the checks found, the answer of the decide
--       question and the outcome. gateway/studio/tool_gaps.py is the one
--       module that runs SQL on the table. The member purge
--       (gateway/routes/admin/members.py, key `tool_gaps`) deletes the rows
--       of a leaver.
-- Why:  agent_sandbox.md §6.1 and §9.2. The team needs real counts of the
--       tools that the chat did not have, before anyone spends on a runtime.
--       SBX-4 lets a build start only from a `no_match` row of the same run.
-- Depends on: 02_chat_history.sql (chat_session), 130_org_access_control.sql
--             (organization).
--
-- THE TEXT OF A ROW. `need` is the query of the model, 1 to 280 characters.
--       It stays in the tenant, and no log line holds it. Each other column
--       holds no tenant text. `category` is one key of the pack families, or
--       `other`, and the CHECK holds its shape. `candidates` holds at most 10
--       ids, each with no space: `pkg:<uuid>`, a first-party tool name or a
--       catalog action id. `failed_tool` is a platform tool name (SBX-14).
--
-- THE LISTS. `answer` and `outcome` hold the values of agent_sandbox.md §7.4
--       and §12.1 decision 3. SBX-1a writes the outcomes existing, reuse,
--       compose, no_match and unsure. SBX-4 writes declined, built and
--       build_failed.
--
-- THE CHAT. `thread_id` references the plain `chat_session (id)` with ON
--       DELETE CASCADE, as 459 does. The one writer selects the chat under
--       the bound tenant, so a chat of another org, or no chat, gives no row.
--       A foreign key check skips RLS, so no statement names a chat by its id
--       alone.
--
-- Expand only (R6): one new table and one index. No rename, no drop, no
-- change to an old object. The old code never names the table. Idempotent.
-- The foreign key takes a SHARE ROW EXCLUSIVE lock on `chat_session`, so the
-- file sets `lock_timeout` first, as 459 does. Tenant-scoped (R5):
-- `organization_id` with ENABLE and FORCE row level security and one policy
-- with USING and WITH CHECK. `REFERENCES` comes before `DEFAULT` (the
-- tenancy ratchet).
--
-- The number is taken at build time (R1): the lane booked 502 for this task.
-- Check it again at merge. Pinned by tests/unit/test_user_tool_registry.py,
-- which finds this file by CONTENT, and tests/unit/test_tenant_coverage.py.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS tool_gaps (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    -- The verified member of the run, in lower case.
    member_email     TEXT NOT NULL,
    -- The chat. A Delete of the chat deletes the row.
    thread_id        TEXT NOT NULL REFERENCES chat_session (id) ON DELETE CASCADE,
    -- The agent run.
    run_id           TEXT NOT NULL,
    -- The query of the model. It stays in the tenant.
    need             TEXT NOT NULL,
    -- One key of the pack families, or `other`.
    category         TEXT NOT NULL DEFAULT 'other',
    -- The ids that the checks found. No text of a member.
    candidates       TEXT[] NOT NULL DEFAULT '{}',
    answer           TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    -- The package that answered or that a build made. No foreign key.
    app_id           UUID,
    -- The name of a failed platform tool (SBX-14), or NULL.
    failed_tool      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tool_gaps_email CHECK (
        member_email <> '' AND member_email = lower(member_email)
        AND length(member_email) <= 320),
    CONSTRAINT tool_gaps_run CHECK (
        thread_id <> '' AND length(thread_id) <= 200
        AND run_id <> '' AND length(run_id) <= 200),
    CONSTRAINT tool_gaps_need CHECK (length(need) BETWEEN 1 AND 280),
    CONSTRAINT tool_gaps_category CHECK (category ~ '^[a-z0-9-]{1,40}$'),
    CONSTRAINT tool_gaps_candidates CHECK (
        cardinality(candidates) <= 10
        AND array_to_string(candidates, ',') !~ '[[:space:]]'
        AND length(array_to_string(candidates, ',')) <= 2000),
    CONSTRAINT tool_gaps_answer CHECK (
        answer IN ('tool', 'compose', 'none', 'unsure')),
    CONSTRAINT tool_gaps_outcome CHECK (
        outcome IN ('existing', 'reuse', 'compose', 'no_match', 'unsure',
                    'declined', 'built', 'build_failed')),
    CONSTRAINT tool_gaps_failed_tool CHECK (
        failed_tool IS NULL OR failed_tool ~ '^[a-z0-9_]{1,80}$')
);

-- The gap view of an org (newest first) and its counts for 30 days.
CREATE INDEX IF NOT EXISTS tool_gaps_org_created_idx
    ON tool_gaps (organization_id, created_at DESC);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['tool_gaps'] LOOP
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

COMMENT ON TABLE tool_gaps IS
    'The tool gap record (agent_sandbox.md §7.4, SBX-1a): one row for each '
    'call of find_tools that found no tool. The need stays in the tenant, and '
    'each other column holds ids, a category or a fixed word. A Delete of the '
    'chat and the member purge delete the row. RLS covers the org.';

COMMIT;
