"""WS-CAL CE-13b: the Outlook write path speaks the writer's vocabulary.

Recorded Graph replies only. No request leaves the test.

* a missing calendar or event (HTTP 404, Graph code ``ErrorItemNotFound``) is
  ``ProviderError("not_found")``, which the writer uses to tell a deleted
  calendar from a deleted event;
* HTTP 409 is ``ProviderError("exists")``;
* an insert sends the client id as ``transactionId`` (a repeat makes no copy),
  an update sends none;
* a delete of a missing event is a success;
* ``client_id_is_event_id`` is False, so the writer never adopts or deletes an
  Outlook event by its transaction id.

Mutations (R7): drop the 404/``ErrorItemNotFound`` branch, the 409 branch, or
the ``transactionId`` line of ``outlook.py``. Each test below turns red.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from gateway.calendar_sync import outlook
from gateway.calendar_sync.provider import OutEvent, ProviderError

from tests.unit.test_calendar_sync_read import FakeHttp, answer, cred

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
NOT_FOUND = {"error": {"code": "ErrorItemNotFound", "message": "The specified object was not found."}}


def prov(http: FakeHttp) -> outlook.OutlookProvider:
    return outlook.OutlookProvider(
        http, client_id="cid", client_secret="sec",  # type: ignore[arg-type]
        redirect_uri="https://app.test/api/calendar/oauth/microsoft/callback", clock=lambda: NOW)


def block(**kw) -> OutEvent:
    return OutEvent(title="Block", starts_at=NOW, ends_at=NOW + timedelta(hours=1), **kw)


@pytest.mark.asyncio
async def test_a_missing_calendar_or_event_is_not_found() -> None:
    http = (FakeHttp().on("POST", "/calendars/CAL/events", answer(NOT_FOUND, 404))
            .on("PATCH", "/me/events/EV1", answer(NOT_FOUND, 404)))
    for external_id in (None, "EV1"):
        with pytest.raises(ProviderError) as info:
            await prov(http).upsert_event(cred(), "CAL", block(client_id="abc"), external_id)
        assert info.value.code == "not_found"


@pytest.mark.asyncio
async def test_the_graph_code_alone_is_not_found_too() -> None:
    http = FakeHttp().on("PATCH", "/me/events/EV1", answer(NOT_FOUND, 400))
    with pytest.raises(ProviderError) as info:
        await prov(http).upsert_event(cred(), "CAL", block(), "EV1")
    assert info.value.code == "not_found"


@pytest.mark.asyncio
async def test_a_conflict_is_exists_and_another_failure_keeps_its_graph_code() -> None:
    http = FakeHttp().on("POST", "/calendars/CAL/events",
                         answer({"error": {"code": "ErrorDuplicate"}}, 409))
    with pytest.raises(ProviderError) as info:
        await prov(http).upsert_event(cred(), "CAL", block(client_id="abc"), None)
    assert info.value.code == "exists"
    http = FakeHttp().on("POST", "/calendars/CAL/events",
                         answer({"error": {"code": "ErrorAccessDenied"}}, 403))
    with pytest.raises(ProviderError) as info:
        await prov(http).upsert_event(cred(), "CAL", block(), None)
    assert info.value.code == "ErrorAccessDenied"


@pytest.mark.asyncio
async def test_an_insert_sends_the_transaction_id_and_an_update_sends_none() -> None:
    http = (FakeHttp().on("POST", "/calendars/CAL/events", answer({"id": "EV1", "@odata.etag": 'W/"1"'}))
            .on("PATCH", "/me/events/EV1", answer({"id": "EV1"})))
    p = prov(http)
    await p.upsert_event(cred(), "CAL", block(client_id="abc12"), None)
    assert http.calls[-1][3]["transactionId"] == "abc12"
    await p.upsert_event(cred(), "CAL", block(), "EV1")
    assert "transactionId" not in http.calls[-1][3]


@pytest.mark.asyncio
async def test_a_delete_of_a_missing_event_is_a_success() -> None:
    http = FakeHttp().on("DELETE", "/me/events/GONE", answer(NOT_FOUND, 404))
    await prov(http).delete_event(cred(), "CAL", "GONE")


def test_the_client_id_is_not_the_event_id_on_outlook() -> None:
    assert outlook.OutlookProvider.client_id_is_event_id is False
