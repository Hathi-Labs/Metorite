"""A refused staff write leaves a row. Until now none of them did.

Spec: ``project-docs/specs/customer_console.md`` CP-12c (the audit trail).

🔴 **Measured on production, 2026-09-22.** `tier_binding` held ONE row for
`tier-fast/chat`, dated 2026-01-01 and written by a migration. `control_audit`
held no `catalog.binding` row at all — so not a single tier save had EVER
succeeded. The owner had been trying for days, reported it as *"when I delete
or change a particular model it does not change it"*, and nothing anywhere
recorded a reason.

`_audit` is called AFTER a write lands. So the trail recorded successes only,
and could not answer *"why did nothing happen"* — the one question anybody
asks it under pressure.

⚠️ **R8, and a fake would be worthless here.** The handler writes on its OWN
connection precisely because the request's transaction is rolling back. A fake
has no transactions, so it would agree with the broken version too.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_refusals_are_audited.py -v -rs
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (  # noqa: E402
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
    eng.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    from customer_console.main import app

    return TestClient(app)


@pytest.fixture
def db():
    eng = create_engine(_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _box():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        ensure_deployment(conn)
    eng.dispose()


# ⚠️ `control_audit.id` is a UUID, so "rows newer than the last id" is not a
# question it can answer. The mark is the SET of ids that already existed, and
# a new row is one outside it. Exact, and it needs no ordering column.
def _ids(db) -> set[str]:
    with db.begin() as conn:
        return {
            str(r[0]) for r in conn.execute(text("SELECT id FROM control_audit"))
        }


def _refusals(db, *, since: set[str]) -> list[dict]:
    with db.begin() as conn:
        return [
            dict(r._mapping)
            for r in conn.execute(
                text(
                    "SELECT id, actor, action, detail FROM control_audit "
                    "WHERE action = 'refused' ORDER BY created_at"
                )
            )
            if str(r._mapping["id"]) not in since
        ]


class TestARefusedWriteIsRecorded:
    def test_the_exact_refusal_the_owner_hit(self, client, db):
        """🔴 Binding a model that declares no capability.

        This is the 400 that stopped every tier save on production, and it
        left no trace of any kind.
        """
        mark = _ids(db)
        r = client.post(
            "/catalog/bindings",
            headers=OP,
            json={"tier": "tier-fast", "task": "chat", "models": ["nope/not-a-model"]},
        )
        assert r.status_code == 400, r.text

        rows = _refusals(db, since=mark)
        assert len(rows) == 1, (
            "a refused tier save left no audit row, so the console still "
            "cannot answer 'why did nothing happen'")
        d = rows[0]["detail"]
        assert d["status"] == 400
        assert d["path"] == "/catalog/bindings"
        assert d["method"] == "POST"
        assert "declares no capability" in d["why"]

    def test_the_answer_the_caller_gets_is_UNCHANGED(self, client):
        """⚠️ An audit trail that can alter a response is worse than none.

        The handler replaces FastAPI's own, so the body and status have to
        match what it produced before — a caller parsing `detail` must not
        start seeing a different shape.
        """
        r = client.post(
            "/catalog/bindings",
            headers=OP,
            json={"tier": "tier-fast", "task": "chat", "models": ["nope/x"]},
        )
        assert r.status_code == 400
        assert set(r.json()) == {"detail"}
        assert isinstance(r.json()["detail"], str)

    def test_a_401_writes_NOTHING(self, client, db):
        """⚠️ The caller never became staff, so this is not an operator act.

        Recording it would turn the operator trail into a scan log, and the
        rows this exists to surface would be buried inside a week.
        """
        mark = _ids(db)
        r = client.post(
            "/catalog/bindings",
            headers={"Authorization": "Bearer wrong"},
            json={"tier": "tier-fast", "task": "chat", "models": ["x/y"]},
        )
        assert r.status_code == 401
        assert _refusals(db, since=mark) == []

    def test_a_refused_GET_writes_nothing(self, client, db):
        """A read that refuses changed nothing, and a console full of them
        cannot be read."""
        mark = _ids(db)
        r = client.get("/billing/summary?org_slug=no-such-org-xyz", headers=OP)
        assert r.status_code >= 400
        assert _refusals(db, since=mark) == []

    def test_the_BODY_never_reaches_the_row(self, client, db):
        """🔴 `POST /keys` and `POST /providers/credentials` carry secrets, and
        a refusal is exactly when somebody retries with one in hand."""
        mark = _ids(db)
        secret = f"sk-{uuid.uuid4().hex}"
        client.post(
            "/providers/credentials",
            headers=OP,
            json={"provider": "", "api_key": secret},
        )
        for row in _refusals(db, since=mark):
            assert secret not in str(row["detail"]), (
                "the refusal trail recorded a request body, and that body "
                "held a credential")

    def test_the_actor_is_recorded_so_the_trail_names_WHO(self, client, db):
        mark = _ids(db)
        client.post(
            "/catalog/bindings",
            headers=OP,
            json={"tier": "tier-fast", "task": "chat", "models": ["nope/x"]},
        )
        rows = _refusals(db, since=mark)
        assert rows and rows[0]["actor"] == "breakglass", (
            "the shared token logs as breakglass everywhere else, and a "
            "second spelling here would split the trail")

    def test_a_SUCCESS_writes_no_refusal_row(self, client, db):
        """The handler runs on HTTPException only. A 200 must stay silent, or
        every act would appear twice."""
        mark = _ids(db)
        slug = f"ref-{uuid.uuid4().hex[:8]}"
        r = client.post(
            "/orgs/provision",
            headers=OP,
            json={
                "slug": slug,
                "name": "N",
                "owner_email": f"o@{slug}.example",
                "core_seats": 1,
                "deployment_label": DEFAULT_DEPLOYMENT_LABEL,
            },
        )
        assert r.status_code == 200, r.text
        assert _refusals(db, since=mark) == []
