"""WS-47 WAC-10f — WhatsApp charts in the product's one chart language.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §13.8.

R7 fences named here:

* ``wac10f-one-kind-list``: the tool offers exactly the kinds that the web
  app's ``kinds.mjs`` draws (``whatsapp_engine.KINDS``).
* ``wac10f-falls-back``: an engine that is off, missing, slow or broken never
  costs the member a chart of the four old kinds. A new kind is refused with
  what to use instead.
* ``wac10f-engine-draws``: with Node and the web app's packages present, every
  kind draws a PNG through the child process, and a bad spec comes back with
  the engine's reason (skipped where the packages are not installed).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

import pytest
from acb_common import get_settings
from acb_skills import whatsapp_engine as engine
from acb_skills import whatsapp_render as render
from acb_skills import whatsapp_ui as wui

pytest.importorskip("pymupdf")

PNG = b"\x89PNG\r\n\x1a\n"
KINDS_MJS = engine.ENGINE.parent / "kinds.mjs"
NODE_MODULES = engine.ENGINE.parents[3] / "node_modules"
needs_engine = pytest.mark.skipif(
    shutil.which("node") is None or not (NODE_MODULES / "echarts").is_dir()
    or not (NODE_MODULES / "@resvg" / "resvg-js").is_dir(),
    reason="the chart engine needs node and the web app's packages",
)

SPECS: dict[str, dict[str, Any]] = {
    "bar": {"labels": ["A", "B"], "values": [3, 1], "tones": ["red", "amber"]},
    "line": {"labels": ["W1", "W2", "W3"], "values": [1, 4, 2]},
    "area": {"labels": ["W1", "W2"], "series": [{"name": "Done", "values": [1, 2]},
                                                {"name": "To do", "values": [4, 3]}]},
    "donut": {"labels": ["Build", "Meet"], "values": [18, 9]},
    "progress": {"labels": ["Tasks"], "values": [23], "totals": [30]},
    "scatter": {"groups": [{"name": "Ops", "points": [[1, 2], [3, 5]]}]},
    "heatmap": {"x": ["9a", "10a"], "y": ["Mon", "Tue"], "values": [[1, 2], [3, 4]]},
    "radar": {"axes": ["Price", "Speed", "Terms"], "series": [{"name": "A", "values": [8, 6, 9]}]},
    "box": {"groups": [{"name": "Ops", "values": [2, 3, 5, 8]}]},
    "waterfall": {"steps": [{"label": "Open", "value": 42}, {"label": "Sales", "value": 31},
                            {"label": "Close", "total": True}]},
    "funnel": {"labels": ["Leads", "Won"], "values": [240, 12]},
    "calendar": {"days": [["2026-10-01", 3], ["2026-10-05", 7]]},
    "gantt": {"today": "2026-10-11", "rows": [{"label": "Design", "start": "2026-09-01",
                                               "end": "2026-09-20", "progress": 100}]},
}


def _engine(monkeypatch: pytest.MonkeyPatch, on: bool = True) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_chart_engine", on, raising=False)


# ── One list of kinds ───────────────────────────────────────────────────────


def test_the_tool_offers_exactly_the_kinds_the_engine_draws() -> None:
    source = KINDS_MJS.read_text(encoding="utf-8")
    block = source.split("export const KINDS = {", 1)[1].split("};", 1)[0]
    js = re.findall(r"^\s*(\w+):", block, re.MULTILINE)
    assert sorted(js) == sorted(engine.KINDS) == sorted(SPECS)


def test_the_tool_docstring_offers_each_kind_the_engine_draws() -> None:
    # What the model reads is the docstring, so the fence is on the docstring
    # (verifier, 2026-10-11): a kind the engine adds must reach the model.
    doc = wui.whatsapp_ui.__doc__ or ""
    missing = [k for k in engine.KINDS if k not in doc]
    assert not missing, f"whatsapp_ui's docstring never offers: {missing}"


# ── The fallback ────────────────────────────────────────────────────────────


async def test_with_the_engine_off_the_old_kinds_still_draw_and_a_new_one_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine(monkeypatch, False)
    with wui.whatsapp_run("orchestrator") as run:
        ok = await wui.whatsapp_ui("chart", {"type": "bar", "title": "T", **SPECS["bar"]})
        refused = await wui.whatsapp_ui("chart", {"type": "funnel", "title": "T", **SPECS["funnel"]})
    assert ok["ok"] is True and run.outbox[0].png.startswith(PNG)
    assert refused["ok"] is False and "bar, line, progress, donut" in refused["error"]


@pytest.mark.parametrize("breakage", ["missing", "timeout", "crash", "garbage", "not_png", "short"])
async def test_a_broken_engine_falls_back_to_the_old_renderer(
    monkeypatch: pytest.MonkeyPatch, breakage: str,
) -> None:
    _engine(monkeypatch, True)
    if breakage == "missing":
        monkeypatch.setattr(engine, "ENGINE", engine.ENGINE.with_name("nowhere.mjs"))
    else:
        monkeypatch.setattr(engine, "_node", lambda: "node")
        monkeypatch.setattr(engine.ENGINE.__class__, "is_file", lambda self: True)

        def _run(*_a: Any, **_k: Any) -> Any:
            if breakage == "timeout":
                raise subprocess.TimeoutExpired("node", engine.TIMEOUT_S)
            out = {"crash": b"", "garbage": b"{oops", "short": b'{"images": []}',
                   "not_png": b'{"images": [{"png": "aGVsbG8="}]}'}[breakage]
            return subprocess.CompletedProcess([], 1 if breakage == "crash" else 0, out, b"")

        monkeypatch.setattr(engine.subprocess, "run", _run)
    with wui.whatsapp_run("orchestrator") as run:
        out = await wui.whatsapp_ui("chart", {"type": "line", "title": "T", **SPECS["line"]})
    assert out["ok"] is True, out
    assert run.outbox[0].png.startswith(PNG)


def test_engine_errors_are_typed() -> None:
    assert issubclass(engine.EngineUnavailable, RuntimeError)
    assert not issubclass(engine.EngineUnavailable, ValueError), \
        "an unavailable engine must not read as a bad spec"


# ── The renditions the thread keeps ─────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(SPECS))
def test_each_kind_has_a_rendition_with_its_numbers(kind: str) -> None:
    text = wui._chart_rendition(kind, "T", SPECS[kind])
    assert text.startswith(f"[Chart, {kind}: T]")
    if kind not in ("scatter", "box", "gantt"):
        assert re.search(r"\d", text.split("]", 1)[1]), text


@pytest.mark.parametrize("junk", [{"series": "x"}, {"days": [["a"]]}, {"steps": 3}, {"groups": [1]}])
def test_a_rendition_never_raises_on_an_odd_shape(junk: dict[str, Any]) -> None:
    for kind in SPECS:
        assert wui._chart_rendition(kind, "T", junk).startswith("[Chart")


# ── The engine itself (needs node and the packages) ─────────────────────────


@needs_engine
@pytest.mark.parametrize("kind", sorted(SPECS))
def test_every_kind_draws_through_the_child_process(kind: str) -> None:
    png = engine.render_png({"type": kind, "title": "T", **SPECS[kind]})
    assert png.startswith(PNG) and len(png) > 2000


@needs_engine
def test_a_bad_spec_comes_back_with_the_engines_reason() -> None:
    with pytest.raises(render.RenderError, match="one value per label"):
        engine.render_png({"type": "bar", "title": "T", "labels": ["a", "b"], "values": [1]})


@needs_engine
async def test_the_tool_draws_a_new_kind_with_the_engine_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _engine(monkeypatch, True)
    with wui.whatsapp_run("orchestrator") as run:
        out = await wui.whatsapp_ui("chart", {"type": "funnel", "title": "Pipeline",
                                              "caption": "Q3.", **SPECS["funnel"]})
        gantt = await wui.whatsapp_ui("gantt", {"title": "Plan", **SPECS["gantt"]})
    assert out["ok"] is True and gantt["ok"] is True
    funnel, plan = run.outbox
    assert funnel.png.startswith(PNG) and funnel.caption == "Q3."
    assert funnel.rendition.startswith("[Chart, funnel: Pipeline] Leads: 240, Won: 12")
    assert plan.rendition.startswith("[Schedule: Plan]")


@needs_engine
async def test_a_bad_spec_reaches_the_model_as_a_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    _engine(monkeypatch, True)
    with wui.whatsapp_run("orchestrator"):
        out = await wui.whatsapp_ui("chart", {"type": "heatmap", "title": "T", "x": ["a"],
                                              "y": ["r"], "values": [["lots"]]})
    assert out["ok"] is False and "must be a number" in out["error"]


# ── The review round of 2026-10-11 ──────────────────────────────────────────


def test_a_status_word_reaches_the_engine_as_the_products_hue() -> None:
    spec = wui._with_hues({"type": "bar", "tones": ["On hold", "Shipped", "review", "red"],
                           "rows": [{"label": "x", "status": "Waiting on vendor"}, {"label": "y"}]})
    from acb_skills import whatsapp_cards as cards

    assert spec["tones"] == [cards.hue(w) for w in ("On hold", "Shipped", "review", "red")]
    assert spec["tones"][0] == "amber" and spec["tones"][3] == "red"
    assert spec["rows"][0]["status"] == cards.hue("Waiting on vendor")
    assert "status" not in spec["rows"][1]


def test_the_child_gets_the_bh1_allowlist_never_the_gateways_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    monkeypatch.setattr(engine, "_node", lambda: "node")
    monkeypatch.setattr(engine.ENGINE.__class__, "is_file", lambda self: True)

    def _run(argv: list[str], **kw: Any) -> Any:
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, b'{"images": [{"error": "x"}]}', b"")

    monkeypatch.setattr(engine.subprocess, "run", _run)
    engine.render_many([{"type": "bar"}])
    assert isinstance(seen.get("env"), dict) and "DATABASE_URL" not in seen["env"]


async def test_a_fault_inside_the_engine_falls_back_and_never_reads_as_a_spec_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine(monkeypatch, True)
    monkeypatch.setattr(engine, "_node", lambda: "node")
    monkeypatch.setattr(engine.ENGINE.__class__, "is_file", lambda self: True)
    monkeypatch.setattr(engine.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, b'{"images": [{"fault": "TypeError"}]}', b""))
    with wui.whatsapp_run("orchestrator") as run:
        out = await wui.whatsapp_ui("chart", {"type": "bar", "title": "T", **SPECS["bar"]})
        new = await wui.whatsapp_ui("chart", {"type": "funnel", "title": "T", **SPECS["funnel"]})
    assert out["ok"] is True and run.outbox[0].png.startswith(PNG)
    assert new["ok"] is False and "TypeError" not in new["error"]


def test_an_unavailable_engine_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[str] = []
    monkeypatch.setattr(engine._log, "warning", lambda event, **kw: logged.append(event))
    monkeypatch.setattr(engine, "ENGINE", engine.ENGINE.with_name("nowhere.mjs"))
    with pytest.raises(engine.EngineUnavailable):
        engine.render_many([{"type": "bar"}])
    assert logged == ["whatsapp_engine.unavailable"]
