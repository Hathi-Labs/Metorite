"""A clock for ``acb_skills.attachment_text`` that only the test moves.

The attachment deadline fences of ``test_read_attachment.py`` and
``test_attachment_xlsx.py`` use it, so that a deadline fires at a count of
checks or of slow steps, and never at an amount of runner time.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from acb_skills import attachment_text as at


class TickClock:
    """``time`` as ``attachment_text`` reads it, with a clock the test moves.

    ``_Deadline`` is the only reader of ``time.monotonic`` in that module, so
    this clock decides when a deadline fires. ``advance`` moves it, and
    *per_read* moves it on each read. A deadline then fires after a COUNT of
    checks or of slow steps, never after an amount of runner time.

    These fences read the real clock before. A slow PDF failed at 4.54 s and
    4.7 s against 4.5 s on loaded CI runners (2026-10-11 and 2026-10-12),
    and #943 widened the bound to 15 s. Now the fence is where the parse
    stops, and a loaded runner cannot move that."""

    def __init__(self, per_read: float = 0.0) -> None:
        self.now = 0.0
        self.per_read = per_read
        self.reads = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)

    def monotonic(self) -> float:
        self.reads += 1
        self.now += self.per_read
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def install(self, monkeypatch: pytest.MonkeyPatch) -> TickClock:
        monkeypatch.setattr(at, "time", self)
        return self
