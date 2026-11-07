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
"""

from __future__ import annotations

from contextvars import ContextVar

_TURN: ContextVar[dict[tuple[str, str], list[float]] | None] = ContextVar(
    "company_search_turn_vectors", default=None,
)


def _key(org: str | None, words: str) -> tuple[str, str]:
    return (str(org or ""), " ".join(str(words or "").split()))


def begin_turn() -> None:
    """Open a fresh store for the current request."""
    _TURN.set({})


def recall(org: str | None, words: str) -> list[float] | None:
    """The vector kept for *words* in this turn, or ``None``."""
    store = _TURN.get()
    return None if store is None else store.get(_key(org, words))


def remember(org: str | None, words: str, vector: list[float] | None) -> None:
    """Keep *vector* for the rest of this turn. No turn, or no vector: no-op."""
    store = _TURN.get()
    if store is not None and vector is not None:
        store[_key(org, words)] = vector
