"""R8: "Read as text" and the model read the same row (WS-63 PZ-20a).

Spec: ``project-docs/specs/personalization.md`` §9, row PZ-20a. The route
``GET /auth/me/assistant-profile/block`` and the executor read the member's
profile with ONE statement, ``READ_SQL_TEMPLATE``. It matches the address in
any case, and the row with a profile wins. A member with two case-variant rows
(a lower-case row with no profile, an upper-case row with one) must see in
Settings the text that the model reads.

The unit half is ``tests/unit/test_assistant_profile_block_view.py``.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import assistant_profile as ap
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from orchestrator import executor as ex
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

PROFILE = {"v": 1, "style": {"length": "short"},
           "facts": [{"id": "f1", "text": "Travels every Friday", "kind": "routine",
                      "at": "2026-10-23T11:00:00Z"},
                     {"id": "f2", "text": "Aime le café", "kind": "about",
                      "at": "2026-10-24T11:00:00Z"}]}


def _address() -> str:
    return f"block.{uuid.uuid4().hex[:8]}@pz20.test"


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE lower(user_id) = lower(:u)"),
                      {"u": email})
            c.execute(text("DELETE FROM people WHERE lower(email) = lower(:u)"), {"u": email})
            c.execute(text("DELETE FROM app_user WHERE lower(email) = lower(:u)"),
                      {"u": email})


def _seed(db, org: str, user_id: str, profile: Any) -> None:
    db.made.append(user_id)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
            "VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
            {"o": org, "u": user_id, "p": None if profile is None else json.dumps(profile)})


def _member_row(db, org: str, email: str, display: str) -> None:
    db.made.append(email)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :d, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": email, "d": display, "o": org})


@contextlib.contextmanager
def _tenant(org: str):
    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture
def executor(monkeypatch, app_engine):  # noqa: F811 — a fixture
    """The executor's opener as ``acb_app``, bound to the org it is asked for."""

    def opener_for(org: str):
        @contextlib.contextmanager
        def _open():
            with Session(app_engine) as s, s.begin():
                s.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                yield s
        return _open

    monkeypatch.setattr(ex, "_opener_for_org", opener_for)
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "assistant_profile_orgs", "", raising=False)


def _executor_block(monkeypatch, org: str, email: str) -> str:
    monkeypatch.setattr(ex, "_current_run_org", lambda: org)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: email)
    ex._PROFILE_CACHE.clear()
    token = ex._RUN_SOLO_ROOM.set(True)
    try:
        return ex._member_profile_entry("orchestrator")[0]
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
        ex._PROFILE_CACHE.clear()


async def _route_text(db, org: str, email: str) -> dict:
    from gateway.routes.admin import assistant_profile as route

    user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access([], roles=["member"]))
    ns = db
    async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
        with _tenant(org):
            res = await route.get_assistant_profile_block(user=user)
    return json.loads(res.body)


async def test_case_variant_rows_give_the_view_the_text_the_model_reads(
    db, executor, monkeypatch,
) -> None:
    email = _address()
    # The lower-case row has no profile. The upper-case row holds it.
    _seed(db, db.org_a, email, None)
    _seed(db, db.org_a, email.upper(), PROFILE)
    _member_row(db, db.org_a, email, "Ana Rao")
    model = _executor_block(monkeypatch, db.org_a, email)
    assert "Travels every Friday" in model
    body = await _route_text(db, db.org_a, email)
    assert body["enabled"] is True
    assert body["text"] == model
    assert body["text"].endswith(ap.BLOCK_FOOTER)


async def test_a_lower_case_row_with_a_profile_also_matches(db, executor, monkeypatch) -> None:
    email = _address()
    _seed(db, db.org_a, email.upper(), PROFILE)
    _member_row(db, db.org_a, email, "Ana Rao")
    body = await _route_text(db, db.org_a, email.lower())
    assert body["text"] == _executor_block(monkeypatch, db.org_a, email.lower())
    assert "Travels every Friday" in body["text"]


async def test_another_org_never_answers_the_view(db, executor, monkeypatch) -> None:
    email = _address()
    _seed(db, db.org_a, email, PROFILE)
    body = await _route_text(db, db.org_b, email)
    assert body == {"enabled": True, "text": ""}
