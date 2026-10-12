"""A runtime tripwire for branch 0 of ``get_current_user`` (WS-55 EA-3a).

Spec: ``project-docs/specs/external_agent_access.md`` §10.1. Branch 0 reads
no host-derived input. The static scan in ``test_tenant_request_binding.py``
covers the shapes an AST can see. This module covers every path a test
EXERCISES, whatever the static shape of the read.

What a wrapped request allows, and nothing else:

* ``method``, ``headers`` and ``scope``. Every other attribute is a
  violation: ``url``, ``base_url``, ``client``, ``state``, ``app``,
  ``cookies``, ``query_params``, ``path_params``, ``stream``, ``body``,
  ``form``, ``_real``, anything.
* ``headers.get``, ``getlist``, ``[name]`` and ``name in`` for the names of
  :data:`ALLOWED_HEADERS`. ``items``, ``keys``, ``values``, ``raw``,
  ``repr``, ``str`` and every other attribute are violations.
  ⚠️ ``iter(headers)`` yields the header NAMES, a deliberate exception: the
  trigger of §10.1 is "any header whose name starts with ``X-Agent-``". A
  value still goes through ``get`` or ``[...]``, which are checked.
* ``scope.get`` and ``scope[key]`` for :data:`ALLOWED_SCOPE_KEYS`. While
  Starlette's or FastAPI's route ``matches`` runs (and only then), also
  :data:`MATCHES_SCOPE_KEYS`, which that code reads. Iteration, ``len``,
  ``in``, ``keys``, ``items``, ``values``, ``copy``, ``pop``,
  ``setdefault``, ``repr`` and ``str`` are violations. The scope is NOT a
  ``dict``, so ``dict.get(scope, ...)`` raises ``TypeError``.

⚠️ **A violation cannot be swallowed.** :class:`TripwireError` is a
``BaseException``, so ``except Exception`` does not catch it. Every violation
is ALSO appended to :data:`VIOLATIONS`, and the fixtures that arm the tripwire
call :func:`assert_clean` at TEARDOWN. So ``except BaseException`` or a bare
``except`` cannot hide one either.

⚠️ **The real request is not reachable through the wrapper's attributes.**
It lives in a closure: ``request._real`` and ``request.headers._real`` are
violations too.

What it cannot prove: a path that no test exercises, and access that goes
around the wrapper (a frame walk, ``gc``, a closure cell).
"""

from __future__ import annotations

import contextvars
from typing import Annotated, Any

from fastapi import Header, Request

#: The four signed headers, and the two that tell branch 0 to refuse a body.
ALLOWED_HEADERS = frozenset({
    "x-agent-connection", "x-agent-timestamp", "x-agent-signature",
    "x-actor-via", "content-length", "transfer-encoding",
})

#: ``route`` and ``app`` find the matched template. ``path`` and
#: ``query_string`` are the signed path and query.
ALLOWED_SCOPE_KEYS = frozenset({"route", "app", "path", "query_string"})

#: What ``RouteContext.matches`` reads, through ``_EffectiveRouteContext``,
#: ``APIRoute`` or Starlette's ``Route``: the type, the path and root path,
#: the path params, the method, and FastAPI's own scope key. Allowed ONLY
#: inside a ``matches`` call. ``test_tenant_request_binding.py`` pins the set
#: that a route served at two prefixes really reads.
MATCHES_SCOPE_KEYS = frozenset({
    "type", "path", "root_path", "path_params", "method", "fastapi",
})

#: Every violation of the running test. The arming fixtures call
#: :func:`assert_clean` at teardown.
VIOLATIONS: list[str] = []

_IN_MATCHES: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "branch_zero_tripwire_in_matches", default=False)

#: The classes this module makes, so a wrapped request is never wrapped twice.
_WRAPPER_CLASSES: set[type] = set()


class TripwireError(BaseException):
    """Branch 0 read something outside the allowlist. NOT an ``Exception``."""


def _violate(what: str) -> Any:
    VIOLATIONS.append(what)
    raise TripwireError(f"branch 0 read {what}")


def _headers(real: Any, log: list[str]) -> Any:
    def _check(name: Any) -> None:
        if not isinstance(name, str) or name.lower() not in ALLOWED_HEADERS:
            _violate(f"the header {name!r}")
        log.append(name.lower())

    class _Headers:
        __slots__ = ()

        def get(self, key: Any, default: Any = None) -> Any:
            _check(key)
            return real.get(key, default)

        def getlist(self, key: Any) -> list[str]:
            _check(key)
            return real.getlist(key)

        def __getitem__(self, key: Any) -> Any:
            _check(key)
            return real[key]

        def __contains__(self, key: Any) -> bool:
            _check(key)
            return key in real

        def __iter__(self):
            # NAMES only, for the trigger. See the module docstring.
            return iter(list(real.keys()))

        def __repr__(self) -> str:
            return _violate("repr(request.headers)")

        __str__ = __repr__

        def __getattr__(self, name: str) -> Any:
            return _violate(f"request.headers.{name}")

    return _Headers()


def _scope(real: dict[str, Any], log: list[str]) -> Any:
    def _check(key: Any) -> None:
        allowed = key in ALLOWED_SCOPE_KEYS or (
            _IN_MATCHES.get() and key in MATCHES_SCOPE_KEYS)
        if not allowed:
            _violate(f"the scope key {key!r}")
        log.append(("matches:" if _IN_MATCHES.get() else "") + str(key))

    class _Scope:
        __slots__ = ()

        def get(self, key: Any, default: Any = None) -> Any:
            _check(key)
            return real.get(key, default)

        def __getitem__(self, key: Any) -> Any:
            _check(key)
            return real[key]

        def _refuse(self, *_a: Any, **_k: Any) -> Any:
            return _violate("the whole scope")

        __iter__ = _refuse
        __len__ = _refuse
        __contains__ = _refuse
        __repr__ = _refuse
        __str__ = _refuse
        keys = _refuse
        items = _refuse
        values = _refuse
        copy = _refuse
        pop = _refuse
        setdefault = _refuse

        def __getattr__(self, name: str) -> Any:
            return _violate(f"request.scope.{name}")

    return _Scope()


def wrap(real: Any, log: dict[str, list[str]] | None = None) -> Any:
    """A request that answers only ``method``, ``headers`` and ``scope``."""
    if type(real) in _WRAPPER_CLASSES:
        return real
    log = log if log is not None else {"headers": [], "scope": []}
    headers = _headers(real.headers, log["headers"])
    scope = _scope(real.scope, log["scope"])
    method = real.method

    class _TripwireRequest:
        __slots__ = ()

        def __getattr__(self, name: str) -> Any:
            if name == "method":
                return method
            if name == "headers":
                return headers
            if name == "scope":
                return scope
            return _violate(f"request.{name}")

        def __repr__(self) -> str:
            return _violate("repr(request)")

        __str__ = __repr__

    _WRAPPER_CLASSES.add(_TripwireRequest)
    return _TripwireRequest()


class Armed:
    """What :func:`arm` returns: the reads of every wrapped request."""

    def __init__(self, deps: Any) -> None:
        self.deps = deps
        self.logs: list[dict[str, list[str]]] = []

    def wrap(self, real: Any) -> Any:
        if type(real) in _WRAPPER_CLASSES:
            return real
        log: dict[str, list[str]] = {"headers": [], "scope": []}
        self.logs.append(log)
        return wrap(real, log)

    @property
    def scope_reads(self) -> set[str]:
        return {k for log in self.logs for k in log["scope"]}

    @property
    def header_reads(self) -> set[str]:
        return {h for log in self.logs for h in log["headers"]}


def _patch_matches(monkeypatch: Any) -> None:
    """Let the route matchers read :data:`MATCHES_SCOPE_KEYS`, and only them."""
    import fastapi.routing as fr
    import starlette.routing as sr

    for owner in (fr._EffectiveRouteContext, fr.APIRoute, sr.Route):
        original = owner.__dict__.get("matches")
        if original is None:
            continue

        def _matches(self: Any, scope: Any, _original: Any = original) -> Any:
            token = _IN_MATCHES.set(True)
            try:
                return _original(self, scope)
            finally:
                _IN_MATCHES.reset(token)

        monkeypatch.setattr(owner, "matches", _matches)


def arm(monkeypatch: Any, deps: Any, agent_user: Any = None) -> Armed:
    """Wrap every request that branch 0 sees, for the rest of the test.

    Patches ``deps._is_agent_request`` and ``deps._agent_user`` so a direct
    caller of ``get_current_user`` is covered too. :func:`current_user_override`
    covers the body of ``get_current_user`` itself, for an app. *agent_user*
    replaces ``_agent_user``, for a mutation test. Clears :data:`VIOLATIONS`;
    the caller calls :func:`assert_clean` at teardown.
    """
    VIOLATIONS.clear()
    armed = Armed(deps)
    is_agent = deps._is_agent_request
    user = agent_user or deps._agent_user

    def _trigger(request: Any) -> bool:
        return is_agent(armed.wrap(request))

    async def _branch_zero(request: Any) -> Any:
        return await user(armed.wrap(request))

    monkeypatch.setattr(deps, "_is_agent_request", _trigger)
    monkeypatch.setattr(deps, "_agent_user", _branch_zero)
    _patch_matches(monkeypatch)
    return armed


def current_user_override(armed: Armed) -> Any:
    """A stand-in for ``get_current_user`` that hands the REAL one a wrapped
    request, so the tripwire also sees the body of ``get_current_user``.
    Install it with ``app.dependency_overrides[get_current_user] = ...``."""
    deps = armed.deps
    real_get_current_user = deps.get_current_user

    async def _get_current_user(
        x_user_email: Annotated[str | None, Header(alias="X-User-Email")] = None,
        x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        request: Request = None,  # type: ignore[assignment]
    ) -> Any:
        return await real_get_current_user(
            x_user_email=x_user_email, x_user_role=x_user_role,
            authorization=authorization,
            request=armed.wrap(request) if request is not None else None,
        )

    return _get_current_user


def assert_clean() -> None:
    """The teardown check. A swallowed violation still fails here."""
    found = list(VIOLATIONS)
    VIOLATIONS.clear()
    assert not found, f"branch 0 read outside the allowlist: {found}"
