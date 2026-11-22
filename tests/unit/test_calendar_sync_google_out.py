"""WS-CAL CE-13: the Google write path carries a client id and names a taken id.

Recorded replies only. No request leaves the test. The R8 suite
``test_calendar_sync_tasks.py`` fences the task logic. This file fences the two
provider facts it relies on:

* an INSERT sends ``OutEvent.client_id`` as the event ``id``, so a retry after
  a timeout names the same event;
* HTTP 409 (the id is taken) is ``ProviderError("exists")``, which the task
  writer adopts.

Mutation (R7): drop the ``client_id`` line of ``google._out_body``, or the
409 branch of ``_call``. Each test below turns red.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from gateway.calendar_sync.provider import OutEvent, ProviderError

from tests.unit.test_calendar_sync_read import NOW, FakeHttp, answer, cred, provider


@pytest.mark.asyncio
async def test_an_insert_sends_the_client_id_as_the_event_id() -> None:
    http = FakeHttp().on("POST", "/calendars/cal1/events", answer({"id": "abc", "etag": '"1"'}))
    await provider(http).upsert_event(cred(), "cal1", OutEvent(
        title="Block", starts_at=NOW, ends_at=NOW + timedelta(hours=1), client_id="abc12"), None)
    assert http.calls[-1][3]["id"] == "abc12"


@pytest.mark.asyncio
async def test_an_event_with_no_client_id_sends_no_id() -> None:
    http = FakeHttp().on("POST", "/calendars/cal1/events", answer({"id": "x1"}))
    await provider(http).upsert_event(cred(), "cal1", OutEvent(
        title="Block", starts_at=NOW, ends_at=NOW + timedelta(hours=1)), None)
    assert "id" not in http.calls[-1][3]


@pytest.mark.asyncio
async def test_a_taken_id_is_the_exists_code() -> None:
    http = FakeHttp().on("POST", "/calendars/cal1/events", answer({"error": {"code": 409}}, 409))
    with pytest.raises(ProviderError) as info:
        await provider(http).upsert_event(cred(), "cal1", OutEvent(
            title="Block", starts_at=NOW, ends_at=NOW + timedelta(hours=1), client_id="abc12"), None)
    assert info.value.code == "exists"


@pytest.mark.asyncio
async def test_an_adopt_confirms_the_event_and_an_ordinary_write_does_not() -> None:
    """Google keeps the id of a deleted event as a cancelled event. The adopt
    PATCH (``revive``) says ``confirmed``, or the block never shows again. An
    ordinary update must NOT say it, or it revives an event the member deleted.
    Mutation: send ``status`` always, or never."""
    http = FakeHttp().on("PATCH", "/calendars/cal1/events/abc12", answer({"id": "abc12"}))
    out = OutEvent(title="Block", starts_at=NOW, ends_at=NOW + timedelta(hours=1), client_id="abc12")
    await provider(http).upsert_event(cred(), "cal1", out, "abc12")
    assert "status" not in http.calls[-1][3]
    from dataclasses import replace

    await provider(http).upsert_event(cred(), "cal1", replace(out, revive=True), "abc12")
    assert http.calls[-1][3]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_the_calendar_list_asks_for_hidden_calendars() -> None:
    """A "Metorite" calendar that the member hid must be found by name on a
    reconnect. Mutation: drop ``showHidden`` from ``google.list_calendars``."""
    http = FakeHttp().on("GET", "/users/me/calendarList", answer({"items": [
        {"id": "hid1", "summary": "Metorite", "accessRole": "owner"}]}))
    found = await provider(http).list_calendars(cred())
    assert [c.id for c in found] == ["hid1"]
    assert http.calls[-1][2]["showHidden"] == "true"
