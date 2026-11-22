"""WS-17 incident 2026-10-18: an email hand-off refused on WhatsApp (task 2126).

Spec ``project-docs/specs/email_app_master_plan.md`` §12.3.13.8.2. The owner
asked over WhatsApp to send a mail. Each email request of the thread was
refused, and the member read "an outgoing mail needs your approval in the
Metorite app, and I can't take that step from here".

The fix of that incident was built on the branch ``mail-handoff-refused``
and never merged. Main closes the same defects another way: the hand-off
card (EM-CR, ``_tool_injection._handoff_child_held``), the held-chat sends
(H-374 and §21, ``egress.CARD_BOUND_SEND_TOOLS``), the WhatsApp Confirm of
an egress call (WAC-P4, ``whatsapp_acts.answer_egress``) and the email
writes by a reply code (H-391). Two of them hold only behind a switch:
a new mail and a forward join a held run only with ``EMAIL_HELD_CARD_SEND``,
and each WhatsApp park needs ``WHATSAPP_ASSISTANT_WRITES``. Their fences are
``test_delegation_no_egress.py``, ``test_wac_untrusted_confirm.py`` and
``test_wac_email_confirm.py``.

This file ports the one guard of the branch that main does not hold: a
fence on each text that tells a member or the model "no card here" or "go
to the app". The owner rule of 2026-10-10 says that a channel changes
delivery, never function. So a NEW such text fails here until
:data:`NO_CARD_TEXTS` names what a WhatsApp member gets. A stale entry fails
too. ``test_wac_channel_parity.py`` holds the tools and the context blocks
of the two channels, and this file holds the refusal texts.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

#: Each text that answers "no card here", or that sends a member to an app for
#: an approval or a change. The key is the file and an ASCII part of the text.
#: The value says what a WhatsApp member gets. A new text fails the fence
#: until it is here.
NO_CARD_TEXTS: dict[tuple[str, str], str] = {
    # ── The email assistant and the email gateway: refusals of content ──
    ("apps/agents/agent-email-assistant/agents.py",
     "This draft holds spaces that the card cannot show"): (
        "Content: the draft text holds spaces that no card shows. The same "
        "answer on every channel."),
    ("apps/agents/agent-email-assistant/agents.py",
     "The files of the draft could not be read, so the card cannot show them"): (
        "A read fault of the files of the draft. The same answer on every "
        "channel."),
    ("apps/agents/agent-email-assistant/agents.py",
     "characters, so the card cannot show it. Ask the member for the right address"): (
        "Content: an address longer than the card limit. The same answer on "
        "every channel."),
    ("apps/services/gateway/gateway/routes/email/automation/drafting.py",
     "a card cannot show it whole. It was not sent"): (
        "DRAFT_SUBJECT_TOO_LONG_DETAIL. Content: a subject past SUBJECT_MAX. "
        "The same answer on every channel."),
    ("apps/services/gateway/gateway/routes/email/automation/drafting.py",
     "This draft holds spaces that a card cannot show"): (
        "DRAFT_SPACING_DETAIL. Content. The same answer on every channel."),
    ("apps/services/gateway/gateway/routes/email/automation/held_card.py",
     "so one card cannot show all of it, and nothing was sent"): (
        "TEXT_TOO_LONG of the held card (§21). Content. The H-391 summary on "
        "WhatsApp never cuts a text either."),
    ("apps/services/gateway/gateway/routes/email/automation/held_card.py",
     "recipients, so one card cannot show them"): (
        "Content: more than MAX_ROWS recipients. The same answer on every "
        "channel."),
    ("apps/services/gateway/gateway/routes/email/automation/held_card.py",
     ", so the card cannot show it."): (
        "Content: an address that address_problem refuses. The same answer on "
        "every channel."),
    # ── Other refusals of content ──
    ("apps/services/gateway/gateway/app_requests.py",
     "The change holds a value that the card cannot show exactly"): (
        "Content: a 422 bad_args of an app request. The same answer on every "
        "channel."),
    ("apps/skills/skill-projects/skill_projects/writes.py",
     "not readable here, so the card cannot show it"): (
        "Content: a reminder that the member cannot read. The same answer on "
        "every channel."),
    ("apps/skills/skill-projects/skill_projects/writes.py",
     "the task is not in your lens, so the card cannot show it"): (
        "Content: a task outside the member's lens. The same answer on every "
        "channel."),
    # ── The edge of an outside agent (WS-55): no WhatsApp member reads these ──
    ("apps/services/agent_edge/agent_edge/tools.py",
     "ask a person to make the space in the Metorite app"): (
        "TEXT_SPACE_PINNED. An outside agent reads it through agent_edge, "
        "never a WhatsApp member."),
    ("apps/services/agent_edge/agent_edge/tools.py",
     "so a person must make this move in the Metorite app"): (
        "An agent never widens who sees a task. An outside agent reads it "
        "through agent_edge, never a WhatsApp member."),
    ("apps/services/agent_edge/agent_edge/tools.py",
     "the member's card cannot show exactly"): (
        "TEXT_REQUEST_UNSHOWABLE. Content of the request of an outside agent."),
    # ── The WhatsApp channel itself ──
    ("apps/services/gateway/gateway/routes/whatsapp_channel/acts.py",
     "could not hold that change for your Confirm"): (
        "REPLY_NOT_HELD: the phone has no current link row, so no act can wait "
        "for a Confirm. A fault of the link, not a missing card."),
    ("apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py",
     "This chat cannot show a confirmation card, so you cannot change data"): (
        "SCOPE_RULE: a WhatsApp run with the profile off (WAC-10a). A run "
        "with the profile reads SCOPE_RULE_NATIVE."),
    ("apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py",
     "You cannot change data from WhatsApp. When the member asks for a change"): (
        "_NO_CHANGE, inside SCOPE_RULE_NATIVE: a WhatsApp run with writes off "
        "for its org (WHATSAPP_ASSISTANT_WRITES, the owner's switch). With "
        "writes on, SCOPE_RULE_WRITES replaces the sentence."),
    ("apps/services/gateway/gateway/routes/whatsapp_channel/bot_run.py",
     "For any other change, tell the member to make it in the Metorite web"): (
        "SCOPE_RULE_WRITES, only with WHATSAPP_ASSISTANT_WRITES on: a change "
        "that whatsapp_acts cannot park. ALLOWED_ACTS, PROFILE_ACTS, an "
        "egress call (WAC-P4) and the email writes (H-391) park for the "
        "member's Confirm or code. D-WAC-4 keeps the rest of the Projects "
        "acts off WhatsApp."),
    ("packages/acb_skills/acb_skills/whatsapp_acts.py",
     "too long to show on WhatsApp"): (
        "EGRESS_TOO_LONG_TEXT (WAC-P4, review P1-1): the recipients and the "
        "files of one egress call do not fit the WhatsApp body, so nothing "
        "parks."),
    # ── The card gates ──
    ("apps/services/orchestrator/orchestrator/_maf_mcp.py",
     "this chat cannot show that card. Tell the member to run it"): (
        "NO_CARD_CHANNEL_TEXT: an MCP tool that can change data, in a run that "
        "refuses cards. WhatsApp keeps the H-299 refusal (agent_architecture.md "
        "item 33). An MCP call needs the live server session of its run, so it "
        "cannot park."),
    ("packages/acb_skills/acb_skills/ask_tools.py",
     "This chat cannot show questions"): (
        "NO_CARD_QUESTIONS: a question, not an approval. The model asks in "
        "text, and the member answers in the next message."),
    ("packages/acb_skills/acb_skills/ask_tools.py",
     "a card cannot show here, so nothing was done."): (
        "CARD_NOT_SHOWN, its outcome: a run with no person, or a card that "
        "WhatsApp cannot park. Only with WHATSAPP_ASSISTANT_WRITES on, an "
        "allowed act, a profile write and an email card park first. With "
        "writes off, a card on WhatsApp reads this."),
    ("packages/acb_skills/acb_skills/ask_tools.py",
     "Ask them to ask for it again in the Metorite web app"): (
        "CARD_NOT_SHOWN, its next step. The same runs as its outcome above."),
    ("packages/acb_skills/acb_skills/chat_constraints.py",
     "This chat cannot show a card for the member to approve"): (
        "DROP_NO_CARD: the drop of a chat limit needs the member's card. On "
        "WhatsApp the item stays, and nothing is lost."),
    ("packages/acb_skills/acb_skills/write_artifact.py",
     "This chat cannot show a card that waits for the member"): (
        "GENUI_NO_CARD_TEXT: a blocking UI card, not an approval. The model "
        "asks in text."),
    # ── Not a refusal ──
    ("packages/acb_skills/acb_skills/chat_parity.py",
     "The approval card in the chat reads its own request"): (
        "Not a refusal: the reason of a row of the chat parity table."),
}

#: The words of a refusal for want of a card, and of each text that sends a
#: member to an app for an approval or a change. The incident's own answer
#: matches twice.
_NO_CARD = re.compile(
    r"(cannot|can't|can not) (show|take)[^.]{0,30}(card|step|questions)"
    r"|card cannot show|no card channel"
    r"|(approv|confirm)\w*[^.]{0,60}\b(in|from|on) the (metorite )?(web )?(app|chat)\b"
    r"|\b(make|run|do|ask for|save or send|fix|send|finish|change)\b[^.]{0,40}\b(in|from) the "
    r"(metorite (web )?(app|chat)|web app|web chat|chat app|email app)",
    re.I)

#: The product trees that the scan reads.
_SCANNED = ("apps", "packages")
_SKIPPED_PARTS = ("/tests/", "/.venv/", "node_modules")


def _docstrings(tree: ast.AST) -> set[int]:
    return {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)}


def _no_card_texts() -> set[tuple[str, str]]:
    """Every string constant of the product code that says "no card here" or
    "go to the app", docstrings aside, as (file, text). A literal part of an
    f-string counts as its own constant."""
    found: set[tuple[str, str]] = set()
    for base in _SCANNED:
        for path in (_REPO / base).rglob("*.py"):
            rel = path.relative_to(_REPO).as_posix()
            if any(part in f"/{rel}" for part in _SKIPPED_PARTS):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            docs = _docstrings(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docs and _NO_CARD.search(node.value)):
                    found.add((rel, node.value))
    return found


def test_the_fence_reads_the_incidents_own_words() -> None:
    """The answer that the owner read on WhatsApp on 2026-10-18 matches. The
    first build of the fence missed it (fix round 1 of the branch)."""
    said = ("I couldn't send it myself: an outgoing mail needs your approval in "
            "the Metorite app, and I can't take that step from here.")
    assert _NO_CARD.search(said)
    assert _NO_CARD.search("Approve it in the Metorite app.")
    assert _NO_CARD.search("Send it from the Email app.")
    assert _NO_CARD.search("Tell the member to make this change in the Metorite web app.")
    assert not _NO_CARD.search("Tap Confirm to make this change, or Cancel.")
    assert not _NO_CARD.search("Reply YES 7K3Q to send it.")


def test_every_no_card_answer_names_its_whatsapp_behaviour() -> None:
    """A new "no card here" or "go to the app" text fails until
    :data:`NO_CARD_TEXTS` names what a WhatsApp member gets. A stale entry
    fails too."""
    found = _no_card_texts()

    def names(rel: str, text: str) -> list[tuple[str, str]]:
        return [k for k in NO_CARD_TEXTS if k[0] == rel and k[1] in text]

    unnamed = sorted((rel, text[:80]) for rel, text in found if not names(rel, text))
    assert not unnamed, f"name the WhatsApp behaviour of: {unnamed}"
    used = {k for rel, text in found for k in names(rel, text)}
    stale = sorted(set(NO_CARD_TEXTS) - used)
    assert not stale, f"stale entries: {stale}"


def test_each_entry_is_ascii_and_says_something() -> None:
    """A key holds no character that an editor can change unseen, and each
    value says what a WhatsApp member gets."""
    for (rel, part), behaviour in NO_CARD_TEXTS.items():
        assert part.isascii() and part.strip(), (rel, part)
        assert (_REPO / rel).is_file(), rel
        assert len(behaviour.split()) >= 5, (rel, part)
