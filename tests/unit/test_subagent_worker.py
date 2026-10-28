"""WS-67 OA-9a worker half: the internal ``worker`` agent (R7 fence).

Spec ``project-docs/specs/one_assistant_context.md`` §4.5, OA-9a narrowings 1
to 5, and §6.1. The worker runs ONE job of ``spawn_subagents``. These cases
hold what the spec names:

* the ``config.json`` of the worker (narrowing 4);
* a build with no job never raises, and the agent then holds no tool
  (narrowing 3, "at run, never at build");
* the refusal with no job, at the THREE callers that ask before any load:
  ``agent_tools.call_agent``, ``executor._run_sub_agent_streaming`` and
  ``executor.run_agent`` (narrowing 3);
* the worker is in no list that a picker or a specialist tool reads, and a
  dynamic agent with its name does not take its place (narrowing 5);
* the open set is the job, and the held set is the job without the egress
  tools (narrowing 2).

Hermetic: no database, no network. The model wire is replaced and the agent,
the executor seams and the injection are the real ones.

Mutations this file catches (each run red, then restored):

* M1 ``_INTERNAL_AGENTS`` is not searched first -> ``test_a_dynamic_agent_does_not_take_the_place``;
* M2 ``call_agent`` stops asking ``worker_refusal`` -> ``test_call_agent_refuses_the_worker_with_no_job``;
* M3 ``run_agent`` stops asking it -> ``test_run_agent_refuses_the_worker_with_no_job``;
* M4 ``_run_sub_agent_streaming`` stops asking it -> ``test_the_streaming_seam_refuses_...``;
* M5 the held set keeps a web tool -> ``test_the_held_set_is_the_job_without_the_egress_tools``;
* M6 the injection scope ignores the job -> ``test_the_open_set_is_exactly_the_job``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from acb_skills import subagent_contract as sc
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

from tests.unit._subagent_harness import (
    REPO_ROOT,
    WORKER_DIR,
    WireModel,
    build_worker,
    completion,
    counted,
)

CONFIG = json.loads((Path(REPO_ROOT) / WORKER_DIR / "config.json").read_text(encoding="utf-8"))
JOB_TOOLS = ("web_search", "fetch_page", "remember", "recall_timeline")


def _job(*tools: str) -> sc.Job:
    return sc.Job(number=1, job="Find the facts.", tools=tuple(tools), tier="fast")


@pytest.fixture()
def _frame():
    """An own artifact-context scope with a run bound, as an executor frame."""
    with artifact_context_scope():
        bind_artifact_context(run_id="run-w", agent_name="orchestrator", no_egress=False)
        yield


# ── The config.json of the worker (narrowing 4) ─────────────────────────────


def test_the_config_names_the_worker_and_its_tier() -> None:
    assert CONFIG["name"] == "worker"
    assert CONFIG["runtime"] == "maf"
    assert CONFIG["model_tier"] == "tier-fast"
    # test_usage_attribution needs a slug that the navigation shows.
    assert CONFIG["app"] == "chat"


def test_the_config_is_shared_and_not_shareable() -> None:
    """As for projects-assistant: D85 then withholds the shell tools."""
    assert CONFIG["sharing"] == {
        "instancing": "shared", "visibility": "organization",
        "shareable": False, "outputs_visibility": "instance",
    }


def test_the_opt_out_is_the_allowed_set_without_the_two_web_tools() -> None:
    """20 names: ``FLOOR_OPT_OUT_ALLOWED`` (22) without ``web_search`` and
    ``fetch_page``. An opted-out name leaves the scope even when the job names it."""
    from orchestrator._tool_injection import FLOOR_OPT_OUT_ALLOWED

    assert len(CONFIG["floor_opt_out"]) == 20
    assert set(CONFIG["floor_opt_out"]) == FLOOR_OPT_OUT_ALLOWED - sc.WEB_TOOLS
    assert len(set(CONFIG["floor_opt_out"])) == 20


def test_the_default_scope_is_not_empty_and_there_is_no_own_scope() -> None:
    """``[]`` reads as "inject every tool" (``config.get("tool_scope") or None``)."""
    assert CONFIG["tool_scope"] == ["web_search", "fetch_page"]
    assert "own_tool_scope" not in CONFIG


# ── No job: a build never raises, and the agent holds no tool (narrowing 3) ──


def test_a_build_with_no_job_never_raises_and_holds_no_tool() -> None:
    """The folder fences build every agent with no job. A raise at build turns
    ``test_agent_voice`` and ``test_chat_upload_every_agent`` red."""
    with artifact_context_scope():
        bind_artifact_context()
        assert sc.bound_job() is None
        calls: list[str] = []
        wire = WireModel([completion(calls=[("remember", "{}")]), completion(text="{}")])
        agent = build_worker(wire, [counted("remember", calls)])
        asyncio.run(agent.run("do it"))
    assert calls == []
    assert "remember" not in wire.tool_names(0)


def test_the_build_never_reads_the_job_of_another_frame() -> None:
    """The job lives on one frame. A frame that binds none builds a tool-less agent."""
    with artifact_context_scope():
        sc.bind_job(_job("remember"))
    with artifact_context_scope():
        assert sc.bound_job() is None


# ── The refusal with no job, at three callers (narrowing 3) ─────────────────


def test_worker_refusal_is_none_for_every_other_name(_frame) -> None:
    for name in ("projects-assistant", "email-assistant", "orchestrator", "", None, "Worker"):
        assert sc.worker_refusal(name) is None, name


def test_worker_refusal_answers_with_no_job_and_none_with_one(_frame) -> None:
    refusal = sc.worker_refusal("worker")
    assert refusal is not None and "spawn_subagents" in refusal
    sc.bind_job(_job("remember"))
    assert sc.worker_refusal("worker") is None


def test_a_value_that_is_not_a_job_reads_as_no_job(_frame) -> None:
    """Only :func:`bind_job` binds a job. A mapping that a tool or a payload
    puts under the key is not a :class:`Job`."""
    from acb_skills.write_artifact import derive_artifact_context

    derive_artifact_context(**{sc.JOB_KEY: {"tools": ["send_email"], "job": "x"}})
    assert sc.bound_job() is None
    assert sc.worker_refusal("worker") is not None


@pytest.fixture()
def _no_load(monkeypatch):
    """Any agent load fails the test: a refusal must come before every load."""
    from orchestrator import executor

    def _boom(*_a, **_k):
        raise AssertionError("an agent was loaded before the refusal")

    monkeypatch.setattr(executor, "load_agent", _boom)
    return executor


def test_call_agent_refuses_the_worker_with_no_job(_frame, _no_load) -> None:
    from acb_skills.agent_tools import call_agent

    text = asyncio.run(call_agent("worker", "do a thing"))
    assert text == sc.NO_JOB_REFUSAL


def test_call_agents_parallel_refuses_the_worker_with_no_job(_frame, _no_load) -> None:
    from acb_skills.agent_tools import call_agents_parallel

    text = asyncio.run(call_agents_parallel(json.dumps(
        [{"agent": "worker", "message": "do a thing"}])))
    assert sc.NO_JOB_REFUSAL in text


def test_the_streaming_seam_refuses_the_worker_with_no_job(_frame, _no_load) -> None:
    text = asyncio.run(_no_load._run_sub_agent_streaming("worker", "x", "run-1", None))
    assert text == sc.NO_JOB_REFUSAL


def test_run_agent_refuses_the_worker_with_no_job(_frame, _no_load) -> None:
    """The batch fallback of ``call_agent`` goes straight to ``run_agent``, so
    the streaming seam alone is not enough."""
    with pytest.raises(_no_load.AgentRunError) as err:
        asyncio.run(_no_load.run_agent("worker", {"message": "x"}, run_id="run-2"))
    assert str(err.value) == sc.NO_JOB_REFUSAL


def test_the_streaming_seam_loads_the_worker_for_a_job(_frame, monkeypatch) -> None:
    """The control: with a job bound, the refusal does not fire and the worker
    loads, from its folder."""
    from orchestrator import executor

    seen: dict[str, object] = {}

    def _stop(name, **kwargs):
        seen["name"], seen["local_path"] = name, kwargs.get("local_path")
        raise RuntimeError("stop after the load")

    monkeypatch.setattr(executor, "load_agent", _stop)
    sc.bind_job(_job("remember"))
    text = asyncio.run(executor._run_sub_agent_streaming("worker", "x", "run-3", None))
    assert seen == {"name": "worker", "local_path": "apps/agents/agent-worker"}
    assert "stop after the load" in text


# ── In no list that a picker or a specialist tool reads (narrowing 5) ───────


def test_the_worker_is_in_no_registry_list() -> None:
    from gateway.routes import agent as routes

    assert "worker" not in {a["name"] for a in routes._AGENT_REGISTRY}
    assert "worker" not in routes._KNOWN_AGENTS
    assert [a["name"] for a in routes._INTERNAL_AGENTS] == ["worker"]
    # And not in the deploy registry that the gateway reads at start.
    registry = json.loads((REPO_ROOT / "agent_registry.json").read_text(encoding="utf-8"))
    assert "worker" not in json.dumps(registry)


def test_only_the_executor_reads_the_internal_list() -> None:
    """The picker (``list_agents``), the workflow catalog, ``workspace.py``,
    ``observability.py`` and the specialist tools all read ``_AGENT_REGISTRY``.
    A list that none of them reads cannot show the worker. ``list_agents`` is
    not called here: it reads the alias table from a database."""
    readers = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for base in ("apps", "packages")
        for path in (REPO_ROOT / base).rglob("*.py")
        if "node_modules" not in path.parts
        and "_INTERNAL_AGENTS" in path.read_text(encoding="utf-8", errors="replace")
    )
    assert readers == [
        "apps/agents/agent-worker/agents.py",  # a comment
        "apps/services/gateway/gateway/routes/agent.py",  # the definition
        "apps/services/orchestrator/orchestrator/executor.py",  # the lookups
        "packages/acb_skills/acb_skills/subagent_contract.py",  # a comment
    ]


def test_the_specialist_tools_do_not_show_the_worker(monkeypatch) -> None:
    import orchestrator.agents as pkg
    from gateway.routes import agent as routes

    monkeypatch.setattr(routes, "_load_dynamic_agents", lambda: [])
    names = {getattr(t, "name", "") for t in pkg._mod._load_specialist_agents_as_tools()}
    assert "worker" not in names
    assert "projects_assistant" in names, "the list must not be empty"


def test_a_dynamic_agent_does_not_take_the_place(monkeypatch) -> None:
    """The three lookups by name search ``_INTERNAL_AGENTS`` FIRST."""
    from gateway.routes import agent as routes
    from orchestrator import executor

    fake = {"name": "worker", "local_path": "/evil/agent", "agent_runtime": "maf"}
    monkeypatch.setattr(routes, "_load_dynamic_agents", lambda: [fake])
    entry = next(e for e in executor._registry_entries() if e["name"] == "worker")
    assert entry["local_path"] == "apps/agents/agent-worker"


def test_the_lookups_by_name_all_use_the_internal_first_list() -> None:
    """The two runs that load a sub-agent by name (``_run_sub_agent_streaming``
    and ``_run_agent_inner``) read ``_registry_entries()``, and neither builds
    its own ``_load_dynamic_agents() + _AGENT_REGISTRY``."""
    import ast

    path = REPO_ROOT / "apps/services/orchestrator/orchestrator/executor.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for fn in ("_run_sub_agent_streaming", "_run_agent_inner"):
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == fn)
        body = ast.get_source_segment(src, node) or ""
        assert "_registry_entries()" in body, fn
        assert "_AGENT_REGISTRY" not in body, fn


# ── The open set and the held set (narrowing 2) ─────────────────────────────


def _request_tools(held: bool) -> list[str]:
    """The tools in the first model request of the real worker, injected as the
    executor injects them, for a job of :data:`JOB_TOOLS`. *held* is no_egress."""
    from orchestrator import _tool_injection as ti

    wire = WireModel([completion(text='{"items": []}')])
    with artifact_context_scope():
        bind_artifact_context(run_id="run-s", agent_name="orchestrator", no_egress=held)
        sc.bind_job(_job(*JOB_TOOLS))
        agent = build_worker(wire)
        agents = [agent]
        scope = sc.child_tool_scope("worker", CONFIG["tool_scope"])
        ti._inject_agent_tools(
            agents, is_sub_agent=True, tool_scope=scope, agent_name="worker",
            agent_config=CONFIG, no_egress=held,
        )
        asyncio.run(agents[0].run("do it"))
    return wire.tool_names(0)


@pytest.fixture()
def _approve_all(monkeypatch):
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")


def test_the_open_set_is_exactly_the_job(_approve_all) -> None:
    """Narrowing 2: open set = the tools of the job. The floor that no opt-out
    removes (``ask_questions``, ``emit_generative_ui``, ``read_attachment``) is
    held back by the allow-list pair."""
    assert sorted(_request_tools(held=False)) == sorted(JOB_TOOLS)


def test_the_held_set_is_the_job_without_the_egress_tools(_approve_all) -> None:
    """Narrowing 2: held set = open set minus ``{web_search, fetch_page}``."""
    held = set(_request_tools(held=True))
    assert held == set(JOB_TOOLS) - {"web_search", "fetch_page"}
    assert held == set(_request_tools(held=False)) - sc.WEB_TOOLS


def test_the_worker_reads_no_floor_tool_of_its_own_scope(_approve_all) -> None:
    """The scope of a job with no tool is the default scope, and the pair still
    holds the child to an empty allow-list."""
    from orchestrator import _tool_injection as ti

    wire = WireModel([completion(text='{"items": []}')])
    with artifact_context_scope():
        bind_artifact_context(run_id="run-e", agent_name="orchestrator", no_egress=False)
        sc.bind_job(_job())
        agents = [build_worker(wire)]
        ti._inject_agent_tools(
            agents, is_sub_agent=True,
            tool_scope=sc.child_tool_scope("worker", CONFIG["tool_scope"]),
            agent_name="worker", agent_config=CONFIG, no_egress=False,
        )
        asyncio.run(agents[0].run("do it"))
    assert wire.tool_names(0) == []


def test_child_tool_scope_follows_the_job_for_the_worker_only(_frame) -> None:
    assert sc.child_tool_scope("projects-assistant", ["a", "b"]) == ["a", "b"]
    assert sc.child_tool_scope("projects-assistant", None) is None
    assert sc.child_tool_scope("worker", None) == ["web_search", "fetch_page"]
    sc.bind_job(_job("remember", "recall_timeline"))
    assert sc.child_tool_scope("worker", None) == ["remember", "recall_timeline"]
