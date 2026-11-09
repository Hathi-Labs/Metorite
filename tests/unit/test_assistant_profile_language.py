"""PZ-51 (personalization.md §5.3): the language rule and the language line.

R7 fences named here:

* ``language-rule-in-prompt``: every turn carries ``LANGUAGE_RULE_LINE``,
  with no profile block, and removing it fails this test.
* ``language-names-match-ts``: ``LANGUAGE_NAMES`` equals ``LANGUAGES`` in
  ``settings/assistant/lib/profile.ts``.
* ``language-tag-round-trip``: ``block_language`` returns the TAG.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from acb_common import assistant_profile as ap

ROOT = Path(__file__).resolve().parents[2]
TS = ROOT / "workbench/control_plane/src/app/settings/assistant/lib/profile.ts"


def test_language_names_equal_the_ts_map() -> None:
    text = TS.read_text(encoding="utf-8")
    body = re.search(r"export const LANGUAGES[^=]*=\s*\{(.*?)\};", text, re.S).group(1)
    ts = dict(re.findall(r'(\w+):\s*"([^"]+)"', body))
    assert len(ts) == 9
    assert ap.LANGUAGE_NAMES == ts


@pytest.mark.parametrize("tag,name", sorted(ap.LANGUAGE_NAMES.items()))
def test_style_line_names_the_language_and_round_trips_the_tag(tag, name) -> None:
    line = ap._style_line({"language": tag}, [])
    assert line == f"Language: {name} ({tag}). Answer in it, unless they write in another language."
    assert ap.block_language("## x\n" + line + "\nend") == tag


def test_an_unknown_tag_stays_bare_and_round_trips() -> None:
    line = ap._style_line({"language": "en-IN"}, [])
    assert line.startswith("Language: en-IN. Answer in it")
    assert ap.block_language(line) == "en-IN"
    assert ap.block_language("") is None
    assert ap.block_language("no language here") is None


def test_the_rule_text_is_pinned() -> None:
    rule = ap.LANGUAGE_RULE_LINE
    assert "answer the member in the member's language" in rule
    assert "Write every saved record in English" in rule
    for word in ("task and project titles and descriptions", "comments", "notes",
                 "CRM records", "calendar events", "email or message drafts"):
        assert word in rule
    assert "explicit request for another language" in rule


def test_every_turn_carries_the_rule_without_a_profile_block() -> None:
    from orchestrator import executor as ex

    for agent in ("orchestrator", "email-assistant", ""):
        parts = ex._context_preamble_parts({}, {}, agent)
        assert parts.count(ap.LANGUAGE_RULE_LINE) == 1
