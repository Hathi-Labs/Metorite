"""An operator sets how many seats an organization holds (2026-09-26).

Owner request: the operator console could assign a seat to a person but could
not change the COUNT. ``POST /billing/seats/count`` sets a target total and
writes the difference as a signed ``seat_grant`` row.

**R8.** Every clause drives the real route against a real Postgres.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()
TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=("CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
            "A skip here is not a pass; CI must set it."),
)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _URL:
        return
    with create_engine(_URL, future=True).begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", "internal")
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture
def org(client, db):
    with db.begin() as c:
        ensure_deployment(c)
    slug = f"sc-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "core_seats": 5, "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    assert r.status_code == 200, r.text
    return slug


def _purchased(db, slug: str) -> int:
    with db.connect() as c:
        return int(c.execute(text(
            "SELECT COALESCE(sum(g.quantity_purchased), 0) FROM seat_grant g "
            "JOIN organization o ON o.id = g.organization_id "
            "WHERE o.slug = :s AND g.plan_slug = 'core'"), {"s": slug}).scalar_one())


def _count(client, slug, seats, reason="trial extension"):
    return client.post("/billing/seats/count", headers=OP, json={
        "org_slug": slug, "plan_slug": "core", "seats": seats, "reason": reason})


def test_it_raises_the_count_to_the_target(client, db, org):
    assert _purchased(db, org) == 5
    r = _count(client, org, 12)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["previous"], body["seats"], body["changed"]) == (5, 12, True)
    assert _purchased(db, org) == 12


def test_a_reduction_is_a_NEGATIVE_row_and_history_survives(client, db, org):
    assert _count(client, org, 3).status_code == 200
    assert _purchased(db, org) == 3
    with db.connect() as c:
        rows = [int(r[0]) for r in c.execute(text(
            "SELECT g.quantity_purchased FROM seat_grant g JOIN organization o "
            "ON o.id = g.organization_id WHERE o.slug = :s ORDER BY g.created_at"),
            {"s": org})]
    assert rows[-1] == -2
    assert 5 in rows, "the original grant was rewritten, not appended to"


def test_it_refuses_to_go_below_the_seats_in_use(client, db, org):
    """The owner holds one seat, so zero must be refused and nothing written."""
    r = _count(client, org, 0)
    assert r.status_code == 409
    assert "in use" in r.text
    assert _purchased(db, org) == 5


def test_the_same_number_writes_nothing(client, db, org):
    r = _count(client, org, 5)
    assert r.status_code == 200 and r.json()["changed"] is False
    with db.connect() as c:
        n = c.execute(text(
            "SELECT count(*) FROM seat_grant g JOIN organization o "
            "ON o.id = g.organization_id WHERE o.slug = :s"), {"s": org}).scalar_one()
    assert n == 1


def test_every_change_is_audited_with_its_reason(client, db, org):
    _count(client, org, 9, reason="partner pilot")
    with db.connect() as c:
        detail = c.execute(text(
            "SELECT a.detail FROM control_audit a JOIN organization o "
            "ON o.id = a.organization_id WHERE o.slug = :s AND a.action = 'seat.count'"),
            {"s": org}).scalar_one()
    assert detail["from"] == 5 and detail["to"] == 9
    assert detail["reason"] == "partner pilot"


def test_a_reason_is_required(client, org):
    r = client.post("/billing/seats/count", headers=OP, json={
        "org_slug": org, "plan_slug": "core", "seats": 7})
    assert r.status_code == 422


def test_it_needs_admin_with_elevation():
    from customer_console import operator_roles

    rule = operator_roles.MATRIX[("POST", "/billing/seats/count")]
    assert rule.min_role == operator_roles.ADMIN and rule.elevated is True
