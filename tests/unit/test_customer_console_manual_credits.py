"""Manual credits: the bank-transfer fence and the ledger the operator reads.

Owner ask, 2026-08-30: a customer pays by bank transfer, an operator
verifies it and credits them from the console. Two gaps stood between that
sentence and the truth, and this suite closes both against a real Postgres
(R8):

* **The same transfer could be credited twice, silently.** `add_credit` was
  a plain INSERT, so the reference typed twice was the same money granted
  twice — found in a dispute, months later. The grant route now refuses a
  duplicate (reason, ref) with the FIRST row as evidence.
* **There was no ledger to verify against.** The customer page showed one
  number. `GET /credits/ledger` now returns the rows — the same rows a
  customer would be shown in a dispute.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tests.unit._customer_console_ladder import (
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}


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
    slug = f"mc-{uuid.uuid4().hex[:8]}"
    client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": f"o@{slug}.com",
        "deployment_label": DEFAULT_DEPLOYMENT_LABEL})
    return slug


def _grant(client, slug, credits, reason="manual", ref=None):
    body = {"org_slug": slug, "credits": credits, "reason": reason}
    if ref is not None:
        body["ref"] = ref
    return client.post("/credits/grant", headers=OP, json=body)


def test_a_bank_transfer_reference_cannot_be_credited_twice(client, org):
    """🔴 The headline. The second write is refused WITH the first row."""
    utr = f"UTR-{uuid.uuid4().hex[:10]}"
    first = _grant(client, org, "500", ref=utr)
    assert first.status_code == 200, first.text

    second = _grant(client, org, "500", ref=utr)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert utr in detail
    assert "500" in detail  # the refusal carries its own evidence
    assert "adjustment" in detail  # and names the correct path

    # And the balance holds the FIRST grant only.
    bal = client.get(
        "/credits/balance", headers=OP,
        params={"org_slug": org}).json()["balance"]
    assert Decimal(bal) == Decimal("500")


def test_an_adjustment_citing_the_same_reference_passes(client, org):
    """Correcting a mistaken manual row cites the same ref, different
    reason — the fence is keyed on the PAIR on purpose."""
    utr = f"UTR-{uuid.uuid4().hex[:10]}"
    assert _grant(client, org, "500", ref=utr).status_code == 200
    fix = _grant(client, org, "-200", reason="adjustment", ref=utr)
    assert fix.status_code == 200, fix.text
    bal = client.get(
        "/credits/balance", headers=OP,
        params={"org_slug": org}).json()["balance"]
    assert Decimal(bal) == Decimal("300")


def test_grants_without_a_reference_never_collide(client, org):
    """A ref-less plan grant is not a payment. Two of them are two grants."""
    assert _grant(client, org, "100", reason="grant").status_code == 200
    assert _grant(client, org, "100", reason="grant").status_code == 200
    bal = client.get(
        "/credits/balance", headers=OP,
        params={"org_slug": org}).json()["balance"]
    assert Decimal(bal) == Decimal("200")


def test_a_whitespace_ref_is_no_ref(client, org):
    """'  ' must not become a dedupe key every blank grant collides on."""
    assert _grant(client, org, "50", ref="  ").status_code == 200
    assert _grant(client, org, "50", ref="").status_code == 200


def test_the_ledger_read_returns_the_rows_newest_first(client, org):
    utr = f"UTR-{uuid.uuid4().hex[:10]}"
    _grant(client, org, "500", ref=utr)
    _grant(client, org, "-200", reason="adjustment", ref=utr)

    out = client.get(
        "/credits/ledger", headers=OP, params={"org_slug": org})
    assert out.status_code == 200, out.text
    entries = out.json()["entries"]
    assert len(entries) == 2
    # Newest first, money as strings, the reference readable.
    assert entries[0]["reason"] == "adjustment"
    assert entries[0]["delta"] == "-200.0000"
    assert entries[1]["reason"] == "manual"
    assert entries[1]["delta"] == "500.0000"
    assert entries[0]["ref"] == utr
    assert entries[0]["created_at"] is not None


def test_the_ledger_read_is_operator_gated(client, org):
    assert client.get(
        "/credits/ledger", params={"org_slug": org}
    ).status_code in (401, 403)


def test_an_unknown_reason_is_still_refused(client, org):
    """The LEDGER_REASONS vocabulary holds — the fence did not widen it."""
    r = _grant(client, org, "10", reason="bank-transfer",
               ref=f"UTR-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 422
