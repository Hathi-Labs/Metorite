"""The reminder push message (WS-66 RE-9a).

Spec: ``calendar_events_reminders.md`` §5.6 "Settled 2026-10-30 (RE-9)",
decision 4. ``gateway.reach.push_message.build`` is pure: no send, no database.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from gateway.push.sender import PushMessage
from gateway.reach import push_message
from gateway.reach.push_message import HIDDEN_TITLE, build

RID = "6f1c2b9e-0000-4000-8000-00000000abcd"
# 2026-11-02 04:00 UTC is 9:30 AM in Kolkata and 11:00 PM (the day before) in New York.
FIRE = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
ROW = {"id": RID, "title": "Call the Acme buyer", "fire_at": FIRE, "tz": "Asia/Kolkata"}
ORG = "Fracktal Works"


def test_the_kinds_are_the_feed_s_three() -> None:
    from gateway.routes.shell.feed import REMINDER_KINDS

    assert push_message.KINDS == REMINDER_KINDS


@pytest.mark.parametrize("kind,urgency,ttl", [
    ("reminder_due", "high", 3600),
    ("reminder_check", "normal", 4 * 3600),
    ("reminder_unconfirmed", "low", 12 * 3600),
])
def test_each_kind_gives_the_exact_message(kind: str, urgency: str, ttl: int) -> None:
    msg = build(kind, ROW, ORG, hide_details=False)
    assert isinstance(msg, PushMessage)
    assert msg.title == "Call the Acme buyer at 9:30 AM"
    assert msg.url == f"/chat?reminder={RID}"
    assert msg.tag == RID
    assert (msg.urgency, msg.ttl) == (urgency, ttl)


def test_the_time_is_in_the_zone_of_the_row() -> None:
    ny = build("reminder_due", {**ROW, "tz": "America/New_York"}, ORG, False)
    assert ny.title == "Call the Acme buyer at 11:00 PM"
    noon = build("reminder_due", {**ROW, "fire_at": "2026-11-02T06:30:00+00:00",
                                  "tz": "UTC"}, ORG, False)
    assert noon.title.endswith("at 6:30 AM")
    midnight = build("reminder_due", {**ROW, "fire_at": datetime(2026, 11, 2, 0, 5, tzinfo=UTC),
                                      "tz": "UTC"}, ORG, False)
    assert midnight.title.endswith("at 12:05 AM")


def test_the_payload_names_the_org_in_the_body() -> None:
    for hide in (False, True):
        msg = build("reminder_due", ROW, ORG, hide_details=hide)
        assert msg.body == ORG
        assert json.loads(msg.payload())["body"] == ORG


def test_hidden_details_leave_no_title_or_time_in_the_payload() -> None:
    msg = build("reminder_due", ROW, ORG, hide_details=True)
    assert msg.title == HIDDEN_TITLE == "Metorite: a reminder"
    assert msg.body == ORG
    raw = msg.payload()
    data = json.loads(raw)
    assert data == {"title": "Metorite: a reminder", "body": ORG,
                    "url": f"/chat?reminder={RID}", "tag": RID}
    for secret in ("Acme", "buyer", "9:30", "AM", "PM"):
        assert secret not in raw
    # The same holds for the other two kinds.
    for kind in ("reminder_check", "reminder_unconfirmed"):
        assert "Acme" not in build(kind, ROW, ORG, True).payload()


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(KeyError):
        build("reminder_gone", ROW, ORG, False)


def test_a_long_title_is_clipped_and_a_bad_zone_falls_back_to_utc() -> None:
    msg = build("reminder_due", {**ROW, "title": "x" * 200, "tz": "Not/AZone"}, ORG, False)
    assert len(msg.title) < 100
    assert msg.title.endswith("at 4:00 AM")


def test_the_payload_url_passes_the_sender_s_path_check() -> None:
    # `PushMessage.payload` raises on a url that is not a path on this app.
    assert build("reminder_due", ROW, ORG, False).payload()
