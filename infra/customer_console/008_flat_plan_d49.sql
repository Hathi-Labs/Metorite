-- ============================================================================
-- 008_flat_plan_d49.sql — one flat seat at ₹500, and the package ladder retired
-- ============================================================================
-- Source of record: work_plan.md **D49** (owner call, 2026-08-24), restated in
-- specs/launch_surface.md §4. This SUPERSEDES D23/D24 — 002_seed_catalog.sql's
-- ladder (Core ₹600 + Center packages ₹600/₹300 + add-ons + all-Centers ₹1,800
-- + Complete ₹3,000) is no longer what a customer is offered. As with 002, if a
-- number here disagrees with D49, D49 wins and this file is the bug.
--
-- **`core` is repriced, not replaced.** Membership IS the Core seat (D19.3), and
-- `customer_console/seats.py::CORE_PLAN_SLUG` is the one slug the sign-in path
-- allocates. Adding a parallel `flat` slug would immediately raise "which seat
-- does this member really hold" for every existing assignment — the second
-- implementation CLAUDE.md §5 forbids — and would strand every `seat_assignment`
-- row already written. One slug, one price, no new vocabulary.
--
-- ── R6, expand/contract ─────────────────────────────────────────────────────
-- Deploy applies migrations BEFORE restarting services, so the code running when
-- this lands is the code that predates it. Nothing here breaks that code:
--   * no column is added, renamed or dropped — the shape is untouched;
--   * `core` keeps its slug, its `kind` and its row, so every FK and every
--     `plan_slug = 'core'` predicate still resolves;
--   * deactivating a row is a value change on a column that already exists, and
--     `active` is already consulted by `store.active_plans` / `store.priced_plan`
--     (both filter on it), so old code reads the new value correctly.
-- Nothing is DELETED. A retired plan's rows are the audit trail behind invoices
-- already issued (`seat_assignment` keeps `released_at` for the same reason),
-- and `store.priced_plan`'s `active` filter is what actually stops the checkout
-- selling them.
--
-- ── What this migration deliberately does NOT do ────────────────────────────
-- **It does not touch a single `seat_assignment` or `seat_grant` row.** An
-- organization that already holds seats on a plan D49 retires — repricing,
-- converting, refunding, prorating — is OWNER-GATE (`launch_surface.md` LS-11,
-- work_plan.md §6: money on a live system). Their existing seats keep working;
-- deciding what they should cost is not an agent's call. Today, on every box,
-- that set is empty or Fracktal's (D42's ₹0 onboarding), which is exactly why
-- this can land as data now and the decision can be taken later.
--
-- Idempotent: re-running changes nothing, so the ladder can be replayed.
-- ============================================================================

-- ── 1. The one sellable seat ────────────────────────────────────────────────
--
-- WHERE slug = 'core' rather than an upsert: this is a repricing of a row 002
-- guarantees exists, and an INSERT ... ON CONFLICT here would quietly create it
-- on a database where 002 had not run — which would mean seeding a price into a
-- schema whose seat vocabulary was never established.
--
-- The display name loses "Core". Under D23 "Core" meant the mandatory BASE of a
-- stack you added packages to; there is no stack now, so a customer reading
-- "Core" would reasonably ask what the other tiers are.
UPDATE plan_catalog
   SET price_inr  = 500.00,
       name       = 'Metorite',
       active     = TRUE,
       sort_order = 10
 WHERE slug = 'core';


-- ── 2. Everything else is retired as a customer object ──────────────────────
--
-- Center packages, both org-wide add-ons, and both bundles. `kind` is the
-- discriminator rather than a slug list on purpose: a slug list would silently
-- miss a package seeded between 002 and here, and `core` is the only row whose
-- kind is 'core', so "everything that is not the flat seat" is exactly this
-- predicate. It also means a future Center package seeded by an older ladder
-- file cannot slip back into the catalog as sellable.
UPDATE plan_catalog
   SET active = FALSE
 WHERE kind IN ('center', 'addon', 'bundle')
   AND active;


-- ── 3. The one thing this migration can silently fail to do ────────────────
--
-- Both statements above are UPDATEs, and an UPDATE that matches nothing
-- succeeds. If 002 never ran on this database — a hand-built Console, a restore
-- from before the seed — step 1 changes zero rows and the box comes up serving a
-- catalog with no `core` at all, which the sign-in path then cannot allocate a
-- seat against. That failure surfaces as a broken first sign-in hours later, so
-- it is worth catching here, where the reason is still in front of you.
--
-- Deliberately narrow: it asserts only what THIS migration is responsible for.
-- An earlier draft also demanded "exactly one active plan in the catalog", which
-- reads well and is wrong — 002's own doctrine is that a price (and an `active`
-- flag) may move by a legitimate operator write, and a migration that aborts the
-- whole deploy because an operator activated something is a guard that costs
-- more than it protects. The exact-set claim belongs in the test suite, against
-- a freshly applied ladder, and that is where it lives
-- (`test_customer_console_sql.py::test_the_catalog_sells_exactly_one_thing_at_500`).
--
-- ⚠️ **This file must not contain a percent sign anywhere — comments included.**
-- The ladder is applied two ways: by psql (`apply_customer_console_migrations.sh`)
-- and, in the R8 suites, through psycopg. psycopg scans the whole statement for
-- its own placeholder syntax BEFORE the server ever sees it, and it does not
-- skip SQL comments, so a percent sign in prose is refused with "only 's', 'b',
-- 't' are allowed as placeholders" — or, when the next byte is multi-byte, with
-- a UnicodeDecodeError from inside psycopg's own error formatter, which is how
-- this was found. Doubling it would satisfy psycopg and break psql, since
-- PL/pgSQL then prints a literal one.
--
-- That is why the two RAISEs below use `USING MESSAGE` with concatenation
-- rather than the usual format-placeholder form: the ordinary
-- `RAISE EXCEPTION 'saw <placeholder>', x` spelling is unavailable here.
DO $$
DECLARE
    flat RECORD;
BEGIN
    SELECT slug, price_inr, active INTO flat
      FROM plan_catalog WHERE slug = 'core';

    IF NOT FOUND THEN
        RAISE EXCEPTION USING MESSAGE =
            'D49: plan_catalog has no ''core'' row to reprice — 002_seed_catalog.sql '
            'has not been applied to this database. Apply the ladder in order; '
            'membership IS the Core seat (D19.3) and the sign-in path allocates '
            'against that slug.';
    END IF;

    IF flat.price_inr <> 500.00 OR NOT flat.active THEN
        RAISE EXCEPTION USING MESSAGE =
            'D49: expected core active at 500.00 after this migration; found '
            || flat.price_inr || ' active=' || flat.active;
    END IF;
END
$$;
