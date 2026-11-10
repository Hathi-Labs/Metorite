"""Custom Apps · runtime AI presence + cost attribution (RFC §4.6).

Locks ``ai_complete`` (``gateway.routes.apps.runtime``)'s wiring into the live
activity bus: a start/end presence pair (so a working app shows in the
observability office view exactly like a working agent) around the ACTUAL
provider call, and an explicit ``source=f"app:{slug}"`` override so
``/observability/cost``'s "by app" breakdown can tell one app's spend from
another's (stack-inferred attribution collapses every custom app into one
generic "apps" bucket — see ``acb_llm.context._infer_app_source``).

DB-touching seams (``_tenant_session``/``get_app_or_404``/``_month_ai_usage``/
``record_app_audit``) and the LLM call itself are monkeypatched with fakes —
no live Postgres, no live model — mirroring ``test_app_actions.py``'s
conventions (``_tenant_session`` swapped in as an ``asynccontextmanager``,
the H2 seam shape).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from gateway.routes.apps import runtime
from gateway.routes.apps.runtime import AiCompleteRequest, ai_complete

VIEWER = UserContext(email="viewer@fracktal.in", role=UserRole.EMPLOYEE)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    """Enough of litellm's ``ModelResponse`` shape: attribute access for
    ``resp.choices[0].message.content`` AND ``.get("usage")`` for
    ``_usage_from_response``'s dict-like read."""

    def __init__(self, content: str = "Hello!", prompt_tokens: int = 10,
                 completion_tokens: int = 5) -> None:
        self.choices = [_FakeChoice(content)]
        self._usage = {"prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens}

    def get(self, key: str, default: Any = None) -> Any:
        return self._usage if key == "usage" else default


def _row(**overrides: Any) -> Any:
    defaults = {"id": "app-1", "live_version": 1, "manifest": {}}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _FakeDB:
    async def commit(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, used_tokens: int = 0,
                   budget: int = 100_000) -> None:
    row = _row()

    @asynccontextmanager
    async def _tenant_session() -> AsyncIterator[_FakeDB]:
        db = _FakeDB()
        yield db
        await db.commit()

    async def _get_app_or_404(_db: Any, _slug: str, _user: UserContext) -> Any:
        return row, []

    async def _month_ai_usage(_db: Any, _app_id: str) -> dict[str, Any]:
        return {"tokens": used_tokens, "cost_usd": 0.0, "calls": 0}

    async def _record_app_audit(**_kw: Any) -> None:
        pass

    monkeypatch.setattr(runtime, "_tenant_session", _tenant_session)
    monkeypatch.setattr(runtime, "get_app_or_404", _get_app_or_404)
    monkeypatch.setattr(runtime, "_month_ai_usage", _month_ai_usage)
    monkeypatch.setattr(runtime, "resolve_ai_budget", lambda _manifest: budget)
    monkeypatch.setattr(runtime, "resolve_ai_tier", lambda _tier, _manifest: "tier-fast")
    monkeypatch.setattr(runtime, "record_app_audit", _record_app_audit)


@pytest.mark.asyncio
async def test_ai_complete_emits_start_and_end_presence_around_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runtime, "publish_app_activity",
        lambda slug, **fields: calls.append({"slug": slug, **fields}),
    )

    captured_kwargs: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> tuple[Any, str]:
        captured_kwargs.update(kwargs)
        return _FakeResponse(), "deepseek/deepseek-chat"

    monkeypatch.setattr("acb_llm.acompletion_with_fallback", _fake_completion)

    result = await ai_complete(
        "filament-tracker", AiCompleteRequest(prompt="hi"), user=VIEWER,
    )

    # Explicit per-app source — never the collapsed generic "apps" bucket.
    assert captured_kwargs["source"] == "app:filament-tracker"

    assert len(calls) == 2
    start, end = calls
    assert start["slug"] == end["slug"] == "filament-tracker"
    assert start["phase"] == "start"
    assert end["phase"] == "end"
    assert start["run_id"] == end["run_id"]
    assert start["run_id"]  # non-empty
    assert end["model"] == "deepseek/deepseek-chat"
    assert end["tokens"] == 15  # 10 prompt + 5 completion
    assert result["text"] == "Hello!"


@pytest.mark.asyncio
async def test_ai_complete_emits_error_end_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_common(monkeypatch)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runtime, "publish_app_activity",
        lambda slug, **fields: calls.append({"slug": slug, **fields}),
    )

    async def _fake_completion(**_kwargs: Any) -> tuple[Any, str]:
        raise RuntimeError("provider down")

    monkeypatch.setattr("acb_llm.acompletion_with_fallback", _fake_completion)

    with pytest.raises(HTTPException) as excinfo:
        await ai_complete("filament-tracker", AiCompleteRequest(prompt="hi"), user=VIEWER)
    assert excinfo.value.status_code == 502

    # A failed call must still close its presence key — never rely on the
    # 15-minute TTL self-heal for the COMMON failure case.
    assert len(calls) == 2
    start, end = calls
    assert start["phase"] == "start"
    assert end["phase"] == "end"
    assert end["status"] == "error"
    assert start["run_id"] == end["run_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "   "])
async def test_ai_complete_with_no_text_is_an_error_not_a_200(
    monkeypatch: pytest.MonkeyPatch, content: str,
) -> None:
    """Task 1128: a blank answer read as a real one in the app. It is a 502
    with a clear detail now. The spent tokens are still recorded, and the
    presence key closes with an error."""
    _patch_common(monkeypatch)
    calls: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runtime, "publish_app_activity",
        lambda slug, **fields: calls.append({"slug": slug, **fields}),
    )

    async def _audit(**kw: Any) -> None:
        audits.append(kw)

    monkeypatch.setattr(runtime, "record_app_audit", _audit)
    seen: dict[str, Any] = {}

    async def _blank(**kwargs: Any) -> tuple[Any, str]:
        seen.update(kwargs)
        return _FakeResponse(content=content), "deepseek/deepseek-v4-pro"

    monkeypatch.setattr("acb_llm.acompletion_with_fallback", _blank)

    with pytest.raises(HTTPException) as excinfo:
        await ai_complete("filament-tracker", AiCompleteRequest(prompt="hi"), user=VIEWER)
    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == runtime.EMPTY_AI_ANSWER
    assert seen["job"] == "writing"
    assert [a["tokens_out"] for a in audits] == [5]
    assert calls[-1]["phase"] == "end" and calls[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_ai_complete_budget_exhausted_never_touches_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The budget check returns before the LLM is ever called — no presence
    # key should be created for a call that never actually ran.
    _patch_common(monkeypatch, used_tokens=100_000, budget=100_000)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        runtime, "publish_app_activity",
        lambda slug, **fields: calls.append({"slug": slug, **fields}),
    )

    async def _unreachable(**_kwargs: Any) -> Any:
        raise AssertionError("must not reach the LLM when budget is exhausted")

    monkeypatch.setattr("acb_llm.acompletion_with_fallback", _unreachable)

    result = await ai_complete(
        "filament-tracker", AiCompleteRequest(prompt="hi"), user=VIEWER,
    )
    assert isinstance(result, JSONResponse)
    assert result.status_code == 429
    assert calls == []
