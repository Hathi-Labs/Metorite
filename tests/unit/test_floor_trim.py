"""WS-67 OA-8f: ``ORCHESTRATOR_FLOOR_TRIM`` takes ten floor tools out.

``project-docs/specs/one_assistant_context.md``, the note "As built, OA-8f".
Seven days of production use showed 220 orchestrator tool calls and no call
of ten floor tools. With the flag on, the orchestrator opts out of them
through the one seam of ``_tool_injection._floor_opt_out``, so the estimate
of the pack cap, the scope, the addendum and the final list agree. With the
flag off (the default) a run is byte-identical to one without this code.

``call_agent`` stays: ``LOAD_NO_PATH`` of OA-7i and the Projects route line
name it.

Mutations this file catches (R7):

* the reader ignores the flag (always on) → the flag-off tests are red;
* the trim set loses a name, or gains ``call_agent`` → red;
* ``_floor_trim`` stops checking the agent name → the Projects agent loses
  tools it has not opted out of → red;
* the persona or the rules of a pack name a trimmed tool → the model is told
  of a tool that the run does not hold → red;
* the estimate ignores the trim → it differs from the injected count → red.

Run: ``uv run pytest tests/unit/test_floor_trim.py -q``.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_skills import tool_packs as tp
from orchestrator import _tool_injection as ti

from tests.unit.test_tool_packs_choice import (
    CONFIG,
    _bullets,
    _names,
    _tenant,
    orchestrator,  # noqa: F401  (a fixture)
)

NINE = frozenset({
    "list_integrations", "get_errors", "run_diagnostics", "save_note",
    "recall_notes", "load_artifact_kit", "load_design_system", "manage_todo_list",
    "call_agents_parallel", "decide",
})


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixed registry and no database; the decide tool on, so it can leave."""
    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents:")
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _n: frozenset())
    monkeypatch.setattr("orchestrator.app_tools.load_app_action_tools", lambda _n: [])
    monkeypatch.setattr("acb_skills.decide_tools.decide_tool_enabled", lambda: True)
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "orchestrator_floor_trim", on, raising=False)
    ti._build_injected_tools_addendum.cache_clear()


def _inject(agents: list[Any]) -> tuple[set[str], str]:
    with _tenant():
        ti._inject_agent_tools(
            agents, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
            agent_config=CONFIG, no_egress=False)
    return set(_names(agents)), str(agents[0].default_options.get("instructions") or "")


# ── The set and the reader ──────────────────────────────────────────────────


def test_the_trim_set_is_the_nine_floor_tools() -> None:
    """Mutation: lose a name, or add ``call_agent`` → red. Every name is a floor
    tool that an agent may opt out of, and none is a kept tool."""
    assert NINE == tp.FLOOR_TRIM_TOOLS
    assert NINE <= ti.FLOOR_OPT_OUT_ALLOWED
    assert not NINE & ti._FLOOR_KEEP
    assert "call_agent" not in tp.FLOOR_TRIM_TOOLS


def test_the_flag_defaults_off_and_has_one_reader(monkeypatch) -> None:
    """The reader answers False on a box with no setting. Mutation: the reader
    returns True → red."""
    assert get_settings().orchestrator_floor_trim is False
    assert tp.floor_trim_enabled() is False
    _flag(monkeypatch, True)
    assert tp.floor_trim_enabled() is True


def test_off_the_orchestrator_opts_out_of_nothing(monkeypatch) -> None:
    """Mutation: the trim ignores the flag → the orchestrator loses ten tools
    on every box → red."""
    _flag(monkeypatch, False)
    assert ti._floor_opt_out("orchestrator", CONFIG) == frozenset()
    assert ti._floor_opt_out("orchestrator", None) == frozenset()


def test_on_the_orchestrator_opts_out_of_the_nine_and_only_it(monkeypatch) -> None:
    """The ten join the config's own opt-out. Another agent is not touched.
    Mutation: drop the agent-name check → the Projects agent loses the ten
    (``save_note`` among them) and an agent with no opt-out loses tools → red."""
    _flag(monkeypatch, True)
    assert ti._floor_opt_out("orchestrator", CONFIG) == NINE
    own = {"floor_opt_out": ["get_workflow_run"]}
    assert ti._floor_opt_out("orchestrator", own) == NINE | {"get_workflow_run"}
    assert ti._floor_opt_out("projects-assistant", {}) == frozenset()
    assert ti._floor_opt_out("projects-assistant", own) == {"get_workflow_run"}
    assert ti._floor_opt_out(None, None) == frozenset()


# ── The injection ───────────────────────────────────────────────────────────


def test_off_the_run_holds_the_nine(orchestrator, monkeypatch) -> None:  # noqa: F811
    """Non-vacuity, and the flag-off fence: with the flag off the ten are in
    the tool list. Mutation: the trim applies with the flag off → red."""
    _flag(monkeypatch, False)
    names, _text = _inject(orchestrator)
    assert names >= NINE, sorted(NINE - names)


def test_on_the_run_holds_none_of_the_nine_and_keeps_the_rest(orchestrator, monkeypatch) -> None:  # noqa: F811
    """With the flag on, the tool list loses exactly the ten, and nothing else
    changes. ``call_agent`` stays. Mutation: trim a tenth tool, or keep one of
    the ten → red."""
    _flag(monkeypatch, False)
    before, _ = _inject(orchestrator)
    # A second agent for the flag-on run, so the first injection does not leak.
    import orchestrator.agents as pkg

    again = [pkg.build_orchestrator_agent(with_history=False)]
    _flag(monkeypatch, True)
    after, _text = _inject(again)
    assert before - after == NINE
    assert after <= before
    assert "call_agent" in after and "call_agent_background" in after
    assert not NINE & after


def test_on_the_prompt_names_none_of_the_nine(orchestrator, monkeypatch) -> None:  # noqa: F811
    """The persona of the orchestrator and the rules of each pack name no
    trimmed tool. (The orchestrator is a native MAF agent, so the injected-tools
    addendum is not part of its prompt: it goes to Copilot SDK agents only. The
    injection appends the writing rules and the attachment rule, and those are
    scanned too.)
    A tool in a call is written ``name(`` or in backticks. Mutation: name one
    of the ten in the persona or in a pack's rules → red."""
    _flag(monkeypatch, True)
    persona = str(orchestrator[0].default_options.get("instructions") or "")
    _names_now, text = _inject(orchestrator)
    assert text.startswith(persona.rstrip()), "the injection only appends"
    assert "Metorite Platform Tools" not in text, "a native agent gets no tool addendum"
    bullets = "\n".join(_bullets().values())
    rules = "\n".join(p.rules for p in tp.PACKS.values())
    for blob_name, blob in (("persona", text), ("bullets", bullets), ("pack rules", rules)):
        assert blob.strip(), blob_name
        for name in sorted(NINE):
            assert re.search(rf"(`{name}`|{name}\()", blob) is None, (blob_name, name)
            if name != "decide":  # "decide" is also a plain English word
                assert re.search(rf"\b{name}\b", blob) is None, (blob_name, name)


@pytest.mark.parametrize("on", [False, True])
async def test_the_estimate_equals_the_injected_count_with_the_flag(
    on: bool, orchestrator, monkeypatch,  # noqa: F811
) -> None:
    """The estimate and the injection read ONE seam. Mutation: the estimate
    stops reading ``_floor_opt_out`` → it counts ten tools the run lacks
    (flag on) → red."""
    _flag(monkeypatch, on)
    with _tenant():
        base = ti._pack_base_count(
            orchestrator[0], agent_name="orchestrator", tool_scope=list(CONFIG["tool_scope"]),
            agent_config=CONFIG, disabled=frozenset())
        ti._inject_agent_tools(
            orchestrator, tool_scope=list(CONFIG["tool_scope"]), agent_name="orchestrator",
            agent_config=CONFIG, no_egress=False)
        await ti._add_crm_facts_block(orchestrator, frozenset(_names(orchestrator)), no_egress=False)
    assert base == len(_names(orchestrator)), (on, base)
    if on:
        assert not NINE & set(_names(orchestrator))


def test_the_docstrings_of_the_tools_that_stay_name_no_trimmed_tool(
    orchestrator, monkeypatch,  # noqa: F811
) -> None:
    """The model reads each tool's description. With the trim on, a tool that
    stays must not tell the model to call a trimmed one (``load_design_system``
    once pointed at ``load_artifact_kit``). Mutation: add ``load_artifact_kit``
    to ``load_design_system`` out of the trim → red here."""
    _flag(monkeypatch, True)
    names, _text = _inject(orchestrator)
    pool = orchestrator[0].default_options["tools"]
    for tool in pool:
        name = ti._tool_name(tool)
        doc = str(getattr(tool, "description", "") or getattr(getattr(tool, "func", tool), "__doc__", "") or "")
        for gone in sorted(NINE):
            if gone == "decide" or gone in names:
                continue
            assert f"{gone}(" not in doc and f"`{gone}`" not in doc, (name, gone)
