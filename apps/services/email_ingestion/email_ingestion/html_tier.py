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

* **The four writers (EM-S3, §14.4.3).** With :func:`hot_only` true, no
  writer stores ``body_html`` for a cold message. The upsert of ``persist.py``
  reads :func:`drops_html`. ``body_backfill.write_bodies``, the open of
  ``transport/messages.py`` and ``core.hydrate_message_body`` write
  :data:`COLD_SAFE_HTML_SET`. No writer clears HTML that a row holds. EM-S4
  does that. Fence: ``tests/unit/test_email_html_hot_only.py``.

The sync window is a different window, and ``import_window.py`` owns it
(D-EM-53). Neither module imports the other. Every time here is in UTC.

Fence: ``tests/unit/test_email_html_tier.py``. It also fails when a module
outside this one holds the number of days next to ``received_at``, or reads
one of the two flags.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = [
    "COLD_SAFE_HTML_SET",
    "HTML_HOT_DAYS",
    "cold_before",
    "drops_html",
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
    again, so each writer keeps it. The four writers of EM-S3 read it through
    :func:`drops_html` and :func:`cold_before`.
    """
    from acb_common.settings import get_settings

    settings = get_settings()
    return bool(settings.email_html_from_provider) and bool(settings.email_html_hot_only)


def drops_html(received_at: datetime | None, now: datetime | None = None) -> bool:
    """True when a writer stores no ``body_html`` for this message (EM-S3).

    True only when :func:`hot_only` is true and the message is cold. The
    upsert and the open read it, because each one holds ``received_at``.
    """
    return hot_only() and is_cold(received_at, now)


def cold_before(now: datetime | None = None) -> datetime | None:
    """The value that a writer binds to ``:html_cold_before`` (EM-S3).

    It is :func:`hot_cutoff` while :func:`hot_only` is true, else ``None``.
    ``received_at < NULL`` is never true in SQL, so with ``None`` each row
    is hot and the writer stores the HTML as before.
    """
    return hot_cutoff(now) if hot_only() else None


#: The SET term of a writer that fetched a body and updates one row (EM-S3).
#: Bind ``:html_cold_before`` to :func:`cold_before` and ``:bh`` to the
#: fetched HTML. The term never writes fetched HTML into a cold row, and a
#: cold row keeps the HTML that it holds. EM-S4 clears stored HTML, and this
#: term does not. A row with ``received_at`` NULL is never cold, as in
#: :func:`is_cold`. ``NULLIF`` turns an empty stored value into NULL, so
#: ``html_remote`` sees that the row holds no HTML.
COLD_SAFE_HTML_SET = (
    "body_html = CASE WHEN received_at < :html_cold_before "
    "THEN NULLIF(body_html, '') ELSE :bh END"
)
