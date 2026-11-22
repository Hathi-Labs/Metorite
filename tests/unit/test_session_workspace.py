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


def _fake_acb_graph(
    workspace_path: str | None, app_folders: list[str] | None = None,
) -> types.ModuleType:
    """Build a stub acb_graph module. The chat_session lookup returns a path.

    The ``apps`` lookup (task 2462) returns *app_folders*: the folders of the
    apps of the run's org. ``None`` means the folder of the session itself,
    so a test that is not about the owner check sees one matching app.
    """
    mod = types.ModuleType("acb_graph")
    folders = [workspace_path] if app_folders is None else app_folders

    class _Row:
        def __init__(self, wp: str | None) -> None:
            self.workspace_path = wp

    class _Session:
        def execute(self, statement, params=None, **_kw):
            sql = str(statement)

            class _Res:
                @staticmethod
                def fetchone():
                    return _Row(workspace_path)

                @staticmethod
                def fetchall():
                    assert "FROM apps" in sql and params == {"org": ORG}, (sql, params)
                    return [_Row(f) for f in folders]
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


ORG = "11111111-1111-1111-1111-111111111111"


def _with_db(
    monkeypatch: pytest.MonkeyPatch, workspace_path: str | None,
    app_folders: list[str] | None = None,
) -> None:
    import apps.services.orchestrator.orchestrator.executor as ex

    monkeypatch.setitem(sys.modules, "acb_graph", _fake_acb_graph(workspace_path, app_folders))
    # The run of thread t1 belongs to ORG, as ``_claim_run_org`` records it.
    monkeypatch.setitem(ex._RUN_ORG, "t1", ORG)


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


def test_override_refuses_a_folder_that_no_app_of_the_run_org_owns(
    apps_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 2462: org A deleted app ``foo``, and org B took the slug. The
    stale path of org A's chat now names org B's folder. The ``apps`` read of
    org A finds no row for it, so the run falls back to its own dir."""
    ws = apps_root / "foo"
    ws.mkdir()
    other = apps_root / "bar"
    other.mkdir()
    _with_db(monkeypatch, str(ws), app_folders=[str(other)])
    assert _session_workspace_override("t1", {"allow_session_workspace": True}) is None


def test_override_refuses_a_run_with_no_org(
    apps_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import apps.services.orchestrator.orchestrator.executor as ex

    ws = apps_root / "foo"
    ws.mkdir()
    _with_db(monkeypatch, str(ws))
    monkeypatch.delitem(ex._RUN_ORG, "t1")
    assert _session_workspace_override("t1", {"allow_session_workspace": True}) is None


def test_an_app_row_that_names_the_root_covers_no_app(
    apps_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = apps_root / "foo"
    ws.mkdir()
    _with_db(monkeypatch, str(ws), app_folders=[str(apps_root)])
    assert _session_workspace_override("t1", {"allow_session_workspace": True}) is None


def test_a_subdir_of_an_app_of_the_run_org_is_kept(
    apps_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = apps_root / "foo"
    sub = ws / "src"
    sub.mkdir(parents=True)
    _with_db(monkeypatch, str(sub), app_folders=[str(ws)])
    assert _session_workspace_override("t1", {"allow_session_workspace": True}) == str(
        sub.resolve())


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
    # H-201 part 3: without an override, a shared run never works in the
    # clone. With no tenant it is refused.
    from apps.services.orchestrator.orchestrator.executor import RunWorkspaceRefused

    with pytest.raises(RunWorkspaceRefused):
        _resolve_effective_agent_dir(clone, {})
