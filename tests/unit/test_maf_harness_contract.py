"""WS43-F8 — the MAF harness contract that the ``code_task`` session uses (WS-43e).

Spec ``project-docs/specs/maf_coding_engine.md`` §4.4, §7.5, §7.6 and §10
WS43-F8. What breaks it: an ``agent-framework-core`` upgrade renames a file
tool or a ``create_harness_agent`` parameter that WS-43 uses, or the session
drops ``disable_file_memory=True`` or ``allow_concurrent_invocation=False``.

Mutations this suite catches (R7), each run red once by hand:

* ``HARNESS_FLAGS["disable_file_memory"]`` set to ``False``: the flags test
  and the real-build test;
* ``FUNCTION_INVOCATION`` set to ``{}``: the concurrency test;
* ``build_code_task_agent`` stops passing ``file_access_store``: the
  parameters test;
* a name in ``CODE_TASK_FILE_TOOLS`` changed: the file tool test.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

#: Every ``create_harness_agent`` parameter that the session passes.
USED_PARAMETERS = frozenset({
    "name", "agent_instructions", "tools", "file_access_store", "skills_provider",
    "middleware", "disable_todo", "disable_mode", "disable_web_search",
    "disable_file_memory", "file_access_disable_readonly_tool_approval",
    "file_access_disable_write_tool_approval",
})

#: The eight file tools of §4.4.
FILE_TOOLS = frozenset({
    "file_access_read", "file_access_read_lines", "file_access_write",
    "file_access_replace", "file_access_replace_lines", "file_access_ls",
    "file_access_grep", "file_access_delete",
})


def test_create_harness_agent_still_takes_every_parameter_the_session_uses() -> None:
    from agent_framework import create_harness_agent

    params = set(inspect.signature(create_harness_agent).parameters)
    assert params >= USED_PARAMETERS, USED_PARAMETERS - params


def test_the_file_tool_names_are_the_eight_of_the_spec() -> None:
    from acb_skills.sandbox_tools import CODE_TASK_FILE_TOOLS
    from agent_framework import FileAccessProvider

    names = {
        value for key, value in vars(FileAccessProvider).items()
        if key.endswith("_TOOL_NAME") and isinstance(value, str)
    }
    assert names == FILE_TOOLS
    assert CODE_TASK_FILE_TOOLS == FILE_TOOLS


def test_the_skill_tool_names_match_the_provider() -> None:
    from acb_skills.sandbox_tools import CODE_TASK_SKILL_TOOLS
    from agent_framework import SkillsProvider

    assert {
        SkillsProvider.LOAD_SKILL_TOOL_NAME,
        SkillsProvider.READ_SKILL_RESOURCE_TOOL_NAME,
        SkillsProvider.RUN_SKILL_SCRIPT_TOOL_NAME,
    } == CODE_TASK_SKILL_TOOLS


def test_the_session_flags_are_pinned() -> None:
    from orchestrator.code_session import FUNCTION_INVOCATION, HARNESS_FLAGS

    assert HARNESS_FLAGS == {
        "disable_todo": True,
        "disable_mode": True,
        "disable_web_search": True,
        "disable_file_memory": True,
        "file_access_disable_readonly_tool_approval": True,
        "file_access_disable_write_tool_approval": True,
    }
    assert FUNCTION_INVOCATION == {"allow_concurrent_invocation": False}


def test_the_invocation_configuration_still_has_the_concurrency_key() -> None:
    from agent_framework._tools import (
        FunctionInvocationConfiguration,
        normalize_function_invocation_configuration,
    )

    assert "allow_concurrent_invocation" in FunctionInvocationConfiguration.__annotations__
    kept = normalize_function_invocation_configuration({"allow_concurrent_invocation": False})
    assert kept["allow_concurrent_invocation"] is False


class _Guard:
    def hold(self) -> Any:
        class _Hold:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _Hold()

    async def prepare(self) -> None:
        return None

    def writes_refused(self) -> bool:
        return False


def _parts(tmp_path: Path) -> tuple[Any, Any, list[Any]]:
    from acb_skills.sandbox_tools import LockedSkillsSource, code_task_run_command
    from acb_skills.tenant_file_store import WorkspaceFileStore
    from agent_framework import SkillsProvider, tool

    store = WorkspaceFileStore(workspace=tmp_path, guard=_Guard(), member="m@example.com")
    skills = SkillsProvider(LockedSkillsSource(tmp_path, _Guard(), "m@example.com"))
    return store, skills, [tool(code_task_run_command, name="run_command")]


def test_build_passes_the_pinned_parameters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The session calls ``create_harness_agent`` with the store, the skills
    and every pinned flag, and its client keeps the tool calls in order."""
    import agent_framework
    from orchestrator import code_session

    seen: dict[str, Any] = {}

    def capture(client: Any, **kwargs: Any) -> str:
        seen["client"], seen["kwargs"] = client, kwargs
        return "agent"

    monkeypatch.setattr(agent_framework, "create_harness_agent", capture)
    store, skills, tools = _parts(tmp_path)
    code_session.build_code_task_agent(store=store, skills=skills, tools=tools, model="tier-balanced")
    kwargs = seen["kwargs"]
    assert set(kwargs) <= USED_PARAMETERS
    assert kwargs["file_access_store"] is store
    assert kwargs["skills_provider"] is skills
    for key, value in code_session.HARNESS_FLAGS.items():
        assert kwargs[key] is value
    config = seen["client"].function_invocation_configuration
    assert config["allow_concurrent_invocation"] is False


def test_the_real_build_has_no_file_memory_todo_mode_or_web_tool(tmp_path: Path) -> None:
    """With the real ``create_harness_agent``: no file memory under the
    gateway's working dir, no todo or mode provider, and no hosted tool."""
    from orchestrator import code_session

    store, skills, tools = _parts(tmp_path)
    agent = code_session.build_code_task_agent(
        store=store, skills=skills, tools=tools, model="tier-balanced",
    )
    kinds = {type(p).__name__ for p in agent.context_providers}
    assert "FileMemoryProvider" not in kinds
    assert "TodoProvider" not in kinds and "AgentModeProvider" not in kinds
    assert "FileAccessProvider" in kinds and "SkillsProvider" in kinds
    names = {getattr(t, "name", "") for t in agent.default_options.get("tools") or []}
    assert names == {"run_command"}, names
