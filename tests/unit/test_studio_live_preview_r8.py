"""R8 — the live preview read of an MCP tool stays in its org and member (STU-I11a).

Spec: ``project-docs/specs/ai_studio.md`` §14 I11a. Code:
``gateway/connections/mcp_tools.py`` (``call_read_tool``,
``fresh_tool_list`` and the answer cache) and
``gateway/package_host/resolve.py`` (``met_connections``, the read of the
``connections`` rule of the door).

This file runs the REAL registry readers (``servers_for_org`` and
``enabled_meta``) on a phase-4 promoted scratch database, as the
NON-privileged role. Each org holds a registry row, an old ``mcp_servers``
row and one server named ``shared``. Two walls keep the orgs apart:

* the registry (``integration_connections``) has FORCE RLS, bound by
  ``tenant_session``;
* ``mcp_servers`` has no RLS. Only the ``organization_id`` predicate of
  ``connections/mcp._LEGACY_SQL`` keeps an old row of org B out of org A.

The cache is a fake raw Redis under the REAL ``TenantRedis`` wrapper, as the
B1b-2 R8 suite does, because CI has no Redis. The MCP client is a counting
fake: no test makes a network call.

**Acceptance.**

a. A live read of org A calls only a server of org A, on the registry path
   and on the legacy path, with the header of org A. A server of org B gives
   ``server_not_found`` and no call.
b. The server ``shared`` of both orgs: an answer of org A never serves org B,
   and org B gets its own call with its own header.
c. A cached list of org B never serves a live read of org A.
d. Each key of an answer carries the org of its read. A key of org A cannot
   be read for org B.
e. Two members of org A never share an answer.
f. The ``connections`` read of the door binds the org of the session.
g. A class B tool of a real row makes no call.
h. A member whose access denies ``integrations:use:mcp-<server>`` makes no
   call on a real row, and a server of ``agent_scope: []`` refuses. The
   refresh door holds the same two rules, and it claims no slot.

**The mutations (R7).** Each ran on 2026-11-15, then was restored with git.

* M1. ``connections/mcp.py::_LEGACY_SQL``: drop the ``organization_id``
  predicate. ``test_r8_a_live_read_calls_only_a_server_of_the_own_org`` goes
  red.
* M2. ``mcp_tools._scope``: bind one shared org (``"shared"``). 2 tests go
  red: ``test_r8_a_cached_list_of_org_b_never_serves_org_a`` and
  ``test_r8_the_answer_key_of_org_a_cannot_be_read_for_org_b``.
  ``test_r8_an_answer_of_org_a_never_serves_org_b`` stays green, because the
  input hash also holds the server identity, and the two urls differ. That is
  a second wall, not the first.
* M3. ``call_read_tool``: key the answer without the member.
  ``test_r8_two_members_never_share_an_answer`` goes red.
* M4. ``call_read_tool``: drop the grant check.
  ``test_r8_a_denied_grant_or_no_agent_scope_makes_no_call`` goes red.
* M5. ``refresh``: drop the grant check, and M6, drop the scope check. Each
  turns ``test_r8_a_refresh_needs_the_grant_and_the_scope`` red.

Run (one R8 suite at a time on the shared scratch database)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_live_preview_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import tenant_redis
from acb_common.db import clear_tenant
from acb_common.tenant_redis import TenantMismatch, TenantRedis
from sqlalchemy import text

# ``promoted``, ``graph_on_promoted`` and ``registry_on_promoted`` are
# fixtures, used by name. The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_on_promoted,
)
from tests.unit.test_studio_mcp_tools import RawRedis

pytestmark = _DB_GATE

SHARED = "shared"
ANA = "ana@example.test"
BEN = "ben@example.test"


def _seed_legacy(p: Any, org: str, name: str) -> None:
    """One old ``mcp_servers`` row, as the code before IN-4 wrote it."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO mcp_servers (name, label, description, transport, "
            "command, url, env_vars, headers, agent_scope, enabled, "
            "organization_id) VALUES (:n, :n, 'old row', 'http-sse', NULL, :u, "
            "'{}'::jsonb, CAST(:h AS jsonb), '[\"*\"]'::jsonb, true, "
            "CAST(:o AS uuid))"),
            {"n": name, "u": f"https://{name}.example.com/mcp", "o": org,
             "h": json.dumps({"Authorization": f"Bearer {name}"})})


@pytest.fixture
async def two_orgs(registry_on_promoted, monkeypatch):  # noqa: F811
    """Each org: one registry server, one old row and the server ``shared``,
    each with a header of its own org."""
    from gateway import outbound_guard
    from gateway.connections import mcp, mcp_tools

    monkeypatch.setattr(outbound_guard, "_resolve", lambda _h, _p: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)

    async def _no_client(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a real MCP client was built")

    monkeypatch.setattr(outbound_guard, "mcp_client", _no_client)
    p = registry_on_promoted
    tag = uuid.uuid4().hex[:6]
    names = {"reg_a": f"stu-lp-reg-a-{tag}", "reg_b": f"stu-lp-reg-b-{tag}",
             "old_a": f"stu-lp-old-a-{tag}", "old_b": f"stu-lp-old-b-{tag}"}
    for org, key in ((p.org_a, "reg_a"), (p.org_b, "reg_b")):
        await mcp.save(org, name=names[key], url=f"https://{names[key]}.example.com/mcp",
                       headers={"Authorization": f"Bearer {key}"}, connected_by="r8-seed",
                       always_confirm=False)
    for org, side in ((p.org_a, "a"), (p.org_b, "b")):
        await mcp.save(org, name=SHARED, url=f"https://shared-{side}-{tag}.example.com/mcp",
                       headers={"Authorization": f"Bearer shared-{side}"},
                       connected_by="r8-seed", always_confirm=False)
    _seed_legacy(p, p.org_a, names["old_a"])
    _seed_legacy(p, p.org_b, names["old_b"])

    raw = RawRedis()
    w = SimpleNamespace(p=p, names=names, raw=raw, tag=tag, lists=[], calls=[])
    monkeypatch.setattr(mcp_tools, "_redis", lambda: TenantRedis(raw))
    monkeypatch.setattr(mcp_tools, "_now", lambda: raw.now)

    async def _lister(server: Any) -> list[dict[str, Any]]:
        w.lists.append(server.url)
        return [{"name": "get_issue", "annotations": {"readOnlyHint": True}},
                {"name": "delete_repo", "annotations": {"destructiveHint": True}}]

    async def _caller(server: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        w.calls.append((server.url, dict(server.headers).get("Authorization"), tool))
        return {"content": [{"type": "text", "text": f"from {server.url}"}], "isError": False}

    monkeypatch.setattr(mcp_tools, "_sdk_list", _lister)
    monkeypatch.setattr(mcp_tools, "_sdk_call", _caller)
    clear_tenant()
    try:
        yield w
    finally:
        clear_tenant()


async def _read(org: str, server: str, *, member: str = ANA, tool: str = "get_issue",
                args: dict[str, Any] | None = None, grants: tuple[str, ...] = ("integrations:use:*",),
                denies: tuple[str, ...] = ()) -> Any:
    from acb_auth import build_access
    from gateway.connections import mcp_tools

    access = build_access(grants, [(d, "deny") for d in denies])
    try:
        return await mcp_tools.call_read_tool(org, member, server, tool, args or {},
                                              may_use=access.has)
    except mcp_tools.LiveRefused as refused:
        return refused.reason


def _url(name: str) -> str:
    return f"https://{name}.example.com/mcp"


async def test_r8_a_live_read_calls_only_a_server_of_the_own_org(two_orgs) -> None:
    w = two_orgs
    org_a, names = w.p.org_a, w.names
    assert (await _read(org_a, names["reg_a"])).result["content"][0]["text"] == (
        f"from {_url(names['reg_a'])}")
    assert (await _read(org_a, names["old_a"])).server == names["old_a"]
    assert await _read(org_a, names["reg_b"]) == "server_not_found"
    assert await _read(org_a, names["old_b"]) == "server_not_found"
    assert w.calls == [(_url(names["reg_a"]), "Bearer reg_a", "get_issue"),
                       (_url(names["old_a"]), f"Bearer {names['old_a']}", "get_issue")]
    assert w.lists == [_url(names["reg_a"]), _url(names["old_a"])]
    # The legacy row of org B was never moved into org A.
    with w.p.admin_engine.connect() as c:
        moved = [str(r[0]) for r in c.execute(text(
            "SELECT organization_id FROM integration_connections WHERE provider = :pr"),
            {"pr": f"mcp:{names['old_b']}"})]
    assert moved == []


async def test_r8_an_answer_of_org_a_never_serves_org_b(two_orgs) -> None:
    w = two_orgs
    got_a = await _read(w.p.org_a, SHARED, args={"n": 1})
    got_b = await _read(w.p.org_b, SHARED, args={"n": 1})
    assert got_a.cached is False and got_b.cached is False
    assert got_a.result != got_b.result
    assert w.calls == [(f"https://shared-a-{w.tag}.example.com/mcp", "Bearer shared-a",
                        "get_issue"),
                       (f"https://shared-b-{w.tag}.example.com/mcp", "Bearer shared-b",
                        "get_issue")]
    # Each one hits its own cache now, and no call more runs.
    assert (await _read(w.p.org_a, SHARED, args={"n": 1})).result == got_a.result
    assert (await _read(w.p.org_b, SHARED, args={"n": 1})).result == got_b.result
    assert len(w.calls) == 2


async def test_r8_a_cached_list_of_org_b_never_serves_org_a(two_orgs) -> None:
    from gateway.connections import mcp_tools

    w = two_orgs
    assert (await mcp_tools.refresh(w.p.org_b, SHARED, may_use=lambda _p: True)).ok is True
    assert w.lists == [f"https://shared-b-{w.tag}.example.com/mcp"]
    await _read(w.p.org_a, SHARED)
    # Org A read its own live list, never the fresh list of org B.
    assert w.lists[1:] == [f"https://shared-a-{w.tag}.example.com/mcp"]
    assert all(k.startswith((f"cc:{w.p.org_a}:", f"cc:{w.p.org_b}:")) for k in w.raw.data)


async def test_r8_the_answer_key_of_org_a_cannot_be_read_for_org_b(two_orgs) -> None:
    from gateway.connections import mcp_tools

    w = two_orgs
    await _read(w.p.org_a, w.names["reg_a"])
    keys = [k for k in w.raw.data if f":{mcp_tools.LIVE_NAMESPACE}:" in k]
    assert keys and all(k.startswith(f"cc:{w.p.org_a}:") for k in keys)
    client = TenantRedis(w.raw)
    with tenant_redis.organization_scope(w.p.org_a):
        key = tenant_redis.key(mcp_tools.LIVE_NAMESPACE, *keys[0].split(":")[3:])
        assert await client.get(key) is not None
    with tenant_redis.organization_scope(w.p.org_b), pytest.raises(TenantMismatch):
        await client.get(key)


async def test_r8_two_members_never_share_an_answer(two_orgs) -> None:
    w = two_orgs
    await _read(w.p.org_a, w.names["reg_a"], member=ANA)
    got = await _read(w.p.org_a, w.names["reg_a"], member=BEN)
    assert got.cached is False and len(w.calls) == 2
    assert not any(ANA in k or BEN in k for k in w.raw.data)


async def test_r8_the_connections_read_binds_the_org_of_the_session(two_orgs) -> None:
    from gateway.db import tenant_session
    from gateway.package_host import resolve

    w = two_orgs
    async with tenant_session(w.p.org_a) as db:
        met = await resolve.met_connections(db, viewer=ANA)
    # I9a: the answer keeps the org rows and the member rows apart.
    assert f"mcp:{w.names['reg_a']}" in met.org and f"mcp:{SHARED}" in met.org
    assert f"mcp:{w.names['reg_b']}" not in met.org and f"mcp:{w.names['old_b']}" not in met.org
    assert not any(n.startswith("mcp:") for n in met.member)


async def test_r8_a_class_b_tool_of_a_real_row_makes_no_call(two_orgs) -> None:
    w = two_orgs
    assert await _read(w.p.org_a, w.names["reg_a"], tool="delete_repo") == "not_class_a"
    assert w.calls == [] and w.lists == [_url(w.names["reg_a"])]


async def test_r8_a_denied_grant_or_no_agent_scope_makes_no_call(two_orgs) -> None:
    from gateway.connections import mcp

    w = two_orgs
    reg_a = w.names["reg_a"]
    assert await _read(w.p.org_a, reg_a, denies=(f"integrations:use:mcp-{reg_a}",)) == (
        "grant_denied")
    assert await _read(w.p.org_a, reg_a, grants=()) == "grant_denied"
    await mcp.save(w.p.org_a, name=reg_a, url=_url(reg_a), headers={"Authorization": "x"},
                   connected_by="r8-seed", always_confirm=False, agent_scope=())
    assert await _read(w.p.org_a, reg_a) == "scope_denied"
    assert w.calls == [] and w.lists == []


async def test_r8_a_refresh_needs_the_grant_and_the_scope(two_orgs) -> None:
    from acb_auth import build_access
    from gateway.connections import mcp, mcp_tools

    w = two_orgs
    reg_a = w.names["reg_a"]
    denied = build_access(["integrations:use:*"], [(f"integrations:use:mcp-{reg_a}", "deny")])
    for may_use in (denied.has, build_access([]).has):
        with pytest.raises(mcp_tools.RefreshRefused):
            await mcp_tools.refresh(w.p.org_a, reg_a, may_use=may_use)
    # A server of org B is refused as one that the member may not use.
    with pytest.raises(mcp_tools.RefreshRefused):
        await mcp_tools.refresh(w.p.org_a, w.names["reg_b"], may_use=lambda _p: True)
    await mcp.save(w.p.org_a, name=reg_a, url=_url(reg_a), headers={"Authorization": "x"},
                   connected_by="r8-seed", always_confirm=False, agent_scope=())
    with pytest.raises(mcp_tools.RefreshRefused):
        await mcp_tools.refresh(w.p.org_a, reg_a, may_use=lambda _p: True)
    assert w.lists == []
    assert not any(":refresh:" in k for k in w.raw.data)
    # Not vacuous: with the grant and the scope, the refresh reads.
    assert (await mcp_tools.refresh(w.p.org_a, SHARED, may_use=lambda _p: True)).ok is True
    assert w.lists == [f"https://shared-a-{w.tag}.example.com/mcp"]



async def test_r8_a_draft_never_meets_the_mcp_need_on_real_rows(two_orgs) -> None:
    """I9a on the real registry: org A holds a connected ``mcp:<reg_a>`` row,
    and the rule of a draft preview still finds the need unmet."""
    from gateway.db import tenant_session
    from gateway.package_host import resolve
    from gateway.routes.studio import invoke

    w = two_orgs
    async with tenant_session(w.p.org_a) as db:
        met = await resolve.met_connections(db, viewer=ANA)
    name = f"mcp:{w.names['reg_a']}"
    assert name in met.org
    assert invoke._live_unmet({"connections": [name]}, met) == (name, "org")
