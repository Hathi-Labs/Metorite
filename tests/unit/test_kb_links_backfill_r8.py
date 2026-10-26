"""R8 — the backfill of ``kb_links`` for pages written before KB-2h (K-2).

Against a REAL Postgres, as the non-privileged role. A page that names a page
and has no page-kind row gains the rows the write path makes. A second run
changes nothing, a dry run writes nothing, and a second org is not touched.
The backfill is :func:`backlinks.backfill_org_links`, which calls the write
path's own ``sync_page_links``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.knowledge import backlinks
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_kb_backlinks_r8 import _page, _rows, _target_rows
from tests.unit.test_kb_pages_r8 import (  # noqa: F401
    CAPABILITY,
    _address,
    _app_dsn,
    _assert_non_priv,
    _principal,
    _space,
    ns,
)


@pytest.fixture
def kb(ns):  # noqa: F811 — a fixture
    yield ns
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM kb_chunks"))


def _forget(p: Any, *pages: str) -> None:
    """Make the page look as if it was written before KB-2h: no link rows."""
    with p.admin_engine.begin() as c:
        for page in pages:
            c.execute(text("DELETE FROM kb_links WHERE page_id = CAST(:p AS uuid)"),
                      {"p": page})


def _snapshot(p: Any) -> list[tuple]:
    with p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id, page_id, target_id, created_by, created_at "
            "FROM kb_links ORDER BY page_id, target_id"))]


async def _backfill(p: Any, org: str, **kw: Any) -> dict[str, int]:
    from acb_common.db import clear_tenant, release_tenant

    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            return await backlinks.backfill_org_links(org, **kw)
    finally:
        release_tenant(scope)


@_DB_GATE
async def test_the_backfill_gives_old_pages_their_rows_and_a_rerun_changes_nothing(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    space = await _space(kb, admin, "Handbook", [{"subject": "org", "level": "read"}])
    t1 = await _page(kb, admin, space, "T1")
    t2 = await _page(kb, admin, space, "T2")
    old = await _page(kb, admin, space, "Old",
                      f"[a](/knowledge/pages/{t1}) [b](/knowledge/pages/{t2}) "
                      f"```\n/knowledge/pages/{t1}\n```")
    plain = await _page(kb, admin, space, "Plain", "No link.")
    fresh = await _page(kb, admin, space, "Fresh", f"[a](/knowledge/pages/{t1})")
    assert set(_target_rows(kb, old)) == {t1, t2}
    _forget(kb, old, plain)
    assert _rows(kb, old) == 0

    got = await _backfill(kb, kb.org_a)
    assert got["linked"] == 1 and got["rows"] == 2
    assert set(_target_rows(kb, old)) == {t1, t2}
    assert _rows(kb, plain) == 0
    assert _rows(kb, fresh) == 1

    before = _snapshot(kb)
    again = await _backfill(kb, kb.org_a)
    assert again["linked"] == 0 and again["rows"] == 0
    assert _snapshot(kb) == before


@_DB_GATE
async def test_a_dry_run_and_another_org_write_nothing(kb, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _principal(_address("admin"), kb.org_a, CAPABILITY)
    admin_b = _principal(_address("admin-b"), kb.org_b, CAPABILITY)
    space = await _space(kb, admin, "Handbook", [{"subject": "org", "level": "read"}])
    space_b = await _space(kb, admin_b, "Handbook", [{"subject": "org", "level": "read"}])
    target = await _page(kb, admin, space, "Target")
    old = await _page(kb, admin, space, "Old", f"[t](/knowledge/pages/{target})")
    target_b = await _page(kb, admin_b, space_b, "TargetB")
    old_b = await _page(kb, admin_b, space_b, "OldB", f"[t](/knowledge/pages/{target_b})")
    _forget(kb, old, old_b)

    dry = await _backfill(kb, kb.org_a, dry_run=True)
    assert dry["linked"] == 1 and _rows(kb, old) == 0

    await _backfill(kb, kb.org_a)
    assert _rows(kb, old) == 1
    assert _rows(kb, old_b) == 0, "the backfill of org A wrote a row for org B"
