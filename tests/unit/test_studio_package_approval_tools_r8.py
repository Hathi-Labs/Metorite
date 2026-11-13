"""WS-STU J2b-3a — the chat twins of the approval routes, on a real database
(R8).

Spec: ``project-docs/specs/ai_studio.md`` §14, the J2b-3a row. Code:
``studio_request_approval``, ``studio_list_approvals``, ``studio_approve`` and
``studio_reject`` in ``packages/acb_skills/acb_skills/studio_tools.py``, over
the routes of J2b-1. The hermetic twin is
``tests/unit/test_studio_package_approval_tools.py``.

The tools run as they do in a chat. They call the routes over HTTP: an
``httpx.ASGITransport`` carries each request into a FastAPI app that holds the
REAL Studio router. The app reads the member from ``X-User-Email``, as the
internal bearer path of the gateway does, and the org of the run. Each
handler opens ``tenant_session()`` on a phase-4 promoted scratch database, as
the non-privileged app role, so FORCE RLS binds each statement. Org A has Owen
(the owner of each app, with ``studio:publish``), Ada (an admin, with
``studio:publish``) and Mel (a member with ``feature:studio`` only). Org B has
Zed, with ``studio:publish``. The packages are made through the real route
handlers, as in ``test_studio_package_approval_routes_r8.py``.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_package_approval_tools_r8.py -v -rs -p no:randomly

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J2b-3a).**

a. Owen asks through the tool (202 ``pending``). Ada lists and approves
   through the tools. Exactly one version moves, and a second stays pending.
b. Owen approving his own version gets the route's 403 text. Mel, with no
   right, gets the 403 text of the list route on the approve, the reject and
   the list.
c. A version that nobody asked for shows no card and moves nothing.
d. Org B sees nothing of org A, and its approve finds no pending row.

The card answers ``approved`` for the member, as in a chat where the member
picks Approve. Each approve and each reject shows one card (review P1).
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

import httpx
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from acb_skills import ask_tools, egress
from acb_skills import studio_tools as st
from fastapi import FastAPI, Request
from gateway.routes.studio import packages
from gateway.routes.studio import router as studio_router
from gateway.studio import packages_store as store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_studio_package_approval_routes_r8 import _call, _manifest

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@j2b3a.test"
ADA = f"ada.{TAG}@j2b3a.test"
MEL = f"mel.{TAG}@j2b3a.test"
ZED = f"zed.{TAG}@j2b3a.test"
_EMAIL_LIKE = f"%.{TAG}@j2b3a.test"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
GRANTS = {OWEN: PUBLISHER, ADA: PUBLISHER, MEL: STUDIO, ZED: PUBLISHER}


def _msg(out: dict[str, Any]) -> str:
    return out["errors"][0]["message"]


def _rows(out: dict[str, Any]) -> list[dict[str, Any]]:
    lines = out["approvals"].split("\n")
    return json.loads("\n".join(lines[1:-1]))


class _Chat:
    """The member and the org of the run, as the run boundary binds them."""

    def __init__(self) -> None:
        self.email = OWEN
        self.org = ""


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for both. The
    tools reach the REAL router as the member of the chat. The members are
    seeded as the superuser. The teardown removes each package of the
    members, with its versions, objects and audit rows."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    chat = _Chat()
    app = FastAPI()
    app.include_router(studio_router)

    async def as_member(request: Request) -> UserContext:
        email = request.headers.get("X-User-Email", "")
        return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=chat.org,
                           access=build_access(list(GRANTS.get(email, ()))))

    app.dependency_overrides[get_current_user] = as_member
    real = httpx.AsyncClient
    monkeypatch.setattr(st.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.ASGITransport(app=app), **kw))
    monkeypatch.setattr(st, "_current_user_email", lambda: chat.email)

    async def clear(ctx=None) -> bool:
        return False

    monkeypatch.setattr(egress, "write_must_show_card", clear)
    monkeypatch.setattr(egress, "chat_is_held", clear)
    cards: list[str] = []

    async def card(title, detail="", context="", **kw):
        cards.append(title)
        return True  # the member picks Approve

    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    with p.admin_engine.begin() as c:
        for email, org in ((OWEN, p.org_a), (ADA, p.org_a), (MEL, p.org_a), (ZED, p.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org)"), {"e": email, "org": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, chat=chat, cards=cards)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE owner_email LIKE :t"), {"t": _EMAIL_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE owner_email LIKE :t"), {"t": _EMAIL_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _as(world: Any, org: str, email: str, call: Awaitable[Any]) -> Any:
    """Run one tool *call* as *email* in *org*, with the org bound as the run
    boundary of a chat binds it."""
    world.chat.org, world.chat.email = org, email
    token = bind_tenant(org)
    try:
        return await call
    finally:
        release_tenant(token)


async def _live(org: str, email: str, versions: int = 1) -> str:
    """A new package of *email* with *versions* published versions, through
    the real route handlers."""
    mid = f"j2b3a{TAG}-app{next(_COUNTER)}"
    status, got = await _call("create_package", org, email, payload=_manifest(mid))
    assert status == 200, got
    pid = str(got["package"]["id"])
    for n in range(versions):
        if n:
            saved = await _call("save_draft", org, email, package_id=pid,
                                payload=_manifest(mid, f"Purpose {n}."))
            assert saved[0] == 200, saved
        published = await _call("publish", org, email, package_id=pid, payload={})
        assert published[0] == 200, published
    return pid


def _states(world: Any, pid: str) -> dict[int, str]:
    with world.p.admin_engine.connect() as c:
        return {int(r[0]): str(r[1]) for r in c.execute(text(
            "SELECT version, review_status FROM app_versions WHERE app_id = CAST(:p AS uuid)"),
            {"p": pid}).all()}


def _mine(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if str(r["owner_email"]).endswith(f".{TAG}@j2b3a.test")]


# ── a. The owner asks, and an admin lists and approves one version ────────


def test_r8_the_owner_asks_and_an_admin_lists_and_approves_one_version(world) -> None:
    async def go() -> tuple[str, dict[str, Any]]:
        pid = await _live(world.a, OWEN, versions=2)
        return pid, {
            "ask 1": await _as(world, world.a, OWEN, st.studio_request_approval(pid, 1)),
            "ask 2": await _as(world, world.a, OWEN, st.studio_request_approval(pid, 2)),
            "list": await _as(world, world.a, ADA, st.studio_list_approvals()),
            "approve": await _as(world, world.a, ADA, st.studio_approve(pid, 1)),
        }

    pid, got = _run(world, go)
    for key in ("ask 1", "ask 2"):
        assert got[key]["ok"] is True and got[key]["status"] == 202, got[key]
        assert got[key]["review_status"] == "pending"
    listed = got["list"]
    assert listed["ok"] is True, listed
    rows = _mine(_rows(listed))
    assert [(r["package_id"], r["version"]) for r in rows] == [(pid, 1), (pid, 2)]
    assert (rows[0]["owner_email"], rows[0]["requested_by"]) == (OWEN, OWEN)
    assert rows[0]["manifest_cut"] is False
    assert json.loads(rows[1]["manifest"])["purpose"] == "Purpose 1."
    approved = got["approve"]
    assert approved["ok"] is True and approved["status"] == 200, approved
    assert (approved["package_id"], approved["version"], approved["review_status"]) == (
        pid, 1, "approved")
    assert approved["decision"] == "approved" and len(world.cards) == 1
    # One version only: version 2 stays pending.
    assert _states(world, pid) == {1: "approved", 2: "pending"}


# ── b. The owner and a member without the right ───────────────────────────


def test_r8_the_owner_and_a_member_without_the_right_get_the_route_text(world) -> None:
    async def go() -> tuple[str, dict[str, Any]]:
        pid = await _live(world.a, OWEN)
        assert (await _as(world, world.a, OWEN, st.studio_request_approval(pid, 1)))["ok"]
        return pid, {
            "owner approves": await _as(world, world.a, OWEN, st.studio_approve(pid, 1)),
            "member approves": await _as(world, world.a, MEL, st.studio_approve(pid, 1)),
            "member rejects": await _as(world, world.a, MEL, st.studio_reject(pid, 1)),
            "member lists": await _as(world, world.a, MEL, st.studio_list_approvals()),
        }

    pid, got = _run(world, go)
    for key, out in got.items():
        assert (out["ok"], out["status"], out["code"]) == (False, 403, "forbidden"), (key, out)
    assert _msg(got["owner approves"]) == store.SELF_APPROVAL
    # The move reads the list first, and the list route refuses Mel.
    assert _msg(got["member approves"]) == packages.NO_REVIEW_RIGHT
    assert _msg(got["member rejects"]) == packages.NO_REVIEW_RIGHT
    assert len(world.cards) == 1  # Owen's own approve showed its card, then the route refused
    assert _msg(got["member lists"]) == packages.NO_REVIEW_RIGHT
    assert _states(world, pid) == {1: "pending"}


# ── c. A version that nobody asked for ────────────────────────────────────


def test_r8_a_version_that_nobody_asked_for_moves_nothing(world) -> None:
    async def go() -> tuple[str, list[dict[str, Any]]]:
        pid = await _live(world.a, OWEN)
        return pid, [await _as(world, world.a, ADA, st.studio_approve(pid, 1)),
                     await _as(world, world.a, ADA, st.studio_reject(pid, 1))]

    pid, answers = _run(world, go)
    for out in answers:
        assert out["ok"] is False and "is not in the list" in _msg(out), out
    assert world.cards == []
    assert _states(world, pid) == {1: "auto"}


def test_r8_a_decided_version_gives_the_409_of_the_route(world) -> None:
    async def go() -> list[dict[str, Any]]:
        pid = await _live(world.a, OWEN)
        assert (await _as(world, world.a, OWEN, st.studio_request_approval(pid, 1)))["ok"]
        assert (await _as(world, world.a, ADA, st.studio_reject(pid, 1)))["ok"]
        return [await _as(world, world.a, ADA, st.studio_approve(pid, 1)),
                await _as(world, world.a, OWEN, st.studio_request_approval(pid, 1))]

    late, asked = _run(world, go)
    assert (asked["status"], asked["code"], asked["review_status"]) == (
        409, "review_conflict", "rejected"), asked
    # A rejected version is no longer pending, so the approve finds no row.
    assert late["ok"] is False and _msg(late) == st._APV_NOT_LISTED


# ── d. Org B ──────────────────────────────────────────────────────────────


def test_r8_org_b_sees_nothing_of_org_a(world) -> None:
    async def go() -> tuple[str, dict[str, Any]]:
        pid = await _live(world.a, OWEN)
        assert (await _as(world, world.a, OWEN, st.studio_request_approval(pid, 1)))["ok"]
        return pid, {
            "list": await _as(world, world.b, ZED, st.studio_list_approvals()),
            "approve": await _as(world, world.b, ZED, st.studio_approve(pid, 1)),
            "ask": await _as(world, world.b, ZED, st.studio_request_approval(pid, 1)),
        }

    pid, got = _run(world, go)
    assert got["list"]["ok"] is True and pid not in json.dumps(got["list"])
    assert _msg(got["approve"]) == st._APV_NOT_LISTED
    assert (got["ask"]["status"], _msg(got["ask"])) == (
        404, st._APV_ASK_NOT_FOUND.format(version=1, pid=pid))
    assert world.cards == []
    assert _states(world, pid) == {1: "pending"}
