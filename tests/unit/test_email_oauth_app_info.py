"""EM-T3b — the public facts of the mail OAuth app.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.3, EM-T3b.

The guided page for admin approval builds the admin-consent link from the
client ID and the redirect URI. Both come from
``GET /email/oauth/{provider}/app``, so the browser holds no constant.

R7 fence ``email-app-info-no-secret``: the answer holds the client ID and the
redirect URI of settings, never the client secret. The route stays behind the
session.
"""
from __future__ import annotations

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import get_settings
from fastapi import HTTPException
from gateway.routes.email.transport import oauth

ORG = "11111111-2222-3333-4444-555555555555"
MEMBER = "dana@example.com"
CLIENT_ID = "app-info-client-id"
CLIENT_SECRET = "app-info-client-secret-never-sent"

_APP_ENV = (
    "MSFT_OAUTH_CLIENT_ID", "MSFT_OAUTH_CLIENT_SECRET",
    "AUTH_MICROSOFT_ENTRA_ID_ID", "AUTH_MICROSOFT_ENTRA_ID_SECRET",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "msft_oauth_client_id", CLIENT_ID, raising=False)
    monkeypatch.setattr(s, "msft_oauth_client_secret", CLIENT_SECRET, raising=False)
    for name in _APP_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.example.test")


def _member(org: str | None = ORG, email: str | None = MEMBER) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


async def test_the_answer_holds_the_client_id_and_the_redirect_uri() -> None:
    info = await oauth.oauth_app_info("microsoft", user=_member())
    assert info.client_id == CLIENT_ID
    assert info.redirect_uri == (
        "https://app.example.test/api/email/oauth/microsoft/callback"
    )
    # The same value the authorize leg sends, so the admin consent and the
    # member consent name one registered URI.
    assert info.redirect_uri == oauth._build_redirect_uri("microsoft")


async def test_the_answer_never_holds_the_secret() -> None:
    info = await oauth.oauth_app_info("microsoft", user=_member())
    dumped = info.model_dump_json()
    assert CLIENT_SECRET not in dumped
    assert set(info.model_dump()) == {"provider", "client_id", "redirect_uri"}


async def test_another_provider_is_a_404() -> None:
    for provider in ("gmail", "imap", "zoho"):
        with pytest.raises(HTTPException) as exc:
            await oauth.oauth_app_info(provider, user=_member())
        assert exc.value.status_code == 404


async def test_no_client_id_in_settings_is_a_503(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "msft_oauth_client_id", "", raising=False)
    with pytest.raises(HTTPException) as exc:
        await oauth.oauth_app_info("microsoft", user=_member())
    assert exc.value.status_code == 503


@pytest.mark.parametrize("user", [_member(org=None), _member(email=None)])
async def test_a_caller_with_no_org_or_no_email_is_refused(user) -> None:
    with pytest.raises(HTTPException) as exc:
        await oauth.oauth_app_info("microsoft", user=user)
    assert exc.value.status_code == 403


def test_the_route_is_not_public() -> None:
    import gateway.main as main

    assert "/email/oauth/{provider}/app" not in main.PUBLIC_ROUTES
