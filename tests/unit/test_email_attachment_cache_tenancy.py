"""WS-17 EM-T2b — the attachment cache goes through ``tenant_redis``.

Before EM-T2b, ``download_attachment`` opened a raw redis-py client per call
and wrote the key ``email:att:cache:{attachment_id}``. That key carries no
tenant. Two organizations that hold the same attachment id share one entry.

These tests drive the real handler with a fake database session, a fake
provider session and a fake Redis behind the REAL ``TenantRedis`` wrapper. So
the key that reaches Redis is the key the wrapper builds, not a key the test
wrote. They pin the five done-when lines of ``email_app_master_plan.md``
§10.4.5 EM-T2b.

The last test reads and writes a real Redis when one answers on
``REDIS_URL`` (default ``redis://localhost:6379/0``). It skips when none does.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis as tr
from acb_common.tenant_redis import TenantRedis
from gateway.routes.email.transport import attachments as m

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
ATT_ID = "33333333-3333-4333-8333-333333333333"

#: Not valid UTF-8 on purpose: 0xff and 0xfe never start a UTF-8 sequence, and
#: 0x80 is a lone continuation byte. A decoding client raises on this value.
NON_UTF8 = b"%PDF-\xff\xfe\x00\x80binary\xc3"


@pytest.fixture(autouse=True)
def _unbound():
    """Every test starts with NO tenant bound, and ends with none bound."""
    token = tr._ORGANIZATION_ID.set(None)
    try:
        yield
    finally:
        tr._ORGANIZATION_ID.reset(token)


class _BytesRedis:
    """A redis-py stand-in that stores raw bytes and records every call."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []

    async def get(self, name: str) -> bytes | None:
        self.calls.append(("get", name))
        return self.store.get(name)

    async def setex(self, name: str, seconds: int, value: bytes) -> bool:
        self.calls.append(("setex", name))
        assert seconds == m.ATTACHMENT_CACHE_TTL_SECS
        self.store[name] = value
        return True


class _Harness:
    """Patches the three seams of ``download_attachment`` for one test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
        self.redis = _BytesRedis()
        self.payload = payload
        self.provider_calls = 0
        self.binary_flags: list[bool] = []

        row = SimpleNamespace(
            id=ATT_ID, filename="report.pdf", mime_type="application/pdf",
            size_bytes=len(payload), provider_attachment_id="prov-att",
            storage_path=None, provider_message_id="prov-msg",
            account_id="acct-1",
        )

        class _Db:
            async def execute(self, *_a: Any, **_k: Any) -> Any:
                return SimpleNamespace(fetchone=lambda: row)

        @asynccontextmanager
        async def _tenant_session():
            yield _Db()

        harness = self

        class _Provider:
            async def get_attachment(self, _msg: str, _att: str) -> bytes:
                harness.provider_calls += 1
                return harness.payload

        @asynccontextmanager
        async def _provider_session(*_a: Any, **_k: Any):
            yield SimpleNamespace(provider=_Provider())

        def _get_tenant_redis(*, binary: bool = False) -> TenantRedis:
            harness.binary_flags.append(binary)
            return TenantRedis(harness.redis)

        monkeypatch.setattr(m, "_tenant_session", _tenant_session)
        monkeypatch.setattr(m, "provider_session", _provider_session)
        monkeypatch.setattr(m, "get_tenant_redis", _get_tenant_redis)


def _user(org: str | None) -> UserContext:
    return UserContext(
        email="member@example.com", role=UserRole.EMPLOYEE, organization_id=org,
    )


async def _download(org: str | None) -> tuple[str, bytes]:
    resp = await m.download_attachment(ATT_ID, _user(org))
    body = b""
    async for chunk in resp.body_iterator:
        body += chunk if isinstance(chunk, bytes) else chunk.encode()
    return resp.headers["X-Cache"], body


async def test_org_a_entry_is_a_miss_for_org_b(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness(monkeypatch, b"bytes of org A")

    assert await _download(ORG_A) == ("MISS", b"bytes of org A")
    assert await _download(ORG_A) == ("HIT", b"bytes of org A")
    assert h.provider_calls == 1

    # Same attachment id, other organization: the entry of org A is not visible.
    h.payload = b"bytes of org B"
    assert await _download(ORG_B) == ("MISS", b"bytes of org B")
    assert h.provider_calls == 2
    assert len(h.redis.store) == 2, "one entry per organization, never a shared one"


async def test_written_key_carries_the_organization_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _Harness(monkeypatch, b"x")
    await _download(ORG_A)

    written = [name for op, name in h.redis.calls if op == "setex"]
    assert written == [f"cc:{ORG_A}:email-att:{ATT_ID}"]
    assert all(name.startswith(f"cc:{ORG_A}:email-att:") for _, name in h.redis.calls)
    assert not any("email:att:cache" in name for _, name in h.redis.calls)
    # The handler asks for the binary pool, every time.
    assert h.binary_flags and all(h.binary_flags)
    # The binding ends with the request: nothing leaks to the next one.
    assert not tr.organization_bound()


async def test_non_utf8_bytes_come_back_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnicodeDecodeError):
        NON_UTF8.decode("utf-8")
    h = _Harness(monkeypatch, NON_UTF8)

    assert await _download(ORG_A) == ("MISS", NON_UTF8)
    assert await _download(ORG_A) == ("HIT", NON_UTF8)
    assert h.provider_calls == 1


async def test_no_organization_means_no_redis_call(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness(monkeypatch, b"provider bytes")

    assert await _download(None) == ("MISS", b"provider bytes")
    assert await _download(None) == ("MISS", b"provider bytes")
    assert h.provider_calls == 2
    assert h.redis.calls == []
    assert h.binary_flags == [], "no Redis client is even requested"


async def test_a_redis_failure_falls_back_to_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _Harness(monkeypatch, b"provider bytes")

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise ConnectionError("redis down")

    h.redis.get = _boom  # type: ignore[method-assign]
    assert await _download(ORG_A) == ("MISS", b"provider bytes")
    # A failed read turns the cache off for this request: no write either.
    assert [op for op, _ in h.redis.calls if op == "setex"] == []


def test_the_handler_no_longer_reaches_redis_by_hand() -> None:
    import inspect

    from gateway.routes.email import core

    src = inspect.getsource(m)
    assert "email:att:cache" not in src
    assert "_get_redis" not in src
    assert not hasattr(core, "_get_redis")
    handler = inspect.getsource(m.download_attachment)
    assert "organization_scope(org_id)" in handler
    assert 'key("email-att", attachment_id)' in handler


# ---------------------------------------------------------------------------
# A real Redis, when one answers. The fake above stores what it is handed, so
# only a real server proves that the binary pool does not decode the reply.
# ---------------------------------------------------------------------------

async def test_binary_pool_round_trips_non_utf8_on_a_real_redis(monkeypatch) -> None:
    import redis.asyncio as aioredis

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    probe = aioredis.from_url(url, socket_connect_timeout=0.5)
    try:
        await probe.ping()
    except Exception:
        pytest.skip(f"no Redis answers on {url}")
    finally:
        await probe.aclose()

    monkeypatch.setattr(tr.get_settings(), "redis_url", url, raising=False)
    tr.reset_pool_for_tests()
    org = f"t2b-{uuid.uuid4().hex[:12]}"
    try:
        with tr.organization_scope(org):
            client = tr.get_tenant_redis(binary=True)
            k = tr.key("email-att", ATT_ID)
            await client.setex(k, 30, NON_UTF8)
            try:
                assert await client.get(k) == NON_UTF8
            finally:
                await client.delete(k)
    finally:
        pool = tr._BINARY_POOL
        tr.reset_pool_for_tests()
        if pool is not None:
            await pool.aclose()
