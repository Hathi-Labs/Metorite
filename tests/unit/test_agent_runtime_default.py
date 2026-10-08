"""WS43-F18 — a repo agent defaults to MAF, and a new Copilot agent is refused.

WS-43n (D84, D92). Spec: ``project-docs/specs/maf_coding_engine.md`` §15.5
and the WS-43n done-when. Three halves:

1. **Registration** (``POST /agent``, ``register_agent``). The runtime comes
   from the repo's own ``config.json`` and never from the request. A repo
   that declares nothing gets ``maf``. A repo that declares
   ``github-copilot`` gets HTTP 400 with the migration text, and nothing is
   saved. The GitHub fetch of ``config.json`` fails closed.
2. **The back-fill** in ``list_agents``. A legacy row with no runtime gets
   ``maf``, also when it came from a GitHub repo.
3. **The loader** (``LoadedAgent.build_agents``). Until WS-43r, a Copilot
   agent still loads, with one deprecation line that names it. With the
   WS-43r switch set, it raises ``AgentRuntimeUnsupported``. A MAF agent
   never logs the line.

Hermetic: no SQL runs, no network is reached, and no model is called. R8
binds nothing here.

Mutations this file catches (R7), each run red before the change:

* the registration default goes back to ``github-copilot`` for a repo URL ->
  ``test_a_github_repo_that_declares_nothing_gets_maf``;
* the 400 check is removed, or reads only when metadata is missing ->
  ``test_a_copilot_repo_is_refused_with_400`` (both sources) and
  ``test_full_metadata_does_not_skip_the_runtime_check``;
* a network fault registers the agent blind ->
  ``test_a_github_fault_fails_closed``;
* the back-fill goes back to ``github-copilot`` ->
  ``test_the_back_fill_gives_maf``;
* the loader stops flagging a Copilot agent, or logs it each run ->
  ``test_an_allowlisted_copilot_agent_still_loads_with_one_line``;
* the WS-43r switch does not refuse ->
  ``test_the_ws43r_switch_refuses_a_copilot_agent``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import loader
from fastapi import BackgroundTasks, HTTPException

from tests.unit._native_maf_harness import REPO_ROOT

agent_routes = pytest.importorskip(
    "gateway.routes.agent", reason="gateway not installed in this environment",
)

_USER = SimpleNamespace(email="owner@example.com")


# ── Registration ────────────────────────────────────────────────────────────


@pytest.fixture
def saved(monkeypatch) -> list[list[dict]]:
    """Replace the registry store. Each save is recorded, so a refusal can
    prove that it saved nothing."""
    calls: list[list[dict]] = []
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(
        agent_routes, "_save_dynamic_agents", lambda agents: calls.append(list(agents)),
    )
    return calls


def _register(**fields: Any) -> dict:
    req = agent_routes.RegisterAgentRequest(**fields)
    return asyncio.run(agent_routes.register_agent(req, BackgroundTasks(), user=_USER))


def _local_repo(tmp_path: Path, config: dict | None) -> Path:
    repo = tmp_path / "agent-repo"
    repo.mkdir()
    if config is not None:
        (repo / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return repo


class _FakeResponse:
    def __init__(self, status_code: int, body: object = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


def _fake_github(monkeypatch, *, status_code: int = 200, body: object = None, boom: bool = False):
    """Replace ``httpx.AsyncClient`` so the config fetch reaches no network."""
    import httpx

    seen: list[str] = []

    class _Client:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a) -> None:
            return None

        async def get(self, url: str, headers=None):
            seen.append(url)
            if boom:
                raise httpx.ConnectError("no route to GitHub")
            return _FakeResponse(status_code, body)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return seen


def test_the_request_carries_no_runtime_field() -> None:
    fields = agent_routes.RegisterAgentRequest.model_fields
    assert not {"runtime", "agent_runtime"} & set(fields), (
        "WS-43n done-when 2: the runtime comes from the repo's config.json only"
    )


def test_a_local_repo_that_declares_nothing_gets_maf(tmp_path, saved) -> None:
    repo = _local_repo(tmp_path, {"description": "d"})
    entry = _register(name="new-agent", local_path=str(repo))
    assert entry["agent_runtime"] == "maf"
    assert saved and saved[-1][-1]["agent_runtime"] == "maf"


def test_a_github_repo_that_declares_nothing_gets_maf(monkeypatch, saved) -> None:
    _fake_github(monkeypatch, status_code=200, body={"description": "d"})
    entry = _register(name="new-agent", repo_url="https://github.com/acme/agent-new")
    assert entry["agent_runtime"] == "maf"


def test_a_github_repo_with_no_config_gets_maf(monkeypatch, saved) -> None:
    seen = _fake_github(monkeypatch, status_code=404)
    entry = _register(name="new-agent", repo_url="acme/agent-new")
    assert entry["agent_runtime"] == "maf"
    assert len(seen) == 3, "main, master and HEAD are each tried"


def test_a_request_runtime_is_ignored(tmp_path, saved) -> None:
    """A client cannot ask for the Copilot runtime in the body."""
    repo = _local_repo(tmp_path, {"description": "d"})
    entry = _register(
        name="new-agent", local_path=str(repo), agent_runtime="github-copilot",
    )
    assert entry["agent_runtime"] == "maf"


@pytest.mark.parametrize("label", ["github-copilot", "GitHub-Copilot", "copilot", "copilot-sdk"])
def test_a_copilot_repo_is_refused_with_400_local(tmp_path, saved, label) -> None:
    repo = _local_repo(tmp_path, {"description": "d", "runtime": label})
    with pytest.raises(HTTPException) as exc:
        _register(name="new-agent", local_path=str(repo))
    assert exc.value.status_code == 400
    assert loader.COPILOT_MIGRATION_TEXT in exc.value.detail
    assert not saved, "a refused agent must not be saved"


def test_a_copilot_repo_is_refused_with_400_github(monkeypatch, saved) -> None:
    _fake_github(monkeypatch, status_code=200, body={"runtime": "github-copilot"})
    with pytest.raises(HTTPException) as exc:
        _register(name="new-agent", repo_url="https://github.com/acme/agent-new")
    assert exc.value.status_code == 400
    assert loader.COPILOT_MIGRATION_TEXT in exc.value.detail
    assert not saved


def test_full_metadata_does_not_skip_the_runtime_check(monkeypatch, tmp_path, saved) -> None:
    """The old code read config.json only when the request left the
    description or the integrations empty. The runtime check must not."""
    full = {"description": "set", "integrations": ["zoho-crm"]}
    seen = _fake_github(monkeypatch, status_code=200, body={"runtime": "github-copilot"})
    with pytest.raises(HTTPException) as exc:
        _register(name="new-agent", repo_url="acme/agent-new", **full)
    assert exc.value.status_code == 400
    assert seen, "config.json was not fetched"

    repo = _local_repo(tmp_path, {"runtime": "github-copilot"})
    with pytest.raises(HTTPException) as exc:
        _register(name="new-agent-2", local_path=str(repo), **full)
    assert exc.value.status_code == 400
    assert not saved


@pytest.mark.parametrize("kind", ["exception", "server-error"])
def test_a_github_fault_fails_closed(monkeypatch, saved, kind) -> None:
    if kind == "exception":
        _fake_github(monkeypatch, boom=True)
    else:
        _fake_github(monkeypatch, status_code=503)
    with pytest.raises(HTTPException) as exc:
        _register(name="new-agent", repo_url="acme/agent-new")
    assert exc.value.status_code == 502
    assert not saved, "an agent of unknown runtime must not be saved"


# ── The back-fill ───────────────────────────────────────────────────────────


def test_the_back_fill_gives_maf(monkeypatch) -> None:
    legacy = [
        {"name": "legacy-gh", "repo_name": "acme/agent-old", "agent_runtime": None},
        {"name": "legacy-local", "local_path": "/x", "agent_runtime": ""},
        # A row that names a runtime keeps it (§15.5 step 4).
        {"name": "legacy-copilot", "repo_name": "acme/agent-c", "agent_runtime": "github-copilot"},
    ]
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [dict(a) for a in legacy])
    monkeypatch.setattr(agent_routes, "_declared_runtime", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_routes, "_load_agent_aliases", lambda: {})

    async def _no_git(_path: str) -> int:
        return 0

    monkeypatch.setattr(agent_routes, "_git_behind_count", _no_git)
    user = SimpleNamespace(has_permission=lambda _p: True, can_run_agent=lambda _n: True)

    by_name = {a["name"]: a for a in asyncio.run(agent_routes.list_agents(user=user))}
    assert by_name["legacy-gh"]["agent_runtime"] == "maf"
    assert by_name["legacy-local"]["agent_runtime"] == "maf"
    assert by_name["legacy-copilot"]["agent_runtime"] == "github-copilot"


# ── The loader ──────────────────────────────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw: Any) -> None:
        self.warnings.append((event, kw))

    def info(self, *_a: Any, **_k: Any) -> None:
        pass


@pytest.fixture
def log(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(loader, "_log", rec)
    monkeypatch.setattr(loader, "_copilot_deprecation_logged", set())
    return rec


def _deprecations(rec: _Recorder) -> list[dict]:
    return [kw for ev, kw in rec.warnings if ev == "loader.copilot_agent_deprecated"]


def _loaded(agent_name: str, agent_dir: Path) -> loader.LoadedAgent:
    return loader.LoadedAgent(
        agent_name=agent_name,
        run_id=str(uuid.uuid4()),
        agent_dir=agent_dir,
        graph_module=None,
        injected_paths=[],
        module_name=f"_ws43n_{uuid.uuid4().hex}",
    )


def _fake_agent_repo(tmp_path: Path, *, copilot: bool) -> Path:
    """An agents.py whose agent has the Copilot shape, or the MAF shape."""
    repo = tmp_path / ("copilot" if copilot else "maf")
    repo.mkdir()
    attr = "{'model': 'x'}" if copilot else "None"
    (repo / "agents.py").write_text(
        "class _Agent:\n"
        f"    _default_options = {attr}\n"
        "def build_agents():\n"
        "    return [_Agent()]\n",
        encoding="utf-8",
    )
    return repo


def test_a_maf_agent_logs_no_deprecation(tmp_path, log) -> None:
    built = _loaded("maf-agent", _fake_agent_repo(tmp_path, copilot=False)).build_agents()
    assert len(built) == 1
    assert _deprecations(log) == []


def test_a_copilot_agent_logs_one_line_per_process(tmp_path, log) -> None:
    repo = _fake_agent_repo(tmp_path, copilot=True)
    for _ in range(3):
        assert _loaded("old-agent", repo).build_agents()
    lines = _deprecations(log)
    assert len(lines) == 1, lines
    assert lines[0]["agent"] == "old-agent"
    assert "15.5" in lines[0]["spec"]


def test_the_ws43r_switch_refuses_a_copilot_agent(tmp_path, log, monkeypatch) -> None:
    monkeypatch.setattr(loader, "COPILOT_AGENTS_SUPPORTED", False)
    with pytest.raises(loader.AgentRuntimeUnsupported) as exc:
        _loaded("old-agent", _fake_agent_repo(tmp_path, copilot=True)).build_agents()
    assert loader.COPILOT_MIGRATION_TEXT in str(exc.value)
    assert isinstance(exc.value, loader.AgentLoadError), "callers catch AgentLoadError"
    # A MAF agent still loads with the switch set.
    assert _loaded("maf-agent", _fake_agent_repo(tmp_path, copilot=False)).build_agents()


def test_the_switch_is_on_until_ws43r() -> None:
    """Ship state: the Copilot agents still run. WS-43r flips this."""
    assert loader.COPILOT_AGENTS_SUPPORTED is True


@pytest.mark.parametrize(
    ("name", "rel_dir"),
    [
        ("task-manager", "apps/agents/agent-task-manager"),
        ("app-builder", "apps/agents/agent-app-builder"),
    ],
)
def test_an_allowlisted_copilot_agent_still_loads_with_one_line(log, name, rel_dir) -> None:
    """The real factories of two WS43-F31 allowlist entries load unchanged."""
    pytest.importorskip("agent_framework_github_copilot")
    built = _loaded(name, REPO_ROOT / rel_dir).build_agents()
    assert built and loader.is_copilot_agent(built[0])
    lines = _deprecations(log)
    assert [line["agent"] for line in lines] == [name]
