"""WS-17 EM-G7 — the connect backend of Gmail.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.9, D-EM-31 (the
scopes), D-EM-35 (the capability read) and the orchestrator decision of
2026-10-05 on the dark flag ``EMAIL_GMAIL_CONNECT``.

R7 fences named here (the eight of the spec first):

* ``test_gmail_asks_the_two_scopes_of_d_em_31``: the authorize URL asks
  ``gmail.modify`` and ``gmail.settings.basic``, offline, with consent.
* ``test_a_grant_without_settings_basic_saves_nothing``: a grant that lacks
  either scope bounces ``scope_missing`` and saves no mailbox (M1).
* ``test_a_token_without_refresh_token_saves_nothing``: a Google token with
  no refresh token bounces ``token_exchange_failed`` (M4).
* ``test_each_bounce_names_the_provider``: each failure of the callback
  carries ``provider``, and only a known one (M2).
* ``test_google_errors_map_to_their_reasons``: ``access_denied`` and
  ``admin_policy_enforced`` have reasons of their own (M5).
* ``test_not_configured_names_no_integrations``: the member reads no
  operator instruction (GM-25).
* ``test_the_providers_route_answers_booleans_only``: the capability read
  holds booleans keyed by provider id, never a client ID (M3, M10).
* ``test_gmail_app_info_returns_the_client_id``: item 9.

The dark flag (M11, M12, M13): with the Google app configured and the flag
off, the capability says ``gmail: false``, the authorize leg refuses before it
signs a state, and the callback refuses before the token exchange, so no
``email_accounts`` row is written. With the flag on, Gmail connects as before.

Every case here is hermetic. The callback writes through ``_save_account``
only, and the R8 suites ``test_email_tenant_bind_rls.py`` and
``test_email_import_floor.py`` prove its SQL. This slice changes no SQL.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import env_guard, get_settings
from acb_common.settings import Settings
from email_ingestion.providers.gmail import GMAIL_SCOPES
from fastapi import HTTPException
from gateway.routes.email.transport import oauth, signing

REPO = Path(__file__).resolve().parents[2]
SECRET = "em-g7-test-secret"
ORG = "11111111-2222-3333-4444-555555555555"
MEMBER = "dana@example.com"
GMAIL_ID = "em-g7-gmail-client-id"
GMAIL_SECRET = "em-g7-gmail-client-secret-never-sent"
MSFT_ID = "em-g7-msft-client-id"
MSFT_SECRET = "em-g7-msft-client-secret-never-sent"

MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SETTINGS_BASIC = "https://www.googleapis.com/auth/gmail.settings.basic"
FULL_GRANT = f"{MODIFY} {SETTINGS_BASIC}"
CALLBACK_PAGE = "https://app.example.test/email/oauth/callback"

_APP_ENV = (
    "MSFT_OAUTH_CLIENT_ID", "MSFT_OAUTH_CLIENT_SECRET",
    "AUTH_MICROSOFT_ENTRA_ID_ID", "AUTH_MICROSOFT_ENTRA_ID_SECRET",
    "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
)

#: The real writer, taken before any fixture replaces it.
_REAL_SAVE_ACCOUNT = oauth._save_account


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both apps configured, and the Gmail flag ON. A dark case turns it off."""
    s = get_settings()
    monkeypatch.setattr(s, "gateway_session_secret", SECRET, raising=False)
    monkeypatch.setattr(s, "gmail_oauth_client_id", GMAIL_ID, raising=False)
    monkeypatch.setattr(s, "gmail_oauth_client_secret", GMAIL_SECRET, raising=False)
    monkeypatch.setattr(s, "msft_oauth_client_id", MSFT_ID, raising=False)
    monkeypatch.setattr(s, "msft_oauth_client_secret", MSFT_SECRET, raising=False)
    monkeypatch.setattr(s, "email_gmail_connect", True, raising=False)
    for name in _APP_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.example.test")

    # The authorize leg asks the database whether the member already has a
    # mailbox (EM-T8a). These tests run with no database.
    async def _no_mailbox(org, member, provider):
        return False

    monkeypatch.setattr(oauth, "_member_has_mailbox", _no_mailbox)


def _dark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "email_gmail_connect", False, raising=False)


def _no_app(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    prefix = "gmail_oauth" if provider == "gmail" else "msft_oauth"
    for part in ("client_id", "client_secret"):
        monkeypatch.setattr(get_settings(), f"{prefix}_{part}", "", raising=False)


def _member(email: str = MEMBER) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG)


def _query(resp) -> dict[str, str]:
    """The query of a redirect to the callback page, one value per key."""
    assert resp.status_code == 302
    url = urlparse(resp.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == CALLBACK_PAGE
    return {k: v[0] for k, v in parse_qs(url.query).items()}


# ── The callback, with fakes at its edges ──────────────────────────────────


class _Spy:
    """Fakes for each edge of the callback, and a record of each call."""

    def __init__(self) -> None:
        self.token: dict = {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3599,
            "token_type": "Bearer", "scope": FULL_GRANT,
        }
        self.exchanged = 0
        self.profiled = 0
        self.saved: list[dict] = []
        self.blocks = 0
        self.exchange_raises = False
        self.profile_raises = False
        self.save_raises = False

    async def exchange(self, code, redirect_uri):
        self.exchanged += 1
        if self.exchange_raises:
            raise RuntimeError("400 Bad Request")
        return dict(self.token)

    async def profile(self, provider, token):
        self.profiled += 1
        if self.profile_raises:
            raise RuntimeError("403 Forbidden")
        return "dana@contoso.test"

    async def save(self, **kw):
        if self.save_raises:
            raise RuntimeError("relation does not exist")
        self.saved.append(kw)
        return "acc-1"

    def tenant_session(self, org):
        """Stands in for ``_tenant_session``: count the block, then refuse."""
        self.blocks += 1
        raise AssertionError("the callback opened a database block")

    async def resolve(self, email):
        return ("u1", ORG)


class _Store:
    def encrypt(self, raw: str) -> str:
        return "enc:" + raw


@pytest.fixture()
def spy(monkeypatch) -> _Spy:
    import email_ingestion.scheduler as sched
    from acb_auth import access
    from acb_llm import key_store

    s = _Spy()

    async def _no_sync(account_id, organization_id=None):
        return None

    monkeypatch.setattr(oauth, "_exchange_gmail_token", s.exchange)
    monkeypatch.setattr(oauth, "_exchange_msft_token", s.exchange)
    monkeypatch.setattr(oauth, "_get_provider_email", s.profile)
    monkeypatch.setattr(oauth, "_save_account", s.save)
    monkeypatch.setattr(oauth, "_tenant_session", s.tenant_session)
    monkeypatch.setattr(access, "resolve_identity", s.resolve)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(sched, "refresh_account_sync", _no_sync)
    return s


async def _callback(
    provider: str = "gmail", *, code: str | None = "c0de",
    state: str | None = None, error: str | None = None,
    error_description: str | None = None,
):
    if state is None and code is not None:
        state = signing.sign_oauth_state(org=ORG, member=MEMBER, provider=provider)
    return await oauth.oauth_callback(
        provider, user=_member(), code=code, state=state, error=error,
        error_description=error_description,
    )


# ── 1. The scopes (D-EM-31) ────────────────────────────────────────────────


async def test_gmail_asks_the_two_scopes_of_d_em_31() -> None:
    assert GMAIL_SCOPES == [MODIFY, SETTINGS_BASIC]

    resp = await oauth.oauth_authorize("gmail", user=_member(), redirect_after="")

    assert resp.status_code == 302
    url = urlparse(resp.headers["location"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query["scope"] == [FULL_GRANT]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["client_id"] == [GMAIL_ID]
    assert "mail.google.com" not in url.geturl(), "the widest scope is gone"
    for absent in ("include_granted_scopes", "code_challenge", "response_mode"):
        assert absent not in query, absent
    assert "openid" not in query["scope"][0]


# ── 2. The granted scope ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "granted",
    [
        pytest.param(MODIFY, id="no-settings-basic"),
        pytest.param(SETTINGS_BASIC, id="no-modify"),
        pytest.param("https://mail.google.com/", id="the-old-widest-scope-only"),
        pytest.param("", id="empty"),
        pytest.param(None, id="absent"),
        pytest.param([MODIFY, SETTINGS_BASIC], id="not-a-string"),
    ],
)
async def test_a_grant_without_settings_basic_saves_nothing(spy, granted) -> None:
    if granted is None:
        del spy.token["scope"]
    else:
        spy.token["scope"] = granted

    query = _query(await _callback("gmail"))

    assert query == {"error": "scope_missing", "provider": "gmail"}
    assert spy.exchanged == 1
    assert spy.profiled == 0, "a partial grant must not reach the profile read"
    assert spy.saved == [] and spy.blocks == 0, "a partial grant must save nothing"


async def test_a_grant_with_both_scopes_and_more_saves_the_mailbox(spy) -> None:
    spy.token["scope"] = f"openid {SETTINGS_BASIC} {MODIFY}"
    query = _query(await _callback("gmail"))
    assert "error" not in query
    assert query["provider"] == "gmail" and query["account_id"] == "acc-1"
    assert len(spy.saved) == 1 and spy.saved[0]["provider"] == "gmail"


# ── 3. The refresh token ───────────────────────────────────────────────────


@pytest.mark.parametrize("refresh", [None, "", "absent"])
async def test_a_token_without_refresh_token_saves_nothing(spy, refresh) -> None:
    if refresh == "absent":
        del spy.token["refresh_token"]
    else:
        spy.token["refresh_token"] = refresh

    query = _query(await _callback("gmail"))

    assert query == {"error": "token_exchange_failed", "provider": "gmail"}
    assert spy.profiled == 0 and spy.saved == [] and spy.blocks == 0


async def test_the_microsoft_leg_does_not_read_the_google_rules(spy) -> None:
    """Items 2 and 3 are for Google. A Graph token names no Gmail scope."""
    spy.token = {"access_token": "at", "refresh_token": "", "scope": "Mail.ReadWrite"}
    query = _query(await _callback("microsoft"))
    assert "error" not in query
    assert len(spy.saved) == 1


# ── 4. The provider on each bounce ─────────────────────────────────────────


def _arrange(spy: _Spy, case: str) -> dict:
    """Set the spy for one failure, and return the callback arguments."""
    if case == "no-code-no-error":
        return {"code": None}
    if case == "a-provider-error":
        return {"code": None, "error": "access_denied"}
    if case == "a-bad-state":
        return {"state": "not.a-state"}
    if case == "the-exchange-fails":
        spy.exchange_raises = True
    elif case == "the-profile-read-fails":
        spy.profile_raises = True
    elif case == "the-save-fails":
        spy.save_raises = True
    return {}


@pytest.mark.parametrize("provider", ["gmail", "microsoft"])
@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("no-code-no-error", "invalid_state"),
        ("a-provider-error", "consent_declined"),
        ("a-bad-state", "invalid_state"),
        ("the-exchange-fails", "token_exchange_failed"),
        ("the-profile-read-fails", "email_fetch_failed"),
        ("the-save-fails", "account_save_failed"),
    ],
)
async def test_each_bounce_names_the_provider(spy, provider, case, reason) -> None:
    query = _query(await _callback(provider, **_arrange(spy, case)))
    assert query == {"error": reason, "provider": provider}


@pytest.mark.parametrize("segment", ["zoho", "imap", "Gmail", "gmail ", "x" * 40])
async def test_a_bounce_never_echoes_another_path_segment(spy, segment) -> None:
    query = _query(await _callback(segment, code=None))
    assert query == {"error": "invalid_state"}


# ── 5. The Google errors ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "description", "shown"),
    [
        ("access_denied", None, "consent_declined"),
        ("admin_policy_enforced", None, "workspace_admin_blocked"),
        ("Admin_Policy_Enforced", None, "workspace_admin_blocked"),
        ("org_internal", None, "org_internal"),
        ("invalid_scope", None, "invalid_scope"),
        # The description is never read for Google, and never echoed.
        ("invalid_request", "AADSTS90094: Need admin approval", "invalid_request"),
        ("<b>x</b>", None, "provider_error"),
    ],
)
async def test_google_errors_map_to_their_reasons(spy, error, description, shown) -> None:
    resp = await _callback(
        "gmail", code=None, error=error, error_description=description,
    )
    assert _query(resp) == {"error": shown, "provider": "gmail"}
    for fragment in ("AADSTS", "admin approval", "<b>"):
        assert fragment not in resp.headers["location"]
    assert spy.exchanged == 0 and spy.saved == []


@pytest.mark.parametrize(
    ("error", "description", "shown"),
    [
        ("admin_policy_enforced", None, "admin_policy_enforced"),
        ("access_denied", "AADSTS90094: x", "admin_consent_required"),
    ],
)
async def test_the_microsoft_errors_are_unchanged(spy, error, description, shown) -> None:
    resp = await _callback(
        "microsoft", code=None, error=error, error_description=description,
    )
    assert _query(resp) == {"error": shown, "provider": "microsoft"}


# ── 6. No operator text for a member (GM-25) ───────────────────────────────


async def test_not_configured_names_no_integrations(monkeypatch) -> None:
    assert set(oauth._NOT_CONFIGURED) == set(oauth.CONNECT_PROVIDERS)
    for provider, text in oauth._NOT_CONFIGURED.items():
        assert "not available yet" in text, provider
        for banned in (
            "integrations", "client id", "client_id", "secret", "google cloud",
            "azure", "configure", "→",
        ):
            assert banned not in text.lower(), (provider, banned)

    # The authorize leg answers with that text when the app is absent.
    for provider in oauth.CONNECT_PROVIDERS:
        _no_app(monkeypatch, provider)
        with pytest.raises(HTTPException) as exc:
            await oauth.oauth_authorize(provider, user=_member(), redirect_after="")
        assert exc.value.status_code == 400
        assert exc.value.detail == oauth._NOT_CONFIGURED[provider]


# ── 7. The capability read (D-EM-35) ───────────────────────────────────────


def _body(result) -> dict:
    return result.model_dump() if hasattr(result, "model_dump") else dict(result)


async def test_the_providers_route_answers_booleans_only(monkeypatch) -> None:
    body = _body(await oauth.oauth_providers(user=_member()))

    assert body == {"microsoft": True, "gmail": True}
    assert all(type(v) is bool for v in body.values()), body
    dumped = json.dumps(body)
    for leak in (GMAIL_ID, GMAIL_SECRET, MSFT_ID, MSFT_SECRET, "http", "callback"):
        assert leak not in dumped, leak

    # The model cannot grow a field that carries one.
    model = oauth.OAuthProviders
    assert model.model_config.get("extra") == "forbid"
    assert set(model.model_fields) == {"microsoft", "gmail"}
    assert all(f.annotation is bool for f in model.model_fields.values())

    # Each answer follows its own app. A client ID with no secret is not one.
    monkeypatch.setattr(get_settings(), "gmail_oauth_client_secret", "", raising=False)
    assert _body(await oauth.oauth_providers(user=_member())) == {
        "microsoft": True, "gmail": False,
    }
    _no_app(monkeypatch, "microsoft")
    assert _body(await oauth.oauth_providers(user=_member())) == {
        "microsoft": False, "gmail": False,
    }


async def test_the_providers_route_is_gated_and_mounted() -> None:
    import gateway.main as main

    from tests.unit._routes import served_routes

    paths = {getattr(r, "path", "") for r in served_routes(main.app.routes)}
    assert "/email/oauth/providers" in paths
    assert "/email/oauth/providers" not in main.PUBLIC_ROUTES
    for user in (
        UserContext(email=MEMBER, role=UserRole.EMPLOYEE),
        UserContext(email=None, role=UserRole.AGENT, organization_id=ORG),
    ):
        with pytest.raises(HTTPException) as exc:
            await oauth.oauth_providers(user=user)
        assert exc.value.status_code == 403


# ── 8. The app facts for a Workspace admin ─────────────────────────────────


async def test_gmail_app_info_returns_the_client_id() -> None:
    info = await oauth.oauth_app_info("gmail", user=_member())
    assert info.provider == "gmail"
    assert info.client_id == GMAIL_ID
    assert info.redirect_uri == (
        "https://app.example.test/api/email/oauth/gmail/callback"
    )
    assert info.redirect_uri == oauth._build_redirect_uri("gmail")
    assert GMAIL_SECRET not in info.model_dump_json()


# ── The dark flag, EMAIL_GMAIL_CONNECT (orchestrator, 2026-10-05) ──────────


def test_the_dark_flag_defaults_off() -> None:
    field = Settings.model_fields["email_gmail_connect"]
    assert field.default is False
    assert field.annotation is bool


async def test_flag_off_the_capability_says_gmail_false(monkeypatch) -> None:
    _dark(monkeypatch)
    assert oauth.oauth_app("gmail").configured, "the app stays configured"
    body = _body(await oauth.oauth_providers(user=_member()))
    assert body == {"microsoft": True, "gmail": False}


@pytest.mark.parametrize("login_hint", [None, "dana@gmail.example"])
async def test_flag_off_the_authorize_leg_refuses_and_signs_nothing(
    monkeypatch, login_hint,
) -> None:
    """A reconnect sends a hint. It is refused too, and production holds no
    Gmail mailbox (§12.3.9 as-built)."""
    _dark(monkeypatch)
    signed: list[dict] = []
    asked: list[tuple] = []

    def _sign(**kw):
        signed.append(kw)
        return "never"

    async def _ask(*args):
        asked.append(args)
        return False

    monkeypatch.setattr(oauth, "sign_oauth_state", _sign)
    monkeypatch.setattr(oauth, "_member_has_mailbox", _ask)

    with pytest.raises(HTTPException) as exc:
        await oauth.oauth_authorize(
            "gmail", user=_member(), redirect_after="", login_hint=login_hint,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == oauth._NOT_CONFIGURED["gmail"]
    assert "EMAIL_GMAIL_CONNECT" not in exc.value.detail
    assert "flag" not in exc.value.detail.lower()
    assert signed == [] and asked == []


@pytest.mark.parametrize(
    "returned",
    [
        pytest.param({"code": "c0de"}, id="a-code"),
        pytest.param({"code": None, "error": "access_denied"}, id="an-error"),
    ],
)
async def test_flag_off_the_callback_refuses_and_writes_nothing(
    spy, monkeypatch, returned,
) -> None:
    """A state signed before a flip-off must not complete.

    The REAL ``_save_account`` runs here, and the spy stands in for
    ``_tenant_session``. So any write would open a database block, and the
    count of blocks proves that no ``email_accounts`` row is written.
    """
    monkeypatch.setattr(oauth, "_save_account", _REAL_SAVE_ACCOUNT)
    state = signing.sign_oauth_state(org=ORG, member=MEMBER, provider="gmail")
    _dark(monkeypatch)

    query = _query(await _callback("gmail", state=state, **returned))

    assert query == {"error": "provider_unavailable", "provider": "gmail"}
    assert spy.exchanged == 0, "a dark callback must not spend the code"
    assert spy.profiled == 0
    assert spy.blocks == 0, "a dark callback must open no database block"


async def test_flag_off_the_gmail_app_info_is_a_503(monkeypatch) -> None:
    _dark(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await oauth.oauth_app_info("gmail", user=_member())
    assert exc.value.status_code == 503
    assert GMAIL_ID not in str(exc.value.detail)


async def test_flag_on_the_gmail_connect_runs_as_today(spy) -> None:
    resp = await oauth.oauth_authorize("gmail", user=_member(), redirect_after="/email")
    assert resp.status_code == 302
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]

    query = _query(await _callback("gmail", state=state))

    assert query == {
        "account_id": "acc-1", "email": "dana@contoso.test",
        "provider": "gmail", "redirect_after": "/email",
    }
    assert len(spy.saved) == 1
    assert spy.saved[0]["provider"] == "gmail" and spy.saved[0]["org"] == ORG


async def test_the_flag_does_not_touch_microsoft(spy, monkeypatch) -> None:
    _dark(monkeypatch)
    assert _body(await oauth.oauth_providers(user=_member()))["microsoft"] is True
    resp = await oauth.oauth_authorize("microsoft", user=_member(), redirect_after="")
    assert resp.status_code == 302
    query = _query(await _callback("microsoft"))
    assert "error" not in query and query["provider"] == "microsoft"
    info = await oauth.oauth_app_info("microsoft", user=_member())
    assert info.client_id == MSFT_ID


def test_gmail_connect_enabled_is_the_one_reader_of_the_flag() -> None:
    """The field and one reader. A second reader is how a flag stays on in
    one place while it is off in another. A file name such as this one's
    does not count, so the match needs no word character before it."""
    use = re.compile(r"(?<!\w)email_gmail_connect\b")
    hits = []
    for root in ("apps", "packages"):
        for path in (REPO / root).rglob("*.py"):
            if use.search(path.read_text(encoding="utf-8")):
                hits.append(path.relative_to(REPO).as_posix())
    assert sorted(hits) == [
        "apps/services/gateway/gateway/routes/email/transport/oauth.py",
        "packages/acb_common/acb_common/settings.py",
    ]


# ── The one list of mail-app names (O-GM-5) ────────────────────────────────


def test_every_name_that_oauth_app_reads_is_a_platform_name() -> None:
    """``oauth_app`` reads its names through ``env("…")``. No tenant route may
    write one, so each must be a platform name of ``env_guard`` (layer B).
    ``env_guard`` is the ONE list of the mail-app names: a second list in the
    email package could drift from it (review round 1)."""
    path = (
        REPO / "apps/services/email_ingestion/email_ingestion/providers"
        / "app_credentials.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "env"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert names == {
        "GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET",
        "MSFT_OAUTH_CLIENT_ID", "MSFT_OAUTH_CLIENT_SECRET",
        "AUTH_MICROSOFT_ENTRA_ID_ID", "AUTH_MICROSOFT_ENTRA_ID_SECRET",
    }
    for name in names:
        assert env_guard.is_platform_env(name), name
        assert env_guard.is_platform_env(name.lower()), "settings ignores case"
    # The settings fields that oauth_app reads first, and the dark flag.
    for field in (
        "gmail_oauth_client_id", "gmail_oauth_client_secret",
        "msft_oauth_client_id", "msft_oauth_client_secret", "email_gmail_connect",
    ):
        assert field in Settings.model_fields
        assert env_guard.is_platform_env(field), field
    # No second list of the names lives in the email package.
    src = path.read_text(encoding="utf-8")
    for second in ("MAIL_APP_ENV_PREFIXES", "is_mail_app_env", "GMAIL_OAUTH_\"", "startswith("):
        assert second not in src, second
