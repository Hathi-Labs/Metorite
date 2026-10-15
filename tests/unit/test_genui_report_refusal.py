"""The ``report`` card is refused when it is over a cap (owner directive, 2026-10-16).

``make_report`` sends a ``generative_ui`` card with the template ``report``,
and ``ReportView`` in ``genUITemplates.tsx`` draws it. The renderer caps each
list (``REPORT_LIMITS`` in ``lib/report.ts``), so an oversize report would draw
cut with nothing to say so. ``genui_refusal`` refuses it instead, and the
refusal names the cap, so the model can send it again.

Mutations this file catches (R7), each run red before the change:

- ``genui_refusal`` stops calling ``genui_report_refusal`` -> "an oversize
  report is refused through emit_generative_ui";
- a cap is widened -> the case of that cap;
- the size check goes -> "a report over the size cap is refused".
"""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest


def _wa():
    return importlib.import_module("acb_skills.write_artifact")


def _report(**over: object) -> dict:
    data: dict = {
        "kind": "metorite.report",
        "path": "outputs/q3.report.json",
        "pdf": "outputs/q3.pdf",
        "title": "Q3 review",
        "date": "16 Oct 2026",
        "kpis": [{"label": "Revenue", "value": "4.2", "unit": "Cr", "delta": "+12%", "good": "up"}],
        "summary": "**Revenue grew.**",
        "sections": [
            {"heading": "Pipeline", "visual": {"kind": "chart", "type": "bar", "labels": ["A"], "values": [1]}},
            {"heading": "Deals", "visual": {"kind": "table", "columns": ["a", "b"], "rows": [["x", 1]]}},
        ],
        "sources": ["CRM"],
    }
    data.update(over)
    return data


def _ui(data: object) -> dict:
    return {"type": "template", "props": {"name": "report", "data": data}}


def test_a_good_report_is_not_refused() -> None:
    wa = _wa()
    assert wa.genui_report_refusal(_report()) is None
    assert wa.genui_refusal(_ui(_report())) is None


def test_an_oversize_report_is_refused_through_emit_generative_ui() -> None:
    wa = _wa()
    big = _report(kpis=[{"label": f"K{i}", "value": i} for i in range(9)])
    res = asyncio.run(wa.emit_generative_ui(json.dumps(_ui(big))))
    assert res["ok"] is False
    assert res["error"].startswith("ui.props.data:"), res["error"]
    assert "at most 8 tiles" in res["error"]


@pytest.mark.parametrize(
    ("over", "words"),
    [
        ({"kpis": [{"label": "k", "value": 1}] * 9}, "at most 8 tiles"),
        ({"sections": []}, "1 to 30 sections"),
        ({"sections": [{"heading": "s"}] * 31}, "1 to 30 sections"),
        ({"sources": ["s"] * 31}, "at most 30 lines"),
        ({"title": ""}, 'needs a "title"'),
        ({"title": "t" * 121}, '"title" is longer than 120'),
        ({"summary": "s" * 2401}, '"summary" is longer than 2400'),
        ({"sections": [{"heading": "s", "bullets": ["b"] * 17}]}, "at most 16 lines"),
        ({"sections": [{"visual": {"kind": "map"}}]}, '"kind" must be one of'),
        ({"sections": [{"visual": {"kind": "table", "columns": list("abcdefg"), "rows": []}}]}, "at most 6 columns"),
        ({"sections": [{"visual": {"kind": "table", "columns": ["a"], "rows": [["x"]] * 21}}]}, "at most 20 rows"),
        ({"sections": [{"visual": {"kind": "chart", "type": "bar"}}] * 13}, "at most 12 visuals"),
        ({"sections": ["not an object"]}, "a section is an object"),
    ],
)
def test_each_cap_is_refused_by_name(over: dict, words: str) -> None:
    found = _wa().genui_report_refusal(_report(**over))
    assert found and words in found, found


def test_a_report_over_the_size_cap_is_refused() -> None:
    wa = _wa()
    # Every text field is under its cap, so only the size cap can refuse it:
    # a visual's own data has no cap of its own here.
    labels = [f"label {i:06d} " + "x" * 200 for i in range(1000)]
    chart = {"kind": "chart", "type": "bar", "labels": labels, "values": list(range(1000))}
    big = _report(sections=[{"heading": "s", "visual": chart}])
    assert len(json.dumps(big)) > wa.GENUI_REPORT_MAX_CHARS
    found = wa.genui_report_refusal(big)
    assert found and "Shorten it" in found and str(wa.GENUI_REPORT_MAX_CHARS) in found


def test_data_that_is_not_an_object_is_refused() -> None:
    wa = _wa()
    assert "a JSON object" in (wa.genui_report_refusal([1, 2]) or "")
    assert "a JSON object" in (wa.genui_refusal(_ui("text")) or "")


def test_a_report_with_no_data_asks_for_its_shape_and_draws_nothing() -> None:
    """``genui_guide`` answers first: no data is a request for the shape."""
    wa = _wa()
    res = asyncio.run(wa.emit_generative_ui('{"type":"template","props":{"name":"report"}}'))
    assert res["ok"] is False and res["drawn"] is False
    assert res["shape"].startswith(f"• report — {wa.GENUI_TEMPLATE_SHAPES['report']}")


def test_the_caps_are_the_documents_caps() -> None:
    """One cap per list: ``make_document``'s where it has one."""
    from acb_skills import documents

    wa = _wa()
    caps = {
        "MAX_SECTIONS": 30, "MAX_VISUALS": 12, "TITLE_MAX": 120, "TEXT_MAX": 1200, "MAX_BULLETS": 8,
    }
    for name, value in caps.items():
        assert getattr(documents, name) == value, name
    assert documents.VISUAL_KINDS == ("chart", "stats", "table", "board", "timeline", "gantt", "agenda")
    assert (wa.GENUI_REPORT_MAX_KPIS, wa.GENUI_REPORT_MAX_TABLE_COLUMNS, wa.GENUI_REPORT_MAX_TABLE_ROWS) == (8, 6, 20)


def test_the_renderer_caps_match() -> None:
    """``REPORT_LIMITS`` in ``lib/report.ts`` draws at most what this refuses
    above, so a report the tool accepts is never cut on screen."""
    import re
    from pathlib import Path

    wa = _wa()
    ts = (Path(__file__).resolve().parents[2] / "workbench" / "control_plane" / "src" / "lib" / "report.ts").read_text(
        encoding="utf-8"
    )
    block = ts.split("export const REPORT_LIMITS = {", 1)[1].split("} as const;", 1)[0]
    limits = {k: int(v) for k, v in re.findall(r"^\s*(\w+): (\d+),", block, re.M)}
    assert limits["kpis"] == wa.GENUI_REPORT_MAX_KPIS
    assert limits["sources"] == wa.GENUI_REPORT_MAX_SOURCES
    assert limits["tableColumns"] == wa.GENUI_REPORT_MAX_TABLE_COLUMNS
    assert limits["tableRows"] == wa.GENUI_REPORT_MAX_TABLE_ROWS
    assert limits["sections"] == 30 and limits["bullets"] == 16
