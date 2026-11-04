# ruff: noqa: F811
"""R8: ``executor._member_zones`` against a real database (task 1649).

The unit half is ``tests/unit/test_chat_now_line.py``. Here the read runs as
the ``acb_app`` role, which cannot bypass FORCE ROW LEVEL SECURITY.

* It reads the verified member's ``user_settings.timezone`` and
  ``people.timezone`` in the run's organization.
* A second member's zone never leaks, and nor does another org's.
* The ``UTC`` column default loses to a real ``people`` zone.
* A missing row gives no zones.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.today import resolve_zone
from orchestrator import executor as ex
from sqlalchemy import text

from tests.unit.test_executor_profile_block_r8 import (  # noqa: F401
    _DB_GATE,
    _address,
    _member_row,
    _person,
    app_engine,
    bound,
    db,
    promoted,
)

pytestmark = _DB_GATE


def _settings(db, org: str, email: str, zone: str | None) -> None:
    db.made.append(email)
    cols, vals = ("", "") if zone is None else (", timezone", ", :z")
    with db.admin_engine.begin() as c:
        c.execute(text(
            f"INSERT INTO user_settings (organization_id, user_id{cols}) "
            f"VALUES (CAST(:o AS uuid), :u{vals})"),
            {"o": org, "u": email, "z": zone})


@pytest.fixture
def run(monkeypatch):
    state = {"org": None, "member": None}
    monkeypatch.setattr(ex, "_current_run_org", lambda: state["org"])
    monkeypatch.setattr(ex, "_verified_run_member", lambda: state["member"])
    ex._ZONE_CACHE.clear()
    yield state
    ex._ZONE_CACHE.clear()


def test_it_reads_both_zones_of_the_verified_member(db, bound, run) -> None:
    email = _address()
    _member_row(db, db.org_a, email, "Ana")
    _person(db, db.org_a, email, None, "Europe/Berlin")
    _settings(db, db.org_a, email, "Asia/Tokyo")
    run.update(org=db.org_a, member=email)
    assert ex._member_zones() == ("Asia/Tokyo", "Europe/Berlin")


def test_another_member_and_another_org_never_leak(db, bound, run) -> None:
    me, other = _address(), _address()
    _member_row(db, db.org_a, me, "Me")
    _member_row(db, db.org_a, other, "Other")
    _settings(db, db.org_a, other, "Asia/Tokyo")
    _person(db, db.org_a, other, None, "Europe/Berlin")
    # The same address in org B has its own zones.
    _settings(db, db.org_b, me, "America/New_York")
    run.update(org=db.org_a, member=me)
    zones = ex._member_zones()
    assert zones[0] == "" and "Tokyo" not in str(zones) and "New_York" not in str(zones)
    assert "Berlin" not in str(zones)
    # RLS, not the WHERE: a session bound to org B sees no org A row.
    ex._ZONE_CACHE.clear()
    bound["org"] = db.org_b
    run.update(org=db.org_a, member=other)
    assert ex._member_zones() == ("", "")


def test_the_utc_column_default_loses_to_a_real_people_zone(db, bound, run) -> None:
    email = _address()
    _member_row(db, db.org_a, email, "Ana")
    _person(db, db.org_a, email, None, "Asia/Kolkata")
    _settings(db, db.org_a, email, None)  # the column default
    run.update(org=db.org_a, member=email)
    settings_zone, people_zone = ex._member_zones()
    assert settings_zone == "UTC" and people_zone == "Asia/Kolkata"
    assert resolve_zone(settings_zone, people_zone) == "Asia/Kolkata"


def test_a_missing_row_gives_no_zones(db, bound, run) -> None:
    run.update(org=db.org_a, member=_address())
    assert ex._member_zones() == ("", "")
