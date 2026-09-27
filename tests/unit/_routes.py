"""Walk every route an app serves, at the path it serves it on.

🔴 **``app.routes`` is a TREE since FastAPI 0.137.** ``include_router`` no
longer copies a router's routes into the app. It adds one ``_IncludedRouter``
node that holds the router, so a walk over ``app.routes`` sees the handful of
routes declared on the app itself and nothing else. A structural test that
walks it passes with nothing checked. FastAPI's release notes call
``router.routes`` an internal detail from 0.137 on.

``iter_route_contexts`` (FastAPI 0.137.2) is the public walk. Each item it
yields has the effective ``path`` (every include prefix on it), ``methods``,
``endpoint``, ``name`` and the combined ``dependencies``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def served_routes(routes: Sequence[Any]) -> list[Any]:
    """Every route under ``routes``, flattened, at its served path."""
    from fastapi.routing import iter_route_contexts

    return list(iter_route_contexts(routes))
