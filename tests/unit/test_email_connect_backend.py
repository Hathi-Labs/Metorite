"""EM-T3a — the backend for the connect flow (hermetic half).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.3, EM-T3a items 1
to 5.

R7 fences named here:

* ``email-app-creds-from-settings``: a new account blob holds no app
  credential, a refresh sends the secret of settings and never the secret of
  the blob, and an export writes token fields only. With no secret in
  settings, a refresh fails closed and sends nothing.
* ``email-authority-common``: with the sign-in tenant set to a GUID, the
  authorize leg, the token exchange and a refresh all use ``/common/``.
* ``email-login-hint``: the authorize URL carries the email of the session as
  ``login_hint``. A query hint replaces it only when it parses as an address.
* ``email-consent-errors``: an ``AADSTS`` code in ``error_description`` maps
  to ``admin_consent_required`` or ``consent_declined``. The description text
  never reaches the Location.
* ``email-initial-sync-flag``: the account model carries
  ``initial_sync_done``. The SQL half is R8 and lives in
  ``test_email_accounts_initial_sync_rls.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from email_ingestion.providers import factory
from email_ingestion.providers.app_credentials import APP_CREDENTIAL_KEYS
from gateway.routes.email.transport import accounts, oauth, signing

REPO = Path(__file__).resolve().parents[2]
SECRET = "em-t3a-test-secret"
ORG = "11111111-2222-3333-4444-555555555555"
MEMBER = "dana@example.com"
TENANT_GUID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"

SETTINGS_ID = "settings-client-id"
SETTINGS_SECRET = "settings-client-secret"
OLD_BLOB = {
    "access_token": None,
    "refresh_token": "rt-0",
    "client_id": "old-client-id",
    "client_secret": "old-revoked-secret",
    "tenant_id": TENANT_GUID,
}

_APP_ENV = (
    "MSFT_OAUTH_CLIENT_ID", "MSFT_OAUTH_CLIENT_SECRET",
    "AUTH_MICROSOFT_ENTRA_ID_ID", "AUTH_MICROSOFT_ENTRA_ID_SECRET",
    "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "gateway_session_secret", SECRET, raising=False)
    for name in ("msft_oauth_client_id", "gmail_oauth_client_id"):
        monkeypatch.setattr(s, name, SETTINGS_ID, raising=False)
    for name in ("msft_oauth_client_secret", "gmail_oauth_client_secret"):
        monkeypatch.setattr(s, name, SETTINGS_SECRET, raising=False)
    for name in _APP_ENV:
        monkeypatch.delenv(name, raising=False)
    # A sign-in tenant is set, so a reader of it would leave `/common/`.
    for name in (
        "MICROSOFT_TENANT_ID", "AUTH_MICROSOFT_ENTRA_ID_TENANT",
        "AUTH_MICROSOFT_TENANT_ID",
    ):
        monkeypatch.setenv(name, TENANT_GUID)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.example.test")


def _no_app_in_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    for name in (
        "msft_oauth_client_id", "msft_oauth_client_secret",
        "gmail_oauth_client_id", "gmail_oauth_client_secret",
    ):
        monkeypatch.setattr(s, name, "", raising=False)


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _TokenEndpoint:
    """Stands in for ``httpx.AsyncClient`` and records every POST."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def __call__(self, *_a, **_kw) -> _TokenEndpoint:
        return self

    async def __aenter__(self) -> _TokenEndpoint:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def post(self, url: str, data: dict | None = None, **_kw) -> _Resp:
        self.posts.append((url, dict(data or {})))
        return _Resp({"access_token": "at-new", "refresh_token": "rt-new"})


@pytest.fixture()
def token_endpoint(monkeypatch: pytest.MonkeyPatch) -> _TokenEndpoint:
    fake = _TokenEndpoint()
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    return fake


def _member(email: str = MEMBER) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG)


# ── 1. The app credentials come from settings ──────────────────────────────


async def test_a_new_account_blob_holds_no_app_credential(monkeypatch) -> None:
    import email_ingestion.scheduler as sched
    from acb_auth import access
    from acb_llm import key_store

    saved: list[dict] = []

    class _Store:
        def encrypt(self, raw: str) -> str:
            return raw

    async def _exchange(code, redirect_uri):
        return {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3599,
            "token_type": "Bearer", "scope": "Mail.ReadWrite",
        }

    async def _mailbox(provider, token):
        return "box@contoso.test"

    async def _save(**kw):
        saved.append(kw)
        return "acc-1"

    async def _resolve(email):
        return ("u1", ORG)

    async def _no_sync(account_id, organization_id=None):
        return None

    monkeypatch.setattr(oauth, "_exchange_msft_token", _exchange)
    monkeypatch.setattr(oauth, "_get_provider_email", _mailbox)
    monkeypatch.setattr(oauth, "_save_account", _save)
    monkeypatch.setattr(access, "resolve_identity", _resolve)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "refresh_account_sync", _no_sync)

    state = signing.sign_oauth_state(
        org=ORG, member=MEMBER, provider="microsoft", redirect_after="",
    )
    resp = await oauth.oauth_callback(
        "microsoft", user=_member(), code="c0de", state=state, error=None,
    )

    assert "error" not in parse_qs(urlparse(resp.headers["location"]).query)
    blob = json.loads(saved[0]["encrypted_creds"])
    assert blob["access_token"] == "at" and blob["refresh_token"] == "rt"
    assert not APP_CREDENTIAL_KEYS & blob.keys(), blob.keys()


def test_the_blob_copy_helper_is_gone() -> None:
    assert not hasattr(oauth, "_provider_oauth_app_creds")


@pytest.mark.parametrize("provider", ["microsoft", "gmail"])
async def test_a_refresh_sends_the_secret_of_settings_not_of_the_blob(
    provider, token_endpoint,
) -> None:
    p = factory.build_provider(provider, dict(OLD_BLOB))

    await p._refresh_access_token()

    [(url, data)] = token_endpoint.posts
    assert data["client_id"] == SETTINGS_ID
    assert data["client_secret"] == SETTINGS_SECRET
    assert "old" not in json.dumps(data)
    if provider == "microsoft":
        assert url == "https://login.microsoftonline.com/common/oauth2/v2.0/token"


@pytest.mark.parametrize("provider", ["microsoft", "gmail"])
async def test_an_export_after_a_refresh_holds_no_app_credential(
    provider, token_endpoint,
) -> None:
    p = factory.build_provider(provider, dict(OLD_BLOB))

    await p._refresh_access_token()
    out = p.export_credentials()

    assert p.credentials_dirty()
    assert out["access_token"] == "at-new" and out["refresh_token"] == "rt-new"
    assert not APP_CREDENTIAL_KEYS & out.keys(), out.keys()


@pytest.mark.parametrize("provider", ["microsoft", "gmail"])
async def test_no_secret_in_settings_fails_closed_and_never_reads_the_blob(
    provider, token_endpoint, monkeypatch,
) -> None:
    """A fallback to the blob is how a revoked secret stays alive."""
    _no_app_in_settings(monkeypatch)
    p = factory.build_provider(provider, dict(OLD_BLOB))

    with pytest.raises(ValueError, match="not configured"):
        await p._refresh_access_token()
    assert token_endpoint.posts == []


async def test_the_runtime_environment_is_read_after_settings(
    token_endpoint, monkeypatch,
) -> None:
    """The key store writes a saved credential to ``os.environ`` at run time,
    after ``get_settings()`` was cached."""
    _no_app_in_settings(monkeypatch)
    monkeypatch.setenv("MSFT_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("MSFT_OAUTH_CLIENT_SECRET", "env-secret")
    p = factory.build_provider("microsoft", dict(OLD_BLOB))

    await p._refresh_access_token()

    [(_url, data)] = token_endpoint.posts
    assert (data["client_id"], data["client_secret"]) == ("env-id", "env-secret")


# ── 2. One authority for mail ──────────────────────────────────────────────


async def _authorize(provider: str = "microsoft", **kw):
    kw.setdefault("redirect_after", "")
    resp = await oauth.oauth_authorize(provider, user=kw.pop("user", _member()), **kw)
    assert resp.status_code == 302
    url = urlparse(resp.headers["location"])
    return url, parse_qs(url.query)


async def test_the_authorize_url_uses_common_with_a_sign_in_tenant_set() -> None:
    url, query = await _authorize()
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )
    assert TENANT_GUID not in url.geturl()
    assert query["client_id"] == [SETTINGS_ID]


async def test_the_token_exchange_uses_common(token_endpoint) -> None:
    await oauth._exchange_msft_token("c0de", "https://app.example.test/cb")

    [(url, data)] = token_endpoint.posts
    assert url == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    assert data["client_secret"] == SETTINGS_SECRET


def test_the_email_code_reads_no_sign_in_tenant() -> None:
    files = [
        REPO / "apps/services/gateway/gateway/routes/email/transport/oauth.py",
        *sorted((REPO / "apps/services/email_ingestion/email_ingestion/providers")
                .glob("*.py")),
    ]
    for path in files:
        src = path.read_text(encoding="utf-8")
        for name in (
            "MICROSOFT_TENANT_ID", "AUTH_MICROSOFT_ENTRA_ID_TENANT",
            "AUTH_MICROSOFT_TENANT_ID",
        ):
            # A quoted name is a read. A docstring may name it to say why not.
            for quoted in (f'"{name}"', f"'{name}'"):
                assert quoted not in src, f"{path.name} reads {name}"
        assert 'credentials.get("client_secret")' not in src, path.name
        assert 'credentials.get("client_id")' not in src, path.name


async def test_authorize_answers_400_with_no_client_id(monkeypatch) -> None:
    from fastapi import HTTPException

    _no_app_in_settings(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await _authorize()
    assert exc.value.status_code == 400


# ── 3. login_hint ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["microsoft", "gmail"])
async def test_the_hint_defaults_to_the_session_email(provider) -> None:
    _url, query = await _authorize(provider)
    assert query["login_hint"] == [MEMBER]
    assert query.get("prompt", []) != ["select_account"]


async def test_a_well_formed_query_hint_is_sent_and_encoded() -> None:
    url, query = await _authorize(login_hint="ravi+mail@contoso.test")
    assert query["login_hint"] == ["ravi+mail@contoso.test"]
    assert "login_hint=ravi%2Bmail%40contoso.test" in url.query


@pytest.mark.parametrize(
    "bad",
    [
        "not-an-address",
        "a@b",
        "dana@example.com&prompt=select_account",
        "Dana <dana@example.com>",
        "x" * 250 + "@contoso.test",
        "",
    ],
)
async def test_a_malformed_query_hint_is_dropped(bad) -> None:
    url, query = await _authorize(login_hint=bad)
    assert query["login_hint"] == [MEMBER]
    assert "select_account" not in url.query


async def test_the_authorize_query_is_encoded_once() -> None:
    """The URL was built by concatenation. Each value now round-trips."""
    _url, query = await _authorize(redirect_after="/email?a=1&b=2")
    assert query["redirect_uri"] == [
        "https://app.example.test/api/email/oauth/microsoft/callback"
    ]
    claims = signing.verify_oauth_state(query["state"][0])
    assert claims is not None and claims["redirect_after"] == "/email?a=1&b=2"
    assert "MailboxSettings.ReadWrite" in query["scope"][0]
    assert query["scope"][0].startswith("offline_access ")


# ── 4. Consent errors ──────────────────────────────────────────────────────

_DESCRIPTION_TEXT = "Need admin approval. Trace ID: 5f1e Correlation ID: 9a2b"


@pytest.mark.parametrize(
    ("error", "description", "shown"),
    [
        ("access_denied", "AADSTS90094: " + _DESCRIPTION_TEXT, "admin_consent_required"),
        ("consent_required", "AADSTS90095: " + _DESCRIPTION_TEXT, "admin_consent_required"),
        ("invalid_client", "AADSTS65001: " + _DESCRIPTION_TEXT, "admin_consent_required"),
        ("access_denied", "AADSTS65004: " + _DESCRIPTION_TEXT, "consent_declined"),
        ("access_denied", None, "consent_declined"),
        ("access_denied", _DESCRIPTION_TEXT, "consent_declined"),
        ("Access_Denied", "AADSTS50105: " + _DESCRIPTION_TEXT, "consent_declined"),
        ("invalid_request", "AADSTS50011: " + _DESCRIPTION_TEXT, "invalid_request"),
        ("<b>x</b>", "AADSTS50011", "provider_error"),
        ("server_error", "<script>AADSTS90094x</script>", "admin_consent_required"),
    ],
)
async def test_a_consent_error_lands_on_its_reason(error, description, shown) -> None:
    resp = await oauth.oauth_callback(
        "microsoft", user=_member(), code=None, state=None, error=error,
        error_description=description,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    url = urlparse(location)
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://app.example.test/email/oauth/callback"
    )
    assert parse_qs(url.query) == {"error": [shown]}
    for fragment in ("Trace", "Correlation", "admin approval", "AADSTS", "script"):
        assert fragment not in location


def test_the_callback_accepts_error_description() -> None:
    import inspect

    param = inspect.signature(oauth.oauth_callback).parameters["error_description"]
    assert param.default is None


async def test_a_description_with_no_error_is_still_invalid_state() -> None:
    resp = await oauth.oauth_callback(
        "microsoft", user=_member(), code=None, state=None, error=None,
        error_description="AADSTS90094",
    )
    assert parse_qs(urlparse(resp.headers["location"]).query) == {
        "error": ["invalid_state"],
    }


# ── 5. The first-sync flag ─────────────────────────────────────────────────


def test_the_account_model_carries_initial_sync_done() -> None:
    field = accounts.EmailAccountModel.model_fields["initial_sync_done"]
    assert field.default is False


def test_the_three_account_reads_return_initial_sync_done() -> None:
    import inspect

    for fn in (
        accounts.list_accounts, accounts.set_default_account,
        accounts.update_account,
    ):
        src = inspect.getsource(fn)
        assert src.count("initial_sync_done") >= 2, fn.__name__
