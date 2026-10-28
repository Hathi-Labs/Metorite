"""The sub-agent contract of OA-9: the jobs in, and the items out (WS-67).

Spec ``project-docs/specs/one_assistant_context.md`` §6 (D117 item 5), slice
OA-9a, "GO for the parser only" (§4.5). Fence:
``tests/unit/test_subagent_contract.py``.

**The caller and the flag.** OA-9b builds ``spawn_subagents``
(``agent_tools``) on these parsers, and it is the one caller. The tool is dark
behind ``SPAWN_SUBAGENTS_ORGS`` (:func:`spawn_subagents_on`, OWNER-ONLY). This
module opens no connection, reads no Redis and calls no model (R5).

**The worker (§4.5, OA-9a and OA-9b).** The internal agent ``worker`` runs a
job. :func:`bind_job` puts the job on the frame of the parent. The worker
reads it with :func:`bound_job`, and :func:`worker_refusal` refuses a run that
has no job. :func:`child_pin` is the allow-list pair that keeps a child to the
tools of its job.

**The jobs in (§6.2, §6.5).** :func:`parse_jobs` checks the ``jobs`` argument
of ``spawn_subagents``. At most :data:`MAX_JOBS` jobs run, and each job past
the cap gets a name in the answer. A job runs only when each of its tools is
a tool of the parent run, reads only, and starts no other agent. A job that
names a tool pack does not run until OA-9c (§4.5, OA-9b item 3). Each refused
job gets one line that says why, so the model can call again.

**Read first, writes after (§6.5).** A child reads. The parser refuses a job
that names a tool that is not read-only, and the line tells the model to do
that step itself after the children answer. OA-9d adds children that write.

**The items out (§6.4).** :func:`parse_items` reads the one JSON object that a
child ends with, ``{"items": [{"value": ..., "kind": ..., "source": ...}]}``.
The parse is strict, as ``system_one.parse_answers`` holds its shape. Output
in any other shape becomes ONE item of kind ``inferred``, with the source
``unstructured``, that holds the whole text. So no text of a child is lost,
and no text becomes ``quoted`` that the child did not mark. The main
assistant states each ``inferred`` item as a guess.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from acb_skills.tool_annotations import get_annotations

__all__ = [
    "CHILD_LOOP_BUDGET_S",
    "CHILD_SOURCE",
    "EXTRA_CALL_TOOLS",
    "MEMBER_SOURCE",
    "DEFAULT_TIER",
    "FANOUT_TIMEOUT_S",
    "INTERNAL_AGENTS",
    "ITEM_KEYS",
    "JOB_KEY",
    "JOB_KEYS",
    "JOB_TIMEOUT_S",
    "KINDS",
    "MAX_CHILD_ROUNDS",
    "MAX_JOBS",
    "MAX_NAMED_OVERFLOW",
    "MAX_SPAWNS_PER_TURN",
    "MEMBER_TOOLS",
    "SPAWN_TOOL",
    "TIERS",
    "UNSTRUCTURED",
    "WEB_TOOLS",
    "WORKER_NAME",
    "Item",
    "Job",
    "JobPlan",
    "annotated_read_only",
    "bind_job",
    "bound_job",
    "child_pin",
    "child_tool_scope",
    "parse_items",
    "parse_jobs",
    "spawn_subagents_on",
    "spawn_lock",
    "take_spawn",
    "unstructured",
    "worker_refusal",
]

# ── The caps of §6.2 ────────────────────────────────────────────────────────

#: The name of the spawn tool. A child never holds it.
SPAWN_TOOL = "spawn_subagents"

#: The most jobs that run in one spawn call. It is the cap of the one fan-out,
#: ``agent_tools._PARALLEL_MAX``, and the fence pins the two together.
MAX_JOBS = 5

#: The most jobs past the cap that a refusal line names. The rest are counted.
MAX_NAMED_OVERFLOW = 10

#: The most spawn calls in one turn, so a turn runs at most 10 children.
MAX_SPAWNS_PER_TURN = 2

#: The most model rounds of one child.
MAX_CHILD_ROUNDS = 4

#: A job stops after this many seconds and gives an error item.
JOB_TIMEOUT_S = 120.0

#: The time that the tool loop of a child may use. It is below
#: :data:`JOB_TIMEOUT_S`, which also holds the loads, so the final call with
#: no tools runs before the job clock stops the child (§6.6).
CHILD_LOOP_BUDGET_S = 90.0

#: A whole fan-out stops after this many seconds.
FANOUT_TIMEOUT_S = 180.0

#: The tiers a job may ask for. ``fast`` sends ``tier-fast``, and ``main``
#: sends the resolved tier of the parent (§6.6).
TIERS = ("fast", "main")
DEFAULT_TIER = "main"

#: The keys of one job. Any other key refuses the job, so a task in the shape
#: of ``call_agents_parallel`` (``agent``, ``message``) never runs as a job.
JOB_KEYS = frozenset({"job", "packs", "tools", "tier"})

#: The web reads that a child may hold beside the read-only tools (§6.5).
WEB_TOOLS = frozenset({"web_search", "fetch_page"})

# ── The items of §6.4 ───────────────────────────────────────────────────────

#: The kinds of an item. ``quoted`` has a source that holds the value word
#: for word. ``inferred`` is a guess.
KINDS = ("quoted", "inferred")

#: The keys of one item. An item with any other key is not the contract.
ITEM_KEYS = frozenset({"value", "kind", "source"})

#: The source of the one item that holds output in any other shape.
UNSTRUCTURED = "unstructured"

#: The longest job text or tool name that a refusal line repeats.
_LABEL_CHARS = 60

#: Tools that ask the member or show a card. They are marked read-only, but a
#: child has no member to ask (§6.5: each card is the main assistant's).
MEMBER_TOOLS = frozenset({"ask_questions", "ask_user", "request_confirmation", "share_artifact"})

#: Tools that add model calls of their own, and so break the 4-round cap of a
#: child (§6.6). ``decide`` runs a second model call inside the tool.
EXTRA_CALL_TOOLS = frozenset({"decide"})

#: The source that only the member's own words may carry. A child item never
#: holds it (§6.4): the parser rewrites it to :data:`CHILD_SOURCE`.
MEMBER_SOURCE = "member"
CHILD_SOURCE = "sub-agent (claimed member)"

_CALL_SHAPE = (
    f"Error: jobs must be a JSON array of 1 to {MAX_JOBS} jobs. Each job is an object "
    'like {"job": "<a job that holds all it needs>", "tools": ["web_search"], '
    '"tier": "fast"}. "tools" and "tier" are optional, and "tier" is "fast" or "main".'
)


@dataclass(frozen=True)
class Job:
    """One job that may run. ``number`` is its place in the call, from 1."""

    number: int
    job: str
    tools: tuple[str, ...]
    tier: str


@dataclass(frozen=True)
class JobPlan:
    """What one spawn call runs, and one line for each job that does not run.

    ``error`` is set when the call itself is not an array of jobs. Then no
    job runs.
    """

    jobs: tuple[Job, ...] = ()
    refused: tuple[str, ...] = ()
    error: str = ""

    def notes(self) -> str:
        """The lines that the spawn tool adds to its answer, or ``""``."""
        return "\n".join(line for line in (self.error, *self.refused) if line)


@dataclass(frozen=True)
class Item:
    """One fact of a child, with its kind and its source."""

    value: str
    kind: str
    source: str


# ── The jobs in ─────────────────────────────────────────────────────────────


def annotated_read_only(name: str) -> bool:
    """True when *name* is a web read, or the risk registry says it reads only.

    A tool with no entry in the registry is not read-only, so an unknown tool
    fails closed. An agent's own tool is in the registry once its module has
    loaded (``tool_annotations.annotate``).
    """
    if name in WEB_TOOLS:
        return True
    return (get_annotations(name) or {}).get("read_only") is True


def _handoff_tools() -> frozenset[str]:
    """The tools that start another agent. A child holds none of them (§6.2).

    ``egress.DELEGATION_TOOLS`` is the one list of the delegation tools. It is
    read here, at the call, because ``egress`` imports the agent framework.
    """
    from acb_skills.egress import DELEGATION_TOOLS

    return frozenset({SPAWN_TOOL, *DELEGATION_TOOLS})


def _clip(text: str) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= _LABEL_CHARS:
        return one_line
    return one_line[: _LABEL_CHARS - 3].rstrip() + "..."


def _label(number: int, raw: Any) -> str:
    """``job 6 ("the first words")``: the name of a job in a refusal line."""
    text = raw.get("job") if isinstance(raw, dict) else None
    if isinstance(text, str) and text.strip():
        return f'job {number} ("{_clip(text)}")'
    return f"job {number}"


def _tier_of(raw: Any) -> str | None:
    if raw is None:
        return DEFAULT_TIER
    if not isinstance(raw, str):
        return None
    tier = raw.strip().casefold()
    return tier if tier in TIERS else None


def _tools_of(raw: Any) -> tuple[str, ...] | None:
    """The tool names of a job, once each and in order, or ``None`` when bad."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            return None
        name = entry.strip()
        if name not in names:
            names.append(name)
    return tuple(names)


def _refusal(
    raw: Any,
    parent: frozenset[str],
    read_only: Callable[[str], bool],
    handoff: frozenset[str],
) -> tuple[str, Job | None]:
    """Why the job *raw* cannot run, or ``("", job)`` when it can.

    The checks run in a fixed order, and the first one that fails names the
    reason. The returned job has ``number`` 0, and the caller sets it.
    """
    if not isinstance(raw, dict):
        return "it is not a JSON object.", None
    extra = sorted(str(k) for k in raw if k not in JOB_KEYS)
    if extra:
        return (f"it has the key {', '.join(_clip(k) for k in extra)}. A job has only "
                "the keys job, packs, tools and tier."), None
    text = raw.get("job")
    if not isinstance(text, str) or not text.strip():
        return "it has no job text.", None
    tier = _tier_of(raw.get("tier"))
    if tier is None:
        return 'its tier must be "fast" or "main".', None
    if raw.get("packs") not in (None, []):
        # §4.5, OA-9b item 3: a pack in a child waits for OA-9c.
        return ("a sub-agent cannot load a tool pack yet. Name its tools "
                "instead."), None
    tools = _tools_of(raw.get("tools"))
    if tools is None:
        return "its tools must be a list of tool names.", None
    for name in tools:
        if name in MEMBER_TOOLS:
            return (f"a sub-agent cannot ask the member or show a card, so it cannot "
                    f"hold '{_clip(name)}'."), None
        if name in handoff:
            return (f"a sub-agent cannot start another agent, so it cannot hold "
                    f"'{_clip(name)}'."), None
        if name in EXTRA_CALL_TOOLS:
            return (f"'{_clip(name)}' makes model calls of its own, and a "
                    "sub-agent has a fixed number of model calls. Do that step "
                    "yourself."), None
        if name not in parent:
            return (f"this chat has no tool '{_clip(name)}', and a sub-agent gets "
                    "only tools of this chat."), None
        if not read_only(name):
            return (f"'{_clip(name)}' is not a read-only tool, and a sub-agent may "
                    "only read. Do that step yourself after the sub-agents "
                    "answer."), None
    return "", Job(number=0, job=text.strip(), tools=tools, tier=tier)


def _one_of_each(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook``: a repeated key raises, so no value is lost.

    ``json.loads`` keeps the LAST value of a repeated key. That would drop a
    quoted fact in silence, so a repeat makes the answer another shape.
    """
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("repeated key")
        out[key] = value
    return out


def _loads(raw: Any) -> Any:
    """The JSON value of *raw*, or ``None`` when it is not JSON."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw, object_pairs_hook=_one_of_each)
    except (ValueError, RecursionError):
        return None


def parse_jobs(
    raw: str | list[Any],
    *,
    parent_tools: Collection[str],
    read_only: Callable[[str], bool] = annotated_read_only,
    delegating: Collection[str] = (),
) -> JobPlan:
    """Check the ``jobs`` of one spawn call against the parent run (§6.2).

    *raw* is the JSON text that the model sent, or its parsed list.
    *parent_tools* is the ``run_tools`` of the parent run. A job may name only
    those tools. *delegating* names more tools that start another agent, such as the
    specialist tools of the parent. They get the hand-off reason.
    *read_only* says whether a tool reads only. OA-9b passes the
    parent's own view of its tools, and the default reads the risk registry.

    The first :data:`MAX_JOBS` jobs are checked, and a valid one runs. Each
    job past the cap gets its name in one line and does not run, as in
    ``call_agents_parallel``. A refused job never stops the others.
    """
    jobs = _loads(raw)
    if not isinstance(jobs, list) or not jobs:
        return JobPlan(error=_CALL_SHAPE)
    parent = frozenset(parent_tools)
    handoff = _handoff_tools() | frozenset(delegating)
    ready: list[Job] = []
    refused: list[str] = []
    for number, entry in enumerate(jobs[:MAX_JOBS], start=1):
        why, job = _refusal(entry, parent, read_only, handoff)
        if job is None:
            label = _label(number, entry)
            refused.append(f"{label[0].upper()}{label[1:]} did not run: {why}")
            continue
        ready.append(Job(number=number, job=job.job, tools=job.tools, tier=job.tier))
    left_out = jobs[MAX_JOBS:]
    if left_out:
        shown = left_out[:MAX_NAMED_OVERFLOW]
        names = ", ".join(_label(n, e) for n, e in enumerate(shown, start=MAX_JOBS + 1))
        more = len(left_out) - len(shown)
        if more:
            names += f", and {more} more"
        refused.append(f"At most {MAX_JOBS} jobs run in one call. These did not run: "
                       f"{names}. Call again with them.")
    return JobPlan(jobs=tuple(ready), refused=tuple(refused))


# ── The items out ───────────────────────────────────────────────────────────


def _as_text(raw: str | bytes | None) -> str:
    """The answer as text. Bytes decode, and a value of another type is no text."""
    if isinstance(raw, bytes | bytearray):
        return bytes(raw).decode("utf-8", errors="replace")
    return raw if isinstance(raw, str) else ""


def unstructured(text: str | bytes | None) -> Item:
    """The one item that holds output in any other shape. It is a guess."""
    return Item(value=_as_text(text).strip(), kind="inferred", source=UNSTRUCTURED)


def _value_of(raw: Any) -> str | None:
    """The value of an item as text, or ``None`` when it holds nothing."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, list | dict) and not raw:
        return None
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))


def _items_of(payload: Any) -> list[Item] | None:
    """The items of the contract object, or ``None`` when it is another shape."""
    if not isinstance(payload, dict) or set(payload) != {"items"}:
        return None
    rows = payload["items"]
    if not isinstance(rows, list):
        return None
    items: list[Item] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != ITEM_KEYS:
            return None
        kind = row["kind"]
        kind = kind.strip().casefold() if isinstance(kind, str) else None
        if kind not in KINDS:
            return None
        source = row["source"]
        if not isinstance(source, str) or not source.strip():
            return None
        value = _value_of(row["value"])
        if value is None:
            return None
        source = source.strip()
        if source.casefold() == MEMBER_SOURCE:
            # A page can tell the child to claim the member. Only the member's
            # own words carry that source, so the claim is a guess (§6.4).
            kind, source = "inferred", CHILD_SOURCE
        items.append(Item(value=value, kind=kind, source=source))
    return items


def parse_items(text: str | bytes | None) -> list[Item]:
    """The items of a child's answer, in its order (§6.4).

    The answer must be exactly one JSON object with the one key ``items``, a
    list. Each item has exactly the keys ``value``, ``kind`` and ``source``.
    ``kind`` is ``quoted`` or ``inferred``, ``source`` is not blank, and
    ``value`` is not empty. ``{"items": []}`` is a child that found nothing.

    Any other answer, a code fence around the object too, becomes ONE item
    from :func:`unstructured`. One bad item does the same to the whole answer,
    so a mark of ``quoted`` never stands beside text that the parse lost.
    """
    raw = _as_text(text)
    try:
        payload = json.loads(raw, object_pairs_hook=_one_of_each)
    except (ValueError, RecursionError):
        return [unstructured(raw)]
    items = _items_of(payload)
    return items if items is not None else [unstructured(raw)]


# ── The worker and its job (§4.5 OA-9a and OA-9b, §6.1, §6.8) ──────────────

#: The internal agent that runs one job. It is in no registry list that a
#: picker or a specialist tool reads (``gateway.routes.agent._INTERNAL_AGENTS``).
WORKER_NAME = "worker"

#: The names that only a bound job may run. ``worker_refusal`` refuses each of
#: them on a frame that binds no job.
INTERNAL_AGENTS = frozenset({WORKER_NAME})

#: The artifact-context key of the job that a worker runs. Only
#: :func:`bind_job` sets it, on the frame of one child, so a model never names
#: it (R11). A value that is not a :class:`Job` reads as no job.
JOB_KEY = "subagent_job"

#: The tool scope of a worker whose job names no tool. It is the scope of
#: ``agent-worker/config.json``. The pair of :func:`child_pin` holds the child
#: to its job whatever the scope says.
_DEFAULT_CHILD_SCOPE = ("web_search", "fetch_page")

NO_JOB_REFUSAL = (
    "The worker agent runs only the job of a spawn_subagents call. Call "
    "spawn_subagents with a job, or do the step yourself."
)


def bind_job(job: Job) -> None:
    """Bind *job* on THIS frame, for the worker that starts on it (§6.1).

    Call it inside the task of one child. A task copies the context when it
    starts, so no sibling and no parent sees the job.
    """
    from acb_skills.write_artifact import derive_artifact_context

    derive_artifact_context(**{JOB_KEY: job})


def bound_job() -> Job | None:
    """The job bound on this frame, or ``None``. Fails closed."""
    try:
        from acb_skills.write_artifact import artifact_context

        value = artifact_context().get(JOB_KEY)
    except Exception:
        return None
    return value if isinstance(value, Job) else None


def worker_refusal(name: object) -> str | None:
    """Why the agent *name* cannot run on this frame, or ``None`` (§4.5, OA-9a 3).

    An internal agent runs only for a bound job. Three callers ask it before
    any load: ``agent_tools.call_agent``, ``executor._run_sub_agent_streaming``
    and ``executor.run_agent``. It never raises, and any other name gets
    ``None``.
    """
    if str(name or "") not in INTERNAL_AGENTS:
        return None
    return None if bound_job() is not None else NO_JOB_REFUSAL


def child_tool_scope(agent_name: object, scope: list[str] | None) -> list[str] | None:
    """The injection scope of *agent_name*: the tools of its job for a worker.

    Any other agent gets *scope* back. A worker with no job tool gets the
    default scope, because an empty list would read as "inject every tool"
    (§4.5, OA-9a 4).
    """
    if str(agent_name or "") not in INTERNAL_AGENTS:
        return scope
    job = bound_job()
    if job is not None and job.tools:
        return list(job.tools)
    return list(_DEFAULT_CHILD_SCOPE)


def child_pin(allowed: Collection[str]) -> list[Any]:
    """The allow-list pair that keeps a child to its own tools (§4.5, OA-9b 1).

    A tool whose name is not in *allowed* is taken out of each model request
    and refused when called, so its body never runs. The pair does not depend
    on the parser or on the floor opt-out. It covers what arrives after both:
    the three floor tools that no opt-out removes, a granted app tool, an MCP
    tool and a tool that ``add_tools`` adds. The shape is that of
    ``code_session._session_tool_pin``.
    """
    from acb_skills.tool_guard import RefuseTools, WithholdTools, tool_name

    names = frozenset(allowed)

    def foreign(item: Any) -> bool:
        return tool_name(item) not in names

    def answer(name: str) -> str:
        return f"{name} is not a tool of this sub-agent job. Nothing ran."

    return [WithholdTools(foreign), RefuseTools(foreign, answer)]


# ── The flag and the spawn count (§6.2) ─────────────────────────────────────


def spawn_subagents_on(org: object) -> bool:
    """True when ``spawn_subagents`` is on for organization *org*. Fails closed.

    ``SPAWN_SUBAGENTS_ORGS`` is a comma list of organization ids, or ``*``
    for each one. Empty means off. An empty *org* gives False, and so does a
    fault in the settings read. The caller passes the bound tenant of the run
    and never a value from request input (R5). Any value other than empty on
    a box is OWNER-ONLY (``work_plan.md`` §6 D4).
    """
    org_id = str(org or "").strip().lower()
    if not org_id:
        return False
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "spawn_subagents_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return "*" in entries or org_id in entries


#: The spawn calls of each run, by run id. A run is one turn, and it lives in
#: one process. The table keeps the newest runs only.
_SPAWNS: dict[str, int] = {}
_SPAWNS_KEPT = 2048


#: One lock for each run, so two spawn calls of one model step run one after
#: the other. Then at most :data:`MAX_JOBS` children of a run run at once
#: (§6.2), and not 2 x 5. The table keeps the newest runs only.
_SPAWN_LOCKS: dict[str, asyncio.Lock] = {}


def spawn_lock(run_id: object) -> asyncio.Lock:
    """The lock that serializes the spawn calls of the run *run_id*."""
    key = str(run_id or "")
    lock = _SPAWN_LOCKS.pop(key, None) or asyncio.Lock()
    _SPAWN_LOCKS[key] = lock
    while len(_SPAWN_LOCKS) > _SPAWNS_KEPT:
        old = next(iter(_SPAWN_LOCKS))
        if _SPAWN_LOCKS[old].locked():
            break
        _SPAWN_LOCKS.pop(old)
    return lock


def take_spawn(run_id: object) -> bool:
    """Count one spawn call of the run *run_id*. False when the run used its 2.

    The run id comes from the run binding of the executor, never from the
    model. A call with no run id is refused (fail closed).
    """
    key = str(run_id or "")
    if not key:
        return False
    used = _SPAWNS.pop(key, 0)
    if used >= MAX_SPAWNS_PER_TURN:
        _SPAWNS[key] = used
        return False
    _SPAWNS[key] = used + 1
    while len(_SPAWNS) > _SPAWNS_KEPT:
        _SPAWNS.pop(next(iter(_SPAWNS)))
    return True
