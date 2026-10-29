"""Task #986 — the agent strip's focus is a WHERE clause of the list (R8).

The Projects board read page 1 of ``GET /projects/tasks`` and filtered those
100 rows in the browser, so a strip that read "2 Working" drew one card while
the bar read "100 of 801". ``list_tasks`` now takes ``agent_focus`` and
``agent_worked``, and ``agent_summary`` counts through the SAME predicate
(``filters.agent_focus_clause``).

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role. One project holds more than a page of tasks, and the
tasks with working runs are the OLDEST, so they lie beyond page 1.

R7, the mutations (each turns a test here red):

* **M1** the route drops the ``agent_focus`` clause -> the working test.
* **M2** ``waiting`` drops the member arm -> the waiting test.
* **M3** the list reads a copy of the predicate, not the helper -> the
  list-equals-strip assertions.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

A_EMAIL = "af986-a@focus-r8.test"
B_EMAIL = "af986-b@focus-r8.test"
OTHER = "af986-other@focus-r8.test"
FILLER = 130  # more than one page of 100


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = b._principal(A_EMAIL, promoted.org_a)
    ns.b = b._principal(B_EMAIL, promoted.org_b)
    ns.other = b._principal(OTHER, promoted.org_a)
    b.member(promoted, promoted.org_a, OTHER)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    b.clear(world.p)
    world.proj = b.project(world.p, world.p.org_a, A_EMAIL)
    return world


def _old(w: Any, *ids: str) -> None:
    """Make the tasks the OLDEST, so the default order puts them after page 1."""
    with w.p.admin_engine.begin() as c:
        for tid in ids:
            c.execute(text("UPDATE pm_tasks SET created_at = now() - interval '30 days' "
                           "WHERE id = CAST(:t AS uuid)"), {"t": tid})


def _filler(w: Any) -> None:
    for _ in range(FILLER):
        b.task(w.p, w.p.org_a, A_EMAIL, w.proj)


async def _list(w: Any, user: Any, query: str) -> tuple[int, Any]:
    async with b._session(w.p) as call:
        got = await call(user, "GET", f"/projects/tasks?project_id={w.proj['id']}"
                                      f"&include_subtree=true&page_size=100&{query}")
    return got.status_code, got.json()


async def _strip(w: Any, user: Any) -> dict[str, int]:
    async with b._session(w.p) as call:
        got = await call(user, "GET", f"/projects/{w.proj['id']}/agent-summary")
    assert got.status_code == 200, got.text
    return got.json()


@_DB_GATE
class TestTheFocusIsAWhereClause:
    async def test_working_tasks_beyond_page_one_come_back_and_equal_the_strip(self, w):
        org = w.p.org_a
        one = b.task(w.p, org, A_EMAIL, w.proj)
        two = b.task(w.p, org, A_EMAIL, w.proj)
        b.run(w.p, org, one, A_EMAIL, "in_progress")
        b.run(w.p, org, two, A_EMAIL, "in_progress")
        _old(w, one, two)
        _filler(w)
        # The unfiltered page 1 holds neither of them: the defect.
        status, plain = await _list(w, w.a, "")
        assert status == 200 and plain["total"] == FILLER + 2
        assert not {one, two} & {r["id"] for r in plain["rows"]}
        status, got = await _list(w, w.a, "agent_focus=working&with_status_counts=true")
        assert status == 200, got
        assert {r["id"] for r in got["rows"]} == {one, two}
        assert got["total"] == 2
        assert sum(got["status_counts"].values()) == 2
        assert (await _strip(w, w.a))["working"] == got["total"]

    async def test_every_focus_total_equals_its_strip_count(self, w):
        org = w.p.org_a
        queued, awaiting, review, merged = (b.task(w.p, org, A_EMAIL, w.proj) for _ in range(4))
        b.run(w.p, org, queued, A_EMAIL, "created")
        b.run(w.p, org, awaiting, A_EMAIL, "awaiting")
        b.ref(w.p, org, review, 1, "open")
        b.ref(w.p, org, merged, 2, "merged", merged="now() - interval '1 day'")
        _old(w, queued, awaiting, review, merged)
        _filler(w)
        strip = await _strip(w, w.a)
        for focus, field in (("queued", "queued"), ("waiting", "waiting_on_you"),
                             ("review", "in_review"), ("merged", "merged_this_week")):
            status, got = await _list(w, w.a, f"agent_focus={focus}")
            assert status == 200, got
            assert got["total"] == strip[field] == 1, focus
            assert len(got["rows"]) == 1

    async def test_waiting_is_the_callers_run_and_not_another_members(self, w):
        org = w.p.org_a
        mine, theirs = b.task(w.p, org, A_EMAIL, w.proj), b.task(w.p, org, A_EMAIL, w.proj)
        b.run(w.p, org, mine, A_EMAIL, "awaiting")
        b.run(w.p, org, theirs, OTHER, "awaiting")
        _old(w, mine, theirs)
        _filler(w)
        _, got = await _list(w, w.a, "agent_focus=waiting")
        assert [r["id"] for r in got["rows"]] == [mine] and got["total"] == 1
        _, got = await _list(w, w.other, "agent_focus=waiting")
        assert [r["id"] for r in got["rows"]] == [theirs]
        # A name in the request is not a parameter: the caller is the session's.
        _, got = await _list(w, w.a, f"agent_focus=waiting&af_me={OTHER}&me={OTHER}")
        assert [r["id"] for r in got["rows"]] == [mine]

    async def test_agent_worked_filters_the_rows_and_the_total(self, w):
        org = w.p.org_a
        worked = b.task(w.p, org, A_EMAIL, w.proj)
        b.run(w.p, org, worked, A_EMAIL, "completed")
        _old(w, worked)
        _filler(w)
        status, got = await _list(w, w.a, "agent_worked=true")
        assert status == 200, got
        assert [r["id"] for r in got["rows"]] == [worked] and got["total"] == 1

    async def test_the_runs_flag_off_keeps_no_run_focus_but_keeps_review(self, w, monkeypatch):
        from acb_common import get_settings

        org = w.p.org_a
        t, pr = b.task(w.p, org, A_EMAIL, w.proj), b.task(w.p, org, A_EMAIL, w.proj)
        b.run(w.p, org, t, A_EMAIL, "in_progress")
        b.ref(w.p, org, pr, 1, "open")
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        _, got = await _list(w, w.a, "agent_focus=working")
        assert got["total"] == 0 and got["rows"] == []
        _, got = await _list(w, w.a, "agent_focus=review")
        assert got["total"] == 1

    async def test_another_org_sees_nothing(self, w):
        org = w.p.org_a
        t = b.task(w.p, org, A_EMAIL, w.proj)
        b.run(w.p, org, t, A_EMAIL, "in_progress")
        own = b.project(w.p, w.p.org_b, B_EMAIL)
        async with b._session(w.p) as call:
            got = await call(w.b, "GET", f"/projects/tasks?project_id={own['id']}"
                                         "&agent_focus=working")
            assert got.status_code == 200 and got.json()["total"] == 0
            named = await call(w.b, "GET", f"/projects/tasks?project_id={w.proj['id']}"
                                           "&agent_focus=working")
        assert named.status_code == 404

    async def test_an_unknown_focus_is_422(self, w):
        status, body = await _list(w, w.a, "agent_focus=nonsense")
        assert status == 422
        assert "waiting" in str(body)
