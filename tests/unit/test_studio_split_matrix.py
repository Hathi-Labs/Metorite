"""WS-67 TS-1b: the golden matrix of a Studio turn (task 3207).

``project-docs/specs/one_assistant_context.md`` §4.2.9, "The Studio split"
and the block TS-1b. The matrix is the R7 fence of the split.

Each case is one turn on the Studio surface. The matrix keeps, for each case
and for each value of ``HELD_CARD_WRITES_ORGS`` (off and on):

* the packs that :func:`tool_packs.choose` gives, and the packs it left out;
* the tool count of the request, as ``_tool_injection._apply_tool_packs``
  counts it (the base, the pack tools, less each specialist that leaves);
* the tool names of the packs;
* the answer of :func:`tool_packs.plan_loader`: the offer and the owners
  that the loader reaches.

The test is pure: no database, no Redis and no model. The golden file
``tests/unit/golden/studio_split_matrix.json`` was made on main BEFORE the
split. Run this file as a script to write it again.

Run: ``uv run pytest tests/unit/test_studio_split_matrix.py -q``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp

GOLDEN = Path(__file__).parent / "golden" / "studio_split_matrix.json"

ORG, SESSION, MEMBER = "org-matrix", "session-matrix", "member@matrix.test"
#: The base of the orchestrator on production, with the load tool (+1). The
#: same numbers as ``test_pack_turn_keeps_used_packs.BASE``.
BASE = 56 + 1
#: The specialist tools of the base, one for each owner.
SPECIALISTS = {"projects-assistant": 1, "email-assistant": 1, "crm-assistant": 1}
TURN = 10


def _sticky(slug: str, used: int, place: int) -> tp.Sticky:
    return tp.Sticky(slug=slug, first=(1, place), used=used)


def _skill(slug: str, *tools: str) -> Any:
    """A published skill that the words of the turn name. ``choose`` reads only
    its slug and its tools (``tool_packs._skills_of``)."""
    return SimpleNamespace(slug=slug, tools=tuple(tools))


#: The cases: the words, the sticky packs, the skills, and whether the admin
#: list holds the dark ``studio-workflows`` pack.
CASES: dict[str, dict[str, Any]] = {
    "studio_alone": {"words": "make the form blue"},
    "studio_with_email": {"words": "check my emails"},
    "studio_with_crm_records_and_calendar_day": {
        "words": "move the deal to won and rebuild my day"},
    "studio_with_email_and_crm_records": {
        "words": "add this email to the sales pipeline"},
    "studio_with_projects_reminders": {"words": "remind me to call Ravi tomorrow"},
    "studio_with_two_sticky_packs": {
        "words": "make the form blue",
        "sticky": (_sticky("crm-records", 8, 0), _sticky("projects-cycles", 9, 1))},
    "studio_with_a_named_skill": {
        "words": "run my weekly digest",
        "skills": (_skill("skill-weekly-digest", "studio_record_list"),)},
    "studio_with_projects_reminders_and_a_named_skill": {
        "words": "remind me to run my weekly digest",
        "skills": (_skill("skill-weekly-digest", "studio_record_list"),)},
    "studio_with_two_named_skills": {
        "words": "run my weekly digest and my sales digest",
        "skills": (_skill("skill-sales-digest", "studio_record_list"),
                   _skill("skill-weekly-digest", "studio_record_list"))},
    "studio_with_workflow_words_and_studio_workflows_on": {
        "words": "publish the workflow", "workflows": True},
}

#: The values of ``HELD_CARD_WRITES_ORGS``: off, and on for each org.
HELD = {"off": "", "on": "*"}


def _size(slug: str) -> int:
    return len(sf.SKILL_FAMILIES[slug]["tools"])


def _allowed(workflows: bool) -> frozenset[str]:
    """The packs of a box whose ``TOOL_PACKS`` names each pack but the dark
    ``studio-workflows`` (an owner-only line). The followers come in."""
    every = frozenset(sf.pack_slugs())
    return every if workflows else every - {"studio-workflows"}


def _row(case: dict[str, Any]) -> dict[str, Any]:
    allow = _allowed(bool(case.get("workflows")))
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), TURN,
                       tuple(case.get("sticky", ())), str(case["words"]), True,
                       surface="studio", skills=tuple(case.get("skills", ())))
    choice = tp.choose(turn, allowed=allow, base_tools=BASE, specialists=SPECIALISTS)
    skills = [str(s.slug) for s in choice.skills]
    named = {name for s in choice.skills for name in s.tools}
    needs = [slug for slug in choice.slugs if named & set(sf.SKILL_FAMILIES[slug]["tools"])]
    loaded = [(slug, False) for slug in choice.slugs]
    # The count of ``_apply_tool_packs``: the base, the pack tools, less each
    # specialist tool that a pack replaces.
    draft = tp.PackRun.of(turn, choice, loaded, skills=skills, needs=needs)
    gone = sum(SPECIALISTS.get(owner, 0) for owner in draft.replaced)
    current = BASE + sum(_size(slug) for slug in choice.slugs) - gone
    plan = tp.plan_loader(
        allow, [*choice.slugs, *skills], current=current,
        removable={o: n for o, n in SPECIALISTS.items() if o not in draft.replaced},
        spare={slug: _size(slug) for slug in draft.spare()})
    return {
        "packs": list(choice.slugs),
        "layers": {slug: choice.layers.get(slug, "") for slug in choice.slugs},
        "skipped": list(choice.skipped),
        "evicted": list(choice.evicted),
        "skills": skills,
        "skills_skipped": list(choice.skills_skipped),
        "tools": current,
        "names": [name for slug in choice.slugs for name in sf.SKILL_FAMILIES[slug]["tools"]],
        "offered": list(plan.offered),
        "via_loader": sorted(plan.via_loader),
    }


def matrix() -> dict[str, dict[str, Any]]:
    """Each case under each value of ``HELD_CARD_WRITES_ORGS``."""
    from acb_common import get_settings

    settings = get_settings()
    before = getattr(settings, "held_card_writes_orgs", "")
    out: dict[str, dict[str, Any]] = {}
    try:
        for held, value in HELD.items():
            settings.held_card_writes_orgs = value
            for name, case in CASES.items():
                out[f"{name}|held={held}"] = _row(case)
    finally:
        settings.held_card_writes_orgs = before
    return out


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_the_matrix_equals_the_golden_of_main() -> None:
    """On main, before the split, each case gives its golden row. Mutation:
    change a pack, a word or a cap -> red."""
    got = matrix()
    want = _golden()["main"]
    assert sorted(got) == sorted(want)
    for key in want:
        assert got[key] == want[key], key


if __name__ == "__main__":  # write the golden of main again
    if "--write-main" not in sys.argv:
        raise SystemExit("usage: python tests/unit/test_studio_split_matrix.py --write-main")
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps({"main": matrix()}, indent=1, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"wrote {GOLDEN}")
