"""apis-config runs on native MAF. task-manager is HELD on the Copilot path.

Spec: ``project-docs/specs/agent_architecture.md`` §3.1, §11.3, §11.3.1.
Owner approval 2026-10-03. Split by the supervisor after the PR #585 review.

Both agents built a ``GitHubCopilotAgent`` while their ``config.json`` said
``"runtime": "maf"``. apis-config moved, and three things must hold for it.
Each test class pins one:

1. **The factory returns an ``agent_framework.Agent`` with the SAME tools.**
   ``_ORIGIN_MAIN_TOOLS`` is the tool-name set the Copilot factory built on
   origin/main (``f0264ce8``), copied by hand from a run of the old factory.
   It is a frozen list on purpose: a set derived from the current module would
   agree with whatever the module now says.
2. **The registry label agrees.** The executor reads ``_AGENT_REGISTRY``'s
   ``agent_runtime`` as well as the agent's shape, and the sub-agent path
   reads ONLY the label. A ``"github-copilot"`` label sends a MAF agent down
   the Copilot path, which assumes ``_default_options``.
3. **The executor runs it on Tier 1.** The real factory output goes through
   the real ``run_agent_stream``. Only ``agent.run`` is replaced, so no model
   is called. Tier 1 calls ``run(..., stream=True)`` and never touches the
   Copilot session store.

task-manager stays on Tier 1.5 until HANDOFF H-215. Its confirm turn needs
the ids in the tool output of the turn before, and Tier 1 sends earlier turns
as text only. ``TestTaskManagerIsHeldOnTheCopilotPath`` fails if someone
flips one half of it.

The agent modules load by file path, the way the Dynamic Agent Loader loads
them. No gateway process, no Redis and no model are started.
"""
from __future__ import annotations

import ast
import asyncio
import json
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.unit._native_maf_harness import (
    REPO_ROOT,
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    load_agent_module,
    parse_frames,
    text_turn,
    tool_turn,
)

#: The tool names the Copilot factory built on origin/main ``f0264ce8``
#: (``build_agents()[0]._tools``). Frozen, never derived — see the docstring.
_ORIGIN_MAIN_TOOLS: dict[str, frozenset[str]] = {
    "apis-config": frozenset({"web_search"}),
}

#: Registry name → agent directory. The registry's ``local_path`` is the real
#: mapping. ``test_the_registry_points_at_this_directory`` holds the two equal.
_AGENT_DIRS: dict[str, str] = {
    "apis-config": "apps/agents/agent-apis-config",
}

#: The agent held on the Copilot path until H-215.
_HELD = ("task-manager", "apps/agents/agent-task-manager")

AGENTS = sorted(_AGENT_DIRS)

pytest.importorskip("agent_framework", reason="agent_framework not installed")


def _load(name: str) -> ModuleType:
    """Import ``<agent dir>/agents.py`` under a unique module name."""
    return load_agent_module(_AGENT_DIRS[name])


def _tool_names(agent: Any) -> set[str]:
    tools = (getattr(agent, "default_options", None) or {}).get("tools") or []
    return {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools}


def _registry_entry(name: str) -> dict[str, Any]:
    routes_agent = pytest.importorskip(
        "gateway.routes.agent", reason="gateway not installed in this environment",
    )
    entry = next(
        (e for e in routes_agent._AGENT_REGISTRY if e.get("name") == name), None,
    )
    assert entry is not None, f"{name} missing from _AGENT_REGISTRY"
    return entry


# ── 1. The factory ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", AGENTS)
class TestTheFactoryBuildsANativeMafAgent:
    def test_build_agents_returns_one_agent_framework_agent(self, name: str) -> None:
        import agent_framework

        built = _load(name).build_agents()
        assert len(built) == 1
        agent = built[0]
        assert isinstance(agent, agent_framework.Agent)
        assert type(agent).__module__.startswith("agent_framework.")
        assert "Copilot" not in type(agent).__name__
        # A Copilot-SDK agent carries _default_options. The executor's
        # capability check reads exactly this attribute.
        assert not hasattr(agent, "_default_options")
        # Left unset, so no factory outranks platform policy (§3.2).
        assert getattr(agent, "_permission_handler", None) is None
        assert agent.name == name

    def test_the_tool_names_match_origin_main(self, name: str) -> None:
        agent = _load(name).build_agents()[0]
        names = _tool_names(agent)
        expected = _ORIGIN_MAIN_TOOLS[name]
        assert names == expected, (
            f"{name} tool surface changed in the move: "
            f"missing={sorted(expected - names)} extra={sorted(names - expected)}"
        )

    def test_the_instructions_are_the_agents_own_file(self, name: str) -> None:
        mod = _load(name)
        agent = mod.build_agents()[0]
        on_disk = (REPO_ROOT / _AGENT_DIRS[name] / "instructions.md").read_text(
            encoding="utf-8",
        )
        assert agent.default_options["instructions"] == on_disk == mod.INSTRUCTIONS

    def test_the_build_time_model_is_still_tier_balanced(self, name: str) -> None:
        """The Copilot factory pinned ``"model": "tier-balanced"`` and read no
        env var. The executor replaces it with the run's tier on either path."""
        agent = _load(name).build_agents()[0]
        assert agent.client.model == "tier-balanced"

    def test_the_client_attributes_every_request(self, name: str) -> None:
        """One client serves every member, so the member must come from the
        run on EACH request (``attributed_openai``), never from build time."""
        from acb_llm.attribution import _stamp

        agent = _load(name).build_agents()[0]
        oc = agent.client.client
        assert _stamp in oc._client.event_hooks["request"]
        assert oc.default_headers["X-CC-Agent"] == name
        assert oc.default_headers["X-CC-Source"] == "chat"
        assert "X-CC-Member" not in oc.default_headers
        assert str(oc.base_url).rstrip("/").endswith("/v1")

    def test_the_source_never_imports_the_copilot_sdk(self, name: str) -> None:
        src = (REPO_ROOT / _AGENT_DIRS[name] / "agents.py").read_text(encoding="utf-8")
        imported: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not {m for m in imported if "copilot" in m.lower()}, imported


# ── 2. The registry label ────────────────────────────────────────────────────


@pytest.mark.parametrize("name", AGENTS)
class TestTheRegistryAgrees:
    def test_the_registry_label_is_maf(self, name: str) -> None:
        assert _registry_entry(name)["agent_runtime"] == "maf"

    def test_the_config_declares_maf(self, name: str) -> None:
        cfg = json.loads(
            (REPO_ROOT / _AGENT_DIRS[name] / "config.json").read_text(encoding="utf-8"),
        )
        assert cfg["runtime"] == "maf"

    def test_the_registry_points_at_this_directory(self, name: str) -> None:
        assert _registry_entry(name)["local_path"] == _AGENT_DIRS[name]


# ── 3. The executor routes them to Tier 1 ────────────────────────────────────


def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def _update(contents: list[Any]) -> SimpleNamespace:
    return SimpleNamespace(role="assistant", contents=contents, message_id="m1")


def _drive_tier1(name: str, monkeypatch) -> dict[str, Any]:
    """Run the REAL factory output through the REAL ``run_agent_stream``.

    Only ``agent.run`` is replaced. The fake stream records what a tool would
    see at that moment: the run's artifact context and the run queue.
    """
    executor = pytest.importorskip(
        "orchestrator.executor", reason="orchestrator not installed",
    )
    routes_agent = pytest.importorskip(
        "gateway.routes.agent", reason="gateway not installed",
    )
    from acb_skills.write_artifact import artifact_context

    mod = _load(name)
    agent_dir = REPO_ROOT / _AGENT_DIRS[name]
    config = json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))
    seen: dict[str, Any] = {
        "run_calls": [], "inside": [], "agents": [],
        "copilot_session_reads": [], "hitl_handlers": [],
    }

    def _build() -> list[Any]:
        agents = mod.build_agents()
        agent = agents[0]

        def _fake_run(*_a: Any, **kw: Any) -> Any:
            seen["run_calls"].append(kw)

            async def _stream() -> Any:
                seen["inside"].append({
                    "artifact": dict(artifact_context()),
                    "queue_set": executor._active_run_queue.get(None) is not None,
                })
                yield _update([_text("native ")])
                yield _update([_text("answer")])

            return _stream()

        agent.run = _fake_run
        seen["agents"].append(agent)
        return agents

    class _Loaded:
        def __init__(self) -> None:
            self.agent_dir = agent_dir
            self.agent_name = name
            self.config = config

        def build_agents(self) -> list[Any]:
            return _build()

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    # The static registry only — a dev box's dynamic_agents rows must not decide.
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(
        executor, "_get_stored_session_id",
        lambda tid: seen["copilot_session_reads"].append(tid),
    )
    monkeypatch.setattr(
        executor, "_make_user_input_handler",
        lambda tid: seen["hitl_handlers"].append(tid),
    )

    async def _collect() -> list[str]:
        return [
            line async for line in executor.run_agent_stream(
                name, {"message": "hi"},
                run_id=f"run-{name}", thread_id=f"thread-{name}",
            )
        ]

    seen["events"] = parse_frames(asyncio.run(_collect()))
    return seen


@pytest.mark.usefixtures("_a_tenant")
@pytest.mark.parametrize("name", AGENTS)
def test_the_executor_runs_the_real_factory_output_on_tier_1(
    name: str, monkeypatch,
) -> None:
    seen = _drive_tier1(name, monkeypatch)
    events = seen["events"]
    types = [e.get("type") for e in events]

    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
    text = "".join(
        e.get("delta", "") for e in events if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )
    assert text == "native answer"
    # Tier 1 streams. The Tier 2 batch fallback calls run() without it.
    run_calls = seen["run_calls"]
    assert run_calls and all(c.get("stream") is True for c in run_calls), run_calls
    # Tier 1.5 reads the Copilot session store and binds the SDK's ask_user
    # handler. Neither may happen for a native agent.
    assert seen["copilot_session_reads"] == []
    assert seen["hitl_handlers"] == []


@pytest.mark.usefixtures("_a_tenant")
@pytest.mark.parametrize("name", AGENTS)
def test_tier_1_gives_this_agent_what_the_copilot_path_did(
    name: str, monkeypatch,
) -> None:
    """HITL, the injected tools and their B6 gate, and the run's artifact
    context (H-201 part 4) — each one present on the native path."""
    seen = _drive_tier1(name, monkeypatch)
    assert seen["inside"], "the agent never ran"
    inside = seen["inside"][0]

    # H-201 part 4: a tool that runs now sees THIS run's context. On the
    # Copilot path this needed carry_run_context. On Tier 1 it is inherited.
    ctx = inside["artifact"]
    assert ctx.get("agent_name") == name
    assert ctx.get("run_id") == f"run-{name}"
    assert ctx.get("session_id") == f"thread-{name}"
    assert ctx.get("workspace_root")

    # ask_questions parks on a Future (its Path A) only when the run queue is
    # set. Tier 1 sets it, so the HITL card blocks the turn as it should.
    assert inside["queue_set"] is True

    # The injected platform tools land on the MAF agent, wrapped by the B6
    # gate (functools.wraps leaves __wrapped__). Own tools keep their names.
    agent = seen["agents"][0]
    tools = agent.default_options["tools"]
    by_name = {getattr(t, "name", None) or getattr(t, "__name__", ""): t for t in tools}
    for injected in ("ask_questions", "write_artifact"):
        assert injected in by_name, f"{injected} was not injected into {name}"
        assert hasattr(by_name[injected], "__wrapped__"), f"{injected} is not gated"
    assert _ORIGIN_MAIN_TOOLS[name] <= set(by_name)


# ── 4. A follow-up turn of apis-config needs no earlier tool output ──────────


@pytest.mark.usefixtures("_a_tenant")
def test_an_apis_config_follow_up_turn_carries_the_list_it_answers(monkeypatch) -> None:
    """Why apis-config may move and task-manager may not (H-215).

    apis-config has no propose-then-apply tool. Its two-turn flow is "list
    the APIs of a company" and then "help with the ones I pick". Turn 2 needs
    the NAMES, and the assistant's own answer in turn 1 carries them. Tier 1
    sends that answer as text, so it reaches the model. The search results of
    turn 1 do not, and the agent searches again, which only reads.
    """
    import acb_skills.web_tools as web_tools

    hit_url = "https://developers.google.com/sheets/api/guides/authorizing"

    async def _fake_serp(query: str, max_results: int) -> list[dict]:
        return [{"title": "Sheets API auth", "href": hit_url, "body": "OAuth"}]

    monkeypatch.setattr(web_tools, "_serpapi_search", _fake_serp)
    answer = "Google has these APIs: Sheets, Drive, Calendar. Which ones?"
    first = ScriptedModel([tool_turn("web_search", '{"query": "Google APIs"}'), text_turn(answer)])
    events1, _ = drive_native(
        "apis-config", _AGENT_DIRS["apis-config"], monkeypatch, first,
        message="I want to connect Google", thread_id="thread-ac-two-turn",
    )
    assert [e.get("type") for e in events1][-1] == "RUN_FINISHED"
    turn1_tool = [m for m in first.bodies[-1]["messages"] if m.get("role") == "tool"]
    assert any(hit_url in str(m.get("content")) for m in turn1_tool)

    second = ScriptedModel([text_turn("Here are the steps for Sheets.")])
    events2, _ = drive_native(
        "apis-config", _AGENT_DIRS["apis-config"], monkeypatch, second,
        message="Sheets", thread_id="thread-ac-two-turn",
        history=[
            {"role": "user", "content": "I want to connect Google"},
            {"role": "assistant", "content": answer},
        ],
    )
    assert [e.get("type") for e in events2][-1] == "RUN_FINISHED"
    sent = json.dumps(second.bodies[0]["messages"])
    assert "Sheets, Drive, Calendar" in sent, "turn 2 lost the list it answers"
    # The gap H-215 names, and why it does not hurt this agent: the earlier
    # tool output is absent, and nothing in turn 2 needs an id from it.
    assert hit_url not in sent


# ── 5. task-manager is held on the Copilot path (H-215) ─────────────────────


class TestTaskManagerIsHeldOnTheCopilotPath:
    """Both halves move together, and only after H-215. A MAF factory with a
    ``github-copilot`` label ends every run in RUN_ERROR. A Copilot factory
    with a ``maf`` label loses its session resume on the sub-agent path."""

    def test_the_factory_still_builds_a_copilot_agent(self) -> None:
        pytest.importorskip("agent_framework_github_copilot")
        agent = load_agent_module(_HELD[1]).build_agents()[0]
        assert type(agent).__name__ == "GitHubCopilotAgent"
        assert isinstance(getattr(agent, "_default_options", None), dict)

    def test_the_registry_label_is_still_github_copilot(self) -> None:
        assert _registry_entry(_HELD[0])["agent_runtime"] == "github-copilot"
        assert _registry_entry(_HELD[0])["local_path"] == _HELD[1]
