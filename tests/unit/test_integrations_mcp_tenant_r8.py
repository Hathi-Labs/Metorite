"""WS-54 IN-0 (c) — the MCP list and delete bind the caller's tenant (R8).

Spec: ``project-docs/specs/integrations_console.md`` §2.2 (IN-D3, IN-D4), §7.2
(IN-0) and §9. ``mcp_servers`` is exempt from RLS
(``scripts/gen_tenant_migration.py``) and keyed ``(organization_id, name)``
by migration 158. A ``tenant_session`` does nothing on a table with no
policy, so the ``organization_id`` predicate in the route IS the boundary.

The fences run against a real Postgres with two organizations. They reuse the
phase-4 fixture ``promoted`` and the ``graph_on_promoted`` reroute of
``test_mcp_servers_org_scope.py``. The reroute connects as the non-privileged
``acb_app_h3rls`` role, which reads every row of an RLS-exempt table. That is
the exposure the filter closes.

* Bound to org B, the list shows only org B's row. Neither org A's header
  value nor its env value appears anywhere in the JSON. Org B's own values do
  not appear either, only the key names.
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

import acb_graph
from acb_auth import UserContext, UserRole
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes import integrations
from sqlalchemy import text

# ``promoted`` and ``graph_on_promoted`` are fixtures, used by name. The
# imports are load-bearing although they read as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import graph_on_promoted  # noqa: F401

pytestmark = _DB_GATE

USER = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE)


def _seed(admin_engine, *, org: str, name: str, tag: str) -> None:
    """One http-sse row for ``org``, with a sentinel header and env value."""
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO mcp_servers (name, label, transport, url, headers, "
                "env_vars, enabled, organization_id) VALUES "
                "(:name, :name, 'http-sse', 'https://mcp.example.com/sse', "
                "CAST(:headers AS jsonb), CAST(:env AS jsonb), true, :org)"
            ),
            {
                "name": name, "org": org,
                "headers": json.dumps({"Authorization": f"sentinel-hdr-{tag}"}),
                "env": json.dumps({"API_KEY": f"sentinel-env-{tag}"}),
            },
        )


def _names_of(admin_engine, org: str) -> set[str]:
    with admin_engine.begin() as c:
        rows = c.execute(
            text("SELECT name FROM mcp_servers WHERE organization_id = :org"),
            {"org": org},
        ).fetchall()
    return {r[0] for r in rows}


def _cleanup(admin_engine, names: list[str]) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM mcp_servers WHERE name = ANY(:n)"), {"n": names})


@pytest.fixture
def two_rows(graph_on_promoted):  # noqa: F811
    p = graph_on_promoted
    marker = uuid.uuid4().hex[:8]
    name_a, name_b = f"in0-a-{marker}", f"in0-b-{marker}"
    _seed(p.admin_engine, org=p.org_a, name=name_a, tag="A")
    _seed(p.admin_engine, org=p.org_b, name=name_b, tag="B")
    clear_tenant()
    try:
        yield p, name_a, name_b
    finally:
        clear_tenant()
        _cleanup(p.admin_engine, [name_a, name_b])


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
    for sentinel in ("sentinel-hdr-A", "sentinel-env-A", "sentinel-hdr-B", "sentinel-env-B"):
        assert sentinel not in body, f"{sentinel} reached the list: {body}"
    mine = next(r for r in rows if r["name"] == name_b)
    assert mine["header_names"] == ["Authorization"]
    assert mine["env_var_names"] == ["API_KEY"]
    assert "headers" not in mine and "env_vars" not in mine

    # Not vacuous: an unfiltered read on the same session sees org A's row.
    with acb_graph.get_session() as s:
        unfiltered = {r[0] for r in s.execute(text("SELECT name FROM mcp_servers")).fetchall()}
    assert {name_a, name_b} <= unfiltered


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
