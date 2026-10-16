"""No copy of mail in a hidden chat store (WS-17 EM-G11f3, PR-G8b).

Spec: ``project-docs/specs/email_app_master_plan.md`` section 12.3.13.9.1.
A chat turn about mail went to stores keyed on the member, the agent or the
organization. No mailbox purge reaches them, and the member cannot see most of
them. So a run that holds mail writes none of them. One predicate decides,
``acb_skills.egress.run_holds_mail``: the agent reads mail
(``UNTRUSTED_INPUT_AGENTS``), or the session's EM-G11d mark is not clear. An
unknown mark (a Redis fault in a run with a member) counts as mail.

R7 fences named here, each a test below:

* ``test_a_mail_run_writes_no_memory`` (acceptance 1, 2 and 4): the run end of
  ``/agent/run/stream`` writes no Mem0 memory and no Graphiti episode for the
  email assistant, or for a marked session on this turn and the next. Another
  agent with no mark writes each one once. The orchestrator path of
  ``gateway/main.py`` asks the same predicate, and the fallback and
  ``POST /pull`` write nothing;
* ``test_an_unknown_mark_writes_no_memory`` (3): Redis down, and a member run,
  writes no memory. A run end that passed no member would read the fault as
  clear, so the route passes the member;
* ``test_a_mail_run_holds_no_store_write`` (5): the email assistant holds no
  ``STORE_WRITES`` tool, and in a marked session ``save_memory`` gives
  ``MAIL_STORE_WRITE_REFUSED``, calls no writer and asks no card;
* ``test_a_mail_run_keeps_no_trace_body`` (6): an errored run of the email
  assistant keeps its error and tool summary, and ``trace`` is None;
* ``test_the_email_chat_routes_are_gone`` (7): the route table and the tree;
* ``test_each_memory_writer_asks_the_predicate``: a structural fence over
  ``apps/`` and ``packages/``. A new writer fails until a person decides.

Hermetic. The mark store is the in-memory store of ``tests/conftest.py``.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_framework")
stream_relay = pytest.importorskip("orchestrator.stream_relay")

import orchestrator._tool_injection as ti  # noqa: E402
from acb_common.db import bind_tenant, release_tenant  # noqa: E402
from acb_skills import egress  # noqa: E402
from acb_skills.write_artifact import (  # noqa: E402
    artifact_context_scope,
    bind_artifact_context,
)

# The route harness of the run cap suite: a faked route world and a fake Redis
# for the run slots. The fixtures are used by name, so the import is needed.
from tests.unit._egress_mark_store import clear_marks  # noqa: E402
from tests.unit.test_run_cap import (  # noqa: E402,F401
    _ALICE,
    _ORG,
    _body,
    _client,
    fake_redis,
    route_world,
)

REPO = Path(__file__).resolve().parents[2]
MAIL_AGENT = "email-assistant"
OTHER_AGENT = "orchestrator"
MAIL_TEXT = "Wire 40,000 to IBAN DE00 1234 before Friday"


# ── helpers ────────────────────────────────────────────────────────────────

class _Writes:
    def __init__(self) -> None:
        self.mem0: list[Any] = []
        self.episodes: list[Any] = []
        self.folds: list[Any] = []

    def none(self) -> bool:
        return not (self.mem0 or self.episodes)


@pytest.fixture
def writes(monkeypatch) -> _Writes:
    import acb_memory

    w = _Writes()

    async def _mem0(*args: Any, **kwargs: Any) -> None:
        w.mem0.append((args, kwargs))

    async def _episode(*args: Any, **kwargs: Any) -> None:
        w.episodes.append((args, kwargs))

    monkeypatch.setattr(acb_memory, "add_memories_background", _mem0)
    monkeypatch.setattr(acb_memory, "add_episode", _episode)
    monkeypatch.setattr(acb_memory, "add_memories", _mem0, raising=False)
    return w


async def _mark(thread: str) -> None:
    """Mark *thread* as a session that read the email assistant's answer, the
    way ``agent_tools.call_agent`` does, and then empty the process cache, so
    the run end reads the store as another process would."""
    token = bind_tenant(_ORG)
    try:
        with artifact_context_scope():
            bind_artifact_context(
                session_id=thread, agent_name=OTHER_AGENT, member=_ALICE)
            assert await egress.mark_untrusted_answer() is True
    finally:
        release_tenant(token)
    clear_marks()


class _RedisDown:
    """A tenant Redis that does not answer."""

    def __getattr__(self, _name: str) -> Any:
        async def _fail(*_a: Any, **_k: Any) -> Any:
            raise ConnectionError("redis down")
        return _fail


def _stream_run(monkeypatch, route_world, writes: _Writes, agent: str,  # noqa: F811
                thread: str) -> Any:
    """Drive ``POST /agent/run/stream`` for *agent* and return the run-end
    hook that the route hands to ``run_detached``. The fold is a spy that
    records ``holds_mail`` and returns a folded answer that quotes a mail."""
    from gateway import chat_fold
    from gateway.routes import agent as routes_agent

    hooks: list[Any] = []

    async def _run_detached(thread_id, gen, **kw):
        hooks.append(kw["on_complete"])
        yield {"type": "RUN_STARTED", "threadId": thread_id}

    async def _fold(*_a: Any, **kw: Any) -> dict[str, Any]:
        writes.folds.append(kw.get("holds_mail"))
        return {"content": f"Your mail says: {MAIL_TEXT}"}

    monkeypatch.setattr(stream_relay, "run_detached", _run_detached)
    monkeypatch.setattr(chat_fold, "persist_final_assistant_message", _fold)
    monkeypatch.setattr(
        routes_agent, "_address_agent", lambda req, room, org=None: agent)
    body = _body(thread)
    body["agent"] = agent
    body["payload"]["message"] = f"Summarise this: {MAIL_TEXT}"
    with _client(_ALICE).stream("POST", "/agent/run/stream", json=body) as r:
        assert r.status_code == 200, r.read()
        r.read()
    assert len(hooks) == 1
    return hooks[0]


def _complete(hook: Any) -> None:
    """Run the run-end hook under the run's tenant, as ``run_detached`` does."""
    async def _go() -> None:
        token = bind_tenant(_ORG)
        try:
            await hook()
        finally:
            release_tenant(token)
    asyncio.run(_go())


def _function(rel: str, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _called(node: ast.AST, name: str) -> list[ast.Call]:
    return [c for c in ast.walk(node) if isinstance(c, ast.Call)
            and (getattr(c.func, "id", None) == name or getattr(c.func, "attr", None) == name)]


# ── acceptance 1, 2 and 4 ──────────────────────────────────────────────────

def test_a_mail_run_writes_no_memory(monkeypatch, route_world, writes) -> None:  # noqa: F811
    # 1. The email assistant's own run writes to neither store.
    hook = _stream_run(monkeypatch, route_world, writes, MAIL_AGENT, "g8b-mail")
    _complete(hook)
    assert writes.none(), (writes.mem0, writes.episodes)
    assert writes.folds == [True], "the trace body must be dropped too"

    # 1. Another agent with no mark writes each store ONCE, at the run end.
    hook = _stream_run(monkeypatch, route_world, writes, OTHER_AGENT, "g8b-clear")
    assert writes.none(), "nothing may be written at the run START"
    _complete(hook)
    assert len(writes.mem0) == 1 and len(writes.episodes) == 1
    assert writes.folds[-1] is False
    writes.mem0.clear()
    writes.episodes.clear()

    # 2. The orchestrator delegated to the email assistant, so the session is
    #    marked. This turn writes nothing, and nor does the next one.
    hook = _stream_run(monkeypatch, route_world, writes, OTHER_AGENT, "g8b-marked")
    asyncio.run(_mark("g8b-marked"))
    _complete(hook)
    hook = _stream_run(monkeypatch, route_world, writes, OTHER_AGENT, "g8b-marked")
    _complete(hook)
    assert writes.none(), (writes.mem0, writes.episodes)
    assert writes.folds[-2:] == [True, True]


def test_the_orchestrator_path_of_main_asks_the_same_predicate() -> None:
    """Acceptance 2, the path of ``gateway/main.py`` (W3), and 4 (W4)."""
    main = "apps/services/gateway/gateway/main.py"
    cb = _function(main, "_persist_cb")
    asks = _called(cb, "run_holds_mail")
    assert len(asks) == 1, "the run end asks the predicate once"
    ctx = asks[0].args[1]
    assert isinstance(ctx, ast.Dict)
    assert {k.value for k in ctx.keys if isinstance(k, ast.Constant)} == {
        "session_id", "member"}, "without the member a Redis fault reads clear"
    fold = _called(cb, "persist_final_assistant_message")
    assert fold and any(k.arg == "holds_mail" for k in fold[0].keywords)
    assert asks[0].lineno < fold[0].lineno
    guard = next(n for n in ast.walk(cb) if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Name) and n.test.id == "_holds_mail")
    assert any(isinstance(s, ast.Return) for s in guard.body)
    writers = _called(cb, "add_memories_background") + _called(cb, "add_episode")
    assert writers and all(w.lineno > guard.lineno for w in writers)
    # W4: the copilot route writes memory only inside _persist_cb.
    route = _function(main, "copilot_chat")
    names = {n.id for n in ast.walk(route) if isinstance(n, ast.Name)}
    outside = [n for n in ast.walk(route) if isinstance(n, ast.Name)
               and n.id in {"add_memories_background", "add_episode"}
               and not (cb.lineno <= n.lineno <= cb.end_lineno)]
    assert "run_holds_mail" in names and outside == []


def test_pull_writes_no_memory(monkeypatch, writes) -> None:
    """Acceptance 4, ``POST /pull`` (W5). It has no session to mark."""
    import orchestrator.agents as orch_agents
    from acb_auth.roles import UserContext, UserRole
    from gateway import main as gw_main

    class _Agent:
        def __init__(self) -> None:
            self.default_options: dict[str, Any] = {}

        async def __aenter__(self) -> _Agent:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def run(self, _query: str) -> Any:
            return SimpleNamespace(text=f"The mail says: {MAIL_TEXT}")

    async def _enrich(*_a: Any, **_k: Any) -> str:
        return ""

    monkeypatch.setattr(orch_agents, "build_orchestrator_agent", lambda **_k: _Agent())
    monkeypatch.setattr(orch_agents, "enrich_instructions_with_memory", _enrich)
    user = UserContext(email=_ALICE, role=UserRole.EMPLOYEE, organization_id=_ORG)

    async def _go() -> Any:
        out = await gw_main.pull(gw_main.PullRequest(query="What does my mail say?"), user)
        await asyncio.sleep(0)  # a create_task writer would run here
        return out

    out = asyncio.run(_go())
    assert MAIL_TEXT in out.answer
    assert writes.none(), (writes.mem0, writes.episodes)


# ── acceptance 3 ───────────────────────────────────────────────────────────

def test_an_unknown_mark_writes_no_memory(
    monkeypatch, route_world, writes,  # noqa: F811
) -> None:
    hook = _stream_run(monkeypatch, route_world, writes, OTHER_AGENT, "g8b-down")
    clear_marks()
    monkeypatch.setattr(egress, "get_tenant_redis", lambda: _RedisDown())
    _complete(hook)
    assert writes.none(), (writes.mem0, writes.episodes)
    assert writes.folds == [True]

    # Why the run end passes the member: with no member the run cannot show a
    # card, so a fault reads clear, and that run would write memory.
    async def _states() -> tuple[bool, bool]:
        token = bind_tenant(_ORG)
        try:
            with_member = await egress.run_holds_mail(
                OTHER_AGENT, {"session_id": "g8b-down", "member": _ALICE})
            without = await egress.run_holds_mail(
                OTHER_AGENT, {"session_id": "g8b-down"})
            return with_member, without
        finally:
            release_tenant(token)

    assert asyncio.run(_states()) == (True, False)


# ── acceptance 5 ───────────────────────────────────────────────────────────

@pytest.fixture
def no_db(monkeypatch) -> None:
    import orchestrator.app_tools as app_tools

    monkeypatch.setattr(ti, "_build_registry_block", lambda: "Registered agents: (stub)")
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda name: frozenset())
    monkeypatch.setattr(app_tools, "load_app_action_tools", lambda name: [])
    ti._build_injected_tools_addendum.cache_clear()
    yield
    ti._build_injected_tools_addendum.cache_clear()


class _Native:
    def __init__(self, name: str) -> None:
        self.name = name
        self.default_options: dict[str, Any] = {"tools": [], "instructions": "x"}


def _injected(agent: str, config: dict[str, Any]) -> frozenset[str]:
    return ti._inject_agent_tools(
        [_Native(agent)], tool_scope=config.get("tool_scope") or None,
        agent_name=agent, agent_config=config, no_egress=False,
    )


def test_a_mail_run_holds_no_store_write(monkeypatch, no_db) -> None:
    cfg = json.loads((REPO / "apps/agents/agent-email-assistant/config.json")
                     .read_text(encoding="utf-8"))
    assert "save_memory" in cfg["tool_scope"], "the scope still names it"
    held = _injected(MAIL_AGENT, cfg)
    assert held, "the injection ran"
    assert not (held & egress.STORE_WRITES), sorted(held & egress.STORE_WRITES)
    # The control: another agent still holds the store writes.
    assert egress.STORE_WRITES & _injected(OTHER_AGENT, {})

    # In a marked session the gate refuses save_memory at once: no writer
    # call and no card.
    calls: list[str] = []
    cards: list[str] = []

    async def save_memory(fact: str) -> str:
        calls.append(fact)
        return "Memory saved."

    async def _card(tool_name: str, _kwargs: dict[str, Any]) -> bool:
        cards.append(tool_name)
        return True

    monkeypatch.setattr(ti, "_untrusted_answer_card", _card)
    gated = ti._gate_injected_tool(save_memory)

    async def _call(thread: str) -> str:
        token = bind_tenant(_ORG)
        try:
            with artifact_context_scope():
                bind_artifact_context(
                    session_id=thread, agent_name=OTHER_AGENT, member=_ALICE)
                return await gated(fact=MAIL_TEXT)
        finally:
            release_tenant(token)

    assert asyncio.run(_call("g8b-gate-clear")) == "Memory saved."
    assert calls == [MAIL_TEXT]
    calls.clear()
    asyncio.run(_mark("g8b-gate"))
    out = asyncio.run(_call("g8b-gate"))
    assert out == egress.MAIL_STORE_WRITE_REFUSED.format(tool="save_memory")
    assert calls == [] and cards == []
    # An unknown mark refuses too.
    monkeypatch.setattr(egress, "get_tenant_redis", lambda: _RedisDown())
    clear_marks()
    out = asyncio.run(_call("g8b-gate-down"))
    assert out == egress.MAIL_STORE_WRITE_REFUSED.format(tool="save_memory")
    assert calls == [] and cards == []


# ── acceptance 6 ───────────────────────────────────────────────────────────

_ERROR_EVENTS = [
    {"type": "RUN_STARTED", "runId": "r-1", "_stream_id": "1000-0"},
    {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "read_email",
     "_stream_id": "1001-0"},
    {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": MAIL_TEXT,
     "_stream_id": "1002-0"},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": MAIL_TEXT,
     "_stream_id": "1003-0"},
    {"type": "RUN_ERROR", "message": "the model timed out", "_stream_id": "1004-0"},
]


def _traced_row(monkeypatch, agent: str) -> dict[str, Any]:
    """Fold an errored run through ``persist_final_assistant_message`` with no
    answer of the predicate, as the restart sweep does, and return the
    ``agent_run`` row that it would write."""
    import orchestrator.stream_relay as relay
    from gateway import chat_fold, run_trace
    from gateway.routes import chat as chat_routes

    rows: list[dict[str, Any]] = []

    async def _replay(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        return [dict(e) for e in _ERROR_EVENTS]

    monkeypatch.setattr(relay, "replay_events", _replay)
    monkeypatch.setattr(run_trace, "_persist_row", lambda row, org: rows.append(row))
    monkeypatch.setattr(chat_routes, "_ensure_session", lambda *a, **k: None)
    monkeypatch.setattr(chat_routes, "_upsert_messages", lambda *a, **k: [])

    async def _no_authority(*_a: Any) -> None:
        return None

    monkeypatch.setattr(chat_fold, "_run_authority", _no_authority)
    asyncio.run(chat_fold.persist_final_assistant_message(
        "g8b-trace", "m-1", user_id=_ALICE, agent_name=agent, run_id="r-1",
        organization_id=_ORG))
    assert len(rows) == 1
    return rows[0]


def test_a_mail_run_keeps_no_trace_body(monkeypatch) -> None:
    row = _traced_row(monkeypatch, MAIL_AGENT)
    assert row["status"] == "error"
    assert row["error_message"] == "the model timed out"
    assert row["tool_summary"] and row["tool_summary"][0]["name"] == "read_email"
    assert row["trace"] is None
    assert MAIL_TEXT not in json.dumps(row)

    # Another agent keeps its trace, as on main.
    row = _traced_row(monkeypatch, OTHER_AGENT)
    assert row["status"] == "error" and row["trace"] is not None
    assert MAIL_TEXT in json.dumps(row["trace"])


# ── acceptance 7 ───────────────────────────────────────────────────────────

#: A path that names a removed route. ``openai/chat`` is not one.
_ROUTE_PATH = re.compile(r"(?<![A-Za-z])ai/(chat|quick-action)\b")

#: The one file that may name the path: the test that fences its absence.
_ABSENCE_FENCE = "workbench/control_plane/src/app/email/components/emailPaneCards.test.ts"

_SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".md", ".json", ".sh"}
_SKIP_PARTS = {"node_modules", ".next", "__pycache__", ".venv", "dist", "build"}


def test_the_email_chat_routes_are_gone() -> None:
    from fastapi.testclient import TestClient
    from gateway.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if p.endswith(("/ai/chat", "/ai/quick-action"))]
    client = TestClient(app)
    for path in ("/email/ai/chat", "/email/ai/quick-action"):
        assert client.post(path, json={}).status_code == 404, path

    hits: list[str] = []
    for base in ("apps", "packages", "workbench"):
        for path in (REPO / base).rglob("*"):
            if (path.suffix not in _SCAN_SUFFIXES or not path.is_file()
                    or _SKIP_PARTS & set(path.parts)):
                continue
            rel = path.relative_to(REPO).as_posix()
            if rel == _ABSENCE_FENCE:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _ROUTE_PATH.search(text):
                hits.append(rel)
    assert hits == [], hits


# ── the structural fence ───────────────────────────────────────────────────

#: The memory writers.
WRITERS = frozenset({
    "add_memories_background", "add_episode", "add_memories", "add_scoped_memories",
})

#: A file whose writers need not ask the predicate, and why. An entry that
#: names no writer any more is stale and fails.
#:
#: ⚠️ Limit (R7): ``POST /memory/{scope}/add`` (``routes/memory.py``) calls the
#: Mem0 client's ``add`` directly, so this scan does not see it. AgentChat
#: posts the default agent's turns there on unmount (``chatMemorySave.ts``).
#: Section 12.3.13.9.1 names it W10, and a follow-up owns it.
ALLOWLIST: dict[str, str] = {
    "packages/acb_memory/acb_memory/": "the store itself",
    "apps/services/gateway/gateway/routes/email/automation/assistant.py": (
        "the writing style of f2, keyed on the mailbox, which the purge reaches"),
    "apps/services/gateway/gateway/routes/email/automation/drafting.py": (
        "the draft precedent and the reply preference of f2, keyed on the "
        "mailbox, which the purge reaches"),
    "packages/acb_skills/acb_skills/memory_tools.py": (
        "the STORE_WRITES tools. The email assistant does not hold them, and "
        "the tool gate refuses each one in a run that holds mail "
        "(_tool_injection._gate_injected_tool)"),
    "apps/services/gateway/gateway/routes/tasks/task_memory.py": (
        "the task-manager's decision memory: a committed task title, not a "
        "chat turn"),
}


def _allowed(rel: str) -> str | None:
    return next((k for k in ALLOWLIST if rel == k or (k.endswith("/") and rel.startswith(k))),
                None)


def _writer_sites() -> list[tuple[str, int, ast.AST | None]]:
    """Each reference to a writer in ``apps/`` and ``packages/``, with its
    innermost enclosing function."""
    sites: list[tuple[str, int, ast.AST | None]] = []
    for base in ("apps", "packages"):
        for path in (REPO / base).rglob("*.py"):
            if _SKIP_PARTS & set(path.parts):
                continue
            text = path.read_text(encoding="utf-8-sig")
            if not any(w in text for w in WRITERS):
                continue
            rel = path.relative_to(REPO).as_posix()
            tree = ast.parse(text)
            parents: dict[ast.AST, ast.AST] = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                name = (node.id if isinstance(node, ast.Name)
                        else node.attr if isinstance(node, ast.Attribute) else None)
                if name not in WRITERS or not isinstance(node.ctx, ast.Load):
                    continue
                fn = parents.get(node)
                while fn is not None and not isinstance(
                        fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = parents.get(fn)
                sites.append((rel, node.lineno, fn))
    return sites


def _asks(fn: ast.AST | None) -> bool:
    if fn is None:
        return False
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == "run_holds_mail":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "run_holds_mail":
            return True
    return False


def test_each_memory_writer_asks_the_predicate() -> None:
    sites = _writer_sites()
    assert sites, "the scan found no writer"
    bad = [f"{rel}:{line}" for rel, line, fn in sites
           if not _allowed(rel) and not _asks(fn)]
    assert bad == [], (
        "a memory write that does not ask acb_skills.egress.run_holds_mail. "
        f"Ask it, or add an ALLOWLIST entry with a reason: {bad}")
    used = {_allowed(rel) for rel, _l, _f in sites}
    stale = sorted(set(ALLOWLIST) - used)
    assert stale == [], f"stale ALLOWLIST entries: {stale}"
    assert all(reason.strip() for reason in ALLOWLIST.values())
    # The two run-end writers of the chat ask it.
    asked = {(rel, getattr(fn, "name", "")) for rel, _l, fn in sites if _asks(fn)}
    assert ("apps/services/gateway/gateway/routes/agent.py", "_extract_run_memory") in asked
    assert ("apps/services/gateway/gateway/main.py", "_persist_cb") in asked


def test_the_structural_fence_bites(tmp_path) -> None:
    """A writer with no check is found, and one that asks is not."""
    src = (
        "async def bad(u, c):\n    await add_memories_background(u, c)\n"
        "async def good(a, c):\n    if await run_holds_mail(a, c):\n        return\n"
        "    await add_episode(name='n', content='c')\n")
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    assert not _asks(fns["bad"]) and _asks(fns["good"])


# ── fix round 1, P0: the REAL specialist path marks the session ────────────

def _email_specialist(monkeypatch) -> Any:
    """The orchestrator's own ``email_assistant`` specialist tool, built by
    ``_load_specialist_agents_as_tools`` from a one-entry registry."""
    import orchestrator.agents as orch_pkg
    from gateway.routes import agent as routes_agent

    # The package shadows ``orchestrator/agents.py`` and loads it as ``_mod``.
    # The tool builder and ``delegate_to_agent`` live in that module.
    orch_agents = orch_pkg._mod
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents",
                        lambda: [{"name": MAIL_AGENT, "description": "Reads mail."}])
    monkeypatch.setattr(routes_agent, "_AGENT_REGISTRY", [])
    tool = next(t for t in orch_agents._load_specialist_agents_as_tools()
                if t.name == "email_assistant")
    return tool.func


class _SubRunStopped(RuntimeError):
    pass


@pytest.mark.parametrize("streaming", [True, False], ids=["streaming", "batch"])
def test_the_specialist_path_marks_the_session(monkeypatch, streaming: bool) -> None:
    """Acceptance 2 through the D99 path: the orchestrator calls its
    ``email_assistant`` specialist tool. The executor marks the calling
    session BEFORE the sub-run does any work, so the sub-run here stops at
    its first check and the mark still holds. Then the run end holds mail,
    and a ``fetch_page`` of the same session asks the member's card."""
    import orchestrator.executor as executor

    specialist = _email_specialist(monkeypatch)

    async def _refuse(_name: str) -> None:
        raise _SubRunStopped("the sub-run stops before any work")

    async def _no_recovery(*_a: Any, **_k: Any) -> None:
        return None

    import orchestrator.mutation as mutation

    monkeypatch.setattr(executor, "_assert_may_run_agent", _refuse)
    # The batch path tries to recover a failed run with a model call and a
    # self-mutation. Neither may leave this hermetic test.
    monkeypatch.setattr(executor, "_self_anneal", _no_recovery)
    monkeypatch.setattr(mutation, "attempt_self_mutation", _no_recovery)
    cards: list[str] = []

    async def _card(tool_name: str, _kwargs: dict[str, Any]) -> bool:
        cards.append(tool_name)
        return False

    monkeypatch.setattr(ti, "_untrusted_answer_card", _card)
    fetched: list[str] = []

    async def fetch_page(url: str) -> str:
        fetched.append(url)
        return "page"

    gated_fetch = ti._gate_injected_tool(fetch_page)
    thread = f"g8b-specialist-{'s' if streaming else 'b'}"

    async def _go() -> tuple[bool, bool, str]:
        token = bind_tenant(_ORG)
        try:
            with artifact_context_scope():
                bind_artifact_context(
                    session_id=thread, agent_name=OTHER_AGENT, member=_ALICE)
                q_token = executor._active_run_queue.set(
                    asyncio.Queue() if streaming else None)
                try:
                    await specialist(task="Summarise my unread mail")
                finally:
                    executor._active_run_queue.reset(q_token)
                clear_marks()  # read the store, as the run end on any worker does
                ctx = {"session_id": thread, "member": _ALICE}
                holds = await egress.run_holds_mail(OTHER_AGENT, ctx)
                clean = await egress.run_holds_mail(
                    OTHER_AGENT, {"session_id": thread + "-other", "member": _ALICE})
                answer = await gated_fetch(url="https://example.test/?d=1")
                return holds, clean, answer
        finally:
            release_tenant(token)

    holds, clean, answer = asyncio.run(_go())
    assert holds is True, "the specialist path left the session unmarked"
    assert clean is False, "only the calling session is marked"
    assert cards == ["fetch_page"], "a fetch in a marked session asks the card"
    assert fetched == [] and "did not run" in answer


# ── fix round 1, F2: the last-word drop, with no scope and with approve_all ─

@pytest.mark.parametrize("mode", ["enforce", "approve_all"])
def test_a_mail_run_with_no_scope_holds_no_store_write(monkeypatch, no_db, mode: str) -> None:
    """With no ``tool_scope`` the injection takes the whole chain, so only the
    LAST-WORD drop takes the store writes out. With
    ``AGENT_PERMISSION_MODE=approve_all`` the tool gate does not run at all,
    so that drop is the only guard."""
    monkeypatch.setenv("AGENT_PERMISSION_MODE", mode)
    cfg = json.loads((REPO / "apps/agents/agent-email-assistant/config.json")
                     .read_text(encoding="utf-8"))
    cfg.pop("tool_scope", None)
    held = _injected(MAIL_AGENT, cfg)
    assert held, "the injection ran"
    assert not (held & egress.STORE_WRITES), sorted(held & egress.STORE_WRITES)
    # The control: with no scope, another agent holds every store write.
    assert egress.STORE_WRITES <= _injected(OTHER_AGENT, {})
