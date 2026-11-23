"""WS-CAL CE-13e: the live-check script runs its checks on recorded replies.

No request leaves the test. The script itself is operator-run against real
Google and Microsoft (``scripts/calendar_live_check.py``). Here the same check
functions run against a fake HTTP layer, so a change of the script is fenced:

* it refuses without ``--live`` (and without an org and a connection);
* the Google flow expects 409 for a taken client id, and the adopt PATCH to bring
  the event back, and the plain PATCH to leave it cancelled;
* the Graph flow expects the SAME event id for a repeat transactionId;
* each event it made is deleted, also when a check fails;
* no event has attendees, none is busy, and nothing prints a token.

Mutations (R7): make ``_event`` busy, drop the ``finally`` cleanup, or accept a
different id as "same". Each test below turns red.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_calendar_sync_outlook_out import prov as outlook_prov
from tests.unit.test_calendar_sync_read import FakeHttp, answer, cred
from tests.unit.test_calendar_sync_read import provider as google_prov

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "calendar_live_check.py"
_spec = importlib.util.spec_from_file_location("calendar_live_check", _PATH)
live = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["calendar_live_check"] = live  # a dataclass in it needs its module
_spec.loader.exec_module(live)  # type: ignore[union-attr]

NOW = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
CID = "lcabcdefabcdefabcdefabcdef"


def _list(*items: dict) -> dict:
    return {"items": list(items), "nextSyncToken": "t"}


def _confirmed() -> dict:
    return {"id": CID, "status": "confirmed", "summary": "x", "etag": '"1"',
            "start": {"dateTime": "2026-11-22T08:00:00Z"}, "end": {"dateTime": "2026-11-22T09:00:00Z"}}


def _cancelled() -> dict:
    return {"id": CID, "status": "cancelled"}


def _google_http(*, conflict: bool = True, adopt_works: bool = True, plain_revives: bool = False) -> FakeHttp:
    events = "/calendars/cal1/events"
    http = FakeHttp()
    http.on("POST", events, answer({"id": CID, "etag": '"1"'}),
            answer({"error": {"code": 409}}, 409) if conflict else answer({"id": CID}))
    http.on("DELETE", f"{events}/{CID}", answer({}, 204))
    http.on("PATCH", f"{events}/{CID}", answer({"id": CID}))
    # the reads: after the adopt, then after the plain PATCH
    http.on("GET", events,
            answer(_list(_confirmed() if adopt_works else _cancelled())),
            answer(_list(_confirmed() if plain_revives else _cancelled())))
    return http


@pytest.mark.asyncio
async def test_google_every_check_passes_and_the_event_is_deleted() -> None:
    http = _google_http()
    rows = await live.check_google(google_prov(http), cred(), "cal1", now=NOW, client_id=CID)
    assert [r.ok for r in rows] == [True] * 5, live.render(rows)
    assert live.exit_code(rows) == 0
    assert http.calls[-1][0] == "DELETE"  # the cleanup is the last call


@pytest.mark.asyncio
async def test_google_a_missing_409_and_a_revive_that_fails_are_reported() -> None:
    rows = await live.check_google(google_prov(_google_http(conflict=False)), cred(), "cal1",
                                   now=NOW, client_id=CID)
    assert rows[2].ok is False and "no 409" in rows[2].got
    rows = await live.check_google(google_prov(_google_http(adopt_works=False)), cred(), "cal1",
                                   now=NOW, client_id=CID)
    assert rows[3].ok is False and rows[3].got == "still cancelled"
    rows = await live.check_google(google_prov(_google_http(plain_revives=True)), cred(), "cal1",
                                   now=NOW, client_id=CID)
    assert rows[4].ok is False and rows[4].got == "came back"
    assert live.exit_code(rows) == 1 and "CHECK(S) FAILED" in live.render(rows)


@pytest.mark.asyncio
async def test_google_a_provider_error_stops_the_flow_and_still_cleans_up() -> None:
    http = FakeHttp()
    http.on("POST", "/calendars/cal1/events", answer({"error": {"code": 403}}, 403))
    http.on("DELETE", f"/calendars/cal1/events/{CID}", answer({}, 404))
    rows = await live.check_google(google_prov(http), cred(), "cal1", now=NOW, client_id=CID)
    assert len(rows) == 1 and rows[0].ok is False and "forbidden" in rows[0].got
    assert [c[0] for c in http.calls] == ["POST", "DELETE"]  # the cleanup ran


def _graph_http(second_id: str = "A", third_id: str = "B") -> FakeHttp:
    http = FakeHttp()
    http.on("POST", "/calendars/CAL/events", answer({"id": "A"}), answer({"id": second_id}),
            answer({"id": third_id}))
    http.on("DELETE", "/me/events/A", answer({}, 204))
    http.on("DELETE", "/me/events/B", answer({}, 204))
    return http


@pytest.mark.asyncio
async def test_graph_a_repeat_transaction_id_returns_the_same_event() -> None:
    http = _graph_http()
    rows = await live.check_graph(outlook_prov(http), cred(), "CAL", now=NOW, transaction_id="lctx")
    assert rows[0].ok is True and rows[0].got == "same id"
    assert rows[1].ok is None  # information only
    sent = [c[3] for c in http.calls if c[0] == "POST"]
    assert all(b["transactionId"] == "lctx" for b in sent)
    deleted = {c[1].rsplit("/", 1)[-1] for c in http.calls if c[0] == "DELETE"}
    assert deleted == {"A", "B"}  # every event it made is gone


@pytest.mark.asyncio
async def test_graph_a_second_event_or_a_409_fails_the_check() -> None:
    rows = await live.check_graph(outlook_prov(_graph_http(second_id="B")), cred(), "CAL", now=NOW)
    assert rows[0].ok is False and rows[0].got == "a second event"
    http = FakeHttp()
    http.on("POST", "/calendars/CAL/events", answer({"id": "A"}),
            answer({"error": {"code": "ErrorDuplicate"}}, 409))
    http.on("DELETE", "/me/events/A", answer({}, 204))
    rows = await live.check_graph(outlook_prov(http), cred(), "CAL", now=NOW)
    assert rows[0].ok is False and "exists" in rows[0].got


@pytest.mark.asyncio
async def test_no_event_has_attendees_or_busy_time_and_nothing_prints_a_token() -> None:
    http = _google_http()
    rows = await live.check_google(google_prov(http), cred(), "cal1", now=NOW, client_id=CID)
    bodies = [c[3] for c in http.calls if c[0] in ("POST", "PATCH")]
    assert bodies and all("attendees" not in b and b["transparency"] == "transparent" for b in bodies)
    text = live.render(rows) + json.dumps(bodies)
    assert "access_token" not in text and cred().access_token not in text


def test_the_script_refuses_without_live_and_without_the_ids(capsys: Any) -> None:
    for argv in ([], ["--org", "o", "--connection", "c"], ["--live"], ["--live", "--org", "o"],
                 ["--live", "--connection", "c"]):
        assert live.main(argv) == 2
    out = capsys.readouterr().out
    assert out.count("refused") == 5 and "--live" in out
