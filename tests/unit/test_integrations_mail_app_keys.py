"""WS-17 EM-G7, O-GM-5 — no Integrations write can set a key of a mail app.

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.2 (O-GM-5, decided
by the orchestrator on 2026-10-05) and §12.3.9.

``POST /integrations/configure``, ``PUT /integrations/keys`` and
``DELETE /integrations/keys`` write ``os.environ`` and the env file of the box
for the whole deployment, and ``oauth_app`` reads the mail-app credentials
from that environment. So one write by one member could aim the Gmail or
Microsoft connect of EVERY organization at an OAuth client of their own.

**One refusal, one list (review round 1).** The security fix #633 put the
rule in ``acb_common/env_guard.py``: layer B (``is_platform_env``) refuses
each ``GMAIL_OAUTH_*``, ``MSFT_OAUTH_*`` and ``AUTH_MICROSOFT_ENTRA_ID_*``
name with 403 on every route. EM-G7 adds no second list. This file pins that
the mail keys take that path, and
``tests/unit/test_integrations_env_hardening.py`` pins the guard itself.

R7 fences named here:

* ``test_configure_refuses_each_mail_app_key`` (M9) and
  ``test_configure_refuses_a_mixed_request_whole``: 403 from layer B, and no
  store row, no environment variable and no file line.
* ``test_put_refuses_a_mail_app_key`` (M8): each case reaches layer B. The
  request names a service and a key that resolve, so the 403 is the refusal
  and never a 400 for an unknown service.
* ``test_delete_refuses_a_mail_app_key``: the pop would unset the mail app of
  every organization.
* ``test_the_gmail_oauth_service_is_gone``: a write that names it is a 400
  for an unknown service, and it writes nothing. The id stays reserved.
* ``test_another_key_still_works_on_each_route``: the write of a tenant key
  does not change.

All of it is hermetic: the key store is a fake, and the env file lives in a
temporary folder.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import env_guard
from fastapi import HTTPException
from gateway.routes import integrations

ORG = "11111111-2222-3333-4444-555555555555"

#: Each mail-app name, with the value the operator set on the box.
MAIL_KEYS = (
    "GMAIL_OAUTH_CLIENT_ID",
    "GMAIL_OAUTH_CLIENT_SECRET",
    "MSFT_OAUTH_CLIENT_ID",
    "MSFT_OAUTH_CLIENT_SECRET",
    "AUTH_MICROSOFT_ENTRA_ID_ID",
    "AUTH_MICROSOFT_ENTRA_ID_SECRET",
    "AUTH_MICROSOFT_ENTRA_ID_TENANT",
)
OTHER_KEY = "APOLLO_API_KEY"


class _Store:
    """The encrypted key store, as a record of each call."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    async def put(self, provider: str, value: str, **_kw) -> None:
        self.puts.append((provider, value))

    async def delete(self, provider: str, **_kw) -> None:
        self.deletes.append(provider)


@pytest.fixture()
def box(monkeypatch, tmp_path) -> SimpleNamespace:
    """A box whose operator set every mail-app key, in env and in the file."""
    from acb_common.settings import get_settings
    from acb_llm import key_store

    store = _Store()
    monkeypatch.setattr(key_store, "get_key_store", lambda: store)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{k}=operator-{k.lower()}\n" for k in MAIL_KEYS)
        + f"{OTHER_KEY}=before\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(integrations, "_find_env_file", lambda: env_file)
    # The routes bust the settings cache. Keep the cache of the test run.
    monkeypatch.setattr(get_settings, "cache_clear", lambda: None)
    for name in MAIL_KEYS:
        monkeypatch.setenv(name, f"operator-{name.lower()}")
    # setenv first, so monkeypatch restores the variable that a route sets.
    monkeypatch.setenv(OTHER_KEY, "before")
    return SimpleNamespace(
        store=store, env_file=env_file,
        file_before=env_file.read_text(encoding="utf-8"),
    )


def _executive() -> UserContext:
    return UserContext(
        email="exec@example.com", role=UserRole.EXECUTIVE, organization_id=ORG,
    )


def _assert_untouched(box: SimpleNamespace) -> None:
    import os

    for name in MAIL_KEYS:
        assert os.environ.get(name) == f"operator-{name.lower()}", name
    assert os.environ.get(OTHER_KEY) == "before"
    assert box.env_file.read_text(encoding="utf-8") == box.file_before
    assert box.store.puts == [] and box.store.deletes == []


def _assert_refused_by_layer_b(exc: pytest.ExceptionInfo, key: str) -> None:
    """The 403 of ``_refuse_unsafe_env_writes``, and it names the key."""
    assert exc.value.status_code == 403, exc.value.detail
    assert "platform settings of the deployment" in exc.value.detail
    assert key.upper() in exc.value.detail


# ── POST /integrations/configure ───────────────────────────────────────────


@pytest.mark.parametrize("key", [*MAIL_KEYS, "gmail_oauth_client_id"])
async def test_configure_refuses_each_mail_app_key(box, key) -> None:
    """Settings ignores case, so the lower-case spelling is refused too."""
    req = integrations.ConfigureRequest(
        vars=[integrations.IntegrationVar(key=key, value="attacker-client")],
    )
    with pytest.raises(HTTPException) as exc:
        await integrations.configure_integrations(req, user=_executive())
    _assert_refused_by_layer_b(exc, key)
    _assert_untouched(box)


async def test_configure_refuses_a_mixed_request_whole(box) -> None:
    """One mail-app key refuses the request. The other key is not written."""
    req = integrations.ConfigureRequest(vars=[
        integrations.IntegrationVar(key=OTHER_KEY, value="after"),
        integrations.IntegrationVar(key="GMAIL_OAUTH_CLIENT_SECRET", value="x"),
    ])
    with pytest.raises(HTTPException) as exc:
        await integrations.configure_integrations(req, user=_executive())
    _assert_refused_by_layer_b(exc, "GMAIL_OAUTH_CLIENT_SECRET")
    _assert_untouched(box)


# ── PUT /integrations/keys ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("service", "key_name", "env_var"),
    [
        ("microsoft-oauth", "msft_oauth_client_id", "MSFT_OAUTH_CLIENT_ID"),
        ("microsoft-oauth", "msft_oauth_client_secret", "MSFT_OAUTH_CLIENT_SECRET"),
    ],
)
async def test_put_refuses_a_mail_app_key(box, service, key_name, env_var) -> None:
    """Each case names a service and a key that resolve, so the request
    reaches the refusal. A 400 for an unknown service would prove nothing."""
    assert integrations._guide_env_var(service, key_name) == env_var
    req = integrations.IntegrationKeyRequest(
        service=service, key_name=key_name, value="attacker-client",
    )
    with pytest.raises(HTTPException) as exc:
        await integrations.put_integration_key(req, user=_executive())
    _assert_refused_by_layer_b(exc, env_var)
    _assert_untouched(box)


# ── DELETE /integrations/keys ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("service", "key_name", "env_var"),
    [
        ("microsoft-oauth", "msft_oauth_client_id", "MSFT_OAUTH_CLIENT_ID"),
        ("microsoft-oauth", "msft_oauth_client_secret", "MSFT_OAUTH_CLIENT_SECRET"),
    ],
)
async def test_delete_refuses_a_mail_app_key(box, service, key_name, env_var) -> None:
    assert integrations._guide_env_var(service, key_name) == env_var
    req = integrations.IntegrationKeyDelete(service=service, key_name=key_name)
    with pytest.raises(HTTPException) as exc:
        await integrations.delete_integration_key(req, user=_executive())
    _assert_refused_by_layer_b(exc, env_var)
    _assert_untouched(box)


# ── The gmail-oauth service is gone ────────────────────────────────────────


async def test_the_gmail_oauth_service_is_gone(box) -> None:
    """No guide offers it, so put and delete answer 400 for an unknown
    service and write nothing. Its id stays reserved, so a custom
    integration cannot take it."""
    assert "gmail-oauth" not in integrations._SETUP_GUIDES
    assert "gmail-oauth" not in integrations._GUIDE_CATEGORIES
    assert "gmail-oauth" in integrations.RESERVED_SERVICE_IDS
    for key_name in ("gmail_oauth_client_id", "gmail_oauth_client_secret"):
        put = integrations.IntegrationKeyRequest(
            service="gmail-oauth", key_name=key_name, value="attacker-client",
        )
        with pytest.raises(HTTPException) as exc:
            await integrations.put_integration_key(put, user=_executive())
        assert exc.value.status_code == 400
        assert "Unknown service" in exc.value.detail
        gone = integrations.IntegrationKeyDelete(service="gmail-oauth", key_name=key_name)
        with pytest.raises(HTTPException) as exc:
            await integrations.delete_integration_key(gone, user=_executive())
        assert exc.value.status_code == 400
    _assert_untouched(box)


def test_no_mail_app_key_is_a_tenant_key() -> None:
    """The allowlist of layer C holds no mail-app key, so no form offers one.
    The status route moves each guide key outside it to the read-only
    ``operator_env_vars``."""
    hit = sorted(k for k in integrations.BUILTIN_ENV_KEYS if env_guard.is_platform_env(k))
    assert hit == []
    for key in MAIL_KEYS:
        assert key not in integrations.BUILTIN_ENV_KEYS, key
        assert env_guard.is_platform_env(key), key


# ── What must keep working ─────────────────────────────────────────────────


async def test_another_key_still_works_on_each_route(box) -> None:
    import os

    assert OTHER_KEY in integrations.BUILTIN_ENV_KEYS
    req = integrations.ConfigureRequest(
        vars=[integrations.IntegrationVar(key=OTHER_KEY, value="configured")],
    )
    out = await integrations.configure_integrations(req, user=_executive())
    assert out["written"] == [OTHER_KEY]
    assert os.environ[OTHER_KEY] == "configured"
    assert f"{OTHER_KEY}=configured" in box.env_file.read_text(encoding="utf-8")
    assert box.store.puts[-1] == ("apollo:api_key", "configured")

    put = integrations.IntegrationKeyRequest(
        service="apollo", key_name="api_key", value="put",
    )
    out = await integrations.put_integration_key(put, user=_executive())
    assert out["ok"] is True and out["env_var"] == OTHER_KEY
    assert os.environ[OTHER_KEY] == "put"
    assert f"{OTHER_KEY}=put" in box.env_file.read_text(encoding="utf-8")

    gone = integrations.IntegrationKeyDelete(service="apollo", key_name="api_key")
    out = await integrations.delete_integration_key(gone, user=_executive())
    assert out["deleted"] is True
    assert box.store.deletes == ["apollo:api_key"]
    assert OTHER_KEY not in os.environ

    # The mail apps of the box are unchanged through all three writes.
    for name in MAIL_KEYS:
        assert os.environ.get(name) == f"operator-{name.lower()}", name
