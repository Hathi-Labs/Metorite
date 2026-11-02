"""PZ-33a: the answer checks of a chat summary and its flag (WS-63, D142).

Spec: ``project-docs/specs/personalization.md`` §15.3 (D-PZ-28), §15.8 and
§15.9 item 7. Module: ``gateway/assistant_summaries.py``. No database.

What this proves:

- item 7 (the pure part): a pasted mail of more than 1,500 characters, a tool
  event and the brief row never reach the prompt, and never ground a line.
  So a line of the answer that copies an address, a long number or a
  sensitive word from them drops. A sensitive line that the member typed
  stays;
- the caps: 3 sentences and 400 characters of text, 3 follow-ups of 120
  characters each, and an answer that does not parse gives no summary;
- ``chat_summary_for(org)``: off for an org off the list, off with the
  profile switch off, on for ``*``, and it reads the settings at each call;
- the flag name: a platform name of the env guard, a reload name, and a name
  that the staging tool may write, with the comment that it spends credits.

Review round 1 (2026-10-31) adds:

- the joined text: a number, an address in words or a run of digit words
  that crosses the end of a line drops the whole answer (P1-a);
- a sensitive line stays only when the member typed each sensitive term, and
  a line with a form that ``grounded`` cannot match drops (P1-b);
- a card answer is not a typed row (P1-c), with a leading space too;
- review round 2: a domain in words behind the end of a sentence is flagged
  (``_SPOKEN_DOT``).

PZ-33b replaces the number joints of rounds 1 to 3 with ONE strict digit
rule (``digit_runs``, ``personalization.md`` §15.9, "Open"). Each character
that is not a real letter joins two digits, and a run of 6 digits or more
must be a run of the member's typed words. Digits of each script count,
after NFKD. A modifier letter and a click letter read as marks.
Every long-number probe of the three PZ-33a reviews is a drop here. A typed
number in any marks, a version, a ratio, a short price and a 5-digit room
stay. A time range, a date and a price of 6 digits or more that the member
did not type drop: the agent accepted that cost.

Mutations, each red here: drop the sensitive-line rule of ``_keep``; drop
the sensitive-term rule; drop the joined-text check (``_passes``); put the
punctuation joint of round 3 back in place of ``digit_runs``; count a card
answer as typed; let ``member_texts`` skip ``typed_text``; let
``prompt_rows`` read ``tool_events``; drop the brief-row check; drop
``enabled_for`` from ``chat_summary_for``.
"""

# ruff: noqa: RUF001 - the probes hold digits and marks of other scripts on purpose
from __future__ import annotations

import json
import random
import re
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import assistant_profile as ap
from acb_common import env_guard, get_settings
from acb_common.settings import install_settings
from gateway import assistant_summaries as summaries

ROOT = Path(__file__).resolve().parents[2]
STAGING_TOOL = ROOT / "deploy" / "hostinger" / "staging" / "acb-staging-flag"
STAGING_README = ROOT / "deploy" / "hostinger" / "staging" / "README.md"

FLAG = "ASSISTANT_CHAT_SUMMARY_ORGS"
MEMBER = "asha@pz33.test"
ORG = "0a0a0a0a-0000-4000-8000-000000003333"
OTHER_ORG = "0b0b0b0b-0000-4000-8000-000000003333"

#: A long number, an address and a sensitive word of a pasted mail. None of
#: them is in the member's own typed words.
PASTED_PHONE = "+91 98450 12345"
PASTED_MAIL = "priya.rao@acme-pay.example"
PASTED_SALARY = "my salary is 42 lakh"


def _pasted_mail() -> str:
    """A pasted mail of more than 1,500 characters, with no header line. So
    only the length rule of ``typed_text`` keeps it out."""
    body = (
        f"Hi team, call me on {PASTED_PHONE} or write to {PASTED_MAIL}. "
        f"Between us, {PASTED_SALARY}. "
    )
    text = body + ("The quarterly numbers look fine and the plan holds. " * 40)
    assert len(text) > ap.TYPED_MAX
    return text


def _row(id_: str, role: str, content: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {"id": id_, "role": role, "content": content,
                              "author_kind": None, "author_email": None,
                              "tool_events": [], "custom_events": [], "reasoning": None}
    row.update(extra)
    return row


def _chat() -> list[dict[str, object]]:
    """A solo chat in time order: the brief, two typed rows, a paste, a tool
    event, and a reply of Mira."""
    return [
        _row("brief-2026-10-31", "assistant",
             "Good morning. Invoice 7788990011 waits for you.",
             author_kind="agent", author_email="orchestrator"),
        _row("m1", "user", "Help me plan the Q3 pricing deck for Priya.",
             author_kind="human", author_email=MEMBER),
        _row("a1", "assistant", "Sure. Three tiers, and the old price stays.",
             author_kind="agent", author_email="orchestrator",
             tool_events=[{"name": "read_mail",
                           "result": "Account 556677889900 of the client"}],
             reasoning="The bank code is 4455667788",
             custom_events=[{"name": "generative_ui", "value": {"pin": "99887766"}}]),
        _row("m2", "user", _pasted_mail(), author_kind="human", author_email=MEMBER),
        _row("m3", "user", "Keep the old price for current customers. "
             "The diabetes check is on Friday, remind me.",
             author_kind="human", author_email=MEMBER),
        _row("s1", "system", "Mira joined the chat. Code 1234567890."),
    ]


# ── Item 7: what reaches the prompt ─────────────────────────────────────────


def test_a_pasted_mail_over_the_cap_reaches_neither_the_prompt_nor_the_words() -> None:
    rows = _chat()
    texts = summaries.member_texts(rows, MEMBER)
    words = summaries.member_words(rows, MEMBER)
    prompt = " ".join(summaries.prompt_rows(rows, MEMBER))
    assert len(texts) == 2, texts
    for leak in (PASTED_PHONE, PASTED_MAIL, PASTED_SALARY, "quarterly numbers"):
        assert leak not in words
        assert leak not in prompt


def test_a_pasted_mail_with_a_header_line_stays_out_too() -> None:
    pasted = "From: priya@acme.example\nSubject: rates\n\nCall 9845012345."
    rows = [_row("m1", "user", pasted, author_email=MEMBER)]
    assert summaries.member_texts(rows, MEMBER) == []
    assert summaries.prompt_rows(rows, MEMBER) == []


def test_a_tool_event_reasoning_and_a_custom_event_stay_out_of_the_prompt() -> None:
    prompt = summaries.prompt_rows(_chat(), MEMBER)
    joined = json.dumps(prompt)
    for leak in ("556677889900", "4455667788", "99887766", "read_mail"):
        assert leak not in joined
    # The owner, 2026-11-02: the prompt holds the member's own words only.
    assert "Sure. Three tiers" not in joined, "a reply of Mira stays out"
    assert prompt == summaries.member_texts(_chat(), MEMBER)


def test_the_brief_row_and_a_system_row_stay_out_of_the_prompt() -> None:
    joined = json.dumps(summaries.prompt_rows(_chat(), MEMBER))
    assert "7788990011" not in joined
    assert "1234567890" not in joined


def test_the_brief_row_pattern_is_the_id_that_the_brief_route_makes() -> None:
    from gateway.routes.chat_brief import brief_message_id

    made = brief_message_id(date(2026, 10, 31))
    assert summaries.BRIEF_ROW.match(made), made
    assert summaries.is_brief_row({"id": made})
    assert not summaries.is_brief_row({"id": "m-brief-2026-10-31"})


def test_a_user_row_of_another_author_is_not_the_members() -> None:
    rows = [_row("m1", "user", "Plan the offsite.", author_email="bob@pz33.test"),
            _row("m2", "user", "Plan the offsite.", author_kind="agent")]
    assert summaries.member_texts(rows, MEMBER) == []
    assert summaries.prompt_rows(rows, MEMBER) == []
    # A legacy row names no author, and a solo chat has one member.
    legacy = [_row("m3", "user", "Plan the offsite.")]
    assert summaries.member_texts(legacy, MEMBER) == ["Plan the offsite."]


def test_pz33b_no_row_of_an_agent_reaches_the_prompt() -> None:
    """A reply of Mira can restate a mail, a CRM record or a tool answer, and
    the text of a mentioned agent is a stranger's too. So no agent row goes
    in, and a legacy row with no author neither (the owner, 2026-11-02)."""
    rows = [_row("a1", "assistant", "CRM agent: Acme paid 4.2 lakh",
                 author_kind="agent", author_email="crm-assistant"),
            _row("a2", "assistant", "Sure, I saved it.",
                 author_kind="agent", author_email="orchestrator"),
            _row("a3", "assistant", "An older reply."),
            _row("m1", "user", "Book the review.", author_email=MEMBER)]
    assert summaries.prompt_rows(rows, MEMBER) == ["Book the review."]


def test_the_prompt_keeps_the_newest_rows_under_the_cap() -> None:
    rows = [_row(f"m{i}", "user", f"{i:03d}" + "x" * 97, author_email=MEMBER)
            for i in range(200)]
    kept = summaries.prompt_rows(rows, MEMBER)
    assert sum(len(w) for w in kept) == summaries.INPUT_MAX
    assert kept[-1].startswith("199"), "the newest row must go in"
    assert kept[0].startswith("080"), "the oldest rows drop first"
    short = summaries.prompt_rows(rows, MEMBER, limit=150)
    assert [w[:3] for w in short] == ["198", "199"]
    assert len(short[0]) == 50, "the oldest row that goes in loses its end"


# ── Item 7: the answer checks drop what the member did not type ─────────────


def _answer(text: str, follow_ups: list[str] | None = None) -> str:
    return json.dumps({"text": text, "follow_ups": follow_ups or []})


def test_a_line_that_copies_the_pasted_mail_drops() -> None:
    words = summaries.member_words(_chat(), MEMBER)
    raw = _answer(
        f"Planned the Q3 pricing deck. Priya asked for a call on {PASTED_PHONE}. "
        f"Mail goes to {PASTED_MAIL}.",
        [f"Note that {PASTED_SALARY}", "Send the deck to Priya by Friday"])
    out = summaries.check_answer(raw, words)
    assert out is not None
    assert out.text == "Planned the Q3 pricing deck."
    assert out.follow_ups == ("Send the deck to Priya by Friday",)


def test_a_line_that_copies_a_tool_event_or_the_brief_drops() -> None:
    words = summaries.member_words(_chat(), MEMBER)
    raw = _answer("Read account 556677889900. Invoice 7788990011 waits. "
                  "Kept the old price for current customers.")
    out = summaries.check_answer(raw, words)
    assert out is not None
    assert out.text == "Kept the old price for current customers."


def test_a_sensitive_line_drops_unless_the_member_typed_it() -> None:
    words = summaries.member_words(_chat(), MEMBER)
    typed = summaries.check_answer(_answer("The diabetes check is on Friday."), words)
    assert typed is not None and typed.text == "The diabetes check is on Friday."
    untyped = summaries.check_answer(
        _answer("They seem depressed about the deck. Planned the deck."), words)
    assert untyped is not None and untyped.text == "Planned the deck."
    assert summaries.check_answer(_answer("They seem depressed."), words) is None


def test_no_typed_words_ground_no_sensitive_line() -> None:
    raw = _answer("Spoke about the home loan. Planned the deck.",
                  ["Call 9845012345 at noon"])
    out = summaries.check_answer(raw, "")
    assert out is not None
    assert (out.text, out.follow_ups) == ("Planned the deck.", ())


# ── Review round 1, P1-a: a form that crosses the end of a line ─────────────

#: The typed words of the probes of the verifier.
PROBE_ROWS = [
    _row("m1", "user", "Help me plan the Q3 pricing deck for Priya. "
         "Keep the old price for current customers.", author_email=MEMBER),
]


def _stored_text_is_clean(out: summaries.Answer | None, words: str) -> None:
    """The stored text, as one string, has no form that its words lack."""
    if out is None:
        return
    whole = " ".join([out.text, *out.follow_ups])
    assert not ap.needs_confirm(whole) or ap.grounded(whole, words), whole


@pytest.mark.parametrize("text", [
    "Call 98450\n12345 waits.",
    "Call Priya on 98450. 12345 is the code.",
    "Write to priya. At acme dot com the deck waits.",
    "Planned the deck: nine eight four. Five oh one.",
], ids=["over a line break", "over a full stop", "an address in words",
        "digit words"])
def test_a_form_across_the_end_of_a_line_writes_nothing(text: str) -> None:
    words = summaries.member_words(PROBE_ROWS, MEMBER)
    for line in summaries.sentences(text):
        assert not ap.needs_confirm(line), "each line alone must pass"
    assert summaries.check_answer(_answer(text), words) is None


def test_a_form_across_the_follow_ups_or_into_them_writes_nothing() -> None:
    words = summaries.member_words(PROBE_ROWS, MEMBER)
    assert summaries.check_answer(
        _answer("Planned the deck.", ["Call 98450", "12345 later"]), words) is None
    assert summaries.check_answer(
        _answer("Call Priya on 98450.", ["12345 is the code"]), words) is None


def test_a_grounded_flagged_line_that_fails_the_whole_text_drops_alone() -> None:
    """The joined text fails, so each flagged line drops, and the rest stays.
    The stored text then passes the check as one string."""
    rows = [_row("m1", "user", "send the deck to priya@acme.example", author_email=MEMBER)]
    words = summaries.member_words(rows, MEMBER)
    alone = summaries.check_answer(_answer("Send the deck to priya@acme.example."), words)
    assert alone is not None and alone.text == "Send the deck to priya@acme.example."
    out = summaries.check_answer(_answer(
        "Planned the quarterly pricing review with many new ideas. "
        "Send the deck to priya@acme.example."), words)
    assert out is not None
    assert out.text == "Planned the quarterly pricing review with many new ideas."
    _stored_text_is_clean(out, words)


# ── Review round 1, P1-b: the sensitive term and the forms with no token ────


def test_a_sensitive_word_the_member_never_typed_drops_its_line() -> None:
    rows = [_row("m1", "user", "Draft the reply to Ravi about the leave request "
                 "for next week and copy Priya.", author_email=MEMBER)]
    words = summaries.member_words(rows, MEMBER)
    line = ("Draft the reply to Ravi about the leave request for next week and "
            "his surgery, and copy Priya.")
    assert ap.grounded(line, words), "80% of the words alone would keep it"
    assert summaries.check_answer(_answer(line), words) is None


def test_depression_and_a_third_partys_salary_drop() -> None:
    words = summaries.member_words(PROBE_ROWS, MEMBER)
    assert summaries.check_answer(_answer(
        "Keep the old price for current customers in the pricing deck for "
        "Priya despite depression."), words) is None
    offer = [_row("m1", "user", "Send the revised offer letter to Kiran today and "
                  "copy the hiring manager on the thread.", author_email=MEMBER)]
    assert summaries.check_answer(_answer(
        "Send the revised offer letter to Kiran today with the new salary and "
        "copy the hiring manager on the thread."),
        summaries.member_words(offer, MEMBER)) is None


def test_a_domain_of_another_script_drops() -> None:
    rows = [_row("m1", "user", "write to them about the deck", author_email=MEMBER)]
    words = summaries.member_words(rows, MEMBER)
    assert summaries.check_answer(_answer("Write to пример.рф about the deck."), words) is None


@pytest.mark.parametrize("line", [
    "Call the vendor on 98450\u200b12345 about the deck.",
    "Call the vendor on 98450/12345 about the deck.",
], ids=["a zero-width space", "a slash"])
def test_a_number_split_by_a_hidden_mark_drops(line: str) -> None:
    rows = [_row("m1", "user", "call the vendor about the deck", author_email=MEMBER)]
    words = summaries.member_words(rows, MEMBER)
    assert summaries.check_answer(_answer(line), words) is None


def test_the_sensitive_terms_and_the_forms_with_no_token() -> None:
    assert ap.sensitive_terms("Home-Loan, then my   health") == {"home loan", "my health"}
    assert ap.sensitive_terms("transport and planning") == frozenset()
    for value in ("Write to пример.рф", "priya at acme dot com", "mail priya (at) acme",
                  "nine eight four five oh one", "acme . io"):
        assert ap.confirm_form_without_token(value), value
    for value in ("mail priya@acme.com", "call 98450 12345", "see https://acme.io/x",
                  "acme-pay.io", "plain words", ""):
        assert not ap.confirm_form_without_token(value), value


# ── Review round 2: number joints and a domain behind a sentence end ───────

#: Each answer stored a number or a domain that the member never typed
#: before round 2 (re-verify of 752d076a6).
ROUND_2_DROPS = [
    ("Call 98450 – 12345 today.", []),
    ("Call 98450–12345 today.", []),
    ("Call 98450 — 12345 today.", []),
    ("Call 98450, 12345 today.", []),
    ("Call 98450; 12345 today.", []),
    ("Call 98450: 12345 today.", []),
    ("Call 98450? 12345 waits.", []),
    ("Call 98450! 12345 today.", []),
    ("Call 98450!12345 today.", []),
    ("Call 98450+12345 today.", []),
    ("Call 98450~12345 today.", []),
    ("Call 98450*12345 today.", []),
    ("Call 984, 501, 2345 today.", []),
    ("Call Priya on 98450 –", ["12345 later"]),
    ("Call Priya on 98450,", ["12345 later"]),
    ("Call 98450! 12345 waits.", []),
    ("See acme. Example dot com has it.", []),
    ("See acme! Example dot com has it.", []),
]


@pytest.mark.parametrize(("text", "follow"), ROUND_2_DROPS,
                         ids=[f"{i}:{t[:24]}" for i, (t, _f) in enumerate(ROUND_2_DROPS)])
def test_round_2_a_joined_number_or_domain_writes_nothing(text: str, follow: list[str]) -> None:
    words = summaries.member_words(PROBE_ROWS, MEMBER)
    assert summaries.check_answer(_answer(text, follow), words) is None


@pytest.mark.parametrize(("text", "typed"), [
    ("Planned the deck. Send it to Priya.", None),
    ("Meet at 10:30 for the Q3, 2026 plan.", None),
    ("Call 98450–12345 today.", "call 98450-12345 today"),
    ("Call 98450/12345 today.", "call 98450/12345 today"),
], ids=["two sentences", "a time and a quarter", "a typed number with a dash",
        "a typed number with a slash"])
def test_round_2_keeps_what_is_no_new_number(text: str, typed: str | None) -> None:
    rows = PROBE_ROWS if typed is None else [_row("m1", "user", typed, author_email=MEMBER)]
    out = summaries.check_answer(_answer(text), summaries.member_words(rows, MEMBER))
    assert out is not None and out.text == text


def test_round_2_two_typed_rows_never_join_into_one_number() -> None:
    rows = [_row("m1", "user", "Room 98450 is booked.", author_email=MEMBER),
            _row("m2", "user", "Ticket 12345 is open.", author_email=MEMBER)]
    words = summaries.member_words(rows, MEMBER)
    assert summaries.check_answer(_answer("Call 98450–12345 today."), words) is None


# ── PZ-33b: the strict digit rule ──────────────────────────────────────────

#: Two typed rows that each hold one half of a number (review rounds 1 to 3).
HALVES_APART = [_row("m1", "user", "Room 98450 is booked.", author_email=MEMBER),
                _row("m2", "user", "Ticket 12345 is open.", author_email=MEMBER)]
#: Both halves in one typed row, with words between them.
HALVES_ONE_ROW = [_row("m1", "user", "Room 98450 is booked and ticket 12345 is open.",
                       author_email=MEMBER)]
#: The two halves at the edges of two typed rows (review round 3).
HALVES_AT_THE_EDGES = [_row("m1", "user", "Call 98450", author_email=MEMBER),
                       _row("m2", "user", "12345 today", author_email=MEMBER)]

#: Each long-number probe of the three PZ-33a reviews: the answer text, the
#: follow-ups and the typed rows. Each stored a number that the member did
#: not type, before PZ-33b or in a round before it.
STRICT_DROPS: list[tuple[str, str, list[str], list[dict[str, object]] | None]] = [
    # Round 1 and its verifier.
    ("a line break", "Call 98450\n12345 waits.", [], None),
    ("a full stop", "Call Priya on 98450. 12345 is the code.", [], None),
    ("a bang and a space", "Call 98450! 12345 waits.", [], None),
    ("halves in two rows", "Call 98450\n12345 waits.", [], HALVES_APART),
    ("halves in one row", "Call 98450\n12345 waits.", [], HALVES_ONE_ROW),
    ("Arabic-Indic digits", "Call ٩٨٤٥٠١٢٣٤٥ today.", [], None),
    ("Arabic-Indic, split", "Call ٩٨٤٥٠\n١٢٣٤٥ waits.", [], None),
    ("fullwidth digits, split", "Call ９８４５０\n１２３４５ waits.", [], None),
    ("Devanagari digits, split", "Call ९८४५०\n१२३४५ waits.", [], None),
    ("a spaced hyphen", "Call 98450 - 12345 today.", [], None),
    ("a zero-width space", "Call the vendor on 98450​12345 about the deck.", [], None),
    ("a slash", "Call the vendor on 98450/12345 about the deck.", [], None),
    # Round 2.
    ("a plain phone", "Call 98450 12345 today.", [], None),
    ("three parts with commas", "Call 984, 501, 2345 today.", [], None),
    ("an en dash", "Call 98450–12345 today.", [], None),
    ("an en dash over the follow-up", "Call Priya on 98450 –", ["12345 later"], None),
    ("a comma over the follow-up", "Call Priya on 98450,", ["12345 later"], None),
    # Round 3.
    ("a minus sign", "Call 98450−12345 today.", [], None),
    ("a figure dash", "Call 98450‒12345 today.", [], None),
    ("a non-breaking hyphen", "Call 98450‑12345 today.", [], None),
    ("a dot leader", "Call 98450…12345 today.", [], None),
    ("a spaced minus sign", "Call 98450 − 12345 today.", [], None),
    ("a no-break space", "Call 98450 12345 today.", [], None),
    ("an ideographic comma", "Call 98450、12345 today.", [], None),
    ("spaces only", "Call 98450 12345 6 today.", [], None),
    ("four bangs", "Call 98450!!!!12345 today.", [], None),
    ("four commas", "Call 98450,,,,12345 today.", [], None),
    ("spaced commas", "Call 98450 , , 12345 today.", [], None),
    ("spaced bangs", "Call 98450 ! ! 12345 today.", [], None),
    ("spaced underscores", "Call 98450 _ _ 12345 today.", [], None),
    ("a slash over the follow-up", "Call Priya on 98450/", ["12345 later"], None),
    ("four bangs over the follow-up", "Call Priya on 98450!!!!", ["12345 later"], None),
    ("spaced commas over the follow-up", "Call Priya on 98450 , ,", ["12345 later"], None),
    ("halves apart, a comma", "Call 98450, 12345 today.", [], HALVES_APART),
    ("halves apart, a space", "Call 98450 12345 today.", [], HALVES_APART),
    ("halves at the edges, an en dash", "Call 98450–12345 today.", [], HALVES_AT_THE_EDGES),
    ("halves at the edges, a comma", "Call 98450, 12345 today.", [], HALVES_AT_THE_EDGES),
    ("superscript digits, a bang", "Call ⁹⁸⁴⁵⁰!¹²³⁴⁵ today.", [], None),
    ("superscript digits, a comma", "Call ⁹⁸⁴⁵⁰, ¹²³⁴⁵ today.", [], None),
    ("circled digits, a bang", "Call ⑨⑧④⑤⓪!①②③④⑤ today.", [], None),
    ("fullwidth digits, a fullwidth bang", "Call ９８４５０！１２３４５ today.", [], None),
    ("superscript digits", "Call ⁹⁸⁴⁵⁰¹²³⁴⁵ today.", [], None),
    # Letters that read as marks (the review of PZ-33b).
    ("a modifier colon", "Call 98450\u02d012345 today.", [], None),
    ("a spaced modifier colon", "Call 98450 \u02d0 12345 today.", [], None),
    ("a prolonged sound mark", "Call 98450\u30fc12345 today.", [], None),
    ("a halfwidth prolonged sound mark", "Call 98450\uff7012345 today.", [], None),
    ("a tatweel", "Call 98450\u064012345 today.", [], None),
    ("a modifier h", "Call 98450\u02b012345 today.", [], None),
    ("a dental click", "Call 98450\u01c012345 today.", [], None),
    ("an alveolar click", "Call 98450\u01c312345 today.", [], None),
]


@pytest.mark.parametrize(("text", "follow", "rows"), [p[1:] for p in STRICT_DROPS],
                         ids=[p[0] for p in STRICT_DROPS])
def test_pz33b_each_long_number_probe_of_the_reviews_drops(
        text: str, follow: list[str], rows: list[dict[str, object]] | None) -> None:
    words = summaries.member_words(PROBE_ROWS if rows is None else rows, MEMBER)
    assert summaries.check_answer(_answer(text, follow), words) is None


@pytest.mark.parametrize("text", [
    "Call 98450!!!!12345 today.", "Call 98450 , , 12345 today.",
    "Call 98450 _ _ 12345 today.", "Call 98450–12345 today.",
    "Call ９８４５０／１２３４５ today.", "Call ٩٨٤٥٠ ١٢٣٤٥ today.",
    "Call 98450 12345 today.",
])
@pytest.mark.parametrize("typed", ["call 98450-12345 today", "call 98450 12345 today",
                                   "call 98450/12345 today"])
def test_pz33b_a_typed_number_stays_in_any_marks(text: str, typed: str) -> None:
    rows = [_row("m1", "user", typed, author_email=MEMBER)]
    out = summaries.check_answer(_answer(text), summaries.member_words(rows, MEMBER))
    assert out is not None and out.text == text


@pytest.mark.parametrize("text", [
    "Planned the deck. Send it to Priya.",
    "Ship the deck v2.3.1 to Priya.",
    "Use a 3:2 ratio for the deck.",
    "Keep the old price of ₹1,200 for current customers.",
    "Room 40512 is booked for the review.",
    "Meet at 10:30 for the Q3, 2026 plan.",
    "The team won 3-2, 4-1 this week.",
    "Rooms 40512 and 40513 are booked.",
], ids=["two sentences", "a version", "a ratio", "a price of 4 digits",
        "a room of 5 digits", "a time and a quarter", "two scores",
        "two rooms with a word between"])
def test_pz33b_a_short_run_stays(text: str) -> None:
    out = summaries.check_answer(_answer(text), summaries.member_words(PROBE_ROWS, MEMBER))
    assert out is not None and out.text == text


@pytest.mark.parametrize(("text", "typed"), [
    ("Meet Priya 10:30–11:00 on the deck.", "meet priya 10:30–11:00 on the deck"),
    ("The deck is due 31/10/2026 for Priya.", "the deck is due 31/10/2026 for priya"),
    ("Keep the old price of ₹1,20,000 for current customers.",
     "keep the old price of rs 1,20,000 for current customers"),
], ids=["a time range", "a date", "a lakh price"])
def test_pz33b_the_accepted_cost_a_long_run_stays_only_when_typed(text: str, typed: str) -> None:
    """A time range, a date and a price of 6 digits or more drop when the
    member did not type them (the cost that the agent accepted, §15.9), and
    stay when the member did."""
    untyped = summaries.member_words(PROBE_ROWS, MEMBER)
    assert summaries.check_answer(_answer(text), untyped) is None
    rows = [_row("m1", "user", typed, author_email=MEMBER)]
    out = summaries.check_answer(_answer(text), summaries.member_words(rows, MEMBER))
    assert out is not None and out.text == text


def test_pz33b_a_part_of_a_longer_typed_run_does_not_count() -> None:
    """The digit strings must be equal. The member typed the country code,
    so a number without it is not a run of the typed words."""
    rows = [_row("m1", "user", "call +91 98450 12345 today", author_email=MEMBER)]
    words = summaries.member_words(rows, MEMBER)
    assert summaries.check_answer(_answer("Call +91 98450 12345 today."), words) is not None
    assert summaries.check_answer(_answer("Call 98450 12345 today."), words) is None


def test_pz33b_an_address_keeps_its_shape_for_the_grounding() -> None:
    """The joint of an address ("@") stays when a number becomes one run, so
    a typed domain never grounds an address of the same digits."""
    rows = [_row("m1", "user", "write to a1.2b.example about the deck", author_email=MEMBER)]
    words = summaries.member_words(rows, MEMBER)
    assert summaries.check_answer(_answer("Write to a1.2b.example about the deck."),
                                  words) is not None
    assert summaries.check_answer(_answer("Write to a1@2b.example about the deck."),
                                  words) is None


def test_pz33b_the_digit_runs() -> None:
    assert summaries.digit_runs("98450!!!!12345") == ["9845012345"]
    assert summaries.digit_runs("98450 , , 12345 and 77") == ["9845012345", "77"]
    assert summaries.digit_runs("Room 12, floor 3") == ["12", "3"]
    assert summaries.digit_runs("٩٨٤ ९८४ ９８４ ⁹⁸⁴ ⑨⑧④") == ["984984984984984"]
    assert summaries.digit_runs("v2.3.1 then 10:30") == ["231", "1030"]
    assert summaries.digit_runs("98450\u02d012345") == ["9845012345"]
    assert summaries.digit_runs("98450\u02b012345") == ["9845012345"]
    assert summaries.digit_runs("98450\u01c012345") == ["9845012345"]
    assert summaries.digit_runs("98450x12345") == ["98450", "12345"]
    assert summaries.digit_runs("") == [] and summaries.digit_runs(None) == []


@pytest.mark.xfail(strict=True, reason=(
    "a real letter ends a run. The agent made that call, and the owner may "
    "overrule it (§15.9). A number split by a real letter is two runs"))
@pytest.mark.parametrize("text", ["Call 98450x12345 today.", "Call 98450 x 12345 today."],
                         ids=["an x", "a spaced x"])
def test_pz33b_the_boundary_a_real_letter_splits_a_number(text: str) -> None:
    """Two probes of review round 3 put a real letter between the digit
    groups. The rule reads a real letter as the end of a run, so these stay
    today. A letter that reads as a mark joins (``STRICT_DROPS``). This test
    records the boundary: it turns red when a change closes it."""
    words = summaries.member_words(PROBE_ROWS, MEMBER)
    assert summaries.check_answer(_answer(text), words) is None


# ── PZ-33b: the bound on the shared worker ─────────────────────────────────


def test_pz33b_one_org_holds_the_shared_worker_for_a_bounded_time() -> None:
    """The worker runs the orgs in turn and one job at a time, so the summary
    jobs of one org must not start the brief of another org late."""
    from gateway import chat_summary_job
    from gateway.jobs import worker

    assert summaries.SUMMARY_SEED_MAX == 5
    assert chat_summary_job.CALL_TIMEOUT_S == 30
    assert summaries.SUMMARY_SEED_MAX * chat_summary_job.CALL_TIMEOUT_S <= 150
    assert chat_summary_job.CALL_TIMEOUT_S < worker.HANDLER_TIMEOUT


# ── PZ-33b: the member's switch and the dedupe key ─────────────────────────


def test_pz33b_the_switch_reads_on_unless_the_member_stored_false(monkeypatch) -> None:
    """PZ-35 declares ``apps.chat.summaries`` later. Before it, a stored
    false turns the switch off, and a missing value or a wrong type reads
    on. After it, the profile's own reader and default decide."""
    assert "chat" not in ap.APP_PREFERENCES or "summaries" in ap.APP_PREFERENCES["chat"]
    for stored, on in ((None, True), ({}, True), ({"summaries": True}, True),
                       ({"summaries": False}, False), ({"summaries": "no"}, True),
                       ('{"summaries": false}', False), ("not json", True), ([1], True)):
        assert summaries.switch_on(stored) is on, stored
    monkeypatch.setitem(ap.APP_PREFERENCES, "chat", {"summaries": True})
    assert summaries.switch_on({"summaries": False}) is False
    assert summaries.switch_on(None) is True
    assert summaries.switch_on({"summaries": "no"}) is True


def test_pz33b_the_dedupe_key_names_the_chat_and_its_newest_row() -> None:
    from gateway.jobs.queue import DEDUPE_CHARS

    assert summaries.dedupe_key("chat-1", "a2") == "summary:chat-1:a2"
    long_key = summaries.dedupe_key("c" * 150, "m" * 100)
    assert len(long_key) <= DEDUPE_CHARS and long_key.startswith("summary:")
    assert long_key == summaries.dedupe_key("c" * 150, "m" * 100)
    assert long_key != summaries.dedupe_key("c" * 150, "m" * 99 + "n")


# ── Review round 1, P1-c: a card answer is not a typed row ──────────────────


def test_pz33b_a_card_answer_is_no_new_turn() -> None:
    """The prompt holds the member's typed words only, and a card answer is
    none of them (the owner, 2026-11-02). So it cannot count as new: a call
    on it would see nothing new."""
    from gateway.chat_recovery import compose_card_answer

    card = _row("m9", "user", compose_card_answer("Send the deck to Priya?", "APPROVE"),
                author_email=MEMBER)
    assert summaries.member_texts([card], MEMBER) == []
    assert summaries.member_words([card], MEMBER) == ""
    assert summaries.prompt_rows([card], MEMBER) == []
    assert summaries.member_texts([_row("m6", "user", "Book it.", author_email=MEMBER)], MEMBER)


def test_a_card_answer_is_not_a_typed_row() -> None:
    from gateway.chat_recovery import compose_card_answer

    card = compose_card_answer(
        "Send the reply to ravi.k@vendor.example about his diagnosis report?", "APPROVE")
    rows = [
        _row("m1", "user", "Help me answer Ravi.", author_kind="human", author_email=MEMBER),
        _row("m2", "user", card, author_kind="human", author_email=MEMBER),
        _row("m3", "user", "My answer to your last question: yes", author_email=MEMBER),
    ]
    assert summaries.member_texts(rows, MEMBER) == ["Help me answer Ravi."]
    # A leading space does not hide a card answer (review round 2).
    spaced = [rows[0], _row("m4", "user", "  " + card, author_email=MEMBER)]
    assert summaries.member_texts(spaced, MEMBER) == ["Help me answer Ravi."]
    assert all("ravi.k" not in w for w in summaries.prompt_rows(rows, MEMBER))
    words = summaries.member_words(rows, MEMBER)
    assert summaries.check_answer(_answer(
        "Sent the reply to ravi.k@vendor.example about his diagnosis report."),
        words) is None


# ── The caps ────────────────────────────────────────────────────────────────


def test_the_text_keeps_three_sentences() -> None:
    out = summaries.check_answer(_answer("One. Two. Three. Four. Five."), "")
    assert out is not None and out.text == "One. Two. Three."


def test_the_text_keeps_four_hundred_characters_by_whole_sentences() -> None:
    first, second = "A" * 250 + ".", "B" * 200 + "."
    out = summaries.check_answer(_answer(f"{first} {second}"), "")
    assert out is not None and out.text == first
    assert summaries.check_answer(_answer("C" * 401 + "."), "") is None
    out = summaries.check_answer(_answer("C" * 401 + ".", ["Send the deck"]), "")
    assert out is not None and (out.text, out.follow_ups) == ("", ("Send the deck",))


def test_the_follow_ups_keep_three_of_one_hundred_and_twenty_characters() -> None:
    items = ["Send the deck", "x" * 121, "Book the room", "Call the team", "Fifth"]
    out = summaries.check_answer(_answer("Planned the deck.", items), "")
    assert out is not None
    assert out.follow_ups == ("Send the deck", "Book the room")
    assert all(len(f) <= summaries.FOLLOW_UP_MAX for f in out.follow_ups)


@pytest.mark.parametrize("mark", ["\u200b", "\u200d", "\ufeff", "\u00ad", "\u2060"])
def test_pz33b_a_format_character_cannot_hide_a_sentence_from_the_cap(mark: str) -> None:
    """A zero-width space after each full stop hid the sentence ends from the
    split, and ``Answer`` then raised on the same text. It must keep the
    cap, and never raise."""
    out = summaries.check_answer(_answer(f"A.{mark} B.{mark} C.{mark} D."), "")
    assert out is not None and out.text == "A. B. C."


def test_pz33b_check_answer_never_raises_and_keeps_the_caps() -> None:
    """Any text of the model gives None or an answer inside the caps."""
    rng = random.Random(7)
    parts = ["A", "deck", ".", "!", "?", " ", "\n", "\t", "\x0b", "\x1c", "\u2028", "\u0085",
             "\u00a0", "\u200b", "\u200d", "\ufeff", "\u00ad", "9", "98450", "salary"]
    for _ in range(3000):
        text = "".join(rng.choice(parts) for _ in range(rng.randint(0, 40)))
        items = ["".join(rng.choice(parts) for _ in range(rng.randint(0, 30)))
                 for _ in range(rng.randint(0, 5))]
        out = summaries.check_answer(_answer(text, items), "A deck")
        if out is not None:
            assert len(out.text) <= summaries.TEXT_MAX
            assert len(summaries.sentences(out.text)) <= summaries.SENTENCES_MAX
            assert len(out.follow_ups) <= summaries.FOLLOW_UPS_MAX


def test_pz33b_a_nest_too_deep_to_parse_gives_no_summary() -> None:
    deep = '{"text": ' + "[" * 100_000 + "]" * 100_000 + "}"
    assert summaries.check_answer(deep, "") is None


def test_white_space_folds_and_a_fence_of_code_parses() -> None:
    raw = "```json\n" + _answer("Planned   the\n deck.", ["  Send\tthe deck "]) + "\n```"
    out = summaries.check_answer(raw, "")
    assert out is not None
    assert (out.text, out.follow_ups) == ("Planned the deck.", ("Send the deck",))


@pytest.mark.parametrize("raw", [
    None, 42, "", "no json here", "[1, 2]", '{"text": 5}', '{"follow_ups": []}',
    '{"text": "Planned.", "follow_ups": "Send it"}', '{"text": "Planned."',
])
def test_an_answer_that_does_not_parse_gives_no_summary(raw: object) -> None:
    assert summaries.check_answer(raw, "") is None


def test_an_answer_over_the_caps_cannot_be_built() -> None:
    with pytest.raises(summaries.SummaryShapeError):
        summaries.Answer(text="x" * 401)
    with pytest.raises(summaries.SummaryShapeError):
        summaries.Answer(text="One. Two. Three. Four.")
    with pytest.raises(summaries.SummaryShapeError):
        summaries.Answer(text="Ok.", follow_ups=("a", "b", "c", "d"))
    with pytest.raises(summaries.SummaryShapeError):
        summaries.Answer(text="Ok.", follow_ups=("x" * 121,))
    with pytest.raises(summaries.SummaryShapeError):
        summaries.Answer(text="")


def test_pz33b_the_waterline_is_the_newest_of_the_summary_and_the_mark() -> None:
    read = summaries.stored
    assert read({"v": 1, "through": "m3", "through_ms": 17}).waterline == 17
    assert read({"v": 1, "mark": {"through": "m4", "through_ms": 23}}).waterline == 23
    assert read({"v": 1, "through_ms": 17, "mark": {"through_ms": 23}}).waterline == 23
    assert read({"v": 1, "through_ms": 30, "mark": {"through_ms": 23}}).waterline == 30
    assert read({"v": 1, "mark": {"through": "m4", "at": "x"}}).waterline is None
    assert read({"v": 1, "through_ms": True, "mark": {"through_ms": "23"}}).waterline is None


def test_the_stored_read_is_lenient() -> None:
    assert summaries.stored(None) is None
    assert summaries.stored("not json") is None
    assert summaries.stored([1]) is None
    assert summaries.stored({"v": 1, "off": True}).off is True
    got = summaries.stored(json.dumps({
        "v": 1, "text": "Planned.", "follow_ups": ["Send", 3], "through": "m3",
        "through_ms": 17, "at": "2026-10-31T10:00:00Z",
        "mark": {"through": "m3", "at": "2026-10-31T09:59:00Z"}}))
    assert got == summaries.Stored(
        text="Planned.", follow_ups=("Send",), through="m3", through_ms=17,
        at="2026-10-31T10:00:00Z", mark_through="m3", mark_at="2026-10-31T09:59:00Z")


# ── chat_summary_for(org) ───────────────────────────────────────────────────


@pytest.fixture
def flags():
    """Set the three settings on a copy, and install it as a reload does."""
    original = get_settings()

    def _set(orgs: str, *, profile: bool = True, profile_orgs: str = "") -> None:
        install_settings(original.model_copy(update={
            "assistant_chat_summary_orgs": orgs,
            "assistant_profile_enabled": profile,
            "assistant_profile_orgs": profile_orgs,
        }))

    yield _set
    install_settings(original)


def test_an_org_off_the_list_gets_no_summary(flags) -> None:
    flags("")
    assert ap.chat_summary_for(ORG) is False
    flags(OTHER_ORG)
    assert ap.chat_summary_for(ORG) is False
    assert ap.chat_summary_for(OTHER_ORG) is True


def test_star_and_a_list_turn_it_on(flags) -> None:
    flags("*")
    assert ap.chat_summary_for(ORG) is True
    flags(f" {ORG.upper()} , {OTHER_ORG}")
    assert ap.chat_summary_for(ORG) is True
    assert ap.chat_summary_for("") is False
    assert ap.chat_summary_for(None) is False


def test_the_profile_switch_off_wins(flags) -> None:
    flags("*", profile=False)
    assert ap.chat_summary_for(ORG) is False
    flags("*", profile=True, profile_orgs=OTHER_ORG)
    assert ap.chat_summary_for(ORG) is False
    assert ap.chat_summary_for(OTHER_ORG) is True


def test_it_reads_the_settings_at_each_call(flags) -> None:
    """A SIGHUP reload installs new settings. The next call reads them, with
    no restart and no import again."""
    flags("")
    assert ap.chat_summary_for(ORG) is False
    flags(ORG)
    assert ap.chat_summary_for(ORG) is True
    flags("")
    assert ap.chat_summary_for(ORG) is False


# ── The flag name ───────────────────────────────────────────────────────────


def test_the_flag_is_a_reloadable_platform_name_with_an_empty_default() -> None:
    from acb_common.settings import Settings
    from gateway.flag_reload import RELOADABLE

    assert Settings.model_fields["assistant_chat_summary_orgs"].default == ""
    assert FLAG in RELOADABLE
    assert env_guard.is_platform_env(FLAG)
    assert FLAG in env_guard.PLATFORM_ENV_NAMES


def _bash_regex(name: str) -> re.Pattern[str]:
    body = STAGING_TOOL.read_text(encoding="utf-8")
    found = re.search(rf"^readonly {name}='([^']+)'$", body, re.M)
    assert found, f"{name} must be one quoted line of the staging tool"
    return re.compile(found.group(1))


def test_the_staging_tool_may_write_the_flag_and_names_its_credit_spend() -> None:
    """§15.8: the name matches neither wall of the tool, it is in the
    allowlist, and both the tool and its README say that it spends credits."""
    for wall in ("DENY_RE", "HELD_RE"):
        assert not _bash_regex(wall).search(FLAG), f"{wall} refuses {FLAG}"
    body = STAGING_TOOL.read_text(encoding="utf-8")
    block = re.search(r"^readonly ALLOWED_KEYS=\(\n(.*?)^\)$", body, re.M | re.S)
    assert block and FLAG in block.group(1).split()
    comment = body[body.index("readonly NAME_RE"):block.start()]
    assert re.search(rf"{FLAG}[^.]*credits", comment), (
        "the allowlist comment must say that the flag spends credits")
    readme = [ln for ln in STAGING_README.read_text(encoding="utf-8").splitlines()
              if ln.startswith(f"| `{FLAG}`")]
    assert len(readme) == 1 and "credits" in readme[0], readme
