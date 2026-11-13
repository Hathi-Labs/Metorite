"""Every Studio model call goes through the metered Console seam. H-287, STU J3b-1.

Spec: ``ai_studio.md`` J3b · ``integrations_console.md`` IN-D15.

An LLM client that skips the seam writes no ``usage_event`` row, so a cap
cannot count its spend. This fence reads source text and runs hermetic.

Out of scope: ``acompletion_stream_text`` (email drafting) and
``/v1/embeddings``. Other slices own those two paths.

Run::

    uv run pytest tests/unit/test_studio_model_calls_metered.py -v
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GW = "apps/services/gateway/gateway"
ORCH = "apps/services/orchestrator/orchestrator"

# file -> names of which at least one must appear in the file
METERED_CLIENTS = {
    "apps/agents/agent-studio-builder/agents.py": ("attributed_openai",),
    f"{ORCH}/agents.py": ("attributed_openai",),
    f"{ORCH}/code_session.py": ("attributed_openai",),
    f"{ORCH}/_copilot_session.py": ("attributed_copilot_provider",),
    f"{GW}/routes/apps/runtime.py": ("acompletion_with_fallback",),
    # The workflow agent node calls the orchestrator run_agent, which builds
    # its client in orchestrator/agents.py (checked above).
    f"{GW}/routes/workflows/service.py": ("orchestrator.executor", "run_agent"),
}

STUDIO_DIRS = (f"{GW}/routes/studio", f"{GW}/studio", f"{GW}/package_host")
STUDIO_FILES = ("packages/acb_skills/acb_skills/studio_tools.py",)
NO_MODEL_CALL = (f"{GW}/routes/studio/invoke.py", f"{GW}/routes/studio/actions.py")


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"{rel} moved: update this fence"
    return path.read_text(encoding="utf-8")


def _studio_sources() -> list[str]:
    out: list[str] = []
    for d in STUDIO_DIRS:
        base = ROOT / d
        assert base.is_dir(), f"{d} moved: update this fence"
        out += [str(p.relative_to(ROOT)).replace("\\", "/") for p in sorted(base.rglob("*.py"))]
    return out + list(STUDIO_FILES)


@pytest.mark.parametrize("rel", sorted(METERED_CLIENTS))
def test_model_path_uses_the_metered_seam(rel: str) -> None:
    src = _read(rel)
    for name in METERED_CLIENTS[rel]:
        assert name in src, f"{rel} no longer uses {name}: a model call here would go unmetered"


def test_workflow_agent_node_has_no_own_client() -> None:
    src = _read(f"{GW}/routes/workflows/service.py")
    assert not re.search(r"\blitellm\b|AsyncOpenAI|openai\.", src)


@pytest.mark.parametrize("rel", _studio_sources())
def test_studio_code_does_not_call_litellm(rel: str) -> None:
    tree = ast.parse(_read(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "litellm" for a in node.names), rel
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "litellm", rel
        elif isinstance(node, ast.Attribute) and node.attr == "acompletion":
            raise AssertionError(f"{rel}:{node.lineno} calls acompletion directly")


@pytest.mark.parametrize("rel", NO_MODEL_CALL)
def test_door_and_actions_make_no_model_call(rel: str) -> None:
    tree = ast.parse(_read(rel))
    bad = {"completion", "run_agent", "acompletion", "acompletion_with_fallback"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            assert name not in bad, f"{rel}:{node.lineno} calls {name}"
