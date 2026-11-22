-- ============================================================================
-- 475_notes_keys_per_org.sql - one notes settings row and one glossary term
-- per organization. The EXPAND half of an expand/contract change (task 3844).
-- ============================================================================
-- Spec: project-docs/specs/multi_tenancy_leak_audit.md · Metorite task 3844 ·
-- R1 · R5 · R6 · R8.
-- R1: origin/main held 471 as its highest file on 2026-11-22. Open PR 2719
-- holds 474 and task 3845 holds 473. So this file is 475. Check the number
-- again at merge (scripts/premerge.sh).
--
-- What: (1) `organization_id` on `copilot_config` and `notes_glossary` on the
--       NUMBERED ladder, only where it is missing. (2) the unique index
--       `copilot_config_org_owner_key` on `(organization_id, owner_email)`.
--       (3) the unique index `notes_glossary_org_user_term_idx` on
--       `(organization_id, user_id, lower(term))`.
--
-- **The fault.** Migration 125 made `owner_email` alone the primary key of
-- `copilot_config` (`copilot_config_pkey`). Migration 99 made
-- `(user_id, lower(term))` unique over all orgs on `notes_glossary`
-- (`notes_glossary_user_term_idx`). FORCE RLS hides the row of another org,
-- but a unique index does not. So when org A holds a key, the insert of
-- org B fails with a unique violation. That error tells org B that another
-- org holds the key. Each organization must own its own keys.
--
-- **Expand now, contract one release later (R6).** The deploy applies
-- migrations BEFORE it restarts the services. So the OLD gateway runs against
-- this schema for a short time. The old code names `ON CONFLICT (owner_email)`
-- and `ON CONFLICT (user_id, lower(term))`. Postgres needs a unique rule on
-- exactly those columns for each clause. The notes router is mounted in
-- production, and `feature:*` (migration 207) gives `notes` to each role but
-- `guest`. So old code can reach both writes during a deploy. This file
-- therefore keeps the old keys and only adds the new indexes.
--
--   Release N (this file): both keys exist on each table. The new code names
--     the new indexes as its conflict targets. A same-org write updates the
--     row. A write of a key that ANOTHER org holds still fails on the old
--     key. It never reaches the row of the other org.
--     `tests/unit/test_notes_keys_per_org_r8.py` pins this on purpose.
--   Release N+1 (the contract, a later numbered file): drop
--     `copilot_config_pkey` and `notes_glossary_user_term_idx`. Only then can
--     org B hold a key that org A holds. It must NOT ship with this file. If
--     it did, the old code's upserts would fail on each write until the
--     restart, because no unique rule on the old columns would remain.
--
-- **NULL organization_id.** Production carries the generated phases 01 to 04.
-- Phase 3 sets `organization_id` NOT NULL on both tables, so production holds
-- no NULL row. A database that never ran the tenancy phases can hold NULL
-- rows. A unique index treats each NULL as distinct, so it does not constrain
-- those rows. Until the contract, the old keys still keep each key once.
--
-- **No duplicate can block the build.** While the old key is unique over all
-- orgs, each old key occurs once in the table. A new key that adds
-- `organization_id` to the old columns cannot hold two equal rows. So the
-- CREATE cannot fail on data.
--
-- **Two starting points, one file** (the same as migrations 223, 354, 414).
--   * Production: the column exists, NOT NULL, with its `*_org_fk`.
--     `ADD COLUMN IF NOT EXISTS` then skips the whole column clause, the
--     inline REFERENCES too, so no second foreign key appears.
--   * A fresh ladder database: no tenancy phase ran (the ladder never replays
--     `generated/`). The column is created here, with the cascade that
--     `test_org_purge_tenant` requires, so each index has a column.
--
-- Tenancy (R5): both tables already have the tenant key and FORCE RLS with
-- a policy. This file adds no table, no policy and no
-- `gen_tenant_migration.EXEMPT` row.
--
-- Never wait for the lock: CREATE INDEX takes a SHARE lock on each table, and
-- each notes settings or glossary write needs it. So the file sets
-- lock_timeout first, as 414 does. On a lock timeout the runner tries the
-- whole file again, and the file is idempotent.
-- Depends on: 99_note_taker_slice2.sql (notes_glossary and its key),
--             125_meeting_agenda.sql (copilot_config and its primary key),
--             130_org_access_control.sql (organization).
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE UNIQUE INDEX IF NOT EXISTS.
-- No CONCURRENTLY: `copilot_config` holds one row per member who saved notes
-- settings, and `notes_glossary` holds a few terms per member. A failed
-- concurrent build leaves an INVALID index behind (see migration 223).
-- Pinned by tests/unit/test_notes_keys_per_org_r8.py, which finds this file
-- by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The column, only where the tenancy phases did not add it ─────────────
--
-- Same type and default as `generated/01_add_columns.sql`, plus the cascade
-- that the purge path needs. `REFERENCES` precedes `DEFAULT`, as in 223.
ALTER TABLE copilot_config
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

ALTER TABLE notes_glossary
    ADD COLUMN IF NOT EXISTS organization_id UUID
    REFERENCES organization (id) ON DELETE CASCADE
    DEFAULT current_setting('app.tenant_id', true)::uuid;

-- ── 2. One notes settings row per organization and member ───────────────────
--
-- copilot_config_pkey stays. The contract release drops it.
CREATE UNIQUE INDEX IF NOT EXISTS copilot_config_org_owner_key
    ON copilot_config (organization_id, owner_email);

COMMENT ON INDEX copilot_config_org_owner_key IS
    'Task 3844: one notes settings row per organization and member. '
    'copilot_config_pkey on owner_email alone stays until the contract '
    'release drops it.';

-- ── 3. One glossary term per organization, member and case-folded term ──────
--
-- notes_glossary_user_term_idx stays. The contract release drops it.
CREATE UNIQUE INDEX IF NOT EXISTS notes_glossary_org_user_term_idx
    ON notes_glossary (organization_id, user_id, lower(term));

COMMENT ON INDEX notes_glossary_org_user_term_idx IS
    'Task 3844: one glossary term per organization, member and case-folded '
    'term. notes_glossary_user_term_idx on (user_id, lower(term)) stays until '
    'the contract release drops it.';

COMMIT;
