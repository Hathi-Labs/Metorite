"""H-391 — the record of an email act from WhatsApp, on a REAL database (R8).

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §18.

The REAL ``acts.park_code``, ``acts.offered`` and ``acts.settle`` SQL run
against the phase-4 catalog of ``test_h3_rls_promotion_rehearsal``, as its
NOSUPERUSER NOBYPASSRLS role. The record of the answer runs on a PRIVATE real
Redis, under the real tenant wrapper (``reply_card.record_reply_answer`` and
``take_reply_answer``). Nothing else is faked, and no mail is sent.

R7 fences named here:

* ``h391-code-migration``: the migration adds one nullable column with no
  default and a shape CHECK, and a second run changes nothing.
* ``h391-code-record``: the row holds the hash of the code, never the code.
  The code lives 15 minutes after its summary went out. The right code
  claims the act once, a wrong code leaves it, and another org reads nothing.
* ``h391-answer-record``: the answer that the approval records is the
  answer that the act route takes, once, for the same member and thread.

Run (real Postgres and a private Redis)::

    eval "$(bash scripts/dev_db.sh --export)"
    docker run -d --rm --name held-r8-redis -p 127.0.0.1:16399:6379 redis:7-alpine
    HELD_R8_REDIS_URL=redis://127.0.0.1:16399/0 \\
        uv run pytest tests/unit/test_wac_email_confirm_r8.py -v -rs
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted``, ``app_engine`` and ``granted`` are fixtures used by name, so
# the imports are load-bearing even though ruff reads them as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_wac_bot_run_r8 import (  # noqa: F401
    _email,
    _phone,
    _seed_link,
    granted,
)
from tests.unit.test_wac_writes_r8 import _bound, _req, _seed_thread

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_TABLE = "whatsapp_pending_acts"
_COLUMN = "confirm_code_hash"
REDIS_URL_ENV = "HELD_R8_REDIS_URL"
REDIS_URL = os.environ.get(REDIS_URL_ENV, "").strip()


def _migration() -> Path:
    """The H-391 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((_ROOT / "infra" / "postgres").glob("*.sql"))
        if f"ADD COLUMN IF NOT EXISTS {_COLUMN}" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one H-391 migration, found {hits}"
    return hits[0]


def _catalog(admin_engine) -> list:
    with admin_engine.connect() as c:
        return [
            c.execute(text(
                "SELECT column_name, is_nullable, column_default FROM "
                "information_schema.columns WHERE table_name = :t AND "
                "column_name = :c"), {"t": _TABLE, "c": _COLUMN}).all(),
            c.execute(text(
                "SELECT conname FROM pg_constraint WHERE conrelid = "
                f"'{_TABLE}'::regclass AND conname = "
                "'whatsapp_pending_acts_code_hash_shape'")).all(),
            c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{_TABLE}'::regclass")).one(),
        ]


def _replay(admin_engine) -> None:
    with admin_engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(_migration().read_text(encoding="utf-8"))
        c.connection.dbapi_connection.commit()


def test_the_migration_adds_one_nullable_column_and_a_second_run_changes_nothing(
    granted,  # noqa: F811
) -> None:
    before = _catalog(granted.admin_engine)
    columns, checks, rls = before
    assert [tuple(c) for c in columns] == [(_COLUMN, "YES", None)]
    assert [tuple(c) for c in checks] == [("whatsapp_pending_acts_code_hash_shape",)]
    assert tuple(rls) == (True, True)
    _replay(granted.admin_engine)
    assert _catalog(granted.admin_engine) == before


def _act() -> Any:
    from acb_skills.whatsapp_acts import ParkedAct, email_card_text

    rows = [{"id": "r1", "label": "To: kim@contoso.test", "kind": "to",
             "class": "thread", "checked": True},
            {"id": "r2", "label": "Cc: x@new.example", "kind": "cc",
             "class": "new", "checked": False}]
    return ParkedAct(tool="send_draft", arguments={"account_id": "a", "draft_id": "d"},
                     card=email_card_text("Send this draft?", "", "", rows, None),
                     title="Send this draft?", detail="", context="", kind="code",
                     rows=rows)


async def test_the_code_record_holds_on_the_real_table(granted) -> None:  # noqa: F811
    from gateway.routes.whatsapp_channel import acts

    p = granted
    email, phone = _email(), _phone()
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    sid = _seed_thread(p.admin_engine, org=p.org_a, email=email)
    req = _req(p.org_a, email, phone, sid)

    async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
        act_id, code = await _bound(p.org_a, lambda: acts.park_code(req, _act()))
        # Not offered yet: the code approves nothing.
        assert await _bound(p.org_a, lambda: acts.settle(req, f"YES {code}")) is None
        act_id, code = await _bound(p.org_a, lambda: acts.park_code(req, _act()))
        await _bound(p.org_a, lambda: acts.offered(act_id, "wamid.out.summary"))
        with p.admin_engine.connect() as c:
            stored, ttl = c.execute(text(
                f"SELECT {_COLUMN}, extract(epoch FROM expires_at - offered_at) "
                f"FROM {_TABLE} WHERE id = CAST(:i AS uuid)"), {"i": act_id}).one()
        assert stored == acts.code_hash(code) and code not in stored
        assert 899 <= float(ttl) <= 901
        # Another org reads no act.
        req_b = _req(p.org_b, email, phone, sid)
        assert await _bound(p.org_b, lambda: acts._live_act(req_b)) is None
        # A wrong code leaves the act. The right one claims it, once.
        wrong = "ZZZZ" if code != "ZZZZ" else "YYYY"
        assert await _bound(p.org_a, lambda: acts.settle(req, f"YES {wrong}")) \
            == acts.REPLY_WRONG_CODE
        out = await _bound(p.org_a, lambda: acts.settle(req, f"YES {code} WITH 2"))
        assert isinstance(out, acts.Approved) and out.kind == "email"
        assert out.include == frozenset({2})
        assert await _bound(p.org_a, lambda: acts.settle(req, f"YES {code}")) \
            == acts.REPLY_NO_ACT
        await _bound(p.org_a, lambda: acts.finish(out, True))

    with p.admin_engine.connect() as c:
        state = c.execute(text(f"SELECT state FROM {_TABLE} WHERE id = CAST(:i AS uuid)"),
                          {"i": act_id}).scalar_one()
    assert state == "done"


def test_the_check_refuses_a_value_that_is_not_a_hash(granted) -> None:  # noqa: F811
    p = granted
    email, phone = _email(), _phone()
    _seed_link(p.admin_engine, org=p.org_a, email=email, phone=phone)
    sid = _seed_thread(p.admin_engine, org=p.org_a, email=email)
    with p.admin_engine.connect() as c:
        link = str(c.execute(text(
            "SELECT id FROM whatsapp_member_links WHERE wa_id = :w"),
            {"w": phone}).scalar_one())
    with pytest.raises(Exception, match="code_hash_shape"), p.admin_engine.begin() as c:
        c.execute(text(
            f"INSERT INTO {_TABLE} (organization_id, member_email, wa_id, "
            "chat_session_id, link_id, tool_name, arguments, card_text, "
            f"{_COLUMN}, expires_at) VALUES (CAST(:o AS uuid), :e, :w, :s, "
            "CAST(:l AS uuid), 'send_draft', '{}'::jsonb, '[]', '7K3Q', "
            "now() + interval '15 minutes')"),
            {"o": p.org_a, "e": email, "w": phone, "s": sid, "l": link})


@pytest.mark.skipif(not REDIS_URL, reason=f"{REDIS_URL_ENV} unset: the record needs a "
                    "PRIVATE real Redis. A skip is not a pass.")
async def test_the_recorded_answer_is_the_answer_the_act_route_takes(monkeypatch) -> None:
    import redis.asyncio as aioredis
    from acb_common.tenant_redis import TenantRedis
    from acb_skills import reply_card as rcard
    from acb_skills import whatsapp_acts as wacts

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    monkeypatch.setattr(rcard, "_tenant_redis", lambda: TenantRedis(client))
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_held_card_send", True)
    org, member, thread = str(uuid.uuid4()), "jane@acme.test", "wa-th-r8"
    rid = uuid.uuid4().hex
    act = _act()
    state = wacts.EmailApproval(card=act.card, without=frozenset(),
                                include=frozenset({2}), org=org, member=member,
                                thread=thread)
    answer = await wacts._email_yes(state, act.rows, None, rid, True)
    assert answer is not None and answer.ticked == frozenset({"r1", "r2"})
    # The act route takes it once, for this member and this thread only.
    assert not await rcard.take_reply_answer(org, rid, "eve@acme.test", answer.answer,
                                             thread, wait_s=0)
    answer = await wacts._email_yes(
        wacts.EmailApproval(card=act.card, without=frozenset(), include=frozenset({2}),
                            org=org, member=member, thread=thread),
        act.rows, None, uuid.uuid4().hex, True)
    assert await rcard.take_reply_answer(org, answer.request_id, member, answer.answer,
                                         thread, wait_s=0)
    assert not await rcard.take_reply_answer(org, answer.request_id, member,
                                             answer.answer, thread, wait_s=0)
    await client.aclose()
