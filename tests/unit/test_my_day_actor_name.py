"""Task 1574: a My Day notification card names the person by the directory name.

The card read "karthik commented on this task today" (the local part of the
address), while the task panel shows "Karthik Iyer". ``list_notifications`` now
gives ``actor_name`` from the one resolver, and the needs view and the feed
signal read it. An agent or a system actor has no directory row, and keeps the
bell's own label. Hermetic.
"""
from __future__ import annotations

from datetime import UTC, datetime

from gateway.routes.shell import needs
from gateway.routes.shell.feed import why

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)


def _row(**over):
    row = {"id": "n1", "task_id": "t1", "kind": "comment", "actor": "karthik@fracktal.in",
           "actor_name": "Karthik Iyer", "task_title": "Paint", "task_number": 7,
           "excerpt": "ok", "created_at": NOW}
    row.update(over)
    return row


def test_the_needs_line_names_the_person_by_the_directory_name() -> None:
    assert needs._sentence(_row()).startswith("Karthik Iyer ")


def test_the_needs_line_keeps_the_local_part_when_the_directory_has_no_name() -> None:
    assert needs._sentence(_row(actor_name=None)).startswith("karthik ")
    assert needs._sentence(_row(actor="agent:builder", actor_name=None)).startswith("builder ")


def test_the_feed_signal_carries_the_directory_name_as_who() -> None:
    [signal] = needs._note_signals([_row()])
    assert signal.facts["who"] == "Karthik Iyer"


def test_the_feed_card_text_says_the_directory_name() -> None:
    [signal] = needs._note_signals([_row()])
    from zoneinfo import ZoneInfo

    key, text = why("notification", signal.facts, NOW, NOW, ZoneInfo("UTC"), NOW.date())
    assert text.startswith("Karthik Iyer commented on this task")
    assert "karthik commented" not in text


def test_a_signal_for_a_member_with_a_directory_name_never_shows_the_local_part() -> None:
    rows = [_row(actor="rohan@fracktal.in", actor_name="Rohan Mehta", id="n2", task_id="t2"),
            _row()]
    for signal in needs._note_signals(rows):
        assert "@" not in signal.facts["who"] and signal.facts["who"][0].isupper()
