"""Module Studio unit tests — the AST validator and the restricted runner.

The validator is the platform-contract rung that keeps modules pure
transforms (spec workflows_app.md §3.4/D5); the runner tests prove the
subprocess harness actually enforces what the validator promises.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from gateway.routes.workflows.engine.modules import (
    ModuleExecutionError,
    run_module_code,
    validate_module_code,
)

VALID = "def run(inputs):\n    total = sum(inputs.get('values', []))\n    return {'total': total}\n"


#: The org of each run here. ``MODULE_RUN_ORGS`` lists it (STU-Q15a), so the
#: runner tests reach the child. ``test_module_runs_off.py`` fences the switch.
ORG = "org-modules"


@pytest.fixture(autouse=True)
def _module_runs_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "module_run_orgs", ORG, raising=False)


def _messages(code: str) -> str:
    return " | ".join(v.message for v in validate_module_code(code))


# ── Validator ───────────────────────────────────────────────────────────────


def test_valid_module_passes() -> None:
    assert validate_module_code(VALID) == []


def test_helpers_and_constants_allowed() -> None:
    code = (
        "THRESHOLD = 10\n"
        "def scale(x):\n"
        "    return x * 2\n"
        "def run(inputs):\n"
        "    return {'v': scale(inputs.get('n', 0)) + THRESHOLD}\n"
    )
    assert validate_module_code(code) == []


@pytest.mark.parametrize(
    ("code", "fragment"),
    [
        ("import os\ndef run(inputs):\n    return {}\n", "Import"),
        ("def run(inputs):\n    __import__('os')\n    return {}\n", "__import__"),
        ("def run(inputs):\n    return {'x': open('/etc/passwd')}\n", "'open'"),
        ("def run(inputs):\n    return {'x': eval('1')}\n", "'eval'"),
        ("def run(inputs):\n    return {'x': ().__class__}\n", "__class__"),
        ("def run(inputs):\n    return {'x': inputs._private}\n", "._private"),
        ("def run(inputs):\n    return {'x': getattr(inputs, 'keys')}\n", "getattr"),
        ("class X:\n    pass\ndef run(inputs):\n    return {}\n", "ClassDef"),
        ("async def run(inputs):\n    return {}\n", "AsyncFunctionDef"),
        ("def run(inputs):\n    yield {}\n", "Yield"),
    ],
)
def test_forbidden_constructs_rejected(code: str, fragment: str) -> None:
    assert fragment in _messages(code)


def test_run_signature_enforced() -> None:
    assert "exactly one top-level" in _messages("def other(x):\n    return {}\n")
    assert "exactly one argument" in _messages("def run(a, b):\n    return {}\n")


def test_syntax_error_reported_not_raised() -> None:
    violations = validate_module_code("def run(inputs:\n    return {}")
    assert violations and "syntax error" in violations[0].message


# ── Runner (subprocess harness) ─────────────────────────────────────────────


def _run(code: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(run_module_code(code, inputs, organization_id=ORG))


def test_runner_executes_transform() -> None:
    assert _run(VALID, {"values": [1, 2, 3]}) == {"total": 6}


def test_runner_wraps_non_dict_result() -> None:
    out = _run("def run(inputs):\n    return 42\n", {})
    assert out == {"result": 42}


def test_runner_surfaces_module_exception() -> None:
    code = "def run(inputs):\n    return {'x': 1 / 0}\n"
    with pytest.raises(ModuleExecutionError, match="ZeroDivisionError"):
        _run(code, {})


def test_runner_revalidates_before_executing() -> None:
    with pytest.raises(ModuleExecutionError, match="validation"):
        _run("import os\ndef run(inputs):\n    return {}\n", {})


def test_runner_kills_infinite_loop() -> None:
    code = "def run(inputs):\n    while True:\n        pass\n"
    with pytest.raises(ModuleExecutionError, match="limit"):
        asyncio.run(run_module_code(code, {}, organization_id=ORG, timeout=2.0))


def test_harness_blocks_dynamic_import_at_runtime() -> None:
    """Even code that would slip a hypothetical validator gap cannot import:
    the harness's builtins allowlist simply has no __import__."""
    # Bypass the validator on purpose by testing the harness directly with a
    # name the validator would catch — the run must fail in the child too.
    code = "def run(inputs):\n    return {'m': __import__('os').getcwd()}\n"
    violations = validate_module_code(code)
    assert violations  # rung 1 catches it…
    # …and rung 2 (the harness) refuses it independently:
    from gateway.routes.workflows.engine import modules as m

    original = m.validate_module_code
    m.validate_module_code = lambda _c: []  # simulate the gap
    try:
        with pytest.raises(ModuleExecutionError, match=r"NameError|not allowed"):
            _run(code, {})
    finally:
        m.validate_module_code = original
