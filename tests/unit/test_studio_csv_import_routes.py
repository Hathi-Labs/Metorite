"""The CSV import routes of AI Studio, the hermetic half (WS-STU C11b, task 1419).

Code: ``gateway/routes/studio/csv_import.py``. The planner has its own suite
(``test_studio_csv_import.py``), and the ledger and the walls of two orgs are
in ``test_studio_csv_import_r8.py``. Here the store calls are replaced by
recorders, so each test sees what the route asks of them.

This file proves: the switch (404 for an org that is not listed), the owner
rule (404 for anyone else), the body checks before any session, the preview
that writes nothing, the one approval over 100 rows, ``skip_invalid``, the
chunk loop with a retry of ``StoreBusy``, and an audit row that holds no cell.

**The mutations (R7).**

* In ``run_import``, drop the ``approve != digest`` check.
  ``test_a_large_import_needs_the_digest_of_the_preview`` goes red.
* In ``run_import``, drop the ``plan.errors and not skip`` check.
  ``test_an_invalid_row_stops_the_import_unless_skip_invalid`` goes red.
* In ``_owned_object``, drop the ``get_package`` check.
  ``test_a_member_who_is_not_the_owner_gets_404`` goes red.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.studio_fields import FieldSpec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions
from gateway.routes.studio import csv_import as route
from gateway.studio import batch_store, objects_store

ORG = str(uuid.uuid4())
PKG = str(uuid.uuid4())
OBJ = str(uuid.uuid4())
ANA = "ana@example.test"
FIELDS = [FieldSpec(key="sku", type="text", required=True), FieldSpec(key="qty", type="number")]
URL = f"/studio/packages/{PKG}/objects/ledger/import"
MAP = {"SKU": "sku", "Qty": "qty"}


def _csv(n: int, bad: set[int] = frozenset()) -> str:
    return "SKU,Qty\n" + "".join(f"s{i},{'x' if i in bad else 1}\n" for i in range(n))


class _World:
    def __init__(self) -> None:
        self.owner = True
        self.sessions = 0
        self.opened: list[dict[str, Any]] = []
        self.chunks: list[str] = []
        self.audits: list[dict[str, Any]] = []
        self.busy = 0
        self.pending: list[int] = [0]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    w = _World()

    @asynccontextmanager
    async def session() -> Any:
        w.sessions += 1
        yield SimpleNamespace()

    async def get_package(db: Any, member: str, pid: str) -> Any:
        assert member == ANA and pid == PKG
        return object() if w.owner else None

    async def get_object(db: Any, name: str, *, package_id: str) -> Any:
        return SimpleNamespace(id=OBJ, fields=FIELDS) if name == "ledger" else None

    async def open_batch(
        db: Any,
        actor: str,
        *,
        package_id: str,
        action: str,
        items: Any,
        expect_digest: str | None = None,
        **kw: Any,
    ) -> Any:
        w.opened.append({"actor": actor, "n": len(items), "digest": expect_digest})
        return batch_store.Batch(
            id=str(uuid.uuid4()),
            package_id=package_id,
            action=action,
            list_digest="d",
            total=len(items),
            status="open",
            run_id=None,
            node_id=None,
            created_by=actor,
            pending=len(items),
        )

    async def run_chunk(db: Any, actor: str, bid: str, writes: Any, *, viewer: Any) -> Any:
        assert viewer.email == ANA
        if w.busy:
            w.busy -= 1
            raise objects_store.StoreBusy("busy")
        w.chunks.append(bid)
        keys = sorted(writes)[(len(w.chunks) - 1) * 50 : len(w.chunks) * 50]
        left = max(0, len(writes) - len(w.chunks) * 50)
        return batch_store.ChunkResult(
            written={k: str(uuid.uuid4()) for k in keys},
            failed={},
            pending=left,
            status="open" if left else "done",
        )

    async def audit(db: Any, **kw: Any) -> None:
        w.audits.append(kw)

    monkeypatch.setattr(route, "_tenant_session", session)
    monkeypatch.setattr(route.packages_store, "get_package", get_package)
    monkeypatch.setattr(route.objects_store, "get_object_by_name", get_object)
    monkeypatch.setattr(route.batch_store, "open_batch", open_batch)
    monkeypatch.setattr(route.batch_store, "run_chunk", run_chunk)
    monkeypatch.setattr(route, "record_app_audit_in", audit)
    monkeypatch.setattr(get_settings(), "studio_orgs", ORG)
    return w


def _client(org: str | None = ORG) -> TestClient:
    app = FastAPI()
    app.include_router(actions.router)

    def _user() -> UserContext:
        return UserContext(
            email=ANA,
            role=UserRole.EMPLOYEE,
            access=build_access(["feature:studio"]),
            organization_id=org,
        )

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def test_an_org_that_is_not_listed_gets_404_on_both_routes(world: _World) -> None:
    c = _client(str(uuid.uuid4()))
    for tail in ("/preview", ""):
        assert c.post(URL + tail, json={"csv": _csv(1), "mapping": MAP}).status_code == 404
    assert world.sessions == 0


def test_a_member_who_is_not_the_owner_gets_404(world: _World) -> None:
    world.owner = False
    for tail in ("/preview", ""):
        assert _client().post(URL + tail, json={"csv": _csv(1), "mapping": MAP}).status_code == 404
    assert world.opened == []


def test_an_unknown_object_and_a_bad_name_get_404(world: _World) -> None:
    c = _client()
    body = {"csv": _csv(1), "mapping": MAP}
    assert c.post(f"/studio/packages/{PKG}/objects/nope/import", json=body).status_code == 404
    assert c.post(f"/studio/packages/{PKG}/objects/Bad-Name/import", json=body).status_code == 404
    assert c.post("/packages/not-a-uuid/objects/ledger/import", json=body).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"csv": 5},
        {"csv": "a\n1", "extra": 1},
        {"csv": "", "mapping": MAP},
        {"csv": _csv(1), "mapping": ["x"]},
        {"csv": _csv(1), "mapping": MAP, "skip_invalid": "yes"},
        {"csv": _csv(1), "mapping": MAP, "currency": 3},
    ],
)
def test_a_bad_body_is_422_before_any_write(world: _World, body: dict[str, Any]) -> None:
    r = _client().post(URL, json=body)
    assert r.status_code == 422, r.text
    assert world.opened == [] and world.chunks == []


def test_an_import_with_no_mapping_is_422(world: _World) -> None:
    assert _client().post(URL, json={"csv": _csv(2)}).status_code == 422


def test_the_preview_suggests_a_map_and_writes_nothing(world: _World) -> None:
    r = _client().post(URL + "/preview", json={"csv": _csv(3)})
    assert r.status_code == 200
    out = r.json()
    assert out["suggested_mapping"] == MAP | {} or out["suggested_mapping"] == {
        "SKU": "sku",
        "Qty": "qty",
    }
    assert out["valid_rows"] == 3 and out["needs_approval"] is False and len(out["digest"]) > 10
    assert out["preview"][0] == {"sku": "s0", "qty": 1}
    assert world.opened == [] and world.chunks == [] and world.audits == []


def test_the_preview_of_an_incomplete_map_says_what_is_missing(world: _World) -> None:
    out = _client().post(URL + "/preview", json={"csv": _csv(1), "mapping": {"Qty": "qty"}}).json()
    assert "required" in out["plan_problem"] and "valid_rows" not in out


def test_a_small_import_runs_in_one_chunk_and_audits_counts_only(world: _World) -> None:
    r = _client().post(URL, json={"csv": _csv(3), "mapping": MAP})
    assert r.status_code == 200
    out = r.json()
    assert (out["written"], out["pending"], out["status"], out["failed"]) == (3, 0, "done", [])
    assert world.opened[0]["n"] == 3 and len(world.chunks) == 1
    (a,) = world.audits
    assert a["detail"]["op"] == "csv_import" and a["detail"]["written"] == 3
    assert "s0" not in repr(a)


def test_a_large_import_needs_the_digest_of_the_preview(world: _World) -> None:
    c = _client()
    body = {"csv": _csv(120), "mapping": MAP}
    r = c.post(URL, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "approval_required"
    assert world.opened == []
    digest = r.json()["detail"]["digest"]
    assert c.post(URL, json=body | {"approve_digest": "0" * 64}).status_code == 409
    # A digest of another CSV does not approve this one.
    other = c.post(URL, json={"csv": _csv(121), "mapping": MAP}).json()["detail"]["digest"]
    assert c.post(URL, json=body | {"approve_digest": other}).status_code == 409
    ok = c.post(URL, json=body | {"approve_digest": digest})
    assert ok.status_code == 200 and ok.json()["written"] == 120
    assert len(world.chunks) == 3 and world.opened[0]["digest"] == digest
    assert world.sessions >= 5  # one session for each chunk, and none held between them


def test_the_preview_digest_is_the_approved_digest(world: _World) -> None:
    c = _client()
    p = c.post(URL + "/preview", json={"csv": _csv(120), "mapping": MAP}).json()
    assert p["needs_approval"] is True
    r = c.post(URL, json={"csv": _csv(120), "mapping": MAP, "approve_digest": p["digest"]})
    assert r.status_code == 200


def test_an_invalid_row_stops_the_import_unless_skip_invalid(world: _World) -> None:
    c = _client()
    body = {"csv": _csv(5, bad={2}), "mapping": MAP}
    r = c.post(URL, json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "rows_invalid"
    assert r.json()["detail"]["errors"][0]["row"] == 3 and world.opened == []
    ok = c.post(URL, json=body | {"skip_invalid": True})
    assert ok.status_code == 200
    assert ok.json()["written"] == 4 and ok.json()["invalid"][0]["field"] == "qty"
    assert "x" not in ok.json()["invalid"][0]["message"].replace("takes", "").replace(
        "exponent", ""
    )


def test_a_csv_with_no_valid_row_writes_nothing(world: _World) -> None:
    r = _client().post(
        URL, json={"csv": _csv(2, bad={0, 1}), "mapping": MAP | {}, "skip_invalid": True}
    )
    assert r.status_code == 422 and world.opened == []


def test_a_busy_store_is_tried_again_and_a_stuck_one_is_reported(world: _World) -> None:
    world.busy = 2
    ok = _client().post(URL, json={"csv": _csv(3), "mapping": MAP})
    assert ok.status_code == 200 and ok.json()["written"] == 3
    world.busy = 99
    stuck = _client().post(URL, json={"csv": _csv(3), "mapping": MAP})
    assert stuck.status_code == 200 and stuck.json()["stopped"] == "store_busy"
    assert stuck.json()["written"] == 0 and stuck.json()["pending"] == 3
    assert world.audits[-1]["detail"]["written"] == 0


def test_the_body_cap_is_413(world: _World) -> None:
    big = "a" * (route.MAX_BODY_BYTES + 10)
    assert (
        _client().post(URL, content=big, headers={"content-type": "application/json"}).status_code
        == 413
    )
