"""R8 for the email working set (navigation_shell.md §7.1.1): a REAL Redis.

The hermetic suite (``test_email_working_set.py``) fakes the client. This one
proves the key shape, the tenant prefix, the TTL and the member split on a
real server. ``HELD_R8_REDIS_URL`` names a PRIVATE Redis, because the suite
runs FLUSHDB. With it unset the suite skips, and a skip is not a pass::

    docker run -d --rm --name held-r8-redis -p 127.0.0.1:16399:6379 redis:7-alpine
    HELD_R8_REDIS_URL=redis://127.0.0.1:16399/0 \\
        uv run pytest tests/unit/test_email_working_set_r8.py -v -rs
"""
from __future__ import annotations

import os

import pytest

REDIS_URL_ENV = "HELD_R8_REDIS_URL"
URL = os.environ.get(REDIS_URL_ENV, "").strip()

pytestmark = pytest.mark.skipif(
    not URL, reason=f"{REDIS_URL_ENV} unset: R8 needs a PRIVATE real Redis. A skip is not a pass.")

ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"


@pytest.fixture()
def real_redis(monkeypatch):
    import redis
    from acb_common import get_settings, tenant_redis

    from acb_skills import email_chat_context as ecc

    client = redis.Redis.from_url(URL, decode_responses=True)
    client.flushdb()
    monkeypatch.setattr(get_settings(), "redis_url", URL, raising=False)
    tenant_redis.reset_pool_for_tests()
    monkeypatch.setattr(tenant_redis, "_POOL", None)
    # The suite-wide fake store (`_egress_mark_store`) swaps the client; this
    # suite needs the real tenant client on the real server.
    from acb_common.tenant_redis import get_tenant_redis as _real_client
    monkeypatch.setattr(ecc, "get_tenant_redis", _real_client)
    ecc.clear_cache()
    yield client, ecc
    ecc.clear_cache()
    client.flushdb()
    client.close()
    tenant_redis.reset_pool_for_tests()


def _bind(monkeypatch, org: str) -> None:
    import acb_common.db as db

    monkeypatch.setattr(db, "current_tenant", lambda: org)


def _ctx(member: str, session: str = "sess-1") -> dict[str, str]:
    return {"session_id": session, "member": member}


async def test_the_set_lives_under_the_tenant_prefix_with_a_ttl(monkeypatch, real_redis) -> None:
    client, ecc = real_redis
    _bind(monkeypatch, ORG_A)
    assert await ecc.save_working_set({"account_id": "acc-1", "draft_id": "d-1"}, _ctx("vj@x.test"))
    keys = client.keys("*")
    assert len(keys) == 1
    assert keys[0].startswith(f"cc:{ORG_A}:")
    assert "vj@x.test" not in keys[0], "the key holds a hash, never the address"
    assert 0 < client.ttl(keys[0]) <= ecc.TTL_S
    ecc.clear_cache()  # read from Redis, not the process cache
    assert (await ecc.load_working_set(_ctx("vj@x.test")))["draft_id"] == "d-1"


async def test_another_member_or_org_reads_nothing(monkeypatch, real_redis) -> None:
    _client, ecc = real_redis
    _bind(monkeypatch, ORG_A)
    await ecc.save_working_set({"account_id": "acc-1", "draft_id": "d-1"}, _ctx("a@x.test"))
    ecc.clear_cache()
    assert await ecc.load_working_set(_ctx("b@x.test")) == {}
    _bind(monkeypatch, ORG_B)
    assert await ecc.load_working_set(_ctx("a@x.test")) == {}
