"""The SQL of the recipient intent fixes, on a real database (R8, FORCE RLS).

The owner's request of 2026-10-24 (``email_app_master_plan.md`` §21.12)
added five statements. This file runs each one as a non-privileged role, on
a phase-4-promoted catalog, in the world of ``test_email_contact_suggest_r8``:

* ``held_card.THREAD_PARTICIPANTS_SQL`` — each To and Cc of a RECEIVED mail
  of the thread. Mutation: drop the folder filter → the Sent mail's Cc shows.
* ``held_card.ORG_DOMAINS_SQL`` — the organization's domains, never another
  tenant's.
* ``reply_card.USUAL_CC_SQL`` — the Cc of the member's own Sent mail to a
  domain, counted. Mutation: drop the From check → a planted mail counts.
* ``reply_card.RECENT_REPLY_SQL`` and ``reply_card.THREAD_DRAFT_PEOPLE_SQL``.

Run (real Postgres)::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_recipient_intent_r8.py -v -rs
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from gateway.routes.email.automation import held_card as hc
from gateway.routes.email.automation import reply_card as rc
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_contact_suggest_r8 import _assert_non_priv, _World

# ``promoted`` and ``app_engine`` are used by name for fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


def _mail(c, w: _World, *, folder: str, frm: str, to: list[str], cc: list[str],
          thread: str, when: datetime, bcc: list[str] | None = None, tag: str) -> None:
    c.execute(text(
        "INSERT INTO email_messages (account_id, provider_message_id, folder, thread_id, "
        "from_address, to_addresses, cc_addresses, bcc_addresses, subject, received_at, "
        "organization_id) VALUES (CAST(:a AS uuid), :pm, :f, :t, CAST(:fr AS jsonb), "
        "CAST(:to AS jsonb), CAST(:cc AS jsonb), CAST(:bcc AS jsonb), 's', :r, "
        "CAST(:o AS uuid))"),
        {"a": w.account, "pm": f"pm-{tag}-{w.tag}", "f": folder, "t": thread,
         "fr": json.dumps({"email": frm}), "to": json.dumps([{"email": a} for a in to]),
         "cc": json.dumps([{"email": a} for a in cc]),
         "bcc": json.dumps([{"email": a} for a in bcc or []]),
         "r": when, "o": w.p.org_b})


async def _run(w: _World, fn):
    app_dsn = w.p.app_url.render_as_string(hide_password=False)
    token = bind_tenant(w.p.org_b)
    try:
        async with tenant_engine_scope(app_dsn), hc._tenant_session() as db:
            return await fn(db)
    finally:
        release_tenant(token)


async def test_the_recipient_intent_sql_on_a_real_database(
    promoted, app_engine,  # noqa: F811
) -> None:
    _assert_non_priv(app_engine)
    w = _World(promoted)
    sagar = f"sagar-{w.tag}@jain-{w.tag}.test"
    ananda = f"ananda-{w.tag}@jain-{w.tag}.test"
    hidden = f"hidden-{w.tag}@jain-{w.tag}.test"
    planted = f"planted-{w.tag}@evil-{w.tag}.test"
    thread = f"th-{w.tag}"
    now = datetime.now(UTC)
    try:
        w.seed()
        with w.p.admin_engine.begin() as c:
            # Received: From Sagar, To the member, Cc Ananda.
            _mail(c, w, folder="inbox", frm=sagar, to=[w.owner], cc=[ananda],
                  thread=thread, when=now - timedelta(hours=30), tag="in")
            # The member's own Sent mails to the jain domain, Cc Kiran twice.
            for i in range(2):
                _mail(c, w, folder="sent", frm=w.owner, to=[sagar], cc=[w.kiran],
                      thread=f"other-{i}", when=now - timedelta(days=3 + i), tag=f"s{i}")
            # A Sent reply of the thread, an hour ago, Cc Geo.
            _mail(c, w, folder="sent", frm=w.owner, to=[sagar], cc=[w.geo, hidden],
                  thread=thread, when=now - timedelta(hours=1), tag="reply")
            # A stranger's mail in Sent: no Cc of it counts (P1-a).
            _mail(c, w, folder="sent", frm=sagar, to=[sagar], cc=[planted, planted],
                  thread="x", when=now, tag="pl1")
            _mail(c, w, folder="sent", frm=sagar, to=[sagar], cc=[planted],
                  thread="y", when=now, tag="pl2")
            # The chat's draft of the thread: Cc Kiran, Bcc Geo.
            _mail(c, w, folder="drafts", frm=w.owner, to=[sagar], cc=[w.kiran],
                  bcc=[w.geo], thread=thread, when=now, tag="draft")

        people = await _run(w, lambda db: hc.thread_participants(db, w.account, thread))
        assert sorted(people) == sorted([w.owner, ananda])  # no Sent, no draft Cc

        doms = await _run(w, lambda db: hc.org_domains(db, w.owner))
        assert f"fracktal-{w.tag}.test" in doms and f"rival-{w.tag}.test" not in doms

        async def usual(db):
            return (await db.execute(text(rc.USUAL_CC_SQL), {
                "uid": w.owner, "tid": thread, "aid": w.account,
                "domains": [f"jain-{w.tag}.test"]})).fetchall()

        got = {r.addr: (int(r.n), bool(r.in_thread)) for r in await _run(w, usual)}
        assert got[w.kiran] == (2, False)
        assert got[w.geo] == (1, True)
        assert planted not in got

        recent = await _run(w, lambda db: rc.recent_thread_reply(db, w.account, thread))
        assert recent["message_id"]

        async def draft(db):
            return (await db.execute(text(rc.THREAD_DRAFT_PEOPLE_SQL), {
                "aid": w.account, "tid": thread})).fetchone()

        row = await _run(w, draft)
        assert rc._addresses(row.cc_addresses) == [w.kiran]
        assert rc._addresses(row.bcc_addresses) == [w.geo]

        # The classifier with the thread people: Ananda is ``thread``.
        classes = await _run(w, lambda db: hc.classify_addresses(
            db, w.owner, [sagar, ananda], thread_senders=[sagar], thread_people=people,
            org_domains=doms))
        assert classes == {sagar: "sent", ananda: "thread"}
    finally:
        w.purge()
