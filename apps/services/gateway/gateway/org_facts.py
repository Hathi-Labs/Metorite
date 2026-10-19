"""The org-facts read seam (WS-53 CRM-Q32-5, ``crm_platform.md`` §18.14.4).

**One read seam for every app, no second store.** The answered facts of an
app stay in the store of that app. The CRM keeps its facts in the mapping
plan. Every reader reads the facts of the org through :func:`read`, and each
app registers ONE provider with :func:`register`. A provider returns the
facts of its app as rows of one shape, :class:`Fact`, built through
:func:`fact`, the one builder of a row.

**Who owns it.** WS-53 owns this seam until a cross-app owner exists
(``project-docs/work_plan.md`` §4).

**Tenancy (R5).** A provider reads in the caller's tenant session, which the
route opens with the org of the session, never of the request. A provider
applies the access rule of its app. A member that the app does not admit
reads no fact of it. ``set_by`` names a role (``admin``, ``Metorite`` or
``Jev``), never an email, because an agent reads it.

⚠️ **Mem0 ``org:global`` is not a store of org facts** until D17 binds it
to a tenant: one key serves every org there, with no row level security
(§18.14.13).

Fences: ``tests/unit/test_org_facts.py`` (no module outside this file and
the providers builds a fact row) and ``tests/unit/test_org_facts_r8.py``
(R8: each org reads only its own facts, through the route and the tool).
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: The words of ``source``: who gave the fact. ``member`` (CRM-Q33) is a
#: business rule that one member wrote for themselves, which only that
#: member reads.
SOURCES: tuple[str, ...] = ("code", "ai", "admin", "member")

#: ``source`` → ``set_by``. A role, never an email: an agent reads it.
SET_BY: dict[str, str] = {"code": "Metorite", "ai": "Jev", "admin": "admin", "member": "you"}

#: The modules that register a provider when they are imported. A reader
#: imports them first, so the registry never depends on an import order.
PROVIDER_MODULES: tuple[str, ...] = ("gateway.routes.crm.facts",)


@dataclass(frozen=True)
class Fact:
    """One fact of the org (§18.14.4, the row shape).

    ``subject`` names what the fact is about: ``module:Invoices``,
    ``field:Invoices.Total_Product_Amount`` or ``term:revenue``. ``key`` is
    the part, ``value`` its answer (``None``: no field holds it), and
    ``meaning`` one sentence that a person or an agent reads."""

    app: str
    subject: str
    key: str
    value: str | None
    meaning: str
    source: str
    set_at: datetime | None
    set_by: str

    def as_json(self) -> dict[str, Any]:
        return {
            "app": self.app, "subject": self.subject, "key": self.key,
            "value": self.value, "meaning": self.meaning, "source": self.source,
            "set_at": self.set_at.isoformat() if self.set_at is not None else None,
            "set_by": self.set_by,
        }


def fact(app: str, subject: str, key: str, value: str | None, meaning: str,
         source: str, set_at: datetime | None = None) -> Fact:
    """THE builder of a fact row. ``set_by`` follows from ``source``, so no
    provider can put an email there. An unknown source reads as ``code``."""
    word = source if source in SOURCES else "code"
    return Fact(app=app, subject=subject, key=key, value=value, meaning=meaning,
                source=word, set_at=set_at, set_by=SET_BY[word])


#: A provider: the facts of one app, read in the caller's tenant session for
#: the caller. It applies the access rule of its app itself.
Provider = Callable[[Any, Any], Awaitable[Sequence[Fact]]]

_REGISTRY: dict[str, Provider] = {}


def register(app: str, provider: Provider) -> Provider:
    """Register the ONE provider of *app*. A second provider of one app
    replaces the first, so a module that is imported twice registers once."""
    _REGISTRY[app] = provider
    return provider


def providers() -> dict[str, Provider]:
    """Each registered provider, by app. It imports
    :data:`PROVIDER_MODULES` first."""
    for name in PROVIDER_MODULES:
        importlib.import_module(name)
    return dict(_REGISTRY)


def matches(row: Fact, q: str | None) -> bool:
    """*q* matches the subject or the meaning, case-insensitive, in code."""
    if not q:
        return True
    needle = q.strip().lower()
    return needle in row.subject.lower() or needle in row.meaning.lower()


async def read(db: Any, user: Any, *, app: str | None = None,
               q: str | None = None) -> list[Fact]:
    """The facts of the caller's org, in the caller's tenant session.

    *app* keeps the facts of one app, and *q* filters them (:func:`matches`).
    An unknown app gives none. Each provider applies its own access rule."""
    found: list[Fact] = []
    for name, provider in sorted(providers().items()):
        if app and name != app:
            continue
        found.extend(row for row in await provider(db, user) if matches(row, q))
    return found


__all__ = [
    "PROVIDER_MODULES",
    "SET_BY",
    "SOURCES",
    "Fact",
    "Provider",
    "fact",
    "matches",
    "providers",
    "read",
    "register",
]
