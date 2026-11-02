"""PJP-1: one colour vocabulary (D168.2). Spec: project_planning.md §3.2, §10.

Fences:
1. ``hue_of`` maps every alias and the seven production codes of §2.1.
2. ``hues.py`` agrees with ``statusAccent.ts`` through the JSON fixture
   (the vitest ``statusAccent.test.ts`` reads the same file).
3. No status or view card detail holds a bracketed category.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import hues
from skill_projects.hues import hue_of

from tests.unit._projects_agent_fakes import approve, fake_gateway

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests/fixtures/hue_vocabulary.json").read_text(encoding="utf-8"))


def test_every_alias_maps() -> None:
    for word, hue in hues.ALIASES.items():
        assert hue_of(word) == hue
        assert hue_of(word.upper()) == hue
        assert hue in hues.HUES


def test_the_seven_production_codes_map() -> None:
    codes = ["#EAB308", "#6366F1", "#F97316", "#94A3B8", "#CBD5E1", "#16A34A", "#EC4899"]
    want = ["amber", "blue", "amber", "gray", "gray", "green", "violet"]
    assert [hue_of(c) for c in codes] == want
    assert hue_of("#eab308") == "amber"


def test_empty_keeps_the_default_and_an_unknown_word_is_gray() -> None:
    assert hue_of(None) is None and hue_of("") is None and hue_of("  ") is None
    assert hue_of("chartreuse") == "gray"
    assert hue_of("#12") == "gray"


def test_the_server_tables_match_the_fixture() -> None:
    assert FIXTURE["aliases"] == hues.ALIASES
    assert FIXTURE["gray_saturation"] == hues.GRAY_SATURATION
    assert [list(b) for b in hues.HUE_BANDS] == FIXTURE["bands"]
    for code, hue in FIXTURE["hex"].items():
        assert hue_of(code) == hue, code


BRACKET = re.compile(r"\[[a-z_]+\]")


def writes(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["method"] != "GET"]


async def test_a_status_and_view_card_has_no_bracketed_category(monkeypatch) -> None:
    from tests.unit.test_projects_agent_writes import UUID, responder

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, responder)
    await skill_projects.create_status(UUID, "Blocked", category="in_progress")
    await skill_projects.update_status(UUID, "to do", category="done")
    await skill_projects.save_view(UUID, "Mine", view_type="board")
    assert len(asked) == 3
    for card in asked:
        assert not BRACKET.search(card["detail"]), card["detail"]


async def test_the_status_tools_store_the_hue_name(monkeypatch) -> None:
    from tests.unit.test_projects_agent_writes import UUID, responder

    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, responder)
    await skill_projects.create_status(UUID, "Blocked", color="#6366F1")
    posted = [c for c in writes(calls) if c["path"].endswith("/statuses")]
    assert posted[0]["json"]["color"] == "blue"
