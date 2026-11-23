"""The query vector, kept for one turn (WS-KG KG-3b, ``knowledge_graph.md`` §9).

One question to ``GET /search/company`` may ask two sources for meaning:
Knowledge and Email. Both embed the same words through the one embed door
(``kb_index/embed.py``). Without a cache, the door is called twice.

* The route calls :func:`begin_turn` when ``CONTEXT_RECENCY`` is on. The
  store is a ``ContextVar``, so a request never sees the store of another.
* :func:`remember` keeps a vector and :func:`recall` returns it. The key is
  the org and the folded words. The org comes from the session.
* Only a vector is kept. A failure or a closed flag is not kept, because a
  failure of one source says nothing about the gate of another.
* With no turn begun, :func:`recall` finds nothing. So the route with the
  flag off, and any other caller, behave as before.

**One embed task for the turn** (WS-61 KR-ZERO, task 2974). The door takes
longer than one source may wait, so the route starts the embed at its start
with :func:`start`. The task runs while the keyword sources run, one at a
time. Each source that needs the vector awaits the same task with
:func:`wait`, inside its own time. The wait never cancels the task, so a
later source can still take the vector.

* The route checks each gate before it starts the task. A source checks its
  own gate again before it waits.
* :func:`outcome` says what became of the task: ``ran``, ``timed_out``,
  ``failed`` or ``off``. :func:`embed_ms` gives its time.
* :func:`end_turn` cancels a task that still runs when the answer goes.
* With no task started, :func:`started` is false, and each source makes its
  own call as before.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

#: The closed list of :func:`outcome` values, as the answer's ``meaning``.
MEANING_RAN = "ran"
MEANING_TIMED_OUT = "timed_out"
MEANING_FAILED = "failed"
MEANING_OFF = "off"
MEANINGS: tuple[str, ...] = (MEANING_RAN, MEANING_TIMED_OUT, MEANING_FAILED, MEANING_OFF)

_TURN: ContextVar[dict[tuple[str, str], list[float]] | None] = ContextVar(
    "company_search_turn_vectors", default=None,
)


@dataclass
class _Embed:
    """The one embed task of a turn, and what became of it."""

    key: tuple[str, str]
    began: float
    task: asyncio.Task[list[float] | None] | None = None
    #: ``ok``, ``failed`` or ``timed_out`` once the task ends. ``None`` while it runs.
    result: str | None = None
    #: The milliseconds the task took, once it ends.
    ms: int | None = None
    #: True once a source took the vector.
    used: bool = False
    #: True once a source stopped its wait before the vector came.
    late: bool = False


_EMBED: ContextVar[_Embed | None] = ContextVar("company_search_turn_embed", default=None)


def _key(org: str | None, words: str) -> tuple[str, str]:
    return (str(org or ""), " ".join(str(words or "").split()))


def begin_turn() -> None:
    """Open a fresh store for the current request."""
    _TURN.set({})
    _EMBED.set(None)


def recall(org: str | None, words: str) -> list[float] | None:
    """The vector kept for *words* in this turn, or ``None``."""
    store = _TURN.get()
    return None if store is None else store.get(_key(org, words))


def remember(org: str | None, words: str, vector: list[float] | None) -> None:
    """Keep *vector* for the rest of this turn. No turn, or no vector: no-op."""
    store = _TURN.get()
    if store is not None and vector is not None:
        store[_key(org, words)] = vector


def start(
    org: str | None, words: str, make: Callable[[], Awaitable[list[float] | None]],
    *, budget_s: float,
) -> None:
    """Start the one embed of this turn as a task, with *budget_s* seconds.

    *make* is the call of the door. It runs in a task, so the route goes on
    at once. A timeout or an error is an answer, and the task never raises.
    The caller checks the gates first: this function checks none.
    """
    state = _Embed(key=_key(org, words), began=time.monotonic())

    async def _run() -> list[float] | None:
        try:
            vector = await asyncio.wait_for(make(), budget_s)
        except TimeoutError:
            state.result = "timed_out"
            return None
        except Exception:
            state.result = "failed"
            return None
        finally:
            state.ms = round((time.monotonic() - state.began) * 1000)
        state.result = "ok" if vector else "failed"
        return vector or None

    state.task = asyncio.get_running_loop().create_task(_run())
    _EMBED.set(state)


def started(org: str | None, words: str) -> bool:
    """True when this turn started the embed of *words* for *org*."""
    state = _EMBED.get()
    return state is not None and state.task is not None and state.key == _key(org, words)


async def wait(org: str | None, words: str, budget_s: float | None = None) -> list[float] | None:
    """The vector of the turn's task, or ``None`` after *budget_s* seconds.

    With *budget_s* ``None``, the caller's own deadline bounds the wait. The
    wait shields the task, so a source that gives up never cancels it.
    """
    state = _EMBED.get()
    if state is None or state.task is None or state.key != _key(org, words):
        return None
    try:
        if budget_s is None:
            vector = await asyncio.shield(state.task)
        else:
            vector = await asyncio.wait_for(asyncio.shield(state.task), max(0.0, budget_s))
    except TimeoutError:
        state.late = True
        return None
    except asyncio.CancelledError:
        # The caller's own deadline: the source gave up before the vector came.
        if not state.task.done():
            state.late = True
        raise
    if vector is not None:
        state.used = True
    return vector


def outcome() -> str:
    """What became of the turn's embed: one of :data:`MEANINGS`.

    ``off``: no task started. ``ran``: a source took the vector, or the
    vector came and no source needed it. ``timed_out``: a source stopped its
    wait first, or the task ran out of time or still runs. ``failed``: the
    door gave no vector.
    """
    state = _EMBED.get()
    if state is None or state.task is None:
        return MEANING_OFF
    if state.used:
        return MEANING_RAN
    if state.late:
        return MEANING_TIMED_OUT
    if state.result == "failed":
        return MEANING_FAILED
    if state.result == "ok":
        return MEANING_RAN
    return MEANING_TIMED_OUT


def embed_ms() -> int | None:
    """The milliseconds of the turn's embed, or until now while it runs."""
    state = _EMBED.get()
    if state is None:
        return None
    if state.ms is not None:
        return state.ms
    return round((time.monotonic() - state.began) * 1000)


def end_turn() -> None:
    """Cancel the turn's embed task when it still runs. It never raises."""
    state = _EMBED.get()
    if state is not None and state.task is not None and not state.task.done():
        state.task.cancel()
