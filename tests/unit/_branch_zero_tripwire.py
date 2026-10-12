"""A runtime tripwire for branch 0 of ``get_current_user`` (WS-55 EA-3a).

Spec: ``project-docs/specs/external_agent_access.md`` §10.1. Branch 0 must
read no host-derived input. The static scan in
``test_tenant_request_binding.py`` refuses a list of construct classes in the
branch-0 functions. This module records the reads it can see through a
wrapped request, on the paths that the tests run.

What a wrapped request answers, and nothing else:

* ``method``, ``headers`` and ``scope``. Any other attribute is a recorded
  violation: ``url``, ``base_url``, ``client``, ``state``, ``app``,
  ``cookies``, ``query_params``, ``path_params``, ``stream``, ``body``,
  ``form``, ``_real`` and every other name.
* ``headers.get``, ``getlist``, ``[name]`` and ``name in`` for the names of
  :data:`ALLOWED_HEADERS`. The name must be an exact ``str``: a ``str``
  subclass (which can change its own ``lower()``) is a violation. ``items``,
  ``keys``, ``values``, ``raw``, ``repr`` and ``str`` are violations.
  ⚠️ ``iter(headers)`` yields the header NAMES, a deliberate exception: the
  trigger of §10.1 is "any header whose name starts with ``X-Agent-``".
* ``scope.get`` and ``scope[key]`` for :data:`ALLOWED_SCOPE_KEYS`. Inside a
  ``matches`` call of one of the three route classes (and only there), also
  :data:`MATCHES_SCOPE_KEYS`. A ``matches`` call with any other receiver is a
  violation. Iteration, ``len``, ``in``, ``keys``, ``items``, ``values``,
  ``copy``, ``pop``, ``setdefault``, ``repr`` and ``str`` are violations. The
  scope is not a ``dict``. So ``dict.get(scope, ...)`` raises ``TypeError``,
  and the tripwire records that ``TypeError`` too, even when the code
  catches it (a ``sys.monitoring`` RAISE hook, armed only during the test).

How a violation is kept. :class:`TripwireError` is a ``BaseException``, so
``except Exception`` does not catch it. Each violation is also appended to
:data:`VIOLATIONS`, and the arming fixtures call :func:`assert_clean` at
teardown. So a violation that the code catches still fails the test.

What this tripwire does NOT stop. It sees only the reads that go through the
wrapper, on the paths that the tests run. Deliberate code can go around it:
a walk up the caller frames to the real request, a closure cell reached
through a helper that the scan does not cover, or ``gc``. Neither this
tripwire nor the static scan stops deliberate code. The guard against
deliberate code is the digest test and the review rule of §10.1.
"""

from __future__ import annotations

import contextvars
import sys
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
#: inside a ``matches`` call of one of those classes.
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

#: The names of the wrapper classes, as a TypeError message names them.
_WRAPPER_NAMES = ("'_Scope'", "'_Headers'", "'_TripwireRequest'")


class TripwireError(BaseException):
    """Branch 0 read something outside the allowlist. NOT an ``Exception``."""


def _violate(what: str) -> Any:
    VIOLATIONS.append(what)
    raise TripwireError(f"branch 0 read {what}")


# ── the RAISE hook: a TypeError on a wrapper is recorded, caught or not ──────


def _on_raise(code: Any, offset: int, exc: BaseException) -> Any:
    if isinstance(exc, TypeError) and any(n in str(exc) for n in _WRAPPER_NAMES):
        VIOLATIONS.append(f"a TypeError on the wrapper: {exc}")
    return None


class _Monitor:
    """``sys.monitoring`` RAISE events, on while ``armed`` is True.

    ``monkeypatch.setattr(MONITOR, "armed", True)`` turns it on, and the
    undo of that patch turns it off again, so it never outlives the test.
    """

    tool: int | None = None

    @property
    def armed(self) -> bool:
        return self.tool is not None

    @armed.setter
    def armed(self, value: bool) -> None:
        monitoring = sys.monitoring
        if value and self.tool is None:
            for tool in (3, 4, 5):
                if monitoring.get_tool(tool) is None:
                    monitoring.use_tool_id(tool, "branch_zero_tripwire")
                    monitoring.register_callback(
                        tool, monitoring.events.RAISE, _on_raise)
                    monitoring.set_events(tool, monitoring.events.RAISE)
                    self.tool = tool
                    return
            raise RuntimeError("no free sys.monitoring tool id")
        if not value and self.tool is not None:
            monitoring.set_events(self.tool, 0)
            monitoring.register_callback(self.tool, monitoring.events.RAISE, None)
            monitoring.free_tool_id(self.tool)
            self.tool = None


MONITOR = _Monitor()


# ── the wrappers ─────────────────────────────────────────────────────────────


def _headers(real: Any, log: list[str]) -> Any:
    def _check(name: Any) -> None:
        if type(name) is not str or name.lower() not in ALLOWED_HEADERS:
            _violate(f"the header {name!r} (type {type(name).__name__})")
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
        allowed = type(key) is str and (key in ALLOWED_SCOPE_KEYS or (
            _IN_MATCHES.get() and key in MATCHES_SCOPE_KEYS))
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


def _route_classes() -> tuple[type, ...]:
    import fastapi.routing as fr
    import starlette.routing as sr

    return (fr._EffectiveRouteContext, fr.APIRoute, sr.Route)


def _patch_matches(monkeypatch: Any) -> None:
    """Let the route matchers read :data:`MATCHES_SCOPE_KEYS`, and only them,
    and only for a receiver of one of the three route classes."""
    classes = _route_classes()
    for owner in classes:
        original = owner.__dict__.get("matches")
        if original is None:
            continue

        def _matches(self: Any, scope: Any, _original: Any = original) -> Any:
            if not isinstance(self, classes):
                _violate(f"matches() on a {type(self).__name__}")
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
    replaces ``_agent_user``, for a mutation test. Clears :data:`VIOLATIONS`
    and arms the RAISE hook until the test ends; the caller calls
    :func:`assert_clean` at teardown.
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
    monkeypatch.setattr(MONITOR, "armed", True)
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
    """The teardown check. A caught violation still fails here."""
    found = list(VIOLATIONS)
    VIOLATIONS.clear()
    assert not found, f"branch 0 read outside the allowlist: {found}"
