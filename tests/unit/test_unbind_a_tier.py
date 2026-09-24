"""An operator can take a job OFF the air. H-178.

Spec: ``customer_console.md`` §6A.5 (insert-only) · §6A.9 rule 2 (an unbound
task is a 400, never a coercion).

🔴 **There was no way to unbind a job, and that made a broken tier
permanent.** Owner report, 2026-09-24: `tier-stt` pointed at
`groq/whisper-large-v3-turbo` on a box whose only credential is DeepSeek, so
every transcription failed at the provider and billed zero on the way. The
advice was *"unbind it"* and the console could not — `POST /catalog/bindings`
refuses an empty chain, and the board's Save greys out saying the job has no
model left.

⚠️ **Deleting the rows is NOT the fix.** §6A.5 makes this table insert-only
*"because a past invoice was computed against"* it. So an unbound job is an
APPEND: one row at a new `effective_from` whose `model` is NULL.

⚠️ **R8, and hermetically this proves nothing.** Every subject is SQL: a NULL
in a NOT NULL column, a `max(effective_from)` window, an `IS NOT NULL` filter,
a `FOR UPDATE`. A fake agrees with whatever it is handed.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_unbind_a_tier.py -v -rs
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import apply_ladder  # noqa: E402

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


@pytest.fixture
def bound(client, db):
    """A tier bound to a model that really declares the capability."""
    tier = f"t-{uuid.uuid4().hex[:8]}"
    model = f"vendor/m-{uuid.uuid4().hex[:8]}"
    with db.begin() as c:
        c.execute(
            text(
                "INSERT INTO tier_catalog (slug, label, blurb, sort_order, task, "
                "customer_visible) VALUES (:s, :s, '', 900, 'chat', false)"
            ),
            {"s": tier},
        )
        c.execute(
            text(
                "INSERT INTO model_capability (model, task, invocation) "
                "VALUES (:m, 'chat', 'acompletion')"
            ),
            {"m": model},
        )
    r = client.post(
        "/catalog/bindings",
        headers=OP,
        json={"tier": tier, "task": "chat", "models": [model]},
    )
    assert r.status_code == 200, r.text
    return tier, model


def _chain(db, tier: str) -> list[str]:
    """What the CATALOG reports, which is what the board draws."""
    with db.begin() as c:
        return [
            r[0]
            for r in c.execute(
                text(
                    "SELECT b.model FROM tier_binding b "
                    "WHERE b.tier = :t AND b.model IS NOT NULL "
                    "  AND b.effective_from = ("
                    "    SELECT max(x.effective_from) FROM tier_binding x "
                    "    WHERE x.tier = b.tier AND x.task = b.task "
                    "      AND x.effective_from <= now()) "
                    "ORDER BY b.rank"
                ),
                {"t": tier},
            )
        ]


class TestUnbindingTakesTheJobOffTheAir:
    def test_the_job_serves_NOTHING_afterwards(self, client, db, bound):
        """🔴 The whole ticket. `tier-stt` could not reach this state."""
        tier, model = bound
        assert _chain(db, tier) == [model]

        r = client.request(
            "DELETE", "/catalog/bindings",
            headers=OP, json={"tier": tier, "task": "chat"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["already"] is False
        assert _chain(db, tier) == []

    def test_the_ROUTER_refuses_an_unbound_job_rather_than_serving_NULL(
        self, client, db, bound
    ):
        """🔴 The failure this exists to escape, reached from the other side.

        Without the `model IS NOT NULL` filter the Router resolves the
        tombstone as a model named NULL and fails at the provider — which is
        exactly the shape `tier-stt` was already in.
        """
        from customer_console import router as rt

        tier, _ = bound
        client.request(
            "DELETE", "/catalog/bindings",
            headers=OP, json={"tier": tier, "task": "chat"},
        )
        with db.begin() as c:
            with pytest.raises(rt.TierUnknown):
                rt.resolve_tier(c, tier, "chat")

    def test_HISTORY_SURVIVES_because_this_appends_and_never_deletes(
        self, client, db, bound
    ):
        """⚠️ §6A.5: insert-only *"because a past invoice was computed
        against"* these rows. Deleting them would destroy the record of what
        served a call somebody has already paid for."""
        tier, model = bound
        client.request(
            "DELETE", "/catalog/bindings",
            headers=OP, json={"tier": tier, "task": "chat"},
        )
        with db.begin() as c:
            rows = c.execute(
                text(
                    "SELECT model FROM tier_binding WHERE tier = :t "
                    "ORDER BY effective_from"
                ),
                {"t": tier},
            ).all()
        assert [r[0] for r in rows] == [model, None], (
            "the original binding was destroyed rather than superseded")

    def test_it_is_IDEMPOTENT_and_says_which_it_did(self, client, db, bound):
        """A second tombstone would change no answer, and a reader comparing
        two of them could not tell which one mattered."""
        tier, _ = bound
        body = {"tier": tier, "task": "chat"}
        first = client.request("DELETE", "/catalog/bindings", headers=OP, json=body)
        second = client.request("DELETE", "/catalog/bindings", headers=OP, json=body)
        assert first.json()["already"] is False
        assert second.json()["already"] is True
        with db.begin() as c:
            n = c.execute(
                text("SELECT count(*) FROM tier_binding WHERE tier = :t"), {"t": tier}
            ).scalar_one()
        assert n == 2, "a second tombstone was appended"

    def test_REBINDING_after_an_unbind_works(self, client, db, bound):
        """⚠️ Unbinding must not be a one-way door. An operator who takes a
        tier off the air while they find a model has to be able to put one
        back."""
        tier, model = bound
        client.request(
            "DELETE", "/catalog/bindings",
            headers=OP, json={"tier": tier, "task": "chat"},
        )
        assert _chain(db, tier) == []
        r = client.post(
            "/catalog/bindings",
            headers=OP, json={"tier": tier, "task": "chat", "models": [model]},
        )
        assert r.status_code == 200, r.text
        assert _chain(db, tier) == [model]


class TestWhatItRefuses:
    def test_an_unknown_TASK_is_a_400(self, client, bound):
        """The same check `bind_tier` makes: a typo must not tombstone a job
        nobody has."""
        tier, _ = bound
        r = client.request(
            "DELETE", "/catalog/bindings",
            headers=OP, json={"tier": tier, "task": "not-a-task"},
        )
        assert r.status_code == 400
        assert "unknown task" in r.json()["detail"]

    def test_a_job_with_NO_binding_is_a_404_and_not_a_tombstone(self, client, db):
        """⚠️ Distinct from `already`. 404 means it was never bound;
        `already` means it WAS and is now off the air. Writing a tombstone for
        a job that never existed would invent history."""
        tier = f"t-{uuid.uuid4().hex[:8]}"
        with db.begin() as c:
            c.execute(
                text(
                    "INSERT INTO tier_catalog (slug, label, blurb, sort_order, "
                    "task, customer_visible) VALUES (:s, :s, '', 900, 'chat', false)"
                ),
                {"s": tier},
            )
        r = client.request(
            "DELETE", "/catalog/bindings",
            headers=OP, json={"tier": tier, "task": "chat"},
        )
        assert r.status_code == 404
        with db.begin() as c:
            n = c.execute(
                text("SELECT count(*) FROM tier_binding WHERE tier = :t"), {"t": tier}
            ).scalar_one()
        assert n == 0, "a 404 still wrote a row"


class TestEveryReaderFiltersTheTombstone:
    """🔴 A reader that forgets `model IS NOT NULL` hands the Router a model
    named NULL — the exact failure an operator unbinds a tier to ESCAPE.

    ⚠️ A text scan, deliberately. The alternative is trusting that whoever
    adds the ninth reader remembers, and this table now has a value that is
    legal in the column and wrong in every answer.
    """

    def test_every_SERVING_read_filters_the_tombstone(self):
        """🔴 The two reads that pick a model to CALL.

        Without the filter these hand the Router a model named NULL, which
        fails at the provider — the exact shape an operator unbinds a tier to
        escape.

        ⚠️ Checked per FUNCTION rather than by scanning around the match. A
        first version used a character window, and it flagged `resolve_tier`
        whose filter is real and simply sat further away once the comment
        explaining it was written. A fence that fails on prose gets deleted.
        """
        import inspect

        from customer_console import router as rt

        for fn in (rt.resolve_tier, rt.resolve_chain):
            src = inspect.getsource(fn)
            assert "FROM tier_binding" in src, f"{fn.__name__} stopped reading it"
            assert "model IS NOT NULL" in src, (
                f"{fn.__name__} can return a NULL model to the Router")

    def test_resolve_tier_picks_the_newest_SET_not_the_newest_ROW(self):
        """🔴 The bug this ticket nearly shipped.

        `resolve_tier` used `ORDER BY effective_from DESC, rank ASC LIMIT 1`,
        which takes the newest ROW that has a model. That is the same answer
        while every row has one, and the WRONG answer once a tombstone exists:
        it skips the tombstone and keeps serving the superseded binding, so
        unbinding would change nothing the Router does. Caught by the
        behavioural test above, not by reading.
        """
        import inspect

        from customer_console import router as rt

        src = inspect.getsource(rt.resolve_tier)
        assert "SELECT max(effective_from)" in src, (
            "resolve_tier no longer selects the newest SET, so a tombstone "
            "would be stepped over")
        # No assertion that the OLD ordering is absent. The comment above
        # the query quotes it to explain what was wrong, so a `not in`
        # clause fails on the PROSE rather than on the code, which is the
        # trap the docstring one test up describes. The behavioural test
        # is the real fence. This one only pins the shape that makes it
        # work.

    def test_the_CATALOG_read_filters_it(self):
        """The tiers board draws from this. A tombstone would render as a
        chain step with no model."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        src = (root / "apps/services/customer_console/customer_console/main.py").read_text(
            encoding="utf-8"
        )
        assert '"WHERE b.model IS NOT NULL "' in src

    def test_the_MIGRATION_backfill_filters_it_too(self):
        """🔴 Migration 010 copies `tier_binding.model` into
        `model_capability.model`, which is NOT NULL.

        Measured while building this: a ladder replay against a database
        holding one tombstone died with *"null value in column model of
        relation model_capability violates not-null constraint"*. The ladder
        must replay, and CI gates on exactly that.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        sql = (root / "infra/customer_console/010_tasks_units_capabilities.sql").read_text(
            encoding="utf-8"
        )
        backfills = sql.count("INSERT INTO model_capability (model, task, invocation")
        assert backfills == 2, f"the backfill changed shape ({backfills} found)"
        assert sql.count("tb.model IS NOT NULL") == 2, (
            "a backfill from tier_binding does not skip the tombstone")
