"""Custom Apps · grants + consent (RFC: docs/app-workshop/README.md §4.8).

Locks the "share with specific people" surface added in
``gateway.routes.apps.grants`` — subject validation, the sharing
upsert/list/revoke endpoints, and the first-open consent endpoint — plus the
``needs_consent`` disclosure computation added to ``lifecycle.get_app``.
Route-level tests call the async route functions directly with fake,
in-memory DB sessions (mirrors ``tests/unit/test_app_tools.py``'s
convention: no TestClient, no live Postgres — DB-touching seams are
monkeypatched so every test stays hermetic and fast).

H2 note: the handlers' seam is the package's ``_tenant_session`` alias
(``acb_common.db.tenant_session``), swapped in via an ``asynccontextmanager``
that commits on clean exit — so one-transaction contracts stay observable —
while ``_common._get_db`` keeps its plain-coroutine patch. The audit row goes
through ``record_app_audit_bound`` (task 1317).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.apps import _common, grants, lifecycle
from gateway.routes.apps.grants import (
    ConsentRequest,
    GrantCreate,
    consent_app,
    is_valid_subject,
    list_app_grants,
    revoke_app_grant,
    upsert_app_grant,
)

OWNER = UserContext(email="owner@fracktal.in", role=UserRole.EMPLOYEE)
TEAMMATE = UserContext(email="mate@fracktal.in", role=UserRole.EMPLOYEE)


# ── Subject validation (pure) ────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "mate@fracktal.in",
    "a@b.co",
    "agent:task-manager",
    "agent:Foo_Bar-1",
    "agents:*",
])
def test_valid_subjects_accepted(subject: str) -> None:
    assert is_valid_subject(subject)


@pytest.mark.parametrize("subject", [
    "org",
    "",
    "not-an-email",
    "has space@x.com",
    "@nouser.com",
    "trailing@",
    "agent:",
    "agent:has space",
    "agent:has/slash",
    "agents:",
    "x" * 300 + "@x.com",
])
def test_invalid_subjects_rejected(subject: str) -> None:
    assert not is_valid_subject(subject)


# ── Route-level fakes ─────────────────────────────────────────────────────

class _Result:
    def __init__(self, one: Any = None, rows: list[Any] | None = None):
        self._one = one
        self._rows = rows if rows is not None else []

    def fetchone(self) -> Any:
        return self._one

    def fetchall(self) -> list[Any]:
        return self._rows


class _FakeGrantsDB:
    """In-memory ``app_grants`` (+ a canned ``app_versions`` row lookup),
    reproducing the real upsert semantics exactly: the sharing endpoint's
    ``ON CONFLICT`` only ever touches ``role``; the consent endpoint's only
    ever touches ``consented_scope_hash``. ``grants`` is a plain dict shared
    by reference across every fake a test's ``_get_db`` hands out, matching
    ``test_app_tools.py``'s ``_FakeDB`` convention (state persists call to
    call the way a real table would).
    """

    def __init__(
        self, *, grants: dict[str, dict[str, Any]] | None = None,
        version_row: Any = None,
    ):
        self.grants = grants if grants is not None else {}
        self.version_row = version_row
        self.executed: list[tuple[str, dict | None]] = []
        self.committed = False
        self.closed = False

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        params = params or {}
        self.executed.append((s, params))

        if "FROM app_versions" in s:
            return _Result(one=self.version_row)

        if s.startswith("SELECT subject, role") and "FROM app_grants" in s:
            rows = [
                SimpleNamespace(subject=subj, **row)
                for subj, row in sorted(self.grants.items())
            ]
            return _Result(rows=rows)

        if "INSERT INTO app_grants" in s and "role = excluded.role" in s:
            subj = params["subject"]
            existing = self.grants.get(subj)
            if existing is None:
                self.grants[subj] = {
                    "role": params["role"], "consented_scope_hash": "",
                    "granted_by": params["granted_by"], "created_at": None,
                }
            else:
                existing["role"] = params["role"]
            row = self.grants[subj]
            return _Result(one=SimpleNamespace(subject=subj, **row))

        if (
            "INSERT INTO app_grants" in s
            and "consented_scope_hash = excluded.consented_scope_hash" in s
        ):
            subj = params["email"]
            existing = self.grants.get(subj)
            if existing is None:
                self.grants[subj] = {
                    "role": "use", "consented_scope_hash": params["hash"],
                    "granted_by": params["email"], "created_at": None,
                }
            else:
                existing["consented_scope_hash"] = params["hash"]
            return _Result()

        if "DELETE FROM app_grants" in s:
            self.grants.pop(params["subject"], None)
            return _Result()

        return _Result()

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        self.closed = True


def _row(**overrides: Any) -> Any:
    defaults = {
        "id": "app-1", "live_version": 1, "owner_email": OWNER.email,
        "visibility": "org", "status": "live",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_grants(
    monkeypatch: pytest.MonkeyPatch, *, row: Any = None,
    fake_db: _FakeGrantsDB | None = None, caller_grants: list | None = None,
) -> tuple[Any, _FakeGrantsDB]:
    row = row or _row()
    db = fake_db if fake_db is not None else _FakeGrantsDB()

    @asynccontextmanager
    async def _tenant_session() -> AsyncIterator[_FakeGrantsDB]:
        # Commit-on-clean-exit, like the real wrapper — a handler that raises
        # mid-block commits nothing here just as against Postgres.
        yield db
        await db.commit()

    async def _get_app_or_404(
        _db: Any, _slug: str, _user: UserContext, edit: bool = False,
    ) -> Any:
        return row, (caller_grants or [])

    async def _common_get_db() -> _FakeGrantsDB:
        return _FakeGrantsDB()  # keeps record_app_audit best-effort + DB-free

    monkeypatch.setattr(grants, "_tenant_session", _tenant_session)
    monkeypatch.setattr(grants, "get_app_or_404", _get_app_or_404)
    monkeypatch.setattr(_common, "_get_db", _common_get_db)
    return row, db


# ── Sharing endpoints ────────────────────────────────────────────────────

async def test_upsert_rejects_bad_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_grants(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await upsert_app_grant(
            "myapp", GrantCreate(subject="mate@fracktal.in", role="admin"),
            user=OWNER,
        )
    assert exc.value.status_code == 422


async def test_upsert_rejects_bad_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_grants(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await upsert_app_grant(
            "myapp", GrantCreate(subject="org", role="use"), user=OWNER,
        )
    assert exc.value.status_code == 422


async def test_upsert_creates_without_prior_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _row, db = _patch_grants(monkeypatch)
    entry = await upsert_app_grant(
        "myapp", GrantCreate(subject="mate@fracktal.in", role="edit"),
        user=OWNER,
    )
    assert entry.subject == "mate@fracktal.in"
    assert entry.role == "edit"
    assert entry.granted_by == OWNER.email
    assert db.grants["mate@fracktal.in"]["role"] == "edit"


async def test_upsert_second_call_changes_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _row, db = _patch_grants(monkeypatch)
    await upsert_app_grant(
        "myapp", GrantCreate(subject="mate@fracktal.in", role="use"),
        user=OWNER,
    )
    entry = await upsert_app_grant(
        "myapp", GrantCreate(subject="mate@fracktal.in", role="own"),
        user=OWNER,
    )
    assert entry.role == "own"
    assert db.grants["mate@fracktal.in"]["role"] == "own"
    assert len(db.grants) == 1  # still one row, not a second


async def test_list_grants_returns_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = {
        "mate@fracktal.in": {
            "role": "use", "consented_scope_hash": "", "granted_by": OWNER.email,
            "created_at": None,
        },
    }
    _row, _db = _patch_grants(
        monkeypatch, fake_db=_FakeGrantsDB(grants=seeded),
    )
    out = await list_app_grants("myapp", user=OWNER)
    assert [e.subject for e in out] == ["mate@fracktal.in"]
    assert out[0].role == "use"


async def test_revoke_removes_row(monkeypatch: pytest.MonkeyPatch) -> None:
    seeded = {
        "mate@fracktal.in": {
            "role": "use", "consented_scope_hash": "", "granted_by": OWNER.email,
            "created_at": None,
        },
    }
    _row, db = _patch_grants(monkeypatch, fake_db=_FakeGrantsDB(grants=seeded))
    resp = await revoke_app_grant("myapp", "mate@fracktal.in", user=OWNER)
    assert resp == {"subject": "mate@fracktal.in", "deleted": True}
    assert "mate@fracktal.in" not in db.grants


# ── Consent ──────────────────────────────────────────────────────────────

async def test_consent_no_live_version_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_grants(monkeypatch, row=_row(live_version=None))
    with pytest.raises(HTTPException) as exc:
        await consent_app(
            "myapp", ConsentRequest(scope_set_hash="x"), user=TEAMMATE,
        )
    assert exc.value.status_code == 404


async def test_consent_hash_mismatch_is_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_row = SimpleNamespace(
        manifest={"scopes": ["a"]}, scope_set_hash="real-hash",
    )
    _patch_grants(monkeypatch, fake_db=_FakeGrantsDB(version_row=version_row))
    with pytest.raises(HTTPException) as exc:
        await consent_app(
            "myapp", ConsentRequest(scope_set_hash="stale-hash"), user=TEAMMATE,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"error": "scope_set_changed"}


async def test_consent_matching_hash_upserts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_row = SimpleNamespace(
        manifest={"scopes": ["a"]}, scope_set_hash="real-hash",
    )
    _row, db = _patch_grants(
        monkeypatch, fake_db=_FakeGrantsDB(version_row=version_row),
    )
    resp = await consent_app(
        "myapp", ConsentRequest(scope_set_hash="real-hash"), user=TEAMMATE,
    )
    assert resp == {"consented": True}
    assert db.grants[TEAMMATE.email]["consented_scope_hash"] == "real-hash"
    assert db.grants[TEAMMATE.email]["role"] == "use"


async def test_consent_preserves_existing_edit_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UPDATE clause must NOT touch role: an existing editor consenting
    must not be downgraded to 'use'."""
    version_row = SimpleNamespace(
        manifest={"scopes": ["a"]}, scope_set_hash="real-hash",
    )
    seeded = {
        TEAMMATE.email: {
            "role": "edit", "consented_scope_hash": "old-hash",
            "granted_by": OWNER.email, "created_at": None,
        },
    }
    _row, db = _patch_grants(
        monkeypatch,
        fake_db=_FakeGrantsDB(grants=seeded, version_row=version_row),
    )
    resp = await consent_app(
        "myapp", ConsentRequest(scope_set_hash="real-hash"), user=TEAMMATE,
    )
    assert resp == {"consented": True}
    assert db.grants[TEAMMATE.email]["role"] == "edit"          # unchanged
    assert db.grants[TEAMMATE.email]["consented_scope_hash"] == "real-hash"


# ── needs_consent computation (lifecycle.get_app) ───────────────────────

class _LifecycleFakeDB:
    def __init__(self, *, version_row: Any = None, consent_row: Any = None):
        self.version_row = version_row
        self.consent_row = consent_row

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = " ".join(str(sql).split())
        if "FROM app_versions" in s:
            return _Result(one=self.version_row)
        if "FROM app_grants" in s:
            return _Result(one=self.consent_row)
        return _Result()

    async def commit(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _detail_row(**overrides: Any) -> Any:
    defaults = {
        "id": "app-1", "slug": "myapp", "name": "My App", "icon": "",
        "description": "", "status": "live", "visibility": "org",
        "live_version": 1, "owner_email": OWNER.email, "updated_at": None,
        "created_at": None, "manifest": {}, "workspace_path": "",
        "builder_session_id": None, "is_template": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_lifecycle_get_app(
    monkeypatch: pytest.MonkeyPatch, *, row: Any, caller_grants: list,
    db: _LifecycleFakeDB,
) -> None:
    @asynccontextmanager
    async def _tenant_session() -> AsyncIterator[_LifecycleFakeDB]:
        yield db
        await db.commit()

    async def _get_app_or_404(_db: Any, _slug: str, _user: UserContext) -> Any:
        return row, caller_grants

    monkeypatch.setattr(lifecycle, "_tenant_session", _tenant_session)
    monkeypatch.setattr(lifecycle, "get_app_or_404", _get_app_or_404)


async def test_needs_consent_false_for_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _detail_row(owner_email=OWNER.email, live_version=1)
    version_row = SimpleNamespace(manifest={"scopes": ["a"]}, scope_set_hash="h1")
    _patch_lifecycle_get_app(
        monkeypatch, row=row, caller_grants=[],
        db=_LifecycleFakeDB(version_row=version_row),
    )
    detail = await lifecycle.get_app("myapp", user=OWNER)
    assert detail.needs_consent is False
    assert detail.live_scopes == ["a"]
    assert detail.live_scope_set_hash == "h1"


async def test_needs_consent_true_for_viewer_without_grant_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _detail_row(owner_email=OWNER.email, live_version=1)
    version_row = SimpleNamespace(manifest={"scopes": ["a"]}, scope_set_hash="h1")
    _patch_lifecycle_get_app(
        monkeypatch, row=row, caller_grants=[],
        db=_LifecycleFakeDB(version_row=version_row, consent_row=None),
    )
    detail = await lifecycle.get_app("myapp", user=TEAMMATE)
    assert detail.needs_consent is True


async def test_needs_consent_false_when_hash_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _detail_row(owner_email=OWNER.email, live_version=1)
    version_row = SimpleNamespace(manifest={"scopes": ["a"]}, scope_set_hash="h1")
    consent_row = SimpleNamespace(consented_scope_hash="h1")
    _patch_lifecycle_get_app(
        monkeypatch, row=row, caller_grants=[],
        db=_LifecycleFakeDB(version_row=version_row, consent_row=consent_row),
    )
    detail = await lifecycle.get_app("myapp", user=TEAMMATE)
    assert detail.needs_consent is False


async def test_needs_consent_true_when_hash_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _detail_row(owner_email=OWNER.email, live_version=1)
    version_row = SimpleNamespace(manifest={"scopes": ["a"]}, scope_set_hash="h2")
    consent_row = SimpleNamespace(consented_scope_hash="h1")
    _patch_lifecycle_get_app(
        monkeypatch, row=row, caller_grants=[],
        db=_LifecycleFakeDB(version_row=version_row, consent_row=consent_row),
    )
    detail = await lifecycle.get_app("myapp", user=TEAMMATE)
    assert detail.needs_consent is True


async def test_needs_consent_absent_without_live_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _detail_row(owner_email=OWNER.email, live_version=None)
    _patch_lifecycle_get_app(
        monkeypatch, row=row, caller_grants=[], db=_LifecycleFakeDB(),
    )
    detail = await lifecycle.get_app("myapp", user=TEAMMATE)
    assert detail.needs_consent is None
    assert detail.live_scopes is None
    assert detail.live_scope_set_hash is None
