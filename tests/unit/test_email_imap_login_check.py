"""A new IMAP mailbox is saved only after a login works (multi-inbox UI, item 4).

Fence ``email-imap-login-before-save``: ``POST /email/accounts`` logs in to the
IMAP server first. A refused sign-in answers 422, a server that Metorite cannot
reach answers 502, and neither message carries the password. Nothing is saved
or encrypted in either case. The provider login is faked: no network is used.
"""
from __future__ import annotations

import imaplib
import logging

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.roles import UserContext, UserRole
from email_ingestion.providers.imap import IMAPProvider
from fastapi import HTTPException
from gateway.routes.email.transport import accounts

PASSWORD = "s3cret-pass-word"
CREDS = {
    "imap_host": "imap.contoso.test", "imap_port": 993,
    "imap_username": "u", "imap_password": PASSWORD,
    "smtp_host": "smtp.contoso.test", "smtp_port": 587,
}
USER = UserContext(
    email="m@contoso.test", role=UserRole.EMPLOYEE,
    organization_id="00000000-0000-0000-0000-0000000000a1")


class _Reached(Exception):
    """Raised by the fake key store: the login test passed and the route went on."""


@pytest.fixture()
def reach_store(monkeypatch):
    from acb_llm import key_store

    def _boom():
        raise _Reached()

    monkeypatch.setattr(key_store, "get_key_store", _boom)


def _login(monkeypatch, outcome):
    seen: list[dict] = []

    async def _verify(self, timeout=10.0):
        seen.append({"creds": dict(self.credentials), "timeout": timeout})
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(IMAPProvider, "verify_login", _verify)
    return seen


def _req():
    return accounts.CreateAccountRequest(
        provider="imap", email_address="m@contoso.test", credentials=dict(CREDS))


async def test_a_good_login_goes_on_to_save(monkeypatch, reach_store):
    seen = _login(monkeypatch, None)
    with pytest.raises(_Reached):
        await accounts.create_account(_req(), user=USER)
    assert seen[0]["timeout"] <= 15  # a short wait


async def test_a_bad_password_is_422_and_saves_nothing(monkeypatch, reach_store, caplog):
    _login(monkeypatch, imaplib.IMAP4.error("[AUTHENTICATIONFAILED] " + PASSWORD))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HTTPException) as err:
            await accounts.create_account(_req(), user=USER)
    assert err.value.status_code == 422
    assert err.value.detail == "The mail server refused the user name or password."
    assert PASSWORD not in str(err.value.detail)
    assert PASSWORD not in caplog.text


@pytest.mark.parametrize(
    "boom", [TimeoutError("timed out"), ConnectionRefusedError(), OSError("no route")])
async def test_an_unreachable_server_is_502(monkeypatch, reach_store, boom, caplog):
    _login(monkeypatch, boom)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HTTPException) as err:
            await accounts.create_account(_req(), user=USER)
    assert err.value.status_code == 502
    assert err.value.detail == "Metorite could not reach imap.contoso.test:993."
    assert PASSWORD not in caplog.text


async def test_the_provider_login_uses_a_short_timeout_and_restores_its_credentials(monkeypatch):
    calls: list[float] = []

    class _Conn:
        def logout(self):
            calls.append(-1)

    async def _connect(self):
        calls.append(self.credentials["imap_timeout"])
        return _Conn()

    monkeypatch.setattr(IMAPProvider, "_connect_imap", _connect)
    p = IMAPProvider(dict(CREDS))
    await p.verify_login(7.0)
    assert calls == [7.0, -1]
    assert "imap_timeout" not in p.credentials
