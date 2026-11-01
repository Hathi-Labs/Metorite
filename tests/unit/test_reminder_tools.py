"""WS-66 RM-3 — the reminder tools of the Projects chat (D114).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.8 and §10.1.

The done-when of RM-3: "Remind me at 5 to get a haircut" saves ONE row at
17:00 in the member's zone, and the reply quotes that time and zone. A time
with no offset is refused. Read with the RM-3 audit, that is two rules:

1. **The ROUTE refuses a naive time** (unchanged since RM-1).
2. **The TOOL attaches the member's SAVED zone** to a wall time before it
   calls the route, or refuses when no zone is saved. It never guesses one.

And more that this file holds:

3. **The reply quotes the SAVED row.** The time and the zone come from the
   route's answer (``reads.saved_when``), never from the tool's argument.
4. **Each write asks first.** One card through ``writes._confirm``, and no
   write before the member's yes. ``test_projects_agent_writes.py`` holds
   that for every class B and C tool; the cases here pin the reminder words.
5. **Two gates before any card** (review round 1). With
   ``CALENDAR_REMINDERS`` off the routes answer 404, and a run with no card
   channel (WhatsApp) cannot ask. Either way the tool refuses before the card
   and tells the model to make no task in place of the reminder.
6. **A reminder is not a task.** Its receipt prints ``reminder_id:``, never
   ``full_id:``, because a ``full_id:`` line opens a task in the app. And the
   member's receipt shows no line meant for the model.
7. **The clock is real.** A date or an hour that does not exist is refused,
   a time in a gap of the clock shows its real time on the card and in the
   receipt, "morning" is the member's ``day_start_hour``, and a saved zone of
   exactly UTC gets a note on the card.

Red first (R7), each run against the built code and then reverted:

* M1. ``writes.set_reminder``: drop the ``_confirm`` call ->
  ``test_a_denied_card_writes_nothing[set_reminder]`` and
  ``test_every_class_b_tool_awaits_the_one_confirm_door`` fail.
* M2. ``writes._reminder_receipt``: quote the time that the tool made from
  its own argument (``when.shown``) instead of ``saved_when(row)`` ->
  ``test_the_reply_quotes_the_route_answer_not_the_input`` and two more
  fail.
* M3. ``writes._reminder_gate``: drop ``reminders_off`` ->
  ``test_with_the_flag_off_a_write_shows_no_card`` fails for each write.
* M4. ``writes._reminder_gate``: drop ``no_card_channel`` ->
  ``test_a_whatsapp_run_refuses_before_the_card`` fails for each write.

No database and no gateway process: the client is patched at httpx.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import reads as R
from skill_projects import writes as W

from tests.unit._projects_agent_fakes import (
    FakeResponse,
    approve,
    deny,
    fake_gateway,
    writes,
)

REM = "e1f8fad5-d9cb-469f-a165-70867728950e"
TASK_ID = "1f8fad5b-d9cb-469f-a165-70867728950e"
BASE = "/projects/my/reminders"
KOLKATA = "Asia/Kolkata"


def _tomorrow(zone: str = KOLKATA) -> str:
    """Tomorrow's date in *zone*, so a 17:00 on it is always in the future."""
    return (datetime.now(ZoneInfo(zone)) + timedelta(days=1)).date().isoformat()


def _row(**over: Any) -> dict[str, Any]:
    """One reminder as the RM-1 route answers it (``reminders._out``)."""
    row: dict[str, Any] = {
        "id": REM,
        "title": "Get a haircut",
        "notes": None,
        "kind": "action",
        "subject_kind": None,
        "subject_id": None,
        "fire_at": "2099-10-30T11:30:00+00:00",
        "tz": KOLKATA,
        "state": "scheduled",
        "snoozes": 0,
        "important": False,
        "resolution": None,
        "promoted_task_id": None,
    }
    row.update(over)
    return row


def _as_utc(value: str) -> str:
    """What the route stores: the instant in UTC (``reminders._instant``)."""
    return datetime.fromisoformat(value).astimezone(UTC).isoformat()


def _member_view(result: str) -> str:
    """What the member's receipt shows of a result: the client drops each line
    that starts with the legend and each ``<kind>_id:`` line
    (``ProjectToolCards.forPeopleFenced``)."""
    return "\n".join(
        line for line in result.split("\n")
        if not line.startswith(R.DATA_LEGEND[:40]) and not re.match(r"^\s*[a-z_]+_id:", line)
    )


class Gateway:
    """The member's zone, their reminders, and the RM-1 routes, recorded.

    ``today`` is the answer of ``GET /projects/my/today``. ``saved`` changes
    what the create route answers, so a test can make the server store a time
    that the tool did not send. ``off`` is ``CALENDAR_REMINDERS`` off: every
    reminder route answers 404 (``reminders._require_on``).
    """

    def __init__(
        self,
        *,
        zone: str | None = KOLKATA,
        stored: bool = True,
        valid: bool = True,
        day_start: int | None = 8,
        off: bool = False,
        rows: list[dict[str, Any]] | None = None,
        saved: dict[str, Any] | None = None,
        refuse: dict[str, FakeResponse] | None = None,
    ) -> None:
        self.today: dict[str, Any] = {
            "today": datetime.now(UTC).date().isoformat(),
            "timezone": zone or "UTC",
            "stored": stored,
            "valid": valid,
        }
        if day_start is not None:
            self.today["day_start_hour"] = day_start
        self.off = off
        self.rows = rows if rows is not None else [_row()]
        self.saved = saved or {}
        self.refuse = refuse or {}

    def __call__(self, call: dict) -> Any:
        path, method = call["path"], call["method"]
        body = call["json"] if isinstance(call["json"], dict) else {}
        if path in self.refuse:
            return self.refuse[path]
        if path == "/projects/my/today":
            return self.today
        if self.off and path.startswith(BASE):
            return FakeResponse({"detail": "Not Found"}, 404)
        if path == BASE and method == "GET":
            state = (call["params"] or {}).get("state")
            rows = [r for r in self.rows if state is None or r["state"] == state]
            if state is None:
                rows = [r for r in rows if r["state"] in ("scheduled", "due", "unconfirmed")]
            return {"rows": rows, "total": len(rows)}
        if path == BASE and method == "POST":
            out = {**_row(), **body, "fire_at": _as_utc(body["fire_at"]), "tz": body.get("tz")}
            return {**out, **self.saved}
        if method == "PATCH":
            out = {**self.rows[0], **body}
            if "fire_at" in body:
                out["fire_at"] = _as_utc(body["fire_at"])
            return out
        tail = path.rsplit("/", 1)[-1]
        if tail in ("snooze", "reschedule"):
            return {**self.rows[0], "fire_at": _as_utc(body["fire_at"]),
                    "tz": body.get("tz", self.rows[0].get("tz")), "snoozes": 1}
        if tail == "resolve":
            state = "done" if body.get("action") == "done" else "dropped"
            return {**self.rows[0], "state": state, "resolution": state}
        if tail == "reopen":
            return {**self.rows[0], "state": "scheduled", "resolution": None}
        if tail == "promote":
            return {**self.rows[0], "state": "done", "resolution": "promoted",
                    "promoted_task_id": TASK_ID}
        raise AssertionError(f"the fake gateway has no answer for {method} {path}")


def _posts(calls: list[dict], path: str) -> list[dict]:
    return [c for c in writes(calls) if c["path"] == path]


#: One call of each write. A done or a drop is reopened, so its fake row is done.
WRITES: list[tuple[str, dict[str, Any]]] = [
    ("set_reminder", {"title": "Get a haircut", "at": "2099-10-30 17:00"}),
    ("update_reminder", {"reminder_id": REM, "title": "Get a short haircut"}),
    ("resolve_reminder", {"reminder_id": REM}),
    ("reopen_reminder", {"reminder_id": REM}),
    ("reschedule_reminder", {"reminder_id": REM, "at": "2099-11-02 10:00"}),
    ("promote_reminder", {"reminder_id": REM}),
]


def _fake_for(tool: str, **kw: Any) -> Gateway:
    rows = [_row(state="done", resolution="done")] if tool == "reopen_reminder" else None
    return Gateway(rows=rows, **kw)


# ── 1. The route refuses a naive time (unchanged) ──────────────────────────


def test_the_route_still_refuses_a_time_with_no_offset() -> None:
    pytest.importorskip("gateway.routes.projects", reason="gateway not installed")
    from fastapi import HTTPException
    from gateway.routes.projects import reminders as rm

    with pytest.raises(HTTPException) as refused:
        rm._instant("2026-10-31 09:00", "fire_at")
    assert refused.value.status_code == 422
    assert "offset" in str(refused.value.detail)


# ── 2. The tool attaches the member's saved zone ───────────────────────────


async def test_remind_me_at_5_saves_one_row_at_17_in_my_zone_and_quotes_it(monkeypatch) -> None:
    """THE done-when of RM-3."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    day = _tomorrow()
    out = await skill_projects.set_reminder("Get a haircut", at=f"{day} 17:00")
    posted = _posts(calls, BASE)
    assert len(posted) == 1, "one reminder is one row"
    body = posted[0]["json"]
    assert body["fire_at"] == f"{day}T17:00:00+05:30"
    assert body["tz"] == KOLKATA
    assert body["title"] == "Get a haircut" and body["kind"] == "action"
    # The reply states the time and the zone of the SAVED row.
    assert "17:00 IST" in out
    assert f"reminder_id: {REM}" in out
    assert "full_id:" not in out
    # The card named the same time before the write.
    assert "17:00 IST" in asked[0]["context"] and "Asia/Kolkata" in asked[0]["context"]


async def test_a_wall_time_with_no_saved_zone_is_refused_and_nothing_is_asked(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(zone=None, stored=False))
    out = await skill_projects.set_reminder("Get a haircut", at=f"{_tomorrow()} 17:00")
    assert out == R.NO_ZONE
    assert asked == [] and writes(calls) == []


async def test_a_saved_zone_that_is_not_valid_is_no_zone(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(zone="UTC", valid=False))
    out = await skill_projects.set_reminder("Get a haircut", at=f"{_tomorrow()} 17:00")
    assert out == R.NO_ZONE
    assert asked == [] and writes(calls) == []


async def test_a_failed_zone_read_is_no_zone_never_utc(monkeypatch) -> None:
    """``writes._member_clock`` falls back to UTC. A reminder must not."""
    asked = approve(monkeypatch)
    down = FakeResponse({"detail": "Not Found"}, 404)
    calls = fake_gateway(monkeypatch, Gateway(refuse={"/projects/my/today": down}))
    out = await skill_projects.set_reminder("Get a haircut", at=f"{_tomorrow()} 17:00")
    assert out == R.NO_ZONE
    assert asked == [] and writes(calls) == []


async def test_a_time_with_an_offset_and_no_saved_zone_saves_and_says_utc(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(zone=None, stored=False))
    at = f"{_tomorrow()}T17:00:00+05:30"
    out = await skill_projects.set_reminder("Get a haircut", at=at)
    body = _posts(calls, BASE)[0]["json"]
    assert body["fire_at"] == at and "tz" not in body
    assert "11:30 UTC" in out and "no zone is saved" in out


async def test_a_wall_time_takes_the_offset_of_its_own_date(monkeypatch) -> None:
    """New York is -04:00 in July and -05:00 in December: the zone, not a
    fixed offset, places the time."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(zone="America/New_York"))
    await skill_projects.set_reminder("Summer", at="2099-07-01 09:00")
    await skill_projects.set_reminder("Winter", at="2099-12-01 09:00")
    sent = [c["json"]["fire_at"] for c in _posts(calls, BASE)]
    assert sent == ["2099-07-01T09:00:00-04:00", "2099-12-01T09:00:00-05:00"]


async def test_a_time_in_the_gap_of_the_clock_shows_one_time_on_card_and_receipt(
    monkeypatch,
) -> None:
    """02:30 on 14 March 2027 does not exist in New York: the clock jumps from
    02:00 to 03:00. The tool sends the real instant, and the card and the
    receipt both say 03:30 EDT."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(zone="America/New_York"))
    out = await skill_projects.set_reminder("Call home", at="2027-03-14 02:30")
    assert _posts(calls, BASE)[0]["json"]["fire_at"] == "2027-03-14T03:30:00-04:00"
    assert "Sun 14 Mar 2027, 03:30 EDT" in asked[0]["context"]
    assert "Sun 14 Mar 2027, 03:30 EDT" in out


@pytest.mark.parametrize("wall", ["2099-02-30 09:00", "2026-10-31 24:00", "2099-13-01 09:00"])
async def test_a_date_or_hour_that_does_not_exist_is_refused_never_raised(
    wall: str, monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    for call in (
        skill_projects.set_reminder("Get a haircut", at=wall),
        skill_projects.update_reminder(REM, at=wall),
        skill_projects.reschedule_reminder(REM, at=wall),
    ):
        out = await call
        assert "is not a real date and time" in out, out
    assert asked == [] and writes(calls) == []


async def test_morning_is_the_hour_my_day_starts(monkeypatch) -> None:
    """"Tomorrow morning" with no hour: the member's ``day_start_hour``."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(day_start=8))
    await skill_projects.set_reminder("Call the bank", at="2099-10-31 morning")
    assert _posts(calls, BASE)[0]["json"]["fire_at"] == "2099-10-31T08:00:00+05:30"


async def test_morning_with_no_day_start_from_the_gateway_is_refused(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(day_start=None))
    out = await skill_projects.set_reminder("Call the bank", at="2099-10-31 morning")
    assert "cannot read the hour your day starts" in out
    assert asked == [] and writes(calls) == []


async def test_a_saved_zone_of_utc_saves_with_a_note_on_the_card(monkeypatch) -> None:
    """UTC is the column default, so it may not be the member's choice. The
    card says so in one line, and the tool still saves."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(zone="UTC"))
    await skill_projects.set_reminder("Get a haircut", at="2099-10-30 17:00")
    assert f"note: «{R.UTC_NOTE}»" in asked[0]["context"]
    assert _posts(calls, BASE)[0]["json"]["tz"] == "UTC"
    asked.clear()
    fake_gateway(monkeypatch, Gateway())
    await skill_projects.set_reminder("Get a haircut", at="2099-10-30 17:00")
    assert R.UTC_NOTE not in asked[0]["context"]


async def test_a_time_in_the_past_is_refused_with_the_time_now(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    yesterday = (datetime.now(ZoneInfo(KOLKATA)) - timedelta(days=1)).date().isoformat()
    out = await skill_projects.set_reminder("Get a haircut", at=f"{yesterday} 17:00")
    assert "is in the past" in out and "It is now" in out and "IST" in out
    assert asked == [] and writes(calls) == []


async def test_in_minutes_is_a_time_from_now(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    before = datetime.now(UTC)
    await skill_projects.set_reminder("Stretch", in_minutes=20)
    sent = datetime.fromisoformat(_posts(calls, BASE)[0]["json"]["fire_at"])
    assert timedelta(minutes=19) < sent - before < timedelta(minutes=21)


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({"at": ""}, "needs a time"),
        ({"at": "2099-10-30 17:00", "in_minutes": 20}, "not both"),
        ({"at": "2099-10-30"}, "a date and a time"),
        ({"at": "at five"}, "a date and a time"),
        ({"at": "smart"}, "only a move takes it"),
        ({"at": "2099-10-30 17:00", "kind": "alarm"}, "action or notice"),
        ({"at": "2099-10-30 17:00", "subject_kind": "task"}, "both"),
        ({"at": "2099-10-30 17:00", "estimate_minutes": 99999}, "estimate_minutes"),
    ],
)
async def test_a_bad_argument_is_refused_before_the_card(
    kwargs: dict[str, Any], words: str, monkeypatch,
) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    out = await skill_projects.set_reminder("Get a haircut", **kwargs)
    assert words in out, out
    assert asked == [] and writes(calls) == []


# ── 3. The reply quotes the SAVED row ──────────────────────────────────────


async def test_the_reply_quotes_the_route_answer_not_the_input(monkeypatch) -> None:
    """The server stored 18:00 London time, and the tool sent 17:00 Kolkata
    time. The reply states what was stored. Mutation M2 fails here."""
    approve(monkeypatch)
    saved = {"fire_at": "2099-10-30T17:00:00+00:00", "tz": "Europe/London"}
    fake_gateway(monkeypatch, Gateway(saved=saved))
    out = await skill_projects.set_reminder("Get a haircut", at="2099-10-30 17:00")
    assert "17:00 GMT" in out, out
    assert "IST" not in out and "22:30" not in out


async def test_a_reply_with_no_id_never_says_saved(monkeypatch) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, Gateway(saved={"id": None}))
    out = await skill_projects.set_reminder("Get a haircut", at="2099-10-30 17:00")
    assert "Do not tell the member it is saved" in out
    assert "Do not tell" not in _member_view(out)
    assert "reminder_id:" not in out


def test_the_quote_format_is_day_date_time_and_zone() -> None:
    assert R.when_text("2099-10-30T03:30:00+00:00", KOLKATA) == "Fri 30 Oct 2099, 09:00 IST"
    assert R.when_text("2099-10-30T03:30:00+00:00", None) == "Fri 30 Oct 2099, 03:30 UTC"
    # A zone whose short name is a bare offset shows its IANA name.
    assert R.when_text("2099-10-30T05:00:00+00:00", "Asia/Dubai").endswith("09:00 Asia/Dubai")
    this_year = datetime.now(ZoneInfo(KOLKATA)).year
    assert str(this_year) not in R.when_text(f"{this_year}-06-01T03:30:00+00:00", KOLKATA)


@pytest.mark.parametrize(("tool", "kwargs"), WRITES)
async def test_the_members_receipt_shows_no_line_for_the_model(
    tool: str, kwargs: dict, monkeypatch,
) -> None:
    """The guidance for the model rides a legend line, which the receipt
    drops. The model still reads it."""
    approve(monkeypatch)
    fake_gateway(monkeypatch, _fake_for(tool))
    out = await getattr(skill_projects, tool)(**kwargs)
    shown = _member_view(out)
    assert "Tell the member" not in shown and "reopen_reminder" not in shown, shown
    if tool != "promote_reminder":
        assert W.QUOTE_THE_TIME in out
    if tool == "resolve_reminder":
        assert "reopen_reminder undoes this answer" in out


# ── 4. Each write asks first ───────────────────────────────────────────────


@pytest.mark.parametrize(("tool", "kwargs"), WRITES)
async def test_a_declined_card_writes_nothing(tool: str, kwargs: dict, monkeypatch) -> None:
    asked = deny(monkeypatch)
    calls = fake_gateway(monkeypatch, _fake_for(tool))
    out = await getattr(skill_projects, tool)(**kwargs)
    assert out == W.CANCELLED
    assert len(asked) == 1 and writes(calls) == []


async def test_reopen_asks_first_and_reads_the_closed_lists(monkeypatch) -> None:
    done = _row(state="done", resolution="done")
    asked = deny(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(rows=[done]))
    assert await skill_projects.reopen_reminder(REM) == W.CANCELLED
    assert len(asked) == 1 and writes(calls) == []
    states = [c["params"].get("state") for c in calls if c["path"] == BASE and "state" in c["params"]]
    assert states == ["done"], "the done list held it, so the dropped list is not read"


async def test_a_promoted_reminder_cannot_reopen_and_no_card_is_shown(monkeypatch) -> None:
    promoted = _row(state="done", resolution="promoted", promoted_task_id=TASK_ID)
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(rows=[promoted]))
    out = await skill_projects.reopen_reminder(REM)
    assert "cannot reopen" in out
    assert asked == [] and writes(calls) == []


async def test_a_reminder_that_is_not_mine_is_refused_before_the_card(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway(rows=[]))
    out = await skill_projects.resolve_reminder(REM)
    assert "no open reminder with this id" in out
    assert asked == [] and writes(calls) == []


async def test_a_full_list_names_the_id_only_and_lets_the_route_decide(monkeypatch) -> None:
    """More reminders than one page: a miss may still exist. The card names
    the id only, claims no old value, and the route decides."""

    class Full(Gateway):
        def __call__(self, call: dict) -> Any:
            if call["path"] == BASE and call["method"] == "GET":
                return {"rows": [], "total": 900}
            return super().__call__(call)

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Full())
    await skill_projects.update_reminder(REM, title="Get a short haircut")
    assert "cannot show" in asked[0]["detail"]
    assert "→" not in asked[0]["context"]
    assert _posts(calls, f"{BASE}/{REM}")[0]["json"] == {"title": "Get a short haircut"}


async def test_an_update_shows_the_old_and_the_new_time(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    out = await skill_projects.update_reminder(REM, at="2099-10-31 09:00", clear="notes")
    body = _posts(calls, f"{BASE}/{REM}")[0]["json"]
    assert body == {"fire_at": "2099-10-31T09:00:00+05:30", "tz": KOLKATA, "notes": None}
    context = asked[0]["context"]
    assert "Fri 30 Oct 2099, 17:00 IST» → «Sat 31 Oct 2099, 09:00 IST" in context
    assert "Sat 31 Oct 2099, 09:00 IST" in out


async def test_resolve_sends_the_answer_and_says_how_to_undo(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    out = await skill_projects.resolve_reminder(REM, answer="drop")
    assert _posts(calls, f"{BASE}/{REM}/resolve")[0]["json"] == {"action": "drop"}
    assert out.startswith("Dropped:") and "reopen_reminder" in out
    refused = await skill_projects.resolve_reminder(REM, answer="promote")
    assert "promote_reminder" in refused


async def test_a_snooze_and_a_named_time_go_to_their_own_routes(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    await skill_projects.reschedule_reminder(REM, in_minutes=60, snooze="yes")
    await skill_projects.reschedule_reminder(REM, at="2099-11-02 10:00")
    assert [c["path"].rsplit("/", 1)[-1] for c in writes(calls)] == ["snooze", "reschedule"]
    assert writes(calls)[1]["json"] == {"fire_at": "2099-11-02T10:00:00+05:30", "tz": KOLKATA}


async def test_smart_passes_through_and_a_422_says_nothing_moved(monkeypatch) -> None:
    """RM-2 adds the smart slot to the route in parallel. Until then the
    route answers 422, and the reply says so."""
    approve(monkeypatch)
    no_slot = FakeResponse({"detail": "fire_at: the smart slot is not built yet (RM-2)."}, 422)
    calls = fake_gateway(monkeypatch, Gateway(refuse={f"{BASE}/{REM}/reschedule": no_slot}))
    out = await skill_projects.reschedule_reminder(REM, at="smart")
    assert writes(calls)[0]["json"]["fire_at"] == "smart"
    assert "nothing moved" in out and "Name a time" in out


async def test_smart_and_a_far_time_are_refused_for_a_snooze_before_the_card(
    monkeypatch,
) -> None:
    """``/snooze`` refuses ``smart`` and a time past 366 days, so the tool
    does too, and the member sees no card that the route then refuses."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    smart = await skill_projects.reschedule_reminder(REM, at="smart", snooze="yes")
    far = await skill_projects.reschedule_reminder(REM, at="2099-11-02 10:00", snooze="yes")
    patched = await skill_projects.update_reminder(REM, at="smart")
    assert "only a move takes it" in smart and "only a move takes it" in patched
    assert "at most 366 days" in far
    assert asked == [] and writes(calls) == []


async def test_the_promote_prints_the_task_it_made_and_the_reminder(monkeypatch) -> None:
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    out = await skill_projects.promote_reminder(REM)
    assert _posts(calls, f"{BASE}/{REM}/promote")
    assert asked[0]["context"].split("\n")[1].startswith("impact: 1 private task")
    # The task opens in Projects: it is the one full_id of a reminder reply.
    assert f"full_id: {TASK_ID}" in out and f"reminder_id: {REM}" in out


# ── 5. The two gates: the flag, and the card channel ───────────────────────


@pytest.mark.parametrize(("tool", "kwargs"), WRITES)
async def test_with_the_flag_off_a_write_shows_no_card(
    tool: str, kwargs: dict, monkeypatch,
) -> None:
    """``CALENDAR_REMINDERS`` off: the routes answer 404. The tool refuses
    before any card, writes nothing, and sends the model to a private task
    with a block. Mutation M3 fails here."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _fake_for(tool, off=True))
    out = await getattr(skill_projects, tool)(**kwargs)
    assert R.REMINDERS_OFF in out and R.FALLBACK in out, out
    assert R.FALLBACK not in _member_view(out), "the tool name is for the model only"
    assert asked == [] and writes(calls) == []


async def test_with_the_flag_off_the_list_says_so(monkeypatch) -> None:
    fake_gateway(monkeypatch, Gateway(off=True))
    out = await skill_projects.list_reminders()
    assert R.REMINDERS_OFF in out and R.FALLBACK in out


@pytest.mark.parametrize("cards_refused", [True, False])
@pytest.mark.parametrize(("tool", "kwargs"), WRITES)
async def test_a_whatsapp_run_refuses_before_the_card(
    tool: str, kwargs: dict, cards_refused: bool, monkeypatch,
) -> None:
    """No reminder act is in ``whatsapp_acts.ALLOWED_ACTS`` until RM-9, so a
    WhatsApp run cannot confirm one. The tool refuses before any read and any
    card. Mutation M4 fails here."""
    import contextlib

    from acb_skills import ask_tools
    from acb_skills import whatsapp_ui as wui

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _fake_for(tool))
    scope = ask_tools.refuse_cards() if cards_refused else contextlib.nullcontext()
    with scope, wui.whatsapp_run("orchestrator", writes=True):
        out = await getattr(skill_projects, tool)(**kwargs)
    assert R.REMINDERS_NO_CARD in out and R.FALLBACK in out, out
    assert asked == [] and calls == []


async def test_a_run_with_no_card_channel_refuses_too(monkeypatch) -> None:
    from acb_skills import ask_tools

    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, Gateway())
    with ask_tools.refuse_cards():
        out = await skill_projects.set_reminder("Get a haircut", at="2099-10-30 17:00")
    assert R.REMINDERS_NO_CARD in out
    assert asked == [] and calls == []


async def test_an_allowed_act_passes_the_card_gate(monkeypatch) -> None:
    """RM-9 adds the reminder acts to ``ALLOWED_ACTS``. Then the gate lets the
    tool reach its card, with no change in the skill."""
    from acb_skills import whatsapp_acts as wacts
    from acb_skills import whatsapp_ui as wui

    monkeypatch.setitem(wacts.ALLOWED_ACTS, "set_reminder", "skill_projects.writes")
    with wui.whatsapp_run("orchestrator", writes=True):
        assert R.no_card_channel("set_reminder") == ""
        assert R.no_card_channel("update_reminder") != ""


def test_a_refused_reminder_never_becomes_a_task() -> None:
    """Owner, 2026-11-01: a task and a reminder are two different things.

    Mutation: put "make a private task" back in ``FALLBACK`` or in the
    ``set_reminder`` description, and this test fails.
    """
    told = " ".join(R.FALLBACK.split()).lower()
    assert "create_personal_task" not in told
    assert "do not make a task" in told
    doc = " ".join((skill_projects.set_reminder.__doc__ or "").split()).lower()
    assert "private task with a block" not in doc
    assert "make no task in its place" in doc
    task_doc = " ".join((skill_projects.create_personal_task.__doc__ or "").split())
    assert "with or without a time: use set_reminder" in task_doc


# ── 6. The read ────────────────────────────────────────────────────────────


async def test_the_list_states_my_time_and_prints_reminder_ids(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, Gateway())
    out = await skill_projects.list_reminders()
    assert "Your time now:" in out and "(Asia/Kolkata)" in out
    assert "Your day starts at 08:00." in out
    assert "«Get a haircut» · Fri 30 Oct 2099, 17:00 IST · scheduled" in out
    assert f"reminder_id: {REM}" in out and "full_id:" not in out
    assert writes(calls) == []


async def test_the_list_reads_a_start_date_as_my_midnight(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, Gateway())
    await skill_projects.list_reminders(start="2099-10-30", end="2099-10-31 12:00")
    params = next(c["params"] for c in calls if c["path"] == BASE)
    assert params["start"] == "2099-10-30T00:00:00+05:30"
    assert params["end"] == "2099-10-31T12:00:00+05:30"


async def test_an_end_date_includes_that_whole_day(monkeypatch) -> None:
    """An end date is the midnight that starts the NEXT day, as the
    ``calendar`` tool reads it. So start and end of one date read that day."""
    calls = fake_gateway(monkeypatch, Gateway())
    today = date.today().isoformat()
    out = await skill_projects.list_reminders(start=today, end=today)
    params = next(c["params"] for c in calls if c["path"] == BASE)
    next_day = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    assert params["start"] == f"{today}T00:00:00+05:30"
    assert params["end"] == f"{next_day}T00:00:00+05:30"
    assert "My reminders" in out


async def test_the_list_refuses_a_date_when_no_zone_is_saved(monkeypatch) -> None:
    from skill_projects.client import GatewayRefusal

    calls = fake_gateway(monkeypatch, Gateway(zone=None, stored=False))
    with pytest.raises(GatewayRefusal, match="No time zone is saved"):
        await skill_projects.list_reminders(start="2099-10-30")
    assert not [c for c in calls if c["path"] == BASE]


# ── 7. The model is told what a reminder is ────────────────────────────────


def test_the_descriptions_tell_the_model_the_rules() -> None:
    doc = " ".join((skill_projects.set_reminder.__doc__ or "").split())
    assert "remind me to" in doc and "NOT a task" in doc
    assert "quote the time and the zone" in doc
    assert "best guess" in doc and "one correction" in doc
    assert "YYYY-MM-DD morning" in doc and "07:00" not in doc
    assert "create_personal_task" in doc
