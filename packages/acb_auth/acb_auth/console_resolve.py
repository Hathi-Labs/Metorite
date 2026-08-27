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

## The ONE SEAT-ALLOCATING caller, and why it is not ``resolve_access``

:func:`resolve_for_signin` — the function that allocates a Core seat — is called
from ``apps/services/gateway/gateway/routes/signin.py`` and **nothing else**. The
proposed default was to wire it behind ``access.resolve_access``; that was
refused, and the code is why — ``resolve_access`` has six production call sites
and one of them (``routes/rooms.py``) fans it over every participant of a room.
A seat-allocating cross-service call there would fire one Console request **and
one seat allocation** per participant per room load, which is precisely the
farmable cap clause 11 exists to prevent. ``resolve_access``,
``_with_resolved_access`` and ``_ACCESS_SQL`` are untouched by this module.

The MODULE has four importers, each reaching a different, non-seat-allocating
function, and each a **session-email-only** route: ``routes/signin.py``
(:func:`resolve_for_signin`), ``routes/signup.py``
(:func:`provision_org_on_console`), ``routes/seats.py`` (the two seat-admin
writes) and — since CP-2f, 2026-08-24 — ``routes/admin/members.py``
(:func:`invite_member_on_console`). A FIFTH is the drift. Fence:
``tests/unit/test_console_dependency_boundary.py``, whose allow-list is the
statement of that rule.

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
* **"Unreachable" is a behaviour, not a socket.** A 5xx, a ``401`` (this box's
  own credential is wrong — never a fact about the person), a ``408`` or a
  ``429`` all mean *no answer was produced*, so they take the path below rather
  than the refusal path. Reading them as refusals showed every user of every
  tenant *"your account isn't authorized"* during one nginx hiccup, while the
  same outage over a closed port degraded gracefully (finding P1-1).
* Console **unreachable** → a cached person proceeds up to
  ``CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS`` and is refused past it. A
  cache with no ceiling is not a cache, it is a second identity system that
  never expires — and the day it matters is the day an account is suspended for
  non-payment.
* No cached answer and no Console → **refuse**, with the service-unavailable
  copy, never "access denied": the person has done nothing wrong, and a
  wrong-looking denial generates a support ticket and a password reset that fix
  nothing (D33.1).
* A cached ``sign_in: false`` outranks the TTL and refuses **immediately**,
  Console reachable or not — but it stops at ``MAX_STALENESS`` like every other
  record. Unbounded it was an **unrecoverable lockout of every member of the
  organization** (finding P1-2): the 403 writes a person fact onto an ORG row,
  the read serves it to everyone with a resolution, and the only thing that
  could clear it was a 200 the short-circuit itself prevented. Past the ceiling
  the record is re-consulted; nothing is relaxed by that, because an
  unreachable Console then refuses on the uncached path anyway. Staleness may
  only ever make the cache MORE restrictive.
* Freshness is measured with :func:`_within`, so a record stamped in the
  **future** (two clocks: the database's ``now()`` vs this process's) is stale,
  never maximally fresh (finding P2).

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

## Ships dark — this half of it

With ``CUSTOMER_CONSOLE_URL`` or ``CUSTOMER_CONSOLE_DEPLOYMENT_KEY`` unset,
:func:`resolve_for_signin` admits without a call, a query or a write.

⚠️ That is a statement about **this module**, not about sign-in end to end, and
the difference was finding F1 (2026-08-18). The browser tier decides
separately whether to ask at all: the BFF's ``signIn`` callback
(``workbench/control_plane/src/auth.ts``) gates on its own Next-side flag,
``CUSTOMER_CONSOLE_RESOLVE_ENABLED``, default unset = OFF. So "half-configured
here" no longer means "the product still behaves as it did before CP-2b" by
implication — it means this function admits, while whether the hop is attempted
at all is the flag's answer. Wiring a live deployment, and flipping that flag on
one, are both 🔴 OWNER-GATE (§8 gate 7); declaring the settings is not.

⚠️ **The fail-open branch below stops at this module's edge: the ROUTE refuses.**
*(Repair of finding F5, 2026-08-18 — a P0 one hop down from F1.)* The two
switches live in different containers with different env files, so "Next flag on,
gateway env empty" is an ordinary provisioning slip rather than an exotic one —
and passing this function's ``admit=True, source="unwired"`` through would admit
every sign-in with no seat allocated and no log line. ``routes/signin.py``
therefore checks :func:`is_wired` **itself** and answers ``ConsoleUnavailable``
before calling this at all: reaching that route means somebody declared the box
wired. This branch stays as it is for the module's own contract (and for a
caller that is not the route); it is not the product's ship-dark guarantee, and
reading it as one is what F5 was. Fence:
``tests/unit/test_signin_resolve_route.py``.
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
    "ConsoleMemberWriteUnavailable",
    "ConsoleProvisionUnavailable",
    "ConsoleRouterUnavailable",
    "ConsoleSeatWriteUnavailable",
    "ReconcileSummary",
    "ResolveDecision",
    "assign_seat_on_console",
    "chat_completion_on_console",
    "invalidate",
    "invite_member_on_console",
    "is_wired",
    "provision_org_on_console",
    "reconcile",
    "release_seat_on_console",
    "resolve_for_signin",
    "router_is_wired",
    "seat_overview_on_console",
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


class ConsoleProvisionUnavailable(Exception):
    """The Customer Console could not mirror a signup provision.

    Transient by construction — the box is not wired, the network failed, or
    the Console answered anything other than a readable 200 (a 5xx, a 401 on
    this box's own key, a 408/429, or a 4xx we should not treat as a verdict).
    A FRESH tenant-born slug cannot be *permanently* refused by the Console:
    slice 1's create-only guard (``store.org_owned_by_other``) rejects only an
    org already owned by a DIFFERENT identity, and this slug's owner is the same
    session email the tenant plane just made owner — so any non-200 here is an
    outage, never a real "slug taken". CP-2c's signup route maps this to
    ``ConsoleUnavailable`` and a resubmit converges, because both planes are
    idempotent on the slug.
    """


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

    ``signup_eligible`` is the ONE load-bearing signal a caller MAY branch on
    (WS-31 CP-2c). It is ``True`` for exactly one outcome — a 200 with **zero**
    organizations, the genuinely org-less person — and ``False`` for every
    other, admissions and refusals alike. It exists so the self-serve-signup
    "limbo" branch can funnel only the zero-org case: a SUSPENDED / non-paying
    org (``console-refused``, ``cache-dead``) and a seat-capped one
    (``console-error``) all carry ``AccessDenied`` too, and re-keying the funnel
    on the code would readmit a non-paying customer into an org-less session,
    regaining access the registry suspension is meant to deny. ``source`` stays
    log-only and MUST NOT be branched on; this is the field that is.
    """

    admit: bool
    code: str | None = None
    slug: str | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    registry_status: str | None = None
    source: str = ""
    #: True for the zero-org outcome ALONE (outcome iv, ``console-empty``); set
    #: directly on that return, never inferred from ``source``. Default False,
    #: so an undefined value fails closed — no funnel.
    signup_eligible: bool = False


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
    """How stale the record is, in seconds. **May be NEGATIVE.**

    ⚠️ It used to be floored at zero, with a comment claiming the floor was the
    fail-CLOSED choice. It was the fail-OPEN one, and the comment described the
    bug as the fix (review finding P2, 2026-08-18): floored to ``0.0``, a
    record stamped in the FUTURE read as *maximally fresh* and was served from
    the cache without anybody being asked — precisely the direction a freshness
    bound must never be wrong in. Callers use :func:`_within`, which reads a
    negative age as **stale**.

    ⚠️ Two clocks, honestly: ``resolved_at`` is the DATABASE's ``now()`` and
    the comparison is against this app process's, so a skew between them is a
    real state rather than a hypothetical one — which is why the sign matters
    at all.
    """
    stamped = record.resolved_at
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    return (datetime.now(UTC) - stamped).total_seconds()


#: How far into the future a ``resolved_at`` may sit and still be read as
#: "now" rather than as a broken clock.
#:
#: ⚠️ **A tolerance is required because two clocks exist**, not because of any
#: one measured offset. ``resolved_at`` is stamped by the DATABASE's ``now()``
#: while the freshness comparison uses the APP process's ``datetime.now(UTC)``
#: — different machines in the ordinary deployment (app container vs database
#: container), so some skew in either direction is a standing condition, and
#: observed magnitudes vary run to run (samples on the local scratch container
#: ranged from ~0.01s to ~0.4s across sessions — do not treat either figure as
#: a property of the system). With a hard ``0.0`` floor a DB clock running
#: ahead makes freshly-written records read "born in the future" = stale,
#: silently disabling the read-through cache for as long as the skew holds.
#:
#: 60 seconds is 1/15th of the default TTL and 1/1440th of the ceiling, so what
#: a skew can buy is negligible against both; a stamp further out than this is
#: not jitter, it is a clock that stepped, and such a record is treated as
#: STALE (re-consult; refuse if the Console cannot be reached).
_CLOCK_SKEW_TOLERANCE_SECONDS = 60.0


def _within(record: _Record, bound_seconds: float) -> bool:
    """Whether *record* is inside *bound_seconds* of now, failing closed on skew.

    An age below ``-_CLOCK_SKEW_TOLERANCE_SECONDS`` (``resolved_at`` materially
    in the FUTURE) is **outside every bound** — the same treatment as
    past-ceiling: re-consult, and if the Console cannot be reached, refuse
    rather than admit on a record no clock in the system agrees about. Fence:
    ``test_a_record_stamped_in_the_future_is_not_treated_as_fresh``.

    ⚠️ The residual, stated rather than implied: a clock that steps BACKWARDS
    shifts every age down together, so it lengthens every bound by however far
    it stepped. Nothing in a process without a trusted monotonic anchor to the
    row's clock can fix that; what this stops is the unbounded version, where
    any future stamp whatsoever read as maximally fresh.
    """
    return -_CLOCK_SKEW_TOLERANCE_SECONDS <= _age_seconds(record) <= bound_seconds


# ── The HTTP hop ────────────────────────────────────────────────────────────

def _new_http_client(timeout: float = _HTTP_TIMEOUT_SECONDS) -> Any:
    """The one place an HTTP client for the Console is built.

    A function rather than an inline constructor so the timeout is configured
    once — and so a test can drive the four outcomes through a real
    ``httpx.MockTransport`` instead of stubbing out the request-building code
    that is half of what there is to get wrong here.

    ⚠️ The timeout is a PARAMETER as of CP-11 slice 2, and the default is
    unchanged. The Router hop needs a far longer one than a sign-in resolve
    does (see ``_ROUTER_TIMEOUT_SECONDS``), and the alternative was a second
    client factory — which is the thing this module's own note forbids.
    """
    import httpx

    return httpx.AsyncClient(timeout=timeout)


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

    # ── A status that proves we got no ANSWER is an outage, not a verdict ────
    #
    # Repair of review finding P1-1 (2026-08-18). Everything that was not
    # 200/403 used to end at `AccessDenied`, so a Console 502 behind nginx —
    # or a rotated `cc_depl_` key answering 401 — told every user of every
    # tenant *"your account isn't authorized"*, the exact wrong-looking denial
    # D33.1 forbids, while the SAME outage over a closed port degraded to the
    # cache. Two spellings of one event, two opposite behaviours.
    #
    # The line drawn: **5xx** (the Console or something in front of it is
    # broken), **401** (this box's own credential is wrong — never a fact about
    # the person), **408** and **429** (no answer was produced, try later) are
    # transport failures and take the unreachable path, which is bounded by
    # MAX_STALENESS and refuses when nothing is cached — still fail-closed,
    # just honest about why. **403 and 409 are ANSWERS** and keep their meaning.
    if response.status_code >= 500 or response.status_code in (401, 408, 429):
        raise _Unreachable(f"HTTP {response.status_code}")

    if response.status_code == 200:
        try:
            body = response.json()
        except Exception as exc:
            raise _Unreachable("unreadable 200 body") from exc
        if not isinstance(body, dict):
            raise _Unreachable("a 200 body that is not an object")
        return 200, body
    return response.status_code, {}


# ── The Console-provision client (CP-2c slice 2) ─────────────────────────────
#
# ⚠️ **This module is the ONE Console httpx client and the sole reader of
# `CUSTOMER_CONSOLE_URL` / `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` (`is_wired()`).** A
# second httpx client for the Console anywhere — e.g. one built inside the
# signup route — is root `CLAUDE.md` §5's defect by name (a second way to do an
# existing thing). CP-2c's route calls `provision_org_on_console` and holds no
# client, no URL and no key of its own; WS-30 SC-2a's seat writes
# (`assign_seat_on_console` / `release_seat_on_console`, below) are here for the
# same reason, and the gateway seat route likewise holds no client, URL or key.


async def _post_provision(
    slug: str,
    name: str,
    owner_email: str,
    gstin: str | None,
    billing_state: str | None,
) -> dict[str, Any]:
    """Present the deployment key to the ``/orgs/provision`` DEPLOYMENT-KEY arm.

    ⚠️ **The body carries NO ``deployment_label``** — the key IS the deployment,
    and the Console refuses a deployment key that names one with a 400 (R11, the
    same rule the resolve arm applies to ``org_slug``). ``gstin`` and
    ``billing_state`` thread straight to the Console org row; the Console side
    already accepts both (``main.py:169-170``), so no Console change is needed.
    """
    settings = get_settings()
    base = settings.customer_console_url.strip().rstrip("/")
    key = settings.customer_console_deployment_key.strip()

    payload: dict[str, Any] = {
        "slug": slug,
        "name": name,
        "owner_email": owner_email,
    }
    if gstin:
        payload["gstin"] = gstin
    if billing_state:
        payload["billing_state"] = billing_state

    try:
        client = _new_http_client()
        async with client:
            response = await client.post(
                f"{base}/orgs/provision",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except Exception as exc:
        raise ConsoleProvisionUnavailable(str(exc)[:200]) from exc

    if response.status_code == 200:
        try:
            body = response.json()
        except Exception as exc:
            raise ConsoleProvisionUnavailable("unreadable 200 body") from exc
        if not isinstance(body, dict):
            raise ConsoleProvisionUnavailable("a 200 body that is not an object")
        return body

    # Anything that is not a readable 200 is TRANSIENT for a fresh tenant-born
    # slug: the create-only guard cannot permanently refuse the same owner, so a
    # 5xx / 401 / 408 / 429 — or any other non-200 — is an outage to retry, never
    # a real "slug taken". Raising the same type for all of them is what keeps
    # the route from ever answering a false `SlugTaken` off the Console plane.
    raise ConsoleProvisionUnavailable(f"HTTP {response.status_code}")


async def provision_org_on_console(
    slug: str,
    name: str,
    owner_email: str,
    *,
    gstin: str | None = None,
    billing_state: str | None = None,
) -> dict[str, Any]:
    """Mirror a signup provision onto the Customer Console. Idempotent on slug.

    Step 2 of CP-2c's two-plane signup orchestration: the tenant plane is
    provisioned FIRST (the hard one-email-one-org guard), then this mirrors the
    org onto the Console so the registry can meter and cap it. Returns the
    Console's ``{organization_id, slug}`` on success.

    Raises:
        ConsoleProvisionUnavailable: the box is not wired, or the Console did
            not answer a readable 200. These are the ONLY failures a fresh
            tenant-born slug can hit; the route maps them to
            ``ConsoleUnavailable`` and a resubmit converges (both planes
            idempotent on the slug).
    """
    if not is_wired():
        # Ship-dark: an unwired box has no Console to mirror onto. Transient by
        # the same logic — the caller has already committed the tenant plane, so
        # the org works dark, and a wired resubmit catches the Console up.
        raise ConsoleProvisionUnavailable("unwired")
    return await _post_provision(slug, name, owner_email, gstin, billing_state)


# ── The seat-admin client (WS-30 SC-2a / customer_console.md §6 item (h), CP-2h) ─
#
# ⚠️ **Still the ONE Console httpx client** (the note above): these three functions
# are the gateway's path to the Console's deployment-key `seat_admin` door
# (`POST /registry/seats`, `/registry/seats/release` and — since CP-2h slice 1,
# D-SEAT-4 — the READ `/registry/seats/overview`), and they live HERE —
# beside `_post_provision`/`_post_resolve`, reusing `_new_http_client` /
# `is_wired` / the settings reads — because a second Console client anywhere is
# root `CLAUDE.md` §5's defect by name. The gateway route
# (`gateway/routes/seats.py`) holds no client, no URL and no key; it supplies the
# acting `actor_email` (the authenticated SESSION email, R11) and relays what the
# Console answers.
#
# ⚠️ **The body carries `actor_email` and NO `org_slug`.** The org is the ANSWER,
# derived Console-side from `deployment_visible_orgs(deployment_id, actor_email)`
# — the same R11 shape `_post_resolve`/`_post_provision` apply (the caller makes
# no tenant claim), and the Console 400s a deployment key that names an
# `org_slug`. The "admin, not any member" decision also stays Console-side
# (`_seat_admin_for_deployment` 403s a non-admin actor); this client relays that
# refusal rather than pre-judging it.


class ConsoleSeatWriteUnavailable(Exception):
    """The Customer Console produced no seat-door answer we could relay.

    Transport-only, the line `_post_resolve` draws: the box is not wired, the
    network failed, or the Console answered with a status proving no ANSWER was
    produced (a 5xx, a 401 on this box's own deployment key, a 408 or a 429). A
    genuine verdict — 200, or the 400/403/404/409 the ``seat_admin`` door itself
    issues — is NOT this: it is returned as ``(status_code, body)`` for the
    gateway route to relay, so a member's "not an admin" (403) or "at the cap"
    (409) reaches the surface as itself, never as an outage.

    ⚠️ **The name says WRITE and it now also covers the CP-2h READ**
    (``seat_overview_on_console``), deliberately: there is ONE `seat_admin`
    transport with ONE no-answer policy, and a second exception type would be a
    second vocabulary for the identical fact — the caller's only question is
    "verdict or outage", and it is answered the same way for all three arms.
    Renaming it would touch the gateway route and its fence for no behaviour.
    """


async def _post_seat_call(
    endpoint: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Present the deployment key to a ``seat_admin`` arm and read the answer.

    The ONE transport for all three arms (assign · release · overview): one
    bearer, one timeout, one verdict-vs-outage policy. A 5xx / 401 / 408 / 429
    proves no answer was produced and raises
    :class:`ConsoleSeatWriteUnavailable`; every other status is a verdict and is
    returned for the route to relay.

    ⚠️ Callers build the payload; this function never adds a field. In particular
    it never adds an ``org_slug`` — the org is derived Console-side from
    ``deployment_visible_orgs(deployment_id, actor_email)`` (R11), and the Console
    400s a deployment key that names one.
    """
    settings = get_settings()
    base = settings.customer_console_url.strip().rstrip("/")
    key = settings.customer_console_deployment_key.strip()

    try:
        client = _new_http_client()
        async with client:
            response = await client.post(
                f"{base}{endpoint}",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except Exception as exc:
        raise ConsoleSeatWriteUnavailable(str(exc)[:200]) from exc

    # A status that proves we got no ANSWER is an outage, not a verdict — the
    # same line `_post_resolve` draws (finding P1-1): 5xx (the Console is broken),
    # 401 (this box's own deployment key is wrong — never a fact about the
    # member), 408/429 (no answer produced). Everything else (200, and the
    # 400/403/404/409 the door issues) is an answer the caller may act on.
    if response.status_code >= 500 or response.status_code in (401, 408, 429):
        raise ConsoleSeatWriteUnavailable(f"HTTP {response.status_code}")

    try:
        body = response.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return response.status_code, body


def _seat_payload(
    *,
    actor_email: str,
    member_email: str,
    plan_slug: str,
    source: str | None,
) -> dict[str, Any]:
    """The seat WRITE body: the target, the plan, the acting admin — no org (R11)."""
    payload: dict[str, Any] = {
        "member_email": member_email,
        "plan_slug": plan_slug,
        "actor_email": actor_email,
    }
    if source is not None:
        payload["source"] = source
    return payload


async def assign_seat_on_console(
    *,
    actor_email: str,
    member_email: str,
    plan_slug: str,
    source: str,
) -> tuple[int, dict[str, Any]]:
    """Assign a seat via the Console's deployment-key ``seat_admin`` door.

    Returns the Console's ``(status_code, body)`` verbatim for the gateway route
    to relay. Raises :class:`ConsoleSeatWriteUnavailable` when the box is unwired
    or the Console produced no answer (see :func:`_post_seat_call`).
    """
    if not is_wired():
        # Ship-dark: an unwired box has no Console to write to. The gateway route
        # also guards this itself (F5) and refuses before calling; this is the
        # same guarantee one layer down.
        raise ConsoleSeatWriteUnavailable("unwired")
    return await _post_seat_call(
        "/registry/seats",
        _seat_payload(
            actor_email=actor_email,
            member_email=member_email,
            plan_slug=plan_slug,
            source=source,
        ),
    )


async def release_seat_on_console(
    *,
    actor_email: str,
    member_email: str,
    plan_slug: str,
) -> tuple[int, dict[str, Any]]:
    """Release a seat via the Console's deployment-key ``seat_admin`` door.

    The release arm takes no ``source`` (freeing a seat needs no billing
    category). Returns the Console's ``(status_code, body)`` verbatim; raises
    :class:`ConsoleSeatWriteUnavailable` on an unwired box or a no-answer status.
    """
    if not is_wired():
        raise ConsoleSeatWriteUnavailable("unwired")
    return await _post_seat_call(
        "/registry/seats/release",
        _seat_payload(
            actor_email=actor_email,
            member_email=member_email,
            plan_slug=plan_slug,
            source=None,
        ),
    )


async def seat_overview_on_console(
    *, actor_email: str
) -> tuple[int, dict[str, Any]]:
    """Read the acting admin's seat grid + roster (CP-2h slice 1, **D-SEAT-4**).

    The READ arm of the same ``seat_admin`` door the two writes above use, and
    the reason it exists: the customer Seats tab used to compose its picture from
    the Console's ORGANIZATION-key reads (``GET /me/seats`` + ``GET /me/members``)
    through a per-org ``CUSTOMER_CONSOLE_ORG_KEY``. On a SHARED multi-tenant box
    no single org key is correct, so the surface failed closed to "not configured
    for this deployment". The deployment key is per-BOX, so this arm works for
    every tenant on it.

    ⚠️ **Non-allocating, exactly like its two siblings.** It drives the
    admin-gated ``seat_admin`` door and never touches ``resolve_for_signin``, so
    it allocates no seat and the cap cannot be farmed through it — which is the
    condition on `console_resolve` having importers at all
    (``test_console_dependency_boundary.py``).

    The body carries ``actor_email`` alone: the org is DERIVED Console-side from
    ``deployment_visible_orgs(deployment_id, actor_email)`` (R11), so the caller
    makes no tenant claim, and the "admin, not any member" decision stays
    Console-side too. Returns the Console's ``(status_code, body)`` verbatim —
    a 403 (not an admin) or a 409 (member of more than one org on this box) is a
    verdict to relay, never an outage. Raises
    :class:`ConsoleSeatWriteUnavailable` on an unwired box or a no-answer status.
    """
    if not is_wired():
        raise ConsoleSeatWriteUnavailable("unwired")
    return await _post_seat_call(
        "/registry/seats/overview", {"actor_email": actor_email}
    )


# ── The MEMBER-write client (CP-2f · customer_console.md, D50.2) ─────────────
#
# ⚠️ **Still the ONE Console httpx client** (the note above the seat writes):
# this function is the gateway's path to the Console's deployment-key
# `member_admin` door (`POST /registry/members`), and it lives HERE — beside
# `_post_provision` / `_post_resolve` / `_post_seat_write`, reusing
# `_new_http_client` / `is_wired` / the settings reads — because a second Console
# client anywhere is root `CLAUDE.md` §5's defect by name. `routes/admin/
# members.py` holds no client, no URL and no key; it supplies the acting
# `actor_email` (the AUTHENTICATED ADMIN's session email, R11) and ignores the
# answer, because the tenant-plane write it mirrors has already committed.
#
# ⚠️ **The body carries `member_email` + `actor_email` and NO `org_slug`.** The
# org is the ANSWER, derived Console-side from
# `deployment_visible_orgs(deployment_id, actor_email)` — the same R11 shape the
# three siblings apply — and the Console 400s a deployment key that names an
# `org_slug`.
#
# ⚠️ **It carries no ROLE either, and that is a decision, not an omission.** The
# tenant's role slugs and the registry's `{owner,admin,member}` are two
# vocabularies (D12); mapping one onto the other on this wire would mint the
# second grant vocabulary. The Console writes its column default.


class ConsoleMemberWriteUnavailable(Exception):
    """The Customer Console produced no member-write answer we could read.

    Transport-only, the line `_post_resolve` draws: the box is not wired, the
    network failed, or the Console answered with a status proving no ANSWER was
    produced (a 5xx, a 401 on this box's own deployment key, a 408 or a 429). A
    genuine verdict — 200, or the 400/403/409 the ``member_admin`` door itself
    issues — is NOT this: it is returned as ``(status_code, body)``.

    ⚠️ Unlike the seat write, **the caller does not relay this to a browser**.
    The invite's authoritative tenant-plane write has already committed, so every
    outcome here — outage and verdict alike — is best-effort telemetry. The type
    exists so the two are still distinguishable in a log line.
    """


async def invite_member_on_console(
    *,
    actor_email: str,
    member_email: str,
    display_name: str = "",
) -> tuple[int, dict[str, Any]]:
    """Mirror an invited member onto the Customer Console (CP-2f, D50.2).

    Writes an ``org_membership`` row with ``status='invited'`` for the
    organization the acting admin belongs to on THIS deployment, so the invited
    colleague is visible to ``GET /me/members``, is seat-assignable before their
    first sign-in, and — the load-bearing one — resolves to their employer's org
    rather than into the self-serve signup funnel.

    Returns the Console's ``(status_code, body)``.

    Raises:
        ConsoleMemberWriteUnavailable: the box is not wired, or the Console
            produced no answer. Never raised for a verdict.
    """
    if not is_wired():
        # Ship-dark: an unwired box has no Console to mirror onto. The caller
        # treats this exactly like any other failure — best-effort, post-commit,
        # and it never changes the invite's answer.
        raise ConsoleMemberWriteUnavailable("unwired")

    settings = get_settings()
    base = settings.customer_console_url.strip().rstrip("/")
    key = settings.customer_console_deployment_key.strip()

    payload: dict[str, Any] = {
        "member_email": member_email,
        "actor_email": actor_email,
    }
    if display_name:
        payload["display_name"] = display_name

    try:
        client = _new_http_client()
        async with client:
            response = await client.post(
                f"{base}/registry/members",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except Exception as exc:
        raise ConsoleMemberWriteUnavailable(str(exc)[:200]) from exc

    # The same line `_post_resolve` and `_post_seat_write` draw (finding P1-1):
    # 5xx / 401 / 408 / 429 prove no answer was produced. Everything else is an
    # answer.
    if response.status_code >= 500 or response.status_code in (401, 408, 429):
        raise ConsoleMemberWriteUnavailable(f"HTTP {response.status_code}")

    try:
        body = response.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return response.status_code, body


# ── The signup Console-mirror reconciler (CP-2e slice) ───────────────────────
#
# ⚠️ **The closer for CP-2c done-when 5's named dark window.** A TRANSIENT Console
# failure during signup leaves an org live on the TENANT plane but never mirrored
# to the Console, and step 0a's `membership_of → AlreadyMember` short-circuits any
# literal route resubmit — so the org is un-metered until reconciled and nothing
# in the signup route re-drives it. This is that path: an out-of-band SWEEP over
# tenant orgs whose Console mirror is missing, re-driving the SAME create-only-
# guarded arm through the ONE existing Console client, `provision_org_on_console`.
#
# It lives HERE, not in a new module, because a second Console client anywhere is
# root `CLAUDE.md` §5's defect by name and the reconciler must re-drive exactly
# the arm the signup route does. It recovers `owner_email` with the SAME owner
# join `access._ORG_OWNER_SQL` uses (via the public `access.org_owner_of` seam) —
# no second owner grammar — and every read + the marker write go through
# `get_session_factory`, the unbound idiom this module already uses ("the tenant
# is the ANSWER"): no new engine site (R5(b)), no tenant/identity from request
# input (R11 — the sweep takes nothing from a caller, it re-drives what the tenant
# plane already persisted).
#
# ⚠️ **Ships dark, fail-closed.** With the box unwired it is a logged no-op that
# touches neither the Console nor the tenant DB (the early guard below) — so the
# `scripts/` CLI that triggers it is inert until §8 gates 2 + 8 (Console deployed,
# a `{provision}` key wired) are the owner's acts. `provision_org_on_console`
# raising `ConsoleProvisionUnavailable("unwired")` is the same guarantee one layer
# down. Fence: `tests/unit/test_signup_reconciler.py`.

#: Tenant orgs not yet mirrored to the Console. `first_party = false` EXCLUDES the
#: operator's own bootstrap org (migration 157 backfilled `default` to true): it is
#: not a metered self-serve customer, and mirroring it into the customer billing
#: registry would route the first-party path through the customer Console — the
#: bypass D36.2/D36.3 forbids. Every signup-born org is `first_party = false` by
#: the 157 default, so this catches exactly them.
_SELECT_UNMIRRORED_ORGS_SQL = """
    SELECT slug          AS slug,
           display_name  AS display_name,
           gstin         AS gstin,
           billing_state AS billing_state
      FROM organization
     WHERE console_mirrored_at IS NULL
       AND first_party = false
     ORDER BY slug
"""


@dataclass(frozen=True)
class ReconcileSummary:
    """Counts from one reconcile pass, for the operator reading the CLI output.

    ``selected`` is the orgs the sweep predicate matched; ``mirrored`` those the
    Console accepted (its row now exists and the marker was stamped);
    ``unavailable`` those a transient Console failure (or an unwired box, or the
    create-only refusal of a hijack attempt) left for a later pass, marker still
    NULL; ``skipped_no_owner`` the crash-resume shape (an org with no owner yet)
    the sweep cannot reconstruct a Console call for.
    """

    selected: int = 0
    mirrored: int = 0
    unavailable: int = 0
    skipped_no_owner: int = 0


async def reconcile() -> ReconcileSummary:
    """Re-drive the Console mirror for every tenant org that is missing one.

    One idempotent pass: select the unmirrored non-first-party orgs, reconstruct
    each ``(slug, display_name, owner_email, gstin, billing_state)`` from the
    tenant plane, call :func:`provision_org_on_console` (idempotent-on-slug,
    create-only guarded on the Console side), and on SUCCESS stamp
    ``console_mirrored_at`` so a later pass skips it. A transient failure leaves
    the marker NULL and the org for the next pass; the reconciler writes NO tenant
    org/owner, so it can neither fork the tenant plane nor move ownership.

    Cadence is the operator's — this is a single pass, not a scheduler. Returns a
    :class:`ReconcileSummary` of counts.
    """
    # Ship-dark, fail-closed: an unwired box has no Console to mirror onto, so the
    # whole pass is a logged no-op that touches neither the Console nor the tenant
    # DB. `provision_org_on_console` would raise `ConsoleProvisionUnavailable
    # ("unwired")` per org anyway; this early guard makes the CLI inert BEFORE any
    # query, which is what lets it run harmlessly on a box that predates the
    # Console deployment.
    if not is_wired():
        _log.info("console_resolve.reconcile_unwired_noop")
        return ReconcileSummary()

    from sqlalchemy import text

    factory = _get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(text(_SELECT_UNMIRRORED_ORGS_SQL))
        ).mappings().all()

    # Imported at call time (not module scope) so importing this module drags in
    # neither `acb_auth.access` nor `acb_common.provisioning`, and so there is no
    # import-time cycle with `access` (which may import this module).
    from acb_common.provisioning import mark_console_mirrored

    from acb_auth.access import org_owner_of

    summary = {"selected": len(rows), "mirrored": 0,
               "unavailable": 0, "skipped_no_owner": 0}

    for row in rows:
        slug = row["slug"]
        # owner_email via the SAME join `access._ORG_OWNER_SQL` uses — reused, not
        # re-derived. `org_owner_of` degrades to None on a transient read error,
        # which is the safe direction here: skip and let a later pass retry.
        owner_email = await org_owner_of(slug)
        if not owner_email:
            summary["skipped_no_owner"] += 1
            _log.warning("console_resolve.reconcile_no_owner", slug=slug)
            continue

        try:
            await provision_org_on_console(
                slug,
                row["display_name"],
                owner_email,
                gstin=row["gstin"],
                billing_state=row["billing_state"],
            )
        except ConsoleProvisionUnavailable as exc:
            # Transient (unwired mid-sweep, network, 5xx) OR the create-only guard
            # refusing a slug owned by a DIFFERENT identity on the Console — a
            # forced hijack. Either way: marker stays NULL, no second org/owner is
            # written, the pass continues.
            summary["unavailable"] += 1
            _log.warning(
                "console_resolve.reconcile_unavailable",
                slug=slug, error=str(exc)[:200],
            )
            continue

        summary["mirrored"] += 1
        if not await mark_console_mirrored(slug):
            # The Console has the row but the marker write did not stamp it (a
            # best-effort miss, or a concurrent stamp). Harmless: the mirror is
            # idempotent-on-slug, so a later pass re-affirms and re-marks.
            _log.warning("console_resolve.reconcile_marker_missed", slug=slug)

    _log.info("console_resolve.reconcile_pass", **summary)
    return ReconcileSummary(**summary)


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
    FORGET the organizations this box previously resolved them into, log ONE
    structured warning, and let the sign-in proceed on the fresh answer.** Not
    an error, not a refusal, not an insert. Creating that row is
    **provisioning's** act — CP-2a's lifecycle path and WS-29's tenant
    bootstrap — and inventing it here would put tenant creation in a sign-in
    callback, which is both the wrong layer and a write driven by whoever can
    reach the resolve route. The box then has no fallback cache for that person
    until the row exists, which degrades to the uncached case, i.e. fail-closed
    on the next Console outage — the safe direction.

    ⚠️ **The forget is the half that was missing, and it made this docstring
    false** (review finding P1-3, 2026-08-18). The branch used to `return`
    before ``_forget_others``, so a person the Console had MOVED to an
    organization placed here but not yet bootstrapped kept a live
    ``resolved_at`` on the organization they LEFT — and was admitted back into
    it for up to ``MAX_STALENESS`` on the next outage, into an org the registry
    no longer places them in. "Nothing to cache" and "keep the last thing we
    cached" are different answers; only the first degrades in the direction
    §6(k) argues for. Fence:
    ``test_a_move_to_an_unprovisioned_org_clears_the_OLD_admission``.
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
                # Keep NOTHING: this answer places the person somewhere this
                # box cannot record, so every organization it previously
                # resolved them into is now the WRONG one to fall back on.
                # `keep=set()` is §6(c)'s trigger (a) with an empty keep set.
                await _forget_others(session, email, keep=set())
                await session.commit()
                _log.warning(
                    "console_resolve.unprovisioned_org",
                    slug=slug,
                    detail=(
                        "the Customer Console placed this person in an "
                        "organization this deployment has no local row for. "
                        "Sign-in proceeds on the fresh answer, NOTHING is "
                        "cached and any earlier resolution is forgotten; "
                        "creating the row is provisioning's act "
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

    settings = get_settings()
    ceiling = settings.customer_console_resolve_max_staleness_seconds

    # ── The dead-state rule: it outranks the TTL, and stops at the CEILING ──
    # A cached answer more restrictive than "admit" applies at once, without
    # re-consulting and without any grace: Console reachable or not, inside the
    # TTL or outside it. Staleness may only ever make the cache MORE
    # restrictive — a record is relaxed by a successful re-consult, never by
    # expiry.
    #
    # ⚠️ **Bounded by MAX_STALENESS, 2026-08-18 (review finding P1-2), and the
    # unbounded version was an unrecoverable lockout.** `_record_refusal`
    # writes a PERSON fact (`{"sign_in": false}`) onto an ORG row — the org this
    # box last resolved that person into, possibly an unrelated live one — and
    # `_READ_SQL` then hands it to EVERY member with a non-NULL `resolved_at`.
    # Unbounded, that refused every member of that organization forever, at any
    # freshness, and the only thing that could clear it was a successful 200
    # this very branch prevented from ever being requested. Recovery was a
    # manual UPDATE on the tenant database. (`capabilities_of` returning
    # `STATES["deleted"]` for any UNRECOGNISED status string is the amplifier: a
    # typo Console-side 403s a paying customer.)
    #
    # Past the ceiling the record is re-consulted like any other, which is NOT
    # a relaxation: with the Console unreachable a past-ceiling record buys
    # nothing on the fallback path either, so it still refuses; a genuinely
    # dead organization 403s again and re-arms the record. The whole change is
    # that a WRONG record heals within 24h instead of never.
    if (
        cached is not None
        and cached.capabilities.get("sign_in") is False
        and _within(cached, ceiling)
    ):
        return ResolveDecision(
            admit=False,
            code=ACCESS_DENIED,
            slug=cached.slug,
            capabilities=dict(cached.capabilities),
            registry_status=cached.registry_status,
            source="cache-dead",
        )

    if cached is not None and _within(
        cached, settings.customer_console_resolve_ttl_seconds
    ):
        return _admit(cached, "cache-fresh")

    try:
        status_code, body = await _post_resolve(key, display_name)
    except _Unreachable as exc:
        if cached is not None and _within(cached, ceiling):
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
        # name: 409 at the seat cap, plus any other status in which the CONSOLE
        # ANSWERED and the answer was not an admission (400, 404, 422 — a
        # request this box built wrong). It is a refusal — the Console did not
        # admit this person — and it caches NOTHING, because an answer we do
        # not understand proves nothing worth recording. `AccessDenied` is the
        # honest code of the three that exist: at the cap the person genuinely
        # holds no seat and *"ask your admin"* is exactly the remedy. A third
        # code is deliberately not minted here; the ticket names two and adding
        # a third is a decision for the owner, not for a build.
        #
        # ⚠️ This branch no longer covers "any status this box cannot READ"
        # (P1-1, 2026-08-18): 5xx, 401, 408 and 429 never reach it, because
        # `_post_resolve` raises `_Unreachable` for them. A transport failure
        # is an outage and takes the degrade-bounded path; only a decision
        # arrives here.
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
        # ⚠️ The ONLY ``signup_eligible=True`` return in this module. Set here
        # DIRECTLY — never derived from ``source`` — because this is the sole
        # genuinely org-less outcome: the Console answered 200 and listed no
        # organization at all, so there is no tenant to be suspended or capped.
        # The refusals that also carry ``AccessDenied`` (``console-refused`` /
        # ``cache-dead`` for a suspended org, ``console-error`` at the seat cap)
        # leave this default False, so the self-serve funnel cannot readmit a
        # non-paying customer.
        return ResolveDecision(
            admit=False,
            code=ACCESS_DENIED,
            source="console-empty",
            signup_eligible=True,
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


# ── The AI Router client (CP-11 slice 2) ────────────────────────────────────
#
# ⚠️ **Still the ONE Console httpx client** (the note above `_post_provision`).
# This is the tenant box's path to the Console's AI Router
# (`POST /v1/chat/completions`) and it lives HERE for the reason the seat client
# does: a second Console client anywhere is root `CLAUDE.md` §5's defect by name.
#
# ⚠️ **A DIFFERENT CREDENTIAL from every arm above it.** Resolve, provision and
# seat all present `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` (`cc_depl_`). This arm
# presents `CUSTOMER_CONSOLE_ORG_KEY` (`cc_live_`), because the Router's door is
# `KeyCaller` and the ORGANIZATION is a property of that credential.
# `customer_console.md` §6B.2 tabulates the pair — they are the most confusable
# thing in the system. So `is_wired()` is the wrong question here, and
# `router_is_wired()` is the right one.
#
# ⚠️ **SHIPS DARK — nothing calls this.** `v1_compat.py` still serves
# `/v1/chat/completions` from litellm directly. Moving it behind
# `ROUTER_SERVING_ENABLED` is CP-11 slice 3, and that slice owns §6B.5's four
# hazards (streaming, latency, `_ensure_keys_loaded`, attribution).

#: A completion is not a sign-in. `_HTTP_TIMEOUT_SECONDS` is 5s, chosen so an
#: unreachable Console degrades inside a person's patience. Applied to a model
#: call it would abort essentially every real completion. This is long enough
#: for a slow model and still bounded, so a wedged Router cannot pin a gateway
#: worker for ever.
_ROUTER_TIMEOUT_SECONDS = 120.0

#: The Router's own "I cannot do that" for a streaming request. It is a VERDICT
#: and not an outage, even though it is a 5xx: CP-4b is unbuilt, the Console
#: refuses EXPLICITLY rather than silently de-streaming, and slice 3 has to see
#: that answer to decide what to do about it (§6B.5 hazard 1).
_ROUTER_NOT_IMPLEMENTED = 501


class ConsoleRouterUnavailable(Exception):
    """The Router produced no answer we could relay.

    Transport-only, the same line :func:`_post_seat_call` draws: the box is
    unwired, the network failed, or the Console answered with a status proving
    no ANSWER was produced (5xx, 401, 408, 429).

    A genuine verdict is NOT this. It comes back as ``(status_code, body)``,
    and four of them carry meaning the caller must not flatten into "outage":

    * **402** — the balance gate refused (CP-6). The tenant has to see "out of
      credits" as itself.
    * **400** — an unknown tier. A misconfigured agent must be visible, not
      quietly billed (D32.7).
    * **403** — the per-run circuit breaker.
    * **501** — streaming, which CP-4b has not built.
    """


def router_is_wired() -> bool:
    """Whether this box can reach the Console's AI Router.

    BOTH the address and the ORG key, for :func:`is_wired`'s reason: either one
    alone is a misconfiguration rather than a partial capability.

    ⚠️ Deliberately SEPARATE from :func:`is_wired`. A box can be wired for
    sign-in resolution and not for AI, and one predicate for both would arm one
    capability the moment somebody configured the other.
    """
    settings = get_settings()
    return bool(
        settings.customer_console_url.strip()
        and settings.customer_console_org_key.strip()
    )


def _attribution_headers(
    *,
    member: str | None,
    agent: str | None,
    module_slug: str | None,
    run_id: str | None,
) -> dict[str, str]:
    """The ``X-CC-*`` headers that let a ``usage_event`` answer *who burned it*.

    ⚠️ §6B.5 hazard 4: an unattributed usage row can never become a per-member
    cap (CP-7) or a usage statement (SC-4f), so these are not a later nicety.

    Every one is OPTIONAL on the wire and the Console binds an absent header to
    ``None``. An EMPTY value is therefore omitted rather than sent, because
    sending it would record the empty string as a member — which reads as an
    attribution nobody made rather than as the absence of one.
    """
    pairs = (
        ("X-CC-Member", member),
        ("X-CC-Agent", agent),
        ("X-CC-Module", module_slug),
        ("X-CC-Run", run_id),
    )
    return {name: value for name, value in pairs if value}


async def chat_completion_on_console(
    payload: dict[str, Any],
    *,
    member: str | None = None,
    agent: str | None = None,
    module_slug: str | None = None,
    run_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Serve one completion through the Console Router, on OUR provider account.

    Returns the Console's ``(status_code, body)`` for the caller to relay, and
    raises :class:`ConsoleRouterUnavailable` when no answer was produced.

    ⚠️ **``payload["model"]`` must name a TIER, not a model.** The Console 400s
    a bare model id rather than coercing it to a default (D32.7). That refusal
    is a VERDICT this function returns, never an exception it raises.

    ⚠️ **THERE IS NO RETRY, and the omission is deliberate.** Every other arm in
    this module tolerates a retry because a resolve is idempotent. A completion
    is not: the Console meters and CHARGES on the way through, so blindly
    retrying a request that actually succeeded bills the customer twice for one
    answer. An outage fails closed and hands the decision to the caller.
    """
    if not router_is_wired():
        # Ship-dark: an unwired box has no Router to call. Slice 3's flag also
        # guards this, and this is the same guarantee one layer down.
        raise ConsoleRouterUnavailable("unwired")

    settings = get_settings()
    base = settings.customer_console_url.strip().rstrip("/")
    key = settings.customer_console_org_key.strip()

    headers = {"Authorization": f"Bearer {key}"}
    headers.update(
        _attribution_headers(
            member=member, agent=agent, module_slug=module_slug, run_id=run_id
        )
    )

    try:
        client = _new_http_client(_ROUTER_TIMEOUT_SECONDS)
        async with client:
            response = await client.post(
                f"{base}/v1/chat/completions", headers=headers, json=payload
            )
    except Exception as exc:
        raise ConsoleRouterUnavailable(str(exc)[:200]) from exc

    # The verdict-vs-outage line, with ONE carve-out from the seat arm's rule.
    # 501 is a 5xx and is nonetheless an ANSWER: the Router saying CP-4b is
    # unbuilt so streaming is refused. Folding it into the outage branch would
    # report an unreachable Console to somebody whose real problem is that they
    # asked for a stream.
    status = response.status_code
    if status != _ROUTER_NOT_IMPLEMENTED and (
        status >= 500 or status in (401, 408, 429)
    ):
        raise ConsoleRouterUnavailable(f"HTTP {status}")

    try:
        body = response.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return status, body
