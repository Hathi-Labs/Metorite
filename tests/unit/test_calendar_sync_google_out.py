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
