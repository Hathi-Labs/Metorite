"""The import window of a mailbox: how far back a path may write mail.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.7 (WS-17 EM-T6a).
Owner decisions D-EM-10 and D-EM-11 (§10.2).

This module is the ONE owner of three rules. Do not copy them:

* **The ceiling (D-EM-10).** No path writes a message older than 6 months: the
  sync core, Clean older mail, Process past emails and the "Load older"
  backfill of a folder (``transport/folders.py``). A month is 30 days, so the
  ceiling is ``now - 180 days``.
* **The range (D-EM-11).** The member chooses 0 to 6 months at the first
  connect, and the default is 1. :func:`since_for_months` turns the choice
  into the date that the callback writes to ``email_accounts.import_since``.
* **The floor.** A member act (Process past emails, Clean older mail) can pass
  an explicit ``since``. Its floor is the later of that ``since`` and the
  ceiling. Every other sync takes the choice of the member: the later of
  ``import_since`` and the ceiling. A row with ``import_since`` NULL connected
  before EM-T6, and the ceiling alone binds it.

The sync core passes the floor on every sync, deep or shallow, and drops a
message older than the floor before it writes (:func:`below_floor`). Fence:
``tests/unit/test_email_import_floor.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = [
    "CEILING_DAYS",
    "DAYS_PER_MONTH",
    "DEFAULT_IMPORT_MONTHS",
    "MAX_IMPORT_MONTHS",
    "below_floor",
    "ceiling",
    "is_import_months",
    "since_for_months",
    "sync_floor",
]

#: The code counts a month as 30 days (D-EM-10).
DAYS_PER_MONTH = 30

#: The member may choose 0 to this many months (D-EM-11).
MAX_IMPORT_MONTHS = 6

#: The range when the member chose nothing, and the range of an OAuth state
#: that was signed before EM-T6a (D-EM-11).
DEFAULT_IMPORT_MONTHS = 1

#: No sync writes a message older than this many days (D-EM-10).
CEILING_DAYS = MAX_IMPORT_MONTHS * DAYS_PER_MONTH


def _now(now: datetime | None) -> datetime:
    return datetime.now(UTC) if now is None else _aware(now)


def _aware(value: datetime) -> datetime:
    """``value`` in UTC. A naive value is read as UTC.

    Every value this module returns is in UTC, because a provider writes the
    wall time of the floor with a ``Z`` (``outlook.py`` ``list_messages``). A
    floor in another zone would then move by its offset.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_import_months(value: Any) -> bool:
    """True when ``value`` is an integer from 0 to 6. A bool is not one."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_IMPORT_MONTHS
    )


def ceiling(now: datetime | None = None) -> datetime:
    """The oldest date that any sync may write: ``now - 180 days``."""
    return _now(now) - timedelta(days=CEILING_DAYS)


def since_for_months(months: int, now: datetime | None = None) -> datetime:
    """The ``import_since`` of a range of ``months``: ``now - 30 * months days``.

    Raises:
        ValueError: ``months`` is not an integer from 0 to 6.
    """
    if not is_import_months(months):
        raise ValueError(f"an import range is 0 to {MAX_IMPORT_MONTHS} months")
    return _now(now) - timedelta(days=DAYS_PER_MONTH * months)


def sync_floor(
    *,
    since: datetime | None,
    import_since: datetime | None,
    now: datetime | None = None,
) -> datetime:
    """The floor of one sync. It is never older than the ceiling.

    ``since`` is the explicit floor of a member act, or ``None``. With no
    ``since``, the choice of the member (``import_since``) binds. With neither,
    the ceiling binds.
    """
    top = ceiling(now)
    chosen = since if since is not None else import_since
    if chosen is None:
        return top
    # ``_aware`` converts to UTC, so the floor is in UTC whatever the zone of
    # ``since`` was.
    return max(_aware(chosen), top)


def below_floor(received_at: datetime | None, floor: datetime) -> bool:
    """True when a message received at ``received_at`` is older than ``floor``.

    A message with no ``received_at`` is never below the floor, so the core
    keeps it.
    """
    if received_at is None:
        return False
    return _aware(received_at) < _aware(floor)
