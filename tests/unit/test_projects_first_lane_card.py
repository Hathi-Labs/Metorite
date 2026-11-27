"""Task 1792 — the create_tasks batch card names the lane the gateway uses.

``load_default_status`` never gives a new task a Done, Cancelled or Triage
lane. The card is the member's consent, so ``_first_lane`` must say the same.
"""
from __future__ import annotations

from skill_projects.forms import _first_lane


def _lane(name: str, category: str, position: int) -> dict:
    return {"name": name, "category": category, "position": position}


def test_a_done_lane_is_never_the_first_lane():
    # The route reads the set in stage order, so Done already sits last.
    rows = [
        _lane("Backlog", "backlog", 10),
        _lane("To do", "todo", 20),
        _lane("Done", "done", 1),
    ]
    assert _first_lane(rows) == "Backlog"


def test_it_skips_a_closing_lane_even_when_the_route_lists_it_first():
    rows = [_lane("Done", "done", 1), _lane("To do", "todo", 20)]
    assert _first_lane(rows) == "To do"


def test_triage_is_skipped():
    rows = [_lane("Inbox", "triage", 1), _lane("Backlog", "backlog", 50)]
    assert _first_lane(rows) == "Backlog"


def test_a_set_with_only_closing_lanes_gives_none_and_names_no_lane():
    rows = [_lane("Done", "done", 1), _lane("Dropped", "cancelled", 2)]
    assert _first_lane(rows) is None


def test_no_rows_keeps_the_plain_first_lane_wording():
    assert _first_lane([]) == ""
