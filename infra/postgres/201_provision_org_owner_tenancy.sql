-- ============================================================================
-- 201_provision_org_owner_tenancy.sql — H-104's SECOND head
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy.md §11 MT-1j · HANDOFF H-104.
--
-- ⚠️ **Migration 200 fixed one function. This is the same defect in the next
-- one, found by running provisioning against PRODUCTION and watching it fail
-- one statement further along.**
--
-- 200 taught `provision_org_roles` to carry `org_role_permission.
-- organization_id`. Provisioning then got past the roles and raised here:
--
--     null value in column "organization_id" of relation "user_role"
--     violates not-null constraint
--     CONTEXT: PL/pgSQL function provision_org_owner(uuid,text,text) line 54
--
-- `provision_org_owner`'s grant INSERT names `(user_id, role_id, assigned_by)`
-- and the tenancy phase made `user_role.organization_id` NOT NULL, exactly as
-- it did for `org_role_permission`.
--
-- ⚠️ **Why 200's test did not catch it, which is the part worth remembering.**
-- `test_org_provisioning_tenancy.py` added the NOT NULL column to ONE table —
-- the one 200 was about. So the fixture modelled a database production does not
-- have: production carries the column on SEVEN tables. A test that builds a
-- partial copy of the failing environment gives exactly as much confidence as
-- the copy deserves, which was none for this statement. The fixture now applies
-- the column to every table in the provisioning path, so a third head would
-- fail the suite rather than the deploy.
--
-- The seven, measured on production 2026-09-15: `app_user`, `org_membership`,
-- `org_role`, `org_role_permission`, `tenant_placement`,
-- `user_permission_override`, `user_role`. Of the ones provisioning writes,
-- `organization`/`tenant_placement`/`org_role` always named the column,
-- `app_user` already named it (180's INSERT carries it), `org_role_permission`
-- was 200, and `user_role` is this file. `user_permission_override` and
-- `org_membership` are not written by these functions.
--
-- ⚠️ **The same shape as 200, and for the same reasons.** The body is
-- 180's, transformed rather than retyped — the discipline that caught a
-- hand-copied role table being silently WRONG while 200 was being written. Only
-- the one INSERT differs, behind a column test, because both schemas exist: the
-- ladder has no such column and production does (H-104's other half, still
-- open).
--
-- Idempotent: `CREATE OR REPLACE FUNCTION`, and the `ON CONFLICT DO NOTHING`
-- is unchanged. Behaviour on a database WITHOUT the column is byte-identical to
-- 180's.
--
-- Depends on: 180_org_provisioning_create_only_guard.sql (the function this
-- replaces), 200_provision_org_roles_tenancy.sql (its sibling).
-- Fence: tests/unit/test_org_provisioning_tenancy.py, whose fixture now models
-- all seven columns.
-- ============================================================================

CREATE OR REPLACE FUNCTION provision_org_owner(
    p_org_id       UUID,
    p_email        TEXT,
    p_display_name TEXT DEFAULT NULL
)
RETURNS UUID
LANGUAGE plpgsql
AS $provision_org_owner$
DECLARE
    has_org_column BOOLEAN;
    v_email     TEXT;
    v_user_id   UUID;
    v_owner_rid UUID;
    v_holder    UUID;
    v_found     BOOLEAN;
BEGIN
    v_email := btrim(COALESCE(p_email, ''));
    IF p_org_id IS NULL OR v_email = '' THEN
        -- Generic P0001, deliberately UNtranslated: not caller-recoverable, so
        -- the seam re-raises it raw rather than mapping it to a typed refusal.
        RAISE EXCEPTION
            'provision_org_owner: organization_id and email are both required';
    END IF;

    -- app_user is unique on lower(email) GLOBALLY (162), so one address cannot
    -- be a member of two organizations. Adopting it would MOVE a person between
    -- tenants — S1-1's write leak — and attaching this organization's `owner`
    -- role to a row that stays in the other one would be a cross-tenant grant.
    -- Refusing is the only answer that is neither.
    SELECT u.organization_id INTO v_holder FROM app_user u
     WHERE lower(u.email) = lower(v_email);
    v_found := FOUND;
    IF v_found AND v_holder IS NOT NULL AND v_holder <> p_org_id THEN
        -- Dedicated SQLSTATE 'P1001' (slice 7): the seam maps this to
        -- OwnerBelongsElsewhere on the code ALONE. Message unchanged from 179,
        -- so test_an_address_already_in_another_tenant_is_refused still matches.
        RAISE EXCEPTION
            'provision_org_owner: % already belongs to organization % — '
            'refusing to move a member between tenants', v_email, v_holder
            USING ERRCODE = 'P1001';
    END IF;

    -- ── The create-only guard (slice 7) ────────────────────────────────────
    -- The target org must not already be OWNED by a DIFFERENT address. Mirrors
    -- customer_console store.org_owned_by_other: keying on OWNERSHIP resolves
    -- all three cases with one read — a slug with no owner is not a conflict (it
    -- may be completed, the crash-resume shape), the SAME owner is not a
    -- conflict (idempotent retry), a DIFFERENT owner is. Case-insensitive on
    -- lower(email) (the 162 idiom), so `Carol` cannot slip past `carol`.
    IF EXISTS (
        SELECT 1
          FROM user_role ur
          JOIN org_role r ON r.id = ur.role_id
                         AND r.organization_id = p_org_id
                         AND r.slug = 'owner'
          JOIN app_user au ON au.id = ur.user_id
         WHERE lower(au.email) <> lower(v_email)
    ) THEN
        -- Dedicated SQLSTATE 'P1002' (slice 7), DISTINCT from P1001 above so a
        -- caller can tell "slug already owned by another" from "this email
        -- lives in another tenant" without parsing prose.
        RAISE EXCEPTION
            'provision_org_owner: organization % is already owned by another '
            'address — refusing to add a co-owner (create-only)', p_org_id
            USING ERRCODE = 'P1002';
    END IF;

    -- `(lower(email))`, not `(email)`: 162 dropped app_user_email_key, and an
    -- ON CONFLICT target must name an index that EXISTS or Postgres raises
    -- 42P10 at PLAN time — which takes out the fresh-insert path too, not just
    -- the conflict one (MT-1j slice 6, measured 2026-08-19).
    INSERT INTO app_user (email, display_name, role, status,
                          organization_id, joined_at)
    VALUES (v_email,
            COALESCE(NULLIF(btrim(COALESCE(p_display_name, '')), ''), v_email),
            'executive', 'active', p_org_id, now())
    ON CONFLICT (lower(email)) DO UPDATE
        SET status          = 'active',
            organization_id = COALESCE(app_user.organization_id,
                                       EXCLUDED.organization_id),
            joined_at       = COALESCE(app_user.joined_at, EXCLUDED.joined_at)
    RETURNING id INTO v_user_id;

    SELECT r.id INTO v_owner_rid FROM org_role r
     WHERE r.organization_id = p_org_id AND r.slug = 'owner';
    IF v_owner_rid IS NULL THEN
        -- A member row with no grant reads as "provisioned" and holds nothing.
        -- Generic P0001, deliberately UNtranslated: the caller must run
        -- provision_org_roles first (provision_organization always does), so
        -- this is a programming error, not a signup outcome.
        RAISE EXCEPTION
            'provision_org_owner: organization % has no owner role — call '
            'provision_org_roles(organization_id) first', p_org_id;
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'user_role'
           AND column_name = 'organization_id'
    ) INTO has_org_column;

    -- ⚠️ The SECOND head of H-104, found on production 2026-09-15 after
    -- migration 200 fixed the first. `user_role.organization_id` is NOT NULL
    -- there too, and this INSERT never named it — so provisioning got one
    -- statement further and raised again. The two arms are static and only the
    -- tenancy column differs; PL/pgSQL plans a branch on first execution, so
    -- the arm naming a column this database lacks is never planned.
    IF has_org_column THEN
        INSERT INTO user_role (user_id, role_id, assigned_by, organization_id)
        VALUES (v_user_id, v_owner_rid, 'provision_organization', p_org_id)
        ON CONFLICT DO NOTHING;
    ELSE
        INSERT INTO user_role (user_id, role_id, assigned_by)
        VALUES (v_user_id, v_owner_rid, 'provision_organization')
        ON CONFLICT DO NOTHING;
    END IF;

    RETURN v_user_id;
END;
$provision_org_owner$;