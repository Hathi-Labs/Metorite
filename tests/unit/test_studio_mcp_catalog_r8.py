"""R8 — the MCP rows of the action catalog stay in their org (STU-B1b-1).

Spec: ``project-docs/specs/ai_studio.md`` §14 B1b-1. Code:
``gateway/studio/mcp_actions.py::mcp_rows`` over the server list of
``gateway/connections/mcp.py::enabled_meta``.

``mcp_rows`` is pure. It takes the server list of ONE org from its caller,
and it gives a row only for a server of that list. The one reader that the
caller uses is ``enabled_meta(org)``. It opens ``tenant_session(org)``, and
it moves each old ``mcp_servers`` row of the org into the registry first.
Two walls keep the orgs apart there:

* the registry (``integration_connections``) has FORCE RLS, bound by
  ``tenant_session``;
* ``mcp_servers`` is exempt from RLS. Only the ``organization_id`` predicate
  of ``_LEGACY_SQL`` keeps an old row of org B out of a read of org A.

This file runs the real reader on a phase-4 promoted scratch database, as
the NON-privileged role, with a registry row and an old row in each org.

**Acceptance.**

a. The rows of org A name only the servers of org A, on the registry path
   and on the legacy path. The tools map holds the names of org B too, so
   the scope comes from the server list and not from the map.
b. A read of org A never moves the old row of org B into org A.
c. The rows of org B name only the servers of org B (not vacuous).

**The mutations (R7).** Each was run on 2026-11-14, then restored with git.

* M1. ``connections/mcp.py::_LEGACY_SQL``: drop ``WHERE organization_id =
  CAST(:org AS uuid)``. All 3 tests go red, because a read of one org then
  moves the old row of the other org into it.
* M2. ``mcp_actions.mcp_rows``: take the servers from the keys of *tools*.
  ``test_r8_org_a_never_gets_a_row_of_org_b`` and
  ``test_r8_org_b_gets_only_its_own_rows`` go red.

Run (one R8 suite at a time on the shared scratch database)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_studio_mcp_catalog_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import clear_tenant
from sqlalchemy import text

# ``promoted``, ``graph_on_promoted`` and ``registry_on_promoted`` are
# fixtures, used by name. The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_names,
    registry_on_promoted,
)

pytestmark = _DB_GATE

TOOLS = [
    {"name": "get_issue", "annotations": {"readOnlyHint": True}, "description": "Read."},
    {"name": "delete_repo", "annotations": {"destructiveHint": True}, "description": "Wipe."},
]


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


def _legacy_orgs(p: Any, name: str) -> list[str]:
    with p.admin_engine.connect() as c:
        return [str(r[0]) for r in c.execute(text(
            "SELECT organization_id FROM mcp_servers WHERE name = :n"), {"n": name})]


@pytest.fixture
async def two_orgs(registry_on_promoted, monkeypatch):  # noqa: F811
    """Org A and org B each hold one registry server and one old row."""
    from gateway import outbound_guard
    from gateway.connections import mcp

    monkeypatch.setattr(outbound_guard, "_resolve", lambda _h, _p: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    p = registry_on_promoted
    tag = uuid.uuid4().hex[:6]
    names = {
        "reg_a": f"stu-reg-a-{tag}", "reg_b": f"stu-reg-b-{tag}",
        "old_a": f"stu-old-a-{tag}", "old_b": f"stu-old-b-{tag}",
    }
    for org, key in ((p.org_a, "reg_a"), (p.org_b, "reg_b")):
        await mcp.save(org, name=names[key], url=f"https://{names[key]}.example.com/mcp",
                       headers={}, connected_by="r8-seed", always_confirm=False)
    _seed_legacy(p, p.org_a, names["old_a"])
    _seed_legacy(p, p.org_b, names["old_b"])
    clear_tenant()
    try:
        yield p, names
    finally:
        clear_tenant()


async def _rows_of(org: str, names: dict[str, str]) -> list[dict[str, Any]]:
    """The one caller path: the org's server list, then the pure rows. The
    tools map holds every server of BOTH orgs."""
    from gateway.connections import mcp
    from gateway.studio import mcp_actions

    servers = await mcp.enabled_meta(org)
    return mcp_actions.mcp_rows(servers, {n: TOOLS for n in names.values()})


def _servers(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r["server"]) for r in rows}


async def test_r8_org_a_never_gets_a_row_of_org_b(two_orgs) -> None:
    p, names = two_orgs
    rows = await _rows_of(p.org_a, names)
    assert _servers(rows) == {names["reg_a"], names["old_a"]}
    # The class rule holds on the real rows: A for the read, B for the wipe.
    got = {r["id"]: r["class"] for r in rows if r["server"] == names["reg_a"]}
    assert got == {f"mcp:{names['reg_a']}.get_issue": "A",
                   f"mcp:{names['reg_a']}.delete_repo": "B"}
    # Not vacuous: the admin read (no RLS) sees the registry row of org B.
    assert names["reg_b"] in registry_names(p.admin_engine)


async def test_r8_a_read_of_org_a_never_moves_an_old_row_of_org_b(two_orgs) -> None:
    p, names = two_orgs
    await _rows_of(p.org_a, names)
    assert _legacy_orgs(p, names["old_b"]) == [str(p.org_b)]
    with p.admin_engine.connect() as c:
        found = [str(r[0]) for r in c.execute(text(
            "SELECT organization_id FROM integration_connections WHERE provider = :pr"),
            {"pr": f"mcp:{names['old_b']}"})]
    assert found == [], "a read of org A moved an old row of org B"


async def test_r8_org_b_gets_only_its_own_rows(two_orgs) -> None:
    p, names = two_orgs
    rows = await _rows_of(p.org_b, names)
    assert _servers(rows) == {names["reg_b"], names["old_b"]}
