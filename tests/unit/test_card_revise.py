"""Task 3814: a chat message closes a mailbox card, and one email keeps one draft.

Production, 2026-11-18. The owner saw "Save this new email in Drafts?" and
wrote "I think you can draft it better". The chat said "Added to the reply",
and the card did not change. The cards of ``_card_refusal`` never passed
``close_on_steer``, so the run waited in the card and the steer waited for a
tool result. Each redraft then saved a second draft beside the first.

Both fixes are dark behind ``CHAT_CARD_REVISE_ORGS``
(``acb_skills.card_revise.card_revise_on``). Off, each card and each save is
the call of before, byte for byte.

The cards of the email agent, and what a chat message does to each
------------------------------------------------------------------

=============================================  ==========================  =====================
Card kind (where)                              Path                        A chat message
=============================================  ==========================  =====================
Each ``_card_refusal`` card: save a new draft   ``_confirm_destructive``    closes it (flag on),
(``_new_draft``), change a draft                                            text of its act
(``edit_draft``), snooze, labels, rules,
settings, knowledge, Reply Zero, the digest,
a purge, a mailbox change, and the rest
The outward rule card (``_outward_rule_refusal``) ``_confirm_destructive``  closes it (flag on),
                                                                            the ``card`` text
Unsubscribe, one or many senders                ``_confirm_destructive``    closes it (flag on),
                                                                            the ``unsubscribe`` text
Send a draft now (``send_draft``)               ``request_confirmation``    closes it (flag on),
                                                                            the ``send_draft`` text
A reply (``compose_reply``)                     reply form                  closes it (always on,
                                                                            the reply form default)
A new mail (``send_email`` edit card)           reply form ``kind: new``    closes it (always on)
A send now, a forward, a send later             ``close_on_steer=True``     closes it (always on)
Each held card (``_held_card``)                 ``close_on_steer=True``     closes it (always on)
A question card (``ask_tools.ask_questions``)   AG-UI ``elicitation``       nothing: see below
=============================================  ==========================  =====================

``ask_questions`` (``ask_tools.py``, path A) parks the run on its own future
with ``wait_user_future`` and watches no steer. A chat message while a
question card waits does not close it. The steer waits until the run reaches
a tool result, after the member answers the card or the card parks. It stays
so here: a question card asks for words, and the answer of the card is the
tool result. To close it on a steer changes that contract, so it needs its
own slice. The WhatsApp paths (a parked card, a run with no card channel)
never reach the steer watch, so their answers are as before.

Each rule names the mutation that turns it red (R7):

1. **A ``_card_refusal`` card closes on a steer with the flag on.** Mutation:
   drop the ``close_on_steer`` of ``_confirm_destructive`` ->
   ``test_flag_on_the_gate_asks_close_on_steer``. Mutation: answer
   ``_not_done`` for REVISED -> ``test_a_closed_card_answers_the_text_of_its_act``.
2. **Off is the call of before.** Mutation: pass ``close_on_steer=False``
   with the flag off -> ``test_flag_off_the_gate_is_the_call_of_before``.
3. **Two redrafts leave one draft.** Mutation: drop the lookup in
   ``save_one_draft`` -> ``test_two_redrafts_leave_one_draft``.
4. **Other people never lose their draft.** Mutation: match on the subject
   alone -> ``test_a_draft_to_other_people_is_never_written_over``.
5. **A draft that is gone gives a new draft.** Mutation: raise on the 404 ->
   ``test_a_gone_draft_gives_a_new_draft_and_the_record_takes_it``.

R8: this slice adds no SQL. The record lives in tenant Redis, through the
prefix wrapper, so no R8 test applies. What the fence does not prove: that
the model follows the text. That needs a live eval, which costs money.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from acb_skills import ask_tools as at
from acb_skills import card_revise as cr

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"

ORG = "org-card-revise"
BOX = "a0000000-0000-4000-8000-000000000001"
MEMBER = "dana@fracktal.test"
THREAD = "thread-3814"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("ea_card_revise", AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()


# ── Fakes ────────────────────────────────────────────────────────────────────


class _Redis:
    """Tenant Redis, faked at the seam. It keeps the wire key, so a test can
    see the tenant prefix."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.fail = False

    async def get(self, k: Any) -> Any:
        if self.fail:
            raise ConnectionError("down")
        return self.data.get(k.value)

    async def set(self, k: Any, value: Any, **_kw: Any) -> Any:
        if self.fail:
            raise ConnectionError("down")
        self.data[k.value] = value
        return True


class _Mailbox:
    """``PUT /email/drafts``, faked: a create makes a new id, an update by
    ``draft_id`` changes that draft, and a draft that is gone answers 404."""

    def __init__(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    async def put(self, path: str, body: dict[str, Any]) -> Any:
        assert path == "/email/drafts"
        self.calls.append(dict(body))
        did = body.get("draft_id")
        if did:
            if did not in self.drafts:
                raise agents.GatewayError("Email PUT failed (404)", 404,
                                          detail="Draft not found")
            self.drafts[did] = {k: v for k, v in body.items() if k != "draft_id"}
            return {"id": did}
        self._n += 1
        new = f"d{self._n:04d}"
        self.drafts[new] = dict(body)
        return {"id": new}

    def creates(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if not c.get("draft_id")]


@pytest.fixture()
def redis(monkeypatch: pytest.MonkeyPatch) -> _Redis:
    from acb_common.db import bind_tenant, release_tenant

    fake = _Redis()
    monkeypatch.setattr(cr, "get_tenant_redis", lambda: fake)
    token = bind_tenant(ORG)
    yield fake
    release_tenant(token)


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_card_revise_orgs", value)


class _Cards(list):
    """The keyword arguments of each card, and the answers still to give."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: list[Any] = []


@pytest.fixture()
def card(monkeypatch: pytest.MonkeyPatch) -> _Cards:
    """The card gate, faked. Each call keeps its keyword arguments, and the
    answer is the next of ``answers`` (default: closed by a chat message,
    when the card asked for it, else a no)."""
    seen = _Cards()

    async def fake(*_a: Any, **kw: Any) -> Any:
        seen.append(kw)
        if seen.answers:
            return seen.answers.pop(0)
        if kw.get("close_on_steer"):
            return at._noted(at.CLOSED_BY_YOUR_MESSAGE, at.REVISED)
        return at._noted(at.CARD_DECLINED, False)

    monkeypatch.setattr(at, "request_confirmation", fake)
    monkeypatch.setattr(agents, "_card_channel_open", lambda: True)
    monkeypatch.setattr(agents, "_wa_parked", lambda: None)
    return seen


@pytest.fixture()
def tool(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> _Mailbox:
    box = _Mailbox()

    async def name(_aid: str) -> str:
        return f"Fracktal · {MEMBER}"

    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_put", box.put)
    monkeypatch.setattr(agents, "_run_thread", lambda: THREAD)
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    return box


# ── 1. The card closes on a steer, with the flag on ─────────────────────────


async def test_flag_on_the_gate_asks_close_on_steer(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    out = await agents._card_refusal("Snooze?", "d", "Nothing changed.")
    assert card[-1]["close_on_steer"] is True
    assert out == agents._closed_by_message("card")


async def test_flag_off_the_gate_is_the_call_of_before(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, "")
    out = await agents._card_refusal("Snooze?", "d", "Nothing changed.", "ctx")
    assert card[-1] == {"title": "Snooze?", "detail": "d", "context": "ctx"}
    assert out == agents._not_done("Nothing changed.")


async def test_flag_for_another_org_is_off(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, "some-other-org")
    await agents._card_refusal("Snooze?", "d", "Nothing changed.")
    assert "close_on_steer" not in card[-1]


@pytest.mark.parametrize(("closed", "note"), [
    ("new_draft", ""), ("edit_draft", "The draft_id of this draft is X."), ("card", ""),
])
async def test_a_closed_card_answers_the_text_of_its_act(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
    closed: str, note: str,
) -> None:
    _flag(monkeypatch, "*")
    out = await agents._card_refusal("t", "d", "act", closed=closed, closed_note=note)
    assert out == agents._closed_by_message(closed) + (f" {note}" if note else "")
    assert out.startswith(at.card_outcome_head(at.CLOSED_BY_YOUR_MESSAGE, "Nothing was sent."))


async def test_flag_on_an_approve_and_a_no_are_as_before(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([at._noted(at.CARD_APPROVED, True),
                         at._noted(at.CARD_DECLINED, False)])
    assert await agents._card_refusal("t", "d", "The rule was not deleted.") is None
    out = await agents._card_refusal("t", "d", "The rule was not deleted.")
    assert out == agents._not_done("The rule was not deleted.")
    assert out.startswith("Declined")


async def test_flag_on_whatsapp_and_no_channel_keep_their_answers(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    """A WhatsApp park and a run with no card channel never reach the steer
    watch: the card answers False, and the tool says what it said before."""
    _flag(monkeypatch, ORG)
    card.answers.extend([False, False])
    monkeypatch.setattr(agents, "_wa_parked", lambda: "Parked: reply 1 to confirm.")
    assert await agents._card_refusal("t", "d", "act") == "Parked: reply 1 to confirm."
    monkeypatch.setattr(agents, "_wa_parked", lambda: None)
    monkeypatch.setattr(agents, "_card_channel_open", lambda: False)
    assert await agents._card_refusal("t", "d", "act") == agents._NO_CARD_CHANNEL


async def test_the_save_new_draft_card_of_the_owner_closes_on_the_steer(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """The card of the bug: "Save this new email in Drafts?"."""
    _flag(monkeypatch, ORG)
    out = await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates",
                                  body="Hi Ravi.")
    assert card[-1]["title"] == "Save this new email in Drafts?"
    assert card[-1]["close_on_steer"] is True
    assert out == agents._closed_by_message("new_draft")
    assert tool.calls == []  # nothing was saved


async def test_the_same_card_with_the_flag_off_is_as_before(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, "")
    out = await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates",
                                  body="Hi Ravi.")
    assert "close_on_steer" not in card[-1]
    assert out == agents._not_done("No draft was saved.")
    assert tool.calls == []


async def test_a_closed_send_draft_card_answers_its_act(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    draft = "00000000-0000-4000-8000-0000000000d1"

    async def get(_path: str, *_a: Any, **_kw: Any) -> Any:
        return {"id": draft, "account_id": BOX, "folder": "drafts", "subject": "Hi",
                "to_addresses": [{"email": "ravi@contoso.test"}]}

    async def sealed(*_a: Any, **_kw: Any) -> Any:
        return "detail", "context", {"text_digest": "a" * 64}

    async def recent(_did: str, detail: str) -> str:
        return detail

    async def no_post(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a closed card sends nothing")

    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_sealed_card", sealed)
    monkeypatch.setattr(agents, "_with_recent_reply", recent)
    monkeypatch.setattr(agents, "_post", no_post)
    out = await agents.send_draft(BOX, draft)
    assert card[-1]["close_on_steer"] is True
    assert out == agents._closed_by_message("send_draft")


# ── 2. The closed text of each act ──────────────────────────────────────────


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


@pytest.mark.parametrize("act", ["new_draft", "edit_draft", "card"])
def test_the_closed_text_names_the_three_ways(act: str) -> None:
    text = agents._closed_by_message(act)
    assert "Read them in one of three ways." in text
    assert "If they change" in text  # 1. a change: a new card of the same act
    assert "do nothing more" in text  # 2. the act ends
    assert "If they ask about something else, answer that." in text  # 3.
    assert "Do not show the card again until they ask for it." in text
    assert ";" not in text
    for sentence in _sentences(text):
        assert len(sentence.split()) <= 20, sentence


def test_the_new_draft_text_redrafts_in_place() -> None:
    text = agents._closed_by_message("new_draft")
    assert "Its tool is edit_draft." in text
    assert "call edit_draft again with no draft_id" in text
    assert "Drafts keeps one copy" in text
    assert "tell them that no draft was saved" in text


def test_the_edit_draft_text_keeps_the_same_draft() -> None:
    text = agents._closed_by_message("edit_draft")
    assert "call edit_draft again with the same draft_id" in text
    assert "Do not save a new draft." in text
    assert "tell them that the draft did not change" in text


def test_the_generic_text_names_no_tool() -> None:
    text = agents._closed_by_message("card")
    assert "Its tool is" not in text
    assert "call the same tool again" in text


# ── 3. Two redrafts leave one draft ─────────────────────────────────────────


async def test_two_redrafts_leave_one_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    first = await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates",
                                    body="Hi Ravi.")
    assert "The new draft is saved" in first
    second = await agents.edit_draft(BOX, to=["Ravi@Contoso.test"], subject="Rates",
                                     body="Hello Ravi, the rates.")
    third = await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="New rates",
                                    body="Hello Ravi, the new rates.")
    assert len(tool.creates()) == 1
    assert len(tool.drafts) == 1
    [only] = tool.drafts.values()
    assert only["body"] == "Hello Ravi, the new rates."
    assert "The draft is updated" in second and "no second copy" in second
    assert "d0001" in third


async def test_a_draft_to_other_people_is_never_written_over(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi Ravi.")
    # The same subject to other people is another email.
    await agents.edit_draft(BOX, to=["lee@contoso.test"], subject="Rates", body="Hi Lee.")
    assert len(tool.creates()) == 2
    assert sorted(d["to"][0] for d in tool.drafts.values()) == [
        "lee@contoso.test", "ravi@contoso.test"]
    # One shared To person and the same subject is the same email.
    await agents.edit_draft(BOX, to=["ravi@contoso.test", "kiran@contoso.test"],
                            subject="Rates", body="Hi both.")
    assert len(tool.creates()) == 2


async def test_a_gone_draft_gives_a_new_draft_and_the_record_takes_it(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
    redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi Ravi.")
    tool.drafts.clear()  # sent or deleted outside the chat
    out = await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates",
                                  body="Hi again.")
    assert "The new draft is saved" in out and "d0002" in out
    assert [c.get("draft_id") for c in tool.calls] == [None, "d0001", None]
    [raw] = redis.data.values()
    assert [r["id"] for r in json.loads(raw)] == ["d0002"]
    # The next redraft updates the new draft, and makes no third.
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi 3.")
    assert len(tool.creates()) == 2
    assert tool.drafts["d0002"]["body"] == "Hi 3."


async def test_flag_off_each_save_is_a_new_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
    redis: _Redis,
) -> None:
    _flag(monkeypatch, "")
    card.answers.extend([True, True])
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi Ravi.")
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi 2.")
    assert len(tool.creates()) == 2
    assert all("draft_id" not in c for c in tool.calls)
    assert redis.data == {}


async def test_a_redis_fault_saves_as_before(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
    redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    redis.fail = True
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi Ravi.")
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi 2.")
    assert len(tool.creates()) == 2


async def test_the_record_key_holds_the_tenant_and_no_address(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
    redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.append(True)
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi.")
    [key] = redis.data
    assert key.startswith(f"cc:{ORG}:{cr.NAMESPACE}:")
    assert MEMBER not in key and THREAD not in key


async def test_another_chat_never_takes_the_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi.")
    monkeypatch.setattr(agents, "_run_thread", lambda: "another-thread")
    await agents.edit_draft(BOX, to=["ravi@contoso.test"], subject="Rates", body="Hi 2.")
    assert len(tool.creates()) == 2


async def test_edit_draft_of_a_draft_that_the_chat_did_not_save_joins_no_record(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, ORG)
    await cr.note_draft(thread=THREAD, account_id=BOX, member=MEMBER, draft_id="reply-1",
                        to=["ravi@contoso.test"], subject="Re: Rates", only_if_known=True)
    assert redis.data == {}


# ── 4. The gateway saves of a new-mail card use the same rule ───────────────


async def test_the_gateway_save_updates_the_earlier_draft(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis,
) -> None:
    from fastapi import HTTPException
    from gateway.routes.email.automation import drafting

    _flag(monkeypatch, ORG)
    saved: dict[str, Any] = {}
    calls: list[Any] = []

    async def upsert(req: Any, _user: Any) -> Any:
        calls.append(req)
        if req.draft_id:
            if req.draft_id not in saved:
                raise HTTPException(status_code=404, detail="Draft not found")
            saved[req.draft_id] = req
            return {"id": req.draft_id}
        new = f"g{len(saved) + 1}"
        saved[new] = req
        return {"id": new}

    fields = {"account_id": BOX, "to": ["ravi@contoso.test"], "cc": [], "bcc": [],
              "subject": "Rates", "body": "Hi.", "from_chat": True}
    one = await drafting.save_chat_draft(fields, object(), thread=THREAD, member=MEMBER,
                                         org=ORG, upsert=upsert)
    two = await drafting.save_chat_draft({**fields, "body": "Hi 2."}, object(),
                                         thread=THREAD, member=MEMBER, org=ORG, upsert=upsert)
    assert one["id"] == two["id"] == "g1"
    assert len(saved) == 1 and saved["g1"].body == "Hi 2."
    saved.clear()
    three = await drafting.save_chat_draft(fields, object(), thread=THREAD, member=MEMBER,
                                           org=ORG, upsert=upsert)
    assert three["id"] == "g1" and [c.draft_id for c in calls][-2:] == ["g1", None]
    # The agent's edit_draft and the gateway share one record.
    assert await cr.earlier_draft(thread=THREAD, account_id=BOX, member=MEMBER,
                                  to=["ravi@contoso.test"], subject="x") == "g1"


async def test_the_gateway_save_with_the_flag_off_is_one_create(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis,
) -> None:
    from gateway.routes.email.automation import drafting

    _flag(monkeypatch, "")
    calls: list[Any] = []

    async def upsert(req: Any, _user: Any) -> Any:
        calls.append(req)
        return {"id": f"g{len(calls)}"}

    fields = {"account_id": BOX, "to": ["ravi@contoso.test"], "subject": "Rates",
              "body": "Hi.", "from_chat": True}
    for _ in range(2):
        await drafting.save_chat_draft(fields, object(), thread=THREAD, member=MEMBER,
                                       org=ORG, upsert=upsert)
    assert [c.draft_id for c in calls] == [None, None]
    assert redis.data == {}


def test_each_chat_save_of_a_new_draft_goes_through_the_one_rule() -> None:
    """The three saves of a new chat draft: ``_new_draft`` in the agent, and
    the new-mail card saves of ``new_mail_card.py`` and ``held_card.py``."""
    agent = AGENT.read_text(encoding="utf-8")
    body = agent[agent.index("async def _new_draft("):agent.index("async def _save_chat_draft(")]
    assert "_save_chat_draft(payload)" in body
    assert '_put("/email/drafts"' not in body
    gw = ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "email" / "automation"
    for name in ("new_mail_card.py", "held_card.py"):
        tree = ast.parse((gw / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "upsert_draft"):
                continue
            req = node.args[0] if node.args else None
            kws = {k.arg for k in getattr(req, "keywords", [])}
            assert "from_chat" not in kws or "draft_id" in kws, (name, node.lineno)


# ── 5. The table: every card of the agent is on a known path ─────────────────


def test_every_card_of_the_agent_is_on_a_path_of_the_table() -> None:
    """A new card of the email agent goes through ``_card_refusal`` or
    ``_confirm_destructive``, or it passes ``close_on_steer`` or a reply form.
    Mutation: add a bare ``request_confirmation`` -> this test."""
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    bare: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name != "request_confirmation":
            continue
        kws = {k.arg for k in node.keywords}
        if kws & {"close_on_steer", "reply", None}:  # None: a ``**steer``
            continue
        bare.append(node.lineno)
    assert bare == [], f"a card with no steer path at lines {bare}"
