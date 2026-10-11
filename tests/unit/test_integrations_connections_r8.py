"""R8 - the Integrations console read model, against a real Postgres (WS-54 IN-1).

Spec: ``project-docs/specs/integrations_console.md`` §3.2, §3.5, §4, §7.2 (IN-1)
and §9. The module under test is ``routes/integrations_connections.py``.

What this proves on a REAL Postgres, through the real router, as the
NON-privileged role of the phase-4 rehearsal (no superuser, no BYPASSRLS), with
two organizations that each hold all five sources (Zoho, a mailbox, a WhatsApp
number, an MCP server, a stored key):

- each organization lists its own five rows and none of the other's;
- a sentinel in a Zoho blob, an MCP header, a provider key and a mailbox
  credential reaches no response and no log line;
- a mailbox row has no message content;
- a disconnect of Zoho, MCP and a key works for the admin of the org, writes
  one audit event, and leaves the other org's rows alone;
- a mailbox or a WhatsApp number is 409, and another org's id is 404;
- the MCP test sends the STORED header, and a refused URL sends nothing.

Hermetic fakes agree with any SQL they are handed, so none of this is proved
without the database. ``tests/unit/test_integrations_connections.py`` holds the
rules that need no database.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_connection import FakeZoho
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import graph_on_promoted  # noqa: F401

# Every test here needs the real database. Without this gate the suite ERRORS
# where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

ADMIN_A = "admin-a@in1-r8.test"
ADMIN_B = "admin-b@in1-r8.test"
PERMS = ["feature:integrations", "admin:members:read", "integrations:manage"]

S_ZOHO = "SENTINEL-ZOHO"
S_HEADER = "SENTINEL-HEADER"
S_ENV = "SENTINEL-ENVVAL"
S_KEY = "SENTINEL-KEYVAL"
S_MAIL_CRED = "SENTINEL-MAILCRED"
S_MAIL_BODY = "SENTINEL-MAILBODY"
S_MAIL_SUBJECT = "SENTINEL-MAILSUBJECT"
S_WA_CRED = "SENTINEL-WACRED"
ALL_SENTINELS = (
    S_ZOHO, S_HEADER, S_ENV, S_KEY, S_MAIL_CRED, S_MAIL_BODY, S_MAIL_SUBJECT, S_WA_CRED,
)


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _assert_non_priv(app_eng: Any) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


def _user(email: str, org: str, perms: list[str] | None = None) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(PERMS if perms is None else perms),
        organization_id=org,
    )


class Seed:
    """The ids of what one org holds."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.mcp = f"in1-mcp-{tag}-{uuid.uuid4().hex[:6]}"
        self.mailbox = f"box-{tag}@in1-r8.test"
        self.member = f"member-{tag}@in1-r8.test"
        self.zoho_id = ""
        self.mailbox_id = ""
        self.wa_id = ""


@pytest.fixture
def ns(graph_on_promoted, monkeypatch):  # noqa: F811 - a fixture
    """The promoted catalog, the key seam on the app role, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    settings = get_settings()
    for name, value in {
        "database_url": _app_dsn(graph_on_promoted),
        "crm_zoho_client_id": "1000.IN1CLIENT",
        "crm_zoho_client_secret": "in1-client-secret",
        "gateway_session_secret": "in1-session-secret-not-a-default-0123456789",
        "acb_master_key": "in1-master-key-for-the-blob",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.metorite.com")
    monkeypatch.setattr(key_store, "_store", None)

    from gateway.crm_sources.zoho import ZohoSource
    from gateway.routes.crm import connection as conn

    fake = FakeZoho()
    graph_on_promoted.zoho = fake
    monkeypatch.setattr(
        conn, "_source",
        lambda credential=None, transport=None: ZohoSource(
            conn.zoho_oauth_config(), credential, transport=fake.transport),
    )
    yield graph_on_promoted
    with graph_on_promoted.admin_engine.begin() as c:
        for org in (graph_on_promoted.org_a, graph_on_promoted.org_b):
            for table in ("integration_connections", "provider_keys", "mcp_servers"):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})
            c.execute(text(
                "DELETE FROM email_messages WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org})
            for table in ("email_accounts", "wa_accounts"):
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def _seed(p: Any, org: str, tag: str, *, url: str = "https://mcp.example.com/sse") -> Seed:
    """One row in each of the five sources, each with a sentinel secret."""
    from acb_common.db import tenant_session
    from gateway.connections import store
    from gateway.crm_sources.base import SourceCredential

    s = Seed(tag)
    credential = SourceCredential(
        access_token=f"{S_ZOHO}-ACCESS-{tag}", refresh_token=f"{S_ZOHO}-REFRESH-{tag}",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=("ZohoCRM.modules.leads.READ",), provider_meta={"location": "in", "accounts_server": "https://accounts.zoho.in"},
    )
    async with tenant_engine_scope(_app_dsn(p)):
        async with tenant_session(org) as db:
            row = await store.save_credential(
                db, org, "zoho", "crm", credential, connected_by=f"admin-{tag}@in1-r8.test",
                scopes=("ZohoCRM.modules.leads.READ",),
            )
    s.zoho_id = row.id
    with p.admin_engine.begin() as c:
        s.mailbox_id = str(c.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted, sync_status, sync_error, organization_id) "
            "VALUES (:u, 'microsoft', :m, :cred, 'error', "
            "'AADSTS70008 refresh token expired for ' || :m, CAST(:o AS uuid)) "
            "RETURNING id"),
            {"u": s.member, "m": s.mailbox, "cred": f"{S_MAIL_CRED}-{tag}", "o": org},
        ).scalar_one())
        c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, folder, "
            "subject, body_text, from_address, to_addresses, organization_id) "
            "VALUES (CAST(:a AS uuid), :pm, 'inbox', :subj, :body, "
            "CAST('{\"email\": \"x@y.test\"}' AS jsonb), '[]'::jsonb, "
            "CAST(:o AS uuid))"),
            {"a": s.mailbox_id, "pm": f"pm-{uuid.uuid4().hex}",
             "subj": f"{S_MAIL_SUBJECT}-{tag}", "body": f"{S_MAIL_BODY}-{tag}", "o": org})
        s.wa_id = str(c.execute(text(
            "INSERT INTO wa_accounts (user_id, phone_number, phone_number_id, "
            "display_name, credentials_encrypted, provider, sync_status, "
            "organization_id) VALUES (:u, :ph, :pid, :dn, :cred, 'cloud_api', "
            "'live', CAST(:o AS uuid)) RETURNING id"),
            {"u": s.member, "ph": "+910000000001", "pid": uuid.uuid4().hex[:12],
             "dn": f"Shop {tag}", "cred": f"{S_WA_CRED}-{tag}", "o": org},
        ).scalar_one())
        c.execute(text(
            "INSERT INTO mcp_servers (name, label, transport, url, headers, "
            "env_vars, enabled, organization_id) VALUES (:name, :label, "
            "'http-sse', :url, CAST(:h AS jsonb), CAST(:e AS jsonb), true, :org)"),
            {"name": s.mcp, "label": f"Tools {tag}", "url": url, "org": org,
             "h": json.dumps({"Authorization": f"{S_HEADER}-{tag}"}),
             "e": json.dumps({"API_KEY": f"{S_ENV}-{tag}"})})
        c.execute(text(
            "INSERT INTO provider_keys (organization_id, provider, encrypted, "
            "credential_type, service) VALUES (CAST(:o AS uuid), 'apollo:api_key', "
            ":enc, 'integration', 'apollo')"),
            {"o": org, "enc": f"{S_KEY}-{tag}"})
    return s


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None,
) -> Any:
    """One request through the real router, as *user*.

    The override binds the tenant from the user, which is what
    ``_with_resolved_access`` does for a real request. The tenant never comes
    from the request.
    """
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import integrations_connections as mod

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://in1.test",
            ) as client:
                return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


def _ids(res: Any) -> set[str]:
    assert res.status_code == 200, res.text
    return {r["id"] for r in res.json()["connections"]}


def _count(p: Any, sql: str, **params: Any) -> int:
    with p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


# -- Two orgs, each its own ----------------------------------------------------


async def test_each_org_lists_only_its_five_rows(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    b = await _seed(ns, ns.org_b, "b")
    ids_a = _ids(await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/connections"))
    ids_b = _ids(await _call(ns, _user(ADMIN_B, ns.org_b), "GET", "/integrations/connections"))
    assert ids_a == {
        f"zoho:{a.zoho_id}", f"mailbox:{a.mailbox_id}", f"whatsapp:{a.wa_id}",
        f"mcp:{a.mcp}", "key:apollo",
    }
    assert ids_b == {
        f"zoho:{b.zoho_id}", f"mailbox:{b.mailbox_id}", f"whatsapp:{b.wa_id}",
        f"mcp:{b.mcp}", "key:apollo",
    }
    assert not any(i in ids_a for i in (f"mailbox:{b.mailbox_id}", f"mcp:{b.mcp}"))
    assert not any(i in ids_b for i in (f"mailbox:{a.mailbox_id}", f"mcp:{a.mcp}"))


async def test_the_rows_carry_the_contract_fields_and_sort(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/connections")
    rows = res.json()["connections"]
    assert res.json()["unavailable"] == []
    keys = {"id", "kind", "provider", "label", "owning_app", "scope", "owner", "status",
            "last_sync_at", "last_error", "setup_href", "can_disconnect", "detail"}
    assert all(set(r) == keys for r in rows)
    assert [(r["owning_app"], r["label"].lower()) for r in rows] == sorted(
        (r["owning_app"], r["label"].lower()) for r in rows)
    by_id = {r["id"]: r for r in rows}
    zoho = by_id[f"zoho:{a.zoho_id}"]
    assert (zoho["kind"], zoho["owning_app"], zoho["scope"], zoho["status"]) == (
        "oauth", "crm", "org", "connected")
    assert zoho["owner"] == "admin-a@in1-r8.test" and zoho["setup_href"] == "/crm"
    assert zoho["detail"] == {"location": "in", "scopes": "1"} and zoho["can_disconnect"]
    mail = by_id[f"mailbox:{a.mailbox_id}"]
    assert (mail["kind"], mail["provider"], mail["scope"], mail["owner"]) == (
        "mailbox", "outlook", "member", a.member)
    assert (mail["status"], mail["last_error"], mail["setup_href"]) == (
        "needs_reconnect", "auth_failed", "/email")
    assert mail["label"] == a.mailbox and not mail["can_disconnect"]
    wa = by_id[f"whatsapp:{a.wa_id}"]
    assert (wa["kind"], wa["status"], wa["label"], wa["setup_href"]) == (
        "whatsapp", "connected", "Shop a", "/whatsapp")
    mcp = by_id[f"mcp:{a.mcp}"]
    assert mcp["detail"] == {
        "transport": "http-sse", "agent_scope": "*", "host": "mcp.example.com"}
    key = by_id["key:apollo"]
    assert (key["kind"], key["status"], key["scope"]) == ("api_key", "configured", "org")


# -- No credential, no message, no log -----------------------------------------


async def test_no_sentinel_reaches_a_response_or_a_log(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    import structlog
    from gateway import outbound_guard

    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    await _seed(ns, ns.org_b, "b")
    monkeypatch.setattr(outbound_guard, "_resolve", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    net = _Net().install(monkeypatch)
    user = _user(ADMIN_A, ns.org_a)
    bodies: list[str] = []
    with structlog.testing.capture_logs() as logs:
        bodies.append((await _call(ns, user, "GET", "/integrations/connections")).text)
        tested = await _call(ns, user, "POST", f"/integrations/mcp/{a.mcp}/test")
        bodies.append(tested.text)
        for conn_id in (f"mailbox:{a.mailbox_id}", f"whatsapp:{a.wa_id}"):
            bodies.append((await _call(
                ns, user, "POST", f"/integrations/connections/{conn_id}/disconnect")).text)
        for conn_id in (f"zoho:{a.zoho_id}", f"mcp:{a.mcp}", "key:apollo"):
            bodies.append((await _call(
                ns, user, "POST", f"/integrations/connections/{conn_id}/disconnect")).text)
    assert net.requests, "the test must have reached the fake transport"
    everything = "\n".join(bodies) + "\n" + repr(logs)
    for sentinel in ALL_SENTINELS:
        assert sentinel not in everything, f"{sentinel} leaked into a response or a log"
    assert S_MAIL_CRED.lower() not in everything.lower()
    # A provider's free text never reaches last_error.
    assert "AADSTS" not in everything


async def test_a_mailbox_row_has_no_message_content(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/connections")
    mail = next(r for r in res.json()["connections"] if r["id"] == f"mailbox:{a.mailbox_id}")
    blob = json.dumps(mail)
    assert S_MAIL_SUBJECT not in blob and S_MAIL_BODY not in blob
    assert set(mail["detail"]) == {"provider"}


# -- Floors ----------------------------------------------------------------------


async def test_the_floors_hold_against_the_real_router(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    member = _user("plain@in1-r8.test", ns.org_a, ["feature:integrations"])
    reader = _user("reader@in1-r8.test", ns.org_a, ["feature:integrations", "admin:members:read"])
    assert (await _call(ns, member, "GET", "/integrations/connections")).status_code == 403
    for path in (f"/integrations/connections/zoho:{a.zoho_id}/disconnect",
                 f"/integrations/mcp/{a.mcp}/test"):
        assert (await _call(ns, member, "POST", path)).status_code == 403, path
        assert (await _call(ns, reader, "POST", path)).status_code == 403, path
    rows = (await _call(ns, reader, "GET", "/integrations/connections")).json()["connections"]
    assert rows and not any(r["can_disconnect"] for r in rows), (
        "a reader without integrations:manage must not be offered a disconnect")
    assert _count(ns, "SELECT count(*) FROM integration_connections "
                      "WHERE organization_id = CAST(:o AS uuid) AND status = 'connected'",
                  o=ns.org_a) == 1


# -- Disconnect ------------------------------------------------------------------


@pytest.fixture
def audit(monkeypatch):
    events: list[Any] = []
    import acb_audit

    monkeypatch.setattr(acb_audit, "record", events.append)
    return events


async def test_zoho_disconnect_revokes_clears_the_blob_and_audits(ns, app_engine, audit) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    b = await _seed(ns, ns.org_b, "b")
    ns.zoho.requests.clear()
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "POST",
                      f"/integrations/connections/zoho:{a.zoho_id}/disconnect")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "disconnected" and res.json()["can_disconnect"] is False
    revokes = [r for r in ns.zoho.requests if r.url.path.endswith("/token/revoke")]
    assert len(revokes) == 1 and f"{S_ZOHO}-REFRESH-a".encode() in revokes[0].content
    with ns.admin_engine.connect() as c:
        rows = {str(r[0]): (r[1], r[2]) for r in c.execute(text(
            "SELECT id, status, credentials_encrypted FROM integration_connections"))}
    assert rows[a.zoho_id] == ("disconnected", None)
    assert rows[b.zoho_id][0] == "connected" and rows[b.zoho_id][1], "org B must be untouched"
    (event,) = audit
    assert event.actor == f"user:{ADMIN_A}" and event.target == f"zoho:{a.zoho_id}"
    assert event.organization_id == ns.org_a and event.action == "integration.disconnect"
    assert event.payload["organization_id"] == ns.org_a


async def test_mcp_disconnect_removes_only_the_callers_row(ns, app_engine, audit) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    b = await _seed(ns, ns.org_b, "b")
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "POST",
                      f"/integrations/connections/mcp:{a.mcp}/disconnect")
    assert (res.status_code, res.json()) == (200, {"removed": True})
    assert _count(ns, "SELECT count(*) FROM mcp_servers WHERE name = :n", n=a.mcp) == 0
    assert _count(ns, "SELECT count(*) FROM mcp_servers WHERE name = :n", n=b.mcp) == 1
    assert len(audit) == 1 and audit[0].target == f"mcp:{a.mcp}"


async def test_key_disconnect_removes_only_the_callers_service_keys(ns, app_engine, audit) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _seed(ns, ns.org_a, "a")
    await _seed(ns, ns.org_b, "b")
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "POST",
                      "/integrations/connections/key:apollo/disconnect")
    assert (res.status_code, res.json()) == (200, {"removed": True}), res.text
    sql = "SELECT count(*) FROM provider_keys WHERE organization_id = CAST(:o AS uuid)"
    assert _count(ns, sql, o=ns.org_a) == 0
    assert _count(ns, sql, o=ns.org_b) == 1
    assert len(audit) == 1 and audit[0].target == "key:apollo"
    again = await _call(ns, _user(ADMIN_A, ns.org_a), "POST",
                        "/integrations/connections/key:apollo/disconnect")
    assert again.status_code == 404 and len(audit) == 1


async def test_a_member_connection_is_409_and_nothing_changes(ns, app_engine, audit) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    user = _user(ADMIN_A, ns.org_a)
    for conn_id, href in ((f"mailbox:{a.mailbox_id}", "/email"),
                          (f"whatsapp:{a.wa_id}", "/whatsapp")):
        res = await _call(ns, user, "POST", f"/integrations/connections/{conn_id}/disconnect")
        assert res.status_code == 409, res.text
        assert res.json()["detail"] == {"code": "member_connection", "setup_href": href}
    assert _count(ns, "SELECT count(*) FROM email_accounts WHERE id = CAST(:i AS uuid)",
                  i=a.mailbox_id) == 1
    assert audit == []


async def test_an_unknown_id_or_another_orgs_id_is_404(ns, app_engine, audit) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _seed(ns, ns.org_a, "a")
    b = await _seed(ns, ns.org_b, "b")
    user = _user(ADMIN_A, ns.org_a)
    for conn_id in (
        f"zoho:{b.zoho_id}", f"mailbox:{b.mailbox_id}", f"whatsapp:{b.wa_id}",
        f"mcp:{b.mcp}", "mcp:no-such-server", f"zoho:{uuid.uuid4()}", "zoho:not-a-uuid",
        "nonsense", "key:no-such-service",
    ):
        res = await _call(ns, user, "POST", f"/integrations/connections/{conn_id}/disconnect")
        assert res.status_code == 404, (conn_id, res.status_code, res.text)
    assert _count(ns, "SELECT count(*) FROM mcp_servers WHERE name = :n", n=b.mcp) == 1
    assert _count(ns, "SELECT count(*) FROM integration_connections "
                      "WHERE status = 'connected'") == 2
    assert audit == []
    # A prefix that does not match the row's provider is 404 too.
    wrong = await _call(ns, user, "POST", "/integrations/connections/github:"
                        f"{(await _zoho_of(ns, ns.org_a))}/disconnect")
    assert wrong.status_code == 404


async def _zoho_of(p: Any, org: str) -> str:
    with p.admin_engine.connect() as c:
        return str(c.execute(text(
            "SELECT id FROM integration_connections "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).scalar_one())


# -- The MCP test ------------------------------------------------------------------


class _Net:
    """Records each request that reaches a transport."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Net:
        real = httpx.AsyncClient

        def _handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, stream=httpx.ByteStream(b"{}"))

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            if kwargs.get("base_url") == "http://in1.test":
                return real(*args, **kwargs)  # the test's own client to the app
            kwargs.pop("transport", None)
            return real(*args, transport=httpx.MockTransport(_handle), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        return self


async def test_the_mcp_test_sends_the_stored_header(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    from gateway import outbound_guard

    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a")
    monkeypatch.setattr(outbound_guard, "_resolve", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    net = _Net().install(monkeypatch)
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "POST", f"/integrations/mcp/{a.mcp}/test")
    assert (res.status_code, res.json()) == (200, {"ok": True, "tools": None, "error": None})
    (sent,) = net.requests
    assert sent.headers["authorization"] == f"{S_HEADER}-a", "the STORED header must be sent"
    assert S_HEADER not in res.text
    # Org B's server is not reachable by name from org A.
    b = await _seed(ns, ns.org_b, "b")
    other = await _call(ns, _user(ADMIN_A, ns.org_a), "POST", f"/integrations/mcp/{b.mcp}/test")
    assert other.status_code == 404 and len(net.requests) == 1


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://169.254.169.254/", "http://10.0.0.1/", "http://[::1]/",
])
async def test_the_mcp_test_refuses_a_guard_refused_url_and_sends_nothing(
    url, ns, app_engine, monkeypatch,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    a = await _seed(ns, ns.org_a, "a", url=url)
    net = _Net().install(monkeypatch)
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "POST", f"/integrations/mcp/{a.mcp}/test")
    assert res.status_code == 200 and res.json()["ok"] is False
    assert res.json()["error"] == "url_refused" and res.json()["tools"] is None
    assert net.requests == []
    assert S_HEADER not in res.text
