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

The golden file ``tests/unit/golden/studio_split_matrix.json`` has two
columns. ``main`` was made on main BEFORE the split (commit 803d8b26c), and
it does not change. The review rounds added cases. Their ``main`` rows came
from the ``acb_skills`` of 3de2813f0, the base of the branch, and that code
gave each older ``main`` row again, byte for byte. ``split`` is the matrix
after the split. The test holds
the code to ``split``, and it holds the difference of the two columns to the
changes that the audit named (:data:`NAMED_CHANGES`).

CE-15 (task 3463) added one tool, ``my_tasks_list_events``, to the pack
``calendar-day``. Each row of BOTH columns that holds that pack gained the
name and one tool, so its count is one more than the audit's count. No
other field of a row changed.

PAR-8a (task 3903) added seven tools to the dark pack ``studio-workflows``.
Each row of BOTH columns that holds that pack gained the seven names and
seven tools (82 to 89). No other field of a row changed.

P-1a (task 3112) added three more tools to ``calendar-day``:
``my_tasks_list_calendar_connections``, ``my_tasks_suggest_title`` and
``my_tasks_enrich``. Each row of BOTH columns that holds that pack gained the
three names and three tools. A ``main`` count above 110 is the audit's count
plus the growth of the pack. It is not a request that main sent. One more
field changed, in BOTH columns: in
``calendar_projects_and_calendar_day_sticky_with_email|held=off`` the run holds
107 tools, so ``projects-cycles`` (6 tools) no longer fits the cap of 110, and
the loader does not offer it.

P-5b-1 (task 3114) added the dark pack ``people`` of four tools, with no
surface and another owner. :func:`_allowed` leaves it out, as it leaves out
``org-admin``, so no row of the golden file changed.

P-1b (task 3922) added the pack ``mail-tasks`` of two tools. It follows
``projects``, so :func:`_allowed` holds it. Each row whose loader offer held
the Projects packs gained ``mail-tasks`` in ``offered``, in BOTH columns. No
other field of a row changed.

P-1c (task 3923) added the dark pack ``calendar-connections`` of one tool,
with no surface and the owner ``org-admin``. :func:`_allowed` leaves it out
for the same reason, so no row of the golden file changed.

The sticky state of a chat from before the split has its own tests at the end.

The test is pure: no database, no Redis and no model. Run this file as a
script with ``--write-split`` to write the ``split`` column again.

Run: ``uv run pytest tests/unit/test_studio_split_matrix.py -q``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
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
STUDIO_PACKS = frozenset({"studio", "studio-more"})


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
        "words": "move the deal to won and rebuild my day"
    },
    "studio_with_email_and_crm_records": {"words": "add this email to the sales pipeline"},
    "studio_with_projects_reminders": {"words": "remind me to call Ravi tomorrow"},
    "studio_with_two_sticky_packs": {
        "words": "make the form blue",
        "sticky": (_sticky("crm-records", 8, 0), _sticky("projects-cycles", 9, 1)),
    },
    "studio_with_a_named_skill": {
        "words": "run my weekly digest",
        "skills": (_skill("skill-weekly-digest", "studio_record_list"),),
    },
    "studio_with_projects_reminders_and_a_named_skill": {
        "words": "remind me to run my weekly digest",
        "skills": (_skill("skill-weekly-digest", "studio_record_list"),),
    },
    "studio_with_two_named_skills": {
        "words": "run my weekly digest and my sales digest",
        "skills": (
            _skill("skill-sales-digest", "studio_record_list"),
            _skill("skill-weekly-digest", "studio_record_list"),
        ),
    },
    "studio_with_workflow_words_and_studio_workflows_on": {
        "words": "publish the workflow",
        "workflows": True,
    },
    # Review P3: the same words where the owner did not turn studio-workflows on.
    # The allow list comes from FOLLOWS, so an entry for studio-workflows there
    # would load it here.
    "studio_with_workflow_words_and_studio_workflows_off": {"words": "publish the workflow"},
    # Review P2-a: both Studio packs are sticky, and they tie but for the first
    # load. Two word packs make four packs, so one sticky pack must leave.
    "studio_with_a_sticky_pair_and_two_word_packs": {
        "words": "log a call on the Acme deal and rebuild my day",
        "sticky": (
            tp.Sticky(slug="studio", first=(5, 0), used=9),
            tp.Sticky(slug="studio-more", first=(5, 1), used=9),
        ),
    },
    # Review round 2, the over-fire fence: the follow pack was used later than
    # the core, so there is no tie, and the core leaves by the order of
    # ``_leave_order`` (the least recently used first).
    "studio_with_a_sticky_pair_the_follower_used_later": {
        "words": "log a call on the Acme deal and rebuild my day",
        "sticky": (
            tp.Sticky(slug="studio", first=(5, 0), used=8),
            tp.Sticky(slug="studio-more", first=(5, 1), used=9),
        ),
    },
    # Review round 2, example (a): two Projects packs of FOLLOWS tie, with no
    # surface. projects-reports has its own words, so it is no twin, and the
    # order of main holds: projects-plan leaves.
    "projects_plan_and_reports_sticky_with_email": {
        "surface": "",
        "words": "check my emails",
        "sticky": (
            tp.Sticky(slug="projects-plan", first=(9, 0), used=9),
            tp.Sticky(slug="projects-reports", first=(9, 1), used=9),
        ),
    },
    # The same pair, and the words name the follow pack (projects-reports)
    # while the parent is only sticky.
    "projects_plan_and_reports_sticky_the_follower_named": {
        "surface": "",
        "words": "send the status report and check my emails",
        "sticky": (
            tp.Sticky(slug="projects-plan", first=(9, 0), used=9),
            tp.Sticky(slug="projects-reports", first=(9, 1), used=9),
        ),
    },
    # Review round 2, example (b): on the Calendar surface, the core Projects
    # pack and calendar-day tie. calendar-day has no surface, so it is no twin,
    # and the order of main holds: the core leaves.
    "calendar_projects_and_calendar_day_sticky_with_email": {
        "surface": "calendar",
        "words": "rebuild my day and check my emails",
        "sticky": (
            tp.Sticky(slug="projects", first=(9, 0), used=9),
            tp.Sticky(slug="calendar-day", first=(9, 1), used=9),
        ),
    },
}

#: The cases on another surface than Studio. They pin the order of main for
#: the packs of FOLLOWS that are no twin of their parent.
OTHER_SURFACE_CASES = frozenset(
    {
        "projects_plan_and_reports_sticky_with_email",
        "projects_plan_and_reports_sticky_the_follower_named",
        "calendar_projects_and_calendar_day_sticky_with_email",
    }
)

#: The cases whose words name the dark workflow pack. Only these may load it.
WORKFLOW_WORD_CASES = frozenset(
    {
        "studio_with_workflow_words_and_studio_workflows_on",
        "studio_with_workflow_words_and_studio_workflows_off",
    }
)

#: The values of ``HELD_CARD_WRITES_ORGS``: off, and on for each org.
HELD = {"off": "", "on": "*"}


def _size(slug: str) -> int:
    return len(sf.SKILL_FAMILIES[slug]["tools"])


def _allowed(workflows: bool) -> frozenset[str]:
    """The packs of a box whose ``TOOL_PACKS`` names each pack that follows no
    other, less the dark ``studio-workflows`` (an owner-only line). The
    followers come in through :func:`tool_packs.with_followers`, as
    ``packs_on`` adds them. So an entry of FOLLOWS changes this list. The dark
    ``studio-share`` (WS-STU J2d, one tool, no surface) is out of the matrix in
    both lists: the matrix holds the split of TS-1b. The dark ``studio-repair``
    (TOF-3a F12c, two tools, no surface) is out for the same reason. Only an
    owner ``TOOL_PACKS`` line turns it on, and the run drops it from the allow
    list while the org is off its switches (``_tool_injection._apply_tool_packs``).
    The dark ``org-admin`` (PAR-2a, four tools, no surface, another owner) is out
    for the same reason. The dark ``people`` (P-5b-1, task 3114, four tools, no
    surface, owner ``org-admin``) is out for the same reason. The dark
    ``calendar-connections`` (P-1c, task 3923, one tool, no surface, owner
    ``org-admin``) is out for the same reason."""
    every = frozenset(sf.pack_slugs()) - {"studio-share", "studio-repair", "org-admin",
                                          "people", "calendar-connections"}
    if workflows:
        return every
    named = every - frozenset(tp.FOLLOWS) - {"studio-workflows"}
    return tp.with_followers(named) & every


def _row(case: dict[str, Any]) -> dict[str, Any]:
    allow = _allowed(bool(case.get("workflows")))
    turn = tp.PackTurn(
        ORG,
        SESSION,
        tp._member_part(MEMBER),
        TURN,
        tuple(case.get("sticky", ())),
        str(case["words"]),
        True,
        surface=str(case.get("surface", "studio")),
        skills=tuple(case.get("skills", ())),
    )
    choice = tp.choose(turn, allowed=allow, base_tools=BASE, specialists=SPECIALISTS,
                       skill_packs=case.get("skill_packs"))
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
        allow,
        [*choice.slugs, *skills],
        current=current,
        removable={o: n for o, n in SPECIALISTS.items() if o not in draft.replaced},
        spare={slug: _size(slug) for slug in draft.spare()},
    )
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


def matrix(cases: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Each case of *cases* (:data:`CASES` when None) under each value of
    ``HELD_CARD_WRITES_ORGS``."""
    from acb_common import get_settings

    settings = get_settings()
    before = getattr(settings, "held_card_writes_orgs", "")
    out: dict[str, dict[str, Any]] = {}
    try:
        for held, value in HELD.items():
            settings.held_card_writes_orgs = value
            for name, case in (CASES if cases is None else cases).items():
                out[f"{name}|held={held}"] = _row(case)
    finally:
        settings.held_card_writes_orgs = before
    return out


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _view(row: dict[str, Any]) -> dict[str, Any]:
    """What a turn sends and can reach, with the split itself left out: the
    tool count, the tool names as a set, the skills, the sticky packs that
    left, and the loader offer and reach less the two Studio packs."""
    return {
        "tools": row["tools"],
        "names": sorted(row["names"]),
        "skills": row["skills"],
        "evicted": row["evicted"],
        "offered": [s for s in row["offered"] if s not in STUDIO_PACKS],
        "via_loader": row["via_loader"],
    }


def _both(case: str, *fields: str) -> dict[str, frozenset[str]]:
    return {f"{case}|held={held}": frozenset(fields) for held in HELD}


#: The changes that the audit of 2026-11-14 named (§4.2.9, the block TS-1b), as
#: the fields of :func:`_view` that differ between ``main`` and ``split``. Every
#: other case and field is the same. The values are in the golden file.
NAMED_CHANGES: dict[str, frozenset[str]] = {
    # 112 becomes 99: the turn loses studio-more, the last pack that came by
    # the surface alone (``choose``, task 1573).
    **_both("studio_with_crm_records_and_calendar_day", "tools", "names"),
    # 99 becomes 109: the core now fits. With the switch off, the three packs
    # take each place, so the loader loses its offer.
    "studio_with_email_and_crm_records|held=off": frozenset(
        {"tools", "names", "offered", "via_loader"}
    ),
    "studio_with_email_and_crm_records|held=on": frozenset({"tools", "names"}),
    # The same tools at the start. The loader loses its third place and the
    # reach of the Projects owner, unless HELD_CARD_WRITES_ORGS makes the word
    # pack spare.
    "studio_with_projects_reminders|held=off": frozenset({"offered", "via_loader"}),
    # The older sticky pack (crm-records) leaves, not studio-more: 98 becomes 86.
    "studio_with_two_sticky_packs|held=off": frozenset({"tools", "names", "evicted"}),
    # With the switch on, the loader also stops the offer of the email pack. On
    # main the spare packs freed 19 tools: 98 - 19 + 31 = 110, so email fit. After
    # the split only projects-cycles is spare: 86 - 6 + 31 = 111, over the cap.
    # The email specialist stays in the run, so a hand-off to it still works.
    # crm-records, which left, is on offer in its place.
    "studio_with_two_sticky_packs|held=on": frozenset({"tools", "names", "evicted", "offered"}),
    # Review P2-a: both Studio packs are sticky and tie. The follow pack leaves
    # (``_follower_first``), so the core stays: 112 becomes 99. On main the chat
    # had no studio-more, so nothing left.
    **_both("studio_with_a_sticky_pair_and_two_word_packs", "tools", "names", "evicted"),
    # Review round 2: the same pair, but studio-more was used later. No tie, so
    # the core leaves as the least recently used: 112 becomes 102. The rule of
    # the twin applies to a tie only.
    **_both("studio_with_a_sticky_pair_the_follower_used_later", "tools", "names", "evicted"),
    # The three cases on another surface (OTHER_SURFACE_CASES) are not here:
    # the split must give the result of main for them.
    # A named published skill loses its place.
    **_both("studio_with_projects_reminders_and_a_named_skill", "skills"),
    **_both("studio_with_two_named_skills", "skills"),
    # The loader loses its third place: here a skill takes it.
    **_both("studio_with_a_named_skill", "offered", "via_loader"),
    # The loader loses its third place: here the dark studio-workflows takes
    # it, where the owner turned that pack on.
    "studio_with_workflow_words_and_studio_workflows_on|held=off": frozenset(
        {"offered", "via_loader"}
    ),
    "studio_with_workflow_words_and_studio_workflows_on|held=on": frozenset({"via_loader"}),
}


def test_the_matrix_equals_the_golden_split() -> None:
    """After the split, each case gives its golden row. Mutation: move a core
    tool to studio-more, drop the surface of studio-more, or drop FOLLOWS ->
    red."""
    got = matrix()
    want = _golden()["split"]
    assert sorted(got) == sorted(want)
    for key in want:
        assert got[key] == want[key], key


def test_the_split_changes_only_the_named_cases() -> None:
    """The fence of the audit: ``main`` and ``split`` differ only where
    :data:`NAMED_CHANGES` says. Mutation: a ``split`` row that changes one
    more field -> red."""
    golden = _golden()
    main, split = golden["main"], golden["split"]
    assert sorted(main) == sorted(split)
    for key in main:
        before, after = _view(main[key]), _view(split[key])
        differ = frozenset(f for f in before if before[f] != after[f])
        assert differ == NAMED_CHANGES.get(key, frozenset()), (key, differ)


def test_the_core_stays_and_comes_first() -> None:
    """Each turn that held ``studio`` on main holds the core after the split,
    and the core comes before studio-more. Two places of MAX_PACKS go to the
    two Studio packs where both fit. One case is out: there studio-more was
    used later than the core, so the core leaves by the order of use."""
    golden = _golden()
    for key, before in golden["main"].items():
        after = golden["split"][key]["packs"]
        assert len(after) <= tp.MAX_PACKS, key
        if key.startswith("studio_with_a_sticky_pair_the_follower_used_later|"):
            assert after[0] == "studio-more" and "studio" not in after, key
            continue
        if "studio" in before["packs"]:
            assert "studio" in after, key
        if "studio-more" in after:
            assert after.index("studio") < after.index("studio-more"), key
        assert len(after) <= tp.MAX_PACKS, key


@pytest.mark.parametrize(
    ("case", "before", "after"),
    [
        ("studio_alone", 80, 80),
        ("studio_with_email", 110, 110),
        ("studio_with_crm_records_and_calendar_day", 112, 99),
        ("studio_with_email_and_crm_records", 99, 109),
        ("studio_with_projects_reminders", 99, 99),
        ("studio_with_two_sticky_packs", 98, 86),
        ("studio_with_a_sticky_pair_and_two_word_packs", 112, 99),
    ],
)
def test_the_tool_counts_of_the_audit(case: str, before: int, after: int) -> None:
    """The numbers of the block TS-1b, to read at a glance. 57 + 31 + 10 + 13
    - 1 = 110 beside the email pack."""
    golden = _golden()
    for held in HELD:
        key = f"{case}|held={held}"
        assert golden["main"][key]["tools"] == before, key
        assert golden["split"][key]["tools"] == after, key


def test_a_sticky_pair_keeps_the_core_and_moves_studio_more_out() -> None:
    """Review P2-a. Both Studio packs are sticky, with the same use and the
    same answer to "named now". Only the first load differs, and the core was
    loaded first. The cap must move studio-more out, not the core. Before the
    fix the core left: 102 tools with studio-more, crm-records and calendar-day.
    Mutation: drop ``_follower_first`` from the cap of ``choose`` -> red."""
    for held in HELD:
        row = _golden()["split"][f"studio_with_a_sticky_pair_and_two_word_packs|held={held}"]
        assert row == matrix()[f"studio_with_a_sticky_pair_and_two_word_packs|held={held}"]
        assert row["packs"] == ["studio", "crm-records", "calendar-day"], held
        assert row["evicted"] == ["studio-more"] and row["tools"] == 99, held


def test_the_twin_rule_does_not_fire_without_a_tie() -> None:
    """Review round 2, the over-fire fence. studio-more was used in turn 9 and
    the core in turn 8, so the key does not tie. The core leaves, as the least
    recently used, and studio-more stays. Mutation: drop the tie condition of
    ``_follower_first`` -> studio-more leaves -> red."""
    for held in HELD:
        key = f"studio_with_a_sticky_pair_the_follower_used_later|held={held}"
        row = matrix()[key]
        assert row == _golden()["split"][key]
        assert row["packs"] == ["studio-more", "crm-records", "calendar-day"], held
        assert row["evicted"] == ["studio"] and row["tools"] == 102, held


def test_the_other_follow_packs_keep_the_order_of_main() -> None:
    """Review round 2, the examples (a) and (b). projects-reports and
    calendar-day follow a parent, but they have their own words or no surface,
    so they are no twin. The cap gives the result of main, which the golden
    holds from the code of 3de2813f0: the parent leaves. Mutation: drop the
    same-surface-and-words condition (``_twin_follower``) -> the follow pack
    leaves first -> red."""
    assert [s for s in tp.FOLLOWS if tp._twin_follower(s)] == ["studio-more"]
    golden = _golden()
    got = matrix()
    for key in golden["main"]:
        if key.split("|", 1)[0] not in OTHER_SURFACE_CASES:
            continue
        before = golden["main"][key]
        assert got[key]["packs"] == before["packs"], key
        assert got[key]["evicted"] == before["evicted"], key
        assert got[key]["tools"] == before["tools"], key


def test_studio_workflows_stays_out_of_follows_and_out_of_the_matrix() -> None:
    """Review P3. The dark workflow pack is in no entry of FOLLOWS, and no
    Studio turn of the matrix loads it unless its words name a workflow write
    and the owner turned the pack on. The allow list of the matrix comes from
    FOLLOWS. Mutation: add ``"studio-workflows": "studio"`` to FOLLOWS -> red."""
    assert "studio-workflows" not in tp.FOLLOWS
    assert "studio-workflows" not in _allowed(False)
    for key, row in matrix().items():
        case = key.split("|", 1)[0]
        if "studio-workflows" in row["packs"]:
            assert case in WORKFLOW_WORD_CASES and CASES[case].get("workflows"), key


# -- TOF-1b: the layer "skill" has its own rows, outside the golden -----------

#: TOF-1b. A skill that brings the pack of its chat tool
#: (``ai_studio_rows/TOF-1b.md``). These cases are NOT in :data:`CASES`, so
#: the golden file and its rows do not change. Each keeps its row inline
#: (:data:`SKILL_LAYER_ROWS`).
SKILL_LAYER_CASES: dict[str, dict[str, Any]] = {
    # No surface and no pack word: the skill brings crm-records, which takes
    # its place last with the layer "skill". 57 + 13 - 1 = 69 tools.
    "a_skill_brings_crm_records": {
        "surface": "",
        "words": "run my lead digest",
        "skills": (_skill("skill-lead-digest", "create_lead"),),
        "skill_packs": {"skill-lead-digest": ("crm-records",)},
    },
    # The Studio surface loads both Studio packs, so the skill and its pack need
    # 4 places. The skill is refused with "pack_cap", and the code packs stay.
    "studio_with_a_skill_that_brings_a_pack": {
        "words": "make the form blue and run my lead digest",
        "skills": (_skill("skill-lead-digest", "create_lead"),),
        "skill_packs": {"skill-lead-digest": ("crm-records",)},
    },
}

#: The pinned fields of each row of :data:`SKILL_LAYER_CASES`, the same for
#: each value of ``HELD_CARD_WRITES_ORGS``. The loader offer is not pinned, so
#: a new pack elsewhere does not move these rows.
SKILL_LAYER_ROWS: dict[str, dict[str, Any]] = {
    "a_skill_brings_crm_records": {
        "packs": ["crm-records"], "layers": {"crm-records": "skill"}, "skipped": [],
        "evicted": [], "skills": ["skill-lead-digest"], "skills_skipped": [], "tools": 69,
    },
    "studio_with_a_skill_that_brings_a_pack": {
        "packs": ["studio", "studio-more"],
        "layers": {"studio": "surface", "studio-more": "surface"}, "skipped": [],
        "evicted": [], "skills": [], "skills_skipped": [], "tools": 80,
    },
}


def test_a_skill_pack_has_its_own_layer_outside_the_golden() -> None:
    """TOF-1b, decisions 3 to 5. A pack that a skill brings has the layer
    "skill" and comes last. Where the places are full, the code packs of the
    turn stay as the matrix gives them with no map. The golden rows do not
    move, because these cases are not in :data:`CASES`. Mutation: give the
    brought pack the layer "words", or move a Studio pack out for it -> red."""
    got = matrix(SKILL_LAYER_CASES)
    assert not set(SKILL_LAYER_CASES) & set(CASES)
    assert sorted(got) == sorted(f"{n}|held={h}" for n in SKILL_LAYER_CASES for h in HELD)
    for key, row in got.items():
        want = SKILL_LAYER_ROWS[key.split("|", 1)[0]]
        assert {field: row[field] for field in want} == want, key
    for held in HELD:
        assert "crm-records" not in got[f"a_skill_brings_crm_records|held={held}"]["offered"]
    # The refused skill: the same row as with no map, and one "pack_cap".
    studio = SKILL_LAYER_CASES["studio_with_a_skill_that_brings_a_pack"]
    plain = _row({key: value for key, value in studio.items() if key != "skill_packs"})
    assert plain["packs"] == got["studio_with_a_skill_that_brings_a_pack|held=off"]["packs"]
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), TURN, (), str(studio["words"]),
                       True, surface="studio", skills=tuple(studio["skills"]))
    choice = tp.choose(turn, allowed=_allowed(False), base_tools=BASE, specialists=SPECIALISTS,
                       skill_packs=studio["skill_packs"])
    assert [(s.slug, why, packs) for s, why, packs in choice.skills_refused] == [
        ("skill-lead-digest", "pack_cap", ("crm-records",))]


# -- The sticky state of a chat from before the split (item 7) ---------------


def test_a_sticky_studio_pack_from_before_the_split_loads_the_core() -> None:
    """A chat with a sticky ``studio`` from before the merge, on no Studio
    surface, gets the core of 10 tools only. Its history may name a tool that
    left, for up to DECAY_TURNS turns. This is the accepted cost, with no
    shim. Mutation: a shim that counts studio-more with a sticky studio ->
    red."""
    raw = {"~turn": "10", "studio": json.dumps({"first": [3, 0], "used": 8})}
    sticky = tp._sticky_of(raw)
    assert [entry.slug for entry in sticky] == ["studio"]
    turn = tp.PackTurn(
        ORG, SESSION, tp._member_part(MEMBER), TURN, sticky, "now make it blue", True
    )
    choice = tp.choose(turn, allowed=_allowed(False), base_tools=BASE, specialists=SPECIALISTS)
    assert choice.slugs == ("studio",) and choice.layers == {"studio": "sticky"}
    assert len(sf.SKILL_FAMILIES["studio"]["tools"]) == 10
    assert tp.DECAY_TURNS == 8


def test_an_old_reader_skips_the_new_sticky_field(monkeypatch) -> None:
    """A process from before the split reads a state hash that a new process
    wrote: the field ``studio-more`` names no pack there, so ``_sticky_of``
    skips it and keeps ``studio``. Mutation: delete the skip of a field that
    is no pack -> red."""
    raw = {
        "~turn": "10",
        "studio": json.dumps({"first": [3, 0], "used": 9}),
        "studio-more": json.dumps({"first": [3, 1], "used": 9}),
    }
    assert [entry.slug for entry in tp._sticky_of(raw)] == ["studio", "studio-more"]
    # The registry of the old code. A new dict, so the order of PACKS is the
    # same again after the test.
    old = {slug: found for slug, found in tp.PACKS.items() if slug != "studio-more"}
    monkeypatch.setattr(tp, "PACKS", old)
    assert tp.pack("studio-more") is None
    assert [entry.slug for entry in tp._sticky_of(raw)] == ["studio"]


if __name__ == "__main__":  # write the split column again; main never changes
    if "--write-split" not in sys.argv:
        raise SystemExit("usage: python tests/unit/test_studio_split_matrix.py --write-split")
    golden = _golden()
    golden["split"] = matrix()
    GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN}")
