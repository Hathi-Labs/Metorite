"""R8 — MCP OAuth for an org connection, two orgs (WS-54 IN-8a, row O9).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.4, §3.6.6 and
§3.6.11 (O9, and the column half of O6). D98: the connection acts as the
admin who connected it.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (the owner role bypasses RLS, so it would prove nothing),
through the real routers, the real store and the real guard, with a local
fake auth server (``_mcp_oauth_fakes.py``):

- orgs A and B connect ONE server name. A run in A sends the token of A, and
  a run in B sends the token of B;
- org B can never complete org A's consent: B's admin with A's state is 400,
  and A's row stays as it was;
- the admin of A cannot see or disconnect a server of B;
- the consent keeps the row: its url, its label and its H-300 switch stay,
  ``connected_by`` is the admin, and a reconnect by another admin changes it;
- no sentinel token or secret is plain text in any column of the row;
- the store predicate holds on a session that bypasses RLS.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_oauth_org_scope_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

from sqlalchemy import text

from tests.unit import _mcp_oauth_fakes as f
from tests.unit._maf_mcp_fakes import install_offline, set_flag
from tests.unit._tenant_ladder import tenant_engine_scope

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
    _registry,
    ns,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_on_promoted,
)

# ⚠️ Without this gate the suite ERRORS where no ladder URL is set (the deploy
# run), and the deploy stops.
pytestmark = _DB_GATE

ADMIN_A = "admin-a@in8a-r8.test"
ADMIN_A2 = "second-admin-a@in8a-r8.test"
ADMIN_B = "admin-b@in8a-r8.test"
TOKEN_A = "SENTINEL-in8a-r8-token-org-a"
TOKEN_B = "SENTINEL-in8a-r8-token-org-b"


def _name(tag: str) -> str:
    return f"in8a-{tag}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def world(ns, monkeypatch) -> f.World:  # noqa: F811 - a fixture
    f.configure(monkeypatch, keep_key=True)
    return f.install(monkeypatch, f.World())


async def _seed(org: str, name: str, *, always_confirm: bool = True) -> None:
    from gateway.connections import mcp

    await mcp.save(org, name=name, url=f.MCP_URL, headers={}, connected_by="r8-seed",
                   label=f"Label {name}", always_confirm=always_confirm)


async def _connect(world: f.World, who: Any, name: str, token: str) -> Any:
    world.access_token = token
    world.refresh_token = f"refresh-{token}"
    _, code, state = await f.start_and_consent(who, world, name)
    return await f.finish(who, code, state, iss=f.ISSUER)


async def test_two_orgs_connect_one_name_and_each_run_sends_its_own_token(
        ns, world, app_engine, monkeypatch) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    name = _name("both")
    await _seed(ns.org_a, name)
    await _seed(ns.org_b, name)
    res_a = await _connect(world, f.user(ADMIN_A, ns.org_a), name, TOKEN_A)
    res_b = await _connect(world, f.user(ADMIN_B, ns.org_b), name, TOKEN_B)
    assert res_a.status_code == 302 and res_a.headers["location"].endswith(name)
    assert res_b.status_code == 302 and res_b.headers["location"].endswith(name)

    agent_a = await _inject(ns.org_a)
    agent_b = await _inject(ns.org_b)
    tools_a = {t.name: t for t in agent_a.mcp_tools}
    tools_b = {t.name: t for t in agent_b.mcp_tools}
    assert tools_a[name]._static_headers == {"Authorization": f"Bearer {TOKEN_A}"}
    assert tools_b[name]._static_headers == {"Authorization": f"Bearer {TOKEN_B}"}

    # The flag off: a run in either org leaves the OAuth row out (O12, R8).
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "mcp_oauth_enabled", False, raising=False)
    assert name not in {t.name for t in (await _inject(ns.org_a)).mcp_tools}


async def test_org_b_can_never_complete_org_a_consent(ns, world) -> None:  # noqa: F811
    name = _name("steal")
    await _seed(ns.org_a, name)
    await _seed(ns.org_b, name)
    admin_a = f.user(ADMIN_A, ns.org_a)
    _, code, state = await f.start_and_consent(admin_a, world, name)
    stolen = await f.finish(f.user(ADMIN_B, ns.org_b), code, state, iss=f.ISSUER)
    assert stolen.status_code == 400 and stolen.json()["detail"] == "invalid_state"
    assert [r for r in world.requests if r.url.path == "/token"] == []
    for org in (ns.org_a, ns.org_b):
        row = _registry(ns, org)[name]
        assert row["provider_meta"].get("auth") is None, "a refused consent wrote a row"
    # A's own callback still finishes, and writes A's row only.
    done = await f.finish(admin_a, code, state, iss=f.ISSUER)
    assert done.status_code == 302
    assert _registry(ns, ns.org_a)[name]["provider_meta"]["auth"] == "oauth"
    assert _registry(ns, ns.org_b)[name]["provider_meta"].get("auth") is None


async def test_the_admin_of_a_cannot_see_or_disconnect_a_server_of_b(
        ns, world) -> None:  # noqa: F811
    only_b = _name("bonly")
    await _seed(ns.org_b, only_b)
    assert (await _connect(world, f.user(ADMIN_B, ns.org_b), only_b, TOKEN_B)).status_code == 302
    admin_a = f.user(ADMIN_A, ns.org_a)
    listed = await _call(admin_a, "GET", "/integrations/connections")
    assert listed.status_code == 200
    assert only_b not in listed.text and TOKEN_B not in listed.text
    start = await f.call(admin_a, "POST", f"/integrations/mcp/{only_b}/oauth/start")
    assert start.status_code == 404
    gone = await _call(admin_a, "POST", f"/integrations/connections/mcp:{only_b}/disconnect")
    assert gone.status_code == 404
    deleted = await _call(admin_a, "DELETE", f"/integrations/mcp/{only_b}")
    assert deleted.status_code == 404
    row = _registry(ns, ns.org_b)[only_b]
    assert row["status"] == "connected" and row["provider_meta"]["auth"] == "oauth"


async def test_the_consent_keeps_the_row_and_names_the_admin(ns, world) -> None:  # noqa: F811
    name = _name("keep")
    await _seed(ns.org_a, name, always_confirm=True)
    before = _registry(ns, ns.org_a)[name]
    assert (await _connect(world, f.user(ADMIN_A, ns.org_a), name, TOKEN_A)).status_code == 302
    row = _registry(ns, ns.org_a)[name]
    meta = row["provider_meta"]
    assert meta["url"] == f.MCP_URL and meta["label"] == f"Label {name}"
    assert meta["always_confirm"] is True and meta["auth"] == "oauth"
    assert meta["registration"] == "dcr" and meta["issuer"] == f.ISSUER
    assert row["connected_by"] == ADMIN_A  # D98
    assert row["credentials_encrypted"] != before["credentials_encrypted"]
    # Every column of the row, as one JSON text: no plain token or secret.
    for sentinel in (TOKEN_A, f"refresh-{TOKEN_A}", f.SENTINEL_SECRET):
        assert sentinel not in row["whole"], f"{sentinel} is plain text in the row"
    # A reconnect by another admin changes connected_by, and revokes the old
    # refresh token after the commit.
    res = await _connect(world, f.user(ADMIN_A2, ns.org_a), name, TOKEN_B)
    assert res.status_code == 302
    assert _registry(ns, ns.org_a)[name]["connected_by"] == ADMIN_A2
    assert world.revoked == [f"refresh-{TOKEN_A}"]
    # The console lists the server once, with no header, as OAuth.
    from gateway.connections import mcp

    listed = {s.name: s for s in await mcp.list_servers(ns.org_a)}
    assert listed[name].oauth is True and listed[name].connected_by == ADMIN_A2


async def test_the_store_predicate_holds_on_a_session_that_bypasses_rls(
        ns, world) -> None:  # noqa: F811
    """The store names the org in each statement, so a session with RLS off
    still writes one org. The admin is a superuser: RLS does not filter it."""
    from datetime import UTC, datetime

    from acb_common.db import tenant_session
    from gateway.connections import mcp, store
    from gateway.crm_sources.base import SourceCredential

    name = _name("pred")
    await _seed(ns.org_a, name)
    await _seed(ns.org_b, name)
    cred = SourceCredential(access_token=TOKEN_B, refresh_token="r",
                            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                            provider_meta={"issuer": f.ISSUER})
    admin_dsn = ns.admin_engine.url.render_as_string(hide_password=False)
    async with tenant_engine_scope(admin_dsn), tenant_session(ns.org_b) as db:
        seen = (await db.execute(text(
            "SELECT count(*) FROM integration_connections WHERE provider = :p"),
            {"p": mcp.provider_for(name)})).scalar_one()
        done = await store.attach_oauth_credential(
            db, ns.org_b, mcp.provider_for(name), mcp.OWNING_APP, cred,
            connected_by=ADMIN_B)
    assert seen == 2, "the admin session must see both orgs, or this proves nothing"
    assert done is not None and done.after.organization_id == ns.org_b
    assert _registry(ns, ns.org_a)[name]["provider_meta"].get("auth") is None
    assert _registry(ns, ns.org_b)[name]["provider_meta"]["auth"] == "oauth"
