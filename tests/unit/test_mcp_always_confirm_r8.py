"""R8 — H-300: an admin's "always confirm" switch on each MCP server.

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c, item 31 and
acceptance A31 to A34. HANDOFF H-300. Owner, 2026-10-12: "Build it now, keep
MCP on."

The switch is ``provider_meta.always_confirm`` on the ``mcp:<name>`` row of the
connection registry. ``McpPermissionGate`` reads it (the gate half is in
``tests/unit/test_maf_mcp_confirm.py``). What this proves against a REAL
Postgres, as the NON-privileged role of the phase-4 rehearsal, with two orgs:

- **A31** a new server is set to always confirm, unless the POST says no;
- **A32** a row with no key (written before H-300) and a row that the move
  of an old ``mcp_servers`` row wrote both read as false, so each keeps its
  H-299 behaviour;
- **A33** ``PATCH /integrations/mcp/{name}`` flips the switch of the
  caller's org only, keeps the headers, and writes one audit event. A member
  without ``integrations:manage`` gets 403. Another org's name is 404;
- **A34** ``GET /integrations/mcp`` and the read model return the flag, and
  no header value reaches a response or a log line.

Mutation table: rows M34 to M38 of ``tests/unit/test_maf_mcp_confirm.py``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_always_confirm_r8.py -v -rs
"""
from __future__ import annotations

from typing import Any

import pytest
import structlog

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

from acb_common.db import bind_tenant, release_tenant

from tests.unit._maf_mcp_fakes import install_offline, logged_text, set_flag

# ``promoted``, ``app_engine``, ``graph_on_promoted``, ``registry_on_promoted``
# and ``ns`` are fixtures, used by name. The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_mcp_registry_r8 import (  # noqa: F401
    _assert_non_priv,
    _call,
    _inject,
    _name,
    _registry,
    _seed_legacy,
    _user,
    ns,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_on_promoted,
)

# ⚠️ Without this gate the suite ERRORS where no ladder URL is set (the deploy
# run), and the deploy stops.
pytestmark = _DB_GATE

SENTINEL = "Bearer sentinel-h300-always-confirm-5b2e"
ADMIN_A = "admin-a@h300-r8.test"
ADMIN_B = "admin-b@h300-r8.test"


@pytest.fixture
def audit(monkeypatch):
    import acb_audit

    events: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", events.append)
    return events


async def _post(user: Any, name: str, **extra: Any) -> None:
    body = {"name": name, "transport": "http-sse", "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": SENTINEL}, **extra}
    res = await _call(user, "POST", "/integrations/mcp", body)
    assert res.status_code == 201, res.text


async def _listed(user: Any, name: str) -> dict[str, Any]:
    res = await _call(user, "GET", "/integrations/mcp")
    assert res.status_code == 200, res.text
    return next(r for r in res.json() if r["name"] == name)


def _meta(p: Any, org: str, name: str) -> dict[str, Any]:
    return dict(_registry(p, org)[name]["provider_meta"])


async def _flag_of(org: str, name: str) -> bool:
    from gateway.connections import mcp

    server = await mcp.get_one(org, name)
    assert server is not None
    return server.always_confirm


# ── A31: a new server is set to always confirm ──────────────────────────────


async def test_a_new_server_defaults_to_always_confirm(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    user = _user(ADMIN_A, ns.org_a)
    strict, loose = _name("new"), _name("off")
    await _post(user, strict)
    await _post(user, loose, always_confirm=False)

    assert _meta(ns, ns.org_a, strict)["always_confirm"] is True
    assert _meta(ns, ns.org_a, loose)["always_confirm"] is False
    assert (await _listed(user, strict))["always_confirm"] is True
    assert (await _listed(user, loose))["always_confirm"] is False

    # The writer itself defaults to true, for a caller that names no value.
    from gateway.connections import mcp

    direct = _name("direct")
    await mcp.save(ns.org_a, name=direct, url="https://mcp.example.com/mcp",
                   headers={}, connected_by="r8")
    assert _meta(ns, ns.org_a, direct)["always_confirm"] is True


# ── A32: an older row reads as false ────────────────────────────────────────


async def test_a_server_with_no_key_reads_false(ns) -> None:  # noqa: F811
    """A row that the IN-4 writer stored before H-300 has no key."""
    from acb_common.db import tenant_session
    from gateway.connections import mcp, store

    name = _name("nokey")
    async with tenant_session(ns.org_a) as db:
        await store.save_headers(
            db, ns.org_a, mcp.provider_for(name), mcp.OWNING_APP, {"Authorization": SENTINEL},
            provider_meta={"name": name, "label": name, "description": "",
                           "transport": "http-sse", "url": "https://mcp.example.com/mcp",
                           "agent_scope": ["*"]},
            connected_by="before-h300",
        )
    assert "always_confirm" not in _meta(ns, ns.org_a, name)
    assert await _flag_of(ns.org_a, name) is False
    assert (await _listed(_user(ADMIN_A, ns.org_a), name))["always_confirm"] is False


async def test_a_legacy_row_moves_with_the_switch_off(ns) -> None:  # noqa: F811
    name = _name("legacy")
    _seed_legacy(ns, ns.org_a, name)
    listed = await _listed(_user(ADMIN_A, ns.org_a), name)
    assert listed["always_confirm"] is False, "a moved row must keep its H-299 behaviour"
    assert _meta(ns, ns.org_a, name)["always_confirm"] is False


# ── A33: the PATCH ──────────────────────────────────────────────────────────


async def test_the_patch_flips_the_switch_keeps_the_headers_and_audits(ns, audit) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("flip")
    await _post(user, name)
    audit.clear()

    res = await _call(user, "PATCH", f"/integrations/mcp/{name}", {"always_confirm": False})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "name": name, "always_confirm": False}
    assert _meta(ns, ns.org_a, name)["always_confirm"] is False
    assert await _flag_of(ns.org_a, name) is False

    res = await _call(user, "PATCH", f"/integrations/mcp/{name}", {"always_confirm": True})
    assert res.status_code == 200, res.text
    assert await _flag_of(ns.org_a, name) is True

    # The headers, the url and the rest of the row stay.
    from gateway.connections import mcp

    server = await mcp.get_one(ns.org_a, name)
    assert server is not None and server.headers == {"Authorization": SENTINEL}
    assert server.url == "https://mcp.example.com/mcp" and server.enabled is True

    assert len(audit) == 2
    first = audit[0]
    assert first.actor == f"user:{ADMIN_A}" and first.target == f"mcp:{name}"
    assert first.action == "integration.mcp.always_confirm"
    assert first.organization_id == ns.org_a
    assert first.payload == {"organization_id": ns.org_a, "always_confirm": False}
    assert SENTINEL not in repr(audit)


async def test_the_patch_flips_a_disabled_server_too(ns) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("disabled")
    await _post(user, name, enabled=False)
    res = await _call(user, "PATCH", f"/integrations/mcp/{name}", {"always_confirm": False})
    assert res.status_code == 200, res.text
    assert _registry(ns, ns.org_a)[name]["status"] == "disconnected"
    assert _meta(ns, ns.org_a, name)["always_confirm"] is False


async def test_a_member_without_manage_gets_403(ns, audit) -> None:  # noqa: F811
    from acb_auth import UserContext, UserRole, build_access

    name = _name("perm")
    await _post(_user(ADMIN_A, ns.org_a), name)
    audit.clear()
    for perms in (["feature:integrations"], ["feature:integrations", "admin:members:read"]):
        member = UserContext(email="plain@h300-r8.test", role=UserRole.EMPLOYEE,
                             access=build_access(perms), organization_id=ns.org_a)
        res = await _call(member, "PATCH", f"/integrations/mcp/{name}",
                          {"always_confirm": False})
        assert res.status_code == 403, (perms, res.text)
    assert _meta(ns, ns.org_a, name)["always_confirm"] is True
    assert audit == []


async def test_org_b_cannot_flip_org_a_server(ns, audit) -> None:  # noqa: F811
    name_a, name_b = _name("a"), _name("b")
    await _post(_user(ADMIN_A, ns.org_a), name_a)
    await _post(_user(ADMIN_B, ns.org_b), name_b)
    audit.clear()

    res = await _call(_user(ADMIN_B, ns.org_b), "PATCH", f"/integrations/mcp/{name_a}",
                      {"always_confirm": False})
    assert res.status_code == 404, res.text
    assert _meta(ns, ns.org_a, name_a)["always_confirm"] is True, "org B flipped org A's server"
    assert name_a not in _registry(ns, ns.org_b), "the PATCH wrote a row in org B"
    assert audit == []

    # Not vacuous: org B flips its own server through the same route.
    res = await _call(_user(ADMIN_B, ns.org_b), "PATCH", f"/integrations/mcp/{name_b}",
                      {"always_confirm": False})
    assert res.status_code == 200, res.text
    assert _meta(ns, ns.org_b, name_b)["always_confirm"] is False
    assert _meta(ns, ns.org_a, name_a)["always_confirm"] is True


async def test_the_store_predicate_holds_on_a_session_that_bypasses_rls(ns) -> None:  # noqa: F811
    """Two orgs hold one name. On a session with RLS off (the admin is a
    superuser), only the org predicate of the statement can keep the change
    in one org."""
    from gateway.connections import mcp

    from tests.unit._tenant_ladder import tenant_engine_scope

    shared = _name("shared")
    await _post(_user(ADMIN_A, ns.org_a), shared)
    await _post(_user(ADMIN_B, ns.org_b), shared)
    admin_dsn = ns.admin_engine.url.render_as_string(hide_password=False)
    async with tenant_engine_scope(admin_dsn):
        changed = await mcp.set_always_confirm(ns.org_a, shared, False)
    assert changed is not None and changed.always_confirm is False
    assert _meta(ns, ns.org_a, shared)["always_confirm"] is False
    assert _meta(ns, ns.org_b, shared)["always_confirm"] is True, (
        "the change reached the other org's row of the same name")


async def test_the_patch_refuses_a_body_with_no_flag(ns) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("body")
    await _post(user, name)
    for body in ({}, {"always_confirm": "maybe"}):
        res = await _call(user, "PATCH", f"/integrations/mcp/{name}", body)
        assert res.status_code == 422, (body, res.text)
    assert _meta(ns, ns.org_a, name)["always_confirm"] is True


# ── A34: the reads return the flag, and no secret ───────────────────────────


async def test_the_reads_return_the_flag_and_no_header_value(ns, caplog) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("read")
    bodies: list[str] = []
    with structlog.testing.capture_logs() as logs:
        await _post(user, name)
        res = await _call(user, "PATCH", f"/integrations/mcp/{name}", {"always_confirm": False})
        bodies.append(res.text)
        listed = await _call(user, "GET", "/integrations/mcp")
        bodies.append(listed.text)
        model = await _call(user, "GET", "/integrations/connections")
        bodies.append(model.text)
    mine = next(r for r in listed.json() if r["name"] == name)
    assert mine["always_confirm"] is False
    assert mine["header_names"] == ["Authorization"] and mine["env_var_names"] == []
    row = next(r for r in model.json()["connections"] if r["id"] == f"mcp:{name}")
    assert row["detail"]["always_confirm"] == "false"
    everything = "\n".join(bodies) + "\n" + logged_text(logs, caplog)
    assert SENTINEL not in everything and "sentinel-h300" not in everything


async def test_a_run_carries_the_flag_of_each_server(ns, monkeypatch) -> None:  # noqa: F811
    """The real reader, the real injection: each attached tool carries the
    flag of its own row."""
    from orchestrator import _maf_mcp

    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    user = _user(ADMIN_A, ns.org_a)
    strict, loose = _name("strict"), _name("loose")
    await _post(user, strict)
    await _post(user, loose, always_confirm=False)
    token = bind_tenant(ns.org_a)
    try:
        agent = await _inject(ns.org_a)
    finally:
        release_tenant(token)
    flags = {t.name: _maf_mcp.server_always_confirms(t) for t in agent.mcp_tools}
    assert flags == {strict: True, loose: False}
