"""D63 follow-up to H-304 — a seal sends the delta feed's removals.

H-304 (#963) hid a sealed personal tree from every org-wide reader. It left
one gap open: the delta feed (``GET /projects/delta/tasks``) sent no removal
for a tree that had just been sealed. A client that had cached those rows kept
them on screen until its next full pull. The server hid the data, and the
open screens still showed it.

The fix, in two halves:

* ``projects/seal.py`` bumps ``pm_tasks.updated_at`` on every task in the tree,
  through ``core.touch_task``, when a seal or an unseal changes a project;
* ``delta.py`` stream A2 reads ``core.sealed_loss_clause``. It finds each task
  that the reader could reach BEFORE the seal and that changed after the
  cursor. The visibility pass then keeps the hand-over as a row, and every
  other id goes out as a bare removal.

What each test proves:

* an admin with a cursor from before the seal gets a removal for every
  sealed task, and no title or content of one;
* the hand-over stays a ROW: for its assignee, and for the admin who is the
  assignee of the second one;
* a member who never saw the tree gets nothing, which is what tells "changed
  and now invisible" from "never visible";
* an unseal brings the rows back on the next delta;
* org B gets nothing from org A's seal or unseal.

⚠️ **No client test.** The web client does not read the delta feed. Its
consumer is the agent manifest (``skill_projects/manifest.py``). So there is
no store that applies a removal, and nothing to test with vitest.

**The horizon.** The feed reads nothing newer than ``now()`` minus
``delta.HORIZON_LAG_SECONDS``. A seal stamps ``now()``, so a poll inside five
seconds sees nothing. As #974 did, this suite moves time back rather than
waiting: after each door it moves EVERY task stamp, and every held cursor, one
hour back. The order of the stamps does not change, so the feed answers the
same question it would answer five seconds later.

**Mutation table** (each mutant was applied alone, and each one turns the named
test red):

======================================  =======================================
Mutant                                  Test that fails
======================================  =======================================
``seal.py``: no bump at seal time       ``test_the_admin_gets_a_removal_for_
                                        every_sealed_task``
``delta.py``: the removal carries the   ``test_no_removal_or_row_carries_a_
title (final pass without the           sealed_title``
visibility clause)
``delta.py``: every A2 id is removed,   ``test_the_admins_own_hand_over_stays_
the hand-over too                       a_row``
``seal.py``: no bump at unseal time     ``test_the_unseal_brings_the_rows_back``
``core.py``: the pre-seal reach is      ``test_a_member_who_never_saw_the_tree_
dropped (any sealed task)               gets_nothing``
======================================  =======================================

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_sealed_tree_delta_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import _LADDER_DIR, apply_ladder
from tests.unit.test_sealed_tree_visibility_r8 import (
    ADMIN,
    ASSIGNEE,
    B_ADMIN,
    DOMAIN,
    INTERNAL,
    LEAVER,
    PEER,
    _as_app,
    _drop_database,
    _exec_sql_file,
    _ok,
    _Seeder,
)

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)
pytestmark = _DB_GATE

_APP_ROLE = "acb_app_sealeddelta"
_APP_PW = "sealeddelta_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

#: Every reader the timeline polls as.
READERS = (ADMIN, ASSIGNEE, PEER, B_ADMIN)
#: The title of every sealed task that is NOT handed over to the admin. None
#: of them may appear anywhere in the admin's delta after the seal.
SEALED_TITLES = ("leaver private", "leaver area", "handed over to the assignee")


def _build_world(admin_url) -> SimpleNamespace:
    """A private promoted catalog, two tenants, and one tree NOT yet sealed."""
    dedicated = f"{admin_url.database}_sealeddelta"
    _drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(_GENERATED, phase),
                                       encoding="utf-8") as fh:
            _exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))

    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "sealeddelta-a"), (org_b, "sealeddelta-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :slug, :slug)"), {"id": oid, "slug": slug})

    a = _Seeder(eng, org_a, 0)
    b = _Seeder(eng, org_b, 0)
    reader = ["feature:projects", "data:org:read"]
    a.member(ADMIN, reader)
    a.member(ASSIGNEE, ["feature:projects"])
    a.member(PEER, ["feature:projects"])
    a.member(LEAVER, ["feature:projects"])
    b.member(B_ADMIN, reader)

    w = SimpleNamespace(admin=eng, org_a=org_a, org_b=org_b, dedicated=dedicated,
                        p={}, t={})
    p = w.p

    def task(key: str, seeder: _Seeder, project: str, root: str, title: str,
             assignee: str | None = None) -> None:
        w.t[key], _ = seeder.task(project, root, title, assignee=assignee)

    p["team"] = a.project("Team", grant_to="org")
    task("team", a, p["team"], p["team"], "team task")
    p["sealed_root"] = a.project("Leaver root", personal_owner=LEAVER,
                                 grant_to=LEAVER)
    p["sealed_area"] = a.project("Leaver area", parent=p["sealed_root"],
                                 personal_owner=LEAVER)
    task("sealed_private", a, p["sealed_root"], p["sealed_root"],
         "leaver private")
    task("sealed_area", a, p["sealed_area"], p["sealed_root"], "leaver area")
    task("handover", a, p["sealed_root"], p["sealed_root"],
         "handed over to the assignee", assignee=ASSIGNEE)
    task("handover_admin", a, p["sealed_area"], p["sealed_root"],
         "handed over to the admin", assignee=ADMIN)
    p["b_root"] = b.project("Org B", grant_to="org")
    task("b", b, p["b_root"], p["b_root"], "org B task")

    # Three hours back: older than every cursor the timeline holds, so a
    # delta returns only what a door bumped.
    with eng.begin() as c:
        c.execute(text("UPDATE pm_tasks SET updated_at = now() - interval '3 hours'"
                       " WHERE organization_id = ANY(CAST(:o AS uuid[]))"),
                  {"o": [org_a, org_b]})

    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    w.sealed = {w.t["sealed_private"], w.t["sealed_area"], w.t["handover"],
                w.t["handover_admin"]}
    return w


def _back_an_hour(w: SimpleNamespace, cursors: dict[str, str]) -> dict[str, str]:
    """Move every task stamp AND every held cursor one hour back.

    The order of the stamps does not change, so the next delta answers what
    it would answer once the horizon had passed the door's stamp.
    """
    with w.admin.begin() as c:
        c.execute(text("UPDATE pm_tasks SET updated_at = updated_at - interval '1 hour'"
                       " WHERE organization_id = ANY(CAST(:o AS uuid[]))"),
                  {"o": [w.org_a, w.org_b]})
    out = {}
    for who, cursor in cursors.items():
        stamp, _, ident = cursor.rpartition("|")
        when = datetime.fromisoformat(stamp) - timedelta(hours=1)
        out[who] = f"{when.isoformat()}|{ident}"
    return out


async def _delta(w, who: str, cursor: str | None = None) -> dict[str, Any]:
    path = "/projects/delta/tasks?limit=500"
    if cursor is not None:
        path += f"&since={quote(cursor, safe='')}"
    body = (await _ok(w, who, path)).json()
    return body


async def _timeline(w: SimpleNamespace) -> SimpleNamespace:
    """Poll, seal, poll, unseal, poll — once, for every reader."""
    from gateway.routes.projects.seal import (
        seal_personal_tree,
        unseal_personal_tree,
    )

    before = {who: await _delta(w, who) for who in READERS}
    cursors = {who: body["cursor"] for who, body in before.items()}

    sealed = await _as_app(w, w.org_a,
                           lambda db: seal_personal_tree(db, LEAVER, w.org_a))
    assert sealed == 2, "the root and the Area are both sealed"
    cursors = _back_an_hour(w, cursors)
    after_seal = {who: await _delta(w, who, cursors[who]) for who in READERS}
    snapshot_after_seal = await _delta(w, ADMIN)

    cursors = {who: body["cursor"] for who, body in after_seal.items()}
    opened = await _as_app(w, w.org_a,
                           lambda db: unseal_personal_tree(db, LEAVER, w.org_a))
    assert opened == 2, "the root and the Area are both open again"
    cursors = _back_an_hour(w, cursors)
    after_unseal = {who: await _delta(w, who, cursors[who]) for who in READERS}

    return SimpleNamespace(before=before, after_seal=after_seal,
                           snapshot_after_seal=snapshot_after_seal,
                           after_unseal=after_unseal)


@pytest.fixture(scope="module")
def world():
    admin_url = make_url(_URL)
    w = _build_world(admin_url)
    mp = pytest.MonkeyPatch()
    mp.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    mp.setenv("IDENTITY_CUTOVER", "1")
    mp.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    mp.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    # A process-wide latch an earlier suite can set. See the same lines in
    # `test_mcp_edge_authority.py`.
    mp.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    try:
        w.run = asyncio.run(_timeline(w))
        yield w
    finally:
        invalidate_access()
        mp.undo()
        w.admin.dispose()
        _drop_database(admin_url, w.dedicated)


def _rows(body: dict[str, Any]) -> set[str]:
    return {row["id"] for row in body["rows"]}


def _removed(body: dict[str, Any]) -> set[str]:
    return set(body["removed"])


# ═══════════════════════════════════════════════════════════════════════════
# Before the seal: the admin really did see the tree
# ═══════════════════════════════════════════════════════════════════════════


class TestTheStartingPoint:
    def test_the_admin_saw_the_whole_tree_before_the_seal(self, world):
        assert world.sealed <= _rows(world.run.before[ADMIN]), (
            "the admin must hold the tree in its cache before the seal, or "
            "the removal tests below prove nothing")

    def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, world):
        engine = create_engine(make_url(world.app_dsn), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)


# ═══════════════════════════════════════════════════════════════════════════
# After the seal
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSealSendsRemovals:
    def test_the_admin_gets_a_removal_for_every_sealed_task(self, world):
        body = world.run.after_seal[ADMIN]
        t = world.t
        lost = world.sealed - {t["handover_admin"]}
        assert lost <= _removed(body), (
            "a sealed task stays on the admin's open screen", lost - _removed(body))
        assert t["team"] not in _removed(body), "a live task is never removed"

    def test_no_removal_or_row_carries_a_sealed_title(self, world):
        body = world.run.after_seal[ADMIN]
        lost = world.sealed - {world.t["handover_admin"]}
        assert not (_rows(body) & lost), "a sealed task came back as a row"
        assert all(isinstance(r, str) for r in body["removed"]), (
            "a removal is a bare id, never an object")
        raw = json.dumps(body)
        for title in SEALED_TITLES:
            assert title not in raw, title

    def test_the_admins_own_hand_over_stays_a_row(self, world):
        body = world.run.after_seal[ADMIN]
        handover = world.t["handover_admin"]
        assert handover in _rows(body), "the admin lost its own hand-over"
        assert handover not in _removed(body), (
            "the admin got a removal for a task it still sees")

    def test_the_assignee_keeps_the_hand_over(self, world):
        body = world.run.after_seal[ASSIGNEE]
        handover = world.t["handover"]
        assert handover in _rows(body)
        assert not (_removed(body) & world.sealed), (
            "the assignee got a removal from the seal")

    def test_a_member_who_never_saw_the_tree_gets_nothing(self, world):
        body = world.run.after_seal[PEER]
        assert not (_rows(body) & world.sealed)
        assert not (_removed(body) & world.sealed), (
            "the seal told a member with no tie that the tree exists")

    def test_a_snapshot_does_not_list_the_sealed_tree(self, world):
        body = world.run.snapshot_after_seal
        assert body["snapshot"] is True
        assert not (_removed(body) & world.sealed)
        assert _rows(body) & world.sealed == {world.t["handover_admin"]}


# ═══════════════════════════════════════════════════════════════════════════
# After the unseal
# ═══════════════════════════════════════════════════════════════════════════


class TestTheUnsealBringsThemBack:
    def test_the_unseal_brings_the_rows_back(self, world):
        body = world.run.after_unseal[ADMIN]
        assert world.sealed <= _rows(body), (
            "an unsealed task does not come back to the admin",
            world.sealed - _rows(body))
        assert not (_removed(body) & world.sealed)

    def test_the_assignee_still_has_only_the_hand_over(self, world):
        body = world.run.after_unseal[ASSIGNEE]
        assert _rows(body) & world.sealed == {world.t["handover"]}
        assert not (_removed(body) & world.sealed)

    def test_the_member_with_no_tie_still_gets_nothing(self, world):
        body = world.run.after_unseal[PEER]
        assert not ((_rows(body) | _removed(body)) & world.sealed)


# ═══════════════════════════════════════════════════════════════════════════
# The tenant
# ═══════════════════════════════════════════════════════════════════════════


class TestTheTenantHolds:
    @pytest.mark.parametrize("step", ["after_seal", "after_unseal"])
    def test_org_b_gets_nothing_from_org_as_doors(self, world, step):
        body = getattr(world.run, step)[B_ADMIN]
        a_tasks = set(world.t.values()) - {world.t["b"]}
        assert not ((_rows(body) | _removed(body)) & a_tasks), step
        assert _rows(body) == set() and _removed(body) == set(), (
            "org A's door moved org B's feed")
