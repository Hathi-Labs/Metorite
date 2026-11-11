"""R8 — ``style.languages`` over a real Postgres (PZ-52, personalization.md 5.3a).

The PUT and the GET of ``/auth/me/assistant-profile`` store and read the
ordered list through the tenant session. It proves, on the real ``jsonb``
column, as the non-privileged role of the rehearsal:

- a PUT of ``languages`` stores both keys, and a GET reads them back;
- a PUT of ``language`` alone (an old client) reads back as a list of one;
- an empty list clears both keys in the stored row;
- a seventh tag is a 422 and the stored row does not change;
- an old stored row with only ``language`` reads as a list of one, and a row
  that an old build left with a stale ``language`` reads as that language.
"""
from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
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


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})
            c.execute(text("DELETE FROM people WHERE email = :u"), {"u": email})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:tasks", "feature:projects"], roles=["member"]),
    )


def _route():
    from gateway.routes.admin import assistant_profile

    return assistant_profile


async def _put(org: str, user: UserContext, value: object) -> dict:
    with _tenant(org):
        return await _route().put_assistant_profile(
            body={"key": "style", "value": value}, user=user)


async def _get_style(org: str, user: UserContext) -> dict:
    with _tenant(org):
        return (await _route().get_assistant_profile(user=user))["style"]


def _stored_style(ns, email: str) -> dict:
    with ns.admin_engine.connect() as c:
        raw = c.execute(text(
            "SELECT assistant_profile FROM user_settings WHERE user_id = :u"),
            {"u": email}).scalar()
    return (raw if isinstance(raw, dict) else json.loads(raw))["style"]


def _seed(ns, org: str, email: str, style: dict) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
            "VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
            {"o": org, "u": email, "p": json.dumps({"v": 1, "style": style})})


def _address() -> str:
    return f"langs.{uuid.uuid4().hex[:8]}@pz52.test"


async def test_put_and_get_round_trip_the_ordered_list(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(db.app_url.render_as_string(hide_password=False)):
        answer = await _put(db.org_a, user, {"languages": ["en", "hi", "kn"], "length": "short"})
        read = await _get_style(db.org_a, user)
        reordered = await _put(db.org_a, user, {"languages": ["kn", "en"]})
        read2 = await _get_style(db.org_a, user)
    expected = {"language": "en", "languages": ["en", "hi", "kn"], "length": "short"}
    assert answer["style"] == expected
    assert read == expected
    assert reordered["style"] == {"language": "kn", "languages": ["kn", "en"]}
    assert read2 == {"language": "kn", "languages": ["kn", "en"]}
    assert _stored_style(db, email) == {"language": "kn", "languages": ["kn", "en"]}


async def test_an_old_client_that_sends_only_language_gets_a_list_of_one(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(db.app_url.render_as_string(hide_password=False)):
        await _put(db.org_a, user, {"language": "hi"})
        read = await _get_style(db.org_a, user)
    assert read == {"language": "hi", "languages": ["hi"]}
    assert _stored_style(db, email) == {"language": "hi", "languages": ["hi"]}


async def test_an_empty_list_clears_both_keys(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(db.app_url.render_as_string(hide_password=False)):
        await _put(db.org_a, user, {"languages": ["en", "hi"]})
        await _put(db.org_a, user, {"languages": []})
        read = await _get_style(db.org_a, user)
    assert read == {}
    assert _stored_style(db, email) == {}


async def test_a_seventh_tag_is_422_and_the_row_stays(db) -> None:
    email = _address()
    db.made.append(email)
    user = _member(email)
    async with tenant_engine_scope(db.app_url.render_as_string(hide_password=False)):
        await _put(db.org_a, user, {"languages": ["en", "hi"]})
        with pytest.raises(HTTPException) as caught:
            await _put(db.org_a, user, {"languages": ["en", "hi", "kn", "ta", "te", "mr", "bn"]})
        read = await _get_style(db.org_a, user)
    assert caught.value.status_code == 422
    assert read == {"language": "en", "languages": ["en", "hi"]}


async def test_old_stored_rows_read_by_the_rules(db) -> None:
    old, stale = _address(), _address()
    db.made.extend([old, stale])
    _seed(db, db.org_a, old, {"language": "kn"})
    _seed(db, db.org_a, stale, {"language": "ta", "languages": ["en", "hi"]})
    async with tenant_engine_scope(db.app_url.render_as_string(hide_password=False)):
        read_old = await _get_style(db.org_a, _member(old))
        read_stale = await _get_style(db.org_a, _member(stale))
    assert read_old == {"language": "kn", "languages": ["kn"]}
    assert read_stale == {"language": "ta", "languages": ["ta"]}
