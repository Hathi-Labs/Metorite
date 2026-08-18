-- ============================================================================
-- 178_billing_purchase_permission.sql — who may spend the company's money
-- ============================================================================
-- Spec: project-docs/specs/subscription_console.md SC-4a, the B7 block
-- (answered 2026-08-18, clause 2 rewritten 2026-08-19 as finding F-C).
-- Registered as an argued deviation in
-- project-docs/specs/user_management_contract.md §3.
--
--   billing:purchase    create a checkout order and present a discount code
--                       against it — i.e. the two write proxies at
--                       /api/billing/orders and /api/billing/orders/{id}/redeem,
--                       which reach the Customer Console with THIS deployment's
--                       own organization key and are the only gate those routes
--                       will ever have (they do not pass through the gateway,
--                       so nothing re-authorizes them downstream).
--
-- Why a new slug rather than the §3 read floor: `admin:members:read` means
-- "may see the member list". These routes spend money. In most companies of
-- any size the person who administers members and the person who buys are not
-- the same person, and a capability is how that is expressible without a
-- second role system.
--
-- THE ROLE SET: `admin` only.
--   * `owner` already holds '*' (130_org_access_control.sql:188-190) and needs
--     no row; adding one would be a second statement of the same grant. This
--     mirrors 133's `-- owner already holds '*'; nothing to add`.
--   * `manager` is DELIBERATELY EXCLUDED, and this is where the seed diverges
--     from 133_workflows_publish_permission.sql's admin+manager. 133 gated
--     publishing an automation — an operational act a manager owns. This gates
--     buying, and the whole argument for minting the slug is that money
--     authority is NARROWER than the member-admin floor. Seeding `manager`
--     would re-widen it to every holder of that floor and make the argument
--     decorative.
--   * `member` / `guest`: nothing. They never see the billing surface.
--   * `agent_service` gets the row for table-consistency exactly as 133 does
--     (:62-70) — it resolves to '*' in acb_auth.access.SERVICE_ACCESS
--     regardless, and the row is for anyone reading the table directly.
--
-- ⚠️ THE ORG SCOPE, STATED HONESTLY. Like EVERY permission seed in this tree
-- (130:180, 131:36, 133:34) this seeds the `default` organization only. In any
-- OTHER organization `billing:purchase` is born UNHELD — not because of this
-- migration, but because a newly created organization gets no roles at all
-- today. That is a recorded, pre-existing defect class
-- (saas_multitenancy_implementation.md §7.1 step 3 and §8 trap 5) owned by the
-- org-provisioning ticket that parameterises role seeding. Writing a fourth
-- seed that looped every organization would invent a second seeding doctrine
-- inside a checkout slice AND would not help: the roles it would attach to do
-- not exist in those organizations either.
--
-- Purely ADDITIVE: before this migration the capability did not exist, so
-- nothing loses access. Idempotent. Depends on: 130_org_access_control.sql.
-- Fence: tests/unit/test_billing_purchase_capability.py (R8, tenant ladder).
-- ============================================================================

DO $$
DECLARE
    org_id UUID;
    rid    UUID;
BEGIN
    SELECT id INTO org_id FROM organization WHERE slug = 'default';
    IF org_id IS NULL THEN
        RAISE NOTICE '178: no default organization — run migration 130 first';
        RETURN;
    END IF;

    -- owner already holds '*'; nothing to add.

    -- admin: the one role seeded with it. See the role-set note above for why
    -- `manager` is not here.
    SELECT id INTO rid FROM org_role
     WHERE organization_id = org_id AND slug = 'admin';
    IF rid IS NOT NULL THEN
        INSERT INTO org_role_permission (role_id, permission)
        VALUES (rid, 'billing:purchase')
        ON CONFLICT DO NOTHING;
    END IF;

    -- agent_service resolves to '*' in acb_auth.access.SERVICE_ACCESS; the row
    -- is kept consistent for anyone reading the table directly.
    SELECT id INTO rid FROM org_role
     WHERE organization_id = org_id AND slug = 'agent_service';
    IF rid IS NOT NULL THEN
        INSERT INTO org_role_permission (role_id, permission)
        VALUES (rid, 'billing:purchase')
        ON CONFLICT DO NOTHING;
    END IF;
END $$;
