-- ============================================================================
-- 180_org_provisioning_create_only_guard.sql — the tenant-plane create-only
-- guard for provision_org_owner (MT-1j slice 7)
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy.md §11 MT-1j slice 7.
--
-- FORWARD-ONLY (R6). Migration 179 is left BYTE-FOR-BYTE untouched; this file
-- CREATE OR REPLACEs the WHOLE provision_org_owner body. The deploy applies the
-- ladder low→high before restarting services, so the last-replayed body wins
-- and old code always meets the new schema — and there is no schema change here
-- at all, only a function redefinition, which is as expand-only as it gets.
--
-- WHAT WAS WRONG (measured on origin/main). provision_org_owner
-- (179:196-262) guards exactly ONE conflict: an owner_email whose app_user row
-- already belongs to a DIFFERENT organization (the cross-tenant guard,
-- 179:225-229). It does NOT refuse a FRESH email (no app_user row, so
-- v_found = FALSE) claiming an EXISTING owned slug: provision_organization
-- reuses the org via INSERT … ON CONFLICT (slug) DO NOTHING (179:307), the
-- fresh email passes the cross-tenant check, and provision_org_owner writes it
-- an `owner` role on the existing org. With `acme` owned by `alice`, a fresh
-- `carol@c.com` provisioning slug `acme` → 200, carol is now a CO-OWNER of
-- alice's org. This is byte-for-byte the P0 slice 1 caught and fixed on the
-- CONSOLE plane (customer_console store.org_owned_by_other, main.py:794); the
-- tenant plane had no equivalent. Latent today (provision_organization /
-- provision_local_organization have no production caller), but CP-2c slice 2
-- makes this seam the FIRST writer for a USER-SUPPLIED slug from an
-- unauthenticated signup form, so this guard is a hard prerequisite of it.
--
-- TWO CHANGES, both in the body below:
--
--   1. The create-only guard (NEW). Refuse when the target org already holds an
--      `owner` role for an email OTHER THAN p_email — case-insensitive on
--      lower(email) (the 162 idiom, so `Carol` cannot slip past `carol`). A
--      no-owner org still completes (the crash-after-org-before-owner resume
--      shape); the SAME owner stays idempotent. This mirrors
--      store.org_owned_by_other exactly: the predicate keys on OWNERSHIP, which
--      resolves all three cases with one read.
--
--   2. DEDICATED, DISTINCT SQLSTATEs on the two caller-recoverable refusals, so
--      the seam (acb_common.provisioning) can classify on the CODE ALONE, never
--      on Postgres prose (the gap provisioning.py:115-121 left open):
--        * email-belongs-elsewhere → SQLSTATE 'P1001'  (re-tagged from the
--          generic P0001 179 shared it with — legal here, 180 carries the whole
--          body; the human-readable message is unchanged so 179's fences hold);
--        * slug-owned-by-another    → SQLSTATE 'P1002'  (the new guard).
--      P0/P1 are custom classes: Postgres defines only class P0 (PL/pgSQL
--      errors — P0001 raise_exception etc.); class P1 is unassigned and free.
--      NOT 23505 (unique_violation): the genuine app_user / user_role unique
--      constraints in THIS function also raise 23505, and a 23505→typed mapping
--      would misclassify a real constraint hit as SlugOwnedByAnother. The
--      blank-arg raise and the no-owner-role raise stay GENERIC P0001 and pass
--      through the seam RAW — they are not caller-recoverable conditions.
--
-- Idempotent (CREATE OR REPLACE). Depends on: 179 (the function this replaces),
-- 130 (org_role / user_role / app_user), 162 (the lower(email) unique idiom).
-- Fence: tests/unit/test_org_provisioning.py (R8, tenant ladder), slice 7.
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

    INSERT INTO user_role (user_id, role_id, assigned_by)
    VALUES (v_user_id, v_owner_rid, 'provision_organization')
    ON CONFLICT DO NOTHING;

    RETURN v_user_id;
END;
$provision_org_owner$;

COMMENT ON FUNCTION provision_org_owner(UUID, TEXT, TEXT) IS
    'MT-1j slice 7: make a NAMED address the owner of an EXPLICIT organization, '
    'CREATE-ONLY. Refuses a slug already owned by another address (SQLSTATE '
    'P1002) and an email that belongs to another tenant (SQLSTATE P1001); a '
    'no-owner org completes and the same owner is idempotent. Supersedes 179''s '
    'body forward-only (R6).';
