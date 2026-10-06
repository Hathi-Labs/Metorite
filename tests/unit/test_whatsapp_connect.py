"""Unit tests for the Connect wizard backend (W11) — the pure Meta-error
extractor, the verify route (mocked provider), and the connection-info route."""

from __future__ import annotations

import gateway.routes.whatsapp.transport.connect as connect
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
            connect.EmbeddedSignupRequest(code="c", phone_number_id="p"),
            user=None)
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Embedded Signup" in exc.detail


class _FakeDB:
    """The H2 seam's shape: the wrapper (not the handler) commits on exit."""

    def __init__(self):
        self.committed = 0

    async def commit(self):
        self.committed += 1


async def test_embedded_signup_happy_path(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("WHATSAPP_APP_ID", "app")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret")

    async def _exchange(code, app_id, app_secret, gv):
        return "TOKEN"

    subscribed_calls: list[str] = []

    async def _subscribe(waba_id, token, gv):
        subscribed_calls.append(waba_id)

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

    profile = {"display_phone_number": "+91 98765 43210",
               "verified_name": "Fracktal Works"}
    meta_calls: list[dict] = []

    def _provider(name, creds):
        meta_calls.append(dict(creds))
        return _FakeProvider(profile=profile)

    monkeypatch.setattr(connect, "exchange_code_for_token", _exchange)
    monkeypatch.setattr(connect, "subscribe_app_to_waba", _subscribe)
    monkeypatch.setattr(connect, "_tenant_session", _tenant_session)
    monkeypatch.setattr(connect, "_instantiate_provider", _provider)
    # The Embedded Signup path verified the number in step 2. It must not
    # call Meta a second time through the manual-route check.
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts._instantiate_provider",
        lambda name, creds: (_ for _ in ()).throw(AssertionError("Meta twice")))
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts.persist_account", _persist)
    monkeypatch.setattr(
        "gateway.routes.whatsapp.transport.accounts._account_model",
        lambda row: SimpleNamespace(
            id="acc-1", display_name="Fracktal Works",
            phone_number="+91 98765 43210"))

    out = await connect.embedded_signup(
        connect.EmbeddedSignupRequest(
            code="auth-code", phone_number_id="pn-1", waba_id="waba-1"),
        user=SimpleNamespace(email="u@x"))
    assert out.account_id == "acc-1"
    assert out.subscribed is True
    assert subscribed_calls == ["waba-1"]
    # One transaction, committed by the tenant-session wrapper on clean exit.
    assert db.committed == 1
    # Meta checked the exchanged token for this number once, and the profile
    # it returned is the proof that persist_account requires (WA-C1 P1).
    assert [(c["phone_number_id"], c["access_token"]) for c in meta_calls] == [
        ("pn-1", "TOKEN")]
    assert persisted["verified_profile"] is profile


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

    async def execute(self, statement, params=None):
        from types import SimpleNamespace

        sql = str(statement)
        self.sql.append(sql)
        if "INSERT INTO wa_accounts" in sql:
            return _Result(row=SimpleNamespace(
                id=params["id"], phone_number=params["phone"],
                phone_number_id=params["pnid"], waba_id=params["waba"],
                display_name=params["name"], avatar_color=None,
                sync_status="importing", sync_error=None,
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
            return "enc"

    monkeypatch.setattr(accounts, "_tenant_session", _tenant_session)
    monkeypatch.setattr(accounts, "_instantiate_provider", _provider)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    return accounts, db, opened, meta_calls


def _create_req(accounts, token="SUPPLIED"):
    return accounts.CreateAccountRequest(
        phone_number="+91", phone_number_id="pn-A",
        credentials={"access_token": token, "phone_number_id": "pn-OTHER"})


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
    # for a number that the credentials blob names.
    assert meta_calls == [{"name": "cloud_api", "access_token": "SUPPLIED",
                           "phone_number_id": "pn-A"}]


async def test_manual_create_refuses_a_profile_for_another_number(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    accounts, db, opened, _ = _manual_route(
        monkeypatch, _FakeProvider(profile={"id": "pn-SOMEONE-ELSE"}))
    with pytest.raises(HTTPException) as exc:
        await accounts.create_account(
            _create_req(accounts), user=SimpleNamespace(email="carol@b"))
    assert exc.value.status_code == 400
    assert opened == [] and db.sql == []


async def test_manual_create_confirmed_by_meta_inserts(monkeypatch) -> None:
    from types import SimpleNamespace

    accounts, db, opened, _ = _manual_route(
        monkeypatch, _FakeProvider(profile={"id": "pn-A",
                                            "verified_name": "A"}))
    out = await accounts.create_account(
        _create_req(accounts), user=SimpleNamespace(email="alice@a"))
    assert out.phone_number_id == "pn-A"
    assert opened == [1]
    assert any("INSERT INTO wa_accounts" in s for s in db.sql)


async def test_persist_account_cannot_run_without_a_verified_profile() -> None:
    import pytest
    from gateway.routes.whatsapp.transport.accounts import persist_account

    with pytest.raises(TypeError):
        await persist_account(  # type: ignore[call-arg]
            _AccountsDB(), user_id="u", phone_number="+91",
            phone_number_id="pn-A", waba_id=None, display_name="",
            credentials={"access_token": "t"}, webhook_verify_token=None)
