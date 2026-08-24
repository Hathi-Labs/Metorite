"""CP-2g's REGISTRY half — ``POST /orgs/purge`` on the Customer Console.

The doctrine under test: purge is reachable ONLY in ``deleted`` — the terminal
lifecycle state, itself reachable only from ``cancelled`` (the export window).
This suite drives orgs through the REAL lifecycle door to get there, then
asserts the purge strips exactly the personal-data tables, tombstone-renames
the slug (freeing it for reuse — the "start again from scratch" case), and
keeps the financial record.

**R8** — real Postgres via the Console ladder (``CUSTOMER_CONSOLE_DATABASE_URL``):
every guard here is a SQL WHERE clause or an FK, which a hermetic fake would
agree with unconditionally.

Run:
    uv run pytest tests/unit/test_org_purge_console.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console.main import _ORG_PURGE_DELETES, app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder, ensure_deployment

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]

TOKEN = "test-operator-token"
OP = {"Authorization": f"Bearer {TOKEN}"}
BOX_LABEL = "org-purge-suite-box"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _box(db):
    with db.begin() as c:
        ensure_deployment(c, label=BOX_LABEL)


def _new_org(client) -> dict:
    slug = f"cp2g-{uuid.uuid4().hex[:8]}"
    owner = f"owner@{slug}.example"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": owner,
        "core_seats": 2, "deployment_label": BOX_LABEL,
    })
    assert r.status_code == 200, r.text
    return {"slug": slug, "owner": owner, "id": r.json()["organization_id"]}


def _move(client, slug: str, target: str) -> None:
    r = client.post("/orgs/lifecycle", headers=OP,
                    json={"org_slug": slug, "target": target})
    assert r.status_code == 200, r.text


def _walk_to_deleted(client, slug: str) -> None:
    """trial → cancelled → deleted, through the REAL graph — the only path."""
    _move(client, slug, "cancelled")
    _move(client, slug, "deleted")


def _purge(client, slug: str, *, confirm: str | None = None):
    return client.post("/orgs/purge", headers=OP, json={
        "org_slug": slug, "confirm": slug if confirm is None else confirm,
    })


class TestTheGuards:
    @pytest.mark.parametrize("state", ["trial", "cancelled"])
    def test_purge_is_refused_outside_deleted_and_names_the_path(
        self, client, state
    ):
        org = _new_org(client)
        if state == "cancelled":
            _move(client, org["slug"], "cancelled")
        r = _purge(client, org["slug"])
        assert r.status_code == 409, r.text
        # The refusal teaches the path rather than just saying no.
        assert "cancel access" in r.json()["detail"]

    def test_confirm_must_echo_the_slug(self, client):
        org = _new_org(client)
        _walk_to_deleted(client, org["slug"])
        r = _purge(client, org["slug"], confirm="something-else")
        assert r.status_code == 400, r.text

    def test_an_unknown_slug_is_a_404(self, client):
        r = _purge(client, f"never-{uuid.uuid4().hex[:8]}")
        assert r.status_code == 404, r.text

    def test_the_operator_scheme_gates_the_door(self, client):
        r = client.post("/orgs/purge", json={"org_slug": "x", "confirm": "x"})
        assert r.status_code in (401, 403), r.text


class TestThePurge:
    def test_personal_data_goes_the_books_stay_and_the_slug_is_freed(
        self, client, db
    ):
        org = _new_org(client)
        _walk_to_deleted(client, org["slug"])

        r = _purge(client, org["slug"])
        assert r.status_code == 200, r.text
        receipt = r.json()

        # Personal data: gone, and the receipt's counts are the proof (the
        # provision created one owner membership + one core seat).
        assert receipt["deleted"]["org_membership"] >= 1
        assert receipt["deleted"]["seat_assignment"] >= 1
        assert set(receipt["deleted"]) == set(_ORG_PURGE_DELETES)

        with db.begin() as c:
            for table in _ORG_PURGE_DELETES:
                left = c.execute(
                    text(f"SELECT count(*) FROM {table} "
                         "WHERE organization_id = :i"),
                    {"i": org["id"]},
                ).scalar_one()
                assert left == 0, f"{table} still holds rows"
            # The books: the registry row survives as a tombstone, the
            # subscription record survives with it.
            row = c.execute(
                text("SELECT slug, status FROM organization WHERE id = :i"),
                {"i": org["id"]},
            ).one()
            assert row.status == "deleted"
            assert row.slug == receipt["tombstone"]
            assert row.slug.startswith(org["slug"] + "-purged-")
            subs = c.execute(
                text("SELECT count(*) FROM org_subscription "
                     "WHERE organization_id = :i"),
                {"i": org["id"]},
            ).scalar_one()
            assert subs >= 1
            # The audit trail records the act with both names.
            audit = c.execute(
                text("SELECT count(*) FROM control_audit "
                     "WHERE organization_id = :i AND action = 'org.purge'"),
                {"i": org["id"]},
            ).scalar_one()
            assert audit == 1

        # THE point of the tombstone: the slug is free — provisioning the
        # same name again succeeds and mints a NEW organization.
        r2 = client.post("/orgs/provision", headers=OP, json={
            "slug": org["slug"], "name": "N2",
            "owner_email": f"second-{org['owner']}",
            "core_seats": 1, "deployment_label": BOX_LABEL,
        })
        assert r2.status_code == 200, r2.text
        assert r2.json()["organization_id"] != org["id"]

    def test_a_second_purge_of_the_same_slug_is_a_404_not_a_second_kill(
        self, client
    ):
        """After the tombstone rename the original slug no longer resolves —
        a stale retry cannot hit a NEW org that later took the name... unless
        that new org is itself walked to `deleted` first, which is the
        legitimate case, not a hazard."""
        org = _new_org(client)
        _walk_to_deleted(client, org["slug"])
        assert _purge(client, org["slug"]).status_code == 200
        assert _purge(client, org["slug"]).status_code == 404
