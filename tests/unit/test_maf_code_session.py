"""WS43-F7, the store half — every write and delete of the file tools reaches
the blob store (R8).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.4 ("The file tools") and
§10 WS43-F7, and the done-when 5 of WS-43d. WS-43e adds the ``code_task``
half of this fence (the scope switch, the report, the retry) to this file.

What breaks this half: a write or a delete of ``TenantFileStore`` skips the
blob store, lands under another store key, or keeps a legacy row that the
next rehydrate would bring back. The run data (``.run/``) never reaches the
store.

It runs the REAL ``mirror_to_blob_store`` and ``delete_file`` on the H3
rehearsal's phase-4 catalog, as its NOSUPERUSER NOBYPASSRLS role, with no
stub of the blob store (R8). ``TENANT_LADDER_DATABASE_URL`` must be set, and
a skip is not a pass.

Mutations this suite catches (R7), each run red once by hand:

* ``_after_write`` returns before the mirror: the write test;
* ``delete`` skips ``mirror_delete_from_blob_store``: the delete test;
* ``mirror_delete_from_blob_store`` deletes the run's key only: the legacy
  row test.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_skills.tenant_file_store import TenantFileStore

from tests.unit._sandbox_tools_fakes import (  # noqa: F401 — fixture by name
    PA,
    SKILL_SECRET,
    short_tmp,
)
from tests.unit.test_chat_write_under_rls import graph_as_app, members  # noqa: F401
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_tenant_workdirs import _rows


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


def _run(org: str, short_tmp: Path, body: Any) -> Any:  # noqa: F811
    """Run *body(store, ws, run_data)* inside a bound run of *org*."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.agent_paths import ensure_state_dir, tenant_instance, thread_slug
    from acb_skills.write_artifact import bind_artifact_context

    thread = str(uuid.uuid4())
    ws = ensure_state_dir(PA, tenant_instance(org)).resolve()
    (ws / "outputs" / thread_slug(thread)).mkdir(parents=True, exist_ok=True)
    run_data = short_tmp / "run-data" / thread_slug(thread)
    run_data.mkdir(parents=True)

    async def go() -> Any:
        token = bind_tenant(org)
        bind_artifact_context(
            session_id=thread, agent_name=PA, run_id=f"r-{uuid.uuid4().hex[:6]}",
            workspace_root=str(ws), instance=tenant_instance(org),
            gateway_url="http://127.0.0.1:9", gateway_token="x",
        )
        try:
            store = TenantFileStore(
                workspace=ws, outputs_rel=f"outputs/{thread_slug(thread)}",
                run_data=run_data, guard=_Guard(), member="member@example.com",
            )
            out = await body(store, ws, run_data, thread_slug(thread))
            rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*rest, return_exceptions=True)
            return out
        finally:
            release_tenant(token)

    return asyncio.run(go())


@pytest.fixture
def clone(monkeypatch: pytest.MonkeyPatch, short_tmp: Path) -> Path:  # noqa: F811
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(short_tmp / "agents"))
    # A skill marker holds a member id, an HMAC under this secret (WS-43v).
    monkeypatch.setattr(get_settings(), "gateway_session_secret", SKILL_SECRET, raising=False)
    return short_tmp


@_DB_GATE
def test_a_write_lands_on_disk_and_in_the_store_under_the_runs_key(graph_as_app, clone) -> None:  # noqa: F811
    a = graph_as_app.org_a
    name = f"chart-{uuid.uuid4().hex[:6]}.txt"

    async def body(store: TenantFileStore, ws: Path, run_data: Path, slug: str) -> str:
        await store.write(f"outputs/{name}", "A CHART")
        await store.write(".run/rows.csv", "member rows")
        await store.write("agent-data/skills/chart/SKILL.md", "---\n")
        return slug

    slug = _run(a, clone, body)
    rel = f"outputs/{slug}/{name}"
    assert _rows(graph_as_app, rel, agent=PA) == [(a, f"o:{a}", "A CHART")]
    assert _rows(graph_as_app, "agent-data/skills/chart/SKILL.md", agent=PA)
    with graph_as_app.admin_engine.connect() as c:
        from sqlalchemy import text

        leaked = c.execute(text(
            "SELECT count(*) FROM agent_blob WHERE agent_name = :a AND path LIKE '%rows.csv'"),
            {"a": PA}).scalar()
    assert leaked == 0, "the run data reached the blob store"


@_DB_GATE
def test_a_delete_removes_the_file_and_both_rows(graph_as_app, clone) -> None:  # noqa: F811
    from acb_memory import put_file

    a = graph_as_app.org_a
    name = f"old-{uuid.uuid4().hex[:6]}.txt"
    seen: dict[str, Any] = {}

    async def body(store: TenantFileStore, ws: Path, run_data: Path, slug: str) -> None:
        rel = f"outputs/{slug}/{name}"
        await store.write(f"outputs/{name}", "TO DELETE")
        # A legacy row (instance '') at the same path, as an older run left.
        await put_file(PA, rel, b"LEGACY", instance="", organization_id=a)
        seen["rel"], seen["disk"] = rel, ws / rel
        assert await store.delete(f"outputs/{name}") is True

    _run(a, clone, body)
    assert not seen["disk"].exists()
    assert _rows(graph_as_app, seen["rel"], agent=PA) == []


# ═══════════════ WS43-F7, the code_task half (WS-43e, §7.6) ═════════════════
#
# These tests need no database. They run the REAL ``code_tools.code_task``,
# the REAL MAF harness agent and the REAL ``OpenAIChatCompletionClient``. Only
# Docker (``FakeDocker``) and the network (``FakeGateway``) are fakes.
#
# Mutations this half catches (R7), each run red once by hand:
#
# * ``code_task`` ignores the scope and always takes the Copilot path: the
#   scope-switch test;
# * ``maf_engine_for_run`` reads a fixed org, not the run's own: the
#   other-org test;
# * ``_maf_code_task`` falls back to ``run_copilot_code_session`` on a
#   ``CodeSessionRefused``: the broker-refusal test;
# * ``run_maf_code_session`` returns ``response.text``: the report test;
# * ``run_with_empty_retry`` asks only once: the retry test;
# * ``code_task_client`` drops ``X-CC-Source`` or uses another base URL: the
#   Router test;
# * ``_maf_code_task`` calls ``_commit_repo_changes`` or starts host git: the
#   no-host-process test;
# * ``build_run_argv`` drops ``--network none``: the network test;
# * ``_mount_source_for`` takes the workspace from input: the two-org test.

import sys  # noqa: E402

from tests.unit._maf_gateway_fakes import FakeGateway, gateway  # noqa: E402,F401
from tests.unit._sandbox_broker_fakes import bound_run, flag_values, mounts_of  # noqa: E402
from tests.unit._sandbox_tools_fakes import (  # noqa: E402,F401 — fixtures by name
    ORG_A,
    ORG_B,
    Sandbox,
    host_trap,
    new_thread,
    sandbox,
)

AGENT = "agent-x"
MEMBER = "member@example.com"
PERSONAL = f"u:{MEMBER}"


def _code_tools() -> Any:
    import importlib

    return importlib.import_module("acb_skills.code_tools")


@pytest.fixture
def maf(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> Sandbox:  # noqa: F811
    """The broker on a fake Docker, with ``code_task:<ORG_A>`` in the scope.

    The Copilot path and the host commit are traps: a test that expects the
    MAF path fails when either one runs.
    """
    sandbox.set_scope(monkeypatch, f"code_task:{ORG_A}")
    sandbox.copilot = []  # type: ignore[attr-defined]
    sandbox.commits = []  # type: ignore[attr-defined]

    async def copilot(**kw: Any) -> str:
        sandbox.copilot.append(kw)  # type: ignore[attr-defined]
        return "COPILOT-REPORT"

    def commit(root: Path, task: str) -> str | None:
        sandbox.commits.append(str(root))  # type: ignore[attr-defined]
        return None

    monkeypatch.setattr("orchestrator.code_session.run_copilot_code_session", copilot)
    monkeypatch.setattr(_code_tools(), "_commit_repo_changes", commit)
    return sandbox


def _code_task(org: str | None, task: str = "make a chart", **bind: Any) -> str:
    bind.setdefault("instance", PERSONAL)
    with bound_run(org, agent=bind.pop("agent", AGENT), thread=bind.pop("thread", new_thread()),
                   member=MEMBER, **bind):
        return asyncio.run(_code_tools().code_task(task))


def test_the_scope_switch_runs_the_maf_session_for_the_named_org(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """Done-when 1: the named org gets a harness session whose command runs in
    the container, and the Copilot path does not run."""
    gateway.say([("run_command", {"command": "python3 -c 'print(42)'"})], "REPORT: it works")
    out = _code_task(ORG_A)
    assert out.startswith("REPORT: it works"), out
    assert maf.copilot == [], "the Copilot path ran for a named org"  # type: ignore[attr-defined]
    commands = [c[-1] for c in maf.docker.command_execs()]
    assert commands == ["python3 -c 'print(42)'"]
    assert len(gateway.requests) == 2
    assert any("exit 0" in r for r in gateway.tool_results(1))


def test_an_org_the_scope_does_not_name_keeps_the_copilot_path(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """Done-when 2, and flag OFF = unchanged: no model call through MAF, no
    container, and the host commit runs as before."""
    out = _code_task(ORG_B)
    assert "COPILOT-REPORT" in out
    assert len(maf.copilot) == 1  # type: ignore[attr-defined]
    assert maf.commits, "the Copilot path lost its host commit"  # type: ignore[attr-defined]
    assert gateway.requests == [] and maf.docker.calls == []


def test_an_empty_scope_keeps_the_copilot_path(
    maf: Sandbox, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    maf.set_scope(monkeypatch, "")
    assert "COPILOT-REPORT" in _code_task(ORG_A)
    assert gateway.requests == [] and maf.docker.calls == []


def test_a_run_with_no_org_keeps_the_copilot_path(
    maf: Sandbox, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    maf.set_scope(monkeypatch, "code_task:*")
    from orchestrator.code_session import maf_engine_for_run

    with bound_run(None, agent=AGENT, instance=PERSONAL):
        assert maf_engine_for_run() is False


@pytest.mark.parametrize("failure", ["docker_run", "no_image"])
def test_a_broker_failure_never_falls_back(
    maf: Sandbox, gateway: FakeGateway, host_trap: list[str],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """Done-when 3: a clear error, no Copilot path, no model call and no host process."""
    if failure == "docker_run":
        maf.docker.fail_run = True
    else:
        monkeypatch.setattr(maf.env["settings"], "sandbox_image", "")
    gateway.say("REPORT")
    out = _code_task(ORG_A)
    assert out.startswith("code_task failed:"), out
    assert "Nothing ran on the host" in out
    assert maf.copilot == [] and maf.commits == []  # type: ignore[attr-defined]
    assert gateway.requests == [], "the model ran with no sandbox"
    assert host_trap == []


def test_the_report_is_the_last_assistant_message(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """Done-when 4: ``response.text`` holds the compaction line, and the
    report does not."""
    gateway.say(
        ("[Tool results: SECRET narration of the turn]", [("run_command", {"command": "ls"})]),
        "THE REPORT",
    )
    out = _code_task(ORG_A)
    assert out.startswith("THE REPORT"), out
    assert "[Tool results" not in out and "SECRET" not in out


def test_last_assistant_text_never_reads_response_text() -> None:
    from agent_framework import AgentResponse, Message
    from orchestrator.code_session import last_assistant_text

    response = AgentResponse(messages=[
        Message(role="assistant", contents=["[Tool results: x = 1]"]),
        Message(role="tool", contents=["tool output"]),
        Message(role="assistant", contents=["FINAL"]),
    ])
    assert "[Tool results" in response.text
    assert last_assistant_text(response) == "FINAL"


def test_one_empty_answer_gets_one_retry(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """Done-when 5, first half."""
    from orchestrator.code_session import EMPTY_ANSWER_NUDGE

    gateway.say("", "REPORT after the nudge")
    out = _code_task(ORG_A)
    assert out.startswith("REPORT after the nudge"), out
    assert len(gateway.requests) == 2
    assert gateway.last_user_text(1) == EMPTY_ANSWER_NUDGE


def test_two_empty_answers_return_an_error(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """Done-when 5, second half: an error, and still no Copilot fallback."""
    gateway.say("", "")
    out = _code_task(ORG_A)
    assert out.startswith("code_task failed: the coding session gave an empty answer twice"), out
    assert len(gateway.requests) == 2
    assert maf.copilot == []  # type: ignore[attr-defined]


def test_every_model_call_goes_through_the_router_with_the_callers_name(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """Done-when 6, and "every model call goes through our Router": the
    gateway ``/v1``, a tier alias, the LLM API key, ``X-CC-Source: code_task``
    and the CALLING agent's name."""
    from acb_common import get_settings

    settings = get_settings()
    gateway.say([("run_command", {"command": "true"})], "REPORT")
    _code_task(ORG_A)
    base = (settings.litellm_base_url or "http://127.0.0.1:8080").rstrip("/")
    key = (settings.llm_api_key or "sk-local").strip()
    assert len(gateway.requests) == 2
    for req in gateway.requests:
        assert req["url"] == f"{base}/v1/chat/completions"
        assert req["headers"]["x-cc-source"] == "code_task"
        assert req["headers"]["x-cc-agent"] == AGENT
        assert req["headers"]["authorization"] == f"Bearer {key}"
        assert req["body"]["model"] == "tier-balanced"


def test_the_maf_path_imports_nothing_from_copilot(
    maf: Sandbox, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Done-when 1: with every Copilot module unimportable, the MAF path works."""
    import importlib.abc

    blocked = ("copilot", "agent_framework_github_copilot")
    own = ("orchestrator.copilot_agent", "orchestrator.copilot_sandbox",
           "orchestrator._copilot_session")

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name: str, path: Any = None, target: Any = None) -> Any:
            if name in own or name.split(".")[0] in blocked:
                raise ImportError(f"{name} is blocked: the MAF path must not import it")
            return None

    for name in list(sys.modules):
        if name in own or name.split(".")[0] in blocked:
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [Block(), *sys.meta_path])
    gateway.say([("run_command", {"command": "true"})], "REPORT")
    assert _code_task(ORG_A).startswith("REPORT")


def test_the_maf_path_starts_no_host_process_and_no_host_commit(
    maf: Sandbox, gateway: FakeGateway, host_trap: list[str],  # noqa: F811
) -> None:
    """``code_task`` runs in the container, never on the host. A mutation that
    calls host git, or ``_commit_repo_changes``, on the MAF path fails here."""
    with bound_run(ORG_A, agent=AGENT, instance=PERSONAL) as ws:
        (ws / ".git" / "hooks").mkdir(parents=True)
        (ws / ".git" / "config").write_text("[core]\n\tfsmonitor = /tmp/evil\n", encoding="utf-8")
    gateway.say([("run_command", {"command": "git status"})], "REPORT")
    assert _code_task(ORG_A).startswith("REPORT")
    assert host_trap == [], f"a host process started: {host_trap}"
    assert maf.commits == [], "the MAF path ran the host commit"  # type: ignore[attr-defined]
    assert [c[-1] for c in maf.docker.command_execs()] == ["git status"]


def test_a_file_tool_write_reaches_the_blob_store_and_a_dot_path_is_refused(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """§7.4: the store is the same dir that the container mounts, the write
    goes to the blob store, and ``.git`` is not a workspace file."""
    gateway.say(
        [("file_access_write", {"file_name": "agent-data/scripts/x.py", "content": "print(1)\n"}),
         ("file_access_write", {"file_name": ".git/config", "content": "[core]\n"})],
        "REPORT",
    )
    with bound_run(ORG_A, agent=AGENT, instance=PERSONAL, member=MEMBER) as ws:
        out = asyncio.run(_code_tools().code_task("write a script"))
    assert out.startswith("REPORT"), out
    assert (ws / "agent-data" / "scripts" / "x.py").read_text(encoding="utf-8") == "print(1)\n"
    assert ("agent-data/scripts/x.py", b"print(1)\n") in maf.mirrored
    assert not (ws / ".git").exists(), "a file tool wrote a .git"


def test_the_session_holds_only_its_own_tools_and_no_web_tool(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """The tool classes of the session stay pinned: no web search, no host
    shell, no egress tool (H-236)."""
    from acb_skills.sandbox_tools import CODE_TASK_SESSION_TOOLS

    gateway.say("REPORT")
    _code_task(ORG_A)
    offered = gateway.tool_names(0)
    assert "run_command" in offered and "file_access_write" in offered
    assert offered <= CODE_TASK_SESSION_TOOLS, offered - CODE_TASK_SESSION_TOOLS
    for host_tool in ("web_search", "fetch_page", "code_task", "run_script", "call_agent"):
        assert host_tool not in offered


def test_code_task_stays_an_egress_tool() -> None:
    """H-236: a ``no_egress`` run never holds ``code_task``. WS-43e must not
    change its class."""
    from acb_skills.egress import is_egress_tool
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    assert TOOL_ANNOTATIONS["code_task"]["open_world"] is True
    assert is_egress_tool("code_task")


def test_the_sandbox_network_stays_off(
    maf: Sandbox, gateway: FakeGateway,  # noqa: F811
) -> None:
    """The container starts with ``--network none`` and no proxy, and the
    network tool answers that the network is off."""
    from acb_skills.sandbox_tools import NETWORK_OFF

    gateway.say(
        [("request_network_access", {"reason": "pip install", "hosts": ["pypi.org"]})],
        "REPORT",
    )
    _code_task(ORG_A)
    runs = maf.docker.runs()
    assert len(runs) == 1
    assert flag_values(runs[0], "--network") == ["none"]
    env = " ".join(flag_values(runs[0], "--env")).upper()
    assert "PROXY" not in env and "KEY" not in env and "TOKEN" not in env
    assert any(NETWORK_OFF in r for r in gateway.tool_results(1))


def test_two_orgs_get_two_containers_on_their_own_dirs(
    maf: Sandbox, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """Tenant isolation: the same agent and the same thread id in two orgs
    give two containers, each with its own org label and its own mount."""
    maf.set_scope(monkeypatch, f"code_task:{ORG_A},code_task:{ORG_B}")
    thread = new_thread()
    gateway.say([("run_command", {"command": "echo A"})], "REPORT A",
                [("run_command", {"command": "echo B"})], "REPORT B")
    with bound_run(ORG_A, agent=AGENT, thread=thread, instance=f"o:{ORG_A}") as ws_a:
        out_a = asyncio.run(_code_tools().code_task("a"))
    with bound_run(ORG_B, agent=AGENT, thread=thread, instance=f"o:{ORG_B}") as ws_b:
        out_b = asyncio.run(_code_tools().code_task("b"))
    assert out_a.startswith("REPORT A") and out_b.startswith("REPORT B")
    runs = maf.docker.runs()
    assert len(runs) == 2
    names = [r[r.index("--name") + 1] for r in runs]
    assert names[0] != names[1]
    assert f"metorite.org={ORG_A}" in runs[0] and f"metorite.org={ORG_B}" in runs[1]
    assert f"type=bind,source={ws_a.resolve()},target=/workspace" in mounts_of(runs[0])
    assert f"type=bind,source={ws_b.resolve()},target=/workspace" in mounts_of(runs[1])
    execs = maf.docker.command_execs()
    assert [e[e.index("exec") + 3] for e in execs] == names
    assert [e[-1] for e in execs] == ["echo A", "echo B"]


def test_a_run_bound_to_one_org_cannot_mount_another_orgs_dir(
    maf: Sandbox, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """R5: the org comes from the run binding. A working dir of org A in a run
    of org B is refused, and no container starts."""
    from acb_skills.agent_paths import ensure_state_dir

    maf.set_scope(monkeypatch, f"code_task:{ORG_A},code_task:{ORG_B}")
    ws_a = ensure_state_dir(AGENT, f"o:{ORG_A}")
    gateway.say("REPORT")
    out = _code_task(ORG_B, workspace=str(ws_a), instance=f"o:{ORG_A}")
    assert out.startswith("code_task failed:"), out
    assert maf.docker.runs() == [] and gateway.requests == []


# ═════════════════════════ the sandbox_docker half ══════════════════════════

from tests.unit.test_coding_sandbox_image import coding_sandbox_image  # noqa: E402,F401
from tests.unit.test_sandbox_exec_hygiene import (  # noqa: E402,F401 — fixtures by name
    DOCKER_ORG,
    bind_mounts_work,
    docker_image,
    real,
)


@pytest.mark.sandbox_docker
async def test_docker_a_code_task_session_runs_in_a_container_with_no_network(
    real: dict[str, Any], gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The whole MAF session on a REAL container: the model's command runs
    there, it reaches no network, and its file lands in the working dir."""
    monkeypatch.setattr(sb_module(), "_BROKER", real["broker"])
    monkeypatch.setattr(real["settings"], "maf_coding_scope", f"code_task:{DOCKER_ORG}")
    mirrored: list[str] = []

    async def mirror(rel: str, data: bytes, **_kw: Any) -> None:
        mirrored.append(rel)

    import importlib

    monkeypatch.setattr(importlib.import_module("acb_skills.code_tools"), "mirror_to_blob_store",
                        mirror)
    net = (
        "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\""
        " && echo NET-OPEN || echo NET-CLOSED"
    )
    gateway.say(
        [("run_command", {"command": net})],
        [("run_command", {"command": "mkdir -p /workspace/outputs && "
                                     "echo made-in-sandbox > /workspace/outputs/r.txt"})],
        "REPORT",
    )
    with bound_run(DOCKER_ORG, agent=AGENT, thread=new_thread(), instance=PERSONAL) as ws:
        out = await _code_tools().code_task("probe the sandbox")
    assert out.startswith("REPORT"), out
    first = " ".join(gateway.tool_results(1))
    assert "NET-CLOSED" in first and "NET-OPEN" not in first, first
    second = " ".join(gateway.tool_results(2))
    assert "exit 0" in second, second
    if real["bind"]:
        assert (ws / "outputs" / "r.txt").read_text(encoding="utf-8").strip() == "made-in-sandbox"
        assert "outputs/r.txt" in mirrored


def sb_module() -> Any:
    from orchestrator import sandbox_broker

    return sandbox_broker


def test_a_foreign_tool_is_withheld_and_refused(
    maf: Sandbox, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The session pin (``tool_guard``): a tool outside the session's own set,
    such as a web search that an upgrade adds, never reaches the model, and a
    call to it never runs."""
    import importlib

    from agent_framework import tool

    ran: list[str] = []

    async def web_search(query: str) -> str:
        """Search the web."""
        ran.append(query)
        return "LEAKED"

    st = importlib.import_module("acb_skills.sandbox_tools")
    real_parts = st.code_task_session_parts

    def parts(binding: Any) -> Any:
        store, skills, tools = real_parts(binding)
        return store, skills, [*tools, tool(web_search, approval_mode="never_require")]

    monkeypatch.setattr(st, "code_task_session_parts", parts)
    gateway.say([("web_search", {"query": "member data"})], "REPORT")
    out = _code_task(ORG_A)
    assert out.startswith("REPORT"), out
    assert "web_search" not in gateway.tool_names(0)
    assert ran == [], "a foreign tool ran"
    assert "LEAKED" not in " ".join(gateway.tool_results(1))


def test_a_workspace_that_is_not_the_runs_own_is_refused(
    maf: Sandbox, gateway: FakeGateway, short_tmp: Path,  # noqa: F811
) -> None:
    """R5: the session takes its dir from the run binding, and refuses a
    caller that names another dir. No container starts."""
    from orchestrator.code_session import CodeSessionRefused, run_maf_code_session

    other = short_tmp / "elsewhere"
    other.mkdir()
    with bound_run(ORG_A, agent=AGENT, instance=PERSONAL), pytest.raises(CodeSessionRefused):
        asyncio.run(run_maf_code_session(task="t", workspace=str(other)))
    assert maf.docker.runs() == [] and gateway.requests == []
