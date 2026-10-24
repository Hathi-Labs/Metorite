"""H-393: look-alike letters in a chat send (``email_app_master_plan.md`` §21.13).

A Cyrillic "\u0430" inside a Latin word shows on a card as a Latin "a". A model can
use such letters, or an invisible character inside a word, to carry member
data out in a text that looks right. So each chat send path that a held chat
reaches refuses such a word, with one sentence that names the word and the
letter. Outside a held chat the card warns. A text in one script passes.

The one test is ``acb_skills.reply_card.lookalike_words``. The letter that a
look-alike imitates comes from the ONE table of ``held_card`` (``_CONFUSABLE``).
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from acb_skills import held_card as hcard
from acb_skills import reply_card as rcard

REPO = Path(__file__).resolve().parents[2]
_AGENT = REPO / "apps" / "agents" / "agent-email-assistant" / "agents.py"

CYR_A = "\u0430"  # CYRILLIC SMALL LETTER A
MAIL = "0b0b0b0b-0000-4000-8000-00000000000b"
BOX = "acc-1"
MEMBER = "jane@acme.test"


def _load_email():
    spec = importlib.util.spec_from_file_location("ea_lookalike", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_email()


# ── 1. The one test ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, word, letter", [
    (f"Pay to p{CYR_A}ypal now", f"p{CYR_A}ypal", CYR_A),
    ("The c\u03bfde is ready", "c\u03bfde", "\u03bf"),            # a Greek omicron
    ("bank\U0001d41a ok", "bank\U0001d41a", "\U0001d41a"),        # a MATHEMATICAL BOLD a
    ("pay\u200bpal", "paypal", "\u200b"),                          # ZERO WIDTH SPACE
    ("in\u2060voice", "invoice", "\u2060"),                        # WORD JOINER
], ids=["cyrillic", "greek", "mathematical", "zero-width", "word-joiner"])
def test_a_word_that_mixes_scripts_or_hides_a_character_is_named(
    text: str, word: str, letter: str,
) -> None:
    [hit] = rcard.lookalike_words(text)
    assert hit.word == word and hit.letter == letter


@pytest.mark.parametrize("text", [
    "\u0928\u092e\u0938\u094d\u0924\u0947, \u0906\u092a \u0915\u0948\u0938\u0947 \u0939\u0948\u0902? \u0915\u0932 \u092e\u093f\u0932\u0924\u0947 \u0939\u0948\u0902\u0964",         # all Hindi
    "\u041f\u0440\u0438\u0432\u0435\u0442, \u043a\u0430\u043a \u0434\u0435\u043b\u0430? \u0412\u0441\u0442\u0440\u0435\u0447\u0430 \u0432 \u043f\u044f\u0442\u043d\u0438\u0446\u0443.",        # all Russian
    "\u0393\u03b5\u03b9\u03b1 \u03c3\u03bf\u03c5 \u03ba\u03cc\u03c3\u03bc\u03b5",                             # all Greek
    "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645 \u0628\u06cc\u0627\u06cc\u0645",                         # Persian, with its ZWNJ
    "\u6771\u4eac\u30bf\u30ef\u30fc\u306b\u884c\u304d\u307e\u3059",                          # Japanese mixes three scripts
    "T\u30b7\u30e3\u30c4",                                     # and Latin with katakana
    "na\u00efve caf\u00e9, Hawai\u02bbi, fr4cktal.in",            # Latin with marks and digits
    "Thanks \U0001f468\u200d\U0001f469\u200d\U0001f467 see you",            # an emoji joiner
    "Hi Kim,\n\n\u0928\u092e\u0938\u094d\u0924\u0947 and \u041f\u0440\u0438\u0432\u0435\u0442 in one mail.",   # scripts in separate words
], ids=["hindi", "russian", "greek", "persian", "japanese", "katakana", "latin",
        "emoji", "separate-words"])
def test_text_in_one_script_passes(text: str) -> None:
    assert rcard.lookalike_words(text) == []
    assert rcard.lookalike_refusal({"text": text}) is None
    assert rcard.lookalike_warning({"text": text}) is None


def test_the_refusal_is_one_sentence_that_names_the_word_and_the_letter() -> None:
    why = rcard.lookalike_refusal({"subject": "Quote", "text": f"Pay p{CYR_A}ypal"})
    assert why is not None and why.startswith("Not sent. ")
    assert f'"p{CYR_A}ypal"' in why and "Cyrillic" in why and "U+0430" in why
    assert 'looks like the Latin "a"' in why
    assert ";" not in why


def test_the_refusal_names_an_invisible_character_by_its_code_point() -> None:
    why = rcard.lookalike_refusal({"subject": "in\u200binvoice"})
    assert why is not None and "subject" in why and "U+200B" in why


def test_the_letter_it_imitates_comes_from_the_one_table() -> None:
    """No second table (H-393): ``confusable_of`` reads ``_CONFUSABLE``."""
    assert hcard.confusable_of(CYR_A) == "a"
    assert hcard.confusable_of("a") == "" and hcard.confusable_of("\u0905") == ""
    src = (REPO / "packages/acb_skills/acb_skills/reply_card.py").read_text(encoding="utf-8")
    assert "maketrans" not in src


# ── 2. The held card of the gateway refuses (a held chat by definition) ──────


def _held_req(**kw: Any) -> Any:
    from gateway.routes.email.automation import held_card as gh

    base = {"kind": "new", "account_id": str(uuid.uuid4()), "to": ["kim@contoso.test"],
            "subject": "Quote", "note": "Hello Kim."}
    base.update(kw)
    return gh.HeldCardRequest(**base)


async def test_the_held_new_mail_card_refuses_a_lookalike_word() -> None:
    from fastapi import HTTPException
    from gateway.routes.email.automation import held_card as gh

    user = type("U", (), {"email": MEMBER, "organization_id": "org-1"})()
    with pytest.raises(HTTPException) as err:
        await gh._new_card(_held_req(note=f"Send to p{CYR_A}ypal."), user)
    assert err.value.status_code == 422 and "U+0430" in err.value.detail


async def test_the_held_forward_card_refuses_a_lookalike_note() -> None:
    from fastapi import HTTPException
    from gateway.routes.email.automation import held_card as gh

    user = type("U", (), {"email": MEMBER, "organization_id": "org-1"})()
    req = _held_req(kind="forward", message_id=str(uuid.uuid4()), subject="",
                    note=f"FYI p{CYR_A}ypal", account_id="")
    with pytest.raises(HTTPException) as err:
        await gh._forward_card(req, user)
    assert err.value.status_code == 422


def test_the_held_draft_card_checks_the_subject_and_the_text() -> None:
    import inspect

    from gateway.routes.email.automation import held_card as gh

    src = inspect.getsource(gh._draft_card)
    assert 'refuse_lookalike({"subject"' in src


# ── 3. The tools: a held run refuses, another run warns ──────────────────────


class _Gateway:
    def __init__(self, body: str = "Hi Kim,\n\nYes.") -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.body = body

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        if path == "/email/reply-card":
            return {
                "account_id": BOX, "mailbox": f"Work \u00b7 {MEMBER}", "subject": "Re: Quote",
                "original": {"from": "Kim <kim@contoso.test>", "subject": "Quote",
                             "received": "", "excerpt": "Can you confirm?"},
                "recipients": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                               "reply_all": {"to": ["kim@contoso.test"], "cc": []}},
                "body": self.body, "message_id": MAIL, "notices": [],
            }
        if path == "/email/reply-card/act":
            return {"ok": True, "action": "send", "to": ["kim@contoso.test"], "cc": [],
                    "mailbox": f"Work \u00b7 {MEMBER}"}
        raise AssertionError(f"unexpected POST {path}")


@pytest.fixture()
def gw(monkeypatch) -> _Gateway:
    g = _Gateway()
    monkeypatch.setattr(agents, "_post", g.post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    return g


def _answer(body: str) -> str:
    return "APPROVE " + json.dumps({"reply": {
        "action": "send", "reply_all": False, "body": body, "to": ["kim@contoso.test"],
        "cc": [], "message_id": MAIL}})


def _cards(monkeypatch, body: str) -> list[dict[str, Any]]:
    from acb_skills.ask_tools import ReplyAnswer

    seen: list[dict[str, Any]] = []

    async def card(**kw: Any) -> Any:
        seen.append(kw)
        text = _answer(body)
        return ReplyAnswer("rid-7", text, rcard.parse_reply_answer(text), MEMBER, "th-7")

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    return seen


async def test_a_held_compose_reply_refuses_an_agreed_text_before_any_draft(
    monkeypatch, gw,
) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    cards = _cards(monkeypatch, "x")
    out = await agents.compose_reply(MAIL, BOX, draft=f"Pay p{CYR_A}ypal")
    assert out.startswith("Not sent.") and "U+0430" in out
    assert gw.posts == [] and cards == []


async def test_a_held_compose_reply_refuses_a_drafted_lookalike(monkeypatch, gw) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    gw.body = "Hi Kim, the c\u03bfde is 42."
    cards = _cards(monkeypatch, "x")
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith("Not sent.") and cards == []
    assert [p for p, _ in gw.posts] == ["/email/reply-card"]


async def test_a_held_compose_reply_refuses_the_members_edited_text(monkeypatch, gw) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    _cards(monkeypatch, f"Yes, p{CYR_A}ypal.")
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith("Nothing was sent or saved.")
    assert [p for p, _ in gw.posts] == ["/email/reply-card"]


async def test_outside_a_held_chat_the_reply_card_warns(monkeypatch, gw) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    gw.body = f"Hi Kim, pay p{CYR_A}ypal."
    cards = _cards(monkeypatch, "Fine.")
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith("Sent to")
    [card] = cards
    assert any("Check each letter" in n and "U+0430" in n for n in card["reply"]["notices"])


async def test_a_plain_hindi_reply_sends_in_a_held_chat(monkeypatch, gw) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    gw.body = "\u0928\u092e\u0938\u094d\u0924\u0947 \u0915\u093f\u092e, \u0939\u093e\u0901, \u0936\u0941\u0915\u094d\u0930\u0935\u093e\u0930 \u0920\u0940\u0915 \u0939\u0948\u0964"
    _cards(monkeypatch, "\u0928\u092e\u0938\u094d\u0924\u0947 \u0915\u093f\u092e, \u0939\u093e\u0901, \u0936\u0941\u0915\u094d\u0930\u0935\u093e\u0930 \u0920\u0940\u0915 \u0939\u0948\u0964")
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith("Sent to")


async def test_a_held_send_draft_refuses_a_lookalike_subject(monkeypatch) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: True)

    async def seal(_aid: str, _did: str, _lead: str) -> dict[str, Any]:
        return {"subject": f"Inv{CYR_A}ice", "body_text": "Hello.", "text_digest": "d"}

    monkeypatch.setattr(agents, "_draft_seal", seal)
    out = await agents._sealed_card("Work", BOX, "d-1", {"to": ["kim@contoso.test"],
                                                          "cc": [], "bcc": []},
                                    {}, "Not scheduled", "too many")
    assert isinstance(out, str) and out.startswith("Not scheduled.") and "subject" in out


async def test_outside_a_held_chat_the_draft_card_warns(monkeypatch) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: False)

    async def seal(_aid: str, _did: str, _lead: str) -> dict[str, Any]:
        return {"subject": "Invoice", "body_text": f"Pay p{CYR_A}ypal.", "text_digest": "d"}

    monkeypatch.setattr(agents, "_draft_seal", seal)
    detail, _context, _seal = await agents._sealed_card(
        "Work", BOX, "d-1", {"to": ["kim@contoso.test"], "cc": [], "bcc": []}, {},
        "Not sent", "too many")
    assert detail.startswith("Check each letter.")
