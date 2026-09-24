"""The product's own AI goes through OUR Router, so it bills. H-171.

Spec: `launch_surface.md` §4.1 (*"AI usage is metered separately in
credits"*) · **D57.7** (a routed call that fails, FAILS) · CP-6.

🔴 **Measured 2026-09-23: `acb_llm` held ZERO references to the Console.**
`acompletion_with_fallback` imported litellm and called it directly, so the
apps runtime, the email automation, the assistants and the agents — 29 files —
spent our vendor account and billed nobody. Flipping `ROUTER_SERVING_ENABLED`
did not change it, because the Router was not on that path.

⚠️ **Observed is not billed.** `_emit_usage` already logged the vendor cost and
published an activity with D1's four-tuple. What never happened was a
`usage_event` row or a credit drawn down.

⚠️ Hermetic. The subject is which client gets called and what it is handed, so
a real Postgres adds nothing. The BILLING half is proved by the Console's own
R8 suites, which this path now reaches.
"""
from __future__ import annotations

from typing import Any

import pytest

from acb_common.settings import get_settings

pytest.importorskip("litellm")

TIER = "tier-fast"
MESSAGES = [{"role": "user", "content": "hello"}]
OK_BODY = {
    "id": "cmpl-1",
    "model": "deepseek/deepseek-v4-flash",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"},
         "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
}


class RouterSpy:
    """Records what the Router was handed, and answers what the test set."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.calls: list[tuple[dict, dict]] = []
        self._status = status
        self._body = OK_BODY if body is None else body

    async def __call__(self, payload, **kw):
        self.calls.append((payload, kw))
        return self._status, self._body


@pytest.fixture
def routed(monkeypatch):
    """A box that bills: the flag on, the Console reachable."""
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.test")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _spy_on(monkeypatch, spy):
    from acb_auth import console_resolve

    monkeypatch.setattr(console_resolve, "chat_completion_on_console", spy)


class TestRoutingIsOn:
    def test_BOTH_the_flag_and_the_wiring_are_required(self, monkeypatch):
        """⚠️ Neither implies the other. A box with the flag on and no address
        must not silently serve unbilled."""
        from acb_llm.routed import routing_is_on

        for flag, url, expected in (
            ("1", "https://console.test", True),
            ("0", "https://console.test", False),
            ("1", "", False),
        ):
            monkeypatch.setenv("ROUTER_SERVING_ENABLED", flag)
            monkeypatch.setenv("CUSTOMER_CONSOLE_URL", url)
            monkeypatch.setenv(
                "CUSTOMER_CONSOLE_ORG_KEY", "cc_live_fixture_notarealsecret"
            )
            get_settings.cache_clear()
            assert routing_is_on() is expected, (flag, url)
        get_settings.cache_clear()


class TestTheInProductPathNowBills:
    async def test_it_reaches_the_ROUTER_and_not_litellm(self, monkeypatch, routed):
        """🔴 The whole ticket. This path used to call litellm directly."""
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        # If routing leaks through to litellm the test fails loudly rather
        # than quietly spending a real vendor key.
        import litellm

        async def _boom(*a, **k):
            raise AssertionError("the call reached litellm, so it billed nobody")

        monkeypatch.setattr(litellm, "acompletion", _boom)

        resp, used = await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64
        )
        assert spy.calls, "the Router was never called"
        assert resp.choices[0].message.content == "hi"
        assert used == "deepseek/deepseek-v4-flash"

    async def test_it_sends_the_TIER_and_never_a_resolved_model(
        self, monkeypatch, routed
    ):
        """⚠️ The Console picks from `tier_binding`, which an operator ordered
        and which fails over. Sending a concrete model would take that away."""
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await acompletion_with_fallback(
            model=TIER, fallback_model="tier-powerful", messages=MESSAGES,
            max_tokens=64,
        )
        payload, _ = spy.calls[0]
        # ⚠️ The field is called `model` and it carries the TIER ALIAS. A
        # first version sent `{"tier": ...}`, which `CompletionRequest` would
        # have refused outright — it is `extra="forbid"`.
        assert payload["model"] == TIER
        assert not payload["model"].startswith("deepseek/"), (
            "a resolved model id would take away the operator's ranked chain")

    async def test_the_response_is_the_TYPE_the_29_call_sites_already_hold(
        self, monkeypatch, routed
    ):
        """⚠️ They read `resp.choices[0].message.content`. Handing them a dict
        turns a working feature into an AttributeError far from this change."""
        from litellm import ModelResponse

        from acb_llm.context import acompletion_with_fallback

        _spy_on(monkeypatch, RouterSpy())
        resp, _ = await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64
        )
        assert isinstance(resp, ModelResponse)
        assert resp.usage.prompt_tokens == 3

    async def test_attribution_comes_from_the_RUN_CONTEXT_for_free(
        self, monkeypatch, routed
    ):
        """⚠️ Per-member and per-app billing with no call-site change, which is
        the property `_emit_usage`'s docstring already records: *an in-run
        caller passes none of them*."""
        from acb_common._log import bind_run_context

        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        bind_run_context(
            user="dana@example.com", agent="planner", source="email",
            run_id="run-9",
        )
        try:
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64
            )
        finally:
            import structlog

            structlog.contextvars.clear_contextvars()
        _, kw = spy.calls[0]
        assert kw["member"] == "dana@example.com"
        assert kw["agent"] == "planner"
        assert kw["module_slug"] == "email"
        assert kw["run_id"] == "run-9"

    async def test_an_explicit_source_BEATS_the_run_context(
        self, monkeypatch, routed
    ):
        """Custom Apps all run through one module, so only the caller knows
        which app it is — the reason `source` exists at all."""
        from acb_common._log import bind_run_context

        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        bind_run_context(source="apps")
        try:
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64,
                source="app:invoices",
            )
        finally:
            import structlog

            structlog.contextvars.clear_contextvars()
        _, kw = spy.calls[0]
        assert kw["module_slug"] == "app:invoices"


class TestD57_7_ARoutedCallThatFailsFAILS:
    @pytest.mark.parametrize(
        ("status", "word"),
        [(402, "credits"), (400, "tier"), (403, "breaker")],
    )
    async def test_a_refusal_RAISES_and_never_falls_back_to_litellm(
        self, monkeypatch, routed, status, word
    ):
        """🔴 The one moment the unbilled path must not come back.

        A quiet fall back to local litellm would serve the call for free
        exactly when the Console is refusing to be paid for it.
        """
        import litellm

        from acb_llm.context import acompletion_with_fallback
        from acb_llm.routed import RoutedRefusal

        _spy_on(monkeypatch, RouterSpy(status, {"detail": f"out of {word}"}))

        async def _boom(*a, **k):
            raise AssertionError("it fell back to litellm and served unbilled")

        monkeypatch.setattr(litellm, "acompletion", _boom)

        with pytest.raises(RoutedRefusal) as caught:
            await acompletion_with_fallback(
                model=TIER, fallback_model="tier-powerful",
                messages=MESSAGES, max_tokens=64,
            )
        assert caught.value.status == status
        assert word in caught.value.detail

    async def test_an_OUTAGE_also_never_falls_back(self, monkeypatch, routed):
        """⚠️ D57.7 covers no-answer too, not only a refusal."""
        import litellm

        from acb_auth.console_resolve import ConsoleRouterUnavailable
        from acb_llm.context import acompletion_with_fallback

        async def _down(*a, **k):
            raise ConsoleRouterUnavailable("unreachable")

        _spy_on(monkeypatch, _down)

        async def _boom(*a, **k):
            raise AssertionError("it fell back to litellm during an outage")

        monkeypatch.setattr(litellm, "acompletion", _boom)

        with pytest.raises(ConsoleRouterUnavailable):
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64
            )


class TestTheUNROUTEDPathIsUntouched:
    async def test_with_the_flag_OFF_it_still_uses_litellm(self, monkeypatch):
        """⚠️ Ship dark. Every box that has not turned billing on behaves
        exactly as it did before this change."""
        monkeypatch.setenv("ROUTER_SERVING_ENABLED", "0")
        get_settings.cache_clear()

        import litellm

        from acb_llm.context import acompletion_with_fallback

        seen: list[str] = []

        async def _fake(*, model, **k):
            seen.append(model)
            from litellm import ModelResponse

            return ModelResponse(**OK_BODY)

        monkeypatch.setattr(litellm, "acompletion", _fake)
        try:
            await acompletion_with_fallback(
                model=TIER, messages=MESSAGES, max_tokens=64
            )
        finally:
            get_settings.cache_clear()
        assert seen, "the direct path stopped working"


# ── The fence the stub could not be ─────────────────────────────────────────


class TestThePayloadSurvivesTheREALModel:
    """🔴 A stub agrees with whatever it is handed, and that hid a 422.

    The first version of this routing sent `{"tier": ...}` and spread the
    caller's `**extra`. `CompletionRequest` is `extra="forbid"` and its field
    is called `model`, so EVERY routed call would have been refused — and the
    suite above stayed green, because it stubbed the client.

    R8 says verify against the real thing. The real thing here is not a
    database, it is the pydantic model the Console actually validates with.
    """

    @staticmethod
    def _model():
        pytest.importorskip("fastapi")
        from customer_console.main import CompletionRequest

        return CompletionRequest

    async def test_the_payload_VALIDATES(self, monkeypatch, routed):
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64, temperature=0.3
        )
        payload, _ = spy.calls[0]
        # Raises ValidationError if a field is misnamed or unknown.
        parsed = self._model()(**payload)
        assert parsed.model == TIER
        assert parsed.max_tokens == 64

    async def test_litellm_TRANSPORT_options_never_reach_the_Console(
        self, monkeypatch, routed
    ):
        """⚠️ `timeout`, `cache` and `prompt_cache_key` are litellm's, and a
        `forbid` model refuses the whole call rather than ignoring them."""
        from acb_llm.context import acompletion_with_fallback

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await acompletion_with_fallback(
            model=TIER, messages=MESSAGES, max_tokens=64,
            timeout=30, cache={"no-cache": False}, prompt_cache_key="k",
        )
        payload, _ = spy.calls[0]
        for leaked in ("timeout", "cache", "prompt_cache_key"):
            assert leaked not in payload, f"{leaked} would 422 the whole call"
        self._model()(**payload)

    def test_every_FORWARDABLE_key_is_a_real_field(self):
        """⚠️ The allowlist is named, not derived. This is what keeps the two
        from drifting apart without coupling the package to the service."""
        from acb_llm.routed import _FORWARDABLE

        fields = set(self._model().model_fields)
        assert set(_FORWARDABLE) <= fields, (
            f"not fields of CompletionRequest: {set(_FORWARDABLE) - fields}")

    def test_TOOL_CALLING_is_routable_and_forwarded(self):
        """🔴 This clause was INVERTED, and the inversion is the point.

        It first asserted `tools` was absent, because I read the model with a
        line-bounded grep and concluded tool-calling could not be routed. The
        field exists. So does `tool_choice`. Had that stood, every
        tool-calling agent would have stayed unbilled on a wrong premise
        written into a test.
        """
        from acb_llm.routed import _FORWARDABLE

        fields = self._model().model_fields
        for name in ("tools", "tool_choice", "parallel_tool_calls"):
            assert name in fields, name
            assert name in _FORWARDABLE, name

    def test_STREAM_is_never_forwarded_on_the_non_streaming_path(self):
        """⚠️ Asking for frames nothing reads. The streaming client is a
        separate function with a separate contract."""
        from acb_llm.routed import _FORWARDABLE

        assert "stream" not in _FORWARDABLE


# ── The AGENT paths — the largest unbilled surface of all ───────────────────


class TestTheAgentPathsBillToo:
    """🔴 `acompletion_with_fallback`'s own comment says *"client.complete
    covers agent runs"*, so agent runs were the biggest remaining hole. An
    agent loop makes many calls, so it cost most while billing least."""

    def _tier(self):
        from acb_llm.client import LLMTier

        return next(iter(LLMTier))

    async def test_complete_reaches_the_Router_and_still_returns_TEXT(
        self, monkeypatch, routed
    ):
        """⚠️ `complete` answers a string. Returning the response object under
        the flag would break every agent at once, and only when billing is on
        — the hardest kind of break to attribute."""
        from acb_llm import client

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)

        async def _boom(*a, **k):
            raise AssertionError("an agent call reached litellm, so it billed nobody")

        # The bound name, for the reason the flag-off test below records.
        monkeypatch.setattr(client, "acompletion", _boom)

        out = await client.complete(
            tier=self._tier(), messages=MESSAGES, max_tokens=64
        )
        assert out == "hi"
        assert isinstance(out, str)
        assert spy.calls, "the agent path never reached the Router"

    async def test_complete_with_tools_routes_and_FORWARDS_the_tools(
        self, monkeypatch, routed
    ):
        """🔴 The clause that corrected a wrong belief.

        I first left this on the direct path, believing `CompletionRequest`
        had no `tools` field. It has. Tool-calling turns are the longest-lived
        completions in the tree, so that belief would have left the most
        expensive calls free.
        """
        from acb_llm import client

        tool_body = dict(OK_BODY)
        tool_body["choices"] = [
            {
                "index": 0,
                "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1", "type": "function",
                            "function": {"name": "search", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
        spy = RouterSpy(200, tool_body)
        _spy_on(monkeypatch, spy)

        async def _boom(*a, **k):
            raise AssertionError("a tool-calling turn reached litellm, unbilled")

        monkeypatch.setattr(client, "acompletion", _boom)

        tools = [{"type": "function", "function": {"name": "search"}}]
        out = await client.complete_with_tools(
            tier=self._tier(), messages=MESSAGES, tools=tools, max_tokens=64
        )
        payload, _ = spy.calls[0]
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"
        # ⚠️ The contract is a JSON-serialisable dict — the caller feeds it
        # straight back into `messages` for the next turn.
        assert isinstance(out, dict)
        assert out["tool_calls"][0]["function"]["name"] == "search"

    async def test_the_agent_payloads_VALIDATE_against_the_real_model(
        self, monkeypatch, routed
    ):
        """The same R8-shaped fence as the completion path: a stub agrees with
        whatever it is handed, so the pydantic model is the judge."""
        pytest.importorskip("fastapi")
        from customer_console.main import CompletionRequest

        from acb_llm import client

        spy = RouterSpy()
        _spy_on(monkeypatch, spy)
        await client.complete(tier=self._tier(), messages=MESSAGES, max_tokens=64)
        CompletionRequest(**spy.calls[0][0])

        spy2 = RouterSpy(200, OK_BODY)
        _spy_on(monkeypatch, spy2)
        await client.complete_with_tools(
            tier=self._tier(), messages=MESSAGES,
            tools=[{"type": "function", "function": {"name": "x"}}],
            max_tokens=64,
        )
        CompletionRequest(**spy2.calls[0][0])

    async def test_with_the_flag_OFF_the_agent_paths_are_untouched(
        self, monkeypatch
    ):
        """⚠️ Ship dark. A box that has not turned billing on behaves exactly
        as before."""
        monkeypatch.setenv("ROUTER_SERVING_ENABLED", "0")
        get_settings.cache_clear()

        from acb_llm import client

        seen: list[str] = []

        async def _fake(*, model, **k):
            seen.append(model)
            return {"choices": [{"message": {"content": "direct"}}]}

        # 🔴 **`acb_llm.client.acompletion`, NOT `litellm.acompletion`.**
        # `client.py` binds the name at import time (`from litellm import
        # acompletion`), so patching the module attribute leaves the bound
        # reference untouched. The first version of this test did that and
        # made a REAL DeepSeek call, which failed on authentication — a test
        # that spends money is worse than one that fails.
        monkeypatch.setattr(client, "acompletion", _fake)
        try:
            await client.complete(
                tier=self._tier(), messages=MESSAGES, max_tokens=64
            )
        finally:
            get_settings.cache_clear()
        assert seen, "the direct agent path stopped working"
