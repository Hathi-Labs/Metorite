"""H-201d — the ``agent_avatars`` key is per tenant (task 1719, migration 354).

Migration 64 made ``agent_name`` alone the primary key of ``agent_avatars``.
Under FORCE RLS, org B could not save an agent name that org A held. The fix
is an expand/contract pair (R6), because the deploy applies migrations before
it restarts the services:

* **Expand (migration 354, this release).** It adds the unique index
  ``uq_agent_avatars_org_agent`` on ``(organization_id, agent_name)`` and
  keeps the old primary key. ``_save_avatar`` names the new index as its
  conflict target. A same-org save updates the row. A save of a name that
  another org holds still fails, on ``agent_avatars_pkey``.
* **Contract (a later release, HANDOFF H-201d).** It drops
  ``agent_avatars_pkey``. Then each org keeps its own avatar for one name.

This suite proves both states on a REAL, phase-4-promoted catalog, as the
NOSUPERUSER NOBYPASSRLS app role, through the real route functions and
``acb_graph.tenant_session``. The contract test drops the old key in its own
dedicated database, and restores it at the end, so test order does not
matter. The superuser admin engine stands in for the owner role: production's
owner bypasses RLS too (see migration 230), so it sees each org's row.

Mutations this suite catches (R7):

* the CREATE UNIQUE INDEX removed from migration 354: the same-org save is
  500, because no unique rule matches the conflict target;
* ``_save_avatar`` back to ``ON CONFLICT (agent_name)``: the cross-org save
  after the contract drop fails, because no unique rule on ``agent_name``
  remains;
* the old primary key dropped in migration 354: the expand pin of the
  cross-org failure goes red.

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
from sqlalchemy.exc import IntegrityError

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


# ── Expand: both keys exist ─────────────────────────────────────────────────

def test_the_expand_schema_holds_both_keys(graph_as_app):  # noqa: F811
    """Migration 354 adds the per-tenant index and keeps the old key, so the
    old gateway's ``ON CONFLICT (agent_name)`` still works during a deploy."""
    assert _index_columns(graph_as_app, _INDEX) == (
        True, ["organization_id", "agent_name"])
    assert _index_columns(graph_as_app, _OLD_KEY) == (True, ["agent_name"])


def test_a_same_org_save_updates_through_the_new_key(graph_as_app):  # noqa: F811
    """The second save of one name in one org updates the row. Without the
    index, the conflict target matches no unique rule and the save is 500."""
    from gateway.routes.observability import AvatarPut, avatars, put_avatar

    a = graph_as_app.org_a
    alice = _user(_ALICE, a)
    name = _name()

    asyncio.run(put_avatar(name, AvatarPut(config={"colour": "red"}), writer=alice))
    asyncio.run(put_avatar(name, AvatarPut(config={"colour": "teal"}), writer=alice))

    assert _rows(graph_as_app, name) == {a: {"colour": "teal"}}
    assert asyncio.run(avatars(viewer=alice))["avatars"][name]["config"] == {
        "colour": "teal"}


def test_a_name_another_org_holds_still_fails_on_the_old_key(graph_as_app):  # noqa: F811
    """Pins the EXPAND behaviour on purpose. Org B's save does not match org
    A's row on the new index, so it inserts, and the old primary key refuses
    it. The contract release flips this case (HANDOFF H-201d)."""
    from fastapi import HTTPException
    from gateway.routes.observability import AvatarPut, _save_avatar, put_avatar

    a, b = graph_as_app.org_a, graph_as_app.org_b
    name = _name()
    _seed(graph_as_app, a, name, "red")

    with pytest.raises(IntegrityError) as err:
        _save_avatar(name, {"colour": "blue"}, None, b)
    assert err.value.orig.diag.constraint_name == _OLD_KEY

    with pytest.raises(HTTPException) as http:
        asyncio.run(put_avatar(name, AvatarPut(config={"colour": "blue"}),
                               writer=_user(_CAROL, b)))
    assert http.value.status_code == 500
    assert _rows(graph_as_app, name) == {a: {"colour": "red"}}


# ── Contract: the old key is gone ───────────────────────────────────────────

@pytest.fixture
def contracted(graph_as_app):  # noqa: F811
    """The contract state: ``agent_avatars_pkey`` dropped, as the later
    release does. Restored at the end, after the rows that share a name go."""
    names: list[str] = []
    with graph_as_app.admin_engine.begin() as c:
        c.execute(text(f"ALTER TABLE agent_avatars DROP CONSTRAINT {_OLD_KEY}"))
    try:
        yield graph_as_app, names
    finally:
        with graph_as_app.admin_engine.begin() as c:
            c.execute(text("DELETE FROM agent_avatars WHERE agent_name = ANY(:n)"),
                      {"n": names})
            c.execute(text(f"ALTER TABLE agent_avatars ADD CONSTRAINT {_OLD_KEY} "
                           "PRIMARY KEY (agent_name)"))


def test_after_the_contract_each_org_keeps_its_own_avatar(contracted):
    from gateway.routes.observability import (
        AvatarPut,
        avatars,
        delete_avatar,
        put_avatar,
    )

    db, names = contracted
    a, b = db.org_a, db.org_b
    alice, carol = _user(_ALICE, a), _user(_CAROL, b)
    name = _name()
    names.append(name)

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
