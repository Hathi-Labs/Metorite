"""Unit tests for the Connect wizard backend (W11) — the pure Meta-error
extractor, the verify route (mocked provider), and the connection-info route."""

from __future__ import annotations

import json
from typing import ClassVar

import gateway.routes.whatsapp.transport.connect as connect
import pytest
from gateway.routes.whatsapp.transport.connect import friendly_meta_error

# ── pure error extractor ──────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body, status=400):
        self._body = body
        self.status_code = status

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _HttpErr(Exception):
    def __init__(self, body, status=400):
        self.response = _Resp(body, status)
        super().__init__("http error")


def test_extracts_meta_error_message() -> None:
    exc = _HttpErr({"error": {"message": "Invalid OAuth access token.", "code": 190}})
    assert friendly_meta_error(exc) == "Invalid OAuth access token. (Meta code 190)"


def test_extracts_message_without_code() -> None:
    exc = _HttpErr({"error": {"message": "Unsupported request."}})
    assert friendly_meta_error(exc) == "Unsupported request."


def test_non_json_error_falls_back_to_status() -> None:
    exc = _HttpErr(ValueError("not json"), status=401)
    assert friendly_meta_error(exc) == "Meta returned HTTP 401."


def test_no_response_falls_back_to_str() -> None:
    assert "boom" in friendly_meta_error(RuntimeError("boom"))


# ── verify route ──────────────────────────────────────────────────────────────

class _FakeProvider:
    def __init__(self, profile=None, raise_exc=None):
        self._profile = profile or {}
        self._raise = raise_exc

    async def get_phone_number_profile(self):
        if self._raise:
            raise self._raise
        return self._profile


async def test_verify_success_returns_profile(monkeypatch) -> None:
    prof = {"display_phone_number": "+91 98765 43210",
            "verified_name": "Fracktal Works", "quality_rating": "GREEN"}
    monkeypatch.setattr(connect, "_instantiate_provider",
                        lambda name, creds: _FakeProvider(profile=prof))
    out = await connect.verify_account(
        connect.VerifyRequest(phone_number_id="123", access_token="tok"),
        user=None)
    assert out.ok is True
    assert out.verified_name == "Fracktal Works"
    assert out.quality_rating == "GREEN"


async def test_verify_surfaces_meta_error(monkeypatch) -> None:
    monkeypatch.setattr(
        connect, "_instantiate_provider",
        lambda name, creds: _FakeProvider(
            raise_exc=_HttpErr({"error": {"message": "Bad token", "code": 190}})))
    out = await connect.verify_account(
        connect.VerifyRequest(phone_number_id="123", access_token="bad"),
        user=None)
    assert out.ok is False
    assert "Bad token" in out.error


async def test_verify_requires_both_fields() -> None:
    out = await connect.verify_account(
        connect.VerifyRequest(phone_number_id="  ", access_token="tok"), user=None)
    assert out.ok is False
    assert "required" in out.error


async def test_connection_info_generates_token_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("WHATSAPP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
    info = await connection_info_call()
    assert info.base_configured is False
    assert info.webhook_url == ""
    assert info.verify_token.startswith("cc-")


async def test_connection_info_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_PUBLIC_URL", "https://api.example.com/")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "my-token")
    info = await connection_info_call()
    assert info.base_configured is True
    assert info.webhook_url == "https://api.example.com/whatsapp/webhook"
    assert info.verify_token == "my-token"


async def connection_info_call():
    return await connect.connection_info(user=None)


def test_connect_routes_registered() -> None:
    from gateway.routes.whatsapp import router
    paths = {r.path for r in router.routes}
    assert "/whatsapp/accounts/verify" in paths
    assert "/whatsapp/connection/info" in paths
    assert "/whatsapp/connect/embedded" in paths


# ── Embedded Signup (W12) ─────────────────────────────────────────────────────

async def test_connection_info_flags_embedded_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("WHATSAPP_APP_ID", "123456")
    monkeypatch.setenv("WHATSAPP_ES_CONFIG_ID", "cfg-789")
    info = await connect.connection_info(user=None)
    assert info.embedded_signup is True
    assert info.fb_app_id == "123456"
    assert info.es_config_id == "cfg-789"


async def test_connection_info_no_embedded_without_config(monkeypatch) -> None:
    monkeypatch.delenv("WHATSAPP_APP_ID", raising=False)
    monkeypatch.delenv("WHATSAPP_ES_CONFIG_ID", raising=False)
    info = await connect.connection_info(user=None)
    assert info.embedded_signup is False


async def test_embedded_signup_400_when_unconfigured(monkeypatch) -> None:
    from fastapi import HTTPException
    monkeypatch.delenv("WHATSAPP_APP_ID", raising=False)
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    try:
        await connect.embedded_signup(
            connect.EmbeddedSignupRequest(code="c", waba_id="1"),
            user=None)
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Embedded Signup" in exc.detail


#: Meta's example ids from the Graph reference for a WABA's phone numbers.
_ES_WABA = "102290129340398"
_ES_PNID = "1906385232743451"
_ES_PNID_2 = "1913623884432103"


class _FakeDB:
    """The H2 seam's shape: the wrapper (not the handler) commits on exit."""

    def __init__(self):
        self.committed = 0

    async def commit(self):
        self.committed += 1


async def test_embedded_signup_happy_path(monkeypatch) -> None:
    """A plain FINISH: the browser sends the number, and the backend still
    finds it in the WABA list before Meta confirms it (WA-C2 P1, P2)."""
    from types import SimpleNamespace

    monkeypatch.setenv("WHATSAPP_APP_ID", "app")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")
    monkeypatch.delenv("WHATSAPP_GRAPH_VERSION", raising=False)

    async def _exchange(code, app_id, app_secret, gv):
        return "TOKEN"

    subscribed_calls: list[str] = []

    async def _subscribe(waba_id, token, gv):
        subscribed_calls.append(waba_id)

    async def _list(waba_id, token, gv):
        return [{"id": _ES_PNID, "display_phone_number": "+91 98765 43210",
                 "verified_name": "Fracktal Works"}]

    from contextlib import asynccontextmanager

    db = _FakeDB()

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        # Mirrors `_projects_fakes.bind_db`: commit on clean exit, so the
        # one-transaction contract stays observable.
        yield db
        await db.commit()

    persisted: dict = {}

    async def _persist(db, **kw):
        persisted.update(kw)
        return "ROW"

    # A node read on Graph always returns the node's `id`. The check of
    # `verify_cloud_number` refuses a profile without it.
    profile = {"id": _ES_PNID, "display_phone_number": "+91 98765 43210",
               "verified_name": "Fracktal Works"}
    meta_calls: list[dict] = []

    def _provider(name, creds):
        meta_calls.append(dict(creds))
        return _FakeProvider(profile=profile)

    monkeypatch.setattr(connect, "exchange_code_for_token", _exchange)
    monkeypatch.setattr(connect, "subscribe_app_to_waba", _subscribe)
    monkeypatch.setattr(connect, "list_waba_phone_numbers", _list)
    monkeypatch.setattr(connect, "_tenant_session", _tenant_session)
    # The number is checked once, through the one check of the manual route.
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts._instantiate_provider",
        _provider)
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts.persist_account", _persist)
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts._account_model",
        lambda row: SimpleNamespace(
            id="acc-1", display_name="Fracktal Works",
            phone_number="+91 98765 43210"))

    out = await connect.embedded_signup(
        connect.EmbeddedSignupRequest(
            code="auth-code", phone_number_id=_ES_PNID, waba_id=_ES_WABA),
        user=SimpleNamespace(email="u@x"))
    assert out.account_id == "acc-1"
    assert out.subscribed is True
    assert subscribed_calls == [_ES_WABA]
    # One transaction, committed by the tenant-session wrapper on clean exit.
    assert db.committed == 1
    # Meta checked the exchanged token for this number once, at the server's
    # version, and the profile it returned is the proof that persist_account
    # requires (WA-C1 P1).
    assert meta_calls == [{"access_token": "TOKEN", "phone_number_id": _ES_PNID,
                           "graph_version": "v21.0"}]
    assert persisted["verified_profile"] is profile
    assert persisted["phone_number_id"] == _ES_PNID
    assert persisted["sync_status"] == "live"
    assert persisted["credentials"]["onboarding"] == "cloud"


# ── WA-C2: coexistence in Embedded Signup (spec §12.4) ────────────────────────
#
# The fixtures below quote the shapes in Meta's Graph reference. They stand in
# for `httpx.AsyncClient`, so the REAL exchange, list, check and subscribe run.

#: `GET /oauth/access_token` (Embedded Signup, "exchange the token code").
_META_TOKEN_EXCHANGE = {"access_token": "BUSINESS-TOKEN", "token_type": "bearer"}


def _meta_number(pnid: str, name: str = "Jasper's Market") -> dict:
    """One row of `GET /<WABA_ID>/phone_numbers` (Graph reference)."""
    return {"verified_name": name, "display_phone_number": "+1 631-555-5555",
            "id": pnid, "quality_rating": "GREEN", "platform_type": "CLOUD_API"}


class _GraphError(Exception):
    def __init__(self, resp):
        self.response = resp
        super().__init__(f"Meta returned HTTP {resp.status_code}")


class _GraphResp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _GraphError(self)


class _MetaGraph:
    """A fake Graph API. It answers by path and records every request."""

    def __init__(self, numbers, subscribe_error=None):
        self.numbers = numbers
        self.subscribe_error = subscribe_error
        self.calls: list[tuple[str, str, dict]] = []

    def client(self):
        graph = self

        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, headers=None, params=None):
                return graph.answer("GET", url, params)

            async def post(self, url, headers=None, params=None, json=None):
                return graph.answer("POST", url, params)

        return _Client

    def answer(self, method, url, params):
        import httpx

        path = httpx.URL(url).path
        self.calls.append((method, path, dict(params or {})))
        if path.endswith("/oauth/access_token"):
            return _GraphResp(_META_TOKEN_EXCHANGE)
        if path.endswith("/phone_numbers"):
            return _GraphResp({"data": self.numbers, "paging": {
                "cursors": {"before": "QVFIUk5", "after": "QVFIUmF"}}})
        if path.endswith("/subscribed_apps"):
            if self.subscribe_error is not None:
                return _GraphResp(self.subscribe_error, 400)
            return _GraphResp({"success": True})
        node = path.rsplit("/", 1)[-1]
        for n in self.numbers:
            if n["id"] == node:
                return _GraphResp({**n, "code_verification_status": "VERIFIED"})
        return _GraphResp({"error": {"message": "Unsupported get request.",
                                     "code": 100}}, 400)


def _embedded_route(monkeypatch, graph: _MetaGraph):
    """Patch only the edges of the Embedded Signup route: Meta, the session
    and the insert. Returns what `persist_account` received."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import httpx

    monkeypatch.setenv("WHATSAPP_APP_ID", "app")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")
    monkeypatch.delenv("WHATSAPP_GRAPH_VERSION", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", graph.client())

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        yield _FakeDB()

    persisted: list[dict] = []

    async def _persist(db, **kw):
        persisted.append(kw)
        return "ROW"

    monkeypatch.setattr(connect, "_tenant_session", _tenant_session)
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts.persist_account", _persist)
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts._account_model",
        lambda row: SimpleNamespace(id="acc-1", display_name="Jasper's Market",
                                    phone_number="+1 631-555-5555"))
    return persisted


async def _connect(**body):
    from types import SimpleNamespace

    return await connect.embedded_signup(
        connect.EmbeddedSignupRequest(code="auth-code", **body),
        user=SimpleNamespace(email="u@x"))


async def test_coexistence_with_only_a_waba_id_lists_checks_subscribes_and_goes_live(
    monkeypatch,
) -> None:
    """FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING returns `waba_id` and no
    `phone_number_id`. The backend finds the number, and Meta confirms it."""
    graph = _MetaGraph([_meta_number(_ES_PNID)])
    persisted = _embedded_route(monkeypatch, graph)

    out = await _connect(waba_id=_ES_WABA, onboarding="coexistence")

    assert out.subscribed is True
    assert [(m, p) for m, p, _ in graph.calls] == [
        ("GET", "/v21.0/oauth/access_token"),
        ("GET", f"/v21.0/{_ES_WABA}/phone_numbers"),
        ("GET", f"/v21.0/{_ES_PNID}"),
        ("POST", f"/v21.0/{_ES_WABA}/subscribed_apps"),
    ]
    assert graph.calls[1][2] == {
        "fields": "id,display_phone_number,verified_name,quality_rating,platform_type"}
    # P4: the number is on the phone app, so it is registered already.
    assert not [p for _, p, _ in graph.calls if p.endswith("/register")]
    [row] = persisted
    assert row["phone_number_id"] == _ES_PNID
    assert row["waba_id"] == _ES_WABA
    assert row["sync_status"] == "live"
    assert row["credentials"] == {"access_token": "BUSINESS-TOKEN",
                                  "waba_id": _ES_WABA, "onboarding": "coexistence"}
    assert row["verified_profile"]["id"] == _ES_PNID
    assert row["phone_number"] == "+1 631-555-5555"


async def test_a_subscribe_failure_answers_400_and_persists_nothing(monkeypatch) -> None:
    from fastapi import HTTPException

    graph = _MetaGraph(
        [_meta_number(_ES_PNID)],
        subscribe_error={"error": {"message": "Permissions error", "code": 200}})
    persisted = _embedded_route(monkeypatch, graph)

    with pytest.raises(HTTPException) as exc:
        await _connect(waba_id=_ES_WABA, onboarding="coexistence")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Permissions error (Meta code 200)"
    assert persisted == [], "a failed subscribe saved an account"


async def test_a_waba_with_no_number_answers_400(monkeypatch) -> None:
    from fastapi import HTTPException

    graph = _MetaGraph([])
    persisted = _embedded_route(monkeypatch, graph)
    with pytest.raises(HTTPException) as exc:
        await _connect(waba_id=_ES_WABA, onboarding="coexistence")
    assert exc.value.status_code == 400
    assert "no phone number" in exc.value.detail
    assert persisted == []
    assert not [p for _, p, _ in graph.calls if p.endswith("/subscribed_apps")]


@pytest.mark.parametrize("sent", [None, "1111111111"])
async def test_two_numbers_and_no_matching_id_answers_400(monkeypatch, sent) -> None:
    from fastapi import HTTPException

    graph = _MetaGraph([_meta_number(_ES_PNID), _meta_number(_ES_PNID_2, "B")])
    persisted = _embedded_route(monkeypatch, graph)
    with pytest.raises(HTTPException) as exc:
        await _connect(waba_id=_ES_WABA, phone_number_id=sent,
                       onboarding="coexistence")
    assert exc.value.status_code == 400
    assert "more than one" in exc.value.detail or "not in" in exc.value.detail
    assert persisted == []


async def test_two_numbers_use_the_id_the_browser_sent_when_it_is_listed(
    monkeypatch,
) -> None:
    graph = _MetaGraph([_meta_number(_ES_PNID), _meta_number(_ES_PNID_2, "B")])
    persisted = _embedded_route(monkeypatch, graph)
    await _connect(waba_id=_ES_WABA, phone_number_id=_ES_PNID_2)
    assert persisted[0]["phone_number_id"] == _ES_PNID_2
    assert persisted[0]["credentials"]["onboarding"] == "cloud"


async def test_a_plain_finish_for_a_number_outside_the_waba_answers_400(
    monkeypatch,
) -> None:
    """The browser's id is a claim. The WABA list is the fact."""
    from fastapi import HTTPException

    graph = _MetaGraph([_meta_number(_ES_PNID)])
    persisted = _embedded_route(monkeypatch, graph)
    with pytest.raises(HTTPException) as exc:
        await _connect(waba_id=_ES_WABA, phone_number_id="1111111111")
    assert exc.value.status_code == 400
    assert persisted == []


@pytest.mark.parametrize("bad", ["waba-1", "123/phone_numbers", "1?x=", "1#", " 1",
                                 "", "١٢٣"])
async def test_a_non_digit_waba_id_answers_400_before_any_graph_call(
    monkeypatch, bad,
) -> None:
    from fastapi import HTTPException

    graph = _MetaGraph([_meta_number(_ES_PNID)])
    persisted = _embedded_route(monkeypatch, graph)
    with pytest.raises(HTTPException) as exc:
        await _connect(waba_id=bad, onboarding="coexistence")
    assert exc.value.status_code == 400
    assert graph.calls == [], "a bad waba_id reached Meta"
    assert persisted == []


def test_a_request_with_no_waba_id_answers_422(monkeypatch) -> None:
    from types import SimpleNamespace

    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    graph = _MetaGraph([_meta_number(_ES_PNID)])
    _embedded_route(monkeypatch, graph)
    app = FastAPI()
    app.post("/whatsapp/connect/embedded")(connect.embedded_signup)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(email="u@x")
    resp = TestClient(app).post("/whatsapp/connect/embedded",
                                json={"code": "auth-code",
                                      "phone_number_id": _ES_PNID})
    assert resp.status_code == 422
    assert any(e["loc"][-1] == "waba_id" for e in resp.json()["detail"])
    assert graph.calls == []


def test_an_unknown_onboarding_type_answers_422(monkeypatch) -> None:
    from types import SimpleNamespace

    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _embedded_route(monkeypatch, _MetaGraph([]))
    app = FastAPI()
    app.post("/whatsapp/connect/embedded")(connect.embedded_signup)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(email="u@x")
    resp = TestClient(app).post("/whatsapp/connect/embedded", json={
        "code": "c", "waba_id": _ES_WABA, "onboarding": "whatsmeow"})
    assert resp.status_code == 422


# ── the manual create route verifies with Meta first (WA-C1 review P1) ────────

class _Result:
    def __init__(self, row=None, scalar=None):
        self._row, self._scalar = row, scalar

    def fetchone(self):
        return self._row

    def scalar(self):
        return self._scalar


class _AccountsDB:
    """Answers the three statements of ``persist_account``."""

    def __init__(self):
        self.sql: list[str] = []
        self.params: list[dict] = []

    async def execute(self, statement, params=None):
        from types import SimpleNamespace

        sql = str(statement)
        self.sql.append(sql)
        self.params.append(dict(params or {}))
        if "INSERT INTO wa_accounts" in sql:
            return _Result(row=SimpleNamespace(
                id=params["id"], phone_number=params["phone"],
                phone_number_id=params["pnid"], waba_id=params["waba"],
                display_name=params["name"], avatar_color=None,
                sync_status=params["sync_status"], sync_error=None,
                history_import_phase=0, quality_rating=None,
                last_synced_at=None, is_default=params["is_default"]))
        if "COUNT(*)" in sql:
            return _Result(scalar=0)
        return _Result(row=None)


def _manual_route(monkeypatch, provider):
    """Patch the manual route's seams. Returns the fake DB and the list of
    sessions opened, so a test can see that a refusal opened none."""
    from contextlib import asynccontextmanager

    from acb_llm import key_store
    from gateway.routes.whatsapp.transport import accounts

    db = _AccountsDB()
    opened: list[int] = []
    meta_calls: list[dict] = []

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        opened.append(1)
        yield db

    def _provider(name, creds):
        meta_calls.append({"name": name, **creds})
        return provider

    class _Store:
        def encrypt(self, raw):
            db.stored.append(raw)
            return "enc"

    db.stored = []
    monkeypatch.delenv("WHATSAPP_GRAPH_VERSION", raising=False)
    monkeypatch.setattr(accounts, "_tenant_session", _tenant_session)
    if provider is not None:
        monkeypatch.setattr(accounts, "_instantiate_provider", _provider)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    return accounts, db, opened, meta_calls


_PNID = "1234567890"


def _create_req(accounts, token="SUPPLIED", pnid=_PNID, **blob):
    return accounts.CreateAccountRequest(
        phone_number="+91", phone_number_id=pnid,
        credentials={"access_token": token, "phone_number_id": "999", **blob})


async def test_manual_create_refused_by_meta_answers_400_and_writes_nothing(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    err = _HttpErr({"error": {"message": "Invalid OAuth access token.",
                              "code": 190}})
    accounts, db, opened, meta_calls = _manual_route(
        monkeypatch, _FakeProvider(raise_exc=err))

    with pytest.raises(HTTPException) as exc:
        await accounts.create_account(
            _create_req(accounts), user=SimpleNamespace(email="carol@b"))
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid OAuth access token. (Meta code 190)"
    assert opened == [] and db.sql == [], "a refused number opened a session"
    # Meta checked the SUPPLIED token for the number of the request, never
    # for a number that the credentials blob names, at the server's version.
    assert meta_calls == [{"name": "cloud_api", "access_token": "SUPPLIED",
                           "phone_number_id": _PNID, "graph_version": "v21.0"}]


async def test_manual_create_refuses_a_profile_for_another_number(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    accounts, db, opened, _ = _manual_route(
        monkeypatch, _FakeProvider(profile={"id": "5555555555"}))
    with pytest.raises(HTTPException) as exc:
        await accounts.create_account(
            _create_req(accounts), user=SimpleNamespace(email="carol@b"))
    assert exc.value.status_code == 400
    assert opened == [] and db.sql == []


async def test_manual_create_confirmed_by_meta_inserts(monkeypatch) -> None:
    from types import SimpleNamespace

    accounts, db, opened, _ = _manual_route(
        monkeypatch, _FakeProvider(profile={"id": _PNID,
                                            "verified_name": "A"}))
    out = await accounts.create_account(
        _create_req(accounts), user=SimpleNamespace(email="alice@a"))
    assert out.phone_number_id == _PNID
    assert opened == [1]
    assert any("INSERT INTO wa_accounts" in s for s in db.sql)


async def test_the_manual_route_still_writes_importing(monkeypatch) -> None:
    """WA-C2 moves only the Embedded Signup path to `live`. The manual path
    keeps `importing` until a later slice (spec §12.3 F6)."""
    from types import SimpleNamespace

    accounts, db, _opened, _ = _manual_route(
        monkeypatch, _FakeProvider(profile={"id": _PNID}))
    out = await accounts.create_account(
        _create_req(accounts), user=SimpleNamespace(email="alice@a"))
    assert out.sync_status == "importing"
    assert [q["sync_status"] for q in db.params if "sync_status" in q] == [
        "importing"]


async def test_persist_account_binds_the_sync_status_it_is_given(monkeypatch) -> None:
    from acb_llm import key_store
    from gateway.routes.whatsapp.transport.accounts import persist_account

    class _Store:
        def encrypt(self, raw):
            return "enc"

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    db = _AccountsDB()
    row = await persist_account(
        db, user_id="u", phone_number="+91", phone_number_id=_PNID,
        waba_id=_ES_WABA, display_name="", credentials={"access_token": "t"},
        webhook_verify_token=None, verified_profile={"id": _PNID},
        sync_status="live")
    assert row.sync_status == "live"
    [insert] = [s for s in db.sql if "INSERT INTO wa_accounts" in s]
    assert ":sync_status" in insert and "'importing'" not in insert


async def test_persist_account_cannot_run_without_a_verified_profile() -> None:
    import pytest
    from gateway.routes.whatsapp.transport.accounts import persist_account

    with pytest.raises(TypeError):
        await persist_account(  # type: ignore[call-arg]
            _AccountsDB(), user_id="u", phone_number="+91",
            phone_number_id=_PNID, waba_id=None, display_name="",
            credentials={"access_token": "t"}, webhook_verify_token=None)


# ── round 3: the URL Meta reads cannot be steered by the caller ─────────────

async def test_a_profile_with_no_id_answers_400_and_writes_nothing(
    monkeypatch,
) -> None:
    """A node read on Graph always returns ``id``. An edge read such as
    ``/<waba>/phone_numbers`` returns ``{"data": [...]}`` with none."""
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    accounts, db, opened, _ = _manual_route(
        monkeypatch, _FakeProvider(profile={"data": [{"id": _PNID}]}))
    with pytest.raises(HTTPException) as exc:
        await accounts.create_account(
            _create_req(accounts), user=SimpleNamespace(email="carol@b"))
    assert exc.value.status_code == 400
    assert opened == [] and db.sql == [], "an unproven number was inserted"


class _GraphClient:
    """Stands in for ``httpx.AsyncClient`` and records every URL."""

    urls: ClassVar[list[str]] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        import httpx

        _GraphClient.urls.append(str(httpx.URL(url)))

        class _R:
            def raise_for_status(self):
                return None

            def json(self):
                return {"id": _PNID, "verified_name": "A"}

        return _R()


@pytest.mark.parametrize("bad", [
    "v21.0/777/phone_numbers#", "v21.0/777/phone_numbers?x=", "v21.0?x=1",
    "../v1", "v21", "",
])
async def test_a_caller_graph_version_never_reaches_the_url(
    monkeypatch, bad,
) -> None:
    from types import SimpleNamespace

    import httpx

    accounts, db, _opened, _ = _manual_route(monkeypatch, None)
    _GraphClient.urls = []
    monkeypatch.setattr(httpx, "AsyncClient", _GraphClient)

    await accounts.create_account(
        _create_req(accounts, graph_version=bad),
        user=SimpleNamespace(email="carol@b"))

    assert _GraphClient.urls == [f"https://graph.facebook.com/v21.0/{_PNID}"]
    # Defence in depth: the stored blob carries no caller graph_version.
    stored = json.loads(db.stored[-1])
    assert "graph_version" not in stored
    assert stored["phone_number_id"] == _PNID


async def test_the_check_hands_the_provider_only_token_number_and_server_version(
    monkeypatch,
) -> None:
    """The rule in `verify_cloud_number` itself, not the provider's guard.

    The provider also cleans `graph_version`, so a URL test cannot see this
    rule fail (verifier mutation f). A stub provider records the creds.
    """
    import gateway.routes.whatsapp.transport.accounts as accounts

    seen: list[dict] = []

    class _Stub:
        async def get_phone_number_profile(self):
            return {"id": _PNID}

    def _fake(provider, creds):
        seen.append(dict(creds))
        return _Stub()

    monkeypatch.delenv("WHATSAPP_GRAPH_VERSION", raising=False)
    monkeypatch.setattr(accounts, "_instantiate_provider", _fake)

    await accounts.verify_cloud_number(_PNID, {
        "access_token": "TOKEN",
        "graph_version": "v21.0/777/phone_numbers#",
        "phone_number_id": "999",
        "base_url": "https://evil.example",
    })

    assert seen == [{"access_token": "TOKEN", "phone_number_id": _PNID,
                     "graph_version": "v21.0"}]


def test_a_stored_bad_graph_version_builds_the_default_url() -> None:
    from whatsapp_ingestion.providers.cloud_api import WhatsAppCloudProvider

    for bad in ("v21.0/777/phone_numbers#", "v21.0?x=", "x", 21):
        p = WhatsAppCloudProvider({"access_token": "t", "phone_number_id": _PNID,
                                   "graph_version": bad})
        assert p.graph_version == "v21.0", bad
        assert p._messages_url == (
            f"https://graph.facebook.com/v21.0/{_PNID}/messages")
    good = WhatsAppCloudProvider({"access_token": "t", "phone_number_id": _PNID,
                                  "graph_version": "v22.0"})
    assert good.graph_version == "v22.0"


@pytest.mark.parametrize("bad", ["pn-A", "123/456", "123?x=", "123#", " 123", ""])
async def test_a_non_digit_phone_number_id_answers_400(monkeypatch, bad) -> None:
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    accounts, _db, opened, meta_calls = _manual_route(
        monkeypatch, _FakeProvider(profile={"id": bad}))
    with pytest.raises(HTTPException) as exc:
        await accounts.create_account(
            _create_req(accounts, pnid=bad), user=SimpleNamespace(email="c@b"))
    assert exc.value.status_code == 400
    assert meta_calls == [] and opened == []


@pytest.mark.parametrize("bad", ["pn-A", "123/456/messages", "1?x=", ""])
def test_the_provider_refuses_a_non_digit_phone_number_id(bad) -> None:
    import pytest
    from whatsapp_ingestion.providers.cloud_api import WhatsAppCloudProvider

    with pytest.raises(ValueError):
        WhatsAppCloudProvider({"access_token": "t", "phone_number_id": bad})
