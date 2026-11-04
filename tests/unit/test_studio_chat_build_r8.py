"""A chat builds an app on a real database (STU-G1c, R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 G1c. Code: ``studio_save``,
``studio_publish`` and the plan record of ``studio_plan`` in
``packages/acb_skills/acb_skills/studio_tools.py``. The hermetic half is
``test_studio_package_tools.py``.

The tools run as they do in a chat. They call the A5 routes over HTTP: an
``httpx.ASGITransport`` carries each request into a FastAPI app that holds the
REAL Studio router (``gateway.routes.studio``). The app reads the member from
``X-User-Email``, as the internal bearer path of the gateway does. Each
handler opens ``tenant_session()`` on a phase-4 promoted scratch database, as
the non-privileged app role, so FORCE RLS binds. The plan card is a stub that
answers for the member. The approval record goes to an in-memory stand-in of
the tenant Redis client, through the real key builder.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    docker exec metorite-scratch-tenant createdb -U acb <private db>
    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_chat_build_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (G1c).**

a. The first task of the build: the manifest that the route stores hashes
   equal to the manifest that the chat sent. ``jsonb`` orders the keys in its
   own way, so the stored text differs, and the canonical hash
   (``manifest_hash``, sorted keys) is equal. ``based_on`` of a save is the
   hash of the stored form.
b. A member asks for a visit log, approves the plan, and the package, its
   version row and its object and field rows exist. The record is gone after
   the publish.
c. A publish with no approved plan sends no publish request and makes no
   version row.
d. A manifest that drops a published field answers 409. The tool gives the
   problems in the framed form, and no version row and no object row changes.
e. A draft that changed after its plan is refused, with no version row.

**The mutations (R7).** Each ran on 2026-11-04 with this file and the hermetic
file. The red tests are in the docstring of ``test_studio_package_tools.py``.
"""

from __future__ import annotations

import asyncio
import copy
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
from gateway.routes.studio import router as studio_router
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stug1c.test"
REPO_WORKFLOW = ".github/workflows/pr-check.yml"
_COUNTER = iter(range(10_000))

_FIELDS: list[dict[str, Any]] = [
    {"key": "site", "type": "text", "label": "Site"},
    {"key": "status", "type": "select", "options": ["planned", "done"], "hot": True},
    {"key": "qty", "type": "number"},
]


def _mid() -> str:
    return f"g1c{TAG}-visits{next(_COUNTER)}"


def _visit_log(mid: str, fields: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The visit log that a member asks for: one object and one action."""
    return {
        "studio": 1, "id": mid, "name": "Install visits",
        "purpose": "Log each install visit: the site, the state and the count. Café ✓.",
        "objects": [{"name": "visit", "label": "Visit",
                     "fields": copy.deepcopy(_FIELDS if fields is None else fields)}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


class _Redis:
    """The tenant Redis client, in memory. It takes only a real ``TenantKey``."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def setex(self, k: Any, seconds: int, value: str) -> bool:
        self.data[k.value] = value
        self.ttl[k.value] = seconds
        return True

    async def get(self, k: Any) -> Any:
        return self.data.get(k.value)

    async def delete(self, *keys: Any) -> int:
        return sum(self.data.pop(k.value, None) is not None for k in keys)


def _gateway(org: str, seen: list[tuple[str, str]]) -> FastAPI:
    """The REAL Studio router. The member is ``X-User-Email`` of the request,
    and the org is the org of the run."""
    app = FastAPI()
    app.include_router(studio_router)

    async def as_member(request: Request) -> UserContext:
        seen.append((request.method, request.url.path))
        return UserContext(email=request.headers.get("X-User-Email", ""), role=UserRole.EMPLOYEE,
                           access=build_access(["feature:workflows"]), organization_id=org)

    app.dependency_overrides[get_current_user] = as_member
    return app


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    seen: list[tuple[str, str]] = []
    app = _gateway(p.org_a, seen)
    real = httpx.AsyncClient
    monkeypatch.setattr(st.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.ASGITransport(app=app), **kw))
    monkeypatch.setattr(st, "_current_user_email", lambda: ANA)
    redis = _Redis()
    monkeypatch.setattr(st, "_plan_redis", lambda: redis)
    cards: list[dict[str, Any]] = []

    async def card(title, detail="", context="", **kw):
        cards.append({"title": title, "context": context, **kw})
        return True  # the member approves

    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)

    async def clear(ctx=None) -> bool:
        return False

    monkeypatch.setattr(egress, "write_must_show_card", clear)
    monkeypatch.setattr(egress, "chat_is_held", clear)
    yield SimpleNamespace(p=p, a=p.org_a, seen=seen, redis=redis, cards=cards)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": f"g1c{TAG}-%"}).all()]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM app_versions WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"g1c{TAG}-%"})


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine, with the
    org of the run bound (the run boundary of a chat binds it)."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            token = bind_tenant(world.a)
            try:
                return await fn()
            finally:
                release_tenant(token)
    return asyncio.run(go())


# ── Readers (as the superuser, so RLS hides nothing) ───────────────────────


def _stored(world: Any, pid: str) -> dict[str, Any]:
    with world.p.admin_engine.connect() as c:
        raw = c.execute(text("SELECT manifest FROM apps WHERE id = CAST(:p AS uuid)"),
                        {"p": pid}).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


def _versions(world: Any, pid: str) -> list[int]:
    with world.p.admin_engine.connect() as c:
        return [int(r[0]) for r in c.execute(text(
            "SELECT version FROM app_versions WHERE app_id = CAST(:p AS uuid) ORDER BY version"),
            {"p": pid}).all()]


def _tables(world: Any, pid: str) -> dict[str, list[str]]:
    """Each object row of *pid*, with the keys of its field rows."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT o.name, f.key FROM studio_objects o "
            "LEFT JOIN studio_object_fields f ON f.object_id = o.id "
            "WHERE o.package_id = CAST(:p AS uuid) ORDER BY o.name, f.position"),
            {"p": pid}).all()
    out: dict[str, list[str]] = {}
    for name, key in rows:
        out.setdefault(str(name), [])
        if key is not None:
            out[str(name)].append(str(key))
    return out


def _writes(world: Any) -> list[tuple[str, str]]:
    return [(m, p) for m, p in world.seen if m != "GET"]


# ── a. The stored form hashes equal ────────────────────────────────────────


def test_r8_the_stored_manifest_hashes_equal_to_the_sent_manifest(world) -> None:
    """The first task of the build (the audit of G1c). Mutation: hash with no
    ``sort_keys`` in ``manifest_hash`` -> the hash of the stored form differs
    -> red."""
    sent = _visit_log(_mid())
    sent["objects"][0]["fields"].append({"key": "price", "type": "number"})
    # Keys in an order that jsonb does not keep (it sorts by length, then bytes).
    sent = {k: sent[k] for k in ("purpose", "objects", "name", "actions", "studio", "id")}

    async def go() -> dict[str, Any]:
        return await st.studio_save(sent)

    out = _run(world, go)
    assert out["ok"], out
    stored = _stored(world, out["package_id"])
    # Non-vacuity: the database keeps another key order, so the text differs.
    assert json.dumps(stored, ensure_ascii=False) != json.dumps(sent, ensure_ascii=False)
    assert stored == sent
    assert st.manifest_hash(stored) == st.manifest_hash(sent) == out["based_on"]
    assert "manifest" not in out and sent["purpose"] not in json.dumps(out, ensure_ascii=False)


# ── b. The acceptance: a visit log ─────────────────────────────────────────


def test_r8_a_member_asks_for_a_visit_log_approves_the_plan_and_the_tables_exist(world) -> None:
    """Mutation: skip ``_record_plan`` in ``studio_plan`` -> the publish
    refuses -> red."""
    manifest = _visit_log(_mid())

    async def go() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        plan = await st.studio_plan(manifest)
        saved = await st.studio_save(manifest)
        return plan, saved, await st.studio_publish(saved["package_id"], "First version.")

    plan, saved, published = _run(world, go)
    assert plan["decision"] == "approved" and "note" not in plan, plan
    assert saved["ok"] and saved["live_version"] is None, saved
    assert published["ok"] and published["version"] == 1, published
    pid = saved["package_id"]
    assert _versions(world, pid) == [1]
    assert _tables(world, pid) == {"visit": ["site", "status", "qty"]}
    assert "«visit»" in published["tables"]
    assert world.redis.data == {}, "one approval buys one publish"
    assert len(world.cards) == 1  # the plan card, and no second card in a clear chat
    assert _writes(world) == [("POST", "/studio/packages"),
                              ("POST", f"/studio/packages/{pid}/versions")]


# ── c. No plan, no publish ─────────────────────────────────────────────────


def test_r8_a_publish_with_no_approved_plan_sends_no_request_and_makes_no_version(world) -> None:
    """Mutation: skip the record check in ``studio_publish`` -> a version row
    -> red."""
    manifest = _visit_log(_mid())

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        saved = await st.studio_save(manifest)
        return saved, await st.studio_publish(saved["package_id"])

    saved, out = _run(world, go)
    pid = saved["package_id"]
    assert out["ok"] is False and out["errors"][0]["message"] == st._NO_PLAN, out
    assert _versions(world, pid) == [] and _tables(world, pid) == {}
    assert _writes(world) == [("POST", "/studio/packages")]


# ── d. A refused publish ───────────────────────────────────────────────────


def test_r8_a_manifest_that_drops_a_field_answers_409_and_writes_no_row(world) -> None:
    """Mutation: pass the server message raw in ``_problems`` -> the backticks
    of the server text reach the result -> red."""
    first = _visit_log(_mid())
    second = _visit_log(first["id"], fields=[f for f in _FIELDS if f["key"] != "qty"])

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        await st.studio_plan(first)
        saved = await st.studio_save(first)
        assert (await st.studio_publish(saved["package_id"]))["ok"]
        await st.studio_plan(second)
        again = await st.studio_save(second, saved["package_id"], saved["based_on"])
        assert again["ok"], again
        return saved, await st.studio_publish(saved["package_id"])

    saved, out = _run(world, go)
    pid = saved["package_id"]
    assert out["ok"] is False and out["status"] == 409 and out["code"] == "objects_out_of_sync"
    assert out["errors"] == [{
        "path": "$.objects[0]",
        "message": "field_removed: object «visit», field «qty». " + st._KIND_WORDS["field_removed"]
                   + " " + st._FIX_LINES["field_removed"]}]
    assert out["note"] == st._PROBLEMS_NOTE and "`" not in json.dumps(out)
    assert _versions(world, pid) == [1]
    assert _tables(world, pid) == {"visit": ["site", "status", "qty"]}


# ── e. A draft that changed after its plan ─────────────────────────────────


def test_r8_a_draft_that_changed_after_its_plan_is_refused(world) -> None:
    """Mutation: drop the member or the hash from the record key -> the old
    plan opens the new draft -> red."""
    first = _visit_log(_mid())
    second = copy.deepcopy(first)
    second["name"] = "Install visits 2"

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        await st.studio_plan(first)
        saved = await st.studio_save(first)
        again = await st.studio_save(second, saved["package_id"], saved["based_on"])
        assert again["ok"], again
        return saved, await st.studio_publish(saved["package_id"])

    saved, out = _run(world, go)
    assert out["ok"] is False and out["errors"][0]["message"] == st._NO_PLAN, out
    assert _versions(world, saved["package_id"]) == []
    assert len(world.redis.data) == 1, "the record of the first plan stays until its TTL"


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """The ``--must-run`` list is a hand list. A suite that leaves it skips with
    no database and still reads green."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    workflow = (root / REPO_WORKFLOW).read_text(encoding="utf-8")
    assert "tests/unit/test_studio_chat_build_r8.py" in workflow
