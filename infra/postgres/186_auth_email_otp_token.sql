-- ============================================================================
-- 186_auth_email_otp_token.sql — the Auth.js email-OTP verification token
-- ============================================================================
-- Spec: project-docs/specs/customer_console.md §CP-2d clauses 7, 8 and 11 ·
-- WS-31 · owner answers B1 (token home = TENANT plane, RLS-EXEMPT, PREFIXED
-- name) and B3 (the rate-limit numbers) of 2026-08-23.
--
-- WHAT THIS IS
-- ------------
-- The one row an Auth.js email provider cannot work without: the verification
-- token it mints at "send me a code" and consumes at "here is my code". Every
-- other Auth.js adapter table (users / accounts / sessions) is deliberately
-- ABSENT — the session strategy is pinned to `jwt` and identity in this product
-- is the tenant plane's (`app_user` / `user_identity`, reached through CP-2b's
-- resolve). A second identity store is the defect root CLAUDE.md §5 names.
--
-- ⚠️ THE TABLE IS RLS-EXEMPT, AND THAT IS THE DESIGN, NOT AN OMISSION.
-- ---------------------------------------------------------------------------
-- The row is minted BEFORE any session exists, for an email address that may
-- belong to NO organization at all — the zero-org (`console-empty`) case is
-- precisely the self-serve-signup funnel CP-2c built, so it is not a corner. A
-- tenant-scoped policy (`organization_id = current_setting('app.tenant_id', …)`)
-- would therefore have nothing to stamp on INSERT and nothing to match on
-- SELECT, and with H3's phase-4 promotion LIVE in production (2026-08-23: 140
-- policies, 140 forced, the app on a non-privileged role) that is not a future
-- problem — it would brick email OTP the day this shipped. The table is listed
-- in `scripts/gen_tenant_migration.EXEMPT` beside `user_identity` and
-- `org_membership` with the reason "belongs to an email, minted pre-session; no
-- organization_id exists to stamp" (R5(a)'s exempt-identity-table permission).
-- The exemption map IS the security review — challenge this entry, do not copy
-- it. Fences: tests/unit/test_tenant_coverage.py (source gate + the
-- exemption-reason and exemption-count tripwires), tests/unit/
-- test_tenancy_boundary.py (the partition assertions) and tests/unit/
-- test_h3_rls_promotion_rehearsal.py::TestTheEmailOtpTokenIsReachableUnbound
-- (R8 — a phase-4-promoted catalog, as the non-privileged role, UNBOUND).
--
-- ⚠️ THE NAME IS PREFIXED AND THE CREATE FAILS LOUDLY ON A CLASH.
-- ---------------------------------------------------------------------------
-- Auth.js's conventional name is the bare `verification_token`, and Langfuse —
-- which shares a Postgres server with this ladder on the staging box — carries
-- its own table by exactly that name. `CREATE TABLE IF NOT EXISTS` against a
-- colliding relation is a SILENT NO-OP: the migration reports success, the
-- ledger records it, and the adapter then reads and writes somebody else's
-- table. So the name carries the `auth_email_otp_` prefix AND the guard below
-- RAISES when a relation of that name exists without this migration's own
-- columns. Idempotency is preserved for our OWN table (the ladder is replayed
-- on every deploy and twice in the R8 fixtures) — what is refused is a
-- STRANGER's table wearing the name.
--
-- EXPAND-ONLY (R6). A new table with no dependants: old code that has never
-- heard of it is unaffected, and the deploy applies migrations before
-- restarting services, so the adapter never meets a database without it.
--
-- THE RATE-LIMIT COUNTER LIVES HERE, ON THE ROW (owner answer B3)
-- ---------------------------------------------------------------------------
-- `attempts` / `last_attempt_at` are the verification-attempt counter and
-- `created_at` is the send record. Both limits are therefore computed and
-- written inside ONE transaction with the read that gates them, under a
-- per-identifier advisory lock (`acb_auth.email_otp`) — there is no second
-- store to fall out of step with, and NO new Redis surface (the tenant Redis
-- seam deliberately has no unbound fallback, and this row exists before any
-- tenant does).
--
--   * verification: max 5 attempts per identifier per 10-minute window, the
--     window anchored on `last_attempt_at`. On exhaustion the outstanding token
--     is invalidated (`invalidated_at`) AND further attempts answer 429 for the
--     remainder of the window.
--   * sends: max 3 per identifier per hour, counted as rows created in the last
--     hour EXCLUDING the send currently in flight (which both legs of one send
--     identify by `(identifier, expires)`), so the two concurrent legs
--     @auth/core starts in one `Promise.all` reach the SAME answer regardless of
--     which lands first. Without that exclusion the count is racy by one.
--     ⚠️ **That exclusion is NOT what bounds emails, and believing it did was a
--     real hole** (repair of review finding P1b, 2026-08-23): two *different*
--     sends computed in the same millisecond carry the same `expires`, so they
--     excluded each other and BOTH passed a budget of three, upserting one row
--     and mailing two codes. The bound is `reserved_at` below — the send leg
--     CLAIMS the slot atomically and is refused when the claim finds it taken,
--     so N concurrent sends produce exactly one email.
--
-- WHY `token` IS NULLABLE
-- ---------------------------------------------------------------------------
-- `lib/actions/signin/send-token.js` starts `sendVerificationRequest` and
-- `createVerificationToken` CONCURRENTLY, and only the second is handed the
-- token hash. The send leg must be gated BEFORE the mail goes out (a limiter
-- that refuses the token but not the email does not limit anything), so it
-- upserts the row first, on `(identifier, expires)` — and it used to do so with
-- `token` NULL, leaving the hash to the token leg. Hence the nullable column.
--
-- ⚠️ **Both legs now write a hash, and the CLAIM's is authoritative** (repair of
-- review finding P2, round 2, 2026-08-23). Refusing the duplicate send's mail
-- did not stop its token leg — `Promise.all` cancels nothing — so the loser's
-- `createVerificationToken` landed on the shared row and overwrote the winner's
-- hash, and the one email that went out carried a code that could never verify.
-- The browser tier recomputes the same `createHash(`${token}${secret}`)` from
-- the raw code @auth/core hands the send leg and passes it to
-- `acb_auth.email_otp.reserve_send`, whose claim writes it inside the statement
-- that decides who won; the token leg's upsert is now FIRST-WRITER-WINS and can
-- no longer replace it. The column stays nullable for rows written by the first
-- cut of this migration (they expire in ten minutes) and because a NULL-token
-- row can never be consumed anyway — the consume path matches on the hash.
--
-- ⚠️ The stored `token` is NEVER the 6-digit code. @auth/core stores
-- `createHash(`${token}${secret}`)` — SHA-256 of the code salted with
-- AUTH_SECRET — so a reader of this table cannot sign in. That is exactly why
-- the rate limiter, not the column, is the defence.
--
-- Fences: tests/unit/test_email_otp_token.py (R8, real Postgres: create/consume
-- round-trip, single-use, expiry, the 6th attempt refused even with the correct
-- code, the 429 window, the 4th send refused, and the collision guard), plus
-- tests/unit/test_h3_rls_promotion_rehearsal.py for the unbound-reachability
-- half. R7 name: `email-otp-token-limits`.
-- ============================================================================

-- ── The collision guard (ruling H-C) ────────────────────────────────────────
-- A relation of this name that is not OURS is a name clash, and a clash must
-- fail the deploy rather than be absorbed by IF NOT EXISTS. `last_attempt_at`
-- is the marker column because nothing else would plausibly carry it.
DO $$
BEGIN
    IF to_regclass('public.auth_email_otp_token') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'auth_email_otp_token'
              AND column_name  = 'last_attempt_at'
       )
    THEN
        RAISE EXCEPTION
            'auth_email_otp_token already exists and is NOT the CP-2d table '
            '(no last_attempt_at column). Refusing rather than adopting a '
            'stranger''s table: the idempotent create below would no-op here, '
            'and the email-OTP adapter would then read and write it. Rename '
            'the other relation, or rename this one and update '
            'packages/acb_auth/acb_auth/email_otp.py.';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS auth_email_otp_token (
    id              BIGSERIAL   PRIMARY KEY,
    -- The email address the code was sent to, canonicalised to lower(btrim())
    -- by the gateway seam so a case variant cannot reset either limit.
    identifier      TEXT        NOT NULL,
    -- @auth/core's SHA-256 of (code + AUTH_SECRET). NULL until the token leg
    -- of the send lands; a NULL-token row can never be consumed.
    token           TEXT,
    expires         TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The verification-attempt counter (owner answer B3), charged to the live
    -- token of the identifier — a wrong code matches no row, so the attempt has
    -- to land on the outstanding one or a guesser is never counted.
    attempts        INTEGER     NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    consumed_at     TIMESTAMPTZ,
    invalidated_at  TIMESTAMPTZ,
    -- The SEND SLOT, claimed by the send leg alone (`acb_auth.email_otp.
    -- reserve_send`). NULL means "a row exists but no mail has been authorised
    -- against it" — which is what the token leg writes when it lands first.
    -- The claim is `ON CONFLICT … DO UPDATE … WHERE reserved_at IS NULL`, so it
    -- succeeds exactly once per (identifier, expires) and every later claimant
    -- is refused as a duplicate and sends nothing.
    reserved_at     TIMESTAMPTZ,
    -- One row per SEND. Both legs of one send carry the same `expires` instant
    -- (@auth/core computes it once and hands the same value to each), so this
    -- is what makes the upsert converge instead of racing.
    CONSTRAINT auth_email_otp_token_send_key UNIQUE (identifier, expires)
);

-- EXPAND (R6), and idempotent: a box that already applied the first cut of this
-- migration has the table without `reserved_at`, and `CREATE TABLE IF NOT
-- EXISTS` is a no-op there. Nullable with no default, so old rows read as
-- "never claimed" — which for a table whose rows expire in ten minutes is both
-- true and inconsequential.
ALTER TABLE auth_email_otp_token
    ADD COLUMN IF NOT EXISTS reserved_at TIMESTAMPTZ;

-- The consume path's lookup: (identifier, token). Not UNIQUE — two sends in the
-- same second could in principle mint the same hash for the same address, and a
-- unique violation there would present as "the code is broken" rather than as
-- the astronomically unlikely collision it is. ⚠️ The consume statement
-- therefore does NOT assume the lookup is singular: it selects `ORDER BY
-- created_at DESC, id DESC LIMIT 1` into an `UPDATE … WHERE id = (…)`, so a
-- duplicate hash consumes the newest matching token instead of raising
-- `MultipleResultsFound` and 500ing the sign-in (repair of review finding P2,
-- 2026-08-23).
CREATE INDEX IF NOT EXISTS auth_email_otp_token_lookup_idx
    ON auth_email_otp_token (identifier, token);

-- The two rate-limit reads, both keyed on the identifier and a time window.
-- The send read also filters on `reserved_at IS NOT NULL`; at three rows per
-- address per hour the identifier prefix is the whole selectivity, so no
-- separate partial index earns its keep.
CREATE INDEX IF NOT EXISTS auth_email_otp_token_sends_idx
    ON auth_email_otp_token (identifier, created_at DESC);
CREATE INDEX IF NOT EXISTS auth_email_otp_token_attempts_idx
    ON auth_email_otp_token (identifier, last_attempt_at DESC);

COMMENT ON TABLE auth_email_otp_token IS
    'WS-31 CP-2d slice 2: the Auth.js email-OTP verification token, plus its '
    'own rate-limit counters (5 verify attempts / 10 min, 3 sends / hour, per '
    'identifier). RLS-EXEMPT by design — minted pre-session for an address that '
    'may belong to no organization, so there is no organization_id to stamp; '
    'listed in scripts/gen_tenant_migration.EXEMPT with that reason. Stores a '
    'SHA-256 of the code salted with AUTH_SECRET, never the code.';
