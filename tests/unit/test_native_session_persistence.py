"""WS43-F20: the structured history path (WS-43t1) and the session store (WS-43t2).

Spec: ``project-docs/specs/maf_coding_engine.md`` §15.9 and the WS-43t1 and
WS-43t2 slices. HANDOFF H-216 and H-215. Sections 1 to 6 are WS-43t1's.
Sections 7 to 12 are WS-43t2's: the store, the dedup, the staleness and room
rules, the bounds, the odd runs, and the two-turn probe of §15.9.7. Each
section's own header names its fence and its mutations.

The structured branch of the native run input never ran. It built
``Message(role=..., content=...)``, MAF 1.19 refused the keyword, and the
``except`` fell back to the string prompt. A naive repair would also drop the
member's memory, because only the string carried it. Each case here pins one
rule of the repair:

1. **Flag OFF changes nothing.** ``MAF_NATIVE_SESSIONS`` is off by default,
   and the run input is the string ``origin/main`` built, byte for byte. The
   golden strings below were captured from ``origin/main`` ``4de997c9``.
2. **The real ``agent_framework.Message`` class.** No stub. A stub accepts
   ``content=`` as gladly as ``contents=``, which is how H-216 hid.
3. **The context never enters the message list.** ``memory_context``,
   ``system_context`` and the persona (it rides in ``system_context``) reach
   the model through a per-run MAF context provider, as instructions. A MAF
   history provider stores input messages, and stores none of this.
4. **Per run, never on a shared agent.** Two runs of ONE agent object at once,
   with different memory. Neither sees the other's, and the shared agent's
   provider list never changes.
5. **Memory reaches the model after the repair**, through the real executor
   and the real OpenAI chat client (only the HTTP transport is scripted).
6. **The cap of §15.9.6.** The earlier turns stay inside the smaller of the
   window budget and ``_HISTORY_MAX_TOKENS``.

Each fence was proved by a mutation (the PR lists them): ``content=`` back,
``memory_context`` dropped from the structured branch, the provider attached
to the shared agent, and the cap removed.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
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

agent_framework = pytest.importorskip("agent_framework", reason="agent_framework not installed")
executor = pytest.importorskip("orchestrator.executor", reason="orchestrator not installed")

_APIS = ("apis-config", "apps/agents/agent-apis-config")

_MEMORY = "MEMORY-ZETA: the member prefers short answers."
_PERSONA = "PERSONA-OMEGA: you are the inbox persona for ops@x.io."

#: Payloads and the run input ``origin/main`` ``4de997c9`` built for each, with
#: ``integrations = {"gmail": ...}`` and no run model bound. Captured, never
#: derived from the current code: a value derived from the code under test
#: agrees with whatever that code now says.
_INTEGRATIONS: dict[str, Any] = {"gmail": {"token": "x"}}
_GOLDEN: dict[str, tuple[dict[str, Any], str]] = {
    "full": (
        {
            "message": "Apply itm-1 and itm-2",
            "messages": [
                {"role": "user", "content": "Process my inbox."},
                {"role": "assistant", "content": "Apply itm-1 and itm-2?"},
            ],
            "memory_context": "The member prefers short answers.",
            "system_context": "You are the inbox persona. The open mailbox is ops@x.io.",
            "integration_warnings": {"zoho": "missing"},
        },
        "Connected integrations: gmail.\n"
        "Missing integrations (not yet configured): zoho. If the user task requires"
        " one of these, ask them to provide the credential. When they do, output:"
        " <<<SETUP:service_name:ENV_VAR_NAME=value>>>\n"
        "## Memory from past conversations\nThe member prefers short answers.\n"
        "## Current context\nYou are the inbox persona. The open mailbox is ops@x.io.\n"
        "Conversation history:\nUser: Process my inbox.\n"
        "Assistant: Apply itm-1 and itm-2?\n"
        "Apply itm-1 and itm-2",
    ),
    "no_history": (
        {"message": "hi", "memory_context": "Likes tea."},
        "Connected integrations: gmail.\n## Memory from past conversations\nLikes tea.\nhi",
    ),
    "webhook": (
        {"event": "deal.updated", "deal_id": 7},
        'Connected integrations: gmail.\nEvent payload: {"event": "deal.updated", "deal_id": 7}',
    ),
}

_HISTORY = [
    {"role": "user", "content": "I want to connect Google"},
    {"role": "assistant", "content": "Google has Sheets, Drive and Calendar. Which ones?"},
]


@pytest.fixture(autouse=True)
def _no_fit_stats_leak():
    """These cases call the assembler on the test thread, and it sets the
    ``last_fit_stats`` ContextVar there. A later suite that runs the executor
    with no history reads that value and shows a false "Long conversation"
    notice (``test_no_pressure_notice_when_history_fits``). Restore it."""
    from acb_llm.context import last_fit_stats

    token = last_fit_stats.set(None)
    yield
    last_fit_stats.reset(token)


@pytest.fixture
def _flag_on(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "maf_native_sessions", True)


def _compose(payload: dict[str, Any], *, native: bool = True) -> tuple[Any, Any]:
    return executor._compose_maf_run(
        "probe", "run-1", payload, _INTEGRATIONS, native=native,
    )


def _texts(messages: list[Any]) -> list[str]:
    return [m.text for m in messages]


def _system_and_rest(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    messages = body["messages"]
    assert messages[0]["role"] == "system", messages
    return str(messages[0]["content"]), messages[1:]


# ── 1. Flag OFF changes nothing ──────────────────────────────────────────────


def test_the_flag_is_off_by_default() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["maf_native_sessions"].default is False
    assert executor._native_sessions_enabled() is False


@pytest.fixture
def _flag_off(monkeypatch):
    """Pin ``MAF_NATIVE_SESSIONS`` OFF, so an environment that sets it cannot
    turn a flag-off case into a flag-on one (WS-43t1 verifier note)."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "maf_native_sessions", False)


@pytest.mark.usefixtures("_flag_off")
@pytest.mark.parametrize("case", sorted(_GOLDEN))
def test_flag_off_the_run_input_is_todays_string_byte_for_byte(case: str) -> None:
    payload, golden = _GOLDEN[case]
    assert executor._compose_maf_run_input("probe", "run-1", payload, _INTEGRATIONS) == golden
    run_input, provider = _compose(payload)
    assert run_input == golden
    assert provider is None


def test_flag_off_the_run_calls_the_agent_itself() -> None:
    agent = object()
    assert executor._agent_for_run(agent, None) is agent


@pytest.mark.usefixtures("_a_tenant", "_flag_off")
def test_flag_off_the_wire_carries_the_string_prompt(monkeypatch) -> None:
    """Through the real executor: one user message holds today's prompt, with
    the memory and the history inside it as text."""
    model = ScriptedModel([text_turn("ok")])
    events, _ = drive_native(
        *_APIS, monkeypatch, model, message="Sheets", thread_id="thread-f20-off",
        history=_HISTORY,
        extra_payload={"memory_context": _MEMORY, "system_context": _PERSONA},
    )
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED"
    system, rest = _system_and_rest(model.bodies[0])
    assert [m["role"] for m in rest] == ["user"]
    prompt = rest[0]["content"]
    assert _MEMORY in prompt and _PERSONA in prompt
    assert "Conversation history:\nUser: I want to connect Google" in prompt
    assert prompt.endswith("\nSheets")
    assert _MEMORY not in system


# ── 2. The real Message class ────────────────────────────────────────────────


@pytest.mark.usefixtures("_flag_on")
def test_the_structured_branch_builds_real_maf_messages() -> None:
    payload, _golden = _GOLDEN["full"]
    run_input, provider = _compose(payload)

    assert isinstance(run_input, list), f"fell back to the string: {run_input!r}"
    assert all(type(m) is agent_framework.Message for m in run_input)
    assert [m.role for m in run_input] == ["user", "assistant", "user"]
    assert _texts(run_input) == [
        "Process my inbox.", "Apply itm-1 and itm-2?", "Apply itm-1 and itm-2",
    ]
    assert isinstance(provider, agent_framework.ContextProvider)


@pytest.mark.usefixtures("_flag_on")
def test_a_copilot_agent_never_takes_the_structured_branch() -> None:
    payload, golden = _GOLDEN["full"]
    assert _compose(payload, native=False) == (golden, None)


@pytest.mark.usefixtures("_flag_on")
@pytest.mark.parametrize("case", ["no_history", "webhook"])
def test_flag_on_with_no_history_keeps_the_string(case: str) -> None:
    payload, golden = _GOLDEN[case]
    assert _compose(payload) == (golden, None)


@pytest.mark.usefixtures("_flag_on")
def test_flag_on_with_history_and_no_current_turn_keeps_the_string() -> None:
    """No current turn means an event. The string serialises the payload, and
    a message list would drop it."""
    payload = {"messages": list(_HISTORY), "event": "deal.updated"}
    run_input, provider = _compose(payload)
    assert provider is None
    assert isinstance(run_input, str)
    assert run_input == executor._compose_maf_run_input("probe", "run-1", payload, _INTEGRATIONS)
    assert '"event": "deal.updated"' in run_input


@pytest.mark.usefixtures("_flag_on")
def test_the_structured_branch_reads_the_history_loader_once() -> None:
    """The route's loader is a database read. The structured branch runs the
    assembler once, so the loader runs once."""
    calls: list[int] = []

    def _loader() -> list[dict[str, str]]:
        calls.append(1)
        return list(_HISTORY)

    run_input, _provider = _compose({"message": "Sheets", "_history_loader": _loader})
    assert calls == [1]
    assert [m.role for m in run_input] == ["user", "assistant", "user"]
    assert run_input[-1].text == "Sheets"


# ── 3. The context never enters the message list ─────────────────────────────


@pytest.mark.usefixtures("_flag_on")
def test_the_context_travels_by_the_provider_never_as_a_message() -> None:
    payload, _golden = _GOLDEN["full"]
    run_input, provider = _compose(payload)

    for text in _texts(run_input):
        assert "Memory from past conversations" not in text
        assert "inbox persona" not in text
        assert "Connected integrations" not in text
    from acb_llm.prompt_cache import CACHE_BREAK

    assert provider.text.startswith(CACHE_BREAK + "\n")
    assert "## Memory from past conversations\nThe member prefers short answers." in provider.text
    assert "## Current context\nYou are the inbox persona." in provider.text
    assert "Connected integrations: gmail." in provider.text

    # The provider adds instructions, and no context messages.
    context = agent_framework.SessionContext(input_messages=list(run_input))
    asyncio.run(provider.before_run(
        agent=None, session=agent_framework.AgentSession(), context=context, state={},
    ))
    assert context.instructions == [provider.text]
    assert context.context_messages == {}


def _scripted_agent(model: ScriptedModel, **agent_kwargs: Any) -> Any:
    import openai
    from agent_framework.openai import OpenAIChatCompletionClient

    client = OpenAIChatCompletionClient(
        model="tier-balanced",
        async_client=openai.AsyncOpenAI(
            base_url="http://127.0.0.1:9/v1", api_key="x",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(model)),
        ),
    )
    return agent_framework.Agent(
        client=client, instructions="STABLE-INSTRUCTIONS", name="probe", **agent_kwargs,
    )


@pytest.mark.usefixtures("_flag_on")
def test_a_maf_history_provider_stores_none_of_the_context() -> None:
    """WS-43t2 will store the session. Even a history provider that stores
    the context messages of other providers holds none of the memory, while
    the model still received it."""
    from agent_framework import AgentSession, InMemoryHistoryProvider

    model = ScriptedModel([text_turn("done")])
    agent = _scripted_agent(
        model, context_providers=[InMemoryHistoryProvider(store_context_messages=True)],
    )
    payload, _golden = _GOLDEN["full"]
    run_input, provider = _compose(payload)
    session = AgentSession()

    async def _drain() -> None:
        # The scripted model streams, as the gateway does on Tier 1.
        stream = executor._agent_for_run(agent, provider).run(
            run_input, stream=True, session=session,
        )
        async for _update in stream:
            pass
        await stream.get_final_response()

    asyncio.run(_drain())

    system, _rest = _system_and_rest(model.bodies[0])
    assert system.startswith("STABLE-INSTRUCTIONS\n")
    assert "The member prefers short answers." in system
    stored = json.dumps(session.to_dict())
    assert "Process my inbox." in stored, "the turns were not stored at all"
    assert "The member prefers short answers." not in stored
    assert "inbox persona" not in stored
    assert "Connected integrations" not in stored


# ── 4. Per run, never on a shared agent ──────────────────────────────────────


@pytest.mark.usefixtures("_flag_on")
def test_the_per_run_view_leaves_the_shared_agent_unchanged() -> None:
    agent = _scripted_agent(ScriptedModel([text_turn("x")]))
    before = list(agent.context_providers)
    payload, _golden = _GOLDEN["full"]
    _run_input, provider = _compose(payload)

    view = executor._agent_for_run(agent, provider)
    assert view is not agent
    assert view.context_providers == [*before, provider]
    assert view.context_providers is not agent.context_providers
    assert agent.context_providers == before
    # The view sends what the agent sends: one client, one set of options.
    assert view.client is agent.client
    assert view.default_options is agent.default_options


@pytest.mark.usefixtures("_a_tenant", "_flag_on")
def test_two_runs_of_one_agent_at_once_never_share_memory(monkeypatch) -> None:
    """One agent object serves two runs at the same moment, through the real
    ``run_agent_stream``. The model holds both requests until both arrive, so
    both runs are inside ``agent.run`` together. Each request carries only its
    own memory, and the shared agent never holds a run's provider."""
    from orchestrator._native_run_context import RunContextProvider

    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    name, rel = _APIS
    config = json.loads((REPO_ROOT / rel / "config.json").read_text(encoding="utf-8"))
    shared = load_agent_module(rel).build_agents()[0]
    providers_before = list(shared.context_providers)
    bodies: list[dict[str, Any]] = []
    held_by_shared: list[list[Any]] = []
    both_in = asyncio.Event()  # binds to the run's loop at the first wait

    async def _hold_until_both(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content or b"{}"))
        held_by_shared.append(list(shared.context_providers))
        if len(bodies) >= 2:
            both_in.set()
        await asyncio.wait_for(both_in.wait(), timeout=30)
        chunks = [
            {"role": "assistant", "content": "ok"},
            {"finish_reason": "stop"},
        ]
        data = "".join(
            "data: " + json.dumps({
                "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "p",
                "choices": [{
                    "index": 0,
                    "delta": {k: v for k, v in c.items() if k != "finish_reason"},
                    "finish_reason": c.get("finish_reason"),
                }],
            }) + "\n\n"
            for c in chunks
        ) + "data: [DONE]\n\n"
        return httpx.Response(
            200, content=data.encode(), headers={"content-type": "text/event-stream"},
        )

    oc = shared.client.client
    oc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(_hold_until_both), event_hooks=oc._client.event_hooks,
    )

    class _Loaded:
        agent_dir = REPO_ROOT / rel
        agent_name = name

        def __init__(self) -> None:
            self.config = config

        def build_agents(self) -> list[Any]:
            return [shared]  # the SAME object for both runs

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])

    async def _one(member: str) -> list[str]:
        payload = {
            "message": f"question from {member}",
            "messages": list(_HISTORY),
            "memory_context": f"MEMORY-OF-{member}",
        }
        return [
            line async for line in executor.run_agent_stream(
                name, payload, run_id=f"run-{member}", thread_id=f"thread-{member}",
            )
        ]

    async def _both() -> list[list[str]]:
        return list(await asyncio.gather(_one("alice"), _one("bob")))

    for frames in asyncio.run(_both()):
        events = parse_frames(frames)
        assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
        assert [e.get("type") for e in events][-1] == "RUN_FINISHED"

    assert len(bodies) == 2
    by_member: dict[str, str] = {}
    for body in bodies:
        system, rest = _system_and_rest(body)
        member = "alice" if "question from alice" in json.dumps(rest) else "bob"
        other = "bob" if member == "alice" else "alice"
        assert f"MEMORY-OF-{member}" in system
        assert f"MEMORY-OF-{other}" not in system, "one run saw the other's memory"
        assert "MEMORY-OF-" not in json.dumps(rest), "memory entered the message list"
        by_member[member] = system
    assert set(by_member) == {"alice", "bob"}
    for held in held_by_shared:
        assert not [p for p in held if isinstance(p, RunContextProvider)], (
            "a run's provider was attached to the shared agent"
        )
    assert shared.context_providers == providers_before


# ── 5. Memory reaches the model after the repair ─────────────────────────────


@pytest.mark.usefixtures("_a_tenant", "_flag_on")
def test_memory_reaches_the_model_after_the_repair(monkeypatch) -> None:
    """The real executor, the real factory agent, the real OpenAI chat client.
    The earlier turns arrive as turns, and the memory and the persona arrive
    in the system message after the agent's own instructions."""
    from acb_llm.prompt_cache import CACHE_BREAK

    model = ScriptedModel([text_turn("ok")])
    events, built = drive_native(
        *_APIS, monkeypatch, model, message="Sheets", thread_id="thread-f20-on",
        history=_HISTORY,
        extra_payload={"memory_context": _MEMORY, "system_context": _PERSONA},
    )
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED"
    system, rest = _system_and_rest(model.bodies[0])

    assert [(m["role"], m["content"]) for m in rest] == [
        ("user", _HISTORY[0]["content"]),
        ("assistant", _HISTORY[1]["content"]),
        ("user", "Sheets"),
    ]
    assert f"## Memory from past conversations\n{_MEMORY}" in system
    assert f"## Current context\n{_PERSONA}" in system
    # The agent's instructions stay a stable prefix. The run's context sits
    # after the prompt-cache sentinel.
    instructions = built[0].default_options["instructions"]
    assert system.startswith(instructions)
    assert system.index(CACHE_BREAK) < system.index(_MEMORY)
    assert built[0].context_providers == []


# ── 6. The cap of §15.9.6 ────────────────────────────────────────────────────


def _long_history(turns: int, chars: int) -> list[dict[str, str]]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i} " + "x" * chars}
        for i in range(turns)
    ]


@pytest.mark.usefixtures("_flag_on")
def test_the_cap_binds_when_the_window_does_not(monkeypatch) -> None:
    """No run model, so the window fit does nothing. ``_HISTORY_MAX_TOKENS``
    alone must hold the earlier turns."""
    from acb_llm import count_message_tokens

    monkeypatch.setattr(executor, "_HISTORY_MAX_TOKENS", 1_000)
    payload = {"message": "now", "messages": _long_history(12, 1_000), "memory_context": "m"}
    run_input, _provider = _compose(payload)

    assert isinstance(run_input, list)
    *earlier, current = run_input
    assert current.role == "user" and current.text == "now"
    turns = [{"role": m.role, "content": m.text} for m in earlier]
    assert 0 < len(turns) < 12, "nothing was dropped"
    assert count_message_tokens(turns, "") <= 1_000
    assert turns[-1]["content"].startswith("turn-11 "), "the newest turn was dropped"


@pytest.mark.usefixtures("_flag_on")
def test_one_giant_earlier_turn_is_trimmed_not_dropped(monkeypatch) -> None:
    from acb_llm import count_message_tokens

    monkeypatch.setattr(executor, "_HISTORY_MAX_TOKENS", 1_000)
    payload = {"message": "yes", "messages": _long_history(1, 40_000)}
    run_input, _provider = _compose(payload)

    assert [m.role for m in run_input] == ["user", "user"]
    earlier = [{"role": run_input[0].role, "content": run_input[0].text}]
    assert earlier[0]["content"].startswith("turn-0 ")
    assert count_message_tokens(earlier, "") <= 1_000


@pytest.mark.usefixtures("_flag_on")
def test_the_window_binds_when_it_is_the_smaller(monkeypatch) -> None:
    """A large cap and a small window: the window budget holds the whole
    input, the context block counted in it."""
    import acb_llm.context as llm_context
    from acb_llm import count_message_tokens

    monkeypatch.setattr(executor, "_HISTORY_MAX_TOKENS", 10**9)
    monkeypatch.setattr(executor, "_reserved_output_tokens", lambda _m: 2_000)
    monkeypatch.setattr(llm_context, "context_window_for", lambda _m: 10_000)
    budget = 10_000 - 2_000 - 512
    token = executor._active_run_model.set("probe-small-window")
    try:
        payload = {"message": "now", "messages": _long_history(40, 2_000), "memory_context": "m"}
        run_input, provider = _compose(payload)
    finally:
        executor._active_run_model.reset(token)

    assert isinstance(run_input, list)
    sent = [{"role": "system", "content": provider.text.split("\n", 1)[1]}] + [
        {"role": m.role, "content": m.text} for m in run_input
    ]
    assert count_message_tokens(sent, "probe-small-window") <= budget
    assert run_input[-1].text == "now"
    assert len(run_input) < 41


# ═════════════════════════════════════════════════════════════════════════════
# WS-43t2 — the session store (§15.9.2 to §15.9.7)
# ═════════════════════════════════════════════════════════════════════════════
#
# Sections 7 to 10 need no database. They drive the store's rules and the
# executor's wiring, with the store's two database calls replaced by
# `_FakeDb`. Sections 11 and 12 are R8: the real store, the real SQL and the
# real chat helpers, on the H3 phase-4 catalog, as the NOBYPASSRLS role
# `acb_app_h3rls` (`projects_ai_chat.md` §21.10). They skip without
# TENANT_LADDER_DATABASE_URL, and a skip is not a pass.
#
# Each rule was proved by a mutation that turned a case red. The PR lists the
# mutation, the command and the red result for each one.

store = pytest.importorskip(
    "orchestrator.native_session_store", reason="orchestrator not installed",
)

_PROBE_AGENT = "probe-native"
_PROPOSE = "Process my inbox."
_ASK = "Apply itm-1 and itm-2?"
_ORG = "org-f20-a"


def propose_items() -> str:
    """Propose the inbox items to apply. Returns their ids."""
    return json.dumps({"items": ["itm-1", "itm-2"]})


class _LogSpy:
    """Records the store's log lines, so a case can count the outcome lines."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        def _record(event: str, **fields: Any) -> None:
            self.lines.append((level, event, fields))

        return _record

    def named(self, event: str) -> list[dict[str, Any]]:
        return [f for _level, name, f in self.lines if name == event]

    def outcomes(self) -> list[str]:
        return [f["outcome"] for f in self.named("native_session.load")]


class _FakeDb:
    """The store's two database calls, in memory, for the hermetic cases.

    It holds no SQL, so it proves no SQL. The R8 cases of sections 11 and 12
    run the real statements.
    """

    def __init__(self) -> None:
        self.chats: dict[str, dict[str, Any]] = {}
        self.stored: dict[tuple[str, str, str], Any] = {}
        self.reads: list[tuple[str, str, str]] = []
        self.writes: list[dict[str, Any]] = []

    def chat(
        self, tid: str, *, owner: str = "alice@f20.test",
        participants: list[str] | None = None, rows: list[tuple[str, str]] | None = None,
    ) -> None:
        self.chats[tid] = {
            "owner": owner,
            "participants": list(participants or [owner]),
            "rows": list(rows or []),
        }

    def read_state(self, org: str, tid: str, agent: str) -> Any:
        self.reads.append((org, tid, agent))
        chat = self.chats.get(tid)
        if chat is None:
            return store.LoadState(chat_exists=False)
        return store.LoadState(
            chat_exists=True, session_user=chat["owner"],
            participants=list(chat["participants"]), rows=list(chat["rows"]),
            stored=self.stored.get((org, tid, agent)),
        )

    def write_row(
        self, org: str, tid: str, agent: str, body: str, digest: str, fingerprint: str,
    ) -> bool:
        self.writes.append({"org": org, "tid": tid, "agent": agent, "body": body})
        if tid not in self.chats:
            return False
        self.stored[(org, tid, agent)] = store.StoredRow(json.loads(body), digest, fingerprint)
        return True


@pytest.fixture(autouse=True)
def fake_db(request, monkeypatch):
    """Every case without the R8 fixture gets the in-memory store.

    So no hermetic case, and none of WS-43t1's flag-on cases, opens a
    database connection. An R8 case asks for ``graph_as_app`` and gets the
    real calls.
    """
    if "graph_as_app" in request.fixturenames:
        yield None
        return
    db = _FakeDb()
    monkeypatch.setattr(store, "read_state", db.read_state)
    monkeypatch.setattr(store, "write_row", db.write_row)
    yield db


@pytest.fixture
def store_log(monkeypatch) -> _LogSpy:
    spy = _LogSpy()
    monkeypatch.setattr(store, "_log", spy)
    return spy


def _message(role: str, text: str) -> Any:
    return agent_framework.Message(role=role, contents=[text])


def _probe_model() -> ScriptedModel:
    """Turn 1 calls ``propose_items`` and asks. Turn 2 answers the yes."""
    return ScriptedModel([
        tool_turn("propose_items", call_id="call_propose_1"),
        text_turn(_ASK),
        text_turn("Applied itm-1 and itm-2."),
    ])


def _drive_probe(
    monkeypatch, model: ScriptedModel, payload: dict[str, Any], *,
    thread_id: str | None, organization_id: str | None, run_id: str,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Run a native MAF agent with one tool through the REAL ``run_agent_stream``.

    Only the HTTP transport is scripted. Each run builds a new agent, as
    ``build_agents`` does in production, over ONE scripted model, so the
    request bodies of both turns land in ``model.bodies``.
    """
    routes_agent = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
    built: list[Any] = []

    def _build() -> list[Any]:
        agent = _scripted_agent(model, tools=[propose_items])
        built.append(agent)
        return [agent]

    class _Loaded:
        agent_dir = REPO_ROOT / _APIS[1]

        def __init__(self) -> None:
            self.agent_name = _PROBE_AGENT
            self.config: dict[str, Any] = {}

        def build_agents(self) -> list[Any]:
            return _build()

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])

    async def _collect() -> list[str]:
        return [
            line async for line in executor.run_agent_stream(
                _PROBE_AGENT, dict(payload), run_id=run_id, thread_id=thread_id,
                organization_id=organization_id,
            )
        ]

    return parse_frames(asyncio.run(_collect())), built


def _assert_turn_two_holds_turn_one(body: dict[str, Any]) -> None:
    """§15.9.7 step 4: turn 2's request holds turn 1's tool call and its result.

    And the dedup of §15.9.4: each earlier turn is there ONCE, because a
    loaded session makes the input the current turn only.
    """
    messages = body["messages"]
    calls = [
        call for m in messages if m.get("role") == "assistant"
        for call in (m.get("tool_calls") or [])
    ]
    assert [c["function"]["name"] for c in calls] == ["propose_items"], messages
    results = [m for m in messages if m.get("role") == "tool"]
    assert len(results) == 1 and results[0]["tool_call_id"] == calls[0]["id"]
    assert "itm-1" in results[0]["content"] and "itm-2" in results[0]["content"]
    assert [m["content"] for m in messages if m.get("role") == "user"] == [_PROPOSE, "yes"]
    assert [m.get("content") for m in messages if m.get("role") == "assistant"][-1] == _ASK


# ── 7. The store's rules, as pure functions ──────────────────────────────────


def test_the_load_digest_covers_the_rows_through_the_last_user_row() -> None:
    """The rows after the last user row are the answer of the run that saved
    the session. The save hashes the rows before its user turn, then that
    turn, so the two digests meet."""
    before = [("user", "hello"), ("assistant", "hi")]
    saved = store.transcript_digest([*before, ("user", _PROPOSE)])
    server = [*before, ("user", _PROPOSE), ("assistant", "an answer the fold sealed")]
    for rows in (server, [*server, ("user", "yes")]):
        prior = store.rows_before_turn(rows, "yes")
        assert store.transcript_digest(store.covered_rows(prior)) == saved
    assert store.covered_rows([("assistant", "x")]) == []
    assert store.server_rows([("user", "  a "), ("assistant", ""), ("system", "s")]) == [
        ("user", "a"),
    ]


def test_the_browser_view_check_sees_a_regenerate() -> None:
    """A regenerate drops the last turn and its prompt from the browser only.
    The server keeps both rows, so only this check can see it."""
    covered = [("user", "first"), ("assistant", "a1"), ("user", "second")]
    assert store.browser_agrees(None, covered)
    normal = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "a1!"},
              {"role": "user", "content": "second"}, {"role": "assistant", "content": "a2"}]
    assert store.browser_agrees(normal, covered)
    regenerated = normal[:2]
    assert not store.browser_agrees(regenerated, covered)
    assert not store.browser_agrees([], covered)
    # Two equal confirm turns in a row are a real pair, not a regenerate.
    yes_yes = [("user", "yes"), ("assistant", "next?"), ("user", "yes")]
    browser = [{"role": "user", "content": "yes"}, {"role": "assistant", "content": "next?"},
               {"role": "user", "content": "yes"}, {"role": "assistant", "content": "done"}]
    assert store.browser_agrees(browser, yes_yes)


def test_the_fingerprint_moves_with_the_room_and_the_instance() -> None:
    base = {"agent_name": _PROBE_AGENT, "thread_id": "t1", "instance": ""}
    solo = store.session_fingerprint(members=["alice@x.io"], **base)
    assert solo == store.session_fingerprint(members=["alice@x.io", "alice@x.io"], **base)
    assert solo != store.session_fingerprint(members=["alice@x.io", "bob@x.io"], **base)
    assert solo != store.session_fingerprint(members=["bob@x.io"], **base)
    assert solo != store.session_fingerprint(
        members=["alice@x.io"], agent_name=_PROBE_AGENT, thread_id="t1",
        instance="u:alice@x.io",
    )
    assert len(solo) == 64 and int(solo, 16) >= 0


def test_the_append_keeps_a_repeated_answer() -> None:
    """MAF's own ``save_messages`` drops a message with no id when a stored
    one has the same role and content. Two "yes" turns must both stay."""
    provider = store.SessionHistoryProvider()
    state: dict[str, Any] = {}

    async def _two() -> None:
        await provider.save_messages(None, [_message("user", "yes")], state=state)
        await provider.save_messages(None, [_message("user", "yes")], state=state)

    asyncio.run(_two())
    assert [m.text for m in state["messages"]] == ["yes", "yes"]


def test_the_compaction_is_the_one_that_15_9_6_names() -> None:
    """The source names the two strategies in order, and no model-calling
    strategy. The restore imports no pickle: ``from_dict`` uses only MAF's
    state type registry."""
    import ast
    import inspect

    src = inspect.getsource(store)
    assert "TokenBudgetComposedStrategy(" in src
    assert src.index("ToolResultCompactionStrategy(") < src.index("SlidingWindowStrategy(")
    names = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(ast.parse(src)) if isinstance(node, ast.ImportFrom)
    }
    assert "SummarizationStrategy" not in names
    assert not imported & {"pickle", "dill", "marshal", "shelve", "cloudpickle"}
    assert "AgentSession.from_dict(" in src


def test_the_compaction_holds_the_budget_and_keeps_the_newest_tool_group() -> None:
    from agent_framework import CharacterEstimatorTokenizer, Content

    messages: list[Any] = []
    for i in range(12):
        call = f"call_{i}"
        messages += [
            _message("user", f"turn {i} " + "u" * 400),
            agent_framework.Message(role="assistant", contents=[
                Content.from_function_call(call_id=call, name="propose_items", arguments="{}"),
            ]),
            agent_framework.Message(role="tool", contents=[
                Content.from_function_result(call_id=call, result="r" * 2_000 + f" itm-{i}"),
            ]),
            _message("assistant", f"answer {i}"),
        ]
    from agent_framework._compaction import annotate_token_counts

    budget = 3_000
    out = asyncio.run(store.compact_history(messages, budget))
    # Count the way the strategy counts, on the messages the save keeps.
    agent_framework.annotate_message_groups(out)
    annotate_token_counts(out, tokenizer=CharacterEstimatorTokenizer())
    assert agent_framework.included_token_count(out) <= budget
    assert len(out) < len(messages)
    results = [c for m in out for c in m.contents if c.type == "function_result"]
    assert results and results[-1].call_id == "call_11", "the newest tool group was cut"


# ── 8. The load: one outcome line, and the fallbacks ─────────────────────────


def _begin(tid: str, current: str, *, browser: Any = None, instance: str = "",
           agent: str = _PROBE_AGENT, org: str = _ORG) -> Any:
    return asyncio.run(store.begin_turn(
        organization_id=org, thread_id=tid, agent_name=agent, instance=instance,
        current_message=current, browser_history=browser, token_budget=50_000,
    ))


def _finish(turn: Any, budget: int = 50_000) -> str:
    return asyncio.run(store.finish_turn(turn, token_budget=budget))


def _answer(turn: Any, current: str, answer: str) -> None:
    """What a run appends to its session: its user turn and its answer."""
    messages = list(turn.session.state.get(store.HISTORY_SOURCE_ID, {}).get("messages", []))
    messages += [_message("user", current), _message("assistant", answer)]
    turn.session.state[store.HISTORY_SOURCE_ID] = {"messages": messages}


def test_each_load_logs_exactly_one_outcome_line(fake_db, store_log) -> None:
    tid = "thread-f20-outcomes"
    fake_db.chat(tid, rows=[("user", _PROPOSE)])
    turn = _begin(tid, _PROPOSE)
    assert turn.outcome == "no_row" and not turn.loaded
    _answer(turn, _PROPOSE, _ASK)
    assert _finish(turn) == "saved"
    fake_db.chats[tid]["rows"] = [("user", _PROPOSE), ("assistant", _ASK), ("user", "yes")]
    assert _begin(tid, "yes").outcome == "hit"
    fake_db.chats[tid]["participants"].append("bob@f20.test")
    assert _begin(tid, "yes").outcome == "fingerprint_drop"
    fake_db.chats[tid]["participants"].pop()
    fake_db.chats[tid]["rows"][0] = ("user", "Process my INBOX.")
    assert _begin(tid, "yes").outcome == "digest_drop"
    assert store_log.outcomes() == ["no_row", "hit", "fingerprint_drop", "digest_drop"]
    for line in store_log.named("native_session.load"):
        assert line["agent"] == _PROBE_AGENT and line["thread_id"] == tid


def test_a_failed_load_falls_back_and_logs_one_line(fake_db, store_log, monkeypatch) -> None:
    def _boom(*_a: Any) -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(store, "read_state", _boom)
    assert _begin("thread-f20-down", "hi") is None
    assert store_log.outcomes() == ["no_row"]
    assert store_log.named("native_session.load")[0]["reason"] == "load_failed"
    fake_db.chat("thread-f20-corrupt", rows=[("user", "hi")])
    monkeypatch.setattr(store, "read_state", fake_db.read_state)
    fp = store.session_fingerprint(
        members=["alice@f20.test"], agent_name=_PROBE_AGENT,
        thread_id="thread-f20-corrupt", instance="",
    )
    fake_db.stored[(_ORG, "thread-f20-corrupt", _PROBE_AGENT)] = store.StoredRow(
        {"type": "session", "session_id": "s", "state": {"x": {"type": "not-a-type"}}},
        store.transcript_digest([]), fp,
    )
    turn = _begin("thread-f20-corrupt", "hi")
    assert turn.outcome == "no_row" and not turn.loaded
    assert store_log.named("native_session.load")[-1]["reason"] == "restore_failed"


def test_a_thread_with_no_chat_row_runs_with_no_session(fake_db, store_log) -> None:
    assert _begin("email-chat:x:y", "hi") is None
    assert store_log.named("native_session.load")[0]["reason"] == "no_chat"
    assert fake_db.writes == []


def test_the_save_holds_the_turns_and_nothing_else(fake_db) -> None:
    """§15.9.5: a stored session holds the message history only. A provider's
    own state key is never stored, whatever it holds."""
    tid = "thread-f20-only-turns"
    fake_db.chat(tid, rows=[("user", "hi")])
    turn = _begin(tid, "hi")
    _answer(turn, "hi", "hello")
    turn.session.state["metorite-run-context"] = {"text": _MEMORY}
    assert _finish(turn) == "saved"
    body = json.loads(fake_db.writes[-1]["body"])
    assert list(body["state"]) == [store.HISTORY_SOURCE_ID]
    assert _MEMORY not in fake_db.writes[-1]["body"]


def test_the_byte_backstop_refuses_a_save(fake_db, store_log, monkeypatch) -> None:
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["maf_session_max_bytes"].default == 2 * 1024 * 1024
    monkeypatch.setattr(get_settings(), "maf_session_max_bytes", 300)
    tid = "thread-f20-too-big"
    fake_db.chat(tid, rows=[("user", "hi")])
    turn = _begin(tid, "hi")
    _answer(turn, "hi", "x" * 1_000)
    assert _finish(turn) == "too_large"
    assert fake_db.writes == []
    refused = store_log.named("native_session.save_refused")
    assert refused and refused[0]["limit"] == 300 and refused[0]["bytes"] > 300


def test_a_foreign_key_failure_on_the_save_is_chat_gone(fake_db, store_log, monkeypatch) -> None:
    from sqlalchemy.exc import IntegrityError

    def _fk(*_a: Any) -> bool:
        raise IntegrityError(
            "INSERT", {}, Exception("violates foreign key constraint maf_agent_session_thread_id_fkey"),
        )

    tid = "thread-f20-fk"
    fake_db.chat(tid, rows=[("user", "hi")])
    turn = _begin(tid, "hi")
    _answer(turn, "hi", "hello")
    monkeypatch.setattr(store, "write_row", _fk)
    assert _finish(turn) == "chat_gone"
    assert store_log.named("native_session.chat_gone")


# ── 9. The executor: the probe, the dedup and the odd runs ───────────────────


@pytest.mark.usefixtures("_a_tenant", "_flag_on")
def test_the_two_turn_probe_carries_turn_one_tool_output(monkeypatch, fake_db, store_log) -> None:
    """§15.9.7, hermetic: the real executor, the real MAF agent and client,
    the in-memory store. Turn 2's request holds turn 1's tool call and its
    result, the memory reaches the model, and no stored session holds it."""
    tid = "thread-f20-probe"
    fake_db.chat(tid)
    model = _probe_model()
    events, built = _drive_probe(
        monkeypatch, model,
        {"mode": "chat", "message": _PROPOSE, "messages": [], "memory_context": _MEMORY},
        thread_id=tid, organization_id=_ORG, run_id="run-f20-probe-1",
    )
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    assert store_log.outcomes() == ["no_row"]
    assert len(fake_db.writes) == 1
    # What the server keeps for turn 1: the browser's save and the fold.
    fake_db.chats[tid]["rows"] = [("user", _PROPOSE), ("assistant", _ASK), ("user", "yes")]
    events, built2 = _drive_probe(
        monkeypatch, model,
        {"mode": "chat", "message": "yes", "memory_context": _MEMORY, "messages": [
            {"role": "user", "content": _PROPOSE}, {"role": "assistant", "content": _ASK},
        ]},
        thread_id=tid, organization_id=_ORG, run_id="run-f20-probe-2",
    )
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    assert store_log.outcomes() == ["no_row", "hit"]
    _assert_turn_two_holds_turn_one(model.bodies[2])
    assert _MEMORY in model.bodies[2]["messages"][0]["content"]
    assert all(_MEMORY not in w["body"] for w in fake_db.writes)
    # MAF appends a history provider to the agent it runs on when the run has
    # no history provider. The run's copy carried one, so no agent holds it.
    assert [a.context_providers for a in [*built, *built2]] == [[], []]


@pytest.mark.usefixtures("_a_tenant", "_flag_on")
def test_a_dropped_session_falls_back_to_the_text_history(monkeypatch, fake_db, store_log) -> None:
    """A digest_drop runs on the browser's text history as structured turns,
    and saves a fresh session for the next turn."""
    tid = "thread-f20-drop"
    fake_db.chat(tid)
    model = _probe_model()
    _drive_probe(monkeypatch, model, {"mode": "chat", "message": _PROPOSE, "messages": []},
                 thread_id=tid, organization_id=_ORG, run_id="run-f20-drop-1")
    # Somebody edited turn 1 on the server.
    fake_db.chats[tid]["rows"] = [("user", "Process my OTHER inbox."), ("assistant", _ASK)]
    history = [{"role": "user", "content": "Process my OTHER inbox."},
               {"role": "assistant", "content": _ASK}]
    events, _ = _drive_probe(monkeypatch, model,
                             {"mode": "chat", "message": "yes", "messages": history},
                             thread_id=tid, organization_id=_ORG, run_id="run-f20-drop-2")
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED"
    assert store_log.outcomes() == ["no_row", "digest_drop"]
    body = model.bodies[2]["messages"]
    assert not [m for m in body if m.get("role") == "tool"], "a dropped session leaked"
    assert [m["content"] for m in body if m.get("role") == "user"] == [
        "Process my OTHER inbox.", "yes",
    ]
    assert len(fake_db.writes) == 2


@pytest.mark.usefixtures("_a_tenant", "_flag_on")
@pytest.mark.parametrize("case", ["no_thread", "delegated", "no_current_turn"])
def test_an_odd_run_neither_loads_nor_saves(case: str, monkeypatch, fake_db, store_log) -> None:
    """§15.9.5: no thread id, a delegated run, and an event with history but
    no current turn. None of them touches the store."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    tid = None if case == "no_thread" else "thread-f20-odd"
    fake_db.chat("thread-f20-odd")
    payload: dict[str, Any] = {"mode": "chat", "message": _PROPOSE, "messages": []}
    if case == "no_current_turn":
        payload = {"messages": [{"role": "user", "content": "x"}], "event": "deal.updated"}
    model = ScriptedModel([text_turn("ok")])
    with artifact_context_scope():
        if case == "delegated":
            bind_artifact_context(run_id="run-of-the-parent", session_id="thread-parent")
        events, _ = _drive_probe(monkeypatch, model, payload, thread_id=tid,
                                 organization_id=_ORG, run_id=f"run-f20-{case}")
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED", events
    assert fake_db.reads == [] and fake_db.writes == []
    assert store_log.outcomes() == []


def test_the_skip_reasons(monkeypatch) -> None:
    agent = _scripted_agent(ScriptedModel([text_turn("x")]))
    skip = executor._native_session_skip
    msg = {"message": "hi"}
    assert skip(agent, "a", msg, run_id="r", caller_thread_id=None, delegated=False) == "no_thread"
    assert skip(agent, "a", msg, run_id="r", caller_thread_id="a:r", delegated=False) == "no_thread"
    assert skip(agent, "a", msg, run_id="r", caller_thread_id="t", delegated=True) == "delegated"
    assert skip(agent, "a", {"messages": [1]}, run_id="r", caller_thread_id="t",
                delegated=False) == "no_current_turn"
    assert skip(agent, "a", msg, run_id="r", caller_thread_id="t", delegated=False) is None
    owned = _scripted_agent(ScriptedModel([text_turn("x")]),
                            context_providers=[agent_framework.InMemoryHistoryProvider()])
    assert skip(owned, "a", msg, run_id="r", caller_thread_id="t",
                delegated=False) == "agent_history_provider"


@pytest.mark.usefixtures("_flag_on")
def test_no_organization_means_no_store(monkeypatch, fake_db) -> None:
    """The organization is the run binding. With none, nothing loads."""
    monkeypatch.setattr(executor, "_current_run_org", lambda: None)
    agent = _scripted_agent(ScriptedModel([text_turn("x")]))
    turn = asyncio.run(executor._begin_native_session(
        agent, _PROBE_AGENT, {"message": "hi"}, {},
        run_id="r", caller_thread_id="thread-f20-no-org", delegated=False, instance="",
    ))
    assert turn is None and fake_db.reads == []


def test_the_organization_is_resolved_before_the_worker_hop(monkeypatch, fake_db) -> None:
    """``tenant_session`` is sync and takes no ambient tenant. The executor
    reads the run binding on the event loop and hands the value in, and the
    worker sees exactly that value."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "maf_native_sessions", True)
    monkeypatch.setattr(executor, "_current_run_org", lambda: "org-from-the-run-binding")
    fake_db.chat("thread-f20-org")
    agent = _scripted_agent(ScriptedModel([text_turn("x")]))
    asyncio.run(executor._begin_native_session(
        agent, _PROBE_AGENT, {"message": "hi", "organization_id": "org-from-the-payload"}, {},
        run_id="r", caller_thread_id="thread-f20-org", delegated=False, instance="",
    ))
    assert fake_db.reads == [("org-from-the-run-binding", "thread-f20-org", _PROBE_AGENT)]


@pytest.mark.usefixtures("_a_tenant", "_flag_off")
def test_flag_off_the_store_is_never_touched(monkeypatch, fake_db) -> None:
    fake_db.chat("thread-f20-flag-off")
    model = ScriptedModel([text_turn("ok")])
    events, _ = _drive_probe(monkeypatch, model, {"mode": "chat", "message": "hi"},
                             thread_id="thread-f20-flag-off", organization_id=_ORG,
                             run_id="run-f20-flag-off")
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED"
    assert fake_db.reads == [] and fake_db.writes == []


def test_tier_2_passes_native_only_for_a_maf_agent() -> None:
    """The Tier 2 call site passes ``native=not _is_copilot_sdk`` (WS-43t1
    verifier note). ``native=True`` there would give a Copilot SDK agent the
    structured input and a session."""
    import inspect

    src = inspect.getsource(executor.run_agent_stream)
    tier_1, tier_2 = src.split("_run_input, _run_ctx = _compose_maf_run(", 1)
    assert "native=True, session_turn=_session_turn," in tier_1
    assert "if not _is_copilot_sdk and hasattr(agent, \"run\"):" in tier_1
    call = tier_2.split(")", 1)[0]
    assert "native=not _is_copilot_sdk," in call
    assert "session_turn=_session_turn," in call
    assert "native=True" not in call


# ── 10. The dedup, through the composer ──────────────────────────────────────


@pytest.mark.usefixtures("_flag_on")
def test_a_loaded_session_makes_the_input_the_current_turn_only(fake_db) -> None:
    tid = "thread-f20-dedup"
    fake_db.chat(tid, rows=[("user", _PROPOSE)])
    turn = _begin(tid, _PROPOSE)
    _answer(turn, _PROPOSE, _ASK)
    _finish(turn)
    fake_db.chats[tid]["rows"] = [("user", _PROPOSE), ("assistant", _ASK)]
    history = [{"role": "user", "content": _PROPOSE}, {"role": "assistant", "content": _ASK}]
    hit = _begin(tid, "yes", browser=history)
    assert hit.loaded
    payload = {"message": "yes", "messages": history, "memory_context": "m"}
    run_input, provider = executor._compose_maf_run(
        "probe", "run-1", payload, _INTEGRATIONS, native=True, session_turn=hit,
    )
    assert [(m.role, m.text) for m in run_input] == [("user", "yes")]
    assert provider is not None and "m" in provider.text
    # With no session, the same payload carries the whole history.
    run_input, _ = executor._compose_maf_run(
        "probe", "run-1", payload, _INTEGRATIONS, native=True,
    )
    assert [m.text for m in run_input] == [_PROPOSE, _ASK, "yes"]


@pytest.mark.usefixtures("_flag_on")
def test_a_fresh_session_never_takes_the_string(fake_db) -> None:
    """A first turn with no history still gets structured input, because the
    session stores its input messages and the string carries the memory."""
    tid = "thread-f20-first"
    fake_db.chat(tid)
    turn = _begin(tid, "hi")
    run_input, provider = executor._compose_maf_run(
        "probe", "run-1", {"message": "hi", "memory_context": _MEMORY}, _INTEGRATIONS,
        native=True, session_turn=turn,
    )
    assert [(m.role, m.text) for m in run_input] == [("user", "hi")]
    assert _MEMORY in provider.text
