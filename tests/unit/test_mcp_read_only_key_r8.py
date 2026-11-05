"""R8 — MCP-C2: the admin's read-only key on each MCP server (item 32).

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c, item 32 and
acceptance A37 and A38. Metorite task 1584. The owner asked on 2026-11-04 for
full read access to an outside MCP server with no card for each read.

The switch is ``provider_meta.read_only_key`` on the ``mcp:<name>`` row of the
connection registry. The admin says that the key of the server can only read.
``gateway.connections.mcp._read_only_key`` is the ONE parse, into
``StoredServer.read_only_key``. The gate half (no card, the order of
``decide()``) is in ``tests/unit/test_maf_mcp_confirm.py``. What this proves
against a REAL Postgres, as the NON-privileged role of the phase-4 rehearsal,
with two orgs:

- **A37** ``PATCH /integrations/mcp/{name}`` sets the switch of the caller's
  org only, keeps the headers, and writes one audit event. The gateway checks
  the row as it would be AFTER the change: both switches on is 422, and so is
  ``{"read_only_key": true}`` on a row that is set to always confirm. A string
  value is 422. An OAuth row refuses the switch with 422. A body with only
  ``always_confirm`` behaves as A33. A re-POST with no ``read_only_key`` turns
  it off, and its audit event records both flags. A member without
  ``integrations:manage`` gets 403. Another org's name is 404. A new server
  reads as false;
- **A38** ``GET /integrations/mcp`` and the read model return the flag, and
  no header value reaches a response or a log line. A run carries the flag of
  each row to its attached tool.

Mutation table (each mutation was made by hand, and the named test failed):

| # | Mutation | Test that fails |
|---|---|---|
| R1 | the PATCH reads the stored ``always_confirm`` as false, so it checks the body alone | ``test_the_key_on_an_always_confirm_row_is_422`` |
| R2 | the PATCH skips the OAuth check | ``test_an_oauth_row_refuses_the_key`` |
| R3 | the register event leaves out ``read_only_key`` | ``test_a_re_post_with_no_key_turns_it_off_and_audits_both_flags`` |
| R4 | the merge statement of the PATCH loses its org predicate | ``test_the_store_predicate_holds_on_a_session_that_bypasses_rls`` |
| R5 | the PATCH skips the role check | ``test_a_member_without_manage_gets_403`` |
| R6 | the PATCH audits the key under the always-confirm action | ``test_the_patch_sets_the_key_keeps_the_headers_and_audits`` |
| R7 | an unnamed key keeps its RAW stored value, not its parsed state (review P3) | ``test_a_damaged_row_keeps_the_key_off_when_always_confirm_turns_off`` |
| R8 | the PATCH reads the stored key as false, so it checks the body alone | ``test_always_confirm_on_a_read_only_key_row_is_422`` |

Under R1, the parse still reads the key as false, because the row reads as
always confirm. So a row that a bad write leaves with both on asks the card.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_read_only_key_r8.py -v -rs
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

from acb_common.db import bind_tenant, release_tenant
from sqlalchemy import text

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
    _user,
    ns,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_on_promoted,
)

# ⚠️ Without this gate the suite ERRORS where no ladder URL is set (the deploy
# run), and the deploy stops. With it, the suite SKIPS there, so a run with
# no database reads its skip count (``-rs``), never a silent green.
pytestmark = _DB_GATE

SENTINEL = "Bearer sentinel-mcpc2-read-only-key-8c31"
TOKEN = "sentinel-mcpc2-oauth-token-41d7"
ADMIN_A = "admin-a@mcpc2-r8.test"
ADMIN_B = "admin-b@mcpc2-r8.test"


@pytest.fixture
def audit(monkeypatch):
    import acb_audit

    events: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", events.append)
    return events


async def _post(user: Any, name: str, *, status: int = 201, **extra: Any) -> Any:
    body = {"name": name, "transport": "http-sse", "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": SENTINEL}, **extra}
    res = await _call(user, "POST", "/integrations/mcp", body)
    assert res.status_code == status, res.text
    return res


async def _patch(user: Any, name: str, body: dict[str, Any]) -> Any:
    return await _call(user, "PATCH", f"/integrations/mcp/{name}", body)


async def _listed(user: Any, name: str) -> dict[str, Any]:
    res = await _call(user, "GET", "/integrations/mcp")
    assert res.status_code == 200, res.text
    return next(r for r in res.json() if r["name"] == name)


def _meta(p: Any, org: str, name: str) -> dict[str, Any]:
    return dict(_registry(p, org)[name]["provider_meta"])


async def _stored(org: str, name: str) -> Any:
    from gateway.connections import mcp

    server = await mcp.get_one(org, name)
    assert server is not None
    return server


async def _read_only_key_server(user: Any, name: str) -> None:
    """A header server with the switch on, through the real routes."""
    await _post(user, name, always_confirm=False, read_only_key=True)


async def _make_oauth(org: str, name: str) -> None:
    """Turn the header row *name* of *org* into an OAuth row (IN-8a, D98),
    through the one writer of a consent, ``store.attach_oauth_credential``."""
    from acb_common.db import tenant_session
    from gateway.connections import mcp, store
    from gateway.crm_sources.base import SourceCredential

    credential = SourceCredential(
        access_token=TOKEN, refresh_token=f"refresh-{TOKEN}",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with tenant_session(org) as db:
        done = await store.attach_oauth_credential(
            db, org, mcp.provider_for(name), mcp.OWNING_APP, credential, connected_by=ADMIN_A,
        )
    assert done is not None, "the OAuth consent did not attach"


# ── A37: a new server, and the register ─────────────────────────────────────


async def test_a_new_server_reads_as_false(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    user = _user(ADMIN_A, ns.org_a)
    plain, ro = _name("new"), _name("ro")
    await _post(user, plain)
    await _read_only_key_server(user, ro)

    assert _meta(ns, ns.org_a, plain)["read_only_key"] is False
    assert (await _stored(ns.org_a, plain)).read_only_key is False
    assert (await _listed(user, plain))["read_only_key"] is False
    assert _meta(ns, ns.org_a, ro)["read_only_key"] is True
    assert (await _stored(ns.org_a, ro)).read_only_key is True
    assert (await _listed(user, ro))["read_only_key"] is True

    # The writer itself defaults to false, for a caller that names no value.
    from gateway.connections import mcp

    direct = _name("direct")
    await mcp.save(ns.org_a, name=direct, url="https://mcp.example.com/mcp",
                   headers={}, connected_by="r8")
    assert _meta(ns, ns.org_a, direct)["read_only_key"] is False


async def test_a_register_with_both_switches_on_is_422(ns, audit) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("both")
    # ``always_confirm`` is true by default, so the key alone is both on.
    for extra in ({"read_only_key": True}, {"read_only_key": True, "always_confirm": True}):
        res = await _post(user, name, status=422, **extra)
        assert "always confirm" in res.json()["detail"], res.text
    assert name not in _registry(ns, ns.org_a), "a refused register wrote a row"
    assert audit == []


async def test_a_register_refuses_a_key_that_is_not_a_boolean(ns, audit) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    for value in ("true", "on", 1):
        name = _name("strict")
        await _post(user, name, status=422, always_confirm=False, read_only_key=value)
        assert name not in _registry(ns, ns.org_a)
    assert audit == []


async def test_a_re_post_with_no_key_turns_it_off_and_audits_both_flags(ns, audit) -> None:  # noqa: F811
    """A POST of a name that exists is a full register (item 32)."""
    user = _user(ADMIN_A, ns.org_a)
    name = _name("repost")
    await _read_only_key_server(user, name)
    (first,) = audit
    assert first.action == "integration.mcp.registered" and first.target == f"mcp:{name}"
    assert first.payload == {"organization_id": ns.org_a, "always_confirm": False,
                             "read_only_key": True, "outcome": "created"}
    assert (await _stored(ns.org_a, name)).read_only_key is True

    audit.clear()
    await _post(user, name, always_confirm=False)
    assert _meta(ns, ns.org_a, name)["read_only_key"] is False
    assert (await _stored(ns.org_a, name)).read_only_key is False
    (event,) = audit
    assert event.action == "integration.mcp.registered"
    assert event.payload == {"organization_id": ns.org_a, "always_confirm": False,
                             "read_only_key": False, "outcome": "updated"}
    shown = repr(audit)
    assert SENTINEL not in shown and "sentinel-mcpc2" not in shown
    assert "mcp.example.com" not in shown, "the url reached the audit payload"


# ── A37: the PATCH ──────────────────────────────────────────────────────────


async def test_the_patch_sets_the_key_keeps_the_headers_and_audits(ns, audit) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("set")
    await _post(user, name, always_confirm=False)
    audit.clear()

    res = await _patch(user, name, {"read_only_key": True})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "name": name, "read_only_key": True}
    assert _meta(ns, ns.org_a, name)["read_only_key"] is True
    server = await _stored(ns.org_a, name)
    assert server.read_only_key is True and server.always_confirm is False
    # The headers, the url and the rest of the row stay.
    assert server.headers == {"Authorization": SENTINEL}
    assert server.url == "https://mcp.example.com/mcp" and server.enabled is True

    (event,) = audit
    assert event.actor == f"user:{ADMIN_A}" and event.target == f"mcp:{name}"
    assert event.action == "integration.mcp.read_only_key"
    assert event.organization_id == ns.org_a
    assert event.payload == {"organization_id": ns.org_a, "read_only_key": True}

    audit.clear()
    res = await _patch(user, name, {"read_only_key": False})
    assert res.status_code == 200, res.text
    assert (await _stored(ns.org_a, name)).read_only_key is False
    (event,) = audit
    assert event.payload == {"organization_id": ns.org_a, "read_only_key": False}
    assert SENTINEL not in repr(audit)


async def test_the_console_body_sends_both_keys_in_one_patch(ns, audit) -> None:  # noqa: F811
    """A tick of the key on an always-confirm server clears that switch. The
    console sends both keys, and each key writes its own event."""
    user = _user(ADMIN_A, ns.org_a)
    name = _name("console")
    await _post(user, name)  # always confirm, the default of a new server
    audit.clear()

    res = await _patch(user, name, {"always_confirm": False, "read_only_key": True})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "name": name, "always_confirm": False,
                          "read_only_key": True}
    meta = _meta(ns, ns.org_a, name)
    assert meta["always_confirm"] is False and meta["read_only_key"] is True
    assert [(e.action, e.payload) for e in audit] == [
        ("integration.mcp.always_confirm",
         {"organization_id": ns.org_a, "always_confirm": False}),
        ("integration.mcp.read_only_key",
         {"organization_id": ns.org_a, "read_only_key": True}),
    ]

    # And back: a tick of always confirm clears the key.
    res = await _patch(user, name, {"always_confirm": True, "read_only_key": False})
    assert res.status_code == 200, res.text
    server = await _stored(ns.org_a, name)
    assert server.always_confirm is True and server.read_only_key is False


async def test_a_body_with_both_switches_on_is_422(ns, audit) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("both")
    await _post(user, name, always_confirm=False)
    audit.clear()
    res = await _patch(user, name, {"always_confirm": True, "read_only_key": True})
    assert res.status_code == 422, res.text
    meta = _meta(ns, ns.org_a, name)
    assert meta["always_confirm"] is False and meta["read_only_key"] is False
    assert audit == []


async def test_the_key_on_an_always_confirm_row_is_422(ns, audit) -> None:  # noqa: F811
    """The gateway checks the row AFTER the change, not the body alone."""
    user = _user(ADMIN_A, ns.org_a)
    name = _name("merged")
    await _post(user, name)  # always confirm
    audit.clear()
    res = await _patch(user, name, {"read_only_key": True})
    assert res.status_code == 422, res.text
    assert "always confirm" in res.json()["detail"]
    meta = _meta(ns, ns.org_a, name)
    assert meta["always_confirm"] is True and meta["read_only_key"] is False
    assert audit == []


async def test_always_confirm_on_a_read_only_key_row_is_422(ns, audit) -> None:  # noqa: F811
    """The other way round (review of MCP-C2): ``{"always_confirm": true}``
    alone on a row whose key is on would leave both on, so it is 422. The
    console sends both keys, and that body clears the key."""
    user = _user(ADMIN_A, ns.org_a)
    name = _name("merged-ro")
    await _read_only_key_server(user, name)
    audit.clear()
    res = await _patch(user, name, {"always_confirm": True})
    assert res.status_code == 422, res.text
    assert "always confirm" in res.json()["detail"]
    meta = _meta(ns, ns.org_a, name)
    assert meta["always_confirm"] is False and meta["read_only_key"] is True
    server = await _stored(ns.org_a, name)
    assert server.read_only_key is True and server.always_confirm is False
    assert audit == []


async def test_a_damaged_row_keeps_the_key_off_when_always_confirm_turns_off(
        ns, audit, monkeypatch) -> None:  # noqa: F811
    """Review of MCP-C2 (P3). A row with both keys stored true reads as always
    confirm, so its key is off. ``{"always_confirm": false}`` names only one
    switch, so the key keeps the state that the parse reads: off. Before the
    fix, the raw stored key came back into force with no admin act and no
    ``read_only_key`` event, and a destructive tool ran with no card."""
    from types import SimpleNamespace

    from agent_framework import FunctionTool
    from orchestrator import _maf_mcp

    user = _user(ADMIN_A, ns.org_a)
    name = _name("damaged")
    await _post(user, name, always_confirm=False)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET provider_meta = provider_meta || "
            "'{\"always_confirm\": true, \"read_only_key\": true}'::jsonb "
            "WHERE organization_id = CAST(:o AS uuid) AND provider = :p"),
            {"o": ns.org_a, "p": f"mcp:{name}"})
    before = await _stored(ns.org_a, name)
    assert before.always_confirm is True and before.read_only_key is False
    audit.clear()

    res = await _patch(user, name, {"always_confirm": False})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "name": name, "always_confirm": False}
    after = await _stored(ns.org_a, name)
    assert after.always_confirm is False
    assert after.read_only_key is False, "the stored key came back with no admin act"
    assert _meta(ns, ns.org_a, name)["read_only_key"] is False, (
        "the stored row does not match what the gate reads")
    assert [e.action for e in audit] == ["integration.mcp.always_confirm"]

    # The run: the tool carries no read-only key, so a destructive tool needs
    # the card. This run has no card channel, so the gate refuses the call.
    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    token = bind_tenant(ns.org_a)
    try:
        agent = await _inject(ns.org_a)
    finally:
        release_tenant(token)
    (tool,) = [t for t in agent.mcp_tools if t.name == name]
    assert _maf_mcp.server_read_only_key(tool) is False
    ran: list[str] = []

    def _delete(**_k: Any) -> str:
        ran.append("delete")
        return "deleted"

    fn = FunctionTool(name=f"{name}_delete_record", func=_delete, description="Delete.",
                      additional_properties={"_mcp_is_tool": True,
                                             "_mcp_remote_name": "delete_record"})
    tool._functions = [fn]
    tool.__dict__["_ws8c_tool_meta"] = {"delete_record": _maf_mcp.ToolMeta(
        hints={"readOnlyHint": False, "destructiveHint": True}, description="Delete.")}
    gate = _maf_mcp.McpPermissionGate(tools=[tool], member_present=True)
    ctx = SimpleNamespace(function=fn, arguments={"record_id": "r-1"}, result=None)

    async def _call_next() -> None:
        ran.append("call")

    await gate.process(ctx, _call_next)
    assert ran == [], "a destructive tool ran with no card"
    assert ctx.result == _maf_mcp.NO_MEMBER_TEXT.format(tool=fn.name)


async def test_the_patch_refuses_a_value_that_is_not_a_boolean(ns, audit) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("strict")
    await _post(user, name, always_confirm=False)
    audit.clear()
    for body in ({"read_only_key": "true"}, {"read_only_key": "on"}, {"read_only_key": 1},
                 {"read_only_key": None}, {}):
        res = await _patch(user, name, body)
        assert res.status_code == 422, (body, res.text)
    assert _meta(ns, ns.org_a, name)["read_only_key"] is False
    assert audit == []


async def test_an_oauth_row_refuses_the_key(ns, audit) -> None:  # noqa: F811
    """D98: an OAuth row acts as the admin who connected it, with that
    admin's own scopes. A key attestation is wrong in kind there."""
    user = _user(ADMIN_A, ns.org_a)
    name = _name("oauth")
    await _post(user, name, always_confirm=False)
    await _make_oauth(ns.org_a, name)
    assert _meta(ns, ns.org_a, name)["auth"] == "oauth"
    audit.clear()

    res = await _patch(user, name, {"read_only_key": True})
    assert res.status_code == 422, res.text
    assert "OAuth" in res.json()["detail"]
    assert _meta(ns, ns.org_a, name)["read_only_key"] is False
    # A re-register of the OAuth row cannot set it either.
    await _post(user, name, status=422, always_confirm=False, read_only_key=True)
    assert _meta(ns, ns.org_a, name)["read_only_key"] is False
    assert audit == []

    # The parse reads a stored key as false on an OAuth row, and a change of
    # the other switch is not refused for it.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET provider_meta = provider_meta || "
            "'{\"read_only_key\": true}'::jsonb WHERE organization_id = CAST(:o AS uuid) "
            "AND provider = :p"), {"o": ns.org_a, "p": f"mcp:{name}"})
    assert (await _stored(ns.org_a, name)).read_only_key is False
    assert (await _listed(user, name))["read_only_key"] is False
    res = await _patch(user, name, {"always_confirm": True})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text


async def test_a_body_with_only_always_confirm_behaves_as_a33(ns, audit) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name = _name("a33")
    await _post(user, name)
    audit.clear()
    res = await _patch(user, name, {"always_confirm": False})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "name": name, "always_confirm": False}
    (event,) = audit
    assert event.action == "integration.mcp.always_confirm"
    assert event.payload == {"organization_id": ns.org_a, "always_confirm": False}
    assert _meta(ns, ns.org_a, name)["read_only_key"] is False


async def test_a_member_without_manage_gets_403(ns, audit) -> None:  # noqa: F811
    from acb_auth import UserContext, UserRole, build_access

    name = _name("perm")
    await _post(_user(ADMIN_A, ns.org_a), name, always_confirm=False)
    audit.clear()
    for perms in (["feature:integrations"], ["feature:integrations", "admin:members:read"]):
        member = UserContext(email="plain@mcpc2-r8.test", role=UserRole.EMPLOYEE,
                             access=build_access(perms), organization_id=ns.org_a)
        res = await _patch(member, name, {"read_only_key": True})
        assert res.status_code == 403, (perms, res.text)
    assert _meta(ns, ns.org_a, name)["read_only_key"] is False
    assert audit == []


async def test_org_b_cannot_set_org_a_key(ns, audit) -> None:  # noqa: F811
    name_a, name_b = _name("a"), _name("b")
    await _post(_user(ADMIN_A, ns.org_a), name_a, always_confirm=False)
    await _post(_user(ADMIN_B, ns.org_b), name_b, always_confirm=False)
    audit.clear()

    res = await _patch(_user(ADMIN_B, ns.org_b), name_a, {"read_only_key": True})
    assert res.status_code == 404, res.text
    assert _meta(ns, ns.org_a, name_a)["read_only_key"] is False, "org B set org A's key"
    assert name_a not in _registry(ns, ns.org_b), "the PATCH wrote a row in org B"
    assert audit == []

    # Not vacuous: org B sets its own server's key through the same route.
    res = await _patch(_user(ADMIN_B, ns.org_b), name_b, {"read_only_key": True})
    assert res.status_code == 200, res.text
    assert _meta(ns, ns.org_b, name_b)["read_only_key"] is True
    assert _meta(ns, ns.org_a, name_a)["read_only_key"] is False


async def test_the_store_predicate_holds_on_a_session_that_bypasses_rls(ns) -> None:  # noqa: F811
    """Two orgs hold one name. On a session with RLS off, only the org
    predicate of each statement keeps the change in one org."""
    from gateway.connections import mcp

    from tests.unit._tenant_ladder import tenant_engine_scope

    shared = _name("shared")
    await _post(_user(ADMIN_A, ns.org_a), shared, always_confirm=False)
    await _post(_user(ADMIN_B, ns.org_b), shared)  # always confirm in org B
    admin_dsn = ns.admin_engine.url.render_as_string(hide_password=False)
    async with tenant_engine_scope(admin_dsn):
        changed = await mcp.set_switches(ns.org_a, shared, read_only_key=True)
    assert changed is not None and changed.read_only_key is True
    assert _meta(ns, ns.org_a, shared)["read_only_key"] is True
    meta_b = _meta(ns, ns.org_b, shared)
    assert meta_b["read_only_key"] is False and meta_b["always_confirm"] is True, (
        "the change reached the other org's row of the same name")


# ── A38: the reads return the flag, and no secret ───────────────────────────


async def test_the_reads_return_the_flag_and_no_header_value(ns, caplog) -> None:  # noqa: F811
    user = _user(ADMIN_A, ns.org_a)
    name, off = _name("read"), _name("read-off")
    bodies: list[str] = []
    with structlog.testing.capture_logs() as logs:
        await _post(user, name, always_confirm=False)
        await _post(user, off)
        res = await _patch(user, name, {"read_only_key": True})
        bodies.append(res.text)
        listed = await _call(user, "GET", "/integrations/mcp")
        bodies.append(listed.text)
        model = await _call(user, "GET", "/integrations/connections")
        bodies.append(model.text)
    mine = next(r for r in listed.json() if r["name"] == name)
    assert mine["read_only_key"] is True and mine["always_confirm"] is False
    assert mine["header_names"] == ["Authorization"]
    assert next(r for r in listed.json() if r["name"] == off)["read_only_key"] is False
    rows = {r["id"]: r for r in model.json()["connections"]}
    assert rows[f"mcp:{name}"]["detail"]["read_only_key"] == "true"
    assert rows[f"mcp:{off}"]["detail"]["read_only_key"] == "false"
    everything = "\n".join(bodies) + "\n" + logged_text(logs, caplog)
    assert SENTINEL not in everything and "sentinel-mcpc2" not in everything


async def test_a_run_carries_the_key_of_each_server(ns, monkeypatch) -> None:  # noqa: F811
    """The real reader, the real injection: each attached tool carries the
    key of its own row, as the one parse read it."""
    from orchestrator import _maf_mcp

    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    user = _user(ADMIN_A, ns.org_a)
    ro, plain = _name("ro"), _name("plain")
    await _read_only_key_server(user, ro)
    await _post(user, plain, always_confirm=False)
    token = bind_tenant(ns.org_a)
    try:
        agent = await _inject(ns.org_a)
    finally:
        release_tenant(token)
    flags = {t.name: _maf_mcp.server_read_only_key(t) for t in agent.mcp_tools}
    assert flags == {ro: True, plain: False}
