"""The admin view of the packages of an org, hermetic half (STU-J4).

Spec: ``project-docs/specs/ai_studio.md`` §14 J4. Code:
``gateway/studio/packages_admin_list.py`` and ``list_packages`` in
``gateway/routes/studio/packages.py`` (``GET /studio/packages?scope=org``).
The real-database half is ``test_studio_packages_admin_list_r8.py``.

The fences of this file:

* the gate is the admin right, read through the resolved access;
* a holder gets the org list, and any other caller gets the member list;
* the answer holds no manifest, no reason text and no value;
* ``use`` is null, and a package with no audit row has honest empty fields;
* the SQL is one bounded statement, and it never selects the manifest or the
  reason of an audit row;
* the permission string lives in the new module and in no route file of
  ``routes/studio``, so the fence of A5c-2 stays whole.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.studio import actions, packages
from gateway.studio import packages_admin_list as admin_list
from gateway.studio import packages_store

_ROOT = Path(__file__).resolve().parents[2]
_STUDIO_ROUTES = _ROOT / "apps/services/gateway/gateway/routes/studio"
ORG = str(uuid.uuid4())
ANA = "ana@example.test"
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def _row(**over: Any) -> admin_list.AdminRow:
    base: dict[str, Any] = {
        "id": str(uuid.uuid4()), "slug": "visits", "name": "Visits", "description": "d",
        "owner_email": "Ben@Example.test", "status": "draft", "live_version": None,
        "created_at": NOW, "updated_at": NOW, "manifest_id": "visits", "action_calls": 0,
        "last_error_action": None, "last_error_outcome": None, "last_error_at": None,
    }
    base.update(over)
    return admin_list.AdminRow(**base)


class _User:
    def __init__(self, *grants: str) -> None:
        self.email = ANA
        self.access = build_access(list(grants))

    def has_permission(self, permission: str) -> bool:
        return self.access.has(permission)


# ── The gate ───────────────────────────────────────────────────────────────


def test_the_right_is_the_admin_right_of_the_roster() -> None:
    assert admin_list.ADMIN_RIGHT == "admin:members:manage"
    assert admin_list.holds_admin_right(_User("admin:members:manage")) is True
    assert admin_list.holds_admin_right(_User("*")) is True
    assert admin_list.holds_admin_right(_User("feature:studio")) is False
    assert admin_list.holds_admin_right(_User("feature:studio", "studio:publish")) is False


def test_the_module_creates_no_permission() -> None:
    from acb_auth.permissions import CAPABILITIES

    assert admin_list.ADMIN_RIGHT in CAPABILITIES


# ── The answer ─────────────────────────────────────────────────────────────


def test_a_package_with_no_audit_row_has_honest_empty_fields() -> None:
    out = admin_list.row_out(_row(), ANA)
    assert out["state"] == "draft"
    assert out["live_version"] is None
    assert out["action_calls"] == 0
    assert out["last_error"] is None
    assert out["use"] is None
    assert out["mine"] is False


def test_a_live_package_names_its_owner_version_calls_and_last_error() -> None:
    row = _row(status="published", live_version=3, owner_email="ana@example.test",
               action_calls=7, last_error_action="close", last_error_outcome="refused",
               last_error_at=NOW)
    out = admin_list.row_out(row, "ANA@example.test")
    assert out["state"] == "live"
    assert out["live_version"] == 3
    assert out["owner_email"] == "ana@example.test"
    assert out["mine"] is True
    assert out["action_calls"] == 7
    assert out["last_error"] == {"action": "close", "outcome": "refused", "at": NOW}


def test_the_answer_holds_no_manifest_reason_or_value() -> None:
    out = admin_list.row_out(_row(last_error_action="x", last_error_outcome="failed",
                                  last_error_at=NOW), ANA)
    assert set(out) == {
        "id", "slug", "name", "description", "status", "live_version", "manifest_id",
        "created_at", "updated_at", "owner_email", "mine", "state", "action_calls",
        "action_calls_capped", "last_error", "use"}
    assert set(out["last_error"]) == {"action", "outcome", "at"}
    assert "manifest" not in out
    assert "reason" not in out["last_error"]


def test_the_count_says_when_it_stopped_at_its_cap() -> None:
    assert admin_list.row_out(_row(action_calls=admin_list.MAX_COUNT), ANA)["action_calls_capped"]
    assert not admin_list.row_out(_row(action_calls=admin_list.MAX_COUNT - 1), ANA)["action_calls_capped"]


# ── The statement ──────────────────────────────────────────────────────────


def test_the_statement_is_one_bounded_set_read_that_never_selects_a_secret() -> None:
    sql = admin_list._SQL
    assert sql.count("SELECT a.id") == 1
    assert "LIMIT :cap" in sql and "LIMIT :count_cap" in sql
    # The manifest: only the id inside it. The audit row: never the reason.
    assert re.findall(r"manifest\S*", sql) == ["manifest->>'id',", "manifest'"]
    assert "reason" not in sql
    assert "detail->>'action'" in sql and "detail->>'outcome'" in sql
    assert "organization_id" in sql and "current_setting('app.tenant_id'" in sql
    assert "'failed', 'refused'" in sql


# ── The handler ────────────────────────────────────────────────────────────


class _Session:
    pass


def _client(grants: list[str], monkeypatch: pytest.MonkeyPatch,
            reached: list[str]) -> TestClient:
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")

    @asynccontextmanager
    async def _tenant_session() -> Any:
        yield _Session()

    async def _org(_db: Any) -> tuple[list[admin_list.AdminRow], bool]:
        reached.append("org")
        return [_row(owner_email="ben@example.test", action_calls=2)], True

    async def _own(_db: Any, member: str) -> list[packages_store.PackageSummary]:
        reached.append(f"own:{member}")
        return [packages_store.PackageSummary(
            id=str(uuid.uuid4()), slug="mine", name="Mine", description="", status="draft",
            live_version=None, created_at=NOW, updated_at=NOW, manifest_id="mine")]

    monkeypatch.setattr(packages, "_tenant_session", _tenant_session)
    monkeypatch.setattr(admin_list, "list_org_packages", _org)
    monkeypatch.setattr(packages_store, "list_summaries", _own)
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=ANA, role=UserRole.EMPLOYEE, organization_id=ORG,
        access=build_access(grants))
    return TestClient(app, raise_server_exceptions=False)


def test_a_holder_with_the_org_scope_gets_every_package(monkeypatch: pytest.MonkeyPatch) -> None:
    reached: list[str] = []
    r = _client(["feature:studio", "admin:members:manage"], monkeypatch, reached) \
        .get("/studio/packages?scope=org")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admin"] is True and body["truncated"] is True
    assert body["packages"][0]["owner_email"] == "ben@example.test"
    assert reached == ["org"]


def test_a_member_with_the_org_scope_gets_the_member_list(monkeypatch: pytest.MonkeyPatch) -> None:
    reached: list[str] = []
    r = _client(["feature:studio"], monkeypatch, reached).get("/studio/packages?scope=org")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["admin"] is False
    assert [p["name"] for p in body["packages"]] == ["Mine"]
    assert "owner_email" not in body["packages"][0]
    assert reached == [f"own:{ANA}"]


def test_the_default_answer_is_unchanged_even_for_a_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    reached: list[str] = []
    r = _client(["feature:studio", "admin:members:manage"], monkeypatch, reached) \
        .get("/studio/packages")
    assert r.status_code == 200
    assert set(r.json()) == {"packages"}
    assert reached == [f"own:{ANA}"]


@pytest.mark.parametrize("scope", ["all", "ORG", "", "org,all"])
def test_another_scope_is_422_and_reads_nothing(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    reached: list[str] = []
    r = _client(["feature:studio", "admin:members:manage"], monkeypatch, reached) \
        .get(f"/studio/packages?scope={scope}")
    assert r.status_code == 422
    assert reached == []


def test_a_guest_with_the_org_scope_gets_403_and_reads_nothing(
        monkeypatch: pytest.MonkeyPatch) -> None:
    reached: list[str] = []
    r = _client(["feature:chat", "admin:members:manage"], monkeypatch, reached) \
        .get("/studio/packages?scope=org")
    assert r.status_code == 403
    assert reached == []


# ── The fences of the sources ──────────────────────────────────────────────


def test_the_right_is_read_in_the_new_module_and_in_no_route_file() -> None:
    """A5c-2 allows the text of the right only inside its own function of
    ``routes/studio``. This slice keeps the string outside that folder."""
    for path in sorted(_STUDIO_ROUTES.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "admin:members:manage" not in text, path.name
        assert "ADMIN_RIGHT" not in text, path.name


def test_the_route_calls_the_module_and_names_no_right() -> None:
    src = (_STUDIO_ROUTES / "packages.py").read_text(encoding="utf-8")
    assert "admin_list.holds_admin_right(user)" in src
    assert "has_permission" not in src.split("async def list_packages")[1].split("@router")[0]


# ── The chat read (studio_package_get with scope "org") ────────────────────


def _tool_gateway(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> list[str]:
    from acb_skills import studio_tools as st

    paths: list[str] = []

    async def _get(path: str) -> Any:
        paths.append(path)
        return st._Reply(200, data)

    monkeypatch.setattr(st, "_get", _get)
    return paths


async def test_the_chat_read_asks_for_the_org_scope_and_keeps_no_reason(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import studio_tools as st

    row = admin_list.row_out(_row(last_error_action="close", last_error_outcome="failed",
                                  last_error_at=NOW, action_calls=3, live_version=2), ANA)
    row["last_error"]["reason"] = "stored words"
    row["manifest"] = {"purpose": "secret"}
    paths = _tool_gateway(monkeypatch, {"packages": [row], "admin": True, "truncated": False})
    out = await st.studio_package_get(scope="org")
    assert paths == ["/studio/packages?scope=org"]
    assert out["ok"] is True and out["scope"] == "org"
    (got,) = out["packages"]
    assert got["owner"].lower().endswith("ben@example.test")
    assert got["state"] == "live" and got["action_calls"] == 3 and got["use"] == "Not tracked yet"
    assert set(got["last_error"]) == {"action", "outcome", "at"}
    assert "stored words" not in str(out) and "secret" not in str(out)


async def test_a_member_in_chat_gets_their_own_list_marked_own(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import studio_tools as st

    paths = _tool_gateway(monkeypatch, {"packages": [
        {"id": "p1", "name": "Mine", "status": "draft", "live_version": None}], "admin": False})
    out = await st.studio_package_get(scope="org")
    assert paths == ["/studio/packages?scope=org"]
    assert out["scope"] == "own" and out["packages"][0]["name"] == "«Mine»"
    assert "owner" not in out["packages"][0]


async def test_chat_scope_is_checked_and_an_id_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from acb_skills import studio_tools as st

    paths = _tool_gateway(monkeypatch, {"packages": []})
    assert (await st.studio_package_get(scope="all"))["ok"] is False
    assert paths == []
    await st.studio_package_get(scope="")
    assert paths == ["/studio/packages"]
