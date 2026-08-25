-- ============================================================================
-- 185_provision_org_rls_bind.sql — bind app.tenant_id inside provision_organization
-- so the create act's FORCE-RLS'd writes are allowed under phase-4 (WS-29 H6)
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy_handover.md §H6 PRE-FLIP
-- RLS-BINDING CHECKLIST item (b)1 (new-org provisioning writes) ·
-- saas_multitenancy.md §11 MT-1j · D43-A/D43-C.
--
-- FORWARD-ONLY (R6). Migrations 179 and 180 are left BYTE-FOR-BYTE untouched;
-- this file CREATE OR REPLACEs the WHOLE provision_organization body. The deploy
-- applies the ladder low→high before restarting services, so the last-replayed
-- body wins and old code always meets the new schema — and there is no schema
-- change here at all, only a function redefinition, which is as expand-only as it
-- gets. Idempotent (CREATE OR REPLACE; the ladder is applied twice in test).
--
-- WHAT WAS WRONG (measured on the phase-4-promoted catalog as the non-priv
-- acb_app role). provision_organization creates the organization row and its
-- tenant_placement (both RLS-EXEMPT — no policy in generated/04_policies.sql, so
-- they land on an UNBOUND session), then PERFORMs provision_org_roles and
-- provision_org_owner. But three of the rows those two write are into FORCE-RLS'd
-- tables whose policy is `organization_id = current_setting('app.tenant_id',
-- true)::uuid`:
--
--   * org_role_permission (provision_org_roles, 179:173-175) — org_id comes from
--     the phase-1 DEFAULT current_setting('app.tenant_id', true)::uuid;
--   * app_user           (provision_org_owner, 180:129-139) — org_id = p_org_id;
--   * user_role          (provision_org_owner, 180:153-155) — org_id from the
--     same phase-1 DEFAULT.
--
-- On a session with no app.tenant_id bound the GUC is NULL, so the WITH CHECK
-- (and the phase-3 NOT NULL on the DEFAULT-fed columns) refuses the write. The
-- FIRST refusal reached is org_role_permission — provision_org_roles runs BEFORE
-- provision_org_owner — so the create act aborts and **provisioning a customer
-- post-flip fails**. This is LAUNCH-CRITICAL: the owner provisions customers
-- continuously, including after the H3 flip.
--
-- Note org_role itself is RLS-EXEMPT (no policy), so its INSERT lands unbound;
-- the forced sibling is org_role_permission, which is why the create act gets
-- part-way in before it fails. Every table listed above is EITHER stamped with
-- p_org_id explicitly OR fed by the phase-1 DEFAULT — both resolve to the same
-- org once the GUC below is set.
--
-- THE FIX (SET LOCAL, not SECURITY DEFINER). After the organization row is
-- created and its id (v_org_id) is known — the org now EXISTS, on the exempt
-- table, before any forced write — bind the GUC to it with
-- `PERFORM set_config('app.tenant_id', v_org_id::text, true)`. is_local = true is
-- SET LOCAL: transaction-scoped, so it propagates into the PERFORMed
-- provision_org_roles / provision_org_owner (same statement, same transaction)
-- and resets at transaction end — no leak across provisions, and a reused pooled
-- connection is re-bound every call because provision_organization always sets it
-- before writing. This is the ONE GUC seam the app already uses
-- (tenant_session()'s set_config('app.tenant_id', …, true)), stated in SQL for
-- the create path that has no request tenant to bind — never a second GUC path.
--
--   * WHY provision_organization, NOT provision_org_owner. The org id is created
--     HERE, and the first forced write in the act (org_role_permission, via
--     provision_org_roles) runs BEFORE provision_org_owner — a bind inside
--     provision_org_owner would come too late to save the roles seed. One
--     set_config at the orchestrator's single point, right after v_org_id is
--     known, covers all three forced tables through the transaction-scoped GUC.
--   * WHY NOT SECURITY DEFINER. The function stays SECURITY INVOKER (the
--     default), so there is no privilege escalation and no DEFINER function
--     outliving its reason — the exact hole 179's header warned against
--     ("REVISIT AT H3"). The bind makes the caller's own writes satisfy the
--     policy; it does not bypass it. A cross-tenant write is still refused,
--     because the GUC is the org THIS act just created, not an attacker's.
--
-- PRESERVED, because the SET LOCAL is purely additive to 179's body:
--   * 180's create-only guard (a fresh email claiming an owned slug is refused,
--     SQLSTATE P1002; a no-owner org completes; the same owner is idempotent) —
--     provision_org_owner (180's body) is untouched and still PERFORMed;
--   * D43-A's seeding doctrine + parity, D43's idempotency-on-slug, and the
--     tier/target/region CHECK on tenant_placement (159) — none restated here.
--
-- Depends on: 179 (the function this replaces), 180 (the create-only
-- provision_org_owner body it PERFORMs), generated/{01,03,04} (the DEFAULT,
-- the NOT NULL, the FORCE-RLS policies this bind satisfies).
-- Fence: tests/unit/test_h3_rls_promotion_rehearsal.py::
-- TestProvisionOrgBindUnderForceRls (R8, phase-4-promoted catalog as the
-- non-priv acb_app role — GREEN bound-by-the-function / RED no-set_config
-- mutation), R7 name `provision-under-force-rls`; plus the existing
-- tests/unit/test_org_provisioning.py suite (R8, tenant ladder).
--
-- 🔴 Building and R8-testing this DARK is AGENT-SAFE (D43-C); EXECUTING a real
-- customer provision, the H3 phase-4 promotion, and the IDENTITY_CUTOVER flip all
-- stay OWNER-GATE.
-- ============================================================================

CREATE OR REPLACE FUNCTION provision_organization(
    p_slug         TEXT,
    p_display_name TEXT,
    p_owner_email  TEXT DEFAULT NULL,
    p_domain       TEXT DEFAULT NULL,
    p_tier         TEXT DEFAULT 'pool',
    p_target       TEXT DEFAULT 'primary',
    p_region       TEXT DEFAULT 'ap-south-1'
)
RETURNS UUID
LANGUAGE plpgsql
AS $provision_organization$
DECLARE
    v_slug   TEXT;
    v_org_id UUID;
BEGIN
    v_slug := btrim(COALESCE(p_slug, ''));
    IF v_slug = '' THEN
        RAISE EXCEPTION 'provision_organization: slug is required';
    END IF;

    INSERT INTO organization (slug, display_name, domain)
    VALUES (v_slug,
            COALESCE(NULLIF(btrim(COALESCE(p_display_name, '')), ''), v_slug),
            p_domain)
    ON CONFLICT (slug) DO NOTHING;

    SELECT o.id INTO v_org_id FROM organization o WHERE o.slug = v_slug;

    -- ── The RLS bind (185, WS-29 H6) ────────────────────────────────────────
    -- The organization row now EXISTS (exempt table, written above unbound), so
    -- there is finally a tenant to bind. SET LOCAL app.tenant_id to it BEFORE the
    -- downstream FORCE-RLS'd writes — org_role_permission (provision_org_roles),
    -- app_user + user_role (provision_org_owner) — so their WITH CHECK
    -- (`organization_id = current_setting('app.tenant_id', true)::uuid`) is
    -- satisfied and the phase-1 DEFAULT stamps the right org. is_local = true is
    -- transaction-scoped: it reaches the PERFORMed sub-functions and resets at
    -- txn end. SECURITY INVOKER is preserved — this makes the caller's OWN writes
    -- match the policy, it does not bypass it.
    PERFORM set_config('app.tenant_id', v_org_id::text, true);

    -- The tier/target/region CHECK lives on the table (159:45-50) and is NOT
    -- restated here: a second copy of a constraint is a second thing to drift.
    INSERT INTO tenant_placement (organization_id, tier, target, region)
    VALUES (v_org_id, p_tier, p_target, p_region)
    ON CONFLICT (organization_id) DO NOTHING;

    PERFORM provision_org_roles(v_org_id);

    -- Optional: the Console-side half (slice 4) may create the organization
    -- before it knows the owner. Roles and placement still land — an
    -- organization with roles and no owner is recoverable; one with neither is
    -- the 2026-07-30 lockout shape, where no owner means no inviter.
    IF btrim(COALESCE(p_owner_email, '')) <> '' THEN
        PERFORM provision_org_owner(v_org_id, p_owner_email);
    END IF;

    RETURN v_org_id;
END;
$provision_organization$;

COMMENT ON FUNCTION provision_organization(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) IS
    'MT-1j slice 3 + WS-29 H6 RLS-bind: the one idempotent provisioning act — '
    'organization + tenant_placement + system roles + (optionally) a named '
    'owner, keyed on the slug, atomic within the calling statement. Binds '
    'app.tenant_id (SET LOCAL) to the org it just created before the FORCE-RLS''d '
    'roles/owner writes, so provisioning succeeds under phase-4 RLS. Supersedes '
    '179''s body forward-only (R6); SECURITY INVOKER preserved.';
