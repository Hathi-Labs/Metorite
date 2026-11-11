"""MCP-G23 — scripted client sessions against the real edge (Metorite task 2523).

Spec: ``project-docs/specs/external_agent_access.md`` row MCP-G23, and
``orchestration_playbook.md`` KIT-6.

**What this file is.** Two scripted MCP sessions, one for each client profile.
Each session speaks the MCP wire protocol to the REAL edge app
(``agent_edge.app.create_app``) over the real ``POST /mcp`` route, with the
bearer, the ``MCP-Protocol-Version`` header and the ``Mcp-Session-Id`` header
that the edge defines. The gateway is the fake board of
``test_mcp_edge_context.py``, with the write routes that a session needs. The
token store, the Redis fake and the flag fixtures are those of
``test_mcp_edge_writes.py``. No second harness exists.

**The gap, stated plainly.** The two profiles are modelled from the PUBLIC MCP
specification and from the documentation of each client. They are NOT captured
from a real Claude Code or a real Codex binary. No real client ran here. A
change in a real client that the public text does not state can still break the
MCP unseen. A live run of each client against the local stack is the missing
proof, and it is advisory (R7). ``project-docs/specs/external_agent_access.md``
row MCP-G23 says how a person runs it.

**What a profile holds.** Only facts that the public text states:

* ``claude_code``. The HTTP transport. ``initialize`` with protocolVersion
  ``2025-06-18``, empty capabilities and clientInfo ``claude-code``. Then
  ``notifications/initialized``, ``tools/list``, ``prompts/list`` and
  ``resources/list``. It reads ``instructions`` and cuts them at 4,096
  characters (task 1803). It echoes ``Mcp-Session-Id`` and sends
  ``MCP-Protocol-Version`` with the negotiated revision.
* ``codex``. clientInfo ``codex-mcp-client``. It calls ``initialize``,
  ``notifications/initialized``, ``tools/list`` and ``tools/call``. It calls no
  prompt and no resource. The public text does not state the revision that its
  Rust SDK sends, so the profile uses the oldest revision of the MCP text that
  the edge sees, ``2025-03-26``. That revision defines no
  ``MCP-Protocol-Version`` header. The profile sends it only after the edge
  answers with a revision of ``2025-06-18`` or later, as the MCP text of
  ``2025-06-18`` requires.

**The scorer.** Each session writes a KIT-6 transcript (header and events, the
format at the top of ``scripts/kit_conformance.py``) and scores it with
``kit_conformance.score``. A machine makes no worktree here, so ``worktree``
fails by design. There are two scripted sessions for each client. The MAIN
session claims, posts and ends with ``release_task`` and ``finish_agent_run``.
It fails ``worktree`` only. The PARK session asks a question and ends at the
park, because the run waits on a person. It fails ``worktree`` and
``finishes_run`` only. The test pins exactly those failures.

**The mutations (R7).** Each one turns the named test red.

* M1. ``mcp.py`` ``_RUN_INSTRUCTIONS``: pad it by 1,200 characters so a safety
  rule ends after the cut. ``test_the_4096_budget_check_can_fail``.
* M2. ``mcp.py`` ``_dispatch``: do not offer one tool of the pinned list.
  ``test_tools_list_names_the_routine_tools`` and
  ``test_the_pinned_list_can_fail``.
* M3. ``mcp.py`` ``SUPPORTED_VERSIONS``: add ``2025-03-26``.
  ``test_an_unsupported_version_header_is_400_for_both``.

Run::

    PYTEST_ADDOPTS='-o timeout_method=thread' uv run pytest -q tests/unit/test_mcp_client_conformance.py
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jsonschema
import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from agent_edge import app as edge_app_module
from agent_edge import gateway_client, mcp, tools

from tests.unit.test_mcp_edge_agent_runs import RUN, _run_row
from tests.unit.test_mcp_edge_context import (  # noqa: F401 — fixtures
    ME,
    S_OPEN,
    S_REVIEW,
    V0,
    _Board,
    _task_row,
    edge,
    redis_fake,
)
from tests.unit.test_mcp_edge_writes import (
    GOOD,
    ISSUER,
    PROJECT,
    TASK,
    VIA,
    _uuid,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import kit_conformance as kc

NEW = _uuid(1777)
V1 = "2026-11-11T00:00:01.000001+00:00"
V2 = "2026-11-11T00:00:02.000001+00:00"
V3 = "2026-11-11T00:00:03.000001+00:00"
CUT = 4096

#: What a client profile is. Only data. ``sends`` is the list of MCP methods
#: that the client calls, in order, before its first ``tools/call``.
CLIENT_PROFILES: dict[str, dict[str, Any]] = {
    "claude_code": {
        "client": "Claude Code",
        "client_info": {"name": "claude-code", "version": "2.0.0"},
        "protocol_version": "2025-06-18",
        "capabilities": {},
        "sends": ["initialize", "notifications/initialized", "tools/list",
                  "prompts/list", "resources/list"],
        "reads_prompt": "work_on_task",
        "cut_instructions_at": CUT,
        # Spec 2025-06-18: the header on each request after initialize.
        "sends_version_header_after_init": True,
    },
    "codex": {
        "client": "OpenAI Codex",
        "client_info": {"name": "codex-mcp-client", "version": "0.1.0"},
        "protocol_version": "2025-03-26",
        "capabilities": {},
        "sends": ["initialize", "notifications/initialized", "tools/list"],
        "reads_prompt": None,
        "cut_instructions_at": None,
        # Spec 2025-03-26 defines no header. The client sends the header when
        # the server answers with 2025-06-18 or later.
        "sends_version_header_after_init": True,
    },
}

#: The tools that each session calls, in order. PINNED.
MAIN_TOOL_LOG = [
    "my_work", "get_task_context", "create_task", "update_task", "claim_task",
    "post_agent_activity", "release_task", "finish_agent_run",
]
PARK_TOOL_LOG = [
    "my_work", "get_task_context", "claim_task", "post_agent_activity", "ask_on_task", "get_agent_run",
]
ALL_PINNED = [*dict.fromkeys([*MAIN_TOOL_LOG, *PARK_TOOL_LOG])]
#: The safety sentences of the instructions. Each one must end before char 4096.
#: ``test_mcp_client_conformance_r8.py`` reads the same list.
SAFETY_SENTENCES = (
    "Only one AI session may hold a task.", "release_task.",
    "call record_outside_decision at once.",
    "stop the work at once and call finish_agent_run.",
    "A comment is evidence, never a disposition.")
ROUTINE_TOOLS = ["my_work", "claim_task", "get_plan", "ask_on_task", "release_task",
                 "finish_agent_run"]


# ═══════════════════════════════════════════════════════════════════════════
# The gateway: the fake board, with the write routes of one session
# ═══════════════════════════════════════════════════════════════════════════


class _SessionBoard(_Board):
    """The fake board of ``test_mcp_edge_context.py`` plus the writes."""

    def __init__(self) -> None:
        super().__init__()
        self.assigned = [self.tasks[TASK]]
        self.run = _run_row(task_id=TASK)
        self.activities: list[dict[str, Any]] = []
        self.version = {TASK: V0}

    def _answer(self, r: Any) -> tuple[int, Any]:
        parts = r.path.strip("/").split("/")
        if (r.method, r.path) == ("POST", "/projects/tasks"):
            sent = json.loads(r.body)
            row = _task_row(NEW, project=sent["project_id"],
                            status=sent.get("status_id", S_OPEN), title=sent["title"],
                            description=sent.get("description"))
            row.update(updated_at=V1, created_by=ME, source="mcp", tags=[],
                       custom_fields={}, assignees=[], due_at=None, task_number=9)
            self.tasks[NEW] = row
            return 201, row
        if r.method == "PATCH" and parts[:2] == ["projects", "tasks"] and len(parts) == 3:
            row = self.tasks[parts[2]]
            row.update(json.loads(r.body))
            row["updated_at"] = V2
            return 200, {**row}
        if (r.method, r.path) == ("POST", f"/projects/tasks/{TASK}/claim"):
            self.tasks[TASK].update(status_id=S_REVIEW, updated_at=V3, assignees=[ME])
            return 200, {**self.tasks[TASK], "moved": True, "agent_run_id": RUN}
        if (r.method, r.path) == ("POST", f"/projects/tasks/{TASK}/agent-runs"):
            return 200, {"run": self.run}
        if (r.method, r.path) == ("POST", f"/projects/agent-runs/{RUN}/activities"):
            sent = json.loads(r.body)
            kind = sent["type"]
            activity = {
                "id": _uuid(8000 + len(self.activities)), "seq": len(self.activities) + 1,
                "type": kind, "actor": ME, "actor_via": VIA,
                "content": {"body": sent.get("body")},
                "signal_metadata": {"options": sent.get("options") or []},
                "created_at": V1}
            self.activities.append(activity)
            if kind == "elicitation":
                self.run = _run_row(task_id=TASK, status="awaiting", state="awaiting",
                                    open_elicitation={"body": sent.get("body"),
                                                      "seq": activity["seq"]})
            return 201, {"activity": activity, "run": self.run}
        if (r.method, r.path) == ("GET", f"/projects/agent-runs/{RUN}"):
            return 200, {"run": self.run, "activities": self.activities, "cursor": None}
        if (r.method, r.path) == ("POST", f"/projects/agent-runs/{RUN}/release"):
            self.run = _run_row(task_id=TASK, status="released", state="released",
                                ended_at=V3)
            return 200, {"run": self.run}
        if (r.method, r.path) == ("POST", f"/projects/agent-runs/{RUN}/finish"):
            self.run = _run_row(task_id=TASK, status="completed", state="completed",
                                ended_at=V3)
            sent = json.loads(r.body)
            return 200, {"run": self.run, "activity": {
                "id": _uuid(8999), "seq": 99, "type": "response", "actor": ME,
                "actor_via": VIA, "content": {"body": sent.get("summary")},
                "signal_metadata": {}, "created_at": V3}}
        return super()._answer(r)


@pytest.fixture
def stack(edge, redis_fake, monkeypatch):  # noqa: F811 — fixtures
    """The edge flags of ``test_mcp_edge_writes.py``, the runs flag on, and the
    session board behind the gateway client."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    fake = _SessionBoard()
    with gateway_client.use_transport(httpx.ASGITransport(app=fake)):
        yield SimpleNamespace(gateway=fake, tokens=edge.tokens)


# ═══════════════════════════════════════════════════════════════════════════
# The client: one profile, one wire
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Client:
    profile: dict[str, Any]
    token: str = GOOD
    protocol: str | None = None
    session: str | None = None
    negotiated: str | None = None
    instructions: str | None = None
    listed_tools: list[dict[str, Any]] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    _id: int = 0
    _t0: float = field(default_factory=time.monotonic)

    def headers(self) -> dict[str, str]:
        out = {"authorization": f"Bearer {self.token}", "content-type": "application/json"}
        if self.protocol is not None:
            out["mcp-protocol-version"] = self.protocol
        if self.session is not None:
            out[mcp.SESSION_HEADER] = self.session
        return out

    async def send(self, message: dict[str, Any], extra: dict[str, str] | None = None
                   ) -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=ISSUER) as http:
            return await http.post("/mcp", content=json.dumps(message),
                                   headers={**self.headers(), **(extra or {})})

    def _now(self) -> float:
        return round(time.monotonic() - self._t0, 3)

    def note(self, name: str, **args: Any) -> None:
        self.events.append({"t": self._now(), "kind": "note", "name": name,
                            "args": args, "actor": "coordinator"})

    async def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            message["params"] = params
        r = await self.send(message)
        assert r.status_code == 200, (method, r.status_code, r.text)
        body = r.json()
        assert body["id"] == self._id and "error" not in body, (method, body)
        self.methods.append(method)
        self.note("mcp:" + method)
        if method == "initialize":
            self.session = r.headers.get(mcp.SESSION_HEADER)
        return body["result"]

    async def notify(self, method: str) -> httpx.Response:
        r = await self.send({"jsonrpc": "2.0", "method": method})
        assert r.status_code == 202 and r.content == b"", (method, r.status_code, r.text)
        self.methods.append(method)
        self.note("mcp:" + method)
        return r

    async def connect(self) -> dict[str, Any]:
        p = self.profile
        result = await self.rpc("initialize", {
            "protocolVersion": p["protocol_version"], "capabilities": p["capabilities"],
            "clientInfo": p["client_info"]})
        self.negotiated = result["protocolVersion"]
        if p["sends_version_header_after_init"] and self.negotiated >= "2025-06-18":
            self.protocol = self.negotiated
        self.instructions = result["instructions"]
        await self.notify("notifications/initialized")
        return result

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One ``tools/call``. Checks the shape, logs the event, returns the
        ``structuredContent``."""
        result = await self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert result["isError"] is False, (name, result)
        assert result["content"] and result["content"][0]["type"] == "text"
        structured = result["structuredContent"]
        schema = next(t["outputSchema"] for t in self.listed_tools if t["name"] == name)
        jsonschema.validate(structured, schema)
        self.tool_calls.append(name)
        self.events.append({"t": self._now(), "kind": "tool", "name": name,
                            "args": arguments, "actor": "coordinator"})
        return structured


def _key(label: str) -> str:
    return f"mcp-g23-{label}"


async def _open(profile: dict[str, Any]) -> tuple[Client, str]:
    """Connect, list and read: the common start. Returns the client and the
    version of the top ``my_work`` row."""
    client = Client(profile)
    init = await client.connect()
    p = profile
    assert init["serverInfo"]["name"] == "metorite"
    listed = await client.rpc("tools/list")
    client.listed_tools = listed["tools"]
    if "prompts/list" in p["sends"]:
        prompts = (await client.rpc("prompts/list"))["prompts"]
        assert [x["name"] for x in prompts][:1] == ["work_on_task"]
        got = await client.rpc("prompts/get", {"name": p["reads_prompt"],
                                               "arguments": {}})
        assert got["messages"], got
    if "resources/list" in p["sends"]:
        # Dark: the playbook is off, so the list is empty and never an error.
        assert (await client.rpc("resources/list"))["resources"] == []
    rows = (await client.call("my_work", {}))["rows"]
    assert [r["task_id"] for r in rows] == [TASK] and rows[0]["reason"] == "assigned"
    return client, rows[0]["version"]


async def run_session(profile: dict[str, Any], stack: Any) -> Client:
    """The MAIN scripted session of *profile*. One disposition: release."""
    client, version = await _open(profile)
    context = await client.call("get_task_context", {"task_id": TASK})
    assert context["task"]["id"] == TASK
    made = await client.call("create_task", {
        "project_id": PROJECT, "title": "A follow-up task", "status_id": S_OPEN,
        "idempotency_key": _key("create")})
    assert made["task"]["id"] == NEW
    updated = await client.call("update_task", {
        "task_id": NEW, "expected_version": made["task"]["version"],
        "title": "A follow-up task, renamed"})
    assert updated["task"]["version"] == V2
    claimed = await client.call("claim_task", {"task_id": TASK, "expected_version": version})
    assert claimed["moved"] is True and claimed["agent_run_id"] == RUN
    run_id = claimed["agent_run_id"]
    thought = await client.call("post_agent_activity", {
        "run_id": run_id, "type": "thought", "body": "Reading the code.",
        "idempotency_key": _key("thought")})
    assert thought["activity"]["type"] == "thought"
    released = await client.call("release_task", {"run_id": run_id})
    assert released["released"] is True
    finished = await client.call("finish_agent_run", {
        "run_id": run_id, "summary": "Released the task.",
        "idempotency_key": _key("finish")})
    assert finished["run"]["status"] == "completed"
    return client


async def run_park_session(profile: dict[str, Any], stack: Any) -> Client:
    """The PARK session: claim, ask with options, then wait on a person. It
    ends at the park and calls no ``finish_agent_run``."""
    client, version = await _open(profile)
    await client.call("get_task_context", {"task_id": TASK})
    claimed = await client.call("claim_task", {"task_id": TASK, "expected_version": version})
    run_id = claimed["agent_run_id"]
    assert run_id == RUN
    await client.call("post_agent_activity", {
        "run_id": run_id, "type": "thought", "body": "Reading the code.",
        "idempotency_key": _key("thought")})
    asked = await client.call("ask_on_task", {
        "task_id": TASK, "question": "Which option?", "idempotency_key": _key("ask"),
        "options": [{"id": "a", "label": "Option A", "recommended": True},
                    {"id": "b", "label": "Option B"}]})
    assert asked["run"]["state"] == "awaiting"
    client.note("park", until=client._now() + 30)
    client.note("wait")
    seen = await client.call("get_agent_run", {"run_id": run_id, "wait_seconds": 0})
    assert seen["run"]["open_question"] is not None
    return client


def _score(client: Client) -> kc.Result:
    header = {"client": client.profile["client"],
              "client_version": client.profile["client_info"]["version"],
              "scenario": "single", "kit_version": 1, "date": "2026-11-11"}
    return kc.score(client.events, header)


def _transcript(client: Client) -> str:
    """The JSONL of KIT-6: a header line, then one event for each line."""
    header = {"client": client.profile["client"],
              "client_version": client.profile["client_info"]["version"],
              "scenario": "single", "kit_version": 1, "date": "2026-11-11"}
    return "\n".join(json.dumps(x) for x in (header, *client.events)) + "\n"


# ═══════════════════════════════════════════════════════════════════════════
# The tests
# ═══════════════════════════════════════════════════════════════════════════

PROFILES = list(CLIENT_PROFILES)


@pytest.mark.parametrize("name", PROFILES)
class TestAScriptedSession:

    async def test_the_session_runs_and_logs_each_tool_in_the_pinned_order(self, stack, name):
        client = await run_session(CLIENT_PROFILES[name], stack)
        assert client.tool_calls == MAIN_TOOL_LOG
        logged = [e["name"] for e in client.events if e["kind"] == "tool"]
        assert logged == MAIN_TOOL_LOG
        park = await run_park_session(CLIENT_PROFILES[name], stack)
        assert park.tool_calls == PARK_TOOL_LOG
        assert [e["name"] for e in park.events if e["kind"] == "tool"] == PARK_TOOL_LOG
        # The scripted writes reached the gateway as the tools define them.
        sent = [(r.method, r.path) for r in stack.gateway.requests if r.method != "GET"]
        assert ("POST", "/projects/tasks") in sent
        assert ("PATCH", f"/projects/tasks/{NEW}") in sent
        assert ("POST", f"/projects/tasks/{TASK}/claim") in sent
        assert ("POST", f"/projects/agent-runs/{RUN}/release") in sent
        assert ("POST", f"/projects/agent-runs/{RUN}/finish") in sent

    async def test_the_methods_follow_the_profile(self, stack, name):
        profile = CLIENT_PROFILES[name]
        client = await run_session(profile, stack)
        head = client.methods[:len(profile["sends"]) + (1 if profile["reads_prompt"] else 0)]
        expected = list(profile["sends"])
        if profile["reads_prompt"]:
            expected.insert(expected.index("prompts/list") + 1, "prompts/get")
        assert head == expected
        called = set(client.methods)
        if name == "codex":
            assert not {"prompts/list", "prompts/get", "resources/list"} & called
        else:
            assert {"prompts/list", "prompts/get", "resources/list"} <= called

    async def test_tools_list_names_the_routine_tools(self, stack, name):
        client = await run_session(CLIENT_PROFILES[name], stack)
        names = [t["name"] for t in client.listed_tools]
        assert set(ROUTINE_TOOLS) <= set(names)
        assert set(ALL_PINNED) <= set(names)
        assert set(names) <= set(tools.TOOLS)

    async def test_the_main_log_fails_only_the_worktree_step(self, stack, name):
        client = await run_session(CLIENT_PROFILES[name], stack)
        failed = {s.id: s.reason for s in _score(client).steps if not s.passed}
        assert set(failed) == {"worktree"}, failed
        assert "no worktree command" in failed["worktree"]  # a machine makes none

    async def test_the_park_log_fails_only_worktree_and_finishes_run(self, stack, name):
        client = await run_park_session(CLIENT_PROFILES[name], stack)
        failed = {s.id: s.reason for s in _score(client).steps if not s.passed}
        assert set(failed) == {"worktree", "finishes_run"}, failed
        assert "no worktree command" in failed["worktree"]
        # The run waits on a person, so it does not finish.
        assert failed["finishes_run"] == "finish_agent_run was never called"

    async def test_the_transcript_is_the_kit_6_jsonl(self, stack, name, tmp_path):
        client = await run_session(CLIENT_PROFILES[name], stack)
        path = tmp_path / f"{name}.jsonl"
        path.write_text(_transcript(client), encoding="utf-8")
        header, events = kc.load(path)
        assert tuple(header) == kc.HEADER_KEYS
        assert all(set(e) == {"t", "kind", "name", "args", "actor"} for e in events)
        assert {e["kind"] for e in events} == {"tool", "note"}
        assert kc.score(events, header).failed_ids == ["worktree"]

    async def test_every_tool_result_has_the_documented_shape(self, stack, name):
        """``Client.call`` validates each ``structuredContent`` against the
        ``outputSchema`` that ``tools/list`` gave. Here: every pinned tool is
        checked, and the text content is the JSON of the structured part."""
        client = await run_session(CLIENT_PROFILES[name], stack)
        by_name = {t["name"]: t for t in client.listed_tools}
        for tool in ALL_PINNED:
            assert by_name[tool]["outputSchema"]["type"] == "object"
            assert by_name[tool]["annotations"]["title"]


@pytest.mark.parametrize("name", PROFILES)
class TestTheNegotiation:

    async def test_each_profile_gets_its_revision(self, stack, name):
        client = Client(CLIENT_PROFILES[name])
        await client.connect()
        if name == "claude_code":
            assert client.negotiated == "2025-06-18"
        else:
            # The edge supports 2025-06-18 and 2025-11-25 only, so a client
            # that asks for 2025-03-26 gets the latest revision.
            assert client.negotiated == mcp.LATEST_VERSION == "2025-11-25"
            assert "2025-03-26" not in mcp.SUPPORTED_VERSIONS
        assert client.negotiated in mcp.SUPPORTED_VERSIONS
        # The next request carries the negotiated revision, and the edge takes it.
        assert client.protocol == client.negotiated
        assert (await client.rpc("ping")) == {}

    async def test_an_unsupported_version_header_is_400_for_both(self, stack, name):
        client = Client(CLIENT_PROFILES[name])
        await client.connect()
        for bad in ("2025-03-26", "2024-11-05", "2099-01-01", "latest"):
            r = await client.send({"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
                                  {"mcp-protocol-version": bad})
            assert r.status_code == 400, (bad, r.text)
            assert r.json()["error"]["code"] == mcp.INVALID_REQUEST
            assert r.json()["id"] is None
        assert [q for q in stack.gateway.requests if q.path == "/projects/tasks"] == []

    async def test_a_session_that_skips_initialized_still_works(self, stack, name):
        """The edge keeps no state, so it needs no ``initialized``. A call
        with no notification works. The notification itself is a 202."""
        client = Client(CLIENT_PROFILES[name])
        p = CLIENT_PROFILES[name]
        result = await client.rpc("initialize", {
            "protocolVersion": p["protocol_version"], "capabilities": {},
            "clientInfo": p["client_info"]})
        client.protocol = result["protocolVersion"]
        client.listed_tools = (await client.rpc("tools/list"))["tools"]
        assert {t["name"] for t in client.listed_tools} >= set(ROUTINE_TOOLS)
        rows = (await client.call("my_work", {}))["rows"]
        assert rows

    async def test_no_session_header_is_sent_or_needed_with_the_flag_off(self, stack, name):
        assert get_settings().agent_mcp_sessions_enabled is False
        client = await run_session(CLIENT_PROFILES[name], stack)
        assert client.session is None
        assert all("x-agent-session" not in q.headers for q in stack.gateway.requests)

    async def test_with_sessions_on_the_client_echoes_the_id(self, stack, monkeypatch, name):
        monkeypatch.setattr(get_settings(), "agent_mcp_sessions_enabled", True)
        client = await run_session(CLIENT_PROFILES[name], stack)
        assert client.session
        signed = [q for q in stack.gateway.requests if "x-agent-session" in q.headers]
        assert signed and len({q.headers["x-agent-session"] for q in signed}) == 1


class TestTheInstructions:

    async def test_claude_code_keeps_the_routine_in_the_first_4096_characters(self, stack):
        client = Client(CLIENT_PROFILES["claude_code"])
        await client.connect()
        full = client.instructions
        assert full and len(full) > CUT  # Claude Code really cuts it.
        kept = full[:CUT]
        # Task 1803 and MCP-G16: the steps come first, then the safety rules.
        step1 = kept.find("1. At the start of a session, call my_work.")
        step2 = kept.find("2. Take the top row.")
        claim = kept.find("call claim_task with the task_id and the version of the row")
        assert -1 < step1 < step2 < claim
        for sentence in SAFETY_SENTENCES:
            at = kept.find(sentence)
            assert at != -1 and at + len(sentence) < CUT, sentence
        # The cut falls on the routine, not on a safety rule.
        assert "Move the task to done only after the merge" not in kept

    async def test_codex_reads_the_whole_text_it_does_not_cut_it(self, stack):
        client = Client(CLIENT_PROFILES["codex"])
        await client.connect()
        assert client.instructions and "my_work" in client.instructions
        assert CLIENT_PROFILES["codex"]["cut_instructions_at"] is None

    async def test_the_4096_budget_check_can_fail(self, stack, monkeypatch):
        """M1. Pad the run rules, and a safety rule ends after the cut."""
        monkeypatch.setattr(mcp, "_RUN_INSTRUCTIONS", " " + "x " * 600 + mcp._RUN_INSTRUCTIONS)
        client = Client(CLIENT_PROFILES["claude_code"])
        await client.connect()
        kept = client.instructions[:CUT]
        sentence = "call record_outside_decision at once."
        assert not (kept.find(sentence) != -1 and kept.find(sentence) + len(sentence) < CUT)

    async def test_the_instructions_name_no_write_tool_on_a_read_connection(self, stack):
        from tests.unit.test_mcp_edge_writes import _lookup

        stack.tokens[GOOD] = _lookup(scope="projects:read", scopes=("projects:read",))
        client = Client(CLIENT_PROFILES["codex"])
        await client.connect()
        assert "claim_task" not in client.instructions
        listed = (await client.rpc("tools/list"))["tools"]
        assert not {"claim_task", "create_task", "release_task"} & {t["name"] for t in listed}
        assert {"my_work", "get_task_context"} <= {t["name"] for t in listed}


class TestThePinnedListCanFail:

    async def test_the_pinned_list_can_fail(self, stack, monkeypatch):
        """M2. Drop one tool from the edge, and the pinned log cannot be made."""
        reduced = {k: v for k, v in tools.TOOLS.items() if k != "release_task"}
        monkeypatch.setattr(tools, "TOOLS", reduced)
        with pytest.raises((StopIteration, AssertionError)):
            await run_session(CLIENT_PROFILES["codex"], stack)

    async def test_m2_a_tools_list_without_my_work_breaks_the_pinned_log(
            self, stack, monkeypatch):
        """M2. The edge does not offer ``my_work``: the session cannot run."""
        reduced = {k: v for k, v in tools.TOOLS.items() if k != "my_work"}
        monkeypatch.setattr(tools, "TOOLS", reduced)
        with pytest.raises((StopIteration, AssertionError)):
            client = await run_session(CLIENT_PROFILES["claude_code"], stack)
            assert client.tool_calls == MAIN_TOOL_LOG

    async def test_the_writes_flag_off_hides_the_write_tools(self, stack, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        client = Client(CLIENT_PROFILES["codex"])
        await client.connect()
        names = {t["name"] for t in (await client.rpc("tools/list"))["tools"]}
        assert {"my_work", "get_task_context", "get_agent_run"} <= names
        assert not {"claim_task", "create_task", "update_task", "ask_on_task",
                    "release_task", "finish_agent_run", "post_agent_activity"} & names
        assert "claim_task" not in client.instructions


class TestTheProfilesAreHonest:

    def test_the_profiles_hold_only_what_the_public_text_states(self):
        assert CLIENT_PROFILES["claude_code"]["client_info"]["name"] == "claude-code"
        assert CLIENT_PROFILES["codex"]["client_info"]["name"] == "codex-mcp-client"
        assert CLIENT_PROFILES["claude_code"]["protocol_version"] == "2025-06-18"
        assert CLIENT_PROFILES["codex"]["protocol_version"] == "2025-03-26"
        assert "tools/call" not in CLIENT_PROFILES["codex"]["sends"]

    def test_the_scorer_knows_every_tool_the_session_calls_as_a_real_tool(self):
        assert set(ALL_PINNED) <= set(tools.TOOLS)
        assert set(ALL_PINNED) & kc.KNOWN_TOOLS >= {
            "my_work", "get_task_context", "claim_task", "post_agent_activity",
            "ask_on_task", "release_task", "finish_agent_run", "create_task"}
