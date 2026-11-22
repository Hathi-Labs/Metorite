"""CSY-8 (task 3408): the daily use counter of an MCP server, and its read for the panel.

Spec: ``project-docs/specs/chat_connected_systems.md`` §3.3 and §3.7.

What each rule is held by (R7):

* **A1** Each chat call of the gate adds ONE to the count of its org and server.
  ``test_one_call_adds_one``, ``test_two_calls_add_two``,
  ``test_the_gate_counts_each_outcome_of_a_chat_call``.
* **A2** A key holds the tenant prefix. ``test_the_keys_hold_the_tenant_prefix``.
* **A3** The TTL is 8 days. ``test_the_ttl_is_eight_days``.
* **A4** The day rolls at UTC midnight. ``test_the_day_rolls_at_utc_midnight``.
* **A5** The read gives 7 zero-filled days. ``test_the_read_gives_seven_zero_filled_days``.
* **A6** Org B reads none of org A. ``test_org_b_reads_none_of_the_counts_of_org_a``.
* **A7** A Redis fault never reaches the call or the run.
  ``test_a_fault_never_raises``, ``test_a_fault_never_fails_the_call``.
* **A8** A left-out turn adds one by reason. ``test_a_left_out_turn_counts_by_reason``,
  ``test_the_orchestrator_counts_each_reason_of_a_turn``.
* **A9** The route needs ``integrations:manage``, and a name of another org is 404:
  ``test_the_route_is_403_without_manage``, and ``test_integrations_connections_r8.py``.
* **A10** Names and counts only. ``test_no_argument_or_secret_reaches_a_key_or_a_log``.
* **A11** No raw Redis client, and the count sits after the call.
  ``test_the_new_code_holds_no_raw_redis`` and ``test_the_count_sits_after_the_call``.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from acb_common import mcp_use
from acb_common.db import clear_tenant
from acb_common.tenant_redis import TenantRedis, organization_scope

ORG_A = "org-a-csy8"
ORG_B = "org-b-csy8"
NOON = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


class FakeRedis:
    """The raw client under the wrapper: it sees only the wire keys."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}
        self.fail: set[str] = set()
        self.log: list[tuple[str, str]] = []

    def _go(self, op: str, key: str) -> None:
        self.log.append((op, key))
        if op in self.fail:
            raise ConnectionError("redis is down")

    async def incr(self, key: str, amount: int = 1) -> int:
        self._go("incr", key)
        self.data[key] = int(self.data.get(key, 0)) + amount
        return self.data[key]

    async def expire(self, key: str, seconds: int) -> int:
        self._go("expire", key)
        self.ttls[key] = seconds
        return 1

    async def set(self, key: str, value: Any, **kwargs: Any) -> bool:
        self._go("set", key)
        self.data[key] = str(value)
        if kwargs.get("ex"):
            self.ttls[key] = kwargs["ex"]
        return True

    async def get(self, key: str) -> Any:
        self._go("get", key)
        return self.data.get(key)

    async def mget(self, keys: list[str]) -> list[Any]:
        self._go("mget", ",".join(keys))
        return [self.data.get(k) for k in keys]


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(mcp_use, "_client", lambda: TenantRedis(fake))
    clear_tenant()
    yield fake
    clear_tenant()


# -- The count of a call ---------------------------------------------------------


async def test_one_call_adds_one(redis: FakeRedis) -> None:
    assert await mcp_use.count_call(ORG_A, "shop", now=NOON) is True
    assert redis.data == {
        "cc:org-a-csy8:mcp-use:shop:2026-11-20": 1,
        "cc:org-a-csy8:mcp-use-last:shop": str(int(NOON.timestamp())),
    }


async def test_two_calls_add_two(redis: FakeRedis) -> None:
    await mcp_use.count_call(ORG_A, "shop", now=NOON)
    await mcp_use.count_call(ORG_A, "shop", now=NOON)
    assert redis.data["cc:org-a-csy8:mcp-use:shop:2026-11-20"] == 2


async def test_the_keys_hold_the_tenant_prefix(redis: FakeRedis) -> None:
    await mcp_use.count_call(ORG_A, "shop", now=NOON)
    await mcp_use.count_left_out(ORG_A, "shop", "mail", now=NOON)
    assert sorted(redis.data) == [
        "cc:org-a-csy8:mcp-left-out:shop:mail:2026-11-20",
        "cc:org-a-csy8:mcp-use-last:shop",
        "cc:org-a-csy8:mcp-use:shop:2026-11-20",
    ]


async def test_the_ttl_is_eight_days(redis: FakeRedis) -> None:
    await mcp_use.count_call(ORG_A, "shop", now=NOON)
    await mcp_use.count_left_out(ORG_A, "shop", "unreachable", now=NOON)
    assert mcp_use.TTL_SECONDS == 8 * 24 * 3600
    assert set(redis.ttls) == set(redis.data)
    assert set(redis.ttls.values()) == {8 * 24 * 3600}


async def test_the_day_rolls_at_utc_midnight(redis: FakeRedis) -> None:
    before = datetime(2026, 11, 20, 23, 59, 59, tzinfo=UTC)
    after = datetime(2026, 11, 21, 0, 0, 0, tzinfo=UTC)
    await mcp_use.count_call(ORG_A, "shop", now=before)
    await mcp_use.count_call(ORG_A, "shop", now=after)
    assert redis.data["cc:org-a-csy8:mcp-use:shop:2026-11-20"] == 1
    assert redis.data["cc:org-a-csy8:mcp-use:shop:2026-11-21"] == 1


async def test_a_clock_with_a_zone_other_than_utc_still_counts_in_utc(redis: FakeRedis) -> None:
    from datetime import timedelta, timezone

    late_in_india = datetime(2026, 11, 21, 3, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    await mcp_use.count_call(ORG_A, "shop", now=late_in_india)
    assert "cc:org-a-csy8:mcp-use:shop:2026-11-20" in redis.data


async def test_a_name_that_is_not_a_server_name_counts_nothing(redis: FakeRedis) -> None:
    for bad in ("", "(unknown server)", "Shop", "a:b", "x" * 80):
        assert await mcp_use.count_call(ORG_A, bad, now=NOON) is False
    assert await mcp_use.count_left_out(ORG_A, "shop", "not a reason!", now=NOON) is False
    assert redis.data == {}


# -- The read ---------------------------------------------------------------------


async def test_the_read_gives_seven_zero_filled_days(redis: FakeRedis) -> None:
    await mcp_use.count_call(ORG_A, "shop", now=NOON)
    out = await mcp_use.read_use(ORG_A, "shop", ("mail", "unreachable"), now=NOON)
    assert out["available"] is True
    assert [d["date"] for d in out["days"]] == [
        "2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17",
        "2026-11-18", "2026-11-19", "2026-11-20"]
    assert [d["calls"] for d in out["days"]] == [0, 0, 0, 0, 0, 0, 1]
    assert all(d["left_out"] == {"mail": 0, "unreachable": 0} for d in out["days"])
    assert out["calls_7d"] == 1 and out["left_out_7d"] == {"mail": 0, "unreachable": 0}
    assert out["last_call_at"] == int(NOON.timestamp())


async def test_a_server_with_no_use_reads_zeros_and_no_last_call(redis: FakeRedis) -> None:
    out = await mcp_use.read_use(ORG_A, "shop", ("mail",), now=NOON)
    assert out["available"] is True and out["calls_7d"] == 0 and out["last_call_at"] is None
    assert len(out["days"]) == 7


async def test_a_count_older_than_seven_days_is_not_in_the_read(redis: FakeRedis) -> None:
    await mcp_use.count_call(ORG_A, "shop", now=datetime(2026, 11, 13, 12, tzinfo=UTC))
    out = await mcp_use.read_use(ORG_A, "shop", ("mail",), now=NOON)
    assert out["calls_7d"] == 0


async def test_the_read_sums_the_week(redis: FakeRedis) -> None:
    for day in (14, 17, 20, 20):
        await mcp_use.count_call(ORG_A, "shop", now=datetime(2026, 11, day, 9, tzinfo=UTC))
    out = await mcp_use.read_use(ORG_A, "shop", ("mail",), now=NOON)
    assert out["calls_7d"] == 4
    assert [d["calls"] for d in out["days"]] == [1, 0, 0, 1, 0, 0, 2]


async def test_org_b_reads_none_of_the_counts_of_org_a(redis: FakeRedis) -> None:
    await mcp_use.count_call(ORG_A, "shop", now=NOON)
    await mcp_use.count_left_out(ORG_A, "shop", "mail", now=NOON)
    out = await mcp_use.read_use(ORG_B, "shop", ("mail",), now=NOON)
    assert out["calls_7d"] == 0 and out["left_out_7d"] == {"mail": 0} and out["last_call_at"] is None
    for _op, key in redis.log:
        if _op == "mget":
            assert ORG_A not in key
    await mcp_use.count_call(ORG_B, "shop", now=NOON)
    assert (await mcp_use.read_use(ORG_A, "shop", ("mail",), now=NOON))["calls_7d"] == 1
    assert (await mcp_use.read_use(ORG_B, "shop", ("mail",), now=NOON))["calls_7d"] == 1


async def test_a_left_out_turn_counts_by_reason(redis: FakeRedis) -> None:
    await mcp_use.count_left_out(ORG_A, "shop", "mail", now=NOON)
    await mcp_use.count_left_out(ORG_A, "shop", "mail", now=NOON)
    await mcp_use.count_left_out(ORG_A, "shop", "unreachable", now=NOON)
    out = await mcp_use.read_use(ORG_A, "shop", ("mail", "unreachable", "reconnect"), now=NOON)
    assert out["left_out_7d"] == {"mail": 2, "unreachable": 1, "reconnect": 0}
    assert out["days"][-1]["left_out"] == {"mail": 2, "unreachable": 1, "reconnect": 0}
    assert out["calls_7d"] == 0


# -- A fault ----------------------------------------------------------------------


@pytest.mark.parametrize("op", ["incr", "expire", "set"])
async def test_a_fault_never_raises(redis: FakeRedis, op: str) -> None:
    redis.fail.add(op)
    with structlog.testing.capture_logs() as logs:
        assert await mcp_use.count_call(ORG_A, "shop", now=NOON) is False
        assert await mcp_use.count_left_out(ORG_A, "shop", "mail", now=NOON) is (op == "set")
    faults = [x for x in logs if x["event"] == "mcp_use.counter_fault"]
    assert faults and all(set(x) <= {"event", "log_level", "error", "op"} for x in faults)
    assert all(x["error"] == "ConnectionError" for x in faults)


async def test_a_fault_in_the_read_answers_zeros_and_not_available(redis: FakeRedis) -> None:
    redis.fail.add("mget")
    out = await mcp_use.read_use(ORG_A, "shop", ("mail",), now=NOON)
    assert out["available"] is False
    assert out["calls_7d"] == 0 and out["left_out_7d"] == {"mail": 0} and out["last_call_at"] is None
    assert [d["calls"] for d in out["days"]] == [0] * 7


async def test_a_client_that_cannot_be_built_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("no redis url")

    monkeypatch.setattr(mcp_use, "_client", boom)
    assert await mcp_use.count_call(ORG_A, "shop", now=NOON) is False
    assert (await mcp_use.read_use(ORG_A, "shop", ("mail",), now=NOON))["available"] is False


async def test_a_hung_redis_ends_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    class Hung(FakeRedis):
        async def incr(self, key: str, amount: int = 1) -> int:
            await asyncio.sleep(30)
            return 0

    monkeypatch.setattr(mcp_use, "_client", lambda: TenantRedis(Hung()))
    monkeypatch.setattr(mcp_use, "WRITE_DEADLINE_SECONDS", 0.05)
    assert await asyncio.wait_for(mcp_use.count_call(ORG_A, "shop", now=NOON), 5) is False


# -- The gate ---------------------------------------------------------------------

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("mcp", reason="mcp not installed")

from orchestrator import _maf_mcp, executor  # noqa: E402

from tests.unit import test_mcp_call_log as call_log  # noqa: E402


@pytest.fixture
def chat_gate_state():
    structlog.contextvars.clear_contextvars()
    executor._pending_user_input.clear()
    yield
    structlog.contextvars.clear_contextvars()
    executor._pending_user_input.clear()


async def _gate_run(gate: Any, fn: Any, ran: Any, answer: str | None = None) -> Any:
    ctx = call_log._ctx(fn)
    with organization_scope(ORG_A), call_log._card_channel() as queue:
        task = asyncio.create_task(gate.process(ctx, ran))
        if answer is not None:
            await call_log._answer_next(queue, answer)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)
    return ctx


async def test_the_gate_counts_each_outcome_of_a_chat_call(redis: FakeRedis, chat_gate_state) -> None:
    """The count follows the ``mcp.call`` line: every outcome of a chat call adds one."""
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    key = f"cc:{ORG_A}:mcp-use:srv:{day}"
    fn = call_log._function()
    cases = [
        (call_log._gate(fn, {"readOnlyHint": True}), call_log._Ran(), None),                    # ok
        (call_log._gate(fn, {"readOnlyHint": True}), call_log._Ran(RuntimeError("x")), None),  # error
        (call_log._gate(fn, {"readOnlyHint": True}), call_log._Ran(TimeoutError()), None),  # timeout
        (call_log._gate(fn, None, always_confirm=True), call_log._Ran(), "APPROVE"),           # card_approved
        (call_log._gate(fn, None, always_confirm=True), call_log._Ran(), "DENY"),              # card_denied
        (call_log._gate(fn, None, member=False), call_log._Ran(), None),                       # card_refused
    ]
    for n, (gate, ran, answer) in enumerate(cases, start=1):
        await _gate_run(gate, fn, ran, answer)
        assert redis.data[key] == n, (n, redis.data)
    assert f"cc:{ORG_A}:mcp-use-last:srv" in redis.data


async def test_a_job_call_adds_nothing(redis: FakeRedis, chat_gate_state) -> None:
    structlog.contextvars.bind_contextvars(source="cron")
    fn = call_log._function()
    await _gate_run(call_log._gate(fn, {"readOnlyHint": True}), fn, call_log._Ran())
    assert redis.data == {}


async def test_a_call_with_no_readable_arguments_counts_no_server(redis: FakeRedis, chat_gate_state) -> None:
    fn = call_log._function()
    ctx = SimpleNamespace(function=fn, arguments=object(), result=None)
    with organization_scope(ORG_A):
        await call_log._gate(fn, {"readOnlyHint": True}).process(ctx, call_log._Ran())
    assert redis.data == {}


async def test_a_fault_never_fails_the_call(redis: FakeRedis, chat_gate_state) -> None:
    redis.fail.update({"incr", "expire", "set"})
    fn = call_log._function()
    ran = call_log._Ran()
    ctx = await _gate_run(call_log._gate(fn, {"readOnlyHint": True}), fn, ran)
    assert ran.count == 1 and ctx.result is None
    # A call that raises still raises its own error, and not the fault.
    ran = call_log._Ran(ValueError("the tool"))
    ctx = call_log._ctx(fn)
    with organization_scope(ORG_A), pytest.raises(ValueError, match="the tool"):
        await call_log._gate(fn, {"readOnlyHint": True}).process(ctx, ran)


async def test_the_count_is_added_after_the_call_ran(redis: FakeRedis, chat_gate_state) -> None:
    seen: list[int] = []

    class Spy(call_log._Ran):
        async def __call__(self) -> None:
            seen.append(sum(1 for op, _ in redis.log if op == "incr"))
            await super().__call__()

    fn = call_log._function()
    await _gate_run(call_log._gate(fn, {"readOnlyHint": True}), fn, Spy())
    assert seen == [0]
    assert sum(1 for op, _ in redis.log if op == "incr") == 1


async def test_a_run_with_no_org_bound_counts_nothing(redis: FakeRedis, chat_gate_state) -> None:
    fn = call_log._function()
    ctx = call_log._ctx(fn)
    await call_log._gate(fn, {"readOnlyHint": True}).process(ctx, call_log._Ran())
    assert redis.data == {}


async def test_no_argument_or_secret_reaches_a_key_or_a_log(redis: FakeRedis, chat_gate_state) -> None:
    fn = call_log._function()
    with structlog.testing.capture_logs() as logs:
        await _gate_run(call_log._gate(fn, {"readOnlyHint": True}), fn, call_log._Ran())
        redis.fail.add("incr")
        await _gate_run(call_log._gate(fn, {"readOnlyHint": True}), fn, call_log._Ran())
    wire = repr(redis.data) + repr(redis.log) + repr(logs)
    assert call_log.SECRET_ARG not in wire
    # The tool name is not in a key either: the key holds the server and the day.
    assert "do" not in {k.split(":")[3] for k in redis.data if k.count(":") > 3}


# -- The orchestrator's left-out turn -----------------------------------------------

from orchestrator import _tool_injection  # noqa: E402


async def test_the_orchestrator_counts_each_reason_of_a_turn(redis: FakeRedis) -> None:
    reasons = {"shop": "mail", "crm": "unreachable", "bad name!": "mail"}
    with organization_scope(ORG_A):
        await _tool_injection._count_left_out(ORG_A, reasons)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    assert sorted(redis.data) == [
        f"cc:{ORG_A}:mcp-left-out:crm:unreachable:{day}",
        f"cc:{ORG_A}:mcp-left-out:shop:mail:{day}",
    ]
    assert set(redis.data.values()) == {1}


async def test_a_fault_in_the_left_out_count_never_raises(redis: FakeRedis) -> None:
    redis.fail.add("incr")
    await _tool_injection._count_left_out(ORG_A, {"shop": "mail"})
    await _tool_injection._count_left_out(ORG_A, {})
    await _tool_injection._count_left_out("", {"shop": "mail"})


def test_the_reasons_are_the_closed_list_of_the_brief() -> None:
    from acb_skills import server_brief

    assert set(mcp_use.LEFT_OUT_REASONS) == set(server_brief.REASONS)


# -- The route (hermetic half: the permission) ---------------------------------------


def _client(permissions: list[str]) -> Any:
    from acb_auth import UserContext, UserRole, build_access, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import integrations

    app = FastAPI()
    app.include_router(integrations.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email="member@example.test", role=UserRole.EXECUTIVE, access=build_access(permissions))
    return TestClient(app, raise_server_exceptions=False)


def test_the_route_is_403_without_manage() -> None:
    res = _client(["feature:integrations"]).get("/integrations/mcp/shop/use")
    assert res.status_code == 403, res.text
    assert "integrations:manage" in res.json()["detail"]


def test_the_route_is_404_with_no_tenant_bound() -> None:
    res = _client(["feature:integrations", "integrations:manage"]).get("/integrations/mcp/shop/use")
    assert res.status_code == 404, res.text


def test_the_route_takes_no_org_from_the_request() -> None:
    from gateway.routes import integrations

    route = next(r for r in integrations.router.routes if r.path == "/integrations/mcp/{name}/use")
    params = {p.name for p in route.dependant.query_params} | {p.name for p in route.dependant.body_params}
    assert params == set()
    assert route.methods == {"GET"}


# -- Fences -------------------------------------------------------------------------

_NEW = Path(mcp_use.__file__)
_MAF = Path(_maf_mcp.__file__)


def test_the_new_code_holds_no_raw_redis() -> None:
    src = _NEW.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m == "redis" or m.startswith("redis.") for m in imported), imported
    assert "from_url" not in src and "aioredis" not in src
    assert "get_tenant_redis" in src and "TenantKey" in src


def test_the_count_sits_after_the_call() -> None:
    """In ``process`` the count is awaited in ``finally``, after ``_gate`` (which runs the call)."""
    tree = ast.parse(_MAF.read_text(encoding="utf-8"))
    process = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                   and n.name == "process" and any(
                       isinstance(c, ast.Attribute) and c.attr == "_gate"
                       for c in ast.walk(n)))
    (try_node,) = [n for n in process.body if isinstance(n, ast.Try)]
    gate_calls = [c for b in try_node.body for c in ast.walk(b)
                  if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "_gate"]
    count_calls = [c for b in try_node.finalbody for c in ast.walk(b)
                   if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "count_mcp_call"]
    assert len(gate_calls) == 1 and len(count_calls) == 1
