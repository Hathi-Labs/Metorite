"""WS43-F12 — app-builder on the MAF harness (WS-43h, D84, task 2280).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.8, §10 WS43-F12, and the
Custom Apps half of WS43-F13 (§7.5 rule A). The hermetic half: the broker
runs on a fake Docker, and the model is a fake gateway behind the REAL
OpenAI client (``_maf_gateway_fakes``, ``_native_maf_harness``).

What it proves:

* With ``app_builder:<org>`` in ``MAF_CODING_SCOPE``, the factory builds a
  MAF harness agent on the ONE factory of ``code_task``
  (``code_session.coding_harness_agent``), with no ``GitHubCopilotAgent``.
* The agent reads ``instructions.md`` unchanged, with the sandbox paths of the
  build scripts. Its own tools are MAF's eight file tools and ``run_command``.
* The file tools work in the app's own dir and nowhere else. ``run_command``
  runs in the broker's container, with the build-script copy and the vendor
  cache mounted read-only, and the app's ``.git`` covered.
* With the target off, for another org, or with no org, the factory builds
  the Copilot agent, unchanged.
* The executor takes the native path for the MAF object, with the registry
  label ``"maf"``, and the Copilot path for the Copilot object.
* The native session store loads and saves the harness agent's session.
* The checkpoint git of the App Workshop starts no hook and no file monitor.

Mutations this suite catches (R7), each run red once by hand:

* the factory skips the target check: the target-off tests;
* ``app_builder_run_command`` runs the command on the host: the host-trap
  test; the file store drops the dot rule: the ``.git`` test;
* ``agents.py`` does not read ``instructions.md``: the instructions test.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("agent_framework_github_copilot", reason="the Copilot fallback is needed")

from orchestrator import sandbox_broker as sb

from tests.unit._maf_gateway_fakes import FakeGateway, gateway  # noqa: F401
from tests.unit._native_maf_harness import (
    REPO_ROOT,
    ScriptedModel,
    drive_native,
    load_agent_module,
    text_turn,
    tool_turn,
)
from tests.unit._sandbox_broker_fakes import bound_run, flag_values, mounts_of
from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixtures by name
    ORG_A,
    ORG_B,
    Sandbox,
    host_trap,
    new_thread,
    sandbox,
    short_tmp,
)

AGENT = "app-builder"
REL_DIR = "apps/agents/agent-app-builder"
MEMBER = "builder@example.com"

#: The eight file tools of MAF (§4.4).
FILE_TOOLS = frozenset({
    "file_access_read", "file_access_read_lines", "file_access_write",
    "file_access_replace", "file_access_replace_lines", "file_access_ls",
    "file_access_grep", "file_access_delete",
})
BUILD_COMMAND = "node /opt/app-builder/build/build_t2.mjs ."


def _mod() -> Any:
    return load_agent_module(REL_DIR)


@pytest.fixture
def app(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> Path:  # noqa: F811
    """``app_builder:<ORG_A>`` in the scope, a vendor cache, and one app dir.

    The app dir holds its own ``.git``, as the App Workshop makes it.
    """
    sandbox.set_scope(monkeypatch, f"app_builder:{ORG_A}")
    vendor = short_tmp / "vendor-t2"
    (vendor / "node_modules" / "react").mkdir(parents=True)
    (vendor / "node_modules" / "react" / "index.js").write_text("x", encoding="utf-8")
    monkeypatch.setattr(sandbox.env["settings"], "custom_apps_t2_vendor_dir", str(vendor),
                        raising=False)
    ws = sandbox.env["apps"] / "my-app"
    (ws / ".git").mkdir(parents=True)
    (ws / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (ws / "app.json").write_text('{"name": "Old", "tier": "T1"}\n', encoding="utf-8")
    (ws / "index.html").write_text("<h1>Old title</h1>\n", encoding="utf-8")
    return ws


def _run(ws: Path, org: str, body: Any, *, thread: str | None = None, **bind: Any) -> Any:
    bind.setdefault("instance", "")
    bind.setdefault("member", MEMBER)
    with bound_run(org, agent=AGENT, thread=thread or new_thread(), workspace=str(ws),
                   **bind):
        return asyncio.run(body())


def _built(org: str | None, ws: Path | None = None) -> Any:
    """``build_agents()`` inside a bound run of *org*."""
    with bound_run(org, agent=AGENT, thread=new_thread(),
                   workspace=str(ws) if ws else None, instance="" if ws else None):
        agents = _mod().build_agents()
    assert len(agents) == 1
    return agents[0]


# ── the factory ─────────────────────────────────────────────────────────────


def test_the_target_builds_a_harness_agent_on_the_one_factory(
    app: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_framework
    from acb_skills.loader import is_copilot_agent
    from agent_framework_github_copilot import GitHubCopilotAgent
    from orchestrator.native_session_store import HISTORY_SOURCE_ID

    calls: list[dict[str, Any]] = []
    real_factory = agent_framework.create_harness_agent

    def spy(client: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_factory(client, **kwargs)

    monkeypatch.setattr(agent_framework, "create_harness_agent", spy)
    agent = _built(ORG_A, app)
    assert len(calls) == 1, "the agent is not built by create_harness_agent"
    assert not isinstance(agent, GitHubCopilotAgent) and not is_copilot_agent(agent)
    kinds = {type(p).__name__ for p in agent.context_providers}
    assert "FileAccessProvider" in kinds
    assert not kinds & {"TodoProvider", "AgentModeProvider", "FileMemoryProvider"}
    assert HISTORY_SOURCE_ID in {p.source_id for p in agent.context_providers}
    assert not any(type(m).__name__ == "ToolApprovalMiddleware" for m in agent.middleware or [])
    # The code_task flags, and the one more flag of a chat agent.
    from orchestrator.code_session import HARNESS_FLAGS

    for key, value in HARNESS_FLAGS.items():
        assert calls[0][key] is value
    assert calls[0]["disable_tool_auto_approval"] is True


def test_the_agent_reads_instructions_md_with_the_sandbox_paths(app: Path) -> None:
    mod = _mod()
    raw = (REPO_ROOT / REL_DIR / "instructions.md").read_text(encoding="utf-8")
    agent = _built(ORG_A, app)
    text = agent.default_options.get("instructions") or ""
    expected = raw.replace(
        "__T2_BUILD_SCRIPT__", f"node {sb.APP_BUILDER_BUILD_TARGET}/build_t2.mjs",
    ).replace(
        "__T2_INSTALL_SCRIPT__", f"node {sb.APP_BUILDER_BUILD_TARGET}/install_t2_deps.mjs",
    )
    assert expected in text, "the agent does not read instructions.md unchanged"
    assert "## Your workspace IS the app" in text
    assert "__T2_" not in text
    assert str(mod._BUILD_SCRIPT) not in text, "a host path reached the sandbox agent"
    assert BUILD_COMMAND.removesuffix(" .") in text


def test_the_own_tools_are_the_file_tools_and_run_command(
    app: Path, gateway: FakeGateway,  # noqa: F811
) -> None:
    agent = _built(ORG_A, app)
    gateway.say("Done.")
    _run(app, ORG_A, lambda: agent.run("hello"))
    assert gateway.tool_names(0) == FILE_TOOLS | {"run_command"}
    headers = gateway.requests[0]["headers"]
    assert headers["x-cc-agent"] == AGENT and headers["x-cc-source"] == "chat"


# ── the file tools: the app's own dir, and nowhere else ─────────────────────


def test_the_file_tools_edit_the_app_and_refuse_every_other_path(
    app: Path, gateway: FakeGateway, sandbox: Sandbox, short_tmp: Path,  # noqa: F811
) -> None:
    outside = short_tmp / "outside.txt"
    outside.write_text("SECRET", encoding="utf-8")
    gateway.say(
        [("file_access_read", {"file_name": "app.json"}),
         ("file_access_write", {"file_name": "index.html",
                                "content": "<h1>New title</h1>\n", "overwrite": True}),
         ("file_access_write", {"file_name": "../escape.txt", "content": "x"}),
         ("file_access_write", {"file_name": ".git/config", "content": "[core]\nevil\n",
                                "overwrite": True}),
         ("file_access_read", {"file_name": str(outside)}),
         ("file_access_ls", {})],
        "Changed the title.",
    )
    agent = _built(ORG_A, app)
    _run(app, ORG_A, lambda: agent.run("change the title"))
    results = gateway.tool_results(1)
    assert '"name": "Old"' in results[0]
    assert (app / "index.html").read_text(encoding="utf-8") == "<h1>New title</h1>\n"
    assert not (app.parent / "escape.txt").exists()
    assert (app / ".git" / "config").read_text(encoding="utf-8") == "[core]\n"
    assert all(r.startswith("Could not") for r in results[2:5]), results[2:5]
    assert "SECRET" not in " ".join(results)
    listed = results[5]
    assert "index.html" in listed and ".git" not in listed
    assert sandbox.mirrored == [], "an app file reached the blob store"


def test_a_link_in_the_app_dir_is_refused(
    app: Path, gateway: FakeGateway, short_tmp: Path,  # noqa: F811
) -> None:
    target = short_tmp / "host-secret.txt"
    target.write_text("HOST SECRET", encoding="utf-8")
    try:
        (app / "planted.txt").symlink_to(target)
    except OSError:
        pytest.skip("this box cannot make a symlink")
    gateway.say([("file_access_read", {"file_name": "planted.txt"})], "ok")
    agent = _built(ORG_A, app)
    _run(app, ORG_A, lambda: agent.run("read it"))
    assert "HOST SECRET" not in " ".join(gateway.tool_results(1))


# ── run_command: the broker's container, never the host ────────────────────


def test_run_command_runs_the_build_in_the_container_with_the_app_mounts(
    app: Path, gateway: FakeGateway, sandbox: Sandbox, host_trap: list[str],  # noqa: F811
) -> None:
    gateway.say([("run_command", {"command": BUILD_COMMAND})], "Built.")
    agent = _built(ORG_A, app)
    _run(app, ORG_A, lambda: agent.run("build it"))
    assert host_trap == [], f"a host process started: {host_trap}"
    assert [c[-1] for c in sandbox.docker.command_execs()] == [BUILD_COMMAND]
    assert any("exit 0" in r for r in gateway.tool_results(1))
    runs = sandbox.docker.runs()
    assert len(runs) == 1
    argv = runs[0]
    assert flag_values(argv, "--network") == ["none"]
    assert f"metorite.agent={AGENT}" in argv and f"metorite.org={ORG_A}" in argv
    mounts = mounts_of(argv)
    assert f"type=bind,source={app.resolve()},target=/workspace" in mounts
    assert any(m.endswith("target=/workspace/.git,readonly") for m in mounts), mounts
    build = [m for m in mounts if f"target={sb.APP_BUILDER_BUILD_TARGET}" in m]
    vendor = [m for m in mounts if f"target={sb.T2_VENDOR_TARGET}" in m]
    assert len(build) == 1 and build[0].endswith(",readonly")
    assert len(vendor) == 1 and vendor[0].endswith(",readonly")
    assert len(mounts) == 4, f"an app-builder mount that the code does not list: {mounts}"
    copy = Path(build[0].split("source=", 1)[1].split(",", 1)[0])
    assert sorted(p.name for p in copy.iterdir()) == sorted(sb.APP_BUILDER_BUILD_FILES)
    assert str(REPO_ROOT) not in build[0], "the agent dir itself was mounted"
    env = flag_values(argv, "--env")
    assert f"{sb.T2_VENDOR_ENV}={sb.T2_VENDOR_TARGET}" in env
    joined = " ".join(env).upper()
    assert "KEY" not in joined and "TOKEN" not in joined and "PROXY" not in joined


def test_a_vendor_cache_that_holds_git_is_refused(
    app: Path, gateway: FakeGateway, sandbox: Sandbox, short_tmp: Path,  # noqa: F811
) -> None:
    """Rule 5 of §7.1 binds the app-builder list: no source with ``.git``."""
    (short_tmp / "vendor-t2" / ".git").mkdir()
    gateway.say([("run_command", {"command": "true"})], "ok")
    agent = _built(ORG_A, app)
    _run(app, ORG_A, lambda: agent.run("build it"))
    assert sandbox.docker.runs() == []
    assert "Nothing ran on the host" in " ".join(gateway.tool_results(1))


def test_run_command_refuses_the_tenant_dir(
    sandbox: Sandbox, app: Path, gateway: FakeGateway,  # noqa: F811
) -> None:
    """A delegated run works in ``o:<org>``, which holds every thread's files.
    No container mounts it whole (the rule of WS-43e for ``code_task``)."""
    from acb_skills.agent_paths import ensure_state_dir

    tenant = ensure_state_dir(AGENT, f"o:{ORG_A}")
    gateway.say([("run_command", {"command": "ls"})], "ok")
    agent = _built(ORG_A, app)
    _run(tenant, ORG_A, lambda: agent.run("ls"), instance=f"o:{ORG_A}")
    assert sandbox.docker.runs() == []
    assert "only in an app's own workspace" in " ".join(gateway.tool_results(1))


def test_a_flip_off_ends_the_tools_at_once(
    app: Path, gateway: FakeGateway, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Each call reads the scope again, so a flip off refuses every tool."""
    agent = _built(ORG_A, app)
    sandbox.set_scope(monkeypatch, "")
    gateway.say(
        [("run_command", {"command": "true"}),
         ("file_access_write", {"file_name": "index.html", "content": "x", "overwrite": True})],
        "ok",
    )
    _run(app, ORG_A, lambda: agent.run("go"))
    results = " ".join(gateway.tool_results(1))
    assert results.count("MAF_CODING_SCOPE does not name app_builder") == 2, results
    assert sandbox.docker.runs() == []
    assert (app / "index.html").read_text(encoding="utf-8") == "<h1>Old title</h1>\n"


def test_a_run_of_another_org_cannot_use_the_dir_of_this_one(
    app: Path, gateway: FakeGateway, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """R5: the org is the run binding. A tenant dir of org A in a run of org B
    is refused, and no container starts."""
    from acb_skills.agent_paths import ensure_state_dir

    sandbox.set_scope(monkeypatch, f"app_builder:{ORG_A},app_builder:{ORG_B}")
    ws_a = ensure_state_dir(AGENT, f"o:{ORG_A}")
    agent = _built(ORG_B, app)
    gateway.say([("file_access_ls", {})], "ok")
    _run(ws_a, ORG_B, lambda: agent.run("ls"), instance=f"o:{ORG_A}")
    assert "another organization" in " ".join(gateway.tool_results(1))


# ── the target off: the Copilot agent, unchanged ────────────────────────────


@pytest.mark.parametrize("scope", ["", f"app_builder:{ORG_B}", f"code_task:{ORG_A}"])
def test_the_target_off_keeps_the_copilot_agent(
    app: Path, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch, scope: str,  # noqa: F811
) -> None:
    from acb_skills.loader import is_copilot_agent
    from agent_framework_github_copilot import GitHubCopilotAgent

    sandbox.set_scope(monkeypatch, scope)
    agent = _built(ORG_A, app)
    assert isinstance(agent, GitHubCopilotAgent) and is_copilot_agent(agent)
    assert agent._default_options["system_message"]["content"] == _mod().INSTRUCTIONS


def test_a_run_with_no_org_keeps_the_copilot_agent(
    app: Path, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from acb_skills.loader import is_copilot_agent

    sandbox.set_scope(monkeypatch, "app_builder:*")
    with bound_run(None, agent=AGENT, thread=new_thread()):
        built = _mod().build_agents()
    assert is_copilot_agent(built[0]), "a run with no tenant got an unsandboxed MAF agent"


# ── the executor: the object decides the path ──────────────────────────────


def test_the_registry_label_is_maf_and_the_object_decides(app: Path) -> None:
    from gateway.routes.agent import _AGENT_REGISTRY
    from orchestrator.executor import _is_copilot_built

    label = next(e for e in _AGENT_REGISTRY if e["name"] == AGENT)["agent_runtime"]
    assert label == "maf"
    assert _is_copilot_built(_built(ORG_A, app), label) is False
    with bound_run(None, agent=AGENT, thread=new_thread()):
        copilot = _mod().build_agents()[0]
    assert _is_copilot_built(copilot, label) is True


@pytest.fixture
def stream_tenant(
    app: Path, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> Any:
    """Bind ORG_A as a request does, bind the app dir to the chat, and spy on
    the Copilot sandbox step, which must not run on the MAF path."""
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator import executor

    monkeypatch.setattr(executor, "_session_workspace_override", lambda *_a, **_k: str(app))
    copilot_steps: list[Any] = []

    async def no_copilot_sandbox(**kw: Any) -> None:
        copilot_steps.append(kw)

    monkeypatch.setattr(executor, "_maybe_sandbox_session_workspace", no_copilot_sandbox)
    token = bind_tenant(ORG_A)
    try:
        yield copilot_steps
    finally:
        release_tenant(token)
        from acb_skills.write_artifact import bind_artifact_context

        bind_artifact_context()
        executor._RUN_QUEUES.clear()
        executor._pending_user_input.clear()


def test_the_executor_runs_the_maf_agent_and_the_build_in_the_container(
    app: Path, sandbox: Sandbox, stream_tenant: list[Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local walk, with a scripted model (the stack has no AI key): the
    member asks to change one line, the file changes in the app dir, and the
    build runs in the container. The registry entry routes the run."""
    model = ScriptedModel([
        tool_turn("file_access_write", json.dumps({
            "file_name": "index.html", "content": "<h1>New title</h1>\n", "overwrite": True,
        }), "c1"),
        tool_turn("run_command", json.dumps({"command": BUILD_COMMAND}), "c2"),
        text_turn("I changed the title and ran the build."),
    ])
    events, built = drive_native(AGENT, REL_DIR, monkeypatch, model,
                                 message="Change the title to New title.",
                                 thread_id=new_thread())
    from acb_skills.loader import is_copilot_agent

    assert built and not is_copilot_agent(built[0])
    kinds = [e.get("type") for e in events]
    assert "RUN_FINISHED" in kinds and "RUN_ERROR" not in kinds, kinds
    assert (app / "index.html").read_text(encoding="utf-8") == "<h1>New title</h1>\n"
    assert [c[-1] for c in sandbox.docker.command_execs()] == [BUILD_COMMAND]
    assert stream_tenant == [], "the Copilot sandbox step ran on the MAF path"
    # The executor's own tools joined, and the host file and web tools did not.
    offered = {t["function"]["name"] for t in model.bodies[0].get("tools") or []}
    assert {"run_command", "file_access_write", "ask_questions"} <= offered, offered
    assert not offered & sb_withheld(), offered & sb_withheld()


def sb_withheld() -> set[str]:
    from acb_skills.sandbox_tools import WITHHELD_HOST_TOOLS

    return set(WITHHELD_HOST_TOOLS)


# ── the native session store (WS-43t2) ──────────────────────────────────────


def test_the_session_store_keeps_the_harness_history() -> None:
    """The harness agent holds the store's own provider. The store does not
    skip the run for it, and the run gets no second provider beside it."""
    from types import SimpleNamespace

    from agent_framework import InMemoryHistoryProvider
    from orchestrator._native_run_context import agent_for_run
    from orchestrator.executor import _native_session_skip
    from orchestrator.native_session_store import SessionHistoryProvider

    own = SessionHistoryProvider()
    agent = SimpleNamespace(context_providers=[own])
    payload = {"message": "yes"}
    assert _native_session_skip(agent, AGENT, payload, run_id="r", caller_thread_id="t",
                                delegated=False) is None
    other = SimpleNamespace(context_providers=[InMemoryHistoryProvider()])
    assert _native_session_skip(other, AGENT, payload, run_id="r", caller_thread_id="t",
                                delegated=False) == "agent_history_provider"

    class _Agent:
        def __init__(self) -> None:
            self.context_providers = [own]

    view = agent_for_run(_Agent(), None, history=SessionHistoryProvider())
    assert [p.source_id for p in view.context_providers].count(own.source_id) == 1


def test_a_confirm_turn_sees_the_tool_output_of_the_turn_before(
    app: Path, sandbox: Sandbox,  # noqa: F811
) -> None:
    """Two turns on one stored session: the second request holds the first
    turn's tool call and its result (H-215, the confirm turn)."""
    from agent_framework import AgentSession
    from orchestrator.native_session_store import HISTORY_SOURCE_ID

    model = ScriptedModel([
        tool_turn("file_access_read", json.dumps({"file_name": "app.json"}), "c1"),
        text_turn("Rename it to New?"),
        text_turn("Done."),
    ])
    agent = _built(ORG_A, app)
    _wire(agent, model)
    session = AgentSession()

    async def two_turns() -> None:
        async for _u in agent.run("rename the app", stream=True, session=session):
            pass
        async for _u in agent.run("yes", stream=True, session=session):
            pass

    _run(app, ORG_A, two_turns)
    roles = [m["role"] for m in model.bodies[2]["messages"]]
    assert roles.count("tool") == 1 and roles[-1] == "user", roles
    assert any('"name": "Old"' in str(m.get("content")) for m in model.bodies[2]["messages"])
    stored = session.state[HISTORY_SOURCE_ID]["messages"]
    assert [m.role for m in stored].count("user") == 2


def _wire(agent: Any, model: ScriptedModel) -> None:
    import httpx

    oc = agent.client.client
    oc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(model), event_hooks=oc._client.event_hooks,
    )


# ── R8: the harness session through the REAL store ──────────────────────────
#
# The phase-4 catalog of the H3 rehearsal, as its NOBYPASSRLS role. The store
# loads and saves the session that the harness agent's own provider writes,
# so the confirm turn sees the tool output of the turn before (H-215).

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402,F401 — fixtures by name
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_native_session_persistence import (  # noqa: E402,F401 — fixtures by name
    _ALICE,
    _agent_row,
    _chat,
    _session_catalog,
    _tid,
    _user_row,
    graph_as_app,
)


@_DB_GATE
def test_r8_the_store_keeps_the_harness_session_for_the_confirm_turn(
    graph_as_app: Any, app: Path, sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from orchestrator import native_session_store as store
    from orchestrator._native_run_context import agent_for_run
    from orchestrator.executor import _native_session_skip

    org, tid = graph_as_app.org_a, _tid()
    sandbox.set_scope(monkeypatch, f"app_builder:{org}")
    _chat(org, tid, agent=AGENT)
    model = ScriptedModel([
        tool_turn("file_access_read", json.dumps({"file_name": "app.json"}), "c1"),
        text_turn("Rename it to New?"),
        text_turn("Done."),
    ])

    def turn(current: str) -> Any:
        return asyncio.run(store.begin_turn(
            organization_id=org, thread_id=tid, agent_name=AGENT, instance="",
            current_message=current, browser_history=None, token_budget=50_000,
            actor=_ALICE,
        ))

    def run(agent: Any, loaded: Any, text: str) -> None:
        view = agent_for_run(agent, None, history=loaded.history)

        async def go() -> None:
            async for _u in view.run(text, stream=True, session=loaded.session):
                pass

        _run(app, org, go, thread=tid, member=_ALICE)

    _user_row(org, tid, "rename the app")
    first = turn("rename the app")
    assert first is not None and first.outcome == "no_row"
    agent = _built(org, app)
    _wire(agent, model)
    assert _native_session_skip(agent, AGENT, {"message": "x"}, run_id="r",
                                caller_thread_id=tid, delegated=False) is None
    run(agent, first, "rename the app")
    assert asyncio.run(store.finish_turn(first, token_budget=50_000)) == "saved"
    _agent_row(org, tid, "Rename it to New?", agent=AGENT)

    _user_row(org, tid, "yes")
    second = turn("yes")
    assert second is not None and second.loaded, second and second.outcome
    run(agent, second, "yes")
    sent = model.bodies[2]["messages"]
    assert [m["role"] for m in sent].count("tool") == 1, sent
    assert any('"name": "Old"' in str(m.get("content")) for m in sent), sent
    assert sent[-1]["role"] == "user"


# ── the App Workshop checkpoints (WS43-F13, the Custom Apps half) ───────────


def test_the_checkpoint_git_runs_with_no_hook_and_no_file_monitor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from gateway.routes.apps import durability

    seen: list[tuple[list[str], dict[str, str]]] = []

    def capture(argv: list[str], **kw: Any) -> Any:
        seen.append((list(argv), dict(kw.get("env") or {})))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(durability.subprocess, "run", capture)
    durability._git(tmp_path, "status", "--porcelain")
    argv, env = seen[0]
    assert argv[:5] == ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    assert env.get("GIT_CONFIG_NOSYSTEM") == "1"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_planted_hook_never_runs_at_a_checkpoint(tmp_path: Path) -> None:
    from gateway.routes.apps import durability

    def plant(ws: Path) -> Path:
        ws.mkdir()
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
        marker = ws.parent / f"{ws.name}-HOOK-RAN"
        hook = ws / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8")
        hook.chmod(0o755)
        (ws / "index.html").write_text("x\n", encoding="utf-8")
        return marker

    marker = plant(tmp_path / "app")
    assert durability.git_checkpoint(tmp_path / "app", "t") is not None
    assert not marker.exists(), "the checkpoint ran a hook that the app dir held"
    control = plant(tmp_path / "control")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path / "control", check=True)
    subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "c"],
                   cwd=tmp_path / "control", check=False)
    if sys.platform != "win32" or control.exists():
        assert control.exists(), "the plant does not trigger, so this test proves nothing"


# ═════════════════════════ the sandbox_docker half ══════════════════════════
#
# WS43-F12 on a REAL container: Node runs, the build-script copy and the
# vendor cache are read-only, the app's ``.git`` is covered, and the token
# probe finds no GitHub token and no LLM key anywhere in the container.

from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: E402,F401
from tests.unit.test_sandbox_exec_hygiene import (  # noqa: E402,F401 — fixtures by name
    DOCKER_ORG,
    bind_mounts_work,
    docker_image,
    real,
)

CANARY_GH = "ghp_WS43hCanaryGithubTokenValue0000000000"
CANARY_LLM = "sk-WS43hCanaryLlmKeyValue000000000000"


@pytest.mark.sandbox_docker
async def test_docker_the_app_container_runs_node_and_holds_no_secret(
    real: dict[str, Any], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from acb_skills.sandbox_tools import app_builder_run_command

    if not real["bind"]:
        pytest.skip("this Docker cannot bind a host dir, so the mounts prove nothing here")
    monkeypatch.setattr(sb, "_BROKER", real["broker"])
    monkeypatch.setattr(real["settings"], "maf_coding_scope", f"app_builder:{DOCKER_ORG}")
    monkeypatch.setenv("GITHUB_TOKEN", CANARY_GH)
    monkeypatch.setattr(real["settings"], "litellm_master_key", CANARY_LLM)
    vendor = real["tmp"] / "vendor-t2"
    vendor.mkdir()
    (vendor / "VENDOR").write_text("v\n", encoding="utf-8")
    monkeypatch.setattr(real["settings"], "custom_apps_t2_vendor_dir", str(vendor), raising=False)
    ws = real["apps"] / "probe-app"
    (ws / ".git").mkdir(parents=True)
    (ws / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (ws / "index.html").write_text("<h1>x</h1>\n", encoding="utf-8")

    async def run(command: str) -> str:
        return await app_builder_run_command(command, 280)

    with bound_run(DOCKER_ORG, agent=AGENT, thread=new_thread(), workspace=str(ws),
                   instance=""):
        first = await run(
            "node --version && ls /opt/app-builder/build && echo VENDOR=$CUSTOM_APPS_T2_VENDOR_DIR"
            " && cat /opt/t2-vendor/VENDOR")
        second = await run(
            "ls -A /workspace/.git | wc -l; (echo x > /workspace/.git/evil) 2>/dev/null"
            " || echo GIT-READ-ONLY; (touch /opt/app-builder/build/x) 2>/dev/null"
            " || echo BUILD-READ-ONLY; echo y > /workspace/made.txt && echo WS-WRITABLE")
        probe = await run(
            f"grep -rIl --exclude-dir=proc --exclude-dir=sys -e '{CANARY_GH}' "
            f"-e '{CANARY_LLM}' / 2>/dev/null | head -5; echo PROBE-DONE")
    assert "v22." in first and "build_t2.mjs" in first and "install_t2_deps.mjs" in first, first
    assert "VENDOR=/opt/t2-vendor" in first, first
    assert "GIT-READ-ONLY" in second and "BUILD-READ-ONLY" in second, second
    assert "WS-WRITABLE" in second and (ws / "made.txt").exists(), second
    assert not (ws / ".git" / "evil").exists()
    assert "PROBE-DONE" in probe, probe
    body = probe.split("\n", 1)[1] if "\n" in probe else probe
    assert "/" not in body.replace("PROBE-DONE", "").strip(), f"a secret is in the container: {probe}"
