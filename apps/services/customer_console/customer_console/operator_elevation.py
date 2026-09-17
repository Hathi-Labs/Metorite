"""Time-boxed elevation, and the break-glass path — WS-31 **CP-12e**.

Spec: ``project-docs/specs/operator_identity_and_access.md`` §6.3 · §6.4 ·
§8.1 done-whens 20-24 · **D64.4**.

**No operator holds a destructive privilege while sitting still.** An ``admin``
holds the *right to elevate*. They open a window, do the work, and it closes.

The recorded failure mode of just-in-time access is temporary access nobody
expires, which becomes standing privilege wearing a different name. That is why
``expires_at`` is enforced by the Console on every request rather than by
whatever opened the window.

⚠️ **Elevation is not a way to BECOME an admin.** It time-boxes a role the
person already holds. A ``viewer`` who asks is refused, and the refusal is
logged — done-when 23.

⚠️ **Policy only. No SQL.** :mod:`customer_console.store` owns the statements.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from customer_console.operators import ADMIN

_log = logging.getLogger("platform.operators")

__all__ = [
    "DEFAULT_TTL_MINUTES",
    "ELEVATION_FLAG",
    "MIN_REASON_CHARS",
    "ElevationRefused",
    "NotElevated",
    "check_reason",
    "check_window",
    "elevation_required",
    "may_elevate",
    "ttl",
]

#: Thirty minutes. Long enough to finish the job, short enough that forgetting
#: to close it is not the same as never having opened it.
DEFAULT_TTL_MINUTES = 30

#: A reason shorter than this is not a reason. The floor is deliberately low —
#: the point is to make somebody type an intent, not to police prose.
MIN_REASON_CHARS = 12


#: ⚠️ **ELEVATION IS OFF BY DEFAULT — D72, owner decision 2026-09-18.**
#:
#: D64.4 said *"no standing destructive privilege"*: an admin held the right to
#: elevate, not the privilege. This amends that. The owner's reasoning, taken
#: after hitting it while onboarding a customer:
#:
#: > *"Really basic. Simple role-based, and since I only have admins, I want to
#: > be able to do whatever."*
#:
#: **What changed is the company, not the threat.** D64.4 was written for a
#: staff directory with mixed roles, where a hijacked admin tab is a real and
#: separate risk. There are TWO operators today, both `admin`, and both are
#: owners of the business. The window was not standing between an attacker and
#: the data — it was standing between the owner and a routine, reversible act,
#: and the owner could not recognise the refusal as their own control.
#:
#: **The rank check is untouched.** `viewer` and `editor` are still refused by
#: the §5 matrix exactly as before. What this removes is the second factor on
#: top of a rank the person already holds.
#:
#: ⚠️ **The `elevated=True` markers in the MATRIX STAY, and deleting them is
#: not the same change.** They record which nine routes are the sharp ones, and
#: that judgement is still correct and still worth having written down. This
#: flag decides whether a window is DEMANDED; the marker decides which routes
#: would demand one.
#:
#: **Turn it back on with `OPERATOR_ELEVATION_REQUIRED=true`.** The trigger to
#: revisit, named now so it is not left to memory: **the first operator who is
#: not an owner of the company.** At that point the threat D64.4 describes is
#: real again and this is one environment variable.
ELEVATION_FLAG = "OPERATOR_ELEVATION_REQUIRED"


def elevation_required(env: dict[str, str] | None = None) -> bool:
    """Does a sharp route demand an open window as well as the rank? (D72)

    Defaults **False**. A deployment that says nothing gets simple role-based
    access, which is what the owner asked for.
    """
    source = os.environ if env is None else env
    raw = (source.get(ELEVATION_FLAG) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class ElevationRefused(Exception):
    """This operator may not open a window. Callers map this to **403**."""


class NotElevated(Exception):
    """No live window, so this action does not run. Callers map this to **403**.

    Distinct from :class:`ElevationRefused` because they mean different things
    to the person reading the response: *you may not elevate* against *you have
    not elevated yet*. The second is fixed by opening a window.
    """


def ttl(env: dict[str, str] | None = None) -> timedelta:
    """How long a window lasts.

    ⚠️ A zero, negative or unparseable value falls back to the default. Reading
    ``0`` as "never expires" would turn a typo into standing privilege, which
    is the exact thing this module removes.
    """
    source = os.environ if env is None else env
    raw = (source.get("OPERATOR_ELEVATION_TTL_MINUTES") or "").strip()
    if not raw:
        return timedelta(minutes=DEFAULT_TTL_MINUTES)
    try:
        minutes = int(raw)
    except ValueError:
        _log.warning("operator.bad_elevation_ttl")
        return timedelta(minutes=DEFAULT_TTL_MINUTES)
    if minutes <= 0:
        _log.warning("operator.bad_elevation_ttl")
        return timedelta(minutes=DEFAULT_TTL_MINUTES)
    return timedelta(minutes=minutes)


def may_elevate(role: str | None) -> None:
    """Only an ``admin`` may open a window. Raises :class:`ElevationRefused`."""
    if role != ADMIN:
        _log.warning(
            "operator.elevation_refused",
            extra={"elevation_why": "role", "elevation_role": role or "<none>"},
        )
        raise ElevationRefused("forbidden")


def check_reason(reason: str | None) -> str:
    """Return the trimmed reason, or raise :class:`ValueError` (a **400**).

    Required, because a reason is what makes the audit row answer *why* rather
    than only *who* and *what*. An elevation nobody explained is an elevation
    nobody can review afterwards.
    """
    trimmed = (reason or "").strip()
    if len(trimmed) < MIN_REASON_CHARS:
        raise ValueError(
            f"a reason of at least {MIN_REASON_CHARS} characters is required"
        )
    return trimmed


def check_window(row: Any, *, now: datetime) -> None:
    """Raise :class:`NotElevated` unless *row* is a window open right now.

    ``row`` is what ``store.operator_elevation_live`` returned, or ``None``.
    The expiry is compared HERE, on this request, rather than trusted from a
    flag set when the window opened.
    """
    if row is None:
        _log.warning("operator.not_elevated", extra={"elevation_why": "none"})
        raise NotElevated("forbidden")

    expires = row["expires_at"]
    if expires.tzinfo is None:
        from datetime import UTC
        expires = expires.replace(tzinfo=UTC)

    if expires <= now:
        _log.warning("operator.not_elevated", extra={"elevation_why": "expired"})
        raise NotElevated("forbidden")
