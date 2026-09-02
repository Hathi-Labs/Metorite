-- 022 — pin an operator to the sign-in methods THAT PERSON may use. D71.4.
--
-- Spec: `operator_identity_and_access.md` §4.1b and §8.1 done-whens 34 to 40.
--
-- 🔴 **The problem this closes.** D71 admits an operator whose email sits
-- outside our Workspace, because the owner assigns operators Gmail and outside
-- addresses. D71.3 then allows an email code as a fallback. Both are the
-- owner's decision, 2026-09-02.
--
-- A GLOBAL code flag weakens every operator, and the admin most of all. The
-- admin adds operators. So a person who reads the admin's mailbox adds
-- themselves, and the weakest method sets the strength of the whole console.
--
-- **This column separates the two.** The owner keeps `{google}` on their own
-- row. The outside contractor carries `{email}` or NULL. One person's fallback
-- stops being everybody's.
--
-- **NULL is not the empty set, and the difference is the whole design.** NULL
-- means "whatever the box allows", which is what every existing row means
-- today. An empty array would mean "no method admits this person", and a
-- backfill that wrote one would lock out every operator at the next deploy.
-- The CHECK below refuses an empty array for that reason.
--
-- ⚠️ **The guard reads ONE schema, and `current_schema()` is which.** 021
-- recorded why: a guard that reads `information_schema.columns` across every
-- schema answers "the column exists" for an `operator` table the ALTER never
-- touched, and the unqualified `COMMENT ON COLUMN` then fails the deploy under
-- `ON_ERROR_STOP`.
--
-- R6: expand only. One nullable column, no backfill, no rename, no drop. Old
-- code reads the row without this column and behaves exactly as it does today,
-- because NULL carries the meaning the old code already assumed.
-- R1: 021 is the highest number on disk at build time. The merge re-checks it.
--
-- Fence: tests/unit/test_operator_signin.py
--   ::test_a_row_pinned_to_google_refuses_an_email_code
--   ::test_a_null_pin_admits_whatever_the_box_allows
--   ::test_the_database_refuses_a_pin_that_admits_nobody  (this CHECK)

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'operator'
          AND column_name = 'allowed_methods'
    ) THEN
        ALTER TABLE operator
            ADD COLUMN allowed_methods TEXT[];

        ALTER TABLE operator
            ADD CONSTRAINT operator_allowed_methods_not_empty
            CHECK (allowed_methods IS NULL OR cardinality(allowed_methods) > 0);
    END IF;
END $$;

COMMENT ON COLUMN operator.allowed_methods IS
    'Which sign-in methods admit THIS operator (D71.4). NULL means whatever '
    'the box allows, which is what every row meant before this column '
    'existed. A named set restricts the person to those methods, so the '
    'owner keeps {google} on their own row while a contractor carries '
    '{email}. An empty array is refused by CHECK, because it would admit '
    'nobody and a backfill that wrote one would lock out every operator.';
