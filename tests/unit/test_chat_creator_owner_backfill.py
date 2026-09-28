"""Migration 221 under the tenancy shape: real FORCE RLS, a non-privileged role.

Spec: ``project-docs/specs/projects_ai_chat.md`` §20.3 rule 14 (WS-27bm S14,
round 4). Round 3 made the creator of a session an owner only through a
participant row, unless the session has no row at all. Migration 221 gives
every creator that row, so the deploy locks nobody out of a chat they own on
main today.

The ladder-shape tests (no ``organization_id``, no RLS) live in
``test_rooms.py``, beside the room helpers. This file proves the other shape:
the four ``generated/`` phases applied, and the file run as a role that is NOT
a superuser, NOT the owner and NOT BYPASSRLS. So a missing tenant bind in 221
reads no session and inserts no row, and this test goes red.

It reuses the H3 rehearsal's ``promoted`` and ``app_engine`` fixtures
verbatim, as ``test_h6_rbac_rekey.py`` does, so it builds ONE dedicated
database and never touches the shared ladder DB.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the test
# parameters as re-defining them (F811). Both are suppressed where reported.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    app_engine,
    promoted,
)

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra" / "postgres" / "221_chat_session_creator_owner_backfill.sql"
)

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)


def _run_migration(engine) -> None:
    """Run the file verbatim through the DBAPI cursor, in one transaction."""
    with engine.begin() as conn:
        with conn.connection.dbapi_connection.cursor() as cur:
            cur.execute(_MIGRATION.read_text(encoding="utf-8"))


def _seed(admin_engine, org: str, creator: str, guest: str) -> str:
    """A session in ``org`` that holds a row for the guest and none for the
    creator: a chat shared before its first fold."""
    sid = f"pytest-221-{uuid.uuid4().hex[:8]}"
    with admin_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, organization_id) "
            "VALUES (:i, :u, 'orchestrator', :o)"),
            {"i": sid, "u": creator, "o": org})
        conn.execute(text(
            "INSERT INTO chat_session_participant "
            "(session_id, subject, role, organization_id) "
            "VALUES (:i, :g, 'member', :o)"),
            {"i": sid, "g": guest, "o": org})
    return sid


def _rows(admin_engine, sid: str) -> dict:
    with admin_engine.connect() as conn:
        return {
            r.subject: (r.role, str(r.organization_id))
            for r in conn.execute(text(
                "SELECT subject, role, organization_id "
                "FROM chat_session_participant WHERE session_id = :i"),
                {"i": sid})
        }


@_DB_GATE
def test_221_binds_each_tenant_under_force_rls(
    promoted, app_engine,  # noqa: F811
) -> None:
    """Two orgs, one shared-before-the-fold chat in each. The file runs as the
    non-privileged role. Each creator gets an owner row, stamped with the
    organization of her own session. A second run changes nothing."""
    admin = promoted.admin_engine
    sid_a = _seed(admin, promoted.org_a, "alice@a.test", "bob@a.test")
    sid_b = _seed(admin, promoted.org_b, "carol@b.test", "dan@b.test")

    _run_migration(app_engine)

    assert _rows(admin, sid_a) == {
        "alice@a.test": ("owner", promoted.org_a),
        "bob@a.test": ("member", promoted.org_a),
    }
    assert _rows(admin, sid_b) == {
        "carol@b.test": ("owner", promoted.org_b),
        "dan@b.test": ("member", promoted.org_b),
    }

    before = (_rows(admin, sid_a), _rows(admin, sid_b))
    _run_migration(app_engine)
    assert (_rows(admin, sid_a), _rows(admin, sid_b)) == before
