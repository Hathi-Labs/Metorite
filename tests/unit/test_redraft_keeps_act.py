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
