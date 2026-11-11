"""The preview of the action door, the hermetic half (STU-G4).

Spec: ``project-docs/specs/ai_studio.md`` §6.2 step 4 and §14 G4. Code:
``gateway/routes/studio/invoke.py`` (``preview=1``, ``_run_preview_write``) and
``gateway/studio/preview.py`` (the mark and the one emit of a record event).
The R8 half, on a real Postgres, is ``test_studio_preview_door_r8.py``.

This file reuses the fake session of ``test_studio_action_door.py``. It
proves:

- a preview write runs the one write path in a SAVEPOINT that always rolls
  back, then writes one audit row with the outcome ``simulated``;
- a preview needs no proof and opens no card, and it spends no request;
- a preview of a class B or C action of another source runs no executor,
  calls no sender and no event hook, and answers a simulated result;
- class X, an id outside the catalog and a class A source with no executor
  stay refused in a preview;
- a bad value of ``preview`` is 422 before any session, and the switch still
  comes first;
- a call with no ``preview`` key is unchanged;
- the mark of a preview is set during the call only, and a record event emits
  nothing inside it;
- only ``gateway/studio/preview.py`` reaches the event hooks.

**The mutations (R7).** Each was run on 2026-11-11 on this file and the R8
half, and then restored with git.

* P1. ``_run_preview_write``: drop ``raise _Undo``. 8 tests here go red,
  first ``test_a_preview_write_rolls_its_savepoint_back``, and 4 of the R8
  half.
* P2. ``refusal_for``: drop the preview branch. 13 tests here go red, among
  them each simulated send, and 7 of the R8 half.
* P3. ``_mode``: drop the preview branch, so a signed preview runs the direct
  write and an unsigned one asks. 11 tests go red, among them
  ``test_a_signed_preview_still_rolls_back`` and
  ``test_a_preview_opens_no_card_and_spends_none``.
* P4. ``_previews``: answer False for an unknown value. The 5 cases of
  ``test_a_bad_preview_value_is_422_and_opens_no_session`` go red.
* P5. ``emit_record_event``: drop the preview check.
  ``test_a_record_event_in_a_preview_reaches_no_sink`` goes red.
* P6. ``invoke_action``: let a preview keep ``allowed``. No test goes red,
  because ``_mode`` answers ``preview`` first, so a preview never asks and
  never spends. ``not preview`` in ``allowed`` is a second guard, and P3 is
  the mutation of the first.
* P7. ``_run_preview_write``: write with no SAVEPOINT. 9 tests here go red,
  and 4 of the R8 half.
"""

from __future__ import annotations

import ast
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from acb_common.studio_fields import FieldError
from gateway.routes.studio import invoke
from gateway.studio import preview as preview_mod
from ingestion import event_hooks

from tests.unit.test_studio_action_door import (  # noqa: F401
    ANA,
    BODIES,
    _batch,
    _client,
    _code,
    _open,
    _proof,
    _setup,
    dw,
    rw,
    world,
)


@pytest.fixture
def pw(dw):  # noqa: F811
    """The direct world of the door suite: a fake write and a fake audit."""
    return dw


@pytest.fixture
def prw(rw):  # noqa: F811
    """The delegated world of the door suite: a fake request store."""
    return rw

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps/services/gateway/gateway"

#: The log of the fake session for a preview write: the first check, then the
#: session of the write, a SAVEPOINT that rolls back, and the audit row after.
PREVIEW_LOG = ["open", "close", "open", "savepoint", "write", "rollback_savepoint",
               "audit_in", "close"]


def _preview_post(c: Any, pid: str, action: str, body: Any, *,
                  params: dict[str, str] | None = None,
                  headers: dict[str, str] | None = None) -> Any:
    return c.post(f"/studio/apps/{pid}/actions/{action}", json=body,
                  params=params if params is not None else {"preview": "1"},
                  headers=headers or {})


@pytest.fixture
def senders(monkeypatch):
    """Each sender seam and the event hook raise when called. A preview must
    call none of them."""
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


class TestThePreviewWrite:
    @pytest.mark.parametrize("action", ["add", "change", "remove"])
    def test_a_preview_write_rolls_its_savepoint_back(self, pw, action, senders):
        pid, _, _ = _setup(pw)
        r = _preview_post(_client(pw), pid, action, BODIES[action])
        assert r.status_code == 200, r.text
        assert r.json()["preview"] is True
        assert pw.log == PREVIEW_LOG
        assert len(pw.writes) == 1
        (_, kw), = pw.audits_in
        assert kw["detail"]["outcome"] == "simulated" and kw["detail"]["preview"] is True
        assert pw.audits == []  # the best-effort writer never ran
        assert senders == []

    def test_a_preview_write_needs_no_proof(self, pw):
        """An unsigned write without preview is 403. With preview it runs and
        rolls back."""
        pid, _, _ = _setup(pw)
        c = _client(pw)
        live = c.post(f"/studio/apps/{pid}/actions/add", json=BODIES["add"])
        assert (live.status_code, _code(live)) == (403, "approval_required")
        pw.log.clear()
        r = _preview_post(c, pid, "add", BODIES["add"])
        assert r.status_code == 200, r.text
        assert pw.log == PREVIEW_LOG

    def test_a_signed_preview_still_rolls_back(self, pw):
        pid, _, _ = _setup(pw)
        r = _preview_post(_client(pw), pid, "add", BODIES["add"],
                          headers=_proof(pid, "add"))
        assert r.status_code == 200, r.text
        assert pw.log == PREVIEW_LOG

    def test_the_audit_row_of_a_preview_create_names_no_record(self, pw):
        pid, _, _ = _setup(pw)
        c = _client(pw)
        _preview_post(c, pid, "add", BODIES["add"])
        _preview_post(c, pid, "change", BODIES["change"])
        _preview_post(c, pid, "remove", BODIES["remove"])
        ids = [kw["detail"].get("record_id") for _, kw in pw.audits_in]
        assert ids == [None, BODIES["change"]["id"], BODIES["remove"]["id"]]
        assert "Plant 9" not in json.dumps([kw for _, kw in pw.audits_in])

    def test_a_refused_preview_write_is_422_and_audits_refused(self, pw):
        pid, _, _ = _setup(pw)
        pw.write_raises = _batch(FieldError("site", "Field `site` is too long."))
        r = _preview_post(_client(pw), pid, "add", BODIES["add"])
        assert (r.status_code, _code(r)) == (422, "field_invalid")
        (_, kw), = pw.audits_in
        assert kw["detail"]["outcome"] == "refused" and kw["detail"]["preview"] is True
        assert "rollback_savepoint" in pw.log

    def test_a_raw_fault_in_a_preview_rolls_back_and_writes_no_row(self, pw):
        pid, _, _ = _setup(pw)
        pw.write_raises = RuntimeError("a raw fault")
        with pytest.raises(RuntimeError):
            _preview_post(_client(pw), pid, "add", BODIES["add"])
        assert "rollback_savepoint" in pw.log and pw.audits_in == []

    def test_the_mark_is_set_during_the_write_only(self, pw, monkeypatch):
        seen: list[bool] = []
        real = invoke.objects_store.apply_writes

        async def _watch(*a: Any, **k: Any) -> Any:
            seen.append(preview_mod.in_preview())
            return await real(*a, **k)

        monkeypatch.setattr(invoke.objects_store, "apply_writes", _watch)
        pid, _, _ = _setup(pw)
        _preview_post(_client(pw), pid, "add", BODIES["add"])
        c = _client(pw)
        c.post(f"/studio/apps/{pid}/actions/add", json=BODIES["add"],
               headers=_proof(pid, "add"))
        assert seen == [True, False]
        assert preview_mod.in_preview() is False


class TestNoCard:
    @pytest.mark.parametrize("query", [{"preview": "1", "ask": "1"},
                                       {"preview": "1", "request_id": str(uuid.uuid4())}])
    def test_a_preview_opens_no_card_and_spends_none(self, prw, query):
        pid, _, _ = _setup(prw)
        r = _preview_post(_client(prw), pid, "add", BODIES["add"], params=query)
        assert r.status_code == 200, r.text
        assert r.json()["preview"] is True
        for step in ("open_request", "open_of_change", "read_request", "spend"):
            assert step not in prw.log
        assert prw.log == PREVIEW_LOG


class TestTheSimulatedSend:
    @pytest.mark.parametrize("action,uses,cls", [
        ("flow", None, "B"), ("propose", "knowledge.propose", "B"),
        ("sync", "integration:zoho.push", "C")])
    def test_a_preview_of_another_source_runs_nothing(self, pw, senders, action, uses, cls):
        pid, _, _ = _setup(pw)
        r = _preview_post(_client(pw), pid, action, {"to": "someone@example.test"})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["preview"] is True and got["simulated"]["class"] == cls
        assert got["simulated"]["action"] == action
        if uses is not None:
            assert got["simulated"]["uses"] == uses
        # No executor ran: no write, no second session, no sender.
        assert pw.writes == [] and senders == []
        assert pw.log.count("open") == 1
        (row,) = pw.audits
        assert row["detail"]["outcome"] == "simulated" and row["detail"]["preview"] is True
        assert "someone@example.test" not in json.dumps(row)

    def test_without_preview_the_same_send_is_still_403(self, pw, senders):
        pid, _, _ = _setup(pw)
        r = _client(pw).post(f"/studio/apps/{pid}/actions/sync", json={})
        assert (r.status_code, _code(r)) == (403, "approval_required")
        assert senders == []

    @pytest.mark.parametrize("action,code", [
        ("forbidden", "class_x"), ("gone_flow", "not_in_catalog"),
        ("pages", "source_not_open")])
    def test_a_preview_keeps_each_other_refusal(self, pw, action, code):
        pid, _, _ = _setup(pw)
        r = _preview_post(_client(pw), pid, action, {})
        assert (r.status_code, _code(r)) == (403, code)
        (row,) = pw.audits
        assert row["detail"]["outcome"] == "refused" and "preview" not in row["detail"]


class TestTheMarker:
    @pytest.mark.parametrize("value", ["0", "true", "", "yes", "11"])
    def test_a_bad_preview_value_is_422_and_opens_no_session(self, pw, value):
        pid, _, _ = _setup(pw)
        r = _preview_post(_client(pw), pid, "add", BODIES["add"], params={"preview": value})
        assert (r.status_code, _code(r)) == (422, "bad_preview")
        assert pw.log == [] and pw.writes == []

    def test_the_switch_comes_first(self, pw, monkeypatch):
        _open(monkeypatch, "")
        pid, _, _ = _setup(pw)
        for value in ("1", "bad"):
            r = _preview_post(_client(pw), pid, "add", BODIES["add"],
                              params={"preview": value})
            assert r.status_code == 404
        assert pw.log == []

    def test_a_call_with_no_preview_key_is_unchanged(self, pw):
        pid, _, _ = _setup(pw)
        r = _client(pw).post(f"/studio/apps/{pid}/actions/add", json=BODIES["add"],
                             headers=_proof(pid, "add"))
        assert r.status_code == 200 and "preview" not in r.json()
        assert pw.log == ["open", "close", "open", "write", "audit_in", "close"]
        (_, kw), = pw.audits_in
        assert kw["detail"]["outcome"] == "ran" and "preview" not in kw["detail"]

    def test_a_preview_read_is_a_real_read_with_its_audit_row(self, pw):
        pid, _, rid = _setup(pw)
        r = _preview_post(_client(pw), pid, "peek", {"id": rid})
        assert r.status_code == 200 and r.json()["record"]["id"] == rid
        assert "preview" not in r.json()
        (row,) = pw.audits
        assert row["detail"]["outcome"] == "ran" and "preview" not in row["detail"]


class TestTheRecordEvent:
    def test_a_record_event_in_a_preview_reaches_no_sink(self):
        got: list[tuple[str, str]] = []

        async def _sink(source: str, event_type: str, payload: dict[str, Any]) -> None:
            got.append((source, event_type))

        event_hooks.clear_event_sinks()
        event_hooks.register_event_sink(_sink)
        try:
            async def go() -> tuple[bool, bool]:
                with preview_mod.preview_scope():
                    held = await preview_mod.emit_record_event("studio.record.created", {})
                sent = await preview_mod.emit_record_event("studio.record.created", {})
                return held, sent

            held, sent = asyncio.run(go())
        finally:
            event_hooks.clear_event_sinks()
        assert (held, sent) == (False, True)
        assert got == [("studio", "studio.record.created")]

    def test_an_inner_scope_never_clears_the_mark(self):
        with preview_mod.preview_scope():
            with preview_mod.preview_scope(False):
                assert preview_mod.in_preview() is True
            assert preview_mod.in_preview() is True
        assert preview_mod.in_preview() is False
        with preview_mod.preview_scope(False):
            assert preview_mod.in_preview() is False


def _names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


def test_only_the_preview_module_reaches_the_event_hooks() -> None:
    """C3 must emit a record event through ``emit_record_event``, so a preview
    cannot start a workflow. A module of the Studio code that names the event
    hooks itself fails here."""
    reach: set[str] = set()
    for folder in (GATEWAY / "studio", GATEWAY / "routes/studio"):
        for path in sorted(folder.rglob("*.py")):
            names = _names(ast.parse(path.read_text(encoding="utf-8")))
            if {"emit_event", "ingestion.event_hooks", "event_hooks"} & names:
                reach.add(path.relative_to(GATEWAY).as_posix())
    assert reach == {"studio/preview.py"}


def test_the_door_calls_no_sender() -> None:
    """No executor of the door sends. A preview of a send is simulated, and a
    later executor of a send must check the mark first."""
    whole = (GATEWAY / "routes/studio/invoke.py").read_text(encoding="utf-8")
    for name in ("send_email", "send_message", "transport", "emit_event"):
        assert name not in whole
