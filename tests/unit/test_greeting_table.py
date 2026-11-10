"""Fence (R7): My Day and the server greet from ONE table (PZ-39, D1).

``acb_common.today.GREETING_TABLE`` holds the table. ``myDay.ts`` holds the
same table as ``GREETING_TABLE``. This test reads the TypeScript source and
fails when the two differ. It also fails when the hello of the buddy chat,
the part-of-day line or ``greeting_for`` leave the table.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from acb_common.today import GREETING_TABLE, greeting_for, part_of_day

_TS = (
    Path(__file__).resolve().parents[2]
    / "workbench/control_plane/src/lib/shell/myDay.ts"
)


def _ts_table() -> list[tuple[int, str]]:
    src = _TS.read_text(encoding="utf-8")
    body = re.search(r"export const GREETING_TABLE[^=]*=\s*\[(.*?)\n\];", src, re.S)
    assert body, "myDay.ts must export GREETING_TABLE"
    return [(int(h), w) for h, w in re.findall(r'\[\s*(\d+)\s*,\s*"([^"]+)"\s*\]', body.group(1))]


def test_my_day_holds_the_same_table_as_the_server() -> None:
    assert _ts_table() == [(h, w) for h, _part, w in GREETING_TABLE]


def test_the_table_is_the_agreed_one() -> None:
    assert [(h, w) for h, _p, w in GREETING_TABLE] == [
        (5, "Good morning"), (12, "Good afternoon"), (17, "Good evening"), (21, "Hello"),
    ]


def test_no_late_greeting_is_a_goodbye() -> None:
    assert all("night" not in w.lower() for _h, _p, w in GREETING_TABLE)


def test_greeting_for_covers_every_hour() -> None:
    zone = ZoneInfo("Asia/Kolkata")
    for hour in range(24):
        now = datetime(2026, 11, 6, hour, 0, tzinfo=zone)
        want = "Hello" if hour >= 21 or hour < 5 else (
            "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening")
        assert greeting_for("Asia/Kolkata", now) == want, hour
    now = datetime(2026, 11, 6, 2, 0, tzinfo=zone)
    assert part_of_day("Asia/Kolkata", now) == "night"
