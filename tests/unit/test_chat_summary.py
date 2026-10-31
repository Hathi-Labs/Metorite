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

Mutations, each red here: drop the sensitive-line rule of ``_keep``; let
``member_texts`` skip ``typed_text``; let ``prompt_rows`` read
``tool_events``; drop the brief-row check; drop ``enabled_for`` from
``chat_summary_for``.
"""

from __future__ import annotations

import json
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
    prompt = " ".join(w for _who, w in summaries.prompt_rows(rows, MEMBER))
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
    assert ("mira", "Sure. Three tiers, and the old price stays.") in prompt


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


def test_the_prompt_keeps_the_newest_rows_under_the_cap() -> None:
    rows = [_row(f"a{i}", "assistant", f"{i:03d}" + "x" * 97) for i in range(200)]
    kept = summaries.prompt_rows(rows, MEMBER)
    total = sum(len(w) for _who, w in kept)
    assert total == summaries.INPUT_MAX
    assert kept[-1][1].startswith("199"), "the newest row must go in"
    assert kept[0][1].startswith("080"), "the oldest rows drop first"
    short = summaries.prompt_rows(rows, MEMBER, limit=150)
    assert [w[:3] for _who, w in short] == ["198", "199"]
    assert len(short[0][1]) == 50, "the oldest row that goes in loses its end"


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
