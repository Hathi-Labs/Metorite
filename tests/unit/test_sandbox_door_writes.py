"""SBX-F3: the writes of the sandbox door are held for a card (SBX-3a, task 3802).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.5 decisions 3 to 6, and
done-when 3, 5 to 10, 12, 14 and 15.

**Database-free (always runs).** A small gateway: the real default deny, the
real ``get_current_user`` under the branch-0 tripwire, the real tenant
middleware, the real ``SandboxDoorLimits`` and the real batch router
(``gateway.sandbox_batch``). Its write routes sit at real templates and
record that they ran. The capability, the member resolve and Redis are the
stand-ins of ``_sandbox_door_kit`` (its ``FakeRedis``, which holds the held
records, the decisions and the tickets under their full tenant keys). Until
SBX-3b builds the card, each test answers a card with the store function
``sandbox_batch.decide``, as §12.5 says.

**R8 (a real database, as a NOBYPASSRLS role).** Done-when 12: the real
``mint``, the real CRM and Projects routers, the real batch route and the
real door. An approved task and lead land as member M in org A, with
``meta.via`` on the task activity. Org B gets no row. A refused, a timed-out
and an unticked row land nothing. The gate is ``TENANT_LADDER_DATABASE_URL``.
A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_sandbox_door_writes.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_auth import agent_tokens, sandbox_capabilities
from acb_auth.agent_oauth import ACTOR_VIA_PATTERN
from acb_common import get_settings, tenant_redis
from acb_common.db import bind_tenant, release_tenant
from fastapi import Request
from gateway import sandbox_batch

from tests.unit import _branch_zero_tripwire as tripwire
from tests.unit import _sandbox_door_kit as kit

APP_ID = "99999999-9999-4999-8999-999999999999"
STAMP = "sandbox:lead-tool:v1"
TASK = "7f3c0000-0000-4000-8000-000000000001"
LEAD = "1ead0000-0000-4000-8000-000000000002"
_HEX32 = re.compile(r"[0-9a-f]{32}")


@pytest.fixture
def armed_tripwire():
    yield tripwire
    tripwire.assert_clean()


# ── The small gateway ───────────────────────────────────────────────────────


def _door(monkeypatch, armed, **cap_over) -> SimpleNamespace:
    """The kit gateway, the batch router and the write routes of the tests."""
    from acb_auth import deps
    from fastapi import Depends
    from fastapi.responses import JSONResponse

    door = kit.make_app(monkeypatch, tripwire=armed)
    stamps: list[str] = []

    def route(method: str, path: str, status: int = 200) -> None:
        async def handler(request: Request, user: Any = Depends(deps.get_current_user)) -> Any:
            if request.path_params.get("task_id") == "missing":
                return JSONResponse(status_code=404, content={"detail": "Task not found"})
            door.ran.append(f"{method} {path}")
            stamps.append(user.sandbox.stamp if user.sandbox else "-")
            return JSONResponse(status_code=status,
                                content={"ok": True, "body": await request.json()})

        door.app.add_api_route(path, handler, methods=[method])

    # The kit's own POST /crm/leads returns the member, not the body. Drop it.
    door.app.router.routes = [r for r in door.app.router.routes
                              if not (getattr(r, "path", "") == "/crm/leads"
                                      and "POST" in getattr(r, "methods", set()))]
    route("POST", "/crm/leads", 201)
    route("POST", "/crm/leads/{lead_id}/convert")
    route("POST", "/projects/tasks", 201)
    route("PATCH", "/projects/tasks/{task_id}")
    route("PUT", "/projects/tasks/{task_id}/assignees")
    route("POST", "/projects/tasks/bulk")
    route("PUT", "/crm/leads/{record_id}/package-fields/{install_id}/{field_key}")
    door.app.include_router(sandbox_batch.router)

    over: dict[str, Any] = {"write_scopes": ("crm:write", "projects:write"),
                            "app_id": APP_ID, "version": 1}
    over.update(cap_over)
    state = kit.arm_door(monkeypatch, cap=kit.capability(**over))

    async def stamp(app_id: str, version: Any) -> str:
        return STAMP

    monkeypatch.setattr(sandbox_batch, "_stamp", stamp)
    monkeypatch.setattr(sandbox_batch, "POLL_S", 0.01)
    door.stamps = stamps
    door.state = state
    door.redis = state.redis
    return door


def _held(store: kit.FakeRedis, org: str = kit.ORG_A) -> dict[str, dict[str, Any]]:
    """The held records of *org*, by card id."""
    prefix = f"cc:{org}:{sandbox_batch.HELD_NAMESPACE}:"
    return {k[len(prefix):]: json.loads(v) for k, v in store.values.items()
            if k.startswith(prefix)}


def _any_hold(store: kit.FakeRedis) -> bool:
    return any(":sandbox_held" in k for k in (*store.values, *store.lists))


async def _decide(card_id: str, ticked: Any, org: str = kit.ORG_A) -> bool:
    token = bind_tenant(org)
    try:
        return await sandbox_batch.decide(card_id, ticked)
    finally:
        release_tenant(token)


async def _wait_held(store: kit.FakeRedis, count: int, task: asyncio.Task) -> dict:
    for _ in range(500):
        held = _held(store)
        if len(held) >= count or task.done():
            return held
        await asyncio.sleep(0.01)
    raise AssertionError("the batch held nothing")


async def _batch(door, rows: Any, act: Any = None, headers: Any = None,
                 raw: bytes | None = None) -> httpx.Response:
    """POST one batch, and run *act* while it waits."""
    transport = httpx.ASGITransport(app=door.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw",
                                 timeout=30) as client:
        content = raw if raw is not None else json.dumps({"rows": rows}).encode()
        task = asyncio.create_task(client.post(
            "/sandbox/v1/batch", content=content,
            headers=headers if headers is not None else kit.headers()))
        if act is not None:
            await act(task)
        return await task


def _row(method: str, path: str, body: Any = None, query: Any = None) -> dict[str, Any]:
    return {"method": method, "path": path, "query": query or {}, "body": body or {}}


# ── done-when 3: a direct write never runs ─────────────────────────────────


def test_a_direct_write_never_runs(monkeypatch, armed_tripwire):
    """SBX-F3. A direct POST to ``/projects/tasks`` with a live run
    capability that holds ``projects:write`` gets 403 ``sandbox_read_only``,
    and the route does not run."""
    door = _door(monkeypatch, armed_tripwire)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=door.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            return await client.post("/projects/tasks", json={"title": "t"},
                                     headers=kit.headers())

    answer = asyncio.run(go())
    assert answer.status_code == 403
    assert answer.json() == {"detail": sandbox_capabilities.READ_ONLY}
    assert door.ran == []
    assert not _any_hold(door.redis)


# ── done-when 5: the checks before anything is held ────────────────────────

_GOOD = _row("POST", "/projects/tasks", {"title": "a"})

_REFUSALS = [
    ("smoke", {"phase": "smoke", "write_scopes": ()}, [_GOOD], 403,
     sandbox_capabilities.READ_ONLY, None),
    ("no write scope", {"write_scopes": ()}, [_GOOD], 403, sandbox_capabilities.SCOPE, None),
    ("off the list", {}, [_GOOD, _row("POST", "/projects/tasks/bulk", {"action": "delete"})],
     404, sandbox_capabilities.ROUTE_UNKNOWN, 1),
    ("no route", {}, [_row("POST", "/projects/nothing-here")], 404,
     sandbox_capabilities.ROUTE_UNKNOWN, 0),
    ("class X", {}, [_row("PUT", "/crm/leads/1/package-fields/2/k", {"value": 1})], 403,
     sandbox_capabilities.ROUTE_EXCLUDED, 0),
    ("26 rows", {}, [_GOOD] * 26, 422, sandbox_capabilities.BATCH_CAP, None),
    ("65 KiB row", {}, [_GOOD, _row("POST", "/crm/leads", {"notes": "x" * 66_000})], 422,
     sandbox_capabilities.BATCH_CAP, 1),
    ("delete", {}, [_row("DELETE", f"/projects/tasks/{TASK}")], 422,
     sandbox_capabilities.BAD_BATCH, 0),
    ("fifth key", {}, [{**_GOOD, "label": "Approve me"}], 422,
     sandbox_capabilities.BAD_BATCH, 0),
    ("get", {}, [_row("GET", "/projects/tasks")], 422, sandbox_capabilities.BAD_BATCH, 0),
    ("dot dot", {}, [_row("POST", "/projects/../crm/leads")], 422,
     sandbox_capabilities.BAD_BATCH, 0),
    ("query in path", {}, [_row("POST", "/crm/leads?x=1")], 422,
     sandbox_capabilities.BAD_BATCH, 0),
    ("nested query", {}, [_row("POST", "/crm/leads", query={"a": ["b"]})], 422,
     sandbox_capabilities.BAD_BATCH, 0),
    ("scope of the app", {"write_scopes": ("projects:write",)},
     [_GOOD, _row("POST", "/crm/leads", {"last_name": "Rao"})], 403,
     sandbox_capabilities.SCOPE, 1),
    ("agent on create", {},
     [_row("POST", "/projects/tasks", {"title": "t", "assignees": ["agent:ops"]})], 422,
     sandbox_capabilities.BAD_BATCH, 0),
    ("agent on assignees", {},
     [_GOOD, _row("PUT", f"/projects/tasks/{TASK}/assignees",
                  {"assignees": ["m@x.test", " Agent:Ops"]})], 422,
     sandbox_capabilities.BAD_BATCH, 1),
]


@pytest.mark.parametrize(("label", "over", "rows", "status", "code", "index"), _REFUSALS,
                         ids=[r[0] for r in _REFUSALS])
def test_the_batch_refuses_before_it_holds(monkeypatch, armed_tripwire, label, over, rows,
                                           status, code, index):
    """Done-when 5. Each refusal comes before anything is held. The held
    store stays empty, no route runs, and no agent run starts. A short wait,
    so a row that the checks miss fails at once and does not hang."""
    monkeypatch.setattr(get_settings(), "sandbox_approval_timeout_seconds", 1)
    door = _door(monkeypatch, armed_tripwire, **over)
    answer = asyncio.run(_batch(door, rows))
    assert answer.status_code == status, answer.text
    expected: dict[str, Any] = {"detail": code}
    if index is not None:
        expected["i"] = index
    assert answer.json() == expected
    assert door.ran == []
    assert not _any_hold(door.redis)
    assert not any(":sandbox_ticket:" in k for k in door.redis.values)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"rows": []}',
                                 b'{"rows": [], "note": "x"}', b'{"rows": {}}'])
def test_a_bad_body_is_422_bad_batch(monkeypatch, armed_tripwire, raw):
    door = _door(monkeypatch, armed_tripwire)
    answer = asyncio.run(_batch(door, None, raw=raw))
    assert answer.status_code == 422
    assert answer.json() == {"detail": sandbox_capabilities.BAD_BATCH}
    assert not _any_hold(door.redis)


def test_a_body_over_512_kib_is_422_cap(monkeypatch, armed_tripwire):
    door = _door(monkeypatch, armed_tripwire)
    rows = [_row("POST", "/crm/leads", {"notes": "x" * 60_000})] * 9
    answer = asyncio.run(_batch(door, rows))
    assert answer.status_code == 422
    assert answer.json() == {"detail": sandbox_capabilities.BATCH_CAP}
    assert not _any_hold(door.redis)


def test_only_a_sandbox_call_reaches_the_batch_route(monkeypatch, armed_tripwire):
    door = _door(monkeypatch, armed_tripwire)
    member = {"Authorization": f"Bearer {kit.INTERNAL}", "X-User-Email": kit.MEMBER}
    answer = asyncio.run(_batch(door, [_GOOD], headers=member))
    assert answer.status_code == 404
    assert not _any_hold(door.redis)


# ── done-when 6: the cards ──────────────────────────────────────────────────


def test_class_b_shares_a_card_and_class_c_gets_its_own(monkeypatch, armed_tripwire):
    """Done-when 6. Two class B rows and one class C row write two held
    records, each under a card id of 32 hex digits."""
    door = _door(monkeypatch, armed_tripwire)
    rows = [_row("POST", "/projects/tasks", {"title": "a"}),
            _row("POST", f"/crm/leads/{LEAD}/convert"),
            _row("POST", "/crm/leads", {"last_name": "Rao"})]
    seen: dict[str, Any] = {}

    async def act(task):
        held = await _wait_held(door.redis, 2, task)
        seen.update(held)
        run_list = door.redis.lists.get(f"cc:{kit.ORG_A}:sandbox_held_run:run-1", [])
        seen["__run__"] = list(run_list)
        for card_id in held:
            assert await _decide(card_id, None)

    answer = asyncio.run(_batch(door, rows, act))
    assert answer.status_code == 200, answer.text
    run_list = seen.pop("__run__")
    assert len(seen) == 2
    assert all(_HEX32.fullmatch(card_id) for card_id in seen)
    assert sorted(run_list) == sorted(seen)
    by_class = {record["class"]: record for record in seen.values()}
    assert [r["i"] for r in by_class["B"]["rows"]] == [0, 2]
    assert [r["i"] for r in by_class["C"]["rows"]] == [1]
    assert by_class["C"]["rows"][0]["template"] == "/crm/leads/{lead_id}/convert"
    for record in seen.values():
        assert record["owner"] == kit.MEMBER and record["run"] == "run-1"
        assert record["thread"] == "thread-1" and record["stamp"] == STAMP
        assert record["held"] == by_class["B"]["held"]
        assert kit.RAW not in json.dumps(record), "the record never holds the token"
    assert door.ran == []
    assert not _held(door.redis), "the batch drops its records when it ends"


# ── done-when 7, 8 and 9: the answers ──────────────────────────────────────


def test_a_held_batch_times_out_and_lands_nothing(monkeypatch, armed_tripwire):
    """Done-when 7 (SBX-F3: a timed-out card). With no decision, each row
    answers 403 ``sandbox_approval_timeout`` after the wait, and no route
    runs. A ``decide`` after the timeout runs nothing."""
    monkeypatch.setattr(get_settings(), "sandbox_approval_timeout_seconds", 1)
    door = _door(monkeypatch, armed_tripwire)
    rows = [_row("POST", "/projects/tasks", {"title": "a"}),
            _row("POST", f"/crm/leads/{LEAD}/convert")]
    cards: list[str] = []

    async def act(task):
        cards.extend(await _wait_held(door.redis, 2, task))

    answer = asyncio.run(_batch(door, rows, act))
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"rows": [
        {"i": 0, "status": 403, "code": sandbox_capabilities.APPROVAL_TIMEOUT},
        {"i": 1, "status": 403, "code": sandbox_capabilities.APPROVAL_TIMEOUT},
    ]}
    for card_id in cards:
        key = f"cc:{kit.ORG_A}:{sandbox_batch.DECISION_NAMESPACE}:{card_id}"
        assert json.loads(door.redis.values[key]) == {"decision": "expired"}
        assert asyncio.run(_decide(card_id, ["row-0", "row-1"])) is False
        assert json.loads(door.redis.values[key]) == {"decision": "expired"}
    assert door.ran == []


def test_a_refused_card_runs_nothing(monkeypatch, armed_tripwire):
    """Done-when 8 (SBX-F3: a refused card)."""
    door = _door(monkeypatch, armed_tripwire)
    rows = [_row("POST", "/projects/tasks", {"title": "a"}),
            _row("POST", "/crm/leads", {"last_name": "Rao"})]

    async def act(task):
        (card_id,) = await _wait_held(door.redis, 1, task)
        assert await _decide(card_id, None) is True
        assert await _decide(card_id, ["row-0", "row-1"]) is False, "the first writer wins"

    answer = asyncio.run(_batch(door, rows, act))
    assert answer.json() == {"rows": [
        {"i": 0, "status": 403, "code": sandbox_capabilities.WRITE_REFUSED},
        {"i": 1, "status": 403, "code": sandbox_capabilities.WRITE_REFUSED},
    ]}
    assert door.ran == []


def test_the_ticked_rows_run_one_by_one(monkeypatch, armed_tripwire):
    """Done-when 9. Two of three rows ticked: the two run in card order, and
    the third answers 403 ``sandbox_write_refused``. A row whose route
    answers 404 does not stop the next row. Each row runs with the stamp."""
    door = _door(monkeypatch, armed_tripwire)
    rows = [_row("PATCH", "/projects/tasks/missing", {"title": "x"}),
            _row("POST", "/projects/tasks", {"title": "b"}),
            _row("POST", "/crm/leads", {"last_name": "Rao"})]

    async def act(task):
        (card_id,) = await _wait_held(door.redis, 1, task)
        assert await _decide(card_id, ["row-0", "row-2"])

    answer = asyncio.run(_batch(door, rows, act))
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"rows": [
        {"i": 0, "status": 404, "body": {"detail": "Task not found"}},
        {"i": 1, "status": 403, "code": sandbox_capabilities.WRITE_REFUSED},
        {"i": 2, "status": 201, "body": {"ok": True, "body": {"last_name": "Rao"}}},
    ]}
    assert door.ran == ["POST /crm/leads"]
    assert door.stamps == [STAMP]
    assert "ticket" not in answer.text.lower()
    assert not any(":sandbox_ticket:" in k for k in door.redis.values), "each ticket spent"
    # The batch call and each row that ran count against the call cap.
    assert sum(door.redis.counts.values()) == 3


def test_an_approved_row_of_a_class_c_card_runs(monkeypatch, armed_tripwire):
    door = _door(monkeypatch, armed_tripwire)
    rows = [_row("POST", "/projects/tasks", {"title": "a"}),
            _row("POST", f"/crm/leads/{LEAD}/convert", {"deal": True})]

    async def act(task):
        held = await _wait_held(door.redis, 2, task)
        for card_id, record in held.items():
            ids = [r["id"] for r in record["rows"]]
            await _decide(card_id, ids if record["class"] == "C" else None)

    answer = asyncio.run(_batch(door, rows, act))
    assert answer.json()["rows"][0]["code"] == sandbox_capabilities.WRITE_REFUSED
    assert answer.json()["rows"][1]["status"] == 200
    assert door.ran == ["POST /crm/leads/{lead_id}/convert"]


def test_a_capability_that_ends_before_its_row_answers_not_run(monkeypatch, armed_tripwire):
    door = _door(monkeypatch, armed_tripwire)
    rows = [_row("POST", "/crm/leads", {"last_name": "Rao"})]

    async def act(task):
        (card_id,) = await _wait_held(door.redis, 1, task)

        async def gone(raw: str) -> Any:
            return None

        monkeypatch.setattr(sandbox_capabilities, "lookup", gone)
        await _decide(card_id, ["row-0"])

    answer = asyncio.run(_batch(door, rows, act))
    assert answer.json() == {"rows": [
        {"i": 0, "status": 401, "code": sandbox_capabilities.NOT_RUN}]}
    assert door.ran == []


# ── done-when 10: the hold is tenant scoped ────────────────────────────────


def test_the_held_rows_are_tenant_scoped(monkeypatch, armed_tripwire):
    """Done-when 10. The held record and the ticket live under the prefix of
    the row's org. In the scope of org B, a read finds neither."""
    door = _door(monkeypatch, armed_tripwire)
    store = door.redis
    rows = [_row("POST", "/crm/leads", {"last_name": "Rao"})]
    found: dict[str, Any] = {}

    async def act(task):
        (card_id,) = await _wait_held(store, 1, task)
        assert all(k.startswith(f"cc:{kit.ORG_A}:") for k in store.values)
        with tenant_redis.organization_scope(kit.ORG_B):
            found["b"] = await store.get(tenant_redis.key("sandbox_held", card_id))
        assert await _decide(card_id, ["row-0"], org=kit.ORG_B) is False
        found["card"] = card_id
        await _decide(card_id, None)

    asyncio.run(_batch(door, rows, act))
    assert found["b"] is None

    async def tickets() -> tuple[Any, Any, Any]:
        ticket = await sandbox_capabilities.issue_ticket(
            org=kit.ORG_A, token_sha256=door.state.row.token_sha256, method="POST",
            path="/crm/leads", query=b"", body=b"{}", stamp=STAMP, held_id="h" * 32)
        assert f"cc:{kit.ORG_A}:sandbox_ticket:{ticket}" in store.values
        with tenant_redis.organization_scope(kit.ORG_B):
            in_b = await store.get(tenant_redis.key("sandbox_ticket", ticket))
        other = kit.capability(organization_id=kit.ORG_B)
        taken_in_b = await sandbox_capabilities.take_ticket(
            other, ticket, method="POST", path="/crm/leads", query=b"", body=b"{}")
        taken_in_a = await sandbox_capabilities.take_ticket(
            door.state.row, ticket, method="POST", path="/crm/leads", query=b"", body=b"{}")
        return in_b, taken_in_b, taken_in_a

    in_b, taken_in_b, taken_in_a = asyncio.run(tickets())
    assert in_b is None and taken_in_b is None
    assert taken_in_a == STAMP


# ── done-when 14 and 15: the stamp and the wait ────────────────────────────


def test_the_stamp_form():
    """Done-when 14. The stamp matches ``ACTOR_VIA_PATTERN``. A slug that
    makes it too long gives the ``app-<8 hex>`` form."""
    stamp = sandbox_batch.stamp_of("lead-tool", APP_ID, 1)
    assert stamp == "sandbox:lead-tool:v1"
    assert ACTOR_VIA_PATTERN.fullmatch(stamp)
    long = sandbox_batch.stamp_of("a" * 60, APP_ID, 12)
    assert long == "sandbox:app-99999999:v12"
    assert ACTOR_VIA_PATTERN.fullmatch(long)
    for bad in (None, "", "Has Caps", "two words", "x\n"):
        assert sandbox_batch.stamp_of(bad, APP_ID, 1) == "sandbox:app-99999999:v1", bad


def test_the_wait_ends_before_the_step(monkeypatch, armed_tripwire):
    """Done-when 15. The setting is 300 by default. A capability with 100
    seconds left waits 70 seconds. A capability with 20 seconds left holds
    nothing, and each row answers 403 ``sandbox_approval_timeout`` at once."""
    from acb_common.settings import Settings

    assert Settings.model_fields["sandbox_approval_timeout_seconds"].default == 300
    monkeypatch.setattr(get_settings(), "sandbox_approval_timeout_seconds", 300)
    assert sandbox_batch.batch_wait_s(100) == 70
    assert sandbox_batch.batch_wait_s(900) == 300
    assert sandbox_batch.batch_wait_s(30) == 0

    door = _door(monkeypatch, armed_tripwire, seconds_left=20)
    rows = [_row("POST", "/crm/leads", {"last_name": "Rao"}),
            _row("POST", "/projects/tasks", {"title": "a"})]
    answer = asyncio.run(asyncio.wait_for(_batch(door, rows), 5))
    assert answer.json() == {"rows": [
        {"i": 0, "status": 403, "code": sandbox_capabilities.APPROVAL_TIMEOUT},
        {"i": 1, "status": 403, "code": sandbox_capabilities.APPROVAL_TIMEOUT},
    ]}
    assert not _any_hold(door.redis)
    assert door.ran == []


def test_a_redis_fault_at_the_hold_is_503_and_holds_nothing(monkeypatch, armed_tripwire):
    door = _door(monkeypatch, armed_tripwire)
    real_set = door.redis.set
    calls = {"n": 0}

    async def flaky(k, value, nx=False, ex=None):
        if ":sandbox_held:" in str(k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ConnectionError("redis is down")
        return await real_set(k, value, nx=nx, ex=ex)

    monkeypatch.setattr(door.redis, "set", flaky)
    rows = [_row("POST", "/projects/tasks", {"title": "a"}),
            _row("POST", f"/crm/leads/{LEAD}/convert")]
    answer = asyncio.run(_batch(door, rows))
    assert answer.status_code == 503
    assert not _held(door.redis), "the first record was dropped"
    assert door.ran == []


# ═══════════════════════════════════════════════════════════════════════════
# R8 (done-when 12): an approved write lands as the member, on a real database
# ═══════════════════════════════════════════════════════════════════════════

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)

_APP_ROLE = "acb_app_sbxwrite"
_APP_PW = "sbxwrite_scratch_pw"
M = "m-member@sbxwrite.test"
B_MEMBER = "b-member@sbxwrite.test"
SLUG = "lead-tool"


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two tenants with leads and tasks, one app
    of org A for the stamp, and a NOBYPASSRLS app role."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database
    from tests.unit.test_sandbox_capability import _Seeder

    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_sbxwrite"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "sbxwrite-a"), (org_b, "sbxwrite-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :slug, :slug)"), {"id": oid, "slug": slug})
    a, b = _Seeder(eng, org_a), _Seeder(eng, org_b)
    a.member(M, ["feature:crm", "feature:projects"])
    b.member(B_MEMBER, ["feature:crm", "feature:projects"])
    app_id = str(a.one(
        "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id)"
        " VALUES (:s, 'Lead tool', :e, '/tmp/sbxwrite', CAST(:o AS uuid)) RETURNING id",
        s=SLUG, e=M, o=org_a))
    w = SimpleNamespace(
        admin=eng, org_a=org_a, org_b=org_b, app_id=app_id,
        leads_a=a.leads(["A lead"]),
        leads_b=b.leads(["B lead"]),
        task_a=a.task("A task", M, 1),
        task_b=b.task("B task", B_MEMBER, 1001),
        app_dsn=admin_url.set(database=dedicated, username=_APP_ROLE, password=_APP_PW)
        .render_as_string(hide_password=False),
    )
    yield w
    eng.dispose()
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                       "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
        c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
    maint.dispose()


@pytest.fixture
def r8door(monkeypatch, world):
    """The real door, with the flag on for every org and Redis the kit's
    stand-in."""
    from acb_auth import access as access_module
    from acb_auth import deps
    from acb_auth.access import invalidate as invalidate_access

    settings = get_settings()
    monkeypatch.setattr(settings, "sandbox_door_orgs", "*")
    monkeypatch.setattr(settings, "self_serve_signup_enabled", "")
    monkeypatch.setattr(settings, "crm_enabled", True)
    monkeypatch.setattr(settings, "crm_orgs", "*")
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "sbxwrite.test")
    monkeypatch.setattr(access_module, "_tables_missing", False)
    monkeypatch.setattr(sandbox_batch, "POLL_S", 0.02)
    invalidate_access()
    store = kit.FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: store)
    armed = tripwire.arm(monkeypatch, deps)
    try:
        yield SimpleNamespace(world=world, redis=store, armed=armed)
    finally:
        invalidate_access()
    tripwire.assert_clean()


def _gateway(armed) -> Any:
    from acb_auth import deps, require_authenticated
    from fastapi import FastAPI
    from gateway.main import TenantScopeMiddleware
    from gateway.routes import crm as crm_package
    from gateway.routes.projects import router as projects_router
    from gateway.sandbox_door import SandboxDoorLimits

    app = FastAPI(dependencies=[require_authenticated(public=frozenset())])
    app.include_router(crm_package.router)
    app.include_router(projects_router)
    app.include_router(sandbox_batch.router)
    app.add_middleware(SandboxDoorLimits)
    app.add_middleware(TenantScopeMiddleware)
    app.dependency_overrides[deps.get_current_user] = tripwire.current_user_override(armed)
    return app


async def _scoped(world, work):
    from tests.unit._tenant_ladder import tenant_engine_scope

    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


def _one(world, sql: str, **params) -> Any:
    from sqlalchemy import text

    with world.admin.connect() as c:
        return c.execute(text(sql), params).first()


def _counts(world, org: str) -> tuple[int, int, int]:
    row = _one(world, (
        "SELECT (SELECT count(*) FROM crm_leads WHERE organization_id = CAST(:o AS uuid)),"
        " (SELECT count(*) FROM pm_tasks WHERE organization_id = CAST(:o AS uuid)),"
        " (SELECT count(*) FROM pm_activities WHERE organization_id = CAST(:o AS uuid))"
    ), o=org)
    return int(row[0]), int(row[1]), int(row[2])


async def _r8_batch(r8door, token: str, rows: list[dict], decide_as: Any) -> httpx.Response:
    """POST one batch through the real gateway, and answer each card with
    *decide_as* (a function of the held record), or not at all (None)."""
    w = r8door.world

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=_gateway(r8door.armed))
        async with httpx.AsyncClient(transport=transport, base_url="http://gw",
                                     timeout=60) as client:
            task = asyncio.create_task(client.post(
                "/sandbox/v1/batch", json={"rows": rows},
                headers={"X-Sandbox-Capability": token}))
            if decide_as is not None:
                held: dict = {}
                for _ in range(1000):
                    held = _held(r8door.redis, w.org_a)
                    if held or task.done():
                        break
                    await asyncio.sleep(0.01)
                assert held, "the batch held nothing"
                for card_id, record in held.items():
                    await _decide(card_id, decide_as(record), org=w.org_a)
            return await task

    return await _scoped(w, go)


@_DB_GATE
class TestDoneWhen12AnApprovedWriteLandsAsTheMember:
    async def test_r8_an_approved_write_lands_as_the_member(self, r8door, monkeypatch):
        w = r8door.world
        role = _one(w, "SELECT rolbypassrls FROM pg_roles WHERE rolname = :r", r=_APP_ROLE)
        assert role is not None and role[0] is False, "the app role is NOBYPASSRLS"
        before_b = _counts(w, w.org_b)
        minted = await _scoped(w, lambda: sandbox_capabilities.mint(
            org=w.org_a, member=M, run_id="run-r8w", thread_id="thread-r8w", phase="run",
            read_scopes=("crm:read", "projects:read"),
            write_scopes=("crm:write", "projects:write"), app_id=w.app_id, version=1))
        stamp = f"sandbox:{SLUG}:v1"

        # 1. Approved: a task title, a comment, a lead change and a new lead.
        rows = [
            _row("PATCH", f"/projects/tasks/{w.task_a}", {"title": "Approved title"}),
            _row("POST", f"/projects/tasks/{w.task_a}/comments", {"body": "From the tool"}),
            _row("PATCH", f"/crm/leads/{w.leads_a[0]}", {"last_name": "Approved"}),
            _row("POST", "/crm/leads", {"last_name": "Newlead"}),
        ]
        answer = await _r8_batch(r8door, minted.token, rows,
                                 lambda rec: [r["id"] for r in rec["rows"]])
        assert answer.status_code == 200, answer.text
        statuses = [entry["status"] for entry in answer.json()["rows"]]
        assert statuses[0] == 200 and statuses[2] == 200, answer.text
        assert statuses[1] in (200, 201) and statuses[3] == 201, answer.text
        assert _one(w, "SELECT title FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                    t=w.task_a)[0] == "Approved title"
        comment = _one(w, (
            "SELECT created_by, meta->>'via' FROM pm_activities"
            " WHERE task_id = CAST(:t AS uuid) AND type = 'comment'"
            " ORDER BY created_at DESC LIMIT 1"), t=w.task_a)
        assert comment is not None and comment[0] == M and comment[1] == stamp
        assert _one(w, "SELECT last_name FROM crm_leads WHERE id = CAST(:l AS uuid)",
                    l=w.leads_a[0])[0] == "Approved"
        new_id = answer.json()["rows"][3]["body"]["id"]
        new_lead = _one(w, ("SELECT owner_email, organization_id::text FROM crm_leads"
                            " WHERE id = CAST(:l AS uuid)"), l=new_id)
        assert new_lead is not None and new_lead[0] == M and new_lead[1] == w.org_a
        assert _counts(w, w.org_b) == before_b, "org B has no new row"

        # 2. Refused: nothing changes.
        refused = await _r8_batch(r8door, minted.token, [
            _row("PATCH", f"/projects/tasks/{w.task_a}", {"title": "Refused title"})],
            lambda rec: None)
        assert refused.json()["rows"][0]["code"] == sandbox_capabilities.WRITE_REFUSED

        # 3. Unticked: the task row is not ticked, the lead row is.
        unticked = await _r8_batch(r8door, minted.token, [
            _row("PATCH", f"/projects/tasks/{w.task_a}", {"title": "Unticked title"}),
            _row("PATCH", f"/crm/leads/{w.leads_a[0]}", {"last_name": "Ticked"})],
            lambda rec: ["row-1"])
        assert [e["status"] for e in unticked.json()["rows"]] == [403, 200], unticked.text

        # 4. Timed out: nothing changes, and a late approval runs nothing.
        monkeypatch.setattr(get_settings(), "sandbox_approval_timeout_seconds", 1)
        late = await _r8_batch(r8door, minted.token, [
            _row("PATCH", f"/projects/tasks/{w.task_a}", {"title": "Late title"})], None)
        assert late.json()["rows"][0]["code"] == sandbox_capabilities.APPROVAL_TIMEOUT

        assert _one(w, "SELECT title FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                    t=w.task_a)[0] == "Approved title"
        assert _one(w, "SELECT last_name FROM crm_leads WHERE id = CAST(:l AS uuid)",
                    l=w.leads_a[0])[0] == "Ticked"
        assert _counts(w, w.org_b) == before_b, "org B has no new row"
        assert not any(":sandbox_ticket:" in k for k in r8door.redis.values)
        assert agent_tokens.hash_token(minted.token) not in json.dumps(
            [answer.json(), refused.json(), unticked.json(), late.json()])
