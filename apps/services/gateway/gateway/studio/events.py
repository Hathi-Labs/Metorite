"""The form and button events of a Studio package (STU-E3, part 2).

Spec: ``project-docs/specs/ai_studio.md`` §3.3 and §14 row E3. A form is a view
of the block ``form`` whose ``submit`` names an action. A button is a named
action that a view lists in its ``actions``. Both run through the one action
door, so the door emits the event of the pair, after the write commits, through
``studio.preview.emit_record_event``, the ONE emit path. No second event seam
and no second engine exist.

The types are ``studio.form.submitted`` and ``studio.action.pressed``, under the
source ``studio``. The payload holds ids and names: ``package_id``, ``name``
(the id of the form view, or the id of the action), ``action``, ``object``,
``record_id`` and ``actor``. The door adds ``organization_id``. It holds NO
field value, so a restricted field never leaves the store through an event.

An action that is the ``submit`` of a form view is a form event, even when a
view also lists it. An action that no view names emits no press, because
nobody pressed a button for it.
"""

from __future__ import annotations

from typing import Any

#: The event type of a form submit and of a button press.
FORM_EVENT = "studio.form.submitted"
PRESS_EVENT = "studio.action.pressed"


def press_event(
    manifest: Any, action: str, *, package_id: str, object_name: str, record_id: str,
    member: str,
) -> tuple[str, dict[str, Any]] | None:
    """The ``(event_type, payload)`` that the write of *action* earns, or None.

    Pure. *manifest* is the manifest of the package that ran the action. The
    payload holds names and ids and no field value."""
    views = manifest.get("views") if isinstance(manifest, dict) else None
    if not isinstance(views, list) or not action:
        return None
    event_type, name = None, ""
    for view in views:
        if not isinstance(view, dict):
            continue
        if view.get("block") == "form" and view.get("submit") == action:
            event_type, name = FORM_EVENT, str(view.get("id") or "")
            break
        acts = view.get("actions")
        if isinstance(acts, list) and action in acts and event_type is None:
            event_type, name = PRESS_EVENT, action
    if event_type is None or not name:
        return None
    return event_type, {
        "package_id": package_id, "name": name, "action": action, "object": object_name,
        "record_id": record_id, "actor": member,
    }
