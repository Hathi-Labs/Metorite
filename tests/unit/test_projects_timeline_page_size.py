"""The browser must not ask for a page the gateway will refuse.

🔴 **This exists because that shipped, and nothing saw it.**

The task panel's first draft asked for `page_size=200`. `core.py::MAX_PAGE_SIZE`
is 100 and ``Page`` binds ``page_size`` as ``Query(50, ge=1, le=MAX_PAGE_SIZE)``,
so **FastAPI refuses the request before the handler runs**. The panel does not
receive a shortened list. It receives a 422, both of its lists stay empty, and
it draws *"No comments yet"* over a task full of comments. Posting made it
worse: the POST landed, the re-read 422'd, and the author saw an error instead
of the comment they had just written.

⚠️ **Why the two suites that should have caught it did not**, because that is
the part worth keeping:

* ``tests/live/live_comment_threads.py`` builds ``pm_core.Page(page=1,
  page_size=50)`` by hand and calls the handler directly. Calling a handler
  never runs the validation FastAPI does on the way in, so a live harness
  against a real database still says nothing about the query string.
* The visual rig stubs ``/api/**`` and answers 200 whatever the query holds.
  Its own header says it stubs the API. This is the class of defect that
  admission covers.

So the gap is not a missing assertion in either file. It is that **no test
crossed the boundary where the number is checked**, and the number lives in two
languages. This reads both and compares them, which is the only fence that
spans the pair.

R7: this file is that fence. R5: nothing here touches a database.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "workbench/control_plane/src/app/projects/lib/api.ts"
PANEL = (
    REPO / "workbench/control_plane/src/app/projects/components/TaskPanel.tsx"
)


def _numbers_in(line: str) -> list[int]:
    """Every integer on a line, for a line already known to set a page size."""
    return [int(n) for n in re.findall(r"[0-9]+", line)]


def _page_size_numbers(path: Path) -> list[tuple[int, str]]:
    """(number, line) for every integer on a line that names a page size."""
    out: list[tuple[int, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        # Skip prose. The doc comments here quote the failure at length, and
        # a fence that reads its own documentation reports the bug for ever.
        if line.startswith(("*", "//", "/*")):
            continue
        if "pageSize" not in line and "page_size" not in line:
            continue
        out.extend((n, line) for n in _numbers_in(line))
    return out


def _max_page_size() -> int:
    from gateway.routes.projects.core import MAX_PAGE_SIZE

    return int(MAX_PAGE_SIZE)


def test_the_client_mirrors_the_gateways_cap() -> None:
    """`MAX_TIMELINE_PAGE` in TypeScript equals `MAX_PAGE_SIZE` in Python.

    A mirror, and it is allowed to be one only because this test exists. The
    alternative — serving the number from an endpoint — costs a round trip on
    every panel open to learn a constant.
    """
    source = CLIENT.read_text(encoding="utf-8")
    found = re.search(r"MAX_TIMELINE_PAGE:\s*(\d+)", source)
    assert found is not None, (
        "api.ts no longer declares MAX_TIMELINE_PAGE. If the panel went back "
        "to a bare number, this fence is blind again."
    )
    assert int(found.group(1)) <= _max_page_size()


def test_no_caller_asks_for_more_than_the_cap() -> None:
    r"""Every page-size number in the Projects client is servable.

    🔴 **The first version of this test passed on the very value it was
    written to reject**, and that is worth more than the test is. It matched
    ``pageSize:\s*(\d+)``, and the real line reads::

        pageSize: deep ? projectsApi.MAX_TIMELINE_PAGE : 50,

    — a TERNARY, so no digit follows the colon and the pattern found
    nothing. The defect it exists to stop was written in exactly that shape.
    A fence that passes on its own counterexample is worse than no fence,
    because it is counted.

    So this now reads EVERY integer on any line that mentions a page size,
    whatever the expression around it. It over-reads on purpose: a bare `50`
    in a ternary is checked too, which costs nothing and cannot go blind.
    """
    cap = _max_page_size()
    offenders: list[str] = []
    for path in (CLIENT, PANEL):
        for number, line in _page_size_numbers(path):
            if number > cap:
                offenders.append(f"{path.name}: {line}")
    assert not offenders, (
        f"asking for more than MAX_PAGE_SIZE={cap} is a 422, and a 422 on a "
        f"timeline read EMPTIES the panel rather than shortening it: {offenders}"
    )


def test_this_fence_can_actually_fail() -> None:
    """The counterexample, run against the reader rather than trusted.

    ⚠️ Pinning the first version's blind spot. Both shapes below are the
    defect; a reader that sees only the first is the one that shipped.
    """
    plain = _numbers_in("      projectsApi.timeline(id, { pageSize: 200 }),")
    ternary = _numbers_in("          pageSize: deep ? 200 : 50,")
    assert plain == [200]
    assert 200 in ternary


def test_the_cap_is_what_the_dependency_actually_binds() -> None:
    """The Python half, read from the source rather than assumed.

    `MAX_PAGE_SIZE` existing is not the claim. The claim is that `Page` USES
    it as the ceiling — a `Page` that hard-coded a different number would make
    the constant, and therefore this whole file, decorative.
    """
    core = (
        REPO / "apps/services/gateway/gateway/routes/projects/core.py"
    ).read_text(encoding="utf-8")
    assert "page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE)" in core


def test_the_timeline_read_goes_through_the_shared_page_dependency() -> None:
    """`get_timeline` must not grow a page ceiling of its own.

    If it ever needs a larger one, it moves `MAX_PAGE_SIZE` — a second
    ceiling on one route is how the client learns to guess which limit
    applies where.
    """
    activities = (
        REPO / "apps/services/gateway/gateway/routes/projects/activities.py"
    ).read_text(encoding="utf-8")
    body = activities[activities.index("async def get_timeline"):]
    body = body[: body.index("@router.post")]
    assert "page: Page = Depends()" in body
    assert "page_size" not in body, "a second ceiling on this one route"
