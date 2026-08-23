"""Email one-time-code verification tokens — the tenant-plane store (CP-2d).

Spec: ``project-docs/specs/customer_console.md`` §CP-2d clauses 7, 8, 10, 11,
12 · owner answers **B1** (token home = the TENANT plane, RLS-EXEMPT, prefixed
name) and **B3** (the rate-limit numbers) of 2026-08-23 · migration
``infra/postgres/186_auth_email_otp_token.sql``.

**What this module is.** The server half of the Auth.js email-OTP adapter. The
adapter itself is a thin object in the Next tier (``workbench/control_plane/
src/lib/emailOtpAdapter.ts``) with **no database driver of its own** (R5, ruling
H-A); it calls ``gateway/routes/email_otp.py``, which calls exactly these three
functions. Everything that decides — canonical form, both rate limits,
single-use, expiry — lives HERE, in one transaction per act, so there is no
second opinion anywhere in the chain.

**Why it is in ``acb_auth``.** This package owns authentication and the identity
chain; a verification token is an identity artifact. It sits beside
``console_resolve`` (the other pre-session act) and follows
``acb_common.provisioning``'s acquisition idiom for exactly the same reason.

## The three rules that are easy to get wrong

1. ⚠️ **Acquire through ``get_session_factory()`` — UNBOUND, deliberately.**
   Not ``tenant_session()``: this row is minted before any session exists, for
   an address that may belong to no organization at all (the zero-org
   ``console-empty`` case IS the self-serve-signup funnel), so there is no
   tenant to bind and ``TenantUnbound`` would be the only possible outcome. Not
   ``get_db()`` either: that entry point's site count is a ratchet that may only
   go down (``tests/unit/test_db_engine_seam.py``). The table is RLS-EXEMPT, so
   an unbound session is the correct and only way to reach it — proved on a
   phase-4-promoted catalog as a non-privileged role by
   ``tests/unit/test_h3_rls_promotion_rehearsal.py``.

2. ⚠️ **The counters and the reads that gate them share ONE transaction, under a
   per-identifier advisory lock.** ``pg_advisory_xact_lock`` is the idiom the
   tree already uses for check-then-write races (``lock_seat_capacity``,
   ``lock_org_activation``); without it two concurrent verifications both read
   "4 attempts so far" and both proceed, and the fifth-attempt lockout is a
   suggestion. It is per identifier, so two people signing in do not serialise.

3. ⚠️ **The send gate EXCLUDES the send currently in flight, and that exclusion
   is NOT what bounds emails.** ``@auth/core``'s
   ``lib/actions/signin/send-token.js`` starts ``sendVerificationRequest`` and
   ``createVerificationToken`` **concurrently** in one ``Promise.all``, and only
   the second is handed the token hash. The mail must be gated *before* it is
   sent (a limiter that refuses the token but not the email limits nothing), so
   both legs call in and both must reach the SAME answer whichever lands first.
   They identify their shared send by ``(identifier, expires)`` — ``@auth/core``
   computes ``expires`` once and hands the same instant to each — and the count
   excludes it. Count the row and the limit is racily 2-or-3 instead of 3.

   ⚠️ **What bounds emails is** :data:`_CLAIM_SEND_SQL` (repair of review
   finding P1b, 2026-08-23). ``expires`` is ``Date.now() + maxAge``, so two
   *different* sends started in the same millisecond carry the *same* instant:
   they excluded one another from the count, both passed a budget of three, both
   upserted the one row — and two emails went out for one slot. Repeat per
   millisecond and the budget is decorative. So the send leg now **claims** the
   slot in one statement (``ON CONFLICT … DO UPDATE … WHERE reserved_at IS
   NULL``) and is refused with :data:`SEND_DUPLICATE` when the claim finds it
   already taken. Both legs still agree about which send they are; what changed
   is that exactly one of N simultaneous claimants may mail.

## What is NOT here

No user, account or session rows. The session strategy is pinned to ``jwt`` and
identity is the tenant plane's (``app_user`` / ``user_identity``, reached through
CP-2b's resolve); an Auth.js identity table set would be the second identity
store root ``CLAUDE.md`` §5 forbids. The Next-side adapter derives its user
object and persists nothing.

⚠️ **The stored token is a SHA-256 of the code salted with ``AUTH_SECRET``**
(``send-token.js`` hashes before it ever reaches an adapter), never the six
digits. So a reader of this table cannot sign in — and the rate limit below is
therefore the WHOLE defence against guessing a 6-digit code, which is why it is
here and not on a page.

Fences: ``tests/unit/test_email_otp_token.py`` (R8, real Postgres — round trip,
single use, expiry, the 6th attempt refused even with the correct code, the 429
window, the 4th send refused, the **same-millisecond send collision** refused
without a second mail, a **duplicate 6-digit hash** consumed as one row rather
than raising, and the migration's collision guard) and
``tests/unit/test_h3_rls_promotion_rehearsal.py::
TestTheEmailOtpTokenIsReachableUnbound``. R7 name: ``email-otp-token-limits``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_auth.email_otp")

# ── The owner's numbers (B3, 2026-08-23) ────────────────────────────────────
#
# Stated as constants rather than inline literals so the route, the tests and
# this module cannot disagree about them, and so changing one is one edit that
# a reviewer sees.

#: Verification attempts allowed per identifier per :data:`VERIFY_WINDOW_S`.
#: The Nth+1 attempt is refused **even when the code is correct** — that is the
#: property the fence asserts by name.
VERIFY_MAX_ATTEMPTS = 5

#: The verification window, in seconds (10 minutes). Anchored on
#: ``last_attempt_at``, not on ``created_at``: the limit is about guessing, and
#: a guesser who waits for the token to age out has not earned a fresh budget.
VERIFY_WINDOW_S = 10 * 60

#: Code sends allowed per identifier per :data:`SEND_WINDOW_S`.
SEND_MAX_PER_WINDOW = 3

#: The send window, in seconds (1 hour).
SEND_WINDOW_S = 60 * 60


# ── The refusal vocabulary ──────────────────────────────────────────────────


class OtpRateLimited(Exception):
    """A limit in :data:`VERIFY_MAX_ATTEMPTS` / :data:`SEND_MAX_PER_WINDOW` bit.

    Carries a ``code`` the route turns into a 429 body. Distinct from "the code
    was wrong", which is not an exception at all — it is a ``None`` return, and
    conflating the two would tell a mistyping person to come back in ten
    minutes.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


#: Too many code sends for this address in the last hour.
SEND_RATE_LIMITED = "SendRateLimited"

#: Too many verification attempts for this address in the last ten minutes. The
#: outstanding token is invalidated as well, so waiting out the window does not
#: revive a code somebody has been guessing at.
VERIFY_RATE_LIMITED = "VerifyRateLimited"

#: This send's slot was already claimed — a second ``sendVerificationRequest``
#: for the same ``(identifier, expires)``. Distinct from
#: :data:`SEND_RATE_LIMITED` because it is not a budget refusal: the budget was
#: fine, the send was a duplicate, and the whole point of answering is that the
#: adapter throws and **no second email goes out** (review finding P1b). Same
#: exception class, because both mean "this send is refused and nothing is
#: mailed" — one refusal vocabulary, two reasons.
SEND_DUPLICATE = "SendDuplicate"


def canonical_identifier(identifier: str | None) -> str:
    """The one canonical form of an OTP identifier: ``lower(btrim(...))``.

    Applied on the way in AND returned on the way out, so ``Alice@x.com`` and
    ``alice@x.com`` share one rate-limit budget rather than two. ``@auth/core``'s
    own ``defaultNormalizer`` already lowercases and trims, so this agrees with
    it by construction rather than by coincidence — and the callback's
    ``invite.identifier !== paramIdentifier`` comparison therefore still matches.
    """
    return (identifier or "").strip().lower()


# ── SQL. Every parameter CAST explicitly, for provisioning.py's reason: an
# untyped NULL bound through asyncpg leaves the server inferring a type for a
# parameter that has no value.

#: Serialise everything about one identifier. Transaction-scoped, so it is
#: released on commit or rollback and can never be leaked by a pooled
#: connection. Keyed on a namespaced string so it cannot collide with the seat
#: or activation locks, which use the same mechanism.
_LOCK_SQL = """
SELECT pg_advisory_xact_lock(hashtext('acb_auth.email_otp:' || CAST(:identifier AS TEXT)))
"""

#: **Mails** charged to this identifier inside the window, EXCLUDING the send in
#: flight. ``reserved_at IS NOT NULL`` is the whole point: one claimed row is one
#: email, so this counts exactly what the budget is about. A row the token leg
#: created on its own has authorised no mail and does not spend the budget.
_CLAIMED_SEND_COUNT_SQL = """
SELECT count(*) AS n
  FROM auth_email_otp_token
 WHERE identifier = CAST(:identifier AS TEXT)
   AND created_at > now() - make_interval(secs => CAST(:window_s AS DOUBLE PRECISION))
   AND expires <> CAST(:expires AS TIMESTAMPTZ)
   AND reserved_at IS NOT NULL
"""

#: ROWS for this identifier inside the window, excluding the one in flight — the
#: token leg's gate. It counts unclaimed rows too, so a caller driving
#: ``/signin/otp/token`` alone (the internal-bearer residual clause 12 names)
#: cannot mint unbounded rows: clause 12 promises the limit applies to EVERY
#: caller, and a gate that only counted mails would exempt exactly the caller who
#: skips the mail.
_ROW_COUNT_SQL = """
SELECT count(*) AS n
  FROM auth_email_otp_token
 WHERE identifier = CAST(:identifier AS TEXT)
   AND created_at > now() - make_interval(secs => CAST(:window_s AS DOUBLE PRECISION))
   AND expires <> CAST(:expires AS TIMESTAMPTZ)
"""

#: **Claim this send's slot, or answer nothing.** The statement that makes the
#: hourly budget real rather than advisory (review finding P1b).
#:
#: ⚠️ The ``WHERE reserved_at IS NULL`` on the ``DO UPDATE`` is load-bearing and
#: is not an optimisation: when the row is already claimed the conflict arm
#: matches nothing, the statement affects zero rows, ``RETURNING`` yields
#: nothing, and :func:`reserve_send` turns that into :data:`SEND_DUPLICATE`. That
#: is what makes N simultaneous sends — which share an ``expires`` millisecond
#: and therefore share this row — produce exactly ONE email.
#:
#: It still converges with the token leg: a row that leg created carries
#: ``reserved_at IS NULL``, so the send leg claims it rather than being refused,
#: and ``token`` is left alone.
_CLAIM_SEND_SQL = """
INSERT INTO auth_email_otp_token (identifier, token, expires, reserved_at)
VALUES (CAST(:identifier AS TEXT),
        NULL,
        CAST(:expires AS TIMESTAMPTZ),
        now())
ON CONFLICT ON CONSTRAINT auth_email_otp_token_send_key DO UPDATE
   SET reserved_at = now()
 WHERE auth_email_otp_token.reserved_at IS NULL
RETURNING identifier, token, expires
"""

#: The TOKEN leg's write: attach the hash to this send's row, creating it when
#: the send leg has not landed yet (the two are concurrent and genuinely race).
#: ``COALESCE`` keeps an already-written hash, and ``reserved_at`` is never
#: touched here — claiming a send slot is the send leg's act alone.
_UPSERT_SQL = """
INSERT INTO auth_email_otp_token (identifier, token, expires)
VALUES (CAST(:identifier AS TEXT),
        CAST(:token      AS TEXT),
        CAST(:expires    AS TIMESTAMPTZ))
ON CONFLICT ON CONSTRAINT auth_email_otp_token_send_key DO UPDATE
   SET token = COALESCE(EXCLUDED.token, auth_email_otp_token.token)
RETURNING identifier, token, expires
"""

#: Attempts charged to this identifier inside the verification window.
_ATTEMPT_COUNT_SQL = """
SELECT COALESCE(SUM(attempts), 0) AS n
  FROM auth_email_otp_token
 WHERE identifier = CAST(:identifier AS TEXT)
   AND last_attempt_at > now() - make_interval(secs => CAST(:window_s AS DOUBLE PRECISION))
"""

#: Charge one attempt to the LIVE token — the most recent outstanding row.
#:
#: ⚠️ It has to land on a row the guesser did not name, because a WRONG code
#: matches no row at all: charging "the row whose token equals the submitted
#: one" would count only successes, which is a counter that never fires. And it
#: is ONE row rather than every outstanding row, or a single guess against an
#: address with two live codes would cost two attempts.
_CHARGE_ATTEMPT_SQL = """
UPDATE auth_email_otp_token
   SET attempts = attempts + 1,
       last_attempt_at = now()
 WHERE id = (
        SELECT id FROM auth_email_otp_token
         WHERE identifier = CAST(:identifier AS TEXT)
           AND consumed_at IS NULL
           AND invalidated_at IS NULL
         ORDER BY created_at DESC
         LIMIT 1)
RETURNING attempts
"""

#: Consume: single-use and expiry are in the WHERE, so both are enforced by the
#: statement rather than by a branch that can be reordered away.
#:
#: ⚠️ **Exactly ONE row, chosen deterministically** (repair of review finding P2,
#: 2026-08-23). ``(identifier, token)`` is not unique and deliberately so — two
#: live codes for one address can collide on the same 6-digit value (≈1e-6 per
#: pair) and a unique violation would present as "the code is broken". But the
#: bare ``UPDATE … RETURNING`` then returned two rows, read with
#: ``.one_or_none()``, which raises ``MultipleResultsFound`` — a 500 on sign-in
#: for a person whose code was correct. The ``id = (SELECT … LIMIT 1)`` form
#: consumes the NEWEST match instead: newest, because that is the code the person
#: was most recently sent and the other is about to expire anyway.
_CONSUME_SQL = """
UPDATE auth_email_otp_token
   SET consumed_at = now()
 WHERE id = (
        SELECT id FROM auth_email_otp_token
         WHERE identifier = CAST(:identifier AS TEXT)
           AND token      = CAST(:token      AS TEXT)
           AND consumed_at IS NULL
           AND invalidated_at IS NULL
           AND expires > now()
         ORDER BY created_at DESC, id DESC
         LIMIT 1)
RETURNING identifier, token, expires
"""

#: Kill every outstanding code for this identifier. Runs on exhaustion, and on
#: every refusal afterwards, so a code minted DURING the lockout is dead too.
_INVALIDATE_SQL = """
UPDATE auth_email_otp_token
   SET invalidated_at = now()
 WHERE identifier = CAST(:identifier AS TEXT)
   AND consumed_at IS NULL
   AND invalidated_at IS NULL
"""


def _record(row: Any) -> dict[str, Any]:
    """The wire shape ``@auth/core`` expects back from an adapter."""
    return {
        "identifier": row.identifier,
        "token": row.token,
        "expires": row.expires.isoformat() if row.expires else None,
    }


async def _session():
    """A session from the ONE shared async engine, deliberately UNBOUND.

    Imported inside the function, the rule ``acb_common.db`` sets: importing
    this module must not drag SQLAlchemy's async stack into a process that never
    opens a connection.
    """
    from acb_common.db import get_session_factory

    return get_session_factory()()


async def _gate_send(
    session: Any, identifier: str, expires: datetime, *, sql: str
) -> None:
    """Refuse when this identifier has already used its hourly budget.

    Called by BOTH legs of one send with the same ``expires``, which is what
    makes them agree about which send is in flight. They pass DIFFERENT ``sql``
    on purpose — see :data:`_CLAIMED_SEND_COUNT_SQL` (mails, the send leg) and
    :data:`_ROW_COUNT_SQL` (rows, the token leg). Assumes the caller already took
    the advisory lock.
    """
    from sqlalchemy import text

    used = (
        await session.execute(
            text(sql),
            {
                "identifier": identifier,
                "window_s": float(SEND_WINDOW_S),
                "expires": expires,
            },
        )
    ).scalar_one()
    if used >= SEND_MAX_PER_WINDOW:
        raise OtpRateLimited(
            SEND_RATE_LIMITED,
            f"{used} codes already sent to this address in the last "
            f"{SEND_WINDOW_S // 60} minutes",
        )


async def reserve_send(identifier: str, expires: datetime) -> dict[str, Any]:
    """Permit (and record) one code send, or raise :class:`OtpRateLimited`.

    The FIRST leg of a send, called from the Auth.js provider's
    ``sendVerificationRequest`` **before** the mail goes out — which is the only
    place a send limit can actually stop an email. It writes the row with no
    token; :func:`record_token` fills the hash in.

    Two refusals, and they are different failures:

    * :data:`SEND_RATE_LIMITED` — three mails already went to this address this
      hour.
    * :data:`SEND_DUPLICATE` — this send's slot was already claimed. That is the
      concurrent case (review finding P1b): ``expires`` is a millisecond, so N
      sends started together share one, and without the claim they excluded each
      other from the count and all passed. Exactly one claimant may mail.

    Both raise, and the adapter turns a raise into a throw, and the throw is what
    keeps ``sendVerificationRequest`` from reaching Resend.
    """
    from sqlalchemy import text

    who = canonical_identifier(identifier)
    if not who:
        raise ValueError("an OTP identifier is required")

    # The duplicate refusal is decided INSIDE the transaction and raised
    # OUTSIDE it, :func:`consume_token`'s idiom and for its reason: the
    # transaction must still COMMIT (so the advisory lock is released rather
    # than held across the raise, which would serialise the very burst this
    # refusal exists to shed), and raising while the session was open would drive
    # an already-committed transaction through the rollback arm.
    duplicate: OtpRateLimited | None = None
    result: dict[str, Any] | None = None

    session = await _session()
    try:
        await session.begin()
        await session.execute(text(_LOCK_SQL), {"identifier": who})
        await _gate_send(session, who, expires, sql=_CLAIMED_SEND_COUNT_SQL)
        row = (
            await session.execute(
                text(_CLAIM_SEND_SQL), {"identifier": who, "expires": expires}
            )
        ).one_or_none()
        await session.commit()
        if row is None:
            _log.warning(
                "email_otp.send_duplicate", identifier_domain=_domain(who)
            )
            duplicate = OtpRateLimited(
                SEND_DUPLICATE,
                "a code send for this address is already in flight",
            )
        else:
            _log.info("email_otp.reserve_send", identifier_domain=_domain(who))
            result = _record(row)
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

    if duplicate is not None:
        raise duplicate
    assert result is not None  # exactly one of the two arms above ran
    return result


async def record_token(
    identifier: str, token: str, expires: datetime
) -> dict[str, Any]:
    """Persist the verification-token hash, or raise :class:`OtpRateLimited`.

    The SECOND leg — Auth.js's ``createVerificationToken``. It applies a send
    gate of its own rather than trusting that :func:`reserve_send` ran: the two
    are concurrent, and a caller holding the internal bearer could invoke this
    one alone. Its gate counts ROWS rather than mails, because a caller who never
    calls the send leg would otherwise face no limit at all (clause 12: the limit
    applies to every caller).

    It does **not** claim a send slot. Landing first is legitimate and must not
    consume the mail budget or make the send leg look like a duplicate.
    """
    from sqlalchemy import text

    if not (token or "").strip():
        raise ValueError("record_token requires the token hash")

    who = canonical_identifier(identifier)
    if not who:
        raise ValueError("an OTP identifier is required")

    session = await _session()
    try:
        await session.begin()
        await session.execute(text(_LOCK_SQL), {"identifier": who})
        await _gate_send(session, who, expires, sql=_ROW_COUNT_SQL)
        row = (
            await session.execute(
                text(_UPSERT_SQL),
                {"identifier": who, "token": token, "expires": expires},
            )
        ).one()
        await session.commit()
        _log.info("email_otp.record_token", identifier_domain=_domain(who))
        return _record(row)
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def consume_token(identifier: str, token: str) -> dict[str, Any] | None:
    """Consume the token for *identifier*, or ``None`` when it does not match.

    One transaction, in this order and the order matters:

    1. lock the identifier;
    2. refuse with :class:`OtpRateLimited` when the window's attempts are
       already spent — **before** looking at the submitted code, which is what
       makes the (N+1)th attempt refused *even when it is correct*;
    3. charge the attempt to the live token, so a WRONG guess is counted (a
       wrong code matches no row, so nothing else could count it);
    4. consume, with single-use and expiry in the ``WHERE``;
    5. on a miss that spends the budget, invalidate the outstanding token.

    Returns the record ``@auth/core`` expects, or ``None`` — never a partial
    answer, and never an exception for an ordinary typo.
    """
    from sqlalchemy import text

    who = canonical_identifier(identifier)
    if not who:
        raise ValueError("an OTP identifier is required")
    if not (token or "").strip():
        raise ValueError("a token is required")

    # ⚠️ The lockout is decided INSIDE the transaction and raised OUTSIDE it.
    # It still has to COMMIT — invalidating the outstanding token is half of
    # what B3 asks for — so raising while the session was open would drive a
    # already-committed transaction through the rollback arm below.
    limited: OtpRateLimited | None = None
    result: dict[str, Any] | None = None

    session = await _session()
    try:
        await session.begin()
        await session.execute(text(_LOCK_SQL), {"identifier": who})

        spent = (
            await session.execute(
                text(_ATTEMPT_COUNT_SQL),
                {"identifier": who, "window_s": float(VERIFY_WINDOW_S)},
            )
        ).scalar_one()
        if spent >= VERIFY_MAX_ATTEMPTS:
            # Kill anything outstanding on the way out, including a code minted
            # DURING the lockout, then refuse.
            await session.execute(text(_INVALIDATE_SQL), {"identifier": who})
            await session.commit()
            _log.warning(
                "email_otp.verify_rate_limited",
                identifier_domain=_domain(who),
                attempts=int(spent),
            )
            limited = OtpRateLimited(
                VERIFY_RATE_LIMITED,
                f"{spent} verification attempts in the last "
                f"{VERIFY_WINDOW_S // 60} minutes",
            )
        else:
            charged = (
                await session.execute(
                    text(_CHARGE_ATTEMPT_SQL), {"identifier": who}
                )
            ).scalar_one_or_none()

            row = (
                await session.execute(
                    text(_CONSUME_SQL), {"identifier": who, "token": token}
                )
            ).one_or_none()

            exhausted = charged is not None and charged >= VERIFY_MAX_ATTEMPTS
            if row is None and exhausted:
                await session.execute(text(_INVALIDATE_SQL), {"identifier": who})
                _log.warning(
                    "email_otp.attempts_exhausted",
                    identifier_domain=_domain(who),
                    attempts=int(charged or 0),
                )

            await session.commit()
            result = _record(row) if row is not None else None
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

    if limited is not None:
        raise limited
    return result


def _domain(identifier: str) -> str:
    """The domain half of an address, for logging.

    ⚠️ The full address is **never** logged. These lines are written on a
    pre-authentication path that any holder of the internal bearer can drive for
    an address of their choosing, so a log that carried the identifier would be
    a way to write arbitrary addresses into the journal. The domain is enough to
    tell "our customer's staff" from "a scanner" and no more.
    """
    _, _, domain = identifier.partition("@")
    return domain or "-"
