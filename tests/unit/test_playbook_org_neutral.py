"""The playbook holds no HathiLabs rule (D186.2, PB-1). R7: this file is the fence.

The fixture lists the HathiLabs terms. This test fails on a match in any
playbook file. A second test checks that each entry still matches text in the
repository rules, so the list cannot drift. PB-5 widens the scan to the
product strings and defaults.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from acb_playbook import load_all

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "tests/fixtures/playbook_org_tokens.json"
_RULE_FILES = ("CLAUDE.md", "project-docs/work_plan.md", "docs/style_ste.md")


def _entries() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["entries"]


def _pattern(entry: dict) -> re.Pattern[str]:
    return re.compile(re.escape(entry["value"]) if entry["kind"] == "token" else entry["value"])


def hits(text: str) -> list[str]:
    """The ids of the fixture entries that match the text."""
    return [e["id"] for e in _entries() if _pattern(e).search(text)]


def test_the_fixture_is_well_formed():
    entries = _entries()
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    assert all(e["kind"] in {"token", "regex"} and e["value"] for e in entries)
    for e in entries:
        _pattern(e)  # a bad regex raises here


def test_no_playbook_file_holds_a_hathilabs_term():
    for topic in load_all():
        text = "\n".join([topic.title, topic.summary, topic.body])
        assert hits(text) == [], f"{topic.slug} holds a HathiLabs term: {hits(text)}"


@pytest.mark.parametrize("entry", _entries(), ids=lambda e: e["id"])
def test_each_entry_still_matches_a_repository_rule_file(entry):
    pattern = _pattern(entry)
    for name in _RULE_FILES:
        if pattern.search((_ROOT / name).read_text(encoding="utf-8")):
            return
    pytest.fail(f"{entry['id']} matches none of {_RULE_FILES}; remove it or fix it")


@pytest.mark.parametrize(
    "injected",
    [
        "Follow Simplified Technical English.",
        "Write it in STE.",
        "This is rule R5.",
        "See D145 for the loop.",
        "A HathiLabs rule.",
        "This is OWNER-GATE work.",
        "Release at 21:30 UTC.",
    ],
)
def test_the_scan_catches_an_injected_term(injected):
    assert hits(injected), injected
