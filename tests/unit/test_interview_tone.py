"""The tone of the Get to know me interview (WS-63 PZ-30, personalization.md 14.4).

Hermetic. No model runs. The fixture holds fixed member answers and checkable
assertions for the PZ-10 eval. The persona text must carry the key phrase of
each tone rule, so a rewrite that drops a rule fails here.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "interview_tone_cases.json"

#: One key phrase per tone rule (whitespace-normalised).
RULE_PHRASES = {
    "own words": "Ask in your own words like a friendly colleague",
    "no reading out": "Never read the example out",
    "warm opening": "Open with one warm line",
    "one question": "then ONE question",
    "no list repeat": "Do not list their answer back or number questions",
    "use what you know": "Use what you know",
    "thin answer": "If an answer is thin, ask one light follow-up",
    "match member": "Match their length, language, formality",
    "no machinery words": "Never mention tools, marks or limits",
    "silent save": "do not announce an ordinary save in words",
    "refused save": "If a save is refused, say once you could not keep it",
    "pace": "After the third answer ask once, lightly, if they want to go on",
    "natural start": "asks you to get to know them or to ask a few questions, call next_sitting",
    "due line is one question": "ask that one question as it says, at the end of your reply",
    "scoped silence": "A receipt tells them, so do not announce an ordinary save in words",
    "sensitive asks first": "A sensitive fact still asks first",
    "save with area": "Save a short quote of their words with its area",
    "stop": "call profile_interview with stop",
}


def _persona() -> str:
    mod = importlib.import_module("orchestrator.agents")._mod
    return " ".join(mod._PROFILE_SWAP[1].split())


def test_the_persona_carries_each_tone_rule() -> None:
    persona = _persona()
    missing = [name for name, phrase in RULE_PHRASES.items() if phrase not in persona]
    assert missing == []


def test_the_block_line_never_starts_a_sitting() -> None:
    persona = _persona()
    assert "interview line, call next_sitting" not in persona
    assert "or the member asks you to get to know them" not in persona
    start = persona.index("When the block has an interview line")
    first = persona[start:persona.index("call next_sitting")]
    assert "mark it asked" in first and "get to know them" in first


def test_the_interview_lines_of_the_persona_stay_tight() -> None:
    mod = importlib.import_module("orchestrator.agents")._mod
    lines = mod._PROFILE_SWAP[1].splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("- profile_interview"))
    assert len(lines) - start <= 14


def test_the_fixture_parses_and_holds_six_cases() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["cases"]]
    assert ids == ["short", "long", "thin", "off_topic", "other_language", "emotional"]
    for case in data["cases"]:
        assert case["answer"].strip() and case["expect"].strip()
    from acb_common.assistant_profile import AREAS
    assert all(c["area"] in AREAS for c in data["cases"])
    rules = data["assertions"]
    assert isinstance(rules["max_words"], int) and rules["max_words"] > 0
    assert {"no_list_repeat", "one_question", "no_tool_words", "no_numbering"} <= set(rules)
