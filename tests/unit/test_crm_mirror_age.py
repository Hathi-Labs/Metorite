"""The age of the mirror of each module, for the read tools (WS-53 CRM-Q30c).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30c items 1 to 3.
Task 3868.

Database-free, so it never skips. A fake session answers the SQL of the two
readers that the route reuses: ``runs.read_cursors`` and ``holds.read``. The
fake proves the shape that the route builds from their rows. It cannot prove
the SQL itself. That needs a real Postgres (R8), and no R8 suite covers the
route yet.

1. The flag: ``CRM_MIRROR_AGE_ORGS`` has one reader,
   ``flags.mirror_age_allowed``, in the grammar of ``CRM_ORGS``. The default
   is empty.
2. The route: an org off the list gets an empty list, and no session opens.
   A Native org gets an empty list.
3. The states of a module: fresh, 3 hours old, 3 days old, held, unavailable,
   never read, and held with no cursor row.
4. The shared key rule of ``_cursor_key``, which ``_short_by_keys`` uses too.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.crm_sync import engine
from gateway.routes.crm import flags, sync

from tests.unit._crm_fakes import crm_switch_on

_ORG = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"
_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "crm_mirror_age_orgs", value, raising=False)


# ── 1. The flag ──────────────────────────────────────────────────────────────


def test_the_settings_default_is_empty() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["crm_mirror_age_orgs"].default == ""


def test_an_empty_list_allows_no_org(monkeypatch: pytest.MonkeyPatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "")
    assert flags.mirror_age_allowed(_ORG) is False


def test_a_listed_org_is_allowed_and_another_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, f" {_ORG.upper()} ")
    assert flags.mirror_age_allowed(_ORG) is True
    assert flags.mirror_age_allowed(_OTHER) is False


def test_star_needs_an_org_and_the_crm_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    assert flags.mirror_age_allowed(_ORG) is True
    assert flags.mirror_age_allowed(None) is False
    crm_switch_on(monkeypatch, _OTHER)
    assert flags.mirror_age_allowed(_ORG) is False


def test_no_tenant_route_may_write_the_flag() -> None:
    """The exact name is on the deny list, so the fence holds if the ``CRM_``
    prefix ever narrows. The env example lists the flag, empty."""
    from acb_common.env_guard import PLATFORM_ENV_NAMES, is_platform_env

    assert "CRM_MIRROR_AGE_ORGS" in PLATFORM_ENV_NAMES
    assert is_platform_env(" crm_mirror_age_orgs ")
    example = Path(__file__).resolve().parents[2] / ".env.example"
    assert "\nCRM_MIRROR_AGE_ORGS=\n" in example.read_text(encoding="utf-8")


def test_the_flag_has_one_reader() -> None:
    """Only ``flags.py`` names the setting. The chat tool asks that reader."""
    root = Path(__file__).resolve().parents[2]
    readers = sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for base in ("apps", "packages")
        for p in (root / base).rglob("*.py")
        if "crm_mirror_age_orgs" in p.read_text(encoding="utf-8", errors="ignore")
    )
    assert readers == [
        "apps/services/gateway/gateway/routes/crm/flags.py",
        "packages/acb_common/acb_common/flag_registry.py",
    ]


# ── The fake session ─────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Db:
    """Answers the SQL of the mode check, ``runs.read_cursors`` and
    ``holds.read``. Any other statement fails the test."""

    def __init__(self, cursors: dict[str, dict[str, Any] | None], *,
                 holds: dict[str, Any] | None = None, mode: str = "mirror") -> None:
        self.cursors, self.holds, self.mode = cursors, holds, mode
        self.sql: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        self.sql.append(sql)
        self.params.append(dict(params or {}))
        if "FROM crm_sync_cursors" in sql:
            return _Result([
                SimpleNamespace(module=module, last_pulled_at=None, cursor_state=state,
                                initial_done_at=None, last_reconciled_at=None)
                for module, state in self.cursors.items()
            ])
        if sql.startswith("SELECT sync_holds FROM crm_settings"):
            return _Result([] if self.holds is None else [SimpleNamespace(sync_holds=self.holds)])
        if sql.startswith("SELECT mode FROM crm_settings"):
            return _Result([SimpleNamespace(mode=self.mode)])
        raise AssertionError(f"unexpected SQL: {sql}")


def _bind(monkeypatch: pytest.MonkeyPatch, db: _Db) -> list[int]:
    opened: list[int] = []

    @contextlib.asynccontextmanager
    async def session():
        opened.append(1)
        yield db

    monkeypatch.setattr(sync, "_tenant_session", session)
    return opened


def _iso(delta: timedelta) -> str:
    return (_NOW - delta).isoformat()


def _hold(held: bool = True) -> dict[str, Any]:
    return {"held": held, "by": "auto", "count": 9000, "at": _NOW.isoformat()}


#: One cursor row of each state, and rows that are no module of a tool.
CURSORS: dict[str, dict[str, Any] | None] = {
    "zoho:deal": {"in_progress": False, "checked_at": _iso(timedelta(minutes=20))},
    "zoho:module:Quotes": {"in_progress": False, "checked_at": _iso(timedelta(hours=3))},
    "zoho:module:Invoices": {"in_progress": False, "checked_at": _iso(timedelta(days=3))},
    "zoho:module:Sales_Orders": {"in_progress": False, "checked_at": _iso(timedelta(hours=1))},
    # Unavailable: the stamp is the time of the refused ask, not a read.
    "zoho:module:Vendors": {"unavailable": True, "reason": "hidden",
                            "checked_at": _iso(timedelta(minutes=5))},
    # Never read: a first import in progress keeps no stamp.
    "zoho:module:Cases": {"in_progress": True, "page": 3},
    "zoho:company": None,
    # Not a module of a tool: a deleted list, an activity, a state row.
    "zoho:module:Quotes:deleted": {"checked_at": _iso(timedelta(minutes=1))},
    "zoho:note": {"checked_at": _iso(timedelta(minutes=1))},
    "zoho:field_rules": {"rules": {}},
}

HOLDS = {
    "module:Sales_Orders": _hold(),           # held, with a cursor row
    "module:Purchase_Orders": _hold(),        # held, with no cursor row
    "module:Products": _hold(held=False),     # an admin turned it on again
    "activities": _hold(),                    # no tool reads it
    "tasks": _hold(),                         # no tool reads it
}


async def _ages(monkeypatch: pytest.MonkeyPatch, db: _Db) -> dict[str, Any]:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, _ORG)
    _bind(monkeypatch, db)
    rows = await sync.get_mirror_age(user=SimpleNamespace(organization_id=_ORG))
    return {row.key: row for row in rows}


# ── 2. The route, dark and Native ────────────────────────────────────────────


async def test_an_org_off_the_list_gets_an_empty_list_and_no_session(monkeypatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, _OTHER)
    db = _Db(CURSORS, holds=HOLDS)
    opened = _bind(monkeypatch, db)
    assert await sync.get_mirror_age(user=SimpleNamespace(organization_id=_ORG)) == []
    assert opened == [] and db.sql == []


async def test_a_native_org_gets_an_empty_list(monkeypatch) -> None:
    db = _Db(CURSORS, holds=HOLDS, mode="native")
    assert await _ages(monkeypatch, db) == {}
    assert len(db.sql) == 1, "the mode check, then nothing"


async def test_each_statement_names_the_org_of_the_session(monkeypatch) -> None:
    db = _Db(CURSORS, holds=HOLDS)
    await _ages(monkeypatch, db)
    assert len(db.sql) == 3
    assert all(_ORG in p.values() for p in db.params)
    assert any(p.get("prefix") == "zoho:%" for p in db.params)


# ── 3. The states of a module ────────────────────────────────────────────────


async def test_a_fresh_and_an_old_module_carry_their_stamp(monkeypatch) -> None:
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    deals, quotes, invoices = ages["deals"], ages["module:Quotes"], ages["module:Invoices"]
    assert deals.checked_at == _NOW - timedelta(minutes=20)
    assert quotes.checked_at == _NOW - timedelta(hours=3)
    assert invoices.checked_at == _NOW - timedelta(days=3)
    for row in (deals, quotes, invoices):
        assert row.held is False and row.unavailable is False
    assert (deals.api_name, quotes.api_name, invoices.api_name) == ("Deals", "Quotes", "Invoices")


async def test_a_held_module_says_held_and_keeps_its_stamp(monkeypatch) -> None:
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    row = ages["module:Sales_Orders"]
    assert row.held is True and row.unavailable is False
    assert row.checked_at == _NOW - timedelta(hours=1)


async def test_a_held_module_with_no_cursor_row_is_listed(monkeypatch) -> None:
    """Task 3729: a first import over 5,000 records is held before any read,
    so the module has no cursor row at all."""
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    row = ages["module:Purchase_Orders"]
    assert (row.held, row.unavailable, row.checked_at) == (True, False, None)
    assert row.api_name == "Purchase_Orders"


async def test_an_unavailable_module_has_no_stamp(monkeypatch) -> None:
    """The stamp of an unavailable row is the refused ask, never a read."""
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    row = ages["module:Vendors"]
    assert (row.unavailable, row.held, row.checked_at) == (True, False, None)


async def test_a_module_never_read_has_no_stamp(monkeypatch) -> None:
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    for key in ("module:Cases", "organizations"):
        row = ages[key]
        assert (row.checked_at, row.held, row.unavailable) == (None, False, False), key
    assert ages["organizations"].api_name == "Accounts"


async def test_only_the_modules_of_a_tool_are_listed(monkeypatch) -> None:
    """A deleted list, an activity, a state row, a released hold and the
    held keys that no tool reads are not in the list."""
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    assert sorted(ages) == [
        "deals", "module:Cases", "module:Invoices", "module:Purchase_Orders",
        "module:Quotes", "module:Sales_Orders", "module:Vendors", "organizations",
    ]


async def test_an_org_with_no_settings_row_has_no_hold(monkeypatch) -> None:
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=None))
    assert "module:Purchase_Orders" not in ages
    assert ages["module:Sales_Orders"].held is False


async def test_the_answer_holds_times_and_marks_only(monkeypatch) -> None:
    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    for row in ages.values():
        assert set(row.model_dump()) == {"key", "api_name", "checked_at", "held", "unavailable"}


async def test_the_tool_reads_the_answer_of_the_route(monkeypatch) -> None:
    """One join of the two halves: the JSON of the route goes to the read
    tool through a fake ``_get``. It proves the key and the time format."""
    from acb_skills import crm_tools as T

    ages = await _ages(monkeypatch, _Db(CURSORS, holds=HOLDS))
    wire = [row.model_dump(mode="json") for row in ages.values()]
    records = {"rows": [{"id": _ORG, "name": "R-1"}], "total": 1, "field_defs": []}
    routes = {
        T.SHORT_READS_PATH: [], T.MIRROR_AGE_PATH: wire,
        "/crm/modules/Invoices/records": records,
        "/crm/modules/Purchase_Orders/records": records,
        "/crm/modules/Quotes/records": records,
    }

    async def get(path: str, params: dict[str, Any] | None = None, **_kw: Any) -> Any:
        return routes[path]

    monkeypatch.setattr(T, "_get", get)
    monkeypatch.setattr(T, "_utc_now", lambda: _NOW)
    monkeypatch.setattr(T, "_run_org", lambda: _ORG)
    out = await T.crm_list_module_records("Invoices")
    assert out.endswith("\nInvoices were last read from Zoho 3 days ago.")
    held = await T.crm_list_module_records("Purchase_Orders")
    assert held.endswith("\nPurchase_Orders are held, so they may be old.")
    quotes = await T.crm_list_module_records("Quotes")
    assert quotes.endswith("\nQuotes were last read from Zoho 3 hours ago.")


# ── 4. The shared key rule ───────────────────────────────────────────────────


@pytest.mark.parametrize(("module", "key"), [
    ("zoho:deal", "deals"),
    ("zoho:company", "organizations"),
    ("zoho:module:Invoices", "module:Invoices"),
    ("zoho:module:Invoices:deleted", None),
    ("zoho:deal:deleted", None),
    ("zoho:note", None),
    ("zoho:task", None),
    ("zoho:field_rules", None),
    ("zoho:module_counts", None),
    ("other:deal", None),
])
def test_the_key_of_a_cursor_row(module: str, key: str | None) -> None:
    assert sync._cursor_key(module, engine) == key


def test_the_short_reads_keep_their_key_rule() -> None:
    """``_short_by_keys`` reads its keys through ``_cursor_key`` now. The
    gap of a core entity and a module stays, and each other row stays out."""
    def row(state: dict[str, Any]) -> Any:
        return SimpleNamespace(state=state)

    gap = {engine.SHORT_BY_KEY: 20}
    cursors = {
        "zoho:contact": row(gap),
        "zoho:module:Payment_Made": row({engine.SHORT_BY_KEY: 1944}),
        "zoho:contact:deleted": row(gap),
        "zoho:note": row(gap),
        "zoho:field_rules": row(gap),
        "zoho:deal": row({engine.SHORT_BY_KEY: 0}),
    }
    assert sync._short_by_keys(cursors, engine) == {
        "contacts": 20, "module:Payment_Made": 1944,
    }
