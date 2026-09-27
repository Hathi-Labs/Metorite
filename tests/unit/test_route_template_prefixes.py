"""`_route_template` reads the FULL template, with every include prefix on it.

🔴 **Why this file exists.** FastAPI 0.137 stopped copying a router's routes
into its parent. Since then ``scope["route"]`` is the ORIGINAL ``APIRoute``,
and its ``.path`` drops a prefix that ``include_router(prefix=...)`` or a
parent router adds. The default-deny guard and the feature-router guard both
compare that template with a list of public paths. Read without the prefix, a
public route is refused, and a private route whose short path spells a public
one is let through.

No database. Each test builds a small app, so the fence does not depend on
how the gateway happens to include its routers today.
"""

from __future__ import annotations

from acb_auth.deps import _route_template
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient


def _app_that_records(seen: list[str]) -> tuple[FastAPI, APIRouter]:
    async def record(request: Request) -> None:
        seen.append(_route_template(request))

    inner = APIRouter(prefix="/inner")

    @inner.get("/item/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    outer = APIRouter(prefix="/outer")
    outer.include_router(inner)
    app = FastAPI(dependencies=[Depends(record)])
    app.include_router(outer, prefix="/api")
    return app, inner


def test_a_nested_prefixed_route_reads_its_full_template() -> None:
    seen: list[str] = []
    app, _ = _app_that_records(seen)

    assert TestClient(app).get("/api/outer/inner/item/7").status_code == 200
    assert seen == ["/api/outer/inner/item/{item_id}"]


def test_one_route_included_twice_reads_the_prefix_that_served_it() -> None:
    seen: list[str] = []
    app, inner = _app_that_records(seen)
    app.include_router(inner, prefix="/again")
    client = TestClient(app)

    assert client.get("/again/inner/item/1").status_code == 200
    assert client.get("/api/outer/inner/item/2").status_code == 200
    assert seen == [
        "/again/inner/item/{item_id}",
        "/api/outer/inner/item/{item_id}",
    ]


def test_a_route_on_the_app_itself_reads_its_own_path() -> None:
    seen: list[str] = []

    async def record(request: Request) -> None:
        seen.append(_route_template(request))

    app = FastAPI(dependencies=[Depends(record)])

    @app.get("/health/{probe}")
    def health(probe: str) -> dict[str, str]:
        return {"probe": probe}

    assert TestClient(app).get("/health/x").status_code == 200
    assert seen == ["/health/{probe}"]
