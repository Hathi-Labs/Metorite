"""Agent dependency install — RCE guard (BO-7 fast pass).

`_install_agent_deps` runs a uv install from an agent repo's requirements.txt,
ahead of any tool-call permission gate. Since WS-49 BH-7 the target is
agent-site, not the shared venv (`test_agent_deps_target.py` owns that). An
sdist's setup.py / PEP 517 build backend can execute arbitrary code during
that install. These lock that installs default to wheels-only
(--only-binary=:all:) and that the explicit settings opt-out still works.
"""
from __future__ import annotations

import subprocess
import types

import pytest
from acb_skills import agent_site
from acb_skills.loader import _install_agent_deps


class _FakeCompleted:
    returncode = 0
    stdout = "six==1.17.0\n"
    stderr = ""


@pytest.fixture(autouse=True)
def _agent_site(tmp_path_factory, monkeypatch):
    """WS-49 BH-7: the install needs the unit's two dirs. Give it temp ones."""
    root = tmp_path_factory.mktemp("agent-site-root")
    (root / "state").mkdir()
    (root / "cache").mkdir()
    monkeypatch.setattr(agent_site, "STATE_ROOT", root / "state")
    monkeypatch.setattr(agent_site, "AGENT_SITE", root / "state" / "agent-site")
    monkeypatch.setattr(agent_site, "CACHE_ROOT", root / "cache")
    monkeypatch.setattr(agent_site, "CONSTRAINTS", root / "cache" / "constraints.txt")
    monkeypatch.setattr(agent_site, "find_uv", lambda: "/stub/uv")
    monkeypatch.setattr(agent_site, "_frozen", {})


def _installs(captured: list[list[str]]) -> list[list[str]]:
    return [c for c in captured if c[1:3] == ["pip", "install"]]


def _settings(*, allow_source_builds: bool = False) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        agent_deps_allow_source_builds=allow_source_builds,
    )


def test_default_install_is_wheels_only(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("bh7-fake-agent-pkg==1.0\n", encoding="utf-8")

    captured: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    _install_agent_deps(tmp_path, _settings())

    assert _installs(captured), "pip/uv install was never invoked"
    cmd = _installs(captured)[0]
    assert "--only-binary" in cmd
    assert cmd[cmd.index("--only-binary") + 1] == ":all:"


def test_source_builds_opt_out_omits_the_flag(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("bh7-fake-agent-pkg==1.0\n", encoding="utf-8")

    captured: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    _install_agent_deps(tmp_path, _settings(allow_source_builds=True))

    assert _installs(captured), "pip/uv install was never invoked"
    assert "--only-binary" not in _installs(captured)[0]


def test_no_declared_deps_never_invokes_install(tmp_path, monkeypatch):
    captured: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    _install_agent_deps(tmp_path, _settings())

    assert captured == []
