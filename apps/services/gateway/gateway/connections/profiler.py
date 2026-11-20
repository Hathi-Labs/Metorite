"""The profiler of a connection (WS-SEM SEM-13a, task 3835, and SEM-13b,
task 3836).

Spec: ``project-docs/specs/semantic_model.md`` §4.2, §10.5 and §10.7. Dark
behind ``CONNECTION_MODEL_ORGS``. No caller exists yet.

:func:`profile` does steps 1 to 4 of §4.2 for ONE MCP connection of an org.
Step 3, the decide step, lives in ``connections/profiler_decide.py``. It is
dark behind the mode of ``connection.map_model``, ``off`` by default, and
then the ``ai`` section of the draft is empty.

1. **Discover.** ONE live ``tools/list`` with the raw input schemas
   (``mcp_tools.live_tool_schemas``, through the run of
   ``mcp_tools.org_read_run``).
2. **Shape reads.** It calls a tool only when each of these holds. The admin
   approved the text of the tool (B1b-3), and the text has not changed. The
   one class rule puts it in class A. It does not say ``destructiveHint``
   true or ``idempotentHint`` false. Its input schema has no required
   argument, or one required ``string`` argument that a key of an earlier
   read fills. Never a value. The plan is cut to :data:`MAX_CALLS` calls in
   a fixed order. A result over :data:`RESULT_BYTES_MAX` bytes skips that
   tool. Both count in ``left_out``. The time cap of :data:`RUN_S` seconds,
   a cache that cannot count, a full org window and a call that raises a
   change of the server stop the run, and then the run writes nothing. The
   server identity is checked once, at the list read.
3. **Decide (SEM-13b).** Only when the run saves a draft: one request for
   each entity, with closed lists only, paid by the ``connected_by`` member
   (``profiler_decide.decide``). It runs with no session open.
4. **Write the draft.** The body keeps keys, types, counts and the first and
   last date only, never a value. Each key and type is normalised to the
   grammar of §10.4 (:func:`normalise_key`, :func:`normalise_type`).
   ``semantic_model.save_draft`` writes it with ``untrusted_origin=False``.

**No member (§10.5 "The decision on approval").** The run reads with no
member, so it never takes a grant and never counts a member. It still obeys
what the admin approved, and a failed read of the approvals gives no call.

**The origin.** It profiles only an ``mcp:`` org row of the registry whose
transport is ``http-sse`` or ``http`` (the cloud MCP path). It refuses each
other row, so it refuses a local link. SEM-17 sets the untrusted mark for
a local link.

**Tenancy (R5).** *org* comes from the caller's job, never from request
input. Each database read and write runs in ``tenant_session(org)``, and no
session stays open over a network step.

Fences: ``tests/unit/test_connection_profiler.py`` and one case of
``tests/unit/test_connection_semantic_model_r8.py`` (R8).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from acb_common import get_logger

from gateway.connections import mcp as mcp_registry
from gateway.connections import mcp_tools, profiler_decide, semantic_model, store
from gateway.crm_sync.catalogue import PART_ITEMS_MAX, connection_key_problem
from gateway.studio import mcp_actions

_log = get_logger("connections.profiler")

__all__ = [
    "MAX_CALLS",
    "RESULT_BYTES_MAX",
    "RUN_S",
    "Profile",
    "normalise_key",
    "normalise_type",
    "profile",
    "reduce_schema",
    "shape_hash",
]

#: The most ``tools/call`` steps of one run (§4.2 step 2).
MAX_CALLS = 20
#: The most seconds of one run, from its first network step.
RUN_S = 30.0
#: The most bytes of one cut, cleaned result (``mcp_tools.shape_result``).
RESULT_BYTES_MAX = 64 * 1024

#: The seven types of JSON Schema. A reduced schema keeps one of them or
#: :data:`UNKNOWN`.
JSON_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object", "null"})
UNKNOWN = "unknown"

#: The closed table from a source type to one plain word (§10.5 "The
#: normalisation"). A type that is not here becomes :data:`UNKNOWN`.
TYPE_WORDS: Mapping[str, str] = {
    **{w: "string" for w in ("string", "str", "text", "varchar", "char", "utf8")},
    **{w: "integer" for w in ("integer", "int", "int8", "int16", "int32", "int64", "uint8",
                              "uint16", "uint32", "uint64", "long", "bigint", "smallint")},
    **{w: "number" for w in ("number", "float", "float32", "float64", "double", "decimal",
                             "numeric", "real")},
    **{w: "boolean" for w in ("boolean", "bool")},
    "date": "date",
    **{w: "datetime" for w in ("datetime", "timestamp")},
    **{w: "array" for w in ("array", "list")},
    **{w: "object" for w in ("object", "map", "struct")},
    "null": "null",
}

#: The outcomes of :func:`profile`. Only :data:`SAVED` writes a row.
OFF = "off"
SAVED = "saved"
SAME = "same"
EMPTY = "empty"

_DIGITS = re.compile(r"(?<=\d)(?=\d)")
#: A figure in the raw text: 5 or more digits in a row, or 6 or more digits
#: that only ``-`` or ``_`` join (a date such as ``2024-01-15``, an account
#: or card number, an amount such as ``4_500_000``). Such a key or type is
#: left out, never normalised (F2 and G1, fail closed). The rule reads the
#: RAW text, before the dash rule. Any other key gets the ``_`` of the digit
#: rule. No digit rule can see a figure written as letters (§10.5 F3).
_LONG_DIGITS = re.compile(r"\d{5,}|\d(?:[-_]?\d){5,}")
_DATE_HEAD = re.compile(r"(\d{4}-\d{2}-\d{2})(?:$|[T ])")


def _clock() -> float:
    """The clock of the run caps, in seconds. A test replaces it."""
    return time.monotonic()


@dataclass(frozen=True)
class Profile:
    """What one run did. It holds no value, no url and no header.

    ``outcome`` is :data:`OFF`, :data:`SAVED`, :data:`SAME`, :data:`EMPTY`,
    ``refused:<code>``, ``stopped:<cap>`` or ``failed:<reason>``.

    SEM-13b: ``questions`` and ``requests`` count what the decide step asked
    and sent. ``decide`` is the reason that the step ended
    (``profiler_decide.Decided``), or None when the step did not run."""

    outcome: str
    calls: int = 0
    left_out: int = 0
    version: int | None = None
    shape_hash: str | None = None
    questions: int = 0
    requests: int = 0
    decide: str | None = None


# ── The pure parts ──────────────────────────────────────────────────────────


def reduce_schema(schema: Any) -> dict[str, str] | None:
    """The required arguments of an ``inputSchema`` and the JSON type of
    each, or ``None`` when the schema is not an object (no call).

    It keeps no description, no default value and no enum value. A type
    outside :data:`JSON_TYPES`, a list of types and a missing type become
    :data:`UNKNOWN`."""
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        return None
    required = schema.get("required", [])
    props = schema.get("properties", {})
    if (not isinstance(required, list) or not isinstance(props, Mapping)
            or not all(isinstance(r, str) for r in required)):
        return None
    out: dict[str, str] = {}
    for name in required:
        prop = props.get(name)
        kind = prop.get("type") if isinstance(prop, Mapping) else None
        out[name] = kind if isinstance(kind, str) and kind in JSON_TYPES else UNKNOWN
    return out


def normalise_key(raw: Any) -> str | None:
    """*raw* as a key of §10.4, or ``None`` when it still fails the rule.

    A raw key that holds a figure is left out (:data:`_LONG_DIGITS`).
    Else a ``-`` becomes ``_``, and a run of digits gets an ``_``
    between each two digits. So ``list-invoices`` becomes ``list_invoices``
    and ``Custom_Field_10`` becomes ``Custom_Field_1_0``. A dot and a space
    stay refused. The one key rule is ``catalogue.connection_key_problem``,
    and this module keeps no copy."""
    if not isinstance(raw, str) or _LONG_DIGITS.search(raw):
        return None
    key = _DIGITS.sub("_", raw.replace("-", "_"))
    return key if connection_key_problem(key) is None else None


def normalise_type(raw: Any) -> str:
    """A source type as one plain word of :data:`TYPE_WORDS`, else
    :data:`UNKNOWN`. So ``int64`` becomes ``integer``. A word with a run of 5
    or more digits is :data:`UNKNOWN` too, and the column table leaves its
    column out."""
    if not isinstance(raw, str) or _LONG_DIGITS.search(raw):
        return UNKNOWN
    return TYPE_WORDS.get(raw.strip().lower(), UNKNOWN)


def _date_of(value: Any) -> str | None:
    """The ISO date at the head of a date or a date and time, else None."""
    if not isinstance(value, str):
        return None
    found = _DATE_HEAD.match(value)
    if found is None:
        return None
    try:
        return date.fromisoformat(found.group(1)).isoformat()
    except ValueError:
        return None


def _value_type(value: Any) -> str | None:
    """The plain type word of one value, or None for a null. The value
    itself is never kept."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "date" if _date_of(value) is not None else "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return UNKNOWN


def _merge_type(old: str | None, new: str | None) -> str | None:
    if old is None or old == new:
        return new if old is None else old
    if new is None:
        return old
    if {old, new} == {"integer", "number"}:
        return "number"
    if {old, new} <= {"date", "datetime", "string"}:
        return "string"
    return UNKNOWN


def shape_hash(shape: Mapping[str, Any]) -> str:
    """The SHA-256 hex of the canonical JSON of the reduced shape: sorted keys
    and the separators ``,`` and ``:``. The shape holds no value, no count
    and no date (§10.5 "The ``shape_hash``")."""
    text = json.dumps(shape, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


# ── The shape of the reads of one run ───────────────────────────────────────


class _Entity:
    def __init__(self, key: str) -> None:
        self.key = key
        self.records = 0
        self.fields: dict[str, str | None] = {}
        self.sources: dict[str, str] = {}
        self.first: str | None = None
        self.last: str | None = None

    def date(self, value: str) -> None:
        self.first = value if self.first is None else min(self.first, value)
        self.last = value if self.last is None else max(self.last, value)


class _Shape:
    """Keys, types, counts and dates of the results of one run. It keeps no
    value. A key that fails the rule after its normalisation, or that is the
    same as a key already in the entity, is left out and counted."""

    def __init__(self) -> None:
        self.entities: dict[str, _Entity] = {}
        self.dropped: set[tuple[str, str]] = set()
        #: The source keys of the entities that the reads found, in order:
        #: the only words that may fill a required argument.
        self.keys: list[str] = []
        self.fields = 0

    @property
    def left_out(self) -> int:
        return len(self.dropped)

    def _entity(self, raw: str) -> _Entity | None:
        key = normalise_key(raw)
        if key is None or (len(self.entities) >= PART_ITEMS_MAX and key not in self.entities):
            self.dropped.add(("", raw))
            return None
        return self.entities.setdefault(key, _Entity(key))

    def _field(self, ent: _Entity, raw: Any, kind: str | None) -> None:
        name = str(raw)
        key = normalise_key(raw)
        if key is None or ent.sources.get(key, name) != name:
            self.dropped.add((ent.key, name))
            return
        if key not in ent.fields:
            if self.fields >= PART_ITEMS_MAX:
                self.dropped.add((ent.key, name))
                return
            self.fields += 1
            ent.sources[key] = name
            ent.fields[key] = kind
            return
        ent.fields[key] = _merge_type(ent.fields[key], kind)

    def _records(self, ent: _Entity, records: list[Mapping[str, Any]]) -> None:
        ent.records = max(ent.records, len(records))
        for record in records:
            for raw, value in record.items():
                kind = _value_type(value)
                self._field(ent, raw, kind)
                found = _date_of(value)
                if found is not None:
                    ent.date(found)

    def _table(self, ent: _Entity, columns: list[Any], rows: Any) -> None:
        """A table of named, typed columns and rows. A column name is a key
        and its declared type is a type, so neither is a value."""
        rows = rows if isinstance(rows, list) else []
        ent.records = max(ent.records, len(rows))
        names: list[Any] = []
        for col in columns:
            name = col.get("name") if isinstance(col, Mapping) else None
            names.append(name)
            if isinstance(name, str):
                declared = col.get("type")
                if isinstance(declared, str) and _LONG_DIGITS.search(declared):
                    self.dropped.add((ent.key, name))
                    continue
                self._field(ent, name, normalise_type(declared) if declared is not None
                            else None)
        for row in rows:
            cells = (zip(names, row, strict=False) if isinstance(row, list)
                     else row.items() if isinstance(row, Mapping) else ())
            for name, value in cells:
                found = _date_of(value)
                if isinstance(name, str) and found is not None:
                    ent.date(found)

    def add(self, tool: str, result: Mapping[str, Any], named: str | None = None) -> None:
        """Add the shape of one cut, cleaned result of *tool*. *named* is the
        key that filled the argument of the call, when one did."""
        datas: list[Any] = []
        if isinstance(result.get("structured"), Mapping):
            datas.append(result["structured"])
        for item in result.get("content") or []:
            if isinstance(item, Mapping) and item.get("type") == "json":
                datas.append(item.get("data"))
        for data in datas:
            self._data(named or tool, data)

    def _data(self, own: str, data: Any) -> None:
        if isinstance(data, list):
            records = [r for r in data if isinstance(r, Mapping)]
            if records and (ent := self._entity(own)) is not None:
                self._records(ent, records)
            return
        if not isinstance(data, Mapping):
            return
        if isinstance(data.get("columns"), list) and "rows" in data:
            if (ent := self._entity(own)) is not None:
                self._table(ent, data["columns"], data["rows"])
            return
        lists = {k: [r for r in v if isinstance(r, Mapping)] for k, v in data.items()
                 if isinstance(v, list)}
        lists = {k: v for k, v in lists.items() if v}
        if len(lists) <= 1:
            # No list, or one list in a wrapper such as {"data": [...]}: the
            # records are of the tool (or of the key that filled the call).
            # A wrapper word is no entity and never fills an argument.
            if (ent := self._entity(own)) is not None:
                self._records(ent, next(iter(lists.values())) if lists else [data])
            return
        for raw, records in lists.items():
            if (ent := self._entity(raw)) is None:
                continue
            # Only a raw key that passes the key rule AS IT IS may fill an
            # argument, because the call sends the raw key (F1).
            if connection_key_problem(raw) is None and raw not in self.keys:
                self.keys.append(raw)
            self._records(ent, records)

    def body(self) -> dict[str, Any]:
        entities: list[dict[str, Any]] = []
        fields: list[dict[str, Any]] = []
        for ent in self.entities.values():
            item: dict[str, Any] = {"key": ent.key, "records": ent.records}
            if ent.first is not None and ent.last is not None:
                item["first_date"], item["last_date"] = ent.first, ent.last
            entities.append(item)
            fields.extend({"entity": ent.key, "key": k, "type": t or UNKNOWN}
                          for k, t in ent.fields.items())
        return {"v": 1, "code": {"entities": entities, "fields": fields}, "ai": {}, "admin": {}}

    def reduced(self) -> list[dict[str, Any]]:
        """The entities with the key and type of each field: no count, no date."""
        return sorted(({"key": e.key, "fields": {k: t or UNKNOWN for k, t in e.fields.items()}}
                       for e in self.entities.values()), key=lambda e: e["key"])


# ── The run ─────────────────────────────────────────────────────────────────


class _Stop(Exception):
    def __init__(self, cap: str) -> None:
        super().__init__(cap)
        self.cap = cap


def _result_bytes(result: Mapping[str, Any]) -> int:
    return len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))
               .encode("utf-8", "surrogatepass"))


async def _server_of(org: str, connection_id: Any) -> tuple[str, Any] | None:
    """The server name and the ``connected_by`` of the connection
    *connection_id* of *org*, or None.

    Only a ``connected`` ``mcp:`` org row of ``integrations`` with an HTTP
    transport passes (the cloud MCP path, §10.5 "The origin"). The
    ``connected_by`` is a stored fact, never request input: the decide step
    reads its payer from it (§10.7)."""
    from gateway.db import tenant_session

    try:
        async with tenant_session(org) as db:
            conn = await store.get_by_id(db, org, str(connection_id))
    except ValueError:
        return None
    if (conn is None or not conn.provider.startswith(mcp_registry.PREFIX)
            or conn.owning_app != mcp_registry.OWNING_APP
            or conn.scope_kind != store.SCOPE_ORG
            or conn.status != store.STATUS_CONNECTED):
        return None
    transport = (conn.provider_meta or {}).get("transport")
    if transport not in mcp_registry._HTTP_TRANSPORTS:
        return None
    name = conn.provider[len(mcp_registry.PREFIX):]
    if not mcp_registry.NAME_RE.match(name):
        return None
    return name, getattr(conn, "connected_by", None)


def _may_call(run: mcp_tools.OrgReadRun, tool: Mapping[str, Any],
              hints: Mapping[str, bool] | None, cls: str) -> bool:
    """The approval, the class and the two hints of §4.2. The run checks the
    approval and the class again before the call (``_class_a_item``)."""
    hints = hints or {}
    return (not mcp_actions.tool_changed(run.approved, run.meta.name, tool)
            and cls == "A"
            and hints.get("destructiveHint") is not True
            and hints.get("idempotentHint") is not False)


#: The refusals of one call that mean the server is not the server of the
#: list: the run stops and writes nothing (F5). The entry checks the identity
#: only at the list read, so today no call raises them. This is the reaction
#: for when one does (``semantic_model.md`` §10.5 "The plan and the caps").
_CHANGED = frozenset({mcp_tools.LIVE_CHANGED, mcp_tools.LIVE_NO_SERVER})


class _Reads:
    """The calls of one run, under the caps.

    The plan of the run is cut to :data:`MAX_CALLS` before the calls, so the
    call cap never stops a run. :meth:`one` still refuses a 21st call (a hard
    guard). A result over :data:`RESULT_BYTES_MAX`, or over the byte budget
    of the client, skips that tool only. The time cap, a cache that cannot
    count, a full org window and a call that raises a change stop the run."""

    def __init__(self, run: mcp_tools.OrgReadRun, start: float) -> None:
        self.run = run
        self.start = start
        self.calls = 0
        #: Planned calls that the cut removed, and results that were too big.
        self.skipped = 0
        self.shape = _Shape()

    @property
    def left_out(self) -> int:
        return self.shape.left_out + self.skipped

    def cut(self, plan: list[Any]) -> list[Any]:
        """*plan* cut to the calls that are left, the rest counted."""
        room = max(0, MAX_CALLS - self.calls)
        self.skipped += max(0, len(plan) - room)
        return plan[:room]

    async def one(self, tool: str, args: dict[str, Any], named: str | None) -> None:
        if self.calls >= MAX_CALLS:
            raise _Stop("calls")
        spent = _clock() - self.start
        if spent >= RUN_S:
            raise _Stop("time")
        self.calls += 1
        try:
            result, _cut = await self.run.call(tool, args, timeout_s=RUN_S - spent)
        except mcp_tools.LiveLimited:
            raise _Stop("limited") from None
        except mcp_tools.ListFailed as failed:
            if failed.reason == mcp_tools.REASON_CACHE:
                # The org window cannot count, so no step may go on.
                raise _Stop("cache") from None
            if _clock() - self.start > RUN_S:
                raise _Stop("time") from None
            if failed.reason == mcp_tools.REASON_TOO_LARGE:
                self.skipped += 1
            return
        except mcp_tools.LiveRefused as no:
            if no.reason in _CHANGED:
                raise _Stop("changed") from None
            # A refused tool gives no shape. The run goes on.
            if _clock() - self.start > RUN_S:
                raise _Stop("time") from None
            return
        if _clock() - self.start > RUN_S:
            raise _Stop("time")
        if _result_bytes(result) > RESULT_BYTES_MAX:
            # Too big to shape: this tool gives no shape, and the run goes on.
            self.skipped += 1
            return
        self.shape.add(tool, result, named)


async def _run(run: mcp_tools.OrgReadRun) -> Profile | tuple[dict[str, Any], str, _Reads]:
    """Steps 1 and 2 under the slot of the run. It gives a final
    :class:`Profile`, or the body, the hash and the reads to write after the
    slot is free."""
    meta = run.meta
    if meta.always_confirm is not False:
        return Profile("refused:always_confirm")
    if meta.transport not in mcp_registry._HTTP_TRANSPORTS:
        return Profile("refused:transport")
    if not any(server == meta.name for server, _tool in run.approved):
        # A new connect profiles nothing until an admin approves a tool.
        return Profile("refused:no_approval")
    start = _clock()
    listed, schemas = await run.tool_list()
    tools: list[dict[str, Any]] = []
    plain: list[str] = []
    keyed: list[tuple[str, str]] = []
    for tool in sorted(listed.tools, key=lambda t: t["name"]):
        name = tool["name"]
        hints = mcp_actions.tool_hints(tool.get("annotations"))
        cls = mcp_actions.mcp_tool_class(hints, always_confirm=meta.always_confirm)
        required = reduce_schema(schemas[name]) if name in schemas else None
        tools.append({"name": name, "class": cls, "required": required})
        if required is None or not _may_call(run, tool, hints, cls):
            continue
        if not required:
            plain.append(name)
        elif len(required) == 1 and next(iter(required.values())) == "string":
            keyed.append((name, next(iter(required))))
    reads = _Reads(run, start)
    try:
        # The plan, in one fixed order: the reads with no argument by tool
        # name, then the keyed reads by tool name and key. Each part is cut
        # to the calls that are left, so the hash of a run does not depend
        # on where a cap would fall.
        for name in reads.cut(plain):
            await reads.one(name, {}, None)
        found = sorted(reads.shape.keys)
        for name, arg, key in reads.cut([(n, a, k) for n, a in keyed for k in found]):
            await reads.one(name, {arg: key}, key)
    except _Stop as stop:
        _log.info("connections.profiler.stopped", cap=stop.cap, calls=reads.calls)
        return Profile(f"stopped:{stop.cap}", calls=reads.calls, left_out=reads.left_out)
    if not reads.shape.entities:
        return Profile(EMPTY, calls=reads.calls, left_out=reads.left_out)
    body = reads.shape.body()
    digest = shape_hash({"tools": tools, "entities": reads.shape.reduced()})
    return body, digest, reads


async def _write(org: str, connection_id: Any, body: dict[str, Any], digest: str,
                 reads: _Reads, connected_by: Any = None) -> Profile:
    """Steps 3 and 4: no draft when the approved version has the same hash.
    Else the decide step, then one draft through
    ``semantic_model.save_draft``.

    Two short sessions, and the decide step between them with no session
    open (R5, §10.7 decision 9): the first reads ``read_approved``, the
    second saves the draft."""
    from gateway.db import tenant_session

    done: dict[str, Any] = {"calls": reads.calls, "left_out": reads.left_out,
                            "shape_hash": digest}
    async with tenant_session(org) as db:
        approved = await semantic_model.read_approved(db, connection_id)
    if approved is not None and approved.shape_hash == digest:
        # An unchanged shape asks nothing and spends nothing.
        return Profile(SAME, **done)
    decided = await profiler_decide.decide(org, connected_by, body)
    body = {**body, "ai": decided.ai}
    done.update(questions=decided.questions, requests=decided.requests,
                decide=decided.reason)
    try:
        async with tenant_session(org) as db:
            version = await semantic_model.save_draft(db, connection_id, body, digest,
                                                      untrusted_origin=False)
    except semantic_model.ModelRefused as no:
        return Profile(f"refused:model:{no.code}", **done)
    return Profile(SAVED, version=version, **done)


async def profile(org: str, connection_id: Any, *, lister: mcp_tools.Lister | None = None,
                  caller: mcp_tools.Caller | None = None) -> Profile:
    """Profile the MCP connection *connection_id* of *org*, and write a draft
    of its model (§4.2 steps 1, 2 and 4, §10.5).

    *org* comes from the caller's job, never from request input (R5).
    *lister* and *caller* replace the network in a test. The order:

    1. ``connection_model_allowed(org)``, else :data:`OFF`: no list, no
       call and no write.
    2. The row: a ``connected`` ``mcp:`` org row with an HTTP transport,
       else ``refused:connection``.
    3. The no-member entry (``mcp_tools.org_read_run``): a connected server
       of *org* whose ``agent_scope`` holds ``*``, and a read of the
       approvals that did not fail. A server with ``always_confirm``, or
       with no approved tool, gets no profile.
    4. One live list, then the shape reads under the caps.
    5. With the slot free, the draft, unless the approved version has the
       same ``shape_hash``. Before the draft, the decide step of SEM-13b
       (``profiler_decide.decide``), with no session open."""
    if not semantic_model.connection_model_allowed(org):
        return Profile(OFF)
    found = await _server_of(org, connection_id)
    if found is None:
        return Profile("refused:connection")
    server, connected_by = found
    try:
        async with mcp_tools.org_read_run(org, server, lister=lister, caller=caller) as run:
            done = await _run(run)
    except mcp_tools.LiveRefused as no:
        return Profile(f"refused:{no.reason}")
    except mcp_tools.LiveLimited:
        return Profile("stopped:limited")
    except mcp_tools.ListFailed as failed:
        return Profile(f"failed:{failed.reason}")
    if isinstance(done, Profile):
        return done
    # The block above freed the slot of the run, so the write holds none.
    return await _write(org, connection_id, *done, connected_by)
