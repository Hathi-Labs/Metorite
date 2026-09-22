"""Projects · what a task's VALUES do when it crosses a root — one seam.

Two routes move a task between projects. ``POST /tasks/move`` (``move.py``)
moves a selection with the mapping the member agreed to. ``POST
/tasks/{id}/move`` (``tasks.py``) moves one task, and My Tasks promotes
through it (S6c). Until 2026-09-23 they disagreed about what a cross-root
move DOES to a task:

* the bulk path applied the field map, checked the required fields on the
  values AS THEY LAND, corrected ``completed_at`` from the landing lane's
  category, and wrote a D-PM-29 drop entry to the timeline;
* the narrow path merged the caller's answers onto the RAW values, checked
  the required fields on that, and did none of the other three.

So a required field renamed between roots (``po`` → ``customer_po``, same
name "PO") was satisfied on the bulk path and refused on the narrow one, and
a DONE task promoted to a board with no closing lane arrived closed in a lane
that was open. Review found it (S6c repair round).

This module is the one answer. It imports only ``core`` and
``custom_fields``, so both routes can import it without the cycle
``tasks → move → bulk → personal → tasks`` that importing ``move`` from
``tasks`` would open.

The rules themselves are unchanged. :func:`resolve_field_map` and
:func:`apply_field_map` moved here from ``move.py`` (which re-exports them
for its two suites), :func:`custom_fields.apply_values` coerces an answer,
and :func:`custom_fields.assert_required_fields_present` refuses a blank.
What this module adds is the ORDER — map, then answers, then the check —
under one name, :func:`land_custom_fields`.
"""

from __future__ import annotations

from typing import Any

from gateway.routes.projects import custom_fields as cf
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    from_jsonb,
    now,
    record_activity,
)
from sqlalchemy import text

#: Which destination field types may receive which source type.
#:
#: ⚠️ Deliberately NOT "anything to text". Widening everything into a text field
#: would make every map succeed and quietly turn a date into a string that no
#: filter can compare. A mapping that cannot round-trip is a drop wearing a
#: mapping's clothes, and D-PM-29 says a drop must be NAMED.
#:
#: `select` → `multi_select` is the one widening allowed: one chosen option is
#: a legal list of one, and nothing about the value changes.
COMPATIBLE_TYPES: dict[str, frozenset[str]] = {
    "text": frozenset({"text"}),
    "number": frozenset({"number"}),
    "date": frozenset({"date"}),
    "boolean": frozenset({"boolean"}),
    "url": frozenset({"url"}),
    # ⚠️ `select` does NOT widen into `multi_select`. The earlier comment here
    # claimed "one chosen option is a legal list of one, and nothing about the
    # value changes" — and those two clauses contradict each other. A list of
    # one is `["High"]`, and `_coerce_multi_select` refuses a bare string. The
    # widening would have written a value the destination's own coercer
    # rejects, which is the failure this table exists to prevent.
    "select": frozenset({"select"}),
    "multi_select": frozenset({"multi_select"}),
}


#: The choice types, whose OPTIONS have to agree as well as their type.
CHOICE_TYPES = frozenset({"select", "multi_select"})


def compatible(
    source_type: str,
    dest_type: str,
    source_options: Any = None,
    dest_options: Any = None,
) -> bool:
    """May a value of ``source_type`` be written into a ``dest_type`` field?

    ⚠️ **For a choice field the TYPE is not enough, and assuming it was is a
    real defect this module shipped once.** Two spaces can each hold a
    `select` named "Severity" with `[Low, High]` and `[S1, S2, S3]`. The types
    match, so the value `"High"` was copied straight across — and
    `_coerce_select` refuses exactly that value on every later write. The
    task then carried a value its own field rejects: unfilterable,
    uneditable except by hand, and reported as landed rather than dropped.

    So a choice field carries only when the destination's options are a
    SUPERSET of the source's. Anything else is an orphan, which means the
    member is told.
    """
    if dest_type not in COMPATIBLE_TYPES.get(source_type, frozenset()):
        return False
    if source_type not in CHOICE_TYPES:
        return True
    have = {str(o) for o in (source_options or [])}
    allowed = {str(o) for o in (dest_options or [])}
    return have <= allowed


def resolve_field_map(
    source_defs: list[dict[str, Any]],
    dest_defs: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """``(field_key → destination field_key, unmappable source definitions)``.

    ``field_key`` first, because it is the stable identity the schema calls one
    (migration 155: *"Free to change; field_key is not"*). A case-folded NAME is
    the fallback, for the ordinary case of two spaces that each grew a
    "Severity" independently.

    A match on either is still refused when the types cannot carry the value,
    and that refusal makes the field unmappable rather than silently mapped.
    """
    by_key = {str(d["field_key"]): d for d in dest_defs}
    by_name = {str(d["name"]).strip().lower(): d for d in dest_defs}

    mapping: dict[str, str] = {}
    orphans: list[dict[str, Any]] = []
    # 🔴 Which destination keys are already spoken for.
    #
    # Two source fields can legitimately resolve to ONE destination field, and
    # it is the ORDINARY case rather than a curiosity: `pm_custom_fields` is
    # UNIQUE on (project_id, field_key) and on nothing else, so two rows may
    # share a NAME — and WS-27bj's org-wide plus root-local union produces exactly
    # that, an org-wide `priority` beside a root-local `prio`, both called
    # "Priority". One matches by key, the other by name, and both aimed at the
    # same target.
    #
    # Without this, the second value overwrote the first in `landed` and the
    # loser never entered `drops` — so no warning, no `accept_drops` gate and
    # no timeline row. Which value survived followed JSONB key order, so it
    # differed task by task inside ONE bulk move. Found by review, 2026-09-19.
    claimed: dict[str, str] = {}
    # Exact-key matches first, so the order `load_definitions` happens to
    # return cannot decide which of two contenders wins a shared target.
    ordered = sorted(
        source_defs, key=lambda d: 0 if str(d["field_key"]) in by_key else 1
    )
    for definition in ordered:
        key = str(definition["field_key"])
        source_type = str(definition["field_type"])
        # An exact key match is the stronger claim and is resolved first, so a
        # name match can never displace it — see the second pass below.
        target = by_key.get(key) or by_name.get(str(definition["name"]).strip().lower())
        if target is None or not compatible(
            source_type,
            str(target["field_type"]),
            definition.get("options"),
            target.get("options"),
        ):
            orphans.append(definition)
            continue
        target_key = str(target["field_key"])
        if target_key in claimed:
            # Contested. The loser is an ORPHAN, which means the member is told
            # its value will be dropped instead of losing it silently.
            orphans.append(definition)
            continue
        claimed[target_key] = key
        mapping[key] = target_key
    return mapping, orphans


def apply_field_map(
    values: dict[str, Any],
    mapping: dict[str, str],
    dest_keys: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(values as they land, values that are DROPPED)``.

    ⚠️ **A key already legal in the destination is carried through unmapped.**
    Two spaces that share a `field_key` need no map entry, and requiring one
    would make the common case the noisy one.

    A mapped key whose target is not in the destination's definitions is a
    DROP, not a write: the map can be stale by the time it is applied, and
    writing a value under a key nothing defines recreates the orphan this
    feature exists to remove.

    Moved here from ``move.py`` (which re-exports it) so the narrow route can
    reach it without the import cycle the module header names.
    """
    landed: dict[str, Any] = {}
    dropped: dict[str, Any] = {}
    for key, value in values.items():
        target = mapping.get(key, key if key in dest_keys else None)
        # 🔴 `target in landed` is the second half of the collision guard.
        # `resolve_field_map` stops two DEFINITIONS claiming one target, and
        # this stops a hand-supplied `field_map` doing the same. A caller
        # posts the map, so the rule cannot live only where we build it.
        if target is not None and target in dest_keys and target not in landed:
            landed[target] = value
        else:
            dropped[key] = value
    return landed, dropped


async def land_custom_fields(
    db: Any,
    task: Any,
    *,
    dest_root: str,
    field_map: dict[str, str],
    dest_defs: list[dict[str, Any]],
    answers: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The task's ``custom_fields`` as they land in ``dest_root``, and the drops.

    Three steps, in this order and no other:

    1. **The map.** Each stored value moves to the destination key
       :func:`move.resolve_field_map` chose for it, or is dropped.
    2. **The answers.** What the caller typed into the move dialog is merged
       ONTO the landed values, under DESTINATION keys, and coerced by the
       destination's definitions. After the map, so a required field the
       map already satisfied is not asked twice, and a renamed one is
       answerable under its new key.
    3. **The check.** :func:`custom_fields.assert_required_fields_present`
       runs on the result, which is exactly what will be written.

    Answers first would coerce them by the SOURCE's definitions and then
    map them, which is how ``customer_po`` became "No custom field on this
    project" on the narrow path.
    """
    dest_keys = frozenset(str(d["field_key"]) for d in dest_defs)
    landed, dropped = apply_field_map(
        from_jsonb(task.custom_fields), field_map, dest_keys,
    )
    if answers:
        landed, _changes = cf.apply_values(landed, answers, dest_defs)
    await cf.assert_required_fields_present(db, dest_root, landed)
    return landed, dropped


def completion_correction(
    task: Any, landing_category: str | None,
) -> dict[str, Any]:
    """The ``completed_at`` patch a lane change owes, or nothing.

    A lane's CATEGORY says whether a task in it is finished. Landing a task
    in a lane whose category disagrees with its ``completed_at`` leaves the
    board and the row telling two stories: a DONE task promoted to a board
    with no closing lane sits in "Backlog" with a completion date. The same
    rule :func:`core.remap_task_statuses` applies in bulk, one task at a
    time. ``None`` for the category means the lane did not change.
    """
    if landing_category is None:
        return {}
    closing = landing_category in CLOSING_CATEGORIES
    was_closed = getattr(task, "completed_at", None) is not None
    if closing == was_closed:
        return {}
    return {"completed_at": now() if closing else None}


async def lane_category(db: Any, status_id: str | None) -> str | None:
    """The category of one lane, for :func:`completion_correction`."""
    if not status_id:
        return None
    row = (await db.execute(
        text("SELECT category FROM pm_task_statuses WHERE id = CAST(:id AS uuid)"),
        {"id": status_id},
    )).fetchone()
    return None if row is None else str(row.category)


async def record_drops(
    db: Any, *, task_id: str, dropped: dict[str, Any], by: str,
) -> None:
    """The D-PM-29 timeline entry: what was lost, with its VALUES.

    🔴 The values, not just the keys. D-PM-29 promises the old value is
    readable in history and the move card repeats that promise to the
    member; a join over the keys yielded the names and kept none of it.
    """
    if not dropped:
        return
    await record_activity(
        db, activity_type="system", created_by=by, task_id=task_id,
        body=(
            "Dropped on move, no field in the destination: "
            + "; ".join(f"{k}={dropped[k]!r}" for k in sorted(dropped))
        ),
        meta={"dropped_custom_fields": dropped},
    )
