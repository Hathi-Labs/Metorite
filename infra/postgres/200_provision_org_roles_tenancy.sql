-- ============================================================================
-- 200_provision_org_roles_tenancy.sql — provisioning works on a tenancy DB
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy.md §11 MT-1j · HANDOFF H-104.
--
-- ⚠️ **PROVISIONING A SECOND ORGANIZATION ON PRODUCTION IS BROKEN, AND THIS IS
-- THE FIX.** M1 is *a second org can exist safely*. Today a second org cannot
-- be CREATED there at all.
--
-- `provision_org_roles` (179) seeds the six system roles and their grants, and
-- its grant INSERT names only `(role_id, permission)`:
--
--     INSERT INTO org_role_permission (role_id, permission)
--     SELECT rid, p FROM unnest(spec.permissions) AS p
--
-- The tenancy phase added `org_role_permission.organization_id` and made it NOT
-- NULL (`infra/postgres/generated/03_constraints.sql:1029`). So on any database
-- where that phase has run — which is production — the seed raises:
--
--     null value in column "organization_id" of relation
--     "org_role_permission" violates not-null constraint
--
-- `provision_org_roles` is what a NEW organization is built from, so EVERY
-- create fails: self-serve signup, the operator path, and CP-2i's bootstrap
-- sweep alike.
--
-- ⚠️ **It stayed invisible because the two databases DISAGREE.** The generated
-- tenancy files live in a SUBDIRECTORY, and `scripts/apply_migrations.sh` globs
-- `[0-9][0-9]*_*.sql` in `infra/postgres` only — so the ladder never replays
-- them. A fresh developer database and CI's replay have no such column, and
-- production does. `tests/unit/test_org_provisioning.py` therefore passes on a
-- schema production does not have, which is why nothing caught this until
-- migration 196 called the seed for real and the deploy failed on that line.
--
-- ⚠️ **The column test is the fix, not a workaround.** The one statement has to
-- run correctly on BOTH schemas, because both exist and neither is going away
-- this week. 196 already established this exact idiom for the same table and
-- the same reason, and recorded the wider defect rather than fixing it quietly
-- — this migration is that fix, arriving with its own test.
--
-- PL/pgSQL plans a statement on first execution of its branch, so the arm that
-- names a column this database does not have is never planned here. That is
-- what lets both INSERTs be static rather than dynamic strings.
--
-- ⚠️ **It does NOT put the generated files on the ladder.** That is the real
-- repair for the divergence and it is a bigger act with its own blast radius —
-- H-104's other half, still open. This makes provisioning work on both shapes
-- meanwhile, which is what unblocks M1.
--
-- Idempotent: `CREATE OR REPLACE FUNCTION`, and the seed's own
-- `ON CONFLICT DO NOTHING` is unchanged. The function's behaviour on a database
-- WITHOUT the column is byte-identical to 179's.
--
-- Depends on: 179_org_provisioning.sql (the function this replaces).
-- Fence: tests/unit/test_org_provisioning_tenancy.py (R8, and it applies the
-- NOT NULL column itself so it tests the PRODUCTION shape).
-- ============================================================================

CREATE OR REPLACE FUNCTION provision_org_roles(p_org_id UUID)
RETURNS void
LANGUAGE plpgsql
AS $provision_org_roles$
DECLARE
    spec RECORD;
    rid  UUID;
    has_org_column BOOLEAN;
BEGIN
    IF p_org_id IS NULL THEN
        RAISE EXCEPTION 'provision_org_roles: organization_id is required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM organization o WHERE o.id = p_org_id) THEN
        -- Fail closed. Seeding against a dangling id would either be rejected
        -- by the FK several rows in, leaving a half-seeded organization, or
        -- succeed against a typo nobody ever looks at again.
        RAISE EXCEPTION 'provision_org_roles: no organization with id %', p_org_id;
    END IF;

    -- ⚠️ Read ONCE, outside the loop: six roles would otherwise ask the
    -- catalog six times for an answer that cannot change inside one statement.
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'org_role_permission'
           AND column_name = 'organization_id'
    ) INTO has_org_column;

    FOR spec IN
        SELECT * FROM (VALUES
            -- owner: 130:183-190. Holds the wildcard, so no later seed adds a
            -- row for it — 133 and 178 both say so in as many words.
            ('owner', 'Owner',
             'Full control of the organization, including roles and billing.',
             0, ARRAY['*']),

            -- admin: 130:193-207 + 131's integrations/memory + 133's publish +
            -- 178's billing:purchase + 196's projects:settings:write.
            ('admin', 'Admin',
             'Runs the platform: members, agents, integrations, and settings.',
             10, ARRAY[
                'admin:members:read', 'admin:members:invite', 'admin:members:manage',
                'admin:roles:manage', 'admin:access:manage', 'admin:settings:manage',
                'admin:audit:read',
                'feature:*', 'agents:run:*', 'agents:manage',
                'apps:use:*', 'apps:create', 'apps:publish',
                'integrations:manage', 'data:org:read',
                'integrations:use:*', 'memory:read_org', 'memory:write_org',
                'workflows:publish',
                'projects:settings:write',
                'billing:purchase'
             ]),

            -- manager: 130:210-223 + 131 + 133 + 196. NOT billing:purchase — 178's
            -- header argues that exclusion at length and seeding it here would
            -- make the argument decorative.
            ('manager', 'Manager',
             'Org-wide visibility across the apps; cannot change platform config.',
             20, ARRAY[
                'feature:chat', 'feature:email', 'feature:whatsapp', 'feature:tasks',
                'feature:notes', 'feature:memory', 'feature:dashboard',
                'feature:observability', 'feature:artifacts', 'feature:approvals',
                'agents:run:*', 'apps:use:*', 'apps:create',
                'data:org:read', 'admin:members:read',
                'integrations:use:*', 'memory:read_org', 'memory:write_org',
                'workflows:publish',
                'projects:settings:write'
             ]),

            -- member: 130:228-239 + 131's use/read half. Deliberately omits
            -- WhatsApp, Approvals, Integrations, Models and both Build panes:
            -- access is added, not taken away. No memory:write_org — a shared
            -- org fact every employee's agent can append to fills with noise.
            ('member', 'Member',
             'Day-to-day access to the core apps and shared agents.',
             30, ARRAY[
                'feature:chat', 'feature:email', 'feature:tasks', 'feature:notes',
                'feature:memory', 'feature:dashboard', 'feature:artifacts',
                'agents:run:*', 'apps:use:*',
                'integrations:use:*', 'memory:read_org'
             ]),

            -- guest: 130:242-249. An external collaborator gets chat and
            -- explicitly shared apps, and nothing from 131/133/178.
            ('guest', 'Guest',
             'External collaborator: chat and explicitly shared apps only.',
             40, ARRAY['feature:chat', 'apps:use:*']),

            -- agent_service: 130:252-259 + 131 + 133 + 178 + 196. Never assigned
            -- person; it resolves to '*' in acb_auth.access.SERVICE_ACCESS
            -- regardless, and the rows are for anyone reading the table.
            ('agent_service', 'Agent Service',
             'Internal service-to-service principal. Not assignable to people.',
             90, ARRAY[
                'agents:run:*', 'data:org:read',
                'integrations:use:*', 'memory:read_org', 'memory:write_org',
                'workflows:publish',
                'projects:settings:write',
                'billing:purchase'
             ])
        ) AS t(slug, display_name, description, role_rank, permissions)
    LOOP
        INSERT INTO org_role (organization_id, slug, display_name, description,
                              is_system, rank)
        VALUES (p_org_id, spec.slug, spec.display_name, spec.description,
                true, spec.role_rank)
        ON CONFLICT (organization_id, slug) DO NOTHING;

        SELECT r.id INTO rid FROM org_role r
         WHERE r.organization_id = p_org_id AND r.slug = spec.slug;

        -- ON CONFLICT DO NOTHING over the composite primary key: an admin who
        -- has already retuned a role is never re-stomped by a replay, which is
        -- the same promise 131's header makes.
        --
        -- ⚠️ The two arms differ ONLY in the tenancy column, and both are
        -- static. PL/pgSQL plans a statement on first execution of ITS branch,
        -- so the arm naming a column this database lacks is never planned. The
        -- day the generated files join the ladder, the ELSE arm is what gets
        -- deleted, and a reader can see exactly what that costs.
        IF has_org_column THEN
            INSERT INTO org_role_permission (role_id, permission, organization_id)
            SELECT rid, p, p_org_id FROM unnest(spec.permissions) AS p
            ON CONFLICT DO NOTHING;
        ELSE
            INSERT INTO org_role_permission (role_id, permission)
            SELECT rid, p FROM unnest(spec.permissions) AS p
            ON CONFLICT DO NOTHING;
        END IF;
    END LOOP;
END;
$provision_org_roles$;

COMMENT ON FUNCTION provision_org_roles(UUID) IS
    'MT-1j slice 1: seed the six system roles and their grants into ANY '
    'organization. Replays 130/131/133/178 as data (D43-A). Idempotent. '
    'Migration 200 made it carry org_role_permission.organization_id when the '
    'tenancy phase has added that NOT NULL column — without it, provisioning '
    'ANY new organization raised on production while passing on the ladder '
    '(H-104).';
