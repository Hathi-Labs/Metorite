"""The email-OTP adapter's server half — three BFF-internal routes (CP-2d).

Spec: ``project-docs/specs/customer_console.md`` §CP-2d clauses 6, 10, 11, 12 ·
``user_management_contract.md`` R11 · owner answers B2 (this residual, accepted
and named) and B3 (the numbers) of 2026-08-23.

The Auth.js email provider needs a **database adapter**. Ruling H-A says that
adapter may not open a database connection in the Next tier (R5: one engine, one
seam, and the control plane has never had a driver), so the adapter is a thin
object in ``workbench/control_plane/src/lib/emailOtpAdapter.ts`` that calls these
three routes. All the deciding — canonical form, both rate limits, single use,
expiry — is in :mod:`acb_auth.email_otp`, one transaction per act.

Three routes, one per act, because they are three different authorisations of
the same row and collapsing them would hide that:

* ``POST /signin/otp/send``    — may a code be sent? (the hourly budget **and**
                                 the per-send slot claim), and records the send
                                 **with the hash of the code it is about to
                                 mail**. Called BEFORE the mail goes.
* ``POST /signin/otp/token``   — persist the token hash for that send (and never
                                 overwrite one the claim already wrote).
* ``POST /signin/otp/consume`` — spend one verification attempt and, if the hash
                                 matches a live token, consume it.

## The posture, mirrored from ``routes/signin.py`` and ``routes/signup.py``

* **BFF-internal bearer, and NOT in ``PUBLIC_ROUTES``.** Authentication is by
  construction (``FastAPI(dependencies=[require_authenticated(…)])``); adding a
  ``PUBLIC_ROUTES`` entry to "make it reachable" is forbidden by name (root
  ``AGENTS.md`` constraint 10). The BFF reaches these with the internal bearer it
  already holds, through the ONE ``headersActingAs()`` seam — never a second copy
  of the token.
* **The identifier is the AUTHENTICATED context's, never the body (R11).** The
  address being verified rides ``X-User-Email``, exactly as ``signin.py`` takes
  the signing-in address from there and refuses a body ``email``. A body naming
  ``email`` / ``identifier`` / ``org`` is **400, never ignored** — an ignored
  field is a caller who believes it worked.
* 🔓 **Named accepted risk, inherited and stated rather than absorbed (owner
  answer B2).** ``require_authenticated`` accepts the internal Bearer plus an
  ``X-User-Email`` of the caller's choosing and trusts it by design (*"whoever
  holds the internal token can already assert any X-User-Email, so a narrower set
  would be theatre"*, ``deps.py``). So: **a holder of the INTERNAL TOKEN ALONE
  can drive these routes for arbitrary addresses.** What that buys is
  deliberately bounded, and each bound is a property of this file rather than a
  hope:

    - **single use** — a consumed token cannot be replayed (``consumed_at`` is in
      the consume statement's ``WHERE``);
    - **short expiry** — the provider's ``maxAge``, which this deployment leaves
      at ``emailOtp.ts``'s ``DEFAULT_OTP_MAX_AGE_S`` = **10 minutes**;
    - **the rate limit applies to EVERY caller, internal bearer included** —
      there is no privileged path past it, which is the reason it is here and not
      in the browser tier.

  The residual is therefore *up to three emails an hour to a chosen address, and
  a ten-minute denial of email-OTP sign-in for an address whose attempts have
  been burned*. It allocates no seat and creates no organization, so it is
  narrower than either route it is modelled on. **The gate that NARROWS it is
  §4.3's ``GATEWAY_INTERNAL_TOKEN`` / ``LITELLM_MASTER_KEY`` split**
  (``work_plan.md`` §6, §8 gate 1); today one secret opens both doors. Accepted,
  named, and not addressed here.

  ⚠️ **The DENIAL half of that residual does not need the internal token at
  all**, and saying otherwise understated it (repair of review finding F5,
  2026-08-23). ``proxy.ts`` passes ``/api/auth/**`` through **unauthenticated**
  — it must, or nobody could sign in — so anyone on the internet can drive
  ``GET /api/auth/callback/resend?token=…&email=<victim>`` with junk tokens. Each
  one reaches :func:`otp_consume` through the BFF's own bearer, spends one of the
  victim's five attempts, and the fifth **invalidates their outstanding code**.
  Five requests therefore deny email-OTP sign-in to a chosen address for ten
  minutes, anonymously. That is accepted (the alternative — not charging
  attempts for unauthenticated callers — is a limiter with a documented bypass,
  and OAuth sign-in is unaffected), and per-IP throttling belongs at the reverse
  proxy, which is where the deferred item sits. What is NOT accepted is the mail
  half: that is bounded here, and by the per-send slot claim in
  :mod:`acb_auth.email_otp`.

  ⚠️ **A Resend failure spends the slot.** The send budget is reserved before
  the mail is handed to the transport, so a Resend outage burns one of the three
  hourly slots per attempt and three failures lock the address out for an hour.
  That is the fail-CLOSED direction and it is deliberate: releasing the slot on a
  transport error would make "make the transport fail" the bypass.

  ⚠️ **Every call here drives ``deps._with_resolved_access`` with a
  caller-chosen address**, because that is what ``require_authenticated`` does
  with an ``X-User-Email``. For an address that belongs to no member the resolver
  degrades to no-access and logs a swallowed warning — so a novel address per
  request writes a warning line per request on a phase-4-promoted catalog. Log
  volume, not an access decision; named here so it is not rediscovered as a leak.

## Always mounted, and dark by consequence rather than by a flag of its own

There is deliberately no ``EMAIL_OTP_*`` flag read in this file. The feature's
switch is in the browser tier (``EMAIL_OTP_ENABLED`` + ``RESEND_API_KEY``, gated
through ``isEmailOtpProviderReady``): with it off, no provider is registered, no
adapter is passed, and nothing ever calls these routes. A second flag here would
be a second thing to get out of step — the CP-2b finding F1 in miniature.

Fence: ``tests/unit/test_email_otp_token.py`` (R8, real Postgres, driving these
routes' own store functions and the shape rules below).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from acb_auth import UserContext, get_current_user
from acb_auth.email_otp import (
    OtpRateLimited,
    canonical_identifier,
    consume_token,
    record_token,
    reserve_send,
)
from acb_common import get_logger
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

_log = get_logger("gateway.email_otp")

router = APIRouter(prefix="/signin/otp", tags=["signin"])

#: R11: the identity/tenant claims a caller must not assert in the body. The
#: address is the authenticated context's; naming one here is a 400 on SHAPE,
#: before any value is read, so naming a real address and a nonsense one are one
#: refusal. Mirrors ``signup.py::_FORBIDDEN_BODY_KEYS``.
_FORBIDDEN_BODY_KEYS = frozenset({"email", "identifier", "org", "organization"})

#: A body that is not an object at all.
INVALID_BODY = "InvalidBody"

#: The caller presented no address to verify. Fail closed — an OTP act with no
#: identifier has no rate-limit budget to charge and no row to find.
NO_IDENTIFIER = "NoIdentifier"

#: The submitted code matched no live token (wrong, already used, or expired).
#: Deliberately ONE code for all three: telling them apart tells a guesser
#: whether the address has a live code, and ``@auth/core`` renders one page for
#: all of them anyway.
INVALID_TOKEN = "InvalidToken"


def _bad_request(code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"code": code, "detail": detail})


def _too_many(exc: OtpRateLimited) -> JSONResponse:
    """The send/verify refusal, as a 429 the adapter turns into an Auth.js error.

    A 429 rather than a 200-with-a-code (the shape ``signin.py`` uses): this is
    not an outcome the sign-in page renders with friendly copy, it is a refusal
    of the request itself, and ``@auth/core`` has no vocabulary for a partial
    adapter answer. The ``detail`` never names the address.

    Carries three codes, and the third is not a budget refusal:
    ``SendRateLimited`` (three mails this hour), ``VerifyRateLimited`` (five
    guesses in ten minutes) and ``SendDuplicate`` — a second send claiming a slot
    that is already taken, which is how N simultaneous sends yield ONE email.
    All three must reach the adapter as a non-2xx, because the adapter's throw is
    what stops :func:`otp_send`'s caller from mailing anyway.
    """
    return JSONResponse(status_code=429, content={"code": exc.code, "detail": exc.detail})


async def _body(request: Request) -> dict[str, Any] | None:
    """The JSON body as a dict, or ``None`` when it is not an object.

    A body is required on every route here (each carries at least ``expires`` or
    ``token``), so an unparseable one is a shape violation rather than an empty
    default — the opposite of ``signup.py``, whose body is entirely optional.
    """
    try:
        raw = await request.json()
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _identity(user: UserContext | None) -> str:
    """The address being verified, from the authenticated context ALONE (R11)."""
    return canonical_identifier((user.email or "") if user else "")


def _expires(raw: dict[str, Any]) -> datetime | None:
    """Parse the send's ``expires`` instant, or ``None`` when it is unusable.

    ⚠️ It is a REQUIRED field and not a convenience: ``(identifier, expires)`` is
    what makes the two concurrent legs of one send agree about which send they
    are (``acb_auth.email_otp``, rule 3). A missing or malformed value would make
    each leg a send of its own and the hourly budget wrong by a factor of two.

    ``fromisoformat`` handles the ``Z`` suffix from Python 3.11, which is what
    ``Date.prototype.toISOString`` produces.
    """
    value = raw.get("expires")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _shape_refusal(
    raw: dict[str, Any] | None, identifier: str
) -> JSONResponse | None:
    """The two shape gates every route here applies, in one place."""
    if raw is None:
        return _bad_request(INVALID_BODY, "a JSON object body is required")
    named = _FORBIDDEN_BODY_KEYS & set(raw)
    if named:
        _log.warning("email_otp.body_claims_identity", keys=sorted(named))
        return _bad_request(
            INVALID_BODY,
            "the address is the authenticated session's; a body "
            "email/identifier/org is refused (R11)",
        )
    if not identifier:
        return _bad_request(
            NO_IDENTIFIER, "no address to verify was presented"
        )
    return None


@router.post("/send")
async def otp_send(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Permit and record one code send, or refuse with 429.

    Called from the provider's ``sendVerificationRequest`` **before** the mail is
    handed to Resend. That ordering is the whole point: a limiter that refuses
    the token but lets the email out has not limited anything a person receiving
    the mail would notice.

    ⚠️ **``token`` is REQUIRED here too, and it is the hash of the code this send
    is about to mail** (repair of review finding P2, round 2, 2026-08-23). The
    claim writes it, so a same-``expires`` burst's surviving row holds the SLOT
    WINNER's code rather than whichever loser's token leg landed last. It is the
    same ``createHash(`${token}${secret}`)`` value ``/signin/otp/token`` carries —
    never the six digits — recomputed browser-side by ``emailOtp.ts::otpWireHash``
    because ``@auth/core`` hands the send leg the raw code and the hash to the
    other leg.
    """
    raw = await _body(request)
    identifier = _identity(user)
    refusal = _shape_refusal(raw, identifier)
    if refusal is not None:
        return refusal
    assert raw is not None  # narrowed by _shape_refusal
    expires = _expires(raw)
    token = str(raw.get("token") or "").strip()
    if expires is None or not token:
        return _bad_request(
            INVALID_BODY,
            "`token` and an ISO-8601 `expires` instant are required",
        )
    try:
        return await reserve_send(identifier, token, expires)
    except OtpRateLimited as exc:
        return _too_many(exc)


@router.post("/token")
async def otp_token(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Persist the verification-token hash for a permitted send.

    ⚠️ ``token`` here is ``@auth/core``'s SHA-256 of the code salted with
    ``AUTH_SECRET``, computed in ``send-token.js`` before any adapter sees it —
    the six digits never reach this process, let alone the database.
    """
    raw = await _body(request)
    identifier = _identity(user)
    refusal = _shape_refusal(raw, identifier)
    if refusal is not None:
        return refusal
    assert raw is not None  # narrowed by _shape_refusal
    expires = _expires(raw)
    token = str(raw.get("token") or "").strip()
    if expires is None or not token:
        return _bad_request(
            INVALID_BODY, "`token` and an ISO-8601 `expires` are required"
        )
    try:
        return await record_token(identifier, token, expires)
    except OtpRateLimited as exc:
        return _too_many(exc)


@router.post("/consume")
async def otp_consume(
    request: Request,
    user: Annotated[UserContext, Depends(get_current_user)] = None,  # type: ignore[assignment]
) -> Any:
    """Spend one verification attempt; consume the token if the hash matches.

    404 for a miss, 429 for a spent budget, 200 with the record for a hit.
    ⚠️ The attempt is charged **before** the hash is compared, and the budget is
    checked before that — so the sixth attempt in a window is refused **even when
    the code is correct**, which is the property the fence asserts by name.
    """
    raw = await _body(request)
    identifier = _identity(user)
    refusal = _shape_refusal(raw, identifier)
    if refusal is not None:
        return refusal
    assert raw is not None  # narrowed by _shape_refusal
    token = str(raw.get("token") or "").strip()
    if not token:
        return _bad_request(INVALID_BODY, "`token` is required")
    try:
        found = await consume_token(identifier, token)
    except OtpRateLimited as exc:
        return _too_many(exc)
    if found is None:
        return JSONResponse(
            status_code=404,
            content={
                "code": INVALID_TOKEN,
                # One message for wrong / used / expired: distinguishing them
                # tells a guesser whether a live code exists for this address.
                "detail": "that code is not valid for this address",
            },
        )
    return found
