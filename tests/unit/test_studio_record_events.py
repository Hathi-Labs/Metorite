"""STU-C3: a record write emits ``studio.record.*`` through the one event seam.

Hermetic: the fake world of the door suite. The event goes out after the
session commits, carries the org, and holds no field value. A refused write
and a preview emit nothing. The SQL side of C3 is none, so no R8 case is due.
"""

from __future__ import annotations

from typing import Any

import pytest
from gateway.routes.studio import invoke
from ingestion import event_hooks

from tests.unit.test_studio_action_door import (  # noqa: F401
    BODIES,
    ORG,
    _batch,
    _client,
    _proof,
    _setup,
    dw,
    world,
)
from tests.unit.test_studio_preview_door import _preview_post


@pytest.fixture
def seen():
    got: list[tuple[str, str, dict[str, Any]]] = []

    async def _sink(source: str, event_type: str, payload: dict[str, Any]) -> None:
        got.append((source, event_type, payload))

    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(_sink)
    yield got
    event_hooks.clear_event_sinks()


def _live(c: Any, pid: str, action: str) -> Any:
    return c.post(f"/studio/apps/{pid}/actions/{action}", json=BODIES[action],
                  headers=_proof(pid, action))


@pytest.mark.parametrize("action,event,op", [
    ("add", "studio.record.created", "create"),
    ("change", "studio.record.updated", "update"),
    ("remove", "studio.record.deleted", "delete"),
])
def test_one_write_emits_one_event_with_the_org(dw, seen, action, event, op):  # noqa: F811
    pid, _, _ = _setup(dw)
    r = _live(_client(dw), pid, action)
    assert r.status_code == 200, r.text
    assert len(seen) == 1
    source, event_type, payload = seen[0]
    assert (source, event_type) == ("studio", event)
    assert payload["organization_id"] == ORG
    assert payload["package_id"] == pid and payload["op"] == op
    assert payload["object"] == "visit" and payload["record_id"]


def test_the_event_holds_the_changed_keys_and_no_value(dw, seen):  # noqa: F811
    pid, _, _ = _setup(dw)
    _live(_client(dw), pid, "change")
    (_, _, payload), = seen
    assert payload["changed"] == sorted(BODIES["change"]["data"])
    for value in BODIES["change"]["data"].values():
        assert str(value) not in repr(payload)


def test_a_refused_write_emits_nothing(dw, seen):  # noqa: F811
    from acb_common.studio_fields import FieldError

    pid, _, _ = _setup(dw)
    dw.write_raises = _batch(FieldError("site", "Field `site` is too long."))
    r = _live(_client(dw), pid, "add")
    assert r.status_code == 422
    assert seen == []


@pytest.mark.parametrize("action", ["add", "change", "remove"])
def test_a_preview_emits_nothing(dw, seen, action):  # noqa: F811
    pid, _, _ = _setup(dw)
    r = _preview_post(_client(dw), pid, action, BODIES[action])
    assert r.status_code == 200, r.text
    assert seen == []


def test_a_failing_sink_does_not_fail_the_write(dw):  # noqa: F811
    async def _boom(*_a: Any) -> None:
        raise RuntimeError("sink down")

    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(_boom)
    try:
        pid, _, _ = _setup(dw)
        assert _live(_client(dw), pid, "add").status_code == 200
    finally:
        event_hooks.clear_event_sinks()


def test_the_emit_runs_after_the_session_closes(dw, monkeypatch):  # noqa: F811
    at: list[list[str]] = []

    async def _emit(event_type: str, payload: dict[str, Any]) -> bool:
        at.append(list(dw.log))
        return True

    monkeypatch.setattr(invoke, "emit_record_event", _emit)
    pid, _, _ = _setup(dw)
    _live(_client(dw), pid, "add")
    assert at and at[0][-1] in ("close", "audit_in") and "write" in at[0]
