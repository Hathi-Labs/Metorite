"""WS-53 CRM-Q33 fix round 1 — the security review of PR #1151, hermetic half.

Spec: ``project-docs/specs/crm_platform.md`` §18.15.10. The review found two
P0s, a P1, two P2s and one more item:

* P0-1 and P0-2: a card that was only SHOWN opened the gate of a pick. Now a
  pick counts only when the member clicked it: ``ask_questions`` hands the
  gateway's answer to ``crm_tools.record_card_answer``, and the gate takes
  that key once. ``test_crm_answer_first.py`` holds those cases.
* P1: ``member_said`` needs every word, number and negation of a rule.
  ``test_crm_answer_first.py`` holds those cases.
* P2: on WhatsApp, a parked act owns the next message, so the CRM's pending
  ask waits and none is offered. A reply that asks its own numbered choice
  gets no CRM question.
* P2: the purge reaches the CRM: the member's own rules, the address on an
  org rule and in ``zoho:questions``, and the member's CRM keys in Redis. A
  fence fails when a new table keyed by a member email is not in the purge.
* The note: when the answer leaves out "How I got this", the run adds it,
  on the web (``executor._crm_note_events``) and on WhatsApp (``bot_run``).

``test_crm_answer_first_r8.py`` proves the purge on a real Postgres.
"""

# ruff: noqa: F811 - the fixture `db` comes in by import and is used as an argument
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import acb_skills.crm_tools as _T
import pytest

from tests.unit.test_admin_member_purge import db  # noqa: F401 - a fixture

REPO = Path(__file__).resolve().parents[2]
NOTE = ("How I got this: IP Basic Amount (excludes GST). This assumes the field that the AI "
        "found most likely.")


# ── The note: the run adds it when the answer left it out ───────────────────


@pytest.fixture
def run():
    token = _T.open_asks_for_run()
    yield _T.run_asks()
    _T.release_asks(token)


def _text(delta: str) -> dict[str, Any]:
    return {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": delta}


def test_a_streamed_answer_with_no_note_gets_it_once_before_the_end(run) -> None:
    _T._register_note(NOTE)
    _T.observe_event(_text("Collection in October: INR 12,50,000."))
    events = _T.missing_note_events()
    assert [e["type"] for e in events] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert events[1]["delta"].strip() == NOTE
    assert len({e["messageId"] for e in events}) == 1
    assert _T.missing_note_events() == []  # once


def test_a_streamed_answer_that_showed_the_note_gets_nothing(run) -> None:
    _T._register_note(NOTE)
    # The phrase may arrive split across two deltas.
    _T.observe_event(_text("Total: INR 12,50,000. How I"))
    _T.observe_event(_text(" got this: IP Basic Amount."))
    assert _T.missing_note_events() == []


def test_a_run_with_no_note_adds_nothing(run) -> None:
    _T.observe_event(_text("Hello."))
    assert _T.missing_note_events() == []
    token = _T._RUN_ASKS.set(None)
    try:
        assert _T.missing_note_events() == []
    finally:
        _T._RUN_ASKS.reset(token)


def test_the_whatsapp_reply_gets_the_note_when_the_model_left_it_out() -> None:
    holder = _T.TextAsks(channel="whatsapp")
    assert _T.with_missing_notes(holder, "Total: ₹12.5 lakh.") == "Total: ₹12.5 lakh."
    holder.notes.append(NOTE)
    assert _T.with_missing_notes(holder, "Total: ₹12.5 lakh.") == f"Total: ₹12.5 lakh.\n\n{NOTE}"
    assert _T.with_missing_notes(holder, f"Total.\n{NOTE}") == f"Total.\n{NOTE}"


def test_the_executor_adds_the_note_before_each_run_finished() -> None:
    """A source fence: each RUN_FINISHED of ``run_agent_stream`` follows the
    note step, and ``_sse`` watches each event."""
    src = (REPO / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    body = src[src.index("async def run_agent_stream("):src.index("def _classify_error(")]
    finishes = [m.start() for m in re.finditer(r'"type": "RUN_FINISHED"', body)]
    assert len(finishes) == 3
    for at in finishes:
        assert "_crm_note_events()" in body[max(0, at - 700):at]
    sse = src[src.index("def _sse(payload"):src.index("# ── ONE event translator")]
    assert "_observe_crm_note(payload)" in sse


async def test_the_executor_note_step_reads_the_holder(run) -> None:
    from orchestrator import executor

    _T._register_note(NOTE)
    events = executor._crm_note_events()
    assert events and events[1]["delta"].strip() == NOTE


# ── P2: a parked act owns the next WhatsApp message ─────────────────────────


@pytest.mark.parametrize(("reply", "asks"), [
    ("Which one?\n1. Rent\n2. EB\nReply 1 or 2.", True),
    ("Pick one:\n1) Rent\n2) EB\nWhich do you mean?", True),
    ("Top deals:\n1. Acme\n2. Beta\nThat is all for now.", False),
    ("Total: ₹12.5 lakh.", False),
])
def test_a_reply_that_asks_its_own_numbered_choice(reply: str, asks: bool) -> None:
    from gateway.routes.crm import text_asks

    assert text_asks.reply_asks_a_choice(reply) is asks


def _req() -> Any:
    from gateway.routes.whatsapp_channel import bot_run

    return bot_run.RunRequest("m1", "org-a", "asha@x.test", "919800000000", "wamid-1", "s1")


@pytest.fixture
def offers(monkeypatch) -> dict[str, list]:
    from gateway.routes.crm import text_asks

    seen: dict[str, list] = {"offer": [], "drop": []}

    async def offer(org, member, channel, ask):
        seen["offer"].append(ask)
        return "One question, so the next answer is exact: ...\nReply 1, 2 or 3."

    async def drop(org, member, channel):
        seen["drop"].append(member)

    monkeypatch.setattr(text_asks, "offer", offer)
    monkeypatch.setattr(text_asks, "drop", drop)
    return seen


async def test_no_crm_question_goes_out_while_the_run_parked_an_act(offers) -> None:
    from gateway.routes.whatsapp_channel import bot_run

    holder = _T.TextAsks(channel="whatsapp", ask={"kind": "rule", "id": "0123456789abcdef"})
    assert await bot_run._crm_follow_up(_req(), holder, "Done.", parked=True) is None
    assert offers == {"offer": [], "drop": ["asha@x.test"]}


async def test_no_crm_question_goes_out_after_a_reply_with_its_own_choice(offers) -> None:
    from gateway.routes.whatsapp_channel import bot_run

    holder = _T.TextAsks(channel="whatsapp", ask={"kind": "question", "id": "revenue"})
    reply = "Which one?\n1. Rent\n2. EB\nReply 1 or 2."
    assert await bot_run._crm_follow_up(_req(), holder, reply) is None
    assert offers["offer"] == [] and offers["drop"] == ["asha@x.test"]
    assert (await bot_run._crm_follow_up(_req(), holder, "Total: ₹12.5 lakh.")).startswith(
        "One question")


async def test_with_an_act_and_a_crm_ask_both_pending_yes_never_saves_a_rule(
    monkeypatch,
) -> None:
    """The member's "yes": the act settles first. While an act is pending,
    the CRM's pending ask is never read, so "yes" cannot void the act AND
    save a rule. The message then runs as a normal message."""
    from gateway.routes.whatsapp_channel import acts, bot_run, flags

    crm: list[str] = []
    voided: list[str] = []

    async def has_pending(req):
        return True

    async def settle(req, message):
        voided.append(message)
        return None  # "yes" is no Confirm: the act closes, and the message runs

    async def crm_text_answer(req, message):
        crm.append(message)
        return "Saved for you."

    async def thread_turns(session_id, message_id):
        return "yes", []

    async def nothing(*args, **kwargs):
        return None

    async def answer(*args, **kwargs):
        return "A normal answer.", []

    async def not_shared(session_id):
        return False

    monkeypatch.setattr(acts, "has_pending", has_pending)
    monkeypatch.setattr(acts, "settle", settle)
    monkeypatch.setattr(bot_run, "_crm_text_answer", crm_text_answer)
    monkeypatch.setattr(bot_run, "_thread_turns", thread_turns)
    monkeypatch.setattr(bot_run, "_stored_reply", nothing)
    monkeypatch.setattr(bot_run, "_thread_shared", not_shared)
    monkeypatch.setattr(bot_run, "_quick_answer", nothing)
    monkeypatch.setattr(bot_run, "_model_answer", answer)
    monkeypatch.setattr(bot_run, "_write_reply", nothing)
    monkeypatch.setattr(bot_run, "_deliver", nothing)
    monkeypatch.setattr(flags, "native_ui_enabled", lambda: False)
    monkeypatch.setattr(flags, "writes_enabled", lambda org: True)
    await bot_run._answer(_req(), bot_run._Attempt())
    assert voided == ["yes"] and crm == []
    # With no act pending, the same "yes" reaches the CRM's ask.

    async def no_act(req):
        return False

    monkeypatch.setattr(acts, "has_pending", no_act)
    await bot_run._answer(_req(), bot_run._Attempt())
    assert crm == ["yes"]


async def test_a_failed_read_of_the_act_reads_as_pending(monkeypatch) -> None:
    from gateway.routes.whatsapp_channel import acts

    async def broken(req):
        raise RuntimeError("db down")

    monkeypatch.setattr(acts, "_live_act", broken)
    assert await acts.has_pending(_req()) is True


# ── P2: the purge reaches the CRM ───────────────────────────────────────────


def test_the_purge_deletes_the_member_s_own_rules_and_reaches_the_crm() -> None:
    from gateway.routes.admin import members

    rows = {r.table: r.where for r in members._PURGE_DELETES}
    assert rows["crm_business_rules"] == "scope = 'member' AND lower(member_email) = :email"
    src = Path(members.__file__).read_text(encoding="utf-8")
    body = src[src.index("async def purge_member_rows("):src.index("# ── Role assignment")]
    assert "await purge_crm_member(db, org_id, addr)" in body


async def test_the_purge_reaches_the_crm_in_the_admin_route(db) -> None:
    """The admin route's purge scrubs the org rules and rewrites
    ``zoho:questions`` in its own transaction (the fake records each CRM
    statement). The R8 suite proves what each does to real rows."""
    from gateway.routes.admin.members import purge_member

    from tests.unit.test_admin_member_purge import OWNER, PRIYA

    out = await purge_member(PRIYA, admin=OWNER)
    assert any(s.startswith("UPDATE crm_business_rules SET created_by") for s in db.crm_purge)
    assert any("FOR UPDATE" in s and "crm_sync_cursors" in s for s in db.crm_purge)
    # The member's own rules are a counted row of the delete list.
    assert out.deleted["crm_member_rules"] == 0


async def test_the_purge_forgets_each_crm_key_of_the_member_and_no_other() -> None:
    from gateway.routes.crm import questions

    from tests.unit._crm_redis_fake import RawRedis, tenant_client

    raw = RawRedis()
    me, other = "asha@x.test", "bala@x.test"
    names = [
        f"cc:org-a:crm_rule_pending:{me}:0123456789abcdef",
        f"cc:org-a:crm_text_ask:{me}:whatsapp",
        f"cc:org-a:crm_question_hold:{me}:key:Payment_Received:amount",
        f"cc:org-a:crm_question_answered:key:Payment_Received:amount:{me}",
        f"cc:org-a:crm_question_answered:revenue:{me}",
    ]
    kept = [
        f"cc:org-a:crm_rule_pending:{other}:0123456789abcdef",
        f"cc:org-a:crm_text_ask:{other}:whatsapp",
        f"cc:org-a:crm_question_answered:revenue:{other}",
        f"cc:org-b:crm_text_ask:{me}:whatsapp",
    ]
    for name in (*names, *kept):
        raw.store[name] = (60, "x")
    import gateway.routes.crm.questions as q

    original = q._redis
    q._redis = lambda: tenant_client(raw)
    try:
        removed = await questions.forget_member_keys("org-a", me)
    finally:
        q._redis = original
    assert removed == len(names)
    assert sorted(raw.store) == sorted(kept)


#: The tables with a `member_email` column that the member purge does not
#: delete yet. Each predates CRM-Q33 and is a finding for the board, not a
#: decision. A NEW table keyed by a member email must join the purge
#: (`routes/admin/members._PURGE_DELETES`), or this list, with a reason.
KNOWN_UNPURGED: dict[str, str] = {
    "agent_connection": "WS-55, before CRM-Q33",
    "artifact_member": "artifacts, before CRM-Q33",
    "integration_connections": "CRM-Z2, before CRM-Q33",
    "my_day_card_state": "WS-58, before CRM-Q33",
    "my_day_judgments": "WS-58, before CRM-Q33",
    "pm_agent_runs": "WS-55, before CRM-Q33",
    "pm_task_personal": "WS-39, before CRM-Q33",
    "reach_outbox": "reach, before CRM-Q33",
    "reach_requests": "reach, before CRM-Q33",
    "reach_signal_log": "reach, before CRM-Q33",
    "whatsapp_bot_messages": "WS-47, before CRM-Q33",
    "whatsapp_member_links": "WS-47, before CRM-Q33",
    "whatsapp_pending_acts": "WS-47, before CRM-Q33",
}


def _member_email_tables() -> set[str]:
    found: set[str] = set()
    for path in sorted((REPO / "infra/postgres").glob("[0-9]*.sql")):
        sql = "\n".join(re.sub(r"--.*$", "", line)
                        for line in path.read_text(encoding="utf-8").splitlines())
        for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", sql, re.S):
            if re.search(r"^\s*member_email\b", m.group(2), re.M):
                found.add(m.group(1))
        for m in re.finditer(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN IF NOT EXISTS\s+member_email\b",
                             sql):
            found.add(m.group(1))
    return found


def test_every_table_keyed_by_a_member_email_is_in_the_purge() -> None:
    from gateway.routes.admin import members

    tables = _member_email_tables()
    assert "crm_business_rules" in tables and len(tables) >= 10, "the scan found too little"
    purged = {r.table for r in (*members._PURGE_DELETES, *members._PURGE_KEEPS)}
    missing = sorted(tables - purged - set(KNOWN_UNPURGED))
    assert missing == [], (
        f"{missing} hold a member email and are not in the member purge "
        "(routes/admin/members._PURGE_DELETES). Add them, or name them in "
        "KNOWN_UNPURGED with a reason.")
    assert not (purged & set(KNOWN_UNPURGED)), "a purged table is still named as unpurged"


def test_the_fence_finds_a_new_table_with_a_member_email(tmp_path, monkeypatch) -> None:
    """The fence fails on a new table, so it is no list that agrees with
    itself."""
    fake = tmp_path / "infra/postgres"
    fake.mkdir(parents=True)
    for path in (Path(__file__).resolve().parents[2] / "infra/postgres").glob("[0-9]*.sql"):
        (fake / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (fake / "999_new.sql").write_text(
        "CREATE TABLE IF NOT EXISTS crm_new_member_thing (\n"
        "    organization_id UUID NOT NULL,\n    member_email TEXT NOT NULL\n);\n",
        encoding="utf-8")
    monkeypatch.setattr(__import__(__name__, fromlist=["REPO"]), "REPO", tmp_path)
    with pytest.raises(AssertionError, match="crm_new_member_thing"):
        test_every_table_keyed_by_a_member_email_is_in_the_purge()


def test_the_card_of_a_rule_shows_the_server_s_own_words() -> None:
    """The tool builds the rule card from the server's held row, and the
    late pick checks the same words (``questions._rule_label``)."""
    src = (REPO / "apps/services/gateway/gateway/routes/crm/questions.py").read_text(
        encoding="utf-8")
    body = src[src.index("async def _rule_label("):src.index("def _shown_question(")]
    assert "confirm_question(held[\"body\"], held[\"scope\"])" in body
    assert "!= shown" in body
