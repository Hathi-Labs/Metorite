"""R8: the read of the member's profile block against a real database.

WS-63 PZ-2, ``project-docs/specs/personalization.md`` §5.1. The unit half is
``tests/unit/test_executor_profile_block.py``. Here the executor's own read
(``_read_member_profile``, ``acb_common.assistant_profile.READ_SQL``) runs as
the ``acb_app`` role, which cannot bypass FORCE ROW LEVEL SECURITY.

* A row of org A is invisible to a session bound to org B, even when the
  SQL names org A.
* A row with a mixed-case ``user_id`` is found for the lower-case member.
* A NULL column gives no block.
* ``ADDRESS_SQL`` (PZ-4) gives the bound org's ``people.preferred_name`` and
  ``people.timezone``, and never those of another org's person with the
  same address.
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
    raw = ex._read_member_profile(db.org_a, email)[0]
    assert raw == PROFILE
    assert 'Address them as "VJ".' in ap.render_block(raw)


def test_another_org_never_sees_the_row(db, bound) -> None:
    email = _address()
    _seed(db, db.org_a, email, PROFILE)
    assert ex._read_member_profile(db.org_b, email) == (None, "", "", "")
    # RLS, not the WHERE: a session bound to org B that names org A sees no row.
    bound["org"] = db.org_b
    assert ex._read_member_profile(db.org_a, email) == (None, "", "", "")


def test_a_mixed_case_user_id_is_found(db, bound) -> None:
    email = _address()
    stored = email.replace("profile", "Profile").upper()
    _seed(db, db.org_a, stored, PROFILE)
    assert ex._read_member_profile(db.org_a, email.lower())[0] == PROFILE


def test_the_row_with_a_profile_wins_over_a_lower_case_null_row(db, bound) -> None:
    email = _address()
    _seed(db, db.org_a, email, None)
    _seed(db, db.org_a, email.upper(), PROFILE)
    assert ex._read_member_profile(db.org_a, email)[0] == PROFILE


def test_a_null_column_gives_no_block(db, bound, monkeypatch) -> None:
    email = _address()
    _seed(db, db.org_a, email, None)
    assert ex._read_member_profile(db.org_a, email) == (None, "", "", "")

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


# ── ADDRESS_SQL, the form of address (PZ-4) ────────────────────────────────


def _member_row(db, org: str, email: str, display: str | None) -> None:
    db.made.append(email)
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :d, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": email, "d": display, "o": org})


def _person(db, org: str, email: str, preferred: str | None, zone: str | None) -> None:
    db.made.append(email)
    with db.admin_engine.begin() as c:
        # A new member gets a People row from a trigger, so set that row when
        # it is there.
        done = c.execute(text(
            "UPDATE people SET preferred_name = :p, timezone = :z "
            "WHERE organization_id = CAST(:o AS uuid) AND lower(email) = lower(:e)"),
            {"e": email, "o": org, "p": preferred, "z": zone}).rowcount
        if done:
            return
        c.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, source_key, "
            "organization_id, updated_by, updated_at, preferred_name, timezone) "
            "VALUES (gen_random_uuid(), 'Address Test', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now(), :p, :z)"),
            {"e": email, "k": f"manual:{org}:{email}", "o": org, "p": preferred, "z": zone})


def test_the_address_read_gives_the_bound_org_s_person(db, bound) -> None:
    email = _address()
    _member_row(db, db.org_a, email, "  Vijay Raghav Varada ")
    _person(db, db.org_a, email.upper(), " Viju ", "Europe/Berlin")
    # The same address in org B, with another name and zone.
    _person(db, db.org_b, email, "Eve", "America/New_York")
    assert ex._read_member_profile(db.org_a, email) == (
        None, "Viju", "Europe/Berlin", "Vijay Raghav Varada")


def test_another_org_s_person_never_answers_the_join(db) -> None:
    """The member of org A has no People row in A. Org B has a person with
    the same address. The join names the org on both sides, so B's name
    never answers, even for a role that RLS does not narrow."""
    email = _address()
    _member_row(db, db.org_a, email, "Vijay Varada")
    with db.admin_engine.begin() as c:
        # The People row that a trigger made in org A goes, so B's is the only
        # one. Without the org on both sides, the join would then find it.
        c.execute(text("DELETE FROM people WHERE organization_id = CAST(:o AS uuid) "
                       "AND lower(email) = lower(:e)"), {"o": db.org_a, "e": email})
    _person(db, db.org_b, email, "Eve", "America/New_York")
    with db.admin_engine.connect() as c:
        row = c.execute(text(ap.ADDRESS_SQL), {"org": db.org_a, "m": email.upper()}).first()
    assert tuple(row) == (None, None, "Vijay Varada")


def test_the_address_read_under_rls_sees_no_other_org(db, bound) -> None:
    email = _address()
    _member_row(db, db.org_a, email, "Vijay Varada")
    _person(db, db.org_a, email, "Viju", None)
    assert ex._read_member_profile(db.org_b, email) == (None, "", "", "")
    bound["org"] = db.org_b
    assert ex._read_member_profile(db.org_a, email) == (None, "", "", "")


def test_the_whole_block_names_the_preferred_name(db, bound, monkeypatch) -> None:
    email = _address()
    _member_row(db, db.org_a, email, "Vijay Varada")
    _person(db, db.org_a, email, "Viju", "Asia/Kolkata")
    _seed(db, db.org_a, email, {"v": 1, "style": {"length": "short"}})
    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    monkeypatch.setattr(ex, "_current_run_org", lambda: db.org_a)
    monkeypatch.setattr(ex, "_verified_run_member", lambda: email)
    ex._PROFILE_CACHE.clear()
    token = ex._RUN_SOLO_ROOM.set(True)
    try:
        block, zone, greeting = ex._member_profile_entry("orchestrator")
    finally:
        ex._RUN_SOLO_ROOM.reset(token)
        ex._PROFILE_CACHE.clear()
    assert block.split("\n")[1].startswith('Address them as "Viju".')
    assert (zone, greeting) == ("Asia/Kolkata", "brief")
