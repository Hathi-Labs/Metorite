"""An upload into a new main chat lands, and the orchestrator reads it.

Production, 2026-10-09 13:15 UTC: the owner attached a ``.docx`` in the main
AI chat (agent ``orchestrator``). Both ``POST /agent/workspace/{sid}/upload``
calls answered 404 "No workspace found for this session". The
``chat_session`` row existed with ``workspace_path`` NULL, and step 2 of
``workspace._get_workspace_path`` returned None for ``orchestrator`` by
name. That rule came from June 2026 (28508aa13), when the main chat had no
agent clone. The orchestrator is now a shared agent like any other, and its
runs work in its tenant dir (``executor._resolve_run_workspace``).

The route and the run must agree on ONE folder: the route writes to
``agent_paths.upload_dir_rel`` in the dir it resolves, and
``read_attachment`` reads that folder of the dir the executor binds.

R8: the real upload route and a real ``chat_session`` row, as the NOBYPASSRLS
app role (``graph_as_app``).

Mutation this file catches (R7): put ``"orchestrator"`` back in the step 2
exclusion, and the upload answers 404.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db> \\
        uv run pytest tests/unit/test_orchestrator_upload.py -v -rs
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")

from acb_skills import attachment_tools as tools  # noqa: E402
from acb_skills.agent_paths import thread_slug  # noqa: E402
from acb_skills.write_artifact import bind_artifact_context  # noqa: E402

# Resolved by name as fixtures. The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: E402, F401
    _ALICE,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_h201_readers_under_rls import _seed_session  # noqa: E402
from tests.unit.test_h201_tenant_workdirs import _client  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
_O = "orchestrator"


def _docx(text: str) -> bytes:
    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def clone(tmp_path, monkeypatch) -> Path:
    """A scratch clone root that holds the real orchestrator config."""
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes

    settings = get_settings()
    monkeypatch.setattr(settings, "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [{"name": _O}])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    code = tmp_path / "agents" / "repos" / _O
    code.mkdir(parents=True)
    cfg = (ROOT / "apps/agents/agent-orchestrator/config.json").read_text(encoding="utf-8")
    (code / "config.json").write_text(cfg, encoding="utf-8")
    return code


def _run_read(code: Path, org: str, sid: str, name: str) -> str:
    """``read_attachment`` in an orchestrator run of thread *sid*, with the
    working dir and the store key that the executor gives that run."""
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator.executor import _resolve_run_workspace

    cfg = json.loads((code / "config.json").read_text(encoding="utf-8"))
    ws, key = _resolve_run_workspace(code, cfg, organization_id=org)

    async def _go() -> str:
        token = bind_tenant(org)
        bind_artifact_context(session_id=sid, agent_name=_O, run_id="r-orch-upload",
                              workspace_root=ws, instance=key)
        try:
            return await tools.read_attachment(name)
        finally:
            release_tenant(token)
            bind_artifact_context()

    return asyncio.run(_go())


@_DB_GATE
def test_an_upload_into_a_new_main_chat_lands_and_the_run_reads_it(
    graph_as_app, clone,  # noqa: F811
) -> None:
    org = graph_as_app.org_a
    # The row the browser makes when the chat opens: no workspace_path yet.
    sid = _seed_session(graph_as_app, org, _ALICE, None, agent=_O)
    up = _client(_user(_ALICE, org)).post(
        f"/agent/workspace/{sid}/upload",
        files={"files": ("brief.docx", _docx("Apollo ships in November"))},
    )
    assert up.status_code == 200, up.text
    assert up.json()[0]["path"] == f"inputs/{thread_slug(sid)}/brief.docx"
    assert "Apollo ships in November" in _run_read(clone, org, sid, "brief.docx")


@_DB_GATE
def test_the_upload_never_writes_into_the_clone(graph_as_app, clone) -> None:  # noqa: F811
    org = graph_as_app.org_a
    sid = _seed_session(graph_as_app, org, _ALICE, None, agent=_O)
    before = sorted(p.relative_to(clone).as_posix() for p in clone.rglob("*"))
    up = _client(_user(_ALICE, org)).post(
        f"/agent/workspace/{sid}/upload",
        files={"files": ("brief.docx", _docx("x"))},
    )
    assert up.status_code == 200, up.text
    assert sorted(p.relative_to(clone).as_posix() for p in clone.rglob("*")) == before


@_DB_GATE
def test_a_name_with_no_clone_still_has_no_workspace(graph_as_app, clone) -> None:  # noqa: F811
    org = graph_as_app.org_a
    sid = _seed_session(graph_as_app, org, _ALICE, None, agent="default")
    up = _client(_user(_ALICE, org)).post(
        f"/agent/workspace/{sid}/upload",
        files={"files": ("brief.docx", _docx("x"))},
    )
    assert up.status_code == 404, up.text
