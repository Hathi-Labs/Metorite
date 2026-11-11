"""The preview of the action door on a real database (STU-G4, R8).

Spec: ``project-docs/specs/ai_studio.md`` §6.2 step 4 and §14 G4. Code:
``gateway/routes/studio/invoke.py`` (``preview=1``) and
``gateway/studio/preview.py``. The hermetic half is
``test_studio_preview_door.py``. The world, the packages and the call helpers
are those of ``test_studio_action_door_r8.py``.

The real handler runs on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Run it on a PRIVATE ladder
database, one R8 suite at a time::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_preview_door_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (G4).**

a. A preview create, update and delete each answer what the write would
   give. After them, ``studio_records`` and ``studio_record_hot`` of the live
   package hold exactly the rows of before, byte for byte.
b. During a preview write, a second connection sees no change.
c. A preview of a send or a workflow records a simulated send in the audit
   row, and calls no sender. No ``workflow_runs`` row appears.
d. A record event that the write path emits through ``emit_record_event``
   reaches no sink in a preview, and reaches the sink once for a live write.
e. A refused preview write is 422 and writes nothing.
f. Another member and org B get 404 for a preview, and nothing changes.
g. The path with no preview key is unchanged: a direct write commits its
   record and an audit row that says ``ran``.

**The mutations (R7).** Each was run on 2026-11-11. The build note of G4 in
``ai_studio.md`` holds the results.

* P1. ``_run_preview_write``: drop ``raise _Undo``. Cases a, b and d go red.
* P2. ``refusal_for``: drop the preview branch. Cases a, c and d go red with
  403.
* P7. ``_run_preview_write``: call ``_run_object_write`` in place of the
  SAVEPOINT. Cases a, b and d go red.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes.studio import invoke
from gateway.studio import objects_store
from gateway.studio import preview as preview_mod
from ingestion import event_hooks
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_studio_action_door_r8 import (  # noqa: F401
    _DATA,
    ANA,
    BEN,
    _audits,
    _headers,
    _record_row,
    _Request,
    _run,
    _user,
    dworld,
    world,
)

pytestmark = _DB_GATE


@pytest.fixture
def pworld(dworld):  # noqa: F811
    """The world of the door suite, with a usable direct key."""
    return dworld


async def _pcall(org: str, email: str, pid: str, action: str, payload: Any, *,
                 signed: bool = False, query: dict[str, str] | None = None) -> tuple[int, Any]:
    """The real handler, with ``preview=1`` in the query."""
    path = f"/studio/apps/{pid}/actions/{action}"
    headers = _headers(pid, action, email) if signed else {}
    request = _Request(payload, headers=headers, path=path,
                       query={"preview": "1"} if query is None else query)
    token = bind_tenant(org)
    try:
        return 200, await invoke.invoke_action(pid, action, request=request,
                                               user=_user(org, email))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _live(org: str, email: str, pid: str, action: str, payload: Any) -> tuple[int, Any]:
    return await _pcall(org, email, pid, action, payload, signed=True, query={})


def _snapshot(w: Any, pid: str) -> tuple[list[Any], list[Any]]:
    """Every row of ``studio_records`` and ``studio_record_hot`` of the
    package *pid*, read as the superuser, in a fixed order."""
    with w.p.admin_engine.connect() as c:
        records = c.execute(text(
            "SELECT r.id::text, r.data::text, r.created_by, r.updated_by, r.created_at, "
            "r.updated_at FROM studio_records r JOIN studio_objects o ON o.id = r.object_id "
            "WHERE o.package_id = CAST(:p AS uuid) ORDER BY r.id"), {"p": pid}).all()
        hot = c.execute(text(
            "SELECT h.* FROM studio_record_hot h JOIN studio_records r ON r.id = h.record_id "
            "JOIN studio_objects o ON o.id = r.object_id "
            "WHERE o.package_id = CAST(:p AS uuid) ORDER BY 1, 2"), {"p": pid}).all()
    return [tuple(r) for r in records], [tuple(h) for h in hot]


def _runs(w: Any) -> int:
    with w.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM workflow_runs WHERE workflow_id IN "
            "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": w.wf_a, "b": w.wf_b}).scalar_one())


@pytest.fixture
def senders(monkeypatch):
    """Each sender seam and the event hook raise when called."""
    from gateway.routes.email.transport import send as email_send
    from gateway.routes.whatsapp.transport import send as wa_send

    called: list[str] = []

    def _never(name: str):
        async def _boom(*_a: Any, **_k: Any) -> Any:
            called.append(name)
            raise AssertionError(f"a preview called {name}")
        return _boom

    monkeypatch.setattr(email_send, "send_email", _never("send_email"))
    monkeypatch.setattr(wa_send, "send_message", _never("send_message"))
    monkeypatch.setattr(event_hooks, "emit_event", _never("emit_event"))
    return called


@pytest.mark.parametrize("signed", [False, True], ids=["unsigned", "signed"])
def test_r8_a_preview_create_update_and_delete_leave_the_object_tables_unchanged(
        pworld, senders, signed) -> None:
    w = pworld
    before = _snapshot(w, w.pkg)
    other = _snapshot(w, w.pkg2)

    async def go() -> tuple[Any, Any, Any]:
        made = await _pcall(w.a, ANA, w.pkg, "add", {"data": {**_DATA, "site": "Preview 1"}},
                            signed=signed)
        changed = await _pcall(w.a, ANA, w.pkg, "change",
                               {"id": w.rec, "data": {"notes": "Moved in preview"}},
                               signed=signed)
        gone = await _pcall(w.a, ANA, w.pkg, "remove", {"id": w.rec}, signed=signed)
        return made, changed, gone

    (s1, made), (s2, changed), (s3, gone) = _run(w, go)
    assert (s1, s2, s3) == (200, 200, 200), (made, changed, gone)
    assert made["preview"] is True and made["record"]["data"]["site"] == "Preview 1"
    assert changed["record"]["data"]["notes"] == "Moved in preview"
    assert gone == {"deleted": True, "id": w.rec, "preview": True}
    # Nothing of the preview reached the object store of the live package.
    assert _snapshot(w, w.pkg) == before
    assert _snapshot(w, w.pkg2) == other
    assert _record_row(w, made["record"]["id"]) is None
    assert _record_row(w, w.rec) is not None
    # The audit rows say simulated, and none says ran.
    rows = [r[3] for r in _audits(w, w.pkg)]
    assert [(r["action"], r["outcome"], r.get("preview")) for r in rows] == [
        ("add", "simulated", True), ("change", "simulated", True),
        ("remove", "simulated", True)]
    assert rows[0].get("record_id") is None and rows[1]["record_id"] == w.rec
    assert senders == []


def test_r8_another_session_sees_nothing_during_the_preview(pworld, monkeypatch) -> None:
    w = pworld
    before = _snapshot(w, w.pkg)
    seen: list[Any] = []
    real = objects_store.apply_writes

    async def _watch(*a: Any, **k: Any) -> Any:
        done = await real(*a, **k)
        # The write ran in this transaction. A second connection reads now.
        seen.append(_snapshot(w, w.pkg))
        return done

    monkeypatch.setattr(objects_store, "apply_writes", _watch)

    async def go() -> list[tuple[int, Any]]:
        return [await _pcall(w.a, ANA, w.pkg, "add", {"data": {**_DATA, "site": "Mid"}}),
                await _pcall(w.a, ANA, w.pkg, "change", {"id": w.rec, "data": {"notes": "M"}}),
                await _pcall(w.a, ANA, w.pkg, "remove", {"id": w.rec})]

    answers = _run(w, go)
    assert [s for s, _ in answers] == [200, 200, 200], answers
    assert seen == [before, before, before]
    assert _snapshot(w, w.pkg) == before


@pytest.mark.parametrize("action,cls", [("flow", "B"), ("propose", "B")])
def test_r8_a_preview_send_records_a_simulated_send_and_calls_no_sender(
        pworld, senders, action, cls) -> None:
    w = pworld
    runs, before = _runs(w), _snapshot(w, w.pkg)
    status, got = _run(w, lambda: _pcall(w.a, ANA, w.pkg, action,
                                         {"to": "someone@example.test"}))
    assert status == 200, got
    assert got["preview"] is True and got["simulated"]["action"] == action
    assert got["simulated"]["class"] == cls
    assert senders == [] and _runs(w) == runs and _snapshot(w, w.pkg) == before
    (row,) = [r[3] for r in _audits(w, w.pkg) if r[3]["action"] == action]
    assert row["outcome"] == "simulated" and row["preview"] is True
    assert "someone@example.test" not in json.dumps(row)
    # Without preview the same action still gets 403 and runs nothing.
    status, got = _run(w, lambda: _live(w.a, ANA, w.pkg, action, {}))
    assert (status, got["code"]) == (403, "approval_required")
    assert senders == [] and _runs(w) == runs


def test_r8_a_record_event_fires_for_a_live_write_and_never_for_a_preview(
        pworld, monkeypatch) -> None:
    """C3 is not built. Here the write path emits through the one seam, as C3
    must, and the preview holds it."""
    w = pworld
    got: list[str] = []
    real = objects_store.apply_writes

    async def _sink(source: str, event_type: str, payload: dict[str, Any]) -> None:
        got.append(event_type)

    async def _emitting(*a: Any, **k: Any) -> Any:
        done = await real(*a, **k)
        await preview_mod.emit_record_event("studio.record.changed", {"n": len(done)})
        return done

    monkeypatch.setattr(objects_store, "apply_writes", _emitting)
    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(_sink)
    try:
        async def go() -> list[int]:
            return [(await _pcall(w.a, ANA, w.pkg, "add", {"data": _DATA}))[0],
                    (await _pcall(w.a, ANA, w.pkg, "remove", {"id": w.rec}))[0]]

        assert _run(w, go) == [200, 200]
        assert got == []
        status, _ = _run(w, lambda: _live(w.a, ANA, w.pkg, "add", {"data": _DATA}))
        assert status == 200
        assert got == ["studio.record.changed"]
    finally:
        event_hooks.clear_event_sinks()


def test_r8_a_refused_preview_write_is_422_and_writes_nothing(pworld) -> None:
    w = pworld
    before = _snapshot(w, w.pkg)
    status, got = _run(w, lambda: _pcall(w.a, ANA, w.pkg, "change",
                                         {"id": w.rec, "data": {"status": "nope"}}))
    assert status == 422 and got["code"] == "field_invalid", got
    assert _snapshot(w, w.pkg) == before
    (row,) = [r[3] for r in _audits(w, w.pkg)]
    assert row["outcome"] == "refused" and row["preview"] is True


def test_r8_another_member_and_org_b_get_404_for_a_preview(pworld) -> None:
    w = pworld
    before, before_b = _snapshot(w, w.pkg), _snapshot(w, w.pkg_b)

    async def go() -> list[int]:
        return [(await _pcall(w.a, BEN, w.pkg, "add", {"data": _DATA}))[0],
                (await _pcall(w.b, ANA, w.pkg, "remove", {"id": w.rec}))[0],
                (await _pcall(w.a, ANA, w.pkg_b, "remove", {"id": w.rec_b}))[0]]

    assert _run(w, go) == [404, 404, 404]
    assert _snapshot(w, w.pkg) == before and _snapshot(w, w.pkg_b) == before_b
    assert _audits(w, w.pkg) == [] and _audits(w, w.pkg_b) == []


def test_r8_the_path_with_no_preview_key_is_unchanged(pworld) -> None:
    w = pworld
    status, got = _run(w, lambda: _live(w.a, ANA, w.pkg, "add",
                                        {"data": {**_DATA, "site": "Live"}}))
    assert status == 200 and "preview" not in got
    assert _record_row(w, got["record"]["id"]) is not None
    (row,) = [r[3] for r in _audits(w, w.pkg)]
    assert row["outcome"] == "ran" and "preview" not in row
    assert row["record_id"] == got["record"]["id"]


def test_r8_a_bad_preview_value_is_422_and_touches_nothing(pworld) -> None:
    w = pworld
    before = _snapshot(w, w.pkg)
    status, got = _run(w, lambda: _pcall(w.a, ANA, w.pkg, "add", {"data": _DATA},
                                         signed=True, query={"preview": "true"}))
    assert (status, got["code"]) == (422, "bad_preview")
    assert _snapshot(w, w.pkg) == before and _audits(w, w.pkg) == []


def test_r8_a_preview_create_id_is_never_stored(pworld) -> None:
    """The id that a preview create answers names no row, and a later live
    get of it is 404."""
    w = pworld
    status, made = _run(w, lambda: _pcall(w.a, ANA, w.pkg, "add", {"data": _DATA}))
    assert status == 200
    rid = made["record"]["id"]
    assert uuid.UUID(rid) and _record_row(w, rid) is None
    status, _ = _run(w, lambda: _pcall(w.a, ANA, w.pkg, "peek", {"id": rid}, query={}))
    assert status == 404
