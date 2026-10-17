"""R8 — the short reads route of the read tools (WS-53 CRM-Q30b).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q30b items 1, 2
and 8.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal. The seed goes in as the
superuser.

1. A member who is not an admin reads the short reads of the org.
2. With no count of a run, Zoho's count is ours plus the gap, as the Sync
   health card says. With a count of the last run, the route uses it.
3. A whole read, a deleted list and an activity cursor give no row.
4. A Native org gets an empty list. Org B reads none of org A.
5. ``crm_aggregate`` adds the line from this route.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_aggregate_fields_r8 import _call
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_TABLES = ("crm_sync_runs", "crm_sync_cursors", "crm_module_records", "crm_contacts",
           "crm_source_profiles", "integration_connections", "crm_settings")

PATH = "/crm/sync/short-reads"


def _member(org: str) -> Any:
    """A member with the CRM feature and no admin permission."""
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email="member@crm-q30b-r8.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:crm"]), organization_id=org)


def _settings(c: Any, org: str, mode: str) -> None:
    c.execute(text(
        "INSERT INTO crm_settings (organization_id, mode, access) "
        "VALUES (CAST(:o AS uuid), :m, 'everyone') ON CONFLICT (organization_id) "
        "DO UPDATE SET mode = :m, access = 'everyone'"), {"o": org, "m": mode})


def _cursor(c: Any, org: str, module: str, state: dict[str, Any]) -> None:
    c.execute(text(
        "INSERT INTO crm_sync_cursors (organization_id, module, last_run_at, last_status, "
        "cursor_state) VALUES (CAST(:o AS uuid), :m, now(), 'ok', CAST(:s AS jsonb))"),
        {"o": org, "m": module, "s": json.dumps(state)})


def _records(c: Any, org: str, module: str, n: int) -> None:
    for i in range(n):
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, ext_id, "
            "name, fields) VALUES (CAST(:o AS uuid), :m, 'zoho', :e, :e, '{}'::jsonb)"),
            {"o": org, "m": module, "e": f"{module}-{i}"})


@pytest.fixture
def q(promoted, monkeypatch):
    from acb_common import get_settings

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "crm_mirror", True, raising=False)
    monkeypatch.setattr(get_settings(), "crm_mirror_orgs", "*", raising=False)
    a, b = promoted.org_a, promoted.org_b
    with promoted.admin_engine.begin() as c:
        _settings(c, a, "mirror")
        _settings(c, b, "native")
        _records(c, a, "Payment_Made", 3)
        _cursor(c, a, "zoho:module:Payment_Made", {"short_by": 5})
        _cursor(c, a, "zoho:contact", {"short_by": 2})
        _cursor(c, a, "zoho:module:Invoices", {"short_by": 0})       # a whole read
        _cursor(c, a, "zoho:note", {"short_by": 9})                  # an activity
        _cursor(c, a, "zoho:contact:deleted", {"short_by": 4})       # a deleted list
        _cursor(c, b, "zoho:module:Payment_Made", {"short_by": 7})   # org B is Native
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (a, b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def _get(p: Any, org: str) -> Any:
    res = await _call(p, _member(org), "GET", PATH)
    assert res.status_code == 200, res.text
    return res.json()


async def test_a_member_reads_the_short_reads_with_the_rule_of_the_card(q) -> None:
    body = await _get(q, q.org_a)
    assert body == [
        {"key": "contacts", "zoho_count": 2, "readable": 0, "short_by": 2},
        {"key": "module:Payment_Made", "zoho_count": 8, "readable": 3, "short_by": 5},
    ]


async def test_the_count_of_the_last_run_wins(q) -> None:
    with q.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_sync_runs (organization_id, kind, status, estimate) "
            "VALUES (CAST(:o AS uuid), 'incremental', 'ok', CAST(:e AS jsonb))"),
            {"o": q.org_a, "e": json.dumps({"module:Payment_Made": 40})})
    body = {r["key"]: r for r in await _get(q, q.org_a)}
    assert body["module:Payment_Made"] == {
        "key": "module:Payment_Made", "zoho_count": 40, "readable": 35, "short_by": 5}


async def test_a_native_org_gets_none_and_reads_nothing_of_org_a(q) -> None:
    assert await _get(q, q.org_b) == []


async def test_the_aggregate_tool_adds_the_line(q, monkeypatch) -> None:
    from acb_skills import crm_tools

    async def through_router(path: str, params: dict[str, Any] | None = None) -> Any:
        from urllib.parse import urlencode

        clean = {k: v for k, v in (params or {}).items() if v is not None}
        res = await _call(q, _member(q.org_a), "GET",
                          f"{path}?{urlencode(clean)}" if clean else path)
        assert res.status_code == 200, (path, res.text)
        return res.json()

    with q.admin_engine.begin() as c:
        c.execute(text(
            "WITH conn AS (INSERT INTO integration_connections (organization_id, provider, "
            "owning_app) VALUES (CAST(:o AS uuid), 'zoho', 'crm') RETURNING id) "
            "INSERT INTO crm_source_profiles (organization_id, connection_id, version, "
            "profile, profile_hash) SELECT CAST(:o AS uuid), conn.id, 1, CAST(:p AS jsonb), "
            "'h' FROM conn"),
            {"o": q.org_a, "p": json.dumps({"modules": [{
                "api_name": "Payment_Made", "label": "Payment Made", "generic": True,
                "mirrored": True, "api_supported": True}]})})
    monkeypatch.setattr(crm_tools, "_get", through_router)
    out = await crm_tools.crm_aggregate("module:Payment_Made", "owner")
    assert ("Note: Zoho holds 8 Payment_Made records, and the connected Zoho user can "
            "read 3.") in out
