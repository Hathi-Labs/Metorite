"""R8 — the MCP tool cache of the catalog stays in its org (STU-B1b-2).

Spec: ``project-docs/specs/ai_studio.md`` §14 B1b-2. Code:
``gateway/connections/mcp_tools.py`` (the read and the cache) and
``gateway/routes/studio/actions.py`` (``GET /studio/actions`` and
``POST /studio/mcp/{server}/refresh``).

This file runs the REAL handlers and the REAL registry readers
(``servers_for_org`` and ``enabled_meta``) on a phase-4 promoted scratch
database, as the NON-privileged role. Each org holds a registry row, an old
``mcp_servers`` row and one server named ``shared`` in both orgs. Two walls
keep the orgs apart in the database:

* the registry (``integration_connections``) has FORCE RLS, bound by
  ``tenant_session``;
* ``mcp_servers`` has no RLS. Only the ``organization_id`` predicate of
  ``connections/mcp._LEGACY_SQL`` keeps an old row of org B out of org A.

The cache is a fake raw Redis under the REAL ``TenantRedis`` wrapper, as the
GitHub and My Day R8 suites do, because CI has no Redis. So the key of each
command is the real tenant key. The MCP client is a counting fake: no test
makes a network call.

**Acceptance.**

a. A refresh of org A reads only a server of org A, on the registry path
   and on the legacy path. A server of org B is 403 (I11a review), and no client call
   runs for it.
b. The rows of org A name only the servers of org A, and the rows of org B
   only those of org B (not vacuous).
c. A server of one name in both orgs: a refresh of org B gives org A no row.
d. A refresh of org A never moves the old row of org B into org A.
e. The cache key of org A cannot be read for org B.

**The mutations (R7).** Each ran on 2026-11-14, then was restored.

* M1. ``connections/mcp.py::_LEGACY_SQL``: drop ``WHERE organization_id =
  CAST(:org AS uuid)``. 3 tests go red:
  ``test_r8_a_refresh_reads_only_a_server_of_the_own_org``,
  ``test_r8_each_org_gets_only_its_own_rows`` and
  ``test_r8_a_refresh_of_org_a_never_moves_an_old_row_of_org_b``.
* M2. ``mcp_tools._scope``: bind one shared org (``"shared"``). 3 tests go
  red: ``test_r8_each_org_gets_only_its_own_rows``,
  ``test_r8_a_server_of_one_name_in_both_orgs_never_crosses`` and
  ``test_r8_the_cache_key_of_org_a_cannot_be_read_for_org_b``.
* M3. ``mcp_tools.catalog_tools``: on an empty index, refresh each server.
  ``test_r8_a_server_of_one_name_in_both_orgs_never_crosses`` goes red.

Run (one R8 suite at a time on the shared scratch database)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_mcp_tools_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings, tenant_redis
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from acb_common.tenant_redis import TenantMismatch, TenantRedis
from fastapi import HTTPException
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
    """Each org: one registry server, one old row and the server ``shared``."""
    from gateway import outbound_guard
    from gateway.connections import mcp, mcp_tools

    monkeypatch.setattr(outbound_guard, "_resolve", lambda _h, _p: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    p = registry_on_promoted
    tag = uuid.uuid4().hex[:6]
    names = {
        "reg_a": f"stu-tl-reg-a-{tag}", "reg_b": f"stu-tl-reg-b-{tag}",
        "old_a": f"stu-tl-old-a-{tag}", "old_b": f"stu-tl-old-b-{tag}",
    }
    for org, key in ((p.org_a, "reg_a"), (p.org_b, "reg_b")):
        await mcp.save(org, name=names[key], url=f"https://{names[key]}.example.com/mcp",
                       headers={}, connected_by="r8-seed", always_confirm=False)
    for org, side in ((p.org_a, "a"), (p.org_b, "b")):
        await mcp.save(org, name=SHARED, url=f"https://shared-{side}-{tag}.example.com/mcp",
                       headers={}, connected_by="r8-seed", always_confirm=False)
    _seed_legacy(p, p.org_a, names["old_a"])
    _seed_legacy(p, p.org_b, names["old_b"])

    raw = RawRedis()
    calls: list[str] = []
    monkeypatch.setattr(mcp_tools, "_redis", lambda: TenantRedis(raw))

    async def _lister(server: Any) -> list[dict[str, Any]]:
        calls.append(server.url)
        return [{"name": "get_issue", "annotations": {"readOnlyHint": True},
                 "description": f"from {server.url}"},
                {"name": "delete_repo", "annotations": {"destructiveHint": True}}]

    monkeypatch.setattr(mcp_tools, "_sdk_list", _lister)
    clear_tenant()
    try:
        yield p, names, raw, calls, tag
    finally:
        clear_tenant()


def _user(org: str) -> UserContext:
    return UserContext(email="m@example.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio", "integrations:use:*"]),
                       organization_id=org)


async def _refresh(org: str, server: str) -> Any:
    from gateway.routes.studio import actions

    token = bind_tenant(org)
    try:
        return await actions.refresh_mcp_tools(server, _user(org))
    except HTTPException as exc:
        return exc.status_code
    finally:
        release_tenant(token)


async def _catalog(org: str) -> dict[str, Any]:
    from gateway.routes.studio import actions

    token = bind_tenant(org)
    try:
        return await actions.list_actions(_user(org))
    finally:
        release_tenant(token)


def _servers(body: dict[str, Any]) -> set[str]:
    return {str(r["server"]) for r in body["actions"] if r["source"] == "mcp"}


async def test_r8_a_refresh_reads_only_a_server_of_the_own_org(two_orgs) -> None:
    p, names, _raw, calls, _tag = two_orgs
    assert (await _refresh(p.org_a, names["reg_a"]))["ok"] is True
    assert (await _refresh(p.org_a, names["old_a"]))["ok"] is True
    assert await _refresh(p.org_a, names["reg_b"]) == 403
    assert await _refresh(p.org_a, names["old_b"]) == 403
    assert calls == [f"https://{names['reg_a']}.example.com/mcp",
                     f"https://{names['old_a']}.example.com/mcp"]


async def test_r8_each_org_gets_only_its_own_rows(two_orgs) -> None:
    p, names, _raw, _calls, _tag = two_orgs
    for org, side in ((p.org_a, "a"), (p.org_b, "b")):
        for key in (f"reg_{side}", f"old_{side}"):
            assert (await _refresh(org, names[key]))["ok"] is True
        assert (await _refresh(org, SHARED))["ok"] is True
    body_a = await _catalog(p.org_a)
    body_b = await _catalog(p.org_b)
    assert _servers(body_a) == {names["reg_a"], names["old_a"], SHARED}
    assert _servers(body_b) == {names["reg_b"], names["old_b"], SHARED}
    assert body_a["stale"] is False and body_b["stale"] is False
    # The class rule holds on the real rows.
    got = {r["id"]: r["class"] for r in body_a["actions"] if r.get("server") == names["reg_a"]}
    assert got == {f"mcp:{names['reg_a']}.get_issue": "A",
                   f"mcp:{names['reg_a']}.delete_repo": "B"}


async def test_r8_a_server_of_one_name_in_both_orgs_never_crosses(two_orgs) -> None:
    p, _names, raw, calls, tag = two_orgs
    assert (await _refresh(p.org_b, SHARED))["ok"] is True
    assert calls == [f"https://shared-b-{tag}.example.com/mcp"]
    body_a = await _catalog(p.org_a)
    assert _servers(body_a) == set() and body_a["stale"] is True
    body_b = await _catalog(p.org_b)
    assert {r["description"] for r in body_b["actions"] if r.get("server") == SHARED} == {
        f"from https://shared-b-{tag}.example.com/mcp", ""}
    # Each key of the cache carries the org of its refresh.
    assert raw.data and all(k.startswith(f"cc:{p.org_b}:") for k in raw.data)
    assert len(calls) == 1  # no GET called a server


async def test_r8_a_refresh_of_org_a_never_moves_an_old_row_of_org_b(two_orgs) -> None:
    p, names, _raw, _calls, _tag = two_orgs
    assert await _refresh(p.org_a, names["old_b"]) == 403
    with p.admin_engine.connect() as c:
        legacy = [str(r[0]) for r in c.execute(text(
            "SELECT organization_id FROM mcp_servers WHERE name = :n"), {"n": names["old_b"]})]
        moved = [str(r[0]) for r in c.execute(text(
            "SELECT organization_id FROM integration_connections WHERE provider = :pr"),
            {"pr": f"mcp:{names['old_b']}"})]
    assert legacy == [str(p.org_b)]
    assert moved == []


async def test_r8_the_cache_key_of_org_a_cannot_be_read_for_org_b(two_orgs) -> None:
    from gateway.connections import mcp_tools

    p, names, raw, _calls, _tag = two_orgs
    assert (await _refresh(p.org_a, names["reg_a"]))["ok"] is True
    client = TenantRedis(raw)
    with tenant_redis.organization_scope(p.org_a):
        key_a = tenant_redis.key(mcp_tools.NAMESPACE, "tools", names["reg_a"])
        assert await client.get(key_a) is not None
    with tenant_redis.organization_scope(p.org_b), pytest.raises(TenantMismatch):
        await client.get(key_a)
    # The same name in org B is another key, and it is empty.
    with tenant_redis.organization_scope(p.org_b):
        key_b = tenant_redis.key(mcp_tools.NAMESPACE, "tools", names["reg_a"])
        assert key_b.value != key_a.value
        assert await client.get(key_b) is None
