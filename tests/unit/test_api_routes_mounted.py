"""API-3 (task 3789): the app mounts each API route exactly once (R7).

``gateway.main`` includes the API routers inside ``try/except Exception``. An
import error there would drop the routes in silence, and every other test
builds its own ``FastAPI`` app. So this test reads the real ``main.app``.
``served_routes`` expands the mounted sub-apps, as ``test_default_deny_auth``
reads them. Mutation: replace ``app.include_router(_api_connections_router)`` with ``pass``
and ``test_the_four_call_routes_are_mounted_once`` fails.
"""

from __future__ import annotations

from collections import Counter

import pytest

from tests.unit._routes import served_routes

_CALL_ROUTES = (
    ("GET", "/connections/api/{name}/ops"),
    ("GET", "/connections/api/{name}/ops/{op}"),
    ("POST", "/connections/api/{name}/read"),
    ("POST", "/connections/api/{name}/write"),
)
_CONNECT_ROUTES = (
    ("POST", "/integrations/api/zoho_books/start"),
    ("GET", "/integrations/api/zoho/callback"),
    ("POST", "/integrations/api/zoho_books/pick"),
    ("GET", "/integrations/api/zoho_books"),
)


def _mounted() -> Counter[tuple[str, str]]:
    main = pytest.importorskip("gateway.main")
    seen: Counter[tuple[str, str]] = Counter()
    for route in served_routes(main.app.routes):
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", None) or ():
            seen[(method, path)] += 1
    return seen


def test_the_four_call_routes_are_mounted_once() -> None:
    seen = _mounted()
    for key in _CALL_ROUTES:
        assert seen[key] == 1, (key, seen[key])


def test_the_four_connect_routes_are_mounted_once() -> None:
    seen = _mounted()
    for key in _CONNECT_ROUTES:
        assert seen[key] == 1, (key, seen[key])
