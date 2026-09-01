"""WS-31 — a tier points at an ORDERED CHAIN, not one model.

Spec: ``project-docs/specs/ai_metering_and_analytics.md`` §8 · D-AI-5.
Migration: 011, ``tier_fallback_chain``. ⚠️ Named without its directory on
purpose — ``test_migration_prefixes.py`` scans this whole file for a ladder
path, docstrings included, and a suite that hardcodes one goes stale.

⚠️ **The subject is a chain that LOOKS saved and is not.** Storing an ordered
list in an insert-only table has three failure modes that a hermetic fake would
never show, because they are all properties of what Postgres actually does with
the rows:

  1. Two steps written with separate ``now()`` calls land on different
     microsecond timestamps. Resolution takes every row at the newest one, so
     the chain silently collapses to its last step — and nobody finds out until
     the primary fails.
  2. Resolution that takes "the newest row per rank" splices half of
     yesterday's chain onto half of today's. That is a configuration nobody
     chose and nobody can reproduce from the audit trail.
  3. A superseded chain that is longer than the new one leaves orphan steps
     behind, and they come back the moment the shorter chain is read the
     obvious way.

R8: every test below runs against a real Postgres.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from customer_console.router import TierUnknown, resolve_chain, resolve_tier

from tests.unit._customer_console_ladder import apply_ladder

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    return eng


@pytest.fixture
def conn(engine):
    """A transaction that is ROLLED BACK, so tests cannot see each other.

    ⚠️ A test that asserts on rows it did not create is testing the database's
    contents rather than the function. Every tier below is unique to its test
    and disappears at the end of it.
    """
    with engine.connect() as c:
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()


def _declare(conn, model: str, task: str = "chat") -> None:
    conn.execute(
        text(
            "INSERT INTO model_capability (model, task, invocation) "
            "VALUES (:m, :t, 'acompletion') ON CONFLICT DO NOTHING"
        ),
        {"m": model, "t": task},
    )


def _write_chain(
    conn, tier: str, models: list[str], task: str = "chat", ago: str = "0 hours"
):
    """Write a chain the way the route does — ONE timestamp for every step.

    ⚠️ **`ago` exists because `now()` is TRANSACTION-stable.** These tests run
    inside one rolled-back transaction, so every `now()` in them returns the
    same instant and `pg_sleep` does not move it. Two chains written "one after
    the other" would land on the same timestamp and merge — which is a property
    of the test, not of the route, and it cost two red tests to notice. Dating
    each chain explicitly into the past models two separate requests honestly.
    """
    for m in models:
        _declare(conn, m, task)
    eff = conn.execute(
        text("SELECT now() - CAST(:ago AS INTERVAL)"), {"ago": ago}
    ).scalar_one()
    for rank, m in enumerate(models, start=1):
        conn.execute(
            text(
                "INSERT INTO tier_binding (tier, task, model, rank, "
                "effective_from) VALUES (:tier, :task, :m, :r, :eff)"
            ),
            {"tier": tier, "task": task, "m": m, "r": rank, "eff": eff},
        )
    return eff


# ── The column and its guards ───────────────────────────────────────────────


def test_an_existing_binding_becomes_a_one_step_chain(conn):
    """R6: the column lands with a default, so old rows are already valid."""
    _declare(conn, "acme/one")
    conn.execute(
        text(
            "INSERT INTO tier_binding (tier, task, model) "
            "VALUES ('t-legacy', 'chat', 'acme/one')"
        )
    )
    assert [r.model for r in resolve_chain(conn, "t-legacy")] == ["acme/one"]


def test_rank_zero_is_refused_by_the_database(conn):
    """⚠️ "The first one" and "rank 1" must mean the same thing to a reader.

    An off-by-one here is invisible until an outage, so the check is in the
    schema rather than in whichever caller happens to be careful.
    """
    _declare(conn, "acme/one")
    with pytest.raises(Exception) as exc:
        conn.execute(
            text(
                "INSERT INTO tier_binding (tier, task, model, rank) "
                "VALUES ('t-zero', 'chat', 'acme/one', 0)"
            )
        )
    assert "rank_positive" in str(exc.value)


def test_two_steps_cannot_share_a_rank(conn):
    """The widened primary key is what makes a chain an ORDER, not a bag."""
    _declare(conn, "acme/one")
    _declare(conn, "acme/two")
    eff = conn.execute(text("SELECT now()")).scalar_one()
    conn.execute(
        text("INSERT INTO tier_binding (tier, task, model, rank, "
             "effective_from) VALUES ('t-dup', 'chat', 'acme/one', 1, :e)"),
        {"e": eff},
    )
    with pytest.raises(Exception) as exc:
        conn.execute(
            text("INSERT INTO tier_binding (tier, task, model, rank, "
                 "effective_from) VALUES ('t-dup', 'chat', 'acme/two', 1, :e)"),
            {"e": eff},
        )
    assert "tier_binding_pkey" in str(exc.value)


# ── Resolution ──────────────────────────────────────────────────────────────


def test_the_chain_comes_back_in_rank_order(conn):
    _write_chain(conn, "t-order", ["acme/one", "beta/two", "gamma/three"])
    assert [r.model for r in resolve_chain(conn, "t-order")] == [
        "acme/one", "beta/two", "gamma/three",
    ]


def test_rank_order_is_not_insert_order(conn):
    """Sorted by rank, never by the order the rows happened to be written."""
    for m in ("acme/one", "beta/two"):
        _declare(conn, m)
    eff = conn.execute(text("SELECT now()")).scalar_one()
    for rank, m in ((2, "beta/two"), (1, "acme/one")):
        conn.execute(
            text("INSERT INTO tier_binding (tier, task, model, rank, "
                 "effective_from) VALUES ('t-rev', 'chat', :m, :r, :e)"),
            {"m": m, "r": rank, "e": eff},
        )
    assert [r.model for r in resolve_chain(conn, "t-rev")] == [
        "acme/one", "beta/two",
    ]


def test_resolve_tier_returns_the_PRIMARY_of_a_chain(conn):
    """🔴 The compatibility guarantee, and the reason for the rank tiebreak.

    Every existing caller resolves one model. Without ``rank ASC`` this returns
    an arbitrary step of the chain — and only under multi-step chains, which is
    exactly the configuration the feature exists to create.
    """
    _write_chain(conn, "t-primary", ["acme/one", "beta/two", "gamma/three"])
    assert resolve_tier(conn, "t-primary").model == "acme/one"


def test_a_newer_chain_REPLACES_the_old_one_whole(conn):
    """🔴 The orphan-step failure.

    The old chain has three steps and the new one has two. A reader that took
    "the newest row per rank" would return the new step 1, the new step 2 and
    the OLD step 3 — a chain nobody wrote, that falls back to a model somebody
    deliberately removed.
    """
    _write_chain(conn, "t-shrink", ["acme/one", "beta/two", "gamma/three"],
                 ago="2 hours")
    _write_chain(conn, "t-shrink", ["delta/four", "acme/one"], ago="1 hour")
    assert [r.model for r in resolve_chain(conn, "t-shrink")] == [
        "delta/four", "acme/one",
    ]


def test_a_future_chain_is_staged_and_does_not_take_effect(conn):
    """Same shape as `seat_grant` and `model_rate_card`: date it forward."""
    _write_chain(conn, "t-future", ["acme/one"])
    _declare(conn, "beta/two")
    conn.execute(
        text("INSERT INTO tier_binding (tier, task, model, rank, "
             "effective_from) VALUES ('t-future', 'chat', 'beta/two', 1, "
             "now() + interval '1 day')")
    )
    assert [r.model for r in resolve_chain(conn, "t-future")] == ["acme/one"]


def test_an_unbound_tier_is_refused_rather_than_coerced(conn):
    """§6A.9 rule 2, and it is the same refusal `resolve_tier` makes.

    Falling back to the chat binding would serve a paragraph where somebody
    asked for a picture, and bill for it.
    """
    with pytest.raises(TierUnknown):
        resolve_chain(conn, "t-nothing-here")


def test_a_chain_is_per_TASK_and_the_tasks_do_not_mix(conn):
    """Resolution is (task, tier) -> chain. A tier name alone is not a key."""
    _write_chain(conn, "t-both", ["acme/one", "beta/two"], task="chat")
    _write_chain(conn, "t-both", ["voice/one"], task="transcribe")
    assert [r.model for r in resolve_chain(conn, "t-both", "chat")] == [
        "acme/one", "beta/two",
    ]
    assert [r.model for r in resolve_chain(conn, "t-both", "transcribe")] == [
        "voice/one",
    ]


def test_steps_written_with_separate_now_calls_do_NOT_form_a_chain(conn):
    """🔴 The bug this schema is most likely to be broken by, pinned.

    Two timestamps mean two chains, and resolution correctly returns only the
    newer one. The visible symptom is that the PRIMARY disappears and the tier
    silently serves what was meant to be its backup. The route therefore
    computes the timestamp ONCE and passes it to every INSERT, and this test is
    what says why that line matters.
    """
    _declare(conn, "acme/one")
    _declare(conn, "beta/two")
    for model, rank, ago in (("acme/one", 1, "2 hours"), ("beta/two", 2, "1 hour")):
        conn.execute(
            text("INSERT INTO tier_binding (tier, task, model, rank, "
                 "effective_from) VALUES ('t-split', 'chat', :m, :r, "
                 "now() - CAST(:ago AS INTERVAL))"),
            {"m": model, "r": rank, "ago": ago},
        )
    assert [r.model for r in resolve_chain(conn, "t-split")] == ["beta/two"]
