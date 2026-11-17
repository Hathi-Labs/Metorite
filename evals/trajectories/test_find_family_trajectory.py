"""Golden trajectory: the find family of the one assistant (WS-FIND FIND-6).

Spec: ``project-docs/specs/find_engine.md`` §5.3, §5.4 and the row FIND-6
of §9.2.

The run is the REAL ``run_agent_stream("orchestrator")``, through the
routing harness (``evals/routing/harness.py``). A scripted ``ReplyModel``
makes the choices, so this proves the path and never the choice. The tool is
the real ``search_company``. Only its gateway is a fake.

With ``COMPANY_SEARCH_ENABLED`` on and ``FIND_ORGS`` naming the org:

1. The first model request offers ``search_company`` and ``resolve_name``.
   Its system message holds the standing rule of §5.4, once.
2. The route of ``search_company`` fails with a 500. The model reads the
   fixed text of the HRD-3 guard (``pack_tool_guard.failure_text``), never
   "Error: Function failed.", and the run ends with no error.

With ``FIND_ORGS`` empty, the request offers no ``resolve_name`` and holds
no rule. The unit fence is ``tests/unit/test_find_tools.py``.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any

import httpx
import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")
pytest.importorskip("gateway.retrieval.find", reason="gateway not installed")

from evals.routing import dataset as ds
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    RunRecord,
    text_turn,
    tool_turn,
)

WORDS = "Who is Sharat?"
LIMIT = 60.0
SORRY = "The search failed, so I cannot say yet. Try again in a moment."


class _Gateway:
    """The route of ``search_company``, as the member. It fails with a 500."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path,
                           request.headers.get("X-User-Email", "")))
        return httpx.Response(500, json={"detail": "boom for jane.secret@acme.test"})


def _wire(monkeypatch: pytest.MonkeyPatch, gw: _Gateway) -> None:
    """Point the find tools at the fake gateway, and nothing else."""
    from acb_skills import company_search_tools as cst

    real = httpx.AsyncClient

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(*args, transport=httpx.MockTransport(gw.handler), **kwargs)

    monkeypatch.setattr(cst, "httpx", types.SimpleNamespace(
        AsyncClient=_client, HTTPError=httpx.HTTPError))
    monkeypatch.setenv("GATEWAY_URL", "http://gw.test")


def _run(monkeypatch: pytest.MonkeyPatch, turns: list[list[dict[str, Any]]], *,
         find: str) -> tuple[RunRecord, ReplyModel, _Gateway]:
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "company_search_enabled", True, raising=False)
    monkeypatch.setattr(settings, "find_orgs", find, raising=False)
    gw = _Gateway()
    _wire(monkeypatch, gw)
    with RoutingHarness() as harness:
        harness.models = {ORCHESTRATOR: ReplyModel(turns)}
        model = harness.models[ORCHESTRATOR]
        record = asyncio.run(harness.drive(
            WORDS, "", member=ds.MEMBER, org=ds.ORG, timeout=LIMIT,
        ))
        assert harness.sent == [], harness.sent
    assert record.error is None, record.error
    return record, model, gw


def _offered(body: dict[str, Any]) -> set[str]:
    return {str((t.get("function") or {}).get("name") or "") for t in body.get("tools") or []}


def _system(body: dict[str, Any]) -> str:
    """The text of the system message of one request."""
    parts: list[str] = []
    for message in body.get("messages") or []:
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, list):
            parts += [str(p.get("text") or "") for p in content if isinstance(p, dict)]
        else:
            parts.append(str(content or ""))
    return "\n".join(parts)


def _tool_results(model: ReplyModel) -> list[str]:
    return [str(m.get("content")) for b in model.bodies for m in b.get("messages") or []
            if m.get("role") == "tool"]


def test_on_the_family_is_offered_and_a_failure_is_text(monkeypatch) -> None:
    from acb_common.tool_refusals import FAILED
    from acb_skills.pack_tool_guard import failure_text
    from orchestrator.agents import FIND_STANDING_RULE

    turns = [
        tool_turn("search_company", {"query": "Sharat"}, call_id="call_1"),
        text_turn(SORRY),
    ]
    record, model, gw = _run(monkeypatch, turns, find=ds.ORG)

    # 1. The family is offered, and the persona holds the standing rule once.
    first = model.bodies[0]
    assert {"search_company", "resolve_name"} <= _offered(first)
    assert _system(first).count(FIND_STANDING_RULE) == 1

    # 2. The route failed, and the model read the fixed text of the guard.
    assert [name for name, _ in record.tool_calls] == ["search_company"]
    assert [(m, p, who) for m, p, who in gw.calls] == [
        ("GET", "/search/company", ds.MEMBER)]
    results = _tool_results(model)
    assert results == [failure_text(RuntimeError(), read_only=True)]
    assert results[0].startswith(FAILED)
    assert "Function failed" not in results[0]
    assert "jane.secret" not in results[0] and "500" not in results[0]
    assert record.answer == SORRY


def test_off_the_request_is_the_request_of_before(monkeypatch) -> None:
    from orchestrator.agents import FIND_STANDING_RULE

    record, model, gw = _run(monkeypatch, [text_turn("Hello.")], find="")
    first = model.bodies[0]
    assert "search_company" in _offered(first)
    assert "resolve_name" not in _offered(first)
    assert FIND_STANDING_RULE not in _system(first)
    assert "Where else" not in _system(first)
    assert gw.calls == []
    assert record.answer == "Hello."
