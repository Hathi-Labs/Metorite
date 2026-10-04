"""H-201 — the run's artifact context is per run, never process-global
(``projects_ai_chat.md`` §21.16).

``_WRITE_ARTIFACT_CONTEXT`` was ONE dict for the whole gateway process. It
held the run's workspace, its tenant partition and its session. The gateway is
one uvicorn process, so when a run of org A and a run of org B overlapped, the
later setup overwrote the earlier. Run A's ``write_artifact`` then wrote A's
document into B's tenant dir and posted the event to B's session.

Now the context is a ContextVar that holds an immutable mapping
(``acb_skills.write_artifact.artifact_context``). Each path carries it:

* a MAF tool call runs in the run's task, or in a copy of its context;
* a Copilot SDK tool call starts on the SDK's reader thread with NO context,
  so ``carry_run_context`` runs each handler in a copy of the run's context;
* ``asyncio.to_thread`` copies the context into the worker thread;
* a sub-agent derives its OWN context, and its parent's does not change;
* a delegated personal agent works in the ``u:`` dir of the parent's member.

With no context, every reader fails closed: it writes nothing and emits to no
session.

Mutations this suite catches (R7). The spec's table carries the counts.

* the global dict put back (``artifact_context`` reads one module dict);
* ``carry_run_context`` made a no-op;
* ``asyncio.to_thread`` in ``run_script`` swapped for a bare
  ``run_in_executor``;
* the sub-agent's reset of its token removed, or its derive removed;
* the delegated batch run takes its member from the payload;
* the Copilot sub-agent ignores the parent's member;
* ``_delegated_instance`` stops refusing a personal agent with no member;
* ``_bind_run_instance`` no longer stamps the ``o:`` key of a shared run;
* the "single live run" fallback put back in ``resolve_run_queue`` or in
  ``resolve_relay_thread_id``;
* the temp-dir fallback put back in ``write_artifact``;
* the permission policy allows a write with no workspace;
* the carry loop removed from stream Tier 1.5, or from ``_run_with_maf_agent``;
* a module-level dict added to ``write_artifact.py``.

The stream run's own reset, and its ``o:`` stamp, are fenced in
``test_h201_tenant_workdirs.py::test_a_stream_run_is_given_its_tenant_dir``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_h201_run_context.py -v -rs
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import threading
import types
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("sqlalchemy")

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_tenant_workdirs import (  # noqa: F401
    _P,
    _S,
    _cfg,
    _rows,
    _tenant_dir,
    disk,
)

_GW = "http://127.0.0.1:9"  # refuses at once, so _notify's POST is a no-op


def _bind(sid: str, ws: str, key: str, *, agent: str = _S, member: str = "") -> None:
    from acb_skills.write_artifact import bind_artifact_context

    bind_artifact_context(
        session_id=sid, agent_name=agent, run_id=f"r-{uuid.uuid4().hex[:6]}",
        workspace_root=ws, instance=key, member=member,
        gateway_url=_GW, gateway_token="x",
    )


@pytest.fixture
def mirrors(monkeypatch):
    """The blob write-through, recorded with the context it ran in."""
    wa = importlib.import_module("acb_skills.write_artifact")

    seen: list[dict] = []

    async def _mirror(rel_path, data, **_kw):
        ctx = wa.artifact_context()
        seen.append({"path": rel_path, "agent": wa._current_agent_name(),
                     "instance": ctx.get("instance"), "session": ctx.get("session_id")})

    monkeypatch.setattr(wa, "mirror_to_blob_store", _mirror)
    return seen


@pytest.fixture
def queues():
    """A run queue per session, as the executor registers them."""
    from orchestrator import executor

    made: dict[str, asyncio.Queue] = {}

    def _make(sid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        executor._register_run_queue(sid, q)
        made[sid] = q
        return q

    yield _make
    for sid in made:
        executor._unregister_run_queue(sid)


def _events(q: asyncio.Queue) -> list[str]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev and ev.get("name") == "artifact_created":
            out.append(ev["value"]["path"])
    return out


async def _drain_tasks() -> None:
    rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*rest, return_exceptions=True)


def _in_thread(rel: str, sid: str) -> str:
    """Where a shared agent's run puts *rel*: its thread's own folder (H-227)."""
    from acb_skills.agent_paths import thread_scoped_rel

    return thread_scoped_rel(rel, sid)


# ── Two runs on one event loop, through the MAF path ────────────────────────

def test_two_overlapping_runs_each_write_into_their_own_tenant(disk, mirrors, queues) -> None:  # noqa: F811
    """Run A binds, then waits. Run B binds, which overwrote A's value in the
    old global dict. Then A writes. Each file, each blob mirror and each event
    must belong to its own run."""
    from acb_skills.write_artifact import write_artifact
    from orchestrator.executor import _resolve_run_workspace

    ws_a, key_a = _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-a")
    ws_b, key_b = _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-b")
    rel = f"outputs/plan-{uuid.uuid4().hex[:6]}.md"

    async def _main() -> dict:
        qa, qb = queues("sid-a"), queues("sid-b")
        a_bound, b_bound = asyncio.Event(), asyncio.Event()

        async def _run_a() -> dict:
            _bind("sid-a", ws_a, key_a)
            a_bound.set()
            await b_bound.wait()
            return await write_artifact(rel, "FROM A", overwrite=True)

        async def _run_b() -> dict:
            await a_bound.wait()
            _bind("sid-b", ws_b, key_b)
            b_bound.set()
            await asyncio.sleep(0)
            return await write_artifact(rel, "FROM B", overwrite=True)

        res_a, res_b = await asyncio.gather(_run_a(), _run_b())
        await _drain_tasks()
        return {"a": res_a, "b": res_b, "qa": _events(qa), "qb": _events(qb)}

    got = asyncio.run(_main())
    assert got["a"]["download_url"].startswith("/api/agent/workspace/sid-a/")
    assert got["b"]["download_url"].startswith("/api/agent/workspace/sid-b/")
    ra, rb = _in_thread(rel, "sid-a"), _in_thread(rel, "sid-b")
    assert (Path(ws_a) / ra).read_text(encoding="utf-8") == "FROM A"
    assert (Path(ws_b) / rb).read_text(encoding="utf-8") == "FROM B"
    assert got["qa"] == [ra] and got["qb"] == [rb]
    assert sorted((m["instance"], m["session"]) for m in mirrors) == [
        (key_a, "sid-a"), (key_b, "sid-b")]


# ── The Copilot SDK path ────────────────────────────────────────────────────

def _sdk_dispatch(loop: asyncio.AbstractEventLoop, handler, invocation):
    """Call *handler* the way the Copilot SDK does.

    The SDK reads its JSON-RPC stream on a thread of its own and hands each
    call to the loop with ``run_coroutine_threadsafe``. The call therefore
    starts in the context of that thread, which holds no run value.
    """
    async def _execute():
        result = handler(invocation)
        if inspect.isawaitable(result):
            result = await result
        return result

    box: dict = {}

    def _reader() -> None:
        box["fut"] = asyncio.run_coroutine_threadsafe(_execute(), loop)

    t = threading.Thread(target=_reader)
    t.start()
    t.join()
    return asyncio.wrap_future(box["fut"])


def test_two_overlapping_copilot_runs_each_write_into_their_own_tenant(
    disk, mirrors, queues,  # noqa: F811
) -> None:
    """The same two runs through the REAL Copilot tool adapter: the upstream
    ``_prepare_tools`` makes the handler, and the call starts on another
    thread. Without ``carry_run_context`` the tool reads no context and
    writes nothing. With the global dict it read the later run's."""
    from acb_skills.write_artifact import artifact_context, write_artifact
    from agent_framework_github_copilot import GitHubCopilotAgent
    from copilot.tools import ToolInvocation
    from orchestrator.copilot_agent import carry_run_context
    from orchestrator.executor import _resolve_run_workspace

    ws_a, key_a = _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-a")
    ws_b, key_b = _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-b")
    rel = f"outputs/cp-{uuid.uuid4().hex[:6]}.md"
    roots: dict[str, str | None] = {}

    async def _main() -> dict:
        loop = asyncio.get_running_loop()
        qa, qb = queues("sid-a"), queues("sid-b")
        a_ready, b_ready = asyncio.Event(), asyncio.Event()

        async def _run(tag, sid, ws, key, mine, other, body) -> str:
            _bind(sid, ws, key)

            def _perm(_req, _ctx=None):
                roots[tag] = artifact_context().get("workspace_root")
                return {"kind": "approved"}

            agent = GitHubCopilotAgent(instructions="x", tools=[write_artifact])
            agent._permission_handler = _perm
            carry_run_context(agent)
            # The SDK prepares the tools when the run opens its session.
            tool = agent._prepare_tools(agent._tools)[0]
            mine.set()
            await other.wait()
            await _sdk_dispatch(loop, agent._permission_handler, {"kind": "write"})
            out = await _sdk_dispatch(loop, tool.handler, ToolInvocation(
                tool_name="write_artifact",
                arguments={"path": rel, "content": body, "overwrite": True}))
            return out.text_result_for_llm

        res = await asyncio.gather(
            _run("a", "sid-a", ws_a, key_a, a_ready, b_ready, "COPILOT A"),
            _run("b", "sid-b", ws_b, key_b, b_ready, a_ready, "COPILOT B"),
        )
        await _drain_tasks()
        return {"res": res, "qa": _events(qa), "qb": _events(qb)}

    got = asyncio.run(_main())
    assert "sid-a" in got["res"][0] and "sid-b" in got["res"][1], got["res"]
    ra, rb = _in_thread(rel, "sid-a"), _in_thread(rel, "sid-b")
    assert (Path(ws_a) / ra).read_text(encoding="utf-8") == "COPILOT A"
    assert (Path(ws_b) / rb).read_text(encoding="utf-8") == "COPILOT B"
    assert got["qa"] == [ra] and got["qb"] == [rb]
    assert roots == {"a": ws_a, "b": ws_b}
    assert sorted(m["instance"] for m in mirrors) == sorted([key_a, key_b])


def test_a_copilot_tool_with_no_carried_context_writes_nothing(disk, mirrors, queues) -> None:  # noqa: F811
    """The fail-closed half: the same call with no ``carry_run_context``."""
    from acb_skills.write_artifact import write_artifact
    from agent_framework_github_copilot import GitHubCopilotAgent
    from copilot.tools import ToolInvocation
    from orchestrator.executor import _resolve_run_workspace

    ws, key = _resolve_run_workspace(disk.shared, _cfg(None), organization_id="org-a")
    rel = f"outputs/none-{uuid.uuid4().hex[:6]}.md"

    async def _main() -> tuple[str, list[str]]:
        q = queues("sid-a")
        _bind("sid-a", ws, key)
        agent = GitHubCopilotAgent(instructions="x", tools=[write_artifact])
        tool = agent._prepare_tools(agent._tools)[0]
        out = await _sdk_dispatch(asyncio.get_running_loop(), tool.handler, ToolInvocation(
            tool_name="write_artifact", arguments={"path": rel, "content": "X"}))
        await _drain_tasks()
        return out.text_result_for_llm, _events(q)

    text, events = asyncio.run(_main())
    assert "No workspace is configured" in text
    assert not (Path(ws) / rel).exists()
    assert events == [] and mirrors == []


# ── A worker thread inside a run ────────────────────────────────────────────

def test_a_worker_thread_keeps_its_runs_context(tmp_path, monkeypatch) -> None:
    """``run_script`` builds the script's env on a worker thread. Two runs
    overlap: A declares an integration and B does not. Each script must see
    exactly its own run's declaration."""
    import acb_skills.code_tools as ct

    async def _no_mirror(*_a, **_k):
        return None

    monkeypatch.setattr(ct, "mirror_to_blob_store", _no_mirror)
    monkeypatch.setenv("ZOHO_CLIENT_ID", "pk_run_a_only")
    dirs = {}
    for tag in ("a", "b"):
        d = tmp_path / tag
        (d / "agent-data" / "scripts").mkdir(parents=True)
        (d / "agent-data" / "scripts" / "env.py").write_text(
            "import os\nprint(os.environ.get('ZOHO_CLIENT_ID', 'none'))\n",
            encoding="utf-8")
        dirs[tag] = d

    async def _main() -> tuple[str, str]:
        from acb_skills.write_artifact import derive_artifact_context

        a_bound, b_bound = asyncio.Event(), asyncio.Event()

        async def _run_a() -> str:
            _bind("sid-a", str(dirs["a"]), "o:org-a")
            derive_artifact_context(integrations=["zoho-crm"])
            a_bound.set()
            await b_bound.wait()
            return await ct.run_script("agent-data/scripts/env.py")

        async def _run_b() -> str:
            await a_bound.wait()
            _bind("sid-b", str(dirs["b"]), "o:org-b")
            derive_artifact_context(integrations=[])
            b_bound.set()
            return await ct.run_script("agent-data/scripts/env.py")

        return tuple(await asyncio.gather(_run_a(), _run_b()))

    out_a, out_b = asyncio.run(_main())
    assert "pk_run_a_only" in out_a, out_a
    assert "pk_run_a_only" not in out_b and "none" in out_b, out_b


def test_a_bare_executor_thread_sees_no_run() -> None:
    """``run_in_executor`` does not copy the context. A reader there sees an
    empty context, never another run's."""
    from acb_skills.write_artifact import artifact_context

    async def _main() -> dict:
        _bind("sid-a", "/ws/a", "o:org-a")
        inside = dict(artifact_context())
        loop = asyncio.get_running_loop()
        bare = await loop.run_in_executor(None, lambda: dict(artifact_context()))
        copied = await asyncio.to_thread(lambda: dict(artifact_context()))
        return {"inside": inside, "bare": bare, "copied": copied}

    got = asyncio.run(_main())
    assert got["inside"]["session_id"] == "sid-a"
    assert got["bare"] == {}
    assert got["copied"] == got["inside"]


# ── Sub-agents ──────────────────────────────────────────────────────────────

class _FakeCopilotAgent:
    """A Copilot SDK agent as ``_run_sub_agent_streaming`` sees one."""

    def __init__(self, seen: dict, gate: asyncio.Event | None = None) -> None:
        self._default_options: dict = {}
        self._permission_handler = None
        self._tools: list = []
        self._seen = seen
        self._gate = gate

    def _prepare_tools(self, tools):
        return list(tools)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def run(self, _message, stream=True):
        from acb_skills.write_artifact import artifact_context

        async def _gen():
            self._seen["sub"] = dict(artifact_context())
            self._seen["carried"] = bool(
                getattr(self, "_metorite_run_context_carried", False))
            if self._gate is not None:
                self._seen["running"].set()
                await self._gate.wait()
            return
            yield  # pragma: no cover - an async generator with no updates

        return _gen()


@contextmanager
def _sub_agent_wiring(disk, monkeypatch, agent_name: str, agent) -> None:  # noqa: F811
    """``_run_sub_agent_streaming`` with a Copilot SDK sub-agent and nothing
    else loaded."""
    from gateway.routes import agent as agent_routes
    from orchestrator import executor

    cfg = _cfg("personal" if agent_name == _P else None)
    path = disk.personal if agent_name == _P else disk.shared

    @contextmanager
    def _load(_name, **_k):
        yield types.SimpleNamespace(agent_dir=path, config=cfg, build_agents=lambda: [agent])

    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [
        {"name": agent_name, "agent_runtime": "github-copilot"}])
    monkeypatch.setattr(executor, "load_agent", _load)
    monkeypatch.setattr(executor, "_inject_agent_tools", lambda *_a, **_k: None)
    monkeypatch.setattr(executor, "_apply_own_tool_scope", lambda *_a, **_k: None)
    monkeypatch.setattr(executor, "_apply_agent_md_overrides", lambda *_a, **_k: None)
    yield


def test_a_sub_agent_gets_its_own_context_and_leaves_the_parents(disk, monkeypatch) -> None:  # noqa: F811
    """The parent binds its context. A sibling task of the parent reads the
    context while the sub-agent runs, and the parent reads it after. Both see
    the parent's own mapping. The sub-agent sees its own workspace and key,
    under the parent's session."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import artifact_context
    from orchestrator import executor

    gate = asyncio.Event()
    seen: dict = {"running": asyncio.Event()}

    async def _main() -> dict:
        token = bind_tenant("org-p")
        try:
            _bind("sid-p", "/ws/parent", "o:org-p", agent="parent-agent", member=_ALICE)
            before = artifact_context()

            async def _sibling() -> dict:
                await seen["running"].wait()
                during = dict(artifact_context())
                gate.set()
                return during

            sibling = asyncio.ensure_future(_sibling())
            out = await executor._run_sub_agent_streaming(_S, "do it", "r-sub", None)
            return {"out": out, "during": await sibling, "before": before,
                    "after": artifact_context()}
        finally:
            release_tenant(token)

    with _sub_agent_wiring(disk, monkeypatch, _S, _FakeCopilotAgent(seen, gate)):
        got = asyncio.run(_main())

    assert got["after"] is got["before"], "the sub-agent changed the parent's context"
    assert got["during"]["workspace_root"] == "/ws/parent"
    sub = seen["sub"]
    assert sub["workspace_root"] == str(_tenant_dir("org-p"))
    assert (sub["instance"], sub["agent_name"], sub["run_id"]) == ("o:org-p", _S, "r-sub")
    # The parent's session and member stay, so a link opens in the parent chat.
    assert (sub["session_id"], sub["member"]) == ("sid-p", _ALICE)
    assert seen["carried"], "the sub-agent's SDK callbacks carry no run context"


def test_a_delegated_personal_copilot_agent_works_in_the_members_dir(
    disk, monkeypatch,  # noqa: F811
) -> None:
    """P2-c on the Copilot sub-agent path. The member is the PARENT's bound
    member, and the delegated message names nobody."""
    from acb_skills.agent_paths import agent_state_dir
    from orchestrator import executor

    seen: dict = {}

    async def _main() -> str:
        _bind("sid-p", "/ws/parent", "o:org-p", member=_ALICE)
        return await executor._run_sub_agent_streaming(_P, "do it", "r-sub", None)

    with _sub_agent_wiring(disk, monkeypatch, _P, _FakeCopilotAgent(seen)):
        asyncio.run(_main())
    key = f"u:{_ALICE}"
    assert seen["sub"]["workspace_root"] == str(agent_state_dir(_P, key))
    assert seen["sub"]["instance"] == key


def test_a_delegated_personal_copilot_agent_with_no_member_is_refused(
    disk, monkeypatch,  # noqa: F811
) -> None:
    """The parent has a tenant and no member. The personal sub-agent is
    refused, and it never falls back to the org dir of that tenant."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.agent_paths import agent_state_dir, tenant_instance
    from orchestrator import executor

    seen: dict = {}

    async def _main() -> str:
        token = bind_tenant("org-p")
        try:
            _bind("sid-p", "/ws/parent", "o:org-p", member="")
            return await executor._run_sub_agent_streaming(_P, "do it", "r-sub", None)
        finally:
            release_tenant(token)

    with _sub_agent_wiring(disk, monkeypatch, _P, _FakeCopilotAgent(seen)):
        out = asyncio.run(_main())
    assert "personal" in out and "no member" in out, out
    assert "sub" not in seen, "the personal sub-agent ran with no member"
    assert not agent_state_dir(_P, tenant_instance("org-p")).exists()


# ── The batch path: delegation, the o: stamp ────────────────────────────────

@pytest.fixture
def batch(disk, monkeypatch):  # noqa: F811
    """The batch run path with the model call and the store stubbed. The stub
    records the artifact context and the run context the agent was given."""
    import acb_memory
    from acb_common import get_run_context
    from acb_skills.write_artifact import artifact_context
    from orchestrator import executor

    seen: list[dict] = []

    async def _nothing(*_a, **_k):
        return ""

    async def _rehydrate(*_a, **_k):
        return 0

    async def _run_maf(*_a, **_k):
        seen.append({"ctx": dict(artifact_context()),
                     "run": dict(get_run_context() or {})})
        return {"answer": "ok"}

    @contextmanager
    def _load(name, **_k):
        personal = name == _P
        yield types.SimpleNamespace(
            agent_dir=disk.personal if personal else disk.shared,
            config=_cfg("personal" if personal else None), build_agents=lambda: [])

    monkeypatch.setattr(acb_memory, "rehydrate_workspace", _rehydrate)
    monkeypatch.setattr(executor, "_install_push_guard", _nothing)
    monkeypatch.setattr(executor, "_get_current_head", _nothing)
    monkeypatch.setattr(executor, "_detect_agent_commits", _nothing)
    monkeypatch.setattr(executor, "_run_with_maf_agent", _run_maf)
    monkeypatch.setattr(executor, "load_agent", _load)
    return seen


def test_a_delegated_personal_batch_agent_works_in_the_members_dir(disk, batch) -> None:  # noqa: F811
    """P2-c on the batch path (call_agent's fallback, delegate_to_agent, the
    MAF sub-agent). The payload claims another member, and it loses."""
    from acb_skills.agent_paths import agent_state_dir
    from acb_skills.write_artifact import artifact_context
    from orchestrator import executor

    async def _main() -> dict:
        _bind("sid-p", "/ws/parent", "o:org-p", member=_ALICE)
        before = artifact_context()
        await executor.run_agent(
            _P, {"message": "x", "mode": "sub_task", "user_email": "mallory@evil.test"},
            run_id="r-d", organization_id="org-p")
        return {"before": before, "after": artifact_context()}

    got = asyncio.run(_main())
    key = f"u:{_ALICE}"
    assert batch[-1]["ctx"]["workspace_root"] == str(agent_state_dir(_P, key))
    assert batch[-1]["ctx"]["instance"] == key
    assert got["after"] is got["before"], "the delegated run left its context behind"


def test_a_delegated_personal_batch_agent_with_no_member_is_refused(disk, batch) -> None:  # noqa: F811
    from orchestrator import executor

    async def _main() -> None:
        _bind("sid-p", "/ws/parent", "o:org-p", member="")
        await executor.run_agent(
            _P, {"message": "x", "mode": "sub_task"}, run_id="r-n", organization_id="org-p")

    with pytest.raises(executor.AgentRunError) as err:
        asyncio.run(_main())
    assert "no member" in str(err.value)
    assert batch == [], "the personal agent ran with no member"


def test_a_shared_run_stamps_its_tenant_key(disk, batch) -> None:  # noqa: F811
    """``_bind_run_instance`` stamps the partition the run WORKS in, so a
    shared run's logs and presence show ``o:<org>``."""
    from orchestrator import executor

    asyncio.run(executor.run_agent(_S, {"message": "hi"}, run_id="r-o", organization_id="org-w"))
    assert batch[-1]["run"].get("instance") == "o:org-w"
    assert batch[-1]["ctx"]["instance"] == "o:org-w"


# ── No context: no write, no emit ───────────────────────────────────────────

def test_no_run_context_writes_nothing_and_emits_to_no_session(tmp_path, queues, mirrors) -> None:
    """A frame with no run context. One other run is live, which the old
    code took to be "this" run."""
    wa = importlib.import_module("acb_skills.write_artifact")
    from acb_skills.error_tools import get_errors
    from acb_skills.note_tools import recall_notes, save_note
    from acb_skills.permission_policy import decide
    from orchestrator import executor

    async def _main() -> dict:
        live = queues("sid-other-run")
        out = {
            "write": await wa.write_artifact("outputs/x.md", "X"),
            "share": await wa.share_artifact("outputs/x.md"),
            "ui": await wa.emit_generative_ui('{"type":"text","props":{"text":"hi"}}'),
            "note": await save_note("NOTES.md", "a fact"),
            "recall": await recall_notes("NOTES.md"),
            "errors": await get_errors("[]"),
            "queue": executor.resolve_run_queue(None),
            "thread": executor.resolve_relay_thread_id(),
            "perm": decide({"has_write_file_redirection": True, "path": str(tmp_path / "x")}),
        }
        await _drain_tasks()
        out["live"] = live.qsize()
        return out

    got = asyncio.run(_main())
    assert "error" in got["write"] and "No workspace" in got["write"]["error"]
    assert got["share"]["artifacts"] == []
    assert got["ui"]["ok"] is False
    assert "nothing was saved" in got["note"] and "nothing was read" in got["recall"]
    assert "nothing was checked" in got["errors"]
    assert got["queue"] is None and got["thread"] is None
    assert got["perm"][:2] == (False, "write_without_workspace")
    assert got["live"] == 0, "an event reached another run's stream"
    assert mirrors == []


# ── The run side on the R8 database ─────────────────────────────────────────

@_DB_GATE
def test_two_overlapping_batch_runs_write_their_own_dirs_rows_and_sessions(
    graph_as_app, disk, queues, monkeypatch,  # noqa: F811
) -> None:
    """The whole run path, twice at once, on one event loop: ``run_agent`` for
    org A and org B. The agent of each run calls the real ``write_artifact``
    after the other run bound its context. The blob rows reach the database as
    the NOSUPERUSER NOBYPASSRLS role."""
    import acb_memory
    from acb_skills.write_artifact import write_artifact
    from orchestrator import executor

    a, b = graph_as_app.org_a, graph_as_app.org_b
    rel = f"outputs/both-{uuid.uuid4().hex[:6]}.md"

    async def _nothing(*_a, **_k):
        return ""

    async def _rehydrate(*_a, **_k):
        return 0

    @contextmanager
    def _load(_name, **_k):
        yield types.SimpleNamespace(agent_dir=disk.shared, config=_cfg(None),
                                    build_agents=lambda: [])

    events: dict[str, asyncio.Event] = {}

    async def _agent(*_a, agent_name, run_id, thread_id, **_k):
        # Run A waits until run B has bound its context, then both write.
        mine, other = ("a", "b") if thread_id == "sid-a" else ("b", "a")
        events[mine].set()
        await events[other].wait()
        await asyncio.sleep(0)
        res = await write_artifact(rel, f"BODY {mine.upper()}", overwrite=True)
        return {"answer": json.dumps(res)}

    monkeypatch.setattr(acb_memory, "rehydrate_workspace", _rehydrate)
    monkeypatch.setattr(executor, "_install_push_guard", _nothing)
    monkeypatch.setattr(executor, "_get_current_head", _nothing)
    monkeypatch.setattr(executor, "_detect_agent_commits", _nothing)
    monkeypatch.setattr(executor, "_run_with_maf_agent", _agent)
    monkeypatch.setattr(executor, "load_agent", _load)

    async def _main() -> dict:
        events.update(a=asyncio.Event(), b=asyncio.Event())
        qa, qb = queues("sid-a"), queues("sid-b")
        out_a, out_b = await asyncio.gather(
            executor.run_agent(_S, {"message": "a"}, run_id="r-a", thread_id="sid-a",
                               organization_id=a),
            executor.run_agent(_S, {"message": "b"}, run_id="r-b", thread_id="sid-b",
                               organization_id=b),
        )
        await _drain_tasks()
        return {"a": json.loads(out_a["answer"]), "b": json.loads(out_b["answer"]),
                "qa": _events(qa), "qb": _events(qb)}

    got = asyncio.run(_main())
    assert got["a"]["download_url"].startswith("/api/agent/workspace/sid-a/")
    assert got["b"]["download_url"].startswith("/api/agent/workspace/sid-b/")
    ra, rb = _in_thread(rel, "sid-a"), _in_thread(rel, "sid-b")
    assert (_tenant_dir(a) / ra).read_text(encoding="utf-8") == "BODY A"
    assert (_tenant_dir(b) / rb).read_text(encoding="utf-8") == "BODY B"
    assert got["qa"] == [ra] and got["qb"] == [rb]
    assert _rows(graph_as_app, ra) == [(a, f"o:{a}", "BODY A")]
    assert _rows(graph_as_app, rb) == [(b, f"o:{b}", "BODY B")]


def test_the_global_dict_is_gone() -> None:
    """No module of the product keeps a process-global artifact dict. The
    name that held it must not come back."""
    root = Path(__file__).resolve().parents[2]
    hits = []
    for base in ("apps", "packages", "evals"):
        for p in (root / base).rglob("*.py"):
            if "_WRITE_ARTIFACT_CONTEXT" in p.read_text(encoding="utf-8", errors="replace"):
                hits.append(p.relative_to(root).as_posix())
    assert hits == [], hits


def test_write_artifact_keeps_no_module_level_run_state() -> None:
    """No module-level mutable mapping in ``write_artifact.py`` may hold run
    state, under any name. The one run store is the ContextVar."""
    import ast

    src = (Path(__file__).resolve().parents[2]
           / "packages/acb_skills/acb_skills/write_artifact.py").read_text(encoding="utf-8")
    bad = []
    for node in ast.parse(src).body:
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        mutable = isinstance(value, (ast.Dict, ast.DictComp)) or (
            isinstance(value, ast.Call) and getattr(value.func, "id", "") in
            {"dict", "defaultdict", "OrderedDict"})
        if mutable:
            bad += [ast.unparse(t) for t in targets]
    assert bad == [], f"a module-level dict in write_artifact.py: {bad}"


# ── The two live carry sites, through the executor itself ──────────────────
#
# The tests above call ``carry_run_context`` directly. These two drive the
# executor's own Copilot paths, so deleting the carry loop at a call site
# turns exactly one of them red: stream Tier 1.5, and ``_run_with_maf_agent``.

def _sdk_agent_class():
    """A ``GitHubCopilotAgent`` whose ``run`` stands in for the SDK session.

    ``_prepare_tools`` is the REAL upstream adapter, so the handler is the
    one the SDK would get. ``run`` calls the ``write_artifact`` handler from
    another thread, as the SDK's JSON-RPC reader does. Nothing else of the
    SDK runs, so no CLI is needed.
    """
    from agent_framework_github_copilot import GitHubCopilotAgent
    from copilot.tools import ToolInvocation

    class _SdkAgent(GitHubCopilotAgent):
        rel = ""
        results: ClassVar[list] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def _call_write_artifact(self) -> None:
            tool = next(t for t in self._prepare_tools(self._tools)
                        if t.name == "write_artifact")
            out = await _sdk_dispatch(
                asyncio.get_running_loop(), tool.handler, ToolInvocation(
                    tool_name="write_artifact",
                    arguments={"path": self.rel, "content": "SDK BODY",
                               "overwrite": True}))
            type(self).results.append(out.text_result_for_llm)
            for _ in range(5):  # let the blob mirror and _notify run
                await asyncio.sleep(0)

        def run(self, _msg=None, *, stream=False, **_kw):
            if stream:
                async def _gen():
                    await self._call_write_artifact()
                    return
                    yield  # pragma: no cover - an async generator
                return _gen()

            async def _once():
                await self._call_write_artifact()
                return types.SimpleNamespace(text="done")
            return _once()

    return _SdkAgent


@pytest.fixture
def sdk_run(disk, monkeypatch, mirrors):  # noqa: F811
    """The executor with ``load_agent`` giving one Copilot SDK agent that
    holds the real ``write_artifact``. The git helpers and the rehydrate are
    stubbed. The executor's own tool injection still runs."""
    import acb_memory
    from acb_skills.write_artifact import write_artifact
    from orchestrator import executor

    cls = _sdk_agent_class()
    cls.rel = f"outputs/sdk-{uuid.uuid4().hex[:6]}.md"
    cls.results = []

    async def _nothing(*_a, **_k):
        return ""

    async def _rehydrate(*_a, **_k):
        return 0

    class _Client:
        async def get_last_session_id(self):
            return None

    def _build():
        agent = cls(instructions="x", tools=[write_artifact])
        agent._client = _Client()
        return [agent]

    @contextmanager
    def _load(_name, **_k):
        yield types.SimpleNamespace(agent_dir=disk.shared, config=_cfg(None),
                                    build_agents=_build)

    monkeypatch.setattr(acb_memory, "rehydrate_workspace", _rehydrate)
    monkeypatch.setattr(executor, "_install_push_guard", _nothing)
    monkeypatch.setattr(executor, "_get_current_head", _nothing)
    monkeypatch.setattr(executor, "_detect_agent_commits", _nothing)
    monkeypatch.setattr(executor, "load_agent", _load)
    monkeypatch.setattr(executor, "_get_stored_session_id", lambda *_a, **_k: None)
    return cls


def test_the_stream_copilot_path_carries_the_run_context(disk, sdk_run, mirrors) -> None:  # noqa: F811
    """``run_agent_stream`` down Tier 1.5. The file lands in the run's tenant
    dir, the blob mirror carries the run's key, and the artifact event
    reaches this run's own stream."""
    from orchestrator import executor

    tid = f"t15-{uuid.uuid4().hex[:6]}"

    async def _collect() -> list[dict]:
        out = []
        async for line in executor.run_agent_stream(
            _S, {"message": "write it"}, run_id="r-t15", thread_id=tid,
            organization_id="org-t15",
        ):
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
        return out

    events = asyncio.run(_collect())
    rel = _in_thread(sdk_run.rel, tid)
    assert sdk_run.results and f"/api/agent/workspace/{tid}/" in sdk_run.results[0], (
        sdk_run.results, [e.get("type") for e in events])
    assert (_tenant_dir("org-t15") / rel).read_text(encoding="utf-8") == "SDK BODY"
    assert [m["instance"] for m in mirrors] == ["o:org-t15"]
    created = [e["value"]["path"] for e in events
               if e.get("type") == "CUSTOM" and e.get("name") == "artifact_created"]
    assert created == [rel], [e.get("type") for e in events]


def test_the_batch_copilot_path_carries_the_run_context(disk, sdk_run, mirrors, queues) -> None:  # noqa: F811
    """``run_agent`` down ``_run_with_maf_agent``, the batch Copilot path."""
    from orchestrator import executor

    tid = f"b-{uuid.uuid4().hex[:6]}"

    async def _main() -> list[str]:
        q = queues(tid)
        await executor.run_agent(_S, {"message": "write it"}, run_id="r-b",
                                 thread_id=tid, organization_id="org-bt")
        await _drain_tasks()
        return _events(q)

    got = asyncio.run(_main())
    rel = _in_thread(sdk_run.rel, tid)
    assert sdk_run.results and f"/api/agent/workspace/{tid}/" in sdk_run.results[0], sdk_run.results
    assert (_tenant_dir("org-bt") / rel).read_text(encoding="utf-8") == "SDK BODY"
    assert [m["instance"] for m in mirrors] == ["o:org-bt"]
    assert got == [rel]
