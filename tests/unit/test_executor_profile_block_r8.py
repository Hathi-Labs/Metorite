"""R8: the read of the member's profile block against a real database.

WS-63 PZ-2, ``project-docs/specs/personalization.md`` §5.1. The unit half is
``tests/unit/test_executor_profile_block.py``. Here the executor's own read
(``_read_member_profile``, ``acb_common.assistant_profile.READ_SQL``) runs as
the ``acb_app`` role, which cannot bypass FORCE ROW LEVEL SECURITY.

* A row of org A is invisible to a session bound to org B, even when the
  SQL names org A.
* A row with a mixed-case ``user_id`` is found for the lower-case member.
* A NULL column gives no block.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import assistant_profile as ap
from acb_common import get_settings
from orchestrator import executor as ex
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

PROFILE = {"v": 1, "address": {"name": "VJ"},
           "facts": [{"id": "f1", "text": "Travels every Friday", "kind": "routine",
                      "at": "2026-10-23T11:00:00Z"}]}


def _address() -> str:
    return f"profile.{uuid.uuid4().hex[:8]}@pz2.test"


@pytest.fixture
def db(promoted):  # noqa: F811 — a fixture
    """The promoted catalog, with this module's rows removed after."""
    made: list[str] = []
    promoted.made = made
    yield promoted
    with promoted.admin_engine.begin() as c:
        for email in made:
            c.execute(text("DELETE FROM user_settings WHERE lower(user_id) = lower(:u)"),
                      {"u": email})


def _seed(db, org: str, user_id: str, profile: Any) -> None:
    db.made.append(user_id)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
            "VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
            {"o": org, "u": user_id, "p": None if profile is None else json.dumps(profile)})


@pytest.fixture
def bound(monkeypatch, app_engine):  # noqa: F811 — a fixture
    """The executor's opener, as ``acb_app`` with the tenant that
    ``state["org"]`` names. The real opener binds the org that it is asked
    for. A test sets ``state["org"]`` to bind another one."""
    state: dict[str, Any] = {"org": None}

    def opener_for(org: str):
        @contextlib.contextmanager
        def _open():
            with Session(app_engine) as s, s.begin():
                s.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                          {"o": state["org"] or org})
                yield s
        return _open

    monkeypatch.setattr(ex, "_opener_for_org", opener_for)
    return state


def test_the_member_reads_the_own_profile(db, bound) -> None:
    email = _address()
    _seed(db, db.org_a, email, PROFILE)
    raw = ex._read_member_profile(db.org_a, email)
    assert raw == PROFILE
    assert 'Address them as "VJ".' in ap.render_block(raw)


def test_another_org_never_sees_the_row(db, bound) -> None:
    email = _address()
    _seed(db, db.org_a, email, PROFILE)
    assert ex._read_member_profile(db.org_b, email) is None
    # RLS, not the WHERE: a session bound to org B that names org A sees no row.
    bound["org"] = db.org_b
    assert ex._read_member_profile(db.org_a, email) is None


def test_a_mixed_case_user_id_is_found(db, bound) -> None:
    email = _address()
    stored = email.replace("profile", "Profile").upper()
    _seed(db, db.org_a, stored, PROFILE)
    assert ex._read_member_profile(db.org_a, email.lower()) == PROFILE


def test_the_row_with_a_profile_wins_over_a_lower_case_null_row(db, bound) -> None:
    email = _address()
    _seed(db, db.org_a, email, None)
    _seed(db, db.org_a, email.upper(), PROFILE)
    assert ex._read_member_profile(db.org_a, email) == PROFILE


def test_a_null_column_gives_no_block(db, bound, monkeypatch) -> None:
    email = _address()
    _seed(db, db.org_a, email, None)
    assert ex._read_member_profile(db.org_a, email) is None

    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    monkeypatch.setattr(ex, "_current_run_org", lambda: db.org_a)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: email)
    ex._PROFILE_CACHE.clear()
    token = ex._RUN_SOLO_ROOM.set(True)
    try:
        assert ex._member_profile_block("orchestrator") == ""
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
        ex._PROFILE_CACHE.clear()


def test_the_whole_block_through_the_real_read(db, bound, monkeypatch) -> None:
    email = _address()
    _seed(db, db.org_a, email, PROFILE)
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    monkeypatch.setattr(ex, "_current_run_org", lambda: db.org_a)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: email)
    ex._PROFILE_CACHE.clear()
    token = ex._RUN_SOLO_ROOM.set(True)
    try:
        assert ex._member_profile_block("orchestrator") == ap.render_block(PROFILE)
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
        ex._PROFILE_CACHE.clear()
