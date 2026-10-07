"""The HTML hot window of a mailbox: which messages keep ``body_html``.

Spec: ``project-docs/specs/email_app_master_plan.md`` §14.4.1 (WS-17 EM-S1).
Owner decisions D-EM-49 and D-EM-53 (§14.3).

This module is the ONE owner of the HTML hot window. Do not copy its rules:

* **The window (D-EM-49).** A message received in the last 90 days keeps its
  ``body_html``. An older message is cold. The provider holds its HTML, and the
  reading pane gets it from there (``GET /email/messages/{id}/html``).
* **A message with no ``received_at`` is never cold**, so it keeps its HTML.
* **The two flags.** :func:`from_provider` reads ``EMAIL_HTML_FROM_PROVIDER``,
  which opens the route and the ``html_remote`` signal. :func:`hot_only` is true
  only when ``EMAIL_HTML_HOT_ONLY`` is true too. So a writer never drops HTML
  that the pane cannot get again. This module is the one reader of both flags.

The sync window is a different window, and ``import_window.py`` owns it
(D-EM-53). Neither module imports the other. Every time here is in UTC.

Fence: ``tests/unit/test_email_html_tier.py``. It also fails when a module
outside this one holds the number of days next to ``received_at``, or reads
one of the two flags.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = [
    "HTML_HOT_DAYS",
    "from_provider",
    "hot_cutoff",
    "hot_only",
    "is_cold",
]

#: A message received in the last this many days keeps its ``body_html``
#: (D-EM-49). The coordinator set the number on 2026-10-07.
HTML_HOT_DAYS = 90


def _aware(value: datetime) -> datetime:
    """``value`` in UTC. A naive value is read as UTC, as the sync reads it."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def hot_cutoff(now: datetime | None = None) -> datetime:
    """The oldest time that is still hot: ``now - 90 days``, in UTC."""
    current = datetime.now(UTC) if now is None else _aware(now)
    return current - timedelta(days=HTML_HOT_DAYS)


def is_cold(received_at: datetime | None, now: datetime | None = None) -> bool:
    """True when ``received_at`` is older than :func:`hot_cutoff`.

    A message at the cutoff is still hot. A message with no ``received_at``
    is never cold, so it keeps its HTML.
    """
    if received_at is None:
        return False
    return _aware(received_at) < hot_cutoff(now)


def from_provider() -> bool:
    """True when the pane gets the HTML of a cold message from the provider.

    It reads ``EMAIL_HTML_FROM_PROVIDER``. False is the default, so the route
    answers 404 and ``html_remote`` stays false. 🔴 The value ``true`` on a box
    is gate ``enforcement-flip`` (``work_plan.md`` §6 row D4).
    """
    from acb_common.settings import get_settings

    return bool(get_settings().email_html_from_provider)


def hot_only() -> bool:
    """True when a writer stores no ``body_html`` for a cold message.

    True only when ``EMAIL_HTML_FROM_PROVIDER`` and ``EMAIL_HTML_HOT_ONLY``
    are both true. With the first one false, the pane cannot get the HTML
    again, so each writer keeps it. EM-S3 adds the writers that read this.
    """
    from acb_common.settings import get_settings

    settings = get_settings()
    return bool(settings.email_html_from_provider) and bool(settings.email_html_hot_only)
