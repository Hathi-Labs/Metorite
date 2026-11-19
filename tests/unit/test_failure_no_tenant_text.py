"""P1 — a platform row and a task hold no organization id and no tenant text.

Spec: ``project-docs/specs/tool_failure_log.md`` §9 P1, §7.3 and §10.1 (D225).
Code: ``acb_common.failure_log`` (the write seam) and
``gateway/failures/sync.py`` (the task seam).

A real database (R8): the promoted phase-4 catalog, as the role
``acb_app_h3rls``, which RLS binds. Org B is a customer, and org A is the
internal org. The failures of each org carry its own marker strings in the
exception message, the arguments of the failing function, the tool name, the
MCP server name and a path parameter of the route. Each org id is a marker
too. The write seam records them, and the
sync job of the internal org A makes the tasks. Then no marker is in a
column of a platform table, in a task or in a comment of the job.

The test proves that it can see: each tool and server marker IS in the
tenant column ``failure_events.tool_name`` of its org, where §5.1 allows it.

Run (a private ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_failure_no_tenant_text.py -v -rs

Without the database the test SKIPS. A skip is not a pass.

**Mutation (R7).** P1-M1: the normaliser admits a route path that is not a
declared template (drop ``path in route_paths`` in
``failure_fingerprint``). The marker of the path then reaches the platform
row and the task title, and this test goes red (run on 2026-11-19). The
task text reads no tenant row, so no mutation of ``sync.py`` alone can put
a marker there.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import failure_log as fl
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from gateway import build_info
from gateway.failures import sync
from sqlalchemy import text

from tests.unit import _github_r8 as g
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_failure_store_r8 import _migration
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_tool_failure_tasks_r8 import RUN_AS, seed_failure_project

pytestmark = _DB_GATE

def _markers() -> dict[str, str]:
    """One marker for each place that §9 P1 names, unique to one org."""
    tag = uuid.uuid4().hex[:10]
    return {k: f"{k}mark{tag}" for k in ("msg", "arg", "tool", "server", "path")}


def _crm_get_record(msg: str, record: str) -> None:
    raise KeyError(f"Amount for {msg} in {record}")


def _record(m: dict[str, str]) -> None:
    """Four failures of the bound org, each with its markers."""
    try:
        _crm_get_record(m["msg"], m["arg"])
    except KeyError as exc:  # a chat tool, a code of ours
        fl.record_tool_failure(exc, source="chat_tool", error_code="exception",
                               tool="crm_get_record", surface="chat")
        fl.record_tool_failure(exc, source="chat_tool", error_code="exception",
                               tool=m["tool"], surface="chat")
    try:
        raise TimeoutError(m["msg"])
    except TimeoutError as exc:  # an outside MCP server
        fl.record_tool_failure(exc, source="mcp_call", error_code="mcp_timeout",
                               tool=f"{m['server']}.{m['tool']}", kind="mcp", surface="chat")
    try:
        _crm_get_record(m["msg"], m["arg"])
    except KeyError as exc:  # a door 5xx on a concrete path
        fl.record_tool_failure(exc, source="door_5xx", error_code="unhandled_500",
                               tool=f"GET /items/{m['path']}", kind="route",
                               route_paths={"/items/{item_id}"}, surface="app")


def test_p1_no_marker_reaches_a_platform_row_or_a_task(promoted, app_engine,  # noqa: F811
                                                        monkeypatch) -> None:
    p = promoted
    body = _migration().replace("acb_app", _APP_ROLE)
    with p.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(body)
    project, _ = seed_failure_project(p, p.org_a)
    s = get_settings()
    # Org B is a customer. Org A is the internal org, and its own failures
    # carry markers too (review F4): the job reads them as counts only.
    marks = {p.org_b: _markers(), p.org_a: _markers()}
    for name, value in (("tool_failure_log_orgs", f"{p.org_b},{p.org_a}"),
                        ("tool_failure_tasks", True),
                        ("tool_failure_org_id", p.org_a), ("tool_failure_project_id", project),
                        ("tool_failure_run_as", RUN_AS)):
        monkeypatch.setattr(s, name, value)
    monkeypatch.setattr(build_info, "build_committed_at", lambda sha: None)

    class Down:
        async def incr(self, *a: Any, **k: Any) -> int:
            raise ConnectionError("no redis in this suite")

        expire = incr

    saved = fl.builtin_names()
    fl._reset_for_tests()
    monkeypatch.setattr(fl, "_redis", lambda: Down())
    monkeypatch.setattr(fl, "WRITE_TIMEOUT_S", 30.0)
    fl.set_builtin_names({"crm_get_record"})
    fl.set_release("abc1234")
    cfg = sync.config()
    assert cfg is not None

    async def go() -> None:
        async with tenant_engine_scope(g.app_dsn(p)):
            for org, m in marks.items():
                token = bind_tenant(org)
                try:
                    _record(m)
                finally:
                    release_tenant(token)
            assert await fl.flush() == 8
            await fl.stop_drain()
            await sync.sync(cfg, now=datetime.now(UTC), running=None)

    try:
        asyncio.run(go())
    finally:
        fl._reset_for_tests()
        fl.set_builtin_names(saved)

    markers = [v for m in marks.values() for v in m.values()] + [p.org_b, p.org_a]
    # The fence can see: the tenant rows of each org hold its names, as §5.1
    # allows.
    for org, m in marks.items():
        names = " ".join(str(r[0]) for r in g.rows(
            p, "SELECT tool_name FROM failure_events "
               "WHERE organization_id = CAST(:o AS uuid)", o=org))
        assert m["tool"] in names and m["server"] in names, org
    platform = " ".join(str(r[0]) for t in ("failure_fingerprints", "failure_daily")
                        for r in g.rows(p, f"SELECT row_to_json(x)::text FROM {t} x"))
    assert "crm_get_record" in platform and "route:other" in platform
    tasks = g.rows(p, "SELECT id, title, description FROM pm_tasks "
                      "WHERE project_id = CAST(:proj AS uuid)", proj=project)
    assert len(tasks) == 3, [t.title for t in tasks]  # two owned codes, one `other`
    text_of_tasks = " ".join(f"{t.title} {t.description}" for t in tasks)
    comments = " ".join(str(r[0]) for r in g.rows(
        p, "SELECT body FROM pm_activities WHERE task_id = ANY(CAST(:ids AS uuid[]))",
        ids=[str(t.id) for t in tasks]))
    for marker in markers:
        assert marker not in platform, marker
        assert marker not in text_of_tasks, marker
        assert marker not in comments, marker
    with p.admin_engine.begin() as c:
        for table in ("failure_config", "failure_events", "failure_org_days",
                      "failure_daily", "failure_fingerprints"):
            c.execute(text(f"DELETE FROM {table}"))
