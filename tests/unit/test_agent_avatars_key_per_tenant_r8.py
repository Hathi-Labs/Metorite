"""H-201d — the ``agent_avatars`` key is per tenant (task 1719, migrations 354 and 474).

Migration 64 made ``agent_name`` alone the primary key of ``agent_avatars``.
Under FORCE RLS, org B could not save an agent name that org A held. The fix
is an expand/contract pair (R6), because the deploy applies migrations before
it restarts the services:

* **Expand (migration 354).** It added the unique index
  ``uq_agent_avatars_org_agent`` on ``(organization_id, agent_name)`` and
  kept the old primary key. ``_save_avatar`` names the new index as its
  conflict target.
* **Contract (migration 474, this release, HANDOFF H-443).** It drops
  ``agent_avatars_pkey``. Then each org keeps its own avatar for one name.
  The merger ships it only after 354 is in the production ledger from an
  earlier release.

This suite proves the contract state on a REAL, phase-4-promoted catalog, as
the NOSUPERUSER NOBYPASSRLS app role, through the real route functions and
``acb_graph.tenant_session``. The superuser admin engine stands in for the
owner role: production's owner bypasses RLS too (see migration 230), so it
sees each org's row.

Mutations this suite catches (R7):

* the DROP removed from migration 474: the old key is still there, and the
  cross-org save fails on ``agent_avatars_pkey``;
* the CREATE UNIQUE INDEX removed from migration 354: the same-org save is
  500, because no unique rule matches the conflict target;
* ``_save_avatar`` back to ``ON CONFLICT (agent_name)``: the cross-org save
  fails, because no unique rule on ``agent_name`` remains.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<your_db> \\
        uv run pytest tests/unit/test_agent_avatars_key_per_tenant_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    _CAROL,
    _admin_one,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_INDEX = "uq_agent_avatars_org_agent"
_OLD_KEY = "agent_avatars_pkey"


def _name() -> str:
    return f"h201d-av-{uuid.uuid4().hex[:8]}"


def _seed(promoted, org: str, name: str, colour: str) -> None:  # noqa: F811
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO agent_avatars (agent_name, config, organization_id) "
            "VALUES (:n, CAST(:c AS jsonb), CAST(:o AS uuid))"),
            {"n": name, "c": json.dumps({"colour": colour}), "o": org})


def _rows(promoted, name: str) -> dict[str, dict]:  # noqa: F811
    """Every row for *name*, as the owner (admin) sees it: ``{org: config}``."""
    with promoted.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text AS org, config FROM agent_avatars "
            "WHERE agent_name = :n"), {"n": name}).all()
    return {r.org: r.config for r in rows}


def _forget(promoted, name: str) -> None:  # noqa: F811
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM agent_avatars WHERE agent_name = :n"),
                  {"n": name})


def _index_columns(promoted, index: str) -> tuple[bool, list[str]] | None:  # noqa: F811
    row = _admin_one(promoted, """
        SELECT i.indisunique AS uniq,
               array_agg(a.attname ORDER BY k.ord) AS cols
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
         WHERE c.relname = :i
         GROUP BY i.indisunique""", i=index)
    return None if row is None else (row.uniq, list(row.cols))


# ── Contract: the per-tenant key alone ──────────────────────────────────────

def test_the_contract_schema_holds_only_the_per_tenant_key(graph_as_app):  # noqa: F811
    """Migration 474 drops the old primary key and keeps the per-tenant
    index. No unique or primary key of the table is on ``agent_name`` alone."""
    assert _index_columns(graph_as_app, _INDEX) == (
        True, ["organization_id", "agent_name"])
    assert _index_columns(graph_as_app, _OLD_KEY) is None
    row = _admin_one(graph_as_app, """
        SELECT count(*) AS n
          FROM pg_index i
          JOIN pg_attribute a ON a.attrelid = i.indrelid
                             AND a.attnum = ANY (i.indkey)
         WHERE i.indrelid = 'agent_avatars'::regclass
           AND i.indisunique AND a.attname = 'agent_name'
           AND i.indnatts = 1""")
    assert row.n == 0, "a key on agent_name alone is back"


def test_a_same_org_save_updates_through_the_new_key(graph_as_app):  # noqa: F811
    """The second save of one name in one org updates the row. Without the
    index, the conflict target matches no unique rule and the save is 500."""
    from gateway.routes.observability import AvatarPut, avatars, put_avatar

    a = graph_as_app.org_a
    alice = _user(_ALICE, a)
    name = _name()
    try:
        asyncio.run(put_avatar(name, AvatarPut(config={"colour": "red"}), writer=alice))
        asyncio.run(put_avatar(name, AvatarPut(config={"colour": "teal"}), writer=alice))

        assert _rows(graph_as_app, name) == {a: {"colour": "teal"}}
        assert asyncio.run(avatars(viewer=alice))["avatars"][name]["config"] == {
            "colour": "teal"}
    finally:
        _forget(graph_as_app, name)


def test_each_org_keeps_its_own_avatar_for_one_name(graph_as_app):  # noqa: F811
    """After the contract, org B saves a name that org A holds, and the two
    rows stay apart under RLS. Before it, the insert failed on the old key."""
    from gateway.routes.observability import (
        AvatarPut,
        avatars,
        delete_avatar,
        put_avatar,
    )

    db = graph_as_app
    a, b = db.org_a, db.org_b
    alice, carol = _user(_ALICE, a), _user(_CAROL, b)
    name = _name()
    try:
        # Each org sets one name, and a second save of each org updates its row.
        asyncio.run(put_avatar(name, AvatarPut(config={"colour": "red"}), writer=alice))
        asyncio.run(put_avatar(name, AvatarPut(config={"colour": "blue"}), writer=carol))
        asyncio.run(put_avatar(name, AvatarPut(config={"colour": "green"}), writer=carol))

        # Under RLS, as the app role, each org reads its own row only.
        assert asyncio.run(avatars(viewer=alice))["avatars"][name]["config"] == {
            "colour": "red"}
        assert asyncio.run(avatars(viewer=carol))["avatars"][name]["config"] == {
            "colour": "green"}

        # The owner sees one row for each org, each with its own look.
        assert _rows(db, name) == {a: {"colour": "red"}, b: {"colour": "green"}}

        # A delete in org B leaves org A's row.
        asyncio.run(delete_avatar(name, writer=carol))
        assert _rows(db, name) == {a: {"colour": "red"}}
        assert name not in asyncio.run(avatars(viewer=carol))["avatars"]
    finally:
        _forget(db, name)


def test_a_direct_cross_org_save_meets_no_key(graph_as_app):  # noqa: F811
    """The write path under the app role: org B's own ``_save_avatar`` of a name
    that org A holds inserts, with no IntegrityError, and org A's row stays.
    Red when the DROP is gone from migration 474."""
    from gateway.routes.observability import _save_avatar

    db = graph_as_app
    a, b = db.org_a, db.org_b
    name = _name()
    _seed(db, a, name, "red")
    try:
        _save_avatar(name, {"colour": "blue"}, None, b)
        assert _rows(db, name) == {a: {"colour": "red"}, b: {"colour": "blue"}}
    finally:
        _forget(db, name)
