"""projects-assistant — the AI chat inside the Projects app (WS-27bm).

Spec: ``project-docs/specs/projects_ai_chat.md``.

A native MAF agent whose every tool is a thin wrapper over the gateway's
``/projects/*`` routes, called **as the acting member**: internal bearer plus
``X-User-Email`` from the per-run ContextVar the executor binds. So the agent
inherits the route's rule and nothing else decides authority. It never opens a
database session and never holds SQL (``agent-crm/agents.py`` proved the
shape, and its header carries the reasoning).

The tool surface lives in ``apps/skills/skill-projects``. This file holds the
agent's identity and nothing that could disagree with the skill:

* ``skill_projects.__all__`` is the tool list. ``config.json: own_tool_scope``
  is held equal to it by ``tests/unit/test_projects_agent.py``.
* ``skill_projects.manifest.MANIFEST`` is the route allowlist, and
  ``tests/unit/test_projects_chat_coverage.py`` holds it against the real
  router.

Slice 1 (this file's first version) ships the reads only. The write tools
arrive in S2 and S3 with their confirmation cards, and D-PM-35 keeps the two
hard deletes off the surface until WS-40 answers who may delete.

Registered as a MAF agent named ``projects-assistant``; ``build_agents()`` is
the Dynamic Agent Loader entry point. ⚠️ The directory is ``agent-projects``,
the agent is ``projects-assistant`` — ``local_path`` in ``_AGENT_REGISTRY`` is
the whole mapping.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from acb_common import get_settings

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = (
    _INSTRUCTIONS_FILE.read_text(encoding="utf-8")
    if _INSTRUCTIONS_FILE.exists()
    else "You are the Projects assistant. Answer questions about projects and "
    "tasks with the provided tools, and cite the task number and title."
)

AGENT_NAME = "projects-assistant"

_TOOLS: list[Any] = []
try:
    import skill_projects

    _TOOLS = [getattr(skill_projects, name) for name in skill_projects.__all__]
except ImportError:
    # skill-projects not installed yet — the agent still boots, tool-less,
    # which the registration test reports rather than a silent half-load.
    pass


def _register_agent_tools() -> dict[str, Any]:
    """Tool map for the gateway's direct quick-action calls (importlib path)."""
    return {fn.__name__: fn for fn in _TOOLS}


# ── WS-46 P2: refusals the model reads (projects_agent_parity.md §7.5, §7.6) ──
#
# Each exported tool joins the agent as a strict MAF tool. Two things change,
# and no tool body changes:
#
# 1. Its function is `skill_projects.refusals.refusals_as_text(fn)`. A
#    GatewayRefusal becomes the tool's text answer. MAF would give the model
#    only "Error: Function failed.", and the model could not fix the call.
# 2. Its input model forbids an extra key. The schema the model reads then
#    says `additionalProperties: false`. MAF validates BEFORE the function
#    runs and answers a failure with "Error: Argument parsing failed.", which
#    names nothing. So `_StrictTool.invoke` checks the keys first and answers
#    an unknown one by name. MAF used to drop it in silence, and the call
#    then succeeded without it.
#
# Fences F4 and F5: tests/unit/test_projects_agent_refusals.py. The name,
# the description, the annotation and the tool list stay the same, so
# COVERED_PROJECTS_TOOLS (F6) and own_tool_scope (F7) do not change.


def _strict_model(model: Any) -> Any:
    """*model*, as a subclass that refuses a key it does not declare."""
    from pydantic import ConfigDict

    return type(
        model.__name__,
        (model,),
        {"model_config": ConfigDict(extra="forbid"), "__module__": model.__module__},
    )


_STRICT_TOOL: Any = None


def _strict_tool_class() -> Any:
    """The FunctionTool subclass for this agent's tools, made once."""
    global _STRICT_TOOL
    if _STRICT_TOOL is not None:
        return _STRICT_TOOL
    from agent_framework import SKIP_PARSING, Content, FunctionTool
    from pydantic import ValidationError
    from skill_projects.refusals import bad_value_text, unknown_argument_text

    class _StrictTool(FunctionTool):
        """A tool that answers an argument it does not declare by name."""

        def unknown_arguments(self, arguments: Any) -> list[str]:
            if not isinstance(arguments, Mapping) or self.input_model is None:
                return []
            declared = set(self.input_model.model_fields)
            return sorted(str(k) for k in arguments if k not in declared)

        def visible_arguments(self) -> list[str]:
            return list((self.parameters() or {}).get("properties", {}))

        def hidden_arguments(self, arguments: Any) -> list[str]:
            """Declared keys the schema hides, such as ``importance: Removed``.

            MAF checks the call against the schema too, and the schema now
            says ``additionalProperties: false``. So MAF would refuse a
            hidden key with "Argument parsing failed." The tool declares it
            for one reason: to answer an old call with the words that
            replace it (``skill_projects.priority``). So the tool answers.
            """
            if not isinstance(arguments, Mapping):
                return []
            shown = set(self.visible_arguments())
            return [str(k) for k in arguments if k not in shown]

        async def invoke(
            self,
            *,
            arguments: Any = None,
            context: Any = None,
            tool_call_id: str | None = None,
            skip_parsing: bool = False,
            **kwargs: Any,
        ) -> Any:
            given = arguments
            if given is None and not kwargs and context is not None:
                given = getattr(context, "arguments", None)
            def answer_with(text: Any) -> Any:
                if skip_parsing or self.result_parser is SKIP_PARSING:
                    return text
                return [Content.from_text(str(text))]

            unknown = self.unknown_arguments(given)
            if unknown:
                return answer_with(
                    unknown_argument_text(self.name, unknown, self.visible_arguments())
                )
            if self.hidden_arguments(given):
                # This branch skips MAF's invoke, so it also answers a value
                # that fails validation, as MAF would not.
                try:
                    values = self.input_model.model_validate(dict(given)).model_dump(
                        exclude_unset=True
                    )
                except ValidationError as exc:
                    bad = [str(e["loc"][0]) for e in exc.errors() if e.get("loc")]
                    return answer_with(bad_value_text(self.name, bad))
                answer = self(**values)
                if inspect.isawaitable(answer):
                    answer = await answer
                return answer_with(answer)
            return await super().invoke(
                arguments=arguments,
                context=context,
                tool_call_id=tool_call_id,
                skip_parsing=skip_parsing,
                **kwargs,
            )

    _STRICT_TOOL = _StrictTool
    return _STRICT_TOOL


def _agent_tool(fn: Any) -> Any:
    """*fn* as this agent registers it: refusals as text, strict arguments."""
    from agent_framework import FunctionTool
    from skill_projects.refusals import refusals_as_text

    wrapped = refusals_as_text(fn)
    name = fn.__name__
    description = fn.__doc__ or ""
    # MAF's own input model, from the signature, and then its strict subclass.
    # One source, so the schema keeps every field, default and hidden field
    # (`importance: Removed`) that MAF would build.
    base = FunctionTool(name=name, description=description, func=wrapped).input_model
    return _strict_tool_class()(
        name=name,
        description=description,
        func=wrapped,
        input_model=_strict_model(base),
    )


def _agent_tools() -> list[Any]:
    return [_agent_tool(fn) for fn in _TOOLS]


def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's ``/v1`` (litellm SDK)."""
    settings = get_settings()
    base_url = (
        os.environ.get("LITELLM_BASE_URL", "")
        or getattr(settings, "litellm_base_url", "")
        or "http://127.0.0.1:8080"
    ).rstrip("/")
    api_key = (
        os.environ.get("LITELLM_MASTER_KEY", "")
        or getattr(settings, "litellm_master_key", "")
        or "sk-local"
    )
    return {"type": "openai", "base_url": f"{base_url}/v1", "api_key": api_key}


def build_agents() -> list[Any]:
    """Construct the Projects assistant as a native MAF agent on the gateway's
    ``/v1``. ``OpenAIChatCompletionClient``, not ``OpenAIChatClient``: the
    latter speaks the Responses API, which ``v1_compat`` does not serve."""
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient
    from acb_llm.attribution import attributed_openai

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        # D-AI-4: the app declares a default tier. Balanced, until the rate
        # card is priced (H-42) and the owner picks otherwise (spec §12.3).
        model=os.environ.get("PROJECTS_AGENT_MODEL", "tier-balanced"),
        # Usage slice 1: `attributed_openai` adds the member, app and run to
        # EVERY request, from the run context. A fixed header cannot, because
        # one client serves everyone who chats with this agent.
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": AGENT_NAME, "X-CC-Source": "chat"},
        ),
    )
    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        name=AGENT_NAME,
        description=(
            "Projects assistant — reads the Projects app as the member "
            "who is asking: the tree of spaces and projects, a node's "
            "summary, task lists with the app's own filters, one task in "
            "full with its timeline, the member's own work, who could "
            "take a task, a project's vocabulary, the five analytics "
            "reads (stuck, load, throughput, finished, outlook), the team's "
            "capacity (who holds the work, their hours, skills and "
            "at-risk tasks, for a member with HR read access), who fits "
            "a task best and who could help whom (ranked by skill, spare "
            "hours and availability, for the same member), where the plan "
            "interferes with itself (dependencies out of order, late "
            "blockers, parallel work, and for the same member overcommitment, "
            "absence, leaving and work over a ceiling), a table of "
            "tasks or the server's exact groups over them for a "
            "question no analytics read answers, and the "
            "saved reports, rendered now. Creates and updates tasks and "
            "projects, assigns, comments, links, moves, watches, defers "
            "and completes, saves a report, edits the project's statuses, "
            "types, fields and tags, sets a repeat rule, captures a "
            "private task and files the member's own triage. Every write "
            "shows the member a card first and does nothing if they "
            "decline. Archives, restores and moves a project, archives, "
            "merges and bulk-edits tasks, deletes a status, type, field, "
            "tag, view, report, comment or attachment and reverts a "
            "change, one act per card with the counts on the card. Draws a "
            "timeline, a board, a task table and a report as cards, edits "
            "a task or a project from a form in the chat, plans a project "
            "from a goal as an editable plan, and writes a status report. "
            "Reads the calendar, the intake queue, notifications, watchers, "
            "saved views and who may see a project; captures and triages "
            "intake, saves a view and clears the bell. Opens a task, a "
            "project or an app in the member's page. It never deletes a "
            "project or a task."
        ),
        tools=_agent_tools(),
    )
    # WS-43d (maf_coding_engine.md §16.3, D86): the sandbox tools join THIS
    # run only, as a per-run view, and only when the broker covers this
    # agent for the run's own organization. Otherwise this is `agent`.
    return [_for_this_run(agent)]


def _for_this_run(agent: Any) -> Any:
    """*agent*, or a per-run view that carries the sandbox tools.

    ``acb_skills.sandbox_tools.attach_for_run`` decides, from the run's own
    tenant. With an empty ``MAF_CODING_SCOPE`` it returns *agent* itself.
    """
    try:
        from acb_skills.sandbox_tools import attach_for_run
    except ImportError:
        return agent
    return attach_for_run(agent, AGENT_NAME)


__all__ = ["AGENT_NAME", "INSTRUCTIONS", "_register_agent_tools", "build_agents"]
