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

__all__ = [
    "add_credit",
    "credit_deltas",
    "ensure_identity",
    "ensure_organization",
    "grant_seats",
    "has_live_seat",
    "plan_price",
    "record_usage",
    "release_seat",
    "resolve_key",
    "run_spend",
    "seat_rows",
    "try_assign_seat",
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
    """
    row = conn.execute(
        text(
            """
            INSERT INTO usage_event
                (organization_id, request_id, billed_credits, user_email,
                 agent, module_slug, model, tier, prompt_tokens,
                 completion_tokens, cached_tokens, provider_cost_usd, run_id, client_ref)
            VALUES
                (:org, :request_id, :billed, :user_email, :agent, :module_slug,
                 :model, :tier, :prompt_tokens, :completion_tokens,
                 :cached_tokens, :provider_cost_usd, :run_id, :client_ref)
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
        },
    ).first()

    if row is None:
        return False

    if billed_credits:
        add_credit(
            conn, org_id=org_id, delta=-billed_credits,
            reason="usage", ref=request_id,
        )
    return True


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
