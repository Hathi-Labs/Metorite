"""NS-4a, R8: GET /shell/search on a REAL Postgres (navigation_shell.md NS-4a, done-when 4).

The shell route adds no SQL. It calls each app's own search. So this runs the
WHOLE route, the shell's gate and mapping included, over the seeded rows of
`test_projects_search_r8.py`: org A holds "Calibrate the extruder", and org B
holds "Beta secret". A member of org A finds the first through the bar, and
never the second.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.projects import search as projects_route
from gateway.routes.shell import search as shell

# The seed, its URL guard and its helpers, shared rather than copied.
from tests.unit.test_projects_search_r8 import (  # noqa: F401
    ACTOR,
    _async_url,
    _visibility,
    pytestmark,
    seeded,
)


async def _shell(seeded, monkeypatch, q: str) -> dict:  # noqa: F811
    # Room for a cold database. The log checks prove the provider finished.
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 60.0)
    from contextlib import asynccontextmanager

    from acb_auth import UserContext, UserRole, build_access
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)

    @asynccontextmanager
    async def _session(*_a, **_k):
        async with eng.connect() as conn:
            yield conn

    async def _vis(_db, _user):
        return _visibility(seeded["org_a"])

    monkeypatch.setattr(projects_route, "_tenant_session", _session)
    monkeypatch.setattr(projects_route, "resolve_visibility", _vis)
    user = UserContext(email=ACTOR, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))
    try:
        return await shell.shell_search(q=q, scope="/projects", user=user)
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_the_bar_finds_this_orgs_task_by_its_words(seeded, monkeypatch):  # noqa: F811
    body = await _shell(seeded, monkeypatch, "extruder")
    groups = {g["app"]: g for g in body["groups"]}
    assert list(groups) == ["tasks"], "only the app the member holds answers"
    items = groups["tasks"]["items"]
    assert [i["title"] for i in items] == ["Calibrate the extruder"]
    task_id = seeded["tasks"][("org_a", 9)]
    assert items[0]["href"] == f"/projects?task={task_id}"
    assert items[0]["hint"].startswith("Task · search-r8-")


@pytest.mark.asyncio
async def test_the_bar_never_returns_another_orgs_task(seeded, monkeypatch, caplog):  # noqa: F811
    # ⚠️ A provider that FAILED also returns no group, so an empty answer
    # alone would pass on a broken build. The log says whether it ran clean.
    import logging

    with caplog.at_level(logging.INFO, logger=shell.__name__):
        body = await _shell(seeded, monkeypatch, "Beta secret")
    assert body["groups"] == []
    assert not [r for r in caplog.records if "provider failed" in r.getMessage()]
    assert not [r for r in caplog.records if "timed out" in r.getMessage()]
