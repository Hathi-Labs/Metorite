"""The orchestrator answers as a chief of staff, and no other agent changes.

Owning spec: ``project-docs/specs/agent_writing_voice.md`` §11 (owner,
2026-10-13).

``acb_llm.voice.ROLES["chief_of_staff"]`` holds the contract.
``AGENT_ROLES`` gives it to the orchestrator only, and
``_tool_injection._apply_voice`` adds it to that agent's voice block. The
orchestrator's own instructions (``orchestrator/agents.py``) carry the
delegation rule: merge the replies, never relay them whole.

Mutations this file catches (R7):

* ``_apply_voice`` stops passing the role: the orchestrator loses it;
* ``AGENT_ROLES`` maps another agent, or the role leaks into the plain agent
  block: another agent's prompt changes;
* a sub-agent gets the role;
* the role loses a rule (bottom line, 2 to 5 points, no paste, proactive,
  recommendation, the cap, expand on request);
* the role grows past its ceiling, or breaks the voice it sits in;
* "relay its full response" comes back to the orchestrator's instructions;
* the spec copy and the code drift apart;
* a brevity rule of spec 11.8 goes, or the cap goes soft again ("about");
* the cap of the prompt climbs to the cap that the eval measures;
* the two example replies go, grow past the cap, or break the voice;
* the core or the plain agent block changes, which changes every agent;
* the examples reach another agent.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import orchestrator._tool_injection as ti
import pytest
from acb_llm.voice import (
    AGENT,
    AGENT_ROLES,
    CHIEF_OF_STAFF_HEADING,
    CHIEF_OF_STAFF_WORD_CAP,
    CORE,
    ROLES,
    VOICE_HEADING,
    core_prompt,
    role_for,
    voice_lint,
    voice_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROLES["chief_of_staff"]

#: The in-tree agents (folder, registry name). ``test_agent_voice.py`` fails
#: when a folder joins and this list does not.
AGENTS: tuple[tuple[str, str], ...] = (
    ("agent-apis-config", "apis-config"),
    ("agent-app-builder", "app-builder"),
    ("agent-crm", "crm"),
    ("agent-email-assistant", "email-assistant"),
    ("agent-orchestrator", "orchestrator"),
    ("agent-projects", "projects-assistant"),
    ("agent-task-manager", "task-manager"),
    ("agent-whatsapp-assistant", "whatsapp-assistant"),
)

#: Measured 2026-10-13 in the run-context tokenizer: the role 278, the
#: orchestrator's voice block 847. In tiktoken ``o200k_base``: 256 and 794.
#: Measured 2026-10-14, after the brevity rules of spec 11.8: the role 296,
#: the block 866. In ``o200k_base``: 276 and 814. Neither ceiling moved.
ROLE_TOKEN_CEILING = 300
ORCHESTRATOR_BLOCK_TOKEN_CEILING = 900


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.app_tools as app_tools

    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents: (stub)")
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda name: [])
    monkeypatch.delenv("SKILLS_FAIL_CLOSED", raising=False)
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


def _tokens(text: str) -> int:
    from acb_llm.context import count_message_tokens
    return count_message_tokens([{"role": "system", "content": text}])


def _built_prompt(agent_dir: str, agent_name: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The prompt of *agent_dir*, as the executor builds it."""
    pytest.importorskip("agent_framework")
    from acb_common.settings import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-cos-dummy")
    get_settings.cache_clear()
    path = ROOT / "apps/agents" / agent_dir
    monkeypatch.syspath_prepend(str(path))
    spec = importlib.util.spec_from_file_location(
        "cos_" + agent_dir.replace("-", "_"), path / "agents.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    agents = mod.build_agents()
    cfg: dict[str, Any] = json.loads((path / "config.json").read_text(encoding="utf-8"))
    ti._apply_own_tool_scope(agents, cfg.get("own_tool_scope") or None)
    ti._inject_agent_tools(
        agents, tool_scope=cfg.get("tool_scope") or None,
        agent_name=agent_name, agent_config=cfg, no_egress=False,
    )
    get_settings.cache_clear()
    agent = agents[0]
    opts = getattr(agent, "_default_options", None)
    if isinstance(opts, dict) and opts.get("system_message"):
        msg = opts["system_message"]
        return msg["content"] if isinstance(msg, dict) else str(msg)
    return agent.default_options.get("instructions") or ""


# ── The text ───────────────────────────────────────────────────────────────

def test_the_role_holds_each_rule_of_the_contract() -> None:
    """By marker, not by the full text, so the owner may reword a rule."""
    assert ROLE.startswith(CHIEF_OF_STAFF_HEADING)
    for marker in (
        "Open with the bottom line",          # BLUF
        "Do not restate the question",         # no recap
        "the 2 to 5 points that matter",       # distil, ranked
        "Never paste the output of a read",    # no raw dump
        "text the member asked for",           # ...but a draft stays whole
        "one folded card",                     # detail one click away
        "did not ask about only when it is real",  # proactive, never padding
        "then your recommendation",            # decision support
        "who owns it",                         # next step and owner
        "give all of it",                      # expand on request
    ):
        assert marker in ROLE, marker


def test_the_role_holds_the_brevity_rules() -> None:
    """Spec 11.8. Each marker answers an overrun of the 2026-10-14 live run."""
    for marker in (
        f"at most {CHIEF_OF_STAFF_WORD_CAP} words",  # a hard cap, not "about"
        "one line each, most important",       # C09: two or three sentences a point
        "all that is on track in one clause",  # C01, C10: a line per healthy item
        "No second list of reasons",           # C03: the reasons, then the options again
        "no headings or labels",               # C02: "Needs you now", "Due today"
        "no summary",                          # C04: the top risk again at the end
        "no menu of choices",                  # C01, C02: "A, B, or C?"
        "one short offer at most",             # nearly every case closed on an offer
    ):
        assert marker in ROLE, marker
    assert "about 120" not in ROLE


def test_the_prompt_cap_sits_under_the_cap_the_eval_measures() -> None:
    """A model reads a soft cap as a target, so the prompt asks for less than
    the eval allows (spec 11.8)."""
    from evals.orchestrator_cos import run

    assert run.ROUTINE_CAP == 120
    assert 60 <= CHIEF_OF_STAFF_WORD_CAP < run.ROUTINE_CAP


def test_the_role_obeys_the_voice_it_sits_in() -> None:
    assert voice_lint(ROLE) == []
    assert "—" not in ROLE
    for name in ti._CORE_STANDARD_TOOL_NAMES:
        assert f"{name}(" not in ROLE and f"`{name}`" not in ROLE, name


def test_the_role_stays_under_its_token_ceiling() -> None:
    assert _tokens(ROLE) <= ROLE_TOKEN_CEILING
    assert _tokens(voice_prompt(AGENT, role="chief_of_staff")) <= ORCHESTRATOR_BLOCK_TOKEN_CEILING


def test_the_spec_copies_the_role_byte_for_byte() -> None:
    spec = (ROOT / "project-docs/specs/agent_writing_voice.md").read_text(encoding="utf-8")
    section = spec.split("### 11.1 The contract", 1)[1]
    fence = "`" * 3
    block = section.split(fence + "text\n", 1)[1].split("\n" + fence, 1)[0]
    assert block == ROLE


# ── The seam ───────────────────────────────────────────────────────────────

def test_only_the_orchestrator_reads_a_role() -> None:
    assert AGENT_ROLES == {"orchestrator": "chief_of_staff"}
    assert role_for("orchestrator") == "chief_of_staff"
    for _, name in AGENTS:
        if name != "orchestrator":
            assert role_for(name) is None, name
    assert role_for(None) is None and role_for("") is None


def test_the_role_joins_the_agent_block_after_the_overlays() -> None:
    text = voice_prompt(AGENT, role="chief_of_staff")
    assert text == f"{voice_prompt(AGENT)}\n{ROLE}"
    assert text.count(CORE) == 1 and text.count(CHIEF_OF_STAFF_HEADING) == 1


def test_the_plain_agent_block_carries_no_role() -> None:
    assert CHIEF_OF_STAFF_HEADING not in voice_prompt(AGENT)
    assert CHIEF_OF_STAFF_HEADING not in core_prompt()


def test_a_role_on_a_drafting_surface_fails_loudly() -> None:
    with pytest.raises(ValueError):
        voice_prompt("email", role="chief_of_staff")
    with pytest.raises(KeyError):
        voice_prompt(AGENT, role="chief_of_stuff")


def test_a_sub_agent_never_reads_the_role() -> None:
    text = ti._with_voice("You are a sub-agent.", sub_agent=True, role="chief_of_staff")
    assert CHIEF_OF_STAFF_HEADING not in text
    assert text.endswith(core_prompt())


class _Native:
    def __init__(self, name: str) -> None:
        self.name = name
        self.default_options: dict[str, Any] = {"tools": [], "instructions": "You route."}


def test_only_the_registry_name_selects_the_role() -> None:
    """Every executor site passes the registry name. The object's own name
    never selects a role, so a repo agent that names itself "orchestrator"
    gets the plain voice (review round 1)."""
    by_param, by_attr, other = _Native("x"), _Native("orchestrator"), _Native("orchestrator")
    ti._apply_voice([by_param], agent_name="orchestrator")
    ti._apply_voice([by_attr])
    ti._apply_voice([other], agent_name="crm")
    assert CHIEF_OF_STAFF_HEADING in by_param.default_options["instructions"]
    assert CHIEF_OF_STAFF_HEADING not in by_attr.default_options["instructions"]
    assert CHIEF_OF_STAFF_HEADING not in other.default_options["instructions"]


# ── Every in-tree agent, built as the executor builds it ───────────────────

def test_the_built_orchestrator_reads_the_role_once_inside_its_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = _built_prompt("agent-orchestrator", "orchestrator", monkeypatch)
    assert prompt.count(CHIEF_OF_STAFF_HEADING) == 1
    assert voice_prompt(AGENT, role="chief_of_staff") in prompt
    at = prompt.index(CHIEF_OF_STAFF_HEADING)
    assert prompt.index(VOICE_HEADING) < at
    for marker in ("## Delegatable agents", "Rich UI by default", "### Output discipline"):
        if marker in prompt:
            assert at < prompt.index(marker), marker


@pytest.mark.parametrize(("agent_dir", "agent_name"), [a for a in AGENTS if a[1] != "orchestrator"])
def test_every_other_agent_keeps_the_plain_voice(
    agent_dir: str, agent_name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = _built_prompt(agent_dir, agent_name, monkeypatch)
    assert CHIEF_OF_STAFF_HEADING not in prompt
    assert voice_prompt(AGENT) in prompt


# ── The delegation rule in the orchestrator's own instructions ─────────────

def _live_instructions() -> str:
    # ``orchestrator.agents`` is a package that shadows ``agents.py`` and
    # loads it as ``_mod``.
    import orchestrator.agents as pkg

    return pkg._mod._PULL_INSTRUCTIONS


def test_the_orchestrator_merges_replies_and_never_relays_them() -> None:
    """``agents.py`` is the one source (spec §11.7: ``instructions.md`` is gone)."""
    text = _live_instructions()
    assert "relay its full response" not in text
    assert "never relay it whole" in text
    assert "merge their replies into ONE answer" in text
    assert "Never give the replies one agent after another" in text
    assert "When two replies\n  disagree, say so" in text
    assert "Keep a deliverable whole" in text


# ── The example replies (spec 11.8) ────────────────────────────────────────

#: The heading of the two example replies in the orchestrator's instructions.
SHAPE_HEADING = "THE SHAPE OF A ROUTINE REPLY."


def _examples() -> dict[str, str]:
    """Each example reply of the instructions, by its label, dedented."""
    import textwrap

    text = _live_instructions()
    assert text.count(SHAPE_HEADING) == 1
    body = text.split(SHAPE_HEADING, 1)[1]
    out: dict[str, str] = {}
    for label in ("A status:", "A decision:"):
        assert body.count("\n" + label + "\n") == 1, label
        chunk = body.split("\n" + label + "\n", 1)[1].split("\n\n", 1)[0]
        out[label] = textwrap.dedent(chunk).strip()
    return out


def test_the_instructions_show_two_example_replies_in_the_target_shape() -> None:
    from evals.orchestrator_cos import run

    examples = _examples()
    assert set(examples) == {"A status:", "A decision:"}
    for label, reply in examples.items():
        assert 25 <= run.words(reply) <= CHIEF_OF_STAFF_WORD_CAP, label
        assert voice_lint(reply) == [], label
        assert "**" not in reply and "#" not in reply, label   # no headings, no labels
        assert reply.count("?") == 0, label                      # no menu
        assert not run._PREAMBLE.match(reply), label             # bottom line first
        assert not run._TOOL_NAME.search(reply), label
        lines = [ln for ln in reply.splitlines() if ln.startswith("- ")]
        assert 2 <= len(lines) <= 5, label
    # The decision example recommends, by the eval's own test of it.
    assert any(run._RECOMMEND.search(s) for s in run._sentences(examples["A decision:"]))
    # The facts are made up, and the instructions say so.
    assert "Their facts are made up, so never repeat them." in _live_instructions()


def test_the_examples_reach_the_orchestrator_and_no_other_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = _built_prompt("agent-orchestrator", "orchestrator", monkeypatch)
    assert prompt.count(SHAPE_HEADING) == 1
    for agent_dir, agent_name in AGENTS:
        if agent_name != "orchestrator":
            assert SHAPE_HEADING not in _built_prompt(agent_dir, agent_name, monkeypatch), agent_name


#: SHA-256 of the plain agent block and of the sub-agent block, taken on
#: 2026-10-14 before and after the brevity change: the two match. A change of
#: either one changes every agent. Spec 11.8 says the brevity rules stay out of
#: the core. Change these only in a PR that changes the core on purpose.
PLAIN_AGENT_BLOCK_SHA256 = "b49783fa31cc955047b194c21e514c0e019e4f24c555964c81ed983167a42c6d"
SUB_AGENT_BLOCK_SHA256 = "d36190c37ac645a614e0f2cc463efd9876a6a949c41a66c1c83749b0ebb776e4"


def test_the_voice_of_every_other_agent_is_byte_for_byte_the_same() -> None:
    import hashlib

    assert hashlib.sha256(voice_prompt(AGENT).encode("utf-8")).hexdigest() == PLAIN_AGENT_BLOCK_SHA256
    assert hashlib.sha256(core_prompt().encode("utf-8")).hexdigest() == SUB_AGENT_BLOCK_SHA256
    for marker in ("at most", "no menu of choices", "no headings or labels"):
        assert marker not in CORE, marker
