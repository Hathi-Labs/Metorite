-- ============================================================================
-- 007_payments.sql — the money -> entitlement path: orders, events, discounts
-- ============================================================================
-- Spec: customer_console.md §6 CP-9 §9.2 (the order tables and their state
-- machine) · subscription_console.md SC-4g (ii) (the two discount tables).
-- The two tickets share ONE migration because they are one schema change: a
-- redemption references an order, so splitting them across two numbers would
-- create a window in which the FK has no target.
--
-- ⚠️ NUMBER TAKEN AT BUILD TIME (R1). The ladder was 001-006 on disk and no
-- branch, local or remote, carried a 007 — re-checked at merge, because three
-- collisions in two weeks came from trusting a number written down earlier.
--
-- ⚠️ EVERY MONEY COLUMN HERE IS INTEGER PAISE, and that is the point of the
-- file. `plan_catalog.price_inr` stays NUMERIC(12,2) rupees — prices are data
-- and the D23/D24 ladder is denominated in rupees — and exactly one named
-- function (`payments.paise`) converts, at order creation. Razorpay's API is
-- denominated in paise, and a rupee amount that round-trips through JSON as a
-- float is how Rs 1,800.00 becomes Rs 1,799.99 in a place nobody looks. A
-- NUMERIC/REAL/DOUBLE column added to any table below is a defect and
-- `test_customer_console_payments.py` fails on it structurally.
--
-- ⚠️ NO ROW-LEVEL SECURITY, as everywhere on this ladder: this is the Control
-- plane (saas_multitenancy.md §0.9.2), cross-tenant by design. Scoping is done
-- by the route's ownership predicate (CP-9 §9.3(7)) — every order route
-- resolves the organization FROM THE KEY and answers one byte-identical 404
-- for "another org's order" and "no such order".
--
-- R6: additive, `IF NOT EXISTS` throughout, nothing renamed, no existing
-- column touched. The suites replay the whole ladder twice
-- (`tests/unit/_customer_console_ladder.py`), so a statement that is not
-- replay-safe fails on a real server rather than in review.
-- ============================================================================


-- ── CP-9 §9.2 · the order ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS payment_order (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Deliberately NOT `ON DELETE CASCADE`, unlike its neighbours on this
    -- ladder. An order is a financial record: it must not disappear because a
    -- row upstream was deleted. Nothing in the tree deletes an organization
    -- (`deleted` is a lifecycle STATUS, reached only through the export
    -- window), so this costs nothing today and refuses loudly if that changes.
    organization_id     UUID NOT NULL REFERENCES organization(id),
    -- `created -> attempted -> captured | failed | abandoned`, and the last
    -- three are TERMINAL — no edge leaves them. The graph itself lives in
    -- `customer_console.payments`, which extends `lifecycle.can_transition`'s
    -- idiom rather than inventing a second one; this CHECK is the vocabulary,
    -- not the machine.
    --
    -- `abandoned` is written by EXPIRY, never by the customer: a customer who
    -- walks away tells you nothing, and an order that stays `created` forever
    -- is the state that makes "how many orders are open" unanswerable.
    status              TEXT NOT NULL DEFAULT 'created'
                        CHECK (status IN ('created', 'attempted', 'captured',
                                          'failed', 'abandoned')),
    -- 'none' = the Rs 0 redemption path, which deliberately never reaches a
    -- provider (SC-4g (iv)). ⚠️ Not to be confused with
    -- `org_subscription.provider`, which is CHECK (razorpay|manual) and takes
    -- NULL on that path — writing 'none' there violates its constraint.
    provider            TEXT NOT NULL CHECK (provider IN ('razorpay', 'none')),
    -- NULL on the 'none' path. UNIQUE so one provider order can back exactly
    -- one of ours — the webhook resolves our order through this column, and a
    -- duplicate would make that resolution ambiguous at the worst moment.
    provider_order_id   TEXT UNIQUE,

    gross_paise         BIGINT NOT NULL CHECK (gross_paise >= 0),
    discount_paise      BIGINT NOT NULL DEFAULT 0 CHECK (discount_paise >= 0),
    -- gross - discount: the GST base. GST is computed AFTER the discount
    -- (SC-4g (iii)) — standard GST invoice practice, and the only reading
    -- under which 100 percent off yields taxable 0 -> GST 0 -> total 0, which
    -- is what D42 requires.
    taxable_paise       BIGINT NOT NULL CHECK (taxable_paise >= 0),
    gst_paise           BIGINT NOT NULL CHECK (gst_paise >= 0),
    total_paise         BIGINT NOT NULL CHECK (total_paise >= 0),
    -- The arithmetic, enforced by the database rather than by whoever writes
    -- the next route. Three CHECKs beyond the spec's sketch, deliberately: the
    -- columns are redundant by design (they are the invoice's own lines) and
    -- redundancy without a constraint is just an opportunity to disagree.
    CHECK (discount_paise <= gross_paise),
    CHECK (taxable_paise = gross_paise - discount_paise),
    CHECK (total_paise = taxable_paise + gst_paise),

    -- Snapshot, never a join (CP-9 §9.6): SC-5e lets an admin edit the billing
    -- state, and "changing the state changes the tax treatment of the NEXT
    -- invoice and never a past one".
    gst_split           TEXT CHECK (gst_split IN ('cgst_sgst', 'igst')),
    customer_gstin      TEXT,
    place_of_supply     TEXT,

    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Stamped when the order reaches a terminal state, by the one transition
    -- function. NULL means "still open", which is a question an operator asks.
    terminal_at         TIMESTAMPTZ
);

COMMENT ON TABLE payment_order IS
    'A pending INTENT. Creating one moves no value: it writes this table and '
    'payment_order_line and nothing else — no entitlement, no seat, no ledger '
    'row, no subscription change. That is what makes it safe to reach with the '
    'customer read-only organization key (CP-9 §9.3). Value moves on exactly '
    'two events, both carrying an authority the caller does not hold: a '
    'signature-verified provider webhook, or redemption of an operator-issued '
    'discount code.';

COMMENT ON COLUMN payment_order.provider_order_id IS
    'The provider identifier the webhook arrives with. NULL on the Rs 0 path, '
    'which never reaches a provider at all.';

CREATE INDEX IF NOT EXISTS payment_order_org_created_idx
    ON payment_order (organization_id, created_at DESC, id DESC);


CREATE TABLE IF NOT EXISTS payment_order_line (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id            UUID NOT NULL REFERENCES payment_order(id),
    -- REFERENCES the catalog, so an order cannot name a plan that does not
    -- exist. The route additionally refuses a plan that is not ACTIVE — the FK
    -- cannot express that, and 9.1's rule is that the checkout must not be able
    -- to sell a thing the catalog does not price.
    plan_slug           TEXT NOT NULL REFERENCES plan_catalog(slug),
    quantity            INT NOT NULL CHECK (quantity > 0),
    -- SNAPSHOT of the catalog price at order time, in paise. Prices are data
    -- and data moves; an order priced by a join would silently re-price itself
    -- when the catalog changed, which is the invoice equivalent of a mutable
    -- balance column.
    unit_price_paise    BIGINT NOT NULL CHECK (unit_price_paise >= 0)
);

CREATE INDEX IF NOT EXISTS payment_order_line_order_idx
    ON payment_order_line (order_id);


CREATE TABLE IF NOT EXISTS payment_event (
    -- TRANSPORT-level dedup, and only that. It makes the SAME delivery,
    -- delivered twice, a no-op — the retry case and nothing more.
    --
    -- ⚠️ It is NOT the money guard, and believing it is would double-grant:
    -- Razorpay sends DIFFERENT event ids for one capture (`payment.captured`
    -- and `order.paid` are two events, two ids, one payment), so this key does
    -- not see them as duplicates and never will. What makes the second one
    -- harmless is the terminal-state rule on payment_order.status (CP-9 §9.5).
    provider_event_id   TEXT PRIMARY KEY,
    -- FK-LESS on purpose: an event may arrive for an order this database has
    -- never heard of (a stray delivery, a wrong-account webhook), and the
    -- honest response is to record it, not to reject it with an integrity
    -- error. The receipt is evidence even when the reference resolves to
    -- nothing.
    order_id            UUID,
    kind                TEXT,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    body                JSONB
);

COMMENT ON TABLE payment_event IS
    'Every signature-verified delivery, recorded. Both events of one capture '
    'land here; exactly one of them fulfils. Nothing unverified ever reaches '
    'this table — the signature is checked before the body is parsed as '
    'anything but bytes (CP-9 §9.5).';

CREATE INDEX IF NOT EXISTS payment_event_order_idx
    ON payment_event (order_id, received_at DESC);


-- ── SC-4g (ii) · discount codes ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS discount_code (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The split-key pattern, reused verbatim from `keys.py`: a prefix in the
    -- clear and indexed, and a SHA-256 hash of the secret. A database
    -- disclosure hands over no working codes, and lookup is one indexed read
    -- rather than a scan-and-compare. Format `cc_disc_<prefix>_<secret>`.
    prefix              TEXT NOT NULL UNIQUE,
    code_hash           TEXT NOT NULL,
    -- What it is FOR. Lands on the audit row at issue, so a code is
    -- explainable a year later without asking whoever minted it.
    label               TEXT NOT NULL,
    -- NULL = an OPEN code any organization may present. Non-NULL binds it.
    organization_id     UUID REFERENCES organization(id),
    kind                TEXT NOT NULL CHECK (kind IN ('percent', 'fixed')),
    -- BASIS POINTS, never a float: 33 and a third percent has no float
    -- representation and a discount that rounds differently on two reads is a
    -- dispute. 10000 = 100 percent.
    percent_bp          INT CHECK (percent_bp BETWEEN 1 AND 10000),
    amount_paise        BIGINT CHECK (amount_paise > 0),
    CHECK ((kind = 'percent') = (percent_bp IS NOT NULL)),
    CHECK ((kind = 'fixed')   = (amount_paise IS NOT NULL)),
    -- Defaulting to 1 is the SAFE default: the dangerous mistake is a code
    -- intended for one customer that turns out to be reusable, never the
    -- reverse.
    max_redemptions     INT NOT NULL DEFAULT 1 CHECK (max_redemptions > 0),
    expires_at          TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    created_by          TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE discount_code IS
    'A bearer secret that grants value, so it is stored the way this service '
    'already stores bearer secrets (keys.py): prefix in the clear, SHA-256 of '
    'the secret, and the operator sees the full code ONCE at issue. A code '
    'that must be re-read later is re-issued, not recovered — a recoverable '
    'discount code is a shared password in a database somebody exports.';

COMMENT ON COLUMN discount_code.max_redemptions IS
    'Enforced by COUNT(discount_redemption) under an advisory lock, never by a '
    'mutable times_used column — the same append-only argument credit_ledger '
    'makes, and the same reason.';


CREATE TABLE IF NOT EXISTS discount_redemption (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discount_code_id    UUID NOT NULL REFERENCES discount_code(id),
    organization_id     UUID NOT NULL REFERENCES organization(id),
    order_id            UUID NOT NULL REFERENCES payment_order(id),
    -- gross · discount · net, recorded per redemption whether or not an
    -- invoice document is ever rendered (SC-4g (vi)): SC-5b/5c own the
    -- DOCUMENT, this owns the AMOUNTS.
    gross_paise         BIGINT NOT NULL CHECK (gross_paise >= 0),
    discount_paise      BIGINT NOT NULL CHECK (discount_paise >= 0),
    net_paise           BIGINT NOT NULL CHECK (net_paise >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One order can never redeem one code twice, enforced by the database
    -- rather than by the route remembering to look.
    UNIQUE (discount_code_id, order_id)
);

COMMENT ON TABLE discount_redemption IS
    'The launch carrier of "a discounted purchase is tellable from a paid one a '
    'year later" (SC-4g done-when 6). It rides here and on seat_grant.reason '
    'rather than on credit_ledger, because the subscription path writes ZERO '
    'ledger rows until credit packs are priced — a three-way test over an empty '
    'table passes for the wrong reason.';

CREATE INDEX IF NOT EXISTS discount_redemption_order_idx
    ON discount_redemption (order_id);

CREATE INDEX IF NOT EXISTS discount_redemption_org_idx
    ON discount_redemption (organization_id, created_at DESC);
