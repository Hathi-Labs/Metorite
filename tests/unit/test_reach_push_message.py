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
    # RM-2b moved the kinds into the needs path, with no re-export from feed.py.
    from gateway.routes.shell.needs import REMINDER_KINDS

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


# ── The morning summary (RX-5, calendar_events_reminders.md §5.11) ──────────


@pytest.mark.parametrize("today,late,title", [
    (3, 1, "3 due today, 1 overdue"),
    (3, 0, "3 due today"),
    (0, 1, "1 overdue"),
])
def test_the_title_gives_the_counts_and_leaves_out_a_zero(today, late, title) -> None:
    assert push_message.due_title(today, late) == title


def test_the_summary_gives_the_first_title_and_the_rest() -> None:
    msg = push_message.build_due(due_today=3, overdue=1, first_title="  Call   the buyer ",
                                 org_name=ORG, hide_details=False)
    assert isinstance(msg, PushMessage)
    assert msg.title == "3 due today, 1 overdue"
    assert msg.body == "Call the buyer and 3 more"
    assert (msg.url, msg.tag, msg.urgency) == ("/", push_message.DUE_TAG, "normal")
    assert msg.ttl == push_message.ttl_of(push_message.DUE_SUMMARY) == 4 * 3600
    assert json.loads(msg.payload())["url"] == "/"


def test_one_task_has_no_and_more() -> None:
    msg = push_message.build_due(due_today=1, overdue=0, first_title="Call the buyer",
                                 org_name=ORG, hide_details=False)
    assert msg.body == "Call the buyer"


def test_hidden_details_keep_the_counts_and_drop_the_title() -> None:
    msg = push_message.build_due(due_today=2, overdue=1, first_title="Call the buyer",
                                 org_name=ORG, hide_details=True)
    assert msg.title == "2 due today, 1 overdue"
    assert msg.body == ORG
    assert "buyer" not in msg.payload()
    empty = push_message.build_due(due_today=1, overdue=0, first_title="x", org_name=" ",
                                   hide_details=True)
    assert empty.body == "Metorite"


def test_a_long_title_is_cut_before_the_rest() -> None:
    msg = push_message.build_due(due_today=2, overdue=0, first_title="x" * 200, org_name=ORG,
                                 hide_details=False)
    assert msg.body.endswith("… and 1 more")
    assert len(msg.body) == push_message.TITLE_CLIP + len(" and 1 more")


def test_a_read_at_its_cap_says_a_floor_and_never_a_false_split() -> None:
    msg = push_message.build_due(due_today=1, overdue=199, first_title="Old task", org_name=ORG,
                                 hide_details=False, capped=True)
    assert msg.title == "200+ due or overdue"
    assert msg.body == "Old task and 199+ more"


def test_the_summary_is_no_reminder_kind() -> None:
    assert push_message.DUE_SUMMARY not in push_message.KINDS
