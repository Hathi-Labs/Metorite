"""Every path the Tasks and Calendar client calls is a path the gateway serves.

**Why this exists.** The `gtd_` retirement moves names in both halves of the
product, one slice at a time. The danger of that shape is not a broken query —
a query against a table that is gone fails loudly. The danger is that a route
moves and the client does not, or the reverse, because nothing in the tree
compares them. The frontend then calls a 404 and the app degrades to its mock
data silently, which is what this app does when the gateway does not answer.

Owner requirement, 2026-09-22: *"the connection between the frontend and
backend is also properly updated and maintained."* This is that requirement as
a test.

**One client file holds the whole contract.** Tasks AND Calendar both go
through `workbench/control_plane/src/app/tasks/lib/api.ts`, either by
`gatewayFetch(path)` (which prefixes `/api/tasks`, proxied to the gateway's
`/tasks` router) or by the lens table in `lens.ts` (which prefixes
`/api/projects`, proxied to `/projects`). So reading two files answers for
every call the Calendar makes.

⚠️ **This checks PATHS, not payloads.** A field renamed on a response model
still passes here. It is the cheap half of the contract, and the half a rename
sweep actually breaks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_TASKS_LIB = REPO / "workbench" / "control_plane" / "src" / "app" / "tasks" / "lib"
CLIENT = _TASKS_LIB / "api.ts"
LENS = _TASKS_LIB / "lens.ts"

#: `gatewayFetch<T>(`/calendar/day-state?day=${...}`)` and the plain-quote form.
_GATEWAY_FETCH = re.compile(r"gatewayFetch\s*(?:<[^>]*>)?\s*\(\s*([`'\"])(.*?)\1", re.S)

#: The lens table: `plan: "my/calendar/plan",`
_LENS_PATH = re.compile(r"^\s*[A-Za-z][A-Za-z0-9_]*:\s*\"(my/[^\"]+)\"", re.M)


def _literal_prefix(path: str) -> str:
    """The part of a client path that a route must start with.

    A template literal can interpolate an id (`/items/${id}/subtasks`), a
    whole query string (`/items${qs}`) or a variable segment chosen from a
    union (`/calendar/${kind}`). None of those can be resolved by reading the
    file, and resolving them is not what this fence is for. What it checks is
    that the LITERAL part in front still names something the gateway serves —
    which is exactly what a rename breaks and an id never does.
    """
    path = path.split("${", 1)[0]
    path = path.split("?", 1)[0]
    return path.rstrip("/") or "/"


def _client_paths() -> set[str]:
    text = CLIENT.read_text(encoding="utf-8")
    found = {"/tasks" + _literal_prefix(m.group(2))
             for m in _GATEWAY_FETCH.finditer(text)}
    lens = LENS.read_text(encoding="utf-8")
    found |= {"/projects/" + _literal_prefix(m.group(1))
              for m in _LENS_PATH.finditer(lens)}
    return {p for p in found if p not in ("/tasks", "/projects")}


def _served_paths() -> set[str]:
    from gateway.routes.projects import router as projects_router
    from gateway.routes.tasks import router as tasks_router

    served: set[str] = set()
    for router in (tasks_router, projects_router):
        for route in router.routes:
            path = getattr(route, "path", None)
            if path:
                served.add(path.rstrip("/") or "/")
    return served


def _unserved(client: set[str], served: set[str]) -> list[str]:
    """Client prefixes that no served route begins with."""
    return sorted(p for p in client if not any(s.startswith(p) for s in served))


def test_the_client_calls_at_least_one_path():
    """A regex that matches nothing makes every assertion below vacuous.

    The failure `_tenant_ladder.py` guards the same way, and for the same
    reason: an empty set is a subset of everything.
    """
    paths = _client_paths()
    assert len(paths) > 20, sorted(paths)
    assert "/tasks/settings" in paths
    assert "/tasks/calendar/day-state" in paths


def test_the_gateway_serves_at_least_one_path():
    served = _served_paths()
    assert len(served) > 40, len(served)


def test_every_client_path_is_a_route_the_gateway_serves():
    """The fence. A rename that moves one half and not the other fails here."""
    missing = _unserved(_client_paths(), _served_paths())
    assert not missing, (
        "the control plane calls paths the gateway does not serve: "
        f"{missing}. Either the route moved and the client did not, or the "
        "client moved and the route did not. The app answers a 404 by "
        "falling back to mock data, so this does not show as an error."
    )


@pytest.mark.parametrize(
    "path",
    [
        "/tasks/settings",
        "/tasks/calendar/day-state",
        "/projects/my/calendar/plan",
        "/projects/my/calendar/rollover",
    ],
)
def test_the_calendar_and_settings_doors_are_served_and_called(path):
    """Slice 2 renamed three tables underneath the settings and day-state
    doors, and no door moved. S8 PR 1 moved the planner doors onto the lens
    for good: the client calls `/projects/my/calendar/*` and nothing else."""
    assert path in _served_paths()
    assert path in _client_paths()


#: The `/tasks` routes S8 PR 1 deleted. None may come back as a served path,
#: and the client may call none of them.
RETIRED = [
    "/tasks/items", "/tasks/items/batch", "/tasks/items/bulk",
    "/tasks/items/bulk-archive", "/tasks/hierarchy", "/tasks/spaces",
    "/tasks/folders", "/tasks/local-projects", "/tasks/accounts",
    "/tasks/providers", "/tasks/sync", "/tasks/sync/status",
    "/tasks/status-catalog", "/tasks/projects", "/tasks/contexts",
    "/tasks/calendar", "/tasks/calendar/plan", "/tasks/calendar/replan",
    "/tasks/calendar/rollover", "/tasks/calendar/estimate-stats",
]


#: Literal prefixes the client still reads in front of a surviving door:
#: `/items/${id}/clarify` reads as `/tasks/items`, and `/calendar/${kind}`
#: (the agent planner) as `/tasks/calendar`. They are not calls to the
#: retired routes, so only the served half is checked for them.
_PREFIXES_OF_A_LIVE_DOOR = {"/tasks/items", "/tasks/calendar"}


@pytest.mark.parametrize("path", RETIRED)
def test_a_retired_door_is_neither_served_nor_called(path):
    assert path not in _served_paths()
    if path not in _PREFIXES_OF_A_LIVE_DOOR:
        assert path not in _client_paths()


def test_the_ai_doors_on_items_survive():
    """Three `/tasks/items/{id}/*` doors stay: the handler picks the store
    through `item_source()`, so they answer the one store."""
    served = _served_paths()
    for door in ("clarify", "enrich", "suggest-title"):
        assert f"/tasks/items/{{item_id}}/{door}" in served, door
