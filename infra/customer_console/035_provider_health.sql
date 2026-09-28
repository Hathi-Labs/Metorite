-- Provider health: the balance of each vendor account we call on, and the
-- refusals it gave us. Owner request, 2026-09-28.
--
-- 🔴 **Why this exists.** From 2026-09-26 to 2026-09-28 the DeepSeek account
-- held -0.05 USD. DeepSeek refused every call with HTTP 402, the Router mapped
-- that to a 502 "upstream provider error", and all AI on the platform failed
-- for two days. Nobody was told, because nothing watched the account.
--
-- Two tables, because they answer two different questions:
--
--   provider_health   ONE row per provider. What the vendor SAID the last time
--                     we asked it (the balance probe), the per-provider low
--                     threshold, and the alert state we last logged.
--   provider_refusal  An append log of refusals the Router SAW on the serving
--                     path (401/402/403/429 and 5xx). One row per provider and
--                     status per flush, with a count. This is the only signal
--                     for a vendor that does not expose its balance.
--
-- ⚠️ **Platform data, not tenant data.** Neither table carries an
-- `organization_id`. A refusal from a customer's OWN key (BYOK) is never
-- written here: it says nothing about OUR account. `provider_balance.py`
-- filters it before the row exists.
--
-- ⚠️ **No secret, and no fragment of one, in either table.** `probe_error`
-- holds a short sanitized reason ("http 401", "timeout"), never a vendor body.
-- `test_provider_balance.py` pins that.
--
-- ⚠️ **R6 — expand only, and idempotent.** The ladder replays every file on
-- every deploy, so every statement is IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS provider_health (
    provider            text PRIMARY KEY,
    -- What the vendor reported. NULL = not exposed, or never read.
    --
    -- ⚠️ **`funds_left`, and NOT `balance`, on purpose.** The ladder refuses
    -- any column named `*balance*` (`test_customer_console_credits.py`):
    -- a CUSTOMER's credit balance is SUM(credit_ledger.delta), never a
    -- stored number. This is a figure a VENDOR states about OUR account, and
    -- a different name keeps the two from ever being confused.
    funds_left          numeric(20,6),
    -- The vendor's own currency. Nothing is converted: a CNY balance stays
    -- CNY, and a days-left estimate is only drawn for USD.
    currency            text,
    -- The vendor's own "can this account serve" flag, where it gives one.
    available           boolean,
    funds_checked_at    timestamptz,
    -- ok | not_exposed | failed. NULL = never probed.
    probe_status        text,
    probe_error         text,
    -- The HTTP status of a failed probe, when the vendor answered at all. A
    -- 401 here means the vendor refused OUR key, which the Router meets too.
    probe_http          integer,
    -- Per-provider override of the env default. NULL = use the default.
    low_threshold       numeric(20,6),
    -- The status we last LOGGED. Compared-and-set, so each transition logs
    -- once across every worker and every restart.
    alert_state         text,
    alert_changed_at    timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT provider_health_probe_status_known
        CHECK (probe_status IS NULL
               OR probe_status = ANY (ARRAY['ok'::text, 'not_exposed'::text, 'failed'::text]))
);

CREATE TABLE IF NOT EXISTS provider_refusal (
    id          bigserial PRIMARY KEY,
    provider    text NOT NULL,
    status      integer NOT NULL,
    refusals    integer NOT NULL CHECK (refusals > 0),
    first_at    timestamptz NOT NULL,
    last_at     timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS provider_refusal_provider_last_idx
    ON provider_refusal (provider, last_at DESC);

COMMENT ON TABLE provider_health IS
    'One row per vendor we call on: the last balance it reported, and the alert '
    'state we last logged. Platform data. Never holds a secret.';

COMMENT ON TABLE provider_refusal IS
    'Refusals the Router saw from a vendor on OUR platform key (never BYOK), '
    'counted per flush. The alert for a vendor that exposes no balance.';
