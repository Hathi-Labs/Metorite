"""Task 2221, slice 2 — no system tick or webhook row lands under the operator org.

Eight writers record an event with no ``organization_id`` and no tenant
source (``test_audit_tick_writers_org.py`` names them and says why). With
``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit._persist`` filed each row under the
OPERATOR org (Option-A). The fix sends each one through a local ``_audit``,
so ``refuse_tenantless`` skips it.

This file also holds the verifier F1 fence of slice 1: an HTTP vote with a
forged org header lands in the org of the voter.

Fences (R7):

* ``audit-tick-no-operator-org`` (R8, phase-4 two-org catalog, non-priv role
  ``acb_app_h3rls``): one case for each of the eight events. No row reaches
  the seam, and the table holds no row for the event in any org. RED when the
  guard of that one writer is dropped: the row then lands under the operator
  org.
* ``audit-vote-ignores-org-header`` (R8): ``POST /chat/feedback`` as a member
  of one org, with three headers that name the other org. The row lands in
  the voter's org only. RED when the route drops the stamp, or reads the org
  from a header.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_audit_tick_writers_org_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import log as audit
from sqlalchemy import text

from tests.unit.test_audit_tick_writers_org import CASE_IDS, CASES, spy_on_writers

# ``graph_on_promoted`` and ``promoted`` are used by name for fixture injection.
from tests.unit.test_audit_writers_org_r8 import (  # noqa: F401
    _default_org,
    _delete,
    _orgs_of,
    _visible_as,
    graph_on_promoted,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE


@pytest.fixture
def written(monkeypatch) -> list[Any]:
    """Each event that reaches the real ``acb_audit.record``, in order.

    A pass-through on the ``record`` name of each writer. The write itself is
    the real one, so a dropped guard writes a real row.
    """
    real = audit.record
    seen: list[Any] = []

    def _spy(event: Any) -> None:
        seen.append(event)
        real(event)

    spy_on_writers(monkeypatch, _spy)
    return seen


def _rows_since(admin_engine, since: datetime, actor: str, action: str,
                target: str) -> list[tuple[str, str]]:
    """``(id, org)`` of each row of the event written at or after *since*."""
    with admin_engine.connect() as c:
        return [
            (r[0], r[1])
            for r in c.execute(
                text(
                    "SELECT id::text, organization_id::text FROM audit_event "
                    "WHERE actor = :a AND action = :ac AND target = :t AND at >= :s"
                ),
                {"a": actor, "ac": action, "t": target, "s": since},
            )
        ]


# ==========================================================================
# 1. The eight writers: no row under the operator org, and no row at all.
# ==========================================================================
class TestTickWritersUnderRls:
    @pytest.mark.parametrize(("case", "drive", "actor", "action", "target"), CASES, ids=CASE_IDS)
    def test_no_row_lands_under_the_operator_org(
        self, graph_on_promoted, written, monkeypatch, case, drive, actor, action, target,  # noqa: F811
    ):
        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        since = datetime.now(UTC) - timedelta(seconds=1)
        rows: list[tuple[str, str]] = []
        try:
            got_target = drive(monkeypatch)
            rows = _rows_since(p.admin_engine, since, actor, action, target or got_target)
            ids = [str(e.id) for e in written]
            leaked = _orgs_of(p.admin_engine, ids) if ids else []
            assert operator not in leaked and operator not in [o for _i, o in rows], (
                f"{case}: a row with no tenant landed under the OPERATOR org — "
                "cross-tenant leak"
            )
            assert written == [], (
                f"{case}: a row with no tenant reached the audit seam: "
                f"{[(e.actor, e.action) for e in written]}"
            )
            assert rows == [], f"{case}: the table holds a row for the event: {rows}"
        finally:
            _delete(p.admin_engine, [str(e.id) for e in written] + [i for i, _o in rows])


# ==========================================================================
# 2. Verifier F1 of slice 1: a forged org header does not move a vote.
# ==========================================================================
FORGED = ("X-Organization-Id", "X-Org-Id", "X-Tenant-Id")


async def _vote_over_http(voter: Any, headers: dict[str, str], message_id: str) -> Any:
    """``POST /chat/feedback`` through the real chat router, as *voter*.

    The override stands for the session: it gives the member and the org that
    the identity resolved. The route must take the org from it only.
    """
    import httpx
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes import chat

    async def _caller() -> Any:
        return voter

    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = _caller
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://chat.test",
    ) as client:
        got = await client.post(
            "/chat/feedback",
            json={"message_id": message_id, "vote": "down", "session_id": "s-f1"},
            headers=headers,
        )
    await audit.drain()
    return got


def _rows_of_message(admin_engine, message_id: str) -> list[tuple[str, str]]:
    with admin_engine.connect() as c:
        return [
            (r[0], r[1])
            for r in c.execute(
                text(
                    "SELECT id::text, organization_id::text FROM audit_event "
                    "WHERE action = 'message_feedback' AND target = :t"
                ),
                {"t": f"message:{message_id}"},
            )
        ]


class TestTheVoteOverHttp:
    @pytest.mark.parametrize("voter_side", ["b", "a"])
    async def test_a_forged_org_header_does_not_move_the_vote(
        self, graph_on_promoted, voter_side,  # noqa: F811
    ):
        from acb_auth import UserContext, UserRole, build_access

        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        own, other = (p.org_b, p.org_a) if voter_side == "b" else (p.org_a, p.org_b)
        voter = UserContext(
            email=f"f1-voter-{voter_side}@t2221.test", role=UserRole.EMPLOYEE,
            access=build_access(["feature:chat"]), organization_id=own,
        )
        message_id = f"m-f1-{uuid.uuid4().hex}"
        rows: list[tuple[str, str]] = []
        try:
            got = await _vote_over_http(voter, {h: other for h in FORGED}, message_id)
            assert got.status_code == 200, got.text
            assert got.json() == {"ok": True}
            rows = _rows_of_message(p.admin_engine, message_id)
            orgs = sorted(o for _i, o in rows)
            assert other not in orgs, (
                "the vote landed in the org that a request header named — the "
                "tenant came from request input (R5)"
            )
            assert operator not in orgs, "the vote landed under the OPERATOR org"
            assert orgs == [own], f"expected one row in the voter's org, got {orgs}"
            ids = [i for i, _o in rows]
            assert _visible_as(p.app_url, own, ids) == 1
            assert _visible_as(p.app_url, other, ids) == 0
            assert _visible_as(p.app_url, operator, ids) == 0
        finally:
            _delete(p.admin_engine, [i for i, _o in rows])
