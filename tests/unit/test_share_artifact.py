"""Unit tests for the share_artifact agent tool.

share_artifact surfaces a file the agent already wrote (via shell/editor) as a
download/preview card in the chat — so Copilot SDK agents never hand-build URLs.
"""
from __future__ import annotations

import asyncio
from importlib import import_module

# Import the submodule explicitly: acb_skills/__init__ rebinds the name
# ``write_artifact`` to the *function*, which would shadow the module.
wa = import_module("acb_skills.write_artifact")


async def _noop(**_kwargs) -> None:  # avoid the gateway/SSE side effects in tests
    return None


def _set_ctx(tmp_path) -> None:
    wa.bind_artifact_context(**
        {"session_id": "sess-1", "workspace_root": str(tmp_path)}
    )


def test_share_single_file_returns_download_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "report.pdf").write_bytes(b"%PDF-1.4 hello")

    res = asyncio.run(wa.share_artifact("outputs/report.pdf"))

    assert len(res["artifacts"]) == 1
    art = res["artifacts"][0]
    assert art["path"] == "outputs/report.pdf"
    assert art["name"] == "report.pdf"
    assert art["size"] == len(b"%PDF-1.4 hello")
    assert art["mime_type"] == "application/pdf"
    assert (
        res["download_url"]
        == "/api/agent/workspace/sess-1/file?path=outputs/report.pdf"
    )


def test_share_root_level_file(tmp_path, monkeypatch) -> None:
    # Copilot agents often write to the working-dir root, not outputs/.
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)
    (tmp_path / "summary.xlsx").write_bytes(b"xlsxdata")

    res = asyncio.run(wa.share_artifact("summary.xlsx"))

    assert res["artifacts"][0]["path"] == "summary.xlsx"
    assert (
        res["download_url"]
        == "/api/agent/workspace/sess-1/file?path=summary.xlsx"
    )


def test_share_directory_expands_to_all_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)
    out = tmp_path / "outputs"
    out.mkdir()
    (out / "a.txt").write_text("a")
    (out / "b.csv").write_text("b")

    res = asyncio.run(wa.share_artifact("outputs"))

    paths = sorted(a["path"] for a in res["artifacts"])
    assert paths == ["outputs/a.txt", "outputs/b.csv"]


def test_share_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)

    res = asyncio.run(wa.share_artifact("../../etc/passwd"))

    assert res["artifacts"] == []
    assert "outside the workspace" in res["error"]


def test_share_missing_file_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_notify", _noop)
    _set_ctx(tmp_path)

    res = asyncio.run(wa.share_artifact("outputs/nope.pdf"))

    assert res["artifacts"] == []
    assert "not found" in res["error"].lower()
