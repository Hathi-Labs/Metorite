"""A skill that the venv provides is not cloned (production, 2026-10-18).

The box logged ``loader.deps_provided_by_venv`` and then
``loader.clone_new`` and ``loader.skill_clone_failed`` for
``FracktalWorks/skill-my-tasks`` and ``skill-projects`` on every start of
their agents. Both are uv workspace members, so the gateway's venv already
holds them. The clone could only fail (no valid token) or shadow the
monorepo's copy with an older one from GitHub.

``loader._attach_skill_repos`` now asks ``agent_site.venv_version`` first. A
skill the venv provides gets no clone, no pull and no ``sys.path`` entry.
Any other skill is cloned as before, because a repo agent's own skill is not
in the venv. Both paths of ``load_agent`` (a local path, and a GitHub repo)
call the one helper.

Hermetic: no git, no network. R8 binds nothing here.

Mutations this suite catches (R7), each run red by hand:

* the venv check is removed -> ``test_a_venv_skill_is_not_cloned``;
* the venv check skips every skill -> ``test_a_skill_the_venv_lacks_is_cloned``;
* a load path keeps its own loop -> ``test_both_load_paths_use_the_helper``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from acb_skills import agent_site, loader

REPO = Path(__file__).resolve().parents[2]


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    warning = info


@pytest.fixture
def log(monkeypatch) -> _Log:
    rec = _Log()
    monkeypatch.setattr(loader, "_log", rec)
    return rec


def _attach(repos: list[str], tmp_path: Path) -> list[str]:
    return loader._attach_skill_repos(
        repos, agent_name="projects-assistant", org="acme", token=None,
        cache_root=tmp_path, settings=object(),
    )


def test_a_venv_skill_is_not_cloned(monkeypatch, tmp_path, log) -> None:
    cloned: list[str] = []
    monkeypatch.setattr(agent_site, "venv_version", lambda name: "0.1.0")
    monkeypatch.setattr(loader, "_ensure_repo", lambda name, **_k: cloned.append(name))
    before = list(sys.path)

    assert _attach(["skill-projects", "skill-my-tasks"], tmp_path) == []
    assert cloned == [], "a skill the venv provides was cloned"
    assert sys.path == before, "a venv skill must not go before the venv"
    provided = [kw["skill"] for ev, kw in log.events if ev == "loader.skill_provided_by_venv"]
    assert provided == ["skill-projects", "skill-my-tasks"]


def test_a_skill_the_venv_lacks_is_cloned(monkeypatch, tmp_path, log) -> None:
    skill_dir = tmp_path / "skill-acme"
    (skill_dir / "src").mkdir(parents=True)
    monkeypatch.setattr(agent_site, "venv_version", lambda name: None)
    monkeypatch.setattr(loader, "_ensure_repo", lambda name, **_k: skill_dir)

    paths = _attach(["skill-acme"], tmp_path)
    try:
        assert paths == [str(skill_dir / "src")]
        assert sys.path[0] == str(skill_dir / "src")
    finally:
        for p in paths:
            sys.path.remove(p)


def test_a_failed_clone_still_loads(monkeypatch, tmp_path, log) -> None:
    def _boom(name: str, **_k: Any) -> Path:
        raise loader.AgentLoadError("git clone failed")

    monkeypatch.setattr(agent_site, "venv_version", lambda name: None)
    monkeypatch.setattr(loader, "_ensure_repo", _boom)
    assert _attach(["skill-acme"], tmp_path) == []
    assert [ev for ev, _ in log.events] == ["loader.skill_clone_failed"]


def test_the_real_workspace_skills_are_provided() -> None:
    """The two skills of the production log are members of the venv here.
    On the box the same ``uv sync`` installs them."""
    pytest.importorskip("skill_projects")
    pytest.importorskip("skill_my_tasks")
    assert agent_site.venv_version("skill-projects") is not None
    assert agent_site.venv_version("skill-my-tasks") is not None


def test_both_load_paths_use_the_helper(monkeypatch, tmp_path) -> None:
    """``load_agent`` attaches skills through the helper, by a local path and
    by a GitHub repo. Only the agent's own repo reaches ``_ensure_repo``."""
    agent_dir = tmp_path / "repos" / "projects-assistant"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agents.py").write_text("def build_agents():\n    return []\n",
                                         encoding="utf-8")
    (agent_dir / "config.json").write_text(
        json.dumps({"skill_repos": ["skill-projects"]}), encoding="utf-8",
    )
    ensured: list[str] = []

    def _ensure(name: str, **_k: Any) -> Path:
        ensured.append(name)
        return agent_dir

    attached: list[list[str]] = []
    real_attach = loader._attach_skill_repos

    def _spy(repos: list[str], **kw: Any) -> list[str]:
        attached.append(list(repos))
        return real_attach(repos, **kw)

    settings = type("S", (), {"github_org": "acme", "agents_clone_dir": str(tmp_path)})()
    monkeypatch.setattr(loader, "get_settings", lambda: settings)
    monkeypatch.setattr(loader, "_get_auth_token", lambda _s: None)
    monkeypatch.setattr(loader, "_ensure_repo", _ensure)
    monkeypatch.setattr(loader, "_ensure_local_git_repo", lambda *_a, **_k: None)
    monkeypatch.setattr(loader, "_install_agent_deps", lambda *_a, **_k: None)
    monkeypatch.setattr(loader, "_attach_skill_repos", _spy)
    monkeypatch.setattr(agent_site, "venv_version", lambda name: "0.1.0")

    with loader.load_agent("projects-assistant", repo_name="acme/agent-projects"):
        pass
    assert ensured == ["agent-projects"], "only the agent's repo is cloned"

    source = tmp_path / "src-agent"
    source.mkdir()
    with loader.load_agent("projects-assistant", local_path=str(source)):
        pass
    assert ensured == ["agent-projects"], "the local path cloned a venv skill"
    assert attached == [["skill-projects"], ["skill-projects"]]
