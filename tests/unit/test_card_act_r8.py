"""WS-PZ MM-1b — the card answer in the act record, on a real database (R8).

Code: ``gateway/card_act.py`` over ``member_action``. Hermetic twin:
``test_card_act.py``. Run on a PRIVATE ladder database as
``test_member_action_r8.py`` says.

a. An APPROVE and a REJECT of the session member land as ``card_approve`` and
   ``card_reject`` with ``via = card`` in the session's org, and hold no text.
b. A reply-shaped answer, the flag off, and a missing org write nothing.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from gateway import card_act
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
    yield SimpleNamespace(p=promoted)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM member_action WHERE member_email LIKE :t"), {"t": _LIKE})


def _answer(world: Any, org: str | None, request_id: str, answer: str,
            declined: bool = False, shared: bool = False) -> bool:
    user = SimpleNamespace(organization_id=org, email=ANA.upper())

    async def go() -> bool:
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn):
            return await card_act.record_card_answer(
                user, request_id, answer, declined, shared_room=shared)
    return asyncio.run(go())


def _rows(world: Any) -> list[dict[str, Any]]:
    with world.p.admin_engine.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT * FROM member_action WHERE member_email LIKE :t ORDER BY at"),
            {"t": _LIKE}).mappings()]


def test_r8_a_yes_and_a_no_land_as_card_acts(world) -> None:
    a = world.p.org_a
    assert _answer(world, a, "r" * 32, "APPROVE") is True
    assert _answer(world, a, "s" * 32, "REJECT") is True
    rows = _rows(world)
    assert [(r["app"], r["act"], r["via"], r["item_kind"]) for r in rows] == [
        ("chat", "card_approve", "card", "card"), ("chat", "card_reject", "card", "card")]
    assert all(str(r["organization_id"]) == a and r["member_email"] == ANA for r in rows)
    assert [r["item_id"] for r in rows] == ["r" * 32, "s" * 32]
    assert all(r["features"] == {} and r["targets"] == [] and r["mailbox_id"] is None
               for r in rows)


def test_r8_other_shapes_the_flag_and_no_org_write_nothing(world, monkeypatch) -> None:
    a = world.p.org_a
    assert _answer(world, a, "t" * 32, '{"kind":"send","body":"hi"}') is False
    assert _answer(world, None, "t" * 32, "APPROVE") is False
    assert _answer(world, a, "t" * 32, "", declined=True) is False
    assert _answer(world, a, "t" * 32, "APPROVE", shared=True) is False
    monkeypatch.setattr(get_settings(), "member_actions_orgs", "", raising=False)
    assert _answer(world, a, "t" * 32, "APPROVE") is False
    assert _rows(world) == []
