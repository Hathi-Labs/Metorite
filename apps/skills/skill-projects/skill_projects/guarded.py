"""Class C tools — hard to undo. One act, one card, the impact first.

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.3, §5.2 and §10.2.

Three rules bind every tool here, and ``test_projects_agent_writes.py``
holds each one:

1. **One act, one card.** A tool given a list where it takes one id refuses
   before any card (``_many``). A member who wants five projects archived
   answers five cards. ``bulk_update`` and ``merge_tasks`` take a selection
   because the SELECTION is the act the route performs in one transaction;
   both cap it at ``MAX_BATCH`` and name every task on the card.
2. **No pre-approval.** No argument skips the card, and no tool passes
   ``non_interactive_default``. The card is ``writes._confirm``, the one
   door class B uses, so the same source fence covers both files.
3. **The impact comes first.** The card's first line after ``CARD_NOTE`` is
   ``impact: …`` — the counts the route will act on, read BEFORE the write
   wherever a read exists: the summary and the tree for an archive, the
   task list's total for a status, ``/tags/{id}/impact`` for a tag, the
   preview for a status set. Where no read exists (a type's tasks, a field's
   values, a view's positions) the line names the scope and says the receipt
   carries the count the route reports. A card that says "delete X" with no
   size is a signature bought under a misdescription.

What is deliberately NOT here: hard delete of a project or a task (D-PM-35,
class X until WS-40), a bulk ``delete`` action for the same reason, and a
grant write (spec §12, question 2).
"""

from __future__ import annotations

import json
from typing import Any

from skill_projects import client as _client
from skill_projects.client import (
    GatewayRefusal,
    data,
    delete,
    get,
    post,
    uuid_of,
)
from skill_projects.priority import (
    Removed,
    card_view,
    level_note,
    priority_fields,
    takes_priority,
)
from skill_projects.reads import _day, _task_line
from skill_projects.writes import (
    _REOPENING,
    CANCELLED,
    CARD_NOTE,
    MAX_BATCH,
    OVERLAY_ARGUMENTS,
    _clears,
    _confirm,
    _field_of,
    _fields_block,
    _int_or_none,
    _node,
    _one_named,
    _org_wide,
    _overlay_values,
    _ref,
    _resolve_assignee,
    _split,
    _subtask_counts,
    _subtask_receipt,
    _subtasks_phrase,
    _subtasks_wanted,
    _task,
    _vocab,
    agent_assignee_refusal_as_text,
    ask_about_subtasks,
)

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


CLOSING = ("done", "cancelled")
ONE_ACT = "takes ONE id. A guarded act is one card per row. Ask again for each one."

#: WS-27bn R5d (§9 Q12). The words of the server's 403, in
#: ``reports.DELETE_REFUSED``. A test pins the two as one sentence.
REPORT_DELETE_REFUSED = "Only the author of this report or an admin may delete it."


def _many(value: str) -> bool:
    """A comma or a newline in a one-row argument is a list. A space is
    not: a status is named, and "In progress" is one status."""
    raw = str(value or "").strip()
    return ("," in raw) or ("\n" in raw)


def _guard_card(impact: str, rest: dict[str, Any]) -> str:
    """The class C card body: the note, then ``impact: …``, then the rest.

    The impact line is the tool's own sentence, so it is not fenced the way
    a member value is. Member values inside it already carry their fence.
    """
    body = _fields_block(rest)
    assert body.startswith(CARD_NOTE)
    line = "impact: " + " ".join(impact.split())
    return CARD_NOTE + "\n" + line + body[len(CARD_NOTE) :]


def _open_count(summary: dict[str, Any]) -> int:
    """Open tasks from a summary's ``by_category``, or its total when the
    breakdown is absent. Never a sum of a page (§9.12.7)."""
    by_cat = summary.get("by_category")
    if isinstance(by_cat, dict) and by_cat:
        return sum(int(v or 0) for k, v in by_cat.items() if k not in CLOSING)
    return int(summary.get("tasks") or 0)


def _find_node(rows: list[dict[str, Any]], pid: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("id")) == pid:
            return row
        found = _find_node(row.get("children") or [], pid)
        if found is not None:
            return found
    return None


def _descendants(node: dict[str, Any] | None, *, archived: bool | None = None) -> int:
    """How many projects sit under a node in the tree read.

    ``archived=None`` counts every descendant, ``False`` the live ones and
    ``True`` the filed ones.
    """
    if node is None:
        return 0
    total = 0
    for child in node.get("children") or []:
        is_archived = bool(child.get("archived_at"))
        if archived is None or is_archived == archived:
            total += 1
        total += _descendants(child, archived=archived)
    return total


async def _tree_node(pid: str) -> dict[str, Any] | None:
    tree = (await get("/projects/tree")) or {}
    return _find_node(tree.get("rows") or [], pid)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


#: A title on a many-row card is clipped here, so all fifty rows fit the
#: 4000-character card and the member sees every NAME (S3 review). The
#: clip cuts titles, never rows: a row behind the truncation marker is a
#: row the member never signed.
TITLE_CLIP = 60


def _short_ref(task: dict[str, Any]) -> str:
    title = " ".join(str(task.get("title") or "").split())
    if len(title) > TITLE_CLIP:
        title = title[: TITLE_CLIP - 1] + "…"
    number = task.get("task_number")
    return f"#{number} {data(title)}" if number is not None else data(title)


# ── Projects ─────────────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def archive_project(project_id: str) -> str:
    """File a project and its whole subtree out of the default surfaces.
    ONE project per call. The card leads with how many projects the archive
    stamps and how many open tasks go out of sight with them. Tasks are not
    touched. unarchive_project restores exactly these rows."""
    if _many(project_id):
        return f"archive_project {ONE_ACT}"
    pid, node = await _node(project_id)
    if node.get("archived_at"):
        return f"{data(node.get('name'))} is already archived."
    summary = (await get(f"/projects/nodes/{pid}/summary")) or {}
    below = _descendants(await _tree_node(pid), archived=False)
    open_tasks = _open_count(summary)
    # Both reads are visibility-filtered and the archive is not: it stamps
    # the whole subtree. So the card says "you can see", and never claims
    # the number is the whole.
    impact = (
        f"{_plural(1 + below, 'project')} you can see archived · "
        f"{_plural(open_tasks, 'open task')} you can see leave the boards, lists and "
        "searches with them; the whole subtree is filed, including any rows you cannot see"
    )
    if not await _confirm(
        title="Archive this project?",
        detail=f"{data(node.get('name'))} · {impact}",
        context=_guard_card(
            impact,
            {
                "project": data(node.get("name")),
                "tasks": "not changed; they follow their project out of view",
                "undo": "unarchive_project restores exactly the rows this archive stamps",
            },
        ),
    ):
        return CANCELLED
    result = (await post(f"/projects/nodes/{pid}/archive")) or {}
    return (
        f"Archived {data(node.get('name'))}: {_plural(int(result.get('projects') or 0), 'project')} "
        f"filed, {_plural(int(result.get('open_tasks') or 0), 'open task')} out of view."
        f"\n  project_id: {pid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def unarchive_project(project_id: str) -> str:
    """Bring an archived project back, with every row its own archive filed.
    ONE project per call. A project filed by an ANCESTOR's archive is refused
    by the route: restore that ancestor instead. The card leads with how many
    archived projects sit in the subtree."""
    if _many(project_id):
        return f"unarchive_project {ONE_ACT}"
    pid, node = await _node(project_id)
    if not node.get("archived_at"):
        return f"{data(node.get('name'))} is not archived."
    origin = node.get("archived_root_id")
    if origin and str(origin) != pid:
        # The route's 422 (tree.py `unarchive_node`), said before the card.
        return (
            f"{data(node.get('name'))} was filed by an ancestor's archive (project {origin}). "
            "Restore that project instead; restoring this one alone would leave it "
            "visible inside an archived tree."
        )
    filed = _descendants(await _tree_node(pid), archived=True)
    impact = (
        f"up to {_plural(1 + filed, 'project')} restored — this one and the archived rows "
        "under it that its own archive filed"
    )
    if not await _confirm(
        title="Restore this project?",
        detail=f"{data(node.get('name'))} · archived {_day(node.get('archived_at'))}",
        context=_guard_card(
            impact,
            {
                "project": data(node.get("name")),
                "note": "a subproject archived on its own before this stays archived",
            },
        ),
    ):
        return CANCELLED
    result = (await post(f"/projects/nodes/{pid}/unarchive")) or {}
    return (
        f"Restored {data(node.get('name'))}: {_plural(int(result.get('projects') or 0), 'project')} "
        f"back on the surface.\n  project_id: {pid}"
    )


# ── The order of a node among its siblings (WS-46 P7, G13) ─────────────────
#
# The tree drag's maths (``lib/treeDrop.ts``), mirrored: a float position, the
# midpoint of the two neighbours, and a one-time spread of a sibling set that
# was never ordered. ``tests/unit/test_projects_project_fields.py`` holds the
# two constants equal to the TypeScript.

#: What a first, unordered sibling set is spread across (``POSITION_SPAN``).
POSITION_SPAN = 65536
#: The gap below which two positions are re-spread (``MIN_GAP``).
MIN_GAP = 1e-6
PLACES = ("first", "last", "before", "after")


def _place_of(place: str) -> tuple[str, str] | str:
    """``first``, ``last``, ``before <full_id>`` or ``after <full_id>`` ->
    ``(word, sibling id)``, ``("", "")`` for none, or the refusal."""
    raw = " ".join(str(place or "").replace(":", " ").split())
    if not raw:
        return "", ""
    word, _, rest = raw.partition(" ")
    word = word.lower()
    if word in ("first", "last") and not rest:
        return word, ""
    if word in ("before", "after") and rest:
        return word, uuid_of(rest, "place")
    return (
        "place is first, last, before <full_id> or after <full_id>, where the id is a "
        "sibling's full_id from projects_tree."
    )


def _position_at(others: list[dict[str, Any]], index: int) -> float | None:
    """``positionAt``: the float for a node landing at *index* among
    *others*, or ``None`` when the set must be spread first."""
    if any(not isinstance(n.get("position"), (int, float)) for n in others):
        return None
    before = float(others[index - 1]["position"]) if index > 0 else None
    after = float(others[index]["position"]) if index < len(others) else None
    if before is None and after is None:
        return POSITION_SPAN / 2
    if before is None:
        return after / 2  # type: ignore[operator]
    if after is None:
        return before + POSITION_SPAN / 2
    if after - before < MIN_GAP:
        return None
    return (before + after) / 2


def _spread(others: list[dict[str, Any]], moving: str, index: int) -> list[tuple[str, float]]:
    """``spreadPositions``: every sibling, evenly spread, the node at *index*."""
    order = [str(n["id"]) for n in others[:index]] + [moving]
    order += [str(n["id"]) for n in others[index:]]
    step = POSITION_SPAN / (len(order) + 1)
    return [(nid, step * (i + 1)) for i, nid in enumerate(order)]


def _plan_place(
    rows: list[dict[str, Any]], pid: str, parent_id: str | None, word: str, sibling: str
) -> tuple[float, list[tuple[str, float]], str] | str | None:
    """``(position, spread, phrase)`` for the node *pid* placed by *word*
    under *parent_id*, ``None`` when it is there already, or the refusal.

    The siblings are the live children of the parent in the tree's order,
    which is the order the app draws.
    """
    if parent_id is None:
        siblings = rows
    else:
        parent = _find_node(rows, parent_id)
        siblings = (parent or {}).get("children") or []
    live = [n for n in siblings if not n.get("archived_at")]
    others = [n for n in live if str(n.get("id")) != pid]
    if word in ("first", "last"):
        index = 0 if word == "first" else len(others)
        phrase = f"{word} among {_plural(len(others) + 1, 'sibling')}"
    else:
        at = next((i for i, n in enumerate(others) if str(n.get("id")) == sibling), None)
        if at is None:
            return (
                f"{sibling} is not a live sibling there. The siblings are: "
                + ", ".join(f"{data(n.get('name'))} ({n.get('id')})" for n in others)
            )
        index = at if word == "before" else at + 1
        phrase = f"{word} {data(others[at].get('name'))}"
    now = next((i for i, n in enumerate(live) if str(n.get("id")) == pid), None)
    if now is not None and now == index:
        return None
    position = _position_at(others, index)
    if position is not None:
        return position, [], phrase
    spread = _spread(others, pid, index)
    mine = next(p for nid, p in spread if nid == pid)
    return mine, [(nid, p) for nid, p in spread if nid != pid], phrase


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def move_project(
    project_id: str, parent_project_id: str = "", to_top_level: bool = False, place: str = ""
) -> str:
    """Re-parent a project, folder or subproject under another node, or
    make it a space (to_top_level=true). ONE node per call. Every task under
    it is re-rooted, its types and counter follow the new root, and a task
    whose lane the new set lacks is re-pointed by category. The card leads
    with the subtree size and the task count.
    place sets its order among its siblings, as the tree drag does: first,
    last, before <full_id> or after <full_id> of a sibling. With place and no
    parent, the node stays under its parent and only its order changes. A
    sibling set that was never ordered is numbered once, and the card says
    how many siblings that touches."""
    if _many(project_id):
        return f"move_project {ONE_ACT}"
    placed = _place_of(place)
    if isinstance(placed, str):
        return placed
    word, sibling = placed
    reorder = bool(word) and not parent_project_id.strip() and not to_top_level
    if not reorder and bool(parent_project_id.strip()) == bool(to_top_level):
        return (
            "Pass parent_project_id, or to_top_level=true to make it a space, not both. "
            "To change only the order, pass place alone."
        )
    pid, node = await _node(project_id)
    found = await _move_target(pid, node, parent_project_id, word, reorder)
    if isinstance(found, str):
        return found
    target, parent, where, tree_rows = found
    plan = _plan_place(tree_rows, pid, target, word, sibling) if word else (0.0, [], "")
    if plan is None:
        return f"{data(node.get('name'))} is already {word} there. Nothing changed."
    if isinstance(plan, str):
        return plan
    position, spread, phrase = plan
    card = await _move_card(
        pid, node, parent, target, where, tree_rows, phrase if word else "", len(spread), reorder
    )
    if isinstance(card, str):
        return card
    title, detail, impact, rest = card
    if not await _confirm(title=title, detail=detail, context=_guard_card(impact, rest)):
        return CANCELLED
    # The spread first, every row of it, as the tree drag writes it: the
    # node's own position means nothing until its siblings carry one.
    for sid, at in spread:
        sibling_id = uuid_of(sid, "sibling")
        await post(
            f"/projects/nodes/{sibling_id}/move",
            {"parent_project_id": target, "position": at},
        )
    payload: dict[str, Any] = {"parent_project_id": target}
    if word:
        payload["position"] = position
    result = (await post(f"/projects/nodes/{pid}/move", payload)) or {}
    if reorder:
        return f"Placed {data(node.get('name'))} {phrase} under {where}.\n  project_id: {pid}"
    remapped = (result.get("statuses_remapped") or {}).get("moved")
    tail = f" {remapped} task(s) changed lane." if remapped else ""
    spot = f", {phrase}" if word else ""
    return f"Moved {data(node.get('name'))} under {where}{spot}.{tail}\n  project_id: {pid}"


async def _move_target(
    pid: str, node: dict[str, Any], parent_project_id: str, word: str, reorder: bool
) -> tuple[str | None, dict[str, Any] | None, str, list[dict[str, Any]]] | str:
    """``(target parent id, parent row, its name, the tree)`` for a move, or
    the refusal. A reorder keeps the node's own parent."""
    parent: dict[str, Any] | None = None
    parent_id = ""
    if parent_project_id.strip():
        parent_id, parent = await _node(parent_project_id)
        if parent_id == pid:
            return "A project cannot be moved under itself."
        if not word and str(parent.get("id")) == str(node.get("parent_project_id")):
            return f"{data(node.get('name'))} is already under {data(parent.get('name'))}."
    elif not reorder and node.get("parent_project_id") is None:
        return f"{data(node.get('name'))} is already a space."
    rows = ((await get("/projects/tree")) or {}).get("rows") or []
    if not reorder:
        where = "the top level (a space)" if parent is None else data(parent.get("name"))
        return parent_id or None, parent, where, rows
    current = node.get("parent_project_id")
    if not current:
        return None, None, "the top level", rows
    target = uuid_of(current, "parent_project_id")
    return target, None, data((_find_node(rows, target) or {}).get("name") or "its parent"), rows


async def _move_card(
    pid: str,
    node: dict[str, Any],
    parent: dict[str, Any] | None,
    target: str | None,
    where: str,
    rows: list[dict[str, Any]],
    phrase: str,
    spread: int,
    reorder: bool,
) -> tuple[str, str, str, dict[str, Any]] | str:
    """``(title, detail, impact, rest)`` of the move card, or the refusal.
    *target* is the new parent's id as the caller gave it, canonical."""
    pid = uuid_of(pid, "project_id")
    name = data(node.get("name"))
    order: dict[str, Any] = {"place": phrase} if phrase else {}
    if spread:
        order["order"] = (
            f"the siblings had no order yet, so {_plural(spread, 'other sibling')} "
            "get a position too, once"
        )
    if reorder:
        rest = {"project": name, "under": where, **order, "undo": "move it back with place"}
        impact = f"{name} placed {phrase} under {where}"
        return "Reorder this project?", f"{name} → {phrase} under {where}", impact, rest
    summary = (await get(f"/projects/nodes/{pid}/summary")) or {}
    subtree = _find_node(rows, pid)
    if parent is not None and target and subtree is not None and _find_node(
        subtree.get("children") or [], target
    ):
        # `assert_no_project_cycle`'s 422, said before the card.
        return f"{data(parent.get('name'))} is inside {name}. A tree cannot loop."
    tasks = int(summary.get("tasks") or 0)
    impact = (
        f"{_plural(1 + _descendants(subtree), 'project')} moved · "
        f"{_plural(tasks, 'task')} re-rooted under {where}"
    )
    rest = {
        "project": name,
        "to": where,
        **order,
        "statuses": "a task whose lane the destination set lacks is re-pointed by category",
        "undo": "move it back; the counter and types follow the new root",
    }
    return "Move this project?", f"{name} → {where} · {impact}", impact, rest


# ── Tasks ────────────────────────────────────────────────────────────────────


async def _status_name(task: dict[str, Any]) -> str:
    rows = await _vocab(str(task.get("project_id")), "statuses")
    for row in rows:
        if str(row.get("id")) == str(task.get("status_id")):
            return data(row.get("name"))
    return "(unknown)"


@_annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def archive_task(task_id: str, include_subtasks: str = "") -> str:
    """Shelve one task from every board, list, calendar and search. ONE
    task per call, from any status; archiving is a filing decision and
    claims no outcome. include_subtasks (yes or no): yes shelves its
    subtasks too. When the task has subtasks and no answer is given, the
    tool asks first (D-PM-38: the app takes them along by default). The card
    carries the title, the lane and what happens to the subtasks.
    unarchive_task is the undo, and it restores one task."""
    if _many(task_id):
        return f"archive_task {ONE_ACT}"
    wanted = _subtasks_wanted(include_subtasks)
    tid, task = await _task(task_id)
    if task.get("archived_at"):
        return f"{_ref(task)} is already archived."
    count = await _subtask_counts(tid)
    if wanted is None and count.total:
        return ask_about_subtasks(task, count.total, "archive", count.capped)
    lane = await _status_name(task)
    if wanted and count.total:
        tail = f"{_subtasks_phrase(count.total, 'archive', count.capped)} archived with it"
    else:
        tail = f"{_subtasks_phrase(count.open, 'complete', count.capped)} left on the board"
    impact = f"1 task archived from lane {lane} · {tail}"
    rest = {"task": _ref(task), "status": lane, "undo": "unarchive_task"}
    if not await _confirm(
        title="Archive this task?",
        detail=f"{_ref(task)} · {impact}",
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    params = {"include_subtasks": True} if wanted else None
    row = await post(f"/projects/tasks/{tid}/archive", params=params)
    merged = {**task, **(row if isinstance(row, dict) else {})}
    return "\n".join(
        ["Archived:", *_task_line(merged), *_subtask_receipt(row, "subtasks_archived", "archive")]
    )


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def merge_tasks(target_task_id: str, source_task_ids: str) -> str:
    """Fold one or more tasks into a survivor, in the SAME project. Their
    comments, attachments, links, subtasks and watchers move to the
    survivor; each source is archived and points at it. The card names the
    survivor and every source. Not reversible: the sources become stubs."""
    if _many(target_task_id):
        return f"merge_tasks {ONE_ACT.replace('ONE id', 'ONE survivor')}"
    ids = [uuid_of(t, "source_task_id") for t in _split(source_task_ids)]
    if not ids:
        return "Name at least one source task."
    if len(ids) > MAX_BATCH:
        return f"That is {len(ids)} sources. The limit for one card is {MAX_BATCH}."
    tid, target = await _task(target_task_id)
    if tid in ids:
        return "A task cannot be merged into itself."
    if target.get("merged_into_task_id"):
        return f"{_ref(target)} has itself been merged away. Merge into the task it went to."
    sources = [(await _task(s))[1] for s in ids]
    gone = [s for s in sources if s.get("merged_into_task_id")]
    if gone:
        return "Already merged away: " + ", ".join(_ref(s) for s in gone) + "."
    wrong = [s for s in sources if str(s.get("project_id")) != str(target.get("project_id"))]
    if wrong:
        return (
            "Merge is same-project only. Move first: "
            + ", ".join(_ref(s) for s in wrong)
            + f" are not in the project of {_ref(target)}."
        )
    impact = f"{_plural(len(sources), 'task')} merged into {_ref(target)} and archived as stubs"
    rest: dict[str, Any] = {"survivor": _ref(target)}
    for i, s in enumerate(sources):
        rest[f"source {i + 1}"] = _short_ref(s)
    rest["moves"] = "comments, attachments, links, subtasks and watchers go to the survivor"
    if not await _confirm(
        title=f"Merge {_plural(len(sources), 'task')} into {_ref(target)}?",
        detail=impact,
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    row = await post(f"/projects/tasks/{tid}/merge", {"sources": ids})
    merged = (row or {}).get("merged") or ids
    out = [f"Merged {len(merged)} into {_ref(target)}:", *_task_line({**target, **(row or {})})]
    return "\n".join(out)


BULK_ACTIONS = ("archive", "unarchive")
_CLEARABLE = {"due": "due_at", "start": "start_date", "estimate": "estimate_mins"}


def _bulk_patch(
    status: str,
    importance: Any,
    due: str,
    start: str,
    estimate_mins: int,
    clear: str,
    priority: str = "",
    important: str = "",
    leveraged: str = "",
) -> dict[str, Any] | str:
    """The ``patch`` half of a bulk body, or the refusal.

    A selection is mixed, so each passed flag is written as it stands (no
    ``current``), and a flag not passed is left alone on every task. This is
    the bulk bar's rule (``BULK_FLAG_OPTIONS``, review 2026-09-24). So
    ``important=true`` writes 2 over a stored 3, which still reads as
    Important (``priority_fields``).
    """
    patch: dict[str, Any] = {}
    if status.strip():
        patch["status"] = status.strip()
    flags = priority_fields(
        priority=priority, important=important, leveraged=leveraged, importance=importance
    )
    if isinstance(flags, str):
        return flags
    patch.update(flags)
    if due.strip():
        patch["due_at"] = due.strip()
    if start.strip():
        patch["start_date"] = start.strip()
    est = _int_or_none(estimate_mins)
    if est is not None:
        patch["estimate_mins"] = est
    cleared = _clears(clear, _CLEARABLE, patch)
    if isinstance(cleared, str):
        return cleared
    return {**patch, **cleared}


async def _bulk_body(
    ids: list[str],
    patch: dict[str, Any],
    assignees_add: str,
    assignees_remove: str,
    tags_add: str,
    tags_remove: str,
    action: str,
) -> dict[str, Any] | str:
    """The wire body of ``POST /projects/tasks/bulk``, or the refusal."""
    body: dict[str, Any] = {"task_ids": ids}
    if patch:
        body["patch"] = patch
    for key, raw in (("assignees_add", assignees_add), ("assignees_remove", assignees_remove)):
        # H-236: only an assignee to ADD can start an agent's run.
        people = [
            await _resolve_assignee(a, dispatch=key == "assignees_add") for a in _split(raw)
        ]
        if people:
            body[key] = people
    for key, raw in (("tags_add", tags_add), ("tags_remove", tags_remove)):
        values = _split(raw)
        if values:
            body[key] = values
    verb = str(action or "").strip().lower()
    if verb == "delete":
        return "Deleting tasks is not offered here (D-PM-35). Archive is the remove verb."
    if verb and verb not in BULK_ACTIONS:
        return f"action is {' or '.join(BULK_ACTIONS)}."
    if verb and len(body) > 1:
        return f"'{verb}' goes on its own. Send it without a patch, assignees or tags."
    if verb:
        body["action"] = verb
    if len(body) == 1:
        return "Nothing to change. Pass a field, assignees, tags or an action."
    return body


def _bulk_subtasks(body: dict[str, Any], include_subtasks: str) -> dict[str, str]:
    """``include_subtasks`` on a bulk act, asked once for the whole selection.

    It means something with ``action: archive`` and with a status patch,
    where the route cascades into a Done lane. It is set on *body* here.
    Returns the card's ``subtasks`` line. A bulk act does not read each
    task's subtree first, so the line states the rule and not a count.
    """
    wanted = _subtasks_wanted(include_subtasks)
    archiving = body.get("action") == "archive"
    closing = "status" in (body.get("patch") or {})
    if not archiving and not closing:
        if wanted is not None:
            raise GatewayRefusal(
                "include_subtasks goes with action archive, or with a status in a Done lane."
            )
        return {}
    if wanted:
        body["include_subtasks"] = True
        if archiving:
            return {"subtasks": "archived with each task"}
        return {"subtasks": "open subtasks completed too, where a task lands in a Done lane"}
    return {"subtasks": "stay as they are (include_subtasks=yes takes them too)"}


def _bulk_impact(body: dict[str, Any], n: int) -> str:
    verb = body.get("action")
    if verb:
        return f"{_plural(n, 'task')} {verb}d"
    described: dict[str, Any] = {}
    for key, value in (body.get("patch") or {}).items():
        # The flags as a member reads them, never the stored number (H-173).
        if key in ("importance", "leveraged"):
            described.update(card_view({key: value}))
        else:
            described[key] = value
    for key in ("assignees_add", "assignees_remove", "tags_add", "tags_remove"):
        if key in body:
            described[key] = ", ".join(body[key])
    return f"{_plural(n, 'task')} changed: " + ", ".join(
        f"{k} → {'cleared' if v is None else v}" for k, v in described.items()
    )


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
@takes_priority
@agent_assignee_refusal_as_text
async def bulk_update(
    task_ids: str,
    status: str = "",
    due: str = "",
    start: str = "",
    estimate_mins: int = 0,
    clear: str = "",
    assignees_add: str = "",
    assignees_remove: str = "",
    tags_add: str = "",
    tags_remove: str = "",
    action: str = "",
    priority: str = "",
    important: str = "",
    leveraged: str = "",
    importance: Removed = None,
    include_subtasks: str = "",
    personal: str = "",
) -> str:
    """One change across a selection of tasks, in one transaction. task_ids
    is comma-separated, at most 50. status is by NAME and is resolved per
    task's project. due and start YYYY-MM-DD, clear empties due, start,
    estimate, important, leveraged or priority (both flags). A flag you do
    not pass stays as it is on every task. assignees_add/remove and
    tags_add/remove take comma-separated values. action is archive or
    unarchive, on its own with no other change. Deleting is not offered.
    include_subtasks (yes or no) is asked once for the whole selection: yes
    with action archive shelves each task's subtasks too, and yes with a
    status in a Done lane completes their open subtasks. Without it the
    subtasks stay as they are, and the card says so.
    personal sets the member's OWN overlay on every task, as the My Tasks
    bulk bar does: a JSON object with set_my_overlay's arguments, for example
    {"disposition": "someday", "context": "@home"}. It goes on its own, with
    no other change.
    The card names every task and the exact change."""
    ids = [uuid_of(t, "task_id") for t in _split(task_ids)]
    if not ids:
        return "Give at least one task id."
    if len(ids) > MAX_BATCH:
        return f"That is {len(ids)} tasks. The limit for one card is {MAX_BATCH}."
    if str(personal or "").strip():
        others = [
            name for name, value in (
                ("status", status), ("due", due), ("start", start), ("clear", clear),
                ("estimate_mins", estimate_mins), ("assignees_add", assignees_add),
                ("assignees_remove", assignees_remove), ("tags_add", tags_add),
                ("tags_remove", tags_remove), ("action", action), ("priority", priority),
                ("important", important), ("leveraged", leveraged),
                ("include_subtasks", include_subtasks),
            )
            if str(value or "").strip() not in ("", "0")
        ]
        if others:
            return (
                f"personal goes on its own. Send {', '.join(others)} in another call: the "
                "overlay is yours, and the other fields are the team's."
            )
        return await _bulk_personal(ids, personal)
    patch = _bulk_patch(
        status, importance, due, start, estimate_mins, clear, priority, important, leveraged
    )
    if isinstance(patch, str):
        return patch
    body = await _bulk_body(
        ids, patch, assignees_add, assignees_remove, tags_add, tags_remove, action
    )
    if isinstance(body, str):
        return body
    subtasks = _bulk_subtasks(body, include_subtasks)
    tasks = [(await _task(t))[1] for t in ids]
    impact = _bulk_impact(body, len(tasks))
    rest: dict[str, Any] = dict(subtasks)
    for i, t in enumerate(tasks):
        rest[f"task {i + 1}"] = _short_ref(t)
    if not await _confirm(
        title=f"Change {_plural(len(tasks), 'task')} at once?",
        detail=impact,
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    result = (await post("/projects/tasks/bulk", body)) or {}
    out = [f"Applied to {result.get('applied', 0)} of {result.get('requested', len(ids))} tasks."]
    door = ("subtasks_archived", "archive") if body.get("action") else ("subtasks_completed", "complete")
    out.extend(_subtask_receipt(result, *door))
    for row in result.get("skipped") or []:
        out.append(f"- skipped {row.get('task_id')}: {data(row.get('reason'))}")
    for row in result.get("failed") or []:
        out.append(f"- failed {row.get('task_id')}: {data(row.get('reason'))}")
    # Rows for what the route APPLIED. A skipped or failed id printed as a
    # task row would read as done on the receipt card.
    applied = {str(r.get("task_id")) for r in result.get("results") or [] if isinstance(r, dict)}
    for t in tasks:
        if str(t.get("id")) in applied:
            out.extend(_task_line(t))
            # The level asked for, against each task's own due date.
            out.extend(level_note(priority, {**t, **patch}))
    return "\n".join(out)


async def _bulk_personal(ids: list[str], personal: str) -> str:
    """``action: personal`` (``bulk.py`` ``validate_personal``): the member's
    own overlay on every task of the selection, under one class C card."""
    try:
        given = json.loads(personal)
    except ValueError:
        given = None
    if not isinstance(given, dict):
        return (
            "personal is a JSON object with set_my_overlay's arguments, for example "
            f'{{"disposition": "someday"}}. The arguments are {", ".join(OVERLAY_ARGUMENTS)}.'
        )
    values = await _overlay_values(given, {})
    if isinstance(values, str):
        return values
    if not values:
        return "Nothing to change in personal."
    tasks = [(await _task(t))[1] for t in ids]
    shown = ", ".join(
        f"{k} → {'cleared' if v is None else (v.get('email') if isinstance(v, dict) else v)}"
        for k, v in values.items()
    )
    impact = f"{_plural(len(tasks), 'task')}: your own overlay only · {shown}"
    rest: dict[str, Any] = {"seen by": "you only. The board does not change"}
    finished = [t for t in tasks if t.get("completed_at")]
    if values.get("disposition") in _REOPENING and finished:
        rest["reopens"] = (
            f"{_plural(len(finished), 'finished task')} reopen on the board (D77)"
        )
    for i, t in enumerate(tasks):
        rest[f"task {i + 1}"] = _short_ref(t)
    if not await _confirm(
        title=f"Set your triage of {_plural(len(tasks), 'task')}?",
        detail=impact,
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    body = {"task_ids": ids, "action": "personal", "personal": values}
    result = (await post("/projects/tasks/bulk", body)) or {}
    out = [
        f"Your triage is set on {result.get('applied', 0)} of "
        f"{result.get('requested', len(ids))} tasks: {shown}."
    ]
    for row in result.get("skipped") or []:
        out.append(f"- skipped {row.get('task_id')}: {data(row.get('reason'))}")
    for row in result.get("failed") or []:
        out.append(f"- failed {row.get('task_id')}: {data(row.get('reason'))}")
    return "\n".join(out)


# ── The timeline ─────────────────────────────────────────────────────────────


async def _timeline_row(task_id: str, activity_id: str, kind: str) -> dict[str, Any] | None:
    tid = uuid_of(task_id, "task_id")
    aid = uuid_of(activity_id, "activity_id")
    payload = await get(
        f"/projects/tasks/{tid}/timeline", {"kind": kind, "page": 1, "page_size": 50}
    )
    for row in (payload or {}).get("rows") or []:
        if str(row.get("id")) == aid:
            return row
    return None


@_annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def delete_comment(task_id: str, comment_id: str) -> str:
    """Delete a comment the member wrote. The words are cleared and the row
    is hidden; replies keep their place. The author only, checked before the
    card. The card shows the exact text that goes."""
    if _many(comment_id):
        return f"delete_comment {ONE_ACT}"
    tid, task = await _task(task_id)
    row = await _timeline_row(tid, comment_id, "comments")
    if row is None:
        return f"No comment with that id is in the latest 50 comments of {_ref(task)}."
    author = str(row.get("created_by") or "").lower()
    # Through the module, not a bound name: the acting member is a run
    # ContextVar read at call time, and a test patches it on the module.
    if author != _client.current_user_email().lower():
        return f"Only the author can delete a comment. This one is by {data(author)}."
    aid = uuid_of(comment_id, "comment_id")
    impact = "1 comment deleted; its text is cleared for everyone"
    if not await _confirm(
        title="Delete your comment?",
        detail=f"on {_ref(task)}",
        context=_guard_card(impact, {"task": _ref(task), "comment": data(row.get("body"))}),
    ):
        return CANCELLED
    await delete(f"/projects/comments/{aid}")
    return f"Deleted your comment on {_ref(task)}.\n  full_id: {tid}"


def _change_lines(changes: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """``(field, "now → restored")`` per change, labels over ids."""
    out: list[tuple[str, str]] = []
    for c in changes:
        field = str(c.get("field") or "?")
        now = c.get("new_label", c.get("new"))
        back = c.get("old_label", c.get("old"))
        out.append((field, f"{data(now)} → {data(back)}"))
    return out


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def revert_activity(task_id: str, activity_id: str) -> str:
    """Undo one field change from a task's timeline: the values it recorded
    as "old" are written back. activity_id comes from task_detail's
    timeline. The revert is itself a change, so the timeline shows it
    happened. The card lists each field as now → restored. A move between
    projects or a status change is not revertible here."""
    if _many(activity_id):
        return f"revert_activity {ONE_ACT}"
    tid, task = await _task(task_id)
    row = await _timeline_row(tid, activity_id, "events")
    if row is None or row.get("type") != "field_change":
        return f"No field change with that id is in the latest 50 events of {_ref(task)}."
    changes = [c for c in ((row.get("meta") or {}).get("changes") or []) if isinstance(c, dict)]
    if not changes:
        return "That change carries nothing to restore."
    lines = _change_lines(changes)
    impact = f"{_plural(len(lines), 'field')} restored on {_ref(task)}"
    aid = uuid_of(activity_id, "activity_id")
    if not await _confirm(
        title="Revert this change?",
        detail=f"{_ref(task)} · " + ", ".join(f"{f}: {v}" for f, v in lines),
        context=_guard_card(impact, {"task": _ref(task), **dict(lines)}),
    ):
        return CANCELLED
    result = (await post(f"/projects/activities/{aid}/revert")) or {}
    reverted = ", ".join(result.get("reverted") or []) or "nothing"
    skipped = result.get("skipped") or []
    tail = f" Not revertible here: {', '.join(skipped)}." if skipped else ""
    return f"Reverted {reverted} on {_ref(task)}.{tail}\n  full_id: {tid}"


# ── Vocabulary deletes ───────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_status(project_id: str, status: str, move_to: str = "") -> str:
    """Delete a lane, moving the tasks in it to move_to (a status NAME in
    the same set) first. The card leads with how many tasks move: the count
    the statuses read returns per lane, which is the count the route acts
    on. The last lane, the last Done lane (D79) and the last lane that
    closes a task cannot be deleted, and the tool says so before any card."""
    if _many(status):
        return f"delete_status {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    # ONE read for the lanes AND the counts (admin.py `list_statuses`): its
    # docstring says the count exists so a delete can be offered safely.
    # A second count through the task list was visibility- and
    # triage-filtered, and the route's own `count_where` is neither.
    listing = (await get(f"/projects/nodes/{pid}/statuses")) or {}
    lanes = listing.get("rows") or []
    counts = listing.get("counts") or {}
    row = _one_named(lanes, status, "status", "statuses")
    sid = uuid_of(str(row.get("id")), "status_id")
    survivors = [lane for lane in lanes if str(lane.get("id")) != sid]
    # The route's three 409s (admin.py `delete_status`), said before the card.
    if not survivors:
        return f"{data(row.get('name'))} is the only status here. A project needs at least one."
    # D79: every status set keeps a Done status, because Mark done needs one.
    if str(row.get("category")) == "done" and not any(
        str(lane.get("category")) == "done" for lane in survivors
    ):
        return (
            f"{data(row.get('name'))} is the last Done status here. Every status set "
            "keeps at least one Done status. Add another Done status first."
        )
    if not any(str(lane.get("category")) in CLOSING for lane in survivors):
        return (
            f"{data(row.get('name'))} is the only lane that closes a task. Add another Done "
            "or Cancelled lane first."
        )
    owner = (await get(f"/projects/nodes/{pid}/status-set")) or {}
    if owner.get("may_edit") is False:
        return "You may not edit this project's statuses. It needs the settings permission."
    in_use = int(counts.get(sid) or 0)
    target: dict[str, Any] | None = None
    if move_to.strip():
        target = _one_named(lanes, move_to, "status", "statuses")
        if str(target.get("id")) == sid:
            return "A status cannot hand its tasks to itself."
    elif in_use:
        return (
            f"{data(row.get('name'))} still holds {_plural(in_use, 'task')}. "
            "Pass move_to, the status they go to."
        )
    where = data(target.get("name")) if target else "(none needed; the lane is empty)"
    impact = f"1 lane deleted · {_plural(in_use, 'task')} moved to {where}"
    if not await _confirm(
        title="Delete this status?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))} · {impact}",
        context=_guard_card(
            impact,
            {"status": data(row.get("name")), "category": row.get("category"), "move to": where},
        ),
    ):
        return CANCELLED
    params: dict[str, Any] = {}
    if target is not None:
        params["move_to"] = uuid_of(str(target.get("id")), "move_to")
    result = (await delete(f"/projects/statuses/{sid}", params)) or {}
    # The route returns `tasks_affected` (admin.py `delete_status`). The
    # first version read `moved`, which a fake that echoed anything let
    # through (R8).
    moved = result.get("tasks_affected", in_use)
    return (
        f"Deleted status {data(row.get('name'))}; {_plural(int(moved or 0), 'task')} moved to {where}."
        f"\n  status_id: {sid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def set_status_set(project_id: str, mode: str, copy_from: str = "") -> str:
    """Switch where a project's lanes come from. mode=inherit drops its own
    set and uses the parent's; mode=own gives it a set of its own, copied
    from copy_from (a project id) or from the set it uses today. Every task
    is re-pointed by category in one transaction. The card leads with the
    preview's counts: tasks that change lane, that become done, that
    reopen."""
    if _many(project_id):
        return f"set_status_set {ONE_ACT}"
    which = str(mode or "").strip().lower()
    if which not in ("inherit", "own"):
        return "mode is inherit or own."
    pid, node = await _node(project_id)
    current = (await get(f"/projects/nodes/{pid}/status-set")) or {}
    if current.get("may_edit") is False:
        return "You may not edit this project's statuses. It needs the settings permission."
    if which == "inherit" and not current.get("can_inherit"):
        return f"{data(node.get('name'))} is a space. It has nothing to inherit from."
    if which == "inherit" and not current.get("owns"):
        return f"{data(node.get('name'))} already inherits its statuses."
    payload: dict[str, Any] = {"mode": which}
    source_name = ""
    if copy_from.strip():
        if which != "own":
            return "copy_from goes with mode=own."
        src_id, src = await _node(copy_from)
        payload["copy_from"] = src_id
        source_name = data(src.get("name"))
    preview = (await post(f"/projects/nodes/{pid}/status-set/preview", payload)) or {}
    moving = int(preview.get("moving") or 0)
    completing = int(preview.get("completing") or 0)
    reopening = int(preview.get("reopening") or 0)
    lanes = ", ".join(data(lane.get("name")) for lane in preview.get("lanes") or [])
    impact = (
        f"{_plural(moving, 'task')} change lane · {completing} become done · {reopening} reopen"
    )
    rest: dict[str, Any] = {"project": data(node.get("name")), "mode": which, "lanes after": lanes}
    if source_name:
        rest["copied from"] = source_name
    if which == "inherit":
        rest["inherits from"] = data(current.get("inherit_from_name"))
    if not await _confirm(
        title="Switch this project's status set?",
        detail=f"{data(node.get('name'))} → {which} · {impact}",
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    result = (await post(f"/projects/nodes/{pid}/status-set", payload)) or {}
    moved = result.get("moved", moving)
    return (
        f"{data(node.get('name'))} now {'inherits its statuses' if which == 'inherit' else 'owns its statuses'}; "
        f"{_plural(int(moved or 0), 'task')} changed lane.\n  project_id: {pid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_type(project_id: str, type_name: str) -> str:
    """Delete a task type from the project's root. Tasks that carry it keep
    existing, untyped. The route counts them as it deletes and the receipt
    carries the number. The Epic system type and an org-wide type cannot
    be deleted from here."""
    if _many(type_name):
        return f"delete_type {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    row = _one_named(await _vocab(pid, "types"), type_name, "type")
    if _org_wide(row):
        return f"{data(row.get('name'))} is organization-wide. It is not deleted from a project."
    if row.get("is_system"):
        return f"{data(row.get('name'))} is a system type and cannot be deleted."
    kid = uuid_of(str(row.get("id")), "type_id")
    impact = (
        f"1 type deleted · every task in {data(node.get('name'))}'s tree that carries it becomes "
        "untyped (the receipt carries the count)"
    )
    if not await _confirm(
        title="Delete this task type?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))}",
        context=_guard_card(impact, {"type": data(row.get("name"))}),
    ):
        return CANCELLED
    result = (await delete(f"/projects/types/{kid}")) or {}
    return (
        f"Deleted type {data(row.get('name'))}; {_plural(int(result.get('tasks_untyped') or 0), 'task')} "
        f"now untyped.\n  type_id: {kid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_field(project_id: str, field: str) -> str:
    """Delete a custom field AND every value filed under its key, across
    the project's tree. The route counts the values as it clears them and
    the receipt carries the number. An org-wide field is not deleted from
    here."""
    if _many(field):
        return f"delete_field {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    row = _field_of(await _vocab(pid, "fields"), field)
    if _org_wide(row):
        return f"{data(row.get('name'))} is organization-wide. It is not deleted from a project."
    fid = uuid_of(str(row.get("id")), "field_id")
    impact = (
        f"1 field deleted · every value under key {data(row.get('field_key'))} in "
        f"{data(node.get('name'))}'s tree is cleared (the receipt carries the count)"
    )
    if not await _confirm(
        title="Delete this custom field and its values?",
        detail=f"{data(row.get('name'))} (key {data(row.get('field_key'))}) in {data(node.get('name'))}",
        context=_guard_card(
            impact, {"field": data(row.get("name")), "type": row.get("field_type")}
        ),
    ):
        return CANCELLED
    result = (await delete(f"/projects/fields/{fid}")) or {}
    cleared = int(((result.get("cascaded") or {}).get("values_cleared")) or 0)
    return (
        f"Deleted field {data(row.get('name'))}; {_plural(cleared, 'value')} cleared."
        f"\n  field_id: {fid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_tag(project_id: str, tag: str) -> str:
    """Delete a tag and strip it from every task in the project's tree.
    The card leads with the impact read: how many tasks, in how many
    trees. An org-wide tag is not deleted from here."""
    if _many(tag):
        return f"delete_tag {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    row = _one_named(await _vocab(pid, "tags"), tag, "tag")
    if _org_wide(row):
        return f"{data(row.get('name'))} is organization-wide. It is not deleted from a project."
    gid = uuid_of(str(row.get("id")), "tag_id")
    hit = (await get(f"/projects/tags/{gid}/impact")) or {}
    tasks = int(hit.get("tasks") or 0)
    impact = f"1 tag deleted · {_plural(tasks, 'task')} untagged"
    if not await _confirm(
        title="Delete this tag?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))} · {impact}",
        context=_guard_card(impact, {"tag": data(row.get("name"))}),
    ):
        return CANCELLED
    result = (await delete(f"/projects/tags/{gid}")) or {}
    stripped = int(((result.get("cascaded") or {}).get("tasks_untagged")) or 0)
    return f"Deleted tag {data(row.get('name'))}; {_plural(stripped, 'task')} untagged.\n  tag_id: {gid}"


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def merge_tags(project_id: str, tag: str, into: str) -> str:
    """Fold one tag into another and delete the first. Every task wearing
    the first gets the second once. Same project only, root-local only.
    The card leads with how many tasks are retagged."""
    if _many(tag) or _many(into):
        return f"merge_tags {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    rows = await _vocab(pid, "tags")
    source = _one_named(rows, tag, "tag")
    target = _one_named(rows, into, "tag")
    if source.get("id") == target.get("id"):
        return "A tag cannot be merged into itself."
    if _org_wide(source) or _org_wide(target):
        return "An organization-wide tag is not merged from a project."
    sid = uuid_of(str(source.get("id")), "tag_id")
    tid = uuid_of(str(target.get("id")), "into")
    # The list's `task_count` excludes archived tasks; the merge's rewrite
    # does not (tags.py `_rewrite`). `/impact` counts with the merge's scope.
    hit = (await get(f"/projects/tags/{sid}/impact")) or {}
    worn = int(hit.get("tasks") or 0)
    impact = (
        f"{_plural(worn, 'task')} retagged {data(source.get('name'))} → "
        f"{data(target.get('name'))} · 1 tag deleted"
    )
    if not await _confirm(
        title="Merge these tags?",
        detail=f"{data(source.get('name'))} into {data(target.get('name'))} in {data(node.get('name'))}",
        context=_guard_card(
            impact, {"from": data(source.get("name")), "into": data(target.get("name"))}
        ),
    ):
        return CANCELLED
    result = (await post(f"/projects/tags/{sid}/merge", {"into_tag_id": tid})) or {}
    return (
        f"Merged {data(result.get('merged') or source.get('name'))} into "
        f"{data(result.get('into') or target.get('name'))}; "
        f"{_plural(int(result.get('retagged') or 0), 'task')} retagged.\n  tag_id: {tid}"
    )


# ── Views, reports, attachments ──────────────────────────────────────────────


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def delete_view(project_id: str, view_name: str) -> str:
    """Delete a saved view, its hand-arranged order and every member's
    arrangement of it. The route counts both as it deletes and the receipt
    carries the numbers."""
    if _many(view_name):
        return f"delete_view {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    rows = ((await get(f"/projects/nodes/{pid}/views")) or {}).get("rows") or []
    row = _one_named(rows, view_name, "view")
    vid = uuid_of(str(row.get("id")), "view_id")
    impact = (
        "1 view deleted · its manual order and every member's arrangement of it go with it "
        "(the receipt carries the counts)"
    )
    if not await _confirm(
        title="Delete this view?",
        detail=f"{data(row.get('name'))} ({row.get('view_type')}) in {data(node.get('name'))}",
        context=_guard_card(impact, {"view": data(row.get("name")), "type": row.get("view_type")}),
    ):
        return CANCELLED
    result = (await delete(f"/projects/views/{vid}")) or {}
    cascaded = result.get("cascaded") or {}
    return (
        f"Deleted view {data(row.get('name'))}; {cascaded.get('positions', 0)} positions and "
        f"{cascaded.get('user_states', 0)} member arrangements went with it.\n  view_id: {vid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def report_delete(report_id: str) -> str:
    """Delete a saved report definition, with its schedule and recipients.
    The numbers it renders are not stored, so nothing else is lost."""
    if _many(report_id):
        return f"report_delete {ONE_ACT}"
    rid = uuid_of(report_id, "report_id")
    row = (await get(f"/projects/reports/{rid}")) or {}
    if row.get("can_delete") is False:
        # WS-27bn R5d (§9 Q12). The server computed the rule. Say it, and
        # show no card that the DELETE would refuse.
        return f"{REPORT_DELETE_REFUSED}\n  report_id: {rid}"
    scope = "the portfolio"
    if row.get("project_id"):
        _scope_id, scope_node = await _node(str(row.get("project_id")))
        scope = data(scope_node.get("name"))
    impact = "1 report deleted, with its schedule and recipient list"
    if not await _confirm(
        title="Delete this report?",
        detail=f"{data(row.get('name'))} · {scope}",
        context=_guard_card(impact, {"report": data(row.get("name")), "scope": scope}),
    ):
        return CANCELLED
    await delete(f"/projects/reports/{rid}")
    return f"Deleted report {data(row.get('name'))}.\n  report_id: {rid}"


@_annotate(read_only=False, destructive=True, idempotent=True, open_world=False)
async def delete_attachment(task_id: str, attachment_id: str) -> str:
    """Detach a file from a task. The bytes are kept, because the same
    file may hang off another task. attachment_id comes from task_detail.
    The card names the file."""
    if _many(attachment_id):
        return f"delete_attachment {ONE_ACT}"
    tid, task = await _task(task_id)
    aid = uuid_of(attachment_id, "attachment_id")
    files = ((await get(f"/projects/tasks/{tid}/attachments")) or {}).get("rows") or []
    row = next((f for f in files if str(f.get("id")) == aid), None)
    if row is None:
        return f"{_ref(task)} has no attachment with that id. task_detail lists them."
    impact = f"1 file detached from {_ref(task)}; the bytes are kept"
    if not await _confirm(
        title="Detach this file?",
        detail=f"{data(row.get('name'))} from {_ref(task)}",
        context=_guard_card(impact, {"file": data(row.get("name")), "task": _ref(task)}),
    ):
        return CANCELLED
    await delete(f"/projects/tasks/{tid}/attachments/{aid}")
    return f"Detached {data(row.get('name'))} from {_ref(task)}.\n  full_id: {tid}"


__all__ = [  # noqa: RUF022 — grouped by what they act on
    "archive_project",
    "unarchive_project",
    "move_project",
    "archive_task",
    "merge_tasks",
    "bulk_update",
    "delete_comment",
    "revert_activity",
    "delete_status",
    "set_status_set",
    "delete_type",
    "delete_field",
    "delete_tag",
    "merge_tags",
    "delete_view",
    "report_delete",
    "delete_attachment",
]
