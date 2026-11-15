"""The events of the core apps, on the one event bus (STU-E12a).

``ingestion.event_hooks.emit_event`` is the one bus, and this module adds no
second one. It holds the emit sites of the core apps, the one reader of
``CORE_EVENTS_ORGS`` and the table of the sites.

* ``CORE_EVENTS_ORGS`` is a comma list of organization ids. ``*`` opens every
  org. Empty, the default, emits nothing. A SIGHUP reload applies a change
  (``docs/flag_reload.md``). A flip in production is ``enforcement-flip``.
* ``people.person.updated`` (source ``people``) goes out AFTER the commit of a
  person edit. The payload holds ids, the actor, the changed keys and the org.
  It holds no value. Only a DIRECTORY-tier key is named. A change to an HR or
  a private key emits nothing. Directory-tier keys are org-visible, so the
  dispatcher needs no author check for a ``people.*`` event. A private source
  (calendar) needs the ``_author_may_hear`` branch (E3-S).
* ``organization_id`` in the payload is information only. The dispatcher reads
  the bound tenant and never the payload (R5, R11).

Fence: ``tests/unit/test_core_app_events.py`` and
``tests/unit/test_people_events_r8.py``.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.db import current_tenant

_log = get_logger("gateway.core_events")

PEOPLE_SOURCE = "people"
PERSON_UPDATED = "people.person.updated"


@functools.lru_cache(maxsize=16)
def _parse_orgs(raw: str) -> frozenset[str]:
    """The entries of a comma list, trimmed and in lower case."""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def core_events_allowed(organization_id: str | None) -> bool:
    """True when ``CORE_EVENTS_ORGS`` holds the org, or holds ``*``. Default
    OFF. The one reader of the setting. The org is the bound tenant, never
    request input (R5)."""
    if not organization_id:
        return False
    listed = _parse_orgs(str(getattr(get_settings(), "core_events_orgs", "") or ""))
    return "*" in listed or str(organization_id).strip().lower() in listed


def directory_keys(names: Iterable[str]) -> list[str]:
    """The directory-tier keys of *names*, sorted. Any other key leaves."""
    from gateway.routes.tasks.people import DIRECTORY_FIELDS

    return sorted(set(names) & set(DIRECTORY_FIELDS))


def person_payload(person_id: str, actor: str, changed: list[str], org: str) -> dict[str, Any]:
    """The payload of ``people.person.updated``. Ids, the actor and the changed
    KEYS. It holds no value."""
    return {"person_id": str(person_id), "actor": actor, "changed": changed,
            "organization_id": org}


async def emit_person_updated(person_id: str, actor: str, fields: Iterable[str]) -> bool:
    """Emit ONE ``people.person.updated`` for one committed edit. Answer True
    when it went out. It never raises: a sink that fails must not fail the
    write. It emits nothing with the flag off, with no bound tenant, or when no
    directory-tier key changed."""
    try:
        org = current_tenant()
        if not core_events_allowed(org):
            return False
        changed = directory_keys(fields)
        if not changed:
            return False
        from ingestion.event_hooks import emit_event

        await emit_event(PEOPLE_SOURCE, PERSON_UPDATED,
                         person_payload(person_id, actor, changed, str(org)))
        return True
    except Exception as exc:
        _log.warning("core_events.person_updated_failed", error=type(exc).__name__)
        return False


#: The emit site of each core event, by event type. The catalog lists the same
#: types. `test_core_app_events.py` fails when the two differ.
EMIT_SITES: dict[str, Callable[..., Awaitable[bool]]] = {
    PERSON_UPDATED: emit_person_updated,
}
