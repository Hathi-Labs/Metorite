"""R8 — the ``If-Match`` compare-and-set of ``PUT /tasks/settings`` (CAL-3, task 1677).

The day rule tools read the whole list of ``day_templates``, change it and write
it back. Two changes at once would lose one of them. So ``PUT /tasks/settings``
takes an optional ``If-Match`` token (``settings.day_templates_token``), locks the
member's row in the transaction, and answers 412 when the token is stale.

This runs the REAL route functions as the NON-privileged role of the phase-4
rehearsal (``acb_app_h3rls``) on a real Postgres with two organizations, so the
``FOR UPDATE`` and the advisory lock run as SQL. A hermetic fake agrees with any
SQL it is handed.

Run: ``uv run pytest tests/unit/test_day_rules_if_match_r8.py -q`` (needs the dev DB,
``eval "$(bash scripts/dev_db.sh --export)"``).
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

FOCUS = {"days": [0, 1, 2, 3, 4], "start_hour": 8, "end_hour": 11, "kind": "focus",
         "label": "Deep work", "theme": ""}
BLOCK = {"days": [4], "start_hour": 15, "end_hour": 17, "kind": "block",
         "label": "No meetings", "theme": ""}


def _address() -> str:
    return f"rules.{uuid.uuid4().hex[:8]}@cal3.test"


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


async def _put(org: str, email: str, rules: list[dict], if_match: str | None = None):
    from gateway.routes.tasks.settings import UserSettingsPatch, put_user_settings

    with _tenant(org):
        return await put_user_settings(
            UserSettingsPatch(day_templates=rules), SimpleNamespace(email=email),
            if_match=if_match)


async def _get(org: str, email: str):
    from gateway.routes.tasks.settings import get_user_settings

    with _tenant(org):
        return await get_user_settings(user=SimpleNamespace(email=email))


def _token(rules: list[dict]) -> str:
    from gateway.routes.tasks.settings import day_templates_token

    return day_templates_token(rules)


async def test_a_stale_token_answers_412_and_the_list_is_unchanged(db) -> None:
    """Mutation: skip ``_check_day_rules_token`` in ``write_member_settings`` -> the
    stale write lands -> red."""
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, email, [FOCUS])
        stale = (await _get(db.org_a, email)).day_templates
        await _put(db.org_a, email, [FOCUS, BLOCK])  # another change lands first
        with pytest.raises(HTTPException) as err:
            await _put(db.org_a, email, [BLOCK], if_match=f'"{_token(stale)}"')
        after = (await _get(db.org_a, email)).day_templates
    assert err.value.status_code == 412
    assert "changed since you read them" in err.value.detail
    assert after == [FOCUS, BLOCK]


async def test_a_matching_token_writes(db) -> None:
    """Mutation: compare the wrong list (the new one) -> red."""
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, email, [FOCUS])
        read = (await _get(db.org_a, email)).day_templates
        saved = await _put(db.org_a, email, [FOCUS, BLOCK], if_match=_token(read))
        after = (await _get(db.org_a, email)).day_templates
    assert saved.day_templates == after == [FOCUS, BLOCK]


async def test_no_header_writes_as_before(db) -> None:
    """The Calendar sheet sends no header. Mutation: refuse a write with no
    ``If-Match`` -> red."""
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, email, [FOCUS])
        await _put(db.org_a, email, [BLOCK])
        after = (await _get(db.org_a, email)).day_templates
    assert after == [BLOCK]


async def test_a_member_with_no_row_writes_with_the_token_of_the_empty_list(db) -> None:
    """No row reads as the empty list, whose token is the one of a new member. A
    stale token still answers 412 there, and the row is not made."""
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        with pytest.raises(HTTPException) as err:
            await _put(db.org_a, email, [FOCUS], if_match=f'"{_token([BLOCK])}"')
        assert err.value.status_code == 412
        assert (await _get(db.org_a, email)).day_templates == []
        await _put(db.org_a, email, [FOCUS], if_match=_token([]))
        after = (await _get(db.org_a, email)).day_templates
    assert after == [FOCUS]


async def test_the_token_names_this_organization_only(db) -> None:
    """The same address in the other organization has its own list and token."""
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, email, [FOCUS])
        await _put(db.org_b, email, [BLOCK])
        in_b = (await _get(db.org_b, email)).day_templates
        with pytest.raises(HTTPException):
            await _put(db.org_b, email, [FOCUS, BLOCK], if_match=_token([FOCUS]))
        await _put(db.org_b, email, [FOCUS, BLOCK], if_match=_token(in_b))
        a = (await _get(db.org_a, email)).day_templates
    assert in_b == [BLOCK] and a == [FOCUS]


async def test_two_changes_at_once_let_one_win(db) -> None:
    """Both writers read the same list and send its token. The row lock lets one
    write, and the other finds a new list and gets 412. Mutation: drop the lock
    and the compare -> both pass -> red."""
    email = _address()
    db.made.append(email)
    async with tenant_engine_scope(_app_dsn(db)):
        await _put(db.org_a, email, [FOCUS])
        read = (await _get(db.org_a, email)).day_templates
        token = _token(read)
        results = await asyncio.gather(
            _put(db.org_a, email, [FOCUS, BLOCK], if_match=token),
            _put(db.org_a, email, [], if_match=token),
            return_exceptions=True,
        )
        after = (await _get(db.org_a, email)).day_templates
    fails = [r for r in results if isinstance(r, HTTPException)]
    wins = [r for r in results if not isinstance(r, BaseException)]
    assert len(wins) == 1 and len(fails) == 1 and fails[0].status_code == 412, results
    assert after == wins[0].day_templates
