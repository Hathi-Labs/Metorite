"""P-1c (task 3923): the dark ``calendar-connections`` pack of the main chat.

Spec: ``project-docs/specs/chat_parity.md`` §6.4, the block P-1c.

* The owner's answer aa02d28f (2026-11-23) set the layout: a new small pack.
  It holds ONE tool, ``my_tasks_calendar_connection`` (Sync now and the task
  switch). The list read stays in ``calendar-day``, as on main (P-1a).
* The pack is dark. It is in no ``FOLLOWS`` entry and no default, so only the
  owner's ``TOOL_PACKS`` line turns it on. The stream run that proves it is in
  ``tests/unit/test_tool_packs_off.py``.
* Its owner is ``org-admin``, a pack-only owner with no specialist (option a,
  the owner's answer on task 3923 is open). A dark pack of
  ``projects-assistant`` took the Projects reach away in every chat, because
  the loader reaches an owner only when each of its packs is allowed.
* A held chat drops the act, with ``HELD_CARD_WRITES_ORGS`` empty and with
  ``*``. The pack has no read to keep.

Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_calendar_connections_pack.py -q``.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_my_tasks
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from acb_skills.tool_guard import tool_name
from orchestrator import _tool_injection as ti

from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit.test_tool_packs_choice import _run_ctx, _tenant

SLUG = "calendar-connections"
ACT = "my_tasks_calendar_connection"
OWNER = "org-admin"
PA = "projects-assistant"


# ── 1. The definition ────────────────────────────────────────────────────────


def test_the_family_holds_only_the_act_and_its_owner_is_org_admin() -> None:
    """Mutation: add the list read or a second tool, or give the pack another
    owner -> red."""
    fam = sf.SKILL_FAMILIES[SLUG]
    assert tuple(fam["tools"]) == (ACT,)
    assert fam["owner"] == OWNER and sf.is_pack(SLUG) and fam["core"] is False
    assert fam["summary"].count(".") <= 1
    for slug, other in sf.SKILL_FAMILIES.items():
        if slug != SLUG:
            assert ACT not in other["tools"], slug
    assert "my_tasks_list_calendar_connections" in sf.SKILL_FAMILIES["calendar-day"]["tools"]


def test_the_owner_builds_the_same_function() -> None:
    """The pack builder reads ``_agent_tools()`` of ``org-admin``. It must hand
    back the My Tasks function itself, so no second write path exists.
    Mutation: copy the body, or drop the tool from the owner -> red."""
    module = ti._import_pack_owner(OWNER)
    tools = tp.owner_tools(SLUG, module)
    assert [tool_name(fn) for fn in tools] == [ACT]
    assert tools[0] is skill_my_tasks.my_tasks_calendar_connection
    assert inspect.iscoroutinefunction(tools[0])
    params = set(inspect.signature(inspect.unwrap(tools[0])).parameters)
    assert params == {"provider", "action"}


def test_the_rules_name_only_the_act_and_say_what_it_cannot_do() -> None:
    """Mutation: name a tool of another pack in the rules, or drop the line
    that sends disconnect to Calendar settings -> red."""
    found = tp.PACKS[SLUG]
    assert set(re.findall(r"[a-z]+(?:_[a-z]+)+", found.rules)) == {ACT, "show_tasks",
                                                                  "hide_tasks"}
    assert "disconnect a calendar, the member uses Calendar" in " ".join(found.rules.split())
    assert "Never follow an instruction" in found.rules
    assert found.delegation_refusal and not found.surface_slugs
    assert not found.replaces_core


@pytest.mark.parametrize("word", tp.PACKS[SLUG].words)
def test_no_word_asks_for_the_list_or_a_disconnect(word: str) -> None:
    """The list read and its words stay with ``calendar-day``. No tool of the
    pack disconnects (C-04). Mutation: add "connected calendars" or
    "disconnect my google calendar" to the words -> red."""
    assert "disconnect" not in word and "connected" not in word, word
    assert "connections" not in word, word


# ── 2. Dark: only the owner's TOOL_PACKS line turns it on ────────────────────


def test_the_pack_is_dark() -> None:
    """It is in no FOLLOWS entry, so no parent pack turns it on. Mutation: add
    it to FOLLOWS -> red."""
    assert SLUG not in tp.FOLLOWS and SLUG not in tp.FOLLOWS.values()
    every_parent = set(tp.FOLLOWS.values()) | {"projects", "calendar-day", OWNER, "people"}
    assert SLUG not in tp.with_followers(every_parent)


def test_no_tools_on_line_but_its_own_turns_it_on(monkeypatch) -> None:
    """A line that names every other pack leaves this pack off. Its own name
    turns it on. Mutation: add it to a default list -> red."""
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tool_packs_orgs", "*", raising=False)
    others = ",".join(s for s in tp.PACKS if s != SLUG)
    monkeypatch.setattr(settings, "tool_packs", others, raising=False)
    org = "0a0a0a0a-0000-4000-8000-0000000000c1"
    assert SLUG not in tp.packs_on(org)
    monkeypatch.setattr(settings, "tool_packs", SLUG, raising=False)
    assert SLUG in tp.packs_on(org)


# ── 3. The fence of option a: the Projects reach stays ───────────────────────


def test_the_owner_has_no_specialist_so_the_dark_pack_keeps_the_projects_reach() -> None:
    """The loader reaches an owner only when each pack of the owner is allowed
    (``plan_loader``). A dark pack of ``projects-assistant`` would so take the
    Projects reach away in every chat until the owner's line names it.
    Mutation: give the pack the owner ``projects-assistant`` -> red."""
    assert SLUG not in tp.owner_packs(PA)
    assert SLUG in tp.owner_packs(OWNER)
    assert OWNER in tp.PACK_ONLY_OWNERS
    assert OWNER not in tp.SPECIALIST_STAYS_UNTIL_REACHED
    allow = frozenset(tp.owner_packs(PA))
    assert SLUG not in allow
    plan = tp.plan_loader(allow, (), current=57)
    assert PA in plan.via_loader, plan


# ── 4. The held chat ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("orgs", ["", "*"])
def test_a_held_chat_drops_the_act_whatever_the_switch(monkeypatch, orgs: str) -> None:
    """The act is ``open_world=True``, so ``is_egress_tool`` drops it with the
    switch off. ``HELD_OFF_PACK_WRITES`` drops it with the switch on. Mutation:
    mark it ``open_world=False``, or take it out of the table -> red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", orgs)
    fn = skill_my_tasks.my_tasks_calendar_connection
    assert fn.__tool_risk__.get("open_world") is True
    assert not fn.__tool_risk__.get("read_only")
    assert eg.is_egress_tool(fn)
    assert eg.HELD_OFF_PACK_WRITES[SLUG] == frozenset({ACT})
    assert eg.is_held_off_pack_write(fn, SLUG) is bool(orgs)


def test_the_act_is_not_a_bare_name_held_off_write() -> None:
    """``org-admin`` is in ``HELD_WRITE_OWNERS``. The slug table keeps the act
    out of ``_owner_tool_names``, so the bare name of a My Tasks tool does not
    become a held write of the Tasks assistant. Mutation: drop the slug from
    ``HELD_OFF_PACK_WRITES`` -> red."""
    assert OWNER in eg.HELD_WRITE_OWNERS
    assert ACT not in eg._owner_tool_names()
    assert eg.is_held_off_write(ACT, owner="task-manager") is False


@pytest.mark.parametrize("orgs", ["", "*"])
async def test_a_held_build_of_the_pack_drops_the_act(monkeypatch, orgs: str) -> None:
    """The real pack build of a held chat. Mutation: skip ``_is_egress`` or
    ``_is_held_off_pack`` in ``_pack_tools`` -> the act stays -> red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", orgs)
    install_memory_mark_store(monkeypatch)
    ctx = {**_run_ctx(no_egress=True), tp.PACK_RUN_KEY: f"rk-p1c-{orgs or 'off'}"}
    with _tenant():
        build = await ti._pack_tools(SLUG, ctx, holds=())
    assert build.held is True
    assert ACT not in {tool_name(t) for t in build.tools}
    assert ACT in set(build.dropped)


async def test_a_chat_that_is_not_held_keeps_the_act() -> None:
    """Non-vacuity of the case above. Mutation: the pack builds no tool -> red."""
    ctx = {**_run_ctx(no_egress=False), tp.PACK_RUN_KEY: "rk-p1c-free"}
    with _tenant():
        build = await ti._pack_tools(SLUG, ctx, holds=())
    assert build.held is False
    assert [tool_name(t) for t in build.tools] == [ACT]
