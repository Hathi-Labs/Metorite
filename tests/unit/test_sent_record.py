"""The sent-mail record of a chat (task 3594), the hermetic fences.

Spec: ``project-docs/specs/chat_organization.md`` §16.3. Migration
``infra/postgres/<n>_chat_card_record_recipients.sql``, found by its content.
The SQL and RLS are proved on a real database in ``test_sent_record_r8.py``
(R8). Here: the line of the run context, the turn-2 case of run a8ad07f3, the
explicit bind of a send to its card, the fail-open read, and the dark switch.

Mutations, each red here:

* take ``_sent_acts_block`` out of ``executor._context_preamble_parts``: the
  turn-2 duplicate case finds no line;
* drop the frame check of ``ask_tools.note_sent_message``: the case of a card
  of another tool call writes a send;
* drop the ``_LAST_APPROVED.set(None)`` of ``card_call``: the same case;
* call ``_note_sent`` before ``_post("/email/send")``: the failed-send case
  writes a send.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import ask_tools
from acb_skills.write_artifact import bind_artifact_context
from orchestrator import card_record as cr
from orchestrator import executor as ex

_ROOT = Path(__file__).resolve().parents[2]
_SQL_MARK = "ADD COLUMN IF NOT EXISTS recipients TEXT[]"

ORG = "33333333-3333-4333-8333-333333333333"
OTHER_ORG = "44444444-4444-4444-8444-444444444444"
MEMBER = "mahesh@alpha.example"
SESSION = "chat-3594"
RUN = "run-3594-2"
T0 = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
RULE = ("Do not send the same act again unless the member asks for it. "
        "Before you say that something was not sent, read the lines above.")


def _act(i: int, people: list[str] | None = None, mid: str | None = None) -> dict[str, Any]:
    return {"request_id": f"{i:032x}", "act_kind": "agents.send_email",
            "message_id": mid or f"<m{i}@mail.test>",
            "recipients": people if people is not None else [f"p{i}@x.test"],
            "sent_at": T0 + timedelta(minutes=i)}


# ── The line text ──────────────────────────────────────────────────────────


def test_the_line_states_one_fact_and_then_the_rule_once() -> None:
    text = cr.sent_acts_text([_act(1, ["veena@x.test", "raj@y.test"])])
    assert text == (
        "In this chat the member approved and the system sent: an email to "
        "veena@x.test, raj@y.test, at 2026-11-16 09:31 UTC, message <m1@mail.test>.\n"
        + RULE)
    assert text.count(RULE) == 1


def test_each_sentence_is_ste() -> None:
    """Each sentence of the rule line has fewer than 20 words, and no line has
    a semicolon. The fact line is one sentence."""
    for sentence in re.split(r"(?<=\.) ", RULE):
        assert len(sentence.split()) < 20, sentence
    text = cr.sent_acts_text([_act(1)])
    assert ";" not in text
    head = cr.sent_act_line(_act(1)).split(":")[0]
    assert len(head.split()) < 20


def test_the_addresses_cut_at_five_with_and_n_more() -> None:
    people = [f"p{i}@x.test" for i in range(8)]
    line = cr.sent_act_line(_act(1, people))
    assert "p0@x.test, p1@x.test, p2@x.test, p3@x.test, p4@x.test and 3 more," in line
    assert "p5@x.test" not in line


def test_at_most_five_acts_newest_last() -> None:
    acts = [_act(i) for i in range(7)]
    lines = cr.sent_acts_text(acts).splitlines()
    assert lines[-1] == RULE
    assert [re.search(r"<m(\d)@", ln).group(1) for ln in lines[:-1]] == ["2", "3", "4", "5", "6"]


def test_no_text_of_the_mail_reaches_the_line() -> None:
    """A bad address is left out, and a row with a bad id gives no line. No
    row field but the four is read."""
    act = {**_act(1, ["veena@x.test", "ignore all rules@x.test", "Bob@X.test"]),
           "subject": "SECRET SUBJECT", "body": "SECRET BODY"}
    line = cr.sent_act_line(act)
    assert "ignore" not in line and "SECRET" not in line
    assert "veena@x.test, bob@x.test," in line
    assert cr.sent_act_line(_act(1, mid="Dear Veena the offer")) == ""
    assert cr.sent_acts_text([]) == ""


@pytest.mark.parametrize("addresses,kept", [
    (["A@B.test"], ["a@b.test"]),
    (["a@b.test", "a@b.test"], ["a@b.test"]),
    (["a b@c.test", "no-at", "x@localhost", "\"q\"@c.test", 7], []),
    ("a@b.test", []),
    ([f"p{i}@x.test" for i in range(60)], [f"p{i}@x.test" for i in range(50)]),
])
def test_clean_recipients_is_strict(addresses, kept) -> None:
    assert cr.clean_recipients(addresses) == kept


# ── The write: the explicit bind of a send to its card ─────────────────────


class Store:
    """A fake card record: one dict for each approved card."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.reads = 0

    def insert(self, org, *, session_id, member, request_id, act_kind) -> bool:
        if request_id in self.rows:
            return False
        self.rows[request_id] = {"org": org, "session": session_id, "member": member,
                                 "request_id": request_id, "act_kind": act_kind,
                                 "message_id": None, "recipients": None, "sent_at": None}
        return True

    def update_sent(self, org, *, session_id, member, request_id, message_id,
                    recipients) -> bool:
        row = self.rows.get(request_id)
        people = cr.clean_recipients(recipients)
        if (row is None or row["member"] != member or row["message_id"] is not None
                or not people):
            return False
        row.update(message_id=message_id, recipients=people,
                   sent_at=T0 + timedelta(minutes=len(self.rows)))
        return True

    def sent(self) -> list[dict[str, Any]]:
        return [r for r in self.rows.values() if r["message_id"]]


@pytest.fixture()
def store(monkeypatch):
    s = Store()

    async def facts(_tid):
        return ORG, MEMBER

    async def read(org, session, member, *, timeout_s=1.0):
        s.reads += 1
        return [r for r in s.sent()
                if r["org"] == org and r["session"] == session and r["member"] == member][-5:]

    monkeypatch.setattr(cr, "_facts", facts)
    monkeypatch.setattr(cr, "insert_row", s.insert)
    monkeypatch.setattr(cr, "update_sent", s.update_sent)
    monkeypatch.setattr(cr, "sent_acts", read)
    import orchestrator.executor as executor

    monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: SESSION)
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "*", raising=False)
    monkeypatch.setattr(ex, "_current_run_org", lambda: ORG)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    return s


async def send_email() -> None:
    """A stand-in send tool: its name is the act kind."""


async def _approve_and_send(rid: str, *, sent: bool = True, mid: str = "<m1@mail.test>",
                            people: tuple[str, ...] = ("veena@x.test",)) -> bool:
    """One tool call: a card that the member approved, then the send."""
    with ask_tools.card_call(send_email, (), {}):
        await ask_tools._record_approved_card(rid)
        if not sent:
            return False  # the send failed: the tool never notes it
        return await ask_tools.note_sent_message(mid, list(people))


def test_a_send_after_its_approved_card_writes_once(store) -> None:
    rid = "a" * 32

    async def go() -> tuple[bool, bool]:
        with ask_tools.card_call(send_email, (), {}):
            await ask_tools._record_approved_card(rid)
            first = await ask_tools.note_sent_message("<m1@mail.test>", ["Veena@X.test"])
            second = await ask_tools.note_sent_message("<m2@mail.test>", ["raj@x.test"])
        return first, second

    assert asyncio.run(go()) == (True, False)
    [row] = store.sent()
    assert (row["message_id"], row["recipients"]) == ("<m1@mail.test>", ["veena@x.test"])


def test_an_approved_card_whose_send_failed_writes_nothing(store) -> None:
    assert asyncio.run(_approve_and_send("b" * 32, sent=False)) is False
    assert store.sent() == []
    assert store.rows["b" * 32]["message_id"] is None


def test_a_declined_card_writes_nothing(store) -> None:
    """No approved card in the frame: a send note counts for nothing."""
    async def go() -> bool:
        with ask_tools.card_call(send_email, (), {}):
            return await ask_tools.note_sent_message("<m1@mail.test>", ["a@x.test"])

    assert asyncio.run(go()) is False
    assert store.rows == {}


def test_a_card_of_another_tool_call_never_reaches_a_later_send(store) -> None:
    """The review note: on the Copilot path one context is copied to each
    tool of a run. A card that tool A approved never binds a send of tool B."""
    async def go() -> tuple[bool, bool]:
        with ask_tools.card_call(send_email, (), {}):
            await ask_tools._record_approved_card("c" * 32)
        # A later tool in the SAME context, with no card of its own.
        with ask_tools.card_call(send_email, (), {}):
            in_b = await ask_tools.note_sent_message("<m9@mail.test>", ["a@x.test"])
        # The same context, outside any tool call.
        outside = await ask_tools.note_sent_message("<m9@mail.test>", ["a@x.test"])
        return in_b, outside

    assert asyncio.run(go()) == (False, False)
    assert store.sent() == []


def test_a_copied_context_keeps_no_card_of_an_earlier_tool(store) -> None:
    import contextvars

    async def go() -> bool:
        with ask_tools.card_call(send_email, (), {}):
            await ask_tools._record_approved_card("d" * 32)
            snapshot = contextvars.copy_context()

        async def tool_b() -> bool:
            with ask_tools.card_call(send_email, (), {}):
                return await ask_tools.note_sent_message("<m9@mail.test>", ["a@x.test"])

        return await asyncio.get_running_loop().create_task(tool_b(), context=snapshot)

    assert asyncio.run(go()) is False
    assert store.sent() == []


def test_with_no_frame_the_card_id_must_match(store) -> None:
    """A run with no tool call frame (``approve_all``): only the card id that
    the tool awaited binds the send."""
    async def go(rid_given: str | None) -> bool:
        await ask_tools._record_approved_card("e" * 32)
        return await ask_tools.note_sent_message("<m1@mail.test>", ["a@x.test"],
                                                 request_id=rid_given)

    assert asyncio.run(go(None)) is False
    assert asyncio.run(go("f" * 32)) is False
    assert asyncio.run(go("e" * 32)) is True


def test_flag_off_writes_nothing(store, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "", raising=False)
    assert asyncio.run(_approve_and_send("1" * 32)) is False
    assert store.sent() == []


# ── The read: the run context of turn 2 ─────────────────────────────────────


def _turn2(**kw: Any) -> list[str]:
    async def go() -> list[str]:
        bind_artifact_context(session_id=SESSION, run_id=RUN, member=MEMBER,
                              batch_thread=kw.get("batch_thread", False))
        await ex._prime_sent_acts(
            kw.get("agent", "orchestrator"), organization_id=kw.get("org", ORG),
            thread_id=SESSION, delegated=kw.get("delegated", False),
            batch_thread=kw.get("batch_thread", False))
        return ex._context_preamble_parts({}, {}, "orchestrator")

    return asyncio.run(go())


def _sent_lines(parts: list[str]) -> list[str]:
    return [ln for p in parts for ln in p.splitlines()
            if ln.startswith("In this chat the member approved and the system sent")]


def test_turn_2_reads_the_send_of_turn_1_and_a_declined_duplicate_adds_no_line(store) -> None:
    """Run a8ad07f3: turn 1 sent the mail. Turn 2 raises a duplicate, and the
    member declines it. The context of turn 2 and of turn 3 holds ONE line."""
    assert asyncio.run(_approve_and_send("2" * 32)) is True
    parts = _turn2()
    assert _sent_lines(parts) == [
        "In this chat the member approved and the system sent: an email to "
        "veena@x.test, at 2026-11-16 09:31 UTC, message <m1@mail.test>."]
    assert any(RULE in p for p in parts)

    async def duplicate_declined() -> bool:
        with ask_tools.card_call(send_email, (), {}):
            # The card closed as declined: no approved card, no send.
            return await ask_tools.note_sent_message("<m2@mail.test>", ["veena@x.test"])

    assert asyncio.run(duplicate_declined()) is False
    assert len(_sent_lines(_turn2())) == 1


def test_a_read_fault_gives_no_line_and_never_raises(store, monkeypatch) -> None:
    asyncio.run(_approve_and_send("3" * 32))

    async def broken(*_a, **_k):
        raise TimeoutError

    monkeypatch.setattr(cr, "sent_acts", broken)
    assert _sent_lines(_turn2()) == []


def test_flag_off_gives_no_line_and_no_read(store, monkeypatch) -> None:
    asyncio.run(_approve_and_send("4" * 32))
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "", raising=False)
    store.reads = 0
    assert _sent_lines(_turn2()) == []
    assert store.reads == 0


def test_the_flag_turned_off_after_the_prime_drops_the_line(store, monkeypatch) -> None:
    asyncio.run(_approve_and_send("5" * 32))

    async def go() -> tuple[list[str], list[str]]:
        bind_artifact_context(session_id=SESSION, run_id=RUN, member=MEMBER)
        await ex._prime_sent_acts("orchestrator", organization_id=ORG, thread_id=SESSION,
                                  delegated=False, batch_thread=False)
        before = ex._context_preamble_parts({}, {}, "orchestrator")
        monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "", raising=False)
        return before, ex._context_preamble_parts({}, {}, "orchestrator")

    before, after = asyncio.run(go())
    assert len(_sent_lines(before)) == 1 and _sent_lines(after) == []


@pytest.mark.parametrize("kw", [
    {"delegated": True}, {"batch_thread": True}, {"org": OTHER_ORG},
])
def test_a_run_that_fails_a_gate_gets_no_line_and_no_read(store, kw) -> None:
    asyncio.run(_approve_and_send("6" * 32))
    store.reads = 0
    assert _sent_lines(_turn2(**kw)) == []
    assert store.reads == 0


def test_a_chat_with_untrusted_input_still_gets_the_line(store) -> None:
    """Rule 7: no gate of the prime reads the untrusted mark."""
    asyncio.run(_approve_and_send("7" * 32))
    src = Path(ex.__file__).read_text(encoding="utf-8")
    body = src[src.index("async def _prime_sent_acts"):src.index("def _sent_acts_block")]
    assert "untrusted" not in re.sub(r'""".*?"""', "", body, flags=re.S)
    assert len(_sent_lines(_turn2())) == 1


def test_the_child_of_a_run_gets_none(store) -> None:
    from acb_skills.write_artifact import derive_artifact_context

    asyncio.run(_approve_and_send("8" * 32))

    async def go() -> tuple[str, str]:
        bind_artifact_context(session_id=SESSION, run_id=RUN, member=MEMBER)
        await ex._prime_sent_acts("orchestrator", organization_id=ORG, thread_id=SESSION,
                                  delegated=False, batch_thread=False)
        parent = ex._sent_acts_block("orchestrator")
        derive_artifact_context(run_id="child-run")
        return parent, ex._sent_acts_block("orchestrator")

    parent, child = asyncio.run(go())
    assert parent.startswith("In this chat") and child == ""


def test_the_stream_run_awaits_the_prime_after_the_celebrations() -> None:
    src = Path(ex.__file__).read_text(encoding="utf-8")
    start = src.index("await _prime_celebrations(")
    assert src.index("await _prime_sent_acts(", start) > start
    assert src.count("await _prime_sent_acts(") == 1


# ── The email tools note the send after it went, never before ──────────────


def _email_agent() -> Any:
    path = _ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"
    name = "email_agents_3594"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _source() -> str:
    return (_ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py").read_text(
        encoding="utf-8")


@pytest.mark.parametrize("act", [
    'res = await _post("/email/send", payload)',
    'res = await _post("/email/forward", payload)',
])
def test_a_plain_send_notes_after_the_post(act) -> None:
    src = _source()
    at = src.index(act)
    nxt = src.index("await _note_sent(", at)
    # The note is the next await after the post: nothing between can fail
    # the send and leave a written record.
    assert src.index("await ", at + len(act)) == nxt
    assert src.rfind("await _note_sent(", 0, at) < src.rfind("async def ", 0, at)


def test_every_act_of_a_card_notes_only_a_send() -> None:
    src = _source()
    reply = src[src.index("async def compose_reply("):src.index("# ── Sender categorization")]
    assert re.search(r'if res\.get\("action"\) == "send":\n(?:\s+#[^\n]*\n)*\s+await _note_sent\(',
                     reply)
    new = src[src.index("async def _new_mail_answered("):src.index("async def _always_copy_cc(")]
    assert re.search(r'if res\.get\("action"\) == "send":\n(?:\s+#[^\n]*\n)*\s+await _note_sent\(',
                     new)
    held = src[src.index("async def _note_held_sent("):src.index("_SUBJECT_MAX = 200")]
    assert 'res.get("action") not in ("send", "forward")' in held
    assert src.count("await _note_held_sent(") == 2
    draft = src[src.index("async def send_draft("):src.index("# ── The chat as the member")]
    assert draft.index("await _note_sent(") > draft.index('"/email/drafts/send"')


def test_the_send_email_tool_writes_after_the_send_and_not_on_a_failure(
        store, monkeypatch) -> None:
    """The real ``send_email`` of the email agent, its old card path, with a
    fake gateway. A send that the gateway refuses writes nothing."""
    mod = _email_agent()
    posted: list[str] = []
    fail = {"on": False}

    async def post(path, body=None, *a, **k):
        posted.append(path)
        if path == "/email/send":
            if fail["on"]:
                raise mod.GatewayError("422", 422, detail="The file is not in your workspace.")
            return {"id": "18c0ffee", "ok": True}
        return {}

    async def approve(**_kw):
        await ask_tools._record_approved_card("9" * 32)
        return True

    async def name(_aid):
        return "Mahesh · mahesh@alpha.example"

    async def same_cc(account_id, to, cc, bcc):
        return cc, []

    monkeypatch.setattr(mod, "_post", post)
    monkeypatch.setattr(mod, "_mailbox_name", name)
    monkeypatch.setattr(mod, "_held_card_mode", lambda: False)
    monkeypatch.setattr(mod, "_edit_card_open", lambda: False)
    monkeypatch.setattr(mod, "_always_copy_cc", same_cc)
    monkeypatch.setattr(mod, "_usual_mailbox_note", lambda *a, **k: _empty())
    monkeypatch.setattr(ask_tools, "request_confirmation", approve)

    async def call() -> str:
        with ask_tools.card_call(mod.send_email, (), {}):
            return await mod.send_email("acc-1", body="Hi", to=["Veena@X.test"],
                                        subject="Offer")

    fail["on"] = True
    with pytest.raises(mod.GatewayError):
        asyncio.run(call())
    assert store.sent() == []
    store.rows.clear()
    fail["on"] = False
    assert "18c0ffee" in asyncio.run(call())
    [row] = store.sent()
    assert (row["message_id"], row["recipients"]) == ("18c0ffee", ["veena@x.test"])


async def _empty() -> str:
    return ""


def test_a_refused_address_is_not_recorded_as_sent() -> None:
    """The mail server refused x@y.test, so the line must not say that the
    mail went to it (Opus review of 8d35f393a, P2)."""
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    path = root / "apps" / "agents" / "agent-email-assistant" / "agents.py"
    src = path.read_text(encoding="utf-8")
    assert "def _not_refused(" in src
    ns: dict = {}
    start = src.index("def _not_refused(")
    end = src.index("\n\n\nasync def _note_sent(")
    exec("from typing import Any\n" + src[start:end], ns)  # noqa: S102
    f = ns["_not_refused"]
    assert f(["a@x.test", "x@y.test"], {"refused": ["X@Y.test"]}) == ["a@x.test"]
    assert f(["a@x.test"], {}) == ["a@x.test"]
    assert f(["a@x.test"], None) == ["a@x.test"]
    assert src.count("_not_refused(") >= 4
    assert importlib.util is not None and sys is not None


def test_set_sent_off_reads_no_redis(monkeypatch) -> None:
    """Flag off for the bound tenant: no run fact is read after the mail went
    (Opus review of 8d35f393a, P2)."""
    import asyncio

    from acb_common import db as acb_db
    from acb_common import get_settings
    from orchestrator import card_record as cr

    looked: list[str] = []

    async def facts(_tid):
        looked.append("facts")
        return "org-1", "alice@x.test"

    monkeypatch.setattr(cr, "_facts", facts)
    monkeypatch.setattr(acb_db, "current_tenant", lambda: "org-1")
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "", raising=False)
    assert asyncio.run(cr.set_sent("t", "a" * 32, "<m@x.test>", ["a@x.test"])) is False
    assert looked == []

    async def slow(_tid):
        await asyncio.sleep(10)
        return "org-1", "alice@x.test"

    monkeypatch.setattr(cr, "_facts", slow)
    monkeypatch.setattr(cr, "_TIMEOUT_S", 0.05)
    monkeypatch.setattr(get_settings(), "chat_card_record_orgs", "*", raising=False)
    assert asyncio.run(cr.set_sent("t", "a" * 32, "<m@x.test>", ["a@x.test"])) is False
