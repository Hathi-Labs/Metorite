"""The generic declarative builder — equivalence with the hand-written factories.

Spec: ``project-docs/specs/agent_architecture.md`` §1, §6.1.

The claim being tested is the whole argument for declarative agents: that a
hand-written ``agents.py`` for a tools-plus-instructions agent produces nothing
the manifest can't. The load-bearing test is
:func:`test_skill_tools_match_task_manager_factory` — it parses the real
``agent-task-manager/agents.py`` and asserts the builder resolves the identical
tool set, so retiring that file is provably lossless rather than hopefully so.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from acb_skills.manifest import AgentManifest

declarative = pytest.importorskip(
    "orchestrator.declarative",
    reason="orchestrator not installed in this environment",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_MANAGER_DIR = REPO_ROOT / "apps" / "agents" / "agent-task-manager"


def _tool_names_in_factory(agents_py: Path) -> set[str]:
    """Names a factory appends to its ``_TOOLS`` list, read from the source.

    Parsed rather than imported: importing the factory would pull in the Copilot
    SDK and construct an agent, which is exactly what this test exists to make
    unnecessary.
    """
    tree = ast.parse(agents_py.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and getattr(node.target, "id", "") == "_TOOLS":
            for element in ast.walk(node.value):
                if isinstance(element, ast.Name):
                    names.add(element.id)
    return names


# ---------------------------------------------------------------------------
# Equivalence — the reason this module exists
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (TASK_MANAGER_DIR / "agents.py").is_file(), reason="task-manager not on disk",
)
def test_skill_tools_match_task_manager_factory() -> None:
    """The builder must resolve exactly what task-manager assembles by hand.

    If this passes, ``agent-task-manager/agents.py`` is 136 lines of boilerplate
    the manifest already expresses — and deleting it changes nothing.
    """
    expected = _tool_names_in_factory(TASK_MANAGER_DIR / "agents.py")
    pytest.importorskip("skill_task_gtd", reason="skill-task-gtd not installed")

    cfg = json.loads((TASK_MANAGER_DIR / "config.json").read_text("utf-8"))
    manifest = AgentManifest.from_config(cfg, name="task-manager")

    resolved = declarative.resolve_skill_tools(manifest.capabilities.skills)
    resolved_names = {getattr(t, "__name__", repr(t)) for t in resolved}

    assert resolved_names == expected, (
        "declarative resolution diverged from the hand-written factory: "
        f"missing={sorted(expected - resolved_names)} "
        f"extra={sorted(resolved_names - expected)}"
    )


@pytest.mark.skipif(
    not (TASK_MANAGER_DIR / "instructions.md").is_file(),
    reason="task-manager instructions not on disk",
)
def test_instructions_come_from_the_agent_directory() -> None:
    loaded = declarative.load_instructions(TASK_MANAGER_DIR)
    on_disk = (TASK_MANAGER_DIR / "instructions.md").read_text("utf-8").strip()
    assert loaded == on_disk
    assert loaded != declarative.DEFAULT_INSTRUCTIONS


# ---------------------------------------------------------------------------
# Skill resolution
# ---------------------------------------------------------------------------

def test_repo_name_maps_to_module_name() -> None:
    assert declarative.skill_module_name("skill-task-gtd") == "skill_task_gtd"
    assert declarative.skill_module_name(" skill-zoho-crm ") == "skill_zoho_crm"


def test_missing_skill_is_skipped_not_raised() -> None:
    """Fail-soft, matching the factories' ``except ImportError: pass``."""
    assert declarative.resolve_skill_tools(["skill-does-not-exist"]) == []


def test_no_skills_resolves_empty() -> None:
    assert declarative.resolve_skill_tools(None) == []
    assert declarative.resolve_skill_tools([]) == []


def test_own_tool_scope_narrows_the_surface() -> None:
    pytest.importorskip("skill_task_gtd", reason="skill-task-gtd not installed")
    everything = declarative.resolve_skill_tools(["skill-task-gtd"])
    narrowed = declarative.resolve_skill_tools(
        ["skill-task-gtd"], own_tool_scope=["gtd_capture", "gtd_list"],
    )
    assert {t.__name__ for t in narrowed} == {"gtd_capture", "gtd_list"}
    assert len(narrowed) < len(everything)


def test_duplicate_skills_do_not_duplicate_tools() -> None:
    pytest.importorskip("skill_task_gtd", reason="skill-task-gtd not installed")
    once = declarative.resolve_skill_tools(["skill-task-gtd"])
    twice = declarative.resolve_skill_tools(["skill-task-gtd", "skill-task-gtd"])
    assert len(once) == len(twice)


def test_resolution_is_deterministic() -> None:
    """Tool order feeds the prompt's tool surface, which prompt caching relies on
    being byte-stable across turns."""
    pytest.importorskip("skill_task_gtd", reason="skill-task-gtd not installed")
    a = [t.__name__ for t in declarative.resolve_skill_tools(["skill-task-gtd"])]
    b = [t.__name__ for t in declarative.resolve_skill_tools(["skill-task-gtd"])]
    assert a == b


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

def test_absent_instructions_fall_back(tmp_path: Path) -> None:
    assert declarative.load_instructions(tmp_path) == declarative.DEFAULT_INSTRUCTIONS
    assert declarative.load_instructions(None) == declarative.DEFAULT_INSTRUCTIONS


def test_blank_instructions_fall_back(tmp_path: Path) -> None:
    (tmp_path / "instructions.md").write_text("   \n\n", encoding="utf-8")
    assert declarative.load_instructions(tmp_path) == declarative.DEFAULT_INSTRUCTIONS


def test_instructions_are_stripped(tmp_path: Path) -> None:
    (tmp_path / "instructions.md").write_text("\n\nYou are terse.\n\n", encoding="utf-8")
    assert declarative.load_instructions(tmp_path) == "You are terse."


# ---------------------------------------------------------------------------
# The built agent
# ---------------------------------------------------------------------------

def test_builder_produces_a_maf_agent_with_manifest_identity(tmp_path: Path) -> None:
    """A declarative agent is native MAF — never a Copilot agent (§11)."""
    agent_framework = pytest.importorskip("agent_framework")
    (tmp_path / "instructions.md").write_text("Be precise.", encoding="utf-8")

    manifest = AgentManifest.from_config(
        {
            "name": "probe",
            "kind": "declarative",
            "tool_scope": ["ask_questions"],
            "sharing": {"instancing": "shared"},
        },
        name="probe",
    )
    agent = declarative.build_declarative_agent(
        manifest, agent_dir=tmp_path, client=object(),
    )

    assert isinstance(agent, agent_framework.Agent)
    assert agent.name == "probe"


def test_builder_sets_no_permission_handler(tmp_path: Path) -> None:
    """The regression this whole design exists to prevent.

    Two factories set ``PermissionHandler.approve_all``, which pre-populated
    ``_permission_handler`` so the executor's ``if ... is None`` guard skipped and
    B6's risk-aware policy never applied. A declarative agent has no factory in
    which to take that back — assert it stays unset.
    """
    pytest.importorskip("agent_framework")
    manifest = AgentManifest.from_config(
        {"name": "probe", "kind": "declarative", "tool_scope": ["ask_questions"]},
        name="probe",
    )
    agent = declarative.build_declarative_agent(
        manifest, agent_dir=tmp_path, client=object(),
    )
    assert getattr(agent, "_permission_handler", None) is None


def _tool_names(agent) -> set[str]:
    """Tool names off a built MAF agent.

    MAF keeps instructions and tools in ``default_options`` — the same shape the
    Copilot factories use — and wraps each callable in a ``FunctionTool``, so the
    name lives on the wrapper rather than on ``__name__``.
    """
    tools = (agent.default_options or {}).get("tools") or []
    return {
        getattr(t, "name", None) or getattr(t, "__name__", "") or repr(t)
        for t in tools
    }


def test_explicit_instructions_win_over_the_file(tmp_path: Path) -> None:
    pytest.importorskip("agent_framework")
    (tmp_path / "instructions.md").write_text("From the file.", encoding="utf-8")
    manifest = AgentManifest.from_config({"name": "probe"}, name="probe")
    agent = declarative.build_declarative_agent(
        manifest, agent_dir=tmp_path, instructions="Explicit.", client=object(),
    )
    assert agent.default_options["instructions"] == "Explicit."


def test_file_instructions_are_used_when_not_overridden(tmp_path: Path) -> None:
    pytest.importorskip("agent_framework")
    (tmp_path / "instructions.md").write_text("From the file.", encoding="utf-8")
    manifest = AgentManifest.from_config({"name": "probe"}, name="probe")
    agent = declarative.build_declarative_agent(
        manifest, agent_dir=tmp_path, client=object(),
    )
    assert agent.default_options["instructions"] == "From the file."


def test_extra_tools_are_appended(tmp_path: Path) -> None:
    pytest.importorskip("agent_framework")

    def probe_tool() -> str:
        """A probe."""
        return "ok"

    manifest = AgentManifest.from_config({"name": "probe"}, name="probe")
    agent = declarative.build_declarative_agent(
        manifest, agent_dir=tmp_path, extra_tools=[probe_tool], client=object(),
    )
    assert "probe_tool" in _tool_names(agent)


@pytest.mark.skipif(
    not (TASK_MANAGER_DIR / "config.json").is_file(), reason="task-manager not on disk",
)
def test_built_agent_carries_the_full_skill_surface() -> None:
    """End to end: manifest in, an agent holding all 29 GTD tools out."""
    pytest.importorskip("agent_framework")
    pytest.importorskip("skill_task_gtd", reason="skill-task-gtd not installed")

    cfg = json.loads((TASK_MANAGER_DIR / "config.json").read_text("utf-8"))
    manifest = AgentManifest.from_config(cfg, name="task-manager")
    agent = declarative.build_declarative_agent(
        manifest, agent_dir=TASK_MANAGER_DIR, client=object(),
    )

    expected = _tool_names_in_factory(TASK_MANAGER_DIR / "agents.py")
    assert _tool_names(agent) == expected
    assert agent.name == "task-manager"
