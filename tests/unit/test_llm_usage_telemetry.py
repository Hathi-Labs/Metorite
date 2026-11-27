"""Unit tests for per-call LLM usage telemetry (HH-3).

Covers ``_usage_stats`` extraction (token + provider cache counters) and the
fact that a successful ``complete`` emits usage without affecting the result.
"""
from __future__ import annotations

import pytest
from acb_llm import client as llm_client
from acb_llm.client import LLMTier, _compute_cost, _usage_stats, complete
from acb_llm.context import _infer_app_source


def test_usage_stats_extracts_tokens_and_cache_counters():
    response = {
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 80,
            "total_tokens": 1280,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 300,
            "prompt_tokens_details": {"cached_tokens": 900},
        },
    }
    stats = _usage_stats(response)
    assert stats == {
        "prompt_tokens": 1200,
        "completion_tokens": 80,
        "total_tokens": 1280,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 300,
        "cached_tokens": 900,
    }


def test_usage_stats_tolerates_missing_or_partial_usage():
    assert _usage_stats({}) == {}
    assert _usage_stats({"usage": None}) == {}
    assert _usage_stats({"usage": {"prompt_tokens": 10}}) == {"prompt_tokens": 10}
    assert _usage_stats(object()) == {}


@pytest.mark.asyncio
async def test_complete_emits_usage_on_success(monkeypatch):
    async def _no_keys() -> None:
        return None

    async def _ok(**kwargs):
        return {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }

    emitted: list[tuple] = []
    monkeypatch.setattr(llm_client, "_ensure_keys_loaded", _no_keys)
    monkeypatch.setattr(llm_client, "acompletion", _ok)
    monkeypatch.setattr(
        llm_client, "_emit_usage",
        lambda model, tier, resp: emitted.append((model, tier)),
    )

    result = await complete(
        tier=LLMTier.TIER_1, messages=[{"role": "user", "content": "hi"}],
    )
    assert result == "hello"
    assert len(emitted) == 1
    assert emitted[0][1] == "tier1"


# ── Cost pricing (E2 live cost) ──────────────────────────────────────────────

def test_compute_cost_prices_a_known_model():
    resp = {"usage": {"prompt_tokens": 1000, "completion_tokens": 500,
                      "total_tokens": 1500}}
    cost = _compute_cost("gpt-4o-mini", resp, resp["usage"])
    assert isinstance(cost, float) and cost > 0


def test_compute_cost_returns_none_for_unknown_model():
    resp = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    # Unknown model → no price in litellm's catalogue → None (UI shows "—",
    # never a misleading $0).
    assert _compute_cost("totally-made-up-model-xyz", resp, resp["usage"]) is None


def test_compute_cost_none_for_dynamically_stub_priced_model():
    # H8: ensure_model_registered() injects a PLACEHOLDER zero price so litellm
    # can route a new provider model — but that price is unknown, not $0. The
    # cost path must report unknown (None), not a confident $0.00, otherwise the
    # spend dashboard shows $0 for exactly the tier models in use.
    from acb_llm.client import ensure_model_registered

    # A model with a known provider prefix but NOT in litellm's price catalogue,
    # so ensure_model_registered takes the dynamic-stub path (a real, priced
    # model would return early and keep its real price — verified separately).
    model = "deepseek/deepseek-v99-unpriced-test"
    provider = ensure_model_registered(model)  # registers the zero-cost routing stub
    assert provider == "deepseek"
    resp = {"usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
    assert _compute_cost(model, resp, resp["usage"]) is None


def test_emit_usage_from_v1_compat_prices_and_tags_source(monkeypatch):
    # v1_compat (the agent-completion choke point) calls _emit_usage with an
    # explicit source and, on the streaming path, a bare {"usage": …} dict. This
    # is what makes chat-agent model calls + cost observable — assert it emits a
    # priced model activation attributed to the given source.
    import acb_common

    captured: list[dict] = []
    monkeypatch.setattr(acb_common, "publish_activity",
                        lambda **kw: captured.append(kw))

    # Streaming shape: usage-only dict (what v1_compat captures from the chunk).
    llm_client._emit_usage(
        "gpt-4o-mini", "",
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 500,
                   "total_tokens": 1500}},
        source="chat",
    )
    assert len(captured) == 1
    ev = captured[0]
    assert ev["kind"] == "model"
    assert ev["source"] == "chat"
    assert ev["tokens"] == 1500
    assert isinstance(ev["cost_usd"], float) and ev["cost_usd"] > 0


def test_emit_usage_never_raises_on_bad_response():
    # A malformed response must never take down the completion that produced it.
    llm_client._emit_usage("gpt-4o-mini", "", object(), source="chat")
    llm_client._emit_usage("gpt-4o-mini", "", {"usage": None}, source="memory")


# ── WS-6c: the D1 attribution four-tuple (run_id, member, agent, instance) ──
# These assert on the event as it reaches the STREAM (_axadd), not on the
# publish_activity kwargs — the whole point is that the run context, not the
# call site, supplies the stamp, and that merge happens inside _build_event.

_USAGE = {"usage": {"prompt_tokens": 1000, "completion_tokens": 500,
                    "total_tokens": 1500}}


def _emit_and_capture(monkeypatch, *, bind: dict | None = None, **emit_kw):
    """Run _emit_usage on a loop with _axadd stubbed; return the events."""
    import asyncio

    from acb_common import activity, bind_run_context, clear_run_context
    from acb_common.db import bind_tenant, release_tenant

    recorded: list[dict] = []

    async def _fake_axadd(evt, _org):
        recorded.append(evt)

    monkeypatch.setattr(activity, "_axadd", _fake_axadd)

    async def _run():
        # H-201c: the feed drops an event with no tenant, so the run binds one.
        token = bind_tenant("org-telemetry-test")
        if bind:
            bind_run_context(**bind)
        try:
            llm_client._emit_usage("gpt-4o-mini", "tier-fast", _USAGE, **emit_kw)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            clear_run_context()
            release_tenant(token)

    asyncio.run(_run())
    return recorded


def test_in_run_call_is_attributed_with_no_call_site_change(monkeypatch):
    # THE acceptance criterion: _emit_usage is called exactly as it always was
    # — no attribution kwargs at all — and the activation still carries all
    # four D1 fields, because they ride the run context.
    events = _emit_and_capture(monkeypatch, bind={
        "run_id": "run-42", "thread_id": "t-1", "agent": "email-assistant",
        "user": "alice@fracktal.in", "source": "email",
        "instance": "u:alice@fracktal.in",
    })
    assert len(events) == 1
    ev = events[0]
    assert ev["run_id"] == "run-42"
    assert ev["user"] == "alice@fracktal.in"        # the member
    assert ev["agent"] == "email-assistant"
    assert ev["instance"] == "u:alice@fracktal.in"  # the tenant partition
    # …without losing anything the feed already showed.
    assert ev["kind"] == "model"
    assert ev["model"] == "gpt-4o-mini"
    assert ev["source"] == "email"
    assert ev["tokens"] == 1500


def test_shared_agent_run_emits_no_instance_field(monkeypatch):
    # '' (shared) is absent, never the string "''" — the event shape a
    # pre-WS-6 consumer already handles.
    events = _emit_and_capture(monkeypatch, bind={
        "run_id": "run-43", "agent": "task-manager", "source": "chat",
        "instance": "",
    })
    assert "instance" not in events[0]


def test_out_of_run_caller_may_pass_the_stamp_explicitly(monkeypatch):
    # The v1_compat-shaped case (WS-6b's future call site): a bare HTTP request
    # inherits no contextvars, so it supplies the tuple itself. `member` lands
    # on the feed's `user` field so both paths roll up identically.
    events = _emit_and_capture(
        monkeypatch,
        source="chat", agent="orchestrator",
        run_id="run-44", member="bob@fracktal.in", instance="t:growth",
    )
    ev = events[0]
    assert ev["run_id"] == "run-44"
    assert ev["user"] == "bob@fracktal.in"
    assert ev["agent"] == "orchestrator"
    assert ev["instance"] == "t:growth"


def test_existing_source_and_agent_kwargs_are_untouched(monkeypatch):
    # No-regression: today's only explicit caller (v1_compat) passes exactly
    # these two and must keep behaving identically.
    events = _emit_and_capture(monkeypatch, source="chat", agent="apis-config")
    ev = events[0]
    assert ev["source"] == "chat"
    assert ev["agent"] == "apis-config"
    assert "run_id" not in ev and "instance" not in ev


def test_unpriced_model_publishes_unknown_cost_never_zero(monkeypatch):
    # The null-cost contract, end to end through the emitter: _compute_cost
    # returns None for a stub-priced model and NOTHING may coerce that to 0 —
    # a confident $0.00 is worse than an honest "—", and a 0 would also fold
    # into the daily rollup as real spend.
    from acb_llm.client import ensure_model_registered

    model = "deepseek/deepseek-v98-unpriced-emit-test"
    ensure_model_registered(model)

    import asyncio

    from acb_common import activity
    from acb_common.db import bind_tenant, release_tenant

    recorded: list[dict] = []

    async def _fake_axadd(evt, _org):
        recorded.append(evt)

    monkeypatch.setattr(activity, "_axadd", _fake_axadd)

    async def _run():
        token = bind_tenant("org-telemetry-test")  # H-201c: a write needs a tenant
        try:
            llm_client._emit_usage(model, "tier-fast", _USAGE)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            release_tenant(token)

    asyncio.run(_run())
    ev = recorded[0]
    assert ev.get("cost_usd") is None
    assert ev.get("cost_usd") != 0
    assert "cost_usd" not in ev  # dropped, so the rollup skips it entirely


# ── App-source inference (zero-touch cross-app attribution) ──────────────────

def test_infer_app_source_reads_the_originating_app_module():
    # Real chain in prod: <app handler> -> acompletion_with_fallback ->
    # _infer_app_source. _getframe(2) lands on the app handler, so a module
    # under gateway.routes.<app> attributes to <app> with NO call-site changes.
    def fake_acompletion():  # stands in for acompletion_with_fallback (frame 1)
        return _infer_app_source()

    for modname, expect in [
        ("gateway.routes.email.automation.drafting", "email"),
        ("gateway.routes.tasks.ai", "tasks"),
        ("gateway.routes.newapp.handlers", "newapp"),  # future app, no wiring
        ("orchestrator.executor", None),               # agent run → run context
    ]:
        g = {"__name__": modname, "fake_acompletion": fake_acompletion}
        exec("def handler():\n    return fake_acompletion()", g)  # noqa: S102
        assert g["handler"]() == expect
