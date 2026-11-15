"""WS-STU I9b-1 — the scope match of a connection need, on a real database (R8).

Spec: ``project-docs/specs/ai_studio_rows/I9b-1.md``. Code:
``gateway/connections/store.py::met_providers`` (reads ``scopes``, never the
blob), ``gateway/package_host/resolve.py::need_reason`` (the one rule) and
``gateway/routes/studio/invoke.py::_needs_connection`` (the door).

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds each statement. It reuses the world
of ``test_studio_connections_whose_r8.py``: org A has Owen (publisher), Ada (an
admin) and Mel (a member). Org B has Zed. ``STUDIO_CONNECTION_GATE_ORGS`` is on.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=<the DSN of your private database>
    uv run pytest tests/unit/test_studio_scope_match_r8.py -v -rs -p no:randomly

Without the database every test here SKIPS, and the run reads green.

**The mutations of I9b-1.** Each ran on 2026-11-15, after a commit.

* X1. ``need_reason``: ``len(row) >= len(scopes)`` for the subset test.
  ``test_r8_a_row_of_the_same_size_but_other_scopes_is_unmet`` goes red.
* X2. ``need_reason``: fold the case. ``test_r8_scopes_are_compared_literally`` goes red.
* X3. ``need_reason``: drop ``not org_approved``.
  ``test_r8_an_org_row_meets_an_org_need_only_on_an_approved_org_install`` goes red.
* X4. ``_MET_SQL``: drop ``AND member_email = :member``.
  ``test_r8_the_row_of_another_member_never_counts_scopes_included`` goes red.
* X5. ``need_reason``: reason ``missing`` for a fewer-scopes row.
  ``test_r8_a_member_row_with_fewer_scopes_is_reconnect_and_more_is_met`` goes red.
* X6. ``need_reason``: drop the empty-scopes rule.
  ``test_r8_a_row_with_no_scopes_never_meets_a_need_with_scopes`` goes red.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.connections import store as connections_store
from gateway.routes.studio import invoke
from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_studio_connections_whose_r8 import (
    _DENIED,
    _WAITS,
    TALLY,
    _door,
    _installed,
    _live,
    _run,
)
from tests.unit.test_studio_connections_whose_r8 import world as _whose_world  # noqa: F401
from tests.unit.test_studio_package_approval_routes_r8 import ADA, MEL, OWEN, ZED
from tests.unit.test_studio_package_approval_routes_r8 import (  # noqa: F401
    world as _base_world,
)

pytestmark = _DB_GATE


@pytest.fixture
def world(_whose_world):  # noqa: F811
    """The world of ``test_studio_connections_whose_r8.py`` (gate on, spy on the list action)."""
    return _whose_world

RECONNECT = {"code": invoke.NEEDS_CONNECTION, "reason": "reconnect",
             "message": f"Reconnect {TALLY}: your connection does not grant what this app needs."}


def _literal(scopes: list[str]) -> str:
    return "{" + ",".join('"' + x + '"' for x in scopes) + "}"


def _row(world: Any, org: str, scopes: list[str], *, member: str | None = None,
         owning_app: str = "integrations", provider: str = TALLY) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, member_email, status, scopes) VALUES (CAST(:o AS uuid), :p, :a, "
            ":k, :m, 'connected', CAST(:s AS text[]))"),
            {"o": org, "p": provider, "a": owning_app, "k": "member" if member else "org",
             "m": member.lower() if member else None, "s": _literal(scopes)})


def _need(whose: str, scopes: list[str]) -> list[Any]:
    return [{"name": TALLY, "as": whose, "scopes": scopes}]


def test_r8_a_member_row_with_fewer_scopes_is_reconnect_and_more_is_met(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        pid = await _installed(world.a, OWEN, _need("member", ["read", "write"]))
        out = [await _door(world.a, MEL, pid)]                  # no row: missing
        _row(world, world.a, ["read"], member=MEL)
        out.append(await _door(world.a, MEL, pid))              # fewer: reconnect
        _row(world, world.a, ["read", "write", "admin"], member=ADA)
        out.append(await _door(world.a, MEL, pid))              # Ada's row never counts
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE integration_connections SET scopes = "
                           "CAST('{read,write,admin}' AS text[]) WHERE member_email = :m"),
                      {"m": MEL.lower()})
        out.append(await _door(world.a, MEL, pid))              # more than the need: met
        return out

    missing, fewer, other, more = _run(world, go)
    base = {"provider": TALLY, "as": "member"}
    assert missing == (409, {**_DENIED, **base})
    assert fewer == (409, {**RECONNECT, **base}) and other == fewer
    assert more[0] == 200 and world.ran == [1]


def test_r8_scopes_are_compared_literally(world) -> None:
    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        pid = await _installed(world.a, OWEN, _need("member", ["Mail.Read"]))
        _row(world, world.a, ["mail.read"], member=MEL)
        first = await _door(world.a, MEL, pid)
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE integration_connections SET scopes = "
                           "CAST('{Mail.Read}' AS text[]) WHERE member_email = :m"),
                      {"m": MEL.lower()})
        return first, await _door(world.a, MEL, pid)

    first, second = _run(world, go)
    assert first[0] == 409 and first[1]["reason"] == "reconnect"
    assert second[0] == 200


def test_r8_a_row_of_the_same_size_but_other_scopes_is_unmet(world) -> None:
    async def go() -> tuple[int, Any]:
        pid = await _installed(world.a, OWEN, _need("member", ["a", "b"]))
        _row(world, world.a, ["c", "d"], member=MEL)
        return await _door(world.a, MEL, pid)

    status, detail = _run(world, go)
    assert status == 409 and detail["reason"] == "reconnect" and world.ran == []


def test_r8_a_row_with_no_scopes_never_meets_a_need_with_scopes(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        scoped = await _installed(world.a, OWEN, _need("member", ["read"]))
        plain = await _installed(world.a, OWEN, [{"name": TALLY, "as": "member"}])
        _row(world, world.a, [], member=MEL)
        return [await _door(world.a, MEL, scoped), await _door(world.a, MEL, plain)]

    scoped, plain = _run(world, go)
    assert scoped[0] == 409 and scoped[1]["reason"] == "reconnect"
    assert plain[0] == 200, "a need with no scopes is met by any connected row"


def test_r8_two_rows_are_never_merged_into_one_account(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        pid = await _installed(world.a, OWEN, _need("member", ["a", "b"]))
        _row(world, world.a, ["a"], member=MEL)
        _row(world, world.a, ["b"], member=MEL, owning_app="calendar")
        out = [await _door(world.a, MEL, pid)]
        _row(world, world.a, ["a", "b"], member=MEL, owning_app="mail")
        out.append(await _door(world.a, MEL, pid))
        return out

    split, whole = _run(world, go)
    assert split[0] == 409 and split[1]["reason"] == "reconnect"
    assert whole[0] == 200


def test_r8_an_org_row_meets_an_org_need_only_on_an_approved_org_install(world) -> None:
    async def go() -> dict[str, Any]:
        pid = await _installed(world.a, OWEN, _need("org", ["read"]))
        _row(world, world.a, ["read", "write"])
        approved = await _door(world.a, MEL, pid)               # approved ORG install: met
        draft = await _door(world.a, OWEN, pid)                 # the draft of the owner
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE app_versions SET review_status = 'pending' "
                           "WHERE app_id = CAST(:p AS uuid)"), {"p": pid})
        pending = await _door(world.a, MEL, pid)
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE integration_connections SET scopes = '{}'"
                           " WHERE scope_kind = 'org' AND provider = :p"), {"p": TALLY})
            c.execute(text("UPDATE app_versions SET review_status = 'approved' "
                           "WHERE app_id = CAST(:p AS uuid)"), {"p": pid})
        empty = await _door(world.a, MEL, pid)
        return {"approved": approved, "draft": draft, "pending": pending, "empty": empty}

    got = _run(world, go)
    base = {"provider": TALLY, "as": "org"}
    assert got["approved"][0] == 200
    assert got["draft"] == (409, {**_WAITS, **base})
    assert got["pending"] == (409, {**_WAITS, **base})
    assert got["empty"][0] == 409 and got["empty"][1]["reason"] == "reconnect"


def test_r8_a_private_install_or_a_draft_with_an_org_need_asks_an_admin(world) -> None:
    async def go() -> tuple[int, Any]:
        pid = await _live(world.a, OWEN, _need("org", ["read"]))
        _row(world, world.a, ["read"])
        return await _door(world.a, OWEN, pid)

    assert _run(world, go) == (409, {**_WAITS, "provider": TALLY, "as": "org"})


def test_r8_org_b_never_meets_the_need_of_org_a(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        pid = await _installed(world.a, OWEN, _need("org", ["read"]))
        _row(world, world.b, ["read", "write"])                 # org B holds the row
        out = [await _door(world.a, MEL, pid)]
        _row(world, world.b, ["read"], member=MEL)               # Mel's address in org B
        out.append(await _door(world.a, MEL, pid))
        return out

    assert _run(world, go) == [(409, {**_DENIED, "provider": TALLY, "as": "org"})] * 2


def test_r8_the_row_of_another_member_never_counts_scopes_included(world) -> None:
    async def go() -> dict[str, Any]:
        pid = await _installed(world.a, OWEN, _need("member", ["read"]))
        _row(world, world.a, ["read", "write"], member=ADA)
        _row(world, world.b, ["read"], member=ZED)
        mel = await _door(world.a, MEL, pid)
        ada = await _door(world.a, ADA, pid)
        return {"mel": mel, "ada": ada}

    got = _run(world, go)
    assert got["mel"] == (409, {**_DENIED, "provider": TALLY, "as": "member"})
    assert got["ada"][0] == 200
    blob = repr(got["mel"]).lower()
    assert ADA.lower() not in blob and "write" not in blob


def test_r8_the_store_reads_the_scopes_of_each_row_and_no_blob(world) -> None:
    async def go() -> Any:
        _row(world, world.a, ["a"])
        _row(world, world.a, ["b"], owning_app="x")
        _row(world, world.a, ["m"], member=MEL)
        _row(world, world.a, ["z"], member=ADA)
        async with tenant_session(world.a) as db:
            return await connections_store.met_providers(db, world.a, MEL)

    org_met, member_met = _run(world, go)
    assert set(org_met[TALLY]) == {frozenset({"a"}), frozenset({"b"})}
    assert member_met[TALLY] == (frozenset({"m"}),)


def test_the_met_sql_names_no_credentials_column() -> None:
    sql = connections_store._MET_SQL.lower()
    assert "scopes" in sql and "credentials" not in sql and "blob" not in sql

