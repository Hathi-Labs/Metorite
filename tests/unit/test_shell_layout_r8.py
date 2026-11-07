"""WS-PKG PKG-17a — the org default of the shell layout on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §9.1. Code:
``gateway/package_host/shell_layout.py``. The hermetic twin is
``tests/unit/test_shell_layout.py``.

The routes run in a test app. A middleware binds the tenant, as the real
seam does, and a dependency override names the viewer. The write goes through
``acb_common.org_settings`` as the NON-privileged role of the phase-4
rehearsal (no superuser, no BYPASSRLS), with two organizations.

Run (one R8 suite at a time, because ``promoted`` drops ``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_shell_layout_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-17a).**

1. An admin PUT stores a per-role default, and a member reads it back.
2. A member with stored pins gets their own. A member with null pins gets
   the role default (``resolve_layout``, on the stored document).
3. Org B reads none of the default of org A.
4. A non-admin PUT is 403 and stores nothing.
5. The flag off answers 404, and stores nothing.
6. The row sits under FORCE RLS, and a second PUT replaces the first.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings, org_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from gateway.package_host import shell_layout as sl
from gateway.routes.admin import me
from gateway.routes.shell import router
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

PANES = sorted(me.SHELL_PANES)[:3]
PRESET = sorted(me.SHELL_PRESETS)[0]
GOOD = {"roles": {"member": {"preset": PRESET, "pins": PANES}}}


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    dsn = p.app_url.set(drivername="postgresql").render_as_string(hide_password=False)
    monkeypatch.setattr(org_settings, "_conninfo", lambda: dsn)
    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_settings WHERE key = 'shell_layout'"))
    yield p
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM org_settings WHERE key = 'shell_layout'"))


def _client(org: str, admin: bool) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _bind(request: Request, call_next):  # the seam: the tenant is the session's
        token = bind_tenant(org)
        try:
            return await call_next(request)
        finally:
            release_tenant(token)

    user = UserContext(
        email=f"u@{org[:4]}.test", role=UserRole.EMPLOYEE,
        access=build_access(["admin:settings:manage"] if admin else [], roles=["member"]),
        organization_id=org)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _rows(p) -> list[tuple[str, dict]]:
    with p.admin_engine.connect() as c:
        return [(r[0], r[1]) for r in c.execute(text(
            "SELECT organization_id::text, value FROM org_settings WHERE key = 'shell_layout'"))]


@_DB_GATE
def test_r8_a_round_trip(world) -> None:
    assert _client(world.org_a, True).put("/shell/layout", json=GOOD).status_code == 200
    got = _client(world.org_a, False).get("/shell/layout")
    assert got.status_code == 200
    assert got.json()["roles"] == GOOD["roles"] | {"member": {"preset": PRESET, "pins": PANES}}
    assert got.json()["updatedBy"].startswith("u@")


@_DB_GATE
def test_r8_pins_win_and_null_pins_take_the_role_default(world) -> None:
    _client(world.org_a, True).put("/shell/layout", json=GOOD)
    stored = _client(world.org_a, False).get("/shell/layout").json()
    own = sl.resolve_layout({"pins": [PANES[2]], "preset": None}, stored, "member")
    none = sl.resolve_layout({"pins": None, "preset": None}, stored, "member")
    assert own == {"pins": [PANES[2]], "preset": PRESET}
    assert none == {"pins": PANES, "preset": PRESET}


@_DB_GATE
def test_r8_org_b_reads_none_of_org_a(world) -> None:
    _client(world.org_a, True).put("/shell/layout", json=GOOD)
    got = _client(world.org_b, False).get("/shell/layout")
    assert got.status_code == 200
    assert got.json()["roles"] == {}


@_DB_GATE
def test_r8_the_two_orgs_hold_their_own(world) -> None:
    _client(world.org_a, True).put("/shell/layout", json=GOOD)
    _client(world.org_b, True).put(
        "/shell/layout", json={"roles": {"guest": {"pins": PANES[:1]}}})
    assert set(_client(world.org_a, False).get("/shell/layout").json()["roles"]) == {"member"}
    assert set(_client(world.org_b, False).get("/shell/layout").json()["roles"]) == {"guest"}
    assert sorted(o for o, _ in _rows(world)) == sorted([world.org_a, world.org_b])


@_DB_GATE
def test_r8_a_second_put_replaces_the_first(world) -> None:
    c = _client(world.org_a, True)
    c.put("/shell/layout", json=GOOD)
    c.put("/shell/layout", json={"roles": {"admin": {"pins": PANES[:1]}}})
    rows = _rows(world)
    assert len(rows) == 1
    assert set(rows[0][1]["roles"]) == {"admin"}


@_DB_GATE
def test_r8_a_non_admin_put_is_403_and_stores_nothing(world) -> None:
    assert _client(world.org_a, False).put("/shell/layout", json=GOOD).status_code == 403
    assert _rows(world) == []


@_DB_GATE
def test_r8_the_flag_off_is_404_and_stores_nothing(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")
    assert _client(world.org_a, True).put("/shell/layout", json=GOOD).status_code == 404
    assert _client(world.org_a, False).get("/shell/layout").status_code == 404
    assert _rows(world) == []


@_DB_GATE
def test_r8_the_row_is_under_force_rls(world) -> None:
    with world.admin_engine.connect() as c:
        forced = c.execute(text(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'org_settings'")).scalar()
    assert forced is True


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for wf in ("pr-check.yml", "deploy.yml"):
        text_ = (root / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        assert "tests/unit/test_shell_layout_r8.py" in text_
