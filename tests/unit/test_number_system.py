"""The member's number system: lakh and crore, or million and billion.

Owner, 2026-10-19: "When I ask for graphs and numbers, you use the million
convention, which is not standard in India. Use the user's preferences, and
keep it in the organizational memory." Spec:
``project-docs/specs/whatsapp_assistant_channel.md`` §13.10.

R7 fences named here:

* ``number-system-member-wins``: the member's override wins over the org's
  value, and the org's value over the default ("indian"). The mutation that
  drops the member's arm from ``resolve`` fails here.
* ``number-system-stamped-by-the-server``: a chart that the server builds
  (``emit_generative_ui``, the WhatsApp ``_chart``, ``make_report``) carries
  ``numbering``, and the server's value replaces a value the model wrote.
* ``number-system-prompt-line``: the chat's per-turn context holds the ONE
  line of the resolved system.

The R8 half (the migration, the settings round trip) is
``tests/unit/test_number_system_r8.py``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from acb_common import number_system as ns
from orchestrator import executor as ex

wa = importlib.import_module("acb_skills.write_artifact")


@pytest.fixture(autouse=True)
def _fresh_cache():
    ns.forget()
    yield
    ns.forget()


# ── number-system-member-wins ───────────────────────────────────────────────


def test_the_default_is_indian_for_every_org() -> None:
    assert ns.DEFAULT == "indian"
    assert ns.resolve() == "indian"
    assert ns.resolve(None, None) == "indian"


def test_the_org_value_applies_when_the_member_set_none() -> None:
    assert ns.resolve("international", None) == "international"
    assert ns.resolve({"value": "international", "updatedBy": "a@x.test"}, None) == "international"


def test_the_member_override_wins_over_the_org() -> None:
    assert ns.resolve("international", "indian") == "indian"
    assert ns.resolve("indian", "international") == "international"
    assert ns.resolve({"value": "international"}, "indian") == "indian"


def test_a_bad_value_is_not_set() -> None:
    assert ns.clean("Indian") is None
    assert ns.clean("roman") is None
    assert ns.clean({"value": 3}) is None
    assert ns.clean("{not json") is None
    assert ns.clean('{"value": "international"}') == "international"
    assert ns.resolve("roman", "lakh") == "indian"
    assert ns.resolve("international", "roman") == "international"


# ── number-system-prompt-line ───────────────────────────────────────────────


def test_the_prompt_line_follows_the_system() -> None:
    indian = ns.prompt_line("indian")
    intl = ns.prompt_line("international")
    assert indian == "Write amounts in lakh and crore (₹45.3 lakh, ₹1.35 crore), never million or billion."
    assert "million" in intl and "never lakh or crore" in intl
    assert "\n" not in indian and "\n" not in intl
    assert ns.prompt_line(None) == indian


@pytest.fixture()
def _run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A run with an org and a member, and a recorded read."""
    state: dict[str, Any] = {"org": "org-a", "member": "a@x.test", "row": (None, None), "reads": 0}

    def read(org: str, member: str) -> str:
        state["reads"] += 1
        return ns.resolve(*state["row"])

    monkeypatch.setattr(ex, "_current_run_org", lambda: state["org"])
    monkeypatch.setattr(ex, "_verified_run_member", lambda: state["member"])
    monkeypatch.setattr(ex, "_read_number_system", read)
    monkeypatch.setattr(ex, "_run_org_display_name", lambda: "Acme Tools")
    return state


def _line(parts: list[str]) -> list[str]:
    return [p for p in parts if p.startswith("Write amounts in")]


def test_the_chat_context_carries_one_line_of_the_member_system(_run: dict[str, Any]) -> None:
    assert _line(ex._context_preamble_parts({}, {})) == [ns.PROMPT_LINES["indian"]]
    ns.forget()
    _run["row"] = ("international", None)
    assert _line(ex._context_preamble_parts({}, {})) == [ns.PROMPT_LINES["international"]]
    ns.forget()
    _run["row"] = ("international", "indian")
    assert _line(ex._context_preamble_parts({}, {})) == [ns.PROMPT_LINES["indian"]]


def test_both_chat_paths_carry_the_line(_run: dict[str, Any]) -> None:
    _run["row"] = ("international", None)
    assert ns.PROMPT_LINES["international"] in ex._run_context_text({}, {})
    assert ns.PROMPT_LINES["international"] in ex._build_event_message(
        "orchestrator", "run-1", {"message": "hi"}, {})


def test_the_line_never_comes_from_the_payload(_run: dict[str, Any]) -> None:
    """R5: the payload is request input."""
    text = "\n".join(ex._context_preamble_parts(
        {"number_system": "international", "numbering": "international"}, {}))
    assert ns.PROMPT_LINES["indian"] in text
    assert ns.PROMPT_LINES["international"] not in text


def test_a_turn_reads_once_and_a_save_drops_the_cache(_run: dict[str, Any]) -> None:
    for _ in range(5):
        ex._context_preamble_parts({}, {})
    assert _run["reads"] == 1
    ns.forget("ORG-A")
    ex._context_preamble_parts({}, {})
    assert _run["reads"] == 2


def test_a_run_with_no_org_gets_the_default_no_read_and_no_line(_run: dict[str, Any]) -> None:
    _run["org"] = None
    assert ex.run_number_system() == "indian"
    assert _line(ex._context_preamble_parts({}, {})) == []
    assert _run["reads"] == 0


def test_the_read_fails_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "_opener_for_org", lambda org: None)
    assert ex._read_number_system("org-a", "a@x.test") == "indian"

    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(ex, "_opener_for_org", lambda org: boom)
    assert ex._read_number_system("org-a", "a@x.test") == "indian"


def test_the_read_names_the_run_org_and_member(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            seen.update(params)
            seen["sql"] = str(sql)

            class _R:
                def first(self):
                    return ({"value": "international"}, None)
            return _R()

    monkeypatch.setattr(ex, "_opener_for_org", lambda org: _Session)
    assert ex._read_number_system("org-a", "a@x.test") == "international"
    assert seen["org"] == "org-a" and seen["uid"] == "a@x.test"
    assert "org_settings" in seen["sql"] and "user_settings" in seen["sql"]


# ── number-system-stamped-by-the-server ─────────────────────────────────────


def _chart(**extra: Any) -> dict[str, Any]:
    return {"type": "template", "props": {"name": "chart", "data": {
        "type": "bar", "title": "Revenue", "labels": ["Q1"], "values": [1350000], **extra}}}


def _report() -> dict[str, Any]:
    return {"type": "template", "props": {"name": "report", "data": {
        "title": "Q3", "sections": [
            {"heading": "Revenue", "visual": {"kind": "chart", "type": "bar", "title": "R",
                                               "labels": ["a"], "values": [1]}},
            {"heading": "Plan", "visual": {"kind": "gantt", "title": "G", "rows": []}},
            {"heading": "Table", "visual": {"kind": "table", "title": "T", "columns": [], "rows": []}},
        ]}}}


def test_the_stamp_reaches_every_chart_of_a_tree() -> None:
    tree = {"type": "stack", "children": [_chart(), {"type": "card", "children": [_report()]},
                                          {"type": "template", "props": {"name": "dataGrid", "data": {}}}]}
    ns.stamp_ui(tree, "international")
    assert tree["children"][0]["props"]["data"]["numbering"] == "international"
    sections = tree["children"][1]["children"][0]["props"]["data"]["sections"]
    assert [s["visual"].get("numbering") for s in sections] == ["international", "international", None]
    assert "numbering" not in tree["children"][2]["props"]["data"]


def test_the_server_value_replaces_the_model_value() -> None:
    spec = _chart(numbering="international")
    ns.stamp_ui(spec, "indian")
    assert spec["props"]["data"]["numbering"] == "indian"
    assert ns.stamp_chart({"numbering": "roman"}, "bogus") == {"numbering": "indian"}


def test_a_strange_tree_never_raises() -> None:
    for node in (None, [], "x", {"type": "template", "props": "x"}, {"children": "x"},
                 {"type": "template", "props": {"name": "report", "data": {"sections": [1, None]}}}):
        ns.stamp_ui(node, "indian")


async def test_a_chart_the_chat_draws_carries_numbering(monkeypatch: pytest.MonkeyPatch) -> None:
    q: asyncio.Queue = asyncio.Queue()
    ex._register_run_queue("num-sess", q)
    wa.derive_artifact_context(session_id="num-sess")
    monkeypatch.setattr(wa, "run_numbering", lambda: "international")
    try:
        res = await wa.emit_generative_ui(json.dumps(_chart(numbering="indian")))
    finally:
        ex._RUN_QUEUES.clear()
        wa.bind_artifact_context()
    assert res["ok"] is True
    event = q.get_nowait()
    assert event["value"]["props"]["data"]["numbering"] == "international"


def test_run_numbering_reads_the_one_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ex, "run_number_system", lambda: "international")
    assert wa.run_numbering() == "international"

    def boom() -> str:
        raise RuntimeError("no run")

    monkeypatch.setattr(ex, "run_number_system", boom)
    assert wa.run_numbering() == "indian"


def test_a_whatsapp_chart_carries_numbering(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = importlib.import_module("acb_skills.whatsapp_ui")
    drawn: list[dict[str, Any]] = []

    def engine(spec: dict[str, Any]) -> bytes:
        drawn.append(spec)
        return b"\x89PNG"

    monkeypatch.setattr(ui, "_engine_png", engine)
    monkeypatch.setattr(wa, "run_numbering", lambda: "international")
    ui._chart({"type": "bar", "title": "Revenue", "labels": ["a"], "values": [1], "numbering": "indian"})
    assert drawn[0]["numbering"] == "international"


async def test_a_kept_report_carries_numbering(monkeypatch: pytest.MonkeyPatch) -> None:
    reports = importlib.import_module("acb_skills.reports")
    seen: dict[str, Any] = {}

    def refusal(data: dict[str, Any]) -> str:
        seen["report"] = data
        return "stop here"

    monkeypatch.setattr(wa, "run_numbering", lambda: "international")
    monkeypatch.setattr(reports, "card_refusal", refusal)
    out = await reports.make_report({"title": "Q3", "sections": [
        {"heading": "Revenue", "visual": {"kind": "chart", "type": "bar", "title": "R",
                                           "labels": ["a"], "values": [1]}}]})
    assert out == {"ok": False, "error": "stop here"}
    assert seen["report"]["sections"][0]["visual"]["numbering"] == "international"


# ── number-system-one-formatter: the Python twin agrees with the web ────────

_CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "number_format_cases.json")
    .read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: str(c["value"]))
def test_the_python_twin_agrees_with_number_format_mjs(case: dict[str, Any]) -> None:
    """``numberFormat.test.ts`` reads the same table."""
    assert ns.compact_number(case["value"], "indian") == case["indian"]
    assert ns.compact_number(case["value"], "international") == case["international"]


def test_grouping_follows_the_system() -> None:
    assert ns.group(123456789, "indian") == "12,34,56,789"
    assert ns.group(123456789, "international") == "123,456,789"
    assert ns.group(1350.5, "indian") == "1,350.5"


# ── The old WhatsApp renderer (the fallback when the engine is off) ─────────

NB = chr(0xA0)


def test_the_old_renderer_writes_the_old_way_outside_a_system() -> None:
    render = importlib.import_module("acb_skills.whatsapp_render")
    assert render.fmt(1250000) == "1,250,000"
    assert render.fmt(1250000, "₹") == "₹1,250,000"


def test_the_old_renderer_writes_lakh_and_crore_in_the_indian_system() -> None:
    render = importlib.import_module("acb_skills.whatsapp_render")
    with render.numbering("indian"):
        assert render.fmt(1250000) == f"12.5{NB}L"
        assert render.fmt(13500000, "₹") == f"₹1.35{NB}Cr"
        assert render.fmt(1.35e10) == f"1,350{NB}Cr"
        assert render.fmt(85000) == "85,000"
    assert render.fmt(1250000) == "1,250,000"


def test_the_old_renderer_writes_million_in_the_international_system() -> None:
    render = importlib.import_module("acb_skills.whatsapp_render")
    with render.numbering("international"):
        assert render.fmt(1250000) == "1.25M"
        assert render.fmt(2.5e9, "₹") == "₹2.5B"
        assert render.fmt(850000) == "850,000"


@pytest.mark.parametrize(("system", "want", "never"), [
    ("indian", f"1.35{NB}Cr", "13.5M"), ("international", "13.5M", f"{NB}Cr")])
def test_a_fallback_chart_uses_the_stamped_system(
    monkeypatch: pytest.MonkeyPatch, system: str, want: str, never: str,
) -> None:
    ui = importlib.import_module("acb_skills.whatsapp_ui")
    drawn: list[str] = []
    monkeypatch.setattr(ui, "_engine_png", lambda spec: None)
    monkeypatch.setattr(ui, "_svg_image", lambda svg, rendition, caption: drawn.append(svg))
    monkeypatch.setattr(wa, "run_numbering", lambda: system)
    ui._chart({"type": "bar", "title": "Revenue", "labels": ["Q1", "Q2"],
               "values": [13500000, 4000000], "numbering": "roman"})
    assert want in drawn[0] and never not in drawn[0]
