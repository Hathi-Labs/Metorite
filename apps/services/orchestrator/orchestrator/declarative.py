"""The one builder every declarative agent runs on.

Spec: ``project-docs/specs/agent_architecture.md`` §1, §6.1.

Four of six first-party agents are 24-136 lines of boilerplate a manifest
expresses exactly: read ``instructions.md``, import a named set of skill tools,
pick a model tier, return one agent.  There is no control flow in any of them.
This module is what replaces that boilerplate — **one** code path shared by every
declarative agent, so observability, permissions, caching and memory improvements
land once instead of being re-implemented (or quietly overridden) per factory.

The override problem is not hypothetical: two agents set
``on_permission_request: PermissionHandler.approve_all`` in their own factory,
which pre-populated ``_permission_handler`` and made the executor's risk-aware
policy skip.  Nothing here sets a permission handler, a session, or a cancel
path — those belong to the platform, and a declarative agent has no factory in
which to take them back.

What this module deliberately does NOT do:

* **Inject platform tools.** The executor's ``_inject_agent_tools`` still owns
  that, gated by ``tool_scope``.  This builder supplies only the agent's own
  skill tools, exactly as a hand-written ``agents.py`` does today.
* **Resolve memory or integrations.** Run-time context assembly is unchanged.

Usage::

    from acb_skills.manifest import AgentManifest
    from orchestrator.declarative import build_declarative_agent

    manifest = AgentManifest.from_config(config, name="task-manager")
    agent = build_declarative_agent(manifest, agent_dir=Path(...))
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from acb_common import get_logger

_log = get_logger("orchestrator.declarative")

__all__ = [
    "DEFAULT_INSTRUCTIONS",
    "build_declarative_agent",
    "load_instructions",
    "resolve_skill_tools",
    "skill_module_name",
]

DEFAULT_INSTRUCTIONS = (
    "You are a Metorite agent. Use the provided tools to answer the "
    "user's request accurately, and cite a source URL whenever one is available."
)


def skill_module_name(repo: str) -> str:
    """``"skill-task-gtd"`` → ``"skill_task_gtd"``.

    ``config.json`` names skills by *repo* (hyphenated); they import by *module*
    (underscored).  The loader already relies on this correspondence when it puts
    a cloned skill on ``sys.path``.
    """
    return repo.strip().replace("-", "_")


def resolve_skill_tools(
    skill_repos: list[str] | None,
    *,
    own_tool_scope: list[str] | None = None,
) -> list[Any]:
    """Collect the callables a declarative agent's declared skills export.

    A skill package publishes its tool surface through ``__all__`` — verified
    against ``skill-task-gtd``, whose 29 exported callables are exactly the 29
    tools ``agent-task-manager/agents.py`` assembles by hand.  That equivalence is
    asserted in ``tests/unit/test_declarative_builder.py``.

    Args:
        skill_repos:    Repo names from ``config.json``'s ``skill_repos``.
        own_tool_scope: Optional allowlist, mirroring ``config.json``'s field of
                        the same name.  ``None`` keeps everything the skill
                        exports (today's behaviour for every first-party agent).

    Returns:
        Tool callables, de-duplicated, in declaration order.

    Missing or broken skills are logged and skipped rather than raised — the
    same fail-soft contract as the hand-written factories' ``except ImportError:
    pass``, so an agent still boots when a skill isn't installed yet.
    """
    tools: list[Any] = []
    seen: set[str] = set()
    allow = set(own_tool_scope) if own_tool_scope else None

    for repo in skill_repos or []:
        module_name = skill_module_name(repo)
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            _log.warning(
                "declarative.skill_import_failed",
                skill=repo, module=module_name, error=str(exc)[:200],
            )
            continue

        exported = getattr(module, "__all__", None)
        if not exported:
            _log.warning(
                "declarative.skill_exports_nothing",
                skill=repo,
                detail="no __all__; a skill must publish its tool surface",
            )
            continue

        for name in exported:
            if name in seen:
                continue
            if allow is not None and name not in allow:
                continue
            obj = getattr(module, name, None)
            if callable(obj):
                tools.append(obj)
                seen.add(name)

    if allow is not None:
        missing = allow - seen
        if missing:
            _log.warning(
                "declarative.own_tool_scope_no_match",
                missing=sorted(missing),
                detail="declared in own_tool_scope but not exported by any skill",
            )

    return tools


def load_instructions(agent_dir: Path | str | None) -> str:
    """Read ``instructions.md`` from the agent directory.

    Falls back to :data:`DEFAULT_INSTRUCTIONS` when absent — matching what each
    hand-written factory does inline today, so an agent whose instructions file
    is missing still runs instead of failing to construct.
    """
    if agent_dir is None:
        return DEFAULT_INSTRUCTIONS
    path = Path(agent_dir) / "instructions.md"
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    except Exception as exc:
        _log.warning("declarative.instructions_read_failed", path=str(path), error=str(exc)[:200])
    return DEFAULT_INSTRUCTIONS


def build_declarative_agent(
    manifest: Any,
    *,
    agent_dir: Path | str | None = None,
    instructions: str | None = None,
    extra_tools: list[Any] | None = None,
    client: Any = None,
) -> Any:
    """Build the MAF agent a manifest describes.

    Args:
        manifest:     An :class:`acb_skills.manifest.AgentManifest`.
        agent_dir:    Directory holding ``instructions.md``.  Ignored when
                      *instructions* is given.
        instructions: Explicit instructions, overriding the file.
        extra_tools:  Tools to append after the skill-resolved ones.
        client:       Chat client; defaults to the gateway ``/v1`` client, the
                      same one ``build_orchestrator_agent`` uses.

    Returns:
        A native MAF ``Agent``.  Never a Copilot agent — under
        ``agent_architecture.md`` §11 there is one runtime, and Copilot is a tool
        (``code_task``) an agent calls rather than a way to run one.

    Note the omissions.  No ``on_permission_request`` (the executor injects the
    risk-aware handler), no session wiring, no cancel path: platform concerns
    stay with the platform.
    """
    from agent_framework import Agent

    from orchestrator.agents import _make_openai_client

    tools = resolve_skill_tools(
        getattr(manifest.capabilities, "skills", []),
        own_tool_scope=getattr(manifest.capabilities, "own_tool_scope", None),
    )
    if extra_tools:
        tools = tools + list(extra_tools)

    resolved_instructions = (
        instructions if instructions is not None else load_instructions(agent_dir)
    )

    if client is None:
        client = _make_openai_client(agent_name=manifest.slug, source="chat")

    _log.info(
        "declarative.agent_built",
        agent=manifest.slug,
        tools=len(tools),
        skills=len(getattr(manifest.capabilities, "skills", []) or []),
        tier=manifest.isolation_tier(),
    )

    return Agent(
        client=client,
        name=manifest.slug,
        instructions=resolved_instructions,
        tools=tools,
    )
