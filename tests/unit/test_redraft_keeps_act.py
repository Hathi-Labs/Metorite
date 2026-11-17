"""HRD-8a: a card that a chat message closed keeps its act (``chat_failure_modes.md`` C2).

On 2026-11-11 the owner asked the chat to forward the Textron mail with a
note, and wrote in the chat while the forward card was open. ``forward_email``
answered with ``_CARD_REVISED``, the text of a REPLY card, which told the
model to "call compose_reply again". The redraft became a NEW mail, and the
owner discarded it (task 2987). ``send_email`` and ``schedule_send`` gave the
same reply text, and the held card gave a text that named no tool.

Now ``agents._closed_by_message(act)`` builds the text of each act: the head
of the one table of card outcomes (HRD-7), then the steps of the act, which
name its own tool. Each rule names the mutation that turns it red (R7):

1. **Each text names its own tool and no other send tool.** Mutation: put
   "compose_reply" in the forward text ->
   ``test_each_text_names_its_own_tool_and_no_other_send_tool`` and
   ``test_a_forward_is_never_told_to_reply_or_to_write_a_new_mail``.
   Mutation: drop the tool name in the builder -> the same tests.
2. **The send tools answer with the text of their own act**, never with
   ``_CARD_REVISED``. Mutation: let ``send_email`` return ``_CARD_REVISED``
   again -> ``test_a_send_tool_answers_with_the_text_of_its_own_act`` and
   ``test_only_compose_reply_gives_the_reply_text``.
3. **Each held card answers with the act of its kind.** Mutation: map
   ``forward`` to ``new_mail`` -> ``test_a_held_card_answers_with_the_text_of_its_own_act``.
4. **Each text keeps the HRD-7 rules**: the measure reads it as an act that
   did not happen, it never reads as a decline, and the chat draws it as
   "Draft being changed" with the member line alone.

What the fence does not prove: that the model follows the text. That needs a
live eval, which costs money (§6 of the spec). HRD-8b adds the act kind to
the held-card record and a line on the new card.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from acb_skills import ask_tools as at

from tests.unit.test_card_outcome_vocabulary import _decline_pattern, _nothing_happened

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"
EMAIL_CARDS = (ROOT / "workbench" / "control_plane" / "src" / "components" / "email"
               / "EmailToolCards.tsx")
CARD_OUTCOMES_TS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "cardOutcomes.ts"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("ea_redraft_keeps_act", AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()

#: Each tool of the email agent that sends mail. A closed card names only the
#: ones of its own act.
SEND_TOOLS = ("compose_reply", "send_email", "forward_email", "schedule_send", "send_draft")

#: Each act: the tool that does it again, and the send tools that its text can name.
ACTS: dict[str, tuple[str, frozenset[str]]] = {
    # A reply can also go out from its draft: a Bcc with edit_draft, then send_draft.
    "reply": ("compose_reply", frozenset({"compose_reply", "send_draft"})),
    "new_mail": ("send_email", frozenset({"send_email"})),
    "send": ("send_email", frozenset({"send_email"})),
    "forward": ("forward_email", frozenset({"forward_email"})),
    "scheduled_send": ("schedule_send", frozenset({"schedule_send"})),
    "send_draft": ("send_draft", frozenset({"send_draft"})),
    "unsubscribe": ("unsubscribe_sender", frozenset()),
}


def _names(tool: str, text: str) -> bool:
    """True when *text* names *tool* as a whole word. An underscore is a word
    character, so ``send_email`` is not found inside ``resend_email``."""
    return re.search(rf"\b{re.escape(tool)}\b", text) is not None


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


# ── 1. Each text names its own tool ─────────────────────────────────────────


def test_each_act_has_one_text() -> None:
    assert set(agents._CLOSED_STEPS) == set(ACTS)
    texts = [agents._closed_by_message(a) for a in ACTS]
    assert len(set(texts)) == len(texts)
    with pytest.raises(KeyError):
        agents._closed_by_message("archive")


@pytest.mark.parametrize("act", list(ACTS))
def test_each_text_names_its_own_tool_and_no_other_send_tool(act: str) -> None:
    tool, allowed = ACTS[act]
    text = agents._closed_by_message(act)
    assert f"Its tool is {tool}." in text, text
    assert _names(tool, text)
    for other in SEND_TOOLS:
        if other not in allowed:
            assert not _names(other, text), (act, other)


def test_a_forward_is_never_told_to_reply_or_to_write_a_new_mail() -> None:
    """Task 2987: the forward of the Textron mail became a new mail."""
    text = agents._closed_by_message("forward")
    assert _names("forward_email", text)
    for other in ("compose_reply", "send_email", "send_draft", "schedule_send"):
        assert not _names(other, text), other
    assert "Keep it a forward of the same email." in text
    assert "Do not make it a new email or a reply." in text
    assert "call forward_email again with the same email_id" in text
    assert "Send it to the people that the member names." in text


def test_a_send_now_keeps_the_reply_it_was() -> None:
    """``send_email`` with ``reply_to_email_id`` is a reply with a file or a
    new subject. Its redraft stays that, through ``send_email``."""
    text = agents._closed_by_message("send")
    assert "keep the same reply_to_email_id" in text
    assert not _names("compose_reply", text)


# ── 2. The send tools answer with the text of their own act ─────────────────

BOX = "a0000000-0000-4000-8000-000000000001"
MAIL = "b0000000-0000-4000-8000-0000000000f1"
DRAFT = "00000000-0000-4000-8000-0000000000d1"
MEMBER = "dana@fracktal.test"


class _Gw:
    """The gateway, faked at ``_get`` and ``_post``. A closed card posts
    nothing but the held card's own record."""

    def __init__(self) -> None:
        self.gets: list[str] = []
        self.posts: list[str] = []
        self.cards: list[dict[str, Any]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.gets.append(path)
        if path == f"/email/messages/{MAIL}":
            return {"id": MAIL, "account_id": BOX, "subject": "Textron quote",
                    "attachments": [{"id": "f1", "filename": "quote.pdf",
                                     "size_bytes": 2048}]}
        if path == f"/email/messages/{DRAFT}":
            return {"id": DRAFT, "account_id": BOX, "folder": "drafts",
                    "subject": "Re: Quote", "body_text": "Hi Ravi.",
                    "to_addresses": [{"email": "ravi@contoso.test"}],
                    "cc_addresses": [], "bcc_addresses": []}
        if path == "/email/drafts/seal":
            return {"text_digest": "a" * 64, "subject": "Re: Quote", "body_text": "Hi Ravi."}
        return {}

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        self.posts.append(path)
        if path == "/email/held-card":
            return {"card_id": "e" * 32, "title": "Send this mail?", "detail": "d",
                    "context": "c", "rows": []}
        raise AssertionError(f"a card that a message closed posts nothing: {path}")


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch) -> _Gw:
    g = _Gw()
    monkeypatch.setattr(agents, "_get", g.get)
    monkeypatch.setattr(agents, "_post", g.post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    # The plain card of each tool: no held run, and no new-mail card to edit.
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    monkeypatch.setattr(agents, "_edit_card_open", lambda: False)
    monkeypatch.setattr(at, "reply_card_channel_open", lambda: True)

    async def name(_aid: str) -> str:
        return f"Fracktal · {MEMBER}"

    async def no_note(*_a: Any) -> str:
        return ""

    async def no_rule(_aid: str, _to: list[str], cc: list[str], _bcc: Any) -> Any:
        return cc, []

    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_usual_mailbox_note", no_note)
    monkeypatch.setattr(agents, "_always_copy_cc", no_rule)

    async def card(*_a: Any, **kw: Any) -> Any:
        """The card closes as a chat message closes it (``REVISED``)."""
        g.cards.append(kw)
        return at._noted(at.CLOSED_BY_YOUR_MESSAGE, at.REVISED)

    monkeypatch.setattr(at, "request_confirmation", card)
    return g


def _soon() -> str:
    return (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()


#: Each send tool whose plain card closes on a chat message, by its act.
PLAIN: dict[str, Any] = {
    "send": lambda: agents.send_email(BOX, body="Hi Ravi.", to=["ravi@contoso.test"],
                                      subject="Rates"),
    "forward": lambda: agents.forward_email(MAIL, ["lee@contoso.test"],
                                            note="Please see the quote."),
    "scheduled_send": lambda: agents.schedule_send(BOX, DRAFT, _soon()),
}

#: Each tool of a held run, by the act of the kind of its held card.
HELD: dict[str, Any] = {
    "new_mail": PLAIN["send"],
    "forward": PLAIN["forward"],
    "scheduled_send": PLAIN["scheduled_send"],
    "send_draft": lambda: agents.send_draft(BOX, DRAFT),
    "unsubscribe": lambda: agents.unsubscribe_sender(BOX, email="news@list.example"),
}


@pytest.mark.parametrize("act", list(PLAIN))
async def test_a_send_tool_answers_with_the_text_of_its_own_act(gw: _Gw, act: str) -> None:
    out = await PLAIN[act]()
    assert out == agents._closed_by_message(act)
    assert out != agents._CARD_REVISED
    assert not _names("compose_reply", out)
    # The card closes on a chat message, and nothing went out.
    [card] = gw.cards
    assert card.get("close_on_steer") is True
    assert gw.posts == []


@pytest.mark.parametrize("act", list(HELD))
async def test_a_held_card_answers_with_the_text_of_its_own_act(
    gw: _Gw, monkeypatch: pytest.MonkeyPatch, act: str,
) -> None:
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    out = await HELD[act]()
    assert out == agents._closed_by_message(act)
    assert gw.posts == ["/email/held-card"]
    [card] = gw.cards
    assert card.get("close_on_steer") is True


def _held_kinds() -> set[str]:
    """Each ``kind`` that a call of ``_held_card`` in the agent sends."""
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    kinds: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_held_card" and node.args
                and isinstance(node.args[0], ast.Dict)):
            for key, value in zip(node.args[0].keys, node.args[0].values, strict=True):
                if (isinstance(key, ast.Constant) and key.value == "kind"
                        and isinstance(value, ast.Constant)):
                    kinds.add(value.value)
    return kinds


def test_each_kind_of_held_card_has_an_act() -> None:
    """A new kind of held card needs its act, or its card raises before it shows."""
    kinds = _held_kinds()
    assert kinds == {"new", "forward", "draft", "schedule", "unsubscribe"}
    assert set(agents._HELD_KIND_ACTS) == kinds
    assert set(agents._HELD_KIND_ACTS.values()) <= set(agents._CLOSED_STEPS)
    # The held test above drives each kind.
    assert set(HELD) == set(agents._HELD_KIND_ACTS.values())


def _function_of(tree: ast.Module) -> dict[int, str]:
    """The top-level function of each node, by the node's ``id``."""
    out: dict[int, str] = {}
    for fn in tree.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                out[id(node)] = fn.name
    return out


def test_only_compose_reply_gives_the_reply_text() -> None:
    """``_CARD_REVISED`` is the text of a reply card. Only ``_revised_line``
    reads it, and only ``compose_reply`` calls ``_revised_line``."""
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    owner = _function_of(tree)
    readers = {owner.get(id(n), "<module>") for n in ast.walk(tree)
               if isinstance(n, ast.Name) and n.id == "_CARD_REVISED"
               and isinstance(n.ctx, ast.Load)}
    assert readers == {"_revised_line"}
    callers = {owner.get(id(n), "<module>") for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_revised_line"}
    assert callers == {"compose_reply"}
    reply = agents._closed_by_message("reply")
    assert reply == agents._CARD_REVISED
    assert not hasattr(agents, "_HELD_CARD_REVISED")


# ── 3. The rules of HRD-7 hold for each text ────────────────────────────────


@pytest.mark.parametrize("act", list(ACTS))
def test_each_text_says_nothing_was_done_and_never_a_decline(act: str) -> None:
    """The measure of ``evals/one_assistant_measure`` reads it as no act, and
    the member did not answer the card, so it never reads as a no (task 2494)."""
    text = agents._closed_by_message(act)
    assert _nothing_happened().search(text), act
    assert not _decline_pattern().search(text), (act, _decline_pattern().search(text))


@pytest.mark.parametrize("act", list(ACTS))
def test_each_text_is_simplified_technical_english(act: str) -> None:
    text = agents._closed_by_message(act)
    assert ";" not in text
    for sentence in _sentences(text):
        assert len(sentence.split()) <= 20, sentence


@pytest.mark.parametrize("act", list(ACTS))
def test_the_chat_draws_each_text_as_a_draft_being_changed(act: str) -> None:
    """The two-way fence of HRD-7 holds the TS rows equal to the Python table
    (``test_card_outcome_vocabulary.py``, ``cardOutcomes.test.ts``). So:

    * The text starts with the lead and its colon, and ``cardOutcomeOf``
      reads "closed by your message". ``noActionOf`` maps it to the chip
      "Draft being changed".
    * The text holds no next step of the table, so ``memberLineOf`` gives
      the member line alone, and no step for the model reaches the member."""
    row = at.CARD_OUTCOMES[at.CLOSED_BY_YOUR_MESSAGE]
    text = agents._closed_by_message(act)
    assert text.startswith(f"{row.lead}: {row.happened} Nothing was sent. ")
    assert text.startswith(at.card_outcome_head(at.CLOSED_BY_YOUR_MESSAGE, "Nothing was sent."))
    assert row.next not in text
    cards = EMAIL_CARDS.read_text(encoding="utf-8")
    assert 'if (outcome === "closed by your message") return "revised";' in cards
    assert 'revised: { icon: "PencilLine", label: "Draft being changed" },' in cards
    lines = CARD_OUTCOMES_TS.read_text(encoding="utf-8")
    assert "const at = t.indexOf(row.next, head.length);" in lines


# ═══ HRD-8b: the link keeps the act kind and the Cc, and the next card says what changed ═══
#
# ``redraft.link_fields`` links a new card to the card that a chat message
# revised. HRD-8b adds ``act`` and ``cc`` to the link and ``changed`` to the
# answer: the act kind, the To and the Cc that differ from the revised card.
# The default (task 3459, until the owner answers): a card with a changed act
# or Cc STILL takes the link (``revises``, ``draft``) and carries ``changed``.
# Each mutation names the tests that turn red (R7):
#
# 17. a link written with no act or Cc: ``test_the_link_keeps_the_act_and_the_cc``.
# 18. a ``changed`` that includes a part that is equal, or that claims a part of
#     an old link that has none: ``test_changed_since_*``.
# 19. a changed Cc that drops the link: ``test_a_changed_cc_still_takes_the_link``.
# 20. a ``changed`` that never reaches the form: ``test_the_form_carries_changed``.
#
# Not here: a forward and a reply card (HRD-8b-2), and the words of the line
# (``redraftCard.test.ts``). The Redis case of the real order skips in pr-check
# until task 1856.
import json  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from gateway.routes.email.automation import held_card as hc  # noqa: E402
from gateway.routes.email.automation import redraft as _rd  # noqa: E402

from tests.unit import _held_card_fakes as F  # noqa: E402
from tests.unit.test_email_redraft_snapshot import env  # noqa: E402, F401  (a fixture, by name)


def test_act_of_reads_the_kinds_that_the_revise_knows():
    assert _rd.act_of({"kind": "new-mail"}) == "new"
    assert _rd.act_of({"kind": "new"}) == "new"
    assert _rd.act_of({"kind": "forward"}) == "forward"  # HRD-8b-2
    assert _rd.act_of({"kind": "unsubscribe"}) == ""  # a kind that the revise does not read
    assert _rd.act_of({}) == ""


def test_changed_since_names_only_what_differs():
    link = {"act": "new", "to": ["a@x.test"], "cc": ["c@x.test"]}
    assert _rd.changed_since(link, act="new", to=["A@x.test"], cc=["c@x.test"]) == {}
    out = _rd.changed_since(link, act="forward", to=["a@x.test", "b@x.test"], cc=[])
    assert out == {"act": {"was": "new", "now": "forward"},
                   "to": {"added": ["b@x.test"], "removed": []},
                   "cc": {"added": [], "removed": ["c@x.test"]}}


def test_changed_since_claims_nothing_for_a_part_that_an_old_link_lacks():
    old = {"to": ["a@x.test"]}  # a link written before HRD-8b
    assert _rd.changed_since(old, act="new", to=["a@x.test"], cc=["c@x.test"]) == {}
    assert _rd.changed_since(old, act="new", to=["a@x.test", "b@x.test"], cc=[]) == {
        "to": {"added": ["b@x.test"], "removed": []}}


async def test_the_link_keeps_the_act_and_the_cc(env):  # noqa: F811 — a fixture, by name
    from tests.unit.test_email_redraft_snapshot import THREAD, _card, _revise

    old = await _card()
    await _revise(old["card_id"])
    links = [json.loads(v) for k, v in env["redis"].keys.items() if ":card_revised:" in k]
    assert len(links) == 1
    assert links[0]["act"] == "new" and links[0]["cc"] == [F.COLLEAGUE]
    assert links[0]["card_id"] == old["card_id"] and THREAD


async def test_a_card_with_the_same_people_says_nothing_changed(env):  # noqa: F811 — a fixture, by name
    from tests.unit.test_email_redraft_snapshot import _card, _revise

    old = await _card()
    await _revise(old["card_id"])
    new = await _card()
    assert new["revises"] == old["card_id"] and "changed" not in new


async def test_a_changed_cc_still_takes_the_link(env):  # noqa: F811 — a fixture, by name
    """The default of task 3459: the card takes the link and says what changed."""
    from tests.unit.test_email_redraft_snapshot import _card, _revise

    old = await _card()
    await _revise(old["card_id"])
    new = await _card(cc=[F.STRANGER])
    assert new["revises"] == old["card_id"] and new["draft"] == 2
    assert new["changed"] == {"cc": {"added": [F.STRANGER.lower()], "removed": [F.COLLEAGUE]}}
    rec = json.loads(env["redis"].keys[f"cc:{F.ORG}:new-mail-card:{new['card_id']}"])
    assert rec["changed"] == new["changed"]


async def test_a_card_to_other_people_still_takes_no_link(env):  # noqa: F811 — a fixture, by name
    """The To-overlap rule stays: a changed To with no shared address is its own card."""
    from tests.unit.test_email_redraft_snapshot import _card, _revise

    old = await _card()
    await _revise(old["card_id"])
    new = await _card(to=[F.OTHER])
    assert "revises" not in new and "changed" not in new


async def test_the_form_carries_changed(env):  # noqa: F811 — a fixture, by name
    from tests.unit.test_email_redraft_snapshot import _held, _revise

    old = await _held()
    await _revise(old["card_id"])
    new = await _held(cc=[F.COLLEAGUE])
    assert new["form"]["revises"] == old["card_id"]
    assert new["form"]["changed"] == {"cc": {"added": [F.COLLEAGUE], "removed": [F.STRANGER.lower()]}}


def test_the_form_check_keeps_the_shape_of_changed_and_refuses_the_rest():
    from acb_skills.ask_tools import _new_mail_changed

    ok = {"act": {"was": "new", "now": "forward"},
          "to": {"added": ["a@x.test"], "removed": []},
          "cc": {"added": [], "removed": ["c@x.test"]}}
    assert _new_mail_changed(ok) == ok
    assert _new_mail_changed({"cc": {"added": [], "removed": []}}) == {}  # nothing differs
    for bad in (
        {}, "x", {"subject": "different"},
        {"act": {"was": "new", "now": "weird"}},
        {"act": {"was": "new"}},
        {"to": {"added": "a@x.test", "removed": []}},
        {"to": {"added": [3], "removed": []}},
        {"cc": {"added": ["a" * 400], "removed": []}},
        {"cc": {"added": ["a@x.test"]}},
    ):
        with pytest.raises(ValueError):
            _new_mail_changed(bad)


def test_the_agent_copies_changed_into_the_plain_new_mail_form():
    """The plain card is the common path: ``_new_mail_form`` copies ``changed`` with the link.
    Mutation: delete the copy in ``agents._new_mail_form`` -> red."""
    from acb_skills.ask_tools import clean_reply_form

    changed = {"cc": {"added": ["x@y.test"], "removed": []}}
    prep = {"to": [F.KNOWN], "cc": [F.COLLEAGUE, "x@y.test"], "bcc": [], "subject": "Rates",
            "body": "Hi", "revises": "a" * 32, "draft": 2, "changed": changed}
    form = agents._new_mail_form(prep, [])
    assert form["revises"] == "a" * 32 and form["changed"] == changed
    assert clean_reply_form(form)["changed"] == changed
    # No link, no `changed`: the tool copies it only with the link it belongs to.
    assert "changed" not in agents._new_mail_form({**prep, "revises": ""}, [])


def test_the_form_check_refuses_changed_with_no_link():
    from acb_skills.ask_tools import clean_reply_form

    form = {**_plain_form_for_changed(), "changed": {"cc": {"added": ["x@y.test"], "removed": []}}}
    with pytest.raises(ValueError):
        clean_reply_form(form)
    ok = clean_reply_form({**form, "revises": "a" * 32, "draft": 2})
    assert ok["changed"] == {"cc": {"added": ["x@y.test"], "removed": []}}


def _plain_form_for_changed() -> dict:
    people = {"to": [F.KNOWN], "cc": []}
    return {"kind": "new", "mailbox": "Work", "subject": "Rates", "body": "Hi",
            "recipients": {"reply": people, "reply_all": people}, "files": [],
            "notices": [], "note": ""}


# ═══ HRD-8b-2: a forward card joins the revise path ═══
#
# A held forward card is a rows card in the held-card namespace. It names its chat
# thread, and a chat message that closes it spends it and writes the link with
# ``act: "forward"``. The next card that can show the line (a new mail) takes the
# link, and says the act changed. The forward card takes no link itself: it draws
# no "Draft N" and no line, so it would only use the link up. A reply card has its
# own record and joins in HRD-8b-3. Mutations (R7):
#
# 21. a forward record with no thread: ``test_a_forward_card_names_its_thread``.
# 22. a revise that refuses a forward card: ``test_a_revised_forward_writes_the_link_with_its_act``.
# 23. a forward card that takes the link: ``test_a_forward_card_takes_no_link``.
# 24. a snapshot kept for a forward card: ``test_no_snapshot_is_kept_for_a_forward_card``.
# 25. an agent that never revises a closed forward:
#     ``test_a_closed_forward_card_is_revised_in_an_edit_run_and_only_there``.


async def _forward(thread: str | None = None, **kw: Any) -> dict[str, Any]:
    from tests.unit.test_email_redraft_snapshot import THREAD

    body = {"message_id": F.MAIL, "to": [F.KNOWN], "cc": [F.COLLEAGUE],
            "card_thread_id": THREAD if thread is None else thread, **kw}
    return await hc.held_card(hc.HeldCardRequest(kind="forward", **body), F.user())


def test_a_forward_request_may_name_its_thread():
    hc.HeldCardRequest(kind="forward", message_id=F.MAIL, to=[F.KNOWN], card_thread_id="t1")
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError
        hc.HeldCardRequest(kind="draft", account_id=F.BOX, draft_id="d" * 8, card_thread_id="t1")


async def test_a_forward_card_names_its_thread(env, monkeypatch):  # noqa: F811 — a fixture, by name
    from tests.unit.test_email_redraft_snapshot import THREAD

    card = await _forward()
    rec = json.loads(env["redis"].keys[f"cc:{F.ORG}:held-card:{card['card_id']}"])
    assert rec["kind"] == "forward" and rec["thread"] == THREAD
    # With the edit flag off the record names no thread, so nothing revises it.
    from tests.unit import test_email_redraft_snapshot as snap

    snap.edit_card(monkeypatch, "")
    off = await _forward()
    rec = json.loads(env["redis"].keys[f"cc:{F.ORG}:held-card:{off['card_id']}"])
    assert "thread" not in rec


async def test_a_revised_forward_writes_the_link_with_its_act(env):  # noqa: F811
    from tests.unit.test_email_redraft_snapshot import _revise

    card = await _forward()
    out = await _revise(card["card_id"])
    assert out["revised"] is True and out["draft"] == 2 and out["snapshot"] == ""
    links = [json.loads(v) for k, v in env["redis"].keys.items() if ":card_revised:" in k]
    assert len(links) == 1
    assert links[0]["act"] == "forward" and links[0]["cc"] == [F.COLLEAGUE]
    assert links[0]["to"] == [F.KNOWN] and links[0]["card_id"] == card["card_id"]
    # The record is spent: a late answer to the revised card finds no card.
    assert f"cc:{F.ORG}:held-card:{card['card_id']}" not in env["redis"].keys


async def test_a_new_mail_after_a_forward_says_the_act_changed(env):  # noqa: F811
    from tests.unit.test_email_redraft_snapshot import _card, _revise

    old = await _forward()
    await _revise(old["card_id"])
    new = await _card(cc=[F.COLLEAGUE])
    assert new["revises"] == old["card_id"] and new["draft"] == 2
    assert new["changed"] == {"act": {"was": "forward", "now": "new"}}


async def test_a_forward_card_takes_no_link(env):  # noqa: F811
    """It would use the link up and show nothing. The link waits for a card that shows it."""
    from tests.unit.test_email_redraft_snapshot import _card, _revise

    old = await _card()
    await _revise(old["card_id"])
    fwd = await _forward()
    assert "revises" not in fwd and "draft" not in fwd
    rec = json.loads(env["redis"].keys[f"cc:{F.ORG}:held-card:{fwd['card_id']}"])
    assert "revises" not in rec and "draft" not in rec
    assert (await _card())["revises"] == old["card_id"]  # the link is still there


async def test_a_forward_of_another_member_or_thread_is_not_revised(env):  # noqa: F811
    from tests.unit.test_email_redraft_snapshot import OTHER_THREAD, _revise

    card = await _forward()
    with pytest.raises(HTTPException) as other_thread:
        await _revise(card["card_id"], thread=OTHER_THREAD)
    with pytest.raises(HTTPException) as other_member:
        await _revise(card["card_id"], who=F.OTHER)
    assert other_thread.value.status_code == 404 and other_member.value.status_code == 404
    assert f"cc:{F.ORG}:held-card:{card['card_id']}" in env["redis"].keys  # nothing was spent


async def test_no_snapshot_is_kept_for_a_forward_card(env):  # noqa: F811
    from tests.unit.test_email_redraft_snapshot import _keep, _snapshot_keys

    card = await _forward()
    assert await _keep(card["card_id"]) is False
    assert _snapshot_keys(env["redis"]) == []


async def test_a_closed_forward_card_is_revised_in_an_edit_run_and_only_there(gw, monkeypatch):
    """The agent spends a closed forward card in a run that can draw the edit card.
    Mutation: drop the ``_revise_forward_card`` call in ``_held_card`` -> red."""
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    posts: list[tuple[str, Any]] = []
    fail = {"revise": False}

    async def post(path: str, body: Any = None, **_kw: Any) -> Any:
        posts.append((path, body))
        if path == "/email/held-card":
            return {"card_id": "a" * 32, "title": "Forward this email?", "detail": "",
                    "context": "", "rows": []}
        if fail["revise"]:
            raise agents.GatewayError(500, "boom")
        return {"revised": True, "draft": 2}

    monkeypatch.setattr(agents, "_post", post)
    # A plain run: no edit card, no thread, no revise.
    assert await HELD["forward"]() == agents._closed_by_message("forward")
    assert [p for p, _ in posts] == ["/email/held-card"]
    assert "card_thread_id" not in posts[0][1]
    # A run that draws the edit card: the closed forward is spent, and the text is the same.
    posts.clear()
    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)
    monkeypatch.setattr(agents, "_run_thread", lambda: "chat-thread-1")
    assert await HELD["forward"]() == agents._closed_by_message("forward")
    assert [p for p, _ in posts] == ["/email/held-card", "/email/new-card/revise"]
    assert posts[0][1]["card_thread_id"] == "chat-thread-1"
    assert posts[1][1] == {"card_id": "a" * 32, "card_thread_id": "chat-thread-1"}
    # A fault of the revise never changes the answer (the link is a label).
    posts.clear()
    fail["revise"] = True
    assert await HELD["forward"]() == agents._closed_by_message("forward")
    assert posts[-1][0] == "/email/new-card/revise"
