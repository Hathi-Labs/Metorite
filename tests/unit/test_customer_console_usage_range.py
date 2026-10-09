"""A chosen date range on the operator usage reads. WS-50 slice 7.

Spec: ``project-docs/specs/operator_console_money.md`` §4, slice 7.

The owner asked to analyse any period, not only "the last 30 days". The
usage reads take ``from`` and ``to`` (inclusive calendar days in India).

The failure modes:

  1. **An edge off by a day or a time zone.** A call at 23:30 IST on the last
     day is inside. A call at 00:10 IST the next day is outside. Cut in UTC,
     both land on the wrong side.
  2. **A range that changes the default.** With no ``from``, every read must
     answer exactly as before.
  3. **A range that is clamped instead of refused.** It would answer a
     question nobody asked.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL, reason="R8 requires a REAL Postgres; set CUSTOMER_CONSOLE_DATABASE_URL"
)

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        ensure_deployment(conn)
    eng.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    from customer_console.main import app

    return TestClient(app)


@pytest.fixture
def org(client):
    slug = f"rng-{uuid.uuid4().hex[:8]}"
    client.post(
        "/orgs/provision",
        headers=OP,
        json={
            "slug": slug,
            "name": "R",
            "owner_email": f"o@{slug}.com",
            "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
        },
    )
    return slug


def _call(slug: str, at: datetime, credits: str, cost: str) -> None:
    eng = create_engine(_URL, future=True)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO usage_event (organization_id, request_id, billed_credits, "
                "provider_cost_usd, created_at) "
                "SELECT id, :r, :b, :c, :at FROM organization WHERE slug = :s"
            ),
            {"r": f"rng-{uuid.uuid4().hex}", "b": credits, "c": cost, "at": at, "s": slug},
        )
    eng.dispose()


def _row(client, slug: str, **params):
    r = client.get("/admin/usage/orgs", headers=OP, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    mine = [x for x in body["rows"] if x["slug"] == slug]
    return body, (mine[0] if mine else None)


# A fixed month in the past, so "now" never matters to the edges.
D1 = datetime(2026, 8, 1, tzinfo=IST).date()
D31 = datetime(2026, 8, 31, tzinfo=IST).date()


def _seed(slug: str) -> None:
    _call(slug, datetime(2026, 7, 31, 23, 50, tzinfo=IST), "1", "0.01")  # before
    _call(slug, datetime(2026, 8, 1, 0, 10, tzinfo=IST), "10", "0.10")  # first minutes
    _call(slug, datetime(2026, 8, 31, 23, 30, tzinfo=IST), "100", "1.00")  # last hour
    _call(slug, datetime(2026, 9, 1, 0, 10, tzinfo=IST), "1000", "10.0")  # after


class TestTheEdges:
    def test_a_range_counts_India_days_inclusive(self, client, org):
        _seed(org)
        body, row = _row(client, org, **{"from": D1.isoformat(), "to": D31.isoformat()})
        assert row is not None
        assert Decimal(row["credits"]) == Decimal("110")
        assert Decimal(row["costUsd"]) == Decimal("1.10")
        assert body["windowDays"] == 31
        assert body["rangeFrom"] == "2026-08-01"
        assert body["rangeTo"] == "2026-08-31"

    def test_a_one_day_range(self, client, org):
        _seed(org)
        _, row = _row(client, org, **{"from": "2026-08-31", "to": "2026-08-31"})
        assert Decimal(row["credits"]) == Decimal("100")

    def test_the_daily_series_spans_exactly_the_range(self, client, org):
        _seed(org)
        r = client.get(
            "/admin/usage/daily",
            headers=OP,
            params={"org_slug": org, "from": "2026-08-01", "to": "2026-08-31"},
        )
        assert r.status_code == 200, r.text
        days = r.json()["days"]
        assert len(days) == 31
        assert days[0]["day"] == "2026-08-01"
        assert days[-1]["day"] == "2026-08-31"
        # Bucketed in India: the 23:30 call is on the 31st, not the 1st of Sept.
        assert Decimal(days[-1]["credits"]) == Decimal("100")
        assert Decimal(days[0]["credits"]) == Decimal("10")

    def test_the_breakdown_takes_the_same_range(self, client, org):
        _seed(org)
        r = client.get(
            "/admin/usage/breakdown",
            headers=OP,
            params={"org_slug": org, "from": "2026-08-01", "to": "2026-08-31"},
        )
        assert r.status_code == 200, r.text
        total = sum(Decimal(a["credits"]) for a in r.json()["apps"])
        assert total == Decimal("110")


class TestTheDefaultIsUnchanged:
    def test_no_range_reads_the_last_days_as_before(self, client, org):
        # Large, so the org sorts onto the capped page (H-76).
        _call(org, datetime.now(IST) - timedelta(days=2), "90000000", "0.05")
        _call(org, datetime.now(IST) - timedelta(days=40), "50", "0.50")
        body, row = _row(client, org, days=30)
        assert row is not None
        assert Decimal(row["credits"]) == Decimal("90000000")
        assert body["rangeFrom"] is None and body["rangeTo"] is None


class TestRefusals:
    @pytest.mark.parametrize(
        "params",
        [
            {"from": "2026-08-31", "to": "2026-08-01"},  # backwards
            {"to": "2026-08-31"},  # an end with no start
            {"from": "2024-01-01", "to": "2026-08-31"},  # over a year
        ],
    )
    def test_an_unanswerable_range_is_a_422(self, client, params):
        r = client.get("/admin/usage/orgs", headers=OP, params=params)
        assert r.status_code == 422, r.text
