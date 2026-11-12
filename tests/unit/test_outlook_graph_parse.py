"""CE-10b: the Graph calendar parser, pure functions, recorded replies."""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/services/gateway"))
from gateway.calendar_sync import outlook_graph as g  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "outlook_calendar"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_first_page_has_next_link_and_no_delta_link():
    pg = g.parse_delta_page(load("delta_page1.json"))
    assert pg.next_link and not pg.delta_link
    assert [e.external_id for e in pg.events] == ["EV1", "EV2", "EV3"]


def test_series_instance_free_and_allday():
    by = {e.external_id: e for e in g.parse_delta_page(load("delta_page1.json")).events}
    ev1 = by["EV1"]
    assert ev1.external_series_id == "MASTER1" and ev1.busy and ev1.status == "confirmed"
    assert ev1.starts_at == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    assert ev1.location == "Room 4" and ev1.conference_url.startswith("https://teams")
    assert by["EV2"].busy is False
    ev3 = by["EV3"]
    assert (ev3.start_date, ev3.end_date) == (date(2026, 11, 20), date(2026, 11, 21))
    assert ev3.starts_at is None and ev3.ends_at is None and ev3.tz is None


def test_last_page_delta_link_removed_cancelled_and_skips():
    pg = g.parse_delta_page(load("delta_page2.json"))
    assert pg.delta_link and not pg.next_link
    assert pg.removed_ids == ("EV5",)
    by = {e.external_id: e for e in pg.events}
    assert by["EV4"].status == "cancelled"
    assert by["EV6"].title == "(No title)" and by["EV6"].status == "tentative"
    assert pg.skipped == 1  # the item with no id


def test_merge_pages_and_remove_wins_over_earlier_add():
    p1 = g.parse_delta_page(load("delta_page1.json"))
    p2 = g.parse_delta_page(load("delta_page2.json"))
    res = g.merge_pages([p1, p2])
    assert res.delta_link == p2.delta_link
    assert {e.external_id for e in res.events} == {"EV1", "EV2", "EV3", "EV4", "EV6"}
    # a later removal of EV1 drops it from events
    rm = g.DeltaPage(removed_ids=("EV1",), delta_link="d")
    assert "EV1" not in {e.external_id for e in g.merge_pages([p1, rm]).events}


def test_unknown_time_zone_is_refused():
    ev = {"id": "x", "start": {"dateTime": "2026-01-01T00:00:00", "timeZone": "Pacific Standard Time"},
          "end": {"dateTime": "2026-01-01T01:00:00", "timeZone": "Pacific Standard Time"}}
    with pytest.raises(g.GraphParseError):
        g.parse_event(ev)


def test_long_title_is_cut_to_200():
    ev = {"id": "x", "subject": "a" * 300,
          "start": {"dateTime": "2026-01-01T00:00:00", "timeZone": "UTC"},
          "end": {"dateTime": "2026-01-01T01:00:00", "timeZone": "UTC"}}
    assert len(g.parse_event(ev).title) == 200


def test_skip_rule_for_series_written_by_metorite():
    ev = g.parse_delta_page(load("delta_page1.json")).events[0]
    assert g.skip_series_instance(ev, {"MASTER1"})
    assert not g.skip_series_instance(ev, {"OTHER"})


def test_calendar_picking_leaves_out_metorite_calendar():
    cals = load("calendars.json")
    assert g.pick_calendar_ids(cals) == ["AAA", "BBB"]
    assert g.find_metorite_calendar_id(cals) == "CCC"


def test_delta_url_and_headers():
    s, e = g.window(datetime(2026, 11, 12, tzinfo=timezone.utc))
    url = g.delta_url("AA=/B", s, e)
    assert "AA%3D%2FB/calendarView/delta?startDateTime=2026-10-13T00%3A00%3A00Z" in url
    assert "outlook.timezone=\"UTC\"" in g.read_headers("t")["Prefer"]


def test_event_payload_timed_and_allday():
    t = g.event_payload(title="x", starts_at=datetime(2026, 1, 1, 9, tzinfo=timezone.utc),
                        ends_at=datetime(2026, 1, 1, 10, tzinfo=timezone.utc), busy=False)
    assert t["isAllDay"] is False and t["showAs"] == "free"
    assert t["start"] == {"dateTime": "2026-01-01T09:00:00", "timeZone": "UTC"}
    a = g.event_payload(title="x", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2))
    assert a["isAllDay"] is True
    with pytest.raises(ValueError):
        g.event_payload(title="x")


def test_delta_expired():
    assert g.delta_expired(410, None)
    assert g.delta_expired(400, {"error": {"code": "syncStateNotFound"}})
    assert not g.delta_expired(500, None)
