"""A day start of 0 (midnight) stays 0 (task 1210).

`_load` read `day_start_hour` with `int(x or 7)`, so 0 became 7. The mapping
is pure, so a fake row proves it. No SQL runs here.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.routes.tasks import settings


class _Res:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _DB:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def execute(self, *_a: Any, **_k: Any) -> _Res:
        return _Res(self._row)


def _row(**over: Any) -> SimpleNamespace:
    base = dict(chat_model=None, clarify_model=None, atomize_model=None,
                email_capture_model=None, capture_dedup=True,
                auto_sync_on_open=True)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("stored,expected", [(0, 0), (6, 6), (None, 7)])
def test_day_start_hour_keeps_zero(stored: Any, expected: int) -> None:
    got = asyncio.run(settings._load(_DB(_row(day_start_hour=stored)), "a@b.co"))
    assert got.day_start_hour == expected
