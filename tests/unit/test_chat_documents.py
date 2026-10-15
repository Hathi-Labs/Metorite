"""H-315 — the web AI chat makes a deck or a report as a PDF file.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §15.4.

``acb_skills.documents.make_document`` takes the spec of ``whatsapp_ui`` kind
``document``, checks it with the same ``prepare``, lays it out through the ONE
PDF seam (``gateway.pdf_render.render_pdf``, kind ``document``, its child
process) and saves it with the safe opener
(``documents.save_outputs``, H-236), so the chat shows a PDF card.

R7 fences named here:

* ``chat-doc-artifact``: one PDF artifact, from the real child render.
* ``chat-doc-identity``: the member and the org of the render come from the
  run, never from the spec.
* ``chat-doc-refusal``: a bad spec writes nothing and says what to fix.
* ``chat-doc-channel``: every assistant holds it, and WhatsApp withholds it
  for its own ``document`` kind.
"""
from __future__ import annotations

from typing import Any

import pytest

pymupdf = pytest.importorskip("pymupdf")

from acb_skills import documents  # noqa: E402
from acb_skills import whatsapp_ui as wui  # noqa: E402

DECK = {
    "format": "deck", "title": "Acme Q4 plan", "filename": "Acme Q4 plan",
    "slides": [
        {"type": "cover", "title": "Acme Q4 plan"},
        {"type": "content", "title": "Revenue",
         "visual": {"kind": "chart", "type": "bar", "title": "Closed revenue",
                    "labels": ["Jul", "Aug"], "values": [32, 41]}},
    ],
}


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """A chat run on a real working dir. Each saved file is listed with its
    bytes. The blob mirror and the file card are faked."""
    import importlib

    # The package re-exports the FUNCTION under the module's name.
    wa = importlib.import_module("acb_skills.write_artifact")

    async def _quiet(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(wa, "_notify", _quiet)
    monkeypatch.setattr(wa, "mirror_to_blob_store", _quiet)

    class _Written(list):
        def refresh(self) -> _Written:
            self[:] = [{"path": f.relative_to(tmp_path).as_posix(), "content": f.read_bytes()}
                       for f in sorted((tmp_path / "outputs").rglob("*")) if f.is_file()] \
                if (tmp_path / "outputs").exists() else []
            return self

    with wa.artifact_context_scope():
        wa.bind_artifact_context(workspace_root=str(tmp_path), session_id="s",
                                 agent_name="orchestrator")
        yield _Written()


async def test_a_deck_becomes_one_pdf_artifact_from_the_real_child(
    written: list[dict[str, Any]],
) -> None:
    out = await documents.make_document(DECK)
    assert out["ok"] is True
    assert out["download_url"].endswith("outputs/Acme Q4 plan.pdf")
    (file,) = written.refresh()
    assert file["path"] == "outputs/Acme Q4 plan.pdf"
    doc = pymupdf.open(stream=file["content"], filetype="pdf")
    assert doc.page_count == 2 and "Closed revenue" in doc[1].get_text()


async def test_the_render_names_the_runs_member_and_org_never_the_specs(
    written: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id
    from gateway import pdf_render

    seen: list[tuple[str, str | None]] = []
    real = pdf_render.render_pdf

    async def _spy(kind: str, source: str, *, member: str, org: str | None) -> bytes:
        seen.append((member, org))
        return await real(kind, source, member=member, org=org)

    monkeypatch.setattr(pdf_render, "render_pdf", _spy)
    user = _bind_memory_user_id("a@fracktal.in")
    tenant = bind_tenant("11111111-1111-4111-8111-111111111111")
    try:
        spec = {**DECK, "member": "b@evil.test", "org": "other-org"}
        out = await documents.make_document(spec)
    finally:
        release_tenant(tenant)
        _unbind_memory_user_id(user)
    assert out["ok"] is True
    assert seen == [("a@fracktal.in", "11111111-1111-4111-8111-111111111111")]


@pytest.mark.parametrize(("spec", "says"), [
    ({"format": "slides", "title": "T"}, '"format" must be one of'),
    ({**DECK, "slides": [{"type": "content", "title": "T", "image": "x.png"}]},
     "text and numbers only"),
])
async def test_a_bad_spec_writes_nothing_and_says_what_to_fix(
    written: list[dict[str, Any]], spec: dict, says: str,
) -> None:
    out = await documents.make_document(spec)
    assert out["ok"] is False and says in out["error"]
    assert written.refresh() == []


async def test_on_whatsapp_the_tool_points_at_the_document_kind(
    written: list[dict[str, Any]],
) -> None:
    with wui.whatsapp_run("orchestrator"):
        out = await documents.make_document(DECK)
    assert out == {"ok": False, "error": 'on WhatsApp, call whatsapp_ui kind "document"'}
    assert written.refresh() == []


def test_the_chat_assistants_hold_it_and_whatsapp_withholds_it() -> None:
    """The main chat assistant and the Projects assistant name it in their
    scope (the ``documents`` family). WhatsApp withholds it for its own
    ``document`` kind."""
    import json
    from pathlib import Path

    from acb_skills.skill_families import SKILL_FAMILIES

    root = Path(__file__).resolve().parents[2] / "apps" / "agents"
    for agent in ("agent-orchestrator", "agent-projects"):
        scope = json.loads((root / agent / "config.json").read_text(encoding="utf-8"))
        assert "make_document" in scope["tool_scope"], agent
    # §14.9 of projects_ai_chat.md: make_report joined the family.
    assert SKILL_FAMILIES["documents"]["tools"] == ("make_document", "make_report")
    assert "make_document" in wui.WITHHELD_TOOLS


async def test_a_pdf_that_could_not_be_saved_is_never_ok(
    written: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review: the save refuses with a dict (a quota, no workspace) and never
    raises. The model must not claim a file that does not exist."""
    import contextlib
    import importlib

    wa = importlib.import_module("acb_skills.write_artifact")

    @contextlib.asynccontextmanager
    async def _over(_root: Any) -> Any:
        yield True

    monkeypatch.setattr(wa, "_chat_dir_guard", _over)
    out = await documents.make_document(DECK)
    assert out["ok"] is False and "over its quota" in out["error"]
    assert written.refresh() == []
