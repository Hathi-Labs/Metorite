"""R8 — migration 317 gives a stop of the interview from before PZ-32 its
``stopped_at`` (WS-63 PZ-32, repair round 2).

Spec: ``project-docs/specs/personalization.md`` §14.4 "PZ-32 as built" item
8. ``assistant_profile.interview_stopped`` reads ONLY ``stopped_at``. A stop
before PZ-32 marked the ten old areas ``never`` and wrote no
``stopped_at``, so the migration backfills it, once.

**R8.** A real Postgres, on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal`` (FORCE RLS on ``user_settings``). The
migration runs as a role that owns ``user_settings`` and is NOT a
superuser, so FORCE RLS binds it as it binds the runner in production, and
only the per-tenant pass of the file sees a row. Run::

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_interview_stop_backfill_r8.py -v -rs

Mutations this file catches (R7). ``M`` is the migration file:

* ``M`` writes no ``stopped_at``, or no ``never`` for ``name`` and
  ``language``, or changes another area;
* ``M`` matches a row that is not the old stop: an old area open, no area
  ``never``, or a ``stopped_at`` already;
* ``M`` has no per-tenant pass under row security, so the owner changes
  nothing;
* a second run changes a row again;
* the list of old areas of ``M`` drifts from ``assistant_profile.OLD_AREAS``.
"""
# ruff: noqa: F811 — the imported fixture `promoted` is the argument of each test
from __future__ import annotations

import copy
import json
import os
import re
import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import assistant_profile as ap
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import _LADDER_DIR
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

pytestmark = _DB_GATE

#: Owns ``user_settings`` and is NOT a superuser, as the production runner.
_OWNER_ROLE = "acb_owner_pz32stop"
_OWNER_PW = "pz32stop_owner_pw"

#: Found by content, never by number (R1): a renumber at merge must not read
#: as a broken migration.
_MIGRATION = next(p for p in sorted(os.listdir(_LADDER_DIR))
                  if p.endswith("_assistant_profile_interview_stopped.sql"))
_SQL = os.path.join(_LADDER_DIR, _MIGRATION)

T0 = "2026-09-01T10:00:00Z"
ASKED = "2026-09-20T08:30:00Z"


def _areas(states: dict[str, str]) -> dict[str, Any]:
    return {a: {"state": s, "at": T0} for a, s in states.items()}


def _old_stop(**extra: str) -> dict[str, Any]:
    """The shape that a stop before PZ-32 wrote: ``goals`` answered, each
    other old area ``never``, no ``stopped_at``, no ``name``, no ``language``."""
    states = {a: ("answered" if a == "goals" else "never") for a in ap.OLD_AREAS}
    return {"v": 1, "learning": True, "facts": [{"id": "f1", "text": "Ship it",
                                                  "kind": "work", "area": "goals"}],
            "interview": {"started_at": T0, "last_asked_at": ASKED,
                          "areas": _areas({**states, **extra})}}


def _not_a_stop(kind: str) -> dict[str, Any]:
    profile = _old_stop()
    areas = profile["interview"]["areas"]
    if kind == "one_open":
        del areas["role"]
    elif kind == "one_skipped":
        areas["role"]["state"] = "skipped"
    elif kind == "no_never":
        for area in areas:
            areas[area]["state"] = "answered"
    elif kind == "stopped":
        profile["interview"]["stopped_at"] = "2026-10-30T09:00:00Z"
    return profile


@pytest.fixture(scope="module")
def world(promoted):
    """The promoted catalog, with ``user_settings`` owned by a role that RLS
    binds. The rows of this module go at the end."""
    db = promoted.admin_engine.url.database
    with create_engine(promoted.admin_engine.url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_OWNER_ROLE}')"
            f" THEN CREATE ROLE {_OWNER_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_OWNER_ROLE} WITH LOGIN PASSWORD '{_OWNER_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{db}" TO {_OWNER_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_OWNER_ROLE}"))
        c.execute(text(f"GRANT SELECT ON organization TO {_OWNER_ROLE}"))
        owner = c.execute(text("SELECT tableowner FROM pg_tables "
                               "WHERE tablename = 'user_settings'")).scalar_one()
        c.execute(text(f"ALTER TABLE user_settings OWNER TO {_OWNER_ROLE}"))
    promoted.owner_url = promoted.admin_engine.url.set(username=_OWNER_ROLE,
                                                       password=_OWNER_PW)
    promoted.pz32_made = []
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org, email in promoted.pz32_made:
            c.execute(text("DELETE FROM user_settings WHERE organization_id = CAST(:o AS uuid)"
                           " AND user_id = :u"), {"o": org, "u": email})
        c.execute(text(f'ALTER TABLE user_settings OWNER TO "{owner}"'))


def _put(world, org: str, profile: dict[str, Any]) -> str:
    email = f"stop.{uuid.uuid4().hex[:8]}@pz32stop.test"
    world.pz32_made.append((org, email))
    with world.admin_engine.begin() as c:
        c.execute(text("INSERT INTO user_settings (organization_id, user_id, assistant_profile)"
                       " VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
                  {"o": org, "u": email, "p": json.dumps(profile)})
    return email


def _get(world, org: str, email: str) -> dict[str, Any]:
    with world.admin_engine.begin() as c:
        raw = c.execute(text("SELECT assistant_profile FROM user_settings"
                             " WHERE organization_id = CAST(:o AS uuid) AND user_id = :u"),
                        {"o": org, "u": email}).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


def _run(url) -> list[str]:
    """Apply the file once on *url*. Returns the notices."""
    notices: list[str] = []
    eng = create_engine(url, future=True)
    try:
        with eng.begin() as conn:
            conn.connection.dbapi_connection.add_notice_handler(
                lambda diag: notices.append(diag.message_primary or ""))
            with conn.connection.dbapi_connection.cursor() as cur, \
                    open(_SQL, encoding="utf-8") as fh:
                cur.execute(fh.read())
    finally:
        eng.dispose()
    return notices


def test_the_list_of_old_areas_is_the_one_of_the_code() -> None:
    with open(_SQL, encoding="utf-8") as fh:
        sql = fh.read()
    lists =re.findall(r"unnest\(ARRAY\[([^\]]*)\]\)", sql)
    old = [tuple(re.findall(r"'([a-z_]+)'", body)) for body in lists]
    assert old.count(ap.OLD_AREAS) == 2, old
    assert ("name", "language") in old
    assert set(ap.AREAS) == {*ap.OLD_AREAS, "name", "language"}


def test_317_backfills_each_old_stop_once_and_nothing_else(world) -> None:
    a, b = world.org_a, world.org_b
    legacy = {org: _put(world, org, _old_stop()) for org in (a, b)}
    # `name` asked before the backfill: a stop marks it never too.
    asked_name = _put(world, a, _old_stop(name="asked"))
    others = {kind: _put(world, a, _not_a_stop(kind))
              for kind in ("one_open", "one_skipped", "no_never", "stopped")}
    before = {email: _get(world, a, email) for email in others.values()}

    # As the owner, under FORCE RLS: only the per-tenant pass sees a row.
    notices = _run(world.owner_url)
    assert any(re.fullmatch(r"317: \d+ interview\(s\) from before PZ-32 got stopped_at", n)
               for n in notices), notices

    for org, email in legacy.items():
        got = _get(world, org, email)
        want = copy.deepcopy(_old_stop())
        want["interview"]["stopped_at"] = ASKED
        want["interview"]["areas"]["name"] = {"state": "never", "at": ASKED}
        want["interview"]["areas"]["language"] = {"state": "never", "at": ASKED}
        assert got == want, org
        assert ap.interview_stopped(got["interview"])
        assert ap.open_areas(got["interview"], ap._parse_time("2030-01-01T00:00:00Z")) == []
    assert _get(world, a, asked_name)["interview"]["areas"]["name"]["state"] == "never"
    for email, row in before.items():
        assert _get(world, a, email) == row, email

    # Idempotent: a second run, as each role, changes nothing.
    settled = {email: _get(world, org, email) for org, email in world.pz32_made}
    for url in (world.owner_url, world.admin_engine.url):
        _run(url)
        assert {email: _get(world, org, email) for org, email in world.pz32_made} == settled


def test_317_with_no_stored_time_stamps_now(world) -> None:
    profile = _old_stop()
    del profile["interview"]["last_asked_at"]
    email = _put(world, world.org_a, profile)
    _run(world.owner_url)
    stored = _get(world, world.org_a, email)["interview"]
    assert ap._parse_time(stored["stopped_at"]) is not None
    assert stored["areas"]["name"] == {"state": "never", "at": stored["stopped_at"]}
