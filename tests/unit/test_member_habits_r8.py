"""WS-PZ MM-2 — habits on a real database (R8).

Spec: ``personalization.md`` §19.5. Code: ``acb_common/member_habits.py`` over
``member_action``. Hermetic twin: ``test_member_habits.py``. Run on a PRIVATE
ladder database, as ``test_member_action_r8.py`` says.

a. Six forwards of ``ge.com`` mail and one archive give the habit "forward,
   6 of 7". The row of a ``rule`` act and the rows of another kind of item,
   another member, another mailbox, another org and older than 90 days are not
   counted.
b. Two acts give no habit. A rule act never makes one.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from acb_common import member_habits as mh
from acb_common.db import tenant_session
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@mm2.test"
BEN = f"ben.{TAG}@mm2.test"
_LIKE = f"%.{TAG}@mm2.test"
MB1, MB2 = str(uuid.uuid4()), str(uuid.uuid4())
ITEM = {"app": "email", "mailbox_id": MB1, "features": {"sender_domain": "ge.com"}}


class _NoCache:
    async def get(self, k: Any) -> Any:
        return None

    async def setex(self, *a: Any) -> None:
        return None


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    from acb_common import tenant_redis

    monkeypatch.setattr(get_settings(), "member_actions_orgs", "*", raising=False)
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **kw: _NoCache())
    yield SimpleNamespace(p=promoted)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM member_action WHERE member_email LIKE :t"), {"t": _LIKE})


def _seed(world: Any, org: str, member: str, act: str, n: int, *, via: str = "app",
          domain: str = "ge.com", mailbox: str = MB1, age: int = 1,
          targets: list[str] | None = None) -> None:
    with world.p.admin_engine.begin() as c:
        for _ in range(n):
            c.execute(text(
                "INSERT INTO member_action (organization_id, member_email, app, act, via, "
                "item_kind, item_id, mailbox_id, features, targets, at) VALUES "
                "(CAST(:o AS uuid), :m, 'email', :a, :v, 'email', :i, :mb, "
                "CAST(:f AS jsonb), :t, now() - make_interval(days => :d))"),
                {"o": org, "m": member, "a": act, "v": via, "i": uuid.uuid4().hex,
                 "mb": mailbox, "f": json.dumps({"sender_domain": domain}),
                 "t": targets or [], "d": age})


def _habit(world: Any, org: str, member: str = ANA, item: dict[str, Any] = ITEM) -> Any:
    async def go() -> Any:
        dsn = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(dsn), tenant_session(org) as db:
            return await mh.member_habits(db, org=org, member=member, item=item)
    return asyncio.run(go())


def test_r8_six_of_seven_is_a_habit_and_the_noise_is_not_counted(world) -> None:
    a, b = world.p.org_a, world.p.org_b
    to = ["veena@ge.com", "kiran@ge.com"]
    _seed(world, a, ANA, "forward", 6, targets=to)
    _seed(world, a, ANA, "archive", 1)
    # Not counted: a rule act, another kind of item, another member, another
    # mailbox, another org, and an act older than 90 days.
    _seed(world, a, ANA, "archive", 5, via="rule")
    _seed(world, a, ANA, "archive", 5, domain="other.com")
    _seed(world, a, BEN, "archive", 5)
    _seed(world, a, ANA, "archive", 5, mailbox=MB2)
    _seed(world, b, ANA, "archive", 5)
    _seed(world, a, ANA, "archive", 5, age=91)
    h = _habit(world, a)
    assert h is not None
    assert (h.act, h.count, h.total) == ("forward", 6, 7)
    assert h.targets == ("kiran@ge.com", "veena@ge.com")
    assert _habit(world, a, member=BEN.upper()).act == "archive"


def test_r8_two_acts_and_a_rule_only_record_give_no_habit(world) -> None:
    a = world.p.org_a
    _seed(world, a, ANA, "forward", 2)
    assert _habit(world, a) is None
    _seed(world, a, ANA, "forward", 9, via="rule")
    assert _habit(world, a) is None
