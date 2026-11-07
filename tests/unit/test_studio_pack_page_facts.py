"""STU-G3a fence: the ``studio`` pack tells the agent to edit the package that the page facts name.

The Studio IDE (``/studio/build``) gives the dock a fact that starts with the words in
``PACKAGE_FACT`` (``lib/studio/ideFacts.ts``). The studio tools keep no draft, and
``studio_package_get`` does not return the manifest, so the rule is the only thing that
steers a save to THIS package.

Mutation 1: delete the PAGE FACTS bullet from ``_STUDIO_RULES``. "names the page facts" fails.
Mutation 2: change the words of ``PACKAGE_FACT`` in ``ideFacts.ts``. "matches the words of the IDE" fails.
"""
from __future__ import annotations

import re
from pathlib import Path

from acb_skills import tool_packs

ROOT = Path(__file__).resolve().parents[2]
FACTS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "studio" / "ideFacts.ts"


def _bullet() -> str:
    rules = tool_packs.rules_for("studio")
    m = re.search(r"- PAGE FACTS\..*?(?=\n- [A-Z]|\Z)", rules, re.S)
    assert m, "the studio rules lost the PAGE FACTS bullet"
    return m.group(0)


def test_the_studio_rules_name_the_page_facts() -> None:
    text = _bullet()
    assert "package id" in text and "make no new package" in text
    assert "based_on" in text


def test_the_rule_matches_the_words_of_the_ide() -> None:
    source = FACTS.read_text(encoding="utf-8")
    assert "PACKAGE_FACT" in source
    assert "package id is" in source
    # The agent finds the fact by "package id", so both sides keep those two words.
    assert "package id" in _bullet()


def test_the_bullet_is_short_plain_english() -> None:
    for sentence in re.split(r"(?<=[.])\s+", _bullet()):
        assert len(sentence.split()) <= 25, sentence
