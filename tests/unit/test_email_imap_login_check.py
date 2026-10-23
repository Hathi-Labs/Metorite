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


class _NoDuplicate:
    """A session whose duplicate read finds nothing."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_a, **_k):
        class _R:
            def fetchone(self):
                return None

        return _R()


@pytest.fixture()
def reach_store(monkeypatch):
    from acb_llm import key_store
    from acb_common import public_address as pa

    def _boom():
        raise _Reached()

    monkeypatch.setattr(key_store, "get_key_store", _boom)
    monkeypatch.setattr(accounts, "_tenant_session", lambda *_a, **_k: _NoDuplicate())
    # The guard (Redis) and the DNS check are tested apart, below.
    monkeypatch.setattr(accounts, "_guard_imap_login", _free_guard)
    monkeypatch.setattr(pa, "resolve", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(pa, "is_local_address", lambda a: False)


async def _free_guard(org, member):
    async def _release():
        return None

    return _release


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


async def test_the_imap_timeout_a_member_sends_is_not_stored(monkeypatch):
    from acb_llm import key_store

    stored: list[str] = []

    class _Store:
        def encrypt(self, raw):
            stored.append(raw)
            raise _Reached()

    monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
    monkeypatch.setattr(accounts, "_tenant_session", lambda *_a, **_k: _NoDuplicate())
    monkeypatch.setattr(accounts, "_verify_imap_login", lambda *_a, **_k: _free_login())
    req = accounts.CreateAccountRequest(
        provider="imap", email_address="m@contoso.test",
        credentials={**CREDS, "imap_timeout": 9999})
    with pytest.raises(_Reached):
        await accounts.create_account(req, user=USER)
    assert "imap_timeout" not in stored[0]


async def _free_login():
    return None


async def test_a_duplicate_is_409_before_any_network_call(monkeypatch):
    class _Dup(_NoDuplicate):
        async def execute(self, *_a, **_k):
            class _R:
                def fetchone(self):
                    return (1,)

            return _R()

    def _no_network(*_a, **_k):
        raise AssertionError("the login test ran for a duplicate")

    monkeypatch.setattr(accounts, "_tenant_session", lambda *_a, **_k: _Dup())
    monkeypatch.setattr(accounts, "_verify_imap_login", _no_network)
    with pytest.raises(HTTPException) as err:
        await accounts.create_account(_req(), user=USER)
    assert err.value.status_code == 409


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "10.0.0.5", "::1"])
@pytest.mark.parametrize("field", ["imap_host", "smtp_host"])
async def test_a_private_host_is_422_and_no_login_runs(monkeypatch, reach_store, host, field):
    from acb_common import public_address as pa

    monkeypatch.setattr(pa, "resolve", lambda h, port: [host] if h == host else ["93.184.216.34"])

    def _no_login(*_a, **_k):
        raise AssertionError("the login ran against a private host")

    monkeypatch.setattr(IMAPProvider, "verify_login", _no_login)
    req = accounts.CreateAccountRequest(
        provider="imap", email_address="m@contoso.test", credentials={**CREDS, field: host})
    with pytest.raises(HTTPException) as err:
        await accounts.create_account(req, user=USER)
    assert err.value.status_code == 422
    assert PASSWORD not in str(err.value.detail)


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, int] = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and str(k) in self.data:
            return None
        self.data[str(k)] = 1
        return True

    async def incr(self, k, amount=1):
        self.data[str(k)] = self.data.get(str(k), 0) + amount
        return self.data[str(k)]

    async def expire(self, k, seconds):
        return True

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(str(k), None)


async def test_one_test_in_flight_and_five_attempts_in_ten_minutes(monkeypatch):
    from acb_common import tenant_redis

    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_k: fake)
    org = "00000000-0000-0000-0000-0000000000a1"
    release = await accounts._guard_imap_login(org, "m@contoso.test")
    with pytest.raises(HTTPException) as busy:  # a second one at once
        await accounts._guard_imap_login(org, "m@contoso.test")
    assert busy.value.status_code == 429
    await release()
    for _ in range(4):  # attempts 2 to 5 pass when each one ends
        await (await accounts._guard_imap_login(org, "m@contoso.test"))()
    with pytest.raises(HTTPException) as many:  # the sixth
        await accounts._guard_imap_login(org, "m@contoso.test")
    assert many.value.status_code == 429
    # another member is not slowed
    await (await accounts._guard_imap_login(org, "other@contoso.test"))()


async def test_a_redis_fault_refuses_the_test(monkeypatch):
    from acb_common import tenant_redis

    def _down(**_k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(tenant_redis, "get_tenant_redis", _down)
    with pytest.raises(HTTPException) as err:
        await accounts._guard_imap_login("00000000-0000-0000-0000-0000000000a1", "m@contoso.test")
    assert err.value.status_code == 503


async def test_the_provider_login_uses_a_short_timeout_and_restores_its_credentials(monkeypatch):
    calls: list[float] = []

    class _Conn:
        def logout(self):
            calls.append(-1)

    async def _connect(self, **kw):
        assert kw["executor"] is not None  # the small login pool
        assert kw["total_s"] == 7.0  # a wall-clock limit
        calls.append(self.credentials["imap_timeout"])
        return _Conn()

    monkeypatch.setattr(IMAPProvider, "_connect_imap", _connect)
    p = IMAPProvider(dict(CREDS))
    await p.verify_login(7.0)
    assert calls == [7.0, -1]
    assert "imap_timeout" not in p.credentials


# ── the provider connect: pinned address, wall-clock deadline, clamp ──────────


@pytest.mark.parametrize(
    "raw,want",
    [(None, 30.0), ("5", 30.0), (True, 30.0), (float("nan"), 30.0), (float("inf"), 30.0),
     (0, 5.0), (1, 5.0), (12, 12.0), (9999, 60.0), (-3, 5.0)])
def test_a_stored_imap_timeout_is_clamped(raw, want):
    from email_ingestion.providers.imap import _clamp_timeout

    assert _clamp_timeout(raw) == want


def _server(handler):
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def _run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    threading.Thread(target=_run, daemon=True).start()
    return srv


def _pin_to_loopback(monkeypatch, seen):
    from acb_common import public_address as pa

    def _resolve(host, port):
        seen.append(host)
        return ["127.0.0.1"]

    monkeypatch.setattr(pa, "resolve", _resolve)
    monkeypatch.setattr(pa, "address_refusal", lambda addresses, is_local=None: None)


async def test_a_drip_feed_server_is_cut_at_the_total_deadline(monkeypatch):
    import time

    def _drip(conn):
        try:
            while True:
                conn.sendall(b"*")  # one byte, never a full greeting line
                time.sleep(0.2)
        except OSError:
            pass

    srv = _server(_drip)
    _pin_to_loopback(monkeypatch, [])
    creds = {**CREDS, "imap_host": "mail.example.test", "imap_port": srv.getsockname()[1],
             "imap_use_ssl": False}
    started = time.monotonic()
    with pytest.raises(OSError):
        await IMAPProvider(creds).verify_login(1.0)
    assert time.monotonic() - started < 4.0  # the per-read timeout is 30 s
    srv.close()


async def test_the_connect_goes_to_the_checked_address_not_the_name(monkeypatch):
    def _bye(conn):
        conn.sendall(b"* OK ready\r\n")
        conn.recv(1024)
        conn.sendall(b"* BYE\r\n")
        conn.close()

    srv = _server(_bye)
    seen: list[str] = []
    _pin_to_loopback(monkeypatch, seen)
    creds = {**CREDS, "imap_host": "mail.example.test", "imap_port": srv.getsockname()[1],
             "imap_use_ssl": False}
    # "mail.example.test" does not resolve for real: only the pinned address works.
    with pytest.raises((imaplib.IMAP4.error, OSError)):
        await IMAPProvider(creds).verify_login(3.0)
    assert seen == ["mail.example.test"]  # resolved once, by the guard
    srv.close()


async def test_the_sync_connect_refuses_a_private_address(monkeypatch):
    from acb_common import public_address as pa

    monkeypatch.setattr(pa, "resolve", lambda h, p: ["169.254.169.254"])
    with pytest.raises(OSError):
        await IMAPProvider(dict(CREDS))._connect_imap()
