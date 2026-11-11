-- ============================================================================
-- 412_studio_publish_admin.sql — the seed of `studio:publish` onto `admin`.
--
-- What: one capability seed, in two parts.
--       Part 1 gives `studio:publish` to the `admin` role of every
--       organization that exists.
--       Part 2 adds the same element to the admin array of
--       provision_org_roles, so a new organization has it too.
--       `owner` holds it through `*`. `manager`, `member`, `guest` and
--       `agent_service` get no row. `agent_service` resolves to `*` in
--       acb_auth.access.SERVICE_ACCESS, as 296 says for its own seed.
-- Why:  WS-STU J1-2 (Metorite task 1707), spec
--       project-docs/specs/ai_studio.md §9 item 3 and §14 J1, and the owner's
--       answer STU-Q4 (D180): "Owners and admins publish and can grant
--       publish." J1-1 added the capability to CAPABILITIES only. Before this
--       file, only the owner held it. The owner allowed this write to the role
--       rows of each live organization on 2026-11-10 (task 1707).
-- What it gates: this file adds no reader. Before J2, the one reader is the
--       org and group install of WS-PKG PKG-8b
--       (gateway/routes/studio/packages.py, install_package). The fence that
--       keeps it the one reader is
--       test_studio_route_gate.py::test_the_share_verb_is_read_by_the_install_only.
-- Depends on: 130_org_access_control.sql (organization, org_role,
--       org_role_permission), 200_provision_org_roles_tenancy.sql (the
--       function body that part 2 rewrites), and 272 and 296 (they added
--       elements after the same anchor).
--
-- Expand only (R6): one INSERT of rows that ON CONFLICT DO NOTHING skips, and
-- one function body with one more array element. No table, no column, no
-- rename and no drop. Old code reads the new row as one more grant.
-- Idempotent per infra/postgres/README.md: a second run inserts no row, and
-- part 2 does nothing when the element is already in the body.
-- Tenant scope (R5): no new table. The tenant of each row comes from the ROLE
-- that gets the grant, never from request input.
-- The number is taken at build time (R1). On 2026-11-11 origin/main held 404.
-- The open branches and worktrees held 396, 398, 400 and 405 to 411: 409 on
-- stu-c8-roles-mt-47d4c51f and stu-g17-save-history, 410 on
-- crm-m3b-mt-4fb4caf2, and 411 in the worktrees of C8 and J3a. This file
-- took 410 first and moved to 412 the same day. Check it again at merge
-- (scripts/premerge.sh). The tests find this file by CONTENT, never by number.
-- Fence: tests/unit/test_studio_publish_seed_r8.py (R8).
-- ============================================================================

BEGIN;

-- ── 1. `studio:publish` for `admin`, in every organization that exists ──────
--
-- TWO ARMS, guarded on whether the tenancy column exists, in the shape of
-- 207 part 1, 272 part 2 and 296 part 4. The generated phase adds
-- `org_role_permission.organization_id`, and the ladder does not replay it.
--
-- The tenancy arm binds each organization in turn, as 221 does. The runner
-- of the box is a superuser and skips row security. A runner that owns the
-- table and is not a superuser meets FORCE ROW LEVEL SECURITY, and its WITH
-- CHECK then needs the bound tenant. The bind is local to this transaction.
-- `organization` and `org_role` have no policy, so the loop reads both.
DO $grant$
DECLARE
    has_org_column boolean;
    org            uuid;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'org_role_permission'
           AND column_name = 'organization_id'
    ) INTO has_org_column;

    IF NOT has_org_column THEN
        INSERT INTO org_role_permission (role_id, permission)
        SELECT r.id, 'studio:publish'
          FROM org_role r
         WHERE r.slug = 'admin'
        ON CONFLICT DO NOTHING;
        RETURN;
    END IF;

    FOR org IN SELECT o.id FROM organization o LOOP
        PERFORM set_config('app.tenant_id', org::text, true);
        EXECUTE $q$
            INSERT INTO org_role_permission (role_id, permission, organization_id)
            SELECT r.id, 'studio:publish', r.organization_id
              FROM org_role r
             WHERE r.slug = 'admin'
               AND r.organization_id = $1
            ON CONFLICT DO NOTHING
        $q$ USING org;
    END LOOP;
END
$grant$;


-- ── 2. And in every organization made from now on ───────────────────────────
--
-- The function is 200's to own, so this file does not restate it. It reads
-- the live definition, adds one element to the admin array, and runs the
-- result, as 272 part 3 and 296 part 5 do. The anchor is `'admin:audit:read',`,
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
        RAISE NOTICE '412: provision_org_roles absent, nothing to rewrite';
        RETURN;
    END IF;

    IF position('''studio:publish''' in src) > 0 THEN
        RAISE NOTICE '412: provision_org_roles already grants studio:publish';
        RETURN;
    END IF;

    hits := (length(src) - length(replace(src, anchor, ''))) / length(anchor);
    IF hits <> 1 THEN
        RAISE EXCEPTION
            '412: provision_org_roles holds % copies of the admin anchor, not 1. '
            '200 changed shape. Update 412 to match before deploying, or new '
            'organizations get studio:publish on the wrong role or on none.',
            hits;
    END IF;

    out := replace(src, anchor, anchor || ' ''studio:publish'',');
    EXECUTE out;
    RAISE NOTICE '412: provision_org_roles rewritten to grant studio:publish to admin';
END
$seed$;

COMMIT;
