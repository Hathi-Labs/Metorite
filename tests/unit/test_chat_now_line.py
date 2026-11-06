"""The "now" line of every chat turn (task 1649).

R7 fences:

* ``now-line-holds-time-and-zone``: the line has the date, the time and the
  zone of the member, and the rule that the chat never asks for the time.
* ``now-zone-falls-back-in-order``: ``user_settings.timezone``, then
  ``people.timezone``, then the default. ``UTC`` (the column default) loses to
  a real zone.
* ``now-line-in-every-turn``: the context builder adds the line, in the zone
  that ``_member_zones`` reads.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from acb_common.today import DEFAULT_ZONE, resolve_zone, today_line
from orchestrator import executor as ex

# 2026-11-04 19:44 UTC is 2026-11-05 01:14 in India.
MOMENT = datetime(2026, 11, 4, 19, 44, tzinfo=UTC)


def test_the_now_line_holds_date_time_and_zone() -> None:
    line = today_line(MOMENT, "Asia/Kolkata", with_time=True)
    assert line.startswith("Now: Thursday 2026-11-05 01:14 IST (Asia/Kolkata).")


def test_the_now_line_tells_the_chat_never_to_ask_for_the_time() -> None:
    line = today_line(MOMENT, "Asia/Kolkata", with_time=True)
    assert "Never ask the member what time it is." in line


def test_the_now_line_reads_another_zone() -> None:
    line = today_line(MOMENT, "America/New_York", with_time=True)
    assert "Wednesday 2026-11-04 14:44 EST (America/New_York)" in line


def test_the_old_date_line_is_unchanged() -> None:
    assert today_line(MOMENT, "Asia/Kolkata") == "Today is Thursday, 5 November 2026 (Asia/Kolkata)."


@pytest.mark.parametrize(
    ("names", "want"),
    [
        (("Europe/Berlin", "Asia/Kolkata"), "Europe/Berlin"),
        (("", "Asia/Tokyo"), "Asia/Tokyo"),
        ((None, None, "Asia/Tokyo"), "Asia/Tokyo"),
        (("Not/AZone", "Asia/Tokyo"), "Asia/Tokyo"),
        (("UTC", "Asia/Tokyo"), "Asia/Tokyo"),
        (("UTC", ""), "UTC"),
        (("", None, "Not/AZone"), DEFAULT_ZONE),
        ((), DEFAULT_ZONE),
    ],
)
def test_the_zone_falls_back_in_order(names, want) -> None:
    assert resolve_zone(*names) == want


def test_the_turn_context_carries_the_now_line_in_the_member_zone(monkeypatch) -> None:
    monkeypatch.setattr(ex, "_member_zones", lambda: ("Asia/Tokyo", ""))
    monkeypatch.setattr(ex, "_run_org_display_name", lambda: "")
    monkeypatch.setattr(ex, "_current_run_org", lambda: None)
    parts = ex._context_preamble_parts({}, {}, "orchestrator")
    now = [p for p in parts if p.startswith("Now: ")]
    assert len(now) == 1
    assert "(Asia/Tokyo)" in now[0] and "JST" in now[0]
    assert "Never ask the member what time it is." in now[0]
    assert not any(p.startswith("Today is ") for p in parts)
