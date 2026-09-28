-- 221_chat_session_creator_owner_backfill.sql — every creator has an owner row.
--
-- What: one data backfill. For each `chat_session` whose `user_id` is an
--       email, it inserts the row (session, creator, 'owner') into
--       `chat_session_participant` when no row for that pair exists.
-- Why:  spec `project-docs/specs/projects_ai_chat.md` §20.3 rule 14 · board
--       WS-27bm slice S14, round 4. Round 3 made the creator an owner only
--       through a participant row, unless the session has no row at all.
--       Before S14, `resolve_room_access` made every creator an owner with no
--       row. Live sessions can hold rows for guests and none for the creator:
--         * a chat shared before its first fold, because the old
--           `_add_participant` inserted only the guest;
--         * a LiteLLM chat that never folds, and that the creator shared;
--         * a session whose fold failed, and that the creator then shared.
--       Without this file, each of those creators gets 404 on the deploy.
-- Depends on: 138_groups_and_session_participants.sql (the table and its
--       first backfill), generated/01..04 (organization_id and the RLS
--       policy) when they are applied. Idempotent.
--
-- ── What it keeps ───────────────────────────────────────────────────────────
--
-- Every creator keeps the access that main gives today. That includes a
-- creator whom an owner "removed", because main never enforced a removal.
-- From this deploy on, a removal holds. `ON CONFLICT DO NOTHING` keeps the
-- role of a creator who has a row, so a demoted creator stays demoted. A
-- `user_id` that is not an email (`'default'`, `'system'`) gets no row. It
-- is outside the participant grammar, so no row can stand for it. Instead
-- `resolve_room_access` keeps the creator fallback for it in every room
-- (spec §20.3 rule 16), so it keeps the owner role that main gives it.
--
-- ── Tenancy ─────────────────────────────────────────────────────────────────
--
-- Two shapes. The ladder runs before `generated/`, so a fresh replay has no
-- `organization_id` and no RLS, and one INSERT serves it. Production has
-- `organization_id UUID NOT NULL`, default
-- `current_setting('app.tenant_id', true)::uuid`, and FORCE ROW LEVEL
-- SECURITY with the policy
-- `organization_id = current_setting('app.tenant_id', true)::uuid`. The
-- runner connects as the table owner, and FORCE binds the owner too unless
-- it is a superuser. So in that shape the file binds each tenant in turn with
-- `set_config('app.tenant_id', ..., true)` and copies the session's own
-- `organization_id`. `organization` has no policy, so the loop can read it.
-- The bind is local to this transaction. The file adds no table, so it adds
-- no `gen_tenant_migration.EXEMPT` row.
-- Fences: `tests/unit/test_rooms.py` (the ladder shape) and
-- `tests/unit/test_chat_creator_owner_backfill.py` (FORCE RLS, run as a role
-- that is not a superuser, not the owner and not BYPASSRLS).
--
-- ── Never wait for the lock ─────────────────────────────────────────────────
--
-- The INSERT takes ROW EXCLUSIVE on the participant table, which every room
-- read touches. The runner sends `SET lock_timeout = '5s'`, and this file
-- sets the same value, as 220 does. On a timeout the runner retries the
-- whole file, and the file is idempotent.

SET lock_timeout = '5s';

DO $$
DECLARE
    org     uuid;
    tenancy boolean;
BEGIN
    -- The ladder runs before `generated/`, so a fresh replay has no
    -- `organization_id` yet. That shape has no RLS either, and one INSERT
    -- serves it.
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'chat_session_participant'
          AND column_name = 'organization_id'
    ) INTO tenancy;

    IF NOT tenancy THEN
        INSERT INTO chat_session_participant (session_id, subject, role)
        SELECT s.id, s.user_id, 'owner'
        FROM chat_session s
        WHERE s.user_id LIKE '%@%'
        ON CONFLICT DO NOTHING;
        RETURN;
    END IF;

    -- The tenancy shape: bind each tenant, then copy its own sessions.
    FOR org IN SELECT id FROM organization LOOP
        PERFORM set_config('app.tenant_id', org::text, true);
        EXECUTE $q$
            INSERT INTO chat_session_participant
                (session_id, subject, role, organization_id)
            SELECT s.id, s.user_id, 'owner', s.organization_id
            FROM chat_session s
            WHERE s.organization_id = $1
              AND s.user_id LIKE '%@%'
            ON CONFLICT DO NOTHING
        $q$ USING org;
    END LOOP;
END $$;
