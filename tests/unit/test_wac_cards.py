"""WS-47 WAC-10c — the WhatsApp card images and the colour vocabulary.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.

R7 fences named here:

* ``wac10c-cards-draw``: each card draws a PNG from valid data, and refuses
  bad data with a reason (a ``RenderError``), never a crash.
* ``wac10c-one-vocabulary``: a status takes the hue that
  ``statusAccent.ts`` gives it (``hue``).
* ``wac10c-table-layout``: spare width goes to the widest TEXT column, never
  to a short id column (the owner's "Due today" image, 2026-10-10).
"""
from __future__ import annotations

import pytest
from acb_skills import whatsapp_cards as cards
from acb_skills import whatsapp_render as render
from acb_skills import whatsapp_ui as wui

pytest.importorskip("pymupdf")

PNG = b"\x89PNG\r\n\x1a\n"


def _png(svg: str) -> bytes:
    out = render.to_png(svg)
    assert out.startswith(PNG)
    return out


# ── The colour vocabulary ───────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "expected"), [
    ("in_progress", "blue"), ("In progress", "blue"), ("Done", "green"),
    ("Blocked", "amber"), ("Waiting on vendor", "amber"), ("todo", "gray"),
    ("Backlog", "gray"), ("cancelled", "red"), ("triage", "violet"),
    ("purple", "violet"), ("orange", "amber"), ("grey", "gray"),
    ("on_hold", "amber"), ("active", "green"), ("at risk", "amber"),
    ("off_track", "red"), ("Overdue", "red"), ("Code review", "blue"),
    (None, "gray"), ("", "gray"), ("Something else", "gray"),
])
def test_a_status_takes_the_web_hue(value: object, expected: str) -> None:
    assert cards.hue(value) == expected


# ── The cards draw, and refuse bad data with a reason ───────────────────────


def test_stats_board_timeline_agenda_gantt_donut_draw() -> None:
    _png(cards.stats_svg("Week", [{"label": "Done", "value": 34, "delta": "+12%"},
                                  {"label": "Overdue", "value": 5, "delta": "+2",
                                   "good": "down", "tone": "red"},
                                  {"label": "Hours", "value": "126 h", "hint": "of 160"}]))
    _png(cards.board_svg("Board", [
        {"name": "To do", "cards": [{"title": "A " * 40, "meta": "Vijay"}], "total": 9},
        {"name": "Done", "cards": []}]))
    _png(cards.timeline_svg("Milestones", [
        {"date": "1 Oct", "title": "BOM", "state": "done"},
        {"date": "8 Oct", "title": "Beta", "detail": "x " * 80, "state": "current"},
        {"date": "1 Nov", "title": "Launch"}]))
    _png(cards.agenda_svg("Tue", [
        {"start": "09:00", "end": "09:30", "title": "Stand-up"},
        {"start": "09:15", "end": "11:00", "title": "Overlap", "kind": "focus"},
        {"start": "12:00", "end": "13:00", "title": "Free", "kind": "free"}],
        all_day=["Due: GST"]))
    _png(cards.gantt_svg("Plan", [
        {"label": "Design", "start": "2026-09-01", "end": "2026-09-20", "status": "done"},
        {"label": "Launch", "start": "2026-11-01", "end": "2026-11-01"}],
        today="2026-10-10"))
    _png(cards.donut_svg("By status", ["To do", "Done"], [3, 7]))


@pytest.mark.parametrize(("fn", "args", "says"), [
    (cards.stats_svg, ("t", []), "non-empty"),
    (cards.stats_svg, ("t", [{"label": "x"}]), "tile value"),
    (cards.stats_svg, ("t", [{"label": "x", "value": 1}] * 9), "at most 8"),
    (cards.board_svg, ("t", [{"name": "a", "cards": []}] * 5), "at most 4"),
    (cards.board_svg, ("t", [{"name": "a", "cards": "nope"}]), "cards"),
    (cards.timeline_svg, ("t", [{"date": "x"}]), "event title"),
    (cards.agenda_svg, ("t", [{"start": "9am", "title": "x"}]), "time like"),
    (cards.agenda_svg, ("t", [{"start": "10:00", "end": "09:00", "title": "x"}]), "end after"),
    (cards.agenda_svg, ("t", [{"start": "00:00", "end": "23:00", "title": "x"}]), "15 hours"),
    (cards.gantt_svg, ("t", [{"label": "x", "start": "soon"}]), "date like"),
    (cards.gantt_svg, ("t", [{"label": "x", "start": "2026-10-10", "end": "2026-10-01"}]),
     "on or after"),
    (cards.donut_svg, ("t", ["a"], [0]), "total above zero"),
    (cards.donut_svg, ("t", list("abcdefghi"), [1] * 9), "at most 8"),
])
def test_bad_card_data_is_refused_with_a_reason(fn, args, says: str) -> None:
    with pytest.raises(render.RenderError, match=says):
        fn(*args)


def test_hostile_text_is_escaped_and_cut() -> None:
    svg = cards.board_svg("<script>", [{"name": "</text><x>", "cards": [
        {"title": "&<>\"'" * 400, "meta": "m" * 4000}]}])
    assert "<script>" not in svg and "</text><x>" not in svg
    _png(svg)


# ── The table layout (the owner's image) ────────────────────────────────────


def test_spare_width_goes_to_the_widest_text_column() -> None:
    need = [40.0, 220.0, 70.0]          # "#", "Task", "Priority"
    widths = render.column_widths(need, [True, False, False], 584.0)
    assert widths[0] == pytest.approx(48.0)          # the id column stays narrow
    assert widths[2] == pytest.approx(70.0)
    assert widths[1] == pytest.approx(584.0 - 48.0 - 70.0)


def test_only_the_wide_columns_shrink() -> None:
    need = [40.0, 900.0, 600.0, 70.0]
    widths = render.column_widths(need, [True, False, False, False], 584.0)
    assert sum(widths) == pytest.approx(584.0, abs=1)
    assert widths[0] == pytest.approx(48.0) and widths[3] == pytest.approx(70.0)
    assert widths[1] > widths[2]


def test_a_digit_string_counts_as_a_number_column() -> None:
    assert render._is_numberish("194") and render._is_numberish("#80")
    assert render._is_numberish("—") and not render._is_numberish("Urgent")


# ── The tool's new kinds ────────────────────────────────────────────────────


@pytest.mark.parametrize(("kind", "data", "says"), [
    ("stats", {"title": "Week", "tiles": [{"label": "Done", "value": 3}]}, "[Stats: Week]"),
    ("board", {"title": "B", "columns": [{"name": "To do", "cards": [{"title": "T1"}]}]},
     "To do (1): T1"),
    ("timeline", {"title": "M", "events": [{"date": "1 Oct", "title": "BOM",
                                            "state": "done"}]}, "1 Oct BOM (done)"),
    ("agenda", {"title": "Tue", "items": [{"start": "09:00", "end": "10:00",
                                           "title": "Stand-up"}]}, "09:00-10:00 Stand-up"),
    ("gantt", {"title": "P", "rows": [{"label": "Design", "start": "2026-09-01",
                                       "end": "2026-09-20", "progress": 50}]},
     "Design: 2026-09-01 to 2026-09-20, 50%"),
    ("chart", {"type": "donut", "title": "S", "labels": ["Done"], "values": [3]},
     "[Chart, donut: S]"),
])
async def test_each_new_kind_queues_an_image_with_a_rendition(kind, data, says) -> None:
    with wui.whatsapp_run("orchestrator") as run:
        out = await wui.whatsapp_ui(kind, data)
    assert out["ok"] is True, out
    (msg,) = run.outbox
    assert msg.kind == "image" and msg.png.startswith(PNG)
    assert says in msg.rendition
