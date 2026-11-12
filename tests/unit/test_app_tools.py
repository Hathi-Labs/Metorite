"""Custom Apps · tools (Phase 2a, RFC: docs/app-workshop/README.md §4.4/§4.7).

Locks the ``cc.tools`` proxy in ``gateway.routes.apps.tools`` and the
publish-time admin review gate it adds to ``gateway.routes.apps.publish``:

* ``parse_tool_scope`` / ``find_declared_tool_scope`` — the manifest scope
  grammar (pure).
* ``merge_tool_args`` — the frozen-constraint security property (constraints
  always win over caller-supplied args).
* The route (``call_app_tool``), called directly as a plain async function
  (mirrors ``tests/unit/test_whatsapp_bridge.py``'s convention — this
  codebase does not use TestClient+dependency_overrides for gateway routes;
  DB-touching helpers are monkeypatched instead so every test stays hermetic
  and fast): scope-not-declared → 403, unknown tool → 404, a read-only tool
  runs without confirm, a destructive tool 409s without confirm and executes
  with it, and a remembered grant skips the second confirm.
* The publish review gate: an org-visibility publish with a write tool scope
  queues ``app.publish_review`` and leaves ``live_version`` untouched; a
  previously-approved scope-set hash skips review on a later publish.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from gateway.routes.apps import _common, publish, tools
from gateway.routes.apps._common import manifest_scopes, scope_set_hash
from gateway.routes.apps.tools import (
    ToolCallRequest,
    ToolSpec,
    call_app_tool,
    find_declared_tool_scope,
    merge_tool_args,
    parse_tool_scope,
)

OWNER = UserContext(email="owner@fracktal.in", role=UserRole.EMPLOYEE)


async def _never(_args: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("handler should not run on this path")


# ── parse_tool_scope ──────────────────────────────────────────────────────

def test_parse_tool_scope_with_query() -> None:
    assert parse_tool_scope("tool:clickup.create_task?list=Procurement") == (
        "clickup.create_task", {"list": "Procurement"},
    )


def test_parse_tool_scope_without_query() -> None:
    assert parse_tool_scope("tool:clickup.create_task") == (
        "clickup.create_task", {},
    )


def test_parse_tool_scope_multiple_constraints() -> None:
    assert parse_tool_scope("tool:clickup.create_task?list=42&status=Open") == (
        "clickup.create_task", {"list": "42", "status": "Open"},
    )


@pytest.mark.parametrize("bad", [
    "storage:app",
    "ai:tier-1",
    "identity:read",
    "",
    "tool",            # missing colon entirely
])
def test_parse_tool_scope_non_tool_prefix_is_none(bad: str) -> None:
    assert parse_tool_scope(bad) is None


@pytest.mark.parametrize("bad", ["tool:", "tool:?list=1"])
def test_parse_tool_scope_empty_tool_name_is_none(bad: str) -> None:
    assert parse_tool_scope(bad) is None


def test_parse_tool_scope_rejects_non_string() -> None:
    assert parse_tool_scope(None) is None  # type: ignore[arg-type]
    assert parse_tool_scope(123) is None  # type: ignore[arg-type]


# ── find_declared_tool_scope ─────────────────────────────────────────────

def test_find_declared_tool_scope_found() -> None:
    manifest = {"scopes": ["identity:read", "tool:clickup.create_task?list=7"]}
    assert find_declared_tool_scope(manifest, "clickup.create_task") == {"list": "7"}


def test_find_declared_tool_scope_not_found() -> None:
    manifest = {"scopes": ["identity:read", "storage:app"]}
    assert find_declared_tool_scope(manifest, "clickup.create_task") is None


def test_find_declared_tool_scope_empty_manifest() -> None:
    assert find_declared_tool_scope({}, "clickup.create_task") is None


def test_find_declared_tool_scope_first_of_multiple_wins() -> None:
    manifest = {"scopes": [
        "tool:clickup.create_task?list=1",
        "tool:clickup.create_task?list=2",
    ]}
    assert find_declared_tool_scope(manifest, "clickup.create_task") == {"list": "1"}


# ── merge_tool_args (the frozen-constraint security property) ────────────

def test_merge_tool_args_constraints_always_win() -> None:
    merged = merge_tool_args(
        {"list": "attacker-controlled-list", "title": "hi"},
        {"list": "Procurement"},
    )
    assert merged == {"list": "Procurement", "title": "hi"}


def test_merge_tool_args_no_constraints_is_passthrough() -> None:
    assert merge_tool_args({"a": 1}, {}) == {"a": 1}


def test_merge_tool_args_none_request_args() -> None:
    assert merge_tool_args(None, {"list": "x"}) == {"list": "x"}


# ── Route-level fakes ──────────────────────────────────────────────────────

class _Result:
    def __init__(self, one: Any = None):
        self._one = one

    def fetchone(self) -> Any:
        return self._one


class _FakeDB:
    """Just enough of the async session surface for the grant check/remember
    queries in ``_run_destructive_tool``; ``grants`` is shared by reference
    across every ``_FakeDB`` a test's ``_get_db`` hands out, so state
    persists call-to-call the way a real table would."""

    def __init__(self, grants: set[tuple[str, str, str]] | None = None):
        self.grants = grants if grants is not None else set()
        self.executed: list[tuple[str, dict | None]] = []
        self.committed = False
        self.closed = False

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        s = str(sql)
        self.executed.append((s, params))
        if "SELECT 1 FROM app_tool_grants" in s:
            key = (params["app_id"], params["email"], params["tool"])
            return _Result(one=(1,) if key in self.grants else None)
        if "INSERT INTO app_tool_grants" in s:
            key = (params["app_id"], params["email"], params["tool"])
            self.grants.add(key)
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


def _patch_common(
    monkeypatch: pytest.MonkeyPatch, *, manifest: dict[str, Any],
    row: Any = None, tool_registry: dict[str, ToolSpec] | None = None,
    build_integrations_result: tuple[dict, dict] | None = None,
    grants: set[tuple[str, str, str]] | None = None,
) -> Any:
    """Wire ``tools.py``'s DB-touching seams to fakes so a route test needs
    neither a live Postgres nor the real Integration Registry."""
    row = row or _row()
    shared_grants = grants if grants is not None else set()

    @asynccontextmanager
    async def _tenant_session() -> AsyncIterator[_FakeDB]:
        # H2: the handlers' seam is the tenant-bound context manager; a fresh
        # fake per block sharing `shared_grants` keeps the state-per-table
        # convention, and commit-on-clean-exit mirrors the real wrapper.
        db = _FakeDB(shared_grants)
        yield db
        await db.commit()

    async def _get_app_or_404(_db: Any, _slug: str, _user: UserContext) -> Any:
        return row, []

    async def _load_live_manifest(_db: Any, _row: Any) -> dict[str, Any]:
        return manifest

    async def _common_get_db() -> _FakeDB:
        return _FakeDB()  # keeps record_app_audit best-effort + DB-free

    monkeypatch.setattr(tools, "_tenant_session", _tenant_session)
    monkeypatch.setattr(tools, "get_app_or_404", _get_app_or_404)
    monkeypatch.setattr(tools, "_load_live_manifest", _load_live_manifest)
    monkeypatch.setattr(_common, "_get_db", _common_get_db)
    if tool_registry is not None:
        monkeypatch.setattr(tools, "_TOOL_REGISTRY", tool_registry)
    if build_integrations_result is not None:
        monkeypatch.setattr(
            tools, "build_integrations",
            lambda *_a, **_k: build_integrations_result,
        )
    return row


# ── Route: scope / registry gating ────────────────────────────────────────

async def test_scope_not_declared_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, manifest={"scopes": []})
    with pytest.raises(HTTPException) as exc:
        await call_app_tool(
            "myapp", "clickup.create_task", ToolCallRequest(), user=OWNER,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "scope_not_declared"


async def test_unknown_tool_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:mystery.tool"]},
        tool_registry={},
    )
    with pytest.raises(HTTPException) as exc:
        await call_app_tool(
            "myapp", "mystery.tool", ToolCallRequest(), user=OWNER,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "unknown_tool"


async def test_integration_unavailable_is_424(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = ToolSpec(integration="test-intg", read_only=True, destructive=False,
                     open_world=False, handler=_never)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.echo"]},
        tool_registry={"test.echo": spec},
        build_integrations_result=({}, {"test-intg": "not configured"}),
    )
    with pytest.raises(HTTPException) as exc:
        await call_app_tool("myapp", "test.echo", ToolCallRequest(), user=OWNER)
    assert exc.value.status_code == 424
    assert exc.value.detail["error"] == "integration_unavailable"


# ── Route: read-only path ─────────────────────────────────────────────────

async def test_read_only_tool_executes_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def _echo(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"echo": args}

    spec = ToolSpec(integration="test-intg", read_only=True, destructive=False,
                     open_world=False, handler=_echo)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.echo?fixed=1"]},
        tool_registry={"test.echo": spec},
        build_integrations_result=({"test-intg": {}}, {}),
    )
    resp = await call_app_tool(
        "myapp", "test.echo", ToolCallRequest(args={"msg": "hi"}), user=OWNER,
    )
    assert resp == {"result": {"echo": {"msg": "hi", "fixed": "1"}}}
    assert calls == [{"msg": "hi", "fixed": "1"}]  # constraint merged in too


async def test_read_only_tool_failure_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("upstream exploded")

    spec = ToolSpec(integration="test-intg", read_only=True, destructive=False,
                     open_world=False, handler=_boom)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.echo"]},
        tool_registry={"test.echo": spec},
        build_integrations_result=({"test-intg": {}}, {}),
    )
    with pytest.raises(HTTPException) as exc:
        await call_app_tool("myapp", "test.echo", ToolCallRequest(), user=OWNER)
    assert exc.value.status_code == 502


# ── Route: destructive path ───────────────────────────────────────────────

async def test_destructive_tool_without_confirm_is_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = ToolSpec(integration="test-intg", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.write"]},
        tool_registry={"test.write": spec},
        build_integrations_result=({"test-intg": {}}, {}),
    )
    resp = await call_app_tool(
        "myapp", "test.write", ToolCallRequest(args={"x": 1}), user=OWNER,
    )
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 409
    assert json.loads(resp.body) == {
        "error": "confirmation_required", "tool": "test.write", "args": {"x": 1},
    }


async def test_destructive_tool_with_confirm_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = ToolSpec(integration="test-intg", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.write"]},
        tool_registry={"test.write": spec},
        build_integrations_result=({"test-intg": {}}, {}),
    )
    proposed: list[dict[str, Any]] = []

    def _fake_propose(**kw: Any) -> SimpleNamespace:
        proposed.append(kw)
        return SimpleNamespace(**kw)

    async def _fake_submit(_proposal: Any) -> dict[str, Any]:
        return {"status": "applied", "ok": True,
                "result": {"provider_task_id": "T1"}}

    monkeypatch.setattr(tools, "propose", _fake_propose)
    monkeypatch.setattr(tools, "submit", _fake_submit)

    resp = await call_app_tool(
        "myapp", "test.write",
        ToolCallRequest(args={"x": 1}, confirm=True), user=OWNER,
    )
    assert resp == {"result": {"provider_task_id": "T1"}}
    assert len(proposed) == 1
    assert proposed[0]["action"] == "app.test_write"   # namespaced, not raw
    assert proposed[0]["payload"] == {"args": {"x": 1}}
    assert proposed[0]["destructive"] is True


async def test_destructive_tool_broker_failure_is_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = ToolSpec(integration="test-intg", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.write"]},
        tool_registry={"test.write": spec},
        build_integrations_result=({"test-intg": {}}, {}),
    )
    monkeypatch.setattr(tools, "propose", lambda **kw: SimpleNamespace(**kw))

    async def _fake_submit(_proposal: Any) -> dict[str, Any]:
        return {"status": "failed", "ok": False, "error": "clickup: 500"}

    monkeypatch.setattr(tools, "submit", _fake_submit)
    with pytest.raises(HTTPException) as exc:
        await call_app_tool(
            "myapp", "test.write",
            ToolCallRequest(args={"x": 1}, confirm=True), user=OWNER,
        )
    assert exc.value.status_code == 502


async def test_destructive_tool_queued_is_202(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = ToolSpec(integration="test-intg", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.write"]},
        tool_registry={"test.write": spec},
        build_integrations_result=({"test-intg": {}}, {}),
    )
    monkeypatch.setattr(tools, "propose", lambda **kw: SimpleNamespace(**kw))

    async def _fake_submit(_proposal: Any) -> dict[str, Any]:
        return {"ok": True, "status": "pending",
                "disposition": "needs_approval", "action_id": "act-9"}

    monkeypatch.setattr(tools, "submit", _fake_submit)
    resp = await call_app_tool(
        "myapp", "test.write",
        ToolCallRequest(args={"x": 1}, confirm=True), user=OWNER,
    )
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 202
    assert json.loads(resp.body) == {"queued": True, "action_id": "act-9"}


async def test_remember_grant_skips_confirm_on_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = ToolSpec(integration="test-intg", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    _patch_common(
        monkeypatch, manifest={"scopes": ["tool:test.write"]},
        tool_registry={"test.write": spec},
        build_integrations_result=({"test-intg": {}}, {}),
    )
    monkeypatch.setattr(tools, "propose", lambda **kw: SimpleNamespace(**kw))

    async def _fake_submit(_proposal: Any) -> dict[str, Any]:
        return {"status": "applied", "ok": True, "result": {}}

    monkeypatch.setattr(tools, "submit", _fake_submit)

    first = await call_app_tool(
        "myapp", "test.write",
        ToolCallRequest(args={"x": 1}, confirm=True, remember=True), user=OWNER,
    )
    assert first == {"result": {}}

    # No confirm this time — the remembered grant should stand in for it.
    second = await call_app_tool(
        "myapp", "test.write", ToolCallRequest(args={"x": 1}), user=OWNER,
    )
    assert second == {"result": {}}


# ── Publish review gate ────────────────────────────────────────────────────

class _PublishResult:
    def __init__(self, one: Any = None):
        self._one = one

    def fetchone(self) -> Any:
        return self._one

    def scalar_one(self) -> Any:
        return self._one


class _PublishFakeDB:
    """Matches on SQL substrings rather than call order — resilient to
    ``publish_app`` growing/reordering unrelated queries."""

    def __init__(self, *, next_version: int = 1, approved_hash: str | None = None):
        self.executed: list[tuple[str, dict | None]] = []
        self.committed = False
        self.closed = False
        self._next_version = next_version
        self._approved_hash = approved_hash

    async def execute(self, sql: Any, params: dict | None = None) -> _PublishResult:
        s = str(sql)
        self.executed.append((s, params))
        if "MAX(version)" in s:
            return _PublishResult(one=self._next_version)
        if "review_status = 'approved'" in s:
            matched = (
                self._approved_hash is not None
                and (params or {}).get("hash") == self._approved_hash
            )
            return _PublishResult(one=(1,) if matched else None)
        return _PublishResult()

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        self.closed = True

    def inserts(self, table: str) -> list[dict | None]:
        return [p for s, p in self.executed
                if s.strip().upper().startswith(f"INSERT INTO {table.upper()}")]

    def updates(self, table: str) -> list[dict | None]:
        return [p for s, p in self.executed
                if s.strip().upper().startswith(f"UPDATE {table.upper()}")]


def _write_workspace(tmp_path: Any, manifest: dict[str, Any]) -> None:
    (tmp_path / "app.json").write_text(json.dumps(manifest))
    (tmp_path / "index.html").write_text("<html><body>ok</body></html>")


def _patch_publish(
    monkeypatch: pytest.MonkeyPatch, *, row: Any, fake_db: _PublishFakeDB,
) -> tuple[list[dict[str, Any]], list[Any]]:
    async def _fake_get_app_or_404(
        _db: Any, _slug: str, _user: UserContext, edit: bool = False,
    ) -> Any:
        return row, []

    @asynccontextmanager
    async def _tenant_session() -> AsyncIterator[_PublishFakeDB]:
        yield fake_db
        await fake_db.commit()

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(publish, "get_app_or_404", _fake_get_app_or_404)
    monkeypatch.setattr(publish, "_tenant_session", _tenant_session)
    monkeypatch.setattr(publish, "sync_workspace_best_effort", _noop)
    monkeypatch.setattr(publish, "record_app_audit_bound", _noop)
    monkeypatch.setattr(publish, "publish_app_activity", lambda *a, **k: None)

    proposed: list[dict[str, Any]] = []
    submitted: list[Any] = []

    def _fake_propose(**kw: Any) -> SimpleNamespace:
        proposed.append(kw)
        return SimpleNamespace(**kw)

    async def _fake_submit(proposal: Any) -> dict[str, Any]:
        submitted.append(proposal)
        return {"ok": True, "status": "pending",
                "disposition": "needs_approval", "action_id": "act-1"}

    monkeypatch.setattr(publish, "propose", _fake_propose)
    monkeypatch.setattr(publish, "submit", _fake_submit)
    return proposed, submitted


async def test_publish_org_with_write_scope_needs_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    manifest = {"entry": "index.html", "scopes": ["tool:test.write"]}
    _write_workspace(tmp_path, manifest)
    spec = ToolSpec(integration="x", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    monkeypatch.setattr(tools, "_TOOL_REGISTRY", {"test.write": spec})

    row = SimpleNamespace(
        id="app-1", owner_email=OWNER.email, visibility="private",
        status="draft", manifest=manifest, workspace_path=str(tmp_path),
        live_version=None,
    )
    fake_db = _PublishFakeDB(next_version=1)
    proposed, submitted = _patch_publish(monkeypatch, row=row, fake_db=fake_db)

    resp = await publish.publish_app(
        "myapp", publish.PublishRequest(notes="v1", visibility="org"), user=OWNER,
    )

    assert resp == {"version": 1, "warnings": [], "review": "pending"}
    inserts = fake_db.inserts("app_versions")
    assert inserts and inserts[0]["review_status"] == "pending"
    assert fake_db.updates("apps") == []          # live_version left untouched

    assert len(submitted) == 1
    assert proposed[0]["action"] == "app.publish_review"
    assert proposed[0]["authority"].value == "suggest"
    assert proposed[0]["payload"]["scopes"] == ["tool:test.write"]
    assert proposed[0]["payload"]["visibility"] == "org"
    assert proposed[0]["payload"]["version"] == 1


async def test_publish_private_with_write_scope_skips_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """Review is only for org-visibility publishes — private/people never
    need it regardless of what tools the manifest declares."""
    manifest = {"entry": "index.html", "scopes": ["tool:test.write"]}
    _write_workspace(tmp_path, manifest)
    spec = ToolSpec(integration="x", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    monkeypatch.setattr(tools, "_TOOL_REGISTRY", {"test.write": spec})

    row = SimpleNamespace(
        id="app-1", owner_email=OWNER.email, visibility="private",
        status="draft", manifest=manifest, workspace_path=str(tmp_path),
        live_version=None,
    )
    fake_db = _PublishFakeDB(next_version=1)
    _proposed, submitted = _patch_publish(monkeypatch, row=row, fake_db=fake_db)

    resp = await publish.publish_app(
        "myapp", publish.PublishRequest(notes="v1"), user=OWNER,
    )
    assert resp["review"] == "auto"
    assert not submitted
    assert fake_db.updates("apps")                 # live_version WAS set


async def test_publish_reuses_previously_approved_scope_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    manifest = {"entry": "index.html", "scopes": ["tool:test.write"]}
    _write_workspace(tmp_path, manifest)
    spec = ToolSpec(integration="x", read_only=False, destructive=True,
                     open_world=True, handler=_never)
    monkeypatch.setattr(tools, "_TOOL_REGISTRY", {"test.write": spec})

    approved_hash = scope_set_hash(manifest_scopes(manifest))
    row = SimpleNamespace(
        id="app-1", owner_email=OWNER.email, visibility="org",
        status="live", manifest=manifest, workspace_path=str(tmp_path),
        live_version=1,
    )
    fake_db = _PublishFakeDB(next_version=2, approved_hash=approved_hash)
    _proposed, submitted = _patch_publish(monkeypatch, row=row, fake_db=fake_db)

    resp = await publish.publish_app(
        "myapp", publish.PublishRequest(notes="v2"), user=OWNER,
    )
    assert resp["review"] == "auto"
    assert not submitted
    inserts = fake_db.inserts("app_versions")
    assert inserts and inserts[0]["review_status"] == "auto"
    assert fake_db.updates("apps")                 # live_version WAS updated
