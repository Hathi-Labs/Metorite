"""GET /projects/search/refs, the parts that need no database.

Owner report, 2026-10-13: a bare ``#171`` in a chat answer must open the task.
The route and its real SQL are proven on Postgres in
``test_projects_task_refs_r8.py``. This file holds the input rule and the
route's registration.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.projects import search as route
from gateway.routes.projects.core import router


def test_numbers_are_parsed_by_the_one_task_number_rule() -> None:
    assert route.ref_numbers("171,#87, 171 ,abc,,0,²,12345678901234567890") == [87, 171]


def test_an_empty_list_is_no_numbers() -> None:
    assert route.ref_numbers("") == []
    assert route.ref_numbers(",, ,") == []


def test_more_than_the_cap_is_a_422() -> None:
    route.ref_numbers(",".join(str(n) for n in range(1, route.MAX_REFS + 1)))
    with pytest.raises(HTTPException) as err:
        route.ref_numbers(",".join(str(n) for n in range(1, route.MAX_REFS + 2)))
    assert err.value.status_code == 422


async def test_no_numbers_runs_no_query(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise AssertionError("no session for an empty lookup")

    monkeypatch.setattr(route, "_tenant_session", boom)
    assert await route.task_refs(numbers="abc", user=None) == {"rows": []}  # type: ignore[arg-type]


def test_the_route_is_registered_under_projects() -> None:
    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/projects/search/refs" in paths


def test_the_read_uses_the_shared_visibility_and_triage_rules() -> None:
    src = route._REFS_SQL
    assert "{visible}" in src and "{triage}" in src
    assert "PARTITION BY t.task_number" in src
