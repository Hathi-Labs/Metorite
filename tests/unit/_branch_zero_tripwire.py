"""A runtime tripwire for branch 0 of ``get_current_user`` (WS-55 EA-3a).

Spec: ``project-docs/specs/external_agent_access.md`` §10.1. Branch 0 reads
no host-derived input. The static scan in ``test_tenant_request_binding.py``
proves that for the shapes an AST can see. This module proves it for every
path a test EXERCISES, whatever the static shape of the read.

:class:`TripwireRequest` wraps a real Starlette ``Request``. Branch 0 gets the
wrapper, and any read outside the allowlist raises :class:`TripwireError`:

* ``method``, ``headers`` and ``scope`` are the only attributes. ``url``,
  ``base_url``, ``client``, ``state``, ``app``, ``cookies``, ``query_params``,
  ``path_params``, ``stream``, ``body``, ``form`` and every other attribute
  raise.
* ``headers`` answers ``get``, ``getlist``, ``[name]`` and ``name in`` for the
  names of :data:`ALLOWED_HEADERS` only. ``items``, ``keys``, ``values``,
  ``raw`` and every other attribute raise.

  ⚠️ ``iter(headers)`` yields the header NAMES, and that is a deliberate
  exception. The trigger of §10.1 is "any header whose name starts with
  ``X-Agent-``", which needs the names. A name is not a value: a read of the
  ``Host`` value still has to go through ``get`` or ``[...]``, which raise.
* ``scope`` is a dict that answers ``get`` and ``[key]`` for the keys of
  :data:`ALLOWED_SCOPE_KEYS` only. Iteration, ``keys``, ``items``, ``values``,
  ``copy`` and ``in`` raise. The allowed keys are EXACTLY what branch 0 reads
  on its paths (``test_mcp_edge_signing.py`` records and pins them).

What it cannot prove: a path that no test exercises.
"""

from __future__ import annotations

from typing import Any

#: The four signed headers, and the two that tell branch 0 to refuse a body.
ALLOWED_HEADERS = frozenset({
    "x-agent-connection", "x-agent-timestamp", "x-agent-signature",
    "x-actor-via", "content-length", "transfer-encoding",
})

#: ``route`` and ``app`` find the matched template. ``path`` and
#: ``query_string`` are the signed path and query.
ALLOWED_SCOPE_KEYS = frozenset({"route", "app", "path", "query_string"})


class TripwireError(AssertionError):
    """Branch 0 read something outside the allowlist."""


class _Headers:
    def __init__(self, real: Any, log: list[str]) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_log", log)

    def _check(self, name: Any) -> None:
        if not isinstance(name, str) or name.lower() not in ALLOWED_HEADERS:
            raise TripwireError(f"branch 0 read the header {name!r}")
        self._log.append(name.lower())

    def get(self, key: Any, default: Any = None) -> Any:
        self._check(key)
        return self._real.get(key, default)

    def getlist(self, key: Any) -> list[str]:
        self._check(key)
        return self._real.getlist(key)

    def __getitem__(self, key: Any) -> Any:
        self._check(key)
        return self._real[key]

    def __contains__(self, key: Any) -> bool:
        self._check(key)
        return key in self._real

    def __iter__(self):
        # NAMES only, for the trigger. See the module docstring.
        return iter(list(self._real.keys()))

    def __getattr__(self, name: str) -> Any:
        raise TripwireError(f"branch 0 read request.headers.{name}")


class _Scope(dict):
    def __init__(self, real: dict[str, Any], log: list[str]) -> None:
        super().__init__(real)
        self._log = log

    def _check(self, key: Any) -> None:
        if key not in ALLOWED_SCOPE_KEYS:
            raise TripwireError(f"branch 0 read the scope key {key!r}")
        self._log.append(key)

    def __getitem__(self, key: Any) -> Any:
        self._check(key)
        return super().__getitem__(key)

    def get(self, key: Any, default: Any = None) -> Any:
        self._check(key)
        return super().get(key, default)

    def _refuse(self, *_a: Any, **_k: Any) -> Any:
        raise TripwireError("branch 0 walked the whole scope")

    __iter__ = _refuse
    __contains__ = _refuse
    keys = _refuse
    items = _refuse
    values = _refuse
    copy = _refuse
    pop = _refuse
    setdefault = _refuse


class TripwireRequest:
    """A ``Request`` that raises on any read outside the allowlist."""

    def __init__(self, real: Any) -> None:
        log: dict[str, list[str]] = {"headers": [], "scope": []}
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_headers", _Headers(real.headers, log["headers"]))
        object.__setattr__(self, "_scope", _Scope(real.scope, log["scope"]))
        object.__setattr__(self, "reads", log)

    @property
    def method(self) -> str:
        return self._real.method

    @property
    def headers(self) -> _Headers:
        return self._headers

    @property
    def scope(self) -> _Scope:
        return self._scope

    def __getattr__(self, name: str) -> Any:
        raise TripwireError(f"branch 0 read request.{name}")


def arm(monkeypatch: Any, deps: Any, agent_user: Any = None) -> list[TripwireRequest]:
    """Wrap every request that branch 0 sees, for the rest of the test.

    Branch 0 is ``deps._is_agent_request`` (the trigger) and
    ``deps._agent_user`` (the seven steps). ``get_current_user`` itself only
    asks ``request is not None`` and hands the request to those two (the
    static scan pins that). *agent_user* replaces the second one, for a
    mutation test. The returned list collects each wrapper, so a test can
    read what was read.
    """
    seen: list[TripwireRequest] = []
    is_agent = deps._is_agent_request
    user = agent_user or deps._agent_user

    def _wrap(request: Any) -> TripwireRequest:
        if isinstance(request, TripwireRequest):
            return request
        wrapped = TripwireRequest(request)
        seen.append(wrapped)
        return wrapped

    def _trigger(request: Any) -> bool:
        return is_agent(_wrap(request))

    async def _branch_zero(request: Any) -> Any:
        return await user(_wrap(request))

    monkeypatch.setattr(deps, "_is_agent_request", _trigger)
    monkeypatch.setattr(deps, "_agent_user", _branch_zero)
    return seen
