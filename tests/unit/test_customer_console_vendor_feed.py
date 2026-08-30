"""The vendor feed (014): upstream facts land, drift shows, nothing bills.

Owner directive 2026-08-30: model facts and vendor prices flow from
upstream — the operator clicks, never transcribes an HTML pricing page.

🔴 **What this suite exists to prove.**

* The parser turns litellm's per-token floats into EXACT per-million
  Decimals — float-first maths would manufacture ``0.27999…`` and a phantom
  drift warning against every hand-typed profile.
* The mapping speaks this system's real vocabulary: every task the feed can
  emit exists in ``task_catalog``, every invocation in
  ``catalog.KNOWN_INVOCATIONS`` — checked against the live seed, so a rename
  there breaks HERE first.
* The catalog splits feed facts into ``rows`` (declared models — the drift
  surface) and ``available`` (connected vendors only — a model we hold no
  key for is a brochure, not an offer).
* Sync writes its own evidence (``feed_sync_log``) and the packaged litellm
  snapshot really is inside the installed package — the offline fallback is
  load-bearing, so a litellm rename must fail these tests, not production.

⚠️ Fixture idiom follows ``test_customer_console_pricing_truth.py``: real
Postgres (R8), per-test vendor slugs, seed rows never touched.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from customer_console import (
    catalog,
    feed,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    apply_ladder,
    ensure_deployment,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}


# ── Pure parsing — no database, no network ──────────────────────────────────

def test_the_packaged_snapshot_parses_and_speaks_our_grammar():
    """The offline fallback is real: bundled file present, rows plentiful,
    and the one model we serve today mapped into our words."""
    rows = feed.parse_feed(feed.packaged_feed())
    assert len(rows) > 500
    by_model = {r.model: r for r in rows}
    ds = by_model["deepseek/deepseek-chat"]
    assert ds.provider == "deepseek"
    assert ds.task == "chat"
    assert ds.invocation == "acompletion"
    assert ds.input_per_1m is not None and ds.input_per_1m > 0
    assert ds.output_per_1m is not None and ds.output_per_1m > 0
    assert ds.context_window is not None and ds.context_window > 10_000


def test_per_token_floats_become_exact_per_million():
    # 🔴 The whole reason for Decimal(str(v)): 2.8e-07 times 1e6 in float is
    # 0.27999999999999997, and that number would drift-warn against every
    # correctly typed $0.28 profile forever.
    rows = feed.parse_feed({
        "m": {"litellm_provider": "x", "mode": "chat",
              "input_cost_per_token": 2.8e-07,
              "output_cost_per_token": 4.2e-07,
              "cache_read_input_token_cost": 7e-08},
    })
    (r,) = rows
    assert r.input_per_1m == Decimal("0.280000")
    assert r.output_per_1m == Decimal("0.420000")
    assert r.cached_per_1m == Decimal("0.070000")


def test_bare_keys_gain_their_vendor_prefix():
    rows = feed.parse_feed({
        "gpt-x": {"litellm_provider": "openai", "mode": "chat"},
        "openai/gpt-y": {"litellm_provider": "openai", "mode": "chat"},
    })
    assert {r.model for r in rows} == {"openai/gpt-x", "openai/gpt-y"}


def test_sample_spec_and_ownerless_entries_are_skipped():
    rows = feed.parse_feed({
        "sample_spec": {"litellm_provider": "x", "mode": "chat"},
        "orphan": {"mode": "chat"},
        "modeless": {"litellm_provider": "x"},
        "notadict": 3,
        "keeper": {"litellm_provider": "x", "mode": "chat"},
    })
    assert [r.model for r in rows] == ["x/keeper"]


def test_max_tokens_backs_up_output_and_never_the_window():
    # ⚠️ `max_tokens` is litellm's LEGACY name for max OUTPUT. Read it as a
    # window and every older entry claims a context 30x too small.
    rows = feed.parse_feed({
        "old": {"litellm_provider": "x", "mode": "chat", "max_tokens": 4096},
    })
    (r,) = rows
    assert r.context_window is None
    assert r.max_output == 4096


def test_an_unmapped_mode_lands_without_a_task():
    rows = feed.parse_feed({
        "r": {"litellm_provider": "x", "mode": "rerank",
              "input_cost_per_token": 1e-07},
    })
    (r,) = rows
    assert r.mode == "rerank"
    assert r.task is None and r.invocation is None
    assert r.input_per_1m == Decimal("0.100000")


def test_garbage_prices_become_unknown_not_zero():
    rows = feed.parse_feed({
        "neg": {"litellm_provider": "x", "mode": "chat",
                "input_cost_per_token": -1},
        "txt": {"litellm_provider": "x", "mode": "chat",
                "input_cost_per_token": "cheap"},
    })
    by = {r.model: r for r in rows}
    assert by["x/neg"].input_per_1m is None
    assert by["x/txt"].input_per_1m is None


def test_every_invocation_the_feed_can_emit_is_one_we_know():
    for _task, invocation in feed.MODE_MAP.values():
        assert invocation in catalog.KNOWN_INVOCATIONS


def test_fetch_falls_back_to_the_packaged_snapshot():
    def _down():
        raise OSError("no route to host")

    raw, source = feed.fetch_feed(fetcher=_down)
    assert source == "packaged:litellm"
    assert len(raw) > 500


def test_the_autosync_flag_never_crashes_the_app(monkeypatch):
    from customer_console.main import _FEED_SYNC_HOURS_VAR, _feed_sync_hours

    monkeypatch.delenv(_FEED_SYNC_HOURS_VAR, raising=False)
    assert _feed_sync_hours() == 0.0
    monkeypatch.setenv(_FEED_SYNC_HOURS_VAR, "24")
    assert _feed_sync_hours() == 24.0
    monkeypatch.setenv(_FEED_SYNC_HOURS_VAR, "banana")
    assert _feed_sync_hours() == 0.0
    monkeypatch.setenv(_FEED_SYNC_HOURS_VAR, "-3")
    assert _feed_sync_hours() == 0.0


# ── Against the real database (R8) ──────────────────────────────────────────

pytestmark_db = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


def _row(vendor: str, name: str, **over) -> feed.FeedRow:
    base = feed.FeedRow(
        model=f"{vendor}/{name}", provider=vendor, mode="chat",
        task="chat", invocation="acompletion",
        context_window=131_072, max_output=8_192,
        input_per_1m=Decimal("0.280000"), output_per_1m=Decimal("0.420000"),
        cached_per_1m=Decimal("0.070000"),
        reads_images=False, thinks_first=False, deprecated_on=None,
    )
    return base._replace(**over)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _URL:
        yield
        return
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
        ensure_deployment(conn)
    eng.dispose()
    yield


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def vendor():
    """A vendor slug OWNED by this test — never the shared seed vendors."""
    return f"vf{uuid.uuid4().hex[:8]}"


@pytestmark_db
def test_sync_upserts_idempotently_and_writes_its_evidence(db, vendor):
    started = datetime.now(UTC)
    rows = [_row(vendor, "alpha"), _row(vendor, "beta")]
    with db.begin() as conn:
        feed.sync(conn, rows, "github", started)
        feed.sync(conn, [
            _row(vendor, "alpha", input_per_1m=Decimal("0.300000")),
            _row(vendor, "beta"),
        ], "packaged:litellm", started)

    with db.begin() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM vendor_price_feed WHERE provider = :v"),
            {"v": vendor}).scalar()
        assert n == 2  # upsert, not append
        price = conn.execute(text(
            "SELECT vendor_input_per_1m_usd FROM vendor_price_feed "
            "WHERE model = :m"), {"m": f"{vendor}/alpha"}).scalar()
        assert price == Decimal("0.300000")  # the second sync WON
        logs = conn.execute(text(
            "SELECT source, models_seen FROM feed_sync_log "
            "ORDER BY id DESC LIMIT 2")).fetchall()
        assert [tuple(r) for r in logs] == [
            ("packaged:litellm", 2), ("github", 2)]


@pytestmark_db
def test_the_entire_real_snapshot_lands_without_a_check_refusing(db):
    """Sync the WHOLE packaged feed — every real price, window and date
    litellm ships — so the table's CHECKs are proven against the world as it
    is, not three hand-built rows. This is the test that catches 'some
    vendor lists a zero-token window' the day litellm records one."""
    rows = feed.parse_feed(feed.packaged_feed())
    with db.begin() as conn:
        counts = feed.sync(conn, rows, "packaged:litellm", datetime.now(UTC))
        n = conn.execute(
            text("SELECT COUNT(*) FROM vendor_price_feed")).scalar()
    assert counts["rows_upserted"] == len(rows)
    assert n >= len(rows)


@pytestmark_db
def test_every_task_the_feed_can_emit_exists_in_task_catalog(db):
    # The DB half of the vocabulary fence: MODE_MAP's words against the
    # REAL seed, not a copy of it.
    with db.begin() as conn:
        seeded = {r[0] for r in conn.execute(
            text("SELECT slug FROM task_catalog"))}
    for task, _invocation in feed.MODE_MAP.values():
        assert task in seeded


@pytestmark_db
def test_the_endpoint_pulls_lands_and_reports(client, db, vendor, monkeypatch):
    raw = {
        "alpha": {"litellm_provider": vendor, "mode": "chat",
                  "input_cost_per_token": 2.8e-07,
                  "output_cost_per_token": 4.2e-07,
                  "max_input_tokens": 131072},
    }
    monkeypatch.setattr(feed, "fetch_feed", lambda fetcher=None: (raw, "github"))

    out = client.post("/catalog/feed/sync", headers=OP)
    assert out.status_code == 200, out.text
    assert out.json() == {
        "source": "github", "models_seen": 1, "rows_upserted": 1}

    with db.begin() as conn:
        got = conn.execute(text(
            "SELECT vendor_input_per_1m_usd, context_window "
            "FROM vendor_price_feed WHERE model = :m"),
            {"m": f"{vendor}/alpha"}).fetchone()
    assert got is not None
    assert got[0] == Decimal("0.280000")
    assert got[1] == 131072


@pytestmark_db
def test_the_endpoint_refuses_anonymous_callers(client):
    assert client.post("/catalog/feed/sync").status_code in (401, 403)


@pytestmark_db
def test_catalog_splits_declared_drift_from_connected_available(
        client, db, vendor):
    """The read model: declared → ``rows`` (drift surface), undeclared but
    connected → ``available``, keyless vendor → neither."""
    ghost = f"vg{uuid.uuid4().hex[:8]}"  # a vendor we hold NO key for
    started = datetime.now(UTC)
    with db.begin() as conn:
        # A live PLATFORM key for `vendor` only.
        conn.execute(text(
            "INSERT INTO provider_credential (provider, secret_enc, label) "
            "VALUES (:v, 'enc', 'platform')"), {"v": vendor})
        feed.sync(conn, [
            _row(vendor, "declared"),
            _row(vendor, "undeclared"),
            _row(ghost, "elsewhere"),
        ], "github", started)

    # Declare one of the two — through the real endpoint, not SQL.
    ok = client.post("/catalog/capabilities", headers=OP, json={
        "model": f"{vendor}/declared", "task": "chat",
        "invocation": "acompletion", "streams": True})
    assert ok.status_code == 200, ok.text

    got = client.get("/catalog/models", headers=OP).json()["feed"]
    assert got["synced_at"] is not None
    assert got["models"] >= 3
    row_models = {r["model"] for r in got["rows"]}
    avail_models = {r["model"] for r in got["available"]}
    assert f"{vendor}/declared" in row_models
    assert f"{vendor}/declared" not in avail_models
    assert f"{vendor}/undeclared" in avail_models
    assert f"{vendor}/undeclared" not in row_models
    # No key, no offer — the ghost vendor reaches neither list.
    assert f"{ghost}/elsewhere" not in row_models | avail_models
    # ⚠️ Money as strings on the wire, exact.
    undeclared = next(
        r for r in got["available"] if r["model"] == f"{vendor}/undeclared")
    assert undeclared["vendor_input_per_1m_usd"] == "0.280000"
    assert undeclared["task"] == "chat"
    assert undeclared["invocation"] == "acompletion"


def test_a_poisoned_entry_is_skipped_never_fatal():
    """json.loads admits bare Infinity/NaN; a Decimal NaN COMPARISON raises
    and int(inf) overflows — one poisoned entry among 3,000 used to 500
    the whole sync, packaged fallback included."""
    rows = feed.parse_feed({
        "deepseek/ok": {"litellm_provider": "deepseek", "mode": "chat",
                        "input_cost_per_token": 1e-7},
        "bad/inf": {"litellm_provider": "bad", "mode": "chat",
                    "input_cost_per_token": float("inf"),
                    "max_input_tokens": float("inf")},
        "bad/nan": {"litellm_provider": "bad", "mode": "chat",
                    "output_cost_per_token": float("nan")},
    })
    by = {r.model: r for r in rows}
    assert by["deepseek/ok"].input_per_1m is not None
    assert by["bad/inf"].input_per_1m is None
    assert by["bad/inf"].context_window is None
    assert by["bad/nan"].output_per_1m is None
