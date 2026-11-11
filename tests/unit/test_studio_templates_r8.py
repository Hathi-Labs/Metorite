"""The one-step template install of AI Studio on a real database (STU-K1a, R8).

Spec: ``project-docs/specs/ai_studio.md`` section 14, row K1. Code: the routes
``POST /studio/packages/from-template`` and ``GET /studio/templates`` in
``gateway/routes/studio/packages.py``, over ``acb_common/studio_templates``.
The hermetic half is ``test_studio_templates.py``.

The real handlers run on a phase-4 promoted scratch database, as the app role
with no BYPASSRLS, so FORCE RLS binds. Each call binds its org as the auth
layer does. Two organizations, and two members in org A.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_templates_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**Acceptance (K1a).** One call makes a private package of the member, publishes
it as version 1, writes the object rows, and keeps the private install of the
member. A second call for the same member and template is 409
``package_exists`` (the answer of the create route) and writes nothing. Another
member and org B see nothing.

**The mutations (R7).** M1: in ``create_from_template`` skip ``_publish_draft``.
The publish test goes red. M2: take the member from the body. The isolation
test goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common import studio_templates as reg
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.routes.studio import packages
from gateway.studio import objects_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuk1a.test"
BEN = f"ben.{TAG}@stuk1a.test"
ZED = f"zed.{TAG}@stuk1a.test"
TEMPLATE = "field-visits"


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


class _Request:
    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/packages/from-template")

    async def stream(self) -> Any:
        yield self._raw


async def _call(route: str, org: str, email: str, payload: Any = None,
                pid: str | None = None) -> tuple[int, Any]:
    kwargs: dict[str, Any] = {"user": _user(org, email)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route == "create_from_template":
        kwargs["request"] = _Request(payload)
    token = bind_tenant(org)
    try:
        return 200, await getattr(packages, route)(**kwargs)
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for org A. Ana and
    Ben are active members of org A, and Zed is a member of org B. Every package has a slug that starts with
    ``field-visits-``, so the teardown removes the packages of the two members."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", p.org_a)
    with p.admin_engine.begin() as c:
        for email, org in ((ANA, p.org_a), (BEN, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE owner_email = ANY(:o)"), {"o": [ANA, BEN, ZED]}).all()]
        for sql in ("DELETE FROM package_install WHERE app_id = ANY(CAST(:ids AS uuid[]))",
                    "DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))",
                    "DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))",
                    "DELETE FROM app_versions WHERE app_id = ANY(CAST(:ids AS uuid[]))",
                    "DELETE FROM apps WHERE id = ANY(CAST(:ids AS uuid[]))"):
            c.execute(text(sql), {"ids": ids})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:o)"), {"o": [ANA, BEN, ZED]})


def _apps(world: Any) -> list[tuple[Any, ...]]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT id::text, owner_email, organization_id::text, visibility, status, "
            "live_version FROM apps WHERE owner_email = ANY(:o) ORDER BY created_at"),
            {"o": [ANA, BEN, ZED]}).all()]


def _installs(world: Any, pid: str) -> list[tuple[Any, ...]]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, version, scope_kind, scope_subject, installed_by "
            "FROM package_install WHERE app_id = CAST(:p AS uuid)"), {"p": pid}).all()]


def _objects(world: Any, pid: str) -> list[str]:
    with world.p.admin_engine.connect() as c:
        return [str(r[0]) for r in c.execute(text(
            "SELECT name FROM studio_objects WHERE package_id = CAST(:p AS uuid)"),
            {"p": pid}).all()]


def test_r8_one_call_makes_a_private_published_package_and_install(world) -> None:
    status, got = _run(world, lambda: _call("create_from_template", world.a, ANA,
                                            {"template": TEMPLATE}))
    assert status == 200, got
    pid = got["package"]["id"]
    assert got["package"]["visibility"] == "private"
    assert got["version"]["version"] == 1
    assert [o["name"] for o in got["objects"]] == ["visit"]
    (app,) = _apps(world)
    assert app[:5] == (pid, ANA, world.a, "private", "live")
    assert app[5] == 1
    assert _objects(world, pid) == ["visit"]
    (inst,) = _installs(world, pid)
    assert inst[0] == world.a and inst[1] == 1
    assert inst[2] == "private" and inst[4] == ANA


def test_r8_the_objects_are_synced_so_a_record_can_be_made(world) -> None:
    status, got = _run(world, lambda: _call("create_from_template", world.a, ANA,
                                            {"template": TEMPLATE}))
    assert status == 200, got
    pid = got["package"]["id"]

    async def make() -> Any:
        async with tenant_session(world.a) as db:
            obj = await objects_store.get_object_by_name(db, "visit", package_id=pid)
            assert obj is not None
            return await objects_store.create_record(
                db, ANA, obj.id, {"status": "planned", "customer": {"id": "acme-1"}}, package_id=pid)

    rec = _run(world, make)
    assert rec is not None


def test_r8_another_member_and_org_b_see_nothing(world) -> None:
    status, got = _run(world, lambda: _call("create_from_template", world.a, ANA,
                                            {"template": TEMPLATE}))
    assert status == 200, got
    pid = got["package"]["id"]

    async def lists() -> Any:
        return (await _call("list_packages", world.a, BEN),
                await _call("list_packages", world.b, ZED),
                await _call("get_package", world.a, BEN, pid=pid),
                await _call("get_package", world.b, ZED, pid=pid))
    ben, org_b, ben_get, zed_get = _run(world, lists)
    assert ben_get[0] == 404 and zed_get[0] == 404
    assert ben[1]["packages"] == []
    assert org_b[1]["packages"] == []
    # Ben calls the route and gets his own package, never the one of Ana.
    status, mine = _run(world, lambda: _call("create_from_template", world.a, BEN,
                                             {"template": TEMPLATE}))
    assert status == 200 and mine["package"]["id"] != pid
    assert {i[4] for i in _installs(world, mine["package"]["id"])} == {BEN}
    assert {i[4] for i in _installs(world, pid)} == {ANA}


def test_r8_a_second_call_is_package_exists_and_writes_nothing(world) -> None:
    status, first = _run(world, lambda: _call("create_from_template", world.a, ANA,
                                              {"template": TEMPLATE}))
    assert status == 200, first
    before = _apps(world)
    status, got = _run(world, lambda: _call("create_from_template", world.a, ANA,
                                            {"template": TEMPLATE}))
    assert status == 409
    assert got["code"] == packages.PACKAGE_EXISTS
    assert got["package_id"] == first["package"]["id"]
    assert _apps(world) == before
    assert len(_installs(world, first["package"]["id"])) == 1


def test_r8_an_unknown_template_is_422_and_writes_nothing(world) -> None:
    for payload in ({"template": "nope"}, {}, {"template": ["field-visits"]}):
        status, got = _run(world, lambda p=payload: _call("create_from_template", world.a, ANA, p))
        assert status == 422, got
    assert _apps(world) == []


def test_r8_the_route_list_is_the_registry(world) -> None:
    status, got = _run(world, lambda: _call("list_app_templates", world.a, ANA))
    assert status == 200
    assert got["templates"] == [dict(t) for t in reg.list_templates()]


def test_r8_the_switch_off_is_404_and_writes_nothing(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    status, _ = _run(world, lambda: _call("create_from_template", world.a, ANA,
                                          {"template": TEMPLATE}))
    assert status == 404
    status, _ = _run(world, lambda: _call("list_app_templates", world.a, ANA))
    assert status == 404
    assert _apps(world) == []


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_studio_templates_r8\.py \\?$", workflow, re.M), name
