"""A self-anneal retry holds no tool that the first attempt did not hold.

Access is an intersection, never a union. The batch run's first attempt
assembles its agents through ``executor._assemble_batch_agents``: the
``tool_scope``, the own scope, the skill toggles, D85, the floor opt-out,
the Mirror filter, the WhatsApp withholding and H-236. Until 2026-10-16 the
two self-anneal retries built the agents again by hand, with no
``tool_scope``, no own scope, no Mirror filter, no member filter on the
integrations, and a ``load_agent`` with no ``repo_name`` or ``local_path``.
So a retried run could see and call a tool that its scope, or a toggle read
again after a "connection" error, took away. A reviewer of PR #1064 found it.

Now each retry loads the same agent, assembles it through the same function,
and narrows the result to the names the first attempt held.

What breaks this fence (R7), each run red by hand on 2026-10-16:

* M1, the retry injects again with no ``tool_scope`` (the old code);
* M2, the retry drops ``held=``, so a toggle read again widens it;
* M3, the first attempt captures no held set;
* M4, ``_narrow_to_held`` keeps every tool;
* M5, the retry loads with no ``repo_name`` or ``local_path``;
* M6, the retry resolves the member's authority again, so a shared
  session's fallback to one actor can give it a credential;
* M7, the retry binds the ``run_tools`` that its injection returned, with
  neither narrowing (to the held set, and to the first attempt's);
* M8, the retry binds ``run_tools`` only when the first attempt bound some,
  so a delegated child that failed early answers from its parent's set;
* M9, a first attempt that resolved no credential leaves the memory gate of
  the retry open.

The model call, the store, the integrations and the toggle read are stubbed,
so the suite needs no database. The first attempt fails with a transient
error, so the retry runs. M1 also fails the WhatsApp case, by its scope: the
WhatsApp withholding rides a ContextVar, and the old retry kept it too.
"""
from __future__ import annotations

import asyncio
import json
import types
from contextlib import contextmanager
from typing import Any

import pytest

AGENT = "agent-annealscope"
ORG = "org-anneal"
REPO_NAME = "Org/agent-annealscope-src"


# The platform tools of this suite. Real names, so the family registry knows
# them: four core tools, one ``coding`` tool and one ``history`` tool.
async def web_search(query: str) -> str:
    return ""


async def write_artifact(path: str, content: str) -> str:
    return ""


async def manage_todo_list(items: str) -> str:
    return ""


async def save_note(text: str) -> str:
    return ""


async def github_search(query: str) -> str:
    return ""


async def query_history(query: str) -> str:
    return ""


_PLATFORM = [web_search, write_artifact, manage_todo_list, save_note,
             github_search, query_history]


async def read_inbox(limit: int = 10) -> str:
    """The agent's own tool, inside its own scope."""
    return ""


async def wipe_inbox() -> str:
    """The agent's own tool, outside its own scope."""
    return ""


class _NativeAgent:
    """The native MAF ``Agent`` shape: its tools live in ``default_options``."""

    def __init__(self) -> None:
        self.name = AGENT
        self.default_options: dict[str, Any] = {
            "tools": [read_inbox, wipe_inbox], "instructions": "",
        }


def _names(agents: list[Any]) -> frozenset[str]:
    """The names the model can call, read here and not through the code."""
    return frozenset(
        getattr(t, "__name__", "") for a in agents for t in a.default_options["tools"]
    )


@pytest.fixture
def run(tmp_path, monkeypatch):
    """One batch run of a shared agent whose first model call fails."""
    import acb_memory
    import acb_skills.integrations as integ
    import acb_skills.loader as loader
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes
    from orchestrator import _tool_injection as ti
    from orchestrator import app_tools, executor, workflow_tools

    clone = tmp_path / "agents" / "repos" / AGENT
    clone.mkdir(parents=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(settings, "custom_apps_root", str(tmp_path / "agents" / "apps"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [
        {"name": AGENT, "repo_name": REPO_NAME, "local_path": str(clone)},
    ])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])

    def _authorizer_fn(_name: str) -> bool:
        return False

    state: dict[str, Any] = {
        "config": {"name": AGENT}, "loads": [], "attempts": [],
        "disabled": [frozenset()], "bound": [], "holds": [], "make_report": [],
        "creds": [], "memory": [], "authorizer_calls": 0, "builds": 0,
        "integration_calls": 0,
        # Where the first attempt fails: "run" (the model call), "build"
        # (``build_agents``, before the assembly) or "integrations".
        "fail": "run",
        # One answer per authority lookup. The last one repeats.
        "authorizers": [_authorizer_fn],
    }

    def _build():
        state["builds"] += 1
        if state["fail"] == "build" and state["builds"] == 1:
            raise RuntimeError("connection timed out")
        return [_NativeAgent()]

    @contextmanager
    def _load(name, **kw):
        state["loads"].append(kw)
        cfg = json.loads(json.dumps(state["config"]))
        yield types.SimpleNamespace(
            agent_dir=clone, agent_name=name, config=cfg, build_agents=_build,
        )

    def _integrations(m, o, _s, *, is_authorized=None):
        state["integration_calls"] += 1
        if state["fail"] == "integrations" and state["integration_calls"] == 1:
            raise RuntimeError("connection reset by peer")
        names = list(m) + list(o)
        ok = {n: {"token": "x"} for n in names if is_authorized is None or is_authorized(n)}
        return ok, {n: "refused" for n in names if n not in ok}

    async def _authorizer(_payload, _thread=None):
        state["authorizer_calls"] += 1
        seq = state["authorizers"]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def _disabled(_name):
        # One answer per read. The last one repeats.
        seq = state["disabled"]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def _run_maf(agents, **kw):
        from acb_skills.memory_tools import _memory_permission
        from acb_skills.write_artifact import artifact_context, run_holds_tool

        state["attempts"].append(_names(agents))
        # What ``run_holds_tool`` answers on this attempt (§14.10).
        state["bound"].append(artifact_context().get("run_tools"))
        state["holds"].append(
            frozenset(fn.__name__ for fn in _PLATFORM if run_holds_tool(fn.__name__)),
        )
        state["make_report"].append(run_holds_tool("make_report"))
        state["creds"].append(sorted(kw.get("integrations") or {}))
        state["memory"].append(_memory_permission.get())
        if state["fail"] == "run" and len(state["attempts"]) == 1:
            raise RuntimeError("connection reset by peer")
        return {"answer": "ok"}

    async def _quiet(*_a, **_k):
        return ""

    async def _detect(*_a, **_k):
        return None

    async def _rehydrate(*_a, **_k):
        return 0

    async def _fast_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(executor, "load_agent", _load)
    monkeypatch.setattr(loader, "load_agent", _load)
    monkeypatch.setattr(executor, "build_integrations", _integrations)
    monkeypatch.setattr(integ, "build_integrations", _integrations)
    monkeypatch.setattr(executor, "_integration_authorizer", _authorizer)
    monkeypatch.setattr(executor, "_run_with_maf_agent", _run_maf)
    monkeypatch.setattr(executor, "_install_push_guard", _quiet)
    monkeypatch.setattr(executor, "_get_current_head", _quiet)
    monkeypatch.setattr(executor, "_detect_agent_commits", _detect)
    monkeypatch.setattr(acb_memory, "rehydrate_workspace", _rehydrate)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(ti, "_collect_injectable_platform_tools", lambda *_a, **_k: _PLATFORM)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", _disabled)
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(workflow_tools, "load_workflow_tools", lambda *_a, **_k: [])
    def _go(parent_run_tools: frozenset[str] | None = None) -> dict[str, Any]:
        from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

        with artifact_context_scope():
            if parent_run_tools is not None:
                # A batch parent that called this agent (call_agent). The
                # child's context derives from this one.
                bind_artifact_context(
                    agent_name="parent-agent", run_id="r-parent",
                    run_tools=parent_run_tools,
                )
            out = asyncio.run(executor.run_agent(
                AGENT, {"message": "hi"}, run_id="r-anneal", organization_id=ORG,
            ))
        assert out == {"answer": "ok"}
        want = 2 if state["fail"] == "run" else 1
        assert len(state["attempts"]) == want, "the retry never ran"
        return state

    state["go"] = _go
    return state


def _assert_retry_holds_the_first(state: dict[str, Any]) -> tuple[frozenset, frozenset]:
    first, retry = state["attempts"]
    assert retry <= first, f"the retry widened: {sorted(retry - first)}"
    assert retry == first, (sorted(first), sorted(retry))
    return first, retry


def test_a_scoped_agent_keeps_its_scope_on_the_retry(run) -> None:
    """``tool_scope`` and ``own_tool_scope`` hold on the retry (M1)."""
    run["config"] = {
        "name": AGENT, "tool_scope": ["query_history"], "own_tool_scope": ["read_inbox"],
    }
    first, _retry = _assert_retry_holds_the_first(run["go"]())
    assert "query_history" in first and "read_inbox" in first
    # Outside the scope and outside the core floor.
    assert "github_search" not in first
    # Outside the agent's own scope.
    assert "wipe_inbox" not in first


def test_a_disabled_family_stays_off_when_the_retry_reads_nothing(run) -> None:
    """The first read says ``coding`` is off. The read on the retry fails
    open (a "connection" error is what started the retry), so it says
    nothing. The retry still holds no ``coding`` tool (M2, M3, M4)."""
    run["disabled"] = [frozenset({"coding"}), frozenset()]
    first, _retry = _assert_retry_holds_the_first(run["go"]())
    assert "github_search" not in first
    assert "query_history" in first


def test_a_whatsapp_run_keeps_its_withholding_on_the_retry(run) -> None:
    """The web-only tools stay away from a retried WhatsApp run."""
    from acb_skills.whatsapp_ui import WITHHELD_TOOLS, whatsapp_run

    run["config"] = {"name": AGENT, "tool_scope": ["query_history"]}
    with whatsapp_run(AGENT):
        first, _retry = _assert_retry_holds_the_first(run["go"]())
    assert not (first & WITHHELD_TOOLS), sorted(first & WITHHELD_TOOLS)
    assert "write_artifact" not in first and "manage_todo_list" not in first
    assert "github_search" not in first


def test_the_retry_loads_the_same_agent(run) -> None:
    """The retry loads with the first attempt's ``repo_name`` and
    ``local_path``, so it reads the same ``config.json`` (M5)."""
    state = run["go"]()
    first, retry = state["loads"][0], state["loads"][-1]
    assert len(state["loads"]) == 2
    assert retry.get("repo_name") == first.get("repo_name") == REPO_NAME
    assert retry.get("local_path") == first.get("local_path")
    assert first.get("local_path")


def test_the_retry_gets_only_the_first_attempts_credentials(run) -> None:
    """The first attempt's filter refuses ``zoho-crm``. On the retry the
    authority would allow it (a shared session's fold fell back to one actor
    on a database fault). The retry resolves no authority, and gets no zoho
    (M6)."""
    def slack_only(name: str) -> bool:
        return name == "slack"

    run["config"] = {"name": AGENT, "integrations": ["zoho-crm"],
                     "optional_integrations": ["slack"]}
    run["authorizers"] = [slack_only, lambda _name: True]
    state = run["go"]()
    assert state["creds"] == [["slack"], ["slack"]], state["creds"]
    assert state["authorizer_calls"] == 1


def test_a_first_attempt_with_no_credential_gives_the_retry_none(run) -> None:
    """The first attempt fails while it builds the integrations. The retry
    then holds no credential and a closed memory gate, and it does not ask
    the authority again (M9)."""
    run["config"] = {"name": AGENT, "integrations": ["zoho-crm"]}
    run["fail"] = "integrations"
    run["authorizers"] = [lambda _name: True]
    state = run["go"]()
    assert state["creds"] == [[]], state["creds"]
    gate = state["memory"][0]
    assert gate is not None and gate("memory:write") is False
    assert state["authorizer_calls"] == 1  # the first attempt only


def test_a_retry_after_a_failure_before_the_assembly_keeps_the_scope(run) -> None:
    """``build_agents`` fails on the first attempt, so it held nothing yet.
    The retry assembles through the same function and keeps the scope."""
    run["config"] = {"name": AGENT, "tool_scope": ["query_history"]}
    run["fail"] = "build"
    (retry,) = run["go"]()["attempts"]
    assert "query_history" in retry and "github_search" not in retry


def test_a_delegated_child_never_answers_from_its_parents_run_tools(run) -> None:
    """A batch parent holds ``make_report``, and calls this agent, whose
    scope leaves it out. The child's first attempt fails in
    ``build_agents``, before it binds its own ``run_tools``. After the
    retry, ``run_holds_tool("make_report")`` is False (M8)."""
    run["config"] = {"name": AGENT, "tool_scope": ["query_history"]}
    run["fail"] = "build"
    state = run["go"](parent_run_tools=frozenset({"make_report", "github_search"}))
    assert state["make_report"] == [False]
    assert state["bound"] == [frozenset()], state["bound"]


# ── The two helpers, on every pool ───────────────────────────────────────────


def test_narrow_to_held_reads_every_pool() -> None:
    """The native pool, the Copilot ``_tools`` pool and the legacy Copilot
    ``_default_options["tools"]``, which ``_own_tool_pools`` does not read."""
    from orchestrator._tool_injection import _held_tool_names, _narrow_to_held

    native = types.SimpleNamespace(default_options={"tools": [web_search, github_search]})
    copilot = types.SimpleNamespace(_tools=[save_note, query_history], _default_options={})
    legacy = types.SimpleNamespace(_default_options={"tools": [write_artifact, wipe_inbox]})
    agents = [native, copilot, legacy]
    assert _held_tool_names(agents) == {
        "web_search", "github_search", "save_note", "query_history",
        "write_artifact", "wipe_inbox",
    }
    _narrow_to_held(agents, frozenset({"web_search", "save_note", "write_artifact"}))
    assert _held_tool_names(agents) == {"web_search", "save_note", "write_artifact"}
    assert native.default_options["tools"] == [web_search]
    assert copilot._tools == [save_note]
    assert legacy._default_options["tools"] == [write_artifact]


def test_narrow_to_held_never_adds() -> None:
    from orchestrator._tool_injection import _held_tool_names, _narrow_to_held

    agent = types.SimpleNamespace(default_options={"tools": [web_search]})
    _narrow_to_held([agent], frozenset({"web_search", "github_search"}))
    assert _held_tool_names([agent]) == {"web_search"}


# ── run_tools (§14.10, PR #1064): the retry never widens run_holds_tool ──────


def test_run_holds_tool_answers_the_same_after_the_retry(run) -> None:
    """The toggle read on the retry says nothing, so its injection returns
    ``github_search``. The retry still binds the first attempt's answer (M7)."""
    run["disabled"] = [frozenset({"coding"}), frozenset()]
    run["config"] = {"name": AGENT}
    state = run["go"]()
    first, retry = state["holds"]
    assert first, "the first attempt bound no run_tools"
    assert retry == first, (sorted(first), sorted(retry))
    assert "github_search" not in retry
    assert state["bound"][1] <= state["bound"][0]


def test_a_scoped_run_holds_the_same_tools_after_the_retry(run) -> None:
    run["config"] = {"name": AGENT, "tool_scope": ["query_history"]}
    state = run["go"]()
    first, retry = state["holds"]
    assert retry == first
    assert "query_history" in retry and "github_search" not in retry
