"""WS-47 WAC-12 — a PDF document from WhatsApp (spec §15.6, D1 to D8).

The REAL ``acb_skills.documents``, the REAL ``gateway.pdf_render`` (in
process, and once through its child), the REAL ``whatsapp_ui`` tool and the
REAL ``bot_run`` send path run here. The fakes are those of
``test_wac_bot_run.py`` and ``test_wac_native_ui.py``, plus a provider that
records each document it sends.

R7 fences named here:

* ``wac12-deck`` (D1) and ``wac12-report`` (D2): the layout and the page size.
* ``wac12-send`` (D3): one document part, its file name and its caption.
* ``wac12-text-only`` (D4): markup stays text, and a key that would carry an
  image, an SVG or a URL is refused.
* ``wac12-bounds`` (D5): each limit refuses with what to fix.
* ``wac12-seam`` (D6): the layout runs in the ``render_pdf`` child.
* ``wac12-dark`` (D7): with the switch off, the kind refuses.
* ``wac12-unsure`` (D8): a failed upload sends no document.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

pymupdf = pytest.importorskip("pymupdf")

from acb_common import get_settings  # noqa: E402
from acb_skills import documents  # noqa: E402
from acb_skills import whatsapp_ui as wui  # noqa: E402
from gateway import pdf_render  # noqa: E402
from gateway.pdf_render import PdfRenderError, document_to_pdf, render_pdf  # noqa: E402
from gateway.routes.whatsapp_channel import bot_run  # noqa: E402

from tests.unit.test_wac_bot_run import (  # noqa: E402, F401 - `world` is a fixture
    _message,
    _post_and_run,
    _World,
    world,
)
from tests.unit.test_wac_native_ui import _UiProvider  # noqa: E402

STATS = {"kind": "stats", "title": "Pipeline", "tiles": [
    {"label": "Open deals", "value": "12"},
    {"label": "Pipeline", "value": "₹1.4 Cr", "delta": "+18%", "good": "up"}]}
CHART = {"kind": "chart", "type": "bar", "title": "Closed revenue",
         "labels": ["Jul", "Aug", "Sep"], "values": [32, 41, 55], "unit": "L"}
TABLE = {"kind": "table", "title": "Top deals", "columns": ["Deal", "Stage", "Value"],
         "rows": [["Bharat Steel", "Proposal", "₹42 L"], ["Kaveri Foods", "Demo", "₹28 L"]]}

DECK = {
    "format": "deck", "title": "Acme Q4 plan", "subtitle": "The next 90 days",
    "for": "Acme leadership", "filename": "Acme Q4 plan", "caption": "The Q4 plan.",
    "slides": [
        {"type": "cover", "title": "Acme Q4 plan"},
        {"type": "content", "title": "Pipeline", "bullets": ["12 open deals"], "visual": STATS},
        {"type": "content", "title": "Revenue", "visual": CHART},
        {"type": "content", "title": "Top deals", "text": "Two deals hold most of it.",
         "visual": TABLE},
        {"type": "closing", "title": "Next steps", "bullets": ["Call Bharat Steel"]},
    ],
}


def _pdf(spec: dict[str, Any]) -> Any:
    return pymupdf.open(stream=document_to_pdf(json.dumps(documents.prepare(spec))),
                        filetype="pdf")


def _all_text(doc: Any) -> str:
    return "\n".join(page.get_text() for page in doc)


# ── D1 and D2: the layout ───────────────────────────────────────────────────


def test_a_deck_is_one_16_by_9_page_a_slide_and_its_text_is_selectable() -> None:
    doc = _pdf(DECK)
    assert doc.page_count == 5
    assert all((p.rect.width, p.rect.height) == pdf_render.DECK_SIZE for p in doc)
    text = _all_text(doc)
    for words in ("Acme Q4 plan", "Prepared for Acme leadership", "12 open deals",
                  "Bharat Steel", "Next steps", "5 / 5"):
        assert words in text, words
    # The cards are vector: their words are text in the PDF, not pixels.
    assert "Open deals" in doc[1].get_text()


def test_a_report_is_a4_and_a_long_one_flows_onto_more_pages() -> None:
    spec = {"format": "report", "title": "October review", "sections": [
        {"heading": f"Account {i}", "text": "A finding about the account. " * 30,
         "bullets": ["One", "Two"]} for i in range(10)]}
    doc = _pdf(spec)
    assert doc.page_count >= 2
    assert all(round(p.rect.width) == 595 and round(p.rect.height) == 842 for p in doc)
    assert f"1 / {doc.page_count}" in doc[0].get_text()
    # A heading never ends a page: each page's last body line is no heading.
    for page in doc:
        lines = [ln for ln in page.get_text().splitlines() if ln.strip()]
        body = [ln for ln in lines if "/" not in ln and ln != "October review"]
        assert not body or not body[-1].startswith("Account "), page.number


# ── D4: text only ───────────────────────────────────────────────────────────


def test_markup_from_the_model_is_printed_as_text_and_runs_nothing() -> None:
    spec = {"format": "deck", "title": "<script>alert(1)</script>", "slides": [
        {"type": "content", "title": "<b>x</b>",
         "text": '<img src="http://127.0.0.1:9/x.png"> and <a href="javascript:x">y</a>'}]}
    raw = document_to_pdf(json.dumps(documents.prepare(spec)))
    doc = pymupdf.open(stream=raw, filetype="pdf")
    text = _all_text(doc)
    assert "<script>alert(1)</script>" in text and "<b>x</b>" in text
    assert '<img src="http://127.0.0.1:9/x.png">' in text
    assert all(not page.get_links() for page in doc)
    assert b"/JavaScript" not in raw and b"/URI" not in raw


@pytest.mark.parametrize("bad", [
    {"slides": [{"type": "content", "title": "T", "visual": {**STATS, "svg": "<svg/>"}}]},
    {"slides": [{"type": "content", "title": "T", "image": "x.png"}]},
    {"slides": [{"type": "content", "title": "T", "url": "https://evil.example"}]},
    {"html": "<h1>x</h1>", "slides": [{"type": "cover", "title": "T"}]},
])
def test_a_key_that_would_carry_markup_bytes_or_a_url_is_refused(bad: dict) -> None:
    with pytest.raises(documents.DocumentError, match="text and numbers only"):
        documents.prepare({"format": "deck", "title": "T", **bad})


@pytest.mark.parametrize("visual", [
    {"svg": '<svg xmlns="http://www.w3.org/2000/svg"><image href="http://127.0.0.1:9/x"/></svg>'},
    {"svg": '<svg xmlns="http://www.w3.org/2000/svg" style="fill:url(http://x)"/>'},
    {"png": "aGVsbG8="},
])
def test_the_layout_refuses_a_visual_that_metorite_did_not_draw(visual: dict) -> None:
    layout = {"format": "deck", "title": "T", "slides": [
        {"type": "content", "title": "T", "text": "", "bullets": [], "visual": visual}]}
    with pytest.raises(PdfRenderError):
        document_to_pdf(json.dumps(layout))


# ── D5: the bounds ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("spec", "says"), [
    ({"format": "deck", "title": "T", "slides": [{"type": "cover", "title": "T"}] * 21},
     "at most 20 slides"),
    ({"format": "report", "title": "T", "sections": [{"heading": "H", "visual": CHART}] * 13},
     "at most 12 visuals"),
    ({"format": "deck", "title": "T" * 121, "slides": [{"type": "cover", "title": "T"}]},
     "longer than 120"),
    ({"format": "deck", "title": "T", "slides": [{"type": "cover", "title": "T",
                                                  "visual": STATS}]},
     'only a "content" slide'),
    ({"format": "deck", "title": "T", "slides": [{"type": "content", "title": "T",
                                                  "visual": {"kind": "map"}}]},
     "visual.kind"),
    ({"format": "deck", "title": "T", "slides": [{"type": "content", "title": "T",
                                                  "bullets": ["b"] * 9}]},
     "at most 8 bullets"),
    ({"format": "slides", "title": "T"}, '"format" must be one of'),
    ({"format": "deck", "title": "T", "slides": []}, "at least one"),
])
def test_each_limit_refuses_with_what_to_fix(spec: dict, says: str) -> None:
    with pytest.raises(documents.DocumentError, match=says):
        documents.prepare(spec)


def test_a_bad_visual_says_what_the_chart_builder_says() -> None:
    with pytest.raises(documents.DocumentError, match=r"slides\[0\]\.visual"):
        documents.prepare({"format": "deck", "title": "T", "slides": [
            {"type": "content", "title": "T", "visual": {"kind": "chart", "type": "bar"}}]})


def test_the_file_name_is_safe_and_ends_in_pdf() -> None:
    assert documents.filename_of("Q4 plan") == "Q4 plan.pdf"
    assert documents.filename_of("x", "../../etc/passwd\x00.pdf") == "etc passwd.pdf"
    assert documents.filename_of("", "") == "Document.pdf"


# ── D6: the one seam ────────────────────────────────────────────────────────


def test_the_layout_runs_in_the_render_pdf_child() -> None:
    source = json.dumps(documents.prepare(DECK))
    pdf = asyncio.run(render_pdf("document", source, member="a@x.io", org="org-doc"))
    assert pdf.startswith(b"%PDF")
    assert pymupdf.open(stream=pdf, filetype="pdf").page_count == 5


def test_a_document_layout_has_its_own_size_limit() -> None:
    with pytest.raises(PdfRenderError) as err:
        asyncio.run(render_pdf("document", "x" * (pdf_render.MAX_DOCUMENT_BYTES + 1),
                               member="a@x.io", org="org-doc"))
    assert err.value.status == 413


# ── D3, D7, D8: the run ─────────────────────────────────────────────────────


class _DocProvider(_UiProvider):
    """Records each upload's type and each document it sends."""

    def __init__(self, world: _World, *, fail_upload: bool = False) -> None:  # noqa: F811
        super().__init__(world)
        self.fail_upload = fail_upload
        self.documents: list[tuple[str, str, str | None]] = []
        self.pdfs: list[bytes] = []

    async def upload_media(self, data: bytes, mime: str, name: str) -> str:
        if self.fail_upload:
            req = httpx.Request("POST", "https://graph.facebook.com")
            raise httpx.HTTPStatusError("bad", request=req,
                                        response=httpx.Response(400, request=req))
        if mime == "application/pdf":
            self.pdfs.append(data)
        return await super().upload_media(data, mime, name)

    async def send_document(self, to: str, media_id: str, *, filename: str,
                            caption: str | None = None,
                            reply_to_wa_message_id: str | None = None) -> str:
        self.documents.append((media_id, filename, caption))
        return self._sent(("document", filename, caption), reply_to_wa_message_id)


class _DocAgent:
    """``run_agent`` that asks for a document, as the model would."""

    def __init__(self, spec: dict[str, Any], reply: str = "Here is the Q4 plan.") -> None:
        self.spec = spec
        self.reply = reply
        self.outs: list[dict[str, Any]] = []
        self.seen: list[dict[str, Any]] = []

    async def __call__(self, agent: str, payload: dict[str, Any], **kw: Any) -> Any:
        self.seen.append(payload)
        self.outs.append(await wui.whatsapp_ui("document", self.spec))
        return {"result": self.reply}


def _switches(monkeypatch: pytest.MonkeyPatch, *, documents_on: bool) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_assistant_native_ui", True, raising=False)
    monkeypatch.setattr(s, "whatsapp_documents", documents_on, raising=False)


def _provider(monkeypatch: pytest.MonkeyPatch, world: _World, **kw: Any) -> _DocProvider:  # noqa: F811
    from whatsapp_ingestion.providers import factory

    prov = _DocProvider(world, **kw)
    monkeypatch.setattr(factory, "build_provider", lambda name, creds: prov)
    return prov


async def test_the_member_gets_the_text_then_one_pdf_file_that_the_thread_names(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    _switches(monkeypatch, documents_on=True)
    prov = _provider(monkeypatch, world)
    world.agent = _DocAgent(DECK)
    await _post_and_run(_message("Make me a deck of the Q4 plan"))

    (out,) = world.agent.outs
    assert out["ok"] is True and out["file"] == "Acme Q4 plan.pdf"
    assert bot_run.DOCUMENTS_RULE in world.agent.seen[0]["system_context"]
    assert [s for _to, s in world.sent] == [
        "Here is the Q4 plan.", ("document", "Acme Q4 plan.pdf", "The Q4 plan.")]
    (pdf,) = prov.pdfs
    assert pymupdf.open(stream=pdf, filetype="pdf").page_count == 5
    (thread,) = world.store.threads.values()
    record = thread[-1]["content"]
    assert "[Document: Acme Q4 plan.pdf · deck, 5 slides]" in record
    assert "Pipeline · Revenue · Top deals" in record


async def test_with_the_switch_off_the_kind_refuses_and_the_rule_offers_nothing(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    _switches(monkeypatch, documents_on=False)
    prov = _provider(monkeypatch, world)
    world.agent = _DocAgent(DECK)
    await _post_and_run(_message("Make me a deck"))
    (out,) = world.agent.outs
    assert out == {"ok": False, "error": "documents are off in this chat. Answer in text"}
    assert bot_run.DOCUMENTS_RULE not in world.agent.seen[0]["system_context"]
    assert world.agent.seen[0]["system_context"] == bot_run.SCOPE_RULE_NATIVE
    assert prov.documents == [] and prov.pdfs == []


async def test_a_failed_upload_sends_no_document_and_the_text_once(
    world: _World, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    _switches(monkeypatch, documents_on=True)
    prov = _provider(monkeypatch, world, fail_upload=True)
    world.agent = _DocAgent(DECK)
    await _post_and_run(_message("Make me a deck"))
    assert prov.documents == []
    assert [s for _to, s in world.sent] == ["Here is the Q4 plan."]


# ── Review round 1 ──────────────────────────────────────────────────────────


def test_the_pdf_ships_its_own_geist_so_ci_lays_out_what_the_box_does() -> None:
    """CI has no ``node_modules``, so a font read from there was a fallback in
    CI and Geist on the box. The fonts now ship with the gateway."""
    for name, _weight in pdf_render._GEIST_FACES:
        assert (pdf_render._GEIST / name).is_file(), name
    assert (pdf_render._GEIST / "OFL.txt").is_file()


def test_a_word_with_tt_ff_or_fi_is_copied_and_searched_as_written() -> None:
    """P1: Geist's ligatures wrote "seǪings" for "settings" in the text layer."""
    words = "http settings Attention office staff fifty flow"
    doc = _pdf({"format": "deck", "title": "Office settings", "slides": [
        {"type": "content", "title": "Office settings", "text": words}]})
    assert words in doc[0].get_text()
    assert doc[0].search_for("settings") and doc[0].search_for("office")


def test_a_slide_with_more_text_than_it_holds_is_refused_by_number() -> None:
    """P1: the body was not drawn, and the tool still said ok."""
    spec = {"format": "deck", "title": "T", "slides": [
        {"type": "cover", "title": "T"},
        {"type": "content", "title": "Where we stand", "text": "Revenue grew a lot. " * 57,
         "bullets": ["A long finding about one account and its open deals. " * 4] * 8,
         "visual": STATS}]}
    layout = documents.prepare(spec)
    with pytest.raises(PdfRenderError, match="Slide 2 has more text than one slide holds"):
        document_to_pdf(json.dumps(layout))


def test_a_full_slide_that_still_fits_keeps_every_bullet() -> None:
    """The other side of the refusal: text that fits is drawn, never dropped."""
    doc = _pdf({"format": "deck", "title": "T", "slides": [
        {"type": "content", "title": "Where we stand", "text": "Revenue grew. " * 40,
         "bullets": [f"Finding {i} about one account and its deals." for i in range(8)],
         "visual": STATS}]})
    text = doc[0].get_text()
    assert "Revenue grew." in text
    assert all(f"Finding {i}" in text for i in range(8))


@pytest.mark.parametrize("cell", ["Product URL (draft)", "curl (v8)", "Add href to links",
                                  "<!DOCTYPE x>"])
def test_a_word_in_a_visual_is_never_read_as_a_resource(cell: str) -> None:
    """P2: the SVG check read the table's own text."""
    doc = _pdf({"format": "report", "title": "T", "sections": [
        {"heading": "H", "visual": {**TABLE, "rows": [[cell, "Demo", "1"]]}}]})
    assert cell in _all_text(doc)


def test_a_second_document_for_the_same_member_is_refused_while_one_runs() -> None:
    pdf_render._members_rendering.add("busy@x.io")
    try:
        with pytest.raises(PdfRenderError) as err:
            asyncio.run(render_pdf("document", json.dumps(documents.prepare(DECK)),
                                   member="busy@x.io", org="org-doc"))
    finally:
        pdf_render._members_rendering.discard("busy@x.io")
    assert err.value.status == 429
