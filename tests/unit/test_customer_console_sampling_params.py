"""A Claude 5.5 step gets no sampling value. Task 2450.

Measured 2026-11-08 on litellm 1.103.0: ``temperature=0.2`` on
``anthropic/claude-haiku-5-5`` raises ``UnsupportedParamsError``. The Console
runs litellm with no ``drop_params``, so ``sampling_params_for`` drops
``temperature``, ``top_p`` and ``top_k`` for each step that refuses them.
The door test for a mixed chain is ``test_customer_console_router.py::
TestSamplingPerStep`` (R8).
"""
from __future__ import annotations

import ast
import logging
import pathlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("litellm")

from customer_console import reasoning
from customer_console.reasoning import sampling_params_for

ROOT = pathlib.Path(__file__).resolve().parents[2]
BODY = {"messages": [{"role": "user", "content": "secret words"}],
        "temperature": 0.2, "top_p": 0.9, "top_k": 40, "max_tokens": 64}

REFUSING = [
    "anthropic/claude-haiku-5-5",
    "anthropic/claude-sonnet-5-5",
    "anthropic/claude-opus-5-5",
    "anthropic/claude-opus-4-8",
]
TAKING = [
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-haiku-4-5",
    "gemini/gemini-3.1-flash-lite",
    "deepseek/deepseek-chat",
    "openai/gpt-4o-mini",
]


@pytest.fixture(autouse=True)
def _fresh_log_memory():
    """The drop log fires once per (model, keys) per process. Each test starts clean."""
    reasoning._LOGGED_DROPS.clear()
    yield
    reasoning._LOGGED_DROPS.clear()


def test_litellm_still_has_the_rule_we_read():
    """Fails LOUD when litellm renames its private sampling rule.

    ``step_takes_sampling`` guards the import, so a rename alone would fall
    back in silence to the model map and the version rule.
    """
    from litellm.llms.anthropic.common_utils import AnthropicModelInfo

    assert callable(getattr(AnthropicModelInfo, "_supports_sampling_params", None))


@pytest.mark.parametrize("model", [
    "anthropic/claude-sonnet-5-6",
    "anthropic/claude-opus-6",
    "anthropic/claude-fable-5-1",
    "anthropic/claude-mythos-5-1",
    "openrouter/anthropic/claude-sonnet-5-5",
])
def test_a_claude_id_newer_than_the_map_still_drops(model):
    """Review of 2026-11-08: a Claude 5+ id that the model map does not know."""
    assert "temperature" not in sampling_params_for(dict(BODY), model)


@pytest.mark.parametrize("model", [
    "anthropic/claude-3-5-sonnet-20241022",
    "anthropic/claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-4-6",
])
def test_an_older_claude_id_keeps_its_values(model):
    body = dict(BODY)
    assert sampling_params_for(body, model) is body


def test_the_drop_logs_once_per_model_and_keys(caplog):
    with caplog.at_level(logging.INFO, logger="platform.router"):
        sampling_params_for(dict(BODY), "anthropic/claude-haiku-5-5")
        sampling_params_for(dict(BODY), "anthropic/claude-haiku-5-5")
        sampling_params_for({"messages": [], "temperature": 0.2},
                            "anthropic/claude-haiku-5-5")
    recs = [r for r in caplog.records if r.getMessage() == "router.sampling_params_dropped"]
    assert [r.sp_dropped for r in recs] == ["temperature,top_p,top_k", "temperature"]


@pytest.mark.parametrize("model", REFUSING)
def test_a_refusing_model_loses_the_sampling_values(model):
    out = sampling_params_for(dict(BODY), model)
    assert out == {"messages": BODY["messages"], "max_tokens": 64}


@pytest.mark.parametrize("model", TAKING)
def test_a_taking_model_keeps_every_value(model):
    body = dict(BODY)
    assert sampling_params_for(body, model) is body


def test_temperature_one_is_kept():
    body = {"messages": [], "temperature": 1}
    assert sampling_params_for(body, "anthropic/claude-haiku-5-5") is body
    out = sampling_params_for({**body, "top_p": 0.5}, "anthropic/claude-haiku-5-5")
    assert out == {"messages": [], "temperature": 1}


def test_a_model_litellm_cannot_place_keeps_its_params():
    body = dict(BODY)
    assert sampling_params_for(body, "aimlapi/typesafe/jev") is body
    assert sampling_params_for(body, "") is body


def test_the_model_map_flag_drops_for_any_provider(monkeypatch):
    import litellm

    monkeypatch.setitem(
        litellm.model_cost, "openai/gpt-4o-mini", {"supports_sampling_params": False})
    assert "temperature" not in sampling_params_for(dict(BODY), "openai/gpt-4o-mini")


def test_the_input_is_not_mutated():
    body = dict(BODY)
    before = dict(body)
    out = sampling_params_for(body, "anthropic/claude-haiku-5-5")
    assert body == before
    assert out is not body


def test_the_log_names_the_model_and_keys_never_the_content(caplog):
    with caplog.at_level(logging.INFO, logger="platform.router"):
        sampling_params_for(dict(BODY), "anthropic/claude-haiku-5-5")
    recs = [r for r in caplog.records if r.getMessage() == "router.sampling_params_dropped"]
    assert len(recs) == 1
    assert recs[0].sp_model == "anthropic/claude-haiku-5-5"
    assert recs[0].sp_dropped == "temperature,top_p,top_k"
    assert "secret words" not in repr(recs[0].__dict__)


def test_no_log_when_nothing_is_dropped(caplog):
    with caplog.at_level(logging.INFO, logger="platform.router"):
        sampling_params_for(dict(BODY), "openai/gpt-4o-mini")
    assert not caplog.records


def test_the_chat_path_runs_the_filter_per_step():
    """Structural: ``_kwargs_for`` (per step) calls the filter."""
    src = (ROOT / "apps/services/customer_console/customer_console/main.py").read_text(
        encoding="utf-8")
    hits = []
    for fn in ast.walk(ast.parse(src)):
        if isinstance(fn, ast.FunctionDef) and fn.name == "_kwargs_for":
            for c in ast.walk(fn):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                        and c.func.id == "sampling_params_for"):
                    hits.append(ast.unparse(c))
    assert hits == ["sampling_params_for(passthrough, step.model)"]
    assert reasoning.SAMPLING_PARAMS == ("temperature", "top_p", "top_k")
