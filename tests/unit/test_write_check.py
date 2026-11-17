"""The write check (LOOP-18, ``proactive_loop.md`` §4.9). Pure: a fake reader.

Each kind has its positive fixture and its negative fixtures (§4.9.3):
a reply in a long thread, a weekly report to the same address, a resend that
the member asked for, and a subject that matches only on stop words. The check
stays in its budget, and a finding holds no text of a mail.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from gateway import write_check as wc

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
ME = "me@acme.test"
SECRET_SUBJECT = "Zebrafish licence"
SECRET_FILE = "Quokka_Agreement_2026-11-12.pdf"


class FakeReader:
    def __init__(self, **kw: Any) -> None:
        self.sent: list[dict[str, Any]] = kw.get("sent", [])
        self.newer: list[dict[str, Any]] = kw.get("newer", [])
        self.task: dict[str, Any] | None = kw.get("task")
        self.open: list[dict[str, Any]] = kw.get("open", [])
        self.delay: float = kw.get("delay", 0.0)
        self.boom: bool = kw.get("boom", False)
        self.calls: list[str] = []

    async def _wait(self, name: str) -> None:
        self.calls.append(name)
        if self.boom:
            raise RuntimeError("db is down")
        if self.delay:
            await asyncio.sleep(self.delay)

    async def sent_mail(self, recipients, since, exclude_thread_of):
        await self._wait("sent_mail")
        return [r for r in self.sent if r["sent_at"] >= since]

    async def thread_newer(self, email_id):
        await self._wait("thread_newer")
        return self.newer

    async def task_state(self, task_id):
        await self._wait("task_state")
        return self.task

    async def open_tasks(self, project_id):
        await self._wait("open_tasks")
        return self.open


def _sent(days_ago: int = 5, subject: str = SECRET_SUBJECT, files: tuple[str, ...] = (),
          to: str = "mahesh@vendor.test", n: int = 1) -> dict[str, Any]:
    return {"id": f"m{n}", "account_id": "a1", "thread_id": f"t{n}", "subject": subject,
            "sent_at": NOW - timedelta(days=days_ago), "to": [to],
            "file_stems": frozenset().union(*(wc.file_stem_words(f) for f in files))}


async def _run(tool: str, args: dict[str, Any], reader: FakeReader, mode: str = "on", **kw: Any):
    return await wc.check_write(tool, args, member=ME, reader=reader, mode=mode, now=NOW, **kw)


SEND = {"to": ["mahesh@vendor.test"], "subject": "Licence terms", "body": "x"}


# ── The words ──────────────────────────────────────────────────────────────

def test_the_stop_list_drops_glue_numbers_days_and_months():
    assert wc.words("Re: Fwd: Update - Monday 12 November follow up") == frozenset()
    assert wc.words("Re: NDA for Acme 2026") == frozenset({"nda", "acme"})


def test_a_file_name_counts_without_its_extension_and_date_stamp():
    assert wc.file_stem_words("NDA_Acme_2026-11-12.pdf") == frozenset({"nda", "acme"})
    assert wc.file_stem_words("/tmp/x/NDA Acme 20261112.docx") == frozenset({"nda", "acme"})
    assert wc.file_stem_words("report.pdf") == frozenset({"report"})


def test_a_version_or_a_final_in_a_file_name_is_not_a_topic():
    assert wc.file_stem_words("Report_v2_final.pdf") == frozenset({"report"})
    assert wc.file_stem_words("Deck_final.pptx") == frozenset({"deck"})


def test_an_id_form_of_an_attachment_adds_no_word():
    i = wc.intent_of("send_email", {"to": ["a@b.test"], "subject": "Hi",
                                    "attachments": ["task-file:3f2a1b9c-4abc-9def", "knowledge:0a1b2c3d",
                                                    "outputs/NDA_Acme.pdf", "plain"]})
    assert i.file_stems == frozenset({"nda", "acme"})


# ── The intent ─────────────────────────────────────────────────────────────

def test_the_intent_comes_from_the_typed_arguments_only():
    i = wc.intent_of("send_email", {"to": ["Mahesh <Mahesh@Vendor.test>"], "cc": "b@x.test",
                                    "subject": "Re: NDA for Acme", "body": "long text " * 50,
                                    "attachments": ["/f/NDA_Acme_2026-11-12.pdf"]})
    assert i.recipients == ("b@x.test", "mahesh@vendor.test")
    assert i.topic == frozenset({"nda", "acme"}) and i.file_stems == frozenset({"nda", "acme"})
    assert wc.intent_of("nudge_task", {"task_id": "t1"}).task_id == "t1"
    assert wc.intent_of("create_task", {"project_id": "p1", "title": "Fix the login page"}).project_id == "p1"
    assert wc.intent_of("search_emails", {"query": "x"}) is None


def test_a_resend_the_member_asked_for_is_marked():
    assert wc.intent_of("send_email", {"to": ["a@b.test"], "subject": "Resending the NDA"}).resend
    assert wc.intent_of("send_email", {"to": ["a@b.test"], "subject": "NDA again"}).resend
    assert not wc.intent_of("send_email", {"to": ["a@b.test"], "subject": "NDA"}).resend


# ── write_already_sent ─────────────────────────────────────────────────────

async def test_already_sent_fires_on_the_same_address_and_a_subject_word():
    out = await _run("send_email", SEND, FakeReader(sent=[_sent()]))
    assert [f["kind"] for f in out] == ["write_already_sent"]
    f = out[0]
    assert f["facts"]["sent_on"] == "2026-11-11" and f["evidence"][0]["id"] == "m1"
    assert f["visibility_scope"] == [ME]


async def test_already_sent_fires_on_a_file_name():
    args = {"to": ["mahesh@vendor.test"], "subject": "Hello", "attachments": ["/x/Quokka_Agreement_v2.pdf"]}
    out = await _run("send_email", args, FakeReader(sent=[_sent(subject="Hi", files=(SECRET_FILE,))]))
    assert [f["kind"] for f in out] == ["write_already_sent"]


@pytest.mark.parametrize("name,sent,args", [
    ("another address", [_sent(to="zoe@other.test")], SEND),
    ("older than 30 days", [_sent(days_ago=45)], SEND),
    ("a subject that matches only on stop words", [_sent(subject="Re: Update, Monday 12")],
     {"to": ["mahesh@vendor.test"], "subject": "Fwd: Update Tuesday 13"}),
    ("a different topic", [_sent(subject="Holiday plans")], SEND),
    ("a resend the member asked for", [_sent()],
     {"to": ["mahesh@vendor.test"], "subject": "Resending: Licence terms"}),
    ("a weekly report to the same address",
     [_sent(days_ago=d, subject="Weekly report", n=d) for d in (3, 10, 17, 24)],
     {"to": ["mahesh@vendor.test"], "subject": "Weekly report"}),
    ("no topic and no file to match on", [_sent()], {"to": ["mahesh@vendor.test"], "subject": "Hi"}),
    ("no recipient", [_sent()], {"subject": "Licence terms"}),
])
async def test_already_sent_stays_quiet(name, sent, args):
    assert await _run("send_email", args, FakeReader(sent=sent)) == [], name


async def test_a_frequent_contact_does_not_hide_a_real_resend():
    """Three mails to the same address that share only "acme" are not a routine."""
    sent = [_sent(days_ago=3, subject="NDA for Acme", files=("NDA_Acme.pdf",), n=1),
            _sent(days_ago=6, subject="Acme pilot kickoff", n=2),
            _sent(days_ago=9, subject="Acme pilot pricing", n=3)]
    args = {"to": ["mahesh@vendor.test"], "subject": "NDA for Acme", "attachments": ["outputs/NDA_Acme.pdf"]}
    out = await _run("send_email", args, FakeReader(sent=sent))
    assert [f["kind"] for f in out] == ["write_already_sent"]
    assert out[0]["evidence"][0]["id"] == "m1" and out[0]["facts"]["count"] == 1


async def test_a_reply_in_a_long_thread_is_not_a_resend():
    """The reader leaves out the thread of the email that the reply answers."""
    class ThreadAware(FakeReader):
        async def sent_mail(self, recipients, since, exclude_thread_of):
            self.excluded = exclude_thread_of
            return []  # the real reader returns no row of that thread
    r = ThreadAware()
    out = await _run("send_email", {**SEND, "reply_to_email_id": "e-42"}, r)
    assert out == [] and r.excluded == "e-42"


# ── write_thread_moved ─────────────────────────────────────────────────────

async def test_thread_moved_fires_for_a_reply_with_a_newer_message():
    newer = [{"id": "n1", "account_id": "a1", "thread_id": "t9", "received_at": NOW - timedelta(hours=1)}]
    out = await _run("send_email", {**SEND, "reply_to_email_id": "e-1"}, FakeReader(newer=newer))
    assert [f["kind"] for f in out] == ["write_thread_moved"]
    assert out[0]["entity"]["label"] == "a new message on this thread"


async def test_thread_moved_stays_quiet_for_a_new_mail_and_for_no_newer_message():
    newer = [{"id": "n1", "account_id": "a1", "thread_id": "t9", "received_at": NOW}]
    assert await _run("send_email", SEND, FakeReader(newer=newer)) == []
    assert await _run("send_email", {**SEND, "reply_to_email_id": "e-1"}, FakeReader(newer=[])) == []


# ── write_task_settled ─────────────────────────────────────────────────────

def _task(**kw: Any) -> dict[str, Any]:
    return {"id": "t1", "done": False, "assignees": ["pri@acme.test"], "last_nudged_at": None, **kw}


@pytest.mark.parametrize("tool,args,task,reason", [
    ("nudge_task", {"task_id": "t1"}, _task(done=True), "done"),
    ("nudge_task", {"task_id": "t1"}, _task(last_nudged_at=NOW - timedelta(hours=3)), "nudged"),
    ("assign", {"task_id": "t1", "assignees": "Pri@acme.test"}, _task(), "assigned"),
])
async def test_task_settled_fires(tool, args, task, reason):
    out = await _run(tool, args, FakeReader(task=task))
    assert [f["kind"] for f in out] == ["write_task_settled"]
    assert out[0]["facts"]["reason"] == reason


@pytest.mark.parametrize("tool,args,task", [
    ("nudge_task", {"task_id": "t1"}, _task(last_nudged_at=NOW - timedelta(hours=30))),
    ("nudge_task", {"task_id": "t1"}, _task()),
    ("nudge_task", {"task_id": "t1"}, None),  # a task that the member cannot open
    ("assign", {"task_id": "t1", "assignees": "other@acme.test"}, _task()),
    # `assign` replaces the set: a smaller set removes people, it is not "already assigned".
    ("assign", {"task_id": "t1", "assignees": "pri@acme.test"},
     _task(assignees=["pri@acme.test", "ben@acme.test"])),
])
async def test_task_settled_stays_quiet(tool, args, task):
    assert await _run(tool, args, FakeReader(task=task)) == []


# ── write_duplicate_task ───────────────────────────────────────────────────

async def test_duplicate_task_fires_on_a_matching_title_of_the_same_project():
    r = FakeReader(open=[{"id": "x1", "title": "Fix the login page"}])
    out = await _run("create_task", {"project_id": "p1", "title": "fix login page!"}, r)
    assert [f["kind"] for f in out] == ["write_duplicate_task"]
    assert out[0]["evidence"][0]["id"] == "x1"


@pytest.mark.parametrize("title,open_title", [
    ("Fix the login page", "Fix the signup page"),
    ("Pay invoice 1043", "Pay invoice 1042"),            # numbers count in a title
    ("Close the April books", "Close the March books"),  # months count in a title
    ("Review 2026", "Review 2027"),
])
async def test_duplicate_task_stays_quiet_when_titles_differ(title, open_title):
    r = FakeReader(open=[{"id": "x1", "title": open_title}])
    assert await _run("create_task", {"project_id": "p1", "title": title}, r) == []


async def test_duplicate_task_fires_on_the_same_numbered_title():
    r = FakeReader(open=[{"id": "x1", "title": "Pay invoice 1042"}])
    out = await _run("create_task", {"project_id": "p1", "title": "pay invoice 1042"}, r)
    assert [f["kind"] for f in out] == ["write_duplicate_task"]


# ── The finding (§6.1) and the label rule (§4.9.2) ─────────────────────────

async def test_each_finding_has_the_fields_of_6_1():
    out = await _run("send_email", {**SEND, "reply_to_email_id": "e-1"},
                     FakeReader(sent=[_sent()], newer=[{"id": "n1", "account_id": "a1",
                                                        "thread_id": "t9", "received_at": NOW}]))
    assert {f["kind"] for f in out} == {"write_already_sent", "write_thread_moved"}
    for f in out:
        assert set(f) >= {"kind", "entity", "signal_id", "fingerprint", "facts", "expires_at",
                          "acts", "href", "evidence", "confidence", "visibility_scope",
                          "severity_hint", "batch_key"}
        assert 1 <= len(f["evidence"]) <= 3 and f["visibility_scope"] == [ME]
        assert f["entity"]["type"] in {"task", "deal", "thread", "project", "metric"}


async def test_a_finding_holds_no_text_of_a_mail():
    """§4.9.2: no subject, file name or name of a mail reaches the model."""
    sent = _sent(subject=f"Re: {SECRET_SUBJECT}", files=(SECRET_FILE,))
    args = {"to": ["mahesh@vendor.test"], "subject": SECRET_SUBJECT, "reply_to_email_id": "e-1",
            "attachments": [SECRET_FILE]}
    out = await _run("send_email", args, FakeReader(sent=[sent], newer=[
        {"id": "n1", "account_id": "a1", "thread_id": "t9", "received_at": NOW}]))
    assert out
    dump = json.dumps(out, default=str).lower()
    for secret in ("zebrafish", "licence", "quokka", "agreement", "mahesh"):
        assert secret not in dump, secret


# ── Modes, budget, failure ─────────────────────────────────────────────────

async def test_off_runs_nothing_and_shadow_returns_nothing_but_logs(caplog):
    r = FakeReader(sent=[_sent()])
    assert await _run("send_email", SEND, r, mode="off") == [] and r.calls == []
    with caplog.at_level(logging.INFO):
        assert await _run("send_email", SEND, r, mode="shadow") == []
    lines = [rec for rec in caplog.records if rec.message == "write_check"]
    assert {rec.kind for rec in lines} == set(wc.KINDS)
    assert {rec.kind: rec.status for rec in lines}["write_already_sent"] == "fired"
    assert all(rec.mode == "shadow" and rec.ms >= 0 for rec in lines)


async def test_an_unknown_mode_and_an_unchecked_tool_run_nothing():
    r = FakeReader(sent=[_sent()])
    assert await _run("send_email", SEND, r, mode="loud") == []
    assert await _run("search_emails", {"query": "x"}, r) == [] and r.calls == []
    assert await wc.check_write("send_email", SEND, member="", reader=r, mode="on", now=NOW) == []


async def test_the_cap_of_findings_is_enforced(monkeypatch):
    newer = [{"id": "n1", "account_id": "a1", "thread_id": "t9", "received_at": NOW}]
    args = {**SEND, "reply_to_email_id": "e-1"}
    reader = FakeReader(sent=[_sent()], newer=newer)
    assert len(await _run("send_email", args, reader)) == 2
    monkeypatch.setattr(wc, "MAX_SHOWN", 1)
    assert len(await _run("send_email", args, reader)) == 1


async def test_a_slow_read_drops_and_the_check_stays_in_its_budget(caplog):
    r = FakeReader(sent=[_sent()], delay=2.0)
    start = time.monotonic()
    with caplog.at_level(logging.INFO):
        out = await _run("send_email", SEND, r, budget_s=0.2)
    assert out == [] and time.monotonic() - start < 0.6
    assert any(rec.message == "write_check" and rec.status == "late" for rec in caplog.records)


async def test_a_failed_read_is_a_quiet_check_not_a_failed_write(caplog):
    with caplog.at_level(logging.INFO):
        assert await _run("send_email", SEND, FakeReader(boom=True)) == []
    assert any(str(getattr(rec, "status", "")).startswith("error:") for rec in caplog.records)


async def test_the_default_budget_is_800_ms():
    assert wc.BUDGET_S == 0.8
