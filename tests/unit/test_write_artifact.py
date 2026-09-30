"""Unit tests for write_artifact's non-destructive (no-clobber) behavior.

write_artifact must never silently overwrite an existing file (a user upload in
inputs/, or a prior artifact) — it uniquifies to "name (1).ext" unless the caller
explicitly passes overwrite=True.
"""
from __future__ import annotations

import asyncio
from importlib import import_module

# acb_skills/__init__ rebinds the name to the function, so import the module.
wa = import_module("acb_skills.write_artifact")


async def _noop(**_kwargs) -> None:
    return None


def _set_ctx(tmp_path) -> None:
    wa.bind_artifact_context(**
        {"session_id": "sess-1", "workspace_root": str(tmp_path)}
    )


def test_first_write_keeps_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)
    res = asyncio.run(wa.write_artifact("report.md", "v1"))
    assert res["path"] == "outputs/report.md"
    assert (tmp_path / "outputs" / "report.md").read_text(encoding="utf-8") == "v1"


def test_second_write_does_not_clobber(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)
    asyncio.run(wa.write_artifact("report.md", "original"))
    res = asyncio.run(wa.write_artifact("report.md", "new"))
    # Original preserved; the new write went to a uniquified name.
    assert res["path"] == "outputs/report (1).md"
    assert (tmp_path / "outputs" / "report.md").read_text(encoding="utf-8") == "original"
    assert (tmp_path / "outputs" / "report (1).md").read_text(encoding="utf-8") == "new"
    assert res["download_url"].endswith("path=outputs/report (1).md")


def test_overwrite_true_replaces_in_place(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)
    asyncio.run(wa.write_artifact("report.md", "original"))
    res = asyncio.run(wa.write_artifact("report.md", "replaced", overwrite=True))
    assert res["path"] == "outputs/report.md"
    assert (tmp_path / "outputs" / "report.md").read_text(encoding="utf-8") == "replaced"
    assert not (tmp_path / "outputs" / "report (1).md").exists()


def test_does_not_overwrite_user_upload_in_inputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "data.csv").write_text("user-uploaded")
    res = asyncio.run(wa.write_artifact("inputs/data.csv", "agent-version"))
    # The user's original upload is untouched.
    assert (tmp_path / "inputs" / "data.csv").read_text(encoding="utf-8") == "user-uploaded"
    assert res["path"] == "inputs/data (1).csv"
