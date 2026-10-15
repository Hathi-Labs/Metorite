"""A report from the chat: one file, one card and one PDF (B1).

Spec: ``project-docs/specs/projects_ai_chat.md`` §14.9.

``acb_skills.reports.make_report`` takes ONE spec. It checks it with the
checks of ``acb_skills.documents``, writes ``outputs/<stem>.report.json`` and
``outputs/<stem>.pdf`` with the safe opener (``documents.save_outputs``), and
draws ONE ``report`` card through ``emit_generative_ui``. On WhatsApp it sends
the same PDF as a file, and draws no card.

The tests write into a real working dir. The blob mirror, the file card
(``write_artifact._notify``) and the genUI emit are recorded.

R7 fences named here:

* ``report-checks``: a bad spec writes nothing and says what to fix.
* ``report-file``: the file holds the contract of §14.9.3, visuals as data.
* ``report-card``: one ``report`` card, whose data is the file plus its path.
  Its two files register the workspace and get no file card. With no report
  card, the PDF gets its file card.
* ``report-links`` (H-236): a link that a sandbox planted at a target or a
  parent dir is never followed, and the host file is untouched. The same for
  ``make_document``.
* ``report-stem``: the pair shares one stem, in a chat run and a batch run.
* ``report-pdf-visuals``: the PDF holds the drawn chart, tiles and table,
  with the chart engine off.
* ``report-whatsapp``: a WhatsApp run gets the PDF as one element, no file
  and no card.
* ``report-reach``: each chat assistant holds the tool, WhatsApp keeps it,
  and the prompt rule renders only with the tool.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pymupdf = pytest.importorskip("pymupdf")

from acb_skills import documents, reports  # noqa: E402
from acb_skills import whatsapp_ui as wui  # noqa: E402

wa = importlib.import_module("acb_skills.write_artifact")

CHART = {"kind": "chart", "type": "bar", "title": "Closed revenue by month",
         "labels": ["Jul", "Aug", "Sep"], "values": [32, 41, 55], "unit": "L"}
TABLE = {"kind": "table", "title": "Top deals", "columns": ["Deal", "Stage", "Value"],
         "rows": [["Bharat Steel", "Proposal", "42"], ["Kaveri Foods", "Demo", "28"]]}

SPEC: dict[str, Any] = {
    "title": "Q3 pipeline review",
    "subtitle": "Sales, July to September",
    "for": "The board",
    "period": "Q3 2026",
    "date": "16 Oct 2026",
    "summary": "**Revenue grew** for the third month.\n- 12 deals open\n- *two* at risk",
    "kpis": [
        {"label": "Revenue", "value": 128, "unit": "L", "delta": "+18%", "good": "up"},
        {"label": "Win rate", "value": 31, "unit": "%"},
    ],
    "sections": [
        {"heading": "Revenue", "text": "Revenue rose each month.", "visual": CHART},
        {"heading": "Deals", "bullets": ["Bharat Steel is the biggest"], "visual": TABLE},
    ],
    "sources": ["CRM pipeline, 16 Oct 2026"],
}
PDF = "outputs/q3-pipeline-review.pdf"
JSON = "outputs/q3-pipeline-review.report.json"


def _files(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root/outputs``, by its path. No link."""
    out: dict[str, bytes] = {}
    for p in sorted((root / "outputs").rglob("*")) if (root / "outputs").exists() else []:
        if p.is_file() and not p.is_symlink():
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """A chat run on a real working dir. The mirror, the file card and the
    genUI card are recorded."""
    root = tmp_path / "ws"
    root.mkdir()
    seen: dict[str, Any] = {"root": root, "cards": [], "notified": [], "mirrored": []}

    async def _emit(ui: str) -> dict:
        seen["cards"].append(json.loads(ui))
        return {"ok": True}

    async def _notify(*, artifact: dict, session_id: str, announce: bool = True,
                      **_kw: Any) -> None:
        seen["notified"].append((artifact["path"], announce, session_id))

    async def _mirror(rel: str, data: bytes, **kw: Any) -> None:
        seen["mirrored"].append((rel, kw.get("actor", "agent")))

    monkeypatch.setattr(wa, "emit_generative_ui", _emit)
    monkeypatch.setattr(wa, "_notify", _notify)
    monkeypatch.setattr(wa, "mirror_to_blob_store", _mirror)
    with wa.artifact_context_scope():
        wa.bind_artifact_context(workspace_root=str(root), session_id="s1",
                                 agent_name="orchestrator", member="a@fracktal.in")
        yield seen


async def _run(coro: Any) -> Any:
    """Await *coro*, then the mirror and card tasks it started."""
    out = await coro
    rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*rest, return_exceptions=True)
    return out


def _link(target: Path, link: Path, *, is_dir: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this box cannot make a symlink: {exc}")


# ── report-checks ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("change", "says"), [
    ({"title": ""}, '"title" is required'),
    ({"sections": []}, 'a report needs "sections"'),
    ({"summary": "See https://evil.test/x"}, "markdown-lite"),
    ({"summary": "<b>hi</b>"}, "markdown-lite"),
    ({"summary": "![x](y.png)"}, "markdown-lite"),
    ({"sections": [{"heading": "A", "text": "[here](http://x.test)"}]}, "markdown-lite"),
    ({"kpis": [{"label": "A", "value": 1}] * 9}, "at most 8"),
    ({"kpis": [{"label": "A", "value": 1, "good": "sideways"}]}, '"good" must be'),
    ({"kpis": [{"label": "A"}]}, '"value" is required'),
    ({"kpis": [{"label": "A", "value": True}]}, "a number or text"),
    ({"sources": ["s"] * 13}, "at most 12"),
    ({"sections": [{"heading": "A", "visual": {"kind": "map"}}]}, '"visual.kind" must be one of'),
    ({"sections": [{"heading": "A", "image": "x.png"}]}, "text and numbers only"),
    ({"sections": [{"heading": str(i), "visual": CHART} for i in range(12)]},
     "at most 12 visuals, and the kpis count"),
    ({"sections": [{"heading": "A", "visual": {**TABLE, "rows": [["a", "b", "c"]] * 21}}]},
     "at most 20 rows"),
])
async def test_a_bad_spec_writes_nothing_and_says_what_to_fix(
    chat: dict[str, Any], change: dict, says: str,
) -> None:
    out = await _run(reports.make_report({**SPEC, **change}))
    assert out["ok"] is False and says in out["error"], out
    assert _files(chat["root"]) == {} and chat["cards"] == []


#: What the fake card validator answers.
CARD_REFUSAL = "data: the report is 300000 characters, and the most is 200000. Shorten it."


async def test_the_cards_own_validator_refuses_before_any_file(
    chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One validator for the tool and the card: ``genui_report_refusal`` of
    ``write_artifact`` (B2). make_report hands it the data the card gets."""
    seen: list[Any] = []

    def _refuse(data: object, where: str = "data") -> str | None:
        seen.append(data)
        return CARD_REFUSAL

    monkeypatch.setattr(wa, "genui_report_refusal", _refuse, raising=False)
    out = await _run(reports.make_report(SPEC))
    assert out == {"ok": False, "error": CARD_REFUSAL}
    assert _files(chat["root"]) == {} and chat["cards"] == []
    (data,) = seen
    assert data["path"] == JSON and data["pdf"] == PDF
    assert data["kind"] == "metorite.report" and data["sections"]


async def test_every_report_the_tool_makes_is_a_card_the_validator_accepts(
    chat: dict[str, Any],
) -> None:
    """The largest report make_report accepts: 8 kpis, 12 sources, 11 full
    tables and the tiles. The card's validator must accept its data."""
    if not hasattr(wa, "genui_report_refusal"):
        pytest.skip("genui_report_refusal arrives with B2 (PR #1065)")
    full_table = {**TABLE, "columns": list("ABCDEF"), "rows": [list("abcdef")] * 20}
    spec = {**SPEC,
            "kpis": [{"label": f"K{i}", "value": i} for i in range(8)],
            "sources": [f"Source {i}" for i in range(12)],
            "sections": [{"heading": f"S{i}", "visual": full_table} for i in range(11)]}
    out = await _run(reports.make_report(spec))
    assert out["ok"] is True, out
    (card,) = chat["cards"]
    assert wa.genui_report_refusal(card["props"]["data"]) is None


async def test_an_empty_spec_returns_the_field_guide_and_makes_nothing(
    chat: dict[str, Any],
) -> None:
    out = await _run(reports.make_report({}))
    assert out["ok"] is False and out["guide"] == reports.REPORT_GUIDE
    assert _files(chat["root"]) == {} and chat["cards"] == []


# ── report-file and report-card ─────────────────────────────────────────────


async def test_the_file_holds_the_contract_and_the_card_draws_it(
    chat: dict[str, Any],
) -> None:
    out = await _run(reports.make_report({**SPEC, "filename": "Q3 Pipeline Review.pdf"}))
    assert out == {"ok": True, "path": JSON, "pdf_path": PDF, "note": out["note"]}
    assert "Do not paste a link" in out["note"]
    files = _files(chat["root"])
    assert sorted(files) == [PDF, JSON]
    assert files[PDF].startswith(b"%PDF")
    body = json.loads(files[JSON].decode("utf-8"))
    assert body == {
        "kind": "metorite.report", "version": 1,
        "title": "Q3 pipeline review", "subtitle": "Sales, July to September",
        "for": "The board", "date": "16 Oct 2026", "period": "Q3 2026",
        "summary": SPEC["summary"],
        "kpis": [
            {"label": "Revenue", "value": 128, "unit": "L", "delta": "+18%", "good": "up"},
            {"label": "Win rate", "value": 31, "unit": "%"},
        ],
        # The visuals stay DATA, so the web draws live charts from them.
        "sections": [
            {"heading": "Revenue", "text": "Revenue rose each month.", "visual": CHART},
            {"heading": "Deals", "bullets": ["Bharat Steel is the biggest"], "visual": TABLE},
        ],
        "sources": ["CRM pipeline, 16 Oct 2026"],
        "pdf": PDF,
    }
    (card,) = chat["cards"]
    assert card == {"type": "template", "props": {
        "name": "report", "data": {**body, "path": JSON}}}
    # Both files reach the blob store, and each registers the workspace with
    # NO file card: the report card shows them (review item 3).
    assert sorted(chat["mirrored"]) == [(PDF, "agent"), (JSON, "agent")]
    assert sorted(chat["notified"]) == [(PDF, False, "s1"), (JSON, False, "s1")]


async def test_with_no_card_the_member_still_gets_the_pdf(
    monkeypatch: pytest.MonkeyPatch, chat: dict[str, Any],
) -> None:
    """No live chat, or a chat that has no report template: the files exist,
    and the PDF gets its file card. The reply names it and pastes no link
    (slice A, #1063). The result keeps download_url."""
    async def _no_stream(ui: str) -> dict:
        return {"ok": False, "error": "no active run stream to render into"}

    monkeypatch.setattr(wa, "emit_generative_ui", _no_stream)
    out = await _run(reports.make_report(SPEC))
    assert out["ok"] is True
    assert out["download_url"] == f"/api/agent/workspace/s1/file?path={PDF}"
    assert "paste no link" in out["note"] and "Link" not in out["note"]
    assert (PDF, True, "s1") in chat["notified"]
    assert (JSON, True, "s1") not in chat["notified"]


async def test_make_document_tells_the_model_to_name_the_file_not_link_it(
    chat: dict[str, Any],
) -> None:
    """Slice A (#1063): a card shows the file, and the reply names it in
    words. make_document's docstring and note say the same. download_url
    stays in the result, and the PDF gets its file card."""
    from acb_skills.skill_families import tool_json_schema

    desc = " ".join(tool_json_schema(documents.make_document)["function"]["description"].split())
    assert "paste no link" in desc and "download_url in your answer" not in desc
    out = await _run(documents.make_document({
        "format": "report", "title": "T", "sections": [{"heading": "H", "text": "x"}]}))
    assert out["ok"] is True and out["path"] == "outputs/T.pdf" and out["download_url"]
    assert "paste no link" in out["note"] and "Link the download_url" not in out["note"]
    assert chat["notified"] == [("outputs/T.pdf", True, "s1")]


async def test_with_no_workspace_nothing_is_ok() -> None:
    """A run with no working dir writes nothing, and never answers "ok"."""
    with wa.artifact_context_scope():
        wa.bind_artifact_context()
        report = await _run(reports.make_report(SPEC))
        deck = await _run(documents.make_document({
            "format": "report", "title": "T", "sections": [{"heading": "H"}]}))
    assert report["ok"] is False and "No workspace" in report["error"]
    assert deck["ok"] is False and "No workspace" in deck["error"]


async def test_the_render_names_the_runs_member_and_org_never_the_specs(
    chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
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
        out = await _run(reports.make_report({**SPEC, "member": "b@evil.test", "org": "x"}))
    finally:
        release_tenant(tenant)
        _unbind_memory_user_id(user)
    assert out["ok"] is True
    assert seen == [("a@fracktal.in", "11111111-1111-4111-8111-111111111111")]


# ── report-links (H-236) and report-stem ────────────────────────────────────


async def test_a_link_planted_at_a_target_is_never_followed(
    chat: dict[str, Any], tmp_path: Path,
) -> None:
    """A sandbox command plants ``outputs/<stem>.report.json`` and
    ``outputs/<stem>.pdf`` as links to host files, one that exists and one
    that does not yet. The write follows neither: the host stays as it was,
    and the pair takes the next free stem."""
    root = chat["root"]
    host = tmp_path / "host"
    host.mkdir()
    (host / "secret.txt").write_text("HOST", encoding="utf-8")
    (root / "outputs").mkdir()
    _link(host / "secret.txt", root / JSON)
    _link(host / "made-by-the-gateway.pdf", root / PDF)
    out = await _run(reports.make_report(SPEC))
    assert out["ok"] is True, out
    assert (host / "secret.txt").read_text(encoding="utf-8") == "HOST"
    assert sorted(p.name for p in host.iterdir()) == ["secret.txt"]
    assert (root / JSON).is_symlink() and (root / PDF).is_symlink()
    assert out["path"] == "outputs/q3-pipeline-review (1).report.json"
    assert out["pdf_path"] == "outputs/q3-pipeline-review (1).pdf"


async def test_a_link_planted_at_the_folder_refuses_the_write(
    chat: dict[str, Any], tmp_path: Path,
) -> None:
    """``outputs`` itself is a link to a host dir: nothing is written there."""
    host = tmp_path / "host-dir"
    host.mkdir()
    _link(host, chat["root"] / "outputs", is_dir=True)
    report = await _run(reports.make_report(SPEC))
    deck = await _run(documents.make_document({
        "format": "report", "title": "T", "sections": [{"heading": "H"}]}))
    assert report["ok"] is False and "link" in report["error"], report
    assert deck["ok"] is False and "link" in deck["error"], deck
    assert list(host.iterdir()) == [] and chat["cards"] == []


async def test_make_document_never_writes_through_a_dangling_link(
    chat: dict[str, Any], tmp_path: Path,
) -> None:
    """H-315 had the same root cause: a create through a dangling link made
    a host file. The PDF now takes the next free name."""
    host = tmp_path / "host"
    host.mkdir()
    (chat["root"] / "outputs").mkdir()
    _link(host / "planted.pdf", chat["root"] / "outputs" / "T.pdf")
    out = await _run(documents.make_document({
        "format": "report", "title": "T", "sections": [{"heading": "H"}]}))
    assert out["ok"] is True and out["path"] == "outputs/T (1).pdf", out
    assert list(host.iterdir()) == []


async def test_a_taken_stem_moves_the_pair_together(chat: dict[str, Any]) -> None:
    """Either file of a stem is taken: the PDF and the report take ONE new
    stem, so the report file ends in ".report.json" and names its PDF."""
    root = chat["root"]
    (root / "outputs").mkdir()
    (root / JSON).write_text("{}", encoding="utf-8")
    out = await _run(reports.make_report(SPEC))
    assert out["path"] == "outputs/q3-pipeline-review (1).report.json"
    assert out["pdf_path"] == "outputs/q3-pipeline-review (1).pdf"
    assert not (root / PDF).exists(), "a PDF with no report beside it"
    body = json.loads((root / out["path"]).read_text(encoding="utf-8"))
    assert body["pdf"] == out["pdf_path"]
    assert (root / JSON).read_text(encoding="utf-8") == "{}"


async def test_a_batch_run_keeps_one_stem_and_the_suffix(
    chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch run (no chat) writes the flat folder and takes a name the
    history never held. The history holds the report name and not the PDF
    name. The pair still shares one stem: never ``x.report (1).json``."""
    held = {JSON}

    async def _history(rel: str, ctx: Any) -> bool:
        return rel in held

    monkeypatch.setattr(wa, "_path_has_history", _history)
    wa.derive_artifact_context(batch_thread=True, instance="o:org-1")
    out = await _run(reports.make_report(SPEC))
    assert out["path"] == "outputs/q3-pipeline-review (1).report.json", out
    assert out["pdf_path"] == "outputs/q3-pipeline-review (1).pdf"
    assert sorted(chat["mirrored"]) == [
        ("outputs/q3-pipeline-review (1).pdf", "batch"),
        ("outputs/q3-pipeline-review (1).report.json", "batch"),
    ]


# ── report-pdf-visuals ──────────────────────────────────────────────────────


async def test_the_pdf_holds_the_drawn_chart_tiles_and_table_with_the_engine_off(
    chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With WHATSAPP_CHART_ENGINE off, each visual is our own SVG, placed as
    vector. Its words are text in the PDF, and its shapes are drawings."""
    monkeypatch.setattr(wui.engine, "enabled", lambda: False)
    out = await _run(reports.make_report(SPEC))
    assert out["ok"] is True
    doc = pymupdf.open(stream=_files(chat["root"])[PDF], filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    # The title block, with the period in the meta line.
    assert "Q3 pipeline review" in text and "Q3 2026 · 16 Oct 2026" in text
    # The summary as text: the list item has a bullet, and italic is words.
    assert "• 12 deals open" in text and "two at risk" in text and "*two*" not in text
    # The KPI tiles: the stats visual's title and each tile, with the unit.
    assert "Key figures" in text and "Revenue" in text and "128 L" in text and "31%" in text
    # The chart: its title and its axis labels come from the drawn SVG.
    assert "Closed revenue by month" in text
    assert all(month in text for month in ("Jul", "Aug", "Sep"))
    # The table: its title and its cells. "Kaveri Foods" is in no text field,
    # so only the drawn table can put it there.
    assert "Top deals" in text and "Bharat Steel" in text and "Kaveri Foods" in text
    assert "Sources" in text and "CRM pipeline, 16 Oct 2026" in text
    # The shapes: the tiles, the bars and the table stripes are vector
    # drawings. The same report with no visual has the rule lines only.
    from gateway.pdf_render import document_to_pdf

    plain = reports.check_report({
        **{k: v for k, v in SPEC.items() if k != "kpis"},
        "sections": [{"heading": s["heading"], "text": "x"} for s in SPEC["sections"]]})
    bare = pymupdf.open(stream=document_to_pdf(json.dumps(reports.layout_of(plain))),
                        filetype="pdf")
    drawn = sum(len(page.get_drawings()) for page in doc)
    lines = sum(len(page.get_drawings()) for page in bare)
    assert drawn - lines >= 8, (drawn, lines)


@pytest.mark.parametrize(("kpi", "tile"), [
    ({"label": "A", "value": 1234567.5, "unit": "₹"}, {"label": "A", "value": "1234567.5 ₹"}),
    ({"label": "A", "value": 12.0, "unit": "%"}, {"label": "A", "value": "12%"}),
    ({"label": "A", "value": "₹1.4 Cr", "delta": 3, "good": "down"},
     {"label": "A", "value": "₹1.4 Cr", "delta": "+3", "good": "down"}),
    ({"label": "A", "value": 7, "delta": -2.5}, {"label": "A", "value": "7", "delta": "-2.5"}),
])
def test_a_kpi_becomes_a_tile_with_its_number_as_written(kpi: dict, tile: dict) -> None:
    """A tile prints the number as the tool gave it, never "1.23457e+06"."""
    assert reports._tile(kpi) == tile


async def test_thirty_sections_with_a_summary_and_sources_still_make_a_pdf(
    chat: dict[str, Any],
) -> None:
    """The summary and the sources join the 30 sections after ``prepare``
    drew them, so its limit of 30 never refuses a valid report."""
    spec = {**SPEC, "sections": [{"heading": f"Part {i}", "text": "Fine."} for i in range(30)]}
    out = await _run(reports.make_report(spec))
    assert out["ok"] is True, out
    doc = pymupdf.open(stream=_files(chat["root"])[PDF], filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    assert text.index("Summary") < text.index("Part 0") < text.index("Part 29") \
        < text.index("Sources")


# ── report-whatsapp ─────────────────────────────────────────────────────────


async def test_on_whatsapp_the_report_is_one_pdf_element_and_no_file_or_card(
    chat: dict[str, Any],
) -> None:
    from gateway.pdf_render import document_to_pdf

    laid: list[dict[str, Any]] = []

    async def _document(layout: dict[str, Any]) -> bytes:
        laid.append(layout)
        return document_to_pdf(json.dumps(layout))

    with wui.whatsapp_run("orchestrator", document=_document) as run:
        out = await _run(reports.make_report(SPEC))
    assert out["ok"] is True and out["file"] == "Q3 pipeline review.pdf", out
    (element,) = run.outbox
    assert element.kind == "document" and element.pdf.startswith(b"%PDF")
    assert element.filename == "Q3 pipeline review.pdf"
    assert "[Document: Q3 pipeline review.pdf · report" in element.rendition
    # The same layout as the web: the summary leads and the sources end it.
    (layout,) = laid
    heads = [s.get("heading") for s in layout["sections"]]
    assert heads == ["Summary", "Revenue", "Deals", "Sources"]
    assert _files(chat["root"]) == {} and chat["cards"] == []


async def test_on_whatsapp_with_documents_off_it_says_so_and_sends_nothing(
    chat: dict[str, Any],
) -> None:
    with wui.whatsapp_run("orchestrator") as run:
        out = await _run(reports.make_report(SPEC))
    assert out == {"ok": False, "error": "documents are off in this chat. Answer in text"}
    assert run.outbox == [] and _files(chat["root"]) == {} and chat["cards"] == []


async def test_on_whatsapp_a_full_reply_gets_no_fourth_element() -> None:
    async def _document(layout: dict[str, Any]) -> bytes:
        return b"%PDF-1.7"

    with wui.whatsapp_run("orchestrator", document=_document) as run:
        for _ in range(wui.MAX_MESSAGES):
            assert (await reports.make_report(SPEC))["ok"] is True
        out = await reports.make_report(SPEC)
    assert out["ok"] is False and "already has" in out["error"]
    assert len(run.outbox) == wui.MAX_MESSAGES


# ── report-reach ────────────────────────────────────────────────────────────

#: The chat assistants a member can ask for a report.
CHAT_AGENTS = (
    "agent-orchestrator", "agent-projects", "agent-crm", "agent-email-assistant",
    "agent-task-manager", "agent-whatsapp-assistant",
)


def test_every_chat_assistant_holds_it_and_whatsapp_keeps_it() -> None:
    from acb_skills.skill_families import SKILL_FAMILIES

    root = Path(__file__).resolve().parents[2] / "apps" / "agents"
    for agent in CHAT_AGENTS:
        scope = json.loads((root / agent / "config.json").read_text(encoding="utf-8"))
        assert "make_report" in scope["tool_scope"], agent
    assert "make_report" in SKILL_FAMILIES["documents"]["tools"]
    # A channel changes the delivery, never the function (owner, 2026-10-10).
    assert "make_report" not in wui.WITHHELD_TOOLS


def test_the_prompt_rule_renders_only_for_an_agent_that_holds_the_tool() -> None:
    from acb_skills import addendum as ad

    rule = "A report is make_report. Never write a report as a .md file."
    for compact in (False, True):
        with_it = ad.render_injected_tools_addendum(
            is_sub_agent=compact, effective_scope=frozenset({"make_report"}),
            registry_block="", risk_block="")
        without = ad.render_injected_tools_addendum(
            is_sub_agent=compact, effective_scope=frozenset({"write_artifact"}),
            registry_block="", risk_block="")
        assert ("A report is make_report" in with_it) and ("make_report" not in without)
    assert rule in ad.REPORT_RULE.replace("**", "")


def test_the_rules_say_the_pdf_on_whatsapp_and_name_no_md_report() -> None:
    """Review: the rule promised a card on WhatsApp too, and the file rules
    gave ``outputs/reports/q3.md`` as their example."""
    from acb_skills import addendum as ad

    assert "On WhatsApp the member gets the PDF" in " ".join(ad.REPORT_RULE.split())
    for compact in (False, True):
        for design in (False, True):
            block = ad.build_output_discipline_block(compact=compact, design_system=design)
            assert "outputs/reports" not in block and "reports" not in block, block


def test_the_report_guide_says_what_to_do_without_the_tool() -> None:
    """app-builder holds load_design_system and not make_report."""
    assert 'If you do not hold make_report' in reports.REPORT_GUIDE
    assert 'load_design_system("blocks")' in reports.REPORT_GUIDE


def test_the_docstring_sends_the_model_to_the_guide_and_stays_short() -> None:
    from acb_skills.skill_families import tool_json_schema

    desc = " ".join(tool_json_schema(reports.make_report)["function"]["description"].split())
    assert "make_report({})" in desc and "Never write a report as a .md file" in desc
    assert len(desc) < 1000, len(desc)
