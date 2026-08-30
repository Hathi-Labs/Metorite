"""SQL for the Customer Console. Fetches rows; decides nothing.

Every arithmetic and policy decision lives in :mod:`customer_console.seats` and
:mod:`customer_console.credits` as pure functions. This module's only job is to put
rows in and take rows out, so that "how many seats are free" has exactly one
implementation rather than one per caller.

⚠️ **R8 binds this file specifically.** Its subject is queries and predicates,
and hermetic fakes agree with whatever SQL they are handed — five live bugs
shipped green that way (an unencodable ``CAST(:param AS timestamptz)``; a fake
matching ``lower(col) = :param`` against NULL; a ``LEFT JOIN`` whose ``ON``
could not see its table). ``tests/unit/test_customer_console_sql.py`` runs all of it
against a real Postgres and skips loudly when none is configured.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from customer_console.credits import LEDGER_REASON_USAGE

__all__ = [
    "activate_subscription",
    "active_plans",
    "add_credit",
    "apply_discount_to_order",
    "capture_provider_refs",
    "count_redemptions",
    "create_order",
    "credit_deltas",
    "credit_ledger_rows",
    "credit_ref_row",
    "cross_org_summary",
    "current_placement",
    "deployment_by_label",
    "deployment_visible_orgs",
    "detach_provider",
    "ensure_identity",
    "ensure_organization",
    "grant_seats",
    "has_live_seat",
    "issue_deployment_key",
    "issue_discount_code",
    "lock_discount_capacity",
    "lock_seat_capacity",
    "operator_active_admin_count",
    "operator_by_email",
    "operator_by_id",
    "operator_count",
    "operator_elevation_close",
    "operator_elevation_live",
    "operator_elevation_open",
    "operator_insert",
    "operator_list",
    "operator_session_by_prefix",
    "operator_session_insert",
    "operator_session_revoke",
    "operator_session_touch",
    "operator_sessions_revoke_all",
    "operator_set_directory_subject",
    "operator_set_role",
    "operator_set_status",
    "order_by_provider_id",
    "order_for_update",
    "order_lines",
    "order_row",
    "orders_page",
    "org_members",
    "place_organization",
    "plan_price",
    "priced_plan",
    "record_payment_event",
    "record_usage",
    "redemption_for_order",
    "release_seat",
    "resolve_deployment_key",
    "resolve_discount_code",
    "resolve_key",
    "run_spend",
    "seat_rows",
    "set_order_status",
    "set_provider_order_id",
    "try_assign_seat",
    "write_redemption",
]


# ── Registry ────────────────────────────────────────────────────────────────

def ensure_organization(
    conn: Connection, *, slug: str, name: str,
    gstin: str | None = None, billing_state: str | None = None,
) -> str:
    """Create the organization, or return the existing one with that slug.

    ``ON CONFLICT … DO UPDATE`` rather than ``DO NOTHING`` so the statement
    always RETURNS a row — ``DO NOTHING`` returns nothing on conflict, which
    would make a resumed provisioning run look like a failure and is the classic
    way an "idempotent" upsert turns out not to be.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO organization (slug, name, gstin, billing_state)
            VALUES (:slug, :name, :gstin, :billing_state)
            ON CONFLICT (slug) DO UPDATE
                SET updated_at = now()
            RETURNING id
            """
        ),
        {"slug": slug, "name": name, "gstin": gstin,
         "billing_state": billing_state},
    ).first()
    assert row is not None  # guaranteed by DO UPDATE
    return str(row[0])


def ensure_identity(conn: Connection, *, email: str,
                    display_name: str | None = None) -> str:
    """Create the global identity for a human, or return the existing one.

    ``email`` is CITEXT, so ``Ada@Corp.com`` and ``ada@corp.com`` are one
    identity. The tenant plane learned this the expensive way — migration 162
    had to retrofit ``lower(email)`` uniqueness onto ``app_user`` after
    duplicates already existed.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO user_identity (email, display_name)
            VALUES (:email, :display_name)
            ON CONFLICT (email) DO UPDATE
                SET display_name = COALESCE(user_identity.display_name,
                                            EXCLUDED.display_name)
            RETURNING id
            """
        ),
        {"email": email, "display_name": display_name},
    ).first()
    assert row is not None
    return str(row[0])


# ── Seats ───────────────────────────────────────────────────────────────────

def grant_seats(conn: Connection, *, org_id: str, plan_slug: str,
                quantity: int, reason: str | None = None) -> None:
    """Record a seat purchase (or, with a negative quantity, a reduction)."""
    conn.execute(
        text(
            """
            INSERT INTO seat_grant
                (organization_id, plan_slug, quantity_purchased, reason)
            VALUES (:org, :plan, :qty, :reason)
            """
        ),
        {"org": org_id, "plan": plan_slug, "qty": quantity, "reason": reason},
    )


def seat_rows(conn: Connection, *, org_id: str, plan_slug: str,
              now: datetime | None = None) -> tuple[list[int], int]:
    """Return ``(grant quantities in effect, live assignment count)``.

    The time filter is ``effective_from <= :now`` with ``:now`` supplied by the
    caller rather than ``now()`` inline, so a test can place a future-dated
    grant and prove it is not counted yet. A query that reads the database's
    clock cannot be tested for that without waiting.
    """
    params: dict[str, Any] = {"org": org_id, "plan": plan_slug, "now": now}
    grants = [
        int(r[0])
        for r in conn.execute(
            text(
                """
                SELECT quantity_purchased FROM seat_grant
                WHERE organization_id = :org AND plan_slug = :plan
                  AND effective_from <= COALESCE(:now, now())
                """
            ),
            params,
        )
    ]
    assigned = conn.execute(
        text(
            """
            SELECT count(*) FROM seat_assignment
            WHERE organization_id = :org AND plan_slug = :plan
              AND released_at IS NULL
            """
        ),
        {"org": org_id, "plan": plan_slug},
    ).scalar_one()
    return grants, int(assigned)


def lock_seat_capacity(conn: Connection, *, org_id: str,
                       plan_slug: str) -> None:
    """Serialise the capacity decision for one ``(org, plan)`` pair.

    ⚠️ **Without this, the seat cap is check-then-insert and the check does not
    hold.** ``seat_rows`` reads at READ COMMITTED and
    :func:`customer_console.seats.decide_assignment` then decides on a snapshot
    that another transaction is free to invalidate before the INSERT lands. The
    partial unique index (``seat_assignment_live_uniq``) does **not** close it:
    that index enforces *one seat per person*, which is a different claim from
    *N seats per organization*. Two concurrent first sign-ins for two different
    people with one seat left therefore both saw ``available == 1`` and both
    inserted — measured on a real server, 10 races, before this lock existed.

    Transaction-scoped (``_xact_``), so it is released by COMMIT or ROLLBACK
    and there is no unlock call to forget on the 409 path. Taken **before** the
    count, not between the count and the insert: a lock acquired after the read
    protects nothing, because the stale number is already in hand.

    The key is ``hashtext(org:plan)``. A hash collision between two different
    pairs costs one needless serialisation and is otherwise harmless — the
    failure mode of this design is slowness, never an over-assignment.

    **Who takes it, stated honestly:** both arms of ``POST /registry/resolve``,
    via the single ``_allocate_core_seat`` path. ``POST /billing/seats``
    (``main.py``) still does not, and its race is pre-existing — a finding
    recorded in ``customer_console.md`` §6 CP-2b, not closed here.
    """
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{org_id}:{plan_slug}"},
    )


def has_live_seat(conn: Connection, *, org_id: str, plan_slug: str,
                  identity_id: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1 FROM seat_assignment
                WHERE organization_id = :org AND plan_slug = :plan
                  AND user_identity_id = :identity AND released_at IS NULL
                """
            ),
            {"org": org_id, "plan": plan_slug, "identity": identity_id},
        ).first()
    )


def try_assign_seat(conn: Connection, *, org_id: str, plan_slug: str,
                    identity_id: str, source: str = "alacarte") -> bool:
    """Assign a seat. Returns False when the member already holds one.

    The ``ON CONFLICT`` clause names the *partial* unique index by repeating its
    predicate. That is what makes concurrent assignment safe: two requests for
    the same member race to INSERT, the index rejects the loser, and
    ``DO NOTHING`` turns the rejection into "already had one" instead of an
    integrity error surfacing as a 500.

    The caller must still consult :func:`customer_console.seats.decide_assignment`
    for the cap — this function enforces *uniqueness*, not *capacity*.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO seat_assignment
                (organization_id, plan_slug, user_identity_id, source)
            VALUES (:org, :plan, :identity, :source)
            ON CONFLICT (organization_id, plan_slug, user_identity_id)
                WHERE released_at IS NULL
                DO NOTHING
            RETURNING id
            """
        ),
        {"org": org_id, "plan": plan_slug, "identity": identity_id,
         "source": source},
    ).first()
    return row is not None


def release_seat(conn: Connection, *, org_id: str, plan_slug: str,
                 identity_id: str) -> bool:
    """Release a seat, freeing it immediately. Returns False if none was held.

    Sets ``released_at`` rather than deleting the row: who held which seat when
    is the audit trail behind an invoice, and the partial unique index means a
    released seat does not block the same member being re-assigned later.
    """
    row = conn.execute(
        text(
            """
            UPDATE seat_assignment SET released_at = now()
            WHERE organization_id = :org AND plan_slug = :plan
              AND user_identity_id = :identity AND released_at IS NULL
            RETURNING id
            """
        ),
        {"org": org_id, "plan": plan_slug, "identity": identity_id},
    ).first()
    return row is not None


def plan_price(conn: Connection, *, plan_slug: str) -> Decimal | None:
    return conn.execute(
        text("SELECT price_inr FROM plan_catalog WHERE slug = :slug AND active"),
        {"slug": plan_slug},
    ).scalar_one_or_none()


def active_plans(conn: Connection) -> list[dict[str, Any]]:
    """Every catalog row a customer may be sold, in the catalog's own order.

    ``active`` is in the WHERE clause for exactly :func:`priced_plan`'s reason
    and it is the same rule stated once more rather than a caller's option:
    `rnd` and `support` are seeded INACTIVE because their Centers do not exist
    yet, and a catalog READ that leaves the filter to its caller is a catalog
    read that eventually offers R&D Center for sale.

    The route converts ``price_inr`` with ``payments.paise()``, which raises on
    NULL — safe only because ``plan_catalog.price_inr`` is ``NOT NULL``. A
    migration that relaxes that constraint makes this read 500 catalog-wide.

    **Takes no organization, deliberately.** Prices are catalog data, the same
    for every customer (MT-2/SC-1a own per-org pricing and neither is built);
    a parameter this function does not have is a per-org price nobody can add
    here by accident.

    ``ORDER BY sort_order, slug`` — the tie-break is not decoration. Postgres
    is free to return equally-ranked rows in any order, and ``sort_order``
    carries no UNIQUE constraint and a ``DEFAULT 100``
    (``001_customer_console.sql``), so every row inserted without a rank ties
    with every other one. Ties are the default case rather than the exotic one,
    and without the tie-break two reads of an unchanged catalog can disagree
    about the ladder.

    Rupees out, exactly like :func:`plan_price` and :func:`priced_plan`: the
    ONE conversion to paise is ``payments.paise`` at the surface (§9.2), so
    this module never becomes a second place money changes denomination.
    """
    return [
        {"slug": r[0], "name": r[1], "kind": r[2], "price_inr": r[3],
         "sort_order": r[4]}
        for r in conn.execute(
            text(
                """
                SELECT slug, name, kind, price_inr, sort_order
                FROM plan_catalog
                WHERE active
                ORDER BY sort_order, slug
                """
            )
        )
    ]


# ── Credits and usage ───────────────────────────────────────────────────────

def credit_deltas(conn: Connection, *, org_id: str) -> list[Decimal]:
    """Every ledger delta for the org. Balance is their sum, never a column."""
    return [
        r[0]
        for r in conn.execute(
            text("SELECT delta FROM credit_ledger WHERE organization_id = :org"),
            {"org": org_id},
        )
    ]


def run_spend(conn: Connection, *, org_id: str, run_id: str) -> Decimal:
    """Credits already drawn by one agent run — the circuit breaker's input.

    Summed in Postgres rather than by pulling rows into Python: a runaway loop
    is exactly the case where the row count is large, and it is the case the
    query exists for.

    ``COALESCE`` because a run with no usage yet must be **0**, not ``None`` —
    an empty aggregate returning NULL would make the breaker's comparison raise
    on the first call of every run, i.e. on every run. Hermetic fakes are happy
    to return either, which is why this is asserted against a real server (R8).

    Matches the partial index ``usage_event_run_idx (organization_id, run_id)
    WHERE run_id IS NOT NULL`` from migration 003, so the check stays an index
    lookup on the hot path.
    """
    total = conn.execute(
        text(
            """
            SELECT COALESCE(SUM(billed_credits), 0) FROM usage_event
            WHERE organization_id = :org AND run_id = :run
            """
        ),
        {"org": org_id, "run": run_id},
    ).scalar_one()
    return Decimal(total)


def add_credit(conn: Connection, *, org_id: str, delta: Decimal,
               reason: str, ref: str | None = None) -> None:
    conn.execute(
        text(
            """
            INSERT INTO credit_ledger (organization_id, delta, reason, ref)
            VALUES (:org, :delta, :reason, :ref)
            """
        ),
        {"org": org_id, "delta": delta, "reason": reason, "ref": ref},
    )


def credit_ref_row(conn: Connection, *, org_id: str, reason: str,
                   ref: str) -> Any:
    """The EARLIEST ledger row carrying this (reason, ref), or None.

    🔴 The manual-payment fence: an operator crediting a bank transfer types
    its reference, and typing the same reference twice is almost always the
    same transfer entered twice. The grant route refuses the second write
    and names this row, so the refusal carries its own evidence.

    Keyed on (reason, ref) rather than ref alone, deliberately — an
    `adjustment` correcting a `manual` row legitimately cites the SAME
    reference, and must not be blocked by it.
    """
    return conn.execute(
        text(
            "SELECT delta, created_at FROM credit_ledger "
            "WHERE organization_id = :org AND reason = :reason AND ref = :ref "
            "ORDER BY created_at LIMIT 1"
        ),
        {"org": org_id, "reason": reason, "ref": ref},
    ).first()


def credit_ledger_rows(conn: Connection, *, org_id: str,
                       limit: int = 50) -> list[Any]:
    """The newest ledger rows, for the customer page's history panel.

    ⚠️ Deltas leave here as they are stored — the caller stringifies. This
    is the table a customer reads in a dispute, and the operator's view of
    it must be the rows, not a reformatting of them.
    """
    return list(conn.execute(
        text(
            "SELECT delta, reason, ref, created_at FROM credit_ledger "
            "WHERE organization_id = :org "
            "ORDER BY created_at DESC, id LIMIT :lim"
        ),
        {"org": org_id, "lim": limit},
    ))


def record_usage(conn: Connection, *, org_id: str, request_id: str,
                 billed_credits: Decimal, **fields: Any) -> bool:
    """Write one usage event and its ledger draw. Idempotent on ``request_id``.

    Returns True when this call was newly recorded, False when this
    ``(organization_id, request_id)`` pair had already been seen.

    **Idempotency is scoped per organization** (migration 003). It was globally
    unique on ``request_id`` alone, and verification demonstrated the
    consequence: org B posting an id org A had already used got
    ``recorded:false`` and its 500-credit charge silently vanished, while the
    boolean itself leaked whether another tenant had used that id.

    **The ledger write is conditional on the usage insert winning**, which is
    the whole point: a retried request that re-inserts nothing must also
    re-charge nothing. Doing these as two independent statements is how a
    customer gets billed twice for one completion — and a customer billed twice
    is a credibility event, not a rounding error.

    Both statements share the caller's transaction, so they commit or roll back
    together.

    ⚠️ **A REFUSAL comes through this same door** (migration 020, §8.1). Pass
    ``refusal_reason`` and the row records a wall the customer hit instead of a
    call that served. It needs no second writer: a refusal bills 0, and the
    ledger draw below is already conditional on a non-zero charge. A parallel
    insert would be a second seam for one table.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO usage_event
                (organization_id, request_id, billed_credits, user_email,
                 agent, module_slug, model, tier, prompt_tokens,
                 completion_tokens, cached_tokens, provider_cost_usd, run_id, client_ref,
                 task, quantity, unit, served_rank, byok_served, refusal_reason)
            VALUES
                (:org, :request_id, :billed, :user_email, :agent, :module_slug,
                 :model, :tier, :prompt_tokens, :completion_tokens,
                 :cached_tokens, :provider_cost_usd, :run_id, :client_ref,
                 :task, :quantity, :unit, :served_rank, :byok_served,
                 :refusal_reason)
            ON CONFLICT (organization_id, request_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "org": org_id,
            "request_id": request_id,
            "billed": billed_credits,
            "user_email": fields.get("user_email"),
            "agent": fields.get("agent"),
            "module_slug": fields.get("module_slug"),
            "model": fields.get("model"),
            "tier": fields.get("tier"),
            "prompt_tokens": fields.get("prompt_tokens", 0),
            "completion_tokens": fields.get("completion_tokens", 0),
            "cached_tokens": fields.get("cached_tokens", 0),
            "provider_cost_usd": fields.get("provider_cost_usd"),
            "run_id": fields.get("run_id"),
            "client_ref": fields.get("client_ref"),
            # CP-10 slice 2 columns. NULL on a row written before this
            # existed, and NULL from any caller that does not say — a
            # guessed unit must not look like a measured one.
            "task": fields.get("task"),
            "quantity": fields.get("quantity"),
            "unit": fields.get("unit"),
            # Migration 013. `served_rank` NULL from a caller that does not
            # say — the internal `/usage/record` route reports no chain, and
            # its rows must not claim rank-1 service nobody observed.
            "served_rank": fields.get("served_rank"),
            "byok_served": bool(fields.get("byok_served", False)),
            # Migration 020. NULL means the call SERVED, which is what every
            # row written before this column existed did. The database CHECK
            # holds the vocabulary closed, so a fourth slug fails here rather
            # than becoming a second name for one wall.
            "refusal_reason": fields.get("refusal_reason"),
        },
    ).first()

    if row is None:
        return False

    if billed_credits:
        add_credit(
            conn, org_id=org_id, delta=-billed_credits,
            reason=LEDGER_REASON_USAGE, ref=request_id,
        )
    return True


# ── Spend reads (CP-7 slice 1 · D66) ────────────────────────────────────────
#
# ⚠️ **NEITHER QUERY SELECTS `model`, AND THAT IS THE POINT.** D32.7 and D66
# say a customer never sees a model. These two functions are the surface that
# answers "what did we spend it on", so they are exactly where a model column
# would be added by somebody being helpful. `TestNoModelReachesTheCustomer`
# reads this module's AST and fails if `model` appears in either SELECT.
#
# ⚠️ **`user_email` is CALLER-SUPPLIED attribution (`X-CC-Member`), not a
# verified identity.** These are READS, so that is tolerable: an admin looking
# at a cost breakdown is not an authorisation decision. It is NOT tolerable for
# the per-member CAP, which is why the cap engine stays unwired — see H-73.

#: The window every spend read is measured over, in days. NAMED because a
#: figure whose window is unstated reads as authoritative and is not — the
#: same argument `my_billing`'s `windowDays` already makes.
SPEND_WINDOW_DAYS = 30

#: What a usage row is called when it carries no agent and no module. The
#: string is returned to the customer, so it is a word, not a NULL the browser
#: has to decide how to render.
UNATTRIBUTED_ACTIVITY = "unattributed"

#: A NAMED page size. `usage_event` is a table the customer grows by using the
#: product, so an unbounded `SELECT` here is the CP-9 §9.3(6) defect again.
SPEND_PAGE_SIZE = 100


def usage_by_activity(
    conn: Connection, *, org_id: str, days: int = SPEND_WINDOW_DAYS,
    member: str | None = None,
) -> list[dict[str, Any]]:
    """What this organization ran, and what it cost. **Never what it ran ON.**

    Grouped by activity — the agent, else the module, else
    :data:`UNATTRIBUTED_ACTIVITY`. That ordering is D66 (a)'s "the app, the
    agent or the run", narrowed to the two columns `usage_event` actually
    carries for it.

    Pass ``member`` to scope the answer to one person, which is D66 (a)'s own
    view. Leave it ``None`` for the whole organization.

    ⚠️ **`billed_credits` is 0 while the rate card is unpriced (H-42).** The
    call counts are real from the first routed call; the money is not. This
    function does not apologise for that, because `my_billing` reports the same
    zeros from the same rows and a second explanation here would be a second
    vocabulary for one fact.
    """
    total = conn.execute(
        text(
            """
            SELECT COALESCE(agent, module_slug, :unattributed) AS activity,
                   COUNT(*)                                    AS calls,
                   COALESCE(SUM(billed_credits), 0)            AS credits
            FROM usage_event
            WHERE organization_id = :org
              AND created_at >= now() - make_interval(days => :days)
              -- 🔴 A REFUSAL IS NOT A CALL (migration 020, §8.1). Without
              -- this the call count inflates while the credit sum stays
              -- right, because a refusal bills 0 — so the two columns
              -- disagree and nothing on the page says why.
              AND refusal_reason IS NULL
              -- ⚠️ The CAST is load-bearing, not decoration. A bare
              -- `:member IS NULL` gives Postgres no type to infer and the
              -- statement fails to PREPARE with AmbiguousParameter — every
              -- call, not just the filtered one. CITEXT (not TEXT) because
              -- that is `user_email`'s own type, and casting to TEXT would
              -- silently reimpose case-sensitive matching on one side.
              AND (CAST(:member AS CITEXT) IS NULL
                   OR user_email = CAST(:member AS CITEXT))
            GROUP BY 1
            ORDER BY credits DESC, calls DESC, activity ASC
            LIMIT :lim
            """
        ),
        {"org": org_id, "days": days, "member": member,
         "unattributed": UNATTRIBUTED_ACTIVITY, "lim": SPEND_PAGE_SIZE},
    )
    return [
        {"activity": r.activity, "calls": int(r.calls),
         "credits": Decimal(r.credits)}
        for r in total
    ]


def usage_by_member(
    conn: Connection, *, org_id: str, days: int = SPEND_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Per-member cost for one organization — D66 (b), the admin's read.

    ⚠️ **A read, and only a read.** It writes nothing and it decides nothing.
    The same numbers must not be turned into an enforcement decision without
    first fixing the identity they rest on (H-73).

    A row whose `user_email` is NULL is reported under
    :data:`UNATTRIBUTED_ACTIVITY` rather than dropped. Dropping it would make
    the per-member figures silently fail to add up to the organization total,
    and the admin would have no way to see that they did not.
    """
    rows = conn.execute(
        text(
            """
            -- ⚠️ **GROUP BY the CITEXT column, never by its ::text cast.**
            -- The cast strips the case-insensitive collation, so
            -- `Alice@Corp.com` and `alice@corp.com` become two rows and one
            -- person's spend is split across both. Measured against a real
            -- server (R8) — this SELECT grouped by the cast in its first
            -- draft and every hermetic assertion still passed.
            --
            -- MIN() rather than a bare projection so the label is
            -- deterministic: within one CITEXT group Postgres is free to
            -- return whichever spelling it stored first.
            SELECT COALESCE(MIN(user_email::text), :unattributed) AS member,
                   COUNT(*)                                       AS calls,
                   COALESCE(SUM(billed_credits), 0)               AS credits
            FROM usage_event
            WHERE organization_id = :org
              AND created_at >= now() - make_interval(days => :days)
              -- 🔴 A REFUSAL IS NOT A CALL (migration 020, §8.1). Same
              -- argument as `usage_by_activity`: the count inflates and the
              -- credits do not, so the admin reads two numbers that disagree.
              AND refusal_reason IS NULL
            GROUP BY user_email
            ORDER BY credits DESC, calls DESC, member ASC
            LIMIT :lim
            """
        ),
        {"org": org_id, "days": days,
         "unattributed": UNATTRIBUTED_ACTIVITY, "lim": SPEND_PAGE_SIZE},
    )
    return [
        {"member": r.member, "calls": int(r.calls), "credits": Decimal(r.credits)}
        for r in rows
    ]


# ── Operator spend reads (WS-31) ────────────────────────────────────────────
#
# 🔴 **THESE TWO READ ACROSS EVERY TENANT, AND THAT IS THE POINT.** Every other
# read in this module is scoped by `organization_id` because R5 says so. These
# are the operator's "how are our customers using AI" question, which cannot be
# answered one tenant at a time, so the scoping happens at the CALLER — the
# route must be behind the operator role gate, never a customer session.
#
# ⚠️ If you are about to reuse one of these for a customer-facing surface, you
# want `usage_by_activity` / `usage_by_member` instead. They take an `org_id`
# and cannot leak. Reaching for these because they return more is how a tenant
# ends up reading another tenant's spend.

#: Longest window an operator may ask for in one query. A year of daily rows is
#: 365 points, which is already more than any chart reads usefully, and an
#: unbounded window lets one request scan the whole table.
USAGE_MAX_DAYS = 365


def last_seen_by_org(conn: Connection) -> dict[str, Any]:
    """Every organization's last AI call, UNCAPPED. slug -> timestamp.

    🔴 **This read exists because the silent-customer flag was computed over
    the capped page** (H-76's second half). The page sorts by spend and keeps
    the top rows, so the quiet-but-funded customer A3 exists to find was the
    exact row the cap removed — silently, above SPEND_PAGE_SIZE
    organizations. One aggregate over the whole table costs one index scan
    and cannot lose anybody.
    """
    rows = conn.execute(
        text(
            """
            SELECT o.slug, MAX(u.created_at) AS last_seen
            FROM organization o
            LEFT JOIN usage_event u ON u.organization_id = o.id
            GROUP BY o.slug
            """
        )
    )
    return {r.slug: r.last_seen for r in rows}


def usage_by_org(
    conn: Connection, *, days: int = SPEND_WINDOW_DAYS,
    limit: int = SPEND_PAGE_SIZE,
) -> dict[str, Any]:
    """Per-organization AI usage over the window. **Operator-only.**

    ⚠️ **A LEFT JOIN, deliberately.** An organization with no usage must appear
    with zeros rather than vanish: "this customer has bought credits and used
    none" is the single most actionable row on the page, and an INNER JOIN
    silently hides exactly that customer.

    ⚠️ **Purged organizations are RETURNED, not filtered.** Their `usage_event`
    rows survive the purge as billing history, and dropping them here would
    make a past invoice unexplainable. The caller decides whether to show them
    — `partitionRoster` in the console already owns that rule, and a second
    copy of it in SQL would be a second vocabulary for one decision.

    🔴 **The page is CAPPED, and the cap fights the LEFT JOIN.** Rows sort by
    credits descending, so a zero-usage organization sorts LAST — and it is
    precisely the row the LEFT JOIN above exists to include. Past
    `SPEND_PAGE_SIZE` organizations the two rules cancel out and the most
    actionable customers fall off the end.

    Found on 2026-08-30 by running this against a scratch database holding 563
    organizations. It cannot be seen below the cap, so dev, CI and production
    (2 organizations) all agree it is fine.

    This returns `total` so the truncation is at least never SILENT, and the
    console says "100 of 563". The ordering itself is a design question and it
    is NOT settled here: an operator wants the biggest spenders *and* the quiet
    ones, which is two queries or one union, not one `ORDER BY`. HANDOFF H-76.

    ⚠️ **`limit` is a real parameter, not a test hook.** A paginated read owes
    its caller a page size. It exists because the alternative was a test that
    asserted a zero-usage organization appears in the DEFAULT page — which is
    the very thing the cap makes untrue, so the test passed or failed on
    whether the fixture's random slug sorted into the first hundred. A test
    that encodes the defect it is meant to catch is worse than no test.
    """
    rows = conn.execute(
        text(
            """
            SELECT o.slug                                AS slug,
                   o.name                                AS name,
                   -- 🔴 **A FILTER clause, never a WHERE clause** (migration
                   -- 020, §8.1). A WHERE on the right-hand table turns the
                   -- LEFT JOIN below into an inner join, and the zero-usage
                   -- organization this read exists to show disappears. An ON
                   -- clause keeps the join and still hides the refusal from
                   -- `MAX(u.created_at)`, which makes a customer at a wall
                   -- read as SILENT to A3.
                   COUNT(u.id) FILTER
                       (WHERE u.refusal_reason IS NULL)  AS calls,
                   COALESCE(SUM(u.billed_credits), 0)    AS credits,
                   -- The COSTED slice: SUM skips NULL cost rows, so a ratio
                   -- of all-calls credits over some-calls cost overstates
                   -- the margin. These two keep the numerator honest.
                   COUNT(u.id) FILTER
                       (WHERE u.provider_cost_usd IS NOT NULL)
                                                         AS costed_calls,
                   COALESCE(SUM(u.billed_credits) FILTER
                       (WHERE u.provider_cost_usd IS NOT NULL), 0)
                                                         AS costed_credits,
                   -- The same FILTER, for the same reason: a member who only
                   -- hit a wall this month made no call, and counting them
                   -- here would report activity nobody had.
                   COUNT(DISTINCT u.user_email) FILTER
                       (WHERE u.refusal_reason IS NULL) AS members,
                   COALESCE(SUM(u.provider_cost_usd), 0) AS cost_usd,
                   -- ⚠️ `last_seen` takes NO filter, on purpose. A refusal
                   -- MUST move it — a customer at a wall is a customer who is
                   -- trying, and hiding that makes them read as silent.
                   MAX(u.created_at)                     AS last_seen
            FROM organization o
            LEFT JOIN usage_event u
                   ON u.organization_id = o.id
                  AND u.created_at >= now() - make_interval(days => :days)
            GROUP BY o.id, o.slug, o.name
            ORDER BY credits DESC, calls DESC, o.slug ASC
            LIMIT :lim
            """
        ),
        {"days": days, "lim": max(1, int(limit))},
    )
    out = [
        {
            "slug": r.slug,
            "name": r.name,
            "calls": int(r.calls),
            "credits": Decimal(r.credits),
            "costed_calls": int(r.costed_calls),
            "costed_credits": Decimal(r.costed_credits),
            "members": int(r.members),
            "cost_usd": Decimal(r.cost_usd),
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        }
        for r in rows
    ]
    # ⚠️ Counted separately, and cheaply — `count(*)` over `organization` reads
    # one small table. Counting the joined result would repeat the aggregate
    # for no extra truth.
    total = conn.execute(text("SELECT count(*) FROM organization")).scalar_one()
    return {"rows": out, "total": int(total), "shown": len(out)}


def usage_daily(
    conn: Connection, *, days: int = SPEND_WINDOW_DAYS, org_id: str | None = None,
) -> list[dict[str, Any]]:
    """AI usage per day. One series, two callers.

    Pass ``org_id`` for one organization — that is the customer-facing trend,
    and it is tenant-safe. Leave it ``None`` for the platform total, which is
    operator-only.

    🔴 **The gap fill is the whole function.** A series built by grouping
    `usage_event` alone returns only days that HAVE rows, and a chart drawing a
    straight line between two points a week apart reads as steady usage across
    a week that had none. `generate_series` emits every day in the window and
    the LEFT JOIN fills the rest with zeros.

    ⚠️ The `CAST(:org AS UUID)` is load-bearing for the same reason the CAST in
    `usage_by_activity` is: a bare ``:org IS NULL`` gives Postgres no type to
    infer and the statement fails to PREPARE with AmbiguousParameter — on every
    call, including the unfiltered one.
    """
    rows = conn.execute(
        text(
            """
            WITH span AS (
              SELECT generate_series(
                       date_trunc('day', now() - make_interval(days => :days - 1)),
                       date_trunc('day', now()),
                       interval '1 day'
                     ) AS day
            )
            SELECT s.day::date                        AS day,
                   -- 🔴 **A FILTER clause, never a WHERE clause** (migration
                   -- 020, §8.1). The LEFT JOIN onto `generate_series` is the
                   -- whole gap fill: a WHERE on `u` drops every day that has
                   -- only refusals, and the chart draws a straight line
                   -- across it as if usage were steady.
                   COUNT(u.id) FILTER
                       (WHERE u.refusal_reason IS NULL) AS calls,
                   COALESCE(SUM(u.billed_credits), 0) AS credits
            FROM span s
            LEFT JOIN usage_event u
                   ON date_trunc('day', u.created_at) = s.day
                  AND (CAST(:org AS UUID) IS NULL
                       OR u.organization_id = CAST(:org AS UUID))
            GROUP BY s.day
            ORDER BY s.day
            """
        ),
        {"days": days, "org": org_id},
    )
    return [
        {"day": r.day.isoformat(), "calls": int(r.calls),
         "credits": Decimal(r.credits)}
        for r in rows
    ]


def credit_balance_by_org(conn: Connection) -> dict[str, Decimal]:
    """Credit balance for every organization, keyed by slug. **Operator-only.**

    ⚠️ **Summed from the ledger, never read from a column.** `credit_ledger` is
    the authority and `balance_of` already sums it for one organization. A
    stored balance column would be a second authority, and the two disagree the
    first time a write lands outside the path that maintains it.

    ⚠️ An organization with no ledger row returns 0, not a missing key. The
    caller renders every organization, and a `KeyError` inside a page that
    lists all of them is a blank page rather than a missing cell.

    🔴 **UNCAPPED, since 2026-08-30 — this carried `LIMIT 100` with no ORDER
    BY.** Above a hundred organizations, an ARBITRARY hundred had balances and
    every other org silently read 0: a funded, visible customer could render a
    zero balance, a None runway and no silent flag, differently on each
    request. Found by the H-76 test that crowds the page past its cap. A page
    may be capped; the FACTS a page is judged from may not.
    """
    rows = conn.execute(
        text(
            """
            SELECT o.slug                       AS slug,
                   COALESCE(SUM(l.delta), 0)    AS balance
            FROM organization o
            LEFT JOIN credit_ledger l ON l.organization_id = o.id
            GROUP BY o.id, o.slug
            """
        )
    )
    return {r.slug: Decimal(r.balance) for r in rows}


# ── Keys ────────────────────────────────────────────────────────────────────

def issue_key(conn: Connection, *, org_id: str, prefix: str, key_hash: str,
              label: str | None = None, created_by: str | None = None) -> str:
    """Store a freshly minted key. The secret is never passed here — only its hash."""
    row = conn.execute(
        text(
            """
            INSERT INTO llm_api_key
                (organization_id, prefix, key_hash, label, created_by)
            VALUES (:org, :prefix, :hash, :label, :by)
            RETURNING id
            """
        ),
        {"org": org_id, "prefix": prefix, "hash": key_hash,
         "label": label, "by": created_by},
    ).first()
    assert row is not None
    return str(row[0])


def revoke_key(conn: Connection, *, org_id: str, prefix: str) -> bool:
    """Revoke a key. Scoped to the owning org so one customer's admin surface
    can never revoke another's key by guessing a prefix."""
    row = conn.execute(
        text(
            """
            UPDATE llm_api_key SET revoked_at = now()
            WHERE prefix = :prefix AND organization_id = :org
              AND revoked_at IS NULL
            RETURNING id
            """
        ),
        {"prefix": prefix, "org": org_id},
    ).first()
    return row is not None


def list_keys(conn: Connection, *, org_id: str) -> list[dict[str, Any]]:
    """Keys for one org. Returns prefixes and metadata — **never** ``key_hash``.

    The hash is excluded at the SQL level rather than filtered afterwards: a
    column that is never selected cannot be leaked by a later caller that
    forgets to strip it before serialising.
    """
    return [
        {"prefix": r[0], "label": r[1], "created_at": r[2].isoformat(),
         "revoked": r[3] is not None}
        for r in conn.execute(
            text(
                "SELECT prefix, label, created_at, revoked_at FROM llm_api_key "
                "WHERE organization_id = :org ORDER BY created_at DESC"
            ),
            {"org": org_id},
        )
    ]


def issue_deployment_key(
    conn: Connection, *, deployment_id: str, prefix: str, key_hash: str,
    label: str | None = None, created_by: str | None = None,
    capabilities: list[str] | None = None,
) -> str:
    """Store a freshly minted **deployment** key. Only the hash is passed here.

    Mirrors :func:`issue_key` deliberately rather than generalising it: the two
    write different tables with different owners (an ``organization`` there, a
    ``deployment`` here), and a single function taking "which kind" would put
    that decision at every call site.

    ⚠️ CP-2b ships **no HTTP route that calls this** — no done-when specifies
    one, and issuing a real ``cc_depl_`` key into a live deployment is
    OWNER-GATE (§8 gate 7). It exists so fixtures and, later, an operator
    surface have one implementation of the write.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO deployment_key
                (deployment_id, prefix, key_hash, label, created_by,
                 capabilities)
            VALUES (:dep, :prefix, :hash, :label, :by,
                    COALESCE(CAST(:caps AS TEXT[]), '{resolve}'))
            RETURNING id
            """
        ),
        {"dep": deployment_id, "prefix": prefix, "hash": key_hash,
         "label": label, "by": created_by, "caps": capabilities},
    ).first()
    assert row is not None
    return str(row[0])


def resolve_deployment_key(
    conn: Connection, *, prefix: str
) -> tuple[str, str, list[str]] | None:
    """Look up a live deployment key → ``(deployment_id, key_hash, caps)``.

    ``revoked_at IS NULL`` is resolved here rather than left to the caller, for
    the same reason :func:`resolve_key` does it: "did you also check…" is
    exactly the step a second call site forgets.

    Deliberately **no join to `deployment`** and no status filter on it. A
    deployment's own ``status`` (``active|draining|retired``) is an operational
    fact about a box, and nothing in CP-2b decides what a ``draining`` box's
    key should do; inventing an answer here would make sign-in for a whole
    customer depend on a column no ticket has specified. The credential's own
    ``revoked_at`` is the revocation channel.
    """
    row = conn.execute(
        text(
            """
            SELECT deployment_id, key_hash, capabilities
            FROM deployment_key
            WHERE prefix = :prefix AND revoked_at IS NULL
            """
        ),
        {"prefix": prefix},
    ).first()
    return (str(row[0]), row[1], list(row[2])) if row else None


# ── Placement (WS-29 MT-1j slice 4 · D46.6) ─────────────────────────────────
#
# The three statements provisioning needs to put an organization on a box. They
# are separate rather than one "upsert the placement" helper because the
# decision between them is a REFUSAL, and a helper that both moved and refused
# would put that decision at every call site.

def deployment_by_label(conn: Connection, *, label: str) -> str | None:
    """The deployment carrying this ``label``, or ``None``.

    ``deployment.label`` is ``UNIQUE`` (``001_customer_console.sql:84``), which
    is what lets provisioning take a NAME from an operator instead of a UUID —
    and what makes "which box" something a human can say out loud during an
    incident.

    ⚠️ **There is no ``count(*) = 1`` fallback and there must never be one.**
    Inferring the deployment from "there is only one" would be a fourth copy of
    the sole-organization guess MT-1j exists to retire (``key_store.py:114-139``
    · ``model_config.py:40-48`` · ``acb_common/placement.py:46-50``), and it
    would silently mis-place every organization the day a second box exists.
    Forbidden by name — ``saas_multitenancy.md`` §11 MT-1j slice 4, D46.6
    adjudication item 3. ``None`` here is the caller's 404.
    """
    row = conn.execute(
        text("SELECT id FROM deployment WHERE label = :label"),
        {"label": label},
    ).first()
    return str(row[0]) if row else None


def current_placement(conn: Connection, *, org_id: str) -> str | None:
    """The deployment this organization is placed on now, or ``None``.

    Read *before* the write, because :func:`place_organization` conflicts to
    ``DO NOTHING``: without this read, re-provisioning under a different label
    would answer 200 while leaving the organization exactly where it was — the
    caller believing a move happened is worse than the refusal.
    """
    row = conn.execute(
        text(
            "SELECT deployment_id FROM org_placement "
            "WHERE organization_id = :org"
        ),
        {"org": org_id},
    ).first()
    return str(row[0]) if row else None


def org_owned_by_other(
    conn: Connection, *, org_id: str, owner_email: str
) -> bool:
    """True iff the org already has an owner membership held by a DIFFERENT email.

    The deployment-key provision arm **creates only, it never joins** (WS-31
    CP-2c slice 1, R11). It may complete an org that has no owner yet — the
    crash-after-org-before-membership resume shape — and it may idempotently
    re-affirm the same owner, but it must REFUSE to write into an org already
    owned by someone else. Without that refusal a per-box ``provision`` key,
    driven in slice 2 by a user-supplied slug from an UNAUTHENTICATED signup
    form, makes its caller a co-owner of a stranger's organization; the refusal
    therefore lives at the CONSOLE door, not the gateway.

    The predicate keys on **ownership**, which resolves all three cases with one
    read: a slug with no owner is not a conflict (it may be completed), the same
    owner is not a conflict (idempotent retry), a different owner is. ``email``
    is ``CITEXT`` (``001_customer_console.sql:110``), so ``Ada@Corp.com`` cannot
    slip past a membership owned by ``ada@corp.com`` — the inequality is
    case-insensitive without a ``lower()`` on either side.

    Read-only: no engine site, no write, so a refusal on this path rolls back a
    transaction that has written nothing that will commit (R5(b)).
    """
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM org_membership m
                    JOIN user_identity ui ON ui.id = m.user_identity_id
                    WHERE m.organization_id = :org
                      AND m.role = 'owner'
                      AND ui.email <> :owner_email
                )
                """
            ),
            {"org": org_id, "owner_email": owner_email},
        ).scalar_one()
    )


def place_organization(
    conn: Connection, *, org_id: str, deployment_id: str
) -> None:
    """Place an organization on a deployment. **Never moves one.**

    ``ON CONFLICT (organization_id) DO NOTHING``: a re-run is a no-op and
    ``moved_at`` is untouched, because a MOVE is a separate operator act with
    its own semantics (the fixture at ``test_customer_console_resolve.py:249``
    sketches them) owned by a future placement ticket — D46.6 adjudication item
    4. This function refuses nothing and moves nothing; deciding whether the
    row that is already there is the *right* one is :func:`current_placement`'s
    answer and the caller's decision.

    ``database_target`` is deliberately absent from the column list, so it
    lands NULL: day one every row resolves to the same target and the
    INDIRECTION is the point (``001_customer_console.sql:99-104``). A value
    arrives when a real need names one — stated, not forgotten.
    """
    conn.execute(
        text(
            """
            INSERT INTO org_placement (organization_id, deployment_id)
            VALUES (:org, :dep)
            ON CONFLICT (organization_id) DO NOTHING
            """
        ),
        {"org": org_id, "dep": deployment_id},
    )


def deployment_visible_orgs(
    conn: Connection, *, deployment_id: str, email: str
) -> list[dict[str, Any]]:
    """Every organization this deployment may see for this email.

    **The predicate IS the acceptance criterion** (CP-2b clause 4): a
    deployment key resolves an email *if and only if* that email holds a
    membership in an organization whose **current** ``org_placement.
    deployment_id`` equals the key's deployment. *Placement is the boundary,
    not the organization* — which is what makes the pooled case work rather
    than falsify it: re-place an org onto this deployment and the same key
    starts resolving it, with no key change.

    Three consequences worth stating, because each is a decision:

    * ``org_placement`` is joined, not left-joined. An organization with no
      placement row is placed **nowhere**, and nowhere is not here.
    * ``user_identity.email`` is ``CITEXT`` (001:110), so ``Ada@Corp.com`` and
      ``ada@corp.com`` are one person without a ``lower()`` on either side —
      and therefore without defeating the unique index. (The *tenant* plane's
      projection is plain ``TEXT`` with a ``lower(email)`` index and needs the
      opposite care; that half is CP-2b's deployment side, not this one.)
    * ``org_membership.status`` is **not** consulted. Clause 4 states the
      criterion as *holds a membership*, and clause 5 enumerates exactly three
      invisible cases, none of which is "membership was removed". Filtering
      here would answer a question no clause asks — and would do it in the
      restrictive direction, silently. Recorded as a finding, not decided here.

    ``ORDER BY o.slug`` so the answer is stable: a caller comparing two
    responses, and a fence asserting a list, must not depend on the planner.
    """
    return [
        {
            "organization_id": str(r[0]),
            "slug": r[1],
            "status": r[2],
            #: `org_placement.database_target` is nullable with no default
            #: (001:104) — day one every row resolves to the same target and
            #: the INDIRECTION is the point. NULL is a legitimate answer and
            #: reaches the wire as `null`, never as an invented string.
            "placement": r[3],
            "identity_id": str(r[4]),
        }
        for r in conn.execute(
            text(
                """
                SELECT o.id, o.slug, o.status, p.database_target, ui.id
                FROM user_identity ui
                JOIN org_membership m ON m.user_identity_id = ui.id
                JOIN organization o ON o.id = m.organization_id
                JOIN org_placement p ON p.organization_id = o.id
                WHERE ui.email = :email AND p.deployment_id = :dep
                ORDER BY o.slug
                """
            ),
            {"email": email, "dep": deployment_id},
        )
    ]


def add_invited_member(
    conn: Connection, *, org_id: str, identity_id: str
) -> tuple[bool, str]:
    """Create an ``invited`` membership if the org has none for this identity.

    CP-2f / D50.2 — the ONLY member-add writer besides ``provision``'s founder
    INSERT, and the reason an invited colleague can be seen by ``GET /me/members``
    and given a seat before they have ever signed in.

    Returns ``(created, status_now)``.

    **Create-only, by construction.** ``ON CONFLICT DO NOTHING`` means an existing
    row is left exactly as it is: an ``active`` member is never demoted back to
    ``invited`` by a re-invite, and a ``removed`` one is never silently
    resurrected — reinstating somebody is an ``admin:members:manage`` decision on
    the tenant plane and must not ride in on the weaker invite door. The caller is
    told which happened (``created``) and what the row says now (``status_now``),
    so a re-invite is legible rather than a silent 200.

    **``role`` is not a parameter and never will be one.** It takes the column
    default ``member`` (``001_customer_console.sql:121-124``). ``org_membership.
    role`` is registry/billing vocabulary (D12); the tenant's permission
    vocabulary is ``org_role``, and mapping one onto the other here would mint the
    second grant vocabulary root ``CLAUDE.md`` §5 forbids by name — as well as
    letting a customer admin create registry admins through an invite.

    ⚠️ **This writes no seat.** A membership row is not a seat: Core-seat burn
    stays at first resolve (D19.3 / D32.5, ``main._allocate_core_seat``).
    """
    row = conn.execute(
        text(
            """
            INSERT INTO org_membership
                (organization_id, user_identity_id, status)
            VALUES (:org, :i, 'invited')
            ON CONFLICT (organization_id, user_identity_id) DO NOTHING
            RETURNING status
            """
        ),
        {"org": org_id, "i": identity_id},
    ).first()
    if row is not None:
        return True, str(row[0])

    # The conflict path. Re-read rather than assume: what the row says now is
    # exactly the thing the caller could not know, and inventing 'invited' here
    # would report a demotion that did not happen.
    existing = conn.execute(
        text(
            "SELECT status FROM org_membership "
            "WHERE organization_id = :org AND user_identity_id = :i"
        ),
        {"org": org_id, "i": identity_id},
    ).scalar_one()
    return False, str(existing)


def activate_invited_member(
    conn: Connection, *, org_id: str, identity_id: str
) -> bool:
    """Promote an ``invited`` membership to ``active``. Returns whether it moved.

    D50.3 — *"first sign-in auto-activates an invited member"*, driven from the
    DEPLOYMENT arm of ``POST /registry/resolve`` and from nowhere else.

    ⚠️ **The guard is the ``AND status = 'invited'`` in this statement's own
    ``WHERE``**, not an ``if`` beside the call. That is deliberate and it is the
    whole safety argument: the natural implementation (*"anything that is not
    active → activate"*) silently un-suspends people, which
    ``colleague_onboarding.md`` §6 predicted in as many words before this existed.
    Written as a predicate on the UPDATE, ``suspended``, ``removed`` and already-
    ``active`` rows are untouched by construction, and a later edit that widens it
    has to widen the SQL — which is where the fence is looking.

    ``joined_at`` is stamped ``COALESCE(joined_at, now())``, the same rule the
    tenant plane's activation uses, so a returning member keeps their first join
    date.
    """
    row = conn.execute(
        text(
            """
            UPDATE org_membership
               SET status = 'active',
                   joined_at = COALESCE(joined_at, now())
             WHERE organization_id = :org
               AND user_identity_id = :i
               AND status = 'invited'
            RETURNING user_identity_id
            """
        ),
        {"org": org_id, "i": identity_id},
    ).first()
    return row is not None


def org_members(conn: Connection, *, org_id: str) -> list[dict[str, Any]]:
    """Every membership row in one organization: ``email · role · status``.

    The ONE per-org roster read (§6 item (i), done-when 20). It composes the
    established ``org_membership ⋈ user_identity`` join idiom — the SAME join
    :func:`org_owned_by_other` (654-660) and :func:`deployment_visible_orgs`
    (743-749) already use, ``ON ui.id = m.user_identity_id`` — into one helper,
    rather than a second membership-query pattern. It exists as a new helper
    because none of the existing membership reads returns a per-org roster: they
    are single-member-by-identity, by-email-across-orgs, or an owner ``EXISTS``.

    ``email`` is ``CITEXT`` (``001_customer_console.sql:110``); ``role`` and
    ``status`` are the CHECK-constrained membership vocabularies (001:121-131).
    **This module decides nothing** (§1): every membership row is returned with
    its ``status`` on the wire — no ``status`` filter, no per-member policy — so
    the surface, not the read, chooses which statuses to render, exactly as
    :func:`deployment_visible_orgs` records for *not* consulting ``status``.

    ``ORDER BY ui.email`` so the answer is stable — a caller comparing two
    responses, and a fence asserting a list, must not depend on the planner,
    exactly as :func:`deployment_visible_orgs` orders by ``o.slug``.
    """
    return [
        {"email": r[0], "role": r[1], "status": r[2]}
        for r in conn.execute(
            text(
                """
                SELECT ui.email, m.role, m.status
                FROM org_membership m
                JOIN user_identity ui ON ui.id = m.user_identity_id
                WHERE m.organization_id = :org
                ORDER BY ui.email
                """
            ),
            {"org": org_id},
        )
    ]


def live_seats_by_email(conn: Connection, *, org_id: str) -> dict[str, list[str]]:
    """Every LIVE seat in one organization, keyed by the holder's email.

    The read `org_members`'s docstring records as DEFERRED (§6 item (i)),
    undeferred by **D49** — `launch_surface.md` LS-7 needs *Unassigned* to be a
    first-class state on the seat surface, and a member with no row in
    `seat_assignment` is exactly what that means. Without this, "who has no
    seat" is unanswerable from the customer's own credential.

    **One query for the whole org, folded in memory — never one per member.**
    A roster of forty members would otherwise be forty round trips behind one
    HTTP read, and the N+1 would arrive as a slow page long after the change
    that caused it.

    `released_at IS NULL` is the live predicate the whole seat vocabulary uses
    (`has_live_seat`, `seat_rows`, `try_assign_seat`'s partial unique index).
    Stated once here rather than left to the caller for the reason
    :func:`active_plans` gives about `active`: a filter a caller may forget is a
    filter that eventually reports released seats as held.

    The key is the **email**, not the identity id: the caller is the customer's
    own surface, an internal `user_identity` id is not theirs to hold
    (`MemberView`'s stated absences), and email is what the roster is already
    keyed by. `CITEXT` makes the join case-insensitive on the database side;
    the returned key is whatever case the identity row stores, which is the same
    string :func:`org_members` returns — so the two reads zip without
    normalization.

    Plans are ordered so the answer is stable for a caller comparing two
    responses, the reason :func:`deployment_visible_orgs` orders by slug.
    """
    out: dict[str, list[str]] = {}
    for email, plan_slug in conn.execute(
        text(
            """
            SELECT ui.email, sa.plan_slug
            FROM seat_assignment sa
            JOIN user_identity ui ON ui.id = sa.user_identity_id
            WHERE sa.organization_id = :org AND sa.released_at IS NULL
            ORDER BY ui.email, sa.plan_slug
            """
        ),
        {"org": org_id},
    ):
        out.setdefault(email, []).append(plan_slug)
    return out


# ── Operator Console: the cross-org read (§4.1a / CP-8) ─────────────────────

def cross_org_summary(conn: Connection) -> list[dict[str, Any]]:
    """Every organization with the numbers the Operator Console renders (§4.1a).

    The ONE cross-org read behind CP-8's customer list: per company, its
    lifecycle and subscription status, trial expiry, credit balance, and the raw
    seat rows the surface folds through the ONE seat vocabulary. It is the
    cross-org twin of :func:`billing_summary`'s per-org read — the Operator
    door's *list* where ``billing_summary`` is its *detail* — and it decides
    nothing: it returns rows for the caller (``main._org_list``) to fold through
    :func:`customer_console.seats.seat_counts` and :func:`payments.paise`, so the
    list's per-plan counts and money cannot drift from every other surface's.

    ⚠️ **Cross-tenant BY DESIGN, and reachable only under the Operator scheme.**
    This is the one read that spans organizations (``saas_multitenancy.md``
    §0.9.2); it carries no ``organization_id`` filter because its whole job is to
    answer "which customers exist and how are they doing" — the question no
    single tenant deployment can answer and no customer credential may ask.

    **Bounded queries, never N+1.** Four statements regardless of how many
    organizations exist: the org/subscription/balance join, the effective seat
    grants, the live assignment counts, and the active catalog. A per-org loop
    that called :func:`billing_summary`'s helpers would issue O(orgs * plans)
    round trips on a staff surface that lists every customer.

    * ``credit_balance`` is ``SUM(delta)`` grouped by org — the SAME sum
      :func:`credit_deltas` + :func:`customer_console.credits.balance_of`
      compute one org at a time, never a cached balance column (which destroys
      the audit trail §3.3 keeps). Orgs with no ledger row read ``0``.
    * ``grants`` is the list of signed ``quantity_purchased`` whose
      ``effective_from`` has passed — exactly :func:`seat_rows`' list, so a
      future-dated grant is not counted and a reduction stays a negative row;
      ``assigned`` is the live-assignment count. Both are handed to
      :func:`seat_counts` by the caller, unfolded here, so ``available``'s
      zero-clamp and ``oversubscribed`` remain that module's alone.
    * a plan the org holds neither a grant nor a live assignment on is
      **skipped**, not emitted as a zero row — the same rule :func:`_seat_grid`
      makes, so "never bought" and "bought zero" are not made to look alike.

    Two statuses ride together because they legitimately diverge:
    ``status`` is ``organization.status`` (the §4.1d lifecycle the suspend/resume
    act moves) and ``subscription_status`` is ``org_subscription.status`` (the
    billing truth a manual activation sets without touching the lifecycle). MRR
    is the caller's, computed from the seat rows against the active subscription.

    ``ORDER BY o.created_at, o.slug`` so two reads of an unchanged database agree
    and a fence can assert a list — ``created_at`` carries no UNIQUE constraint,
    so the ``slug`` tie-break is not decoration (``active_plans`` makes the same
    argument for its own ordering).
    """
    orgs = conn.execute(
        text(
            """
            SELECT o.id, o.slug, o.name, o.status, o.export_until,
                   o.created_at,
                   s.status AS sub_status, s.trial_ends_at, s.provider,
                   s.current_period_end,
                   COALESCE(c.balance, 0) AS credit_balance
            FROM organization o
            LEFT JOIN org_subscription s ON s.organization_id = o.id
            LEFT JOIN (
                SELECT organization_id, SUM(delta) AS balance
                FROM credit_ledger
                GROUP BY organization_id
            ) c ON c.organization_id = o.id
            ORDER BY o.created_at, o.slug
            """
        )
    ).mappings().all()

    grants: dict[tuple[str, str], list[int]] = {}
    for r in conn.execute(
        text(
            """
            SELECT organization_id, plan_slug, quantity_purchased
            FROM seat_grant
            WHERE effective_from <= now()
            """
        )
    ):
        grants.setdefault((str(r[0]), r[1]), []).append(int(r[2]))

    assigned: dict[tuple[str, str], int] = {}
    for r in conn.execute(
        text(
            """
            SELECT organization_id, plan_slug, count(*)
            FROM seat_assignment
            WHERE released_at IS NULL
            GROUP BY organization_id, plan_slug
            """
        )
    ):
        assigned[(str(r[0]), r[1])] = int(r[2])

    plans = active_plans(conn)  # the ONE active-catalog read, in catalog order

    summary: list[dict[str, Any]] = []
    for o in orgs:
        org_id = str(o["id"])
        seats = []
        for plan in plans:
            slug = plan["slug"]
            g = grants.get((org_id, slug), [])
            a = assigned.get((org_id, slug), 0)
            if not g and not a:
                continue  # never bought, never assigned — not worth a row
            seats.append(
                {
                    "plan_slug": slug,
                    "grants": g,
                    "assigned": a,
                    "price_inr": plan["price_inr"],
                }
            )
        summary.append(
            {
                "slug": o["slug"],
                "name": o["name"],
                "status": o["status"],
                "export_until": o["export_until"],
                "subscription_status": o["sub_status"],
                "provider": o["provider"],
                "trial_ends_at": o["trial_ends_at"],
                "current_period_end": o["current_period_end"],
                "credit_balance": o["credit_balance"],
                "seats": seats,
            }
        )
    return summary


# ── Orders, events and discounts (CP-9 · SC-4g) ─────────────────────────────
#
# ⚠️ Every money value crossing this boundary is an INT of PAISE. Nothing here
# multiplies, discounts or taxes — that is `payments.py`'s job, in pure
# functions a test can run without a database. This module puts the integers in
# and takes them out.

def priced_plan(conn: Connection, *, plan_slug: str) -> dict[str, Any] | None:
    """A catalog row a checkout may sell, or ``None``.

    ``active`` is in the WHERE clause, not left to the caller: 9.1's rule is
    that the checkout cannot sell a thing the catalog does not price, and
    `rnd`/`support` are seeded INACTIVE precisely because their Centers do not
    exist yet. A caller that had to remember the filter is a caller that
    eventually sells R&D Center.
    """
    row = conn.execute(
        text(
            "SELECT slug, kind, price_inr FROM plan_catalog "
            "WHERE slug = :slug AND active"
        ),
        {"slug": plan_slug},
    ).first()
    return (
        {"slug": row[0], "kind": row[1], "price_inr": row[2]}
        if row else None
    )


def create_order(
    conn: Connection, *, org_id: str, provider: str,
    gross_paise: int, discount_paise: int, taxable_paise: int,
    gst_paise: int, total_paise: int, gst_split: str,
    customer_gstin: str | None, place_of_supply: str | None,
    expires_in_minutes: int,
    lines: list[dict[str, Any]],
) -> str:
    """Write the order and its lines. **Writes nothing else, by design.**

    No entitlement, no seat, no ledger row, no subscription change — which is
    exactly what makes this reachable with the customer's own read-mostly
    organization key (CP-9 §9.3(2)). If this function ever grows a second
    table, the transitive fence
    ``test_no_org_key_route_writes_an_entitlement_or_ledger_row`` goes red, and
    that is the intended outcome rather than an obstacle.

    ``expires_at`` is computed by the DATABASE (``make_interval``), not by the
    app process: the two clocks differ by a measured fraction of a second, and
    an expiry the app believes in but the database does not is a bug that only
    appears near the boundary.
    """
    order_id = str(conn.execute(
        text(
            """
            INSERT INTO payment_order
                (organization_id, provider, gross_paise, discount_paise,
                 taxable_paise, gst_paise, total_paise, gst_split,
                 customer_gstin, place_of_supply, expires_at)
            VALUES
                (:org, :provider, :gross, :discount, :taxable, :gst, :total,
                 :split, :gstin, :pos,
                 now() + make_interval(mins => :ttl))
            RETURNING id
            """
        ),
        {"org": org_id, "provider": provider, "gross": gross_paise,
         "discount": discount_paise, "taxable": taxable_paise,
         "gst": gst_paise, "total": total_paise, "split": gst_split,
         "gstin": customer_gstin, "pos": place_of_supply,
         "ttl": expires_in_minutes},
    ).scalar_one())

    for line in lines:
        conn.execute(
            text(
                """
                INSERT INTO payment_order_line
                    (order_id, plan_slug, quantity, unit_price_paise)
                VALUES (:order, :plan, :qty, :unit)
                """
            ),
            {"order": order_id, "plan": line["plan_slug"],
             "qty": line["quantity"], "unit": line["unit_price_paise"]},
        )
    return order_id


def set_provider_order_id(
    conn: Connection, *, order_id: str, provider_order_id: str
) -> None:
    """Record the provider's id for an order we just created there."""
    conn.execute(
        text("UPDATE payment_order SET provider_order_id = :p WHERE id = :i"),
        {"p": provider_order_id, "i": order_id},
    )


def detach_provider(conn: Connection, *, order_id: str) -> None:
    """Make an order the Rs 0 path's own: ``provider='none'``, no provider id.

    SC-4g (iv)'s recorded shape for a 100 percent redemption. The provider
    order created when the intent was still priced is left to expire at the
    provider — the harmless orphan — and this row stops pointing at it, so
    nothing later mistakes an unpaid provider order for the reference of a
    completed purchase.
    """
    conn.execute(
        text(
            "UPDATE payment_order SET provider = 'none', "
            "       provider_order_id = NULL WHERE id = :i"
        ),
        {"i": order_id},
    )


#: Columns every order read returns. Named once so the two reads
#: (`order_row`/`order_for_update` and `orders_page`) cannot drift into
#: answering different questions — and so `provider_order_id` is absent from
#: the *wire* by being absent from the view model, not by being filtered later.
_ORDER_COLUMNS = """
    id, organization_id, status, provider, provider_order_id,
    gross_paise, discount_paise, taxable_paise, gst_paise, total_paise,
    gst_split, customer_gstin, place_of_supply,
    expires_at, created_at, terminal_at,
    (expires_at <= now()) AS expired
"""


def _order_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "status": row.status,
        "provider": row.provider,
        "provider_order_id": row.provider_order_id,
        "gross_paise": int(row.gross_paise),
        "discount_paise": int(row.discount_paise),
        "taxable_paise": int(row.taxable_paise),
        "gst_paise": int(row.gst_paise),
        "total_paise": int(row.total_paise),
        "gst_split": row.gst_split,
        "customer_gstin": row.customer_gstin,
        "place_of_supply": row.place_of_supply,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "terminal_at": row.terminal_at,
        # Evaluated by the DATABASE's clock, never by the app's. The two differ
        # by a measured fraction of a second on our own containers, and an
        # expiry decided on the wrong clock is a decision nobody can reproduce.
        "expired": bool(row.expired),
    }


def order_row(
    conn: Connection, *, order_id: str, org_id: str
) -> dict[str, Any] | None:
    """One order, **scoped to the owning organization** (CP-9 §9.3(7)).

    ``organization_id`` is part of the predicate rather than something the
    caller checks afterwards. A foreign order and a missing one therefore
    return the same ``None`` here, which is what lets the route answer one
    byte-identical 404 for both — a 403-for-foreign / 404-for-unknown split is
    a membership oracle over other tenants' order ids.
    """
    row = conn.execute(
        text(f"SELECT {_ORDER_COLUMNS} FROM payment_order "
             "WHERE id = :id AND organization_id = :org"),
        {"id": order_id, "org": org_id},
    ).first()
    return _order_dict(row) if row else None


def order_for_update(
    conn: Connection, *, order_id: str, org_id: str | None = None
) -> dict[str, Any] | None:
    """One order, **row-locked** for the duration of the transaction.

    ``FOR UPDATE`` is the concurrency half of the money guard: two capture
    events for one order arriving at once are serialised here, so the second
    reads the state the first COMMITTED (``captured``) rather than the state it
    started from. Without it both transactions would see `created`, both would
    pass the terminal check, and both would grant.

    ``org_id`` is optional because the webhook has no organization to scope to
    — it resolves an order from a provider identifier, and the authority it
    carries is a verified signature rather than a key.
    """
    scope = " AND organization_id = :org" if org_id is not None else ""
    row = conn.execute(
        text(f"SELECT {_ORDER_COLUMNS} FROM payment_order "
             f"WHERE id = :id{scope} FOR UPDATE"),
        {"id": order_id, "org": org_id} if org_id is not None
        else {"id": order_id},
    ).first()
    return _order_dict(row) if row else None


def order_by_provider_id(
    conn: Connection, *, provider_order_id: str
) -> dict[str, Any] | None:
    """The order a webhook is about, row-locked. ``None`` when we do not know it."""
    row = conn.execute(
        text(f"SELECT {_ORDER_COLUMNS} FROM payment_order "
             "WHERE provider_order_id = :p FOR UPDATE"),
        {"p": provider_order_id},
    ).first()
    return _order_dict(row) if row else None


def orders_page(
    conn: Connection, *, org_id: str, limit: int,
    status: str | None = None, cursor: str | None = None,
) -> list[dict[str, Any]]:
    """One page of an organization's orders, newest first.

    Keyset pagination on ``(created_at, id)`` rather than OFFSET: a customer
    can grow this table without limit (9.3(6)'s named residual), and OFFSET on
    a growing table both slows down and skips rows when a new one lands
    mid-scan. The cursor is itself org-scoped in the sub-select, so a cursor
    from another tenant's order selects nothing rather than paging into it.
    """
    clauses = ["organization_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": limit}
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    if cursor is not None:
        clauses.append(
            "(created_at, id) < (SELECT created_at, id FROM payment_order "
            " WHERE id = :cursor AND organization_id = :org)"
        )
        params["cursor"] = cursor
    rows = conn.execute(
        text(
            f"SELECT {_ORDER_COLUMNS} FROM payment_order "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ),
        params,
    ).all()
    return [_order_dict(r) for r in rows]


def order_lines(conn: Connection, *, order_id: str) -> list[dict[str, Any]]:
    """The order's lines, with the catalog ``kind`` joined in.

    ``kind`` rides along because fulfilment branches on it (a pack writes the
    ledger, a package writes a seat grant) and a second query per line would
    make that branch N round trips.
    """
    return [
        {"plan_slug": r[0], "quantity": int(r[1]),
         "unit_price_paise": int(r[2]), "kind": r[3]}
        for r in conn.execute(
            text(
                "SELECT l.plan_slug, l.quantity, l.unit_price_paise, p.kind "
                "FROM payment_order_line l "
                "JOIN plan_catalog p ON p.slug = l.plan_slug "
                "WHERE l.order_id = :o ORDER BY l.plan_slug"
            ),
            {"o": order_id},
        )
    ]


def set_order_status(
    conn: Connection, *, order_id: str, status: str, terminal: bool
) -> None:
    """Write a state the caller has already checked against the graph.

    ``terminal`` is passed in rather than derived here, so the terminal set
    lives in exactly one place (``payments.ORDER_TERMINAL_STATES``) and this
    module keeps its promise to decide nothing. ``terminal_at`` is stamped by
    the database's clock for the same reason ``expired`` is read from it.
    """
    conn.execute(
        text(
            "UPDATE payment_order SET status = :s, "
            "       terminal_at = CASE WHEN :terminal THEN now() "
            "                          ELSE terminal_at END "
            "WHERE id = :i"
        ),
        {"s": status, "i": order_id, "terminal": terminal},
    )


def lock_org_activation(conn: Connection, *, org_id: str) -> None:
    """Serialise the manual-activation double-grant guard for one organization.

    The seat cap's idiom (:func:`lock_seat_capacity`) one plane along:
    ``POST /billing/subscriptions/activate`` reads ``org_subscription.status``
    and refuses ``active`` with a 409, then — for a ``trial``/no-row org —
    appends paid seats and credits. At READ COMMITTED that check-then-append
    does not hold: two concurrent activations of the SAME fresh org both read
    status≠``active``, both pass the guard, and because ``grant_seats`` /
    ``add_credit`` are conflict-free INSERTs, **both grant** — a 5-seat/250-credit
    transfer mints 10 seats/500 credits. The 409 only protects the SEQUENTIAL
    repeat, and ``FOR UPDATE`` cannot help because a fresh org has no
    ``org_subscription`` row to lock — which is why this is an advisory lock.

    Taken as the FIRST statement of the route's transaction, **before** the
    status read: a lock acquired after the read protects nothing, because the
    stale status is already in hand. Transaction-scoped (``_xact_``), so it is
    released by COMMIT or ROLLBACK and there is no unlock to forget on the 409
    path.

    The key is PER-ORG (``activation:<org_id>``) so activations of *different*
    orgs never serialise, and namespaced so it cannot collide with the seat
    lock's ``<org>:<plan>`` space or the discount lock's ``discount:<id>`` space
    in ``hashtext``'s single keyspace. A hash collision costs one needless
    serialisation and is otherwise harmless — the failure mode is slowness,
    never a double grant.
    """
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"activation:{org_id}"},
    )


def activate_subscription(
    conn: Connection, *, org_id: str, term_months: int,
    provider: str | None, provider_customer_id: str | None,
    provider_subscription_id: str | None,
) -> None:
    """Start (or renew) the purchased term.

    ⚠️ ``provider`` is ``'razorpay'`` or **NULL**, never ``'none'``:
    ``org_subscription.provider`` is ``CHECK (provider IN ('razorpay',
    'manual'))`` and ``'none'`` — which is legitimate on
    ``payment_order.provider`` — violates it. NULL is the honest record of "no
    provider was involved", and it is the ONE difference between the Rs 0 and
    paid paths that the equivalence fence asserts rather than excludes.

    The period is computed by the database so both paths read one clock.

    ⚠️ This writes ``org_subscription.status``, which is a different column
    from ``organization.status`` and is deliberately the only one fulfilment
    touches: a suspended organization that pays holds an active paid term and
    is still suspended until an operator posts the transition (done-when 16).
    The two rows disagreeing is the *expected* intermediate state, and CP-8's
    queue is what renders it.
    """
    conn.execute(
        text(
            """
            INSERT INTO org_subscription
                (organization_id, status, current_period_start,
                 current_period_end, provider, provider_customer_id,
                 provider_subscription_id)
            VALUES
                (:org, 'active', now()::date,
                 (now() + make_interval(months => :months))::date,
                 :provider, :customer, :subscription)
            ON CONFLICT (organization_id) DO UPDATE SET
                status = 'active',
                current_period_start = EXCLUDED.current_period_start,
                current_period_end = EXCLUDED.current_period_end,
                provider = EXCLUDED.provider,
                provider_customer_id = EXCLUDED.provider_customer_id,
                provider_subscription_id = EXCLUDED.provider_subscription_id,
                updated_at = now()
            """
        ),
        {"org": org_id, "months": term_months, "provider": provider,
         "customer": provider_customer_id,
         "subscription": provider_subscription_id},
    )


def record_payment_event(
    conn: Connection, *, provider_event_id: str, order_id: str | None,
    kind: str, body: str,
) -> bool:
    """Record one verified delivery. **False when we have seen this id before.**

    ``ON CONFLICT DO NOTHING`` on the primary key is TRANSPORT dedup and only
    that: it makes the same delivery, delivered twice, a no-op. It does not and
    cannot stop a double grant, because one capture arrives as two events with
    two different ids — that is the terminal-state rule's job.

    In the SAME transaction as the fulfilment it guards, so a fulfilment that
    rolls back takes its receipt with it and the provider's retry is evaluated
    afresh rather than deduped into silence.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO payment_event
                (provider_event_id, order_id, kind, body)
            VALUES (:eid, :order, :kind, CAST(:body AS jsonb))
            ON CONFLICT (provider_event_id) DO NOTHING
            RETURNING provider_event_id
            """
        ),
        {"eid": provider_event_id, "order": order_id, "kind": kind,
         "body": body},
    ).first()
    return row is not None


def capture_provider_refs(
    conn: Connection, *, order_id: str
) -> dict[str, Any]:
    """Provider identifiers from the newest capture event for this order.

    Read from ``payment_event.body`` rather than carried through the call
    chain: the event is already recorded in this transaction, and a parameter
    threaded through :func:`payments.fulfil` would have to be optional for the
    Rs 0 path — an optional parameter that must be present on one path and
    absent on the other is a second signature wearing one name.
    """
    row = conn.execute(
        text(
            """
            SELECT body #>> '{payload,payment,entity,customer_id}',
                   body #>> '{payload,payment,entity,id}'
            FROM payment_event
            WHERE order_id = :o AND kind IN ('payment.captured', 'order.paid')
            ORDER BY received_at DESC
            LIMIT 1
            """
        ),
        {"o": order_id},
    ).first()
    return {"customer_id": row[0], "payment_id": row[1]} if row else {}


# ── Discount codes (SC-4g) ──────────────────────────────────────────────────

def issue_discount_code(
    conn: Connection, *, prefix: str, code_hash: str, label: str,
    kind: str, org_id: str | None = None, percent_bp: int | None = None,
    amount_paise: int | None = None, max_redemptions: int = 1,
    expires_at: datetime | None = None, created_by: str = "operator",
) -> str:
    """Store a freshly minted code. The secret is never passed here.

    Mirrors :func:`issue_key` and :func:`issue_deployment_key` deliberately
    rather than generalising the three: they write different tables with
    different owners and different lifetimes, and one function taking "which
    kind" would put that decision at every call site.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO discount_code
                (prefix, code_hash, label, organization_id, kind, percent_bp,
                 amount_paise, max_redemptions, expires_at, created_by)
            VALUES (:prefix, :hash, :label, :org, :kind, :bp, :amount,
                    :max, :expires, :by)
            RETURNING id
            """
        ),
        {"prefix": prefix, "hash": code_hash, "label": label, "org": org_id,
         "kind": kind, "bp": percent_bp, "amount": amount_paise,
         "max": max_redemptions, "expires": expires_at, "by": created_by},
    ).first()
    assert row is not None
    return str(row[0])


def resolve_discount_code(
    conn: Connection, *, prefix: str
) -> dict[str, Any] | None:
    """Look up a code by its clear prefix. Returns the row, judges nothing.

    ⚠️ Deliberately **no** filtering on ``expires_at``/``revoked_at`` here,
    unlike :func:`resolve_key`'s ``revoked_at IS NULL``. The difference is the
    acceptance criterion: SC-4g done-when 4 requires ``expired`` and
    ``revoked`` to be told apart *for a code this organization may see*, so a
    query that folded them into "not found" would make the partition
    unimplementable. The route decides; this returns the facts.

    ``expired`` is evaluated against the DATABASE clock for the same reason
    every other clock question here is.
    """
    row = conn.execute(
        text(
            """
            SELECT id, prefix, code_hash, label, organization_id, kind,
                   percent_bp, amount_paise, max_redemptions,
                   (expires_at IS NOT NULL AND expires_at <= now()) AS expired,
                   (revoked_at IS NOT NULL) AS revoked
            FROM discount_code WHERE prefix = :prefix
            """
        ),
        {"prefix": prefix},
    ).first()
    if row is None:
        return None
    return {
        "id": str(row.id), "prefix": row.prefix, "code_hash": row.code_hash,
        "label": row.label,
        "organization_id": (str(row.organization_id)
                            if row.organization_id else None),
        "kind": row.kind, "percent_bp": row.percent_bp,
        "amount_paise": (int(row.amount_paise)
                         if row.amount_paise is not None else None),
        "max_redemptions": int(row.max_redemptions),
        "expired": bool(row.expired), "revoked": bool(row.revoked),
    }


def lock_discount_capacity(conn: Connection, *, code_id: str) -> None:
    """Serialise the redemption-count decision for one code.

    The seat cap's idiom, one table along (``lock_seat_capacity``, and its
    docstring carries the measurement: 10 races, both winners, on a real
    server). Without it ``max_redemptions`` is check-then-insert across two
    DIFFERENT orders, which ``UNIQUE (discount_code_id, order_id)`` does not
    cover — that index stops one order redeeming one code twice, which is a
    different claim from N redemptions in total.

    Transaction-scoped, so there is no unlock to forget on the refusal path.
    The key is namespaced (``discount:<id>``) so it cannot collide with the
    seat lock's ``<org>:<plan>`` space in ``hashtext``'s single keyspace.
    """
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"discount:{code_id}"},
    )


def count_redemptions(conn: Connection, *, code_id: str) -> int:
    """``COUNT(discount_redemption)``, never a mutable ``times_used`` column.

    The same append-only argument ``credit_ledger`` makes: the count and the
    evidence for it are the same rows, so they cannot disagree.
    """
    return int(conn.execute(
        text("SELECT count(*) FROM discount_redemption "
             "WHERE discount_code_id = :c"),
        {"c": code_id},
    ).scalar_one())


def write_redemption(
    conn: Connection, *, code_id: str, org_id: str, order_id: str,
    gross_paise: int, discount_paise: int, net_paise: int,
) -> str | None:
    """Record a redemption. **None when this order already redeemed this code.**

    ``ON CONFLICT DO NOTHING`` on ``(discount_code_id, order_id)`` is what
    makes re-submitting the same code against the same order redeem *once*
    (done-when 5) — enforced by the index rather than by a prior SELECT, so a
    concurrent double-submit loses at the database instead of racing past a
    check.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO discount_redemption
                (discount_code_id, organization_id, order_id, gross_paise,
                 discount_paise, net_paise)
            VALUES (:code, :org, :order, :gross, :discount, :net)
            ON CONFLICT (discount_code_id, order_id) DO NOTHING
            RETURNING id
            """
        ),
        {"code": code_id, "org": org_id, "order": order_id,
         "gross": gross_paise, "discount": discount_paise, "net": net_paise},
    ).first()
    return str(row[0]) if row else None


def redemption_for_order(
    conn: Connection, *, order_id: str
) -> dict[str, Any] | None:
    """The redemption applied to this order, with the code's PREFIX only.

    ``code_hash`` is excluded at the SQL level rather than filtered afterwards,
    exactly as :func:`list_keys` excludes it: a column that is never selected
    cannot be leaked by a later caller that forgets to strip it.
    """
    row = conn.execute(
        text(
            "SELECT r.id, c.prefix, r.discount_paise "
            "FROM discount_redemption r "
            "JOIN discount_code c ON c.id = r.discount_code_id "
            "WHERE r.order_id = :o ORDER BY r.created_at LIMIT 1"
        ),
        {"o": order_id},
    ).first()
    return (
        {"id": str(row[0]), "code_prefix": row[1],
         "discount_paise": int(row[2])}
        if row else None
    )


def apply_discount_to_order(
    conn: Connection, *, order_id: str, discount_paise: int,
    taxable_paise: int, gst_paise: int, total_paise: int,
) -> None:
    """Write the recomputed money columns. The arithmetic happened in Python.

    All four move together in one statement, because the table's CHECK
    constraints relate them (``taxable = gross - discount``,
    ``total = taxable + gst``) — a partial update would be rejected by the
    database, which is the intended design rather than an inconvenience.
    """
    conn.execute(
        text(
            "UPDATE payment_order SET discount_paise = :d, "
            "       taxable_paise = :t, gst_paise = :g, total_paise = :total "
            "WHERE id = :i"
        ),
        {"d": discount_paise, "t": taxable_paise, "g": gst_paise,
         "total": total_paise, "i": order_id},
    )


def resolve_key(conn: Connection, *, prefix: str
                ) -> tuple[str, str, str] | None:
    """Look up a live key. Returns ``(organization_id, key_hash, org_status)``.

    Indexed lookup on the prefix, then a constant-time hash comparison by the
    caller. Two conditions are resolved in SQL rather than left to callers,
    because "did you also check…" is exactly the step a second call site
    forgets:

    * ``revoked_at IS NULL`` — a revoked key resolves to nothing.
    * the owning organization's ``status`` is returned, so the caller can
      refuse a cancelled or suspended customer. Verification found a cancelled
      organization still metering happily against a live key (F4); returning
      the status here is what lets that be a one-line check rather than a
      second query every caller must remember.
    """
    row = conn.execute(
        text(
            """
            SELECT k.organization_id, k.key_hash, o.status
            FROM llm_api_key k
            JOIN organization o ON o.id = k.organization_id
            WHERE k.prefix = :prefix AND k.revoked_at IS NULL
            """
        ),
        {"prefix": prefix},
    ).first()
    return (str(row[0]), row[1], row[2]) if row else None


# ── Operator registry (CP-12a) ──────────────────────────────────────────────
#
# Spec: operator_identity_and_access.md §4 · §6.1 · D64.
#
# SQL only. Whether a row means "admitted" is decided in
# `customer_console.operators`, which holds no queries for the same reason
# this module holds no policy.


def operator_by_email(conn: Connection, email: str) -> dict[str, Any] | None:
    """The registry row for *email*, or ``None``.

    ``lower(email) = :email`` with a lower-cased parameter, because the
    directory's casing is a display choice and an operator must not be
    locked out by it. ⚠️ R8 is why this is not a hermetic test: a fake once
    matched ``lower(col) = :param`` against NULL and shipped green.
    """
    row = conn.execute(
        text(
            """
            SELECT id, email, role, status, directory_subject
              FROM operator
             WHERE lower(email) = :email
            """
        ),
        {"email": (email or "").strip().lower()},
    ).mappings().first()
    return dict(row) if row else None


def operator_count(conn: Connection) -> int:
    """How many operators exist, in ANY status.

    Any status, not just ``active`` — this is the predicate the one-time
    bootstrap turns on, and counting only the active ones would let a
    registry whose single admin was deactivated be bootstrapped a second
    time. That is the back door the bootstrap must not become.
    """
    row = conn.execute(text("SELECT count(*) FROM operator")).first()
    return int(row[0]) if row else 0


def operator_active_admin_count(conn: Connection) -> int:
    """How many ``active`` admins exist — the last-admin guard's input."""
    row = conn.execute(
        text(
            "SELECT count(*) FROM operator "
            "WHERE role = 'admin' AND status = 'active'"
        )
    ).first()
    return int(row[0]) if row else 0


def operator_insert(
    conn: Connection,
    *,
    email: str,
    role: str,
    added_by: str | None = None,
) -> str:
    """Add one operator. Returns the new id.

    ``ON CONFLICT DO NOTHING`` plus a re-read, so adding somebody who is
    already there is not an error and not a duplicate. The caller that
    cares about which one happened compares the returned id.
    """
    normalised = (email or "").strip().lower()
    row = conn.execute(
        text(
            """
            INSERT INTO operator (email, role, added_by)
            VALUES (:email, :role, CAST(:added_by AS UUID))
            ON CONFLICT (email) DO NOTHING
            RETURNING id
            """
        ),
        {"email": normalised, "role": role, "added_by": added_by},
    ).first()
    if row is not None:
        return str(row[0])
    existing = conn.execute(
        text("SELECT id FROM operator WHERE lower(email) = :email"),
        {"email": normalised},
    ).first()
    assert existing is not None  # DO NOTHING fired, so the row is there
    return str(existing[0])


def operator_set_directory_subject(
    conn: Connection, *, operator_id: str, subject: str
) -> None:
    """Record the Entra object id learned on a successful sign-in.

    ``WHERE directory_subject IS NULL`` so the FIRST sign-in writes it and
    later ones do not. A subject that silently changed would mean a
    different directory principal now answers to this row, and that is a
    thing to notice rather than to overwrite.
    """
    conn.execute(
        text(
            """
            UPDATE operator
               SET directory_subject = :subject, updated_at = now()
             WHERE id = CAST(:id AS UUID)
               AND directory_subject IS NULL
            """
        ),
        {"id": operator_id, "subject": subject},
    )


# ── Operator sessions (CP-12b) ──────────────────────────────────────────────


def operator_session_insert(
    conn: Connection,
    *,
    operator_id: str,
    prefix: str,
    key_hash: str,
    expires_at: datetime,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Store a freshly minted session. Only the HASH is passed here."""
    row = conn.execute(
        text(
            """
            INSERT INTO operator_session
                (operator_id, prefix, key_hash, expires_at, ip, user_agent)
            VALUES (CAST(:op AS UUID), :prefix, :hash, :expires,
                    CAST(:ip AS INET), :ua)
            RETURNING id
            """
        ),
        {"op": operator_id, "prefix": prefix, "hash": key_hash,
         "expires": expires_at, "ip": ip, "ua": user_agent},
    ).first()
    assert row is not None
    return str(row[0])


def operator_session_by_prefix(
    conn: Connection, prefix: str
) -> dict[str, Any] | None:
    """The session AND the operator behind it, in one read.

    ⚠️ The JOIN is the point. A session is only as good as the person, so
    ``status`` and ``role`` come back on every request rather than being
    trusted from sign-in time. That is what lets a deactivation take effect
    at once instead of whenever the session happened to expire.
    """
    row = conn.execute(
        text(
            """
            SELECT s.id, s.operator_id, s.key_hash, s.expires_at,
                   s.last_seen_at, s.revoked_at,
                   o.email, o.role, o.status
              FROM operator_session s
              JOIN operator o ON o.id = s.operator_id
             WHERE s.prefix = :prefix
            """
        ),
        {"prefix": prefix},
    ).mappings().first()
    return dict(row) if row else None


def operator_session_touch(conn: Connection, session_id: str) -> None:
    """Push the idle clock forward. Called once a request is admitted.

    ⚠️ Deliberately NOT called on a rejected request. Touching on refusal
    would let an attacker holding a revoked or expired token keep the row
    warm, which is the opposite of what the idle clock is for.

    ⚠️ **Two things protect this, and only one of them is obvious.** The
    ordering in ``auth._staff_session`` is the first. The second is that
    the refusal raises out of a ``get_engine().begin()`` block, so a touch
    written into the ``except`` arm would be ROLLED BACK and never land.
    Measured while mutation-testing CP-12b: a mutation that added the touch
    inside the ``except`` survived for exactly that reason, and only a
    version that opened its OWN transaction was caught. Do not read the
    surviving mutation as a missing fence — read it as a second guard.
    """
    conn.execute(
        text(
            "UPDATE operator_session SET last_seen_at = now() "
            "WHERE id = CAST(:id AS UUID)"
        ),
        {"id": session_id},
    )


def operator_session_revoke(conn: Connection, session_id: str) -> None:
    """Sign out one session.

    ``WHERE revoked_at IS NULL`` so a second sign-out cannot move the
    timestamp — when a session was revoked is an audit fact.
    """
    conn.execute(
        text(
            "UPDATE operator_session SET revoked_at = now() "
            "WHERE id = CAST(:id AS UUID) AND revoked_at IS NULL"
        ),
        {"id": session_id},
    )


def operator_sessions_revoke_all(conn: Connection, operator_id: str) -> int:
    """Revoke EVERY live session for one operator. Returns how many.

    This is the fix for spec §2's F5 — *"removing one person means changing
    the secret for everybody"*. The caller runs it in the SAME transaction
    that suspends or deactivates the operator, so there is no window in
    which the row says `deactivated` and a session still works.
    """
    result = conn.execute(
        text(
            "UPDATE operator_session SET revoked_at = now() "
            "WHERE operator_id = CAST(:op AS UUID) AND revoked_at IS NULL"
        ),
        {"op": operator_id},
    )
    return int(result.rowcount or 0)


# ── Operator administration (CP-12d) ────────────────────────────────────────


def operator_by_id(conn: Connection, operator_id: str) -> dict[str, Any] | None:
    """One registry row by id, or ``None``."""
    row = conn.execute(
        text(
            "SELECT id, email, role, status, directory_subject, created_at "
            "FROM operator WHERE id = CAST(:id AS UUID)"
        ),
        {"id": operator_id},
    ).mappings().first()
    return dict(row) if row else None


def operator_list(conn: Connection) -> list[dict[str, Any]]:
    """Every operator, active first, then by email.

    No pagination. This table holds a handful of people by design — the
    moment it does not, DEF-3's directory sync is the answer rather than a
    page cursor over our own staff.
    """
    rows = conn.execute(
        text(
            """
            SELECT o.id, o.email, o.role, o.status, o.created_at,
                   o.directory_subject IS NOT NULL AS has_signed_in,
                   (SELECT count(*) FROM operator_session s
                     WHERE s.operator_id = o.id
                       AND s.revoked_at IS NULL
                       AND s.expires_at > now()) AS live_sessions
              FROM operator o
             ORDER BY (o.status = 'active') DESC, o.email
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def operator_set_role(conn: Connection, *, operator_id: str, role: str) -> None:
    """Change one operator's role."""
    conn.execute(
        text(
            "UPDATE operator SET role = :role, updated_at = now() "
            "WHERE id = CAST(:id AS UUID)"
        ),
        {"id": operator_id, "role": role},
    )


def operator_set_status(
    conn: Connection, *, operator_id: str, status: str
) -> None:
    """Change one operator's status.

    ⚠️ The caller revokes the sessions. Doing it here would hide a security
    step inside a setter, and a future caller that wanted only the status
    change would silently get both — or, worse, would write its own status
    update and get neither.
    """
    conn.execute(
        text(
            "UPDATE operator SET status = :status, updated_at = now() "
            "WHERE id = CAST(:id AS UUID)"
        ),
        {"id": operator_id, "status": status},
    )


# ── Operator elevation (CP-12e) ─────────────────────────────────────────────


def operator_elevation_open(
    conn: Connection,
    *,
    operator_id: str,
    reason: str,
    reference: str | None,
    expires_at: datetime,
) -> str:
    """Open one elevation window. Returns its id."""
    row = conn.execute(
        text(
            """
            INSERT INTO operator_elevation
                (operator_id, reason, reference, expires_at)
            VALUES (CAST(:op AS UUID), :reason, :ref, :expires)
            RETURNING id
            """
        ),
        {"op": operator_id, "reason": reason, "ref": reference,
         "expires": expires_at},
    ).first()
    assert row is not None
    return str(row[0])


def operator_elevation_live(
    conn: Connection, operator_id: str
) -> dict[str, Any] | None:
    """The newest window that is neither revoked nor expired, or ``None``.

    ⚠️ The expiry is filtered in SQL with `now()` AND re-checked in
    `operator_elevation.check_window`. That is not redundant: the SQL keeps
    the read cheap, and the Python check is what a reviewer can see, so
    neither one is the only thing standing between a stale row and a purge.
    """
    row = conn.execute(
        text(
            """
            SELECT id, reason, reference, granted_at, expires_at
              FROM operator_elevation
             WHERE operator_id = CAST(:op AS UUID)
               AND revoked_at IS NULL
               AND expires_at > now()
             ORDER BY expires_at DESC
             LIMIT 1
            """
        ),
        {"op": operator_id},
    ).mappings().first()
    return dict(row) if row else None


def operator_elevation_close(conn: Connection, operator_id: str) -> int:
    """Close every live window for one operator. Returns how many."""
    result = conn.execute(
        text(
            "UPDATE operator_elevation SET revoked_at = now() "
            "WHERE operator_id = CAST(:op AS UUID) AND revoked_at IS NULL"
        ),
        {"op": operator_id},
    )
    return int(result.rowcount or 0)


# ── CP-12f: reading the audit trail across every company ───────────────────


def activity_page(
    conn: Connection,
    *,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    org_slug: str | None = None,
) -> list[dict[str, Any]]:
    """One page of ``control_audit``, newest first, across ALL organizations.

    Spec: ``operator_identity_and_access.md`` §8.1 done-whens 25-26.

    ⚠️ **LEFT JOIN, and it is load-bearing.** ``control_audit.organization_id``
    is NULLABLE and two classes of row rely on that:

    * every ``operator.*`` action — adding a colleague names no company;
    * every row of a PURGED organization, because the column is
      ``ON DELETE SET NULL`` so the history survives the customer (D63, and
      done-when 19).

    An INNER JOIN would compile, pass a casual read, and silently hide exactly
    the rows an investigation needs. ``test_operator_activity.py`` pins both.

    ⚠️ **Ordering: ``(created_at DESC, id DESC)``, a strict TOTAL order.**
    ``created_at`` alone is not unique — one request can write several rows and
    ``now()`` gives them all the transaction-start stamp — so paging on it
    alone would repeat or drop rows at a page boundary. The ``id`` tiebreak is
    what makes the keyset exact. Read :data:`~customer_console.operator_
    activity.CURSOR_IS_EPHEMERAL` before changing this: the ordering carries a
    known and measured limitation (H-7).
    """
    rows = conn.execute(
        text(
            """
            SELECT a.id,
                   a.actor,
                   a.action,
                   a.detail,
                   a.created_at,
                   o.slug AS org_slug,
                   o.name AS org_name
              FROM control_audit a
              LEFT JOIN organization o ON o.id = a.organization_id
             WHERE (CAST(:actor AS TEXT) IS NULL OR a.actor = CAST(:actor AS TEXT))
               AND (CAST(:action AS TEXT) IS NULL
                    OR a.action = CAST(:action AS TEXT))
               AND (CAST(:org_slug AS TEXT) IS NULL
                    OR o.slug = CAST(:org_slug AS TEXT))
               AND (CAST(:cursor_ts AS TIMESTAMPTZ) IS NULL
                    OR (a.created_at, a.id)
                       < (CAST(:cursor_ts AS TIMESTAMPTZ),
                          CAST(:cursor_id AS UUID)))
             ORDER BY a.created_at DESC, a.id DESC
             LIMIT :limit
            """
        ),
        {
            "actor": actor,
            "action": action,
            "org_slug": org_slug,
            "cursor_ts": cursor_created_at,
            "cursor_id": cursor_id,
            "limit": limit,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


def activity_actions(conn: Connection) -> list[str]:
    """Every ``action`` value that actually occurs, for a filter control.

    Read from the data rather than from a constant list, because the constant
    would be a second vocabulary to keep in step with the call sites. An action
    nobody has performed does not need a filter entry.
    """
    rows = conn.execute(
        text("SELECT DISTINCT action FROM control_audit ORDER BY action")
    ).all()
    return [r[0] for r in rows]


# ── CP-10 slice 1: the provider credential write path (H-40) ───────────────


def provider_credential_insert(
    conn: Connection,
    *,
    provider: str,
    secret_enc: str,
    api_base: str | None = None,
    label: str | None = None,
    organization_id: str | None = None,
) -> str:
    """Install one provider credential. **Only the ciphertext is passed here.**

    Spec: ``customer_console.md`` CP-10 slice 1.

    ⚠️ The plaintext never reaches this module. ``router.encrypt_secret`` is
    the one Fernet seam and the route calls it, so a reader of `store.py`
    cannot accidentally write a secret in the clear.

    ⚠️ **INSERT only, and there is deliberately no UPDATE sibling.** The spec
    forbids a mutable credential row, and `provider_keys.check_api_base`
    records the second reason: without an UPDATE path nobody can re-point an
    existing credential at a host they control and read a secret no route
    returns.
    """
    row = conn.execute(
        text(
            """
            INSERT INTO provider_credential
                (provider, organization_id, secret_enc, api_base, label)
            VALUES (:provider, CAST(:org AS uuid), :secret, :api_base, :label)
            RETURNING id
            """
        ),
        {"provider": provider, "org": organization_id, "secret": secret_enc,
         "api_base": api_base, "label": label},
    ).first()
    assert row is not None
    return str(row[0])


def provider_credential_list(
    conn: Connection, *, include_revoked: bool = False
) -> list[dict[str, Any]]:
    """Every credential, as METADATA. **`secret_enc` is not selected.**

    ⚠️ That omission is the fence, and it is structural rather than a habit.
    A caller cannot leak what a query never fetched, so the plaintext cannot
    reach a response through this function however carelessly it is used.
    ``test_provider_keys.py`` asserts the column list at source level.
    """
    rows = conn.execute(
        text(
            """
            SELECT c.id, c.provider, c.api_base, c.label,
                   c.created_at, c.revoked_at,
                   o.slug AS org_slug
              FROM provider_credential c
              LEFT JOIN organization o ON o.id = c.organization_id
             WHERE (:all_rows OR c.revoked_at IS NULL)
             ORDER BY c.provider, c.organization_id NULLS FIRST,
                      c.created_at DESC
            """
        ),
        {"all_rows": include_revoked},
    ).mappings().all()
    return [dict(r) for r in rows]


def provider_credential_revoke(
    conn: Connection, *, provider: str, organization_id: str | None = None
) -> int:
    """Revoke the LIVE credential for one provider. Returns rows affected.

    ⚠️ Sets ``revoked_at`` rather than deleting the row. The spec names this
    as the one permitted mutation on this table, and the partial unique index
    is built for it: a revoked row stops blocking the live slot, so the next
    install succeeds without destroying the record that the old key existed.

    ⚠️ ``organization_id IS NOT DISTINCT FROM`` rather than ``=``. The platform
    account is the NULL row, and ``NULL = NULL`` is NULL, so an ``=`` here
    would silently revoke nothing and answer as though it had.
    """
    result = conn.execute(
        text(
            """
            UPDATE provider_credential
               SET revoked_at = now()
             WHERE provider = :provider
               AND organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)
               AND revoked_at IS NULL
            """
        ),
        {"provider": provider, "org": organization_id},
    )
    return int(result.rowcount or 0)
