"""The query string the browser actually sends, through FastAPI's validation.

🔴 **The one kind of test this feature was missing, and the reason it broke.**

`Page` is a FastAPI class dependency. Its ceiling is enforced by
``Query(50, ge=1, le=MAX_PAGE_SIZE)``, which FastAPI applies **while parsing the
request** — before any handler body runs. So:

* calling ``get_timeline(...)`` directly, as every hermetic suite and every
  live harness here does, never runs that check at all;
* stubbing ``/api/**`` in the browser rig answers 200 whatever the query holds.

Both were green while the task panel asked for ``page_size=200`` against a cap
of 100, which 422'd every read and drew *"No comments yet"* over a task full of
comments. Two whole test strategies, and the defect sat in the gap between
them.

``test_projects_timeline_page_size.py`` compares the two constants as text.
This one goes the other way: it builds the dependency FastAPI builds and sends
the real query string through a real client. Text agreement and runtime
acceptance are different claims, and the bug needed both to be checked.

Two readings, because a throwaway app and the real route are different claims:

* ``test_the_panels_own_query_string_is_accepted`` mounts ``Page`` on a small
  app and sends a request. That exercises FastAPI's validation for real.
* ``test_the_real_route_advertises_the_contract_the_client_codes_against``
  reads the REGISTERED route's OpenAPI schema. That is the actual endpoint,
  with its actual signature, and it needs no auth, no database and no tenant
  binding to answer what it accepts.

The second is the stronger one and was added after the first, when the first
turned out to prove a claim about a route nobody calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from gateway.routes.projects.core import MAX_PAGE_SIZE, Page

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()

    @app.get("/timeline")
    async def timeline(kind: str = "all", page: Page = Depends()) -> dict:
        return {"kind": kind, "page_size": page.limit, "offset": page.offset}

    return TestClient(app)


def _client_cap() -> int:
    """`MAX_TIMELINE_PAGE`, read from the TypeScript the browser ships."""
    source = (
        REPO / "workbench/control_plane/src/app/projects/lib/api.ts"
    ).read_text(encoding="utf-8")
    found = re.search(r"MAX_TIMELINE_PAGE:\s*(\d+)", source)
    assert found is not None, "api.ts no longer declares MAX_TIMELINE_PAGE"
    return int(found.group(1))


def test_the_panels_own_query_string_is_accepted(client: TestClient) -> None:
    """THE regression, in the shape the browser sends it.

    Not `page_size=100` written here by hand — the number is read out of the
    client source, so a future edit that raises it past the cap fails here
    rather than in somebody's panel.
    """
    got = client.get(f"/timeline?kind=comments&page_size={_client_cap()}")
    assert got.status_code == 200, got.text
    assert got.json()["page_size"] == _client_cap()


def test_one_over_the_cap_is_refused_before_the_handler_runs(
    client: TestClient,
) -> None:
    """And the refusal is a 422, which is why the panel emptied.

    ⚠️ A 422 is not a short list. The client throws, both `setComments` and
    `setEvents` are skipped, and the surface renders its empty state over a
    task that has content. A cap that answered with a truncated page would
    have been a far quieter failure.
    """
    got = client.get(f"/timeline?page_size={MAX_PAGE_SIZE + 1}")
    assert got.status_code == 422
    assert "page_size" in got.text


def test_omitting_it_still_gets_the_default(client: TestClient) -> None:
    """Every caller written before this feature sends no `page_size`."""
    got = client.get("/timeline")
    assert got.status_code == 200
    assert got.json()["page_size"] == 50


def test_the_kind_parameter_rides_the_same_request(client: TestClient) -> None:
    """The two new query parameters do not fight.

    `kind` is a plain handler argument and `page_size` comes from a class
    dependency. Mixing the two forms on one route is exactly where a
    signature ordering mistake would show, and it would show as a 422 on
    every panel open.
    """
    got = client.get("/timeline?kind=events&page_size=10")
    assert got.status_code == 200
    assert got.json() == {"kind": "events", "page_size": 10, "offset": 0}


def _timeline_parameters() -> dict:
    """The REGISTERED route's query contract, from its OpenAPI schema.

    ⚠️ The real endpoint, not a stand-in. Building the schema runs FastAPI's
    whole signature analysis — the same pass that decides what a request is
    allowed to carry — and it needs no auth, no database and no tenant. So
    this is the cheapest honest reading of the contract the browser codes
    against.
    """
    import sys

    sys.path.insert(0, str(REPO / "apps/services/gateway"))
    from gateway.routes.projects import activities  # noqa: F401  (registers)
    from gateway.routes.projects.core import router

    app = FastAPI()
    app.include_router(router)
    spec = app.openapi()
    path = next(p for p in spec["paths"] if p.endswith("/tasks/{task_id}/timeline"))
    return {
        p["name"]: p for p in spec["paths"][path]["get"].get("parameters", [])
    }


def test_the_real_route_advertises_the_contract_the_client_codes_against() -> None:
    params = _timeline_parameters()

    # `kind` must be OPTIONAL. Every caller written before 2026-09-21 omits
    # it, and a required parameter would 422 all of them.
    assert params["kind"]["in"] == "query"
    assert params["kind"]["schema"]["default"] == "all"
    assert not params["kind"].get("required", False)

    # 🔴 The ceiling the browser has to respect, read from the route itself.
    ceiling = params["page_size"]["schema"]["maximum"]
    assert ceiling == MAX_PAGE_SIZE
    assert _client_cap() <= ceiling, (
        f"the panel asks for {_client_cap()} and this route caps at {ceiling}; "
        "that is a 422 on every panel open, and an EMPTY discussion section "
        "rather than a shortened one"
    )
