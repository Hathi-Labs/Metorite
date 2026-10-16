"""Reports built by code: crm_report and render_report make a kept report (B3).

Spec: ``project-docs/specs/projects_ai_chat.md`` §14.10.9 (owner directive,
2026-10-16: "Leverage existing programmatic systems instead of needing to
generate everything using the AI").

With ``make_report=true``, and only in a run that holds ``make_report``, each
tool builds the ``make_report`` spec BY CODE from the data of its own route,
and calls ``acb_skills.reports.make_report`` itself, through the one gate
``reports.by_code``. No model is in the loop, so the card, the report file and
the PDF hold the route's numbers.

The tests write into a real working dir. The blob mirror and the file card
are faked, and each ``generative_ui`` card is recorded. The PDF comes from the
real seam, ``gateway.pdf_render``.

R7 fences named here:

* ``by-code-numbers``: the card and the file hold the route's numbers, for
  each CRM kind and for a rendered Projects report. The PDF holds them too.
* ``by-code-cells``: a name, a title or a reason that a member wrote, with a
  URL or a tag, lands in a table cell, and make_report accepts it.
* ``by-code-gate``: a run that does not hold make_report gets REPORTS_OFF,
  the normal answer and its card, and no file.
* ``by-code-whatsapp``: a WhatsApp run gets the PDF, and no file or card.
* ``by-code-class``: each tool that can write files says so in its risk.
* ``by-code-cap``: a report of every section keeps to 12 visuals.
"""
from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pymupdf = pytest.importorskip("pymupdf")

import skill_projects  # noqa: E402
from acb_skills import crm_tools  # noqa: E402
from acb_skills import reports  # noqa: E402
from acb_skills import whatsapp_ui as wui  # noqa: E402
from skill_projects import views  # noqa: E402

from tests.unit._projects_agent_fakes import fake_gateway  # noqa: E402

wa = importlib.import_module("acb_skills.write_artifact")

RID = "0f8fad5b-d9cb-469f-a165-70867728950e"
URL_STAGE = "Proposal https://evil.test/x"
TAG_STAGE = "Qualified <b>"


# ── The route data ──────────────────────────────────────────────────────────
#
# Each shape is the route's model (`gateway/routes/crm/reports.py`). Each free
# text holds a URL, a tag or a "www." host, which make_report refuses in a
# text field.

PIPELINE = {
    "stages": [
        {"status": {"id": "s1", "name": TAG_STAGE, "color": "gray"},
         "count": 3, "amount": 4200000.0, "weighted": 1260000.0},
        {"status": {"id": "s2", "name": URL_STAGE, "color": "gray"},
         "count": 2, "amount": 1800000.4, "weighted": 900000.0},
    ],
    "count": 5, "amount": 6000000.4, "weighted": 2160000.0,
}
FUNNEL = {
    "stages": [
        {"status": {"id": "s1", "name": TAG_STAGE}, "entered": 10, "advanced": 6,
         "conversion_forward": 60.0, "median_dwell_days": 3.5, "dwell_samples": 6},
        {"status": {"id": "s2", "name": URL_STAGE}, "entered": 6, "advanced": 0,
         "conversion_forward": 0.0, "median_dwell_days": None, "dwell_samples": 0},
    ],
    "deals": 12, "transitions": 30,
    "unmatched": [{"name": "Old <stage> www.old.test", "deals": 2, "mentions": 3}],
}
WIN_LOSS = {
    "window_days": 90, "since": "2026-07-18", "won": 4, "lost": 6, "win_rate": 40.0,
    "won_amount": 900000.0, "lost_amount": 1200000.0, "average_cycle_days": 21.5,
    "closed_without_date": 1,
    "lost_reasons": [
        {"label": "Price www.cheaper.test", "deals": 4, "amount": 800000.0, "attributed": True},
        {"label": "Not recorded", "deals": 2, "amount": 400000.0, "attributed": False},
    ],
}
OWNERS = {
    "owners": [
        {"owner_email": "<asha>@x.io", "open_deals": 5, "weighted": 1500000.0,
         "won_amount": 300000.0},
        {"owner_email": None, "open_deals": 2, "weighted": 100000.0, "won_amount": 0.0},
    ],
    "window_days": 90, "omitted": 3,
}
CRM_DATA = {"pipeline": PIPELINE, "funnel": FUNNEL, "win-loss": WIN_LOSS, "owners": OWNERS}

#: A rendered report, as `GET /projects/reports/{id}/render` and the preview
#: route send it: `{report, period_start, period_end, sections}`.
RENDERED = {
    "report": {"id": RID, "name": "Weekly · Web <App>"},
    "period_start": "2026-10-05",
    "period_end": "2026-10-11",
    "sections": {
        "finished": {
            "total_completed": 7, "total_cancelled": 1, "median_hours": 30.5,
            "projects": [{"name": "Web <App>", "completed": 5, "cancelled": 1},
                         {"name": "Ops www.ops.test", "completed": 2, "cancelled": 0}],
        },
        "throughput": {
            "median_hours": 30.5, "measured": 7,
            "series": [{"week_start": "2026-09-28", "completed": 3, "cancelled": 0},
                       {"week_start": "2026-10-05", "completed": 4, "cancelled": 1}],
        },
        "load": {
            "total_tasks": 9, "people_total": 2, "hidden_people": 1,
            "people": [{"assignee": "asha@x.io", "open_tasks": 6, "overdue": 1},
                       {"assignee": None, "open_tasks": 3, "overdue": 0}],
        },
        "stuck": {"overdue_total": 2, "blocked_total": 1,
                  "overdue": [{"name": "Web <App>", "overdue": 2}]},
        "hygiene": {
            "open_total": 4, "stale_days": 14,
            "by_kind": {"no_assignee": 0, "no_due_date": 1, "no_estimate": 0,
                        "stale_in_progress": 0},
            "rows": [{"kind": "no_due_date",
                      "title": "Fix <Button>, see https://github.com/acme/web/pull/12",
                      "project_name": "Web <App>", "updated_at": "2026-10-01T10:00:00Z"}],
        },
    },
}


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """A chat run on a real working dir that holds make_report, as the
    executor binds it. The mirror and the file card are faked, and each card
    is recorded."""
    root = tmp_path / "ws"
    root.mkdir()
    seen: dict[str, Any] = {"root": root, "cards": []}

    async def _emit(ui: str) -> dict:
        seen["cards"].append(json.loads(ui))
        return {"ok": True}

    async def _quiet(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(wa, "emit_generative_ui", _emit)
    monkeypatch.setattr(wa, "_notify", _quiet)
    monkeypatch.setattr(wa, "mirror_to_blob_store", _quiet)
    with wa.artifact_context_scope():
        wa.bind_artifact_context(workspace_root=str(root), session_id="s1",
                                 agent_name="orchestrator", member="a@fracktal.in",
                                 run_tools=frozenset({"make_report", "emit_generative_ui"}))
        yield seen


@pytest.fixture
def crm(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The CRM routes, as their data. Each path read is recorded."""
    paths: list[str] = []

    async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
        paths.append(path)
        return json.loads(json.dumps(CRM_DATA[path.rsplit("/", 1)[-1]]))

    monkeypatch.setattr(crm_tools, "_get", _get)
    return paths


def _projects(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The Projects report routes, through the client's own transport."""
    from tests.unit.test_projects_report_chat_rfinal import _catalogue, _subjects

    def answer(call: dict) -> Any:
        path = call["path"]
        if path == "/projects/reports/templates":
            return _catalogue()
        if path == "/projects/reports/subjects":
            return _subjects()
        if path == "/projects/reports/preview" or path.endswith("/render"):
            return json.loads(json.dumps(RENDERED))
        raise AssertionError(f"unexpected call {call['method']} {path}")

    return fake_gateway(monkeypatch, answer, user="pm@fracktal.in")


async def _run(coro: Any) -> Any:
    """Await *coro*, then the mirror tasks that it started."""
    out = await coro
    rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*rest, return_exceptions=True)
    return out


def _report(chat: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The one report card's data, and the report file it names."""
    cards = [c for c in chat["cards"] if c["props"]["name"] == "report"]
    assert len(cards) == 1, chat["cards"]
    data = cards[0]["props"]["data"]
    body = json.loads((chat["root"] / data["path"]).read_text(encoding="utf-8"))
    return data, body


def _pdf_text(chat: dict[str, Any], data: dict[str, Any]) -> str:
    doc = pymupdf.open(stream=(chat["root"] / data["pdf"]).read_bytes(), filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _files(root: Path) -> list[str]:
    out = root / "outputs"
    return sorted(p.name for p in out.rglob("*") if p.is_file()) if out.exists() else []


def _section(body: dict[str, Any], heading: str) -> dict[str, Any]:
    return next(s for s in body["sections"] if s.get("heading") == heading)


def _kpis(body: dict[str, Any]) -> dict[str, Any]:
    return {k["label"]: k["value"] for k in body["kpis"]}


# ── by-code-numbers and by-code-cells: crm_report ───────────────────────────


def _pipeline_holds_the_route(body: dict[str, Any]) -> None:
    kpis = _kpis(body)
    assert kpis == {"Open deals": 5, "Pipeline value": round(6000000.4),
                    "Weighted forecast": 2160000, "Open stages": 2}
    chart = _section(body, "Value by stage")["visual"]
    assert chart["type"] == "bar" and chart["labels"] == [TAG_STAGE, URL_STAGE]
    assert chart["values"] == [4200000, round(1800000.4)]
    assert _section(body, "Stages")["visual"]["rows"] == [
        [TAG_STAGE, 3, 4200000, 1260000], [URL_STAGE, 2, 1800000, 900000]]


def _funnel_holds_the_route(body: dict[str, Any]) -> None:
    assert _kpis(body) == {"Deals": 12, "Stages": 2, "Stage moves read": 30,
                           "Unknown stage names": 1}
    chart = _section(body, "Deals that entered each stage")["visual"]
    assert chart["labels"] == [TAG_STAGE, URL_STAGE] and chart["values"] == [10, 6]
    assert _section(body, "Conversion by stage")["visual"]["rows"] == [
        [TAG_STAGE, 10, 6, 60.0, 3.5, 6], [URL_STAGE, 6, 0, 0.0, "unknown", 0]]
    assert _section(body, "Stage names that match no stage")["visual"]["rows"] == [
        ["Old <stage> www.old.test", 2, 3]]


def _win_loss_holds_the_route(body: dict[str, Any]) -> None:
    assert _kpis(body) == {"Won": 4, "Lost": 6, "Win rate": 40.0, "Won value": 900000,
                           "Lost value": 1200000, "Average cycle": 21.5,
                           "Closed with no date": 1}
    assert next(k for k in body["kpis"] if k["label"] == "Win rate")["unit"] == "%"
    donut = _section(body, "Won and lost")["visual"]
    assert donut["type"] == "donut" and donut["labels"] == ["Won", "Lost"]
    assert donut["values"] == [4, 6]
    assert _section(body, "Lost reasons")["visual"]["rows"] == [
        ["Price www.cheaper.test", 4, 800000], ["Not recorded", 2, 400000]]
    assert body["period"] == "The last 90 days, since 2026-07-18"


def _owners_holds_the_route(body: dict[str, Any]) -> None:
    assert _kpis(body) == {"Owners in the report": 2, "Owners not in the report": 3,
                           "Won window": 90}
    owners = _section(body, "Owners")
    assert owners["visual"]["rows"] == [["<asha>@x.io", 5, 1500000, 300000],
                                        ["Unassigned", 2, 100000, 0]]
    assert "3 more owners are not in the report." in owners["text"]


CRM_CHECKS = {"pipeline": _pipeline_holds_the_route, "funnel": _funnel_holds_the_route,
              "win-loss": _win_loss_holds_the_route, "owners": _owners_holds_the_route}


@pytest.mark.parametrize("kind", sorted(CRM_DATA))
async def test_crm_report_makes_the_report_from_the_route_data(
    kind: str, chat: dict[str, Any], crm: list[str],
) -> None:
    """by-code-numbers and by-code-cells. The card and the file hold the
    route's numbers, and each free text is a table cell or a chart label."""
    text = await _run(crm_tools.crm_report(kind, make_report=True))
    assert crm == [f"/crm/reports/{kind}"]
    assert "make_report: " in text and "make_report failed" not in text, text
    assert text.startswith(f"CRM report {kind}:\n")
    assert "The report card shows this report." in text
    data, body = _report(chat)
    for report in (data, body):
        CRM_CHECKS[kind](report)
        assert report["sources"] == [f"CRM {kind}, {report['date']}"]
        assert report["title"].startswith("CRM report · ")
    assert {k: v for k, v in data.items() if k != "path"} == body


async def test_the_crm_pdf_holds_the_route_numbers(
    chat: dict[str, Any], crm: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """by-code-numbers, the PDF half. With the chart engine off each visual
    is our own SVG, so its words are text in the PDF."""
    monkeypatch.setattr(wui.engine, "enabled", lambda: False)
    await _run(crm_tools.crm_report("pipeline", make_report=True))
    data, _ = _report(chat)
    pdf = _pdf_text(chat, data)
    assert "Open deals" in pdf and "6000000 INR" in pdf and "2160000 INR" in pdf
    assert "Value by stage (INR)" in pdf and "Qualified <b>" in pdf


def test_no_crm_text_field_holds_member_text() -> None:
    """by-code-cells. A stage, a reason or an owner never reaches a field
    that the markdown-lite check reads, so check_report accepts every spec."""
    for kind, data in CRM_DATA.items():
        spec = crm_tools._crm_report_spec(kind, json.loads(json.dumps(data)))
        checked = reports.check_report(spec)
        words = [checked.get("summary", ""), checked.get("period", "")]
        words += [s.get("text", "") for s in checked["sections"]]
        words += [s.get("heading", "") for s in checked["sections"]]
        for member_text in (TAG_STAGE, URL_STAGE, "www.", "<asha>"):
            assert not any(member_text in w for w in words), (kind, member_text)
        reports.layout_of(checked)  # every visual draws


# ── by-code-numbers and by-code-cells: render_report ────────────────────────


def _rendered_holds_the_route(body: dict[str, Any]) -> None:
    kpis = _kpis(body)
    assert kpis["Finished"] == 7 and kpis["Cancelled"] == 1
    assert kpis["Median hours"] == 30.5 and kpis["Measured"] == 7
    assert kpis["Open tasks"] == 9 and kpis["Overdue"] == 2
    finished = _section(body, "What we finished")
    assert finished["visual"]["labels"] == ["Web <App>", "Ops www.ops.test"]
    assert finished["visual"]["values"] == [5, 2]
    assert "Finished: 7. Cancelled: 1." in finished["text"]
    tables = [s["visual"] for s in body["sections"]
              if s.get("visual", {}).get("kind") == "table"]
    by_title = {t["title"]: t for t in tables}
    assert by_title["What we finished"]["rows"] == [["Web <App>", 5, 1],
                                                    ["Ops www.ops.test", 2, 0]]
    assert by_title["Open work"]["rows"] == [["asha@x.io", 6, 1], ["Unassigned", 3, 0]]
    assert by_title["Data hygiene"]["rows"] == [[
        "No due date", "Fix <Button>, see https://github.com/acme/web/pull/12",
        "Web <App>", "2026-10-01"]]
    weeks = _section(body, "How long it took")["visual"]
    assert weeks["type"] == "line" and weeks["labels"] == ["2026-09-28", "2026-10-05"]
    assert weeks["values"] == [3, 4]
    # The hidden-people line (WS-27bn R5a) is code's words, so it is text.
    assert any("This report hides 1 other person." in s.get("text", "")
               for s in body["sections"])
    assert body["period"] == "2026-10-05 to 2026-10-11"


@pytest.mark.parametrize("form", [{"report_id": RID},
                                  {"template": "team pulse", "subject": "design"}],
                         ids=["saved", "by-name"])
async def test_render_report_makes_the_report_from_the_render_payload(
    form: dict[str, str], chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """by-code-numbers and by-code-cells, for both forms of render_report.
    The kept report replaces the W3 card, so the chat draws one card."""
    _projects(monkeypatch)
    text = await _run(skill_projects.render_report(**form, make_report=True))
    assert "make_report: " in text and "make_report failed" not in text, text
    assert [c["props"]["name"] for c in chat["cards"]] == ["report"]
    data, body = _report(chat)
    for report in (data, body):
        _rendered_holds_the_route(report)
        assert report["title"] == "Weekly · Web <App>"
    assert {k: v for k, v in data.items() if k != "path"} == body


async def test_the_projects_pdf_holds_the_route_numbers(
    chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wui.engine, "enabled", lambda: False)
    _projects(monkeypatch)
    await _run(skill_projects.render_report(report_id=RID, make_report=True))
    data, _ = _report(chat)
    pdf = _pdf_text(chat, data)
    assert "Tasks finished by project" in pdf and "Web <App>" in pdf
    assert "Median hours" in pdf and "30.5" in pdf


def test_a_report_of_every_section_keeps_to_twelve_visuals() -> None:
    """by-code-cap. Ten sections, each with rows, would draw 14 visuals. The
    charts go first, so the tables stay, and make_report accepts the spec."""
    from gateway.routes.projects import reports as rep

    rows = [{"name": f"P{i}", "assignee": f"p{i}@x.io", "completed": i, "cancelled": 0,
             "open_tasks": i, "overdue": i, "week_start": f"2026-09-{i + 10:02d}",
             "title": f"T{i}", "kind": "no_due_date", "sentence": "S", "severity": "high"}
            for i in range(1, 4)]
    payload = {
        "report": {"name": "Everything"}, "period_start": "2026-10-05",
        "period_end": "2026-10-11",
        "sections": {name: {"total": 3, "projects": rows, "series": rows, "people": rows,
                            "overdue": rows, "rows": rows, "at_risk": rows}
                     for name in rep.SECTIONS},
    }
    assert len(payload["sections"]) == 10
    spec = views._report_spec(payload)
    visuals = [s["visual"] for s in spec["sections"] if "visual" in s]
    assert len(visuals) + (1 if spec.get("kpis") else 0) <= 12
    assert sum(1 for v in visuals if v["kind"] == "table") == 9
    reports.layout_of(reports.check_report(spec))


# ── by-code-gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("run_tools", [
    frozenset({"emit_generative_ui", "write_artifact"}),  # documents turned off
    None,  # a run that bound no tools: fail closed
], ids=["documents-off", "no-run-tools"])
async def test_without_make_report_crm_report_says_so_and_writes_nothing(
    run_tools: Any, chat: dict[str, Any], crm: list[str],
) -> None:
    wa.derive_artifact_context(run_tools=run_tools)
    text = await _run(crm_tools.crm_report("pipeline", make_report=True))
    assert reports.REPORTS_OFF in text
    assert "Total: 5 deals · INR 6,000,000 · weighted INR 2,160,000" in text
    assert "make_report=true" not in text
    assert chat["cards"] == [] and _files(chat["root"]) == []


@pytest.mark.parametrize("run_tools", [
    frozenset({"emit_generative_ui", "write_artifact"}), None,
], ids=["documents-off", "no-run-tools"])
async def test_without_make_report_render_report_draws_its_card_and_no_file(
    run_tools: Any, chat: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    wa.derive_artifact_context(run_tools=run_tools)
    _projects(monkeypatch)
    text = await _run(skill_projects.render_report(report_id=RID, make_report=True))
    assert reports.REPORTS_OFF in text and "Report «Weekly · Web <App>»" in text
    assert [c["props"]["name"] for c in chat["cards"]] == ["reportCard"]
    assert _files(chat["root"]) == []


async def test_each_tool_offers_the_report_only_to_a_run_that_holds_it(
    chat: dict[str, Any], crm: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no flag, a run that holds make_report reads how to ask for one,
    and a run without it reads nothing about it. Neither makes a file."""
    _projects(monkeypatch)
    crm_text = await crm_tools.crm_report("owners")
    pm_text = await skill_projects.render_report(report_id=RID)
    assert reports.report_offer("crm_report") in crm_text
    assert reports.report_offer("render_report") in pm_text
    wa.derive_artifact_context(run_tools=frozenset({"emit_generative_ui"}))
    assert "make_report" not in await crm_tools.crm_report("owners")
    assert "make_report" not in await skill_projects.render_report(report_id=RID)
    assert _files(chat["root"]) == []
    assert {c["props"]["name"] for c in chat["cards"]} == {"reportCard"}


# ── by-code-whatsapp ────────────────────────────────────────────────────────


async def test_on_whatsapp_each_tool_sends_the_pdf_and_no_file_or_card(
    chat: dict[str, Any], crm: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.pdf_render import document_to_pdf

    laid: list[dict[str, Any]] = []

    async def _document(layout: dict[str, Any]) -> bytes:
        laid.append(layout)
        return document_to_pdf(json.dumps(layout))

    _projects(monkeypatch)
    with wui.whatsapp_run("orchestrator", document=_document) as run:
        crm_text = await _run(crm_tools.crm_report("win-loss", make_report=True))
        pm_text = await _run(skill_projects.render_report(report_id=RID, make_report=True))
    for text in (crm_text, pm_text):
        assert "as a PDF" in text and "report card" not in text, text
    assert [m.kind for m in run.outbox] == ["document", "document"]
    assert all(m.pdf.startswith(b"%PDF") for m in run.outbox)
    assert [layout["title"] for layout in laid] == ["CRM report · Win and loss",
                                                    "Weekly · Web <App>"]
    assert _files(chat["root"]) == [] and chat["cards"] == []


# ── by-code-class ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["crm_report", "render_report", "status_report"])
def test_each_tool_that_can_write_files_says_so(tool: str) -> None:
    """An edited turn must not run a file writer again unseen
    (``chat_supersede.is_side_effect_tool``). Each stays closed-world and
    destroys nothing."""
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS
    from gateway.chat_supersede import is_side_effect_tool

    hints = TOOL_ANNOTATIONS[tool]
    assert hints["read_only"] is False and hints["idempotent"] is False
    assert hints["destructive"] is False and hints["open_world"] is False
    assert is_side_effect_tool(tool) is True
    assert "make_report=true" in " ".join(
        (getattr(crm_tools, tool, None) or getattr(skill_projects, tool)).__doc__.split())
