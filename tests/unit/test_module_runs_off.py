"""Member code runs on the box are dark by org (WS-STU STU-Q15a).

Spec: ``project-docs/specs/ai_studio.md`` §12 STU-Q15 and §14 STU-Q15a.
Module Studio runs the Python of a member as a child process on the box, and
the runner is not a sandbox. D127.2 keeps member code off the box. So
``MODULE_RUN_ORGS`` (default empty) decides, per org, whether it runs.

What this file holds (R7):

1. The one reader, ``gateway.module_runs.module_runs_allowed``: empty is off,
   ``*`` is every org, a list is case free, and no org is always off. The
   field is a ``*_orgs`` field, so a SIGHUP reload applies it.
2. The choke point. ``run_module_code`` refuses before it spawns a child, for
   no org and for an org off the list.
3. Each of the three run paths starts NO child with the flag empty:
   ``POST /workflows/modules/test``, ``POST /workflows/modules/{id}/test``
   and the workflow module node.
4. A listed org and ``*`` still run on each path.
5. The refusal explains: 403 ``{code: "module_runs_off", message}`` on a
   route, and the node fails with the same words. The run ends as failed.
6. The org is the session's or the run's, never one from request input.

Mutation, run on 2026-11-06: delete ``refuse_unless_allowed(organization_id)``
in ``run_module_code``. The node cases and the direct cases go red. Delete
``_require_module_runs(user)`` in both routes too, and the route cases go red.
"""

from __future__ import annotations

import ast
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway import module_runs
from gateway.module_runs import MODULE_RUNS_OFF, MODULE_RUNS_OFF_MESSAGE, ModuleRunsOff
from gateway.routes.workflows.engine import modules as engine_modules
from gateway.routes.workflows.engine.graph import compile_graph
from gateway.routes.workflows.engine.handlers import (
    NodeExecutionError,
    NodeServices,
    execute_node,
)
from gateway.routes.workflows.engine.runner import execute_workflow

ROOT = Path(__file__).resolve().parents[2]

ORG_A = "org-a"
ORG_B = "org-b"

CODE = "def run(inputs):\n    return {'echo': inputs.get('x')}\n"


# ── The spawn spy ────────────────────────────────────────────────────────────


class _Proc:
    """A stand-in for the child. It answers as the harness answers."""

    returncode = 0

    def __init__(self, payload: bytes) -> None:
        self._payload = json.loads(payload.decode()) if payload else {}

    async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
        sent = json.loads(data.decode())
        out = {"ok": True, "output": {"echo": sent["inputs"].get("x")}}
        return json.dumps(out).encode(), b""

    def kill(self) -> None:  # pragma: no cover - the stand-in never hangs
        pass

    async def wait(self) -> int:  # pragma: no cover
        return 0


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Each child that the runner starts. The child is a stand-in, so the
    test runs the same on Windows (no ``preexec_fn``) and on Linux."""
    seen: list[tuple[Any, ...]] = []

    async def _spawn(*args: Any, **kwargs: Any) -> _Proc:
        seen.append(args)
        return _Proc(b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    return seen


def _flag(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr(get_settings(), "module_run_orgs", raw, raising=False)


# ── 1. The one reader ────────────────────────────────────────────────────────


class TestTheReader:
    def test_the_default_is_empty_so_every_org_is_off(self) -> None:
        assert Settings.model_fields["module_run_orgs"].default == ""

    @pytest.mark.parametrize(("raw", "org", "on"), [
        ("", ORG_A, False),
        ("*", ORG_A, True),
        (f"{ORG_B}, ORG-A ", ORG_A, True),
        (ORG_B, ORG_A, False),
        ("*", None, False),
        ("*", "", False),
        ("*", "   ", False),
    ])
    def test_the_reader(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, org: str | None, on: bool,
    ) -> None:
        _flag(monkeypatch, raw)
        assert module_runs.module_runs_allowed(org) is on

    def test_a_change_needs_no_restart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The reader reads the settings on each call."""
        _flag(monkeypatch, "")
        assert module_runs.module_runs_allowed(ORG_A) is False
        _flag(monkeypatch, ORG_A)
        assert module_runs.module_runs_allowed(ORG_A) is True

    def test_a_sighup_reload_applies_the_flag(self) -> None:
        from gateway import flag_reload

        assert flag_reload.reloadable("MODULE_RUN_ORGS")

    def test_no_tenant_may_write_the_flag(self) -> None:
        from acb_common import env_guard

        assert env_guard.is_platform_env("MODULE_RUN_ORGS")

    def test_the_refusal_says_what_happened_and_who_can_change_it(self) -> None:
        exc = ModuleRunsOff()
        assert exc.code == MODULE_RUNS_OFF == "module_runs_off"
        assert str(exc) == exc.message == MODULE_RUNS_OFF_MESSAGE
        assert "do not run in this organization" in exc.message
        assert "sandbox" in exc.message
        assert "admin of Metorite can turn them on" in exc.message

    def test_the_refusal_is_not_a_module_execution_error(self) -> None:
        """A caller that catches only ``ModuleExecutionError`` must not turn
        the refusal into a quiet ``ok: false``."""
        assert not issubclass(ModuleRunsOff, engine_modules.ModuleExecutionError)


# ── 2. The choke point ───────────────────────────────────────────────────────


class TestTheChokePoint:
    @pytest.mark.parametrize(("raw", "org"), [
        ("", ORG_A),
        (ORG_B, ORG_A),
        ("*", None),
    ])
    def test_a_refused_call_spawns_nothing(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, raw: str, org: str | None,
    ) -> None:
        _flag(monkeypatch, raw)
        with pytest.raises(ModuleRunsOff):
            asyncio.run(engine_modules.run_module_code(CODE, {"x": 1}, organization_id=org))
        assert spawns == []

    def test_the_switch_runs_before_the_validation(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list,
    ) -> None:
        """A refused org learns nothing about its code, and nothing runs."""
        _flag(monkeypatch, "")
        with pytest.raises(ModuleRunsOff):
            asyncio.run(engine_modules.run_module_code(
                "import os\n", {}, organization_id=ORG_A,
            ))
        assert spawns == []

    @pytest.mark.parametrize("raw", [ORG_A, "*", f"{ORG_B},{ORG_A}"])
    def test_a_listed_org_runs(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, raw: str,
    ) -> None:
        _flag(monkeypatch, raw)
        out = asyncio.run(engine_modules.run_module_code(CODE, {"x": 7}, organization_id=ORG_A))
        assert out == {"echo": 7}
        assert len(spawns) == 1

    def test_the_org_is_a_required_keyword(self) -> None:
        """A caller that forgets the org fails at once, and never runs."""
        with pytest.raises(TypeError):
            asyncio.run(engine_modules.run_module_code(CODE, {}))  # type: ignore[call-arg]

    def test_the_three_run_paths_are_the_only_callers(self) -> None:
        """A new caller passes the choke point too. This names the paths, so
        a fourth one is a decision that a review sees."""
        callers: list[str] = []
        for base in ("apps", "packages"):
            for path in sorted((ROOT / base).rglob("*.py")):
                if "node_modules" in path.parts or ".venv" in path.parts:
                    continue
                src = path.read_text(encoding="utf-8", errors="replace")
                if "run_module_code(" not in src:
                    continue
                for node in ast.walk(ast.parse(src)):
                    if (
                        isinstance(node, ast.Call)
                        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "run_module_code"
                    ):
                        kw = {k.arg for k in node.keywords}
                        assert "organization_id" in kw, f"{path}:{node.lineno}"
                        callers.append(path.relative_to(ROOT).as_posix())
        assert sorted(callers) == [
            "apps/services/gateway/gateway/routes/workflows/engine/handlers.py",
            "apps/services/gateway/gateway/routes/workflows/modules.py",
            "apps/services/gateway/gateway/routes/workflows/modules.py",
        ]


# ── 3 to 6. The two test routes ──────────────────────────────────────────────


class _Db:
    """The session of the saved-module route. It counts each open."""

    def __init__(self) -> None:
        self.opened = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        return SimpleNamespace(fetchone=lambda: SimpleNamespace(code=CODE))


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _Db:
    from gateway.routes.workflows import modules as routes

    fake = _Db()

    @asynccontextmanager
    async def _session(organization_id: Any = None):
        fake.opened += 1
        yield fake

    monkeypatch.setattr(routes, "_tenant_session", _session)
    return fake


def _client(org: str | None) -> TestClient:
    from gateway.routes.workflows import router

    user = UserContext(
        email="ana@alpha.example", role=UserRole.EMPLOYEE, organization_id=org,
        access=build_access(["feature:workflows"], roles=["member"]),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


_DRAFT = ("/workflows/modules/test", {"code": CODE, "inputs": {"x": 3}})
_SAVED = ("/workflows/modules/11111111-1111-1111-1111-111111111111/test", {"inputs": {"x": 3}})


class TestTheRoutes:
    @pytest.mark.parametrize(("path", "body"), [_DRAFT, _SAVED], ids=["draft", "saved"])
    @pytest.mark.parametrize(("raw", "org"), [("", ORG_A), (ORG_B, ORG_A), ("*", None)])
    def test_a_refused_org_gets_403_and_no_child(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, db: _Db,
        path: str, body: dict, raw: str, org: str | None,
    ) -> None:
        _flag(monkeypatch, raw)
        res = _client(org).post(path, json=body)
        assert res.status_code == 403, res.text
        assert res.json()["detail"] == {"code": MODULE_RUNS_OFF, "message": MODULE_RUNS_OFF_MESSAGE}
        assert spawns == []
        # The switch is the first line, so a refused call opens no session.
        assert db.opened == 0

    def test_a_refused_org_gets_403_for_code_that_fails_validation(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, db: _Db,
    ) -> None:
        _flag(monkeypatch, "")
        res = _client(ORG_A).post("/workflows/modules/test", json={"code": "import os\n"})
        assert res.status_code == 403
        assert res.json()["detail"]["code"] == MODULE_RUNS_OFF

    @pytest.mark.parametrize(("path", "body"), [_DRAFT, _SAVED], ids=["draft", "saved"])
    @pytest.mark.parametrize("raw", [ORG_A, "*"])
    def test_a_listed_org_still_runs(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, db: _Db,
        path: str, body: dict, raw: str,
    ) -> None:
        _flag(monkeypatch, raw)
        res = _client(ORG_A).post(path, json=body)
        assert res.status_code == 200, res.text
        assert res.json() == {"ok": True, "output": {"echo": 3}}
        assert len(spawns) == 1

    @pytest.mark.parametrize(("path", "body"), [_DRAFT, _SAVED], ids=["draft", "saved"])
    def test_the_org_comes_from_the_session_never_the_request(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, db: _Db, path: str, body: dict,
    ) -> None:
        _flag(monkeypatch, ORG_B)
        res = _client(ORG_A).post(
            f"{path}?organization_id={ORG_B}",
            json={**body, "organization_id": ORG_B},
            headers={"X-Organization-Id": ORG_B},
        )
        assert res.status_code == 403
        assert spawns == []

    def test_the_choke_point_holds_behind_the_route_check(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, db: _Db,
    ) -> None:
        """The flag turns off between the route check and the run: the
        runner still refuses, and the route still answers 403."""
        from gateway.routes.workflows import modules as routes

        monkeypatch.setattr(routes, "_require_module_runs", lambda user: None)
        _flag(monkeypatch, "")
        res = _client(ORG_A).post(*_DRAFT[:1], json=_DRAFT[1])
        assert res.status_code == 403
        assert res.json()["detail"]["code"] == MODULE_RUNS_OFF
        assert spawns == []

    def test_crud_and_generate_have_no_switch(self) -> None:
        """Only the two test routes read the switch (STU-Q15a item 5)."""
        from gateway.routes.workflows import modules as routes

        src = (ROOT / "apps/services/gateway/gateway/routes/workflows/modules.py").read_text(
            encoding="utf-8",
        )
        tree = ast.parse(src)
        gated = sorted(
            fn.name for fn in ast.walk(tree)
            if isinstance(fn, ast.AsyncFunctionDef)
            and "_require_module_runs" in ast.unparse(fn)
        )
        assert gated == ["test_module", "test_saved_module"]
        assert routes.generate_module and routes.create_module


# ── 3 to 6. The workflow module node ─────────────────────────────────────────


def _services(org: str | None) -> NodeServices:
    async def _unused(*_a: Any) -> Any:  # pragma: no cover
        raise AssertionError("not reached")

    async def get_module_code(module_id: str) -> str | None:
        return CODE if module_id == "m1" else None

    return NodeServices(
        run_agent=_unused, run_tool=_unused, get_module_code=get_module_code,
        organization_id=org,
    )


_BLOCK = {
    "id": "clean", "type": "module",
    "config": {"module_id": "m1", "inputs": {"x": "{{trigger.x}}"}},
}


def _graph() -> dict[str, Any]:
    def node(nid: str, ntype: str, config: dict | None = None) -> dict:
        return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
                "data": {"label": nid, "config": config or {}}}

    return {
        "nodes": [
            node("start", "trigger"),
            node("clean", "module", _BLOCK["config"]),
            node("done", "output", {"value": "{{clean.echo}}"}),
        ],
        "edges": [
            {"id": "a", "source": "start", "target": "clean"},
            {"id": "b", "source": "clean", "target": "done"},
        ],
    }


class TestTheNode:
    @pytest.mark.parametrize(("raw", "org"), [("", ORG_A), (ORG_B, ORG_A), ("*", None)])
    def test_a_refused_node_fails_with_the_message_and_spawns_nothing(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, raw: str, org: str | None,
    ) -> None:
        _flag(monkeypatch, raw)
        state = {"trigger": {"x": 1}}
        with pytest.raises(NodeExecutionError) as exc:
            asyncio.run(execute_node(_BLOCK, state, _services(org)))
        assert str(exc.value) == MODULE_RUNS_OFF_MESSAGE
        assert spawns == []

    def test_the_run_fails_visibly_and_ends(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list,
    ) -> None:
        _flag(monkeypatch, "")
        events: list[tuple[str, str, dict]] = []
        outcome = asyncio.run(asyncio.wait_for(
            execute_workflow(
                compile_graph(_graph(), known_modules={"m1"}),
                {"x": 1},
                _services(ORG_A),
                emit=lambda nid, status, detail: events.append((nid, status, detail)),
            ),
            timeout=30,
        ))
        assert outcome.status == "failed"
        assert outcome.node_results["clean"] == {"status": "error", "error": MODULE_RUNS_OFF_MESSAGE}
        assert outcome.node_results["done"]["status"] == "skipped"
        assert ("clean", "error", {"error": MODULE_RUNS_OFF_MESSAGE}) in events
        assert spawns == []

    @pytest.mark.parametrize("raw", [ORG_A, "*"])
    def test_a_listed_org_still_runs_the_node(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list, raw: str,
    ) -> None:
        _flag(monkeypatch, raw)
        outcome = asyncio.run(execute_workflow(
            compile_graph(_graph(), known_modules={"m1"}), {"x": 5}, _services(ORG_A),
        ))
        assert outcome.status == "succeeded"
        assert outcome.outputs == [5]
        assert len(spawns) == 1

    def test_the_org_is_the_run_s_never_node_input(
        self, monkeypatch: pytest.MonkeyPatch, spawns: list,
    ) -> None:
        _flag(monkeypatch, ORG_B)
        block = {**_BLOCK, "config": {**_BLOCK["config"], "organization_id": ORG_B}}
        state = {"trigger": {"x": 1, "organization_id": ORG_B}}
        with pytest.raises(NodeExecutionError):
            asyncio.run(execute_node(block, state, _services(ORG_A)))
        assert spawns == []

    def test_the_run_s_services_carry_the_run_s_org(self) -> None:
        from gateway.routes.workflows import service

        assert service.build_node_services("workflow:x", "wf-1", ORG_A).organization_id == ORG_A
        assert service.build_node_services("workflow:x", "wf-1", None).organization_id is None
