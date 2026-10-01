"""Workspace path-containment guard (audit BO-14).

write_artifact / save_note / recall_notes turn an agent-supplied path into a
filesystem path. _normalise_path only strips a LEADING ``/.``; an EMBEDDED
``..`` (``outputs/../../etc/x``) or an absolute path used to escape the
workspace — an arbitrary-file write (write_artifact/save_note) and arbitrary
read (recall_notes). resolve_in_workspace now fails those closed.
"""
from __future__ import annotations

import asyncio

import pytest

from acb_skills.note_tools import recall_notes, save_note
from acb_skills.write_artifact import (
    derive_artifact_context,
    resolve_in_workspace,
    write_artifact,
)


def test_resolve_in_workspace_allows_contained_paths(tmp_path):
    assert resolve_in_workspace(tmp_path, "outputs/a.md") is not None
    assert resolve_in_workspace(tmp_path, "agent-data/notes/x.md") is not None


def test_resolve_in_workspace_rejects_traversal_and_absolute(tmp_path):
    assert resolve_in_workspace(tmp_path, "outputs/../../etc/passwd") is None
    assert resolve_in_workspace(tmp_path, "../../../etc/passwd") is None
    assert resolve_in_workspace(tmp_path, "/etc/passwd") is None


def _set_ws(monkeypatch, tmp_path):
    derive_artifact_context(workspace_root=str(tmp_path))
    derive_artifact_context(session_id="sess-test")


def test_write_artifact_refuses_traversal(tmp_path, monkeypatch):
    _set_ws(monkeypatch, tmp_path)
    res = asyncio.run(
        write_artifact("outputs/../../escape.txt", "pwn")
    )
    assert "error" in res
    # Nothing was written outside the workspace.
    assert not (tmp_path.parent / "escape.txt").exists()


def test_write_artifact_writes_contained_file(tmp_path, monkeypatch):
    _set_ws(monkeypatch, tmp_path)
    res = asyncio.run(write_artifact("report.md", "hello"))
    assert res.get("path") == "outputs/report.md"
    assert (tmp_path / "outputs" / "report.md").read_text(encoding="utf-8") == "hello"


def test_save_note_refuses_traversal(tmp_path, monkeypatch):
    _set_ws(monkeypatch, tmp_path)
    out = asyncio.run(
        save_note("agent-data/../../escape.md", "secret")
    )
    assert out.startswith("Refused")
    assert not (tmp_path.parent / "escape.md").exists()


def test_recall_notes_refuses_traversal_read(tmp_path, monkeypatch):
    _set_ws(monkeypatch, tmp_path)
    # Plant a file outside the workspace; recall_notes must not read it. Use an
    # EMBEDDED ".." (a leading "../" is separately neutralised by lstrip('/.')).
    secret = tmp_path.parent / "outside_secret.md"
    secret.write_text("TOP SECRET")
    out = asyncio.run(recall_notes("agent-data/../../outside_secret.md"))
    assert out.startswith("Refused")
    assert "TOP SECRET" not in out


def test_save_and_recall_note_roundtrip_contained(tmp_path, monkeypatch):
    _set_ws(monkeypatch, tmp_path)
    asyncio.run(save_note("NOTES.md", "a durable fact"))
    got = asyncio.run(recall_notes("NOTES.md"))
    assert "a durable fact" in got


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
