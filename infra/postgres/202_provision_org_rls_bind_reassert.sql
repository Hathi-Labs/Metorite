-- ============================================================================
-- 202_provision_org_rls_bind_reassert.sql — 185's body is NOT what production runs
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy.md §11 MT-1j · HANDOFF H-104 ·
-- WS-29 H6 (the RLS bind this re-asserts).
--
-- ⚠️ **Migration 185 is in production's ledger, and its fix is NOT in
-- production's function.** Measured 2026-09-15:
--
--     SELECT position('set_config' in prosrc) > 0 FROM pg_proc
--      WHERE proname = 'provision_organization';   -->  false
--
-- The live body is **179's**. The 7-argument signature is 185's, so the file did
-- apply at some point — then something replaced the body with an older one and
-- left the ledger row standing. `infra/postgres/schema.generated.sql` carries
-- that same pre-185 body, which is the shape a snapshot restore would leave.
--
-- ⚠️ **The ledger cannot repair this, and that is the general lesson.**
-- `apply_migrations.sh` SKIPS a file whose checksum matches the ledger, so
-- editing 185 reaches nothing and re-running the deploy reaches nothing. For a
-- `CREATE OR REPLACE` migration the ledger records *that we ran it*, never *that
-- the object still says what it said*. Any drift after the fact is therefore
-- both invisible and un-repairable in place. Forward-only (R6) is the only way
-- back: a NEW number, re-asserting the body.
--
-- ⚠️ **What it cost.** Provisioning a Console-born organization failed as the
-- non-privileged `acb_app` role with:
--
--     new row violates row-level security policy for table "org_role_permission"
--
-- while the SAME call succeeded when run as `postgres`. That is the whole trap:
-- a superuser bypasses FORCE ROW LEVEL SECURITY, so a hand-run SQL check of the
-- provisioning path proves nothing about the path the application takes. It was
-- found by running the real seam — `scripts/bootstrap_placed_orgs.py` — not by
-- running the function.
--
-- 185's header explains WHY the bind exists and what it must not become; that
-- reasoning is not restated here, per the no-mirrors rule. This file is 185's
-- statements, extracted from 185's own text rather than retyped — the same
-- discipline as 200 and 201, and for the same reason: a hand-copied role table
-- was silently WRONG once already.
--
-- Idempotent (CREATE OR REPLACE). No schema change, a function redefinition
-- only. Depends on: 185_provision_org_rls_bind.sql (the body this restores),
-- 200 and 201 (the tenancy-aware sub-functions it PERFORMs).
-- Fence: tests/unit/test_org_provisioning_rls.py — provisioning as a role that
-- FORCE RLS actually applies to, which no earlier suite did.
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
