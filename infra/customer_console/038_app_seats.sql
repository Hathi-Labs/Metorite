-- ============================================================================
-- 038_app_seats.sql — the Aroh app for an organization, with N seats
-- ============================================================================
-- Spec: customer_console.md §6 "CP-APP · The Aroh app for an organization".
-- Task: Metorite task 1105 (mt-7d3489fd).
--
-- The operator gives an organization the Aroh app with a number of seats. A
-- member of that organization signs in to Aroh with Metorite and takes a seat
-- at once. Aroh's AI use comes from the organization's own AI credits.
--
-- What this file adds, and why each part is the smallest one that works:
--
-- 1. A plan row `aroh`, of the new kind `app`, INACTIVE and priced at zero.
--    An Aroh seat is then an ordinary `seat_grant` and `seat_assignment` row,
--    so the one seat vocabulary (`seats.py`), the capacity lock and the
--    partial unique index all serve it with no second copy. INACTIVE keeps it
--    out of the checkout, the seat grid and the MRR. The operator gives these
--    seats. Nobody buys them.
-- 2. The seat source `app`. An Aroh seat is not a Center, a plan or an
--    a-la-carte seat, and the source says which door put it there.
-- 3. `deployment.app`. It names the app a box serves. NULL is a Metorite
--    tenant box, which is every row that exists today. A unique partial index
--    keeps one box for each app.
-- 4. `org_app`. One row says that an organization has an app, on which box,
--    and whether it is on. It is the app twin of `org_placement`. An
--    organization keeps its ONE `org_placement` row on its Metorite box, so
--    the Aroh box must not take that row. The seat COUNT is not here. It is
--    the `seat_grant` sum for the plan `aroh`, so there is one count.
-- 5. `app_connection`. How the Console reaches the app's hub to push the
--    box's deployment key ("Connect Aroh", owner request 2026-10-30).
--
-- R6: expand only. Two new tables, one new nullable column, two CHECKs made
-- wider, one INSERT. Nothing renamed. Nothing dropped except the two CHECKs,
-- each replaced in the same block by a wider one.
-- R1: 037 is the highest number on disk at build time. Check again at merge.
--
-- This file contains no per-cent character, for the reason 003 gives.
--
-- Fence: tests/unit/test_customer_console_aroh_app.py (R8).
-- ============================================================================


-- ── 1. The plan kind `app` ──────────────────────────────────────────────────
--
-- 001 wrote the kind CHECK inline, so Postgres named it. The block drops every
-- CHECK on `plan_catalog.kind` that is not the new one, then adds the new one.
-- It runs once: on a replay the new constraint exists and the block does
-- nothing.

DO $$
DECLARE
    old_name TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'plan_catalog_kind_known'
    ) THEN
        FOR old_name IN
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = 'plan_catalog'::regclass
              AND c.contype = 'c'
              AND a.attname = 'kind'
        LOOP
            EXECUTE 'ALTER TABLE plan_catalog DROP CONSTRAINT '
                    || quote_ident(old_name);
        END LOOP;
        ALTER TABLE plan_catalog
            ADD CONSTRAINT plan_catalog_kind_known
            CHECK (kind IN ('core', 'center', 'addon', 'bundle', 'app'));
    END IF;
END $$;


-- ── 2. The seat source `app` ────────────────────────────────────────────────

DO $$
DECLARE
    old_name TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'seat_assignment_source_known'
    ) THEN
        FOR old_name IN
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = 'seat_assignment'::regclass
              AND c.contype = 'c'
              AND a.attname = 'source'
        LOOP
            EXECUTE 'ALTER TABLE seat_assignment DROP CONSTRAINT '
                    || quote_ident(old_name);
        END LOOP;
        ALTER TABLE seat_assignment
            ADD CONSTRAINT seat_assignment_source_known
            CHECK (source IN ('core', 'center', 'plan', 'alacarte', 'app'));
    END IF;
END $$;


-- ── 3. The Aroh seat plan ───────────────────────────────────────────────────
--
-- `DO NOTHING` on a replay: an operator who later edits the row keeps the
-- edit. INACTIVE on purpose, see the header.

INSERT INTO plan_catalog (slug, name, kind, price_inr, active, sort_order)
VALUES ('aroh', 'Aroh', 'app', 0.00, FALSE, 950)
ON CONFLICT (slug) DO NOTHING;


-- ── 4. The box that serves an app ───────────────────────────────────────────

ALTER TABLE deployment
    ADD COLUMN IF NOT EXISTS app TEXT REFERENCES plan_catalog (slug);

COMMENT ON COLUMN deployment.app IS
    'The app this box serves, or NULL for a Metorite tenant box. A deployment '
    'key of an app box resolves members through org_app, never through '
    'org_placement, and it takes only the seat of its own app.';

CREATE UNIQUE INDEX IF NOT EXISTS deployment_app_uniq
    ON deployment (app) WHERE app IS NOT NULL;


-- ── 5. The app of an organization ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS org_app (
    organization_id UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE,
    -- The app, which is also the slug of its seat plan.
    app             TEXT NOT NULL REFERENCES plan_catalog (slug),
    -- The box that serves the app to this organization. Copied from the box
    -- that `deployment.app` names when the operator turns the app on. A later
    -- change of that box does not move this row: a placement never moves on
    -- its own (D46.6 item 4).
    deployment_id   UUID NOT NULL REFERENCES deployment (id),
    -- OFF hides the organization from the app box. The seats and the count
    -- stay, so ON again gives each holder the same seat back.
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, app)
);

CREATE INDEX IF NOT EXISTS org_app_deployment_idx
    ON org_app (deployment_id, app) WHERE enabled;

COMMENT ON TABLE org_app IS
    'An organization has an app (Aroh) on a box. The app twin of '
    'org_placement: a deployment key of an app box sees the members of an '
    'organization only while its row here is enabled. The seat count is the '
    'seat_grant sum for the app plan, never a column here.';


-- ── 6. The connection to an app's hub ("Connect Aroh") ──────────────────────
--
-- Owner request, 2026-10-30: the operator connects Aroh from the Operator
-- Console. The Console mints the app box's deployment key and pushes it to
-- the hub (`PUT <base_url>/hub/api/router-key`). One row for each app.
--
-- ⚠️ `operator_token_enc` is the hub's operator token, ENCRYPTED with the key
-- that encrypts `provider_credential.secret_enc` (CUSTOMER_CONSOLE_ENCRYPTION_KEY).
-- No column holds it in the clear, and no read returns it.
-- `key_prefix` is the PUBLIC half of the key the hub holds now. The secret of
-- that key exists only on the hub.

CREATE TABLE IF NOT EXISTS app_connection (
    app                 TEXT PRIMARY KEY REFERENCES plan_catalog (slug),
    base_url            TEXT NOT NULL,
    operator_token_enc  TEXT,
    operator_token_last4 TEXT,
    key_prefix          TEXT,
    connected_at        TIMESTAMPTZ,
    last_result         JSONB,
    updated_by          TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE app_connection IS
    'How the Console reaches the hub of an app (Aroh): its base URL, its '
    'operator token (encrypted), and the prefix of the deployment key the hub '
    'holds now. Written only by the operator doors under /apps/connection.';
