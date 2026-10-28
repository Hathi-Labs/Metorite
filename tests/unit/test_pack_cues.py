"""WS-67 OA-8f: the cues that tell the model WHEN to load a pack.

``project-docs/specs/one_assistant_context.md``, the note "As built, OA-8f".
The live run of OA-8e (2026-10-28) showed four misses:

* ``pm_new_tasks``: the Projects page loaded the core pack, and the model
  called ``create_task`` three times (three cards). It never loaded
  ``projects-bulk`` for ``create_tasks`` (one card).
* ``pm_miss``, ``pm_capacity_open``: the model loaded no pack, or loaded the
  core pack when planning was the need, and then handed the work to the
  Projects assistant.
* ``crm_miss``: the model answered "who did we speak to last" with the 12 CRM
  reads that the base already holds. That is no product miss. The check of
  the harness expected ``crm-records``, which holds the writes and the
  timeline. The ``when`` line of the pack now says so.

Each text is pinned here, and each test names its mutation (R7).

Run: ``uv run pytest tests/unit/test_pack_cues.py -q``.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp

#: The line of each pack, in the member's terms. A change here is a reviewed
#: change of what the model reads in every call of ``load_skill_tools``.
WHEN = {
    "email": "Load for mail: read, search, reply, send, drafts and my inbox.",
    "email-setup": "Load to set up mail: rules, writing style, senders and categories.",
    "crm-records": (
        "Load to change deals, leads and contacts, or to read a record's timeline. "
        "Plain reads of the pipeline need no load."),
    "projects": "Load for tasks, projects and my work: find, create, update, assign and complete.",
    "projects-plan": "Load for team load, capacity, reports, a timeline or a plan from a goal.",
    "projects-structure": "Load to change a project's statuses, fields, tags, access or saved views.",
    "projects-bulk": "Load for two or more tasks at once, and to archive, move or delete.",
}


def test_each_pack_has_its_when_line() -> None:
    """Mutation: empty the ``when`` of one pack → its catalog line falls back to
    the long summary → red. Mutation: change a word of a line → red."""
    assert set(WHEN) == set(tp.PACKS)
    for slug, line in WHEN.items():
        assert tp.PACKS[slug].when == line, slug


def test_the_catalog_line_is_the_when_line_and_is_short() -> None:
    """The catalog rides in every call, so a line stays short. Mutation: the
    catalog uses the summary again → the line is 100+ characters longer → red."""
    for slug, line in WHEN.items():
        entry = tp.catalog([slug])
        assert entry.startswith(f"- {slug}: {line}"), entry
        summary = str(sf.SKILL_FAMILIES[slug]["summary"])
        assert summary not in entry
        assert len(line) <= 130, (slug, len(line))
        assert line.startswith("Load "), slug


def test_the_lines_use_the_members_words() -> None:
    """The cue is in the member's terms: deals, leads, tasks, projects, mail.
    Mutation: write a tool name in a line → red."""
    text = " ".join(WHEN.values()).lower()
    for word in ("deals", "leads", "pipeline", "tasks", "projects", "my work", "mail", "inbox"):
        assert word in text, word
    names = set().union(*(set(sf.SKILL_FAMILIES[s]["tools"]) for s in WHEN))
    for name in sorted(n for n in names if "_" in n):  # a plain word may be a tool too
        assert re.search(rf"\b{re.escape(name)}\b", text) is None, name


def test_the_load_tool_description_carries_the_lines() -> None:
    """The tool the model reads holds the catalog, so the cue rides in each call.
    Mutation: format the description without the catalog → red."""
    tool = tp.load_tool(tuple(WHEN))
    description = str(getattr(tool, "description", ""))
    for slug, line in WHEN.items():
        assert f"- {slug}: {line}" in description, slug


def test_the_crm_line_says_that_plain_reads_need_no_load() -> None:
    """The base holds 12 CRM reads, and a question such as "Who at Orbit did we
    speak to last?" needs none of the pack. The line says so, so a harness
    check that expects ``crm-records`` there is a check problem. Mutation: drop
    the sentence → the model loads a pack for a read it already can do → red."""
    assert "Plain reads of the pipeline need no load." in tp.PACKS["crm-records"].when
    base_reads = {"crm_list_records", "crm_get_record", "crm_list_pipelines", "crm_aggregate"}
    pack_tools = set(sf.SKILL_FAMILIES["crm-records"]["tools"])
    assert not base_reads & pack_tools, "the base reads are not in the pack"


# ── The bulk rule ───────────────────────────────────────────────────────────

BULK_RULE = (
    "- For two or more new tasks at once, call load_skill_tools with the pack\n"
    "  projects-bulk, then call create_tasks once. That shows one card for the set.\n"
    "  Do not call create_task once for each task.\n"
)


def test_the_core_projects_rules_send_many_tasks_to_the_bulk_pack() -> None:
    """The core rules say: for two or more tasks at once, load ``projects-bulk``
    and use ``create_tasks`` (one card). Mutation: drop the bullet, or name
    ``create_task`` for the set → red."""
    assert BULK_RULE in tp._PROJECTS_RULES
    rules = tp.PACKS["projects"].rules
    assert "projects-bulk" in rules and "create_tasks" in rules and "one card" in rules
    assert "create_tasks" in sf.SKILL_FAMILIES["projects-bulk"]["tools"]
    assert "create_tasks" not in sf.SKILL_FAMILIES["projects"]["tools"]
    assert "create_task" in sf.SKILL_FAMILIES["projects"]["tools"]


def test_the_bulk_rule_reaches_the_persona_of_a_run_that_holds_the_core_pack() -> None:
    """The rule rides in the persona, not only in the registry. Mutation: build
    the persona without the core rules → red."""
    persona = tp.persona_for("Base persona.\n", [("projects", False)], {})
    assert BULK_RULE in persona
