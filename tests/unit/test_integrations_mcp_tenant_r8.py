"""WS-54 IN-0 (c) — the MCP list and delete bind the caller's tenant (R8).

Spec: ``project-docs/specs/integrations_console.md`` §2.2 (IN-D3, IN-D4), §7.2
(IN-0, IN-4) and §9.

⚠️ **WS-54 IN-4 (2026-10-12).** The servers moved from ``mcp_servers``
(exempt from RLS) into the connection registry, which has FORCE RLS. The
routes read and delete through ``gateway.connections.mcp`` inside
``tenant_session(org)``, and the headers sit in the encrypted blob. So these
fences seed the registry through the real writer, and they run the routes as
the non-privileged ``acb_app_h3rls`` role (``registry_on_promoted``).

The fences run against a real Postgres with two organizations:

* Bound to org B, the list shows only org B's row. Neither org's header value
  appears anywhere in the JSON, only the key names. No env var is stored, so
  no env var name is listed.
* Bound to org B, a delete of org A's name answers 404, and org A's row stays.
  A delete of org B's own name still works, so the 404 is not vacuous.
* With no tenant bound, the list is ``[]``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_integrations_mcp_tenant_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes import integrations
from sqlalchemy import text

# ``promoted``, ``graph_on_promoted`` and ``registry_on_promoted`` are
# fixtures, used by name. The imports are load-bearing although they read as
# unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_names,
    registry_on_promoted,
    seed_registry,
)

pytestmark = _DB_GATE

USER = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE)


def _names_of(admin_engine, org: str) -> set[str]:
    with admin_engine.begin() as c:
        rows = c.execute(
            text("SELECT provider FROM integration_connections "
                 "WHERE organization_id = CAST(:org AS uuid) AND provider LIKE 'mcp:%'"),
            {"org": org},
        ).fetchall()
    return {r[0].removeprefix("mcp:") for r in rows}


@pytest.fixture
async def two_rows(registry_on_promoted):  # noqa: F811
    p = registry_on_promoted
    marker = uuid.uuid4().hex[:8]
    name_a, name_b = f"in0-a-{marker}", f"in0-b-{marker}"
    await seed_registry(p.org_a, name_a, url="https://mcp.example.com/sse",
                        headers={"Authorization": "sentinel-hdr-A"})
    await seed_registry(p.org_b, name_b, url="https://mcp.example.com/sse",
                        headers={"Authorization": "sentinel-hdr-B"})
    clear_tenant()
    try:
        yield p, name_a, name_b
    finally:
        clear_tenant()


async def _as(org: str | None, call):
    token = bind_tenant(org) if org else clear_tenant()
    try:
        return await call()
    finally:
        release_tenant(token)


async def test_org_b_lists_only_its_row_and_no_secret(two_rows) -> None:
    p, name_a, name_b = two_rows
    rows = await _as(p.org_b, lambda: integrations.list_mcp_servers(user=USER))
    names = {r["name"] for r in rows}
    assert name_b in names, rows
    assert name_a not in names, f"org A's MCP row leaked into org B's list: {rows}"
    body = json.dumps(rows)
    for sentinel in ("sentinel-hdr-A", "sentinel-hdr-B"):
        assert sentinel not in body, f"{sentinel} reached the list: {body}"
    mine = next(r for r in rows if r["name"] == name_b)
    assert mine["header_names"] == ["Authorization"]
    assert mine["env_var_names"] == []
    assert "headers" not in mine and "env_vars" not in mine

    # Not vacuous: the admin read (no RLS, no filter) sees org A's row.
    assert {name_a, name_b} <= registry_names(p.admin_engine)


async def test_org_b_cannot_delete_org_a_row(two_rows) -> None:
    p, name_a, name_b = two_rows
    with pytest.raises(HTTPException) as caught:
        await _as(p.org_b, lambda: integrations.remove_mcp_server(name_a, user=USER))
    assert caught.value.status_code == 404
    assert name_a in _names_of(p.admin_engine, p.org_a), "org B deleted org A's MCP row"

    # Not vacuous: org B deletes its own row through the same route.
    out = await _as(p.org_b, lambda: integrations.remove_mcp_server(name_b, user=USER))
    assert out == {"ok": True, "deleted": name_b}
    assert name_b not in _names_of(p.admin_engine, p.org_b)


async def test_no_tenant_lists_nothing_and_deletes_nothing(two_rows) -> None:
    p, name_a, _name_b = two_rows
    assert await _as(None, lambda: integrations.list_mcp_servers(user=USER)) == []
    with pytest.raises(HTTPException) as caught:
        await _as(None, lambda: integrations.remove_mcp_server(name_a, user=USER))
    assert caught.value.status_code == 404
    assert name_a in _names_of(p.admin_engine, p.org_a)
