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
  ``test_the_backfill_does_not_select_a_done_thread_again``;
* ``mark_done.recheck_after_io`` does nothing ->
  ``test_a_hold_that_begins_during_a_rule_apply_ends_done``;
* ``followups`` asks the hold once, before its rows ->
  ``test_a_mark_done_during_the_follow_up_scan_stays_done``;
* ``mark_done.heal_held`` does nothing ->
  ``test_the_sweep_heals_a_label_that_came_with_no_delta``;
* ``mark_done.converge`` sends once and does not read the labels again ->
  ``test_an_older_done_job_does_not_undo_a_move_back``;
* ``owned_sql`` leaves out a recent move back ->
  ``test_a_sync_before_the_job_of_a_move_back_keeps_the_move_back``;
* the move back writes with no hold in its WHERE ->
  ``test_a_move_back_after_new_mail_changes_nothing``;
* ``from_other_sql`` reads the folder only ->
  ``test_the_members_own_mail_in_any_folder_keeps_the_hold``;
* the anchor counts mail that waits for its rules ->
  ``test_inbound_mail_that_waits_for_its_rules_ends_the_hold``;
* ``approve_execution`` or ``retry_failed_executions`` skips the ask ->
  ``test_approve_and_repair_run_no_rule_in_a_held_thread``;
* the reconcile puts a label on a draft ->
  ``test_mark_done_sets_the_status_and_the_labels_in_one_call``;
* the request waits for a message row, or a writer holds the status row or a
  message row across its I/O (F1, F2) ->
  ``test_a_click_during_the_follow_up_scan_never_waits_and_stays_done`` and
  ``test_a_click_during_the_rules_job_never_waits_and_stays_done``. Each
  asserts that the database counted no deadlock;
* a label job sends a diff and stops when another write owns the row (F3) ->
  ``test_a_later_write_of_the_member_beats_an_older_done_job``;
* the move back ignores a reply of the member (F4) ->
  ``test_a_move_back_after_the_members_own_reply_changes_nothing``;
* the approval or the repair skips its ask after the I/O (F5) ->
  ``test_a_hold_that_begins_during_an_approval_or_a_repair_ends_done``;
* an automated write on a held row re-arms the reminder (F6) ->
  ``test_an_automated_write_on_a_held_row_keeps_the_reminder``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_mark_done_r8.py -v -rs
"""
# ruff: noqa: F811 -- `db` is a fixture that an import brings in.
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_email_keep_separate import _account, _address, _as_member
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
    """A provider that authenticates, records each label call, and keeps the
    labels of each message (``remote``), as a mail server does.

    ``pause`` is an optional ``(test, entered, release)``: the first call for
    which ``test(pmid, add, remove)`` is true sets ``entered`` and waits for
    ``release`` before it lands. ``drafts`` records each nudge draft.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        self.remote: dict[str, set[str]] = {}
        self.pause: tuple[Any, asyncio.Event, asyncio.Event] | None = None
        self.drafts: list[str | None] = []

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    async def set_labels(self, pmid, add=None, remove=None) -> None:
        if self.pause and self.pause[0](pmid, add or [], remove or []):
            _test, entered, release = self.pause
            self.pause = None
            entered.set()
            await release.wait()
        self.calls.append((pmid, tuple(add or ()), tuple(remove or ())))
        labels = self.remote.setdefault(pmid, set())
        labels |= set(add or ())
        labels -= set(remove or ())

    async def create_draft(self, **kw) -> None:
        self.drafts.append(kw.get("thread_id"))


@pytest.fixture
def provider(monkeypatch) -> _Provider:
    from acb_llm import key_store
    from gateway.routes.email import core
    from gateway.routes.email.automation import followups, mark_done, replyzero, runner

    fake = _Provider()
    store = SimpleNamespace(decrypt=lambda raw: json.dumps({}),
                            encrypt=lambda raw: raw)
    monkeypatch.setattr(key_store, "get_key_store", lambda: store)
    for mod in (core, followups, mark_done, replyzero, runner):
        monkeypatch.setattr(mod, "_instantiate_provider",
                            lambda *_a, **_kw: fake)
    return fake


def _mail(db, box: str, thread: str, *, days_ago: float | None,
          folder: str = "inbox", categories: list[str] | None = None,
          sender: str = THEM, org: str | None = None) -> str:
    """One message. Sent mail and drafts come from the address of the mailbox,
    as the provider stores them."""
    mine = folder.lower() in ("sent", "drafts")
    me = _address(db.admin_engine, box)
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
             "frm": json.dumps({"email": me if mine else sender}),
             "to": json.dumps([{"email": sender if mine else me}]),
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


def _follow_up_thread(db, box: str) -> tuple[str, str, str, str, str]:
    """The owner's case: an Awaiting thread in the Follow-up list.

    Returns ``(thread, first inbound, sent, undated draft, nudge draft)``. The
    status is a guess (``· auto``) of long ago, and it names the oldest
    message, so the backfill selects the thread. The nudge draft of the
    follow-up scan is the newest dated message (D3): no label of the
    conversation may land on it.
    """
    thread = f"fu-{uuid.uuid4().hex[:8]}"
    first = _mail(db, box, thread, days_ago=9, categories=["Newsletter"])
    sent = _mail(db, box, thread, days_ago=6, folder="sent",
                 categories=["Awaiting Reply", "Follow-up"])
    undated = _mail(db, box, thread, days_ago=None, folder="drafts")
    _status_row(db, box, thread, "AWAITING", first,
                reason="Backfill — AWAITING_REPLY · auto", days_ago=6)
    nudge = _mail(db, box, thread, days_ago=1, folder="drafts")
    return thread, first, sent, undated, nudge


def _pmid(db, message_id: str) -> str:
    return _message(db, message_id).provider_message_id


def _seed_remote(db, provider: _Provider, *message_ids: str) -> None:
    """The mail server holds the labels of the mirror."""
    for m in message_ids:
        provider.remote[_pmid(db, m)] = set(_labels(db, m))


_CONVERSATION = {"Needs Reply", "Awaiting Reply", "Done", "FYI", "Follow-up"}


def _remote_matches_mirror(db, provider: _Provider, *message_ids: str) -> None:
    for m in message_ids:
        remote = provider.remote.get(_pmid(db, m), set()) & _CONVERSATION
        assert remote == _labels(db, m) & _CONVERSATION, m


def _rule(db, box: str, name: str, label: str, system_type: str | None) -> str:
    """A rule with one LABEL action. The label learner reads such rules."""
    with db.admin_engine.begin() as c:
        rid = str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, system_type, enabled, "
            "organization_id) VALUES (CAST(:a AS uuid), :n, :s, true, "
            "CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "n": name, "s": system_type, "o": db.org_b}).scalar_one())
        c.execute(text(
            "INSERT INTO email_actions (rule_id, type, label, organization_id) "
            "VALUES (CAST(:r AS uuid), 'LABEL', :l, CAST(:o AS uuid))"),
            {"r": rid, "l": label, "o": db.org_b})
    return rid


def _follow_up_settings(db, box: str, *, auto_draft: bool) -> None:
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO email_assistant_settings (account_id, "
            "follow_up_awaiting_days, follow_up_needs_reply_days, "
            "follow_up_auto_draft, organization_id) VALUES "
            "(CAST(:a AS uuid), 1, 1, :d, CAST(:o AS uuid))"),
            {"a": box, "o": db.org_b, "d": auto_draft})


async def _learn(db, box: str, deltas: dict[str, tuple[list[str], list[str]]]) -> None:
    """The label learner of a sync over ``{message: (old, new)}``."""
    from gateway.routes.email.transport import sync

    changes = [(SimpleNamespace(thread_id=_message(db, m).thread_id,
                                categories=new, from_address=None, id=m), old)
               for m, (old, new) in deltas.items()]
    async with _as_member(db, db.org_b):
        await sync.learn_from_label_change_events(box, changes)


async def _sync_stores_the_remote(db, box: str, provider: _Provider,
                                  *message_ids: str) -> None:
    """A sync: it stores the labels of the mail server in the mirror, then
    runs the label learner over the change, as the scheduler does."""
    deltas = {}
    for m in message_ids:
        new = sorted(provider.remote.get(_pmid(db, m), set()))
        deltas[m] = (sorted(_labels(db, m)), new)
        _set_labels(db, m, new)
    await _learn(db, box, deltas)


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


def _all(db, *message_ids: str) -> set[str]:
    """The labels of each of *message_ids*, together."""
    return set().union(*(_labels(db, m) for m in message_ids))


# ── 1. Mark done ─────────────────────────────────────────────────────────────

async def test_mark_done_sets_the_status_and_the_labels_in_one_call(
    db, monkeypatch, provider,
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)

    got, _ = await _resolve(db, box, thread, run_jobs=False)

    assert got == {"ok": True, "thread_id": thread, "done": True,
                   "previous": {"status": "AWAITING", "follow_up": True}}
    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    # The anchor is the newest dated message, here the member's nudge draft.
    assert row.last_message_at == _message(db, nudge).received_at
    # The mirror is right before any background job runs.
    assert not _all(db, first, sent, undated, nudge) & _OPEN
    # "Done" goes on the latest inbound message. A draft is no inbound mail,
    # and the list shows no label on it (D3).
    assert "Done" in _labels(db, first)
    assert not _all(db, undated, nudge)
    assert provider.calls == []


async def test_the_provider_hears_the_removals_after_the_request(
    db, monkeypatch, provider,
):
    """The label job makes the mail server show the committed local labels,
    one whole set for each message that is not a draft."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)
    _seed_remote(db, provider, first, sent, undated, nudge)

    await _resolve(db, box, thread)

    assert provider.remote[_pmid(db, sent)] & _CONVERSATION == set()
    assert provider.remote[_pmid(db, first)] & _CONVERSATION == {"Done"}
    _remote_matches_mirror(db, provider, first, sent, undated, nudge)
    # No label call reaches a draft (D3).
    drafts = {_pmid(db, undated), _pmid(db, nudge)}
    assert not [c for c in provider.calls if c[0] in drafts]


async def test_an_older_done_job_does_not_undo_a_move_back(
    db, monkeypatch, provider,
):
    """(D2a) The label job of Done is inside its first provider call when the
    member moves the thread back. The job finds a newer generation, and the
    mail server ends with the labels of the move back. The next sync keeps
    the move back."""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    _rule(db, box, "Done", "Done", "DONE")
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)
    everything = (first, sent, undated, nudge)
    _seed_remote(db, provider, *everything)
    entered, release = asyncio.Event(), asyncio.Event()
    provider.pause = (lambda *_a: True, entered, release)
    done, done_jobs = await _resolve(db, box, thread, run_jobs=False)

    async with _as_member(db, db.org_b):
        job = asyncio.create_task(done_jobs())
        await asyncio.wait_for(entered.wait(), 30)
        await asyncio.wait_for(_resolve(
            db, box, thread, done=False, restore=done["previous"]), 30)
        release.set()
        await asyncio.wait_for(job, 30)

    row = _row(db, box, thread)
    assert (row.status, row.reason) == (
        "AWAITING", mark_done.RESTORED_FOLLOW_UP_REASON)
    _remote_matches_mirror(db, provider, *everything)
    await _sync_stores_the_remote(db, box, provider, *everything)
    assert _row(db, box, thread).status == "AWAITING"
    assert "Awaiting Reply" in _labels(db, first)
    assert "Follow-up" in _labels(db, sent)
    assert "Done" not in _all(db, *everything)


async def test_a_sync_before_the_job_of_a_move_back_keeps_the_move_back(
    db, monkeypatch, provider,
):
    """(D2b) A sync stores the old labels of the mail server between the
    commit of the move back and its label job. The job still sends the move
    back, and the learner reads the old "Done" as stale."""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    _rule(db, box, "Done", "Done", "DONE")
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)
    everything = (first, sent, undated, nudge)
    _seed_remote(db, provider, *everything)
    done, _ = await _resolve(db, box, thread)
    _got, back_jobs = await _resolve(db, box, thread, done=False,
                                     run_jobs=False, restore=done["previous"])
    # The sync stores the labels of the mail server, which still say Done,
    # before the job of the move back runs. Its learner runs after the job.
    moved = {m: sorted(_labels(db, m)) for m in everything}
    stale = {m: sorted(provider.remote.get(_pmid(db, m), set()))
             for m in everything}
    assert "Done" in set().union(*map(set, stale.values()))
    for m in everything:
        _set_labels(db, m, stale[m])
    provider.calls.clear()
    async with _as_member(db, db.org_b):
        await back_jobs()
    assert provider.calls, "the job of the move back sent nothing"
    await _learn(db, box, {m: (moved[m], stale[m]) for m in everything})

    row = _row(db, box, thread)
    assert (row.status, row.reason) == (
        "AWAITING", mark_done.RESTORED_FOLLOW_UP_REASON)
    assert "Awaiting Reply" in _labels(db, first)
    assert "Follow-up" in _labels(db, sent)
    _remote_matches_mirror(db, provider, *everything)


async def test_the_backfill_does_not_select_a_done_thread_again(
    db, monkeypatch, provider,
):
    """(a) A ``· auto`` row with a stale message id, and an undated row that
    the backfill reads as the latest one."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, _first, _sent, undated, _nudge = _follow_up_thread(db, box)
    assert await _backlog(db, box) == 1

    await _resolve(db, box, thread)

    assert _row(db, box, thread).mid == undated
    assert await _backlog(db, box) == 0


# ── 2. The hold ──────────────────────────────────────────────────────────────

async def test_an_automated_write_keeps_a_held_thread(
    db, monkeypatch, provider,
):
    """(b) A FYI and then a Needs Reply for OLD mail. The first write must not
    erase the mark that the second one reads."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)
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
    assert not _all(db, first, sent, undated, nudge) & _OPEN


async def test_the_guarded_writes_of_the_status_keep_a_held_thread(
    db, monkeypatch, provider,
):
    """(d) The outbound recompute and the reply of the member."""
    from gateway.routes.email.automation import replyzero

    async def _says_awaiting(*_a, **_kw):
        return "AWAITING_REPLY", True

    monkeypatch.setattr(replyzero, "_llm_determine_thread_status", _says_awaiting)
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    _mail(db, box, thread, days_ago=0, folder="sent")

    async with _session(db) as s:
        await replyzero.recompute_thread_status(
            s, box, thread, trigger="outbound", acc_email=ME)
    async with _as_member(db, db.org_b):
        await replyzero._mark_thread_replied(box, thread, "My reply", "Re: s")

    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    assert not _all(db, first, sent, undated, nudge) & _OPEN
    assert not any(set(add) & _OPEN for _p, add, _rem in provider.calls)


async def test_a_held_thread_asks_no_model_and_runs_no_rule(
    db, monkeypatch, provider,
):
    """The resolver and the apply tail. Process past reaches the tail with no
    resolver, and a re-run falls back to the raw match."""
    from gateway.routes.email.automation import replyzero, runner

    async def _never(*_a, **_kw):
        raise AssertionError("a held thread asks no model")

    monkeypatch.setattr(replyzero, "_llm_determine_thread_status", _never)
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, _undated, _nudge = _follow_up_thread(db, box)
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


async def test_a_rerun_of_the_rules_on_held_mail_says_that_nothing_ran(
    db, monkeypatch, provider,
):
    """The re-run falls back to the raw match, and its answer is honest."""
    from gateway.routes.email.automation import engine, runner

    async def _picks_needs_reply(*_a, **_kw):
        return {"rule": {"id": None, "name": "Needs Reply", "system_type": "REPLY",
                         "automated": True,
                         "actions": [{"type": "LABEL", "label": "Needs Reply"}]},
                "reason": "ai", "source": "ai"}

    monkeypatch.setattr(runner, "_match_email_to_rule", _picks_needs_reply)
    monkeypatch.setattr(engine, "_match_email_to_rule", _picks_needs_reply)
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, *_ = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    provider.calls.clear()

    async with _as_member(db, db.org_b):
        got = await runner.run_rules_on_message(
            runner.RuleRunMessageRequest(
                account_id=box, message_id=first, is_test=False),
            user=_user(db.org_b, "feature:email"))

    assert (got["matched"], got["applied"], got["reason"]) == (
        True, False, "Suppressed: you marked this conversation done.")
    assert "Needs Reply" not in _labels(db, first)
    assert _row(db, box, thread).status == "DONE"
    assert provider.calls == []


async def test_a_new_inbound_message_ends_the_hold(
    db, monkeypatch, provider,
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
    db, monkeypatch, provider,
):
    """(d) An Awaiting thread beside it gets its Follow-up label."""
    from gateway.routes.email.automation import followups

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    held, first, sent, undated, nudge = _follow_up_thread(db, box)
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
    assert not _all(db, first, sent, undated, nudge) & _OPEN


async def test_a_label_that_a_resync_brings_back_goes_again(
    db, monkeypatch, provider,
):
    """(d) The learner's correction and a lone "Follow-up"."""
    from gateway.routes.email.transport import sync

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, _first, sent, _undated, _nudge = _follow_up_thread(db, box)
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
        removed = {lab for p, _a, rem in provider.calls
                   if p == _pmid(db, sent) for lab in rem}
        assert set(returned) <= removed, returned

    _set_labels(db, sent, ["Needs Reply"])
    async with _as_member(db, db.org_b):
        await sync._apply_label_status_corrections(box, {thread: "REPLY"})
    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    assert not _labels(db, sent) & _OPEN


async def test_a_write_of_the_member_still_wins(
    db, monkeypatch, provider,
):
    """The Fix of the member, a reopen and a dismiss each move a held thread."""
    from gateway.routes.email.automation import mark_done, replyzero

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, undated, nudge = _follow_up_thread(db, box)

    await _resolve(db, box, thread)
    async with _as_member(db, db.org_b):
        got = await replyzero.apply_thread_status_correction(box, thread, "REPLY")
    assert got["ok"]
    assert _row(db, box, thread).status == "NEEDS_REPLY"
    assert "Needs Reply" in _labels(db, first)
    assert not _all(db, undated, nudge)

    # A reopen reads the folder of the latest dated message. Here that is the
    # nudge draft, which is not in Sent, so the code of before says Needs Reply.
    for body, status in (({"done": False}, "NEEDS_REPLY"),
                         ({"dismiss": True}, "FYI")):
        await _resolve(db, box, thread)
        await _resolve(db, box, thread, **body)
        async with _session(db) as s:
            assert not await mark_done.thread_is_held(s, box, thread)
        assert _row(db, box, thread).status == status


# ── 3. Move back ─────────────────────────────────────────────────────────────

async def test_move_back_restores_the_status_and_follow_up(
    db, monkeypatch, provider,
):
    """(e)"""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)
    done, _ = await _resolve(db, box, thread)
    provider.calls.clear()

    got, _ = await _resolve(db, box, thread, done=False,
                            restore=done["previous"])

    assert got == {"ok": True, "thread_id": thread, "done": False}
    row = _row(db, box, thread)
    assert (row.status, row.reason) == (
        "AWAITING", "Moved back to Follow-up by you")
    # The labels go on the mail of the list, never on a draft (D3).
    assert "Awaiting Reply" in _labels(db, first)
    assert "Follow-up" in _labels(db, sent)
    assert not _all(db, undated, nudge)
    assert "Done" not in _all(db, first, sent, undated, nudge)
    async with _session(db) as s:
        assert not await mark_done.thread_is_held(s, box, thread)
    added = {lab for _p, add, _r in provider.calls for lab in add}
    assert added == {"Awaiting Reply", "Follow-up"}
    assert "Done" in {lab for _p, _a, rem in provider.calls for lab in rem}


async def test_a_second_done_offers_no_move_back(
    db, monkeypatch, provider,
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, *_ = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    again, _ = await _resolve(db, box, thread)
    assert again["previous"] is None


async def test_a_thread_with_no_status_row_gets_one(
    db, monkeypatch, provider,
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"new-{uuid.uuid4().hex[:8]}"
    only = _mail(db, box, thread, days_ago=2)
    got, _ = await _resolve(db, box, thread)
    assert got["previous"] == {"status": "FYI", "follow_up": False}
    row = _row(db, box, thread)
    assert (row.status, row.mid) == ("DONE", only)


async def test_done_closes_the_tasks_and_a_failure_there_keeps_the_done(
    db, monkeypatch, provider,
):
    """Done asks the one task hop, as before. A database error inside it rolls
    back that hop only, and the status and the labels stay."""
    from gateway.routes.tasks import email_link

    asked: list[tuple[str, str, str]] = []

    async def _broken(s, uid, account_id, thread_id):
        asked.append((uid, account_id, thread_id))
        await s.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(email_link, "propagate_thread_done_to_tasks", _broken)
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, _undated, _nudge = _follow_up_thread(db, box)

    got, _ = await _resolve(db, box, thread)
    await _resolve(db, box, thread, done=False, restore=got["previous"])
    await _resolve(db, box, thread)

    # A move back opens no task again, so only the two Done acts ask.
    assert asked == [(ME, box, thread)] * 2
    assert _row(db, box, thread).status == "DONE"
    assert "Done" in _labels(db, first)


async def test_a_restore_of_another_status_is_refused(
    db, monkeypatch, provider,
):
    from gateway.routes.email.automation import replyzero
    from pydantic import ValidationError

    for bad in ({"status": "DONE"}, {"status": "Follow-up"}, {}):
        with pytest.raises(ValidationError):
            replyzero.ThreadResolveRequest(
                account_id="a", thread_id="t", done=False, restore=bad)


# ── 4. The flag off, and the tenant ──────────────────────────────────────────

async def test_with_the_flag_off_the_route_and_the_writers_are_as_before(
    db, monkeypatch, provider,
):
    """(f) No ``previous``, no hold: a Needs Reply opens a Done thread, and a
    restore is a plain reopen."""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, "")
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, _sent, _undated, _nudge = _follow_up_thread(db, box)

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
    db, monkeypatch, provider,
):
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, *_ = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    _flag(monkeypatch, "")
    await _project(db, box, first, "REPLY")
    assert _row(db, box, thread).status == "NEEDS_REPLY"


async def test_the_two_routes_over_http(db, monkeypatch, provider):
    """The JSON of the app, through the real email router and its feature gate:
    the read of the flag, Done, a bad restore, and the move back."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes.email import core

    from tests.unit._tenant_ladder import tenant_engine_scope

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, *_ = _follow_up_thread(db, box)
    user = _user(db.org_b, "feature:email")

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(core.router)
    app.dependency_overrides[get_current_user] = _caller
    body = {"account_id": box, "thread_id": thread}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(db.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://email.test") as client,
        ):
            state = await client.get("/email/reply-zero/mark-done")
            done = await client.post("/email/reply-zero/resolve", json=body)
            bad = await client.post(
                "/email/reply-zero/resolve",
                json={**body, "done": False, "restore": {"status": "DONE"}})
            back = await client.post(
                "/email/reply-zero/resolve",
                json={**body, "done": False,
                      "restore": done.json()["previous"]})
            # The thread is no longer held, so a second move back is refused.
            again = await client.post(
                "/email/reply-zero/resolve",
                json={**body, "done": False,
                      "restore": {"status": "FYI", "follow_up": False}})
    finally:
        release_tenant(scope)

    assert (state.status_code, state.json()) == (200, {"enabled": True})
    assert done.json()["previous"] == {"status": "AWAITING", "follow_up": True}
    assert bad.status_code == 422
    assert back.json() == {"ok": True, "thread_id": thread, "done": False}
    assert again.status_code == 409
    assert _row(db, box, thread).status == "AWAITING"


async def test_a_member_of_another_organization_marks_nothing(
    db, monkeypatch, provider,
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


# ── 5. The races and the edges (fix round 2) ─────────────────────────────────

async def test_a_mark_done_during_the_follow_up_scan_stays_done(
    db, monkeypatch, provider,
):
    """(D1) The scan read its rows, and it is inside the label call of the
    first thread when the member marks the second one done. The second thread
    gets no "Follow-up" and no nudge draft."""
    from gateway.routes.email.automation import followups

    async def _nudge(*_a, **_kw):
        return "Any news on this?"

    monkeypatch.setattr(followups, "_agent_draft_reply", _nudge)
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    first_t, second_t = (f"{p}-{uuid.uuid4().hex[:6]}" for p in "ab")
    a = _mail(db, box, first_t, days_ago=7, folder="sent",
              categories=["Awaiting Reply"])
    b = _mail(db, box, second_t, days_ago=8, folder="sent",
              categories=["Awaiting Reply"])
    _status_row(db, box, first_t, "AWAITING", a, reason="r", days_ago=7)
    _status_row(db, box, second_t, "AWAITING", b, reason="r", days_ago=8)
    _follow_up_settings(db, box, auto_draft=True)
    pmid_a = _pmid(db, a)
    entered, release = asyncio.Event(), asyncio.Event()
    provider.pause = (lambda pmid, add, _r: pmid == pmid_a and "Follow-up" in add,
                      entered, release)

    async with _as_member(db, db.org_b):
        scan = asyncio.create_task(followups._maybe_send_follow_up_reminders(box))
        await asyncio.wait_for(entered.wait(), 30)
        await asyncio.wait_for(_resolve(db, box, second_t), 30)
        after_done = len(provider.calls)
        release.set()
        got = await asyncio.wait_for(scan, 30)

    assert _labels(db, b) == {"Done"}
    assert (got["labeled"], got["drafted"]) == (1, 1)
    assert provider.drafts == [first_t]
    pmid_b = _pmid(db, b)
    assert not [c for c in provider.calls[after_done:]
                if c[0] == pmid_b and set(c[1]) & _OPEN]
    assert provider.remote.get(pmid_b, set()) & _OPEN == set()
    assert _row(db, box, second_t).status == "DONE"


async def test_the_sweep_heals_a_label_that_came_with_no_delta(
    db, monkeypatch, provider,
):
    """(D9) An import or a folder load stored "Follow-up" on a held thread,
    so the learner saw no change. The sweep of the next sync cycle removes
    it, here and at the mail server."""
    from gateway.routes.email.automation import followups

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, sent, undated, nudge = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    _set_labels(db, sent, ["Follow-up"])
    provider.remote[_pmid(db, sent)] = {"Follow-up"}
    await _learn(db, box, {sent: (["Follow-up"], ["Follow-up"])})
    assert "Follow-up" in _labels(db, sent)  # no delta, so the learner left it

    async with _as_member(db, db.org_b):
        got = await followups._maybe_send_follow_up_reminders(box)

    assert got["configured"] is False  # the sweep runs with no follow-up setting
    assert not _all(db, first, sent, undated, nudge) & _OPEN
    assert provider.remote[_pmid(db, sent)] == set()
    assert _row(db, box, thread).reason == "Marked done by you"


async def test_a_hold_that_begins_during_a_rule_apply_ends_done(
    db, monkeypatch, provider,
):
    """(D6) The runner asked the hold, and it is inside its provider call when
    the member marks the thread done. The runner asks again after the call, so
    the thread ends with "Done" only, here and at the mail server."""
    from gateway.routes.email.automation import runner

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"r-{uuid.uuid4().hex[:6]}"
    first = _mail(db, box, thread, days_ago=6)
    _status_row(db, box, thread, "AWAITING", first, reason="r", days_ago=6)
    rule = {"rule": {"id": None, "name": "Needs Reply", "system_type": "REPLY",
                     "automated": True,
                     "actions": [{"type": "LABEL", "label": "Needs Reply"}]},
            "reason": "ai", "source": "ai"}
    row = _message(db, first)
    entered, release = asyncio.Event(), asyncio.Event()
    provider.pause = (lambda _p, add, _r: "Needs Reply" in add, entered, release)

    async def _block_w():
        async with _session(db) as s:
            await runner._apply_matches(
                s, provider, row, {"email": THEM}, {"subject": "s", "from": THEM},
                [rule], apply=True, dry_run=False, about="", signature="",
                account_user=ME, account_id=box, cold_blocker="OFF")

    async with _as_member(db, db.org_b):
        job = asyncio.create_task(_block_w())
        await asyncio.wait_for(entered.wait(), 30)
        await asyncio.wait_for(_resolve(db, box, thread), 30)
        release.set()
        await asyncio.wait_for(job, 30)

    got = _row(db, box, thread)
    assert (got.status, got.reason) == ("DONE", "Marked done by you")
    assert _labels(db, first) == {"Done"}
    assert provider.remote[_pmid(db, first)] == {"Done"}


async def test_approve_and_repair_run_no_rule_in_a_held_thread(
    db, monkeypatch, provider,
):
    """(D5) An approval of a proposal and the repair of a failed run each
    replay a rule. In a held thread neither runs, and History says why."""
    from gateway.routes.email.automation import mark_done, runner

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    out = {}
    for status in ("PENDING", "FAILED"):
        thread = f"p-{uuid.uuid4().hex[:6]}"
        first = _mail(db, box, thread, days_ago=6)
        _status_row(db, box, thread, "NEEDS_REPLY", first, reason="r", days_ago=6)
        rid = _rule(db, box, f"NR-{status}", "Needs Reply", None)
        await _resolve(db, box, thread)
        with db.admin_engine.begin() as c:
            eid = str(c.execute(text(
                "INSERT INTO email_executed_rules (account_id, rule_id, "
                "rule_name, message_id, provider_message_id, thread_id, subject, "
                "from_address, status, automated, actions_taken, action_errors, "
                "organization_id) VALUES (CAST(:a AS uuid), CAST(:r AS uuid), "
                "'x', CAST(:m AS uuid), :p, :t, 's', :f, :st, true, '{}', "
                "CAST('[]' AS jsonb), CAST(:o AS uuid)) RETURNING id"),
                {"a": box, "r": rid, "m": first, "p": _pmid(db, first),
                 "t": thread, "f": THEM, "st": status, "o": db.org_b}).scalar_one())
        provider.calls.clear()
        async with _as_member(db, db.org_b):
            if status == "PENDING":
                out[status] = await runner.approve_execution(
                    eid, user=_user(db.org_b, "feature:email"))
            else:
                out[status] = await runner.retry_failed_executions(
                    box, limit=10, user_email=ME)
        assert "Needs Reply" not in _labels(db, first), status
        assert provider.calls == [], status
        assert _row(db, box, thread).status == "DONE", status
        with db.admin_engine.connect() as c:
            logged = c.execute(text(
                "SELECT status, reason FROM email_executed_rules "
                "WHERE id = CAST(:e AS uuid)"), {"e": eid}).one()
        assert tuple(logged) == ("SKIPPED", mark_done.HELD_SKIP_REASON), status

    assert out["PENDING"]["status"] == "SKIPPED"
    assert (out["FAILED"]["considered"], out["FAILED"]["repaired"]) == (1, 0)


async def test_a_move_back_after_new_mail_changes_nothing(
    db, monkeypatch, provider,
):
    """(D4) A new inbound message ended the hold, and its rules set Needs
    Reply. A late Ctrl+Z answers 409 and writes nothing."""
    from fastapi import HTTPException

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"c-{uuid.uuid4().hex[:6]}"
    sent = _mail(db, box, thread, days_ago=6, folder="sent",
                 categories=["Awaiting Reply"])
    _status_row(db, box, thread, "AWAITING", sent, reason="r", days_ago=6)
    done, _ = await _resolve(db, box, thread)
    answer = _mail(db, box, thread, days_ago=-0.001)
    await _project(db, box, answer, "REPLY")
    before = _row(db, box, thread)
    assert before.status == "NEEDS_REPLY"

    with pytest.raises(HTTPException) as refused:
        await _resolve(db, box, thread, done=False, restore=done["previous"])

    assert refused.value.status_code == 409
    assert tuple(_row(db, box, thread)) == tuple(before)
    assert _labels(db, answer) == {"Needs Reply"}


@pytest.mark.parametrize("folder", ["inbox", "archive", "trash", "junk", "Sent Items"])
async def test_the_members_own_mail_in_any_folder_keeps_the_hold(
    db, monkeypatch, provider, folder,
):
    """(D7) The sender decides, not the folder. A copy of the member's own
    mail keeps the hold, and mail of another person in the same folder ends
    it."""
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    me = _address(db.admin_engine, box)
    thread = f"h-{uuid.uuid4().hex[:6]}"
    sent = _mail(db, box, thread, days_ago=6, folder="sent",
                 categories=["Awaiting Reply"])
    _status_row(db, box, thread, "AWAITING", sent, reason="r", days_ago=6)
    await _resolve(db, box, thread)

    _mail(db, box, thread, days_ago=-0.01, folder=folder, sender=me.upper())
    async with _session(db) as s:
        assert await mark_done.thread_is_held(s, box, thread)
    _mail(db, box, thread, days_ago=-0.02, folder=folder)
    async with _session(db) as s:
        assert not await mark_done.thread_is_held(s, box, thread)


async def test_inbound_mail_that_waits_for_its_rules_ends_the_hold(
    db, monkeypatch, provider,
):
    """(D8) Mail that arrived after the last status write, and that no rule
    run has seen, is not under the anchor. Its classification sets the status.
    The same mail after a rule run is under the anchor."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    for processed, status in ((False, "NEEDS_REPLY"), (True, "DONE")):
        thread = f"d-{uuid.uuid4().hex[:6]}"
        sent = _mail(db, box, thread, days_ago=6, folder="sent",
                     categories=["Awaiting Reply"])
        _status_row(db, box, thread, "AWAITING", sent, reason="r", days_ago=6)
        unseen = _mail(db, box, thread, days_ago=0.001)
        if processed:
            with db.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE email_messages SET rules_processed_at = now() "
                    "WHERE id = CAST(:m AS uuid)"), {"m": unseen})

        got, _ = await _resolve(db, box, thread)
        assert got["previous"] == {"status": "AWAITING", "follow_up": False}
        await _project(db, box, unseen, "REPLY")

        assert _row(db, box, thread).status == status, processed
        assert ("Needs Reply" in _labels(db, unseen)) is (not processed)


# ── 6. The locks (fix round 3) ───────────────────────────────────────────────
#
# Two connections: the real scan or the real rules job in one task, the click
# of the member on the SAME thread in another. A fake I/O call stops the writer
# at a chosen point. The click must answer while the writer still waits in its
# I/O, so the request waits for no provider call and no model call (F1, F2).

#: The longest time that a click may take while a writer waits in its I/O.
_CLICK_S = 10


def _deadlocks(db) -> int:
    with db.admin_engine.connect() as c:
        c.execute(text("SELECT pg_stat_clear_snapshot()"))
        return int(c.execute(text(
            "SELECT deadlocks FROM pg_stat_database "
            "WHERE datname = current_database()")).scalar_one() or 0)


async def _no_deadlock(db, before: int, caps: list[dict]) -> None:
    """No deadlock in the database, and no writer or job failed."""
    # The server counts a deadlock when the stats of the backend flush.
    await asyncio.sleep(1.2)
    assert _deadlocks(db) == before, "the database counted a deadlock"
    failed = [e for e in caps if e.get("log_level") in ("warning", "error")
              and (e.get("event", "").endswith("_failed")
                   or "deadlock" in json.dumps(e, default=str).lower())]
    assert failed == [], failed


def _quick_jobs(monkeypatch) -> None:
    from gateway.routes.email.automation import mark_done

    monkeypatch.setattr(mark_done, "SETTLE_DELAYS",
                        (0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2), raising=False)


async def _click_during(db, box: str, thread: str, entered: asyncio.Event,
                        release: asyncio.Event, writer: asyncio.Task, *,
                        jobs: bool = True) -> dict:
    """The member clicks Mark done while *writer* waits in its I/O. The click
    must answer before the I/O ends. Its label job then runs beside the
    writer, as it does after a real answer."""
    try:
        await asyncio.wait_for(entered.wait(), 30)
        got, done_jobs = await asyncio.wait_for(
            _resolve(db, box, thread, run_jobs=False), _CLICK_S)
        job = asyncio.create_task(done_jobs()) if jobs else None
    finally:
        release.set()
    await asyncio.wait_for(writer, 60)
    if job is not None:
        await asyncio.wait_for(job, 60)
    return got


def _scan_threads(db, box: str) -> tuple[str, str, str, str, str]:
    """Two Awaiting threads in the Follow-up list. The scan reads T first,
    because its last message is newer. Returns ``(T, inbound of T, sent of T,
    U, sent of U)``."""
    t, u = (f"{p}-{uuid.uuid4().hex[:6]}" for p in "tu")
    inbound = _mail(db, box, t, days_ago=9)
    sent = _mail(db, box, t, days_ago=7, folder="sent",
                 categories=["Awaiting Reply"])
    other = _mail(db, box, u, days_ago=8, folder="sent",
                  categories=["Awaiting Reply"])
    _status_row(db, box, t, "AWAITING", sent, reason="r", days_ago=7)
    _status_row(db, box, u, "AWAITING", other, reason="r", days_ago=8)
    _follow_up_settings(db, box, auto_draft=True)
    return t, inbound, sent, u, other


def _assert_done(db, provider: _Provider, box: str, thread: str,
                 *message_ids: str) -> None:
    """The final state of a thread that the member marked done: the status,
    the local labels and the mail server."""
    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    assert not _all(db, *message_ids) & _OPEN
    assert "Done" in _all(db, *message_ids)
    _remote_matches_mirror(db, provider, *message_ids)


@pytest.mark.parametrize("timing", ["label", "model", "later_thread"])
async def test_a_click_during_the_follow_up_scan_never_waits_and_stays_done(
    db, monkeypatch, provider, timing,
):
    """(F1, F2) The real scan, and a Mark done of the thread T in its work:
    during the Follow-up label call of T, during the model call of the nudge
    of T, and while the scan waits in the I/O of a later thread U after it
    wrote T. The click answers at once. No deadlock, and T ends Done here and
    at the mail server, with no nudge drafted after the click."""
    import structlog
    from gateway.routes.email.automation import followups

    entered, release = asyncio.Event(), asyncio.Event()
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    t, inbound, sent, u, other = _scan_threads(db, box)
    _seed_remote(db, provider, inbound, sent, other)
    pmid_sent, pmid_other = _pmid(db, sent), _pmid(db, other)

    async def _nudge(email, *_a, **_kw):
        if timing == "model" and email.get("thread_id") == t:
            entered.set()
            await release.wait()
        return "Any news on this?"

    monkeypatch.setattr(followups, "_agent_draft_reply", _nudge)
    if timing == "label":
        provider.pause = (lambda p, add, _r: p == pmid_sent and "Follow-up" in add,
                          entered, release)
    elif timing == "later_thread":
        provider.pause = (lambda p, add, _r: p == pmid_other and "Follow-up" in add,
                          entered, release)
    _flag(monkeypatch, db.org_b)
    _quick_jobs(monkeypatch)
    before = _deadlocks(db)

    with structlog.testing.capture_logs() as caps:
        async with _as_member(db, db.org_b):
            scan = asyncio.create_task(
                followups._maybe_send_follow_up_reminders(box))
            await _click_during(db, box, t, entered, release, scan)
            got = scan.result()

    await _no_deadlock(db, before, caps)
    assert got["scanned"] == 2, got  # the block of the scan did not fail
    _assert_done(db, provider, box, t, inbound, sent)
    # A nudge of T exists only when the scan drafted it before the click.
    assert provider.drafts == ([t, u] if timing == "later_thread" else [u])
    assert "Follow-up" in _labels(db, other)
    assert _row(db, box, u).status == "AWAITING"


def _runner_thread(db, box: str) -> tuple[str, str, str]:
    """A thread whose new inbound mail waits for the rules job. A static rule
    of Needs Reply matches its sender, with two label actions. Returns
    ``(thread, sent, inbound)``."""
    thread = f"r-{uuid.uuid4().hex[:6]}"
    sent = _mail(db, box, thread, days_ago=3, folder="sent",
                 categories=["Awaiting Reply"])
    inbound = _mail(db, box, thread, days_ago=0.001)
    _status_row(db, box, thread, "AWAITING", sent, reason="r", days_ago=3)
    # The status was written after the mail arrived, so the mail is under the
    # anchor of a Done (D8), and the hold begins at the click.
    with db.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE email_thread_status SET classified_at = now() "
            "WHERE account_id = CAST(:a AS uuid) AND thread_id = :t"),
            {"a": box, "t": thread})
    with db.admin_engine.begin() as c:
        rid = str(c.execute(text(
            "INSERT INTO email_rules (account_id, name, system_type, "
            "from_pattern, enabled, organization_id) VALUES (CAST(:a AS uuid), "
            "'Needs Reply', 'REPLY', :f, true, CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "f": THEM, "o": db.org_b}).scalar_one())
        for label in ("Needs Reply", "Customer"):
            c.execute(text(
                "INSERT INTO email_actions (rule_id, type, label, "
                "organization_id) VALUES (CAST(:r AS uuid), 'LABEL', :l, "
                "CAST(:o AS uuid))"), {"r": rid, "l": label, "o": db.org_b})
    return thread, sent, inbound


@pytest.mark.parametrize("timing", ["ask", "action", "reconcile"])
async def test_a_click_during_the_rules_job_never_waits_and_stays_done(
    db, monkeypatch, provider, timing,
):
    """(F1, F2) The real rules job (Block R, the ask, Block W), and a Mark
    done of the thread in its work: during the rule-match ask (no block
    open), during the label call of the rule's action (the block holds the
    message rows), and during the provider call of the reconcile (before the
    status row, with the flag on). The click answers at once. No deadlock,
    and the thread ends Done here and at the mail server."""
    import structlog
    from gateway.routes.email.automation import replyzero, runner

    async def _says_reply(*_a, **_kw):
        return "REPLY", True

    monkeypatch.setattr(replyzero, "_llm_determine_thread_status", _says_reply)
    entered, release = asyncio.Event(), asyncio.Event()
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, sent, inbound = _runner_thread(db, box)
    _seed_remote(db, provider, sent, inbound)
    pmid_in = _pmid(db, inbound)
    if timing == "ask":
        real_ask = runner.ask_rule_match

        async def _ask(*a, **kw):
            entered.set()
            await release.wait()
            return await real_ask(*a, **kw)

        monkeypatch.setattr(runner, "ask_rule_match", _ask)
    elif timing == "action":
        # The second action: the first one already wrote its local label, so
        # the block holds the row of the inbound mail.
        provider.pause = (lambda p, add, _r: p == pmid_in and "Customer" in add,
                          entered, release)
    else:
        # The reconcile takes "Awaiting Reply" off the sent mail.
        pmid_sent = _pmid(db, sent)
        provider.pause = (
            lambda p, _a, rem: p == pmid_sent and "Awaiting Reply" in rem,
            entered, release)
    _flag(monkeypatch, db.org_b)
    _quick_jobs(monkeypatch)
    before = _deadlocks(db)

    with structlog.testing.capture_logs() as caps:
        async with _as_member(db, db.org_b):
            job = asyncio.create_task(runner._run_rules_job(box, 50, False, ME))
            await _click_during(db, box, thread, entered, release, job)

    await _no_deadlock(db, before, caps)
    _assert_done(db, provider, box, thread, sent, inbound)
    assert _message(db, inbound).rules_processed_at is not None


@pytest.mark.parametrize("body", [{"done": False}, {"dismiss": True}],
                         ids=["reopen", "dismiss"])
async def test_a_later_write_of_the_member_beats_an_older_done_job(
    db, monkeypatch, provider, body,
):
    """(F3) The label job of Done is inside its first provider call when the
    member reopens the thread (the chat tool sends the same body) or
    dismisses it. The mail server ends with the labels of the later write,
    and a reopen says so in its reason."""
    _flag(monkeypatch, db.org_b)
    _quick_jobs(monkeypatch)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"o-{uuid.uuid4().hex[:6]}"
    first = _mail(db, box, thread, days_ago=6, categories=["Needs Reply"])
    _status_row(db, box, thread, "NEEDS_REPLY", first, reason="r", days_ago=6)
    _seed_remote(db, provider, first)
    entered, release = asyncio.Event(), asyncio.Event()
    provider.pause = (lambda _p, add, _r: "Done" in add, entered, release)
    _done, done_jobs = await _resolve(db, box, thread, run_jobs=False)

    async with _as_member(db, db.org_b):
        job = asyncio.create_task(done_jobs())
        try:
            await asyncio.wait_for(entered.wait(), 30)
            await asyncio.wait_for(_resolve(db, box, thread, **body), 30)
        finally:
            release.set()
        await asyncio.wait_for(job, 30)

    assert "Done" not in provider.remote[_pmid(db, first)]
    assert "Done" not in _labels(db, first)
    _remote_matches_mirror(db, provider, first)
    row = _row(db, box, thread)
    want = ("NEEDS_REPLY", "Reopened by you") if "done" in body else (
        "FYI", "Dismissed by the user")
    assert (row.status, row.reason) == want


async def test_a_move_back_after_the_members_own_reply_changes_nothing(
    db, monkeypatch, provider,
):
    """(F4) The member replied after Done. The state of before is no longer
    true, so a move back answers 409 and the thread stays held."""
    from fastapi import HTTPException
    from gateway.routes.email.automation import mark_done

    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"m-{uuid.uuid4().hex[:6]}"
    first = _mail(db, box, thread, days_ago=6, categories=["Needs Reply"])
    _status_row(db, box, thread, "NEEDS_REPLY", first, reason="r", days_ago=6)
    done, _ = await _resolve(db, box, thread)
    assert done["previous"] == {"status": "NEEDS_REPLY", "follow_up": False}
    _mail(db, box, thread, days_ago=-0.001, folder="sent")
    # A new draft alone does not count.
    _mail(db, box, thread, days_ago=-0.002, folder="drafts")
    before = _row(db, box, thread)

    with pytest.raises(HTTPException) as refused:
        await _resolve(db, box, thread, done=False, restore=done["previous"])

    assert refused.value.status_code == 409
    assert tuple(_row(db, box, thread)) == tuple(before)
    async with _session(db) as s:
        assert await mark_done.thread_is_held(s, box, thread)


async def test_a_move_back_with_only_a_new_draft_still_works(
    db, monkeypatch, provider,
):
    """(F4) The control: a draft is not a reply."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"m-{uuid.uuid4().hex[:6]}"
    first = _mail(db, box, thread, days_ago=6, categories=["Needs Reply"])
    _status_row(db, box, thread, "NEEDS_REPLY", first, reason="r", days_ago=6)
    done, _ = await _resolve(db, box, thread)
    _mail(db, box, thread, days_ago=-0.002, folder="drafts")
    got, _ = await _resolve(db, box, thread, done=False, restore=done["previous"])
    assert got["done"] is False
    assert _row(db, box, thread).status == "NEEDS_REPLY"


@pytest.mark.parametrize("path", ["approve", "repair"])
async def test_a_hold_that_begins_during_an_approval_or_a_repair_ends_done(
    db, monkeypatch, provider, path,
):
    """(F5) The approval of a proposal and the repair of a failed run ask
    the hold before they run, and the member clicks during their label call.
    They ask again after it, so the label of the rule does not stay. The
    label job of the click does not run here, so only that second ask can
    put "Done" back."""
    _flag(monkeypatch, db.org_b)
    from gateway.routes.email.automation import runner

    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread = f"p-{uuid.uuid4().hex[:6]}"
    first = _mail(db, box, thread, days_ago=6)
    _status_row(db, box, thread, "NEEDS_REPLY", first, reason="r", days_ago=6)
    rid = _rule(db, box, f"NR-{path}", "Needs Reply", None)
    with db.admin_engine.begin() as c:
        eid = str(c.execute(text(
            "INSERT INTO email_executed_rules (account_id, rule_id, "
            "rule_name, message_id, provider_message_id, thread_id, subject, "
            "from_address, status, automated, actions_taken, action_errors, "
            "organization_id) VALUES (CAST(:a AS uuid), CAST(:r AS uuid), "
            "'x', CAST(:m AS uuid), :p, :t, 's', :f, :st, true, '{}', "
            "CAST('[]' AS jsonb), CAST(:o AS uuid)) RETURNING id"),
            {"a": box, "r": rid, "m": first, "p": _pmid(db, first),
             "t": thread, "f": THEM,
             "st": "PENDING" if path == "approve" else "FAILED",
             "o": db.org_b}).scalar_one())
    entered, release = asyncio.Event(), asyncio.Event()
    provider.pause = (lambda _p, add, _r: "Needs Reply" in add, entered, release)

    async def _work():
        if path == "approve":
            return await runner.approve_execution(
                eid, user=_user(db.org_b, "feature:email"))
        return await runner.retry_failed_executions(box, limit=10, user_email=ME)

    async with _as_member(db, db.org_b):
        work = asyncio.create_task(_work())
        await _click_during(db, box, thread, entered, release, work, jobs=False)

    row = _row(db, box, thread)
    assert (row.status, row.reason) == ("DONE", "Marked done by you")
    assert _labels(db, first) == {"Done"}
    assert provider.remote[_pmid(db, first)] == {"Done"}


async def test_an_automated_write_on_a_held_row_keeps_the_reminder(
    db, monkeypatch, provider,
):
    """(F6) An automated write moves ``last_message_id`` of a held row. It
    must not re-arm the follow-up reminder, so "Move back" gives no second
    nudge."""
    _flag(monkeypatch, db.org_b)
    box = _account(db.admin_engine, org=db.org_b, owner=ME)
    thread, first, *_ = _follow_up_thread(db, box)
    await _resolve(db, box, thread)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE email_thread_status SET follow_up_reminded_at = now() "
            "WHERE account_id = CAST(:a AS uuid) AND thread_id = :t"),
            {"a": box, "t": thread})
    assert _row(db, box, thread).mid != first

    await _project(db, box, first, "REPLY")

    with db.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT status, last_message_id::text AS mid, follow_up_reminded_at "
            "FROM email_thread_status WHERE account_id = CAST(:a AS uuid) "
            "AND thread_id = :t"), {"a": box, "t": thread}).one()
    assert (got.status, got.mid) == ("DONE", first)
    assert got.follow_up_reminded_at is not None
