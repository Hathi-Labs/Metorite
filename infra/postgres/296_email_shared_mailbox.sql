-- ============================================================================
-- 296_email_shared_mailbox.sql — the expand step of the shared mailbox, and
-- the seed of `email:shared:manage` onto `admin`.
--
-- What: two nullable columns, four new tables and one capability seed.
--       `email_accounts.shared_since`  — NULL is a private mailbox. A time is
--                            a mailbox of the organization (D-SM-1). `user_id`
--                            then keeps the member who connected it, as a
--                            record only.
--       `email_messages.drafted_by`    — the member who wrote a local draft
--                            row of a shared mailbox (§5.3). NULL for a draft
--                            that Metorite did not write.
--       `email_mailbox_grants`         — who reads, sends from or manages a
--                            shared mailbox, in the D12 subject grammar:
--                            `org`, `group:<slug>` or a bare email (D-SM-2).
--                            A trigger refuses a grant on a private mailbox.
--       `email_thread_assignments`     — the one member who answers a thread
--                            of a shared mailbox (§5.2). The same trigger
--                            refuses a row on a private mailbox.
--       `email_send_log`               — append-only. One row for each send
--                            from a shared mailbox, with the member who pressed
--                            Send (D-SM-7, §7). The app role gets SELECT,
--                            INSERT and UPDATE of `provider_message_id` only.
--                            A trigger refuses each other change.
--       `email_mailbox_member_prefs`   — one row for each member and shared
--                            mailbox: "in All inboxes" and "keep separate"
--                            (§9, the All inboxes row).
--       Seed: `email:shared:manage` for the `admin` role of every organization,
--       and in provision_org_roles, so a new organization has it too (Q-SM-2).
-- Why:  WS-65 SM-1, spec project-docs/specs/shared_mailbox.md §4.4, §5.2,
--       §5.3, §7, §8.2 step 1 and §9.
-- Depends on: 17_email_accounts.sql (email_accounts, email_messages),
--       130_org_access_control.sql (organization, org_role,
--       org_role_permission), 200_provision_org_roles_tenancy.sql and
--       272_knowledge_pages.sql (the function body that part 4 rewrites),
--       223_email_accounts_unique_per_tenant.sql
--       (email_accounts.organization_id on the numbered ladder).
--
-- Expand only (R6): two nullable columns with no default, four new tables,
-- their indexes, two trigger functions, one INSERT of rows that ON CONFLICT
-- DO NOTHING skips, and one function body with one more array element. No
-- rename, no drop, and no change to an existing column. Old code reads
-- `user_id = :uid`, so a shared row looks private to its connector and to
-- nobody else, which is safe. The code that reads the new objects ships dark
-- behind EMAIL_SHARED_MAILBOX (SM-2).
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, CREATE
-- TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE
-- FUNCTION, DROP TRIGGER IF EXISTS then CREATE, an RLS block guarded on
-- relforcerowsecurity, REVOKE and GRANT, and a function rewrite that does
-- nothing when the element is already there.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of
-- 272_knowledge_pages.sql. `REFERENCES` comes before `DEFAULT`, because the
-- tenancy ratchet in tests/unit/test_tenancy_boundary.py matches the two with
-- no comma between. The foreign keys are inline.
-- The number is taken at build time (R1). On 2026-10-23 origin/main held 295,
-- and open PR #1192 held 289 and open PR #1162 held 286. Check it again at
-- merge. The tests find this file by CONTENT, never by number.
-- Fence: tests/unit/test_email_shared_mailbox_schema_r8.py.
-- ============================================================================

BEGIN;

-- ── 1. The two columns ──────────────────────────────────────────────────────

ALTER TABLE email_accounts
    ADD COLUMN IF NOT EXISTS shared_since TIMESTAMPTZ NULL;

COMMENT ON COLUMN email_accounts.shared_since IS
    'WS-65 SM-1, D-SM-1: NULL is a private mailbox. A time is a mailbox of the '
    'organization, and user_id is then the member who connected it, as a record. '
    'shared_mailbox.md §4.1.';

ALTER TABLE email_messages
    ADD COLUMN IF NOT EXISTS drafted_by TEXT NULL;

COMMENT ON COLUMN email_messages.drafted_by IS
    'WS-65 SM-1: the member who wrote a local draft row of a shared mailbox. '
    'NULL for a draft that Metorite did not write. shared_mailbox.md §5.3.';


-- ── 2. The tables ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS email_mailbox_grants (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    account_id       UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    -- 'org' | 'group:<slug>' | '<email>', stored folded. The validator in
    -- code (acb_auth.permissions.normalize_grant_subject) is the fence, and
    -- this CHECK only refuses a shape no validator would pass.
    subject          TEXT NOT NULL
                     CHECK (subject = lower(subject)
                            AND (subject = 'org'
                                 OR subject ~ '^group:.+$'
                                 OR subject ~ '^[^@[:space:]]+@[^@[:space:]]+$')),
    -- Each level includes the one before it (D-SM-2).
    level            TEXT NOT NULL CHECK (level IN ('read', 'send', 'manage')),
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, account_id, subject)
);

CREATE INDEX IF NOT EXISTS idx_email_mailbox_grants_account
    ON email_mailbox_grants (account_id);
-- Offboarding removes the grants of one address (D-SM-8).
CREATE INDEX IF NOT EXISTS idx_email_mailbox_grants_subject
    ON email_mailbox_grants (organization_id, subject);

CREATE TABLE IF NOT EXISTS email_thread_assignments (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    account_id       UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    thread_id        TEXT NOT NULL,
    assignee_email   TEXT NOT NULL CHECK (assignee_email = lower(assignee_email)),
    assigned_by      TEXT NOT NULL,
    assigned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, account_id, thread_id)
);

-- Reply Zero reads the threads of one assignee.
CREATE INDEX IF NOT EXISTS idx_email_thread_assignments_assignee
    ON email_thread_assignments (organization_id, assignee_email);

CREATE TABLE IF NOT EXISTS email_send_log (
    organization_id     UUID NOT NULL
                        REFERENCES organization (id) ON DELETE CASCADE
                        DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                  UUID NOT NULL DEFAULT gen_random_uuid(),
    account_id          UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    -- The member who pressed Send, or `rule:<id>` for a rule (§6.3).
    sent_by             TEXT NOT NULL CHECK (sent_by <> ''),
    channel             TEXT NOT NULL
                        CHECK (channel IN ('web', 'chat', 'whatsapp', 'rule', 'scheduled')),
    -- The local draft row. No foreign key on purpose: the log outlives the
    -- draft row, which the send removes. The row cascades with the mailbox
    -- through account_id.
    draft_id            UUID NULL,
    -- Set once, after the provider answers (§7 item 2).
    provider_message_id TEXT NULL,
    recipients_count    INTEGER NOT NULL DEFAULT 0 CHECK (recipients_count >= 0),
    -- For a rule send: the author of the rule (§6.3).
    payload             JSONB NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_email_send_log_account
    ON email_send_log (organization_id, account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS email_mailbox_member_prefs (
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    account_id       UUID NOT NULL REFERENCES email_accounts (id) ON DELETE CASCADE,
    member_email     TEXT NOT NULL CHECK (member_email = lower(member_email)),
    -- NULL follows the level: on for `send`, off for `read` only (§9).
    in_all_inboxes   BOOLEAN NULL,
    -- D-EM-28, for each member.
    keep_separate    BOOLEAN NOT NULL DEFAULT false,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, account_id, member_email)
);


-- ── 3. The guards ───────────────────────────────────────────────────────────
--
-- A grant or an assignment names a SHARED mailbox of its own organization,
-- or the write fails. So a grant can never open a private mailbox (§4.4).
-- SECURITY INVOKER: the read of email_accounts runs under the RLS of the
-- writer, so a writer bound to org A cannot see a mailbox of org B, and the
-- row fails as "not shared".
CREATE OR REPLACE FUNCTION email_shared_mailbox_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM email_accounts a
         WHERE a.id = NEW.account_id
           AND a.organization_id = NEW.organization_id
           AND a.shared_since IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            '%: mailbox % is not a shared mailbox of this organization',
            TG_TABLE_NAME, NEW.account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS email_mailbox_grants_shared_only ON email_mailbox_grants;
CREATE TRIGGER email_mailbox_grants_shared_only
    BEFORE INSERT OR UPDATE ON email_mailbox_grants
    FOR EACH ROW EXECUTE FUNCTION email_shared_mailbox_only();

DROP TRIGGER IF EXISTS email_thread_assignments_shared_only ON email_thread_assignments;
CREATE TRIGGER email_thread_assignments_shared_only
    BEFORE INSERT OR UPDATE ON email_thread_assignments
    FOR EACH ROW EXECUTE FUNCTION email_shared_mailbox_only();

-- The send log is append-only (§7 item 2). An UPDATE may only set
-- provider_message_id, and only once. The trigger binds every role, the
-- owner too, so it does not rest on the grants below.
CREATE OR REPLACE FUNCTION email_send_log_set_once() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF OLD.provider_message_id IS NOT NULL THEN
        RAISE EXCEPTION 'email_send_log: provider_message_id of % is already set', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.sent_by IS DISTINCT FROM OLD.sent_by
       OR NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.draft_id IS DISTINCT FROM OLD.draft_id
       OR NEW.recipients_count IS DISTINCT FROM OLD.recipients_count
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'email_send_log: only provider_message_id may change'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS email_send_log_append_only ON email_send_log;
CREATE TRIGGER email_send_log_append_only
    BEFORE UPDATE ON email_send_log
    FOR EACH ROW EXECUTE FUNCTION email_send_log_set_once();

DO $rls$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['email_mailbox_grants', 'email_thread_assignments',
                             'email_send_log', 'email_mailbox_member_prefs'] LOOP
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

-- The app role of saas_multitenancy_handover.md §H3 gets the send log by its
-- default privileges. It keeps SELECT and INSERT, and UPDATE of one column.
-- It loses DELETE, TRUNCATE and the UPDATE of every other column (§7).
REVOKE ALL ON TABLE email_send_log FROM PUBLIC;
DO $grants$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON TABLE email_send_log FROM %I', r);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'acb_app') THEN
        REVOKE ALL ON TABLE email_send_log FROM acb_app;
        GRANT SELECT, INSERT ON TABLE email_send_log TO acb_app;
        GRANT UPDATE (provider_message_id) ON TABLE email_send_log TO acb_app;
    END IF;
END
$grants$;

COMMENT ON TABLE email_mailbox_grants IS
    'WS-65 SM-1: who reads, sends from or manages a shared mailbox, in the D12 '
    'subject grammar (org, group:<slug>, email). shared_mailbox.md §4.4.';
COMMENT ON TABLE email_thread_assignments IS
    'WS-65 SM-1: the one member who answers a thread of a shared mailbox. '
    'shared_mailbox.md §5.2.';
COMMENT ON TABLE email_send_log IS
    'WS-65 SM-1: append-only. One row for each send from a shared mailbox, with '
    'the member who pressed Send. shared_mailbox.md §7.';
COMMENT ON TABLE email_mailbox_member_prefs IS
    'WS-65 SM-1: the All inboxes choices of one member for one shared mailbox. '
    'shared_mailbox.md §9.';


-- ── 4. `email:shared:manage` for `admin`, in every organization that exists ─
--
-- Q-SM-2: only a holder makes a mailbox shared and changes its grants. The
-- owner holds it through `*`. `manager` and `member` do not.
--
-- TWO ARMS, guarded on whether the tenancy column exists, in the shape of
-- 207 part 1 and 272 part 2, and for their reason: the generated phase adds
-- `org_role_permission.organization_id`, and the ladder does not replay it.
-- The tenant comes from the ROLE being granted, never from a GUC, because
-- this statement crosses every organization by design.
DO $grant$
DECLARE
    has_org_column boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'org_role_permission'
           AND column_name = 'organization_id'
    ) INTO has_org_column;

    IF has_org_column THEN
        INSERT INTO org_role_permission (role_id, permission, organization_id)
        SELECT r.id, 'email:shared:manage', r.organization_id
          FROM org_role r
         WHERE r.slug = 'admin'
        ON CONFLICT DO NOTHING;
    ELSE
        INSERT INTO org_role_permission (role_id, permission)
        SELECT r.id, 'email:shared:manage'
          FROM org_role r
         WHERE r.slug = 'admin'
        ON CONFLICT DO NOTHING;
    END IF;
END
$grant$;


-- ── 5. And in every organization made from now on ───────────────────────────
--
-- The function is 200's to own, so this file does not restate it. It reads
-- the live definition, adds one element to the admin array, and runs the
-- result, as 207 part 2 and 272 part 3 do. The anchor is `'admin:audit:read',`,
-- which only the admin array holds. The block raises unless the anchor occurs
-- exactly once, so a changed function body stops the deploy and does not
-- grant the capability to the wrong role.
DO $seed$
DECLARE
    src     text;
    out     text;
    anchor  constant text := '''admin:audit:read'',';
    hits    integer;
BEGIN
    SELECT pg_get_functiondef(p.oid) INTO src
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.proname = 'provision_org_roles' AND n.nspname = current_schema()
     LIMIT 1;

    IF src IS NULL THEN
        RAISE NOTICE '296: provision_org_roles absent, nothing to rewrite';
        RETURN;
    END IF;

    IF position('''email:shared:manage''' in src) > 0 THEN
        RAISE NOTICE '296: provision_org_roles already grants email:shared:manage';
        RETURN;
    END IF;

    hits := (length(src) - length(replace(src, anchor, ''))) / length(anchor);
    IF hits <> 1 THEN
        RAISE EXCEPTION
            '296: provision_org_roles holds % copies of the admin anchor, not 1. '
            '200 changed shape. Update 296 to match before deploying, or new '
            'organizations get email:shared:manage on the wrong role or on none.',
            hits;
    END IF;

    out := replace(src, anchor, anchor || ' ''email:shared:manage'',');
    EXECUTE out;
    RAISE NOTICE '296: provision_org_roles rewritten to grant email:shared:manage to admin';
END
$seed$;

COMMIT;
