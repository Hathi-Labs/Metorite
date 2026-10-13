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
* the spec copy and the code drift apart.
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

#: Measured 2026-10-13 in the run-context tokenizer: the role 254, the
#: orchestrator's voice block 823. In tiktoken ``o200k_base``: 233 and 771.
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
        "one folded card",                     # detail one click away
        "did not ask about only when it is real",  # proactive, never padding
        "your recommendation and its",         # decision support
        "who owns it",                         # next step and owner
        "under about 120 words",               # the soft cap
        "give all of it",                      # expand on request
    ):
        assert marker in ROLE, marker


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


def test_the_role_follows_the_registry_name_or_the_agent_name() -> None:
    by_param, by_attr, other = _Native("x"), _Native("orchestrator"), _Native("orchestrator")
    ti._apply_voice([by_param], agent_name="orchestrator")
    ti._apply_voice([by_attr])
    ti._apply_voice([other], agent_name="crm")
    assert CHIEF_OF_STAFF_HEADING in by_param.default_options["instructions"]
    assert CHIEF_OF_STAFF_HEADING in by_attr.default_options["instructions"]
    # The registry name wins over the object's own name.
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

def _instruction_texts() -> dict[str, str]:
    # ``orchestrator.agents`` is a package that shadows ``agents.py`` and
    # loads it as ``_mod``.
    import orchestrator.agents as pkg

    _PULL_INSTRUCTIONS = pkg._mod._PULL_INSTRUCTIONS

    md = (ROOT / "apps/agents/agent-orchestrator/instructions.md").read_text(encoding="utf-8")
    return {"agents.py": _PULL_INSTRUCTIONS, "instructions.md": md.replace("\r\n", "\n")}


@pytest.mark.parametrize("where", ["agents.py", "instructions.md"])
def test_the_orchestrator_merges_replies_and_never_relays_them(where: str) -> None:
    """``agents.py`` is the live text. ``instructions.md`` is not loaded by
    the orchestrator (spec §11.4), so it must at least not contradict it."""
    text = _instruction_texts()[where]
    assert "relay its full response" not in text
    assert "never relay it whole" in text
    assert "merge their replies into ONE answer" in text
    assert "Never give the replies one agent after another" in text
    assert "When two replies\n  disagree, say so" in text
    assert "Keep a deliverable whole" in text
