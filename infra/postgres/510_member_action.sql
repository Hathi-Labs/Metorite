-- ============================================================================
-- 510_member_action.sql — the act record of the member model (WS-PZ MM-1,
-- task 2878).
--
-- What: one new table. `member_action` holds one row for each act that the
--       member did: a send, a reply, a forward, a done, an archive, a snooze,
--       a label, a delegate, a My Day card press, or the answer to a chat
--       card. A row holds NO subject and NO body. It holds the kind of the
--       act, the kind and the id of the item, derived features of the item
--       (the sender domain, the kind of sender, the triage label), the
--       addresses that the act sent the item to, and the time.
--       acb_common/member_actions.py is the one module that runs an INSERT on
--       it.
-- Why:  personalization.md §19.4 and proactive_loop.md §5.3. A habit (MM-2)
--       reads this record. No store records how the member acts today.
--
-- THE LIFE OF A ROW. `via` says who did the act: `app` (a press in the app),
-- `card` (a yes on a chat card) or `rule` (an automatic act). Only `app` and
-- `card` count as a choice of the member. A row lives 180 days (D-PZ-51). The
-- prune hook of the job worker deletes an older row, and the member purge
-- (gateway/routes/admin/members.py, `member_actions`) deletes the rows of the
-- member in the bound org. It ships dark behind MEMBER_ACTIONS_ORGS.
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and
-- no change of an old object. Idempotent per infra/postgres/README.md:
-- CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, and an RLS block
-- guarded on relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, as 449 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The member comes from the session, never from a request body (R5e).
--
-- The database holds `features` to an object of at most 1 KB. The closed list of
-- keys and the short scalar values are the Python half of the "no text" rule
-- (tests/unit/test_member_action_writer.py), so a new key needs no migration.
--
-- The number is taken at build time (R1). 453 was booked at build, and 510
-- was booked by ~/lanes/reserve_migration.sh when the branch took main.
-- Check it again at merge. Pinned by
-- tests/unit/test_member_action_r8.py, which finds this file by CONTENT,
-- never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS member_action (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The member, in lower case, as the session gave it.
    member_email     TEXT NOT NULL,
    -- The app and the act, for example `email` and `forward`.
    app              TEXT NOT NULL,
    act              TEXT NOT NULL,
    -- Who did it. Only `app` and `card` count as a choice of the member.
    via              TEXT NOT NULL,
    -- The object of the act, for example `email` and its id.
    item_kind        TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    -- The mailbox of the act. NULL for an act with no mailbox.
    mailbox_id       TEXT,
    -- Derived features of the item. Never a subject, never a body.
    features         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- The addresses that the act sent the item to (a forward).
    targets          TEXT[] NOT NULL DEFAULT '{}',
    at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT member_action_member CHECK (
        member_email <> '' AND member_email = lower(member_email)),
    CONSTRAINT member_action_via CHECK (via IN ('app', 'card', 'rule')),
    CONSTRAINT member_action_names CHECK (
        char_length(app) BETWEEN 1 AND 40
        AND char_length(act) BETWEEN 1 AND 40
        AND char_length(item_kind) BETWEEN 1 AND 40
        AND char_length(item_id) BETWEEN 1 AND 200
        AND (mailbox_id IS NULL OR char_length(mailbox_id) BETWEEN 1 AND 200)),
    CONSTRAINT member_action_features_object CHECK (
        jsonb_typeof(features) = 'object' AND pg_column_size(features) <= 1024),
    CONSTRAINT member_action_targets_small CHECK (cardinality(targets) <= 20)
);

-- The read of one member's acts, newest first (MM-2).
CREATE INDEX IF NOT EXISTS member_action_member_idx
    ON member_action (organization_id, member_email, at DESC);
-- The retention prune reads by time.
CREATE INDEX IF NOT EXISTS member_action_at_idx
    ON member_action (at);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['member_action'] LOOP
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

COMMENT ON TABLE member_action IS
    'One act of one member (WS-PZ MM-1): a send, a reply, a forward, a done, '
    'an archive or a card answer. No subject and no body. Only '
    'acb_common/member_actions.py writes it. 180 days, then the prune hook '
    'deletes the row. The member purge deletes the rows of the member.';

COMMIT;
