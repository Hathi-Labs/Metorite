"""A report from the chat: one file, one card and one PDF (B1).

Spec: ``project-docs/specs/projects_ai_chat.md`` §14.9.

``acb_skills.reports.make_report`` takes ONE spec. It checks it with the
checks of ``acb_skills.documents``, writes ``outputs/<slug>.report.json`` and
``outputs/<slug>.pdf`` through ``write_artifact``, and draws ONE ``report``
card through ``emit_generative_ui``. On WhatsApp it sends the same PDF as a
file, and draws no card.

R7 fences named here:

* ``report-checks``: a bad spec writes nothing and says what to fix.
* ``report-file``: the file holds the contract of §14.9.3, visuals as data.
* ``report-card``: one ``report`` card, whose data is the file plus its path.
* ``report-pdf-visuals``: the PDF holds the drawn chart, tiles and table,
  with the chart engine off.
* ``report-whatsapp``: a WhatsApp run gets the PDF as one element, no file
  and no card.
* ``report-reach``: each chat assistant holds the tool, WhatsApp keeps it,
  and the prompt rule renders only with the tool.
"""
from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

pymupdf = pytest.importorskip("pymupdf")

from acb_skills import reports  # noqa: E402
from acb_skills import whatsapp_ui as wui  # noqa: E402

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


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Record each write and each card through the module attribute."""
    wa = importlib.import_module("acb_skills.write_artifact")
    seen: dict[str, list[Any]] = {"writes": [], "cards": []}

    async def _write(path: str, content: Any, *, encoding: Any = "utf-8",
                     overwrite: bool = False) -> dict:
        seen["writes"].append({"path": path, "content": content,
                               "encoding": encoding, "overwrite": overwrite})
        return {"path": path, "size": len(content), "sha256": "x",
                "download_url": f"/api/agent/workspace/s/file?path={path}"}

    async def _emit(ui: str) -> dict:
        seen["cards"].append(json.loads(ui))
        return {"ok": True}

    monkeypatch.setattr(wa, "write_artifact", _write)
    monkeypatch.setattr(wa, "emit_generative_ui", _emit)
    return seen


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
    chat: dict[str, list[Any]], change: dict, says: str,
) -> None:
    out = await reports.make_report({**SPEC, **change})
    assert out["ok"] is False and says in out["error"], out
    assert chat["writes"] == [] and chat["cards"] == []


async def test_an_empty_spec_returns_the_field_guide_and_makes_nothing(
    chat: dict[str, list[Any]],
) -> None:
    out = await reports.make_report({})
    assert out["ok"] is False and out["guide"] == reports.REPORT_GUIDE
    assert chat["writes"] == [] and chat["cards"] == []


# ── report-file and report-card ─────────────────────────────────────────────


async def test_the_file_holds_the_contract_and_the_card_draws_it(
    chat: dict[str, list[Any]],
) -> None:
    out = await reports.make_report({**SPEC, "filename": "Q3 Pipeline Review.pdf"})
    assert out == {"ok": True, "path": "outputs/q3-pipeline-review.report.json",
                   "pdf_path": "outputs/q3-pipeline-review.pdf", "note": out["note"]}
    assert "Do not paste a link" in out["note"]
    pdf, report = chat["writes"]
    assert pdf["path"] == "outputs/q3-pipeline-review.pdf" and pdf["encoding"] is None
    assert pdf["content"].startswith(b"%PDF")
    assert report["path"] == "outputs/q3-pipeline-review.report.json"
    body = json.loads(report["content"])
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
        "pdf": "outputs/q3-pipeline-review.pdf",
    }
    (card,) = chat["cards"]
    assert card == {"type": "template", "props": {
        "name": "report", "data": {**body, "path": "outputs/q3-pipeline-review.report.json"}}}


async def test_the_report_file_takes_the_stem_of_the_pdf_that_was_written(
    monkeypatch: pytest.MonkeyPatch, chat: dict[str, list[Any]],
) -> None:
    """A taken name gives "x (1).pdf". The report becomes "x (1).report.json",
    never "x.report (1).json", which no viewer opens as a report."""
    wa = importlib.import_module("acb_skills.write_artifact")
    real = wa.write_artifact

    async def _taken(path: str, content: Any, **kw: Any) -> dict:
        moved = path.replace("q3-pipeline-review.pdf", "q3-pipeline-review (1).pdf")
        return await real(moved, content, **kw)

    monkeypatch.setattr(wa, "write_artifact", _taken)
    out = await reports.make_report(SPEC)
    assert out["pdf_path"] == "outputs/q3-pipeline-review (1).pdf"
    assert out["path"] == "outputs/q3-pipeline-review (1).report.json"
    assert chat["writes"][1]["overwrite"] is True
    assert json.loads(chat["writes"][1]["content"])["pdf"] == out["pdf_path"]


async def test_with_no_card_the_member_still_gets_the_pdf_link(
    monkeypatch: pytest.MonkeyPatch, chat: dict[str, list[Any]],
) -> None:
    """No live chat, or a chat that has no report template: the files exist,
    so the answer links the PDF."""
    wa = importlib.import_module("acb_skills.write_artifact")

    async def _no_stream(ui: str) -> dict:
        return {"ok": False, "error": "no active run stream to render into"}

    monkeypatch.setattr(wa, "emit_generative_ui", _no_stream)
    out = await reports.make_report(SPEC)
    assert out["ok"] is True
    assert out["download_url"].endswith("outputs/q3-pipeline-review.pdf")
    assert "Link the download_url" in out["note"]


async def test_a_pdf_that_could_not_be_saved_is_never_ok(
    monkeypatch: pytest.MonkeyPatch, chat: dict[str, list[Any]],
) -> None:
    wa = importlib.import_module("acb_skills.write_artifact")

    async def _refuse(path: str, content: Any, **_kw: Any) -> dict:
        return {"error": "This chat's folder is over its quota."}

    monkeypatch.setattr(wa, "write_artifact", _refuse)
    out = await reports.make_report(SPEC)
    assert out["ok"] is False and "over its quota" in out["error"]
    assert chat["cards"] == []


async def test_the_render_names_the_runs_member_and_org_never_the_specs(
    chat: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
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
        out = await reports.make_report({**SPEC, "member": "b@evil.test", "org": "x"})
    finally:
        release_tenant(tenant)
        _unbind_memory_user_id(user)
    assert out["ok"] is True
    assert seen == [("a@fracktal.in", "11111111-1111-4111-8111-111111111111")]


# ── report-pdf-visuals ──────────────────────────────────────────────────────


async def test_the_pdf_holds_the_drawn_chart_tiles_and_table_with_the_engine_off(
    chat: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With WHATSAPP_CHART_ENGINE off, each visual is our own SVG, placed as
    vector. Its words are text in the PDF, and its shapes are drawings."""
    monkeypatch.setattr(wui.engine, "enabled", lambda: False)
    out = await reports.make_report(SPEC)
    assert out["ok"] is True
    doc = pymupdf.open(stream=chat["writes"][0]["content"], filetype="pdf")
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
    chat: dict[str, list[Any]],
) -> None:
    """The summary and the sources join the 30 sections after ``prepare``
    drew them, so its limit of 30 never refuses a valid report."""
    spec = {**SPEC, "sections": [{"heading": f"Part {i}", "text": "Fine."} for i in range(30)]}
    out = await reports.make_report(spec)
    assert out["ok"] is True, out
    doc = pymupdf.open(stream=chat["writes"][0]["content"], filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    assert text.index("Summary") < text.index("Part 0") < text.index("Part 29") \
        < text.index("Sources")


# ── report-whatsapp ─────────────────────────────────────────────────────────


async def test_on_whatsapp_the_report_is_one_pdf_element_and_no_file_or_card(
    chat: dict[str, list[Any]],
) -> None:
    from gateway.pdf_render import document_to_pdf

    laid: list[dict[str, Any]] = []

    async def _document(layout: dict[str, Any]) -> bytes:
        laid.append(layout)
        return document_to_pdf(json.dumps(layout))

    with wui.whatsapp_run("orchestrator", document=_document) as run:
        out = await reports.make_report(SPEC)
    assert out["ok"] is True and out["file"] == "Q3 pipeline review.pdf", out
    (element,) = run.outbox
    assert element.kind == "document" and element.pdf.startswith(b"%PDF")
    assert element.filename == "Q3 pipeline review.pdf"
    assert "[Document: Q3 pipeline review.pdf · report" in element.rendition
    # The same layout as the web: the summary leads and the sources end it.
    (layout,) = laid
    heads = [s.get("heading") for s in layout["sections"]]
    assert heads == ["Summary", "Revenue", "Deals", "Sources"]
    assert chat["writes"] == [] and chat["cards"] == []


async def test_on_whatsapp_with_documents_off_it_says_so_and_sends_nothing(
    chat: dict[str, list[Any]],
) -> None:
    with wui.whatsapp_run("orchestrator") as run:
        out = await reports.make_report(SPEC)
    assert out == {"ok": False, "error": "documents are off in this chat. Answer in text"}
    assert run.outbox == [] and chat["writes"] == [] and chat["cards"] == []


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
    from pathlib import Path

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


def test_the_docstring_sends_the_model_to_the_guide_and_stays_short() -> None:
    from acb_skills.skill_families import tool_json_schema

    desc = " ".join(tool_json_schema(reports.make_report)["function"]["description"].split())
    assert "make_report({})" in desc and "Never write a report as a .md file" in desc
    assert len(desc) < 1000, len(desc)
