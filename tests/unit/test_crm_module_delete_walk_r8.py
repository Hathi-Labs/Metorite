"""R8 — the delete walk of a generic module, on a real Postgres (WS-53 task
3870, ``crm_platform.md`` §18.7, CRM-Q6a).

``tests/unit/test_crm_module_delete_walk.py`` proves the rules of the engine
with a fake session, and a fake agrees with any SQL. This file runs the whole
sync (``engine.run_org``) against the fake Zoho of the CRM-Q6a suite, through
``tenant_session`` as the non-privileged role of the phase-4 rehearsal, with
two organizations. So the seen door, the two reads of ``_drop_stale`` and the
delete door all run on Postgres under FORCE row level security.

1. A record that Zoho deleted, and that its deleted list never named, stays
   after the first complete weekly read and goes after the second. The seen
   stamp moves no ``updated_at``.
2. A record that a weekly read lists again keeps its row.
3. A weekly read that stops at the reserve, or that Zoho refuses, deletes
   nothing. The next complete read deletes it.
4. The walk of org A never touches a row of org B, also a row with the same
   ``ext_id`` and a row that org B's Zoho does not hold.
5. Migration 499 adds ``ext_seen_at`` as a nullable column with no default.
   A second run changes nothing, and the table keeps FORCE row level security.

Each test turns ``CRM_MODULE_REREAD_ORGS`` on for org A only. The 24-hour gap
between two strikes (``engine.MODULE_WALK_GAP``) is 0 here, because the walks
of one test run seconds apart. The hermetic file proves the gap.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import _exec_file
from tests.unit.test_crm_module_records_r8 import (  # noqa: F401
    ModZoho,
    _age_module_reads,
    _connect_all,
    mz,
)
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _assert_non_priv,
    _q,
    _the_loop_is_not_stopping,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_COLUMN = re.compile(r"ALTER TABLE crm_module_records ADD COLUMN IF NOT EXISTS ext_seen_at")

PRODUCTS = "module:Products"


@pytest.fixture
def walk(mz, monkeypatch):
    """The CRM-Q6a fixture, the weekly read on for org A only, and no gap
    between two strikes."""
    from acb_common import get_settings
    from gateway.crm_sync import engine

    p, _a, _b = mz
    monkeypatch.setattr(get_settings(), "crm_module_reread_orgs", p.org_a, raising=False)
    monkeypatch.setattr(engine, "MODULE_WALK_GAP", timedelta(0))
    return mz


def _seed(z: ModZoho, products: int = 3) -> None:
    """One company, *products* Products (p1, p2, ...) and one Install_Base."""
    z.add("Accounts", "a1", 1, Account_Name=f"{z.tag}-Company")
    for n in range(1, products + 1):
        z.add("Products", f"p{n}", 9 + n, Product_Name=f"{z.tag}-Item {n}")
    z.add("Install_Base", "i1", 30, Name=f"{z.tag}-Install")


async def _run(org: str) -> Any:
    from gateway.crm_sync import engine

    result = await engine.run_org(org)
    assert (result.status, result.error) == ("ok", None), result
    return result


def _ids(p: Any, org: str, module: str = "Products") -> set[str]:
    return {r[0] for r in _q(
        p, "SELECT ext_id FROM crm_module_records WHERE organization_id = "
           "CAST(:o AS uuid) AND module = :m", o=org, m=module)}


def _stamps(p: Any, org: str, module: str = "Products") -> dict[str, Any]:
    """``ext_id`` -> ``(ext_seen_at, updated_at)``."""
    return {r[0]: (r[1], r[2]) for r in _q(
        p, "SELECT ext_id, ext_seen_at, updated_at FROM crm_module_records WHERE "
           "organization_id = CAST(:o AS uuid) AND module = :m", o=org, m=module)}


def _state(p: Any, org: str, module: str = "Products") -> dict[str, Any]:
    rows = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                 "CAST(:o AS uuid) AND module = :m", o=org, m=f"zoho:module:{module}")
    return dict(rows[0][0] or {}) if rows else {}


def _due(p: Any, org: str, module: str = "Products") -> None:
    """The last full read of the module is 8 days old: a weekly read is due."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET last_reconciled_at = now() - interval '8 days' "
            "WHERE organization_id = CAST(:o AS uuid) AND module = :m"),
            {"o": org, "m": f"zoho:module:{module}"})


async def _count_again(p: Any, org: str) -> None:
    """One run that reads the Zoho count of each module again. The walk takes
    the stored count and spends no credit, so the count must be fresh."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = jsonb_set(cursor_state, "
            "'{at}', '{}'::jsonb) WHERE organization_id = CAST(:o AS uuid) "
            "AND module = 'zoho:module_counts'"), {"o": org})
    await _run(org)


async def _import_and_lose(p: Any, org: str, z: ModZoho, gone: str, products: int = 3) -> None:
    """Import, then Zoho deletes *gone* and its deleted list never names it.
    The run after that reads the new count.

    The rows of the import move their ``created_at`` one day back. A row with
    no stamp counts by it, against the start of a walk by the gateway's
    clock, so a skew between the two clocks cannot turn a test red."""
    _seed(z, products)
    await _connect_all(p, org, z)
    await _run(org)
    assert gone in _ids(p, org)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_module_records SET created_at = created_at - interval '1 day' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
    z.drop("Products", gone, tombstone=False)
    await _count_again(p, org)


async def _walk(p: Any, org: str) -> Any:
    _due(p, org)
    return await _run(org)


# ── 1. Two strikes ───────────────────────────────────────────────────────────


async def test_a_record_that_vanished_from_zoho_goes_after_two_full_reads(walk) -> None:
    p, a, _b = walk
    await _import_and_lose(p, p.org_a, a, "p2")
    assert {k: v[0] for k, v in _stamps(p, p.org_a).items()} == {
        "p1": None, "p2": None, "p3": None}, "the import stamps nothing"
    before = _stamps(p, p.org_a)

    first = await _walk(p, p.org_a)
    assert _ids(p, p.org_a) == {"p1", "p2", "p3"}, "one miss deleted a row"
    stamps = _stamps(p, p.org_a)
    assert stamps["p2"][0] is None, "strike one"
    assert stamps["p1"][0] is not None and stamps["p3"][0] is not None
    assert stamps["p1"][1] == before["p1"][1], "the stamp moved updated_at"
    assert "seen_prev" in _state(p, p.org_a)
    assert first.counts[PRODUCTS]["deleted"] == 0

    second = await _walk(p, p.org_a)
    assert _ids(p, p.org_a) == {"p1", "p3"}
    assert second.counts[PRODUCTS]["deleted"] == 1
    assert _ids(p, p.org_a, "Install_Base") == {"i1"}, "another module is untouched"


# ── 2. Seen again ────────────────────────────────────────────────────────────


async def test_a_record_seen_again_keeps_its_row(walk) -> None:
    p, a, _b = walk
    await _import_and_lose(p, p.org_a, a, "p2")
    await _walk(p, p.org_a)  # strike one
    a.add("Products", "p2", 11, Product_Name="A-Item 2")  # Zoho shows it again
    await _count_again(p, p.org_a)
    await _walk(p, p.org_a)
    assert _stamps(p, p.org_a)["p2"][0] is not None, "the read listed it"
    a.drop("Products", "p2", tombstone=False)
    await _count_again(p, p.org_a)
    await _walk(p, p.org_a)
    assert "p2" in _ids(p, p.org_a), "a miss after a sighting is strike one"
    await _walk(p, p.org_a)
    assert "p2" not in _ids(p, p.org_a)


# ── 3. A read that does not end ──────────────────────────────────────────────


@pytest.mark.parametrize("stop", ["reserve", "refused"])
async def test_a_stopped_or_refused_read_drops_nothing(walk, monkeypatch, stop) -> None:
    from gateway.crm_sync import engine

    p, a, _b = walk
    # Four Products after the loss: two pages of 2.
    await _import_and_lose(p, p.org_a, a, "p2", products=5)
    await _walk(p, p.org_a)  # strike one

    if stop == "reserve":
        def low_after_first_page(_reads: int) -> None:
            monkeypatch.setattr(engine, "MODULES_RESERVE", 10**9)
            a.on_read.pop("Products", None)

        a.on_read["Products"] = low_after_first_page
    else:
        a.faults["Products"] = (500, {"code": "INTERNAL_ERROR", "message": "boom"})
    stopped = await _walk(p, p.org_a)
    assert "p2" in _ids(p, p.org_a), "a read that did not end deleted a row"
    assert stopped.counts[PRODUCTS]["deleted"] == 0
    if stop == "reserve":
        state = _state(p, p.org_a)
        assert state["in_progress"] and state["seen_count"] == 2

    monkeypatch.setattr(engine, "MODULES_RESERVE", 20)
    a.faults.pop("Products", None)
    _age_module_reads(p, p.org_a)  # past the retry wait of a refused module
    done = await _run(p.org_a) if stop == "reserve" else await _walk(p, p.org_a)
    assert "p2" not in _ids(p, p.org_a), "the complete read is strike two"
    assert done.counts[PRODUCTS]["deleted"] == 1


# ── 4. Tenancy ───────────────────────────────────────────────────────────────


async def test_the_walk_of_org_a_never_touches_org_b(walk, app_engine) -> None:
    p, a, b = walk
    _assert_non_priv(app_engine)
    _seed(b)  # the SAME Zoho ids in org B
    await _connect_all(p, p.org_b, b)
    await _run(p.org_b)
    with p.admin_engine.begin() as c:  # a row of B that B's Zoho does not hold
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, "
            "ext_id, name, created_at) VALUES (CAST(:o AS uuid), 'Products', 'zoho', "
            "'p9', 'B-old', now() - interval '30 days')"), {"o": p.org_b})
    b_before = _stamps(p, p.org_b)

    await _import_and_lose(p, p.org_a, a, "p2")
    for _ in range(2):
        await _walk(p, p.org_a)
    assert _ids(p, p.org_a) == {"p1", "p3"}
    assert _stamps(p, p.org_b) == b_before, "org A's walk wrote a row of org B"
    assert _ids(p, p.org_b) == {"p1", "p2", "p3", "p9"}

    # Org B is off the flag: its due reads walk nothing.
    for _ in range(2):
        await _walk(p, p.org_b)
    assert _ids(p, p.org_b) == {"p1", "p2", "p3", "p9"}
    assert all(seen is None for seen, _u in _stamps(p, p.org_b).values())
    assert "seen_prev" not in _state(p, p.org_b)


# ── 5. The migration ─────────────────────────────────────────────────────────


def test_the_migration_adds_a_nullable_stamp_and_replays(walk) -> None:
    p, _a, _b = walk
    found = [f for f in _MIGRATIONS.glob("*.sql")
             if f.name != "schema.generated.sql"
             and _COLUMN.search(f.read_text(encoding="utf-8"))]
    assert len(found) == 1, [f.name for f in found]
    # The ladder applied it once already. A second run must change nothing.
    with p.admin_engine.begin() as c:
        _exec_file(c, str(found[0]))
    column = _q(p, "SELECT a.attnotnull, format_type(a.atttypid, a.atttypmod), "
                   "a.atthasdef FROM pg_attribute a WHERE a.attrelid = "
                   "to_regclass('crm_module_records') AND a.attname = 'ext_seen_at' "
                   "AND NOT a.attisdropped")
    assert column == [(False, "timestamp with time zone", False)], "nullable, no default"
    forced = _q(p, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                   "WHERE oid = to_regclass('crm_module_records')")
    assert forced == [(True, True)]
    policy = _q(p, "SELECT qual IS NOT NULL, with_check IS NOT NULL FROM pg_policies "
                   "WHERE tablename = 'crm_module_records'")
    assert policy and all(row == (True, True) for row in policy)
