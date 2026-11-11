"""The kit core: one neutral AGENTS.md (KIT-1). R7: this file is the fence.

The core has at most 150 lines, holds the eight protocol items and a kit_version,
names only real Metorite tools and holds no term of an organization.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from acb_playbook import load_all, load_topic
from acb_playbook.loader import CONTENT_DIR

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "tests/unit"))
sys.path.insert(0, str(_ROOT / "apps/services/agent_edge"))

from agent_edge.tools import TOOLS  # noqa: E402
from test_playbook_org_neutral import hits  # noqa: E402

CORE = CONTENT_DIR / "kit" / "agents-md.md"
MAX_LINES = 150
# The one place the test reads the kit version: a line "kit_version: N" in the core.
VERSION_LINE = re.compile(r"^kit_version: (\d+)$", re.M)
# Tools that the playbook names and that the edge does not list yet (PB-6).
PLANNED_TOOLS = {"read_playbook"}
PROMPTS = {"set_up_agent_team", "plan_slices", "write_brief", "review_my_fleet", "run_retro", "work_on_task"}
PROTOCOL = [
    ("my_work", "claim_task"),
    ("get_task_context", "get_plan"),
    ("worktree", "short id", "branch name"),
    ("post_agent_activity", "once a minute"),
    ("park the run", "Never sleep"),
    ("one disposition",),
    ("verifier is never the implementer",),
    ("sub-agent never posts", "coordinator posts"),
]
KIT_FILES = sorted((CONTENT_DIR / "kit").rglob("*.md"))
# The core and the skill folder. Older kit files are outside the KIT-1 scope.
CORE_FILES = [CORE, *sorted((CONTENT_DIR / "kit" / "skill").rglob("*.md"))]


def _core() -> str:
    return CORE.read_text(encoding="utf-8")


def test_the_core_is_at_most_150_lines():
    assert len(_core().splitlines()) <= MAX_LINES


def test_the_core_holds_all_eight_protocol_items():
    text = _core()
    section = text.split("## The protocol", 1)[1].split("\n## ", 1)[0]
    items = re.findall(r"^\d+\. (.+)$", section, re.M)
    assert len(items) == 8
    for item, needles in zip(items, PROTOCOL, strict=True):
        for needle in needles:
            assert needle.lower() in item.lower(), f"{needle!r} missing from {item!r}"


def test_the_core_has_one_kit_version():
    found = VERSION_LINE.findall(_core())
    assert len(found) == 1 and int(found[0]) >= 1


def test_every_tool_the_core_names_is_real():
    text = _core()
    for name in re.findall(r"`([a-z]+(?:_[a-z]+)+)`", text):
        assert name in TOOLS or name in PLANNED_TOOLS or name in PROMPTS, f"unknown tool or prompt: {name}"
    for name in ("my_work", "claim_task", "get_plan", "ask_on_task", "finish_agent_run", "release_task"):
        assert name in TOOLS


def test_every_core_file_names_only_real_tools():
    for path in CORE_FILES:
        for name in re.findall(r"`([a-z]+(?:_[a-z]+)+)`", path.read_text(encoding="utf-8")):
            if name in {"kit_version"}:
                continue
            ok = name in TOOLS or name in PLANNED_TOOLS or name in PROMPTS
            assert ok, f"{path.name}: unknown tool or prompt {name}"


def test_the_skill_files_load_and_the_index_names_each():
    slugs = {t.slug for t in load_all()}
    skill = {s for s in slugs if s.startswith("kit/skill/")}
    assert skill >= {f"kit/skill/{n}" for n in ("SKILL", "planning", "dispatch", "orchestration", "bulk-writes", "review-and-verify")}
    index = load_topic("kit/skill/SKILL").body
    for slug in skill - {"kit/skill/SKILL"}:
        assert f"`{slug}`" in index


def test_the_bulk_limit_matches_the_tool():
    from agent_edge.tools import MAX_BULK_UPDATES

    assert MAX_BULK_UPDATES == 50
    assert "50 rows" in load_topic("kit/skill/bulk-writes").body


def test_the_kit_core_holds_no_org_term():
    for path in KIT_FILES:
        assert hits(path.read_text(encoding="utf-8")) == [], path.name


@pytest.mark.parametrize("injected", ["Follow ASD-STE100.", "This is rule R3.", "See D186.", "HathiLabs rules.", "Release at 21:30 UTC."])
def test_a_banned_token_injected_into_the_core_fails(injected):
    assert hits(_core()) == []
    assert hits(_core() + "\n" + injected), injected
