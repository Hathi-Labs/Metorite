"""WS43-F20, the cases WS-43t1 owns: the structured history path.

Spec: ``project-docs/specs/maf_coding_engine.md`` §15.9 and the WS-43t1 slice.
HANDOFF H-216. WS-43t2 adds the rest of WS43-F20 (the session store, the
dedup, the staleness and room rules, the two-turn probe of §15.9.7).

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


@pytest.mark.usefixtures("_a_tenant")
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
