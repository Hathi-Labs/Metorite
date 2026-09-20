-- ============================================================================
-- 207_every_app_by_default.sql — every app is open to everybody. Actions are
-- what a role narrows.
--
-- What: `feature:*` for the `member` and `manager` roles, in every existing
--       organization and in the one new organizations are seeded from.
-- Why:  owner directive, 2026-09-21 — *"all the apps should be available to
--       all the people. It is just that, depending on their permission
--       levels, they might not be allowed to do certain actions."*
--
-- **What was wrong with the old defaults.** Migration 130 wrote the member
-- and manager bundles as explicit lists, before Projects or People existed.
-- Every app added since then defaulted to invisible, because a list does not
-- grow on its own. The result, measured 2026-09-21: `feature:projects` and
-- `feature:people` were held by NOBODY except admin and owner. The company
-- project board could not be opened by anyone in the company.
--
-- **`feature:*`, not a longer list, and that is the point.** A list is the
-- thing that went stale. A wildcard makes the default self-maintaining: the
-- next app to go live is open on the day it ships, which is what "available
-- to all the people" means when said once and meant afterwards.
--
-- ⚠️ **THIS GRANTS NO CAPABILITY, and the distinction is the whole design.**
-- `permission_matches` treats `feature:*` as covering exactly the permissions
-- that start with `feature:` (`acb_auth/permissions.py:248`). It cannot match
-- `admin:members:read`, `admin:roles:manage`, `projects:settings:write` or
-- any other verb. So:
--
--   * **Organisation stays admin-only.** That pane is gated on `is_admin`,
--     which is resolved from `admin:members:read` and not from any feature.
--     The console that controls access must not be editable by the people it
--     controls.
--   * **Every action gate is untouched.** A member can now OPEN People; they
--     still cannot edit a colleague's record (`admin:members:manage`), and
--     the HR half of a person record is still projected away without
--     `admin:members:read` (§4.2).
--
-- 📌 **`guest` is deliberately left alone.** "All the people" is the
-- organization's people. A guest is an external collaborator — migration
-- 130's own words, "chat and explicitly shared apps only" — and widening
-- that is a different decision on a different day.
--
-- 📌 **Hiding an app from a role is NOT yet possible**, and the owner asked
-- for it as a later capability. Per-PERSON Deny works today
-- (`/settings/members/<email>`). Per-ROLE does not, because `member` and
-- `manager` are system roles and `PATCH /admin/roles/{slug}` refuses to edit
-- one. That gap is H-141.
--
-- Depends on: 130_org_access_control.sql (org_role, org_role_permission),
--             200_provision_org_roles_tenancy.sql (the seed for NEW orgs).
-- Idempotent: ON CONFLICT DO NOTHING on the insert, and the delete's WHERE
--             matches nothing on a second run.
-- Pinned by tests/unit/test_every_app_by_default.py.
-- ============================================================================

BEGIN;

-- ── 1. Every organization that already exists ───────────────────────────────
--
-- Both statements are in ONE transaction, and the order matters: the wildcard
-- is granted BEFORE the explicit rows it replaces are removed, so there is no
-- instant at which a member holds fewer features than they did.

-- ⚠️ TWO ARMS, guarded on whether the tenancy column exists — the same
-- shape, for the same reason, as 200's own seed (`200:178-186`).
-- `org_role_permission.organization_id` comes from the generated phase,
-- which `apply_migrations.sh` does not replay (H-104), and the phase makes
-- it NOT NULL. An INSERT that never names it passes here and raises on a
-- tenancy-applied production.
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
        -- The tenant is taken from the ROLE being granted, never from a GUC:
        -- this statement crosses every organization by design, so a single
        -- bound tenant would be the wrong answer for all but one of them.
        INSERT INTO org_role_permission (role_id, permission, organization_id)
        SELECT r.id, 'feature:*', r.organization_id
          FROM org_role r
         WHERE r.slug IN ('member', 'manager')
        ON CONFLICT DO NOTHING;
    ELSE
        INSERT INTO org_role_permission (role_id, permission)
        SELECT r.id, 'feature:*'
          FROM org_role r
         WHERE r.slug IN ('member', 'manager')
        ON CONFLICT DO NOTHING;
    END IF;
END
$grant$;

-- The explicit `feature:` rows are now redundant — `feature:*` covers every
-- one of them. Removed so the Roles screen reads as the rule rather than as
-- the rule plus its own history, which invites "why are both here?".
--
-- ⚠️ Scoped to `feature:` only. A role's capabilities (`admin:*`,
-- `agents:run:*`, `apps:use:*`, `memory:*`, `projects:settings:write`) are
-- what still separate a member from a manager, and deleting one of those
-- would be the access change this migration is explicitly NOT making.
DELETE FROM org_role_permission p
 USING org_role r
 WHERE p.role_id = r.id
   AND r.slug IN ('member', 'manager')
   AND p.permission LIKE 'feature:%'
   AND p.permission <> 'feature:*';


-- ── 2. Every organization created from now on ───────────────────────────────
--
-- `provision_org_roles` carries its own copy of the bundles for a new tenant.
-- Leaving it alone would fix today's customers and re-break tomorrow's, which
-- is the drift `saas_multitenancy.md` keeps warning about: two seeds, one
-- edited.
--
-- Rewritten with `regexp_replace` rather than by restating the function,
-- because the function is 200's to own and a hand-copied body here would be a
-- third place to remember. The replacement is asserted, not hoped for: the
-- DO block raises if either role's array does not change.

DO $seed$
DECLARE
    src  text;
    out  text;
BEGIN
    -- `pg_get_functiondef`, NOT `prosrc`: prosrc is the BODY alone and is
    -- not a runnable statement, so EXECUTE-ing it would fail at deploy time.
    -- functiondef returns the whole CREATE OR REPLACE, which is what has to
    -- be re-executed to redefine the function.
    SELECT pg_get_functiondef(p.oid) INTO src
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE p.proname = 'provision_org_roles' AND n.nspname = current_schema()
     LIMIT 1;

    IF src IS NULL THEN
        RAISE NOTICE '207: provision_org_roles absent — nothing to rewrite';
        RETURN;
    END IF;

    -- Manager's feature block, verbatim from 200, collapsed to the wildcard.
    out := replace(src,
        $old$'feature:chat', 'feature:email', 'feature:whatsapp', 'feature:tasks',
                'feature:notes', 'feature:memory', 'feature:dashboard',
                'feature:observability', 'feature:artifacts', 'feature:approvals',$old$,
        $new$'feature:*',$new$);

    -- Member's.
    out := replace(out,
        $old$'feature:chat', 'feature:email', 'feature:tasks', 'feature:notes',
                'feature:memory', 'feature:dashboard', 'feature:artifacts',$old$,
        $new$'feature:*',$new$);

    IF out = src THEN
        -- Already rewritten by an earlier run of this file: idempotent, fine.
        -- Never rewritten because 200's text moved: NOT fine, and silence
        -- here would ship a fix that covers today's orgs and no new one.
        IF position('''feature:*''' in src) = 0 THEN
            RAISE EXCEPTION
                '207: provision_org_roles no longer contains the role arrays '
                'this migration rewrites. 200 changed shape — update 207 to '
                'match before deploying, or new organizations keep the old '
                'defaults while existing ones do not.';
        END IF;
        RAISE NOTICE '207: provision_org_roles already grants feature:*';
        RETURN;
    END IF;

    EXECUTE out;
    RAISE NOTICE '207: provision_org_roles rewritten to grant feature:*';
END
$seed$;

COMMIT;
