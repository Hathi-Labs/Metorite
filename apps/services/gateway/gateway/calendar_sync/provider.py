"""The provider seam of the calendar sync (WS-CAL CE-10, CE-11).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1 and §8.2.

The engine, the writer and the loop know :class:`CalendarProvider` and never
a provider client. Google (``google.py``) and Outlook (``outlook.py``) each
implement it. A provider holds NO state: the member's token enters as an
argument, and the app client enters through the constructor.

Errors reuse ``crm_sources.base`` (:class:`SourceError`, :class:`NeedsReconnect`,
:class:`RateLimited`), so ``connections/shared_token.py`` serves the calendar
too. A code is a short word. No error text holds a token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from gateway.crm_sources.base import (
    NeedsReconnect,
    RateLimited,
    SourceCredential,
    SourceError,
)

#: ``owning_app`` of the connection registry row of every calendar connection.
OWNING_APP = "calendar"

#: The window of a full list (spec §8.1 item 4).
WINDOW_DAYS_BACK = 30
WINDOW_DAYS_AHEAD = 180

#: The most characters of a title (``calendar_entries_title`` CHECK).
TITLE_MAX = 200
UNTITLED = "(No title)"

#: The name of the calendar that Metorite writes to (CE-13).
METORITE_CALENDAR_NAME = "Metorite"


class ProviderError(SourceError):
    """A provider refused or failed. ``code`` is a short word."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or f"The calendar provider answered {code}")
        self.code = code


class CursorExpired(SourceError):
    """The sync token or delta link is dead (HTTP 410). The engine lists again."""

    code = "cursor_expired"


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @classmethod
    def around(cls, now: datetime) -> Window:
        return cls(now - timedelta(days=WINDOW_DAYS_BACK),
                   now + timedelta(days=WINDOW_DAYS_AHEAD))


@dataclass(frozen=True)
class AccountIdentity:
    email: str
    account_id: str


@dataclass(frozen=True)
class CalendarInfo:
    id: str
    name: str
    primary: bool
    writable: bool


@dataclass(frozen=True)
class RemoteEvent:
    """One instance of a provider event, in provider-neutral words.

    A timed event has ``starts_at`` and ``ends_at`` (UTC, aware) and no dates.
    An all-day event has ``start_date`` and an EXCLUSIVE ``end_date`` and no
    instants. ``deleted`` is a hard delete in the feed.
    """

    external_id: str
    title: str = ""
    etag: str | None = None
    url: str | None = None
    notes: str | None = None
    location: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    tz: str | None = None
    busy: bool = True
    status: str = "confirmed"
    conference_url: str | None = None
    external_series_id: str | None = None
    original_start: datetime | None = None
    original_start_date: date | None = None
    deleted: bool = False
    updated: datetime | None = None  # provider last-change instant, UTC aware


@dataclass(frozen=True)
class SyncResult:
    """A read. ``full`` is True when the feed lists the whole window, so the
    writer also deletes the rows that the feed no longer lists."""

    changes: list[RemoteEvent]
    next_cursor: str | None
    full: bool


@dataclass(frozen=True)
class OutEvent:
    """An event that Metorite writes out (CE-13)."""

    title: str
    notes: str | None = None
    location: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    tz: str | None = None
    busy: bool = True
    task_url: str | None = None
    client_id: str | None = None  # id to send on insert, so a lost answer can retry
    #: True only on the adopt PATCH after a 409: the event may be CANCELLED
    #: (Google keeps the id of a deleted event), and only this revives it. An
    #: ordinary update must NOT revive an event the member deleted.
    revive: bool = False


@dataclass(frozen=True)
class OutResult:
    external_id: str
    etag: str | None = None
    url: str | None = None


class CalendarProvider(Protocol):
    provider: str  # provider column of the connection row
    source: str  # calendar_entries.source

    def consent_url(self, *, state: str, login_hint: str | None = None) -> str: ...

    async def exchange_code(self, code: str) -> SourceCredential: ...

    async def account_identity(self, cred: SourceCredential) -> AccountIdentity: ...

    async def refresh(self, cred: SourceCredential) -> SourceCredential: ...

    def needs_refresh(self, cred: SourceCredential, now: datetime) -> bool: ...

    async def revoke(self, cred: SourceCredential) -> bool: ...

    async def list_calendars(self, cred: SourceCredential) -> list[CalendarInfo]: ...

    async def sync(
        self, cred: SourceCredential, cursor: str | None, window: Window,
        *, calendar_id: str | None = None,
    ) -> SyncResult: ...

    async def create_calendar(self, cred: SourceCredential, name: str) -> str: ...

    async def upsert_event(
        self, cred: SourceCredential, calendar_id: str, event: OutEvent,
        external_id: str | None,
    ) -> OutResult: ...

    async def delete_event(
        self, cred: SourceCredential, calendar_id: str, external_id: str,
    ) -> None: ...


def clean_title(raw: str | None) -> str:
    """A title for ``calendar_entries``: 1 to 200 characters, never empty."""
    title = " ".join((raw or "").split())
    if not title:
        return UNTITLED
    if len(title) > TITLE_MAX:
        return title[: TITLE_MAX - 1].rstrip() + "…"
    return title


__all__ = [
    "METORITE_CALENDAR_NAME",
    "OWNING_APP",
    "TITLE_MAX",
    "UNTITLED",
    "AccountIdentity",
    "CalendarInfo",
    "CalendarProvider",
    "CursorExpired",
    "NeedsReconnect",
    "OutEvent",
    "OutResult",
    "ProviderError",
    "RateLimited",
    "RemoteEvent",
    "SyncResult",
    "Window",
    "clean_title",
]
