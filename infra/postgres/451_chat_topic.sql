-- ============================================================================
-- 451_chat_topic.sql - the topics of the chat list (WS-CO CO-2a, task 3501).
--
-- What: one new table. `chat_topic` holds one topic of one org. A topic is
--       `company` (one short list for the org, D163.1) or `member` (the
--       private topic of one member, D163.2). gateway/chat_topic.py is the
--       one module that runs SQL on the table. The member purge
--       (gateway/routes/admin/members.py, `chat_topics`) deletes rows too.
-- Why:  chat_organization.md §6 and §10 (Topics, CO-2). The chat rail groups
--       a member's chats by topic. A name made from a private chat must not
--       reach a colleague, so a member topic belongs to one member.
-- Later: CO-2b adds `topic_id` and `set_by` to `chat_session_member`
--        (migration 450, PR 2471), in its own file. That key can be
--        `(organization_id, topic_id)`, because this file adds
--        UNIQUE (organization_id, id). This file names no other table.
-- Depends on: 130_org_access_control.sql (organization).
--
-- THE SHAPE. `owner_email` is NULL on a company topic and is the lower-case
-- address of the member on a member topic (the CHECK). `hidden_at` is set
-- when an admin hides a company topic. A hidden row keeps its name. The name
-- is 1 to 40 characters, with no space at either end and no control
-- character. The module checks the same rules first, with a clear message.
--
-- ONE NAME FOR EACH LIST. The unique index keys on the org, the scope, the
-- owner and the name in lower case. So a member topic never clashes with the
-- topic of a colleague, and a refusal never shows that a colleague holds a
-- name. NULL is distinct in a unique index, so the owner goes in through
-- COALESCE, as 340 does.
--
-- THE ID. `id` is a UUID with DEFAULT gen_random_uuid(), as 449 has it, and
-- the routes give it as a string. CO-1 and CO-2b key on it.
--
-- THE AUTOMATIC PASS (CO-1). A later pass sets the topic of a chat through
-- one function of CO-2b. That function writes `topic_id` with
-- `set_by = 'auto'` ONLY where no row exists, or where `set_by` is
-- already 'auto'. It never writes over a row with `set_by = 'member'`
-- (D163). The pass names a topic from a private chat as a PRIVATE topic of
-- that member (D163.2), never as a company topic, through
-- `find_or_create_member_topic` of gateway/chat_topic.py.
--
-- WHO READS WHAT. RLS covers the org only. The member rule is in the SQL of
-- the module: each statement reads company topics and the caller's own
-- member topics, and nothing else. An admin reads no member topic of a
-- colleague through the member routes.
--
-- Expand only (R6): one new table and one index. No rename, no drop, no
-- data, and no change to an old object. Idempotent: CREATE TABLE IF NOT
-- EXISTS, CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. The file sets `lock_timeout` first, as 449 and 450 do.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK. The org of a new row
-- comes from the DEFAULT, the bound tenant. The writer never names it.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The generated phases carry the table.
--
-- The number is taken at build time (R1): 451 was booked for this task, and
-- 450 is booked for PR 2471. Check it again at merge. Pinned by
-- tests/unit/test_chat_topic.py and tests/unit/test_chat_topic_r8.py, which
-- find this file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS chat_topic (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The name that the rail shows.
    name             TEXT NOT NULL,
    -- `company` (the org's list) or `member` (one member's own topic).
    scope            TEXT NOT NULL,
    -- NULL on a company topic. The member, in lower case, on a member topic.
    owner_email      TEXT,
    -- When an admin hid a company topic. NULL: the topic shows.
    hidden_at        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    -- The key of a later composite foreign key (CO-2b).
    CONSTRAINT chat_topic_org_id_key UNIQUE (organization_id, id),
    CONSTRAINT chat_topic_scope CHECK (scope IN ('company', 'member')),
    CONSTRAINT chat_topic_owner CHECK (
        (scope = 'company' AND owner_email IS NULL)
        OR (scope = 'member' AND owner_email IS NOT NULL AND owner_email <> ''
            AND owner_email = lower(owner_email))),
    CONSTRAINT chat_topic_name CHECK (
        char_length(name) BETWEEN 1 AND 40
        AND name = btrim(name)
        AND name !~ '[[:cntrl:]]')
);

-- One name for each list: the org, the scope and the owner, in any case.
CREATE UNIQUE INDEX IF NOT EXISTS chat_topic_name_uq
    ON chat_topic (organization_id, scope, COALESCE(owner_email, ''), lower(name));

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['chat_topic'] LOOP
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

COMMENT ON TABLE chat_topic IS
    'A topic of the chat list (WS-CO CO-2a). company: the org list. member: '
    'the private topic of one member, which no colleague reads. The member '
    'purge deletes the member topics of a leaver. RLS covers the org.';

COMMIT;
