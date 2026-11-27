"""WS-PZ MM-1b — the card answer in the act record, on a real database (R8).

Code: ``orchestrator/card_act.py`` over ``member_action``. Hermetic twin:
``test_card_act.py``. Run on a PRIVATE ladder database as
``test_member_action_r8.py`` says.

a. An approve and a decline of the run's own member land as ``card_approve``
   and ``card_reject`` with ``via = card`` in the run's org, and hold no text.
b. Another outcome, a press of another member, the flag off and a missing org
   write nothing.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from orchestrator import card_act
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@mm1b.test"
_LIKE = f"%.{TAG}@mm1b.test"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "member_actions_orgs", "*", raising=False)
    import acb_common.db as db

    monkeypatch.setattr(db, "current_tenant", lambda: None)
    # Each call opens a new engine scope, so each write connects cold. The
    # bound of a warm write (3 s) is not what this file tests.
    monkeypatch.setattr(card_act, "_TIMEOUT_S", 60.0)
    yield SimpleNamespace(p=promoted, monkeypatch=monkeypatch)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM member_action WHERE member_email LIKE :t"), {"t": _LIKE})


def _answer(world: Any, org: str | None, request_id: str | None, outcome: str,
            responder: str = ANA.upper(), *, late: bool = False) -> bool:
    async def facts(_tid: str) -> tuple[str | None, str]:
        return org, ANA

    world.monkeypatch.setattr(card_act, "_facts", facts)

    async def go() -> bool:
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn):
            return await card_act.record_card_act(
                "thread-1", request_id, outcome, responder, late=late)
    return asyncio.run(go())


def _rows(world: Any) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT * FROM member_action WHERE member_email LIKE :t ORDER BY at, act"),
            {"t": _LIKE}).mappings()]


def test_r8_an_approve_and_a_decline_land_as_card_acts(world) -> None:
    a = world.p.org_a
    assert _answer(world, a, "a" * 32, "approved") is True
    assert _answer(world, a, "b" * 32, "declined") is True
    assert _answer(world, a, None, "approved", "", late=True) is True
    rows = _rows(world)
    assert sorted((r["app"], r["act"], r["via"], r["item_kind"]) for r in rows) == [
        ("chat", "card_approve", "card", "card"), ("chat", "card_approve", "card", "card"),
        ("chat", "card_reject", "card", "card")]
    assert all(str(r["organization_id"]) == a and r["member_email"] == ANA for r in rows)
    assert {"a" * 32, "b" * 32} <= {r["item_id"] for r in rows}
    assert all(r["features"] == {} and r["targets"] == [] and r["mailbox_id"] is None
               for r in rows)


def test_r8_other_outcomes_members_the_flag_and_no_org_write_nothing(world) -> None:
    a = world.p.org_a
    assert _answer(world, a, "c" * 32, "timed out") is False
    assert _answer(world, a, "c" * 32, "closed by your message") is False
    assert _answer(world, a, "c" * 32, "another member answered") is False
    assert _answer(world, a, "c" * 32, "approved", f"bob.{TAG}@mm1b.test") is False
    assert _answer(world, None, "c" * 32, "approved") is False
    world.monkeypatch.setattr(get_settings(), "member_actions_orgs", "", raising=False)
    assert _answer(world, a, "c" * 32, "approved") is False
    assert _rows(world) == []
