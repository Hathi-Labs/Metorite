"""WS-STU F3: published skills load as org tool packs (no database).

Spec: ``project-docs/specs/ai_studio.md`` §7 "Runtime", §11.1 and the §14 F3
row. Code: ``acb_skills/skill_packs.py``, ``acb_skills/tool_packs.choose``
and ``persona_for``, and ``orchestrator/_tool_injection._turn_with_skills``,
``_skills_present`` and ``_apply_tool_packs``. The tenant and member rules on
a real database are in ``tests/unit/test_skill_packs_r8.py`` (R8).

Each case names, in its docstring, the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_skill_packs.py -q``.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common.db import bind_tenant, release_tenant
from acb_skills import skill_packs as sp
from acb_skills import tool_packs as tp

ORG = "0a0a0a0a-0000-4000-8000-0000000000f3"
MEMBER = "ana@stuf3.test"
SESSION = "chat-stuf3-1"
_ZWSP = chr(0x200B)
_ID_A = "00000000-0000-4000-8000-00000000000a"
_ID_B = "00000000-0000-4000-8000-00000000000b"
_ID_C = "00000000-0000-4000-8000-00000000000c"


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "description": "Use this when someone asks for a quote.",
        "instructions": "Read the price list page, then fill the quote template.",
        "actions": ["knowledge.get_page", "knowledge.list_spaces"],
    }
    body.update(over)
    return body


def _pack(skill_id: str = _ID_A, name: str = "Quote helper", **over: Any) -> sp.SkillPack:
    built = sp.build(skill_id, name, "", 2, "f" * 64, _body(**over),
                     max_tools=tp.MAX_PACK_TOOLS)
    assert isinstance(built, sp.SkillPack), built
    return built


def _turn(words: str = "", skills: tuple[Any, ...] = ()) -> tp.PackTurn:
    return tp.PackTurn(ORG, SESSION, tp._member_part(MEMBER), 1, (), words, True,
                       skills=skills)


# ── The map from an action to a chat tool ─────────────────────────────────


@pytest.mark.parametrize(("action", "tool"), [
    ("knowledge.get_page", "read_page"),
    ("knowledge.list_spaces", "list_spaces"),
    ("workflow:0f0f0f0f-0000-4000-8000-000000000001", "run_workflow"),
    ("knowledge.list_pages", None),  # the row plans its tool (KB-5)
    ("knowledge.no_such_op", None),
    ("integration:send_sms", None),
    ("object:items.list", "studio_record_list"),
    ("object:items.create", "studio_record_create"),
    ("object:items.update", "studio_record_update"),
    ("object:items.delete", "studio_record_delete"),
    ("object:items.get", None),  # no get tool exists
    ("object:items", None),
    ("object:.list", None),
    ("object:Items.list", None),  # the manifest grammar refuses an upper-case name
    ("object:a b.list", None),
    ("workflow:", None),
    ("projects.create_task", None),  # Projects is a tool app, no door op
    (42, None),
])
def test_action_tool_is_the_one_map(action: Any, tool: str | None) -> None:
    """Mutation: map each ``<app>.<op>`` to its op name, or map an object
    action to a tool → red."""
    assert sp.action_tool(action) == tool


# ── F10a: the buttons of a manifest ───────────────────────────────────────

#: R7 debt list. A button of a shipped template that has no chat tool yet.
#: It may only SHRINK. F10b removes an entry when its button gets a tool.
#: A new unserved button in a shipped template fails the test, and so does an
#: entry that is served now (delete it from the list).
_SHIPPED_UNSERVED_DEBT: frozenset[tuple[str, str, str, str]] = frozenset({
    ("field_visits", "check_in", "close_visit", "set"),
})


def _m(actions: list[dict[str, Any]], views: list[dict[str, Any]]) -> dict[str, Any]:
    return {"actions": actions, "views": views}


def test_button_tools_splits_served_and_unserved() -> None:
    """Mutation: serve a set-action, a door op or an integration, serve an
    undeclared button, or drop ``submit`` from the scan -> red."""
    manifest = _m(
        [
            {"id": "add", "uses": "object:item.create"},
            {"id": "done", "uses": "object:item.update", "set": {"s": "done"}},
            {"id": "door", "uses": "knowledge.get_page"},
            {"id": "sms", "uses": "integration:send_sms"},
            {"id": "one", "uses": "object:item.get"},
        ],
        [
            {"id": "v1", "actions": ["add", "done", "door", "sms", "ghost", "one"]},
            {"id": "v2", "submit": "add"},
        ],
    )
    served, unserved = sp.button_tools(manifest)
    assert served == (("v1", "add", "studio_record_create"),
                      ("v2", "add", "studio_record_create"))
    assert unserved == (
        ("v1", "done", "set"), ("v1", "door", "door_op"),
        ("v1", "sms", "integration"), ("v1", "ghost", "undeclared"),
        ("v1", "one", "no_tool"),
    )


def test_shipped_template_buttons_only_shrink_the_debt() -> None:
    """R7 fence. The unserved buttons of each shipped template must equal the
    named debt list. The list may only shrink. Mutation: add a template button
    with no tool, or leave a served button in the list -> red."""
    found: set[tuple[str, str, str, str]] = set()
    for name, manifest in _template_files():
        _, unserved = sp.button_tools(manifest)
        found |= {(name, v, a, r) for v, a, r in unserved}
    assert found == set(_SHIPPED_UNSERVED_DEBT)


def _template_files() -> list[tuple[str, dict[str, Any]]]:
    import json
    from pathlib import Path

    from acb_common import studio_templates as st

    root = Path(st.__file__).parent
    return [(p.stem, json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(root.glob("*.json"))]


# ── The build: what fails to load ──────────────────────────────────────────


def test_a_skill_pack_holds_its_tools_its_rules_and_its_cost() -> None:
    """Mutation: drop the tools line or the instructions from the rules, or
    state ``changes_prefix`` False → red."""
    found = _pack()
    assert found.slug == f"skill:{_ID_A}" and sp.is_skill_slug(found.slug)
    assert found.tools == ("read_page", "list_spaces")
    assert "Read the price list page" in found.rules
    assert "Tools of this skill: read_page, list_spaces." in found.rules
    assert "Use it when: Use this when someone asks for a quote." in found.rules
    assert found.token_cost == sp.estimate_tokens(found.rules) > 0
    assert found.changes_prefix is True
    assert found.version == 2 and found.content_hash == "f" * 64


def test_the_loader_strips_each_hidden_character() -> None:
    """F1b: a store before F1b could keep a hidden character. Mutation: drop
    ``strip_hidden`` from the instructions or the name → red."""
    found = _pack(name=f"Quote{_ZWSP} helper",
                  instructions=f"Read{_ZWSP} the price list.",
                  description=f"For{_ZWSP} quotes.",
                  actions=[f"knowledge.get{_ZWSP}_page"])
    assert _ZWSP not in found.rules and _ZWSP not in found.name
    assert "Read the price list." in found.rules
    assert found.tools == ("read_page",)


@pytest.mark.parametrize(("over", "reason"), [
    ({"instructions": "Greet {{customer_name}} and send the quote."}, "unresolved_variable"),
    ({"instructions": "Close the }} marker."}, "unresolved_variable"),
    ({"description": "For {{ team }} quotes."}, "unresolved_variable"),
    ({"instructions": "   "}, "no_instructions"),
    ({"instructions": 7}, "no_instructions"),
    ({"actions": ["knowledge.get_page", "object:items.get"]}, "action_unserved"),
    ({"actions": ["integration:send_sms"]}, "action_unserved"),
    ({"instructions": "x" * (sp.MAX_RULES_TOKENS * 4 + 4)}, "too_long"),
])
def test_a_broken_skill_fails_to_load(over: dict[str, Any], reason: str) -> None:
    """Mira gets no broken prompt (§11.1). Mutation: drop the variable check,
    let an unserved action through, or drop the cost cap → red."""
    assert sp.build(_ID_A, "Quote helper", "", 1, "0" * 64, _body(**over),
                    max_tools=tp.MAX_PACK_TOOLS) == reason


def test_a_variable_in_the_name_fails_to_load() -> None:
    assert sp.build(_ID_A, "Quote {{x}}", "", 1, "0" * 64, _body(),
                    max_tools=tp.MAX_PACK_TOOLS) == "unresolved_variable"


def test_a_skill_of_more_tools_than_the_cap_fails_to_load() -> None:
    """Mutation: drop the tool cap of the build → red."""
    assert sp.build(_ID_A, "Quote helper", "", 1, "0" * 64, _body(),
                    max_tools=1) == "too_many_tools"
    one = sp.build(_ID_A, "Quote helper", "", 1, "0" * 64,
                   _body(actions=["knowledge.get_page", "knowledge.get_page"]), max_tools=1)
    assert isinstance(one, sp.SkillPack) and one.tools == ("read_page",)


def test_a_skill_with_no_action_loads_its_rules_only() -> None:
    """Shape S0: a skill with no app holds rules and no tool."""
    found = _pack(actions=[])
    assert found.tools == () and "Tools of this skill: none." in found.rules


# ── The match ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("words", "hit"), [
    ("Use the quote helper for Acme", True),
    ("USE THE QUOTE   HELPER", True),
    ("run quote-helper now", True),  # the slug, with its hyphen as a space
    ("the quote helpers are slow", False),  # a whole phrase only
    ("a quote for Acme", False),
    ("", False),
])
def test_a_skill_matches_its_name_as_a_whole_phrase(words: str, hit: bool) -> None:
    """Mutation: match a part of a word, or any word of the name → red."""
    assert sp.matches("Quote helper", "quote-helper", words) is hit


def test_a_very_short_name_never_matches() -> None:
    assert sp.matches("QA", "qa", "please QA this") is False


# ── The choice: the cap and the order ──────────────────────────────────────


def test_choose_loads_a_matched_skill_after_the_code_packs() -> None:
    """Mutation: put the skills before the code packs, or leave them out of
    the choice → red."""
    turn = _turn("check my inbox and use the quote helper", (_pack(),))
    choice = tp.choose(turn, allowed=frozenset({"email"}), base_tools=40)
    assert choice.slugs == ("email",)
    assert [s.slug for s in choice.skills] == [f"skill:{_ID_A}"]
    assert choice.skills_skipped == ()


def test_three_packs_a_turn_holds_for_skills_too() -> None:
    """The cap of 3 packs counts the skill packs. Mutation: let skills past
    ``MAX_PACKS`` → red."""
    skills = (_pack(_ID_C, "C"), _pack(_ID_A, "A"), _pack(_ID_B, "B"))
    turn = _turn("Make my email rules stricter", skills)
    choice = tp.choose(turn, allowed=frozenset({"email", "email-setup"}), base_tools=10)
    assert choice.slugs == ("email", "email-setup")
    assert [s.slug for s in choice.skills] == [f"skill:{_ID_A}"]
    assert choice.skills_skipped == (f"skill:{_ID_B}", f"skill:{_ID_C}")
    none = tp.choose(_turn("hello", skills), allowed=frozenset(), base_tools=10)
    assert len(none.skills) == tp.MAX_PACKS == 3


def test_skills_load_in_a_fixed_order() -> None:
    """One set of skills gives one persona, whatever order the read gave.
    Mutation: keep the order of the read → red."""
    a, b = _pack(_ID_A, "A"), _pack(_ID_B, "B")
    one = tp.choose(_turn("x", (b, a)), allowed=frozenset(), base_tools=10)
    two = tp.choose(_turn("x", (a, b)), allowed=frozenset(), base_tools=10)
    assert [s.slug for s in one.skills] == [s.slug for s in two.skills] == [a.slug, b.slug]
    p1 = tp.persona_for("BASE", [], {}, skills=one.skills)
    p2 = tp.persona_for("BASE", [], {}, skills=two.skills)
    assert p1 == p2 and p1.index(a.rules) < p1.index(b.rules)


def test_a_skill_past_the_tool_cap_never_loads_in_the_choice() -> None:
    """A pack of more than 35 tools never loads, also when a caller built it
    by hand. Mutation: drop the tool check of ``_skills_of`` → red."""
    import dataclasses

    big = dataclasses.replace(_pack(), tools=tuple(f"t{i}" for i in range(36)))
    choice = tp.choose(_turn("x", (big,)), allowed=frozenset(), base_tools=10)
    assert choice.skills == ()


def test_the_persona_holds_the_skill_rules_after_the_code_rules() -> None:
    """Mutation: drop the skill blocks of ``persona_for`` → red. With no skill
    the persona stays byte for byte."""
    found = _pack()
    assert tp.persona_for("BASE\n", [], {}) == "BASE\n"
    out = tp.persona_for("BASE\n", [("email-setup", False)], {}, skills=(found,))
    assert out.index(tp.PACKS["email-setup"].rules.splitlines()[0]) < out.index(found.rules)
    assert out.endswith(found.rules + "\n")


def test_a_record_names_its_skills() -> None:
    found = _pack()
    turn = _turn("x", (found,))
    choice = tp.choose(turn, allowed=frozenset(), base_tools=10)
    record = tp.PackRun.of(turn, choice, [], skills=[found.slug])
    assert record.skills == (found.slug,) and record.slugs == ()


# ── The run: the gates of the read, and the tools of the run ───────────────


@contextlib.contextmanager
def _tenant(org: str = ORG) -> Iterator[None]:
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture
def ti(monkeypatch) -> Any:
    from orchestrator import _tool_injection as ti
    from orchestrator import executor as ex

    reads: list[str] = []

    class _Store:
        async def list_loadable(self, _db: Any, member: str) -> list[Any]:
            reads.append(member)
            return []

        async def loadable_versions(self, *_a: Any) -> list[Any]:
            return []

    monkeypatch.setattr(ti, "_studio_open", lambda: True)

    async def _open() -> bool:
        return True

    monkeypatch.setattr(ti, "_studio_member_open", _open)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: MEMBER)
    import gateway.studio as studio_pkg

    monkeypatch.setattr(studio_pkg, "skills_store", _Store(), raising=False)
    import sys

    monkeypatch.setitem(sys.modules, "gateway.studio.skills_store", studio_pkg.skills_store)

    @contextlib.asynccontextmanager
    async def _session(*_a: Any, **_k: Any) -> Any:
        yield object()

    import acb_common.db as db

    monkeypatch.setattr(db, "tenant_session", _session)
    ti._reads = reads  # type: ignore[attr-defined]
    return ti


async def test_the_read_runs_only_for_the_bound_org_and_the_verified_member(
    ti, monkeypatch,
) -> None:
    """R5: the org is the bound tenant and the member is the verified member,
    and both must agree with the turn. Mutation: drop the org check or the
    member check of ``_turn_with_skills`` → a read for another org or
    member → red."""
    from orchestrator import executor as ex

    with _tenant():
        await ti._turn_with_skills(_turn("use the quote helper"))
    assert ti._reads == [MEMBER], "non-vacuity: the read runs for the run's own turn"
    ti._reads.clear()
    with _tenant("0a0a0a0a-0000-4000-8000-0000000000ff"):
        await ti._turn_with_skills(_turn("use the quote helper"))
    monkeypatch.setattr(ex, "_verified_run_member", lambda: "ben@stuf3.test")
    with _tenant():
        await ti._turn_with_skills(_turn("use the quote helper"))
    monkeypatch.setattr(ex, "_verified_run_member", lambda: "")
    with _tenant():
        await ti._turn_with_skills(_turn("use the quote helper"))
    assert ti._reads == []


async def test_the_read_is_dark_without_studio(ti, monkeypatch) -> None:
    """Off: ``STUDIO_ORGS`` leaves the org out, or the member lacks
    ``feature:studio``. Then no read runs. A turn with no words reads
    nothing. Mutation: drop either gate → red."""
    with _tenant():
        await ti._turn_with_skills(_turn(""))
    monkeypatch.setattr(ti, "_studio_open", lambda: False)
    with _tenant():
        await ti._turn_with_skills(_turn("use the quote helper"))
    monkeypatch.setattr(ti, "_studio_open", lambda: True)

    async def _closed() -> bool:
        return False

    monkeypatch.setattr(ti, "_studio_member_open", _closed)
    with _tenant():
        await ti._turn_with_skills(_turn("use the quote helper"))
    assert ti._reads == []


async def test_a_fault_of_the_read_loads_no_skill(ti, monkeypatch) -> None:
    import acb_common.db as db

    @contextlib.asynccontextmanager
    async def _broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(db, "tenant_session", _broken)
    turn = _turn("use the quote helper")
    with _tenant():
        assert await ti._turn_with_skills(turn) is turn


def test_a_skill_loads_only_when_the_run_holds_each_of_its_tools(monkeypatch) -> None:
    """Mutation: drop the check of ``_skills_present`` → the skill whose tool
    the run lacks loads → red."""
    from orchestrator import _tool_injection as ti

    monkeypatch.setattr(ti, "_pack_base_names", lambda *_a, **_k: {"read_page", "list_spaces"})
    ok = _pack()
    lacks = _pack(_ID_B, "B", actions=["workflow:0f0f0f0f-0000-4000-8000-000000000001"])
    kept = ti._skills_present((ok, lacks), set(), object(), agent_name="orchestrator",
                              tool_scope=None, agent_config=None, disabled=frozenset())
    assert kept == (ok,)
    both = ti._skills_present((ok, lacks), {"run_workflow"}, object(), agent_name="orchestrator",
                              tool_scope=None, agent_config=None, disabled=frozenset())
    assert both == (ok, lacks)


async def test_a_turn_that_names_a_skill_loads_its_rules_in_the_run(monkeypatch) -> None:
    """The acceptance through ``_apply_tool_packs``: a turn that names a skill
    loads its rules in the persona, the record names it, and the log has one
    ``executor.skill_loaded`` line. No code pack loads. Mutation: return None
    from the apply when no code pack loads → red."""
    from types import SimpleNamespace

    from orchestrator import _tool_injection as ti

    found = _pack(actions=[])

    async def _with(turn: Any) -> Any:
        import dataclasses

        return dataclasses.replace(turn, skills=(found,))

    monkeypatch.setattr(ti, "_turn_with_skills", _with)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 40)
    monkeypatch.setattr(ti, "_pack_base_names", lambda *_a, **_k: set())
    lines: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(ti._log, "info", lambda event, **kw: lines.append((event, kw)))
    agent = SimpleNamespace(default_options={"tools": [], "instructions": "BASE"})
    turn = _turn("use the quote helper")
    with _tenant():
        applied = await ti._apply_tool_packs(
            [agent], turn, {"session_id": SESSION}, allowed=frozenset(),
            agent_name="orchestrator", tool_scope=None, agent_config=None, bullets={})
    assert applied is not None and applied.record.skills == (found.slug,)
    assert agent.default_options["instructions"].endswith(found.rules + "\n")
    events = [kw for event, kw in lines if event == "executor.skill_loaded"]
    assert len(events) == 1 and events[0]["skill"] == _ID_A
    assert events[0]["token_cost"] == found.token_cost and events[0]["changes_prefix"] is True
    assert "Read the price list" not in repr(events), "the log holds no skill text"


# ── Review fixes: the loader counts the skill packs (P1), and a skill keeps
# the code pack that holds its tool (P2) ─────────────────────────────────────


async def test_the_loader_plan_counts_the_skill_packs_of_the_run(monkeypatch) -> None:
    """P1, the plan of layer (b). Three skills fill the 3 places, so the
    loader offers no pack. Mutation: pass only the code slugs as ``held`` to
    ``_plan_pack_loader`` → the plan offers ``email`` → red."""
    import dataclasses
    from types import SimpleNamespace

    from orchestrator import _tool_injection as ti

    three = (_pack(_ID_A, "Quote desk", actions=[]), _pack(_ID_B, "Price check", actions=[]),
             _pack(_ID_C, "Visit plan", actions=[]))

    async def _with(turn: Any) -> Any:
        return dataclasses.replace(turn, skills=three)

    seen: dict[str, Any] = {}

    async def _plan(_agent: Any, allow: frozenset[str], held: list[str], **kw: Any) -> Any:
        seen["held"] = list(held)
        seen["plan"] = tp.plan_loader(allow, held, current=kw["current"])
        return seen["plan"]

    monkeypatch.setattr(ti, "_turn_with_skills", _with)
    monkeypatch.setattr(ti, "_plan_pack_loader", _plan)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 40)
    monkeypatch.setattr(ti, "_pack_base_names", lambda *_a, **_k: set())
    agent = SimpleNamespace(default_options={"tools": [], "instructions": "BASE"})
    words = "use the quote desk, the price check and the visit plan"
    with _tenant():
        applied = await ti._apply_tool_packs(
            [agent], _turn(words), {"session_id": SESSION}, allowed=frozenset({"email"}),
            agent_name="orchestrator", tool_scope=None, agent_config=None, bullets={},
            offer_loader=True)
    assert applied is not None and applied.record.slugs == ()
    assert len(applied.record.skills) == tp.MAX_PACKS, "non-vacuity: three skills load"
    assert set(seen["held"]) == {s.slug for s in three}
    assert seen["plan"].offered == ()


async def test_a_load_by_the_model_counts_the_skill_packs_of_the_run() -> None:
    """P1, the admission of layer (b). With three skills, a load refuses on
    the cap and builds nothing. With two, the load fits. Mutation: drop
    ``len(run.skills)`` from ``_room_for`` → the build runs → red."""
    built: list[str] = []

    async def _build(slug: str, *_a: Any) -> Any:
        built.append(slug)
        return None

    turn = _turn("x")
    choice = tp.choose(turn, allowed=frozenset(), base_tools=10)
    full = tp.PackRun.of(turn, choice, [], skills=["skill:a", "skill:b", "skill:c"])
    two = tp.PackRun.of(turn, choice, [], skills=["skill:a", "skill:b"])
    assert tp._room_for(full, [], 5, ()) is None
    assert tp._room_for(two, [], 5, ()) == ()
    loader = tp.LoadSkillTools(offered=("email",), run_key="k", record=full, build=_build)
    answer = await loader._admit_pack(tp.PACKS["email"], {}, [], full)
    assert isinstance(answer, str) and built == []
    await loader._admit_pack(tp.PACKS["email"], {}, [], two)
    assert built == ["email"], "non-vacuity: with a free place the build runs"


def test_a_code_pack_that_a_skill_needs_never_leaves_for_a_load(monkeypatch) -> None:
    """P2. A pack that came in only by the words is spare, unless a loaded
    skill needs one of its tools. Mutation: drop the ``needs`` check of
    ``PackRun.spare`` → red."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", "*")
    turn = _turn("check my inbox")
    choice = tp.choose(turn, allowed=frozenset({"email"}), base_tools=10)
    assert choice.layers.get("email") == "words"
    loaded = [("email", False)]
    skills = ["skill:a", "skill:b"]
    free = tp.PackRun.of(turn, choice, loaded, skills=skills)
    kept = tp.PackRun.of(turn, choice, loaded, skills=skills, needs=["email"])
    assert free.spare() == ("email",), "non-vacuity: a words pack is spare"
    assert tp._room_for(free, [], 5, free.spare()) == ("email",)
    assert kept.spare() == ()
    assert tp._room_for(kept, [], 5, kept.spare()) is None


async def test_the_apply_names_the_code_pack_that_a_skill_needs(monkeypatch) -> None:
    """P2, the apply. A skill whose tool only a code pack of the turn holds
    marks that pack in ``PackRun.needs``. Mutation: pass no ``needs`` to
    ``PackRun.of`` → red."""
    import dataclasses
    from types import SimpleNamespace

    from orchestrator import _tool_injection as ti

    found = _pack(actions=["knowledge.get_page"])

    async def _with(turn: Any) -> Any:
        return dataclasses.replace(turn, skills=(found,))

    def read_page() -> str:
        return ""

    async def _tools(slug: str, *_a: Any, **_k: Any) -> Any:
        return ti.PackBuild(slug=slug, owner="email-assistant", held=False, tools=(read_page,))

    monkeypatch.setattr(ti, "_turn_with_skills", _with)
    monkeypatch.setattr(ti, "_pack_tools", _tools)
    monkeypatch.setattr(tp, "persona_for", lambda *_a, **_k: "BASE")
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 40)
    monkeypatch.setattr(ti, "_pack_base_names", lambda *_a, **_k: set())
    agent = SimpleNamespace(default_options={"tools": [], "instructions": "BASE"})
    with _tenant():
        applied = await ti._apply_tool_packs(
            [agent], _turn("check my inbox and use the quote helper"),
            {"session_id": SESSION}, allowed=frozenset({"email"}),
            agent_name="orchestrator", tool_scope=None, agent_config=None, bullets={})
    assert applied is not None and applied.record.slugs == ("email",)
    assert applied.record.skills == (found.slug,)
    assert applied.record.needs == frozenset({"email"})
