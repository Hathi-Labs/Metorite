"""WS-67 OA-8b: the Projects tool packs (dark behind ``TOOL_PACKS``).

``project-docs/specs/one_assistant_context.md`` §4.3.3 and §4.5 (OA-8b). The
Projects assistant's 121 own tools become four packs of the one assistant:
``projects`` (the core pack), ``projects-plan``, ``projects-structure`` and
``projects-bulk``. Each case names, in its docstring, the mutation that turns
it red (R7). What is locked:

1. The four families equal the owner's tools, none is in two, none holds more
   than 35, and the core pack holds 23.
2. The cap: the core pack fits beside the 31 email tools at the measured base.
3. The shape: a pack holds the owner's ``FunctionTool`` objects (the class
   ``_StrictTool``), so ``additionalProperties: false`` and the unknown
   argument answer stay. The owner's cached tools are never changed.
4. The surface: Projects, Tasks and Calendar load the core pack.
5. The words load a pack as whole words and clash with no email or CRM turn.
6. The persona, the refusal and the rules of the owner without a bullet.
7. A covered organization loads no Projects pack.
8. A batch tool shows one card through the real assembly, and R5.
9. Layers (b) and (c) build the same tools.

Run: ``uv run pytest tests/unit/test_projects_packs.py -q``.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
import skill_projects
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from agent_framework import FunctionTool
from orchestrator import _tool_injection as ti

from evals.one_assistant_measure.scenarios import SCENARIOS
from tests.unit import test_loader_replaces_handoff as L
from tests.unit import test_projects_create_tasks as tc
from tests.unit._egress_mark_store import install_memory_mark_store
from tests.unit._projects_agent_fakes import approve, fake_gateway
from tests.unit.test_load_skill_tools import _call, _dummies, _in_run, _loader, _record
from tests.unit.test_tool_packs_choice import (
    CONFIG,
    EA,
    EVERY,
    MEMBER,
    ORG,
    SESSION,
    _agents_mod,
    _apply,
    _bound,
    _bullets,
    _measured_base,
    _name,
    _names,
    _run_ctx,
    _tenant,
    _turn,
)

REPO = Path(__file__).resolve().parents[2]
PA = "projects-assistant"
PROJECTS_DIR = REPO / "apps" / "agents" / "agent-projects"
SLUGS = ("projects", "projects-plan", "projects-structure", "projects-bulk", "projects-reminders",
         "projects-reports", "projects-authority")
#: After PJP-2 (project_planning.md section 3.3): the 11 report tools left plan, the 10 inbox,
#: area and notification tools left structure for reminders. PJP-4 added update_tasks to
#: bulk, ALN-2a added the two decision tools to reminders, and ALN-4 added the authority pack.
#: WS-39 TPL-1a added the three template tools to structure. WS-39 LP-1a added
#: the three update tools to authority, and SP-8a added set_epic_contract there.
SIZES = {"projects": 23, "projects-plan": 25, "projects-structure": 27, "projects-bulk": 22,
         "projects-reminders": 19, "projects-reports": 11, "projects-authority": 7}
#: CAL-1a: the owner also holds the ``calendar-day`` pack. It is no Projects family
#: (its eight tools are not in ``skill_projects.__all__``), so ``SLUGS`` stays the seven.
OWNER_SLUGS = (*SLUGS, "calendar-day")
UUID = tc.UUID

#: The nine tools that make ONE card for a batch, raised inside the tool body.
BATCH_TOOLS = {
    "projects-bulk": ("bulk_update", "create_tasks", "file_tasks", "merge_tasks", "update_tasks"),
    "projects-structure": ("create_tags", "create_types"),
    "projects-plan": ("link_tasks_batch", "propose_plan"),
}


def _family(slug: str) -> tuple[str, ...]:
    return tuple(sf.SKILL_FAMILIES[slug]["tools"])


def _own_tool_scope() -> set[str]:
    cfg = json.loads((PROJECTS_DIR / "config.json").read_text(encoding="utf-8"))
    return set(cfg["own_tool_scope"])


async def _build(slug: str, *, no_egress: bool = False) -> tuple[Any, str]:
    """The pack *slug* as the guarded builder makes it for one run."""
    run_key = "rk-oa8b"
    ctx = {**_run_ctx(no_egress=no_egress), tp.PACK_RUN_KEY: run_key}
    with _tenant():
        return await ti._pack_tools(slug, ctx, holds=()), run_key


@pytest.fixture
def store(monkeypatch) -> Any:
    """A fresh in-memory tenant Redis for the marks and the sticky packs."""
    return install_memory_mark_store(monkeypatch)


@pytest.fixture
def orchestrator(monkeypatch) -> list[Any]:
    """The real orchestrator agent, as its agent repo builds it."""
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    from orchestrator.agents import build_orchestrator_agent

    return [build_orchestrator_agent(with_history=False)]


# ── 1. The four families ────────────────────────────────────────────────────


def test_the_four_families_equal_the_owners_tools() -> None:
    """The union is ``skill_projects.__all__`` and ``own_tool_scope``, and no
    tool is in two packs. Mutation: drop a tool from a family, add a name the
    owner lacks, or copy one tool into two families -> red."""
    union = [name for slug in SLUGS for name in _family(slug)]
    # SP-12 added get_plan (127). WS-39 LP-1a added the three update tools (130),
    # WS-39 TPL-1a the three template tools (133), and SP-8a set_epic_contract (134).
    assert len(union) == 134 == len(set(union)), "no tool is in two packs"
    assert set(union) == set(skill_projects.__all__) == _own_tool_scope()
    for slug in SLUGS:
        assert sf.SKILL_FAMILIES[slug]["owner"] == PA and sf.is_pack(slug)
        assert len(_family(slug)) == SIZES[slug], slug
        assert len(_family(slug)) <= tp.MAX_PACK_TOOLS
        assert list(_family(slug)) == sorted(_family(slug)), "the family is sorted"
    assert tp.owner_packs(PA) == OWNER_SLUGS


def test_no_projects_tool_is_in_an_email_or_crm_pack() -> None:
    """Mutation: copy a Projects tool name into another owner's pack -> red."""
    mine = {name for slug in SLUGS for name in _family(slug)}
    for slug in ("email", "email-setup", "crm-records"):
        assert not mine & set(_family(slug)), slug


def test_the_core_pack_fits_the_core_budget() -> None:
    """Mutation: a 24th tool in the core pack -> it no longer fits beside the
    email pack at the measured base (see the cap test) -> red there. The
    count is pinned here too."""
    assert len(_family("projects")) == 23


@pytest.fixture(autouse=True)
def _facts_tools_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap numbers here are those of an org on ``CRM_QUESTIONS_ORGS``, which
    holds the 3 CRM facts tools (55 tools). A unit test has no such org, and
    the estimate then drops them (OA-8f), so this keeps the old boundary."""
    monkeypatch.setattr(ti, "_facts_removed", ti._facts_withheld)


# ── 2. The cap ──────────────────────────────────────────────────────────────


async def test_the_core_pack_loads_beside_the_email_pack_at_the_measured_base(
    orchestrator, monkeypatch,
) -> None:
    """The base is measured: 55 tools with the four specialists. The request
    also carries the loader (1). 55 + 1 + 31 + 23 = 110, exactly the cap.
    Mutation: a 24th core tool -> ``choose`` drops the Projects pack and keeps
    email -> red. Mutation: raise a pack above 35 -> red in the registry."""
    base = _measured_base(orchestrator) + 1
    n_email = len(_family("email"))
    n_core = len(_family("projects"))
    assert base + n_email + n_core <= tp.MAX_REQUEST_TOOLS, (base, n_email, n_core)
    words = "email me my overdue tasks"
    fits = tp.choose(_turn(words=words), allowed=EVERY, base_tools=base)
    assert fits.slugs == ("email", "projects") and fits.skipped == (), fits
    # One tool more is past the cap: the last new pack that replaces its
    # specialist leaves, and the email pack stays.
    monkeypatch.setitem(
        sf.SKILL_FAMILIES["projects"], "tools", (*_family("projects"), "one_more"))
    over = tp.choose(_turn(words=words), allowed=EVERY, base_tools=base)
    assert over.slugs == ("email",) and over.skipped == ("projects",), over


def test_a_large_pack_never_joins_the_email_pack_beside_that_base() -> None:
    """``projects-plan`` (35) beside ``email`` (31) is 121 at a base of 56.
    Mutation: drop the tool count from the cap -> both load -> red."""
    choice = tp.choose(
        _turn(words="email me the burndown"), allowed=EVERY, base_tools=56)
    assert choice.slugs == ("email",) and choice.skipped == ("projects-plan",)


# ── 3. The shape of a pack tool ─────────────────────────────────────────────


async def test_each_built_pack_holds_exactly_its_familys_names(store) -> None:
    """The builder skipped every tool that is no coroutine function, and the
    owner builds ``FunctionTool`` objects, so each pack came out empty.
    Mutation: drop the ``FunctionTool`` case of ``_pack_func`` -> every pack
    is empty -> red. Mutation: drop a name from a family -> red."""
    for slug in SLUGS:
        build, _key = await _build(slug)
        assert build is not None and build.held is False, slug
        assert [_name(t) for t in build.tools] == list(_family(slug)), slug
        assert build.dropped == () and build.skipped == (), slug


async def test_a_held_pack_drops_exactly_the_held_off_writes(store) -> None:
    """RX-1 (D169). No Projects tool is an egress tool, and parity with the
    owner's own held run stands: a held pack drops the class C writes that a
    held chat does not hold, and each other tool stays. Mutation: a Projects
    tool that says ``open_world=True`` -> the held pack drops it -> red."""
    for slug in SLUGS:
        build, _key = await _build(slug, no_egress=True)
        assert build is not None and build.held is True, slug
        off = [name for name in _family(slug) if eg.is_held_off_write(name)]
        assert list(build.dropped) == off, slug
        assert [_name(t) for t in build.tools] == [n for n in _family(slug) if n not in off], slug
        assert not [n for n in _family(slug) if ti._is_egress(n)], slug


async def test_a_built_tool_keeps_the_owners_class_and_its_strict_schema(store) -> None:
    """Mutation: ``_pack_call`` rebuilds the tool from its function, as
    ``normalize_tools`` would -> the class is ``FunctionTool``, the schema
    loses ``additionalProperties: false`` and the unknown argument is no
    longer answered by name -> red."""
    from orchestrator._tool_injection import PACK_OWNER_ATTR

    for slug in SLUGS:
        build, _key = await _build(slug)
        for tool in build.tools:
            assert type(tool).__name__ == "_StrictTool", _name(tool)
            assert isinstance(tool, FunctionTool)
            assert tool.parameters().get("additionalProperties") is False, _name(tool)
            assert tool.input_model.model_config.get("extra") == "forbid", _name(tool)
            assert getattr(tool.func, PACK_OWNER_ATTR, None) == PA
            assert getattr(tool.func, "__cc_gated__", False) is True


async def test_the_unknown_argument_answer_survives_the_pack(store) -> None:
    """A call with an invented argument is answered by name and reaches no
    gateway. Mutation: a plain tool for the ``_StrictTool`` -> MAF drops the
    key in silence and the call runs -> red."""
    build, key = await _build("projects")
    tool = next(t for t in build.tools if _name(t) == "task_detail")
    with _tenant(), _bound(**_run_ctx(), **{tp.PACK_RUN_KEY: key}):
        out = await tool.invoke(arguments={"task_id": UUID, "task_ids": UUID})
    text = "\n".join(str(getattr(c, "text", "") or "") for c in out) if not isinstance(
        out, str) else out
    assert "has no argument 'task_ids'" in text, text


async def test_the_pack_copies_the_tool_and_never_changes_the_cached_one(store) -> None:
    """Every run builds the packs again, and the owner's 121 tools are cached.
    Mutation: ``_pack_call`` sets ``func`` on the owner's tool itself -> the
    cached tool holds the wrapper of the first run, and a second run
    wraps a wrapper -> red."""
    source = ti._pack_owner_module(PA)
    cached = ti._owner_pack_tools("projects", PA, source)
    before = [(t, t.func) for t in cached]
    first, _k1 = await _build("projects")
    second, _k2 = await _build("projects")
    assert all(a is not b for a, b in zip(first.tools, cached, strict=True))
    assert all(a is not b for a, b in zip(first.tools, second.tools, strict=True))
    again = ti._owner_pack_tools("projects", PA, source)
    assert [t for t, _f in before] == again, "the owner's tools are built once"
    assert all(t.func is f for t, f in before), "no cached tool was changed"
    assert not any(getattr(t.func, "__cc_gated__", False) for t in again)


def test_the_owner_tools_are_cached_until_the_owner_changes() -> None:
    """121 models for each run cost time. Mutation: no cache -> a second call
    returns other objects -> red. Mutation: a cache that ignores the module
    -> a patched owner keeps the old tools -> red."""
    import types

    source = ti._pack_owner_module(PA)
    one = ti._owner_pack_tools("projects-plan", PA, source)
    assert ti._owner_pack_tools("projects-plan", PA, source)[0] is one[0]
    stub = types.SimpleNamespace(_agent_tools=lambda: [])
    assert ti._owner_pack_tools("projects-plan", PA, stub) == []
    # The stub replaced the entry, and the real owner builds again.
    assert ti._owner_pack_tools("projects-plan", PA, source)[0] is not one[0]


def test_as_agent_tools_keeps_a_function_tool_as_it_is() -> None:
    """Mutation: ``_as_agent_tools`` rebuilds a ``FunctionTool`` from its
    function -> the class and the strict model are lost -> red."""
    source = ti._pack_owner_module(PA)
    strict = ti._owner_pack_tools("projects", PA, source)[0]

    async def plain(x: str = "") -> str:
        return x

    out = ti._as_agent_tools([strict, plain])
    assert out[0] is strict and type(out[0]).__name__ == "_StrictTool"
    assert isinstance(out[1], FunctionTool) and out[1].name == "plain"


def test_pack_call_wraps_the_function_and_keeps_the_class() -> None:
    """Mutation: swap the class for a plain ``FunctionTool`` -> red."""
    source = ti._pack_owner_module(PA)
    strict = ti._owner_pack_tools("projects", PA, source)[0]
    wrapped = ti._pack_call(strict, PA, False, "rk", "projects")
    assert wrapped is not strict and type(wrapped) is type(strict)
    assert wrapped.name == strict.name and wrapped.input_model is strict.input_model
    assert wrapped.func is not strict.func and wrapped.func.__cc_gated__ is True
    assert strict.func is not wrapped.func and not getattr(
        strict.func, "__cc_gated__", False)


def test_the_catalog_prices_every_projects_pack_from_its_tools() -> None:
    """``_pack_tools_by_name`` read ``__name__``, so a ``FunctionTool`` had no
    name and priced nothing. Mutation: read ``__name__`` again -> the 121
    Projects tools are absent -> red."""
    import gateway.routes.integrations_skills as sk

    priced = sk._pack_tools_by_name()
    mine = {name for slug in SLUGS for name in _family(slug)}
    assert mine <= set(priced), sorted(mine - set(priced))
    for slug in SLUGS:
        cost = sum(len(json.dumps(sf.tool_json_schema(priced[n]))) for n in _family(slug))
        assert cost > 1000, slug


# ── 4. The surface ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("surface", ["projects", "tasks", "calendar"])
def test_projects_tasks_and_calendar_load_the_core_pack_and_nothing_else(surface: str) -> None:
    """Mutation: the surface names ``projects`` only -> /tasks loads nothing
    -> red. Mutation: the surface loads each pack of the owner -> red."""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), "", True, surface=surface)
    choice = tp.choose(turn, allowed=EVERY, base_tools=40)
    assert choice.slugs == ("projects",) and choice.layers == {"projects": "surface"}


def test_the_surface_slugs_and_the_clean_value() -> None:
    """Mutation: ``clean_surface`` reads the first slug only, or ``surfaces()``
    drops the tuple -> red."""
    assert tp.PACKS["projects"].surface == ("projects", "tasks", "calendar")
    assert tp.PACKS["projects"].surface_slugs == ("projects", "tasks", "calendar")
    assert tp.PACKS["email"].surface_slugs == ("email",)
    assert tp.PACKS["email-setup"].surface_slugs == ()
    # STU-G1: the studio pack names the Studio surface.
    assert tp.surfaces() == {"email", "crm", "projects", "tasks", "calendar", "studio"}
    for slug in ("projects", "tasks", "calendar"):
        assert tp.clean_surface(slug) == slug and tp.clean_surface(f" {slug.upper()} ") == slug
    for bad in ("notes", "projects/x", "", None, 4, "tasks" + "x" * 40):
        assert tp.clean_surface(bad) == ""


def test_only_the_core_pack_names_a_surface() -> None:
    """Mutation: a second Projects pack names a surface -> a /tasks turn loads
    two packs at once -> red."""
    named = [slug for slug in SLUGS if tp.PACKS[slug].surface_slugs]
    assert named == ["projects"]


# ── 5. The words ────────────────────────────────────────────────────────────

#: Turns that load a Projects pack, and the packs.
LOADS = [
    ("What tasks are overdue?", ("projects",)),
    ("Show me my work", ("projects",)),
    ("Create a task for Priya", ("projects",)),
    ("Add a subtask to this", ("projects",)),
    ("What is in the backlog?", ("projects",)),
    ("Plan the next sprint", ("projects",)),
    ("Show the project board", ("projects",)),
    ("Assign it to Anil", ("projects",)),
    ("Show the burndown", ("projects-plan",)),
    ("Who has spare team capacity?", ("projects-plan",)),
    ("Add a custom field", ("projects-structure",)),
    ("Show the saved views", ("projects-structure",)),
    ("Archive tasks that are done", ("projects", "projects-bulk")),
    ("Merge tasks that repeat", ("projects", "projects-bulk")),
]
#: Turns that load no Projects pack: a mail, a deal, a Notion page, a project
#: with no task in it, and the bare words that the audit left out.
LOADS_NOTHING = [
    "Draft the PRD in Notion",
    "fix the CI pipeline",
    "lead the standup",
    "What stage is the project in?",
    "Close the deal on the contract",
    "Show my calendar",
    "Reply to the note from Anil",
    "Delete the folder on disk",
    "What is the plan for the report?",
    "What is the weather in Pune?",
    "Show my unread WhatsApp chats",
]
#: The OA-8a CRM turns that load the CRM pack (``test_tool_packs_choice.py``).
CRM_TURNS = [
    "How is the CRM looking?",
    "Any new leads from Zoho this week?",
    "What is in the sales pipeline?",
    "Show deals in the deal pipeline",
    "Create a lead for Acme",
    "Please convert the lead to a deal",
    "log a call with Ravi",
    "I need the lost reason",
    "Which pipeline stage is Acme in?",
]
#: Turns for the Projects packs that no email word may load.
PROJECTS_TURNS = [turn for turn, _packs in LOADS] + [
    "List my tasks due this week", "Move the task to Done",
]


@pytest.mark.parametrize(("words", "want"), LOADS)
def test_the_words_load_the_projects_packs_as_whole_words(words: str, want: tuple) -> None:
    """Mutation: drop "tasks" from the core words -> the first turns load
    nothing -> red. Mutation: match inside a word -> red."""
    got = tp.choose(_turn(words=words), allowed=EVERY, base_tools=40).slugs
    assert tuple(s for s in got if s.startswith("projects")) == want


@pytest.mark.parametrize("words", LOADS_NOTHING)
def test_the_bare_words_load_no_projects_pack(words: str) -> None:
    """Mutation: add a bare "project", "calendar", "plan", "report", "delete",
    "reply" or "note" to a word list -> a turn about a mail or a Notion page
    loads the pack -> red."""
    got = tp.choose(_turn(words=words), allowed=EVERY, base_tools=40).slugs
    assert not [s for s in got if s.startswith("projects")], got


def test_the_projects_words_leave_out_the_bare_words() -> None:
    """Mutation: a bare word joins a list -> red."""
    bare = {"project", "projects", "calendar", "plan", "report", "delete", "reply",
            "note", "deal", "pipeline", "email", "mail", "draft", "status", "view",
            "nudge", "undo", "revert", "senders", "sender"}
    for slug in SLUGS:
        words = set(tp.PACKS[slug].words)
        assert words and not words & bare, (slug, words & bare)
        assert all(w == w.strip().lower() for w in words)
    assert {"task", "tasks", "overdue", "my work"} <= set(tp.PACKS["projects"].words)


def test_no_projects_word_matches_an_email_or_crm_turn() -> None:
    """The 18 member turns of the OA-5 email measure and the OA-8a CRM turns
    load no Projects pack. Mutation: a word that those turns use ("invoice"
    or "pipeline") joins a list -> red."""
    turns = [t.words for s in SCENARIOS for t in s.turns]
    assert len(turns) == 18, "non-vacuity: the 18 turns of OA-5"
    for words in [*turns, *CRM_TURNS]:
        for slug in SLUGS:
            assert tp.matched_layer(slug, words) is None, (slug, words)


def test_no_email_word_matches_a_projects_turn() -> None:
    """Mutation: an email word that a task turn uses (for example "draft" or
    "assign") joins the email list -> red."""
    for words in PROJECTS_TURNS:
        for slug in ("email", "email-setup", "crm-records"):
            assert tp.matched_layer(slug, words) is None, (slug, words)


def test_make_a_task_from_this_email_loads_both_packs() -> None:
    """Mutation: a Projects word is "email"-blind, or an email word is
    "task"-blind -> one pack is missing -> red."""
    choice = tp.choose(
        _turn(words="Make a task from this email"), allowed=EVERY, base_tools=40)
    assert choice.slugs == ("email", "projects")


# ── 6. The persona, the refusal, the rules ──────────────────────────────────


def test_the_persona_lines_are_one_constant_that_a_pack_can_take_out() -> None:
    """P2-1. The two lines that send task work to ``projects_assistant`` are
    ``PROJECTS_DELEGATION_BULLETS``, once in the persona, so a Projects pack
    that removes the specialist takes them out by one exact swap. The flag-off
    persona is unchanged (``test_tool_packs_off.py``). Mutation: leave the
    owner out of ``PACK_DELEGATION_BULLETS`` -> the persona keeps a line that
    names a tool that has left -> red."""
    mod = _agents_mod()
    persona = mod._orchestrator_instructions(False)
    assert persona.count(mod.PROJECTS_DELEGATION_BULLETS) == 1
    assert _bullets()[PA] == mod.PROJECTS_DELEGATION_BULLETS
    assert PA not in tp.NO_PERSONA_BULLETS
    left = tp.persona_for(persona, [(s, False) for s in OWNER_SLUGS], _bullets())
    assert mod.PROJECTS_DELEGATION_BULLETS not in left
    assert tp.persona_for(persona, [], _bullets()) == persona


def test_the_projects_persona_swaps_nothing_and_adds_the_rules_in_load_order() -> None:
    """Mutation: raise for an owner with no bullets, or swap a line out of
    the persona -> red."""
    persona = _agents_mod()._orchestrator_instructions(False)
    text = tp.persona_for(
        persona, [("projects", False), ("projects-plan", False)], _bullets())
    assert text.startswith(persona.rstrip("\n") + "\n\n")
    assert text.count("PROJECTS TOOLS.") == 1 and text.count("PROJECTS PLANNING TOOLS.") == 1
    assert text.index("PROJECTS TOOLS.") < text.index("PROJECTS PLANNING TOOLS.")
    assert _agents_mod().EMAIL_DELEGATION_BULLETS in text
    assert "projects_assistant" in text, "the shared persona lines stay"
    assert tp.persona_for(persona, [], _bullets()) == persona


def test_with_some_projects_packs_the_rules_keep_a_path_to_the_owner() -> None:
    """A request holds three packs and the owner has four, so the run never
    holds each Projects pack at layer (a), and the hand-off stays open. The
    rules then swap the "no agent exists" line for the route. Mutation: drop
    the ``handoff_swap`` -> the rules say no agent exists while the hand-off
    is open -> red."""
    rules = tp._rules_block(tp.PACKS["projects"], False, ("projects",))
    assert tp._PROJECTS_NO_AGENT_LINE not in rules
    assert tp._PROJECTS_ROUTE_LINE in rules
    every = tp._rules_block(tp.PACKS["projects"], False, OWNER_SLUGS)
    assert tp._PROJECTS_NO_AGENT_LINE in every and tp._PROJECTS_ROUTE_LINE not in every


def test_every_projects_pack_says_no_agent_exists_and_refuses_the_hand_off() -> None:
    """Mutation: a Projects pack with no ``delegation_refusal`` -> the
    specialist stays beside the pack and the model keeps handing off -> red."""
    for slug in SLUGS:
        found = tp.PACKS[slug]
        assert "No Projects agent exists" in found.delegation_refusal, slug
        assert found.rules.count(tp._PROJECTS_NO_AGENT_LINE) == 1, slug
        assert found.handoff_swap[0] == tp._PROJECTS_NO_AGENT_LINE, slug
    turn = _turn(words="")
    some = tp.PackRun.of(turn, tp.Choice((), {}, {}), [("projects", False)])
    assert some.replaced == frozenset() and some.refusal(PA) is None
    allp = tp.PackRun.of(turn, tp.Choice((), {}, {}), [(s, False) for s in OWNER_SLUGS])
    assert allp.refusal(PA) == tp.PACKS["projects"].delegation_refusal
    assert allp.replaced == {PA}


def test_the_core_rules_name_the_other_packs_and_no_tool_of_them() -> None:
    """The core rules send the work of the other three packs to the loader by
    slug. They name no tool of those packs (the registry fence holds that for
    every pack). Mutation: drop a slug -> the model has no route -> red."""
    rules = tp.rules_for("projects")
    for slug in SLUGS[1:]:
        assert slug in rules, slug
    assert "load_skill_tools" in rules
    # Fix round 2: plan and structure cannot fit beside the core pack, so the
    # rules send that work to call_agent, and only bulk to the loader.
    assert 'call call_agent with agent_name "projects-assistant"' in rules
    assert "When load_skill_tools offers the pack projects-bulk, load it" in rules
    assert "call load_skill_tools with the pack that you need" not in rules.lower()
    for slug in SLUGS[1:]:
        assert tp.PACKS[slug].rules.count("Use them yourself.") == 1
    # OA-8g: was 3300; MT-1228: was 3400; PJP-2 and ALN-4: was 3450
    assert max(len(tp.rules_for(slug)) for slug in SLUGS) < 3500


# ── 7. A covered organization ───────────────────────────────────────────────


@pytest.fixture
def _broker(monkeypatch):
    """Patch the sandbox broker's cover answer for the Projects assistant."""
    from orchestrator import sandbox_broker as sb

    def set_cover(covered: bool) -> None:
        monkeypatch.setattr(sb, "covers", lambda _agent, _org: covered)
        monkeypatch.setattr(sb, "maf_coding_scope_allows", lambda _target, _org: False)

    return set_cover


async def test_a_covered_organization_loads_no_projects_pack(store, _broker) -> None:
    """Mutation: drop the covered check from the pre-filter slot -> a covered
    org gets the 23 tools -> red. Mutation: the check reads the org from a
    request field -> not testable here, the source scan below holds that."""
    _broker(True)
    build, _key = await _build("projects")
    assert build is not None and build.tools == () and build.held is True
    _broker(False)
    build, _key = await _build("projects")
    assert [_name(t) for t in build.tools] == list(_family("projects"))


async def test_a_covered_run_keeps_the_specialist_and_the_persona(
    store, orchestrator, _broker,
) -> None:
    """Mutation: the covered pack loads in an apply -> the specialist leaves
    and the rules join the persona -> red."""
    _broker(True)
    persona = orchestrator[0].default_options["instructions"]
    assert "projects_assistant" in _names(orchestrator), "non-vacuity"
    with _tenant():
        applied = await _apply(orchestrator, "Show my overdue tasks", allowed=EVERY)
    assert applied is None or applied.builds == ()
    assert "projects_assistant" in _names(orchestrator)
    assert orchestrator[0].default_options["instructions"] == persona


def test_the_covered_check_reads_the_run_binding_not_a_request_field() -> None:
    """R5. The filter calls ``_run_covered`` with the agent name and nothing
    from the caller. Mutation: pass an org from an argument -> red."""
    source = inspect.getsource(ti._drop_pack_covered)
    assert '_run_covered("projects-assistant")' in source
    assert ti._PACK_OWNER_FILTERS["projects-assistant"] is ti._drop_pack_covered
    assert ti._PACK_OWNER_FILTERS["crm-assistant"] is ti._drop_pack_mirror_writes


# ── 8. A batch tool, and R5 ─────────────────────────────────────────────────


async def test_each_batch_tool_is_marked_gated_in_its_pack(store) -> None:
    """The nine batch tools raise their card inside the tool body, and the
    pack wrapper is marked, so the assembly gates none of them a second time.
    Mutation: drop ``__cc_gated__`` from ``_pack_call`` -> red."""
    seen = 0
    for slug, names in BATCH_TOOLS.items():
        build, _key = await _build(slug)
        held = {_name(t): t for t in build.tools}
        for name in names:
            assert held[name].func.__cc_gated__ is True, name
            seen += 1
    assert seen == 9


async def test_a_batch_tool_shows_one_card_through_the_real_assembly(
    store, orchestrator, monkeypatch,
) -> None:
    """``create_tasks`` raises its one card with a row for each task. The pack
    wrapper carries ``__cc_gated__``, so the assembly makes ONE permission
    decision and the member sees ONE card with the counts. Mutation: drop
    ``__cc_gated__`` from the pack wrapper -> ``_gate_own_maf_tools`` gates it
    again -> two decisions -> red."""
    decisions: list[str] = []
    real_gate = ti._permission_gate

    def counting_gate(name: str, *args: Any, **kwargs: Any) -> Any:
        decisions.append(name)
        return real_gate(name, *args, **kwargs)

    monkeypatch.setattr(ti, "_permission_gate", counting_gate)
    asked = approve(monkeypatch)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    calls = fake_gateway(monkeypatch, tc._gateway())
    with _tenant():
        applied = await _apply(
            orchestrator, "Make these tasks, and archive tasks too", allowed=EVERY)
        assert applied is not None and applied.record.slugs == ("projects", "projects-bulk")
        ti._inject_agent_tools(
            orchestrator, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
            agent_config=CONFIG, no_egress=False,
        )
        tool = next(t for t in orchestrator[0].default_options["tools"]
                    if _name(t) == "create_tasks")
        assert type(tool).__name__ == "_StrictTool"
        with _bound(**_run_ctx(), **applied.context()):
            answer = await tool.func(UUID, tc.THREE)
    assert len(asked) == 1 and len(asked[0]["rows"]) == 3, asked
    assert decisions == ["create_tasks"], decisions
    assert len(tc._posts(calls)) == 3
    assert str(answer).startswith("Created 3 tasks"), answer
    names = set(_names(orchestrator))
    assert "projects_assistant" in names, "two of four packs: the specialist stays"
    assert names >= set(_family("projects")) | set(_family("projects-bulk"))
    assert len([n for n in _names(orchestrator)]) == len(names), "no tool twice"


async def test_a_pack_call_with_no_acting_user_reaches_no_gateway(store, monkeypatch) -> None:
    """R5. The member comes from the bound run, never from a tool argument.
    With no acting user the client refuses before any request. Mutation: a
    tool falls back to a default member -> a call reaches the gateway -> red."""
    build, key = await _build("projects")
    tool = next(t for t in build.tools if _name(t) == "vocabulary")
    calls = fake_gateway(monkeypatch, {}, user=None)
    with _tenant(), _bound(**_run_ctx(), **{tp.PACK_RUN_KEY: key}):
        answer = await tool.func(UUID)
    assert calls == [], calls
    assert "No acting user" in str(answer), answer


async def test_no_projects_tool_takes_an_organization_or_a_member_argument(store) -> None:
    """R5. Mutation: add an ``organization_id`` or a ``user_email`` parameter
    to a pack tool -> red. The acting member and the tenant come from the
    bound run."""
    identity = {"organization_id", "org_id", "organization", "org", "tenant", "tenant_id",
                "member", "member_email", "user", "user_email", "acting_user", "actor",
                "owner_email", "as_user", "impersonate", "created_by"}
    seen = 0
    for slug in SLUGS:
        build, _key = await _build(slug)
        for tool in build.tools:
            declared = set(tool.input_model.model_fields)
            assert not declared & identity, (_name(tool), sorted(declared & identity))
            seen += 1
    assert seen == 134  # SP-12, LP-1a, TPL-1a, and SP-8a set_epic_contract


async def test_a_pack_call_outside_its_run_refuses(store) -> None:
    """A pack tool acts only in the run that built it (OA-7c condition 3).
    Mutation: drop the run check from ``_pack_call`` -> red."""
    build, _key = await _build("projects")
    tool = next(t for t in build.tools if _name(t) == "vocabulary")
    with _tenant(), _bound(**_run_ctx(), **{tp.PACK_RUN_KEY: "another-run"}):
        answer = await tool.func(UUID)
    assert answer == ti.PACK_CALL_REFUSED.format(tool="vocabulary")


def test_a_held_projects_tool_is_no_egress_tool() -> None:
    """Mutation: a Projects tool loses ``open_world: False`` -> it is egress
    -> the held pack drops it -> red here and in the held test above."""
    source = ti._pack_owner_module(PA)
    for slug in SLUGS:
        for tool in ti._owner_pack_tools(slug, PA, source):
            assert not eg.is_egress_tool(tool), _name(tool)


# ── 9. Layers (b) and (c) build the same FunctionTool packs ─────────────────


async def test_the_loader_adds_a_projects_pack_of_strict_function_tools(store) -> None:
    """Layer (b): ``load_skill_tools`` builds the pack with the same guarded
    builder, and ``add_tools`` takes the owner's ``_StrictTool`` objects.
    Mutation: the build skips a ``FunctionTool`` (``_pack_func``) -> the pack
    is empty and the load answers "not available" -> red."""
    record = _record()
    loader = _loader(record, offered=("projects-plan",))
    live = _dummies(5)
    with _in_run(loader):
        answer, passed = await _call(loader, "projects-plan", live)
    assert passed is False
    assert answer.startswith("Loaded the Projects planning tools (25 tools)"), answer
    assert "PROJECTS PLANNING TOOLS." in answer
    added = live[5:]
    assert [_name(t) for t in added] == list(_family("projects-plan"))
    assert all(type(t).__name__ == "_StrictTool" for t in added)
    assert record.holds() == ("projects-plan",)


async def test_layer_c_adds_the_core_pack_before_the_first_request(store) -> None:
    """Layer (c) loads through the same builder in ``before_run``. Mutation: a
    ``normalize_tools`` that rebuilds the tool -> the class is lost -> red."""
    import types

    class _Ctx:
        def __init__(self) -> None:
            self.tools: list[Any] = []
            self.instructions: list[str] = []

        def extend_tools(self, _source: str, tools: Any) -> None:
            self.tools.extend(tools)

        def extend_instructions(self, _source: str, text: Any) -> None:
            self.instructions.append(str(text))

    record = _record()
    loader = _loader(record, offered=("projects",))
    assert loader.set_early(["projects"]) == ("projects",)
    agent = types.SimpleNamespace(default_options={"tools": _dummies(5)})
    ctx = _Ctx()
    with _in_run(loader):
        await loader.load_early(agent, ctx)
    assert [_name(t) for t in ctx.tools] == list(_family("projects"))
    assert all(type(t).__name__ == "_StrictTool" for t in ctx.tools)
    assert any("PROJECTS TOOLS." in text for text in ctx.instructions)


# ── 10. The loader reaches the Projects owner (OA-7i, `plan_loader`) ────────

PROJ = frozenset(OWNER_SLUGS)
NO_WORDS = "Tell Anil that we pay on Friday"


@pytest.fixture
def agents(monkeypatch) -> list[Any]:
    return L._fresh()


def _specialist(agent: Any) -> bool:
    return "projects_assistant" in L._names(agent.default_options["tools"])


async def test_projects_is_reached_with_no_pack_held_and_the_texts_agree(
    store, agents, monkeypatch,
) -> None:
    """With no pack held, the specialist tool leaves, the persona stays as it
    was, and a hand-off answers a refusal that names ``load_skill_tools`` and
    the Projects packs. The rules say that no Projects agent exists.
    Mutation: leave the owner out of ``NO_PERSONA_BULLETS`` -> ``_available``
    refuses it, the specialist stays -> red."""
    ran = L._fake_run_agent(monkeypatch)
    persona = agents[0].default_options["instructions"]
    assert _specialist(agents[0]), "non-vacuity"
    applied = await L._apply(agents, NO_WORDS, allowed=PROJ)
    assert applied is not None and applied.builds == ()
    assert applied.record.via_loader == {PA} and PA in applied.record.replaced
    assert not _specialist(agents[0])
    bullet = _agents_mod().PROJECTS_DELEGATION_BULLETS
    assert persona.count(bullet) == 1, "non-vacuity"
    assert agents[0].default_options["instructions"] == persona.replace(bullet, "", 1)
    provider = L._provider(agents[0])
    assert provider is not None and provider.loader.offered == OWNER_SLUGS
    with L._in_run(applied):
        refused = await L.agent_tools.call_agent(PA, "Show my overdue tasks")
        background = await L.agent_tools.call_agent_background(PA, "Sync")
        await L.asyncio.sleep(0.05)
    assert refused == tp.LOADER_HANDOFF_REFUSED.format(owner=PA, packs=", ".join(OWNER_SLUGS))
    assert "load_skill_tools" in refused and "projects-plan" in refused
    for slug in SLUGS:
        assert "No Projects agent exists" in tp.PACKS[slug].rules
    assert ran == [PA] and background != refused


def test_the_core_rules_name_the_loader_while_the_owner_is_reached() -> None:
    """The ``loader_route`` line replaces the open-hand-off route line when
    the owner is in ``via_loader``. (A held Projects pack leaves no room for
    the largest pack, so the plan reaches the owner with no pack held, and
    this text is the rule for the case that a load adds.) Mutation: drop
    ``loader_route`` from the core pack -> the rules send the model to an
    owner that is refused -> red."""
    for slug in SLUGS:
        block = tp._rules_block(tp.PACKS[slug], False, (slug,), {PA})
        assert tp._PROJECTS_LOAD_LINE in block, slug
        assert tp._PROJECTS_ROUTE_LINE not in block, slug


async def test_projects_is_reached_while_email_is_held(store, agents) -> None:
    """PJP-2 (project_planning.md section 3.3): ``email`` is held (base 87), and
    the largest Projects pack is 24 tools, so 87 - 1 + 24 = 110 fits. The owner
    is reached and the specialist leaves. Before PJP-2 plan (35 tools) did not
    fit, and the hand-off stayed. Mutation: grow a pack past 24 tools -> the
    owner is not reached -> red."""
    applied = await L._apply(agents, "check my inbox", allowed=PROJ | {"email"})
    assert applied is not None and applied.record.slugs == ("email",)
    assert PA in applied.record.via_loader
    assert not _specialist(agents[0])


def test_projects_is_reached_when_one_pack_fits_and_the_rest_stay_on_offer() -> None:
    """PJP-2 (project_planning.md §3.3): the Projects owner is reached when
    each of its packs is allowed and one of them fits. At a current count of
    90, the 24-tool plan pack does not fit (90 - 1 + 24 = 113), but the
    reports pack does. The plan pack stays on offer, so its load fails on the
    cap and opens the hand-off (LOAD_NO_PATH). Mutation: require every pack
    to fit -> the owner is not reached -> red. Mutation: offer only what fits
    -> a load of the plan pack answers "unknown" and has no path -> red."""
    plan = tp.plan_loader(PROJ, ("email",), current=90, removable={PA: 1})
    assert PA in plan.via_loader
    assert "projects-plan" in plan.offered


def test_projects_is_not_reached_when_no_pack_fits() -> None:
    """The cap still binds: when no Projects pack fits, the specialist stays.
    Mutation: drop the cap from ``plan_loader`` -> the owner is reached -> red."""
    smallest = min(tp._tool_count(s) for s in PROJ if s != "projects")
    current = tp.MAX_REQUEST_TOOLS - smallest + 2
    plan = tp.plan_loader(PROJ, ("email",), current=current, removable={PA: 1})
    assert PA not in plan.via_loader


async def test_a_refused_projects_load_opens_the_hand_off(
    store, agents, monkeypatch,
) -> None:
    """A true count over the cap refuses the load. The answer names
    ``call_agent``, and the hand-off runs. Mutation: drop the ``via_failed``
    mark -> the refusal and the hand-off point at each other -> red."""
    ran = L._fake_run_agent(monkeypatch)
    applied = await L._apply(agents, NO_WORDS, allowed=PROJ)
    assert applied is not None and applied.record.via_loader == {PA}
    provider = L._provider(agents[0])
    assert provider is not None
    with L._in_run(applied):
        assert (await L.agent_tools.call_agent(PA, "x")).startswith("Not run.")
        answer = await L._call(provider.loader, "projects-plan", L._dummies(105))
        out = await L.agent_tools.call_agent(PA, "Show the burndown")
    assert answer == tp.LOAD_NO_PATH.format(
        label=tp._label("projects-plan"), slug="projects-plan", owner=PA)
    assert ran == [PA] and "ran" in out and applied.record.via_failed == {PA}


async def test_in_a_covered_org_the_owner_is_not_reached_and_no_pack_loads(
    store, agents, _broker,
) -> None:
    """The loader cannot serve a covered org, so the specialist and its
    hand-off stay. Mutation: drop the covered check from ``_available`` ->
    the specialist leaves and nothing replaces it -> red."""
    _broker(True)
    applied = await L._apply(agents, NO_WORDS, allowed=PROJ)
    assert _specialist(agents[0])
    assert applied is None or (PA not in applied.record.via_loader and applied.builds == ())
    covered = await L._apply(agents, "Show my overdue tasks", allowed=PROJ)
    assert _specialist(agents[0])
    assert covered is None or covered.builds == ()


# ── 11. OA-8b fix round 1: routing at the MEASURED base ─────────────────────

CORE_WORDS = set(tp.PACKS["projects"].words)
COMPOUND = [
    (slug, word) for slug in SLUGS[1:] for word in tp.PACKS[slug].words
    if tp.matched_layer("projects", word) is not None
]


def _real_base(orchestrator: list[Any]) -> int:
    """The measured base of the real orchestrator, plus the loader (1)."""
    return _measured_base(orchestrator) + 1


def _surface_turn(words: str, surface: str = "") -> tp.PackTurn:
    return tp.PackTurn(
        ORG, SESSION, tp._member_part(MEMBER), 1, (), words, True, surface=surface)


def _reached_by_load(turn: tp.PackTurn, choice: tp.Choice, base: int) -> bool:
    """Task 1747: the plan of the run, as ``_apply_tool_packs`` asks it. The
    packs that a load can move out (``PackRun.spare``) count as free, so the
    Projects owner is reached through them."""
    run = tp.PackRun.of(turn, choice, [(slug, False) for slug in choice.slugs])
    total = base + sum(len(_family(slug)) for slug in choice.slugs)
    spare = {slug: len(_family(slug)) for slug in run.spare()}
    plan = tp.plan_loader(EVERY, choice.slugs, current=total, removable={PA: 1},
                          spare=spare)
    return PA in plan.via_loader


def test_there_are_compound_words_that_also_match_the_core_word() -> None:
    """Non-vacuity of the cases below: the plan, structure and bulk lists hold
    words that the core word "task" or "tasks" matches too."""
    assert len([c for c in COMPOUND if c[0] != "projects-bulk"]) >= 10, COMPOUND
    assert {slug for slug, _w in COMPOUND} >= {"projects-plan", "projects-structure"}


@pytest.mark.parametrize(("slug", "word"), COMPOUND)
def test_a_compound_word_loads_its_specific_pack_at_the_measured_base(
    slug: str, word: str, orchestrator,
) -> None:
    """The word also matches the core word. After PJP-2 the two fit at the
    real base (56), but together they leave no room for a third Projects
    pack. Task 1747 (D169 item 8 as amended, option A): both packs came in
    only by the words, so a load can move either out. So both stay, and the
    loader reaches the owner through them. Before task 1747 the core pack
    stepped aside here. Mutation: drop the words packs from
    ``PackRun.spare`` -> the owner is not reached -> red. Mutation: step
    aside a named pack -> the core pack leaves -> red."""
    base = _real_base(orchestrator)
    n = len(_family("projects")) + len(_family(slug))
    turn = _surface_turn(f"Show the {word}")
    choice = tp.choose(turn, allowed=EVERY, base_tools=base)
    # PJP-2: every pack is 24 tools or fewer, so each fits beside the core pack at 56.
    assert base + n <= tp.MAX_REQUEST_TOOLS, "no Projects pack is too big for the core"
    assert choice.slugs == ("projects", slug), (word, choice)
    assert choice.skipped == () and choice.evicted == (), (word, choice)
    assert _reached_by_load(turn, choice, base), (word, choice)


def test_a_capacity_question_on_the_projects_page_loads_the_planning_pack(
    orchestrator,
) -> None:
    """Surface ``projects`` loads the core pack. "team capacity" matches the
    planning pack. Both cannot fit at the real base, so the planning pack
    stays. PJP-2: both fit now. Task 1747: the surface named the core pack,
    so it never steps aside. The planning pack came in only by the words,
    so a load can move it out (D169 item 8 as amended), and the loader
    reaches the owner through it. Mutation: drop the planning words -> only
    the core pack loads and the capacity tools are missing -> red."""
    base = _real_base(orchestrator)
    for surface in ("projects", "tasks", "calendar"):
        turn = _surface_turn("Who has spare team capacity?", surface)
        choice = tp.choose(turn, allowed=EVERY, base_tools=base)
        assert choice.slugs == ("projects", "projects-plan"), (surface, choice)
        assert _reached_by_load(turn, choice, base), (surface, choice)
    only = tp.choose(_surface_turn("", "projects"), allowed=EVERY, base_tools=base)
    assert only.slugs == ("projects",), "the surface alone still loads the core pack"


def test_a_specific_pack_leaves_the_core_pack_where_both_fit() -> None:
    """Where the two fit, the core pack stays. Mutation: drop the cap test
    from the replacement -> the core pack leaves at a small base -> red."""
    choice = tp.choose(
        _surface_turn("Show the stuck tasks"), allowed=EVERY, base_tools=20)
    assert choice.slugs == ("projects", "projects-plan")


async def _apply_on(agents: list[Any], words: str, surface: str, allowed: frozenset[str]) -> Any:
    from orchestrator.agents import PACK_DELEGATION_BULLETS

    with L._tenant():
        turn = await tp.begin_turn(ORG, SESSION, MEMBER, words, surface)
        return await ti._apply_tool_packs(
            agents, turn, L._run_ctx(), allowed=allowed, agent_name="orchestrator",
            tool_scope=list(CONFIG["tool_scope"]), agent_config=CONFIG,
            bullets=PACK_DELEGATION_BULLETS, offer_loader=True)


async def test_an_access_request_on_the_projects_page_keeps_the_specialist(
    store, agents,
) -> None:
    """"Grant Priya access to this project" on /projects loads the core pack.
    PJP-2: the packs fit now, but here the allow list lacks
    ``projects-reports``, as when an admin turns the reports family off.
    ``plan_loader`` needs EVERY pack of the owner in the list, so the owner is
    not reached. The specialist tool stays, the persona keeps its line, and
    the rules route the work to ``call_agent``. (Production's ``TOOL_PACKS``
    gets the reports pack from ``projects-plan``, ``tool_packs.FOLLOWS``.)
    Mutation: drop ``_specialist_leaves`` from ``PackRun.of`` -> the
    specialist leaves and the access work has no tool and no path -> red."""
    persona = agents[0].default_options["instructions"]
    applied = await _apply_on(
        agents, "Grant Priya access to this project", "projects",
        PROJ - {"projects-reports"})
    assert applied is not None and applied.record.slugs == ("projects",)
    assert applied.record.replaced == frozenset() and applied.record.via_loader == frozenset()
    assert _specialist(agents[0]), "the specialist stays"
    text = agents[0].default_options["instructions"]
    assert _agents_mod().PROJECTS_DELEGATION_BULLETS in text
    assert text.startswith(persona.rstrip("\n"))
    assert tp._PROJECTS_ROUTE_LINE in text and tp._PROJECTS_NO_AGENT_LINE not in text
    assert "call_agent" in tp._PROJECTS_ROUTE_LINE and PA in tp._PROJECTS_ROUTE_LINE
    with L._in_run(applied):
        assert tp.delegation_refusal(PA) is None


async def test_an_access_request_reaches_the_owner_once_the_flag_line_has_every_pack(
    store, agents,
) -> None:
    """The same request with the full list (``packs_on`` adds the packs of
    ``tool_packs.FOLLOWS`` wherever their parents are on): the core pack
    loads, the owner is reached through the loader, and the specialist leaves.
    Mutation: drop ``via_loader`` from the plan -> the specialist stays and the
    model hands off -> red."""
    applied = await _apply_on(
        agents, "Grant Priya access to this project", "projects", PROJ)
    assert applied is not None and applied.record.slugs == ("projects",)
    assert applied.record.via_loader == {PA}
    assert not _specialist(agents[0]), "the hand-off leaves"


@pytest.mark.parametrize(("parent", "follower", "words"), [
    ("projects-plan", "projects-reports", "Show the status report"),
    ("projects", "projects-authority", "Show the authority page of Website Relaunch"),
])
async def test_a_following_pack_is_off_where_an_admin_turned_its_parent_off(
    parent: str, follower: str, words: str, store, agents, monkeypatch,
) -> None:
    """PJP-2 fix round 3 (P3-1). Each pack of ``tool_packs.FOLLOWS`` follows
    its parent. An admin turned the parent off for the orchestrator, so the
    run loads neither pack and the loader offers neither, though the words ask
    for the work of the following pack. Mutation: drop ``with_followers`` from
    the admin's list in ``_apply_tool_packs`` -> the following pack loads ->
    red."""
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: frozenset())
    control = await _apply_on(agents, words, "projects", PROJ)
    assert control is not None and follower in control.record.slugs, "non-vacuity"
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: frozenset({parent}))
    fresh = L._fresh()
    applied = await _apply_on(fresh, words, "projects", PROJ)
    off = {parent, follower}
    assert applied is not None and not off & set(applied.record.slugs), applied.record.slugs
    provider = L._provider(fresh[0])
    assert provider is None or not off & set(provider.loader.offered)


# ── PJP-2 fence (project_planning.md section 10) ─────────────────────────────

#: Production's TOOL_PACKS on 2026-11-02 (owner-only, work_plan.md section 6 D4).
#: ``packs_on`` adds ``projects-reports`` and ``projects-authority`` to it,
#: because the list names their parents (``tool_packs.FOLLOWS``, owner
#: decisions of 2026-11-04).
PRODUCTION_PACKS = frozenset({
    "email", "email-setup", "crm-records", "projects", "projects-plan",
    "projects-structure", "projects-bulk", "projects-reminders",
})


def test_each_other_projects_pack_fits_beside_the_core_pack_and_the_owner_is_reached() -> None:
    """R7 fence of PJP-2. At the real base (56) with the core pack held (79),
    each other Projects pack is 31 tools or fewer (79 - 1 + 31 = 109), and
    ``plan_loader`` puts the owner in ``via_loader``. Mutation: add tools to a
    pack until it holds 33 -> red."""
    core = len(_family("projects"))
    assert 56 + core == 79
    for slug in SLUGS[1:]:
        assert len(_family(slug)) <= 31, slug
        assert 79 - 1 + len(_family(slug)) <= tp.MAX_REQUEST_TOOLS, slug
    plan = tp.plan_loader(
        tp.with_followers(PRODUCTION_PACKS), ("projects",), current=79,
        removable={PA: 1})
    assert PA in plan.via_loader


def test_the_owner_is_not_reached_while_a_pack_of_it_is_off() -> None:
    """``plan_loader`` needs EVERY pack of the owner in the allow list. An allow
    list that lacks ``projects-reports`` and ``projects-authority`` keeps the
    hand-off. Production's list does not lack them: ``packs_on`` adds them
    from their parents (``test_tool_packs_registry.py``). Mutation: let
    ``plan_loader`` skip a missing pack -> the owner is reached with a pack the
    run cannot load -> red."""
    plan = tp.plan_loader(PRODUCTION_PACKS, ("projects",), current=79, removable={PA: 1})
    assert PA not in plan.via_loader


async def test_the_specialist_leaves_when_the_run_holds_every_projects_pack() -> None:
    """The per-owner rule: the specialist leaves for the owner that the loader
    reaches or when every pack is held. Email keeps the older rule. Mutation:
    apply the Projects rule to email -> ``email`` alone keeps the specialist
    -> red."""
    turn = _turn(words="")
    empty = tp.Choice((), {}, {})
    assert tp.PackRun.of(turn, empty, [(s, False) for s in OWNER_SLUGS]).replaced == {PA}
    assert tp.PackRun.of(turn, empty, [("projects", False)], via_loader={PA}).replaced == {PA}
    assert tp.PackRun.of(turn, empty, [("projects", False)]).replaced == frozenset()
    assert tp.PackRun.of(turn, empty, [("email", False)]).replaced == {EA}
    assert {PA} == tp.SPECIALIST_STAYS_UNTIL_REACHED


def test_the_catalog_offers_no_projects_pack_to_an_owner_that_cannot_load() -> None:
    """P3. A covered org (or a missing module) makes ``available`` false, and
    the loader offers none of the owner's packs. Mutation: drop the filter ->
    four packs that always answer "not available" -> red."""
    plan = tp.plan_loader(PROJ, (), current=56, removable={PA: 1}, available=lambda _o: False)
    assert plan.offered == () and plan.via_loader == frozenset()
    ok = tp.plan_loader(PROJ, (), current=56, removable={PA: 1}, available=lambda _o: True)
    assert ok.offered == OWNER_SLUGS and ok.via_loader == {PA}


async def test_a_covered_org_is_offered_no_projects_pack(store, agents, _broker) -> None:
    """The same, through the run. Mutation: drop the filter -> the catalog of
    ``load_skill_tools`` lists the four packs -> red."""
    _broker(True)
    await L._apply(agents, NO_WORDS, allowed=PROJ)
    provider = L._provider(agents[0])
    assert provider is None or not set(provider.loader.offered) & set(SLUGS)


def test_the_skills_catalog_builds_the_projects_tools_once() -> None:
    """P3. ``GET /skills`` used to build 4 x 121 tools for each request. It
    reads the per-owner cache of the run path. Mutation: call
    ``owner_tools`` again -> the objects differ between calls -> red."""
    import gateway.routes.integrations_skills as sk

    one = sk._pack_tools_by_name()
    two = sk._pack_tools_by_name()
    assert one["task_detail"] is two["task_detail"] and one["delete_task"] is two["delete_task"]


MIXED = [
    "Make a task from this email with task type Bug",
    "Make a recurring task from this email",
    "Make a task from this email and add the task tag urgent",
    "Archive tasks named in this email",
    "Make a task from this email about the gantt",
]


@pytest.mark.parametrize("words", MIXED)
@pytest.mark.parametrize("surface", ["", "projects"])
def test_a_mixed_email_turn_keeps_email_and_the_core_pack_at_the_measured_base(
    words: str, surface: str, orchestrator,
) -> None:
    """Fix round 2 (P2). Email (31) + core (23) = 110 at base 56. The specific
    pack cannot join, and dropping the core pack for it would load neither.
    Mutation: restore the unconditional drop -> only email loads -> red."""
    base = _real_base(orchestrator)
    choice = tp.choose(_surface_turn(words, surface), allowed=EVERY, base_tools=base)
    if words == "Archive tasks named in this email":
        # MT-1228: projects-bulk is 21 tools now, so email (31) + bulk fits the
        # cap at base 56 and the bulk pack takes the place of the core pack.
        assert choice.slugs == ("email", "projects-bulk"), (words, choice)
        return
    assert choice.slugs[:2] == ("email", "projects") or (
        choice.slugs == ("email", "projects", "projects-bulk")), (words, choice)
    assert "projects" in choice.slugs and "projects" not in choice.skipped


def test_a_dropped_core_pack_is_recorded_in_skipped() -> None:
    """Where the drop is valid it shows in ``Choice.skipped``. Mutation: do
    not record it -> no log line shows the drop -> red."""
    # PJP-2: at base 56 the two fit, so the drop needs a bigger base (79 + 24 > 110 at 75).
    choice = tp.choose(
        _surface_turn("Who has spare team capacity?", "projects"),
        allowed=EVERY, base_tools=75)
    assert choice.slugs == ("projects-plan",) and "projects" in choice.skipped


# ── PJP-2 fix rounds 2 and 3: the step-aside runs after the cap ─────────────
#
# Fix round 2: the step-aside used to run before the cap, and it took the
# sticky pack that the member used least recently, also when the member named
# it in this turn. Now the cap runs first. Fix round 3: a sticky pack of any
# owner that the words of the turn do not name leaves first, in the cap's
# order. Then the core pack, then a named sticky pack or a new pack of the
# owner, the last one first (project_planning.md section 3.3, rule 2).
#
# Since ALN-4, ``projects-authority`` holds 3 tools. Fix round 4: a pack this
# small does not count in the reach test (``tool_packs.REACH_MIN_TOOLS``), so
# the step-aside acts at the real base again.

#: The real base (56) and the load tool (1), and the one specialist tool.
_BASE = 57
_SPECIALIST = {PA: 1}


def _sticky_turn(words: str, surface: str, *entries: tp.Sticky) -> tp.PackTurn:
    return tp.PackTurn(
        ORG, SESSION, tp._member_part(MEMBER), 20, entries, words, True, surface=surface)


def _owner_reached(choice: tp.Choice, base: int = _BASE,
                   removable: dict[str, int] | None = None) -> bool:
    total = base + sum(len(_family(slug)) for slug in choice.slugs)
    plan = tp.plan_loader(EVERY, choice.slugs, current=total,
                          removable=_SPECIALIST if removable is None else removable)
    return PA in plan.via_loader


def _case_a() -> tp.Choice:
    return tp.choose(
        _sticky_turn("remind me tomorrow at 9 to call Ravi", "projects",
                     tp.Sticky("projects-reminders", (10, 0), 13),
                     tp.Sticky("projects-plan", (11, 0), 19)),
        allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)


def test_a_reminder_on_the_projects_page_keeps_the_sticky_reminders_pack() -> None:
    """The case of the review. ``projects-reminders`` is sticky (used in turn
    13), ``projects-plan`` is sticky (turn 19), the member is on the Projects
    page and says "remind me". With ``HELD_CARD_WRITES_ORGS`` on for the org,
    the cap moves the plan pack out, because the words name the reminders pack.
    The reminders and core packs stay (99 tools), and the owner is reached.
    Mutation: run the step-aside before the cap -> both sticky packs leave and
    the run holds only the core pack -> red."""
    choice = _case_a()
    assert choice.slugs == ("projects-reminders", "projects"), choice
    assert choice.evicted == ("projects-plan",) and choice.skipped == (), choice
    assert _owner_reached(choice)


def test_with_the_switch_off_the_cap_moves_the_older_reminders_pack_out(monkeypatch) -> None:
    """Fix round 3 (P3-2). With ``HELD_CARD_WRITES_ORGS`` off for the org, the
    cap does not read the words of the turn (RX-1), so the least recently used
    sticky pack leaves: the reminders pack. The plan and core packs then make
    104 tools, and no work pack fits beside them (104 - 1 + 11 > 110). With
    the switch off no pack is spare, so the order of fix round 3 applies, and
    the org keeps its reach (D169 item 13). Task 1747 keeps one guard: the
    plan pack ran in the last turn (19), so it stays, and the core pack of
    the page steps aside. Mutation: let the cap read the words with the
    switch off -> the reminders pack stays -> red. Mutation: keep the core
    pack with the switch off -> the owner is not reached -> red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "")
    choice = _case_a()
    assert choice.slugs == ("projects-plan",), choice
    assert choice.evicted == ("projects-reminders",), choice
    assert choice.skipped == ("projects",), choice
    assert _owner_reached(choice)


def test_a_sticky_pack_of_another_owner_leaves_before_a_projects_pack() -> None:
    """The case of ``test_pack_room.py``: ``crm-records``, ``projects`` and
    ``projects-plan`` are sticky (turns 5, 6 and 7), and "remind me" adds the
    reminders pack. The cap moves ``crm-records`` out first, then the core pack.
    SP-12 grew the plan pack from 24 to 25 tools, so the two packs hold 100
    tools and do not keep the owner reached beside each other: the step-aside
    moves the plan pack out too, and the reminders pack stays alone. Before
    SP-12 both stayed (99 tools).
    Mutation: run the step-aside before the cap -> ``crm-records`` stays and
    both newer Projects packs leave -> red."""
    turn = tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 9,
                       (tp.Sticky("crm-records", (1, 0), 5), tp.Sticky("projects", (1, 1), 6),
                        tp.Sticky("projects-plan", (1, 2), 7)),
                       "remind me to call Ravi at 5", True)
    choice = tp.choose(turn, allowed=EVERY, base_tools=56)
    assert choice.slugs == ("projects-reminders",), choice
    assert choice.evicted == ("crm-records", "projects", "projects-plan"), choice
    assert _owner_reached(choice, base=56, removable={})


def test_the_step_aside_moves_the_sticky_pack_that_the_member_did_not_name() -> None:
    """Two sticky Projects packs fit the caps (105 tools) and keep the owner
    unreached. The member asks for a burndown, so the older plan pack is a need
    of this turn, and the newer structure pack steps aside. Task 1747: the
    structure pack was last used in turn 18, not in the last turn, so it can
    step aside. Used in turn 19, it stays (``test_pack_turn_keeps_used_packs``).
    Mutation: let a named sticky pack leave by age alone -> the plan pack
    leaves -> red."""
    choice = tp.choose(
        _sticky_turn("show the burndown", "",
                     tp.Sticky("projects-plan", (10, 0), 13),
                     tp.Sticky("projects-structure", (11, 0), 18)),
        allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert choice.slugs == ("projects-plan",), choice
    assert choice.evicted == ("projects-structure",), choice
    assert _owner_reached(choice)
    turn = _sticky_turn("show the burndown", "",
                        tp.Sticky("projects-plan", (10, 0), 13),
                        tp.Sticky("projects-structure", (11, 0), 19))
    fresh = tp.choose(turn, allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert fresh.slugs == ("projects-plan", "projects-structure"), fresh
    assert fresh.evicted == (), fresh
    assert _reached_by_load(turn, fresh, _BASE), "a load can move the structure pack out"


def test_a_sticky_pack_of_another_owner_does_not_keep_the_core_pack_on_the_page() -> None:
    """``email`` is sticky, the member is on the Projects page, and asks who is
    overloaded. The cap moves ``email`` out. Task 1747: the surface named the
    core pack, so it stays. The plan pack came in only by the words, so a load
    can move it out (D169 item 8 as amended), and the loader reaches the owner
    through it. Before task 1747 the core pack stepped aside here. Mutation:
    run the step-aside before the cap -> red. Mutation: drop the words packs
    from ``PackRun.spare`` -> the owner is not reached -> red."""
    turn = _sticky_turn("who is overloaded", "projects", tp.Sticky("email", (10, 0), 19))
    choice = tp.choose(turn, allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert choice.slugs == ("projects", "projects-plan"), choice
    assert choice.evicted == ("email",) and choice.skipped == (), choice
    assert _reached_by_load(turn, choice, _BASE)


@pytest.mark.parametrize("words", [
    "archive tasks that are done",
    # Fix round 4 (P3): the new bulk words name the pack in these too.
    "archive the rest of the done tasks",
    "now archive the done ones",
])
def test_a_sticky_pack_that_the_words_name_again_outlasts_the_core_pack(words: str) -> None:
    """Fix round 3 (P1-1). ``projects-bulk`` is sticky from the last turn, and
    the member says "archive tasks that are done" on the Projects page. The
    words name the bulk pack again, so where a pack must step aside, the core
    pack goes and the bulk pack stays. A repeat turn pays no load step.
    At the real base the bulk and core packs make 102 tools, and no other work
    pack fits beside them, so one steps aside. Task 1747: neither pack is
    spare (the bulk pack is sticky and named, the core pack came by the
    surface), so the order of fix round 3 applies. Mutation: let every sticky
    pack go before the core pack (fix round 2) -> the bulk pack leaves -> red.
    Mutation: keep each named pack with no spare pack -> the owner is not
    reached -> red."""
    choice = tp.choose(
        _sticky_turn(words, "projects", tp.Sticky("projects-bulk", (19, 0), 19)),
        allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert tp.matched_layer("projects-bulk", words) == "words"
    assert choice.slugs == ("projects-bulk",), choice
    assert choice.skipped == ("projects",) and choice.evicted == (), choice
    assert _owner_reached(choice)


@pytest.mark.parametrize(("base", "by"), [
    # Base 56 and base 57: since CRM-M1, crm-records holds 10 tools, so the cap
    # moves it out at both bases (111 and 112 tools).
    (56, "the cap at 111"),
    (_BASE, "the cap at 112"),
])
def test_an_unnamed_sticky_pack_of_another_owner_leaves_before_the_projects_packs(
    base: int, by: str,
) -> None:
    """Fix round 3 (P2-1). ``crm-records`` (turn 18) and ``projects-bulk`` (turn
    19) are sticky, and the member says "archive tasks that are done" on the
    Projects page. ``crm-records``, which the words do not name, leaves first.
    Then the bulk and core packs make 101 or 102 tools, and no other work pack
    fits beside them. Neither pack is spare, so the order of fix round 3
    applies: the core pack steps aside (task 1747). The bulk pack, which the
    words name and the last turn used, stays. Mutation: let only Projects
    packs step aside -> red. Mutation: keep each named pack with no spare pack
    -> the owner is not reached -> red."""
    choice = tp.choose(
        _sticky_turn("archive tasks that are done", "projects",
                     tp.Sticky("crm-records", (18, 0), 18),
                     tp.Sticky("projects-bulk", (19, 1), 19)),
        allowed=EVERY, base_tools=base, specialists=_SPECIALIST)
    assert choice.slugs == ("projects-bulk",), (by, choice)
    assert choice.evicted == ("crm-records",), (by, choice)
    assert choice.skipped == ("projects",), (by, choice)
    assert _owner_reached(choice, base=base)


def _switch_off(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "")


@pytest.mark.parametrize(("words", "surface", "entries"), [
    ("Who has spare team capacity?", "projects", ()),
    ("show the burndown", "projects", ()),
    ("show the task dependencies", "", ()),
    ("create a task for Ravi to call Acme", "projects",
     (tp.Sticky("projects-plan", (1, 0), 19),)),
    ("now tidy them up", "projects",
     (tp.Sticky("projects", (18, 0), 18), tp.Sticky("projects-plan", (18, 1), 18))),
], ids=["capacity", "burndown", "dependencies", "a-task-plan-sticky", "tidy"])
def test_with_the_switch_off_the_owner_is_reached_as_before(
    words: str, surface: str, entries: tuple[tp.Sticky, ...], monkeypatch,
) -> None:
    """Task 1747, fix round 2. An org that ``HELD_CARD_WRITES_ORGS`` does not
    name has no spare pack. It keeps the reach that it had before task 1747
    (D169 item 13): the order of fix round 3 applies, so one pack steps aside.
    The file turns the switch on for every other case. Mutation: keep each
    named pack with no spare pack -> the owner is not reached -> red."""
    _switch_off(monkeypatch)
    turn = _sticky_turn(words, surface, *entries)
    choice = tp.choose(turn, allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert len(choice.slugs) == 1, choice
    assert tp.PackRun.of(turn, choice, [(s, False) for s in choice.slugs]).spare() == ()
    assert _owner_reached(choice), choice


def test_with_the_switch_off_a_pack_that_the_last_turn_used_stays(monkeypatch) -> None:
    """Task 1747, fix round 2: the one guard that stays with the switch off. Both
    packs ran in the last turn (19), so neither steps aside, and the owner
    stays unreached. With the switch on, a spare pack reaches it. Mutation:
    drop ``fresh`` from ``_main_order_victim`` -> a pack leaves -> red."""
    entries = (tp.Sticky("projects", (19, 0), 19), tp.Sticky("projects-plan", (19, 1), 19))
    turn = _sticky_turn("now tidy them up", "projects", *entries)
    _switch_off(monkeypatch)
    off = tp.choose(turn, allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert off.slugs == ("projects", "projects-plan") and off.evicted == (), off
    assert not _owner_reached(off)
    plan = tp.choose(_sticky_turn("show the burndown", "",
                                  tp.Sticky("projects-structure", (19, 0), 19)),
                     allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    assert "projects-structure" in plan.slugs, "a fresh pack stays beside a new one"


def test_a_pack_too_small_for_work_does_not_reach_the_owner() -> None:
    """Fix round 4 (P2). The core and plan packs are held (57 + 47 = 104
    tools). Only the 3 tools of ``projects-authority`` fit beside them, and a
    pack this small cannot carry a work request, so the owner is not reached.
    The step-aside then splits the two (see the next case). With a work pack
    that fits, the owner is reached as before. Mutation: drop the
    ``REACH_MIN_TOOLS`` filter from ``plan_loader`` -> the owner is reached
    while no work pack fits, and a bulk load refuses with LOAD_NO_PATH -> red."""
    assert len(_family("projects-authority")) < tp.REACH_MIN_TOOLS
    work = min(len(_family(slug)) for slug in SLUGS if slug != "projects-authority")
    assert work >= tp.REACH_MIN_TOOLS
    held = ("projects", "projects-plan")
    current = _BASE + sum(len(_family(slug)) for slug in held)
    plan = tp.plan_loader(EVERY, held, current=current, removable=_SPECIALIST)
    assert PA not in plan.via_loader, plan
    one = tp.plan_loader(EVERY, ("projects",), current=_BASE + 23, removable=_SPECIALIST)
    assert PA in one.via_loader, one


@pytest.mark.parametrize("surface", ["projects", ""])
@pytest.mark.parametrize("words", [
    # Fix round 4 (P3): the new bulk words name the pack.
    "now archive the done ones",
    # No word of the bulk pack: the model loads it.
    "now tidy them up",
])
def test_plan_the_sprint_then_archive_the_done_ones_keeps_the_chat_in_place(
    surface: str, words: str,
) -> None:
    """The trace of the review. Turn 19, "plan the sprint", held the core pack
    and the model loaded the plan pack, so both are sticky. In turn 20 the two
    would make 104 tools, and the bulk pack (22) would not fit beside them.
    Before fix round 4 the authority pack (3) still "fitted", so the owner was
    reached, the bulk load refused with LOAD_NO_PATH, and the chat handed off
    one round later.

    Task 1747: both packs were used in the last turn, so neither steps aside.
    The owner is reached through the packs that a load can move out
    (``PackRun.spare``). Bulk is held at the start, or it fits a load. Without
    the reach, the specialist and the route line stay: the fault of
    2026-11-02. Mutation: read the reach with no spare pack -> red. Mutation:
    drop ``REACH_MIN_TOOLS`` -> the owner is reached with no spare pack -> the
    non-vacuity assert goes red."""
    turn = _sticky_turn(words, surface, tp.Sticky("projects", (19, 0), 19),
                        tp.Sticky("projects-plan", (19, 1), 19))
    choice = tp.choose(turn, allowed=EVERY, base_tools=_BASE, specialists=_SPECIALIST)
    total = _BASE + sum(len(_family(slug)) for slug in choice.slugs)
    assert total <= tp.MAX_REQUEST_TOOLS and choice.skipped == (), choice
    assert len(choice.slugs) == 2, choice
    assert not _owner_reached(choice), "non-vacuity: only a spare pack reaches the owner"
    assert _reached_by_load(turn, choice, _BASE), choice
    if tp.matched_layer("projects-bulk", words):
        assert "projects-bulk" in choice.slugs, choice
        return
    assert choice.slugs == ("projects", "projects-plan") and choice.evicted == (), choice
    run = tp.PackRun.of(turn, choice, [(slug, False) for slug in choice.slugs])
    spare = {slug: len(_family(slug)) for slug in run.spare()}
    assert spare, "non-vacuity: a pack that the words did not name can leave"
    plan = tp.plan_loader(EVERY, choice.slugs, current=total, removable=_SPECIALIST,
                          spare=spare)
    assert "projects-bulk" in plan.offered, plan
    assert total - 1 - sum(spare.values()) + len(_family("projects-bulk")) <= tp.MAX_REQUEST_TOOLS


@pytest.mark.parametrize("words", [
    "archive the completed tasks", "now archive the done ones",
    "archive the rest of the done tasks", "archive all done tasks",
    "delete the completed tasks", "archive the finished ones",
])
def test_a_cleanup_of_finished_tasks_names_the_bulk_pack(words: str) -> None:
    """Fix round 4 (P3). A cleanup of the done, completed or finished tasks names
    the bulk pack, also with no word "task". Mutation: drop the new phrases
    from ``_PROJECTS_BULK_WORDS`` -> red."""
    assert tp.matched_layer("projects-bulk", words) == "words", words


# ── MT-1228. "Remind me" loads a pack that holds set_reminder ───────────────

REMIND_TURNS = (
    "remind me to call Ravi at 5pm",
    "set a reminder for the board meeting",
    "nudge me tomorrow at 9 about the invoice",
    "don't let me forget to send the quote",
    "Do not let me forget the audit",
    "ping me at noon",
)


@pytest.mark.parametrize("words", REMIND_TURNS)
def test_a_remind_turn_loads_a_pack_that_holds_set_reminder(words: str) -> None:
    """Task 1228. A "remind me" turn made a private task, because the seven
    reminder tools sat in ``projects-bulk`` and no reminder word loaded it.
    Mutation: move ``set_reminder`` back to ``projects-bulk``, or drop the
    reminder words -> no chosen pack holds the tool -> red."""
    for base in (56, 75):
        choice = tp.choose(_surface_turn(words), allowed=EVERY, base_tools=base)
        held = [slug for slug in choice.slugs if "set_reminder" in _family(slug)]
        assert held, (words, base, choice)


def test_the_reminder_tools_are_in_one_pack_that_the_words_load() -> None:
    """Mutation: split the seven tools over two packs, or lose one -> red."""
    seven = {"list_reminders", "promote_reminder", "reopen_reminder",
             "reschedule_reminder", "resolve_reminder", "set_reminder",
             "update_reminder"}
    # WS-ALN ALN-2a: the two tools of a decision sit here too. The three tools of
    # the authority page have their own pack (ALN-4).
    organising = {"capture_intake", "intake_queue", "triage_intake", "create_area",
                  "delete_area", "rename_area", "my_areas", "my_contexts",
                  "notifications", "mark_notifications_read"}  # PJP-2: moved from structure
    # WS-39 LP-1a: the two tools of a project update sit with the authority page.
    authority = {"pin_authority", "project_authorities", "unpin_authority",
                 "list_project_updates", "post_project_update", "delete_project_update"}
    assert set(_family("projects-reminders")) == seven | organising | {"answer_decision", "my_decisions"}
    assert not organising & set(_family("projects-structure"))
    # SP-8a: the contract of an epic joins the authority pack.
    assert set(_family("projects-authority")) == authority | {"set_epic_contract"}
    assert not authority & set(_family("projects-bulk"))
    assert not seven & set(_family("projects-bulk"))
    assert not seven & set(_family("projects"))


@pytest.mark.parametrize("words", [
    "create a task to call Ravi", "show my overdue tasks", "add a task for the quote",
])
def test_a_plain_task_turn_loads_no_reminder_pack(words: str) -> None:
    """The control. A task turn keeps its packs. Mutation: add the bare word
    "task" to the reminder words -> the pack joins a task turn -> red."""
    choice = tp.choose(_surface_turn(words), allowed=EVERY, base_tools=56)
    assert choice.slugs == ("projects",), (words, choice)


@pytest.fixture(autouse=True)
def _held_card_writes_on(monkeypatch) -> None:
    """RX-1: this file tests the rule with the switch on. The switch off has its
    own test in ``test_held_card_writes.py``."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
