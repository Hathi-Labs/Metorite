"""WS-47 WAC-1 — the link code routes, without a database.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.2 and §9.

The R8 half (a real catalog, FORCE RLS, the unique indexes) is
``test_wac_link_table.py``. This file holds what needs no database:

* ``wac-code-is-a-hash``: the POST hands the database the SHA-256 of the
  code, and never the plain code.
* ``wac-dark-writes-nothing``: with the switch off, an org not on the list,
  no display number or no ``feature:chat``, the route refuses before it opens
  a session.
* ``wac-org-from-the-tenant``: the route reads the org from
  ``current_tenant()``, and a body or query naming another org changes nothing.
* ``wac-router-mounted``: the gateway app serves both routes. ``main.py``
  swallows a failed import, so a broken module would otherwise ship as a 404.
"""
from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.whatsapp_channel import flags, link

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
NUMBER = "919800000000"
MEMBER = "Alice@Fracktal.in"


class _Result:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1
        return self._rows[0]


class _FakeDb:
    """Records each statement. Answers the INSERT and the list read."""

    def __init__(self, links: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.opened = 0
        self.links = links or []

    def factory(self):
        @asynccontextmanager
        async def _session(organization_id: str | None = None):
            assert organization_id is None, "the route must bind from context"
            self.opened += 1
            yield self

        return _session

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        if sql.lstrip().startswith("INSERT INTO whatsapp_member_links"):
            return _Result([{
                "id": "33333333-3333-4333-8333-333333333333",
                "code_expires_at": datetime.now(UTC) + timedelta(minutes=15),
            }])
        if "FROM whatsapp_member_links l" in sql:
            return _Result(self.links)
        if sql.lstrip().startswith("UPDATE whatsapp_member_links"):
            return _Result([], rowcount=1)
        return _Result([])


@pytest.fixture()
def channel(monkeypatch: pytest.MonkeyPatch):
    """The channel open for ORG_A, with a display number, and a fake DB."""
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_assistant_enabled", True, raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_orgs", f" {ORG_A.upper()} ,x",
                        raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_display_number", NUMBER,
                        raising=False)
    db = _FakeDb()
    monkeypatch.setattr(link, "tenant_session", db.factory())
    monkeypatch.setattr(link, "current_tenant", lambda: ORG_A)
    # The real `_audit` runs. Only the best-effort writer is captured.
    import acb_audit

    db.audits = []
    monkeypatch.setattr(acb_audit, "record", db.audits.append)
    return db


def _client(*, email: str | None = MEMBER, features=("feature:chat",)) -> TestClient:
    app = FastAPI()
    app.include_router(link.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(features)),
    )
    return TestClient(app)


def _insert(db: _FakeDb) -> dict[str, Any]:
    rows = [p for sql, p in db.calls if "INSERT INTO" in sql]
    assert len(rows) == 1, db.calls
    return rows[0]


# ── The code ────────────────────────────────────────────────────────────────

def test_the_alphabet_has_32_unambiguous_symbols() -> None:
    assert len(link.CODE_ALPHABET) == 32
    assert len(set(link.CODE_ALPHABET)) == 32
    assert not set("ILOU") & set(link.CODE_ALPHABET)


def test_a_code_is_ten_symbols_and_codes_differ() -> None:
    codes = {link.new_code() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert len(code) == 10 and set(code) <= set(link.CODE_ALPHABET)


def test_the_hash_is_sha256_hex() -> None:
    assert link.code_hash("ABCDE12345") == hashlib.sha256(b"ABCDE12345").hexdigest()


# ── wac-code-is-a-hash ──────────────────────────────────────────────────────

def test_the_post_stores_the_hash_and_never_the_code(channel: _FakeDb) -> None:
    res = _client().post("/me/whatsapp-link/code")

    assert res.status_code == 201, res.text
    body = res.json()
    code = body["code"]
    params = _insert(channel)
    assert params["hash"] == hashlib.sha256(code.encode()).hexdigest()
    for sql, p in channel.calls:
        assert code not in sql, "the plain code reached a SQL text"
        assert code not in repr(p), "the plain code reached a bound value"
    assert body["link"] == f"https://wa.me/{NUMBER}?text=Link%20me%3A%20{code}"
    assert body["display_number"] == NUMBER
    assert body["code_ttl_minutes"] == 15


def test_the_answer_with_the_code_is_never_cached(channel: _FakeDb) -> None:
    # Review finding 2026-10-09: the answer carries the plain code.
    res = _client().post("/me/whatsapp-link/code")
    assert res.status_code == 201, res.text
    assert res.headers.get("cache-control") == "no-store"


def test_the_issue_is_audited_without_the_code(channel: _FakeDb) -> None:
    code = _client().post("/me/whatsapp-link/code").json()["code"]

    (event,) = channel.audits
    assert event.actor == "user:alice@fracktal.in"
    assert event.action == "whatsapp_link.code_issued"
    assert event.organization_id == ORG_A
    blob = repr(event)  # a dataclass repr names every field
    assert "revoked_pending" in blob
    assert code not in blob
    assert hashlib.sha256(code.encode()).hexdigest() not in blob


def test_the_post_revokes_the_earlier_pending_code_first(channel: _FakeDb) -> None:
    _client().post("/me/whatsapp-link/code")

    kinds = [sql.lstrip().split()[0] for sql, _ in channel.calls]
    assert kinds == ["SELECT", "UPDATE", "INSERT"], kinds
    lock, revoke, _ = channel.calls
    assert "pg_advisory_xact_lock" in lock[0]
    assert "status = 'pending'" in revoke[0] and "'revoked'" in revoke[0]
    assert revoke[1] == {"org": ORG_A, "email": "alice@fracktal.in"}


def test_the_member_is_the_session_address_in_lower_case(channel: _FakeDb) -> None:
    _client().post("/me/whatsapp-link/code")
    assert _insert(channel)["email"] == "alice@fracktal.in"


# ── wac-org-from-the-tenant ─────────────────────────────────────────────────

def test_a_body_or_query_naming_another_org_changes_nothing(channel: _FakeDb) -> None:
    res = _client().post(
        f"/me/whatsapp-link/code?organization_id={ORG_B}",
        json={"organization_id": ORG_B, "member_email": "eve@x.test"},
    )
    assert res.status_code == 201
    params = _insert(channel)
    assert params["org"] == ORG_A and params["email"] == "alice@fracktal.in"


# ── wac-dark-writes-nothing ─────────────────────────────────────────────────

def test_the_switch_off_refuses_and_opens_no_session(channel, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_enabled", False,
                        raising=False)
    client = _client()
    for res in (client.post("/me/whatsapp-link/code"),
                client.get("/me/whatsapp-link")):
        assert res.status_code == 404
        assert res.json()["detail"] == link.CLOSED_DETAIL
    assert channel.opened == 0 and channel.calls == []


def test_an_org_not_on_the_list_refuses_and_opens_no_session(
    channel, monkeypatch,
) -> None:
    monkeypatch.setattr(link, "current_tenant", lambda: ORG_B)
    client = _client()
    assert client.post("/me/whatsapp-link/code").status_code == 404
    assert client.get("/me/whatsapp-link").status_code == 404
    assert channel.opened == 0


def test_no_bound_org_refuses_and_opens_no_session(channel, monkeypatch) -> None:
    monkeypatch.setattr(link, "current_tenant", lambda: None)
    assert _client().post("/me/whatsapp-link/code").status_code == 404
    assert channel.opened == 0


def test_an_empty_org_list_allows_no_org(channel, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_orgs", "",
                        raising=False)
    assert _client().post("/me/whatsapp-link/code").status_code == 404
    assert channel.opened == 0


def test_no_display_number_refuses_the_post_and_reads_disabled(
    channel, monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_display_number", "",
                        raising=False)
    client = _client()
    res = client.post("/me/whatsapp-link/code")
    assert res.status_code == 503
    assert channel.calls == []
    state = client.get("/me/whatsapp-link")
    assert state.status_code == 200
    assert state.json()["enabled"] is False


@pytest.mark.parametrize("value", ["+919800000000", "91 98000 00000", "1234567",
                                   "1234567890123456", "abc"])
def test_a_malformed_display_number_reads_as_unset(monkeypatch, value) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_display_number",
                        value, raising=False)
    assert flags.display_number() is None


def test_no_chat_feature_is_refused_and_opens_no_session(channel: _FakeDb) -> None:
    client = _client(features=("feature:email",))
    assert client.post("/me/whatsapp-link/code").status_code == 403
    assert client.get("/me/whatsapp-link").status_code == 403
    assert channel.opened == 0


def test_a_principal_with_no_address_is_refused(channel: _FakeDb) -> None:
    client = _client(email="system:internal", features=("*",))
    assert client.post("/me/whatsapp-link/code").status_code == 401
    assert channel.opened == 0


# ── The GET ─────────────────────────────────────────────────────────────────

def test_the_get_reads_only_the_session_member_in_the_bound_org(
    channel: _FakeDb,
) -> None:
    channel.links = [{
        "id": "11111111-1111-4111-8111-111111111111",
        "organization_id": ORG_A, "organization_name": "Fracktal",
        "status": "active", "linked_at": datetime(2026, 10, 9, tzinfo=UTC),
        "is_current": True, "code_expires_at": None, "wa_id": "919990000001",
    }]
    res = _client().get("/me/whatsapp-link")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is True and body["display_number"] == NUMBER
    assert body["links"] == [{
        "id": "11111111-1111-4111-8111-111111111111",
        "organization_id": ORG_A, "organization_name": "Fracktal",
        "status": "active", "linked_at": "2026-10-09T00:00:00+00:00",
        "is_current": True, "expires_at": None, "phone_hint": "0001",
    }]
    (sql, params), = channel.calls
    assert params == {"org": ORG_A, "email": "alice@fracktal.in"}
    assert "l.member_email = :email" in sql


# ── wac-router-mounted ──────────────────────────────────────────────────────

def test_the_gateway_app_serves_both_routes() -> None:
    from gateway.main import app

    from tests.unit._routes import served_routes

    served = {(m, r.path) for r in served_routes(app.routes)
              for m in (r.methods or ())}
    assert ("GET", "/me/whatsapp-link") in served
    assert ("POST", "/me/whatsapp-link/code") in served


def test_the_module_reads_no_setting_itself() -> None:
    """``flags`` is the one reader of the three settings."""
    from pathlib import Path

    src = Path(link.__file__).read_text(encoding="utf-8")
    assert not re.search(r"whatsapp_assistant_(enabled|orgs|display_number)", src)
