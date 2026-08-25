"""Tests for the per-session workspace override (Custom Apps builder sessions)."""
from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from apps.services.orchestrator.orchestrator.executor import (
    _resolve_effective_agent_dir,
    _session_workspace_override,
)


def _fake_acb_graph(workspace_path: str | None) -> types.ModuleType:
    """Build a stub acb_graph module whose chat_session lookup returns a path."""
    mod = types.ModuleType("acb_graph")

    class _Row:
        def __init__(self, wp: str | None) -> None:
            self.workspace_path = wp

    class _Session:
        def execute(self, *_a, **_kw):
            class _Res:
                @staticmethod
                def fetchone():
                    return _Row(workspace_path)
            return _Res()

    @contextmanager
    def get_session():
        yield _Session()

    @contextmanager
    def tenant_session(_organization_id=None):
        yield _Session()

    mod.get_session = get_session  # type: ignore[attr-defined]
    # WS-29 acb_graph slice 3: the executor now reads the tenant-bind flag and
    # (when ON) the tenant_session seam through acb_graph, so this stub must
    # mirror that grown API. Default OFF → the override reads via get_session.
    mod.tenant_bind_enabled = lambda: False  # type: ignore[attr-defined]
    mod.tenant_session = tenant_session  # type: ignore[attr-defined]
    return mod


@pytest.fixture()
def apps_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the custom-apps root at a temp dir via the settings fallback."""
    import apps.services.orchestrator.orchestrator.executor as ex

    class _Settings:
        agents_clone_dir = str(tmp_path)
        custom_apps_root = ""

    monkeypatch.setattr(ex, "get_settings", lambda: _Settings())
    root = tmp_path / "custom_apps"
    root.mkdir()
    return root


def _with_db(monkeypatch: pytest.MonkeyPatch, workspace_path: str | None) -> None:
    monkeypatch.setitem(sys.modules, "acb_graph", _fake_acb_graph(workspace_path))


def test_override_requires_opt_in(apps_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = apps_root / "filament-tracker"
    ws.mkdir()
    _with_db(monkeypatch, str(ws))
    assert _session_workspace_override("t1", {}) is None
    assert _session_workspace_override(None, {"allow_session_workspace": True}) is None


def test_override_accepts_contained_workspace(
    apps_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = apps_root / "filament-tracker"
    ws.mkdir()
    _with_db(monkeypatch, str(ws))
    got = _session_workspace_override("t1", {"allow_session_workspace": True})
    assert got == str(ws.resolve())


@pytest.mark.parametrize(
    "candidate",
    [
        "/etc",                              # absolute escape
        "{root}/..",                         # parent traversal
        "{root}",                            # the root itself is not an app
        "{root}/does-not-exist",             # stale path
    ],
)
def test_override_rejects_escapes(
    apps_root: Path, monkeypatch: pytest.MonkeyPatch, candidate: str
) -> None:
    _with_db(monkeypatch, candidate.format(root=apps_root))
    assert _session_workspace_override("t1", {"allow_session_workspace": True}) is None


def test_override_rejects_symlink_escape(
    apps_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = apps_root / "sneaky"
    link.symlink_to(outside)
    _with_db(monkeypatch, str(link))
    assert _session_workspace_override("t1", {"allow_session_workspace": True}) is None


def test_resolve_effective_dir_precedence(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    override = tmp_path / "app-ws"
    override.mkdir()
    # Session override wins over both the clone and config workspace_root.
    assert (
        _resolve_effective_agent_dir(
            clone, {"workspace_root": str(tmp_path)}, session_override=str(override)
        )
        == str(override)
    )
    # Without an override the existing behaviour is unchanged.
    assert _resolve_effective_agent_dir(clone, {}) == str(clone)
