"""WS-54 IN-7 fence — no env that a customer-reachable child gets holds a GitHub token.

The gateway process holds the operator's ``GITHUB_TOKEN`` (``main.py`` sets it
for the Copilot model list). Code that a customer's run can start must never
see it. This test plants a sentinel in ``os.environ`` under every token name,
then asserts the sentinel is absent from the env of each runner that a customer
run can reach:

- ``child_env()``, the base of every ``subprocess`` child of the gateway;
- ``docker_env()``, the env of the ``docker`` CLI;
- ``copilot_env()``, the env of the Copilot CLI child. The SDK adds
  ``COPILOT_SDK_AUTH_TOKEN`` after this, from ``github_token``. That is the
  one operator-paid path (see the report of IN-7);
- ``run_script``'s ``_script_env``, even when the agent declares ``github``;
- the workflow module runner (``env={}``);
- the Docker sandbox container env.

A source scan also refuses ``env_values("GITHUB_TOKEN"...)`` as an ``extra=``
of any child env.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

SENTINEL = "ghp_SENTINEL_operator_token_must_not_reach_a_child"
TOKEN_NAMES = (
    "GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN", "GITHUB_COPILOT_TOKEN",
    "COPILOT_SDK_AUTH_TOKEN",
)
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _plant(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in TOKEN_NAMES:
        monkeypatch.setenv(name, SENTINEL)


def _clean(env: dict[str, str]) -> None:
    assert SENTINEL not in env.values(), "the sentinel reached the env"
    for name in TOKEN_NAMES:
        assert name not in env, name


def test_the_base_child_env_holds_no_github_token() -> None:
    from acb_common.child_env import child_env, copilot_env, docker_env

    _clean(child_env())
    _clean(docker_env())
    _clean(copilot_env())


def test_run_script_env_holds_no_github_token_even_when_github_is_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acb_skills import code_tools

    monkeypatch.setattr(code_tools, "_declared_integrations", lambda: ["github", "zoho-crm"])
    _clean(code_tools._script_env())


async def test_the_workflow_module_runner_gets_an_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.routes.workflows.engine import modules

    seen: list[Any] = []
    real = asyncio.create_subprocess_exec

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("env"))
        return await real(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    try:
        await modules.run_module_code("def run(inputs):\n    return {}\n", {})
    except Exception:  # the harness may fail on this host. Only the env matters.
        pass
    assert seen == [{}]


def test_the_docker_sandbox_env_holds_no_github_token() -> None:
    from orchestrator import sandbox_broker

    _clean(sandbox_broker.sandbox_env("abc123"))


_NAMED = re.compile(r"env_values\([^)]*(GITHUB_TOKEN|GH_TOKEN|COPILOT_GITHUB_TOKEN)")


def test_no_child_env_extra_names_a_github_token() -> None:
    hits: list[str] = []
    for root in ("apps", "packages"):
        for path in (REPO / root).rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            if _NAMED.search(path.read_text(encoding="utf-8", errors="replace")):
                hits.append(path.relative_to(REPO).as_posix())
    assert hits == [], hits
