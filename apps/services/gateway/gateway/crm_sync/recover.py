"""Recover a dropped activity: the rules that need no database (WS-53 CRM-Q23b).

Spec: ``project-docs/specs/crm_platform.md`` §18.10.

The activity passes run before the module pass. So an activity whose parent is
a generic module record that the run has not read yet finds no row, and
``engine._write_record`` drops it (CRM-Q23a). The sync now keeps such an
activity in the cursor state of its entity, under :data:`KEY`, as
``{id: [module, parent_ext_id, first_seen]}``. After the module pass, the
engine reads again each kept activity whose parent is now in, by its id.

This module holds the shape of that list and its rules. It reads no database
and calls no provider. ``engine.py`` owns every read and every write.

* An activity that the sync stores (or that it drops for a reason this list
  does not keep) leaves the list.
* A re-add of an id that is in the list keeps its first ``first_seen``, so an
  activity that changes every day still leaves at :data:`MAX_AGE`.
* The list holds at most :data:`CAP` entries. A new id past the cap is not
  kept, and the entity is marked :data:`OVERFLOW_KEY`. The engine then queues
  one full read of the entity, at most one time in :data:`OVERFLOW_EVERY`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

#: The cursor-state key of the kept activities of one activity entity.
KEY = "recover"
#: The cursor-state key that says a new id did not fit under :data:`CAP`.
OVERFLOW_KEY = "recover_overflow"
#: The cursor-state key of the reads by id that each kept id has had and that
#: did not take it off the list. The engine drops an id at ``RETRY_LIMIT``.
TRIES_KEY = "recover_tries"
#: The cursor-state key of the time of the last refused read by ids. No read
#: by ids of the entity is sent for :data:`READ_PAUSE` after it.
PAUSE_KEY = "recover_paused_at"
#: How long a refused read by ids pauses the reads of its entity.
READ_PAUSE = timedelta(hours=24)
#: The most entries that one activity entity keeps.
CAP = 5_000
#: An entry older than this leaves the list.
MAX_AGE = timedelta(days=7)
#: An overflow queues one full read of its entity at most this often.
OVERFLOW_EVERY = timedelta(days=7)
#: The activity entities that keep a list, in the order the step reads them.
ACTIVITY_ENTITIES: tuple[str, ...] = ("note", "call", "meeting", "task")

#: One entry: ``[module, parent_ext_id, first_seen]``.
Entry = list[str]


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def entries_of(state: Mapping[str, Any] | None) -> dict[str, Entry]:
    """The kept entries of a cursor state. An entry of a wrong shape is dropped."""
    raw = (state or {}).get(KEY)
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Entry] = {}
    for ext_id, entry in raw.items():
        if (
            isinstance(entry, list | tuple) and len(entry) == 3
            and all(isinstance(v, str) and v for v in entry)
        ):
            out[str(ext_id)] = [str(v) for v in entry]
    return out


def merge(
    kept: Mapping[str, Entry],
    landed: Iterable[str],
    dropped: Mapping[str, tuple[str, str]],
    now: str,
) -> tuple[dict[str, Entry], bool]:
    """The list after one page of an activity entity.

    ``landed`` holds every id the page read with no error. ``dropped`` maps
    the ids that this list keeps to ``(module, parent_ext_id)``. ``now`` is
    the ``first_seen`` of a new entry, as ISO text. Returns the new list and
    True when a new id did not fit under :data:`CAP`.
    """
    gone = set(landed) - set(dropped)
    out = {k: list(v) for k, v in kept.items() if k not in gone}
    overflow = False
    for ext_id, (module, parent) in dropped.items():
        old = out.get(ext_id)
        if old is None and len(out) >= CAP:
            overflow = True
            continue
        out[ext_id] = [module, parent, old[2] if old is not None else now]
    return out, overflow


def expire(kept: Mapping[str, Entry], now: datetime) -> dict[str, Entry]:
    """The list without the entries older than :data:`MAX_AGE`. An entry
    whose ``first_seen`` does not parse leaves too."""
    out: dict[str, Entry] = {}
    for ext_id, entry in kept.items():
        seen = _parse(entry[2])
        if seen is not None and now - seen < MAX_AGE:
            out[ext_id] = list(entry)
    return out


def parents(kept: Mapping[str, Entry], mirrored: Iterable[str]) -> set[tuple[str, str]]:
    """The ``(module, parent_ext_id)`` pairs of the list, for the modules that
    the profile mirrors now. Only those can link."""
    allowed = set(mirrored)
    return {(e[0], e[1]) for e in kept.values() if e[0] in allowed}


def recoverable(kept: Mapping[str, Entry], present: set[tuple[str, str]]) -> list[str]:
    """The ids whose parent is in now, oldest ``first_seen`` first."""
    found = [(e[2], ext_id) for ext_id, e in kept.items() if (e[0], e[1]) in present]
    return [ext_id for _seen, ext_id in sorted(found)]


def modules_named(kept: Mapping[str, Entry]) -> set[str]:
    """The modules that the list names."""
    return {e[0] for e in kept.values()}


def tries_of(state: Mapping[str, Any] | None) -> dict[str, int]:
    """The tries of each kept id. A value of a wrong shape is dropped."""
    raw = (state or {}).get(TRIES_KEY)
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(k): int(v) for k, v in raw.items()
        if isinstance(v, int) and not isinstance(v, bool) and v > 0
    }


def paused(last: str | None, now: datetime) -> bool:
    """True while a refused read by ids still pauses the reads of an entity."""
    stamp = _parse(last)
    return stamp is not None and now - stamp < READ_PAUSE


def overflow_due(last: str | None, now: datetime) -> bool:
    """True when an overflow of this entity may queue a full read now."""
    stamp = _parse(last)
    return stamp is None or now - stamp >= OVERFLOW_EVERY


__all__ = [
    "ACTIVITY_ENTITIES",
    "CAP",
    "KEY",
    "MAX_AGE",
    "OVERFLOW_EVERY",
    "OVERFLOW_KEY",
    "PAUSE_KEY",
    "READ_PAUSE",
    "TRIES_KEY",
    "entries_of",
    "expire",
    "merge",
    "modules_named",
    "overflow_due",
    "parents",
    "paused",
    "recoverable",
    "tries_of",
]
