"""The grammar of an API template, and its validator (API-1).

Spec: ``project-docs/specs/api_connections.md`` §2.1 (the template), §2.7
(secrets and egress) and §5 (the fences). Decision D228.

A template is a reviewed file in the repository, one for each API. It names
the identity, the hosts, the sign-in, the context keys, a closed list of
operations and the limits. The chat never sends a path, a host or a method.
It sends an op name and arguments, and :func:`build` checks them against the
row.

**One builder.** An op holds ONE :class:`Route` of the app door's grammar,
with ``api`` set. :func:`build` calls ``build_route_call`` of
``acb_common.app_manifests.base``, the builder that the door's ``build_call``
calls too. There is no second builder.

**Pure.** This module does no I/O and reads no setting, no environment and no
credential. :func:`validate` is a pure function. It raises
:class:`TemplateError`, and the error names the rule that failed.

**The rules of the validator.** These are the names that
:attr:`TemplateError.rule` takes:

* ``id``: the template id does not match :data:`TEMPLATE_ID_PATTERN`.
* ``text``: a label, a summary or a title is empty or too long.
* ``secret_in_field``: a stored text field holds a URL, a host, a header or
  a value that looks like a secret.
* ``http_host``: a host is not a bare ``https://`` origin on port 443.
* ``foreign_host``: a host is not on the host list of the client that the
  template names, or the client is not one that Metorite holds.
* ``sign_in``: the sign-in is not of the closed list, or its fields do not fit
  its kind.
* ``context``: a context key is not a key, or it is named twice.
* ``no_class``: an op has no class, or a class that is not A, B or C.
* ``method``: an op has a method that is not of the closed list, or a read
  that is not a GET.
* ``op_name``: an op name does not match the op pattern.
* ``duplicate_op``: two ops have one name.
* ``free_path``: the path is not a list of plain segments and whole
  placeholders, or a placeholder has no shape.
* ``open_path_shape``: a path shape can match ``/``, ``?``, ``#``, ``%``,
  ``..``, an empty value, or a character outside the closed shape grammar.
* ``query_no_shape``: a query key has no shape, or a key is named twice.
* ``query_open_shape``: a query shape does not fit the closed shape grammar,
  or it can match ``&``, ``=``, ``#``, a line end, a NUL or a look-alike.
* ``private_host``: a template with no client names a host with a private
  last label (:data:`PRIVATE_SUFFIXES`) or an ``xn--`` label.
* ``context_arg``: an op names a context key or the sign-in key as an
  argument. The gateway sets those keys itself.
* ``write_no_confirm``: a class B or C op does not carry ``always_confirm``.
* ``over_cap``: an answer cap, a page size, a page count or a limit is not in
  its range.
* ``page``: the paging of an op does not name query keys of the op, or the
  shape of the size key allows a number above the page size.
* ``total``: a total mark is on an op that is not a paged class A read.
* ``shape_read``: a shape read is not a class A read with no required
  argument.
* ``route``: the door's ``Route`` refuses the row.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from acb_common.app_manifests.base import (
    ARG_KEY_PATTERN,
    METHODS,
    OP_PATTERN,
    PATH_PROBES,
    QUERY_PROBES,
    BadCall,
    Call,
    Route,
    build_route_call,
    path_params,
    shape_is_closed,
)

#: The shape of a template id. The connection row's ``provider`` is
#: ``api:<id>`` (§2.2).
TEMPLATE_ID_PATTERN = r"[a-z][a-z0-9_]{0,31}"

#: The classes of an API op. A reads. B and C write (§2.1).
OP_CLASSES: tuple[str, ...] = ("A", "B", "C")

#: The closed list of sign-in kinds (§2.1): OAuth with a code grant, an API
#: key in a header, an API key in the query, or basic.
SIGN_INS: tuple[str, ...] = ("oauth_code", "key_header", "key_query", "basic")

#: The largest answer cap of an op. It is the cap of
#: ``outbound_guard.MAX_ANSWER_BYTES``. ``test_api_templates.py`` checks that
#: the two agree. This module does not import the guard, because the guard
#: holds the network code.
MAX_ANSWER_CAP = 64 * 1024

#: The gateway total reads at most this many pages (§2.3, §2.3a decision d).
#: It equals the member limit of the call limiter, ten calls a minute.
MAX_TOTAL_PAGES = 10

#: The largest page size of an op. The spec names no number. API-1 takes 200,
#: the largest page that a list read needs for the rung 1 demo.
MAX_PAGE_SIZE = 200

#: The longest label, summary and title.
MAX_TEXT = 200

#: The clients that Metorite holds, and the origins of each one's hosts. A
#: template that names a client gets its hosts from this map, and never copies
#: a host. The gateway fills it at import from each client's own host list.
ClientHosts = Mapping[str, frozenset[str]]

_TEMPLATE_ID = re.compile(TEMPLATE_ID_PATTERN)
_OP = re.compile(OP_PATTERN)
_KEY = re.compile(ARG_KEY_PATTERN)
_REGION = re.compile(r"[a-z]{2,8}")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,31}")
_HEADER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

#: A path is plain segments and whole placeholders, and nothing else.
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}|\{[a-z_][a-z0-9_]{0,63}\}")

#: The rate limit of an API is at most this many calls a minute. No public API
#: that a T1 template names allows more for one org. A larger number is a
#: typing error.
MAX_PER_MINUTE = 6000

#: The daily cap of an API is at most this many calls. A larger number is a
#: typing error, and half of it is the daily budget (\u00a72.3).
MAX_PER_DAY = 1_000_000

#: The scheme and header names that hold a credential word but are fixed
#: names, not values. :func:`_check_sign_in` does not search these for a
#: secret. Each other name passes the secret search.
KNOWN_SCHEMES: frozenset[str] = frozenset({"Zoho-oauthtoken", "Bearer"})
KNOWN_HEADERS: frozenset[str] = frozenset({"Authorization", "X-API-Key", "Api-Key"})

#: A host with one of these last labels is a private or local name. A
#: template with no client may not name one (\u00a72.7).
PRIVATE_SUFFIXES: frozenset[str] = frozenset(
    {"local", "internal", "localhost", "lan", "home", "corp"})

#: Signs of a URL, a host, a header or a secret in a stored text field.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|\bwww\.", re.IGNORECASE)
_HOSTNAME = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24}\b", re.IGNORECASE)
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_HEADER_WORDS = re.compile(
    r"\b(?:authorization|bearer|oauthtoken|api[_-]?key|x-api-key|cookie|"
    r"password|access_token|refresh_token|client_secret)\b|^[A-Za-z][A-Za-z0-9-]*:\s",
    re.IGNORECASE,
)
_SECRET = re.compile(r"[A-Za-z0-9_\-]{32,}|\d{3,5}\.[0-9a-f]{16,}", re.IGNORECASE)


class TemplateError(ValueError):
    """A template that the validator refuses. ``rule`` names the rule."""

    def __init__(self, rule: str, where: str, why: str) -> None:
        super().__init__(f"{where}: {rule}: {why}")
        self.rule = rule
        self.where = where


@dataclass(frozen=True)
class SignIn:
    """How a call signs in (§2.1). ``kind`` is one of :data:`SIGN_INS`."""

    kind: str
    #: The header that carries the credential, such as ``Authorization``.
    header: str = ""
    #: The scheme before the token, such as ``Zoho-oauthtoken``.
    scheme: str = ""
    #: The query key of the key, for ``key_query`` only.
    query_key: str = ""
    #: A client that Metorite holds, such as ``zoho``. Its hosts are the
    #: template's hosts.
    client: str = ""


@dataclass(frozen=True)
class Paging:
    """How a list op pages: the query keys of the page and of its size."""

    page_key: str
    size_key: str
    size: int


@dataclass(frozen=True)
class Total:
    """The mark of a number field that the gateway total adds (§2.3)."""

    #: The key of the answer that holds the list.
    items: str
    #: The number field of each item.
    field: str
    #: The most pages that the total reads. At most :data:`MAX_TOTAL_PAGES`.
    max_pages: int = MAX_TOTAL_PAGES
    #: The currency field of each item, or empty. With one, the gateway total
    #: refuses a list of more than one currency (§2.3a, decision d).
    currency: str = ""


@dataclass(frozen=True)
class Limits:
    """The rate limit of the API, and its daily cap where it has one."""

    per_minute: int
    per_day: int | None = None


@dataclass(frozen=True)
class Operation:
    """One op of a template: one row of the closed list (§2.1)."""

    name: str
    method: str
    path: str
    cls: str
    title: str
    #: A shape for each path placeholder. Each placeholder needs one.
    path_shapes: tuple[tuple[str, str], ...] = ()
    #: Each query key of the op, with its shape. The list is closed.
    query: tuple[tuple[str, str], ...] = ()
    paging: Paging | None = None
    #: The largest answer, in bytes, that the gateway passes on.
    answer_cap: int = MAX_ANSWER_CAP
    total: Total | None = None
    #: A class A read with no required argument, for the profile of a
    #: connection (``semantic_model.md`` SEM rule 8).
    shape_read: bool = False
    #: Each write asks a card, also in a member's own run (§2.6).
    always_confirm: bool = False
    #: The answer fields that the gateway removes (§2.3, step 8).
    secret_fields: tuple[str, ...] = ()

    @functools.cached_property
    def route(self) -> Route:
        """The row in the door's grammar. :func:`build` gives it to the one
        builder."""
        return Route(
            self.method, self.path, tool="", cls=self.cls, op=self.name,
            title=self.title, path_shapes=self.path_shapes, query_shapes=self.query,
            api=True,
        )


@dataclass(frozen=True)
class Template:
    """One reviewed API template (§2.1)."""

    id: str
    label: str
    #: One line that says what the API holds.
    summary: str
    #: Each region, with the https origin of the API there.
    hosts: tuple[tuple[str, str], ...]
    sign_in: SignIn
    #: The keys that the connect step finds, and the gateway sets on each call.
    context: tuple[str, ...]
    operations: tuple[Operation, ...]
    limits: Limits
    version: int = 1

    @property
    def provider(self) -> str:
        """The ``provider`` of the connection row (§2.2)."""
        return f"api:{self.id}"

    def op(self, name: str) -> Operation | None:
        """The op *name*, or ``None``."""
        return next((o for o in self.operations if o.name == name), None)

    def reserved_keys(self) -> frozenset[str]:
        """The keys that the gateway sets, and an argument may never name."""
        keys = set(self.context)
        if self.sign_in.query_key:
            keys.add(self.sign_in.query_key)
        return frozenset(keys)


# ── The validator ───────────────────────────────────────────────────────────


def _check_text(where: str, value: str, *, required: bool = True) -> None:
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > MAX_TEXT:
        raise TemplateError("text", where, "empty or too long")
    _check_plain(where, value)


def _check_plain(where: str, value: str) -> None:
    """Refuse a URL, a host, a header or a secret in a stored field (§2.7)."""
    for pattern, what in ((_URL, "a URL"), (_IP, "an address"), (_HOSTNAME, "a host"),
                          (_HEADER_WORDS, "a header"), (_SECRET, "a secret")):
        if pattern.search(value):
            raise TemplateError("secret_in_field", where, f"the field holds {what}")


def _check_origin(where: str, origin: str) -> None:
    """A bare https origin on port 443, with a public host name."""
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        raise TemplateError("http_host", where, "not a URL") from None
    if parts.scheme != "https":
        raise TemplateError("http_host", where, "a host must be https")
    host = parts.hostname or ""
    labels = host.split(".")
    if (
        origin != f"https://{host}"
        or port is not None
        or len(labels) < 2
        or not all(_HOST_LABEL.fullmatch(label) for label in labels)
        or labels[-1].isdigit()
        or host == "localhost"
    ):
        raise TemplateError("http_host", where, "a host is a bare https origin on 443")


def path_shape_is_closed(pattern: str) -> bool:
    """True when *pattern* keeps a path value in its place.

    The pattern must fit the closed shape grammar of ``base``, and it must
    match none of :data:`PATH_PROBES`. So it cannot match ``/``, ``?``,
    ``#``, ``%``, ``..``, a NUL or a look-alike of these.
    """
    return shape_is_closed(pattern, PATH_PROBES)


def query_shape_is_closed(pattern: str) -> bool:
    """True when *pattern* keeps a query value to one value of one key.

    The pattern must fit the closed shape grammar of ``base``, and it must
    match none of :data:`QUERY_PROBES`. So it cannot match ``&``, ``=``,
    ``#``, a line end, a NUL or a look-alike of these.
    """
    return shape_is_closed(pattern, QUERY_PROBES)


def _check_sign_in(where: str, sign_in: SignIn, client_hosts: ClientHosts) -> None:
    kind = sign_in.kind
    if kind not in SIGN_INS:
        raise TemplateError("sign_in", where, f"the kind is not one of {SIGN_INS}")
    if sign_in.scheme and sign_in.scheme not in KNOWN_SCHEMES:
        _check_plain(f"{where}.scheme", sign_in.scheme)
    if sign_in.header and sign_in.header not in KNOWN_HEADERS:
        _check_plain(f"{where}.header", sign_in.header)
    if sign_in.scheme and not _SCHEME.fullmatch(sign_in.scheme):
        raise TemplateError("secret_in_field", where, "the scheme is a name, not a value")
    if sign_in.header and not _HEADER.fullmatch(sign_in.header):
        raise TemplateError("secret_in_field", where, "the header is a name, not a value")
    if kind in ("oauth_code", "key_header") and not sign_in.header:
        raise TemplateError("sign_in", where, f"{kind} names its header")
    if kind == "key_query":
        if not _KEY.fullmatch(sign_in.query_key):
            raise TemplateError("sign_in", where, "key_query names its query key")
    elif sign_in.query_key:
        raise TemplateError("sign_in", where, "only key_query names a query key")
    if kind == "basic" and (sign_in.header or sign_in.scheme):
        raise TemplateError("sign_in", where, "basic names no header and no scheme")
    if sign_in.client:
        if sign_in.client not in client_hosts:
            raise TemplateError("foreign_host", where, "the client is not one that Metorite holds")
        if kind != "oauth_code":
            raise TemplateError("sign_in", where, "a client signs in with oauth_code")


def _check_hosts(template: Template, client_hosts: ClientHosts) -> None:
    where = f"{template.id}.hosts"
    if not template.hosts:
        raise TemplateError("http_host", where, "a template names at least one host")
    regions = [r for r, _ in template.hosts]
    if len(regions) != len(set(regions)):
        raise TemplateError("http_host", where, "a region is named twice")
    allowed = client_hosts.get(template.sign_in.client) if template.sign_in.client else None
    for region, origin in template.hosts:
        if not isinstance(region, str) or not _REGION.fullmatch(region):
            raise TemplateError("http_host", where, "a region is a short key")
        if not isinstance(origin, str):
            raise TemplateError("http_host", where, "a host is text")
        _check_origin(f"{where}.{region}", origin)
        if allowed is not None:
            if origin not in allowed:
                raise TemplateError("foreign_host", f"{where}.{region}",
                                    "the host is not on the client's own list")
            continue
        labels = origin.removeprefix("https://").split(".")
        if labels[-1] in PRIVATE_SUFFIXES or any(label.startswith("xn--") for label in labels):
            raise TemplateError("private_host", f"{where}.{region}",
                                "a host is a public name in plain ASCII")


def _check_path(where: str, op: Operation) -> None:
    path = op.path
    if not isinstance(path, str) or not path.startswith("/") or path.endswith("/"):
        raise TemplateError("free_path", where, "a path starts with / and ends with a segment")
    for segment in path[1:].split("/"):
        if not _SEGMENT.fullmatch(segment):
            raise TemplateError("free_path", where, "a segment is plain text or one placeholder")
    names = path_params(path)
    if len(names) != len(set(names)):
        raise TemplateError("free_path", where, "a placeholder is named twice")
    shapes = dict(op.path_shapes)
    if len(shapes) != len(op.path_shapes):
        raise TemplateError("free_path", where, "a path shape is named twice")
    for name in names:
        if name not in shapes:
            raise TemplateError("free_path", where, f"the placeholder {name!r} has no shape")
    for name, pattern in op.path_shapes:
        if name not in names:
            raise TemplateError("free_path", where, f"the shape {name!r} is not a placeholder")
        if not path_shape_is_closed(pattern):
            raise TemplateError("open_path_shape", where,
                                f"the shape of {name!r} can leave its place in the path")


def _check_query(where: str, op: Operation) -> None:
    keys = [k for k, _ in op.query]
    if len(keys) != len(set(keys)):
        raise TemplateError("query_no_shape", where, "a query key is named twice")
    for key, pattern in op.query:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise TemplateError("query_no_shape", where, "a query key is not a key")
        if not isinstance(pattern, str) or not pattern:
            raise TemplateError("query_no_shape", where, f"the query key {key!r} has no shape")
        if not query_shape_is_closed(pattern):
            raise TemplateError("query_open_shape", where,
                                f"the shape of {key!r} can let a second value or key through")


def _check_limits(template: Template) -> None:
    where = f"{template.id}.limits"
    limits = template.limits
    if not _positive(limits.per_minute) or limits.per_minute > MAX_PER_MINUTE:
        raise TemplateError("over_cap", where, f"the rate limit is 1 to {MAX_PER_MINUTE}")
    if limits.per_day is not None and (
        not _positive(limits.per_day) or limits.per_day > MAX_PER_DAY
    ):
        raise TemplateError("over_cap", where, f"the daily cap is 1 to {MAX_PER_DAY}")


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _check_op(template: Template, op: Operation) -> None:
    where = f"{template.id}.{op.name}"
    if not isinstance(op.name, str) or not _OP.fullmatch(op.name):
        raise TemplateError("op_name", where, f"the op name does not match {OP_PATTERN}")
    if op.cls not in OP_CLASSES:
        raise TemplateError("no_class", where, f"the class is not one of {OP_CLASSES}")
    if op.method not in METHODS:
        raise TemplateError("method", where, f"the method is not one of {METHODS}")
    if op.cls == "A" and op.method != "GET":
        raise TemplateError("method", where, "a class A read is a GET")
    _check_text(f"{where}.title", op.title)
    _check_path(where, op)
    _check_plain(f"{where}.path", op.path)
    _check_query(where, op)
    reserved = template.reserved_keys()
    for key in (*path_params(op.path), *(k for k, _ in op.query)):
        if key in reserved:
            raise TemplateError("context_arg", where, f"{key!r} is a key that the gateway sets")
    if op.cls in ("B", "C") and not op.always_confirm:
        raise TemplateError("write_no_confirm", where, "a write asks a card at each call")
    if not _positive(op.answer_cap) or op.answer_cap > MAX_ANSWER_CAP:
        raise TemplateError("over_cap", where, f"the answer cap is 1 to {MAX_ANSWER_CAP} bytes")
    _check_paging_and_total(where, op)
    if op.shape_read and (op.cls != "A" or path_params(op.path)):
        raise TemplateError("shape_read", where, "a shape read is a class A read with no required argument")
    for field in op.secret_fields:
        if not isinstance(field, str) or not _KEY.fullmatch(field):
            raise TemplateError("secret_in_field", where, "a secret field is a key")
    try:
        op.route  # noqa: B018 - the door's Route checks the row
    except ValueError as exc:
        raise TemplateError("route", where, str(exc)) from None


def _size_shape_exceeds(pattern: str, size: int) -> bool:
    """True when *pattern* matches a number above *size*.

    The closed grammar has no number bound, so the validator probes it: each
    number from ``size + 1`` to 9999, the same with leading zeros, and runs
    of ``9`` up to 24 digits.
    """
    compiled = re.compile(pattern)
    probes = [str(n) for n in range(size + 1, 10_000)]
    probes += ["0" * pad + str(size + 1) for pad in range(1, 6)]
    probes += ["9" * width for width in range(1, 25)]
    return any(compiled.fullmatch(p) and int(p) > size for p in probes)


def _check_paging_and_total(where: str, op: Operation) -> None:
    query_keys = {k for k, _ in op.query}
    if op.paging is not None:
        paging = op.paging
        if not _positive(paging.size) or paging.size > MAX_PAGE_SIZE:
            raise TemplateError("over_cap", where, f"the page size is 1 to {MAX_PAGE_SIZE}")
        if paging.page_key not in query_keys or paging.size_key not in query_keys:
            raise TemplateError("page", where, "the paging names query keys of the op")
        if paging.page_key == paging.size_key:
            raise TemplateError("page", where, "the page key and the size key differ")
        if _size_shape_exceeds(dict(op.query)[paging.size_key], paging.size):
            raise TemplateError("page", where,
                                f"the shape of {paging.size_key!r} allows a page above {paging.size}")
    if op.total is not None:
        if op.cls != "A" or op.paging is None:
            raise TemplateError("total", where, "a total is on a paged class A read")
        if not (_KEY.fullmatch(op.total.items) and _KEY.fullmatch(op.total.field)):
            raise TemplateError("total", where, "a total names keys of the answer")
        if op.total.currency and not _KEY.fullmatch(op.total.currency):
            raise TemplateError("total", where, "a currency field is a key of the answer")
        if not _positive(op.total.max_pages) or op.total.max_pages > MAX_TOTAL_PAGES:
            raise TemplateError("over_cap", where, f"a total reads 1 to {MAX_TOTAL_PAGES} pages")


def validate(template: Template, client_hosts: ClientHosts) -> None:
    """Refuse a template that breaks a rule of §2.1, §2.7 or §5.

    *client_hosts* maps each client that Metorite holds to its host origins.
    It is an argument, so this function stays pure. Raises
    :class:`TemplateError`, whose ``rule`` names the rule.
    """
    if not isinstance(template.id, str) or not _TEMPLATE_ID.fullmatch(template.id):
        raise TemplateError("id", "template", f"the id does not match {TEMPLATE_ID_PATTERN}")
    _check_text(f"{template.id}.label", template.label)
    _check_text(f"{template.id}.summary", template.summary)
    if not _positive(template.version):
        raise TemplateError("over_cap", f"{template.id}.version", "the version is a positive number")
    _check_sign_in(f"{template.id}.sign_in", template.sign_in, client_hosts)
    _check_hosts(template, client_hosts)
    context = list(template.context)
    if len(context) != len(set(context)) or not all(
        isinstance(k, str) and _KEY.fullmatch(k) for k in context
    ):
        raise TemplateError("context", f"{template.id}.context", "a context key is a key, named once")
    _check_limits(template)
    if not template.operations:
        raise TemplateError("op_name", template.id, "a template has at least one op")
    names = [op.name for op in template.operations]
    if len(names) != len(set(names)):
        raise TemplateError("duplicate_op", template.id, "an op name is used twice")
    for op in template.operations:
        _check_op(template, op)


# ── The call ────────────────────────────────────────────────────────────────


def build(template: Template, op_name: str, args: Mapping[str, Any]) -> Call:
    """The call of *op_name* with *args*, through the ONE builder (§2.1).

    The keys of *args* are a closed list: the path keys and the query keys of
    the row. A key that the gateway sets (a context key or the sign-in key) is
    refused as ``context``. The builder percent-encodes each path value. The
    caller then sets the context keys and the sign-in (§2.3, step 5).
    """
    op = template.op(op_name) if isinstance(op_name, str) else None
    if op is None:
        raise BadCall("op", "unknown")
    if isinstance(args, Mapping):
        for key in args:
            if key in template.reserved_keys():
                raise BadCall(str(key), "context")
        _check_size(op, args)
    return build_route_call(op.route, args)


def _check_size(op: Operation, args: Mapping[str, Any]) -> None:
    """Refuse a page size outside 1 to the op's page size, as ``size``.

    The validator already refuses a size shape that allows a larger number.
    This check holds the cap again at each call. The closed shape grammar has
    no alternation, so a size shape can take ``0``. This check refuses it.
    """
    if op.paging is None:
        return
    value = args.get(op.paging.size_key)
    if isinstance(value, bool) or value is None:
        return
    text = str(value) if isinstance(value, int) else value
    if isinstance(text, str) and text.isascii() and text.isdigit() and not 1 <= int(text) <= op.paging.size:
        raise BadCall(op.paging.size_key, "size")


__all__ = [
    "MAX_ANSWER_CAP",
    "MAX_PAGE_SIZE",
    "MAX_TOTAL_PAGES",
    "OP_CLASSES",
    "PATH_PROBES",
    "SIGN_INS",
    "TEMPLATE_ID_PATTERN",
    "ClientHosts",
    "Limits",
    "Operation",
    "Paging",
    "SignIn",
    "Template",
    "TemplateError",
    "Total",
    "build",
    "path_shape_is_closed",
    "validate",
]
