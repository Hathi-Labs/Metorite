"""The two records of a held card, on a REAL Redis (R8).

``email_app_master_plan.md`` §21.4: the gateway records the card's rows
(``held-card:<card id>``), and respond-input records the digest of the
member's answer (``reply-card:<thread>:<card id>``). Each is ``SET NX`` with
a TTL in the tenant Redis, and each is taken once with ``GETDEL``. The
hermetic half (``test_held_send_card_record.py``) runs these on a fake. This
half runs them on a real server, through the real ``TenantRedis`` wrapper
and the real pool.

R7, each with the mutation that turns it red:

* ``test_a_card_record_is_taken_once`` — read the card with GET.
* ``test_the_records_carry_the_tenant_and_a_ttl`` — drop the TTL, or build
  the key outside ``tenant_redis.key``.
* ``test_another_tenant_reads_no_card`` — key the record by the card id alone.

``HELD_R8_REDIS_URL`` names a PRIVATE Redis, because the suite runs FLUSHDB.
Never point it at a shared server. With it unset, the suite skips, and a
skip is not a pass. Run::

    docker run -d --rm --name held-r8-redis -p 127.0.0.1:16399:6379 redis:7-alpine
    HELD_R8_REDIS_URL=redis://127.0.0.1:16399/0 \\
        uv run pytest tests/unit/test_held_send_card_record_r8.py -v -rs
"""
from __future__ import annotations

import os

import pytest
from fastapi import BackgroundTasks, HTTPException

from tests.unit import _held_card_fakes as F

REDIS_URL_ENV = "HELD_R8_REDIS_URL"
URL = os.environ.get(REDIS_URL_ENV, "").strip()

pytestmark = pytest.mark.skipif(
    not URL, reason=f"{REDIS_URL_ENV} unset: R8 needs a PRIVATE real Redis. A skip is not a pass.")


@pytest.fixture()
def real_redis(monkeypatch):
    import redis
    from acb_common import get_settings, tenant_redis

    client = redis.Redis.from_url(URL, decode_responses=True)
    client.flushdb()
    monkeypatch.setattr(get_settings(), "redis_url", URL, raising=False)
    tenant_redis.reset_pool_for_tests()
    monkeypatch.setattr(tenant_redis, "_POOL", None)
    yield client
    client.flushdb()
    client.close()
    tenant_redis.reset_pool_for_tests()


@pytest.fixture()
def world(monkeypatch, real_redis):
    from gateway.routes.email.automation import held_card as hc

    F.flag_on(monkeypatch)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    F.install_db(monkeypatch)
    sends = F.install_sends(monkeypatch)
    return hc, sends, real_redis


async def _card(hc, user=None):
    return await hc.held_card(
        hc.HeldCardRequest(kind="draft", account_id=F.BOX, draft_id=F.DRAFT), user or F.user())


async def _act(hc, card_id: str, answer: str, user=None):
    return await hc.held_card_act(
        hc.HeldCardActRequest(card_id=card_id, card_thread_id="chat-thread-1", answer=answer),
        BackgroundTasks(), user or F.user())


async def test_the_records_carry_the_tenant_and_a_ttl(world) -> None:
    hc, _sends, client = world
    card = await _card(hc)
    answer = F.rows_answer("to-0")
    assert await F.record_answer(card["card_id"], answer)
    keys = sorted(client.keys("*"))
    assert keys == sorted([
        f"cc:{F.ORG}:held-card:{card['card_id']}",
        f"cc:{F.ORG}:reply-card:chat-thread-1:{card['card_id']}",
    ])
    for k in keys:
        assert 0 < client.ttl(k) <= hc.RECORD_TTL_S


async def test_a_card_record_is_taken_once(world) -> None:
    hc, sends, client = world
    card = await _card(hc)
    answer = F.rows_answer("to-0")
    await F.record_answer(card["card_id"], answer)
    await _act(hc, card["card_id"], answer)
    assert client.keys("*") == []
    await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException) as e:
        await _act(hc, card["card_id"], answer)
    assert e.value.status_code == 409
    assert len(sends.sent) == 1


async def test_another_tenant_reads_no_card(world) -> None:
    hc, sends, _client = world
    card = await _card(hc)
    other = F.user(org="org-held-2")
    answer = F.rows_answer("to-0")
    from acb_skills.reply_card import record_reply_answer

    await record_reply_answer("org-held-2", card["card_id"], F.MEMBER, answer, "chat-thread-1")
    with pytest.raises(HTTPException) as e:
        await _act(hc, card["card_id"], answer, user=other)
    assert e.value.status_code == 409
    assert sends.sent == []
