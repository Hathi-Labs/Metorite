"""First-party logic modules of AI Studio (STU-B4a, ``ai_studio.md`` §3.7 layer 3).

A logic module is a typed function in this repository. The catalog serves it
as the action ``logic:<name>`` (class B). It gets one input, reads through a
``reader`` and returns PROPOSED writes. It applies nothing. The platform
applies the proposal in :mod:`gateway.studio.logic.apply`, all or none.

**The contract.**

* :class:`LogicModule` is frozen. ``input_model`` is a pydantic model. The
  door parses the body with it, so the module sees a typed value only.
* ``reads`` names the objects that the module may read. ``writes`` names the
  ``(object, op)`` pairs that it may propose. A proposal outside ``writes`` is
  refused before any write.
* ``run(input, reader)`` is a coroutine. ``reader`` is the ONLY way to read.
  It is a closure over ``objects_store.query_records`` in the same session,
  with the viewer of the CALLER (C8a), so a row that the caller cannot read
  never reaches the module. The module receives only the reader, by
  convention. It is first-party code that Metorite engineers review, and it
  runs in process. ⚠️ ``reader.__closure__`` holds the live session, so this is
  no isolation boundary. B5 (member-authored code) MUST NOT reuse this closure
  and needs a process or RPC boundary.

**The registry ships EMPTY.** :data:`MODULES` holds no module in production.
A module registers here in the slice that adds it (B4c names the MRP run).
A test adds its own module with ``monkeypatch.setitem``.

⚠️ A module is in-process, trusted code. It has no sandbox and no time limit.
A member-authored module is B5, on the off-box sandbox. Fence:
``tests/unit/test_studio_logic.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

__all__ = ["LOGIC_CLASS", "MODULES", "LogicModule", "ProposedWrite", "Reader"]

#: The class of each logic action. A module writes records, so it is class B,
#: and a call needs a direct proof (B4a) or, later, the member's card (B4b).
LOGIC_CLASS = "B"


@dataclass(frozen=True)
class ProposedWrite:
    """One write that a module proposes. ``op`` is ``create``, ``update`` or
    ``delete``. A create takes ``data`` and no ``id``. An update takes ``id``
    and ``data``. A delete takes ``id`` and no ``data``."""

    object: str
    op: str
    id: str | None = None
    data: Mapping[str, Any] | None = None


#: ``reader(object_name, filter=..., sort=..., limit=..., offset=...)`` gives
#: the records that the caller may read, as ``[{"id": ..., "data": {...}}]``.
Reader = Callable[..., Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class LogicModule:
    """One first-party logic module. See the module text."""

    name: str
    input_model: type[BaseModel]
    reads: frozenset[str]
    writes: frozenset[tuple[str, str]]
    run: Callable[[Any, Reader], Awaitable[list[ProposedWrite]]]
    title: str | None = None


#: The registry, by name. EMPTY in production (B4a).
MODULES: dict[str, LogicModule] = {}
