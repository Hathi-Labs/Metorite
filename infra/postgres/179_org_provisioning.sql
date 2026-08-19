-- ============================================================================
-- 179_org_provisioning.sql — provisioning an organization, for ANY organization
-- ============================================================================
-- Spec: project-docs/specs/saas_multitenancy.md §11 MT-1j, slices 1 + 2 + 3.
-- Decision D43-A (work_plan.md §3): ONE SQL function replaying 130/131/133/178's
-- grants as data — not a `role_template` table, which is the more general answer
-- and buys nothing until customers edit system roles, which no ticket asks for.
--
-- WHAT WAS WRONG. Every role/permission seed in this ladder scopes itself to the
-- `default` organization with a literal predicate (130:180, 131:36, 133:34,
-- 178:60). There has never been a second one, so nothing noticed — but the
-- consequence is stated plainly in 178's own header: in any OTHER organization
-- `billing:purchase` is born UNHELD, "not because of this migration, but because
-- a newly created organization gets no roles at all today". Four specs
-- (saas_multitenancy_implementation.md §7.1/§8, customer_console.md CP-2b §6(k),
-- subscription_console.md, user_management_contract.md §3) each disclaimed the
-- fix to an org-provisioning ticket that did not exist. This is that ticket.
--
-- WHAT THIS MIGRATION SEEDS: nothing. It introduces three callables and applies
-- no data of its own. That is deliberate on two counts:
--
--   * every organization that exists today was already seeded by 130/131/133/178,
--     so replaying the same grants here would be a no-op carrying a predicate
--     that pins the `default` slug — growing the very ratchet MT-1j installs
--     (tests/unit/test_org_provisioning.py::TestTheDefaultSlugRatchet); and
--   * R6/expand-only, and root CLAUDE.md §5: 130 and its three siblings are
--     HISTORY. They are not rewritten to call these functions. The parity
--     between what they produced for `default` and what provision_org_roles()
--     produces for a fresh organization is asserted against a live database
--     instead — `test_the_callable_reproduces_the_default_orgs_grant_set_exactly`
--     compares the two sets role-for-role, which is a stronger claim than a text
--     diff of this file could make and fails if EITHER statement drifts.
--
-- ⚠️ BUILDING AND R8-TESTING THIS IS AGENT-SAFE; EXECUTING IT AGAINST A REAL
-- SECOND ORGANIZATION IS OWNER-GATED (D43-C, work_plan.md §6). H3's RLS
-- promotion is a hard prerequisite: infra/postgres/generated/04_policies.sql has
-- never been replayed, so an organization provisioned today would exist with no
-- database-level isolation whatsoever.
--
-- ⚠️ THESE FUNCTIONS RUN AS THE CALLER (SECURITY INVOKER, the default), and that
-- is a decision to REVISIT AT H3, not now. Once FORCE ROW LEVEL SECURITY is
-- promoted, a caller bound to tenant A cannot insert tenant B's rows — which is
-- correct for the app and wrong for a provisioning path that by definition
-- creates a tenant nobody is bound to yet. Marking them SECURITY DEFINER today
-- would be a privilege decision taken months before the policy it answers to
-- exists, and a DEFINER function that outlives its reason is how a tenancy
-- boundary quietly gains a hole. Recorded here so H3 finds it rather than
-- rediscovers it.
--
-- Idempotent (CREATE OR REPLACE; every write is ON CONFLICT-guarded).
-- Depends on: 130_org_access_control.sql (organization, org_role,
-- org_role_permission, user_role), 159_control_plane.sql (tenant_placement),
-- 162_app_user_email_case.sql (the lower(email) unique index the owner upsert
-- names).
-- Fence: tests/unit/test_org_provisioning.py (R8, tenant ladder).
-- ============================================================================


-- ── Slice 1 · the parameterised role seed ───────────────────────────────────
--
-- The grant set below is 130 + 131 + 133 + 178 replayed as DATA, keyed on
-- organization_id and pinning no organization at all. Anything added to a system
-- role from here on belongs in this list; a fifth single-organization seed
-- (one predicated on the `default` slug, as those four are) is the second
-- seeding doctrine 178's comment refuses.
--
-- ⚠️ That sentence is deliberately phrased AROUND the literal predicate rather
-- than quoting it: MT-1j's ratchet is whitespace-proof and scans comment prose
-- too (161 carries two such hits), so quoting the shape here would grow the
-- baseline this migration exists to hold. Caught red by the fence, 2026-08-19.

CREATE OR REPLACE FUNCTION provision_org_roles(p_org_id UUID)
RETURNS void
LANGUAGE plpgsql
AS $provision_org_roles$
DECLARE
    spec RECORD;
    rid  UUID;
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

    FOR spec IN
        SELECT * FROM (VALUES
            -- owner: 130:183-190. Holds the wildcard, so no later seed adds a
            -- row for it — 133 and 178 both say so in as many words.
            ('owner', 'Owner',
             'Full control of the organization, including roles and billing.',
             0, ARRAY['*']),

            -- admin: 130:193-207 + 131's integrations/memory + 133's publish +
            -- 178's billing:purchase.
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
                'billing:purchase'
             ]),

            -- manager: 130:210-223 + 131 + 133. NOT billing:purchase — 178's
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
                'workflows:publish'
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

            -- agent_service: 130:252-259 + 131 + 133 + 178. Never assigned to a
            -- person; it resolves to '*' in acb_auth.access.SERVICE_ACCESS
            -- regardless, and the rows are for anyone reading the table.
            ('agent_service', 'Agent Service',
             'Internal service-to-service principal. Not assignable to people.',
             90, ARRAY[
                'agents:run:*', 'data:org:read',
                'integrations:use:*', 'memory:read_org', 'memory:write_org',
                'workflows:publish',
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
        INSERT INTO org_role_permission (role_id, permission)
        SELECT rid, p FROM unnest(spec.permissions) AS p
        ON CONFLICT DO NOTHING;
    END LOOP;
END;
$provision_org_roles$;

COMMENT ON FUNCTION provision_org_roles(UUID) IS
    'MT-1j slice 1: seed the six system roles and their grants into ANY '
    'organization. Replays 130/131/133/178 as data (D43-A). Idempotent.';


-- ── Slice 2 · an owner path that is not _BOOTSTRAP_ORG_SLUG ─────────────────
--
-- ⚠️ acb_auth.access._BOOTSTRAP_ORG_SLUG is NOT re-pointed and must never be
-- (D36.3): `default` stays the fresh-box first-run path, and provisioning a
-- customer organization never routes through that constant — aiming it at a
-- customer is the first-party-bypass shape D36.2 forbids. The two paths differ
-- in where the owner's address comes from, which is why they are two paths:
-- ensure_owner_bootstrap() reads EXECUTIVE_EMAILS from the environment, which
-- SQL cannot do; a provisioned organization is TOLD its owner, which is a
-- parameter. Neither is a special case of the other.

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
        RAISE EXCEPTION
            'provision_org_owner: % already belongs to organization % — '
            'refusing to move a member between tenants', v_email, v_holder;
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
    'MT-1j slice 2: make a NAMED address the owner of an EXPLICIT organization. '
    'Not a re-point of _BOOTSTRAP_ORG_SLUG (D36.3) — a second path, because the '
    'address is a parameter here and an environment variable there.';


-- ── Slice 3 · organization + tenant_placement as ONE idempotent act ─────────
--
-- Idempotent on the SLUG, for the reason customer_console/main.py:588-591
-- already argues: the natural key is what a retrying signup form resends.
--
-- Both rows land together because an organization without a tenant_placement is
-- a tenant whose data plane is unresolvable — acb_common.placement.
-- resolve_placement() refuses rather than guessing, by design. A plpgsql
-- function body runs inside the calling statement, so any RAISE below aborts
-- the whole act; there is no state in which the organization exists and the
-- placement does not.

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
    'MT-1j slice 3: the one idempotent provisioning act — organization + '
    'tenant_placement + system roles + (optionally) a named owner, keyed on the '
    'slug. Atomic within the calling statement.';
