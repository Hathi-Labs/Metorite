"""STU-E3: the record filter of a Studio event trigger (pure, no database).

Fence for ``triggers.event_trigger_matches`` and the 422 rules of
``crud._validate_event_filter``. The R8 cases are in
``test_workflows_triggers_r8.py``.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.workflows.crud import TriggerSpec, _validate_event_filter, _validate_trigger_specs
from gateway.routes.workflows.triggers import event_trigger_matches

_PAYLOAD = {"package_id": "p1", "object": "claim", "op": "create", "record_id": "r1"}
_T = "studio.record.created"


def _cfg(**flt) -> dict:
    return {"source": "studio", "event_type": _T, "filter": flt}


def test_no_filter_matches_every_record_event() -> None:
    assert event_trigger_matches({"source": "studio", "event_type": _T}, "studio", _T, _PAYLOAD)


def test_filter_matches_the_package_and_the_object() -> None:
    assert event_trigger_matches(_cfg(package_id="p1", object="claim"), "studio", _T, _PAYLOAD)


@pytest.mark.parametrize("flt", [{"package_id": "p2"}, {"object": "invoice"}, {"op": "update"}])
def test_filter_refuses_another_value(flt) -> None:
    assert not event_trigger_matches(_cfg(**flt), "studio", _T, _PAYLOAD)


def test_filter_fails_closed_without_a_payload_or_with_a_missing_key() -> None:
    assert not event_trigger_matches(_cfg(object="claim"), "studio", _T, None)
    assert not event_trigger_matches(_cfg(object="claim"), "studio", _T, {"package_id": "p1"})


def test_filter_refuses_an_unknown_key_and_an_empty_value() -> None:
    assert not event_trigger_matches(_cfg(record_id="r1"), "studio", _T, _PAYLOAD)
    assert not event_trigger_matches(_cfg(object=""), "studio", _T, {"object": ""})


def test_validate_accepts_a_good_filter() -> None:
    _validate_event_filter(_cfg(package_id="p1", object="claim", op="create"))


@pytest.mark.parametrize("config", [
    {"source": "zoho", "filter": {"object": "x"}},
    {"source": "studio", "filter": {}},
    {"source": "studio", "filter": {"record_id": "r"}},
    {"source": "studio", "filter": {"object": 5}},
    {"source": "studio", "filter": "claim"},
    {"source": "studio", "filter": {"op": "created"}},
    {"source": "studio", "filter": {"op": "Create"}},
    {"source": "studio", "filter": {"object": "claim "}},
])
def test_validate_refuses_a_bad_filter(config) -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_event_filter(config)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("flt", [{}, {"object": " "}, {"op": "created"}])
def test_the_save_path_refuses_a_bad_filter(flt) -> None:
    """The route calls ``_validate_trigger_specs``, so a bad filter is a 422
    there. An empty filter would otherwise read as no filter."""
    with pytest.raises(HTTPException) as exc:
        _validate_trigger_specs(
            [TriggerSpec(kind="event", config={"source": "studio", "filter": flt})])
    assert exc.value.status_code == 422
