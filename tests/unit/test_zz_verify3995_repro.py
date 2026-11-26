"""TEMPORARY verifier repro for task 3995. Delete after the run."""
# ruff: noqa
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from tests.unit.test_email_mark_done_r8 import (  # noqa: F401
    ME, NOW, _account, _as_member, _DB_GATE, _flag, _labels, _mail, _message,
    _project, _resolve, _row, _session, _set_labels, _status_row, app_engine, db,
    promoted, provider,
)

pytestmark = _DB_GATE


def _settings(db, box, draft=False):
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, "
            "follow_up_awaiting_days, follow_up_needs_reply_days, "
            "follow_up_auto_draft, organization_id) VALUES "
            "(CAST(:a AS uuid), 1, 1, :d, CAST(:o AS uuid))"),
            {"a": box, "o": db.org_b, "d": draft})


def _pmid(db, mid):
    return _message(db, mid).provider_message_id


async def test_repro_follow_up_scan_race(db, monkeypatch, provider):
    """Mark done while the follow-up scan runs."""
    from gateway.routes.email.automation import followups

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    first_t = f"a-{uuid.uuid4().hex[:6]}"
    second_t = f"b-{uuid.uuid4().hex[:6]}"
    a = _mail(db, box, first_t, days_ago=7, folder="sent", categories=["Awaiting Reply"])
    b = _mail(db, box, second_t, days_ago=8, folder="sent", categories=["Awaiting Reply"])
    _status_row(db, box, first_t, "AWAITING", a, reason="r", days_ago=7)
    _status_row(db, box, second_t, "AWAITING", b, reason="r", days_ago=8)
    _settings(db, box)

    entered, release = asyncio.Event(), asyncio.Event()
    orig = provider.set_labels
    first = _pmid(db, a)

    async def slow(pmid, add=None, remove=None):
        await orig(pmid, add=add, remove=remove)
        if pmid == first and "Follow-up" in (add or []):
            entered.set()
            await release.wait()

    provider.set_labels = slow

    async def scan():
        async with _as_member(db, db.org_b):
            return await followups._maybe_send_follow_up_reminders(box)

    job = asyncio.create_task(scan())
    await asyncio.wait_for(entered.wait(), 30)
    await asyncio.wait_for(_resolve(db, box, second_t), 30)   # the member marks done
    before = _labels(db, b)
    release.set()
    got = await asyncio.wait_for(job, 30)
    print("\nRACE after-done:", before, "after-scan:", _labels(db, b),
          "row:", _row(db, box, second_t)[:2], "scan:", got)
    print("RACE provider calls for held:",
          [c for c in provider.calls if c[0] == _pmid(db, b)])


async def test_repro_move_back_after_new_inbound(db, monkeypatch, provider):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    t = f"c-{uuid.uuid4().hex[:6]}"
    s = _mail(db, box, t, days_ago=6, folder="sent", categories=["Awaiting Reply", "Follow-up"])
    _status_row(db, box, t, "AWAITING", s, reason="r", days_ago=6)
    await _resolve(db, box, t)
    new = _mail(db, box, t, days_ago=0)
    await _project(db, box, new, "REPLY")
    mid = _row(db, box, t)[:2]
    await _resolve(db, box, t, done=False, restore={"status": "AWAITING", "follow_up": True})
    print("\nSTALE-UNDO after new mail:", mid, "after move back:", _row(db, box, t)[:2],
          "new mail labels:", _labels(db, new))


async def test_repro_anchor_swallows_unseen_mail(db, monkeypatch, provider):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    t = f"d-{uuid.uuid4().hex[:6]}"
    s = _mail(db, box, t, days_ago=6, folder="sent", categories=["Awaiting Reply"])
    _status_row(db, box, t, "AWAITING", s, reason="r", days_ago=6)
    unseen = _mail(db, box, t, days_ago=0.001)  # synced, rules not yet run
    got, _ = await _resolve(db, box, t)
    await _project(db, box, unseen, "REPLY")
    print("\nANCHOR previous:", got["previous"], "row:", _row(db, box, t)[:2],
          "labels:", _labels(db, unseen))


async def test_repro_push_races_undo(db, monkeypatch, provider):
    """The Done job is mid-flight when the member moves the thread back."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    t = f"e-{uuid.uuid4().hex[:6]}"
    first = _mail(db, box, t, days_ago=9, categories=[])
    sent = _mail(db, box, t, days_ago=6, folder="sent", categories=["Awaiting Reply", "Follow-up"])
    _status_row(db, box, t, "AWAITING", first, reason="r", days_ago=6)
    remote = {_pmid(db, first): set(), _pmid(db, sent): {"Awaiting Reply", "Follow-up"}}
    entered, release = asyncio.Event(), asyncio.Event()
    n = {"v": 0}

    async def stateful(pmid, add=None, remove=None):
        n["v"] += 1
        if n["v"] == 1:
            entered.set()
            await release.wait()
        remote[pmid] |= set(add or [])
        remote[pmid] -= set(remove or [])

    provider.set_labels = stateful
    _g, done_jobs = await _resolve(db, box, t, run_jobs=False)

    async def run_done():
        async with _as_member(db, db.org_b):
            await done_jobs()

    job = asyncio.create_task(run_done())
    await asyncio.wait_for(entered.wait(), 30)
    await asyncio.wait_for(_resolve(db, box, t, done=False,
                   restore={"status": "AWAITING", "follow_up": True}), 30)
    release.set()
    await asyncio.wait_for(job, 30)
    print("\nPUSH-RACE mirror first/sent:", _labels(db, first), _labels(db, sent))
    print("PUSH-RACE provider first/sent:", remote[_pmid(db, first)], remote[_pmid(db, sent)])


async def test_repro_import_path_brings_label_back(db, monkeypatch, provider):
    """A returned label with NO delta seen by the learner stays."""
    from gateway.routes.email.transport import sync

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    t = f"f-{uuid.uuid4().hex[:6]}"
    s = _mail(db, box, t, days_ago=6, folder="sent", categories=["Awaiting Reply", "Follow-up"])
    _status_row(db, box, t, "AWAITING", s, reason="r", days_ago=6)
    await _resolve(db, box, t)
    _set_labels(db, s, ["Follow-up"])  # an import batch or folder load stored it
    from types import SimpleNamespace
    ch = SimpleNamespace(thread_id=t, categories=["Follow-up"], from_address=None, id=s)
    async with _as_member(db, db.org_b):
        await sync.learn_from_label_change_events(box, [(ch, ["Follow-up"])])  # next sweep
    print("\nNO-DELTA labels after next sweep:", _labels(db, s))
