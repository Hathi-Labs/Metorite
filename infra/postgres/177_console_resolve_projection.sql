-- ============================================================================
-- 177_console_resolve_projection.sql — WS-31 CP-2b, the deployment half.
--
-- Spec: project-docs/specs/customer_console.md §6 CP-2b (clauses 6, 7, 8) ·
-- §6(j) (the cached OUTCOME) · §6(k) (the join key) · D32.4 · D15.
--
-- Three columns on two tables migration 159 already created, so that a
-- deployment can answer ONE question when the Customer Console is unreachable:
-- *may this sign-in proceed, and with what capabilities.*
--
--   org_membership.resolved_at            the per-person freshness clock
--   organization.registry_status          the registry's LAST-SEEN word, for
--                                         refusal copy only — never a branch
--   organization.registry_capabilities    the capability object EXACTLY as the
--                                         wire carried it
--
-- ── Why these tables, and why no new EXEMPT row ─────────────────────────────
-- `organization` ("the tenant list itself") and `org_membership` ("control
-- plane — the tenant-scoped half; org_id is its PK") are already in
-- `scripts/gen_tenant_migration.py`'s EXEMPT map. A column added to an exempt
-- table needs NO new exemption entry and must not add one: it inherits the
-- table's reason, and the reason still holds — a policy that hid the tenant
-- list from the connection resolving *which tenant this is* would make the box
-- unable to answer its own first question. This migration therefore adds ZERO
-- rows to EXEMPT, creates NO table, and leaves `test_tenant_coverage.py`'s map
-- untouched. R5's source gate is satisfied for that reason and by that fence.
--
-- ── R6: nullable, and NO DEFAULT — an argued deviation ──────────────────────
-- R6's letter says "new columns nullable with a default". Here **NULL is
-- load-bearing**: it means *"this box has never had an answer from the registry
-- for this row"*, which is a different state from every value the column can
-- hold, and it is the state a fail-closed sign-in path must be able to see. A
-- default would make every pre-existing row — including the ones 159 seeded
-- from `app_user` — assert a registry answer nobody ever received. R6's
-- PURPOSE (old code meets new schema safely) is met either way, because the
-- code running before this migration reads neither column. Fence:
-- `test_an_unresolved_row_reads_as_never_resolved_not_as_a_state`
-- (tests/unit/test_deployment_resolve_cache.py).
--
-- ── Why JSONB for the capabilities and not three booleans ───────────────────
-- A fourth capability then becomes a Console-side change rather than a tenant
-- migration, and it matches `organization.settings JSONB` (130:42), the shape
-- already on this table. ⚠️ **A MISSING KEY MEANS NOT OBSERVED, NEVER FALSE.**
-- A 403 outcome writes `{"sign_in": false}` ALONE — `write_seats`/`use_ai` are
-- absent because that outcome did not prove them, and a value the Console never
-- sent is minted information (§6(j) row ii).
--
-- ⚠️ The registry's `organization_id` is a UUID in a DIFFERENT database on a
-- different plane and is deliberately NOT stored here. The join key is
-- `organization.slug` (130:39, TEXT UNIQUE NOT NULL). A third identifier,
-- joinable by nothing local, is a foot-gun the next reader would mistake for a
-- foreign key (§6(k)).
--
-- R1: number taken by listing infra/postgres/ at build time (highest on disk
-- was 176_people_skills.sql) and re-checked at merge.
--
-- Idempotent. Depends on: 130_org_access_control.sql (organization),
-- 159_control_plane.sql (org_membership).
-- ============================================================================

ALTER TABLE org_membership
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

COMMENT ON COLUMN org_membership.resolved_at IS
    'WS-31 CP-2b: when the Customer Console last answered for this person in '
    'this organization. NULL = never observed, which is NOT a state and NOT a '
    'refusal — it means this entry point has no cached answer to fall back on, '
    'so the next unreachable-Console sign-in fails closed.';

ALTER TABLE organization
    ADD COLUMN IF NOT EXISTS registry_status       TEXT,
    ADD COLUMN IF NOT EXISTS registry_capabilities JSONB;

COMMENT ON COLUMN organization.registry_status IS
    'WS-31 CP-2b: the registry''s last-seen lifecycle word '
    '(trial|active|past_due|suspended|cancelled|deleted). ⚠️ REFUSAL COPY '
    'ONLY — never a branch input. A deployment that read this and decided '
    'would be a second copy of customer_console.lifecycle spelled as an `if`. '
    'Deliberately NOT written on a 403: that body is a human sentence, not a '
    'field, and parsing a word out of it would couple this box to the '
    'Console''s message wording.';

COMMENT ON COLUMN organization.registry_capabilities IS
    'WS-31 CP-2b: the capability object exactly as the wire carried it — '
    '{"sign_in":…,"write_seats":…,"use_ai":…}. THIS is what the box branches '
    'on. A cached {"sign_in": false} refuses sign-in immediately at ANY '
    'freshness — Console reachable or not, inside the TTL or outside it. A '
    'MISSING KEY MEANS NOT OBSERVED, NEVER FALSE.';
