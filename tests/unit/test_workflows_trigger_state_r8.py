"""STB-6c on a real database: arm one trigger, and the gate of a Save (R8).

Spec: ``project-docs/specs/ai_studio.md`` row E13c (task 3119). Owner decision
of 2026-11-13 (run answer 09bcb706, D-number pending): arming a trigger on a
published workflow needs ``workflows:publish``. Code: ``set_trigger_state``
and ``refuse_live_arming`` in ``gateway/routes/workflows/publish.py``, and the
call in ``crud.update_workflow``. The hermetic half is
``test_workflows_trigger_state.py``.

Each member holds a REAL system role of org A, seeded by the real
``provision_org_roles``. Migration 200 gives ``workflows:publish`` to the
owner (``*``), the admin and the manager, and not to the member. The real
``resolve_access`` reads the grants on a phase-4 promoted scratch database, as
the non-privileged app role, with the org bound, so FORCE RLS binds. The
REAL Workflows router serves each request over an ASGI transport.

**Acceptance.**

a. A member without ``workflows:publish`` gets 403 on the route, and no
   trigger changes.
b. A holder of the right arms and disarms ONE trigger, and the others of the
   workflow stay as they were. Two triggers of one kind need ``trigger_id``.
c. The workflow of org B answers 404 to a member of org A, and its trigger
   stays off.
d. The E3 rule holds: only the author turns on a Studio record trigger.
e. A ``manual`` trigger gets 422.
f. A draft takes the flag and answers ``live: false``. So does a published
   workflow in an org off ``WORKFLOW_UNATTENDED_ORGS``, and its note says so.
g. A Save of a published or a disabled workflow that arms a schedule,
   webhook or event trigger needs the right. A Save that arms nothing, and a
   Save of a draft that was never published, need none.

Run (a PRIVATE database, one R8 suite at a time, because the ``promoted``
fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_workflows_trigger_state_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**The mutations (R7).** Each ran on 2026-11-13, and each test named here
went red.

* M1: drop ``dependencies=[require_permission(PUBLISH_PERMISSION)]`` from the
  route. Red: ``test_a_member_without_the_right_gets_403``.
* M2: drop the ``refuse_live_arming`` call of ``update_workflow``. Red:
  ``test_a_save_that_arms_a_live_trigger_needs_the_right``.
* M3: drop ``id = :tid AND`` from the two UPDATE statements of the route.
  Red: ``test_a_holder_arms_and_disarms_one_trigger_and_the_others_stay``,
  for each of the three people.
* M4: ``_pick_trigger`` ignores ``trigger_id`` and takes the first trigger.
  Red: the same test, for each of the three people.
* M5: drop the ``_refuse_foreign_studio_trigger`` call of the route. Red:
  ``test_only_the_author_turns_on_a_studio_record_trigger``.
* M6 (review P2-a): the Save checks a published workflow only. Red:
  ``test_a_save_that_arms_a_disabled_workflow_needs_the_right``.
* M7 (review P2-b): ``live`` ignores ``unattended.org_allowed``. Red:
  ``test_an_org_off_the_list_is_armed_but_not_live``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from gateway.routes.workflows import router as workflows_router
from gateway.routes.workflows import unattended
from gateway.routes.workflows.publish import ARMING_REFUSAL
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]

#: The people of org A: their system role, and their overrides.
PEOPLE: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "owner": ("owner", ()),
    "manager": ("manager", ()),
    "member": ("member", ()),
    "member_pub": ("member", (("workflows:publish", "allow"),)),
}

_CRON_A = {"cron": "0 9 * * *"}
_CRON_B = {"cron": "0 18 * * *"}
_STUDIO = {"source": "studio", "event_type": "studio.record.created",
           "filter": {"object": "claim"}}
_ZOHO = {"source": "zoho", "event_type": "lead.created"}

#: name -> (org tag, status, [(kind, config, enabled)]). The owner of org A is
#: the author of each workflow of org A.
WORKFLOWS: dict[str, tuple[str, str, list[tuple[str, dict, bool]]]] = {
    "live": ("A", "published", [
        ("schedule", _CRON_A, False),
        ("schedule", _CRON_B, False),
        ("webhook", {"secret": "hook-secret"}, True),
        ("event", _STUDIO, False),
        ("event", _ZOHO, False),
        ("manual", {}, True),
    ]),
    "draft": ("A", "draft", [("webhook", {}, False)]),
    "disabled": ("A", "disabled", [("webhook", {}, False)]),
    "other org": ("B", "published", [("webhook", {}, False)]),
}


def _email(key: str) -> str:
    return f"{key}.{TAG}@stb6c.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    a, b = promoted.org_a, promoted.org_b
    orgs = {"A": a, "B": b}
    wids: dict[str, str] = {}
    with promoted.admin_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": a})
        c.execute(text("SELECT provision_org_roles(:o)"), {"o": a})
        roles = {str(slug): str(rid) for slug, rid in c.execute(text(
            "SELECT slug, id FROM org_role WHERE organization_id = CAST(:o AS uuid)"),
            {"o": a}).all()}
        for key, (role, overrides) in PEOPLE.items():
            uid = c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
                {"e": _email(key), "o": a}).scalar()
            c.execute(text(
                "INSERT INTO user_role (user_id, role_id, organization_id) "
                "VALUES (:u, CAST(:r AS uuid), CAST(:o AS uuid))"),
                {"u": uid, "r": roles[role], "o": a})
            for permission, effect in overrides:
                c.execute(text(
                    "INSERT INTO user_permission_override (user_id, permission, effect, "
                    "reason, set_by, organization_id) VALUES (:u, :p, :x, 'STB-6c R8', "
                    "'seed@stb6c.test', CAST(:o AS uuid))"),
                    {"u": uid, "p": permission, "x": effect, "o": a})
        for name, (tag, status, _triggers) in WORKFLOWS.items():
            wids[name] = str(c.execute(text(
                "INSERT INTO workflows (name, description, owner_email, hook_token, status, "
                "latest_version, organization_id) VALUES (:n, '', :e, :t, :s, :v, "
                "CAST(:o AS uuid)) RETURNING id"),
                {"n": f"{name} {TAG}", "e": _email("owner"), "t": f"tok-{uuid.uuid4().hex}",
                 "s": status, "v": None if status == "draft" else 1,
                 "o": orgs[tag]}).scalar())
    yield SimpleNamespace(p=promoted, a=a, b=b, wids=wids, orgs=orgs)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM workflows WHERE id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(wids.values())})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": f"%.{TAG}@stb6c.test"})


@pytest.fixture(autouse=True)
def _seed_triggers(world) -> None:
    """Each case starts from the triggers of :data:`WORKFLOWS`. A schedule has
    an old baseline, so a test sees whether a turn-on clears it."""
    with world.p.admin_engine.begin() as c:
        for name, (tag, _status, triggers) in WORKFLOWS.items():
            c.execute(text("DELETE FROM workflow_triggers WHERE workflow_id = CAST(:w AS uuid)"),
                      {"w": world.wids[name]})
            for kind, config, enabled in triggers:
                c.execute(text(
                    "INSERT INTO workflow_triggers (workflow_id, kind, config, enabled, "
                    "last_fired_at, organization_id) VALUES (CAST(:w AS uuid), :k, "
                    "CAST(:c AS jsonb), :on, now() - make_interval(days => 1), "
                    "CAST(:o AS uuid))"),
                    {"w": world.wids[name], "k": kind, "c": json.dumps(config), "on": enabled,
                     "o": world.orgs[tag]})


@pytest.fixture(autouse=True)
def _resolver(monkeypatch: pytest.MonkeyPatch, world) -> None:
    """The role leg reads ``app_user`` on a bound session (the cutover path).
    The Studio author gate is off, so only the right of this slice decides."""
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    monkeypatch.setattr(get_settings(), "workflow_studio_gate_orgs", "")
    access_mod.invalidate()
    _list_orgs(monkeypatch, world.a)


def _list_orgs(monkeypatch: pytest.MonkeyPatch, *orgs: str) -> None:
    """Set ``WORKFLOW_UNATTENDED_ORGS`` for one case. Each case lists org A,
    unless it says not."""
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(workflow_unattended_orgs=",".join(orgs)))


def _app(users: dict[str, UserContext]) -> FastAPI:
    app = FastAPI()
    app.include_router(workflows_router)

    async def _caller(request: Request) -> UserContext:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app.dependency_overrides[get_current_user] = _caller
    return app


def _run(world: Any, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run ``fn(call)`` on the app role's engine. ``call(key, method, path,
    body)`` resolves the member of *key* in org A through the real
    ``resolve_access``, then sends the request through the real router."""
    import acb_auth.access as access_mod

    async def go() -> Any:
        users: dict[str, UserContext] = {}
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with (
            tenant_engine_scope(dsn),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(users)),
                              base_url="http://workflows.test") as client,
        ):
            async def resolve(key: str) -> UserContext:
                token = bind_tenant(world.a)
                try:
                    access = await access_mod.resolve_access(_email(key), use_cache=False)
                finally:
                    release_tenant(token)
                return UserContext(email=_email(key), role=UserRole.EMPLOYEE,
                                   access=access, organization_id=world.a)

            async def call(key: str, method: str, path: str, body: Any = None) -> httpx.Response:
                users[key] = await resolve(key)
                token = bind_tenant(world.a)
                try:
                    return await client.request(method, path, json=body,
                                                headers={"x-test-as": key})
                finally:
                    release_tenant(token)

            call.resolve = resolve  # type: ignore[attr-defined]
            return await fn(call)

    return asyncio.run(go())


def _triggers(world: Any, name: str) -> list[tuple[str, str, bool, bool]]:
    """The triggers of *name*, as the superuser sees them: the kind, the
    config, the flag, and whether a baseline is set. Sorted, so an id does
    not matter."""
    with world.p.admin_engine.connect() as c:
        return sorted(
            (str(k), json.dumps(cfg, sort_keys=True), bool(on), last is not None)
            for k, cfg, on, last in c.execute(text(
                "SELECT kind, config, enabled, last_fired_at FROM workflow_triggers "
                "WHERE workflow_id = CAST(:w AS uuid)"), {"w": world.wids[name]}).all())


def _id_of(world: Any, name: str, kind: str, config: dict) -> str:
    with world.p.admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT id FROM workflow_triggers WHERE workflow_id = CAST(:w AS uuid) "
            "AND kind = :k AND config = CAST(:c AS jsonb)"),
            {"w": world.wids[name], "k": kind, "c": json.dumps(config)}).scalar_one())


def _seeded(name: str, **flips: bool) -> list[tuple[str, str, bool, bool]]:
    """The seed of *name* as :func:`_triggers` reads it, with each flipped
    trigger keyed by ``<kind>_<n>``, the n-th trigger of that kind (from 0).
    A turn-on of a schedule clears its baseline."""
    out = []
    seen: dict[str, int] = {}
    for kind, config, enabled in WORKFLOWS[name][2]:
        n = seen.get(kind, 0)
        seen[kind] = n + 1
        on = flips.get(f"{kind}_{n}", enabled)
        cleared = kind == "schedule" and on and not enabled
        out.append((kind, json.dumps(config, sort_keys=True), on, not cleared))
    return sorted(out)


def _state(wid: str, kind: str) -> str:
    return f"/workflows/{wid}/triggers/{kind}/state"


# ── The right of each person ──────────────────────────────────────────────


def test_resolve_access_gives_each_person_the_right_of_the_cases(world) -> None:
    async def fn(call: Any) -> dict[str, bool]:
        return {key: (await call.resolve(key)).has_permission("workflows:publish")
                for key in PEOPLE}

    assert _run(world, fn) == {"owner": True, "manager": True, "member": False,
                               "member_pub": True}


# ── a. No right ───────────────────────────────────────────────────────────


def test_a_member_without_the_right_gets_403(world) -> None:
    wid = world.wids["live"]

    async def fn(call: Any) -> list[tuple[int, bool]]:
        out = []
        for kind, body in (("webhook", {"enabled": False}), ("event", {
                "enabled": True, "trigger_id": _id_of(world, "live", "event", _ZOHO)})):
            r = await call("member", "POST", _state(wid, kind), body)
            out.append((r.status_code, "workflows:publish" in r.text))
        return out

    assert _run(world, fn) == [(403, True), (403, True)]
    assert _triggers(world, "live") == _seeded("live")


# ── b. One trigger, and the others stay ───────────────────────────────────


def _second_schedule(world: Any) -> tuple[str, str]:
    """The schedule that the route reads SECOND, and its flip key.

    The seed writes both rows in one transaction, so they share
    ``created_at``, and the route orders by ``created_at, id``. So the larger
    id comes second. A route that ignores ``trigger_id`` and takes the first
    row then always changes the wrong one (mutation M3b)."""
    ids = {_id_of(world, "live", "schedule", _CRON_A): "schedule_0",
           _id_of(world, "live", "schedule", _CRON_B): "schedule_1"}
    second = max(ids)
    return second, ids[second]


@pytest.mark.parametrize("key", ["manager", "member_pub", "owner"])
def test_a_holder_arms_and_disarms_one_trigger_and_the_others_stay(world, key: str) -> None:
    wid = world.wids["live"]
    second, flip = _second_schedule(world)

    async def fn(call: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        r = await call(key, "POST", _state(wid, "schedule"), {"enabled": True})
        out["no id"] = (r.status_code, "trigger_id" in r.json()["detail"])
        r = await call(key, "POST", _state(wid, "schedule"),
                       {"enabled": True, "trigger_id": second})
        out["arm"] = (r.status_code, r.json())
        out["after arm"] = _triggers(world, "live")
        r = await call(key, "POST", _state(wid, "webhook"), {"enabled": False})
        out["disarm"] = (r.status_code, r.json()["enabled"], r.json()["live"])
        return out

    got = _run(world, fn)
    assert got["no id"] == (409, True), got
    status, body = got["arm"]
    assert status == 200, got
    assert body == {"workflow_id": wid, "trigger_id": second, "kind": "schedule",
                    "enabled": True, "changed": True, "workflow_status": "published",
                    "live": True, "note": body["note"]}
    assert "hook-secret" not in json.dumps(body) and "tok-" not in json.dumps(body)
    assert got["after arm"] == _seeded("live", **{flip: True}), got
    assert got["disarm"] == (200, False, False), got
    assert _triggers(world, "live") == _seeded("live", webhook_0=False, **{flip: True})


# ── c. Another org ────────────────────────────────────────────────────────


def test_the_workflow_of_another_org_is_404(world) -> None:
    wid = world.wids["other org"]

    async def fn(call: Any) -> tuple[int, str]:
        r = await call("owner", "POST", _state(wid, "webhook"), {"enabled": True})
        return r.status_code, r.json()["detail"]

    assert _run(world, fn) == (404, "Workflow not found")
    assert _triggers(world, "other org") == _seeded("other org")


# ── d. The E3 author rule ─────────────────────────────────────────────────


def test_only_the_author_turns_on_a_studio_record_trigger(world) -> None:
    wid = world.wids["live"]
    studio = _id_of(world, "live", "event", _STUDIO)

    async def fn(call: Any) -> dict[str, Any]:
        body = {"enabled": True, "trigger_id": studio}
        out: dict[str, Any] = {}
        r = await call("manager", "POST", _state(wid, "event"), body)
        out["manager"] = (r.status_code, r.json()["detail"])
        out["after refusal"] = _triggers(world, "live")
        out["author"] = (await call("owner", "POST", _state(wid, "event"), body)).status_code
        out["manager off"] = (await call("manager", "POST", _state(wid, "event"),
                                         {"enabled": False, "trigger_id": studio})).status_code
        return out

    got = _run(world, fn)
    assert got["manager"] == (
        403, "Only the author of this workflow can add or change a Studio record trigger."), got
    assert got["after refusal"] == _seeded("live"), got
    assert (got["author"], got["manager off"]) == (200, 200), got
    assert _triggers(world, "live") == _seeded("live")


# ── e, f. A manual trigger, and a draft ───────────────────────────────────


def test_a_manual_trigger_has_no_state(world) -> None:
    async def fn(call: Any) -> tuple[int, str]:
        r = await call("owner", "POST", _state(world.wids["live"], "manual"),
                       {"enabled": False})
        return r.status_code, r.json()["detail"]

    status, detail = _run(world, fn)
    assert status == 422 and "always available" in detail
    assert _triggers(world, "live") == _seeded("live")


def test_a_draft_takes_the_flag_and_is_not_live(world) -> None:
    async def fn(call: Any) -> dict[str, Any]:
        r = await call("manager", "POST", _state(world.wids["draft"], "webhook"),
                       {"enabled": True})
        return r.json()

    body = _run(world, fn)
    assert (body["enabled"], body["live"], body["workflow_status"]) == (True, False, "draft")
    assert "not published" in body["note"]
    assert _triggers(world, "draft") == _seeded("draft", webhook_0=True)


def test_an_org_off_the_list_is_armed_but_not_live(world, monkeypatch) -> None:
    """No door starts a run for an org off ``WORKFLOW_UNATTENDED_ORGS``, so the
    answer never says live there (review P2-b)."""
    _list_orgs(monkeypatch)  # the shipped default: no org

    async def fn(call: Any) -> dict[str, Any]:
        r = await call("manager", "POST", _state(world.wids["disabled"], "webhook"),
                       {"enabled": True})
        assert r.json()["live"] is False and "disabled" in r.json()["note"], r.text
        r = await call("manager", "POST", _state(world.wids["live"], "event"), {
            "enabled": True, "trigger_id": _id_of(world, "live", "event", _ZOHO)})
        return r.json()

    body = _run(world, fn)
    assert (body["enabled"], body["live"], body["workflow_status"]) == (True, False, "published")
    assert "does not run workflows without a person" in body["note"]
    assert _triggers(world, "live") == _seeded("live", event_1=True)


# ── g. The Save (PUT) gate ────────────────────────────────────────────────


def _doc(world: Any, name: str, **flips: bool) -> list[dict[str, Any]]:
    """The trigger list of a Save, as the editor sends it back, with flips."""
    out = []
    seen: dict[str, int] = {}
    for kind, config, enabled in WORKFLOWS[name][2]:
        n = seen.get(kind, 0)
        seen[kind] = n + 1
        out.append({"kind": kind, "config": config,
                    "enabled": flips.get(f"{kind}_{n}", enabled)})
    return out


def test_a_save_that_arms_a_live_trigger_needs_the_right(world) -> None:
    wid = world.wids["live"]
    added = [*_doc(world, "live"), {"kind": "schedule", "config": {"cron": "*/5 * * * *"},
                                    "enabled": True}]
    turned_on = _doc(world, "live", event_1=True)

    async def fn(call: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for label, doc in (("add", added), ("turn on", turned_on)):
            r = await call("member", "PUT", f"/workflows/{wid}",
                           {"name": f"live {TAG}", "triggers": doc})
            out[label] = (r.status_code, r.json()["detail"])
        out["after refusal"] = _triggers(world, "live")
        r = await call("manager", "PUT", f"/workflows/{wid}", {"triggers": turned_on})
        out["manager"] = r.status_code
        return out

    got = _run(world, fn)
    assert got["add"] == (403, ARMING_REFUSAL), got
    assert got["turn on"] == (403, ARMING_REFUSAL), got
    assert got["after refusal"] == _seeded("live"), got
    assert got["manager"] == 200, got
    # A Save writes new rows, and the zoho event keeps no baseline of a schedule.
    assert [(k, c, on) for k, c, on, _b in _triggers(world, "live")] == [
        (k, c, on) for k, c, on, _b in _seeded("live", event_1=True)]


def test_a_save_that_arms_nothing_needs_no_right(world) -> None:
    wid = world.wids["live"]

    async def fn(call: Any) -> dict[str, int]:
        out: dict[str, int] = {}
        out["unchanged"] = (await call("member", "PUT", f"/workflows/{wid}", {
            "name": f"live {TAG}", "triggers": _doc(world, "live")})).status_code
        out["turn off"] = (await call("member", "PUT", f"/workflows/{wid}", {
            "triggers": _doc(world, "live", webhook_0=False)})).status_code
        out["no triggers"] = (await call("member", "PUT", f"/workflows/{wid}", {
            "description": "edited"})).status_code
        out["draft"] = (await call("member", "PUT", f"/workflows/{world.wids['draft']}", {
            "triggers": _doc(world, "draft", webhook_0=True)})).status_code
        return out

    assert _run(world, fn) == {"unchanged": 200, "turn off": 200, "no triggers": 200,
                               "draft": 200}
    assert [on for _k, _c, on, _b in _triggers(world, "draft")] == [True]
    assert ("webhook", json.dumps({"secret": "hook-secret"}), False) in [
        (k, c, on) for k, c, on, _b in _triggers(world, "live")]


def test_a_save_that_arms_a_disabled_workflow_needs_the_right(world) -> None:
    """An enable writes only the status, so a trigger that a Save arms while
    the workflow is off goes live with no second look (review P2-a). A draft
    that was never published stays free of the rule."""
    wid = world.wids["disabled"]

    async def fn(call: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        r = await call("member", "PUT", f"/workflows/{wid}",
                       {"triggers": _doc(world, "disabled", webhook_0=True)})
        out["member"] = (r.status_code, r.json()["detail"])
        out["after refusal"] = _triggers(world, "disabled")
        out["manager"] = (await call("manager", "PUT", f"/workflows/{wid}", {
            "triggers": _doc(world, "disabled", webhook_0=True)})).status_code
        out["draft"] = (await call("member", "PUT", f"/workflows/{world.wids['draft']}", {
            "triggers": _doc(world, "draft", webhook_0=True)})).status_code
        return out

    got = _run(world, fn)
    assert got["member"] == (403, ARMING_REFUSAL), got
    assert got["after refusal"] == _seeded("disabled"), got
    assert (got["manager"], got["draft"]) == (200, 200), got
    assert [on for _k, _c, on, _b in _triggers(world, "disabled")] == [True]
