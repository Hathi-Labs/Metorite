"""The operator session — an opaque token with an expiry and a row.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §4.3 · §8.1
done-whens 7-12 · **D64**. Board: WS-31 **CP-12b**.

**What this replaces.** Today the Operator Console writes the shared passphrase
INTO the cookie (``session/route.ts:30``). Three consequences, and this module
removes all three:

  * a disclosed cookie is a disclosed passphrase, for the whole team;
  * nothing expires, because a session cookie with no server-side row has no
    clock;
  * nothing can be revoked, so removing one person means changing the secret
    for everybody.

**Two clocks, not one.** ``expires_at`` is absolute and ``last_seen_at`` drives
the idle timeout. They answer different questions — *"signed in 10 hours ago"*
and *"last did anything 10 hours ago"* — and only the second should log
somebody out in the middle of a task.

⚠️ **Policy only. No SQL.** :mod:`customer_console.store` owns the statements,
as it does for :mod:`customer_console.operators` beside this.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from customer_console.keys import ENV_SESSION, mint_key, verify_secret

_log = logging.getLogger("platform.operators")

__all__ = [
    "DEFAULT_ABSOLUTE_TTL_MINUTES",
    "DEFAULT_IDLE_TTL_MINUTES",
    "IssuedSession",
    "SessionRejected",
    "absolute_ttl",
    "idle_ttl",
    "issue",
    "verify",
]

#: Twelve hours. Long enough for a working day, short enough that a stolen
#: token does not outlive the week it was stolen in.
DEFAULT_ABSOLUTE_TTL_MINUTES = 12 * 60

#: Sixty minutes of inactivity. The number that actually protects an unlocked
#: laptop, which is the realistic loss of a staff credential.
DEFAULT_IDLE_TTL_MINUTES = 60


class SessionRejected(Exception):
    """This session does not authenticate anybody. Callers map this to **401**.

    ⚠️ ONE exception for every cause — unknown prefix, wrong secret, expired,
    idle, revoked, and an operator who is no longer ``active``. A 401 that said
    which would tell a stranger whether a prefix they guessed exists. The cause
    goes to the log. This is the same argument
    :mod:`customer_console.operators` makes for its single refusal string.
    """


@dataclass(frozen=True)
class IssuedSession:
    """A freshly minted session. ``token`` exists exactly once, here."""

    prefix: str
    key_hash: str
    token: str
    expires_at: datetime


def _minutes(name: str, default: int, env: dict[str, str] | None) -> int:
    """Read a positive minute count, or fall back to *default*.

    ⚠️ A zero or negative value falls back rather than being honoured. Reading
    ``OPERATOR_IDLE_TIMEOUT_MINUTES=0`` as "never time out" would turn a typo
    into a permanent session, and that is the failure this module exists to
    remove rather than to re-create through configuration.
    """
    source = os.environ if env is None else env
    raw = (source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log.warning("operator.bad_ttl", extra={"operator_ttl_var": name})
        return default
    if value <= 0:
        _log.warning("operator.bad_ttl", extra={"operator_ttl_var": name})
        return default
    return value


def absolute_ttl(env: dict[str, str] | None = None) -> timedelta:
    """How long a session lives at most, however busy the operator is."""
    return timedelta(
        minutes=_minutes(
            "OPERATOR_SESSION_TTL_MINUTES", DEFAULT_ABSOLUTE_TTL_MINUTES, env
        )
    )


def idle_ttl(env: dict[str, str] | None = None) -> timedelta:
    """How long a session survives with nothing happening on it."""
    return timedelta(
        minutes=_minutes(
            "OPERATOR_IDLE_TIMEOUT_MINUTES", DEFAULT_IDLE_TTL_MINUTES, env
        )
    )


def issue(*, now: datetime, env: dict[str, str] | None = None) -> IssuedSession:
    """Mint one session. The caller stores the hash and hands back the token.

    ``mint_key(env=ENV_SESSION)`` — the shared seam, not a private one. The
    secret is never returned to this module twice and is never stored, so a
    disclosure of ``operator_session`` hands over no working session.
    """
    minted = mint_key(env=ENV_SESSION)
    return IssuedSession(
        prefix=minted.prefix,
        key_hash=minted.key_hash,
        token=minted.token,
        expires_at=now + absolute_ttl(env),
    )


def _aware(value: Any) -> datetime:
    """Treat a naive timestamp as UTC.

    Postgres ``TIMESTAMPTZ`` comes back aware, so this only fires for a caller
    that built one by hand. Comparing an aware and a naive datetime raises, and
    a session check that raised would be a 500 where a 401 belongs.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def verify(
    row: Any,
    *,
    secret: str,
    now: datetime,
    env: dict[str, str] | None = None,
) -> None:
    """Raise :class:`SessionRejected` unless *row* is a live, usable session.

    ``row`` is what ``store.operator_session_by_prefix`` returned, or ``None``.
    It carries the session AND the joined operator, because a session is only
    as good as the person behind it.

    ⚠️ **The operator's status is re-read on every request, not trusted from
    sign-in time.** That is what makes deactivation take effect at once rather
    than whenever the session happened to expire. Done-when 10 is the fence.
    """
    if row is None:
        _log.warning("operator.session_rejected", extra={"session_why": "unknown"})
        raise SessionRejected("invalid session")

    if not verify_secret(secret, str(row["key_hash"])):
        # Constant-time inside `verify_secret`. A plain `==` on a hash leaks its
        # prefix through timing, and a leaked prefix narrows an offline search.
        _log.warning("operator.session_rejected", extra={"session_why": "secret"})
        raise SessionRejected("invalid session")

    if row["revoked_at"] is not None:
        _log.warning("operator.session_rejected", extra={"session_why": "revoked"})
        raise SessionRejected("invalid session")

    if _aware(row["expires_at"]) <= now:
        _log.warning("operator.session_rejected", extra={"session_why": "expired"})
        raise SessionRejected("invalid session")

    if _aware(row["last_seen_at"]) + idle_ttl(env) <= now:
        _log.warning("operator.session_rejected", extra={"session_why": "idle"})
        raise SessionRejected("invalid session")

    if str(row["status"]) != "active":
        _log.warning(
            "operator.session_rejected",
            extra={"session_why": "operator_status"},
        )
        raise SessionRejected("invalid session")
