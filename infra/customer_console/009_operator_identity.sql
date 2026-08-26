-- ============================================================================
-- 009_operator_identity.sql — who a platform operator is, and what they may do
-- ============================================================================
-- Spec: project-docs/specs/operator_identity_and_access.md §7 · D64.
-- Board: work_plan.md §2 WS-31, ticket CP-12a.
--
-- ── What forced this ────────────────────────────────────────────────────────
-- The Operator Console has been deployed since 2026-08-22 behind ONE shared
-- passphrase (`OPERATOR_CONSOLE_STAFF_SECRET`). Four measured consequences,
-- and each one of these tables answers one of them:
--
--   * there is no identity, so `control_audit.actor` records the literal string
--     `operator` for every write and the log cannot name a person → `operator`;
--   * the cookie holds the passphrase ITSELF, with no expiry and no row to
--     revoke → `operator_session`;
--   * anybody who signs in can purge an organization → `operator.role`;
--   * nothing is time-boxed, so a destructive privilege is held at rest
--     → `operator_elevation`.
--
-- ⚠️ THESE TABLES ARE DELIBERATELY NOT TENANT-SCOPED, and that is not an
-- oversight for a reviewer to catch. They belong to the CROSS-TENANT plane —
-- an operator is staff, and staff are not a tenant's members. R5(a) is
-- satisfied the way CP-1 satisfied it: `infra/customer_console/` is the
-- Console's OWN ladder, `apply_migrations.sh` does not replay it into the
-- tenant database, and `gen_tenant_migration.py` does not scan it. There is
-- deliberately no `organization_id` here. A column with one would be the bug.
--
-- ⚠️ NO `control_audit` CHANGE. `actor` is already TEXT and already holds an
-- EMAIL under the deployment-key scheme (main.py:1365), and CP-2g's purge
-- scrubber already rewrites email-shaped actors. Adding a second actor column
-- would be the second implementation root CLAUDE.md §5 forbids. CP-12b changes
-- what the existing column is GIVEN, not its shape.
--
-- ── R6, expand/contract ─────────────────────────────────────────────────────
-- The deploy applies migrations BEFORE restarting services, so the code running
-- when this lands is the code that predates it. Nothing here can break that
-- code: three brand-new tables, nothing renamed, nothing dropped, no existing
-- column touched. Code that predates this file cannot notice it exists.
--
-- Every statement is `IF NOT EXISTS`. The R8 suites replay the whole ladder
-- twice (`tests/unit/_customer_console_ladder.py`), so a statement that is not
-- replay-safe fails on a real server rather than in review.
-- ============================================================================


-- ── The registry: who may operate the platform ──────────────────────────────
--
-- The directory answers "who are you". THIS table answers "may you". Both must
-- agree before anybody is admitted (spec §4.1), and the second question is the
-- one a directory cannot answer: without this row, every person our Entra
-- directory ever admits becomes a platform operator on their first sign-in.
-- That is D34.4 ("Supabase Auth authenticates; it never decides entitlement")
-- applied to staff rather than to customers.
CREATE TABLE IF NOT EXISTS operator (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The join to the directory identity. Stored lower-case by the writer, and
    -- UNIQUE, because two rows for one person would make "what is their role"
    -- a question with two answers.
    email             TEXT NOT NULL UNIQUE,

    -- D64.3. Three roles, and the matrix that binds them to routes is spec §5.
    -- `viewer` is the column default on purpose: the narrow role is the one
    -- anything creates by accident, exactly as `deployment_key.capabilities`
    -- defaults to the narrow `{resolve}` (006).
    role              TEXT NOT NULL DEFAULT 'viewer'
                      CHECK (role IN ('viewer', 'editor', 'admin')),

    -- `deactivated` SEALS, it does not erase (D63, spec §6.1 guard 3). The row
    -- stays so the person's `control_audit` history stays readable — deleting
    -- an operator would silently orphan the audit trail that is the whole
    -- point of naming them.
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'suspended', 'deactivated')),

    -- The Entra object id, learned on first successful sign-in and NULL until
    -- then: an operator is added by EMAIL (the only identifier a human knows
    -- before the person has ever signed in). Recording the subject afterwards
    -- is what survives an email change at the directory.
    directory_subject TEXT,

    -- Who added them. NULL for the bootstrap row, which by construction has no
    -- adder — see `operators.bootstrap`.
    added_by          UUID REFERENCES operator (id),

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The admission read is `WHERE lower(email) = :email AND status = 'active'`.
-- The UNIQUE on `email` already indexes the first leg.

-- ⚠️ There is NO constraint here enforcing "at least one active admin". It
-- cannot be written as a CHECK (a row-level constraint cannot see the rest of
-- the table) and a trigger would fire on the bootstrap INSERT, when the
-- invariant is legitimately not yet true. The last-admin guard is therefore
-- CODE (`operators.py`), and R7 requires it name its fence: the guard is
-- `test_operator_identity.py::test_the_last_active_admin_cannot_be_demoted`,
-- shown red first. A reviewer who expects a constraint should read this note
-- rather than add one.


-- ── The session: an opaque token, never the passphrase ──────────────────────
--
-- Today the cookie holds the shared secret itself, so a disclosed cookie is a
-- disclosed passphrase and there is nothing to revoke. This table is the fix:
-- the browser holds `cc_sess_<prefix>_<secret>` and the database holds only the
-- HASH, exactly as `llm_api_key` and `deployment_key` already do.
--
-- ⚠️ A FOURTH VALUE in `keys.py`, not a fourth implementation. `mint_key`,
-- `hash_secret`, `verify_secret` and `split_key` are reused unchanged. That is
-- the precedent `keys.py` sets for the discount code in its own docstring, and
-- following it is why this table has `prefix` + `key_hash` and not a scheme of
-- its own.
CREATE TABLE IF NOT EXISTS operator_session (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id  UUID NOT NULL REFERENCES operator (id),

    -- Clear, indexed, safe to log. Lookup is one indexed read rather than a
    -- scan-and-compare against every hash in the table.
    prefix       TEXT NOT NULL UNIQUE,
    key_hash     TEXT NOT NULL,

    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The ABSOLUTE expiry. NOT NULL and with no default: a session row whose
    -- lifetime the writer forgot to set would be a session that never ends,
    -- which is the defect this table exists to remove. The writer must say.
    expires_at   TIMESTAMPTZ NOT NULL,

    -- The IDLE clock. Separate from `issued_at` because "signed in 10 hours
    -- ago" and "last did something 10 hours ago" are different facts and only
    -- the second one should log a person out mid-task.
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Server-side revocation — the answer to "removing one person means
    -- changing the secret for everybody". Set on sign-out, and set on EVERY
    -- row for an operator in the same transaction that deactivates them.
    revoked_at   TIMESTAMPTZ,

    -- Recorded for the audit trail, not consulted for authentication. Binding a
    -- session to an IP would break every operator on a mobile network, and
    -- pretending otherwise is how a security control becomes a support ticket.
    ip           INET,
    user_agent   TEXT
);

-- The revocation sweep is `WHERE operator_id = :id AND revoked_at IS NULL`.
CREATE INDEX IF NOT EXISTS operator_session_live_idx
    ON operator_session (operator_id)
    WHERE revoked_at IS NULL;


-- ── The elevation window: the right to act, not the privilege at rest ───────
--
-- D64.4. An `admin` does not HOLD purge, suspend, key issuance or a large
-- credit grant. They hold the right to open a window, do the work, and let it
-- close. The recorded failure mode of just-in-time access is temporary access
-- that nobody expires, which is why `expires_at` is NOT NULL here and is
-- enforced by the Console rather than by the browser.
CREATE TABLE IF NOT EXISTS operator_elevation (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id UUID NOT NULL REFERENCES operator (id),

    -- REQUIRED, and the length floor lives in code so the refusal can say what
    -- is wrong. A reason is what makes the audit row answer "why" instead of
    -- only "who" and "what".
    reason      TEXT NOT NULL,

    -- Optional, and it follows SC-4g's `<reason>:<ref>` grammar. ONE reference
    -- vocabulary in this service, not a second one invented here.
    reference   TEXT,

    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

-- The "is a window open right now" read.
CREATE INDEX IF NOT EXISTS operator_elevation_live_idx
    ON operator_elevation (operator_id, expires_at DESC)
    WHERE revoked_at IS NULL;
