"""WS-27bm S8 — the two PDF routes (spec ``projects_ai_chat.md`` §14).

* ``GET /agent/workspace/{sid}/file?path=x.md&format=pdf`` — the same
  workspace, blocked-path and containment checks as a raw read, then the one
  seam in ``gateway/pdf_render.py``.
* ``POST /documents/pdf`` — posted ``text/html`` as a PDF, for the Reports
  download.

The apps here are built the way ``main.py`` builds the real one: the app-wide
``require_authenticated`` guard, then the router. So an anonymous caller is
refused by the same mechanism that refuses one in production.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from acb_auth import get_current_user, require_authenticated
from acb_auth.roles import UserContext, UserRole
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.pdf_render import MAX_SOURCE_BYTES
from gateway.routes import documents, workspace

ANON = UserContext(email=None, role=UserRole.EMPLOYEE)
MEMBER = UserContext(email="a@fracktal.in", role=UserRole.EMPLOYEE)


def _client(router, user: UserContext, *, guarded: bool = True) -> TestClient:
    deps = [require_authenticated(public=frozenset())] if guarded else []
    app = FastAPI(dependencies=deps)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ── The workspace route ─────────────────────────────────────────────────────


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "outputs").mkdir(parents=True)
    (root / "outputs" / "status.md").write_text(
        "# Status\n\nApollo is at risk.\n", encoding="utf-8"
    )
    (root / "outputs" / "page.html").write_text("<h1>Page</h1>", encoding="utf-8")
    (root / "outputs" / "chart.png").write_bytes(b"\x89PNG\r\n")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "outside.md").write_text("# outside", encoding="utf-8")
    monkeypatch.setattr(workspace, "_get_workspace_path", lambda sid, email=None: root)

    async def _no_store(*_a: object, **_k: object) -> bool:
        return False

    monkeypatch.setattr(workspace, "_faultin_from_store", _no_store)
    return root


def _pdf_get(client: TestClient, path: str):
    return client.get("/agent/workspace/s1/file", params={"path": path, "format": "pdf"})


def test_markdown_downloads_as_a_pdf_attachment(ws: Path) -> None:
    res = _pdf_get(_client(workspace.router, MEMBER), "outputs/status.md")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    disposition = res.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert 'filename="status.pdf"' in disposition


def test_html_downloads_as_a_pdf(ws: Path) -> None:
    res = _pdf_get(_client(workspace.router, MEMBER), "outputs/page.html")
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")


def test_the_raw_read_is_unchanged(ws: Path) -> None:
    res = _client(workspace.router, MEMBER).get(
        "/agent/workspace/s1/file", params={"path": "outputs/status.md"}
    )
    assert res.status_code == 200
    assert res.headers["content-disposition"].startswith("inline;")
    assert res.content.startswith(b"# Status")


def test_a_type_it_cannot_convert_is_415(ws: Path) -> None:
    res = _pdf_get(_client(workspace.router, MEMBER), "outputs/chart.png")
    assert res.status_code == 415


def test_an_unknown_format_is_refused(ws: Path) -> None:
    res = _client(workspace.router, MEMBER).get(
        "/agent/workspace/s1/file", params={"path": "outputs/status.md", "format": "docx"}
    )
    assert res.status_code == 422


def test_an_anonymous_caller_is_refused(ws: Path) -> None:
    res = _pdf_get(_client(workspace.router, ANON), "outputs/status.md")
    assert res.status_code == 401


@pytest.mark.parametrize("path", ["../outside.md", "outputs/../../outside.md"])
def test_path_traversal_is_refused(ws: Path, path: str) -> None:
    res = _pdf_get(_client(workspace.router, MEMBER), path)
    assert res.status_code in (400, 404)
    assert not res.content.startswith(b"%PDF")


def test_a_blocked_path_is_refused(ws: Path) -> None:
    res = _pdf_get(_client(workspace.router, MEMBER), ".env")
    assert res.status_code == 404


def test_a_source_over_the_cap_is_413(ws: Path) -> None:
    (ws / "outputs" / "huge.md").write_text("x" * (MAX_SOURCE_BYTES + 1), encoding="utf-8")
    res = _pdf_get(_client(workspace.router, MEMBER), "outputs/huge.md")
    assert res.status_code == 413


# ── POST /documents/pdf ─────────────────────────────────────────────────────


def _post(client: TestClient, body: bytes | str, content_type: str = "text/html; charset=utf-8",
          filename: str = "Weekly delivery.pdf"):
    return client.post(
        "/documents/pdf",
        params={"filename": filename},
        content=body,
        headers={"Content-Type": content_type},
    )


def test_posted_html_comes_back_as_a_pdf_attachment() -> None:
    res = _post(_client(documents.router, MEMBER), "<h2>Weekly delivery</h2><p>Finished: 3</p>")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert 'filename="Weekly delivery.pdf"' in res.headers["content-disposition"]


def test_the_app_guard_refuses_an_anonymous_post() -> None:
    res = _post(_client(documents.router, ANON), "<p>x</p>")
    assert res.status_code == 401


def test_the_route_refuses_an_anonymous_post_on_its_own() -> None:
    """Without the app-wide guard the route still refuses: no identity, no PDF."""
    res = _post(_client(documents.router, ANON, guarded=False), "<p>x</p>")
    assert res.status_code == 401


@pytest.mark.parametrize("content_type", ["application/json", "text/plain", "text/markdown", ""])
def test_only_text_html_is_accepted(content_type: str) -> None:
    res = _post(_client(documents.router, MEMBER), "<p>x</p>", content_type=content_type)
    assert res.status_code == 415


def test_a_body_over_the_cap_is_413() -> None:
    res = _post(_client(documents.router, MEMBER), "x" * (MAX_SOURCE_BYTES + 1))
    assert res.status_code == 413


def test_an_empty_body_is_422() -> None:
    res = _post(_client(documents.router, MEMBER), "   ")
    assert res.status_code == 422


def test_the_filename_cannot_break_the_header() -> None:
    res = _post(_client(documents.router, MEMBER), "<p>x</p>", filename='a"b\r\nX-Evil: 1')
    assert res.status_code == 200
    assert "x-evil" not in {k.lower() for k in res.headers}
    assert res.headers["content-disposition"].count('"') == 2
