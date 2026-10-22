"""H-374: no text that a model reads sends the member away to send a mail.

Owner decision of 2026-10-22, "Drafts, with a card"
(``email_app_master_plan.md`` §18.5). A held chat keeps ``send_draft``
behind its card. Before this change, the chat told the owner to "paste it by
hand" or to "ask me in a new conversation" for a draft that it could send.

This fence reads each text that a model reads: the hold texts of
``acb_skills.egress``, each agent's ``instructions.md``, and each string in
each agent's ``agents.py`` (the docstrings, which MAF gives to the model as
tool descriptions, and the tool answers). A sentence that suggests a new
conversation, a new chat, pasting or doing it by hand fails, unless the
sentence forbids it ("Never tell the member to paste ...").

It also pins the in-chat next steps of the H-374 audit. A refusal of
``send_draft``, ``unsubscribe_sender`` or ``run_rules``, and the cut text on
the draft card, give a step in the chat and never only "the Email app".

What breaks it (R7), each run red by hand on this branch:

* put "or ask for it in a new conversation" back in ``EGRESS_HOLD_NOTE_MAIL``;
* put "Send it from the Email app." back in ``send_draft``;
* put "You cannot add a CC or a BCC." back in ``compose_reply``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from acb_skills import egress as eg

REPO = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO / "apps" / "agents"
EMAIL_AGENT = AGENTS_DIR / "agent-email-assistant" / "agents.py"

#: A way out of the chat that the model must not offer as the way to send.
_WORKAROUND = re.compile(
    r"\bnew (conversation|chat)\b|\bby hand\b|\bpaste\b", re.IGNORECASE)
#: A sentence that forbids the workaround may name it.
_FORBIDS = re.compile(r"\b(never|not|no)\b", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s for s in re.split(r"(?<=[.!?])\s+", flat) if s]


def _offers(text: str) -> list[str]:
    return [s for s in _sentences(text)
            if _WORKAROUND.search(s) and not _FORBIDS.search(s)]


def _strings(path: Path, only: set[str] | None = None) -> list[str]:
    """Each string constant of *path*, or of the named functions only."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: list[ast.AST] = [tree]
    if only is not None:
        roots = [n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name in only]
        assert {n.name for n in roots} == only, "a named function is gone"
    return [n.value for r in roots for n in ast.walk(r)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


_HOLD_TEXTS = ("EGRESS_HELD_MAIL_ANSWER", "EGRESS_HOLD_NOTE_MAIL",
               "EGRESS_HOLD_NOTE_SANDBOX", "EGRESS_WITHHELD_ANSWER")


@pytest.mark.parametrize("name", _HOLD_TEXTS)
def test_no_hold_text_offers_a_workaround(name: str) -> None:
    assert _offers(getattr(eg, name)) == [], name


def test_the_mail_hold_says_a_saved_draft_still_sends() -> None:
    for text in (eg.EGRESS_HOLD_NOTE_MAIL, eg.EGRESS_HELD_MAIL_ANSWER):
        assert "send_draft" in text
        assert "in one sentence" in text


@pytest.mark.parametrize(
    "path", sorted(AGENTS_DIR.glob("*/instructions.md")), ids=lambda p: p.parent.name)
def test_no_agent_instruction_offers_a_workaround(path: Path) -> None:
    assert _offers(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "path", sorted(AGENTS_DIR.glob("*/agents.py")), ids=lambda p: p.parent.name)
def test_no_agent_string_offers_a_workaround(path: Path) -> None:
    found = [s for text in _strings(path) for s in _offers(text)]
    assert found == []


def test_the_email_instructions_send_a_held_draft_with_send_draft() -> None:
    text = " ".join((AGENTS_DIR / "agent-email-assistant" / "instructions.md")
                    .read_text(encoding="utf-8").split())
    assert "`send_draft` works in every chat" in text
    assert "this chat cannot do it because it read outside mail" in text
    assert "Give no other way." in text


#: Text that the H-374 audit removed. Each one sent the member to the Email
#: app as the only way, or contradicted the in-chat Cc flow or the card.
_REMOVED = (
    "Add one in the Email app",
    "Fix it in the Email app",
    "Send it from the Email app",
    "block the sender in the Email app",
    "unsubscribe in the Email app",
    "rules screen of the Email app",
    "Open the draft in the Email app",
    "You cannot add a CC",
    "ALWAYS confirm the recipients",
)


@pytest.mark.parametrize("phrase", _REMOVED)
def test_the_audit_text_stays_out_of_the_email_agent(phrase: str) -> None:
    flat = [" ".join(s.split()) for s in _strings(EMAIL_AGENT)]
    assert not [s for s in flat if phrase in s], phrase


def test_the_send_refusals_give_a_step_in_the_chat() -> None:
    """``_NO_CARD_CHANNEL`` names the Email app on purpose (a run with no
    card), and these functions return it by name, not as a literal."""
    funcs = {"send_draft", "unsubscribe_sender", "run_rules", "_with_draft_text"}
    texts = [" ".join(s.split()) for s in _strings(EMAIL_AGENT, funcs)]
    assert not [s for s in texts if "Email app" in s]
