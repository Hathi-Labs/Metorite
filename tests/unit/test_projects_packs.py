"""WS-67 OA-8b: the Projects tool packs (dark behind ``TOOL_PACKS``).

``project-docs/specs/one_assistant_context.md`` §4.3.3 and §4.5 (OA-8b). The
Projects assistant's 120 own tools become four packs of the one assistant:
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
pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from acb_skills import egress as eg
from acb_skills import skill_families as sf
from acb_skills import tool_packs as tp
from agent_framework import FunctionTool
from orchestrator import _tool_injection as ti

from evals.one_assistant_measure.scenarios import SCENARIOS
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
SLUGS = ("projects", "projects-plan", "projects-structure", "projects-bulk")
SIZES = {"projects": 23, "projects-plan": 35, "projects-structure": 34, "projects-bulk": 28}
UUID = tc.UUID

#: The eight tools that make ONE card for a batch, raised inside the tool body.
BATCH_TOOLS = {
    "projects-bulk": ("bulk_update", "create_tasks", "file_tasks", "merge_tasks"),
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
    assert len(union) == 120 == len(set(union)), "no tool is in two packs"
    assert set(union) == set(skill_projects.__all__) == _own_tool_scope()
    for slug in SLUGS:
        assert sf.SKILL_FAMILIES[slug]["owner"] == PA and sf.is_pack(slug)
        assert len(_family(slug)) == SIZES[slug], slug
        assert len(_family(slug)) <= tp.MAX_PACK_TOOLS
        assert list(_family(slug)) == sorted(_family(slug)), "the family is sorted"
    assert tp.owner_packs(PA) == SLUGS


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


async def test_a_held_pack_drops_nothing_because_no_projects_tool_is_egress(store) -> None:
    """Parity with the owner's own run: the held set of the Projects assistant
    is ``web_search``, ``fetch_page``, ``save_memory`` and ``save_note``, and
    none of them is a Projects tool. Mutation: a Projects tool that says
    ``open_world=True`` -> the held pack drops it -> red."""
    for slug in SLUGS:
        build, _key = await _build(slug, no_egress=True)
        assert build is not None and build.held is True, slug
        assert build.dropped == (), (slug, build.dropped)
        assert [_name(t) for t in build.tools] == list(_family(slug)), slug


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
    """Every run builds the packs again, and the owner's 120 tools are cached.
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
    """120 models for each run cost time. Mutation: no cache -> a second call
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
    name and priced nothing. Mutation: read ``__name__`` again -> the 120
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
    assert tp.surfaces() == {"email", "crm", "projects", "tasks", "calendar"}
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
            "note", "deal", "pipeline", "email", "mail", "draft", "status", "view"}
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


def test_no_bullet_joins_the_persona_set_and_the_owner_is_marked() -> None:
    """The flag-off persona stays byte for byte what it was. Mutation: add a
    ``projects-assistant`` bullet -> red. Mutation: drop the owner from
    ``NO_PERSONA_BULLETS`` -> ``persona_for`` raises -> red."""
    assert set(_bullets()) == {EA}
    assert PA in tp.NO_PERSONA_BULLETS
    assert "projects_assistant" in _agents_mod()._orchestrator_instructions(False)


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
    every = tp._rules_block(tp.PACKS["projects"], False, SLUGS)
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
    assert some.replaced == {PA} and some.refusal(PA) is None
    allp = tp.PackRun.of(turn, tp.Choice((), {}, {}), [(s, False) for s in SLUGS])
    assert allp.refusal(PA) == tp.PACKS["projects"].delegation_refusal


def test_the_core_rules_name_the_other_packs_and_no_tool_of_them() -> None:
    """The core rules send the work of the other three packs to the loader by
    slug. They name no tool of those packs (the registry fence holds that for
    every pack). Mutation: drop a slug -> the model has no route -> red."""
    rules = tp.rules_for("projects")
    for slug in SLUGS[1:]:
        assert slug in rules, slug
    assert "load_skill_tools" in rules
    for slug in SLUGS[1:]:
        assert tp.PACKS[slug].rules.count("Use them yourself.") == 1
    assert max(len(tp.rules_for(slug)) for slug in SLUGS) < 3300


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
    """The eight batch tools raise their card inside the tool body, and the
    pack wrapper is marked, so the assembly gates none of them a second time.
    Mutation: drop ``__cc_gated__`` from ``_pack_call`` -> red."""
    seen = 0
    for slug, names in BATCH_TOOLS.items():
        build, _key = await _build(slug)
        held = {_name(t): t for t in build.tools}
        for name in names:
            assert held[name].func.__cc_gated__ is True, name
            seen += 1
    assert seen == 8


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
    assert "projects_assistant" not in names, "the specialist left"
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
    assert seen == 120


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
    assert answer.startswith("Loaded the Projects planning and reports tools (35 tools)"), answer
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
