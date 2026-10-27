"""The GST evidence cache of the open questions (hotfix 2026-10-20).

``gst_check`` took about 50 s for one org, and each read of the open
questions ran it, so the page gave up. A read now takes the cached evidence,
and the questions step after a sync warms it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gateway.routes.crm import questions as q

ORG = "00000000-0000-0000-0000-0000000000e1"


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[Any, tuple[str, int]] = {}

    async def get(self, name: Any) -> str | None:
        hit = self.data.get(name)
        return hit[0] if hit else None

    async def ttl(self, name: Any) -> int:
        hit = self.data.get(name)
        return hit[1] if hit else -2

    async def setex(self, name: Any, ttl: int, value: str) -> None:
        self.data[name] = (value, ttl)


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(q, "_redis", lambda: fake)
    return fake


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _check(_db: Any, _org: str, src: Any) -> dict[str, tuple[str, ...]]:
        calls.append(str(src.module))
        return {"IP": ("Excludes GST",)}

    monkeypatch.setattr(q, "_gst_evidence", _check)
    return calls


def _src(module: str) -> Any:
    return SimpleNamespace(module=module, kind="module")


def test_a_cached_module_needs_no_check(redis: _FakeRedis, checks: list[str]) -> None:
    first = asyncio.run(q.module_evidence(None, ORG, _src("Payment_Received")))
    second = asyncio.run(q.module_evidence(None, ORG, _src("Payment_Received")))
    assert first == second == {"IP": ("Excludes GST",)}
    assert checks == ["Payment_Received"]  # the second read used the cache


def test_a_redis_fault_still_answers(monkeypatch: pytest.MonkeyPatch, checks: list[str]) -> None:
    def _down() -> Any:
        raise ConnectionError("redis down")

    monkeypatch.setattr(q, "_redis", _down)
    ev = asyncio.run(q.module_evidence(None, ORG, _src("Invoices")))
    assert ev == {"IP": ("Excludes GST",)} and checks == ["Invoices"]


def test_the_step_warms_each_amount_module_once(monkeypatch: pytest.MonkeyPatch,
                                                redis: _FakeRedis, checks: list[str]) -> None:
    monkeypatch.setattr("gateway.routes.crm.flags.answer_first_allowed", lambda _org: True)
    amount = SimpleNamespace(kind=q.qs.KIND_KEY, part="amount", module="Invoices")
    other = SimpleNamespace(kind=q.qs.KIND_KEY, part="date", module="Bills")
    inputs = SimpleNamespace(sources={"Invoices": _src("Invoices"), "Bills": _src("Bills")})
    assert asyncio.run(q.warm_evidence(None, ORG, inputs, [amount, amount, other])) == 1
    # Warm and fresh: the next step checks nothing.
    assert asyncio.run(q.warm_evidence(None, ORG, inputs, [amount])) == 0
    assert checks == ["Invoices"]


def test_the_step_warms_nothing_off_the_list(monkeypatch: pytest.MonkeyPatch,
                                             redis: _FakeRedis, checks: list[str]) -> None:
    monkeypatch.setattr("gateway.routes.crm.flags.answer_first_allowed", lambda _org: False)
    amount = SimpleNamespace(kind=q.qs.KIND_KEY, part="amount", module="Invoices")
    inputs = SimpleNamespace(sources={"Invoices": _src("Invoices")})
    assert asyncio.run(q.warm_evidence(None, ORG, inputs, [amount])) == 0
    assert checks == []
