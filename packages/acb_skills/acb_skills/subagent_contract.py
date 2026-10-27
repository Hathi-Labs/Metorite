"""The sub-agent contract of OA-9: the jobs in, and the items out (WS-67).

Spec ``project-docs/specs/one_assistant_context.md`` §6 (D117 item 5), slice
OA-9a, "GO for the parser only" (§4.5). Fence:
``tests/unit/test_subagent_contract.py``.

**Dark, with no caller.** OA-9b builds ``spawn_subagents`` and its fan-out on
these two parsers. Until then nothing imports this module, and the fence
says so. It opens no connection, reads no Redis and calls no model (R5).

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

import json
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from acb_skills.tool_annotations import get_annotations

__all__ = [
    "DEFAULT_TIER",
    "FANOUT_TIMEOUT_S",
    "ITEM_KEYS",
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
    "Item",
    "Job",
    "JobPlan",
    "annotated_read_only",
    "parse_items",
    "parse_jobs",
    "unstructured",
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
        items.append(Item(value=value, kind=kind, source=source.strip()))
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
