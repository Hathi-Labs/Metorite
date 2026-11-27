"""WS-PZ MM-2 — habits from the act record (hermetic).

Spec: ``personalization.md`` §19.5 and §19.10. Code:
``packages/acb_common/acb_common/member_habits.py``. R8 twin:
``test_member_habits_r8.py``.

a. The rule of D-PZ-52: 3 acts or more, a share of 70% or more.
b. Only ``app`` and ``card`` acts are read. A ``rule`` act never makes a habit.
c. The gates: the flag off, ``learning`` off, an item with no kind.
d. The cache: a hit skips the read, a Redis failure is a miss.
e. No score of a person, and no write: the module names no INSERT or UPDATE.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import member_actions
from acb_common import member_habits as mh

ORG = "11111111-1111-1111-1111-111111111111"
ME = "ana@example.com"
_SRC = Path(mh.__file__).read_text(encoding="utf-8")
FWD = ("forward", ["veena@ge.com", "kiran@ge.com"])
ARCH = ("archive", [])


def test_a_habit_needs_three_acts_and_seventy_percent() -> None:
    assert mh.find_habit("email", [FWD] * 2) is None
    h = mh.find_habit("email", [FWD] * 3)
    assert h and (h.act, h.count, h.total) == ("forward", 3, 3)
    assert h.targets == ("kiran@ge.com", "veena@ge.com")
    h = mh.find_habit("email", [FWD] * 6 + [ARCH])
    assert h and (h.count, h.total) == (6, 7) and round(h.share, 2) == 0.86
    assert mh.find_habit("email", [FWD] * 5 + [ARCH] * 2) is not None  # 5/7 = 71%
    assert mh.find_habit("email", [FWD] * 2 + [ARCH] * 1) is None
    assert mh.find_habit("email", [FWD] * 4 + [ARCH] * 2) is None  # 4/6 = 67%


def test_the_same_act_with_other_targets_is_another_group() -> None:
    rows = [FWD] * 3 + [("forward", ["bob@ge.com"])] * 3
    assert mh.find_habit("email", rows) is None


def test_only_a_choice_is_read() -> None:
    assert mh.CHOICE_VIAS == ("app", "card")
    assert "rule" not in mh.CHOICE_VIAS
    assert "via = ANY" in mh.READ_SQL


def test_the_kind_keeps_known_features_only() -> None:
    kind = mh.kind_of({"features": {"sender_domain": "ge.com", "subject": "x", "edit_distance": 3,
                                    "triage_label": "", "is_reply": False}})
    assert kind == {"sender_domain": "ge.com", "is_reply": False}
    assert mh.kind_of({}) == {}


def test_the_module_writes_nothing_and_scores_no_person() -> None:
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", _SRC.split('"""', 2)[2])
    for word in ("score", "rank"):
        assert not re.search(rf"def \w*{word}", _SRC)


class _Db:
    def __init__(self, rows: list[Any], learning: Any = True) -> None:
        self.rows, self.learning, self.sql = rows, learning, []

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        self.sql.append(sql)
        if sql.startswith("SELECT assistant_profile"):
            return SimpleNamespace(first=lambda: ({"v": 1, "learning": self.learning},))
        return SimpleNamespace(all=lambda: self.rows)


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, k: Any) -> Any:
        return self.store.get(str(k))

    async def setex(self, k: Any, s: int, v: str) -> None:
        assert s == mh.CACHE_SECONDS
        self.store[str(k)] = v


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _Redis:
    from acb_common import tenant_redis

    r = _Redis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **kw: r)
    return r


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "member_actions_orgs", ORG, raising=False)


ITEM = {"app": "email", "features": {"sender_domain": "ge.com"}}


def _run(db: _Db, item: Any = ITEM, member: str = ME) -> Any:
    return asyncio.run(mh.member_habits(db, org=ORG, member=member, item=item))


def test_a_habit_is_read_and_cached(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> None:
    _on(monkeypatch)
    db = _Db([("forward", ["veena@ge.com"])] * 4)
    h = _run(db)
    assert h and h.act == "forward" and h.count == 4
    reads = [s for s in db.sql if s.startswith("SELECT act")]
    assert len(reads) == 1 and len(redis.store) == 1
    again = _run(db)
    assert again == h
    assert len([s for s in db.sql if s.startswith("SELECT act")]) == 1  # a hit


def test_no_habit_is_cached_too(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> None:
    _on(monkeypatch)
    db = _Db([("forward", [])])
    assert _run(db) is None and _run(db) is None
    assert len([s for s in db.sql if s.startswith("SELECT act")]) == 1


def test_a_redis_failure_is_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_common import tenant_redis

    _on(monkeypatch)

    def boom(**kw: Any) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(tenant_redis, "get_tenant_redis", boom)
    assert _run(_Db([("archive", [])] * 3)).act == "archive"


def test_the_gates(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> None:
    rows = [("archive", [])] * 3
    db = _Db(rows)
    assert _run(db) is None and db.sql == []  # flag off: no statement
    _on(monkeypatch)
    assert _run(_Db(rows, learning=False)) is None
    assert _run(_Db(rows), item={"app": "email", "features": {}}) is None
    assert _run(_Db(rows), item={"app": "nope", "features": {"sender_domain": "a"}}) is None
    assert _run(_Db(rows), member="anonymous") is None
    assert _run(_Db(rows)) is not None


def test_a_failed_read_answers_none(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> None:
    _on(monkeypatch)

    class Bad(_Db):
        async def execute(self, stmt: Any, params: Any = None) -> Any:
            if str(stmt).startswith("SELECT act"):
                raise RuntimeError("secret")
            return await super().execute(stmt, params)

    assert _run(Bad([])) is None


def test_the_read_names_the_bound_org_and_the_choice_vias() -> None:
    assert "organization_id = CAST(NULLIF(current_setting('app.tenant_id'" in mh.READ_SQL
    assert member_actions.counts_as_choice("app") and not member_actions.counts_as_choice("rule")
