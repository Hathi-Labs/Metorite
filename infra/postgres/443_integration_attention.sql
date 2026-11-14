-- ============================================================================
-- 443_integration_attention.sql — the items of a connection that need an
-- admin (WS-STU I8b-1a, task 3215).
--
-- What: one new table. `integration_attention` holds one row for each item
--       that needs an admin of the org. The kind `ask` is the ask of one
--       member for one provider that the member may not connect. The kind
--       `broken` is for I10a, and no code writes it yet.
--       gateway/connections/attention.py is the one module that runs SQL on
--       it, and it never names the credential column of the registry.
-- Why:  ai_studio.md §8.2 and §14 I8b-1a. A member who may not connect an
--       org connection presses "Ask an admin". I8b-1b shows each open ask to
--       the admins. A connect at org scope clears it.
-- Depends on: 130_org_access_control.sql (organization), and the
--             (organization_id, id) key `apps_org_id_key` on `apps`, which
--             323 adds.
--
-- THE LIFE OF AN ASK. One row for each org, provider and member (the partial
-- unique index). A second ask within 30 days keeps the row and `asked_at`.
-- A read ignores a row older than 30 days, and a new ask makes it live
-- again. A connect, a withdraw, a package delete (the cascade) and the
-- member purge delete the row. The prune hook of the job worker deletes a
-- row older than 30 days. A row holds no free text of a member.
--
-- Expand only (R6): one new table and two indexes. No rename, no drop, and
-- no change of an old object. Idempotent: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. The foreign key to `apps` takes a SHARE ROW EXCLUSIVE
-- lock on `apps` until COMMIT, so the file sets `lock_timeout` first.
-- Tenant-scoped (R5): `organization_id`, ENABLE and FORCE row level
-- security, and one policy with USING and WITH CHECK, as 441 does.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key to the package is the pair that starts with
-- `organization_id`, because a foreign key check skips RLS. So an ask cannot
-- name a package of another org. It cascades, so a deleted package takes
-- its asks. A `broken` row has no package, and a NULL key skips the check.
--
-- The member purge (gateway/routes/admin/members.py, `connection_asks`)
-- deletes the asks of the member in the bound org. The column is
-- `member_email`, the name that the purge fences read.
--
-- The number is taken at build time (R1): 443 was booked for this task.
-- Check it again at merge. Pinned by tests/unit/test_studio_connection_ask.py
-- and tests/unit/test_studio_connection_ask_r8.py, which find this file by
-- CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS integration_attention (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    -- `ask` (I8b-1a) or `broken` (I10a, no writer yet).
    kind             TEXT NOT NULL,
    -- The connection name of the I1a grammar: a provider id, or
    -- `mcp:<server>`. The longest is `mcp:` and 50 characters.
    provider         TEXT NOT NULL,
    -- The member who asked, in lower case. NULL for a `broken` row.
    member_email     TEXT,
    -- The package whose draft asked: an `apps` row of the same org.
    package_id       UUID,
    asked_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT integration_attention_package_fk FOREIGN KEY (organization_id, package_id)
        REFERENCES apps (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT integration_attention_kind CHECK (kind IN ('ask', 'broken')),
    CONSTRAINT integration_attention_provider CHECK (
        char_length(provider) BETWEEN 1 AND 60),
    CONSTRAINT integration_attention_member CHECK (
        member_email IS NULL OR (member_email <> '' AND member_email = lower(member_email))),
    -- An ask names its member and its package.
    CONSTRAINT integration_attention_ask_shape CHECK (
        kind <> 'ask' OR (member_email IS NOT NULL AND package_id IS NOT NULL))
);

-- One ask for each org, provider and member. The ON CONFLICT of
-- attention.ask names this index by its columns and its predicate.
CREATE UNIQUE INDEX IF NOT EXISTS integration_attention_ask_uq
    ON integration_attention (organization_id, provider, member_email)
    WHERE kind = 'ask';
-- The clear of a connect, and the read of the open asks of an org.
CREATE INDEX IF NOT EXISTS integration_attention_kind_idx
    ON integration_attention (organization_id, kind, provider);

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['integration_attention'] LOOP
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

COMMENT ON TABLE integration_attention IS
    'An item of a connection that needs an admin (WS-STU I8b-1a). The kind ask '
    'is the ask of one member for one provider. A connect at org scope, a '
    'withdraw and the member purge delete it. RLS covers the org.';

COMMIT;
