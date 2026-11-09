"""Cycles in the chat (WS-39 SP-5a, Metorite task 964, D195).

Spec: ``project-docs/specs/project_management_app.md`` §13.2 (the calls). The
store is ``gateway/routes/projects/cycles.py`` (SP-1). A cycle (a sprint) is a
time box over one node: a space, a folder or a project. The route file says
"cycle" and so do these tools. The board says "sprint" for the flag only.

Six tools, each on its own manifest row (D136):

* ``list_cycles`` and ``cycle_detail`` are class A reads.
* ``create_cycle`` and ``plan_cycle`` are class B, one card each. A bulk add
  and a removal show every task on the one card (D124.2).
* ``start_cycle`` and ``close_cycle`` are class C, one card each, with the
  counts first. A start freezes the committed list. A close moves the
  unfinished tasks.

**The flag.** ``PROJECTS_SPRINTS_ORGS`` is the flag, and the routes answer 404
``Not Found`` while it does not hold the organization. Each tool reads before
its card, so a flag that is off shows a sentence and no card. The routes
decide who may act and which task may join which cycle. These tools quote the
server and do not guess. They send no organization and no member (R5).

**Not here.** ``update_cycle`` and ``delete_cycle`` stay in
``manifest.PLANNED``. The MCP gets the cycle of a task as a read only (SP-5a).
A class C act over the MCP needs a preview and a confirm step (SP-5b).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from acb_common.paging import paged

from skill_projects.client import GatewayRefusal, data, delete, get, post, uuid_of
from skill_projects.guarded import ONE_ACT, _guard_card, _many
from skill_projects.reads import legend
from skill_projects.writes import (
    CANCELLED,
    MAX_BATCH,
    _confirm,
    _fields_block,
    _fits_on_card,
    _node,
    _ref,
    _split,
    _task,
)

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


#: What the member reads while ``PROJECTS_SPRINTS_ORGS`` does not hold the org.
CYCLES_OFF = "Cycles are not on for this organization yet."
#: The bounds of the cycle columns: the route's ``NAME_MAX``, ``GOAL_MAX`` and
#: ``SPAN_MAX_DAYS``. A test holds these equal to the route's.
NAME_MAX = 120
GOAL_MAX = 500
SPAN_MAX_DAYS = 366
#: The most choices of one close: the route's ``CHOICES_MAX``.
CHOICES_MAX = 500
#: How many tasks a close card names. The card says the total.
CLOSE_CARD_NAMES = 15
#: The default page of a cycle list and of the tasks of one cycle.
CYCLES_PAGE = 10
TASKS_PAGE = 30
#: The task categories that need no choice at a close: the route's
#: ``core.CLOSING_CATEGORIES``.
_CLOSING = ("done", "cancelled")


def _is_off(exc: GatewayRefusal) -> bool:
    """True for the bare 404 of a dark flag. A missing cycle says ``Cycle not found``."""
    return getattr(exc, "status", None) == 404 and str(getattr(exc, "detail", "")).strip().lower() == "not found"


def _mins(value: Any) -> str:
    try:
        total = int(value or 0)
    except (TypeError, ValueError):
        return "no estimate"
    if total <= 0:
        return "no estimate"
    hours, rest = divmod(total, 60)
    return f"{hours}h{rest:02d}" if hours else f"{rest}m"


def _span(cycle: dict[str, Any]) -> str:
    return f"{cycle.get('starts_on')} to {cycle.get('ends_on')}"


async def _read_cycle(cycle_id: str) -> tuple[str, dict[str, Any] | None]:
    """``(canonical id, the read of the cycle)``, or ``(id, None)`` while the flag is off."""
    cid = uuid_of(cycle_id, "cycle_id")
    try:
        return cid, (await get(f"/projects/cycles/{cid}")) or {}
    except GatewayRefusal as exc:
        if _is_off(exc):
            return cid, None
        raise


def _cycle_line(cycle: dict[str, Any]) -> str:
    seen = int(cycle.get("tasks") or 0)
    hidden = int(cycle.get("hidden") or 0)
    count = f"{seen} tasks" + (f" (+{hidden} hidden from you)" if hidden else "")
    return (
        f"- {data(cycle.get('name'))} · {cycle.get('state')} · {_span(cycle)} · {count}"
        f" · cycle_id {cycle.get('id')}"
    )


# ── Class A: the reads ───────────────────────────────────────────────────────


@_annotate(read_only=True, idempotent=True, open_world=False)
async def list_cycles(project_id: str, limit: int = CYCLES_PAGE, offset: int = 0) -> str:
    """The cycles (sprints) of a project, a folder or a space, newest first.
    Each has its name, its state (planned, active or closed), its dates, its
    task count and its cycle_id. cycle_detail reads one cycle with its tasks."""
    pid, node = await _node(project_id)
    try:
        got = (await get(f"/projects/nodes/{pid}/cycles")) or {}
    except GatewayRefusal as exc:
        if _is_off(exc):
            return CYCLES_OFF
        raise
    page = paged(got.get("cycles") or [], offset, limit, default=CYCLES_PAGE,
                 noun="cycles", param="offset")
    out = [legend(), f"Cycles of {data(node.get('name'))}, newest first ({page.total} in all):"]
    out += [_cycle_line(c) for c in page.items]
    if not page.items:
        out.append("(no cycle yet)")
    if page.line:
        out.append(page.line)
    out.append(f"  project_id: {pid}")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True, open_world=False)
async def cycle_detail(cycle_id: str, limit: int = TASKS_PAGE, offset: int = 0) -> str:
    """The status of one cycle (sprint): its goal, its state, its dates, what
    the start committed, the review minutes of each member, and its tasks with
    their status and estimate. The hidden count is the tasks that are hidden
    from you."""
    cid, got = await _read_cycle(cycle_id)
    if got is None:
        return CYCLES_OFF
    cycle = got.get("cycle") or {}
    out = [
        legend(),
        f"Cycle {data(cycle.get('name'))} · {cycle.get('state')} · {_span(cycle)}",
        f"  goal: {data(cycle.get('goal') or '(none yet)')}",
    ]
    committed = got.get("committed")
    if isinstance(committed, dict):
        out.append(
            f"  committed at the start: {committed.get('tasks', 0)} tasks"
            f" · {_mins(committed.get('estimate_mins'))}"
            + (f" (+{committed['hidden']} hidden from you)" if committed.get("hidden") else "")
        )
    for row in got.get("capacity") or []:
        out.append(f"  review capacity: {data(row.get('member_email'))} {_mins(row.get('review_minutes'))}")
    tasks = got.get("tasks") or []
    page = paged(tasks, offset, limit, default=TASKS_PAGE, noun="tasks", param="offset")
    hidden = int(got.get("hidden") or 0)
    out.append(f"  tasks: {len(tasks)} you can see" + (f", {hidden} hidden from you" if hidden else ""))
    for row in page.items:
        done = " · archived" if row.get("archived") else ""
        out.append(
            f"- #{row.get('task_number')} {data(row.get('title'))} · "
            f"{data(row.get('status_name') or 'no status')} · {_mins(row.get('estimate_mins'))}"
            f"{done} · task_id {row.get('id')}"
        )
    if page.line:
        out.append(page.line)
    out.append(f"  cycle_id: {cid}\n  project_id: {cycle.get('project_id')}")
    return "\n".join(out)


# ── Class B: plan a cycle, and plan its tasks ────────────────────────────────


def _date_or_none(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False, card_write=True)
async def create_cycle(project_id: str, name: str, starts_on: str, ends_on: str, goal: str = "") -> str:
    """Plan a new cycle (sprint) on a project, a folder or a space: a name, a
    start date and an end date as YYYY-MM-DD, and an optional goal. The cycle
    starts as planned and holds no task. The card shows the node, the name and
    the dates. plan_cycle adds tasks. start_cycle starts it."""
    title = " ".join(str(name or "").split())
    if not title:
        return "A cycle needs a name. Ask the member for a short name."
    if len(title) > NAME_MAX:
        return f"The name is {len(title)} characters, and a cycle name holds at most {NAME_MAX}. Make it shorter."
    first, last = _date_or_none(starts_on), _date_or_none(ends_on)
    if first is None or last is None:
        return "A cycle needs a start date and an end date, each as YYYY-MM-DD. Ask the member for the dates."
    if last < first:
        return "The cycle ends before it starts. Ask the member for an end date on or after the start date."
    if (last - first).days > SPAN_MAX_DAYS:
        return f"A cycle lasts at most {SPAN_MAX_DAYS} days. Ask the member for a nearer end date."
    aim = " ".join(str(goal or "").split())
    if len(aim) > GOAL_MAX:
        return f"The goal is {len(aim)} characters, and a goal holds at most {GOAL_MAX}. Make it shorter."
    pid, node = await _node(project_id)
    if node.get("archived_at") or node.get("archived"):
        return (
            f"{data(node.get('name'))} is archived, so it takes no new cycle. "
            "Restore it first, or plan the cycle on another project."
        )
    # The read before the card: it also says when cycles are off.
    try:
        await get(f"/projects/nodes/{pid}/cycles")
    except GatewayRefusal as exc:
        if _is_off(exc):
            return CYCLES_OFF
        raise
    payload: dict[str, Any] = {"name": title, "starts_on": first.isoformat(), "ends_on": last.isoformat()}
    if aim:
        payload["goal"] = aim
    card: dict[str, Any] = {"project": data(node.get("name")), **payload}
    if not await _confirm(
        title="Plan this cycle?",
        detail=f"on {data(node.get('name'))}",
        context=_fields_block(card),
    ):
        return CANCELLED
    row = (await post(f"/projects/nodes/{pid}/cycles", payload)) or {}
    return (
        f"Planned the cycle {data(title)} on {data(node.get('name'))} ({first} to {last}).\n"
        f"  cycle_id: {row.get('id')}\n  project_id: {pid}"
    )


def _ids(csv: str, what: str) -> list[str]:
    seen: dict[str, None] = {}
    for part in _split(csv):
        seen[uuid_of(part, what)] = None
    return list(seen)


async def _refs(ids: list[str]) -> tuple[list[str], str]:
    """The card names of tasks *ids*, or ``([], sentence)`` for the first that is not found."""
    names: list[str] = []
    for tid in ids:
        try:
            _bound, row = await _task(tid)
        except GatewayRefusal as exc:
            if getattr(exc, "status", None) == 404:
                return [], (
                    f"Task {tid} is not found, or it is hidden from you. "
                    "Nothing was changed. Check the task_id with find_tasks."
                )
            raise
        names.append(_ref(row))
    return names, ""


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False, card_write=True)
async def plan_cycle(cycle_id: str, add_task_ids: str = "", remove_task_ids: str = "") -> str:
    """Put tasks into a cycle (sprint) or take tasks out of it, at most 50 per
    call. add_task_ids and remove_task_ids are comma lists of task_id values.
    ONE card shows the cycle and every task. A task sits in one open cycle at a
    time, so to move a task, remove it from its cycle first. A closed cycle
    takes no change."""
    adds = _ids(add_task_ids, "add_task_ids")
    drops = _ids(remove_task_ids, "remove_task_ids")
    if not adds and not drops:
        return "Name the tasks: add_task_ids to put tasks in, or remove_task_ids to take them out."
    if len(adds) + len(drops) > MAX_BATCH:
        return (
            f"{len(adds) + len(drops)} tasks is too many for one card. Send at most {MAX_BATCH} "
            "task ids in one call, and call again for the rest."
        )
    if set(adds) & set(drops):
        return "A task is in both lists. Put each task in add_task_ids or in remove_task_ids."
    cid, got = await _read_cycle(cycle_id)
    if got is None:
        return CYCLES_OFF
    cycle = got.get("cycle") or {}
    if cycle.get("state") == "closed":
        return (
            f"The cycle {data(cycle.get('name'))} is closed, so it takes no change. "
            "Plan the tasks in a planned or an active cycle."
        )
    add_names, refused = await _refs(adds)
    if refused:
        return refused
    drop_names, refused = await _refs(drops)
    if refused:
        return refused
    card: dict[str, Any] = {"cycle": data(cycle.get("name")), "state": cycle.get("state")}
    if add_names:
        card[f"add {len(add_names)} tasks"] = "; ".join(add_names)
    if drop_names:
        card[f"remove {len(drop_names)} tasks"] = "; ".join(drop_names)
    if not _fits_on_card(card):
        return "These tasks do not fit on one card, so nothing was changed. Send fewer tasks and call again."
    if not await _confirm(
        title="Change the tasks of this cycle?",
        detail=f"{data(cycle.get('name'))}: +{len(adds)} / -{len(drops)}",
        context=_fields_block(card),
    ):
        return CANCELLED
    # The removes first, then the adds in id order: one lock order for every
    # bulk add. Each task is its own transaction in the route, so a stop
    # leaves the earlier tasks in, and the answer says how many.
    done_add = done_drop = 0
    stopped = ""
    for tid in sorted(drops):
        try:
            await delete(f"/projects/cycles/{cid}/tasks/{tid}")
            done_drop += 1
        except GatewayRefusal as exc:
            stopped = f"Stopped at task {tid}: {getattr(exc, 'detail', '') or exc}"
            break
    if not stopped:
        for tid in sorted(adds):
            try:
                await post(f"/projects/cycles/{cid}/tasks", {"task_id": tid})
                done_add += 1
            except GatewayRefusal as exc:
                stopped = f"Stopped at task {tid}: {getattr(exc, 'detail', '') or exc}"
                break
    head = f"{data(cycle.get('name'))}: added {done_add} of {len(adds)}, removed {done_drop} of {len(drops)}."
    return f"{head} {stopped}\n  cycle_id: {cid}" if stopped else f"{head}\n  cycle_id: {cid}"


# ── Class C: start, and close with a carry-over ──────────────────────────────


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def start_cycle(cycle_id: str, goal: str = "") -> str:
    """Start a planned cycle (sprint). A start needs a goal: the stored one, or
    goal here. The start saves the list of tasks and their estimates as the
    committed list, and it does not change later. A node has one active cycle,
    so close the active one first. The card names the cycle, the goal and the
    counts. ONE cycle per call."""
    if _many(cycle_id):
        return f"start_cycle {ONE_ACT.replace('id', 'cycle_id')}"
    cid, got = await _read_cycle(cycle_id)
    if got is None:
        return CYCLES_OFF
    cycle = got.get("cycle") or {}
    name = data(cycle.get("name"))
    if cycle.get("state") == "active":
        return f"The cycle {name} has already started."
    if cycle.get("state") == "closed":
        return f"The cycle {name} is closed. Plan a new cycle."
    aim = " ".join(str(goal or cycle.get("goal") or "").split())
    if not aim:
        return (
            f"The cycle {name} has no goal. A cycle needs a goal before it starts. "
            "Ask the member for one sentence that says what the cycle is for."
        )
    if len(aim) > GOAL_MAX:
        return f"The goal is {len(aim)} characters, and a goal holds at most {GOAL_MAX}. Make it shorter."
    # One active cycle for each node: say so before the card.
    siblings = ((await get(f"/projects/nodes/{uuid_of(cycle.get('project_id'), 'project_id')}/cycles")) or {}).get("cycles") or []
    other = next((c for c in siblings if c.get("state") == "active" and str(c.get("id")) != cid), None)
    if other is not None:
        return (
            f"The cycle {data(other.get('name'))} is active on the same project. "
            "Close it first, then start this one."
        )
    tasks = got.get("tasks") or []
    hidden = int(got.get("hidden") or 0)
    minutes = sum(max(int(t.get("estimate_mins") or 0), 0) for t in tasks)
    impact = (
        f"The start commits {len(tasks)} tasks ({_mins(minutes)})"
        + (f" and {hidden} more that are hidden from you" if hidden else "")
        + ". The committed list does not change after the start"
    )
    if not await _confirm(
        title="Start this cycle?",
        detail=name,
        context=_guard_card(impact, {"cycle": name, "goal": aim, "tasks": len(tasks)}),
    ):
        return CANCELLED
    body: dict[str, Any] = {"goal": aim} if str(goal or "").strip() else {}
    row = (await post(f"/projects/cycles/{cid}/start", body)) or {}
    return (
        f"Started the cycle {name}: {row.get('committed_tasks', len(tasks))} tasks committed"
        + (f", {row.get('hidden')} hidden from you" if row.get("hidden") else "")
        + f".\n  cycle_id: {cid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False, open_world=False)
async def close_cycle(cycle_id: str, carry_over: str = "") -> str:
    """Close an active cycle (sprint). Done and cancelled tasks stay in it as
    its record. Every unfinished task needs one place to go: carry_over is
    backlog, or the cycle_id of the next cycle. The card names the cycle, the
    counts and the place. ONE cycle per call."""
    if _many(cycle_id):
        return f"close_cycle {ONE_ACT.replace('id', 'cycle_id')}"
    cid, got = await _read_cycle(cycle_id)
    if got is None:
        return CYCLES_OFF
    cycle = got.get("cycle") or {}
    name = data(cycle.get("name"))
    if cycle.get("state") == "planned":
        return f"The cycle {name} has not started. Start it, or leave it planned."
    if cycle.get("state") == "closed":
        return f"The cycle {name} is already closed."
    tasks = got.get("tasks") or []
    unfinished = [t for t in tasks if not t.get("archived") and t.get("status_category") not in _CLOSING]
    finished = len(tasks) - len(unfinished)
    if len(unfinished) > CHOICES_MAX:
        return f"{len(unfinished)} unfinished tasks is too many for one close. Move some to the backlog first."
    target = str(carry_over or "").strip()
    place = "the backlog"
    choice: dict[str, Any] = {"choice": "backlog"}
    if unfinished and not target:
        siblings = ((await get(f"/projects/nodes/{uuid_of(cycle.get('project_id'), 'project_id')}/cycles")) or {}).get("cycles") or []
        nexts = [c for c in siblings if c.get("state") in ("planned", "active") and str(c.get("id")) != cid]
        lines = [
            f"{len(unfinished)} tasks of {name} are not finished. Ask the member where they go: "
            "the backlog (carry_over backlog), or the next cycle (carry_over with its cycle_id)."
        ]
        lines += [f"- {data(c.get('name'))} · {c.get('state')} · cycle_id {c.get('id')}" for c in nexts]
        return "\n".join(lines)
    if unfinished and target.lower() != "backlog":
        nid, nxt = await _read_cycle(target)
        if nxt is None:
            return CYCLES_OFF
        if nid == cid:
            return "The tasks cannot carry over to the cycle that closes. Pick another cycle, or the backlog."
        if (nxt.get("cycle") or {}).get("state") == "closed":
            return f"The cycle {data((nxt.get('cycle') or {}).get('name'))} is closed. Pick a planned or an active cycle."
        place = f"the cycle {data((nxt.get('cycle') or {}).get('name'))}"
        choice = {"choice": "next", "cycle_id": nid}
    shown = [_ref({"task_number": t.get("task_number"), "title": t.get("title")})
             for t in paged(unfinished, 0, CLOSE_CARD_NAMES, default=CLOSE_CARD_NAMES).items]
    more = len(unfinished) - len(shown)
    impact = (
        f"The close ends {name}. {finished} finished or filed tasks stay in it. "
        f"{len(unfinished)} unfinished tasks move to {place}"
    )
    rest: dict[str, Any] = {"cycle": name, "unfinished": len(unfinished), "carry to": place}
    if shown:
        rest["tasks"] = "; ".join(shown) + (f"; and {more} more" if more > 0 else "")
    if not await _confirm(
        title="Close this cycle?",
        detail=f"{name}: {len(unfinished)} unfinished to {place}",
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    choices = [{"task_id": str(t.get("id")), **choice} for t in sorted(unfinished, key=lambda t: str(t.get("id")))]
    row = (await post(f"/projects/cycles/{cid}/close", {"choices": choices})) or {}
    moved = row.get("moved") or {}
    return (
        f"Closed the cycle {name}. Moved {moved.get('next', 0)} tasks to the next cycle and "
        f"{moved.get('backlog', 0)} to the backlog.\n  cycle_id: {cid}"
    )


__all__ = [  # noqa: RUF022 — reads first, then the writes
    "list_cycles",
    "cycle_detail",
    "create_cycle",
    "plan_cycle",
    "start_cycle",
    "close_cycle",
]
