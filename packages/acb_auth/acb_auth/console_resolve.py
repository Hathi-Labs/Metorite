"""CP-2b — asking the Customer Console whether a sign-in may proceed.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-2b (clauses 6, 7, 8, 9's
deployment half, 11, 12's cache half) · §6(c) failure semantics · §6(e) this
module's home · §6(j) the four outcomes · §6(k) the join key · §5.2 · D32.4 ·
``user_management_contract.md`` R11.

**This is what makes the seat cap real.** A person cannot become a user of an
organization without the Customer Console allocating them a seat, because the
box asks before admitting them.

## Why here

Identity resolution is ``acb_auth``'s job: the seam that already owns *"somebody
is knocking at the front door"* is this package, and putting the decision in a
gateway-service module would place an identity decision outside the identity
seam. This module owns the HTTP call, the read-through cache, the
``invalidate()`` escape hatch and the projection read/write.

## The ONE caller, and why it is not ``resolve_access``

``apps/services/gateway/gateway/routes/signin.py`` and **nothing else**. The
proposed default was to wire this behind ``access.resolve_access``; that was
refused, and the code is why — ``resolve_access`` has six production call sites
and one of them (``routes/rooms.py``) fans it over every participant of a room.
A seat-allocating cross-service call there would fire one Console request **and
one seat allocation** per participant per room load, which is precisely the
farmable cap clause 11 exists to prevent. ``resolve_access``,
``_with_resolved_access`` and ``_ACCESS_SQL`` are untouched by this module.
Fence: ``tests/unit/test_console_dependency_boundary.py``.

## Two caches, two axes, and this one is NOT ``access._cache``

``access._cache`` caches *what this person may do inside a tenant*; this one
caches *which tenant, and what the registry last said about it* — the two axes
D12 already separates. One dict for both would be the second scoping doctrine
root ``AGENTS.md`` §11 forbids, and a second value type inside ``access._cache``
would break ``_cache_get``. What is shared is the **idiom and the escape
hatch**: a module-level dict keyed on ``lower(email)`` with a monotonic
deadline and a public :func:`invalidate`, in the shape of ``access.py:73-98``.

⚠️ **The authoritative store is the projection ROW, not this dict.** A 24-hour
ceiling is unenforceable in a per-process dictionary that a restart wipes, so
the dict is a read-through in FRONT of migration 159's tables and every bound
below is evaluated against ``org_membership.resolved_at``. Consequences to hold
in mind: two workers may each hold a copy for up to a minute, so
:func:`invalidate` is per-process and the authoritative revocation is the row;
and a restart clears the dict but does **not** re-consult the Console for
everyone at once, because the rows still carry the answer.

## Fail closed, degrade bounded — and the bound is a PAIR

* Console **reachable** → a cached answer is re-consulted past
  ``CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS``.
* Console **unreachable** → a cached person proceeds up to
  ``CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS`` and is refused past it. A
  cache with no ceiling is not a cache, it is a second identity system that
  never expires — and the day it matters is the day an account is suspended for
  non-payment.
* No cached answer and no Console → **refuse**, with the service-unavailable
  copy, never "access denied": the person has done nothing wrong, and a
  wrong-looking denial generates a support ticket and a password reset that fix
  nothing (D33.1).
* A cached ``sign_in: false`` overrides all three and refuses **immediately**,
  at any freshness. Staleness may only ever make the cache MORE restrictive.

## It branches on the OUTCOME, never on a lifecycle string

``capabilities_of`` lives in the Console package and this package must not
import it, so the capability decision is made Console-side and only its result
crosses the wire. The four outcomes (§6(j)) are the whole branch surface:

===  =========================  ==========================  ==================
 #   Resolve outcome            Sign-in                     What is cached
===  =========================  ==========================  ==================
 i   200, exactly ONE org       admit                       the whole record
 ii  403                        refuse (AccessDenied)       ``{sign_in: false}``
iii  200, MORE THAN one org     refuse (chooser required)   nothing
 iv  200, ZERO orgs             refuse (AccessDenied)       nothing + forget
===  =========================  ==========================  ==================

A fifth outcome has to be named here, in :func:`resolve_for_signin`, and in
``test_the_four_resolve_outcomes_are_each_handled``. There is one today that
the ticket did not name — the Console's ``409`` at the seat cap — and it is
handled explicitly in :func:`resolve_for_signin` with its reasoning at the
branch. ``registry_status`` is stored, and its ONLY use anywhere is the word a
person is shown; a box that read it and decided would be a second copy of the
state machine spelled as an ``if``.

## Ships dark

With ``CUSTOMER_CONSOLE_URL`` or ``CUSTOMER_CONSOLE_DEPLOYMENT_KEY`` unset,
:func:`resolve_for_signin` admits without a call, a query or a write, and
sign-in behaves exactly as it did before CP-2b. Wiring a live deployment is
🔴 OWNER-GATE (§8 gate 7); declaring the settings is not.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger, get_settings

# The one shared pool (BO-10). The same private name and the same acquisition
# idiom `access.py` uses, so H2's conversion stays mechanical.
#
# ⚠️ Deliberately NOT `tenant_session()`. At sign-in there is no tenant bound —
# the tenant is the ANSWER — so a tenant-bound session would raise
# `TenantUnbound` on the one path that has to work before anybody is inside a
# tenant. The three tables read here (`organization`, `org_membership`,
# `user_identity`) are cross-tenant BY DESIGN and are already exempt from the
# RLS generator for exactly that reason: a policy that hid the tenant list from
# the connection resolving which tenant this is would make the box unable to
# answer its own first question.
from acb_common.db import get_session_factory as _get_session_factory

_log = get_logger("acb_auth.console_resolve")

__all__ = [
    "ACCESS_DENIED",
    "CONSOLE_UNAVAILABLE",
    "WORKSPACE_CHOOSER_REQUIRED",
    "ResolveDecision",
    "invalidate",
    "is_wired",
    "resolve_for_signin",
]

#: Auth.js v5's vocabulary is FIXED — returning `false` from the `signIn`
#: callback produces `AccessDenied` and nothing else, whose shipped copy
#: ("Your account isn't authorized for this workspace") is the phrasing D33.1
#: forbids for two of the refusals below. Two distinguishable reasons therefore
#: need two codes Auth.js does not define, carried by the callback returning a
#: redirect string. Copy lives in one place, `signin/errorCopy.ts`.
CONSOLE_UNAVAILABLE = "ConsoleUnavailable"
WORKSPACE_CHOOSER_REQUIRED = "WorkspaceChooserRequired"
#: The genuine not-authorized case, where the shipped copy is simply TRUE.
ACCESS_DENIED = "AccessDenied"

#: How long a resolved record may be answered from THIS PROCESS's dict before
#: the projection row is re-read. Deliberately the same 60s
#: `access.CACHE_TTL_SECONDS` uses — one idiom — but its own constant, because
#: the two cache different things on different axes and a shared number would
#: couple a permission-revocation latency to a registry-freshness one.
_PROCESS_CACHE_TTL_SECONDS = 60.0

#: A sign-in must never hang on a third party. Short enough that an unreachable
#: Console degrades to the cached path (or to an honest refusal) inside a
#: person's patience, long enough to survive a cold connection.
_HTTP_TIMEOUT_SECONDS = 5.0

_cache: dict[str, tuple[float, _Record]] = {}


class _Unreachable(Exception):
    """The Customer Console did not give us an answer we could read."""


@dataclass(frozen=True)
class _Record:
    """What the projection last recorded about one person's organization."""

    slug: str
    #: The capability object EXACTLY as the wire carried it. A missing key
    #: means NOT OBSERVED, never False.
    capabilities: dict[str, bool]
    #: Refusal copy only. Never an input to a branch.
    registry_status: str | None
    resolved_at: datetime


@dataclass(frozen=True)
class ResolveDecision:
    """The answer the sign-in path acts on.

    ``code`` is ``None`` exactly when ``admit`` is True. ``source`` says which
    of the four paths produced it and exists for operators reading logs, never
    for a caller to branch on.
    """

    admit: bool
    code: str | None = None
    slug: str | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    registry_status: str | None = None
    source: str = ""


# ── Wiring ──────────────────────────────────────────────────────────────────

def is_wired() -> bool:
    """Whether this deployment has been told how to reach the Console.

    BOTH the address and the key, because either alone is a misconfiguration
    rather than a partial capability — and a box that guessed a host would be
    the fail-open posture CP-0 existed to remove.
    """
    settings = get_settings()
    return bool(
        settings.customer_console_url.strip()
        and settings.customer_console_deployment_key.strip()
    )


# ── Cache (the idiom, not the dict, is shared with access.py) ───────────────

def invalidate(email: str | None = None) -> None:
    """Drop this process's cached resolution for one person, or for everyone.

    The named escape hatch of §6(c), in the shape ``access.invalidate`` already
    has. ⚠️ **Per process, and therefore not a revocation.** The authoritative
    record is the projection row; this only stops one worker answering from a
    minute-old copy. Moving an organization off this deployment revokes when
    the next resolve answer stops listing it (which needs the Console
    reachable) or when an operator runs the placement-move runbook against the
    losing box. Absent both, the bound is ``MAX_STALENESS``.
    """
    if email:
        _cache.pop(email.lower().strip(), None)
    else:
        _cache.clear()


def _cache_get(key: str) -> _Record | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    expires_at, record = hit
    if expires_at < time.monotonic():
        _cache.pop(key, None)
        return None
    return record


def _cache_put(key: str, record: _Record) -> None:
    _cache[key] = (time.monotonic() + _PROCESS_CACHE_TTL_SECONDS, record)


def _age_seconds(record: _Record) -> float:
    """How stale the record is, in seconds, floored at zero.

    Floored because a server whose clock steps backwards would otherwise
    produce a negative age that reads as "fresher than now" — which is the one
    direction a freshness bound must never be wrong in.
    """
    stamped = record.resolved_at
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - stamped).total_seconds())


# ── The HTTP hop ────────────────────────────────────────────────────────────

def _new_http_client() -> Any:
    """The one place an HTTP client for the Console is built.

    A function rather than an inline constructor so the timeout is configured
    once — and so a test can drive the four outcomes through a real
    ``httpx.MockTransport`` instead of stubbing out the request-building code
    that is half of what there is to get wrong here.
    """
    import httpx

    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)


async def _post_resolve(
    email: str, display_name: str
) -> tuple[int, dict[str, Any]]:
    """Present the deployment key and ask about one address.

    ⚠️ The body carries the EMAIL and nothing about which company it belongs
    to. The org is the ANSWER, not the assertion — R11 at its strongest
    available reading, *"the caller makes no tenant claim at all"* — and the
    Console refuses a deployment key that names an ``org_slug`` with 400 rather
    than ignoring it.
    """
    settings = get_settings()
    base = settings.customer_console_url.strip().rstrip("/")
    key = settings.customer_console_deployment_key.strip()

    payload: dict[str, Any] = {"email": email}
    if display_name:
        payload["display_name"] = display_name

    try:
        client = _new_http_client()
        async with client:
            response = await client.post(
                f"{base}/registry/resolve",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except Exception as exc:
        raise _Unreachable(str(exc)[:200]) from exc

    if response.status_code == 200:
        try:
            body = response.json()
        except Exception as exc:
            raise _Unreachable("unreadable 200 body") from exc
        if not isinstance(body, dict):
            raise _Unreachable("a 200 body that is not an object")
        return 200, body
    return response.status_code, {}


# ── The projection (migration 159 + 177) ────────────────────────────────────
#
# ⚠️ `user_identity.email` here is plain TEXT with a UNIQUE INDEX on
# `lower(email)` — NOT the Customer Console's CITEXT. Match on `lower(email)`
# on both sides or a UPN case change mints a second human (R10). And the person
# column is `org_membership.user_id` where the Console calls the same thing
# `user_identity_id`: two names, one thing, named here or it is a silent no-op.

_READ_SQL = """
    SELECT o.slug            AS slug,
           o.registry_status AS registry_status,
           o.registry_capabilities AS registry_capabilities,
           m.resolved_at     AS resolved_at
      FROM user_identity ui
      JOIN org_membership m ON m.user_id = ui.id
      JOIN organization o   ON o.id = m.organization_id
     WHERE lower(ui.email) = :email
       AND m.resolved_at IS NOT NULL
     ORDER BY m.resolved_at DESC
     LIMIT 1
"""

_UPSERT_IDENTITY_SQL = """
    INSERT INTO user_identity (email, display_name)
    VALUES (:email, :name)
    ON CONFLICT (lower(email)) DO UPDATE
       SET display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''),
                                   user_identity.display_name),
           updated_at = now()
    RETURNING id::text AS id
"""

#: §6(k): the join key is the SLUG. The Console's `organization_id` is a UUID in
#: a DIFFERENT database — writing it into `organization.id` or
#: `org_membership.organization_id` would either violate the FK or insert a
#: second organization row and split the tenant in half.
_ORG_BY_SLUG_SQL = "SELECT id::text AS id FROM organization WHERE slug = :slug"

_WRITE_ORG_SQL = """
    UPDATE organization
       SET registry_status = :status,
           registry_capabilities = CAST(:caps AS JSONB),
           updated_at = now()
     WHERE id = CAST(:org AS UUID)
"""

#: A 403 proves exactly one thing and exactly that is recorded. `registry_status`
#: is NOT in this statement: the 403 body is a human sentence, not a field, and
#: parsing a word out of it would couple this box to the Console's message
#: wording (§6(j) row ii).
_WRITE_REFUSAL_SQL = """
    UPDATE organization
       SET registry_capabilities = CAST(:caps AS JSONB),
           updated_at = now()
     WHERE id = CAST(:org AS UUID)
"""

#: `resolved_at` moves and NOTHING else is rewritten — clause 8.
_UPSERT_MEMBERSHIP_SQL = """
    INSERT INTO org_membership (organization_id, user_id, resolved_at)
    VALUES (CAST(:org AS UUID), CAST(:uid AS UUID), now())
    ON CONFLICT (organization_id, user_id) DO UPDATE
       SET resolved_at = now()
"""

_RESOLVED_ORGS_SQL = """
    SELECT o.id::text AS id, o.slug AS slug
      FROM user_identity ui
      JOIN org_membership m ON m.user_id = ui.id
      JOIN organization o   ON o.id = m.organization_id
     WHERE lower(ui.email) = :email
       AND m.resolved_at IS NOT NULL
"""

#: Back to NULL, i.e. back to NEVER OBSERVED — which is not a refusal, it is the
#: absence of a fallback. The next unreachable-Console sign-in then fails closed.
_FORGET_SQL = """
    UPDATE org_membership m
       SET resolved_at = NULL
      FROM user_identity ui
     WHERE m.user_id = ui.id
       AND lower(ui.email) = :email
       AND m.organization_id = CAST(:org AS UUID)
"""

#: The 403 is an observation like any other, so the clock moves with it.
_TOUCH_MEMBERSHIP_SQL = """
    UPDATE org_membership m
       SET resolved_at = now()
      FROM user_identity ui
     WHERE m.user_id = ui.id
       AND lower(ui.email) = :email
       AND m.organization_id = CAST(:org AS UUID)
"""


def _as_capabilities(raw: Any) -> dict[str, bool]:
    """Read the JSONB column back as a plain dict of booleans.

    Tolerates a driver that hands back the raw JSON text: SQLAlchemy's asyncpg
    dialect installs a json codec, but this module must not break if the DSN is
    ever served by one that does not.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {k: bool(v) for k, v in raw.items() if isinstance(v, bool)}


async def _read_record(email: str) -> _Record | None:
    """The last thing the registry said about this person, or None.

    None covers three different situations on purpose — no identity row, no
    membership, and a membership whose ``resolved_at`` is NULL — because all
    three mean the same thing to the caller: *this box has no cached answer to
    fall back on.* In particular migration 159 seeded memberships from
    ``app_user`` with a NULL clock, and those must never read as a resolution
    nobody ever received.
    """
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(text(_READ_SQL), {"email": email})
            ).mappings().first()
    except Exception as exc:
        # Fail CLOSED: no cache is the safe reading of "we could not read the
        # cache", because the only thing a cache does here is ADMIT somebody.
        _log.warning("console_resolve.projection_read_failed",
                     error=str(exc)[:200])
        return None
    if row is None:
        return None
    return _Record(
        slug=row["slug"],
        capabilities=_as_capabilities(row["registry_capabilities"]),
        registry_status=row["registry_status"],
        resolved_at=row["resolved_at"],
    )


async def _record_answer(
    email: str, display_name: str, org: dict[str, Any]
) -> None:
    """Cache one admitted organization, and forget any this box no longer serves.

    ⚠️ **When no local ``organization`` row carries the slug: SKIP the write,
    log ONE structured warning, and let the sign-in proceed on the fresh
    answer.** Not an error, not a refusal, not an insert. Creating that row is
    **provisioning's** act — CP-2a's lifecycle path and WS-29's tenant
    bootstrap — and inventing it here would put tenant creation in a sign-in
    callback, which is both the wrong layer and a write driven by whoever can
    reach the resolve route. The box simply has no fallback cache for that
    person until the row exists, which degrades to the uncached case, i.e.
    fail-closed on the next Console outage — the safe direction.
    """
    slug = org.get("slug") or ""
    capabilities = {
        k: bool(v)
        for k, v in (org.get("capabilities") or {}).items()
        if isinstance(v, bool)
    }
    status = org.get("status")

    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            org_row = (
                await session.execute(text(_ORG_BY_SLUG_SQL), {"slug": slug})
            ).mappings().first()
            if org_row is None:
                _log.warning(
                    "console_resolve.unprovisioned_org",
                    slug=slug,
                    detail=(
                        "the Customer Console placed this person in an "
                        "organization this deployment has no local row for. "
                        "Sign-in proceeds on the fresh answer and NOTHING is "
                        "cached; creating the row is provisioning's act "
                        "(customer_console.md §6(k))."
                    ),
                )
                return

            org_id = org_row["id"]
            identity = (
                await session.execute(
                    text(_UPSERT_IDENTITY_SQL),
                    {"email": email, "name": display_name or ""},
                )
            ).mappings().first()
            await session.execute(
                text(_WRITE_ORG_SQL),
                {"status": status, "caps": json.dumps(capabilities),
                 "org": org_id},
            )
            await session.execute(
                text(_UPSERT_MEMBERSHIP_SQL),
                {"org": org_id, "uid": identity["id"]},
            )
            await _forget_others(session, email, keep={org_id})
            await session.commit()
    except Exception as exc:
        # Never changes the caller's answer: the Console said yes, and a
        # failed cache write is a missing fallback, not a refusal.
        _log.warning("console_resolve.projection_write_failed",
                     error=str(exc)[:200])


async def _forget_others(session: Any, email: str, keep: set[str]) -> None:
    """Clear ``resolved_at`` on every resolved row for *email* outside *keep*.

    §6(c)'s named ``invalidate()`` trigger (a): an answer that no longer lists
    an organization this deployment previously served. Read-then-update rather
    than one statement with an array parameter, because the set is almost
    always empty or a single row and a driver-portable list bind is not worth
    the surface.
    """
    from sqlalchemy import text

    rows = (
        await session.execute(text(_RESOLVED_ORGS_SQL), {"email": email})
    ).mappings().all()
    for row in rows:
        if row["id"] in keep:
            continue
        await session.execute(
            text(_FORGET_SQL), {"email": email, "org": row["id"]}
        )


async def _record_refusal(email: str) -> None:
    """Write ``{sign_in: false}`` on the org row this box last admitted them into.

    Exactly the fact the 403 proved and nothing else. ``write_seats`` and
    ``use_ai`` are **absent**, not False, because the 403 body does not carry
    them and a value the Console never sent is minted information — a missing
    key means *not observed*. ``registry_status`` is untouched for the reason
    in ``_WRITE_REFUSAL_SQL``'s note.

    The 403 names no organization, so the target is the row this box previously
    resolved this person into. With no prior row there is nothing to write and
    nothing is lost: a person this box never admitted has no cached admission
    to fall back on either, so the next unreachable-Console sign-in refuses
    anyway.
    """
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(text(_RESOLVED_ORGS_SQL),
                                      {"email": email})
            ).mappings().all()
            if not rows:
                return
            for row in rows:
                await session.execute(
                    text(_WRITE_REFUSAL_SQL),
                    {"caps": json.dumps({"sign_in": False}),
                     "org": row["id"]},
                )
                await session.execute(
                    text(_TOUCH_MEMBERSHIP_SQL),
                    {"email": email, "org": row["id"]},
                )
            await session.commit()
    except Exception as exc:
        _log.warning("console_resolve.refusal_write_failed",
                     error=str(exc)[:200])


async def _forget_all(email: str) -> None:
    """Drop every cached resolution for this person, row and dict alike."""
    invalidate(email)
    try:
        from sqlalchemy import text

        factory = _get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(text(_RESOLVED_ORGS_SQL),
                                      {"email": email})
            ).mappings().all()
            for row in rows:
                await session.execute(
                    text(_FORGET_SQL), {"email": email, "org": row["id"]}
                )
            await session.commit()
    except Exception as exc:
        _log.warning("console_resolve.forget_failed", error=str(exc)[:200])


# ── The decision ────────────────────────────────────────────────────────────

def _admit(record: _Record, source: str) -> ResolveDecision:
    return ResolveDecision(
        admit=True,
        slug=record.slug,
        capabilities=dict(record.capabilities),
        registry_status=record.registry_status,
        source=source,
    )


async def resolve_for_signin(
    email: str, display_name: str = ""
) -> ResolveDecision:
    """Decide whether a sign-in that is COMPLETING may proceed.

    *Is* completing, not *has* completed: the resolve has to be able to refuse,
    so it runs inside the decision rather than after it.

    Args:
        email: a **provider-verified** address. Never request input — the
            caller establishes it from the IdP profile (R11).
        display_name: a label for the projection. Never an identity and never
            a tenant.
    """
    if not is_wired():
        # Ships dark. No call, no query, no write — byte-identical to the
        # behaviour before CP-2b.
        return ResolveDecision(admit=True, source="unwired")

    key = (email or "").lower().strip()
    if not key:
        # Nothing to ask about. Fail closed rather than admit an anonymous
        # caller into whichever organization the Console guesses.
        return ResolveDecision(
            admit=False, code=CONSOLE_UNAVAILABLE, source="no-subject"
        )

    cached = _cache_get(key)
    if cached is None:
        cached = await _read_record(key)
        if cached is not None:
            _cache_put(key, cached)

    # ── The dead-state rule, and it outranks every freshness bound ──────────
    # A cached answer more restrictive than "admit" applies at once, without
    # re-consulting and without any grace: Console reachable or not, inside the
    # TTL or outside it. Staleness may only ever make the cache MORE
    # restrictive — a record is relaxed by a successful re-consult, never by
    # expiry. The only outcome that writes this is a 403, and the only state
    # that produces a 403 is terminal, so refusing forever is the correct
    # answer rather than a trap; `invalidate()` plus a cleared row is the
    # escape hatch if that ever stops being true.
    if cached is not None and cached.capabilities.get("sign_in") is False:
        return ResolveDecision(
            admit=False,
            code=ACCESS_DENIED,
            slug=cached.slug,
            capabilities=dict(cached.capabilities),
            registry_status=cached.registry_status,
            source="cache-dead",
        )

    settings = get_settings()
    if cached is not None and _age_seconds(cached) <= (
        settings.customer_console_resolve_ttl_seconds
    ):
        return _admit(cached, "cache-fresh")

    try:
        status_code, body = await _post_resolve(key, display_name)
    except _Unreachable as exc:
        ceiling = settings.customer_console_resolve_max_staleness_seconds
        if cached is not None and _age_seconds(cached) <= ceiling:
            _log.warning(
                "console_resolve.degraded_to_cache",
                error=str(exc), age_seconds=int(_age_seconds(cached)),
            )
            return _admit(cached, "cache-stale")
        _log.warning("console_resolve.unreachable", error=str(exc))
        return ResolveDecision(
            admit=False, code=CONSOLE_UNAVAILABLE, source="unreachable"
        )

    # ── Outcome ii — 403 ───────────────────────────────────────────────────
    if status_code == 403:
        await _record_refusal(key)
        invalidate(key)
        return ResolveDecision(
            admit=False, code=ACCESS_DENIED, source="console-refused"
        )

    if status_code != 200:
        # ⚠️ The FIFTH outcome, which the ticket's four-row table does not
        # name: 409 at the seat cap, plus any other status this box cannot
        # read. It is a refusal — the Console did not admit this person — and
        # it caches NOTHING, because a status we do not understand proves
        # nothing worth recording. `AccessDenied` is the honest code of the
        # three that exist: at the cap the person genuinely holds no seat and
        # *"ask your admin"* is exactly the remedy. A third code is
        # deliberately not minted here; the ticket names two and adding a
        # third is a decision for the owner, not for a build.
        _log.warning("console_resolve.refused", status_code=status_code)
        return ResolveDecision(
            admit=False, code=ACCESS_DENIED, source="console-error"
        )

    organizations = body.get("organizations") or []

    # ── Outcome iii — more than one visible organization ───────────────────
    # Cache nothing and do NOT forget: the answer still lists organizations
    # this deployment serves, so §6(c)'s invalidate trigger does not fire.
    # Stated honestly rather than glossed — a person who has SINCE become
    # multi-org keeps an older single-org admitted row, so on a later Console
    # outage they are admitted into the one organization this box last resolved
    # them into, bounded by MAX_STALENESS. That is deliberate: it admits them
    # somewhere they demonstrably belong, never somewhere they do not, and
    # poisoning the cache on a refusal that is about AMBIGUITY would lock a
    # paying user out on an outage.
    if len(organizations) > 1:
        return ResolveDecision(
            admit=False,
            code=WORKSPACE_CHOOSER_REQUIRED,
            source="console-multi-org",
        )

    # ── Outcome iv — zero ──────────────────────────────────────────────────
    # The genuine not-authorized case: no membership visible to this
    # deployment at all, and §6(c)'s named invalidate trigger.
    if not organizations:
        await _forget_all(key)
        return ResolveDecision(
            admit=False, code=ACCESS_DENIED, source="console-empty"
        )

    # ── Outcome i — exactly one ────────────────────────────────────────────
    org = organizations[0]
    await _record_answer(key, display_name, org)
    invalidate(key)
    capabilities = {
        k: bool(v)
        for k, v in (org.get("capabilities") or {}).items()
        if isinstance(v, bool)
    }
    return ResolveDecision(
        admit=True,
        slug=org.get("slug"),
        capabilities=capabilities,
        registry_status=org.get("status"),
        source="console",
    )
