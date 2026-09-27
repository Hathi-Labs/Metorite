"""`vendor_reported_cost_usd` — the cost the VENDOR stated, or nothing.

No database. The function is pure, and its whole job is to be strict about
what it will believe. Spec: `credit_pricing.md` §4.1 · migration 031.

🔴 **The subject is the refusals, exactly as in `test_router_vendor_cost`.**
This number goes straight into `usage_event.provider_cost_usd` and is marked
`cost_source='vendor'`, which tells every downstream reader it is a
measurement. So anything we are not sure of must answer ``None`` and let the
computed path have the call instead.
"""

from __future__ import annotations

from decimal import Decimal

from customer_console.router import (
    ExtractedUsage,
    usage_from_response,
    vendor_reported_cost_usd,
)

KEY = "llm_provider-x-litellm-response-cost"


class _Resp:
    """The shape litellm hands back: an object carrying `_hidden_params`."""

    def __init__(self, hidden, usage=None):
        self._hidden_params = hidden
        if usage is not None:
            self.usage = usage


def _hidden(value):
    return {"additional_headers": {KEY: value}}


# ── What it accepts ─────────────────────────────────────────────────────────


def test_a_reported_float_becomes_an_exact_decimal() -> None:
    # 🔴 The point of going through `str`. Decimal(0.000075) is
    # 0.000074999999999999993... and money maths must not carry that.
    assert vendor_reported_cost_usd(_Resp(_hidden(0.000075))) == Decimal("0.00007500")


def test_a_reported_string_is_accepted() -> None:
    assert vendor_reported_cost_usd(_Resp(_hidden("0.00123456"))) == Decimal("0.00123456")


def test_a_reported_zero_is_KEPT_because_free_is_a_measurement() -> None:
    # A free-tier model legitimately costs nothing. That is a fact we measured,
    # not an absence — so it must not collapse to None.
    assert vendor_reported_cost_usd(_Resp(_hidden(0.0))) == Decimal("0")


def test_it_quantizes_to_the_columns_scale() -> None:
    # usage_event.provider_cost_usd is NUMERIC(14, 8).
    got = vendor_reported_cost_usd(_Resp(_hidden("0.0000000149")))
    assert got is not None
    assert got.as_tuple().exponent == -8


# ── What it refuses ─────────────────────────────────────────────────────────


def test_silence_is_none_and_never_zero() -> None:
    # Most vendors report no cost at all. A zero here would tell every margin
    # query the call was free. D-AI-7 rule 3.
    assert vendor_reported_cost_usd(_Resp({})) is None
    assert vendor_reported_cost_usd(_Resp({"additional_headers": {}})) is None
    assert vendor_reported_cost_usd(object()) is None


def test_a_negative_charge_is_refused() -> None:
    # No vendor bills a negative amount. Reading one means we misunderstood
    # the field, and a confidently wrong cost is worse than no cost.
    assert vendor_reported_cost_usd(_Resp(_hidden("-0.01"))) is None


def test_unparseable_values_are_refused_rather_than_raised() -> None:
    for bad in ["", "abc", None, [], {}, True, False]:
        assert vendor_reported_cost_usd(_Resp(_hidden(bad))) is None


def test_a_malformed_hidden_params_never_raises() -> None:
    # Metering is best-effort and must not fail a completion the customer
    # already holds.
    assert vendor_reported_cost_usd(_Resp("not-a-dict")) is None
    assert vendor_reported_cost_usd(_Resp({"additional_headers": "not-a-dict"})) is None


# ── How it reaches the metering path ────────────────────────────────────────


def test_usage_from_response_carries_the_reported_cost() -> None:
    resp = _Resp(
        _hidden(0.000075),
        usage={"prompt_tokens": 100, "completion_tokens": 50},
    )
    got = usage_from_response(resp)
    assert got.prompt_tokens == 100
    assert got.vendor_reported_cost_usd == Decimal("0.00007500")


def test_a_response_with_no_reported_cost_leaves_the_field_none() -> None:
    resp = _Resp({}, usage={"prompt_tokens": 100, "completion_tokens": 50})
    got = usage_from_response(resp)
    assert got.prompt_tokens == 100
    assert got.vendor_reported_cost_usd is None


def test_the_field_defaults_to_none_so_old_callers_are_unchanged() -> None:
    # Every existing construction of ExtractedUsage predates this field.
    assert ExtractedUsage().vendor_reported_cost_usd is None


# ── The contract with the INSTALLED litellm ─────────────────────────────────


def test_litellms_own_openrouter_transform_writes_the_key_we_read() -> None:
    """🔴 **Every test above hands the reader a stub. This one does not.**

    `_LITELLM_COST_KEY` is litellm's internal shape, not a public contract.
    A litellm release that renames the key, or moves it out of
    `_hidden_params["additional_headers"]`, turns every vendor-stated cost
    into ``None`` with no error, and every stub test above stays green.

    So this test runs litellm's REAL OpenRouter response transform over an
    OpenRouter body that states ``usage.cost``, and reads the result through
    the reader production uses. Measured in litellm 1.103.0 on 2026-09-28.
    A litellm upgrade that breaks the contract fails here.
    """
    from unittest.mock import MagicMock

    import httpx
    from litellm.llms.openrouter.chat.transformation import OpenrouterConfig
    from litellm.types.utils import ModelResponse

    body = {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek/deepseek-v4-pro",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hi"},
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
            "cost": 0.000075,
        },
    }
    raw = httpx.Response(
        200,
        json=body,
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    resp = OpenrouterConfig().transform_response(
        model="openrouter/deepseek/deepseek-v4-pro",
        raw_response=raw,
        model_response=ModelResponse(),
        logging_obj=MagicMock(),
        request_data={},
        messages=[{"role": "user", "content": "x"}],
        optional_params={},
        litellm_params={},
        encoding=None,
    )

    got = usage_from_response(resp)
    assert got.prompt_tokens == 12
    assert got.completion_tokens == 3
    assert got.vendor_reported_cost_usd == Decimal("0.00007500")
