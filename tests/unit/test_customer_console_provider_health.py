"""Provider health on a REAL Postgres: migration 035, the route, the refusal.

Owner request, 2026-09-28. DeepSeek held -0.05 USD for two days, refused every
call with 402, the Router answered 502, and nobody was told.

⚠️ **R8.** Every assertion is against rows a real Postgres holds. The hermetic
half (the probes, the rule, the hook) is ``test_provider_balance.py``.

⚠️ **The probes are stubbed and no network is reached.** ``PROBES`` is
replaced for each test with ONE fake vendor, so the other platform credentials
earlier suites left in this shared database are ``not_exposed`` and nothing
is sent anywhere.
"""

from __future__ import annotations

import logging
import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from customer_console import provider_balance as pb
from customer_console import router as router_mod
from customer_console import store
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder, ensure_deployment
from tests.unit.test_customer_console_end_to_end import (
    ENC_KEY,
    OP,
    TOKEN,
    _ask,
    _stage_one_priced_tier,
    _VendorDown,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

SECRET = "sk-provider-health-secret-0123456789"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        ensure_deployment(conn)
    eng.dispose()


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_ENCRYPTION_KEY", ENC_KEY)
    pb.drain_refusals()
    from customer_console.main import app

    yield TestClient(app)
    pb.drain_refusals()


@pytest.fixture
def vendor_down():
    async def _fail(**kwargs):
        raise _VendorDown("insufficient balance")

    router_mod.set_provider_call(_fail)
    yield
    router_mod.set_provider_call(None)


def _row(client, provider: str) -> dict:
    r = client.get("/providers/health", headers=OP)
    assert r.status_code == 200, r.text
    rows = [p for p in r.json()["providers"] if p["provider"] == provider]
    assert len(rows) == 1, r.json()
    return rows[0]


def _install(client, provider: str, *, org_slug: str | None = None) -> None:
    body = {"provider": provider, "secret": SECRET, "label": "health"}
    if org_slug:
        body["org_slug"] = org_slug
    r = client.post("/providers/credentials", headers=OP, json=body)
    assert r.status_code in (200, 201), r.text


def _fake_probe(result: pb.ProbeResult, seen: list[str]):
    def fn(secret, http_client):
        seen.append(secret)
        return result

    return fn


# ── The migration ───────────────────────────────────────────────────────────


def test_migration_035_replays_and_both_tables_exist(db):
    with db.begin() as conn:
        apply_ladder(conn)  # a second replay, as every deploy does
        tables = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('provider_health', 'provider_refusal')"
                )
            ).scalars()
        )
        cols = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'provider_health'"
                )
            ).scalars()
        )
    assert tables == {"provider_health", "provider_refusal"}
    assert {"funds_left", "currency", "available", "funds_checked_at", "probe_status",
            "probe_error", "probe_http", "low_threshold", "alert_state"} <= cols
    # ⚠️ No column that could hold a key.
    assert not {c for c in cols if "secret" in c or "key" in c}


def test_the_probe_status_check_refuses_a_fourth_spelling(db):
    with (
        pytest.raises(Exception, match="provider_health_probe_status_known"),
        db.begin() as conn,
    ):
        conn.execute(
            text("INSERT INTO provider_health (provider, probe_status) VALUES (:p, 'fine')"),
            {"p": f"x{uuid.uuid4().hex[:8]}"},
        )


def test_a_failed_probe_keeps_the_last_good_balance(db):
    p = f"keep{uuid.uuid4().hex[:8]}"
    with db.begin() as conn:
        store.provider_health_save_probe(conn, provider=p, status="ok", balance=Decimal("12.5"),
                                         currency="USD", available=True, error=None,
                                         http_status=None)
    with db.begin() as conn:
        first = store.provider_health_rows(conn)[p]
        store.provider_health_save_probe(conn, provider=p, status="failed", balance=None,
                                         currency=None, available=None, error="timeout",
                                         http_status=None)
    with db.begin() as conn:
        after = store.provider_health_rows(conn)[p]
    assert after["balance"] == Decimal("12.5") and after["currency"] == "USD"
    assert after["balance_checked_at"] == first["balance_checked_at"]
    assert (after["probe_status"], after["probe_error"]) == ("failed", "timeout")


# ── The refusal, through the REAL route ─────────────────────────────────────


def test_a_402_from_OUR_account_turns_the_provider_OUT(client, db, vendor_down, caplog):
    """🔴 The outage, replayed. A customer call meets a vendor 402, the Router
    answers 502 as before, and the provider now reads `out`."""
    run = uuid.uuid4().hex[:8]
    tier, slug, _org, key = _stage_one_priced_tier(client, db, run)
    client.post("/credits/grant", headers=OP, json={
        "org_slug": slug, "credits": "40", "reason": "purchase", "ref": f"b-{run}"})
    vendor = f"e2ez{run}"

    # Before any call the vendor is merely invisible, and NOT green.
    assert _row(client, vendor)["status"] == "unknown"

    caplog.set_level(logging.INFO)
    r = _ask(client, key, tier)
    assert r.status_code == 502, r.text  # the customer-facing answer is unchanged

    row = _row(client, vendor)
    assert row["status"] == "out", row
    assert row["last_refusal_status"] == 402
    assert row["refusals_24h"] == 1
    assert row["balance_exposed"] is False
    with db.begin() as conn:
        n = conn.execute(
            text("SELECT SUM(refusals) FROM provider_refusal WHERE provider = :p"),
            {"p": vendor},
        ).scalar_one()
    assert n == 1

    # 🔴 Logged ONCE per transition: a second read, and a second refusal, add
    # no second `provider.refusing` line.
    assert _ask(client, key, tier).status_code == 502
    _row(client, vendor)
    lines = [m for m in caplog.messages if m.startswith("provider.refusing") and vendor in m]
    assert len(lines) == 1, lines
    assert _row(client, vendor)["refusals_24h"] == 2
    assert SECRET not in caplog.text


def test_a_402_on_a_customers_OWN_key_is_not_our_alert(client, db, vendor_down):
    """BYOK: the customer's account ran dry, not ours."""
    run = uuid.uuid4().hex[:8]
    tier, slug, _org, key = _stage_one_priced_tier(client, db, run)
    vendor = f"e2ez{run}"
    _install(client, vendor, org_slug=slug)  # the org's own key now wins
    client.post("/credits/grant", headers=OP, json={
        "org_slug": slug, "credits": "40", "reason": "purchase", "ref": f"b-{run}"})

    assert _ask(client, key, tier).status_code == 502
    row = _row(client, vendor)
    assert row["refusals_24h"] == 0 and row["status"] == "unknown", row


# ── The probe, the route and the check ──────────────────────────────────────


def test_check_now_probes_stores_and_judges_low(client, monkeypatch):
    vendor = f"hv{uuid.uuid4().hex[:8]}"
    _install(client, vendor)
    seen: list[str] = []
    monkeypatch.setattr(pb, "PROBES", {vendor: pb.Probe(
        _fake_probe(pb.ProbeResult(status="ok", balance=Decimal("3.20"), currency="USD",
                                   available=True), seen), "vendor.example")})

    r = client.post("/providers/health/check", headers=OP)
    assert r.status_code == 200, r.text
    assert seen == [SECRET], "the probe did not receive the decrypted platform key"
    assert SECRET not in r.text
    row = next(p for p in r.json()["providers"] if p["provider"] == vendor)
    assert row["status"] == "low", row
    # Money is a string, like the rest of this API.
    assert (row["balance"], row["currency"], row["threshold"]) == ("3.2", "USD", "5")
    assert row["balance_checked_at"] is not None
    assert row["balance_exposed"] is True


def test_a_per_provider_threshold_overrides_the_default(client, db, monkeypatch):
    vendor = f"hv{uuid.uuid4().hex[:8]}"
    _install(client, vendor)
    monkeypatch.setattr(pb, "PROBES", {vendor: pb.Probe(
        _fake_probe(pb.ProbeResult(status="ok", balance=Decimal("30"), currency="USD",
                                   available=True), []), "vendor.example")})
    client.post("/providers/health/check", headers=OP)
    assert _row(client, vendor)["status"] == "ok"
    with db.begin() as conn:
        conn.execute(text("UPDATE provider_health SET low_threshold = 50 WHERE provider = :p"),
                     {"p": vendor})
    row = _row(client, vendor)
    assert (row["status"], row["threshold"]) == ("low", "50")


def test_a_revoked_or_byok_credential_is_not_listed(client, db):
    run = uuid.uuid4().hex[:8]
    vendor = f"hv{run}"
    _install(client, vendor)
    client.post("/providers/credentials/revoke", headers=OP, json={"provider": vendor})
    names = {p["provider"] for p in client.get("/providers/health", headers=OP).json()["providers"]}
    assert vendor not in names


def test_both_routes_refuse_without_an_operator(client):
    assert client.get("/providers/health").status_code in (401, 403)
    assert client.post("/providers/health/check").status_code in (401, 403)


def test_the_vendor_available_flag_false_reads_out(client, monkeypatch):
    """The exact production shape: `is_available: false`, balance -0.05."""
    vendor = f"hv{uuid.uuid4().hex[:8]}"
    _install(client, vendor)
    monkeypatch.setattr(pb, "PROBES", {vendor: pb.Probe(
        lambda secret, c: pb.parse_deepseek({"is_available": False, "balance_infos": [
            {"currency": "USD", "total_balance": "-0.05"}]}), "vendor.example")})
    client.post("/providers/health/check", headers=OP)
    row = _row(client, vendor)
    assert (row["status"], row["balance"], row["available"]) == ("out", "-0.05", False)
