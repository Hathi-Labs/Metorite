"""The shared types and readers of the app manifests (MCP-P1a, D136).

Spec: ``project-docs/specs/external_agent_access.md`` §19.3 (the manifest
contract) and AD-1 to AD-3 of §19.12.

**One manifest, two doors.** Each app keeps ONE table of its routes. Mira's
chat client reads it through :func:`allowed`, and the MCP door reads it
through :func:`door_ops`, :func:`door_routes` and :func:`build_call`. So an
app with a manifest reaches outside agents with no new MCP tool.

**Pure data (AD-1).** This module imports the standard library only. So the
edge, branch 0 of ``acb_auth.deps``, the gateway and the chat tools can all
import it. It does no I/O.

**The classes.**

    A   a read
    B   a write that the app can undo
    C   a write that is hard to undo
    X   on neither door. ``reason`` says why

**The door of a row (AD-2).** Each A, B or C row names a door ``op``, or an
``off_door`` reason. :class:`App` refuses a row with neither when the module
loads, so a manifest that leaves a route open cannot import.

**The kinds of a door op.** A class A op is a ``read``. A class B op with
``proposal`` only files a pending object that the member finishes in the
app (AD-8). Each other B or C op is a ``write``, and it waits for the
member's approval (§19.5). Before MCP-P1b no approval can exist, so the edge
and branch 0 refuse each such op (AD-18, :attr:`DoorOp.asks`).

**One builder (API-1, ``api_connections.md`` §2.1).** :func:`build_route_call`
builds a call from ONE :class:`Route`. The door's :func:`build_call` calls it
with an open query, as before. An API template op calls it with a closed
query: each key must be a path key or a key of ``query_shapes``. A row with
``api`` set is an op of an API template. It has no chat tool, and an
:class:`App` refuses it.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

CLASSES: tuple[str, ...] = ("A", "B", "C", "X")

#: ``ops`` serves the four door tools. ``tools`` is an app whose door is its
#: own typed tools: Projects (§9).
DOORS: tuple[str, ...] = ("ops", "tools")

#: The methods a row may name.
METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE")

#: The shape of an app id. It is also the prefix of the app's scopes.
APP_ID_PATTERN = r"[a-z]{1,32}"

#: The shape of a door op name (AD-2).
OP_PATTERN = r"[a-z][a-z0-9_]{0,47}"

#: The shape of a path parameter that has no ``path_shapes`` entry: an id.
#: The same pattern as ``agent_edge/gateway_client.py`` (``_ID``).
UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: The shape of a key of ``args``: a parameter or a body field name.
ARG_KEY_PATTERN = r"[A-Za-z_][A-Za-z0-9_]{0,63}"

#: The ``args`` key that becomes the header ``If-Match`` of a write (§19.3).
VERSION_ARG = "expected_version"

#: The longest ``If-Match`` value. The edge checks the shape again.
MAX_VERSION_CHARS = 40

#: The ONE shape of an ``If-Match`` value that the edge sends: the characters
#: of an ISO-8601 timestamp, and no other (MCP-P1b-2 review P2). The edge
#: client refuses each other value before the send, so the ask of a door
#: write refuses it too. Otherwise the member approves a call that no send
#: can deliver. ``build_call`` keeps its own rule, because Studio uses it.
IF_MATCH_PATTERN = r"[0-9][0-9:.+TZ-]{9,39}"

_APP_ID = re.compile(APP_ID_PATTERN)
_OP = re.compile(OP_PATTERN)
_ARG_KEY = re.compile(ARG_KEY_PATTERN)
_PARAM = re.compile(r"\{([^}]+)\}")

#: A path value that holds one of these, or ``..``, can leave its place in
#: the path. The builder refuses it, whatever the shape of the row (API-1).
PATH_BREAKERS: tuple[str, ...] = ("/", "\\", "?", "#", "%", "\x00")

#: The closed grammar of a shape of an API op (API-1). A shape is a run of
#: tokens. A token is one character class of ASCII ranges, ``_`` or ``\-``,
#: with a count in ASCII digits, or one literal ``A-Za-z0-9_-`` character. It
#: has no ``.``, ``\w``, ``\d``, ``+``, ``*``, flag, group, alternation or
#: negated class. The ``re.ASCII`` flag keeps each part of it to ASCII.
_SHAPE_RANGE = r"(?:a-z|A-Z|0-9|[a-z]-[a-z]|[A-Z]-[A-Z]|[0-9]-[0-9]|_|\\-|[A-Za-z0-9])"
_SHAPE_GRAMMAR = re.compile(
    rf"(?:\[{_SHAPE_RANGE}+\]\{{[0-9]{{1,3}}(?:,[0-9]{{1,3}})?\}}|[A-Za-z0-9_]|\\-|-)+",
    re.ASCII,
)

#: The look-alikes of the characters that break a path or a query.
_LOOK_ALIKES: tuple[str, ...] = (
    "\uff0f", "\u2215", "\u2044", "\uff1f", "\uff03", "\uff05", "\uff0e\uff0e",
    "\u2024\u2024", "\uff06", "\uff1d", "\u0661",
)

#: Text that a path shape must never match. The grammar refuses each one
#: already. These probes check the grammar itself.
PATH_PROBES: tuple[str, ...] = (
    "", "/", "a/b", "?", "a?b", "#", "a#b", "%", "%2f", "%2e%2e", "..", "a..b",
    ".", "\\", "\x00", "a\x00b", " ", "\n", *_LOOK_ALIKES,
)

#: Text that a query shape must never match: a character that ends a value or
#: starts a second key, a line end, a NUL and their look-alikes. The spec
#: (``api_connections.md`` §2.1) names no need for ``, : + . @``, so these
#: are probes too.
QUERY_PROBES: tuple[str, ...] = (
    "", "&", "a&b", "&x=1", "=", "a=b", "#", "a#b", "?", "%", "%26", "\n", "a\nb", "\r",
    "\x00", "\\", " ", "a b", ".", "..", ",", ":", "+", "@", ";", "/", *_LOOK_ALIKES,
)


def shape_is_closed(pattern: object, probes: tuple[str, ...]) -> bool:
    """True when *pattern* fits the closed shape grammar and matches none of
    *probes*."""
    if not isinstance(pattern, str) or not _SHAPE_GRAMMAR.fullmatch(pattern):
        return False
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return not any(compiled.fullmatch(p) for p in probes)

#: The bytes that a path value keeps as they are: the unreserved set of
#: RFC 3986. The builder percent-encodes each other byte.
_UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def percent_encode(value: str) -> str:
    """*value* as UTF-8, with each byte outside the unreserved set as ``%XX``.

    It gives the same text as ``urllib.parse.quote(value, safe="")``. This
    module imports no ``urllib``, because the door fence keeps it to a short
    list of standard modules.
    """
    return "".join(chr(b) if b in _UNRESERVED else f"%{b:02X}" for b in value.encode("utf-8"))


@dataclass(frozen=True)
class Page:
    """The paging of a read route that pages with a cursor.

    ``limit`` and ``cursor`` are the query keys of the route. ``next`` is
    the key of the answer that holds the cursor of the next page. The edge
    wraps that cursor in its own signed cursor, so the agent never sends the
    route's own cursor.
    """

    limit: str
    cursor: str
    next: str


@dataclass(frozen=True)
class Route:
    """One route of an app, and what each door does with it (§19.3)."""

    method: str
    #: The path template as FastAPI reports it, prefix included.
    path: str
    #: The chat tool that reaches this route, or ``""`` when none does yet.
    tool: str
    cls: str
    #: Required for class X: why neither door reaches this route.
    reason: str = ""
    #: Required for a chat row of class A, B or C with no tool.
    planned: str = ""
    #: More chat tools that reach the same route.
    also: tuple[str, ...] = ()
    #: The door op name. Unique in the app.
    op: str = ""
    #: Why the MCP door does not serve this row. Required with no ``op``.
    off_door: str = ""
    #: One line for ``describe_app``. Required with ``op``.
    title: str = ""
    #: The op only files a pending object that the member finishes (AD-8).
    proposal: bool = False
    #: The op can cause mail or a message to leave Metorite (AD-19).
    sends: bool = False
    #: The keys of the answer that may leave as plain strings (AD-10).
    plain: tuple[str, ...] = ()
    #: The query keys of a write route.
    query: tuple[str, ...] = ()
    #: A pattern for each path parameter that is not an id.
    path_shapes: tuple[tuple[str, str], ...] = ()
    #: The paging of a read route, or ``None``.
    page: Page | None = None
    #: A pattern for each query key that the row takes (API-1). For an API
    #: op, these keys are the closed list of :func:`build_route_call`.
    query_shapes: tuple[tuple[str, str], ...] = ()
    #: The row is an op of an API template (``api_connections.md`` §2.1). It
    #: has no chat tool, so the chat rules do not run. An :class:`App`
    #: refuses it.
    api: bool = False

    def __post_init__(self) -> None:
        where = f"{self.method} {self.path}"
        if self.method not in METHODS:
            raise ValueError(f"{where}: the method is not one of {METHODS}")
        if self.cls not in CLASSES:
            raise ValueError(f"{where}: class {self.cls!r} is not one of {CLASSES}")
        if self.api:
            self._check_api(where)
        else:
            self._check_chat(where)
        self._check_door(where)

    def _check_api(self, where: str) -> None:
        """The rules of an API op row. The template validator adds its own."""
        if self.cls == "X":
            raise ValueError(f"{where}: an API op is class A, B or C")
        if not self.op:
            raise ValueError(f"{where}: an API op needs an op name")
        if self.tool or self.also or self.planned or self.reason or self.off_door:
            raise ValueError(f"{where}: an API op names no chat tool")

    def _check_chat(self, where: str) -> None:
        """The rules of the chat half, as the Knowledge manifest kept them."""
        if self.cls == "X":
            if not self.reason:
                raise ValueError(f"{where}: an excluded route needs a reason")
            if self.tool or self.also:
                raise ValueError(f"{where}: an excluded route names no tool")
            return
        if self.also and not self.tool:
            raise ValueError(f"{where}: 'also' needs a first tool")
        if not self.tool and not self.planned:
            raise ValueError(
                f"{where}: class {self.cls} needs a tool or the slice that plans one"
            )
        if self.tool and self.planned:
            raise ValueError(f"{where}: a built tool is not planned")

    def _check_door(self, where: str) -> None:
        """The rules of the door half (§19.3). AD-2 is :class:`App`'s."""
        door_fields = (self.title, self.proposal, self.sends, self.plain, self.query,
                       self.path_shapes, self.page, self.query_shapes)
        if self.cls == "X" and (self.op or self.off_door):
            raise ValueError(f"{where}: a class X row has no door op and no off_door")
        if self.op and self.off_door:
            raise ValueError(f"{where}: a row names an op or an off_door, not both")
        if not self.op:
            if any(door_fields):
                raise ValueError(f"{where}: the door fields need an op")
            return
        self._check_op(where)

    def _check_op(self, where: str) -> None:
        """The rules of a row with a door op."""
        if not _OP.fullmatch(self.op):
            raise ValueError(f"{where}: the op {self.op!r} does not match {OP_PATTERN}")
        if not self.title:
            raise ValueError(f"{where}: an op needs a title")
        if self.proposal and self.cls != "B":
            raise ValueError(f"{where}: a proposal is class B (AD-8)")
        if self.sends and self.cls != "C":
            raise ValueError(f"{where}: an op that sends is class C (AD-19)")
        if self.cls == "A" and self.method != "GET":
            raise ValueError(f"{where}: a door read is a GET")
        if self.page is not None and self.cls != "A":
            raise ValueError(f"{where}: only a read pages")
        if self.query and self.method == "GET":
            raise ValueError(f"{where}: 'query' names the query keys of a write")
        names = set(path_params(self.path))
        for name, pattern in self.path_shapes:
            if name not in names:
                raise ValueError(f"{where}: path_shapes names {name!r}, not a path parameter")
            re.compile(pattern)
        for key in (*self.query, *self.plain):
            if not _ARG_KEY.fullmatch(key):
                raise ValueError(f"{where}: {key!r} is not a key")
        self._check_query_shapes(where, names)

    def _check_query_shapes(self, where: str, names: set[str]) -> None:
        """The rules of ``query_shapes`` (API-1)."""
        shaped = [name for name, _ in self.query_shapes]
        if len(shaped) != len(set(shaped)):
            raise ValueError(f"{where}: query_shapes names a key twice")
        for name, pattern in self.query_shapes:
            if not _ARG_KEY.fullmatch(name):
                raise ValueError(f"{where}: {name!r} is not a key")
            if name in names:
                raise ValueError(f"{where}: {name!r} is a path parameter, not a query key")
            if not shape_is_closed(pattern, QUERY_PROBES):
                raise ValueError(f"{where}: the query shape of {name!r} is not closed")


@dataclass(frozen=True)
class App:
    """The header of an app, and its rows (§19.3)."""

    #: The app id and the prefix of its scopes, for example ``crm``.
    id: str
    #: The name on the consent page and in ``list_apps``.
    name: str
    #: One line that says what the app holds.
    reach: str
    #: The ``href`` of the app's pane in ``src/lib/nav.ts``. MCP-P4 reads it.
    pane: str
    #: The feature that the app's router demands, as ``feature:<feature>``.
    feature: str
    #: The import path of the app's router, for the coverage fence.
    router: str
    #: Where a member finishes a proposal. Required with a proposal op.
    finish_in: str = ""
    routes: tuple[Route, ...] = ()
    #: ``ops`` for the four door tools, ``tools`` for an app with typed tools.
    door: str = "ops"

    def __post_init__(self) -> None:
        if not _APP_ID.fullmatch(self.id):
            raise ValueError(f"app id {self.id!r} does not match {APP_ID_PATTERN}")
        if self.door not in DOORS:
            raise ValueError(f"{self.id}: the door {self.door!r} is not one of {DOORS}")
        if self.door == "tools" and self.routes:
            raise ValueError(f"{self.id}: an app with typed tools keeps its rows elsewhere")
        pairs = [(r.method, r.path) for r in self.routes]
        if len(pairs) != len(set(pairs)):
            raise ValueError(f"{self.id}: a route has two rows")
        ops = [r.op for r in self.routes if r.op]
        if len(ops) != len(set(ops)):
            raise ValueError(f"{self.id}: an op name is used twice")
        for row in self.routes:
            if row.api:
                raise ValueError(f"{self.id}: {row.method} {row.path} is an API op, not an app row")
            # AD-2: an A, B or C row names a door op or an off_door reason.
            if row.cls != "X" and not row.op and not row.off_door:
                raise ValueError(
                    f"{self.id}: {row.method} {row.path} names no op and no off_door (AD-2)")
            if row.proposal and not self.finish_in:
                raise ValueError(f"{self.id}: a proposal op needs finish_in")

    @property
    def read_scope(self) -> str:
        return f"{self.id}:read"

    @property
    def write_scope(self) -> str:
        return f"{self.id}:write"

    @property
    def scopes(self) -> tuple[str, ...]:
        """``<id>:read`` when the app has a door op, and ``<id>:write`` too
        when it has a door op of class B or C (§19.6)."""
        door = [r for r in self.routes if r.op]
        if not door:
            return ()
        if any(r.cls in ("B", "C") for r in door):
            return (self.read_scope, self.write_scope)
        return (self.read_scope,)


@dataclass(frozen=True)
class DoorOp:
    """One door op: its app and its row."""

    app: App
    route: Route

    @property
    def name(self) -> str:
        return self.route.op

    @property
    def kind(self) -> str:
        """``read``, ``proposal`` or ``write`` (§19.4)."""
        if self.route.cls == "A":
            return "read"
        return "proposal" if self.route.proposal else "write"

    @property
    def asks(self) -> str:
        """``never`` for a read or a proposal, ``each_call`` for a write."""
        return "each_call" if self.kind == "write" else "never"

    @property
    def scope(self) -> str:
        """``<app>:read`` for a read, ``<app>:write`` for a change (AD-4)."""
        return self.app.read_scope if self.kind == "read" else self.app.write_scope


@dataclass(frozen=True)
class Call:
    """One door call that :func:`build_call` made (§19.3).

    ``query`` is sorted. ``body`` is the compact JSON with sorted keys of a
    write, and ``b""`` for a read, so its bytes are stable (AD-17).
    """

    method: str
    template: str
    path: str
    query: tuple[tuple[str, str], ...]
    body: bytes
    if_match: str | None = None


class BadCall(ValueError):
    """An ``args`` value that :func:`build_call` cannot use.

    ``name`` is the key of ``args``, from the caller's own input. The text
    of the error names only that key.
    """

    def __init__(self, name: str, why: str) -> None:
        super().__init__(f"{name}: {why}")
        self.name = name
        self.why = why


# ── Readers ─────────────────────────────────────────────────────────────────


def path_params(template: str) -> tuple[str, ...]:
    """The path parameter names of *template*, in order."""
    return tuple(_PARAM.findall(template))


def _template_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    out = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{out}$")


@functools.lru_cache(maxsize=32)
def _compiled(app: App) -> tuple[tuple[Route, re.Pattern[str]], ...]:
    return tuple((r, _template_regex(r.path)) for r in app.routes)


def route_for(app: App, method: str, path: str) -> Route | None:
    """The row a concrete ``method path`` falls under, or ``None``.

    ``path`` is the concrete path with the ids filled in. A query string is
    cut.
    """
    bare = path.split("?", 1)[0]
    verb = method.upper()
    for route, pattern in _compiled(app):
        if route.method == verb and pattern.match(bare):
            return route
    return None


def allowed(app: App, method: str, path: str, tool: str) -> tuple[bool, str]:
    """May the chat *tool* send ``method path``? ``(ok, why_not)``.

    The chat client asks this before it builds a request. It refuses a route
    that the table does not carry, a route with no tool, and a route that the
    table gives to another tool.
    """
    route = route_for(app, method, path)
    if route is None:
        return False, f"{method.upper()} {path} is not in the {app.name} route manifest."
    if route.cls == "X":
        return False, f"{method.upper()} {path} is not on the chat surface: {route.reason}"
    if not route.tool:
        return False, f"{method.upper()} {path} has no chat tool yet ({route.planned})."
    if tool not in (route.tool, *route.also):
        return False, f"{method.upper()} {path} belongs to {route.tool}, not to {tool}."
    return True, ""


def tools(app: App) -> set[str]:
    """Every chat tool that the app's rows name."""
    return {name for r in app.routes for name in (r.tool, *r.also) if name}


def tool_class(app: App, name: str) -> str | None:
    """The class a chat tool acts at, or ``None`` for a tool no row names."""
    classes = {r.cls for r in app.routes if name and name in (r.tool, *r.also)}
    return sorted(classes)[0] if classes else None


@functools.lru_cache(maxsize=32)
def door_ops(app: App) -> Mapping[str, DoorOp]:
    """The door ops of *app*, by op name, in the order of its rows."""
    return {r.op: DoorOp(app, r) for r in app.routes if r.op}


@functools.lru_cache(maxsize=8)
def door_routes(apps: tuple[App, ...]) -> Mapping[tuple[str, str], DoorOp]:
    """Each ``(method, template)`` that a door op of *apps* reaches (AD-3)."""
    out: dict[tuple[str, str], DoorOp] = {}
    for app in apps:
        for op in door_ops(app).values():
            pair = (op.route.method, op.route.path)
            if pair in out:
                raise ValueError(f"{pair} is a door op of two apps")
            out[pair] = op
    return out


def scopes_of(apps: Iterable[App]) -> tuple[str, ...]:
    """The scopes of *apps*, in their order (§19.6)."""
    return tuple(s for app in apps for s in app.scopes)


def _query_value(name: str, value: Any) -> str:
    """One query value as text. A list, a dict or another type is refused,
    because the gateway refuses a repeated key."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise BadCall(name, "type")


def _path_value(name: str, value: Any, shape: str) -> str:
    """One path value, checked against *shape*, then percent-encoded (API-1)."""
    # An integer fills a parameter of a number shape, such as a version.
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise BadCall(name, "missing")
    if not re.fullmatch(shape, value):
        raise BadCall(name, "shape")
    # A value of dots only, such as ``.``, is a dot segment.
    if not value.strip(".") or ".." in value or any(c in value for c in PATH_BREAKERS):
        raise BadCall(name, "shape")
    return percent_encode(value)


def build_route_call(route: Route, args: Mapping[str, Any], *,
                     open_query: bool = False) -> Call:
    """The ONE builder of a call over one *route* (§19.3, AD-7, API-1): the
    method, the template, the path, the sorted query and the body bytes.

    * A key with the name of a path parameter fills that parameter. The value
      must match :data:`UUID_PATTERN`, or the pattern of ``path_shapes``. A
      value with a :data:`PATH_BREAKERS` character or ``..`` is refused. The
      builder then percent-encodes the value.
    * A key of ``query_shapes`` is a query parameter. Its value must match
      its pattern.
    * With *open_query* (the door), each other key of a GET is a query
      parameter. For a write, each other key goes into the JSON body, except
      the query keys of ``query``. :data:`VERSION_ARG` becomes ``If-Match``.
    * With no *open_query* (an API op), each other key is ``unknown``. So the
      keys of the row are a closed list.
    * The body is compact JSON with sorted keys, so its bytes are stable.

    A null value is left out. Raises :class:`BadCall` for a value it cannot
    use.
    """
    if not isinstance(args, Mapping):
        raise BadCall("args", "type")
    for key in args:
        if not isinstance(key, str) or not _ARG_KEY.fullmatch(key):
            raise BadCall(str(key)[:40], "key")
    shapes = dict(route.path_shapes)
    names = path_params(route.path)
    path = route.path
    for name in names:
        value = _path_value(name, args.get(name), shapes.get(name, UUID_PATTERN))
        path = path.replace("{" + name + "}", value)
    rest = {k: v for k, v in args.items() if k not in names and v is not None}
    query_shapes = dict(route.query_shapes)
    query: list[tuple[str, str]] = []
    for key in [k for k in rest if k in query_shapes]:
        text = _query_value(key, rest.pop(key))
        if not re.fullmatch(query_shapes[key], text):
            raise BadCall(key, "shape")
        query.append((key, text))
    if not open_query and rest:
        raise BadCall(sorted(rest)[0], "unknown")
    body = b""
    if_match: str | None = None
    if route.method == "GET":
        query += [(k, _query_value(k, v)) for k, v in rest.items()]
    else:
        version = rest.pop(VERSION_ARG, None)
        if version is not None:
            if not isinstance(version, str) or not 0 < len(version) <= MAX_VERSION_CHARS:
                raise BadCall(VERSION_ARG, "shape")
            if_match = version
        query += [(k, _query_value(k, rest.pop(k))) for k in route.query if k in rest]
        try:
            body = json.dumps(rest, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise BadCall("args", "type") from None
    return Call(method=route.method, template=route.path, path=path,
                query=tuple(sorted(query)), body=body, if_match=if_match)


def build_call(app: App, op: str, args: Mapping[str, Any]) -> Call:
    """The door call of *op* in *app* (§19.3, AD-7). It finds the row, then
    calls :func:`build_route_call` with an open query, as the door did.

    Raises :class:`BadCall` for an op the app does not have, and for a value
    it cannot use.
    """
    door = door_ops(app).get(op)
    if door is None:
        raise BadCall("op", "unknown")
    return build_route_call(door.route, args, open_query=True)


__all__ = [
    "APP_ID_PATTERN",
    "ARG_KEY_PATTERN",
    "CLASSES",
    "DOORS",
    "IF_MATCH_PATTERN",
    "MAX_VERSION_CHARS",
    "METHODS",
    "OP_PATTERN",
    "PATH_BREAKERS",
    "PATH_PROBES",
    "QUERY_PROBES",
    "UUID_PATTERN",
    "VERSION_ARG",
    "App",
    "BadCall",
    "Call",
    "DoorOp",
    "Page",
    "Route",
    "allowed",
    "build_call",
    "build_route_call",
    "door_ops",
    "door_routes",
    "path_params",
    "percent_encode",
    "route_for",
    "scopes_of",
    "shape_is_closed",
    "tool_class",
    "tools",
]
