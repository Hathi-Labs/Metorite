"""The Router accepts every request the agent framework really sends (R7).

Live bug, 2026-10-05. Every native agent run (projects-assistant first) failed
its first model call with a 422 from the Customer Console Router::

    executor.native_maf_stream_fallback ... OpenAIChatCompletionClient service
    failed to complete the prompt: Error code: 422 - {'detail': [{'type':
    'extra_forbidden', 'loc': ['body', ...

``CompletionRequest`` is ``extra="forbid"`` on purpose, and MAF 1.19's client
sends two keys it did not name. It adds ``stream_options`` to every stream, and
it renames ``max_tokens`` to ``max_completion_tokens``. The run then fell back
to Tier 2, which shows the member no tool steps.

Three fences, and each one fails CI on a different future.

1. **The real bodies.** The REAL ``OpenAIChatCompletionClient`` builds each
   request over a mock HTTP transport. The body then goes through the gateway's
   own ``_router_outbound`` and into ``CompletionRequest``. A stub client
   would agree with whatever it was handed, which is how this shipped.
2. **The key table.** Every key the client can put on the wire is listed below,
   with the decision taken for it. A MAF or OpenAI SDK upgrade that adds a key
   fails here until somebody decides it. ``extra="forbid"`` stays the default
   for anything unknown.
3. **The fallback log.** A Tier 1 fallback names the refused field in full.
   The old line cut the error at 200 characters, just before the field name.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("agent_framework", reason="agent_framework not installed")
openai = pytest.importorskip("openai")

from agent_framework import FunctionTool, Message  # noqa: E402
from agent_framework.openai import OpenAIChatCompletionClient  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402

from tests.unit._native_maf_harness import _a_tenant  # noqa: E402, F401 — a fixture

# ── The decision table ───────────────────────────────────────────────────────

#: Accepted, and forwarded to the provider (clamped where the key multiplies
#: what one request costs us).
ACCEPT_AND_FORWARD = frozenset({
    "messages", "model", "stream", "temperature", "top_p", "n", "stop",
    "max_tokens", "presence_penalty", "frequency_penalty", "logit_bias",
    "user", "response_format", "seed", "tools", "tool_choice",
    "parallel_tool_calls", "reasoning_effort",
})

#: Accepted, and NOT forwarded as sent. The Router sets or folds the value.
ACCEPT_AND_TRANSFORM = {
    "stream_options": (
        "MAF sends it on every stream. The stream path always asks the "
        "provider for include_usage=true, because the meter reads the usage "
        "frame. A caller cannot switch it off. A buffered call ignores it."
    ),
    "max_completion_tokens": (
        "MAF's spelling of max_tokens. Folded into max_tokens and clamped "
        "the same way. Two different values in one request are refused."
    ),
}

#: Refused with a 422, on purpose. The client CAN send each one, because MAF
#: passes any option to ``AsyncCompletions.create()``. No Metorite agent sets
#: one today.
KEEP_FORBIDDEN = {
    "store": (
        "keeps the completion on OUR vendor account. Retention of a "
        "customer's content is not a caller's choice."
    ),
    "metadata": "means something only with store, and only at OpenAI.",
    "prediction": (
        "OpenAI-only. Another vendor in a chain answers 400, and a 400 is "
        "terminal, so it would stop failover."
    ),
    "modalities": "asks for audio output, and the meter has no audio unit.",
    "audio": "audio output, and the meter has no audio unit.",
    "service_tier": (
        "picks a price tier on OUR account. The rate card meters the "
        "standard rate, so priority would be under-charged."
    ),
    "logprobs": (
        "vendor support is uneven in a mixed chain, and a vendor 400 is "
        "terminal. Open it when a caller needs it."
    ),
    "top_logprobs": "same reason as logprobs.",
    "verbosity": "GPT-5 only. Same reason as logprobs.",
    "prompt_cache_options": (
        "cache policy is the platform's (acb_llm.prompt_cache), not a "
        "caller's."
    ),
    "prompt_cache_key": "same reason as prompt_cache_options.",
    "prompt_cache_retention": (
        "extended retention keeps the prompt on OUR vendor account."
    ),
    "safety_identifier": (
        "attributes abuse to an end user on OUR account. The Router stamps "
        "identity from the credential."
    ),
    "web_search_options": (
        "a hosted search that the vendor bills per call. The meter has no "
        "unit for it."
    ),
    "functions": "deprecated by tools. MAF never sends it.",
    "function_call": "deprecated by tool_choice. MAF never sends it.",
    "moderation": "a vendor feature with no decided cost or behaviour.",
}

#: MAF option keys that never reach the wire under their own name.
#: ``instructions`` becomes a system message, ``conversation_id`` is dropped,
#: and ``_chat_completion_client.OPTION_TRANSLATIONS`` renames the other two.
NEVER_ON_THE_WIRE = frozenset({
    "instructions", "conversation_id", "allow_multiple_tool_calls", "max_tokens",
})

#: Keys the client adds itself, outside the options TypedDict.
CLIENT_INJECTED = frozenset({
    "messages", "model", "stream", "stream_options", "tools",
    "web_search_options",
})

#: Keys OUR executor puts in a native agent's ``default_options``.
EXECUTOR_SET = frozenset({"reasoning_effort"})

DECIDED = ACCEPT_AND_FORWARD | set(ACCEPT_AND_TRANSFORM) | set(KEEP_FORBIDDEN)


def _model():
    from customer_console.main import CompletionRequest

    return CompletionRequest


def _maf_wire_keys() -> set[str]:
    """Every key MAF's chat-completions client can put in a request body."""
    from agent_framework_openai._chat_completion_client import (
        OPTION_TRANSLATIONS,
        OpenAIChatCompletionOptions,
    )

    options = (
        OpenAIChatCompletionOptions.__required_keys__
        | OpenAIChatCompletionOptions.__optional_keys__
    )
    on_wire = {OPTION_TRANSLATIONS.get(k, k) for k in options}
    on_wire -= {"instructions", "conversation_id"}
    return on_wire | CLIENT_INJECTED | EXECUTOR_SET


def _openai_create_keys() -> set[str]:
    """Every key ``AsyncCompletions.create()`` takes. MAF forwards any option
    there unfiltered, so this is the true upper bound of what it can send."""
    from openai.types.chat import completion_create_params as params

    return set(params.CompletionCreateParamsBase.__annotations__) | {"stream"}


# ── 2. The key table is complete and true ────────────────────────────────────


class TestTheKeyTable:
    def test_every_key_maf_can_send_has_a_decision(self) -> None:
        undecided = _maf_wire_keys() - DECIDED
        assert not undecided, (
            f"MAF can now send {sorted(undecided)}. Decide each one in this "
            "file's table (forward, transform or forbid) and in "
            "customer_console.main.CompletionRequest."
        )

    def test_every_key_the_openai_client_takes_has_a_decision(self) -> None:
        undecided = _openai_create_keys() - DECIDED
        assert not undecided, (
            f"AsyncCompletions.create() now takes {sorted(undecided)}. MAF "
            "forwards any option there, so decide each one in this table."
        )

    def test_the_three_lists_do_not_overlap(self) -> None:
        assert not ACCEPT_AND_FORWARD & set(KEEP_FORBIDDEN)
        assert not ACCEPT_AND_FORWARD & set(ACCEPT_AND_TRANSFORM)
        assert not set(ACCEPT_AND_TRANSFORM) & set(KEEP_FORBIDDEN)

    def test_never_on_the_wire_is_still_true(self) -> None:
        """The four renamed or dropped keys are what this file assumes MAF
        does. Read it from MAF, so a change there reopens the table."""
        from agent_framework_openai._chat_completion_client import OPTION_TRANSLATIONS

        assert OPTION_TRANSLATIONS == {
            "allow_multiple_tool_calls": "parallel_tool_calls",
            "max_tokens": "max_completion_tokens",
        }

    def test_every_accepted_key_is_a_field(self) -> None:
        fields = set(_model().model_fields)
        missing = (ACCEPT_AND_FORWARD | set(ACCEPT_AND_TRANSFORM)) - fields
        assert not missing, missing

    def test_every_forbidden_key_is_not_a_field(self) -> None:
        leaked = set(KEEP_FORBIDDEN) & set(_model().model_fields)
        assert not leaked, f"forbidden on purpose, yet a field: {leaked}"

    @pytest.mark.parametrize("key", sorted(KEEP_FORBIDDEN))
    def test_every_forbidden_key_is_refused(self, key: str) -> None:
        with pytest.raises(ValidationError) as err:
            _model().model_validate({
                "messages": [{"role": "user", "content": "x"}], key: True,
            })
        assert any(e["type"] == "extra_forbidden" for e in err.value.errors())

    def test_an_unknown_key_is_still_refused(self) -> None:
        """``extra="forbid"`` is the guard, and this change did not open it."""
        with pytest.raises(ValidationError):
            _model().model_validate({
                "messages": [{"role": "user", "content": "x"}], "api_base": "x",
            })


# ── 1. The bodies the REAL client builds ─────────────────────────────────────


class _Answer(BaseModel):
    text: str


def _echo(text: str) -> str:
    """Echo the text.

    Args:
        text: The text to echo.
    """
    return text


_TOOL = FunctionTool(func=_echo, name="echo", description="Echo the text.")

#: Option sets a native agent run can hand the client.
_CASES: dict[str, dict[str, Any]] = {
    "plain": {},
    "max_tokens": {"max_tokens": 500},
    "tools": {
        "tools": [_TOOL], "tool_choice": "auto", "allow_multiple_tool_calls": True,
    },
    "reasoning": {"reasoning_effort": "high"},
    "everything": {
        "max_tokens": 500, "tools": [_TOOL], "tool_choice": "auto",
        "allow_multiple_tool_calls": True, "reasoning_effort": "medium",
        "temperature": 0.2, "top_p": 0.9, "seed": 7, "stop": ["END"],
        "frequency_penalty": 0.1, "presence_penalty": 0.1,
        "logit_bias": {"50256": -100}, "user": "m@x.io",
        "instructions": "Be brief.",
    },
    "structured": {"response_format": _Answer},
}


def _reply(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("stream"):
        chunk = {
            "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "{\"text\":\"hi\"}"},
                         "finish_reason": "stop"}],
        }
        data = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=data.encode(),
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "{\"text\":\"hi\"}"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


def _maf_body(options: dict[str, Any], *, stream: bool) -> dict[str, Any]:
    """The one request body the real client sends for *options*."""
    bodies: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _reply(request)

    client = OpenAIChatCompletionClient(
        model="tier-balanced",
        async_client=openai.AsyncOpenAI(
            base_url="http://gateway.test/v1", api_key="k",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
        ),
    )
    messages = [Message(role="user", contents=["hi"])]

    async def _run() -> None:
        if stream:
            async for _ in client.get_response(messages, stream=True, options=dict(options)):
                pass
        else:
            await client.get_response(messages, options=dict(options))

    asyncio.run(_run())
    assert len(bodies) == 1, bodies
    return bodies[0]


def _through_the_gateway(body: dict[str, Any]) -> dict[str, Any]:
    """What the Console receives: the gateway's ONE outbound builder."""
    v1_compat = pytest.importorskip("gateway.routes.v1_compat")
    return v1_compat._router_outbound(body)


@pytest.mark.parametrize("stream", [True, False], ids=["stream", "buffered"])
@pytest.mark.parametrize("case", sorted(_CASES))
def test_the_router_accepts_what_maf_sends(case: str, stream: bool) -> None:
    body = _maf_body(_CASES[case], stream=stream)

    # The preconditions that make this test mean something: the two keys
    # that broke production ARE in the body the real client built.
    if stream:
        assert body["stream_options"] == {"include_usage": True}
    if "max_tokens" in _CASES[case]:
        assert body["max_completion_tokens"] == 500
        assert "max_tokens" not in body

    req = _model().model_validate(_through_the_gateway(body))

    if "max_tokens" in _CASES[case]:
        assert req.max_tokens == 500, "the ceiling was not folded into max_tokens"
    if case in ("tools", "everything"):
        assert req.tools and req.parallel_tool_calls is True
    if case == "reasoning":
        assert req.reasoning_effort == "high"


@pytest.mark.usefixtures("_a_tenant")
@pytest.mark.parametrize("think_mode", ["auto", "thinking", "max"])
def test_the_router_accepts_a_real_projects_assistant_run(
    think_mode: str, monkeypatch,
) -> None:
    """The agent that failed in production, through the REAL executor.

    Only the HTTP transport under the agent's client is a script. Every body
    the run sends must pass the Router's validation.
    """
    from tests.unit._native_maf_harness import ScriptedModel, drive_native, text_turn

    model = ScriptedModel([text_turn("done")])
    events, _ = drive_native(
        "projects-assistant", "apps/agents/agent-projects", monkeypatch, model,
        think_mode=think_mode,
    )
    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert model.bodies, "no request reached the model"
    for body in model.bodies:
        assert body.get("stream") is True, "Tier 1 streams; this run did not"
        _model().model_validate(_through_the_gateway(body))


def test_every_key_in_a_real_body_has_a_decision() -> None:
    """The bodies above, read the other way: no key the client really put on
    the wire is missing from the table."""
    seen: set[str] = set()
    for options in _CASES.values():
        for stream in (True, False):
            seen |= set(_maf_body(options, stream=stream))
    assert seen <= DECIDED, seen - DECIDED
    assert seen <= _maf_wire_keys(), seen - _maf_wire_keys()


class TestTheCeiling:
    def _req(self, **extra: Any):
        return _model().model_validate({
            "messages": [{"role": "user", "content": "x"}], **extra,
        })

    def test_max_completion_tokens_becomes_max_tokens(self) -> None:
        assert self._req(max_completion_tokens=900).max_tokens == 900

    def test_the_same_value_twice_passes(self) -> None:
        assert self._req(max_tokens=900, max_completion_tokens=900).max_tokens == 900

    def test_two_different_values_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="disagree"):
            self._req(max_tokens=900, max_completion_tokens=100)

    def test_zero_is_refused_under_either_name(self) -> None:
        with pytest.raises(ValidationError):
            self._req(max_completion_tokens=0)

    def test_max_tokens_alone_is_unchanged(self) -> None:
        req = self._req(max_tokens=64)
        assert req.max_tokens == 64 and req.max_completion_tokens is None


class TestStreamOptions:
    def _req(self, value: Any):
        return _model().model_validate({
            "messages": [{"role": "user", "content": "x"}],
            "stream": True, "stream_options": value,
        })

    @pytest.mark.parametrize("value", [
        {"include_usage": True}, {"include_usage": False},
        {"include_obfuscation": False}, {},
    ])
    def test_the_openai_shapes_are_accepted(self, value: dict) -> None:
        self._req(value)

    def test_an_unknown_stream_option_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            self._req({"include_usage": True, "chunk_size": 9})

    def test_it_is_never_in_the_forwardable_set(self) -> None:
        """The stream path sets its own value. Forwarding the caller's would
        let ``include_usage: false`` switch the meter off."""
        from customer_console.main import _FORWARDABLE

        assert "stream_options" not in _FORWARDABLE
        assert "max_completion_tokens" not in _FORWARDABLE


# ── 3. The fallback log names the refused field ──────────────────────────────


def _router_422(request: httpx.Request) -> httpx.Response:
    """The Router's own model behind a real FastAPI body parameter, so the 422
    is the one FastAPI writes, ``loc: ["body", <field>]`` included."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def _validate(req):  # annotated below, as an object
        return {}

    # Set as an object, not a string: this module's `from __future__` import
    # would turn `req: model` into a name FastAPI cannot resolve.
    _validate.__annotations__ = {"req": _model()}
    app = FastAPI()
    app.post("/v1/chat/completions")(_validate)

    answer = TestClient(app).post("/v1/chat/completions", content=request.content,
                                  headers={"content-type": "application/json"})
    if answer.status_code == 422:
        return httpx.Response(422, json=answer.json())
    return _reply(request)


def _maf_failure(handler, options: dict[str, Any]) -> BaseException:
    client = OpenAIChatCompletionClient(
        model="tier-balanced",
        async_client=openai.AsyncOpenAI(
            base_url="http://gateway.test/v1", api_key="k", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )

    async def _run() -> None:
        async for _ in client.get_response(
            [Message(role="user", contents=["hi"])], stream=True, options=options,
        ):
            pass

    with pytest.raises(Exception) as err:
        asyncio.run(_run())
    return err.value


class TestTheFallbackLog:
    def test_the_refused_field_is_read_from_the_real_exception(self) -> None:
        from orchestrator.executor import _rejected_request_fields

        # Refuse a key the Router still forbids, so the 422 is real.
        exc = _maf_failure(_router_422, {"store": True})
        assert _rejected_request_fields(exc) == ["store"]

    def test_the_production_shape_is_read_in_full(self) -> None:
        """The exact 2026-10-05 body. The old 200-character cut lost the
        field name, and ``rejected_fields`` must not."""
        from orchestrator.executor import _rejected_request_fields

        def _old_router(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": [{
                "type": "extra_forbidden", "loc": ["body", "stream_options"],
                "msg": "Extra inputs are not permitted",
                "input": {"include_usage": True},
            }]})

        exc = _maf_failure(_old_router, {})
        assert _rejected_request_fields(exc) == ["stream_options"]
        assert "stream_options" not in str(exc)[:200], (
            "precondition: the old cut really did hide the field"
        )

    def test_another_failure_names_no_field(self) -> None:
        from orchestrator.executor import _rejected_request_fields

        assert _rejected_request_fields(RuntimeError("timeout")) == []

        def _down(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json={"error": {"message": "down"}})

        assert _rejected_request_fields(_maf_failure(_down, {})) == []

    def test_the_fallback_line_is_built_by_the_redactor(self) -> None:
        """The log site, read from the AST: ``error`` comes from the redactor,
        never from a raw ``str(_nexc)``."""
        import ast
        import inspect

        from orchestrator import executor

        tree = ast.parse(inspect.getsource(executor))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "executor.native_maf_stream_fallback"
        ]
        assert len(calls) == 1, "the fallback log line moved or doubled"
        keywords = {kw.arg: kw.value for kw in calls[0].keywords}
        assert "rejected_fields" in keywords and "error_type" in keywords
        assert ast.unparse(keywords["error"]) == "_redacted_fallback_error(_nexc)"
        assert executor._FALLBACK_ERROR_CHARS <= 200


# ── 4. The fallback log never carries member content ─────────────────────────

#: A member message. It must reach the model request and never the log.
_MEMBER_TEXT = "my salary is 98765 and my PIN is 4321"


def _body_level_422(request: httpx.Request) -> httpx.Response:
    """A 422 shaped the way FastAPI writes one. The first entry refuses a
    field. The second is a BODY-level error, and FastAPI puts the whole
    request, the member's messages included, into its ``input``."""
    body = json.loads(request.content)
    return httpx.Response(422, json={"detail": [
        {"type": "extra_forbidden", "loc": ["body", "stream_options"],
         "msg": "Extra inputs are not permitted", "input": {"include_usage": True}},
        {"type": "value_error", "loc": ["body"], "msg": "Value error, x",
         "input": body, "ctx": {"error": str(body)}},
    ]})


class TestTheFallbackLogIsRedacted:
    @pytest.mark.usefixtures("_a_tenant")
    def test_a_real_run_logs_the_field_and_not_the_message(self, monkeypatch) -> None:
        """Through the REAL executor: the 422 carries the member message,
        the fallback line names the field, and no log line holds the text."""
        import structlog

        from tests.unit._native_maf_harness import drive_native

        class _Model:
            def __init__(self) -> None:
                self.bodies: list[dict[str, Any]] = []

            def __call__(self, request: httpx.Request) -> httpx.Response:
                self.bodies.append(json.loads(request.content))
                return _body_level_422(request)

        model = _Model()
        with structlog.testing.capture_logs() as logs:
            drive_native(
                "projects-assistant", "apps/agents/agent-projects", monkeypatch,
                model, message=_MEMBER_TEXT,
            )

        # Precondition: the member text really was in the refused request.
        assert _MEMBER_TEXT in json.dumps(model.bodies[0])
        fallback = [e for e in logs if e["event"] == "executor.native_maf_stream_fallback"]
        assert len(fallback) == 1, [e["event"] for e in logs]
        assert fallback[0]["rejected_fields"] == ["stream_options"]
        assert fallback[0]["error_type"] == "ChatClientException"
        assert len(fallback[0]["error"]) <= 200
        for entry in logs:
            assert _MEMBER_TEXT not in json.dumps(entry, default=str), entry["event"]

    def test_a_parsed_422_keeps_type_loc_and_msg_only(self) -> None:
        from orchestrator.executor import _redacted_fallback_error

        exc = _maf_failure(_body_level_422, {})
        text = _redacted_fallback_error(exc)
        assert "422" in text and "extra_forbidden" in text
        assert '"input"' not in text and '"ctx"' not in text
        # The refused request lives only in `input`, so none of it remains.
        assert "messages" not in text and "tier-balanced" not in text
        # Removed by the ALLOWLIST, not by the text cut: the second entry's
        # message survives, which a cut at the first `input` would lose.
        assert "Value error, x" in text
        assert not text.endswith("[input removed]")

    def test_a_text_error_is_cut_where_input_starts(self) -> None:
        """No parsed body, so the redactor sees only text. It cuts at the
        ``input`` key, because the end of a value is not safe to find."""
        from orchestrator.executor import _redacted_fallback_error

        raw = RuntimeError(
            "Error code: 422 - {'detail': [{'type': 'value_error', 'loc': "
            f"['body'], 'input': {{'messages': [{{'content': '{_MEMBER_TEXT}'}}]}}}}]}}"
        )
        text = _redacted_fallback_error(raw)
        assert _MEMBER_TEXT not in text
        assert text.endswith("[input removed]")
        assert "value_error" in text

    def test_an_ordinary_error_is_kept_and_cut_to_200(self) -> None:
        from orchestrator.executor import _redacted_fallback_error

        assert _redacted_fallback_error(RuntimeError("timeout")) == "timeout"
        assert len(_redacted_fallback_error(RuntimeError("x" * 5000))) == 200
