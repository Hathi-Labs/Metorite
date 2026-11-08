"""STU-G3a fence: the ``studio`` pack tells the agent to edit the package that the page facts name.

The Studio IDE (``/studio/build``) gives the dock facts from ``lib/studio/ideFacts.ts``: the package
id, the ``based_on`` (the ``manifest_hash`` of the draft) and the draft. The studio tools keep no
draft, and ``studio_package_get`` returns no manifest. So the rule is what steers a save to THIS
package, with a hash that fails closed when the draft moved. The save itself is proved in
``test_studio_package_tools.py``.

Mutation 1: delete the PAGE FACTS bullet from ``_STUDIO_RULES``. "names the page facts" fails.
Mutation 2: rename a word of the facts in ``ideFacts.ts`` that the rule uses. "the rule only uses words that the facts hold" fails.
Mutation 3: put the "save the manifest unchanged first" step back. "needs no unchanged save" fails.
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
    return " ".join(m.group(0).split())


def _fact_text() -> str:
    """The text of every fact line that the IDE writes, as it stands in the source."""
    return FACTS.read_text(encoding="utf-8")


def test_the_studio_rules_name_the_page_facts() -> None:
    text = _bullet()
    assert "package id" in text and "make no new package" in text
    assert "based_on from the facts" in text


def test_the_rule_only_uses_words_that_the_facts_hold() -> None:
    """The agent finds the fact by these words, so both sides keep each of them."""
    facts = _fact_text()
    for word in ("package id", "based_on", "stored draft manifest"):
        assert word in facts, word
    # The first fact is the one that the rule calls "the page facts that name a package id".
    assert re.search(r"PACKAGE_FACT\s*=\s*\"[^\"]*package id is\"", facts)
    assert "package id" in _bullet() and "based_on" in _bullet()


def test_the_rule_needs_no_unchanged_save() -> None:
    assert "unchanged" not in _bullet()


def test_the_bullet_is_short_plain_english() -> None:
    for sentence in re.split(r"(?<=[.])\s+", _bullet()):
        assert len(sentence.split()) <= 25, sentence
