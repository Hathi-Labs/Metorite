"""POST /integrations/discover asks the Console, never a vendor. Task 3007.

Owner report, 2026-11-12. Integrations → Add API → AI Discovery, query
"salesforce", showed the member this in a red box::

    Gateway 500: {"detail":"Discovery failed: litellm.AuthenticationError:
    AnthropicException - ... x-api-key header is required ..."}

``discover_api`` called ``litellm.acompletion`` directly over four hard-coded
models. The tenant plane holds no provider key (owner rule, 2026-11-08), so
all four failed, and the last vendor error went to the member word for word.

The fix sends the call through ``acb_llm.client.complete``, the seam that
goes to the Console when routing is on. This file proves three things:

* the route calls ``complete`` with a tier and the ``structured`` job,
* it reads the answer in each shape that the model gives,
* a failure shows plain copy, and the vendor text stays in the log.

``test_no_direct_ai_vendor_calls.py`` is the fence that keeps ``litellm``
out of ``integrations.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import acb_llm.client as llm_client
import pytest
from acb_auth import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes import integrations

USER = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE)

_SALESFORCE = {
    "service_id": "salesforce",
    "label": "Salesforce",
    "category": "crm",
    "description": "CRM records.",
    "domain": "salesforce.com",
    "setup_url": "https://help.salesforce.com/",
    "docs_url": "https://developer.salesforce.com/docs",
    "instructions": "1. Make a connected app.",
    "env_vars": [{"key": "SALESFORCE_TOKEN", "label": "Token", "sensitive": True}],
}


@pytest.fixture(autouse=True)
def _no_web_search(monkeypatch: pytest.MonkeyPatch) -> None:
    # The SerpAPI step is optional. Without a key it does not run, so the
    # test makes no network call.
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setattr(
        integrations, "get_settings", lambda: SimpleNamespace(serpapi_api_key=""),
    )


def _answer(text: str | Exception) -> tuple[list[dict[str, Any]], Any]:
    calls: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> str:
        calls.append(kwargs)
        if isinstance(text, Exception):
            raise text
        return text

    return calls, fake_complete


async def _discover(monkeypatch: pytest.MonkeyPatch, text: str | Exception) -> Any:
    calls, fake = _answer(text)
    monkeypatch.setattr(llm_client, "complete", fake)
    out = await integrations.discover_api(
        integrations.DiscoverRequest(query="salesforce"), user=USER,
    )
    return calls, out


async def test_the_route_asks_the_console_seam_with_a_tier(monkeypatch):
    calls, out = await _discover(monkeypatch, json.dumps([_SALESFORCE]))
    assert len(calls) == 1
    assert calls[0]["tier"] == llm_client.LLMTier.TIER_2
    assert calls[0]["job"] == "structured"
    # The TIER travels, never a model name. The Console picks the model.
    assert "model" not in calls[0]
    assert "salesforce" in calls[0]["messages"][0]["content"]
    assert out["ok"] is True
    assert [r["service_id"] for r in out["results"]] == ["salesforce"]
    assert "model" not in out


@pytest.mark.parametrize("text", [
    json.dumps([_SALESFORCE]),
    json.dumps(_SALESFORCE),  # one object, not an array
    "```json\n" + json.dumps([_SALESFORCE]) + "\n```",  # a markdown fence
])
async def test_each_answer_shape_reads(monkeypatch, text):
    _calls, out = await _discover(monkeypatch, text)
    assert [r["label"] for r in out["results"]] == ["Salesforce"]


async def test_an_item_without_its_fields_is_dropped(monkeypatch):
    partial = {"service_id": "x", "label": "X"}  # no env_vars
    _calls, out = await _discover(monkeypatch, json.dumps([partial, _SALESFORCE, "junk"]))
    assert [r["service_id"] for r in out["results"]] == ["salesforce"]


async def test_a_console_failure_hides_the_vendor_text(monkeypatch):
    vendor = RuntimeError(
        'litellm.AuthenticationError: AnthropicException - '
        '{"type":"error","error":{"message":"x-api-key header is required"}}'
    )
    with pytest.raises(HTTPException) as caught:
        await _discover(monkeypatch, vendor)
    assert caught.value.status_code == 502
    detail = caught.value.detail
    assert "x-api-key" not in detail
    assert "litellm" not in detail
    assert "Anthropic" not in detail
    assert detail.startswith("AI discovery could not reach the AI model")
    assert "Reference: " in detail


@pytest.mark.parametrize("text", ["", "I cannot help with that.", "[]", "{}"])
async def test_an_unreadable_answer_says_so_plainly(monkeypatch, text):
    with pytest.raises(HTTPException) as caught:
        await _discover(monkeypatch, text)
    assert caught.value.status_code == 502
    assert caught.value.detail.startswith("AI discovery could not make an API list")


async def test_an_empty_query_never_calls_the_model(monkeypatch):
    calls, fake = _answer("[]")
    monkeypatch.setattr(llm_client, "complete", fake)
    with pytest.raises(HTTPException) as caught:
        await integrations.discover_api(integrations.DiscoverRequest(query="  "), user=USER)
    assert caught.value.status_code == 400
    assert calls == []
