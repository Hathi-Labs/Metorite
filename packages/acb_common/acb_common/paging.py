"""One page of a list for an agent's read tool, and the line that says what is left.

Owner report, 2026-10-14. The orchestrator asked the Task manager for items 31
to 35 of a 35-item inbox. ``my_tasks_list`` loaded all 35, printed 30, and had
no way to show the other 5. The data was out of reach, and the tool did not
say so.

The contract every agent-callable read holds:

1. **A page per call.** A read returns at most ``limit`` items, so 500 rows
   never flood the model's context. The default stays at the tool's old size,
   and :data:`PAGE_MAX` bounds what the model may ask for.
2. **A count and a hint.** A result that does not hold everything says how
   many items exist and how to get the next page, in one line (:func:`page_line`).
3. **No silent cut.** A tool never writes ``rows[:30]`` on its result. It calls
   :func:`paged`. ``tests/unit/test_read_tool_paging.py`` fails on a new slice.

Where the backend route pages (``page``/``page_size``, ``limit``/``offset`` or a
cursor), the tool passes the page through and calls :func:`page_line` with the
route's total. Where the route returns everything, the tool slices here.

It lives in ``acb_common``, not ``acb_skills``, because the chat skills
(``skill_my_tasks``, ``skill_projects``) declare ``acb-common`` only, and
``acb-skills`` pulls in the agent framework. ``acb_common.priority`` is in
the same place for the same reason. One helper, one hint: a tool that writes
its own "showing N of M" line is a second way of doing this.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

#: The largest page a model may ask for. A larger ``limit`` is cut to this.
PAGE_MAX = 100


class Page(NamedTuple):
    """One page of a list: its items, the full count, and the hint line."""

    items: list[Any]
    total: int
    offset: int
    #: "" when the page holds the whole list, else one line for the model.
    line: str


def bounds(offset: Any, limit: Any, *, default: int,
           maximum: int = PAGE_MAX) -> tuple[int, int]:
    """``(offset, limit)``, made safe. A missing or bad ``limit`` is the default."""
    try:
        start = max(0, int(offset or 0))
    except (TypeError, ValueError):
        start = 0
    try:
        size = int(limit or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        size = default
    return start, min(size, maximum)


def page_line(total: int | None, offset: int, shown: int, *,
              more: bool | None = None, cursor: str = "",
              noun: str = "items", param: str = "offset",
              page_size: int = 0) -> str:
    """The one line that says what a page left out, or "" when it left nothing.

    ``total`` is the full count, or None when the backend does not give one.
    Then ``more`` (or a ``cursor``) says whether another page exists. With a
    ``cursor`` the hint names it instead of an offset. ``param`` is the
    name of the tool's offset argument, when it is not ``offset``. A tool
    whose argument is a page number passes ``page_size``, and the hint then
    names ``page=N``.
    """

    def _next(end: int) -> str:
        if cursor:
            return f"cursor={cursor!r}"
        if page_size > 0:
            return f"page={end // page_size + 1}"
        return f"{param}={end}"

    end = offset + shown
    if total is not None:
        if total > 0 and offset >= total and page_size > 0:
            last = -(-total // page_size)
            return (f"Nothing on page {offset // page_size + 1}. There are "
                    f"{total} {noun}, so the last page is {last}.")
        if total > 0 and offset >= total:
            return (f"Nothing at {param}={offset}. There are {total} {noun}, "
                    f"so start below {param}={total}.")
        if end >= total:
            return (f"Showing {offset + 1}-{end} of {total}. This is the last page."
                    if offset > 0 and shown else "")
        nxt = _next(end)
        return (f"Showing {offset + 1}-{end} of {total}. "
                f"Call again with {nxt} for the rest.")
    if not (more or cursor):
        if offset > 0 and not shown:
            return f"Nothing at {param}={offset}. This is past the end."
        return (f"Showing {offset + 1}-{end}. This is the last page."
                if offset > 0 else "")
    nxt = _next(end)
    return (f"Showing {offset + 1}-{end}. More {noun} exist. "
            f"Call again with {nxt} for the rest.")


def paged(items: Sequence[Any], offset: Any = 0, limit: Any = 0, *,
          default: int, maximum: int = PAGE_MAX, noun: str = "items",
          param: str = "offset", page_size: int = 0) -> Page:
    """Slice ``items`` to one page, and write the line that says what is left."""
    start, size = bounds(offset, limit, default=default, maximum=maximum)
    rows = list(items)
    chunk = rows[start:start + size]
    return Page(chunk, len(rows), start,
                page_line(len(rows), start, len(chunk), noun=noun, param=param,
                          page_size=page_size))


def prefix_limit(offset: Any, limit: Any, *, default: int, cap: int,
                 maximum: int = PAGE_MAX) -> tuple[int, int, int]:
    """``(start, size, ask)`` for a backend that has a LIMIT and no OFFSET.

    The tool asks for ``ask`` rows (one more than the page needs, so it can
    tell that more exist), and :func:`prefix_page` cuts the page from them.
    ``cap`` is the backend's own largest LIMIT.
    """
    start, size = bounds(offset, limit, default=default, maximum=maximum)
    return start, size, max(1, min(start + size + 1, cap))


def prefix_page(rows: Sequence[Any], start: int, size: int, *, cap: int,
                asked: int = 0, noun: str = "items", page_size: int = 0,
                narrow: str = "", rest: str = "") -> Page:
    """One page from the first rows of a LIMIT-only backend (:func:`prefix_limit`).

    ``asked`` is the LIMIT the tool sent, and ``cap`` by default. Fewer rows
    than that means the backend sent every row, so the line has the exact
    total. A full answer means that more exist.

    Past ``cap`` the backend returns nothing. That is the one cut this module
    cannot page through, so the line says so instead of "the last page", and
    is never silent. ``narrow`` is what to add when the tool HAS a filter that
    reaches past the cap (for example "Use more exact words to reach them.").
    A tool with no filter passes nothing. ``rest`` names what lies past the
    cap, "Older <noun>" by default. A search ranked by relevance passes
    "Other matches".
    """
    got = list(rows)
    chunk = got[start:start + size]
    limit = asked or cap
    if len(got) < limit:
        return Page(chunk, len(got), start, page_line(
            len(got), start, len(chunk), noun=noun, page_size=page_size))
    more = len(got) > start + size
    if more or limit < cap:
        return Page(chunk, len(got), start, page_line(
            None, start, len(chunk), more=more, noun=noun, page_size=page_size))
    end = start + len(chunk)
    shown = f"Showing {start + 1}-{end}. " if chunk else f"Nothing past {cap} {noun}. "
    line = f"{shown}{rest or 'Older ' + noun} are past the source's limit of {cap}."
    return Page(chunk, len(got), start, f"{line} {narrow}".strip())
