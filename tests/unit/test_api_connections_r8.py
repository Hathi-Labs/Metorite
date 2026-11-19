"""R8: the API-3 call routes against a real Postgres (task 3789, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.3, §2.3a, §2.8 and §5.
The routes are ``gateway/routes/api_connections.py``. The rows sit in
``integration_connections`` under FORCE RLS, and the suite connects as the
NON-privileged role of the phase-4 rehearsal (no superuser, no BYPASSRLS). The
store, the tenant session and ``shared_token.fresh_credential`` are real. Only
the wire (``outbound_guard.send`` and the DNS lookup) and Redis are fakes. The
tokens are sentinels, and no request leaves the box.

What it proves:

- a member of org A reads with org A's row and token, and a member of org B
  with org B's. A name that only org B holds is "Not Found" for org A;
- the org comes from the session only: a body key or a header that names
  another org changes nothing, and a caller with no org is "Not Found";
- a grant-refused name answers the same 404 as an unknown name, and both
  read the row;
- ``read`` runs class A only, and ``write`` refuses every call;
- the flag off is 404 for each route, before any read of the row;
- no answer, error text or log line holds a credential, also when the API
  echoes it, when the API fails with it, and when the transport fails.

Mutation table. Each row names a mutation and the test that it breaks.

| # | Mutation | Test that fails |
|---|---|---|
| R1 | ``_resolve`` reads the row in a session of another org | ``test_org_a_cannot_call_a_connection_of_org_b`` |
| R2 | ``_granted`` skips the manage check of ``grant = admin`` | ``test_a_refused_grant_is_the_same_404_as_an_unknown_name`` |
| R3 | ``read`` drops the class check | ``test_read_runs_class_a_only_and_write_refuses_every_call`` |
| R4 | ``write`` sends the request of a class B op | ``test_read_runs_class_a_only_and_write_refuses_every_call`` |
| R5 | the router drops the flag dependency | ``test_the_flag_off_is_404_for_each_route`` |
| R6 | ``_fetch`` drops ``call.clean`` | ``test_an_echoed_credential_never_reaches_the_answer_or_the_log`` |
| R7 | a transport fault passes ``str(exc)`` | ``test_a_failed_call_never_shows_the_credential`` |
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.api_connections", reason="gateway not installed")

import structlog
from sqlalchemy import text

from tests.unit import _api_call_fakes as f
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set.
pytestmark = _DB_GATE

READ = "/connections/api/zoho_books/read"
WRITE = "/connections/api/zoho_books/write"
FIND = "/connections/api/zoho_books/ops"
DESCRIBE = "/connections/api/zoho_books/ops/list_invoices"


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _assert_non_priv(app_eng: Any) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1]


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog, the flag on, the master key set, a fake wire and
    a fake Redis. The rows go after each test."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "acb_master_key", "api3-r8-master-key-for-the-blob",
                        raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    f.flag(monkeypatch)
    f.use_redis(monkeypatch, f.FakeRedis())
    promoted.wire = f.use_wire(monkeypatch, f.Wire())
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            c.execute(text(
                "DELETE FROM integration_connections "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


async def _seed(ns: Any, org: str, cred: Any) -> None:
    """One ``api:zoho_books`` row, written by the real store in the org's session."""
    from acb_common.db import tenant_session
    from gateway.connections import store

    async with tenant_engine_scope(_app_dsn(ns)), tenant_session(org) as db:
        await store.save_credential(db, org, "api:zoho_books", "integrations", cred,
                                    connected_by=f.ADMIN, scopes=cred.scopes)


async def _call(ns: Any, who: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    async with tenant_engine_scope(_app_dsn(ns)):
        return await f.call(who, method, path, **kw)


def _sent_org(sent: Any) -> list[str]:
    return [v for k, v in parse_qsl(urlsplit(sent.url).query) if k == "organization_id"]


# ── Two orgs ─────────────────────────────────────────────────────────────────


async def test_each_org_reads_with_its_own_row_and_token(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    await _seed(ns, ns.org_a, f.credential())
    await _seed(ns, ns.org_b, f.credential(f.S_ACCESS_B, f.S_REFRESH_B, books_org=f.BOOKS_ORG_B))
    a = await _call(ns, f.user(org=ns.org_a), "POST", READ, body=f.read())
    b = await _call(ns, f.user("admin-b@api3.test", org=ns.org_b), "POST", READ, body=f.read())
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    sent_a, sent_b = ns.wire.sent
    assert _sent_org(sent_a) == [f.BOOKS_ORG] and _sent_org(sent_b) == [f.BOOKS_ORG_B]
    assert sent_a.headers["Authorization"].endswith(f.S_ACCESS)
    assert sent_b.headers["Authorization"].endswith(f.S_ACCESS_B)


async def test_org_a_cannot_call_a_connection_of_org_b(ns) -> None:
    await _seed(ns, ns.org_b, f.credential(f.S_ACCESS_B, f.S_REFRESH_B, books_org=f.BOOKS_ORG_B))
    for method, path, body in (("POST", READ, f.read()), ("POST", WRITE, f.read()),
                               ("GET", FIND, None), ("GET", DESCRIBE, None)):
        r = await _call(ns, f.user(org=ns.org_a), method, path, body=body)
        assert (r.status_code, r.json()) == (404, {"detail": "Not Found"}), (path, r.text)
    assert ns.wire.sent == []


async def test_the_org_comes_from_the_session_only(ns) -> None:
    await _seed(ns, ns.org_a, f.credential())
    await _seed(ns, ns.org_b, f.credential(f.S_ACCESS_B, f.S_REFRESH_B, books_org=f.BOOKS_ORG_B))
    # A body key that names org B is refused, and a header changes nothing.
    r = await _call(ns, f.user(org=ns.org_a), "POST", READ,
                    body={**f.read(), "organization_id": ns.org_b})
    assert r.status_code == 422
    r = await _call(ns, f.user(org=ns.org_a), "POST", READ, body=f.read(),
                    headers={"X-Organization-Id": ns.org_b, "X-Tenant-Id": ns.org_b})
    assert r.status_code == 200, r.text
    (sent,) = ns.wire.sent
    assert _sent_org(sent) == [f.BOOKS_ORG] and sent.headers["Authorization"].endswith(f.S_ACCESS)
    # A member of org B reaches org B's row only, never org A's.
    other = await _call(ns, f.user(f.MEMBER, org=ns.org_b, perms=f.MEMBER_PERMS), "GET", FIND)
    assert other.status_code == 404  # a member: no integrations:manage
    # A caller with no org, or no member, is the same 404.
    for who in (f.user(org=None), f.user(email="", org=ns.org_a)):
        r = await _call(ns, who, "POST", READ, body=f.read())
        assert (r.status_code, r.json()) == (404, {"detail": "Not Found"})
    assert len(ns.wire.sent) == 1


# ── The grant ────────────────────────────────────────────────────────────────


async def test_a_refused_grant_is_the_same_404_as_an_unknown_name(ns, monkeypatch) -> None:
    from gateway.connections import store

    await _seed(ns, ns.org_a, f.credential())
    reads: list[str] = []
    real_get = store.get

    async def _counted(db, org, provider, owning_app, **kw):
        reads.append(provider)
        return await real_get(db, org, provider, owning_app, **kw)

    monkeypatch.setattr(store, "get", _counted)
    refused = [
        await _call(ns, f.user(f.MEMBER, org=ns.org_a, perms=f.MEMBER_PERMS), "POST", READ,
                    body=f.read()),
        await _call(ns, f.user(org=ns.org_a, perms=["integrations:manage"]), "POST", READ,
                    body=f.read()),
        await _call(ns, f.user(org=ns.org_a, perms=["integrations:use:api-other",
                                                    "integrations:manage"]), "POST", READ,
                    body=f.read()),
    ]
    unknown = await _call(ns, f.user(org=ns.org_a), "POST",
                          "/connections/api/no_such_api/read", body=f.read())
    for r in (*refused, unknown):
        assert (r.status_code, r.text) == (404, unknown.text)
    assert reads == ["api:zoho_books"] * 3 + ["api:no_such_api"]
    assert ns.wire.sent == []
    # The two grants that open it: the named one, and the wildcard.
    for perms in (["integrations:use:api-zoho_books", "integrations:manage"], f.ADMIN_PERMS):
        ok = await _call(ns, f.user(org=ns.org_a, perms=perms), "POST", READ, body=f.read())
        assert ok.status_code == 200, ok.text


# ── The class and the write switch ───────────────────────────────────────────


async def test_read_runs_class_a_only_and_write_refuses_every_call(ns, monkeypatch) -> None:
    from gateway.connections import api as api_templates
    from gateway.connections.api.template import Operation, validate
    from gateway.connections.api.zoho_books import ZOHO_BOOKS

    writer = Operation(name="create_invoice", method="POST", path="/books/v3/invoices",
                       cls="B", title="Make one invoice.", always_confirm=True)
    variant = f.with_op(ZOHO_BOOKS, writer)
    validate(variant, api_templates.CLIENT_HOSTS)
    monkeypatch.setattr(api_templates, "template",
                        lambda name: variant if name == "zoho_books" else None)
    await _seed(ns, ns.org_a, f.credential(template=variant))
    admin = f.user(org=ns.org_a, perms=["*"])

    r = await _call(ns, admin, "POST", READ, body=f.read("create_invoice"))
    assert (r.status_code, r.json()["detail"]["code"]) == (403, "wrong_class")
    for op in ("create_invoice", "list_invoices", "no_such_op"):
        r = await _call(ns, admin, "POST", WRITE, body=f.read(op))
        assert r.status_code in (403, 404), r.text
    assert (await _call(ns, admin, "POST", WRITE, body=f.read("create_invoice"))).json() == {
        "detail": {"code": "writes_off"}}
    found = await _call(ns, admin, "GET", FIND)
    assert [o["op"] for o in found.json()["operations"]] == ["get_invoice", "list_invoices"]
    hidden = await _call(ns, admin, "GET", "/connections/api/zoho_books/ops/create_invoice")
    assert hidden.status_code == 404
    assert ns.wire.sent == []
    ok = await _call(ns, admin, "POST", READ, body=f.read())
    assert ok.status_code == 200 and len(ns.wire.sent) == 1


async def test_a_changed_template_asks_the_admin_again(ns) -> None:
    from gateway.connections.api.template import Operation
    from gateway.connections.api.zoho_books import ZOHO_BOOKS

    older = f.with_op(ZOHO_BOOKS, Operation(
        name="list_bills", method="GET", path="/books/v3/bills", cls="A", title="List the bills."))
    await _seed(ns, ns.org_a, f.credential(template=older))
    r = await _call(ns, f.user(org=ns.org_a), "POST", READ, body=f.read())
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "operations_changed")
    assert ns.wire.sent == []


async def test_the_flag_off_is_404_for_each_route(ns, monkeypatch) -> None:
    from gateway.connections import store

    await _seed(ns, ns.org_a, f.credential())
    f.flag(monkeypatch, "")
    monkeypatch.setattr(store, "get", lambda *a, **k: pytest.fail("the row is never read"))
    for method, path, body in (("POST", READ, f.read()), ("POST", WRITE, f.read()),
                               ("GET", FIND, None), ("GET", DESCRIBE, None)):
        r = await _call(ns, f.user(org=ns.org_a, perms=["*"]), method, path, body=body)
        assert (r.status_code, r.json()) == (404, {"detail": "Not Found"}), path
    f.flag(monkeypatch, ns.org_b)
    r = await _call(ns, f.user(org=ns.org_a, perms=["*"]), "POST", READ, body=f.read())
    assert r.status_code == 404
    assert ns.wire.sent == []


# ── The sentinel ─────────────────────────────────────────────────────────────


def _no_sentinel(*texts: str) -> None:
    for blob in texts:
        for s in f.SENTINELS:
            assert s not in blob, s
            assert quote(s, safe="") not in blob


async def test_an_echoed_credential_never_reaches_the_answer_or_the_log(
        ns, caplog: pytest.LogCaptureFixture) -> None:
    await _seed(ns, ns.org_a, f.credential())
    echo = json.dumps({"code": 0, "invoices": [{
        "total": 1, "note": f"token {f.S_ACCESS}",
        "url": f"https://x.test/?authtoken={quote(f.S_ACCESS, safe='')}",
        "header": f"Zoho-oauthtoken {f.S_ACCESS}", "hid​den": f"{f.S_REFRESH}",
        f.S_ACCESS: "a key"}]}).encode()
    from gateway import outbound_guard

    ns.wire.answer = outbound_guard.Answer(status_code=200, body=echo)
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        r = await _call(ns, f.user(org=ns.org_a), "POST", READ, body=f.read())
    assert r.status_code == 200, r.text
    _no_sentinel(r.text, json.dumps(events, default=str),
                 "\n".join(rec.getMessage() for rec in caplog.records))
    (line,) = [e for e in events if e.get("event") == "api.call"]
    assert set(line) >= {"connection", "op", "status", "ms", "bytes"}
    assert line["op"] == "list_invoices" and line["status"] == 200
    assert "args" not in line and "url" not in line and "headers" not in line
    item = r.json()["data"]["invoices"][0]
    assert item["hidden"] == "[removed]" and "[removed]" in item["note"]


@pytest.mark.parametrize("fault", ["status", "transport", "refused"])
async def test_a_failed_call_never_shows_the_credential(
        ns, fault, caplog: pytest.LogCaptureFixture) -> None:
    from gateway import outbound_guard

    await _seed(ns, ns.org_a, f.credential())
    ns.wire.answer = {
        "status": outbound_guard.Answer(status_code=401, body=json.dumps({
            "code": 57, "message": f"bad token Zoho-oauthtoken {f.S_ACCESS}"}).encode()),
        "transport": httpx.ConnectError(f"failed with {f.S_ACCESS} and {f.S_REFRESH}"),
        "refused": outbound_guard.OutboundRefused("www.zohoapis.in", f"odd {f.S_ACCESS}"),
    }[fault]
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        r = await _call(ns, f.user(org=ns.org_a), "POST", READ, body=f.read())
    assert r.status_code == 502, r.text
    _no_sentinel(r.text, json.dumps(events, default=str),
                 "\n".join(rec.getMessage() for rec in caplog.records))
    detail = r.json()["detail"]
    assert detail["code"] == {"status": "upstream_error", "transport": "transport_error",
                              "refused": "refused"}[fault]
