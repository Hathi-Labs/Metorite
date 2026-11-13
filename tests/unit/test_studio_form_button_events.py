"""STU-E3 part 2: a form submit and a button press emit an event, hermetic.

Code: ``studio/events.py`` and ``routes/studio/invoke.py``. The form is a view
of the block ``form`` whose ``submit`` names an action. The button is an action
that a view lists in its ``actions``. The door emits ``studio.form.submitted``
or ``studio.action.pressed`` after the write commits, through the one path
``studio.preview.emit_record_event``. The payload holds ids and names, never a
field value. The R8 half (one event, one run, two orgs) is in
``test_workflows_triggers_r8.py``.

**Mutations this file catches:**

* F1. ``events.press_event``: put a field value in the payload (the no-value
  case red).
* F2. ``invoke._do_object_write``: drop the ``out.press`` line (the form and
  button cases red).
* F3. ``invoke.run_workflow_write``: drop ``out.press = None`` (the loop fence
  red).
* F4. ``events.press_event``: ignore the ``block == "form"`` test (the form
  case red).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from gateway.routes.studio import invoke
from gateway.studio import events
from ingestion import event_hooks

from tests.unit.test_studio_action_door import (  # noqa: F401
    BODIES,
    MANIFEST,
    ORG,
    _batch,
    _client,
    _object,
    _package,
    _proof,
    _record,
    dw,
    world,
)
from tests.unit.test_studio_preview_door import _preview_post

_ROOT = Path(__file__).resolve().parents[2]

#: The visit manifest, with a form that submits ``change`` and a view that lists
#: the button ``remove``. ``add`` is in no view, so nobody presses it.
_VIEWS = [
    {"id": "check-in", "block": "form", "object": "visit", "fields": ["site"], "submit": "change"},
    {"id": "board", "block": "board", "source": {"object": "visit"}, "actions": ["remove", "change"]},
]
_MANIFEST = {**MANIFEST, "views": _VIEWS}


@pytest.fixture
def seen():
    got: list[tuple[str, str, dict[str, Any]]] = []

    async def _sink(source: str, event_type: str, payload: dict[str, Any]) -> None:
        got.append((source, event_type, payload))

    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(_sink)
    yield got
    event_hooks.clear_event_sinks()


def _setup(w: Any) -> str:
    pid = _package(w, manifest=_MANIFEST)
    _record(w, _object(w, pid))
    return pid


def _live(c: Any, pid: str, action: str) -> Any:
    return c.post(f"/studio/apps/{pid}/actions/{action}", json=BODIES[action],
                  headers=_proof(pid, action))


def _types(seen: list) -> list[str]:
    return [t for _, t, _ in seen]


def test_a_form_submit_emits_one_form_event_beside_the_record_event(dw, seen):  # noqa: F811
    pid = _setup(dw)
    assert _live(_client(dw), pid, "change").status_code == 200
    assert _types(seen) == ["studio.record.updated", "studio.form.submitted"]
    source, _, payload = seen[1]
    assert source == "studio"
    assert (payload["package_id"], payload["name"], payload["action"]) == (
        pid, "check-in", "change")
    assert payload["organization_id"] == ORG and payload["object"] == "visit"
    assert payload["record_id"] and payload["actor"]


def test_a_button_press_emits_one_press_event(dw, seen):  # noqa: F811
    pid = _setup(dw)
    assert _live(_client(dw), pid, "remove").status_code == 200
    assert _types(seen) == ["studio.record.deleted", "studio.action.pressed"]
    payload = seen[1][2]
    assert (payload["name"], payload["action"], payload["organization_id"]) == (
        "remove", "remove", ORG)


def test_an_action_that_no_view_names_emits_no_press(dw, seen):  # noqa: F811
    pid = _setup(dw)
    assert _live(_client(dw), pid, "add").status_code == 200
    assert _types(seen) == ["studio.record.created"]


def test_the_press_payload_holds_no_field_value(dw, seen):  # noqa: F811
    pid = _setup(dw)
    _live(_client(dw), pid, "change")
    payload = seen[1][2]
    assert set(payload) == {"package_id", "name", "action", "object", "record_id", "actor",
                            "organization_id"}
    for value in BODIES["change"]["data"].values():
        assert str(value) not in repr(payload)


def test_a_refused_write_emits_no_press(dw, seen):  # noqa: F811
    from acb_common.studio_fields import FieldError

    pid = _setup(dw)
    dw.write_raises = _batch(FieldError("site", "Field `site` is too long."))
    assert _live(_client(dw), pid, "change").status_code == 422
    assert seen == []


@pytest.mark.parametrize("action", ["change", "remove"])
def test_a_preview_emits_no_press(dw, seen, action):  # noqa: F811
    pid = _setup(dw)
    assert _preview_post(_client(dw), pid, action, BODIES[action]).status_code == 200
    assert seen == []


def test_a_failing_sink_does_not_fail_the_write(dw):  # noqa: F811
    async def _boom(*_a: Any) -> None:
        raise RuntimeError("sink down")

    event_hooks.clear_event_sinks()
    event_hooks.register_event_sink(_boom)
    try:
        pid = _setup(dw)
        assert _live(_client(dw), pid, "change").status_code == 200
    finally:
        event_hooks.clear_event_sinks()


def test_a_workflow_write_is_no_press() -> None:
    """A workflow write clears ``out.press``, so a workflow cannot press the
    button that starts it again (no loop)."""
    tree = ast.parse((_ROOT / "apps/services/gateway/gateway/routes/studio/invoke.py")
                     .read_text(encoding="utf-8"))
    func = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "run_workflow_write")
    sets = [n for n in ast.walk(func) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "press" for t in n.targets)
            and isinstance(n.value, ast.Constant) and n.value.value is None]
    assert sets


def test_press_event_is_pure_and_form_wins() -> None:
    manifest = {"views": [
        {"id": "f", "block": "form", "submit": "go"},
        {"id": "t", "block": "table", "actions": ["go", "stop"]},
    ]}
    kw = {"package_id": "p", "object_name": "o", "record_id": "r", "member": "m@x.test"}
    assert events.press_event(manifest, "go", **kw)[0] == events.FORM_EVENT
    assert events.press_event(manifest, "go", **kw)[1]["name"] == "f"
    assert events.press_event(manifest, "stop", **kw)[0] == events.PRESS_EVENT
    for bad in (None, {}, {"views": "x"}, {"views": [None, 3]}):
        assert events.press_event(bad, "go", **kw) is None
    assert events.press_event(manifest, "", **kw) is None
    assert events.press_event(manifest, "other", **kw) is None


def test_the_new_module_reaches_no_event_seam() -> None:
    """Only ``preview.py`` names ``emit_event`` or ``event_hooks`` (G4)."""
    body = (_ROOT / "apps/services/gateway/gateway/studio/events.py").read_text(encoding="utf-8")
    assert "emit_event" not in body and "event_hooks" not in body
    assert invoke.emit_record_event is not None
