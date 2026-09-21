-- 174_people_absences.sql — who is away, and when (People Center P-5 / WS-28k).
--
-- What: `people_absences` — one row per span somebody is not working.
-- Why:  an assigner needs to know Rahul is away next week, and the dashboard's
--       "at risk" is arithmetic over the working hours somebody actually has
--       left before a deadline — which is wrong by a whole week if it counts
--       days they are on holiday.
--       Spec: project-docs/specs/people_center_app.md §5.8 · **D-PC-7**.
-- Depends on: 49_gtd_people.sql.
--
-- ── Availability, NOT leave management (D-PC-7) ─────────────────────────────
-- This table is deliberately four columns of FACT and nothing else. There is
-- no approver, no status, no balance, no accrual, no entitlement, no carry-over
-- and no policy — because **leave management is a different product**: it needs
-- a policy model nothing in the platform has, and an approval path that should
-- reuse the Action Broker inbox when it comes (§10).
--
-- A half-built approval chain is worse than none: it looks like a control, so
-- people stop checking with each other, and then it turns out nothing was ever
-- enforced. If a future ticket adds `approved_by` to this table, that ticket
-- has become leave management and needs §10's decision first.
--
-- ── The shape ───────────────────────────────────────────────────────────────
-- Inclusive dates, not timestamps: "away 12th to 16th" is how people say it and
-- how a calendar draws it, and an instant would force every caller to decide
-- what 09:00 on the 16th means. `partial` covers the half-day and the
-- conference-with-patchy-wifi — it reduces the day rather than removing it.
--
-- Tenancy (R5a): a NEW table, so it carries `organization_id` from the start
-- (see the column) AND is discovered by `scripts/gen_tenant_migration.py`, so
-- the generated RLS policy attaches to it when that set is promoted. It is
-- deliberately NOT in that script's `EXEMPT` map — an absence is tenant data
-- like any other row about a person.
--
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS.

-- == The `gtd_` name is retired (owner directive, 2026-09-21) ================
--
-- **THE RENAME LIVES IN THE FILE THAT CREATES THE TABLE, and that is what
-- makes it safe.** The obvious alternative -- one migration at the end that
-- renames -- breaks every earlier file on replay: an ALTER TABLE addresses a
-- name that is gone, and CREATE TABLE IF NOT EXISTS happily builds an empty
-- duplicate beside the real one. Both shapes were measured on 2026-09-21 and
-- both were abandoned.
--
-- One file answers for one table in all three states:
--
--   * Fresh install -- nothing to rename, the CREATE below makes the new
--     name directly.
--   * A database that predates this -- the old table is renamed WITH ITS
--     ROWS, and the CREATE below then finds the name taken and skips.
--   * Replay -- already renamed, the guard matches nothing. Idempotent.
--
-- relkind = 'r' so a view wearing the old name is left alone rather than
-- renamed into the table's place.
--
-- Index and constraint names keep their old spelling. A rename does not
-- touch them, migration 148 looks some of them up BY NAME, and no query
-- names an index.
--
-- WARNING: this block names the gtd_person_absences table, so it must survive a rename
-- sweep. The first attempt added it BEFORE sweeping the tree, and the sweep
-- rewrote `ALTER TABLE gtd_person_absences RENAME TO people_absences` into `ALTER TABLE people_absences RENAME TO
-- people_absences` -- a silent no-op that left an upgraded database on the old tables
-- with new empty ones beside them. Caught only by rebuilding a real
-- pre-change database and upgrading it, which is now
-- tests/unit/test_people_rename_upgrade.py. The old name is spelled ONCE
-- here, as a quoted literal passed to format().

DO $rename_people_absences$
DECLARE
    old_name CONSTANT text := 'gtd_person_absences';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.people_absences') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'people_absences');
        RAISE NOTICE 'renamed % -> people_absences', old_name;
    END IF;
END
$rename_people_absences$;

CREATE TABLE IF NOT EXISTS people_absences (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- CASCADE: an absence is a fact about a person and means nothing without
    -- them. Deleting a person is already a deliberate act; leaving orphaned
    -- spans behind would make the availability query answer for nobody.
    person_id           UUID NOT NULL
                            REFERENCES people (id) ON DELETE CASCADE,
    -- ── The tenant key, declared here rather than retrofitted ──────────────
    -- R5a and `test_tenancy_boundary.test_a_new_table_must_carry_a_tenant_key`:
    -- a table added while the system is knowingly becoming multi-tenant
    -- declares its key on day one, because backfilling one onto live rows
    -- costs orders of magnitude more than an empty column does now.
    --
    -- The DEFAULT is the generated migration's own idiom — the tenant comes
    -- from the session GUC that `acb_common.db.tenant_session` binds, so no
    -- call site passes it and none can pass the wrong one. An insert outside a
    -- bound session finds no GUC, defaults to NULL and **fails the NOT NULL**:
    -- fail closed, never "the usual org" (MT-1c).
    --
    -- ⚠️ Not a parent trigger like the 17 `pm_*` tables use, because the parent
    -- cannot supply what it does not have: `people` carries no
    -- `organization_id` until the generated MT-1b migration is promoted, which
    -- is a maintenance-window decision and not this ticket's.
    -- ⚠️ REFERENCES before DEFAULT, and the order is not cosmetic:
    -- `test_tenancy_boundary` matches `organization_id … REFERENCES
    -- organization(` with **no comma in between**, and every form of
    -- `current_setting('app.tenant_id', true)` contains one. Written the other
    -- way round, a table that IS tenant-scoped reads as unscoped to the
    -- ratchet. Both orders are legal Postgres — verified against a real
    -- database, along with the two behaviours below.
    organization_id     UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE
                            DEFAULT NULLIF(
                                current_setting('app.tenant_id', true), '')::uuid,
    -- Inclusive on both ends. A one-day absence has starts_on = ends_on, which
    -- is what "I am off on Friday" means and what a UI has to render.
    starts_on           DATE NOT NULL,
    ends_on             DATE NOT NULL,
    -- `away`     — not working at all (holiday, leave, anything)
    -- `holiday`  — a public holiday, which the whole org shares
    -- `partial`  — working, but less: a half day, a conference, a training week
    --
    -- Three words on purpose. Every additional kind is a policy question
    -- wearing a vocabulary disguise ("sick" invites "how many days left"), and
    -- the assignment question only needs "are they there, and how much".
    kind                TEXT NOT NULL DEFAULT 'away'
                            CHECK (kind IN ('away', 'holiday', 'partial')),
    -- How much of the day they DO work, for `partial`. NULL means "all of it"
    -- for a partial with no figure given, and is meaningless for the other two.
    hours_per_day       REAL,
    -- Free text, theirs. Not required, and never parsed: a note somebody has to
    -- fill in correctly is a field that gets filled in wrongly.
    note                TEXT,
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- An end before its start is not a short absence, it is a typo. Refused
    -- here as well as in the route, because this is the constraint that holds
    -- when an importer or a hand-run statement is the writer.
    CHECK (ends_on >= starts_on)
);

-- The query every consumer runs: "is this person away between these dates".
-- Person first because it is always known and always equality; the date is the
-- range half.
CREATE INDEX IF NOT EXISTS idx_gtd_person_absences_person
    ON people_absences (person_id, starts_on, ends_on);

-- The dashboard's other question — "who is away this week" — has no person to
-- filter on, so it needs the dates on their own.
CREATE INDEX IF NOT EXISTS idx_gtd_person_absences_window
    ON people_absences (organization_id, starts_on, ends_on);
