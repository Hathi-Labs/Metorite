"""Mark done sticks, on a real database (task 3995, R8).

Spec: ``email_app_master_plan.md`` §6.6. A member marks a thread done. No
automated writer may move it back to Needs Reply, Awaiting Reply or Follow-up
until a new inbound message arrives.

Against a REAL Postgres, under FORCE RLS, as the non-privileged app role. With
no ``TENANT_LADDER_DATABASE_URL`` the suite skips, and a skip is not a pass.
Only the provider and the model are fakes.

Mutations this file catches (R7):

* ``mark_done.upsert_expressions`` returns the plain expressions ->
  ``test_an_automated_write_keeps_a_held_thread``;
* ``mark_done.held_threads`` returns nothing -> the label, rules, resolver and
  sync tests;
* the held ``ORDER BY`` differs from the backfill's ->
  ``test_the_backfill_does_not_select_a_done_thread_again``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_mark_done_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_email_keep_separate import _account, _as_member
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_read_tool_paging_r8 import (  # noqa: F401
    ME,
    _direct,
    _user,
    db,
)

pytestmark = _DB_GATE

THEM = "buyer@acme.example"
NOW = datetime.now(UTC)


# ── seeding and reading ──────────────────────────────────────────────────────

def _flag(monkeypatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_mark_done_orgs", value)


class _Provider:
    """A provider that authenticates and records each label call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    async def set_labels(self, pmid, add=None, remove=None) -> None:
        self.calls.append((pmid, tuple(add or ()), tuple(remove or ())))

    async def create_draft(self, **_kw) -> None:
        raise AssertionError("no draft in this suite")


@pytest.fixture
def provider(monkeypatch) -> _Provider:
    from acb_llm import key_store
    from gateway.routes.email import core
    from gateway.routes.email.automation import followups, mark_done, replyzero

    fake = _Provider()
    store = SimpleNamespace(decrypt=lambda raw: json.dumps({}),
                            encrypt=lambda raw: raw)
    monkeypatch.setattr(key_store, "get_key_store", lambda: store)
    for mod in (core, followups, mark_done, replyzero):
        monkeypatch.setattr(mod, "_instantiate_provider",
                            lambda *_a, **_kw: fake)
    return fake


def _mail(db, box: str, thread: str, *, days_ago: float | None,
          folder: str = "inbox", categories: list[str] | None = None,
          sender: str = THEM, org: str | None = None) -> str:
    mine = folder == "sent"
    with db.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, folder, from_address, to_addresses, subject, body_text, "
            "received_at, categories, organization_id) VALUES "
            "(CAST(:a AS uuid), :p, :t, :f, CAST(:frm AS jsonb), "
            "CAST(:to AS jsonb), 's', 'b', :r, CAST(:c AS text[]), "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "p": f"pm-{uuid.uuid4().hex[:10]}", "t": thread,
             "f": folder,
             "frm": json.dumps({"email": ME if mine else sender}),
             "to": json.dumps([{"email": sender if mine else ME}]),
             "r": None if days_ago is None else NOW - timedelta(days=days_ago),
             "c": categories or [], "o": org or db.org_b}).scalar_one())


def _status_row(db, box: str, thread: str, status: str, msg: str,
                *, reason: str, days_ago: float = 5) -> None:
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "last_message_id, last_message_at, reason, classified_at, "
            "organization_id) VALUES (CAST(:a AS uuid), :t, :s, "
            "CAST(:m AS uuid), :at, :r, :at, CAST(:o AS uuid))"),
            {"a": box, "t": thread, "s": status, "m": msg, "r": reason,
             "at": NOW - timedelta(days=days_ago), "o": db.org_b})


def _row(db, box: str, thread: str) -> Any:
    with db.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, reason, last_message_id::text AS mid, "
            "last_message_at, classified_at FROM email_thread_status "
            "WHERE account_id = CAST(:a AS uuid) AND thread_id = :t"),
            {"a": box, "t": thread}).one_or_none()


def _labels(db, message_id: str) -> set[str]:
    with db.admin_engine.connect() as c:
        return set(c.execute(text(
            "SELECT categories FROM email_messages WHERE id = CAST(:m AS uuid)"),
            {"m": message_id}).scalar_one() or [])


def _set_labels(db, message_id: str, labels: list[str]) -> None:
    with db.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE email_messages SET categories = CAST(:c AS text[]) "
            "WHERE id = CAST(:m AS uuid)"), {"m": message_id, "c": labels})


def _message(db, message_id: str) -> Any:
    with db.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT * FROM email_messages WHERE id = CAST(:m AS uuid)"),
            {"m": message_id}).one()


def _follow_up_thread(db, box: str) -> tuple[str, str, str, str]:
    """The owner's case: an Awaiting thread in the Follow-up list.

    Returns ``(thread, first inbound, sent, undated draft)``. The status is a
    guess (``· auto``) of long ago, and it names the oldest message, so the
    backfill selects the thread.
    """
    thread = f"fu-{uuid.uuid4().hex[:8]}"
    first = _mail(db, box, thread, days_ago=9, categories=["Newsletter"])
    sent = _mail(db, box, thread, days_ago=6, folder="sent",
                 categories=["Awaiting Reply", "Follow-up"])
    undated = _mail(db, box, thread, days_ago=None, folder="drafts")
    _status_row(db, box, thread, "AWAITING", first,
                reason="Backfill — AWAITING_REPLY · auto", days_ago=6)
    return thread, first, sent, undated


# ── the calls ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _session(db):
    from gateway.routes.email.core import _tenant_session

    async with _as_member(db, db.org_b), _tenant_session() as s:
        yield s


async def _resolve(db, box: str, thread: str, *, org: str | None = None,
                   run_jobs: bool = True, **body: Any):
    from fastapi import BackgroundTasks
    from gateway.routes.email.automation import replyzero

    jobs = BackgroundTasks()
    async with _as_member(db, org or db.org_b):
        got = await replyzero.resolve_thread(
            replyzero.ThreadResolveRequest(
                account_id=box, thread_id=thread, **body),
            jobs, user=_user(org or db.org_b, "feature:email"))
        if run_jobs:
            await jobs()
    return got, jobs


async def _project(db, box: str, message_id: str, status_key: str | None):
    """The runner's tail for one message: project, then reconcile the labels."""
    from gateway.routes.email.automation import replyzero

    matches = [] if status_key is None else [
        {"rule": {"system_type": status_key, "name": status_key}, "reason": "ai"}]
    message = _message(db, message_id)
    async with _session(db) as s:
        label = await replyzero.project_reply_status_from_matches(
            s, box, message, matches)
        if label:
            await replyzero._reconcile_thread_labels(
                s, None, box, message.thread_id, label)
    return label


async def _backlog(db, box: str) -> int:
    from gateway.routes.email.automation import replyzero

    async with _session(db) as s:
        return await replyzero._count_reply_zero_backlog(s, box)


_OPEN = {"Needs Reply", "Awaiting Reply", "Follow-up"}


# ── 1. Mark done ─────────────────────────────────────────────────────────────

async def test_mark_done_sets_the_status_and_the_labels_in_one_call(
    db, monkeypatch, provider,  # noqa: F811
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated = _follow_up_thread(db, box)

    got, _ = await _resolve(db, box, thread, run_jobs=False)

    assert got == {"ok": True, "thread_id": thread, "done": True,
                   "previous": {"status": "AWAITING", "follow_up": True}}
    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    # The anchor is the newest dated message, here the sent one.
    assert row.last_message_at == _message(db, sent).received_at
    # The mirror is right before any background job runs.
    assert not (_labels(db, first) | _labels(db, sent) | _labels(db, undated)) & _OPEN
    # "Done" goes on the latest message that is not in Sent.
    assert "Done" in _labels(db, first) | _labels(db, undated)
    assert provider.calls == []


async def test_the_provider_hears_the_removals_after_the_request(
    db, monkeypatch, provider,  # noqa: F811
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, _first, sent, _undated = _follow_up_thread(db, box)

    await _resolve(db, box, thread)

    pmid = _message(db, sent).provider_message_id
    removed = {lab for p, _add, rem in provider.calls if p == pmid for lab in rem}
    assert removed == {"Awaiting Reply", "Follow-up"}
    assert any("Done" in add for _p, add, _rem in provider.calls)


async def test_an_older_done_job_does_not_undo_a_move_back(
    db, monkeypatch, provider,  # noqa: F811
):
    """The job drops a change that the mirror no longer holds."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, _first, sent, _undated = _follow_up_thread(db, box)
    _got, done_jobs = await _resolve(db, box, thread, run_jobs=False)
    await _resolve(db, box, thread, done=False, run_jobs=False,
                   restore={"status": "AWAITING", "follow_up": True})

    async with _as_member(db, db.org_b):
        await done_jobs()

    assert not any("Follow-up" in rem for _p, _add, rem in provider.calls)
    assert not any("Done" in add for _p, add, _rem in provider.calls)


async def test_the_backfill_does_not_select_a_done_thread_again(
    db, monkeypatch, provider,  # noqa: F811
):
    """(a) A ``· auto`` row with a stale message id, and an undated row that
    the backfill reads as the latest one."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, _first, _sent, undated = _follow_up_thread(db, box)
    assert await _backlog(db, box) == 1

    await _resolve(db, box, thread)

    assert _row(db, box, thread).mid == undated
    assert await _backlog(db, box) == 0


# ── 2. The hold ──────────────────────────────────────────────────────────────

async def test_an_automated_write_keeps_a_held_thread(
    db, monkeypatch, provider,  # noqa: F811
):
    """(b) A FYI and then a Needs Reply for OLD mail. The first write must not
    erase the mark that the second one reads."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    before = _row(db, box, thread)

    await _project(db, box, first, None)
    label = await _project(db, box, first, "REPLY")

    assert label == "Needs Reply"  # the rule did match
    after = _row(db, box, thread)
    assert (after.status, after.reason, after.last_message_at,
            after.classified_at) == (
        "DONE", "Marked done by you", before.last_message_at,
        before.classified_at)
    assert after.mid == first  # the backfill sees the write
    assert not (_labels(db, first) | _labels(db, sent) | _labels(db, undated)) & _OPEN


async def test_the_guarded_writes_of_the_status_keep_a_held_thread(
    db, monkeypatch, provider,  # noqa: F811
):
    """(d) The outbound recompute and the reply of the member."""
    from gateway.routes.email.automation import replyzero

    async def _says_awaiting(*_a, **_kw):
        return "AWAITING_REPLY", True

    monkeypatch.setattr(replyzero, "_llm_determine_thread_status", _says_awaiting)
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    _mail(db, box, thread, days_ago=0, folder="sent")

    async with _session(db) as s:
        await replyzero.recompute_thread_status(
            s, box, thread, trigger="outbound", acc_email=ME)
    async with _as_member(db, db.org_b):
        await replyzero._mark_thread_replied(box, thread, "My reply", "Re: s")

    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    assert not (_labels(db, first) | _labels(db, sent) | _labels(db, undated)) & _OPEN
    assert not any(set(add) & _OPEN for _p, add, _rem in provider.calls)


async def test_a_held_thread_asks_no_model_and_runs_no_rule(
    db, monkeypatch, provider,  # noqa: F811
):
    """The resolver and the apply tail. Process past reaches the tail with no
    resolver, and a re-run falls back to the raw match."""
    from gateway.routes.email.automation import replyzero, runner

    async def _never(*_a, **_kw):
        raise AssertionError("a held thread asks no model")

    monkeypatch.setattr(replyzero, "_llm_determine_thread_status", _never)
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, _undated = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    provider.calls.clear()
    message = _message(db, first)
    reply_rule = {"rule": {"id": None, "name": "Needs Reply",
                           "system_type": "REPLY", "automated": True,
                           "actions": [{"type": "LABEL", "label": "Needs Reply"}]},
                  "reason": "ai", "source": "ai"}

    async with _session(db) as s:
        resolved = await replyzero.resolve_conversation_status_matches(
            s, box, message, [reply_rule], provider=provider)
        assert [m["suppressed"] for m in resolved] == ["held"]
        assert await replyzero.read_job_status(s, box, message) is None
        # The raw match, as Process past gives it.
        await runner._apply_matches(
            s, provider, message, {"email": THEM}, {}, [reply_rule],
            apply=True, dry_run=False, about="", signature="",
            account_user=ME, account_id=box, cold_blocker="LABEL")

    assert provider.calls == []
    assert "Needs Reply" not in _labels(db, first)
    with db.admin_engine.connect() as c:
        logged = c.execute(text(
            "SELECT status, reason FROM email_executed_rules "
            "WHERE account_id = CAST(:a AS uuid)"), {"a": box}).all()
    assert [tuple(r) for r in logged] == [
        ("SKIPPED", "Suppressed: you marked this conversation done.")]


async def test_a_new_inbound_message_ends_the_hold(
    db, monkeypatch, provider,  # noqa: F811
):
    """(c) A sent mail and a draft do not end it. A new inbound mail does."""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, *_ = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    _mail(db, box, thread, days_ago=-0.01, folder="sent")
    _mail(db, box, thread, days_ago=-0.01, folder="Drafts")
    async with _session(db) as s:
        assert await mark_done.thread_is_held(s, box, thread)

    answer = _mail(db, box, thread, days_ago=-0.02)
    async with _session(db) as s:
        assert not await mark_done.thread_is_held(s, box, thread)
    await _project(db, box, answer, "REPLY")

    row = _row(db, box, thread)
    assert (row.status, row.mid) == ("NEEDS_REPLY", answer)
    assert "Needs Reply" in _labels(db, answer)


async def test_the_follow_up_scan_leaves_a_held_thread(
    db, monkeypatch, provider,  # noqa: F811
):
    """(d) An Awaiting thread beside it gets its Follow-up label."""
    from gateway.routes.email.automation import followups

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    held, first, sent, undated = _follow_up_thread(db, box)
    await _resolve(db, box, held)
    waiting = f"w-{uuid.uuid4().hex[:8]}"
    nudged = _mail(db, box, waiting, days_ago=8, folder="sent")
    _status_row(db, box, waiting, "AWAITING", nudged, reason="r", days_ago=8)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, "
            "follow_up_awaiting_days, follow_up_needs_reply_days, "
            "follow_up_auto_draft, organization_id) VALUES "
            "(CAST(:a AS uuid), 1, 1, false, CAST(:o AS uuid))"),
            {"a": box, "o": db.org_b})

    async with _as_member(db, db.org_b):
        got = await followups._maybe_send_follow_up_reminders(box)

    assert got["labeled"] == 1
    assert "Follow-up" in _labels(db, nudged)
    assert not (_labels(db, first) | _labels(db, sent) | _labels(db, undated)) & _OPEN


async def test_a_label_that_a_resync_brings_back_goes_again(
    db, monkeypatch, provider,  # noqa: F811
):
    """(d) The learner's correction and a lone "Follow-up"."""
    from gateway.routes.email.transport import sync

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, _first, sent, _undated = _follow_up_thread(db, box)
    await _resolve(db, box, thread)

    # The provider still had the old labels, and the sync stored them.
    for returned in (["Needs Reply", "Follow-up"], ["Follow-up"]):
        provider.calls.clear()
        _set_labels(db, sent, returned)
        change = SimpleNamespace(thread_id=thread, categories=returned,
                                 from_address=None, id=sent)
        async with _as_member(db, db.org_b):
            await sync.learn_from_label_change_events(box, [(change, [])])
        assert not _labels(db, sent) & _OPEN, returned
        assert {lab for _p, _a, rem in provider.calls for lab in rem} == set(returned)

    _set_labels(db, sent, ["Needs Reply"])
    async with _as_member(db, db.org_b):
        await sync._apply_label_status_corrections(box, {thread: "REPLY"})
    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    assert not _labels(db, sent) & _OPEN


async def test_a_write_of_the_member_still_wins(
    db, monkeypatch, provider,  # noqa: F811
):
    """The Fix of the member, a reopen and a dismiss each move a held thread."""
    from gateway.routes.email.automation import mark_done, replyzero

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, undated = _follow_up_thread(db, box)

    await _resolve(db, box, thread)
    async with _as_member(db, db.org_b):
        got = await replyzero.apply_thread_status_correction(box, thread, "REPLY")
    assert got["ok"]
    assert _row(db, box, thread).status == "NEEDS_REPLY"
    assert "Needs Reply" in _labels(db, first) | _labels(db, undated)

    # A reopen reads the folder of the latest dated message, here the sent one.
    for body, status in (({"done": False}, "AWAITING"),
                         ({"dismiss": True}, "FYI")):
        await _resolve(db, box, thread)
        await _resolve(db, box, thread, **body)
        async with _session(db) as s:
            assert not await mark_done.thread_is_held(s, box, thread)
        assert _row(db, box, thread).status == status


# ── 3. Move back ─────────────────────────────────────────────────────────────

async def test_move_back_restores_the_status_and_follow_up(
    db, monkeypatch, provider,  # noqa: F811
):
    """(e)"""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, undated = _follow_up_thread(db, box)
    done, _ = await _resolve(db, box, thread)
    provider.calls.clear()

    got, _ = await _resolve(db, box, thread, done=False,
                            restore=done["previous"])

    assert got == {"ok": True, "thread_id": thread, "done": False}
    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("AWAITING", "Moved back by you")
    inbound = _labels(db, first) | _labels(db, undated)
    assert "Awaiting Reply" in inbound
    everything = inbound | _labels(db, _sent)
    assert "Follow-up" in everything and "Done" not in everything
    async with _session(db) as s:
        assert not await mark_done.thread_is_held(s, box, thread)
    added = {lab for _p, add, _r in provider.calls for lab in add}
    assert added == {"Awaiting Reply", "Follow-up"}
    assert "Done" in {lab for _p, _a, rem in provider.calls for lab in rem}


async def test_a_second_done_offers_no_move_back(
    db, monkeypatch, provider,  # noqa: F811
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, *_ = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    again, _ = await _resolve(db, box, thread)
    assert again["previous"] is None


async def test_a_thread_with_no_status_row_gets_one(
    db, monkeypatch, provider,  # noqa: F811
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"new-{uuid.uuid4().hex[:8]}"
    only = _mail(db, box, thread, days_ago=2)
    got, _ = await _resolve(db, box, thread)
    assert got["previous"] == {"status": "FYI", "follow_up": False}
    row = _row(db, box, thread)
    assert (row.status, row.mid) == ("DONE", only)


async def test_a_restore_of_another_status_is_refused(
    db, monkeypatch, provider,  # noqa: F811
):
    from gateway.routes.email.automation import replyzero
    from pydantic import ValidationError

    for bad in ({"status": "DONE"}, {"status": "Follow-up"}, {}):
        with pytest.raises(ValidationError):
            replyzero.ThreadResolveRequest(
                account_id="a", thread_id="t", done=False, restore=bad)


# ── 4. The flag off, and the tenant ──────────────────────────────────────────

async def test_with_the_flag_off_the_route_and_the_writers_are_as_before(
    db, monkeypatch, provider,  # noqa: F811
):
    """(f) No ``previous``, no hold: a Needs Reply opens a Done thread, and a
    restore is a plain reopen."""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, "")
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, _undated = _follow_up_thread(db, box)

    got, _ = await _resolve(db, box, thread)
    assert got == {"ok": True, "thread_id": thread, "done": True}
    row = _row(db, box, thread)
    assert (row.status, row.reason) == (
        "DONE", "Backfill — AWAITING_REPLY · auto")
    assert await _backlog(db, box) == 1

    await _project(db, box, first, "REPLY")
    assert _row(db, box, thread).status == "NEEDS_REPLY"

    await _resolve(db, box, thread)
    got, _ = await _resolve(db, box, thread, done=False,
                            restore={"status": "FYI", "follow_up": True})
    assert got == {"ok": True, "thread_id": thread, "done": False}
    assert _row(db, box, thread).status != "FYI"
    async with _as_member(db, db.org_b):
        state = await mark_done.mark_done_state(
            user=_user(db.org_b, "feature:email"))
    assert state == {"enabled": False}


async def test_a_held_row_stops_holding_when_the_flag_goes_off(
    db, monkeypatch, provider,  # noqa: F811
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, *_ = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    _flag(monkeypatch, "")
    await _project(db, box, first, "REPLY")
    assert _row(db, box, thread).status == "NEEDS_REPLY"


async def test_a_member_of_another_organization_marks_nothing(
    db, monkeypatch, provider,  # noqa: F811
):
    """(g) The route reads through the tenant seam, so org A finds no box."""
    from fastapi import HTTPException

    _flag(monkeypatch, "*")
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, *_ = _follow_up_thread(db, box)
    with pytest.raises(HTTPException) as refused:
        await _resolve(db, box, thread, org=db.org_a)
    assert refused.value.status_code in (403, 404)
    assert _row(db, box, thread).status == "AWAITING"
