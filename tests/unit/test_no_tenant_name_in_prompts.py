"""No prompt that every tenant's agents read names one tenant (D99 follow-up).

Measured 2026-10-13: the orchestrator's own prompt began "You are the AI
Company Brain orchestrator for Fracktal Works." Every organization's general
assistant read that line, so a customer's assistant called itself the brain
of another company. The App Workshop prompt asked for "Fracktal-flavored"
sample data, and the Email, CRM and memory tool text used Fracktal names as
examples. The model reads tool docstrings as tool descriptions.

This fence reads the text those agents receive and refuses the name of a
tenant. Fracktal Works is customer zero, so its name is the one that leaks.
A new tenant's name would leak the same way, and this list grows with it.

What it does not cover: test fixtures, operator infrastructure (the GitHub
org of the agent repos) and comments that never reach a model.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Names of real organizations that must never reach a shared prompt.
TENANT_NAMES = re.compile(r"fracktal", re.IGNORECASE)

#: Every agent's own instructions file.
INSTRUCTIONS = sorted((ROOT / "apps" / "agents").glob("*/instructions.md"))

#: Python modules whose docstrings or string constants become prompt text.
PROMPT_MODULES = [
    ROOT / "apps" / "services" / "orchestrator" / "orchestrator" / "agents.py",
    ROOT / "packages" / "acb_skills" / "acb_skills" / "memory_tools.py",
]


def test_the_fence_reads_something() -> None:
    # A glob that finds nothing passes every case below vacuously.
    assert len(INSTRUCTIONS) >= 5
    assert all(p.exists() for p in PROMPT_MODULES)


@pytest.mark.parametrize("path", INSTRUCTIONS, ids=lambda p: p.parent.name)
def test_no_agent_instructions_name_a_tenant(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [line.strip() for line in text.splitlines() if TENANT_NAMES.search(line)]
    assert hits == [], f"{path.relative_to(ROOT)} names a tenant: {hits}"


def _prompt_strings(path: Path) -> list[str]:
    """Every docstring and module-level string constant: the text a model reads."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node)
            if doc:
                out.append(doc)
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out.append(node.value.value)
    return out


@pytest.mark.parametrize("path", PROMPT_MODULES, ids=lambda p: p.name)
def test_no_prompt_module_names_a_tenant(path: Path) -> None:
    hits = [s.splitlines()[0][:80] for s in _prompt_strings(path) if TENANT_NAMES.search(s)]
    assert hits == [], f"{path.relative_to(ROOT)} carries a tenant's name in prompt text: {hits}"


def test_the_orchestrator_says_whose_brain_it_is() -> None:
    # `orchestrator.agents` is a package that shadows `agents.py` and loads it
    # by path, so the constant is read from the module's own prompt strings.
    prompts = _prompt_strings(PROMPT_MODULES[0])
    pull = [s for s in prompts if s.startswith("You are the AI Company Brain orchestrator")]
    assert len(pull) == 1, "_PULL_INSTRUCTIONS not found in agents.py"
    assert "the member's own organization" in pull[0]
    assert not TENANT_NAMES.search(pull[0])
