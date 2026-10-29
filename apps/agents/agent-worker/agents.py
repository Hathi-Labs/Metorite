"""agent-worker -- the internal sub-agent of the spawn tool (WS-67 OA-9).

Spec: ``project-docs/specs/one_assistant_context.md`` §4.5 (OA-9a and OA-9b)
and §6. The main assistant starts a worker for ONE job. A worker only reads.

It is **internal**. It sits in ``gateway.routes.agent._INTERNAL_AGENTS`` and
in no list that a picker or a specialist tool reads. It refuses to run with no
job, and the refusal happens at run time, never at build time: the folder
fences build every agent of this directory with no job
(``subagent_contract.worker_refusal``).

The tools of a worker are the tools of its job (``config.json`` has only the
safe default scope). The executor injects them, and three layers hold the
child to them (§6.5): the parser refuses a bad job, the injection gives the
worker only the job and removes the floor, and **the allow-list pair of
``subagent_contract.child_pin`` is the control that counts**. It takes every
tool that is not in the job out of each model request and refuses a call to
one, so its body never runs. That covers the three floor tools that no opt-out
removes (``ask_questions``, ``emit_generative_ui`` and ``read_attachment``).

A child makes at most 4 model calls: ``max_iterations`` of 3 rounds, then the
one final call that MAF makes with the tools off. The wall clock of the job is
``subagent_contract.JOB_TIMEOUT_S``, and the spawn tool enforces it. The tool loop
has the shorter ``CHILD_LOOP_BUDGET_S``, so the final call runs before that clock.

Exports:
    build_agents() -> list[Agent]
    build_agent()  -> Agent
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from acb_common import get_settings

_INSTRUCTIONS_FILE = Path(__file__).parent / "instructions.md"
INSTRUCTIONS = _INSTRUCTIONS_FILE.read_text(encoding="utf-8") if _INSTRUCTIONS_FILE.exists() else (
    "You are a Metorite sub-agent. Do the one job you are given, only read, and "
    'end with one JSON object {"items": [{"value": ..., "kind": "quoted" | '
    '"inferred", "source": ...}]}.'
)

#: The name the registry, the run allowlist and the usage ledger know.
AGENT_NAME = "worker"

#: The build-time model. The executor replaces it with the tier of the job
#: (``_apply_model_for_maf_agent``), and a ``fast`` job sends this one.
MODEL = "tier-fast"


def _llm_provider() -> dict[str, Any]:
    """BYOK provider config pointing at the gateway's /v1.

    ⚠️ No member header here, on purpose (H-181): one agent serves every
    person. :func:`acb_llm.attribution.attributed_openai` stamps the member,
    the app and the run on EACH request, from the run context.
    """
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


def build_agent() -> Any:
    """Construct the worker as a NATIVE MAF agent for the job on this frame.

    With no job bound, the allow-list is empty, so the agent holds no tool at
    all. It never raises: the refusal is ``worker_refusal``, asked at run time.
    """
    from acb_llm.attribution import attributed_openai
    from acb_skills import subagent_contract as sc
    from agent_framework import Agent
    from agent_framework.openai import OpenAIChatCompletionClient

    job = sc.bound_job()
    allowed = frozenset(job.tools) if job is not None else frozenset()

    prov = _llm_provider()
    client = OpenAIChatCompletionClient(
        model=MODEL,
        async_client=attributed_openai(
            base_url=prov["base_url"],
            api_key=prov["api_key"],
            default_headers={"X-CC-Agent": AGENT_NAME, "X-CC-Source": "chat"},
        ),
        # 3 rounds, then MAF's one final call with the tools off: 4 calls.
        function_invocation_configuration={
            "max_iterations": sc.MAX_CHILD_ROUNDS - 1,
            "max_duration_seconds": sc.CHILD_LOOP_BUDGET_S,
        },
    )
    return Agent(
        client=client,
        instructions=INSTRUCTIONS,
        name=AGENT_NAME,
        description="Internal sub-agent: runs one read-only job and ends with items.",
        tools=[],
        middleware=sc.child_pin(allowed),
    )


def build_agents() -> list[Any]:
    """Dynamic Agent Loader entry point."""
    return [build_agent()]
