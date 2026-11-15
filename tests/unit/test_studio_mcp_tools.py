"""The ``tools/list`` read of the gateway and its cache, the hermetic half (STU-B1b-2).

Spec: ``project-docs/specs/ai_studio.md`` §14 B1b-2. Code:
``gateway/connections/mcp_tools.py`` (the read, the cleaning and the cache),
``gateway/studio/mcp_actions.py::unique_tools`` (the stricter duplicate) and
``gateway/routes/studio/actions.py`` (the catalog GET and
``POST /studio/mcp/{server}/refresh``). The R8 half is
``test_studio_mcp_tools_r8.py``.

No test here makes a real network call. A counting fake takes the place of
``mcp_tools._sdk_list``, and ``outbound_guard.mcp_client`` raises unless a
test gives it a ``MockTransport``. The cache is a fake raw Redis under the
REAL ``TenantRedis`` wrapper, so the key discipline is the real one.

This file proves:

- one refresh makes one client call, and a catalog GET makes none, also on
  a cold cache and after a TTL ends;
- the cache keeps the last good list for 7 days. Past 300 seconds it is
  stale, and its rows are still served (review fix P2-2);
- each answer and the whole read have a byte cap, ONE deadline covers every
  page, and the read stops at the item cap (review fix P2-1);
- a late failure never drops the list of a newer refresh (P3-1);
- a refusal in a group or a cause chain is ``refused``, and a server key
  with a trailing new line is 404 (P3-2);
- a hostile description (a lone surrogate, a hidden character) never
  reaches the cache or a row, and the GET still answers 200;
- a client that raises or times out gives a 502, no row of that server, and
  leaves the cache of each other server;
- a list of 10,000 tools keeps 200, with ``truncated``;
- an annotation object of the SDK gives the same class as a mapping;
- a duplicate name takes the stricter class, in each order;
- a name with a dot or over 64 characters is left out, and the others stay;
- a server of another org is 404, and the org never comes from the body;
- a second refresh within the interval is 429;
- no answer holds a url, a header or an exception text;
- the real SDK path sends the stored headers through the pinned client only.

**The mutations (R7).** Each ran on 2026-11-15, then was restored. M12 to
M16 come from the second review (P1, P2-a and P2-b).

* M1 (a). ``mcp_tools._scope``: bind one shared org (``"shared"``). 11 tests
  go red, among them ``test_the_cache_of_one_org_never_serves_another``.
  The R8 file goes red too (see there).
* M2 (b). ``mcp_tools.catalog_tools``: on an empty index, refresh each
  server. 3 tests go red, among them ``test_a_get_never_calls_a_server[False]``.
* M3 (c). ``mcp_tools._description``: no ``strip_hidden``. 3 tests go red:
  both hostile cases and ``test_a_cut_never_leaves_a_hidden_joiner``.
* M4 (d). ``mcp_actions.unique_tools``: keep the first item. 3 tests go red,
  among them ``test_a_duplicate_name_takes_the_stricter_class[read_first]``.
* M5 (P2-1). ``_sdk_list``: call ``mcp_client`` with no budget. 5 tests go
  red: both cases of ``test_a_huge_or_endless_answer_ends_with_too_large``,
  ``test_the_total_cap_holds_over_many_small_answers`` and both cases of
  ``test_the_sdk_path_sends_the_headers_through_the_pinned_client``.
* M6 (P2-1). ``list_tools``: a deadline of 5 times ``READ_TIMEOUT_S``, one
  for each page. ``test_one_deadline_covers_every_page`` goes red.
* M7 (P2-1). ``_pages``: no stop at ``MAX_LISTED``.
  ``test_the_read_stops_at_the_item_cap`` goes red.
* M8 (P2-2). ``LIST_TTL_S = 300``. 3 tests go red:
  ``test_the_last_good_list_is_served_and_its_age_sets_stale``,
  ``test_one_old_list_makes_the_catalog_stale`` and
  ``test_catalog_rows_keep_the_rows_after_300_seconds``.
* M9 (P3-1). ``_forget``: no compare of the slot token.
  ``test_a_late_failure_never_drops_the_list_of_a_newer_refresh`` goes red.
* M10 (P3-2). ``_reason``: read the top exception only. 4 tests go red:
  two wrapped cases of ``test_a_failed_server_shows_no_row_and_leaves_the_others``
  and both cases of ``test_a_refusal_in_the_cause_chain_is_found``.
* M11 (P3-2). ``_server_key``: ``match`` in place of ``fullmatch``. The two
  ``%0A`` cases of ``test_a_server_key_that_the_registry_refuses_is_404`` go red.
* M12 (P1). ``_PinnedTransport``: no refusal of a ``Content-Encoding``. The 6
  cases of ``test_a_coded_answer_is_refused_before_one_byte_is_read`` go red.
* M13 (P1). No ``Accept-Encoding: identity``, in the transport and in
  ``_sdk_list``. Both cases of
  ``test_the_sdk_path_sends_the_headers_through_the_pinned_client`` go red.
* M14 (P2-a). ``_forget``: drop the list whatever its age.
  ``test_a_late_failure_never_drops_the_list_of_a_newer_refresh`` goes red.
* M15 (P2-a). ``_forget``: keep the list when the slot has expired (the old
  slot rule). ``test_a_failure_after_the_slot_expired_still_drops_the_old_list``
  and the late-failure test go red.
* M16 (P2-b). ``catalog_tools``: no compare of the identity hash.
  ``test_a_new_server_with_an_old_name_gets_no_cached_row`` goes red.

``test_a_cancel_of_the_read_reaches_the_caller`` is a guard, not a fence. The
old ``contextlib.suppress`` cleanup passes it too, so no mutation turns it red.
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantMismatch, TenantRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway import outbound_guard
from gateway.connections import mcp as mcp_registry
from gateway.connections import mcp_tools
from gateway.routes.studio import actions
from gateway.studio import mcp_actions, packages_store

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
SECRET = "Bearer SECRET-TOKEN-b1b2"
HOST = "secret-host.example.test"


# ── The fakes ──────────────────────────────────────────────────────────────


class RawRedis:
    """The raw commands of the cache, on a dict, with a clock for the TTL."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.expiry: dict[str, float] = {}
        self.now = 1000.0
        self.fail: BaseException | None = None

    def _live(self, name: str) -> bool:
        if self.fail is not None:
            raise self.fail
        end = self.expiry.get(name)
        if end is not None and end <= self.now:
            self.data.pop(name, None)
            self.expiry.pop(name, None)
        return name in self.data

    async def setex(self, name: str, seconds: int, value: Any) -> bool:
        self._live(name)
        self.data[name] = str(value)
        self.expiry[name] = self.now + int(seconds)
        return True

    async def set(self, name: str, value: Any, nx: bool = False,
                  ex: int | None = None, **_: Any) -> bool | None:
        if self._live(name) and nx:
            return None
        self.data[name] = str(value)
        if ex is not None:
            self.expiry[name] = self.now + int(ex)
        return True

    async def get(self, name: str) -> Any:
        return self.data.get(name) if self._live(name) else None

    async def mget(self, names: list[str]) -> list[Any]:
        return [self.data.get(n) if self._live(n) else None for n in names]

    async def ttl(self, name: str) -> int:
        if not self._live(name):
            return -2
        end = self.expiry.get(name)
        return -1 if end is None else int(end - self.now)

    async def delete(self, *names: str) -> int:
        gone = 0
        for n in names:
            if self._live(n):
                gone += 1
            self.data.pop(n, None)
            self.expiry.pop(n, None)
        return gone

    async def hset(self, name: str, field: str, value: Any) -> int:
        self._live(name)
        self.data.setdefault(name, {})[field] = str(value)
        return 1

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.data[name]) if self._live(name) else {}

    async def hdel(self, name: str, *fields: str) -> int:
        if not self._live(name):
            return 0
        found = self.data[name]
        gone = sum(1 for f in fields if found.pop(f, None) is not None)
        if not found:
            self.data.pop(name, None)
            self.expiry.pop(name, None)
        return gone

    async def expire(self, name: str, seconds: int) -> bool:
        if not self._live(name):
            return False
        self.expiry[name] = self.now + int(seconds)
        return True

    def tools_key(self, org: str, server: str) -> str:
        return f"{tenant_redis.KEY_ROOT}:{org}:{mcp_tools.NAMESPACE}:tools:{server}"


def _server(name: str, org: str, *, always_confirm: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, enabled=True, always_confirm=always_confirm, read_only_key=False,
        url=f"https://{HOST}/{org}/{name}", headers={"Authorization": SECRET}, oauth=False)


def _tool(name: str, annotations: Any = None, description: Any = "") -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "description": description}
    if annotations is not None:
        item["annotations"] = annotations
    return item


READ = {"readOnlyHint": True}
WRITE = {"destructiveHint": True}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Two orgs, each with its servers, a counting lister, the fake Redis and
    a client of the real router. ``world.bound`` is the org of the session."""
    raw = RawRedis()
    w = SimpleNamespace(
        raw=raw, calls=[], tools={}, fail={}, bound=ORG_A,
        servers={ORG_A: [_server("github", ORG_A), _server("linear", ORG_A)],
                 ORG_B: [_server("github", ORG_B), _server("secret-b", ORG_B)]},
        registry_reads=[],
    )
    monkeypatch.setattr(mcp_tools, "_redis", lambda: TenantRedis(raw))
    monkeypatch.setattr(mcp_tools, "_now", lambda: raw.now)

    async def _servers_for_org(org: str, *, oauth_wanted: Any = None) -> list[Any]:
        w.registry_reads.append(("servers_for_org", org))
        return [s for s in w.servers.get(org, []) if oauth_wanted is None or oauth_wanted(s)]

    async def _enabled_meta(org: str) -> list[Any]:
        w.registry_reads.append(("enabled_meta", org))
        return [SimpleNamespace(**{**vars(s), "headers": {}}) for s in w.servers.get(org, [])]

    monkeypatch.setattr(mcp_registry, "servers_for_org", _servers_for_org)
    monkeypatch.setattr(mcp_registry, "enabled_meta", _enabled_meta)

    async def _lister(server: Any) -> list[Any]:
        w.calls.append((server.name, server.url))
        if server.name in w.fail:
            raise w.fail[server.name]
        return w.tools.get(server.url, [])

    monkeypatch.setattr(mcp_tools, "_sdk_list", _lister)

    async def _no_client(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a real MCP client was built")

    monkeypatch.setattr(outbound_guard, "mcp_client", _no_client)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")

    @asynccontextmanager
    async def _session() -> Any:
        class _DB:
            async def execute(self, *_a: Any, **_k: Any) -> Any:
                return SimpleNamespace(fetchall=lambda: [])
        yield _DB()

    monkeypatch.setattr(actions, "_tenant_session", _session)

    async def _packages(*_a: Any, **_k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(packages_store, "list_package_actions", _packages)
    monkeypatch.setattr(actions, "current_tenant", lambda: w.bound)

    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email="m@example.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:studio"]), organization_id=w.bound)
    w.client = TestClient(app, raise_server_exceptions=False)
    return w


def _give(w: SimpleNamespace, org: str, server: str, tools: list[Any]) -> None:
    w.tools[f"https://{HOST}/{org}/{server}"] = tools


def _refresh(w: SimpleNamespace, server: str, org: str | None = None, **kw: Any) -> Any:
    if org is not None:
        w.bound = org
    return w.client.post(f"/studio/mcp/{server}/refresh", **kw)


def _get(w: SimpleNamespace, org: str | None = None) -> Any:
    if org is not None:
        w.bound = org
    return w.client.get("/studio/actions")


def _mcp(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(r["id"], r["class"]) for r in body["actions"] if r["source"] == "mcp"]


# ── One call for a refresh, none for a GET ─────────────────────────────────


def test_one_refresh_makes_one_call_and_fills_the_cache(world) -> None:
    _give(world, ORG_A, "github", [_tool("get_issue", READ), _tool("create_issue", WRITE)])
    r = _refresh(world, "github")
    assert r.status_code == 200, r.text
    assert r.json() == {"server": "github", "ok": True, "tools": 2, "truncated": False}
    assert world.calls == [("github", f"https://{HOST}/{ORG_A}/github")]
    body = _get(world).json()
    assert _mcp(body) == [("mcp:github.get_issue", "A"), ("mcp:github.create_issue", "B")]
    assert body["stale"] is True  # linear has no list yet
    assert len(world.calls) == 1


@pytest.mark.parametrize("warm", [False, True])
def test_a_get_never_calls_a_server(world, warm: bool) -> None:
    """Cold: the index is empty. Warm: one server is cached and one is not.
    Neither GET calls a client."""
    _give(world, ORG_A, "github", [_tool("get_issue", READ)])
    if warm:
        assert _refresh(world, "github").status_code == 200
    before = len(world.calls)
    for _ in range(3):
        assert _get(world).status_code == 200
    assert len(world.calls) == before
    assert ("servers_for_org", ORG_A) not in world.registry_reads[before:]


def test_a_cold_cache_gives_no_row_and_stale(world) -> None:
    body = _get(world).json()
    assert _mcp(body) == [] and body["stale"] is True
    assert world.calls == []
    # An empty index opens no database read either.
    assert world.registry_reads == []


def test_the_get_is_not_stale_when_each_server_is_cached(world) -> None:
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _give(world, ORG_A, "linear", [_tool("b", READ)])
    _refresh(world, "github")
    _refresh(world, "linear")
    body = _get(world).json()
    assert body["stale"] is False
    assert _mcp(body) == [("mcp:github.a", "A"), ("mcp:linear.b", "A")]


def test_the_last_good_list_is_served_and_its_age_sets_stale(world) -> None:
    """Review fix P2-2. The list lives 7 days. Past 300 s it is stale, and the
    rows are still served, so ``catalog_rows`` does not change every 5 min."""
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _give(world, ORG_A, "linear", [_tool("b", READ)])
    _refresh(world, "github")
    _refresh(world, "linear")
    key = world.raw.tools_key(ORG_A, "github")
    assert world.raw.expiry[key] - world.raw.now == mcp_tools.LIST_TTL_S == 7 * 24 * 3600
    assert mcp_tools.FRESH_S == 300
    world.raw.now += 299
    body = _get(world).json()
    assert body["stale"] is False
    assert _mcp(body) == [("mcp:github.a", "A"), ("mcp:linear.b", "A")]
    world.raw.now += 2
    body = _get(world).json()
    assert body["stale"] is True
    assert _mcp(body) == [("mcp:github.a", "A"), ("mcp:linear.b", "A")]
    world.raw.now += mcp_tools.LIST_TTL_S
    body = _get(world).json()
    assert _mcp(body) == [] and body["stale"] is True
    assert len(world.calls) == 2


def test_one_old_list_makes_the_catalog_stale(world) -> None:
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _give(world, ORG_A, "linear", [_tool("b", READ)])
    _refresh(world, "github")
    world.raw.now += 400
    _refresh(world, "linear")
    body = _get(world).json()
    assert body["stale"] is True and len(_mcp(body)) == 2
    _refresh(world, "github")
    assert _get(world).json()["stale"] is False


async def test_catalog_rows_keep_the_rows_after_300_seconds(world) -> None:
    """The callers of ``catalog_rows`` (the skill check, the package publish,
    ``_named_row``) see the same id between two refreshes."""
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _refresh(world, "github")
    world.raw.now += 3600
    rows = await actions.catalog_rows()
    assert "mcp:github.a" in {r["id"] for r in rows} and rows.stale is True


def test_an_always_confirm_server_makes_each_cached_tool_b(world) -> None:
    """The class comes from the server list of NOW, not from the cache."""
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _refresh(world, "github")
    world.servers[ORG_A][0].always_confirm = True
    assert _mcp(_get(world).json()) == [("mcp:github.a", "B")]


def test_a_disconnected_server_shows_no_cached_row(world) -> None:
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _refresh(world, "github")
    world.servers[ORG_A] = [s for s in world.servers[ORG_A] if s.name != "github"]
    assert _mcp(_get(world).json()) == []


# ── The org ────────────────────────────────────────────────────────────────


def test_a_server_of_another_org_is_404(world) -> None:
    _give(world, ORG_B, "secret-b", [_tool("a", READ)])
    r = _refresh(world, "secret-b", org=ORG_A)
    assert r.status_code == 404 and r.json() == {"detail": "Not found"}
    assert world.calls == []


def test_the_org_never_comes_from_the_body_or_the_query(world) -> None:
    _give(world, ORG_B, "secret-b", [_tool("a", READ)])
    r = world.client.post(f"/studio/mcp/secret-b/refresh?organization_id={ORG_B}",
                          json={"organization_id": ORG_B, "org": ORG_B})
    assert r.status_code == 404
    assert world.calls == []


def test_the_cache_of_one_org_never_serves_another(world) -> None:
    """Both orgs hold a server named ``github``. A refresh of org B must not
    give org A a row."""
    _give(world, ORG_B, "github", [_tool("org_b_tool", READ)])
    assert _refresh(world, "github", org=ORG_B).status_code == 200
    body = _get(world, org=ORG_A).json()
    assert _mcp(body) == [] and body["stale"] is True
    assert _mcp(_get(world, org=ORG_B).json()) == [("mcp:github.org_b_tool", "A")]


async def test_a_key_of_org_a_cannot_be_read_for_org_b(world) -> None:
    client = TenantRedis(world.raw)
    with tenant_redis.organization_scope(ORG_A):
        key_a = tenant_redis.key(mcp_tools.NAMESPACE, "tools", "github")
    with tenant_redis.organization_scope(ORG_B), pytest.raises(TenantMismatch):
        await client.get(key_a)


def test_a_dark_org_gets_404_and_no_call(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "")
    _give(world, ORG_A, "github", [_tool("a", READ)])
    assert _refresh(world, "github").status_code == 404
    assert world.calls == [] and world.registry_reads == []


@pytest.mark.parametrize("name", ["Bad.Name", "x" * 60, "a_b", "github%0A", "github%0a"])
def test_a_server_key_that_the_registry_refuses_is_404(world, name: str) -> None:
    assert _refresh(world, name).status_code == 404
    assert world.calls == []


# ── A failure stays with its server ────────────────────────────────────────


@pytest.mark.parametrize("fault,code", [
    (RuntimeError(f"boom {SECRET} https://{HOST}/x"), "mcp_unreachable"),
    (outbound_guard.OutboundRefused(HOST, "not public"), "mcp_refused"),
    (ExceptionGroup("tg", [ConnectionError(SECRET)]), "mcp_unreachable"),
    (ExceptionGroup("tg", [ExceptionGroup("in", [outbound_guard.OutboundRefused(HOST, "x")])]),
     "mcp_refused"),
    (outbound_guard.AnswerTooLarge(HOST, "too large"), "mcp_too_large"),
    (ExceptionGroup("tg", [outbound_guard.AnswerTooLarge(HOST, "too large")]),
     "mcp_too_large"),
])
def test_a_failed_server_shows_no_row_and_leaves_the_others(world, fault, code) -> None:
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _give(world, ORG_A, "linear", [_tool("b", READ)])
    _refresh(world, "github")
    _refresh(world, "linear")
    world.raw.now += mcp_tools.REFRESH_INTERVAL_S + 1
    world.fail["github"] = fault
    r = _refresh(world, "github")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == code
    assert SECRET not in r.text and HOST not in r.text and "boom" not in r.text
    assert _mcp(_get(world).json()) == [("mcp:linear.b", "A")]


def _caused(cause: BaseException, *, explicit: bool) -> BaseException:
    try:
        try:
            raise cause
        except BaseException as inner:
            if explicit:
                raise RuntimeError("wrapped") from inner
            raise RuntimeError("wrapped")  # noqa: B904 - the implicit context is the case
    except RuntimeError as outer:
        return outer


@pytest.mark.parametrize("explicit", [True, False])
def test_a_refusal_in_the_cause_chain_is_found(explicit: bool) -> None:
    """Review fix P3-2: a refusal in ``__cause__`` or ``__context__``."""
    exc = _caused(outbound_guard.OutboundRefused(HOST, "not public"), explicit=explicit)
    assert mcp_tools._reason(exc) == "refused"
    group = ExceptionGroup("tg", [RuntimeError("x"), exc])
    assert mcp_tools._reason(group) == "refused"
    assert mcp_tools._reason(RuntimeError("plain")) == "unreachable"


def test_a_late_failure_never_drops_the_list_of_a_newer_refresh(world) -> None:
    """Review fixes P3-1 and P2-a. Refresh 1 holds the slot and waits. The
    slot ends, refresh 2 takes a new slot and stores its list, and refresh 1
    then fails. The list of refresh 2 is newer than the start of refresh 1,
    so it stays."""
    gate = asyncio.Event()

    async def _slow_then_fail(_server: Any) -> list[Any]:
        await gate.wait()
        raise RuntimeError("late")

    async def _good(_server: Any) -> list[Any]:
        return [_tool("fresh", READ)]

    async def _run() -> Any:
        first = asyncio.ensure_future(mcp_tools.refresh(ORG_A, "github", lister=_slow_then_fail))
        slot = f"{tenant_redis.KEY_ROOT}:{ORG_A}:{mcp_tools.NAMESPACE}:refresh:github"
        for _ in range(1000):  # bounded, so a broken key fails and never hangs
            if world.raw.data.get(slot):
                break
            await asyncio.sleep(0)
        assert world.raw.data.get(slot), "refresh 1 took no slot"
        world.raw.now += mcp_tools.REFRESH_INTERVAL_S + 1
        second = await mcp_tools.refresh(ORG_A, "github", lister=_good)
        gate.set()
        return second, await first

    second, first = asyncio.run(_run())
    assert second.ok is True and first.ok is False
    assert _mcp(_get(world).json()) == [("mcp:github.fresh", "A")]


def test_a_failure_after_the_slot_expired_still_drops_the_old_list(world) -> None:
    """Review fix P2-a. A list from before the refresh, a read that outlasts
    the slot, and no newer refresh: the failure drops the old list, so a
    server that now fails does not keep its rows for 7 days."""
    _give(world, ORG_A, "github", [_tool("old", READ)])
    _refresh(world, "github")
    world.raw.now += mcp_tools.REFRESH_INTERVAL_S + 1
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def _outlasts_the_slot(_server: Any) -> list[Any]:
        entered.set()
        await gate.wait()
        raise RuntimeError("down")

    async def _run() -> Any:
        first = asyncio.ensure_future(
            mcp_tools.refresh(ORG_A, "github", lister=_outlasts_the_slot))
        await asyncio.wait_for(entered.wait(), 3)
        assert await _slot_live(world)
        world.raw.now += mcp_tools.REFRESH_INTERVAL_S + 10  # the slot ends
        assert not await _slot_live(world)
        gate.set()
        return await first

    assert asyncio.run(_run()).ok is False
    body = _get(world).json()
    assert _mcp(body) == [] and body["stale"] is True


async def _slot_live(world: Any) -> bool:
    return await world.raw.get(
        f"{tenant_redis.KEY_ROOT}:{ORG_A}:{mcp_tools.NAMESPACE}:refresh:github") is not None


def test_a_new_server_with_an_old_name_gets_no_cached_row(world) -> None:
    """Review fix P2-b. The member removes ``github`` at url A and connects a
    new ``github`` at url B. The list of A is never served as rows of B."""
    _give(world, ORG_A, "github", [_tool("from_a", READ)])
    _refresh(world, "github")
    assert _mcp(_get(world).json()) == [("mcp:github.from_a", "A")]
    world.servers[ORG_A][0].url = f"https://other-{HOST}/{ORG_A}/github"
    body = _get(world).json()
    assert _mcp(body) == [] and body["stale"] is True
    _give(world, ORG_A, "github", [])
    world.tools[f"https://other-{HOST}/{ORG_A}/github"] = [_tool("from_b", READ)]
    world.raw.now += mcp_tools.REFRESH_INTERVAL_S + 1
    assert _refresh(world, "github").status_code == 200
    assert _mcp(_get(world).json()) == [("mcp:github.from_b", "A")]


def test_the_cached_identity_holds_no_url() -> None:
    server = SimpleNamespace(url=f"https://{HOST}/x")
    ident = mcp_tools._identity(server)
    assert len(ident) == 32 and HOST not in ident
    assert ident != mcp_tools._identity(SimpleNamespace(url=f"https://{HOST}/y"))


def test_a_failure_of_the_slot_holder_drops_its_list(world) -> None:
    """Not vacuous: with no newer refresh, a failure still drops the list."""
    _give(world, ORG_A, "github", [_tool("a", READ)])
    _refresh(world, "github")
    world.raw.now += mcp_tools.REFRESH_INTERVAL_S + 1
    world.fail["github"] = RuntimeError("down")
    assert _refresh(world, "github").status_code == 502
    assert _mcp(_get(world).json()) == []


def test_a_server_that_times_out_shows_no_row(world, monkeypatch) -> None:
    monkeypatch.setattr(mcp_tools, "READ_TIMEOUT_S", 0.05)

    async def _slow(server: Any) -> list[Any]:
        world.calls.append((server.name, server.url))
        await asyncio.sleep(5)
        return [_tool("never")]

    monkeypatch.setattr(mcp_tools, "_sdk_list", _slow)
    _give(world, ORG_A, "linear", [])
    started = time.monotonic()
    r = _refresh(world, "github")
    assert time.monotonic() - started < 3
    assert r.status_code == 502 and r.json()["detail"]["code"] == "mcp_timeout"
    assert _mcp(_get(world).json()) == []


def test_a_redis_outage_keeps_the_catalog_up(world) -> None:
    world.raw.fail = ConnectionError("redis down")
    body = _get(world).json()
    assert _mcp(body) == [] and body["stale"] is True
    r = _refresh(world, "github")
    assert r.status_code == 503 and "redis" not in r.text
    assert world.calls == []


# ── The rate limit ─────────────────────────────────────────────────────────


def test_a_second_refresh_within_the_interval_is_429(world) -> None:
    _give(world, ORG_A, "github", [_tool("a", READ)])
    assert _refresh(world, "github").status_code == 200
    r = _refresh(world, "github")
    assert r.status_code == 429
    assert 0 < int(r.headers["Retry-After"]) <= mcp_tools.REFRESH_INTERVAL_S
    assert len(world.calls) == 1
    # Another server of the org has its own slot.
    assert _refresh(world, "linear").status_code == 200
    world.raw.now += mcp_tools.REFRESH_INTERVAL_S + 1
    assert _refresh(world, "github").status_code == 200
    assert len(world.calls) == 3


def test_the_slot_of_one_org_does_not_hold_another(world) -> None:
    assert _refresh(world, "github", org=ORG_A).status_code == 200
    assert _refresh(world, "github", org=ORG_B).status_code == 200


# ── Each tool is data ──────────────────────────────────────────────────────


@pytest.mark.parametrize("hostile", [
    "Read one issue.\ud800",
    "Read​ one⁦ issue\U000e0041\U000e0042.",
])
def test_a_hostile_description_never_reaches_the_cache_or_a_row(world, hostile) -> None:
    _give(world, ORG_A, "github", [_tool("get_issue", READ, hostile)])
    assert _refresh(world, "github").status_code == 200
    stored = world.raw.data[world.raw.tools_key(ORG_A, "github")]
    cached = json.loads(stored)["tools"][0]["description"]
    from acb_skills.hidden_text import find_hidden

    assert find_hidden(cached) == []
    r = _get(world)
    assert r.status_code == 200
    row = next(x for x in r.json()["actions"] if x["source"] == "mcp")
    assert find_hidden(row["description"]) == []
    assert row["description"].startswith("Read")


def test_a_cut_never_leaves_a_hidden_joiner() -> None:
    text = "a" * (mcp_tools.DESCRIPTION_CHARS - 1) + "‍b"
    listed = mcp_tools.clean_tools([_tool("t", READ, text)])
    from acb_skills.hidden_text import find_hidden

    assert find_hidden(listed.tools[0]["description"]) == []
    assert len(listed.tools[0]["description"]) <= mcp_tools.DESCRIPTION_CHARS


def test_a_list_of_10000_tools_keeps_200(world) -> None:
    _give(world, ORG_A, "github", [_tool(f"t{i}", READ, "x" * 5000) for i in range(10_000)])
    r = _refresh(world, "github")
    assert r.json() == {"server": "github", "ok": True, "tools": 200, "truncated": True}
    value = json.loads(world.raw.data[world.raw.tools_key(ORG_A, "github")])
    assert len(value["tools"]) == 200 and value["truncated"] is True
    assert all(len(t["description"]) == mcp_tools.DESCRIPTION_CHARS for t in value["tools"])
    assert len(_mcp(_get(world).json())) == 200


def test_an_sdk_annotation_object_gives_the_class_of_a_mapping() -> None:
    from mcp import types

    sdk = types.Tool(name="read", inputSchema={"type": "object"},
                     annotations=types.ToolAnnotations(readOnlyHint=True), description="R.")
    plain = SimpleNamespace(name="write", description="W.",
                            annotations=SimpleNamespace(readOnlyHint=True, destructiveHint=True))
    junk = SimpleNamespace(name="junk", annotations=SimpleNamespace(readOnlyHint="true"))
    listed = mcp_tools.clean_tools([sdk, plain, junk])
    assert [t["annotations"] for t in listed.tools] == [
        {"readOnlyHint": True}, {"readOnlyHint": True, "destructiveHint": True}, None]
    rows = mcp_actions.mcp_rows([_server("github", ORG_A)], {"github": listed.tools})
    assert [(r["mcp_tool"], r["class"]) for r in rows] == [
        ("read", "A"), ("write", "B"), ("junk", "B")]


@pytest.mark.parametrize("order", ["read_first", "write_first"])
def test_a_duplicate_name_takes_the_stricter_class(world, order: str) -> None:
    pair = [_tool("t", READ, "safe"), _tool("t", WRITE, "wipe")]
    if order == "write_first":
        pair.reverse()
    _give(world, ORG_A, "github", [*pair, _tool("u", READ)])
    _refresh(world, "github")
    value = json.loads(world.raw.data[world.raw.tools_key(ORG_A, "github")])
    assert [t["name"] for t in value["tools"]] == ["t", "u"]
    assert _mcp(_get(world).json()) == [("mcp:github.t", "B"), ("mcp:github.u", "A")]


def test_duplicates_count_once_toward_the_cap() -> None:
    items = [_tool("same", READ) for _ in range(500)] + [_tool("same", WRITE)]
    listed = mcp_tools.clean_tools(items)
    assert [t["name"] for t in listed.tools] == ["same"] and listed.truncated is False
    assert listed.tools[0]["annotations"] == WRITE


@pytest.mark.parametrize("bad", ["a.b", "x" * 65, "has space", "ünï", "", None, 7])
def test_a_name_that_the_grammar_refuses_is_left_out(world, bad: Any) -> None:
    """Trap 4. The grammar keeps refusing a dot and a name over 64
    characters (fail closed). The other tools of the server stay."""
    _give(world, ORG_A, "github", [_tool("ok_one", READ), {"name": bad}, _tool("x" * 64, READ)])
    r = _refresh(world, "github")
    assert r.json()["tools"] == 2
    assert [i for i, _ in _mcp(_get(world).json())] == [
        "mcp:github.ok_one", f"mcp:github.{'x' * 64}"]


def test_a_cached_tool_holds_three_fields_only() -> None:
    item = {"name": "t", "description": "d", "annotations": READ,
            "inputSchema": {"x": 1}, "url": f"https://{HOST}", "headers": {"A": SECRET}}
    assert mcp_tools.clean_tools([item]).tools == [
        {"name": "t", "description": "d", "annotations": READ}]


def test_a_damaged_cache_value_reads_as_cold(world) -> None:
    key = world.raw.tools_key(ORG_A, "github")
    world.raw.data[key] = "not json"
    index = f"{tenant_redis.KEY_ROOT}:{ORG_A}:{mcp_tools.NAMESPACE}:index"
    world.raw.data[index] = {"github": "1"}
    body = _get(world).json()
    assert _mcp(body) == [] and body["stale"] is True


# ── The real SDK path, on a MockTransport ──────────────────────────────────


def _mcp_server(pages: int, seen: list[httpx.Request]) -> httpx.MockTransport:
    """A minimal MCP server: JSON answers, and one tool on each page."""

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method != "POST":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
        else:
            cursor = int((msg.get("params") or {}).get("cursor") or 0)
            result = {"tools": [{"name": f"p{cursor}", "inputSchema": {"type": "object"},
                                 "annotations": {"readOnlyHint": True}}]}
            if cursor + 1 < pages:
                result["nextCursor"] = str(cursor + 1)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": result})

    return httpx.MockTransport(handle)


@pytest.mark.parametrize("pages,expected", [(2, 2), (50, mcp_tools.MAX_PAGES)])
async def test_the_sdk_path_sends_the_headers_through_the_pinned_client(
        monkeypatch, pages: int, expected: int) -> None:
    real_guard = outbound_guard  # the world fixture is not in use here
    seen: list[httpx.Request] = []
    monkeypatch.setattr(real_guard, "_resolve", lambda _h, _p: ["93.184.216.34"])
    monkeypatch.setattr(real_guard, "_is_local_address", lambda _a: False)
    monkeypatch.setattr(real_guard, "_mcp_inner_transport", lambda: _mcp_server(pages, seen))
    built: list[str] = []
    real_client = real_guard.mcp_client

    async def _counted(url: str, **kw: Any) -> Any:
        built.append(url)
        assert isinstance(kw.get("budget"), outbound_guard.AnswerBudget)
        return await real_client(url, **kw)

    monkeypatch.setattr(real_guard, "mcp_client", _counted)
    server = _server("github", ORG_A)
    server.url = "https://mcp.example.com/mcp"
    # A stored header cannot ask for a coded answer (review fix P1).
    server.headers = {"Authorization": SECRET, "Accept-Encoding": "gzip"}
    items = await mcp_tools._sdk_list(server)
    assert built == ["https://mcp.example.com/mcp"]
    assert [i.name for i in items] == [f"p{n}" for n in range(expected)]
    posts = [r for r in seen if r.method == "POST"]
    assert posts and all(r.headers["authorization"] == SECRET for r in posts)
    assert all(r.headers["accept-encoding"] == "identity" for r in seen)
    assert all(r.url.host == "93.184.216.34" and r.headers["host"] == "mcp.example.com"
               for r in posts)


def _guard_on(monkeypatch, handler: Any) -> None:
    monkeypatch.setattr(outbound_guard, "_resolve", lambda _h, _p: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    monkeypatch.setattr(outbound_guard, "_mcp_inner_transport",
                        lambda: httpx.MockTransport(handler))


def _rpc(msg: dict[str, Any], result: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": result})


def _init(msg: dict[str, Any]) -> httpx.Response:
    return _rpc(msg, {"protocolVersion": msg["params"]["protocolVersion"],
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake", "version": "1"}})


async def _list_of(monkeypatch, handler: Any) -> Any:
    """``list_tools`` of one server, with the real ``_sdk_list`` on *handler*."""
    _guard_on(monkeypatch, handler)
    server = _server("github", ORG_A)
    server.url = "https://mcp.example.com/mcp"

    async def _servers(_org: str, **_k: Any) -> list[Any]:
        return [server]

    monkeypatch.setattr(mcp_registry, "servers_for_org", _servers)
    return await mcp_tools.list_tools(ORG_A, "github")


CHUNK = 64 * 1024


@pytest.mark.parametrize("kind", ["json_50_mib", "endless_sse"])
async def test_a_huge_or_endless_answer_ends_with_too_large(monkeypatch, kind: str) -> None:
    """Review fix P2-1. A 50 MiB body and an endless SSE stream each end with
    ``too_large``, fast, and the read pulls only about the cap of bytes."""
    monkeypatch.setattr(mcp_tools, "READ_TIMEOUT_S", 5.0)
    pulled = {"bytes": 0}

    async def _body(limit: int | None) -> Any:
        sent = 0
        while limit is None or sent < limit:
            pulled["bytes"] += CHUNK
            sent += CHUNK
            yield (b"data: " + b"x" * (CHUNK - 8) + b"\n\n") if limit is None else b" " * CHUNK
            await asyncio.sleep(0)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return _init(msg)
        if kind == "json_50_mib":
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=_body(50 * 1024 * 1024))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_body(None))

    started = time.monotonic()
    with pytest.raises(mcp_tools.ListFailed) as failed:
        await _list_of(monkeypatch, handle)
    assert failed.value.reason == "too_large"
    assert time.monotonic() - started < 4
    assert pulled["bytes"] <= mcp_tools.MAX_ANSWER_BYTES + 2 * CHUNK


async def test_the_total_cap_holds_over_many_small_answers(monkeypatch) -> None:
    """Each page is under the cap of one answer, and the pages together pass
    the cap of the read."""
    monkeypatch.setattr(mcp_tools, "MAX_READ_BYTES", 300 * 1024)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return _init(msg)
        cursor = int((msg.get("params") or {}).get("cursor") or 0)
        return _rpc(msg, {"tools": [{"name": f"t{cursor}", "inputSchema": {"type": "object"},
                                     "description": "d" * 200_000}],
                          "nextCursor": str(cursor + 1)})

    with pytest.raises(mcp_tools.ListFailed) as failed:
        await _list_of(monkeypatch, handle)
    assert failed.value.reason == "too_large"


async def test_one_deadline_covers_every_page(monkeypatch) -> None:
    """Each page answers in 0.3 s, under the deadline. Five pages together
    pass the deadline of 1 s, so the read ends with ``timeout``."""
    monkeypatch.setattr(mcp_tools, "READ_TIMEOUT_S", 1.0)

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return _init(msg)
        await asyncio.sleep(0.3)
        cursor = int((msg.get("params") or {}).get("cursor") or 0)
        return _rpc(msg, {"tools": [{"name": f"t{cursor}", "inputSchema": {"type": "object"}}],
                          "nextCursor": str(cursor + 1)})

    started = time.monotonic()
    with pytest.raises(mcp_tools.ListFailed) as failed:
        await _list_of(monkeypatch, handle)
    assert failed.value.reason == "timeout"
    assert time.monotonic() - started < 2.5


async def test_the_read_stops_at_the_item_cap(monkeypatch) -> None:
    """Pages of 600 tools: the read keeps MAX_LISTED + 1 items and stops."""
    pages: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return _init(msg)
        cursor = int((msg.get("params") or {}).get("cursor") or 0)
        pages.append(cursor)
        return _rpc(msg, {"tools": [{"name": f"p{cursor}_{i}", "inputSchema": {"type": "object"}}
                                    for i in range(600)], "nextCursor": str(cursor + 1)})

    listed = await _list_of(monkeypatch, handle)
    assert pages == [0, 1]
    assert len(listed.tools) == mcp_tools.MAX_TOOLS and listed.truncated is True


def _gzip_bomb(inflated: int) -> bytes:
    """Gzip of *inflated* spaces, built in chunks, so the test holds no copy."""
    import zlib

    packer = zlib.compressobj(9, zlib.DEFLATED, 31)
    block = b" " * (1024 * 1024)
    parts = [packer.compress(block) for _ in range(inflated // len(block))]
    parts.append(packer.flush())
    return b"".join(parts)


@pytest.mark.parametrize("coding", ["gzip", "deflate", "br", "GZIP", "x-gzip", "gzip, identity"])
async def test_a_coded_answer_is_refused_before_one_byte_is_read(monkeypatch, coding) -> None:
    """Review fix P1. The cap counts wire bytes, and httpx inflates a coded
    answer after the transport. So any ``Content-Encoding`` but identity is
    refused before the body is read: 120 MiB of gzip spaces, sent as about
    120 KiB, never inflates."""
    monkeypatch.setattr(mcp_tools, "READ_TIMEOUT_S", 3.0)
    bomb = _gzip_bomb(120 * 1024 * 1024)
    assert len(bomb) < mcp_tools.MAX_ANSWER_BYTES
    pulled = {"bytes": 0}

    async def _body() -> Any:
        for at in range(0, len(bomb), CHUNK):
            pulled["bytes"] += CHUNK
            yield bomb[at:at + CHUNK]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        return httpx.Response(200, headers={"content-type": "application/json",
                                            "content-encoding": coding}, content=_body())

    started = time.monotonic()
    with pytest.raises(mcp_tools.ListFailed) as failed:
        await _list_of(monkeypatch, handle)
    assert failed.value.reason == "too_large"
    assert pulled["bytes"] == 0
    assert time.monotonic() - started < 2.5


@pytest.mark.parametrize("coding", [None, "identity", " Identity "])
async def test_an_identity_answer_still_works(monkeypatch, coding) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        answer = (_init(msg) if msg["method"] == "initialize" else
                  _rpc(msg, {"tools": [{"name": "t", "inputSchema": {"type": "object"}}]}))
        if coding is not None:
            answer.headers["content-encoding"] = coding
        return answer

    listed = await _list_of(monkeypatch, handle)
    assert [t["name"] for t in listed.tools] == ["t"]


async def test_a_run_without_a_budget_keeps_its_headers(monkeypatch) -> None:
    """The run path (``_maf_mcp``) passes no budget, so nothing changes there."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-encoding": "gzip"},
                              content=_gzip_bomb(1024 * 1024))

    _guard_on(monkeypatch, handle)
    client = await outbound_guard.mcp_client("https://mcp.example.com/mcp")
    try:
        r = await client.get("https://mcp.example.com/mcp", headers={"accept-encoding": "gzip"})
        assert len(r.content) == 1024 * 1024
    finally:
        await client.aclose()
    assert seen[0].headers["accept-encoding"] == "gzip"


async def test_a_cancel_of_the_read_reaches_the_caller(monkeypatch) -> None:
    """The cleanup of ``_sdk_list`` never swallows a cancel of the read."""
    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(405)

    _guard_on(monkeypatch, handle)
    server = _server("github", ORG_A)
    server.url = "https://mcp.example.com/mcp"
    task = asyncio.ensure_future(mcp_tools._sdk_list(server))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)


def test_clean_tools_reads_at_most_the_item_cap() -> None:
    def _endless() -> Any:
        i = 0
        while True:
            i += 1
            if i > 10 * mcp_tools.MAX_LISTED:
                raise AssertionError("clean_tools read past the cap")
            yield _tool(f"t{i}", READ)

    assert mcp_tools.clean_tools(_endless()).truncated is True


async def test_the_sdk_path_refuses_a_url_that_is_not_https(monkeypatch) -> None:
    server = _server("github", ORG_A)
    server.url = "http://mcp.example.com/mcp"
    with pytest.raises(outbound_guard.OutboundRefused):
        await mcp_tools._sdk_list(server)


# ── The source ─────────────────────────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_the_module_imports_no_orchestrator_and_no_own_client() -> None:
    """A service never imports another service. The one client is
    ``outbound_guard.mcp_client``: no ``httpx`` client and no raw Redis."""
    path = Path(mcp_tools.__file__)
    names = _imports(path)
    assert not any(n.startswith("orchestrator") for n in names), names
    assert not any(n.split(".")[0] in ("httpx", "redis", "requests", "aiohttp", "socket")
                   for n in names), names
    source = path.read_text(encoding="utf-8")
    assert "outbound_guard.mcp_client(" in source
    assert "AsyncClient(" not in source and "create_mcp_http_client" not in source


def test_no_log_line_names_a_header_or_an_exception_text() -> None:
    """Each log call passes the name, a reason code and a class name only."""
    tree = ast.parse(Path(mcp_tools.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "_log"):
            for kw in node.keywords:
                text = ast.unparse(kw.value)
                assert "headers" not in text and "url" not in text, text
                assert text in ("server", "reason", "type(exc).__name__"), text
