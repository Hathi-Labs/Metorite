"""STU-D13a: the fixed ``record`` layout of ``pdf_render.document_to_pdf``.

Spec: ``project-docs/specs/ai_studio.md`` D13a. Hermetic: no database.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import threading
import time
from typing import Any

import pymupdf
import pytest
from gateway.pdf_render import (
    MAX_DOCUMENT_PDF_BYTES,
    PdfRenderError,
    document_to_pdf,
    record_source,
    render_pdf,
)

SECRET = "sk-live-SECRETVALUE-9f3a"


def _png(size: int = 32) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size), False)
    pix.set_rect(pix.irect, (20, 90, 200))
    return pix.tobytes("png")


def _jpeg(size: int = 32) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size), False)
    pix.set_rect(pix.irect, (200, 90, 20))
    return pix.tobytes("jpeg")


def _record(**over: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "title": "Invoice INV-0042",
        "fields": [{"label": "Customer", "value": "Acme Works"},
                   {"label": "Due", "value": "2026-12-01"}],
        "columns": ["Item", "Qty", "Amount"],
        "rows": [["Bracket", "4", "120.00"], ["Plate", "2", "80.00"],
                 ["Bolt", "100", "15.50"]],
        "logo": None,
    }
    rec.update(over)
    return rec


def _pdf(**over: Any) -> bytes:
    return document_to_pdf(record_source(_record(**over)))


def _pages(pdf: bytes) -> list[str]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [p.get_text() for p in doc]


def _refused(status: int, **over: Any) -> PdfRenderError:
    with pytest.raises(PdfRenderError) as err:
        _pdf(**over)
    assert err.value.status == status
    assert str(err.value).endswith(".")
    return err.value


@pytest.mark.parametrize("make", [_png, _jpeg])
def test_a_record_with_a_logo_and_three_lines_is_one_pdf(make: Any) -> None:
    pdf = _pdf(logo=make())
    assert pdf.startswith(b"%PDF")
    assert len(pdf) < MAX_DOCUMENT_PDF_BYTES
    pages = _pages(pdf)
    assert len(pages) == 1
    text = pages[0]
    for word in ("Invoice INV-0042", "Acme Works", "2026-12-01", "Bracket", "Plate",
                 "Bolt", "120.00", "15.50", "Customer", "Item", "Amount"):
        assert word in text
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert len(doc[0].get_images()) == 1


def test_values_are_literal_text_and_no_request_is_made() -> None:
    hits: list[int] = []
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(0.2)
    port = server.getsockname()[1]
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                continue
            hits.append(1)
            conn.close()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        values = [f"<img src=http://127.0.0.1:{port}/x>", "<script>alert(1)</script>",
                  "**bold**", "{{x}}"]
        pdf = _pdf(fields=[{"label": f"L{i}", "value": v} for i, v in enumerate(values)],
                   rows=[[v, "1", "2"] for v in values])
        time.sleep(0.5)
    finally:
        stop.set()
        thread.join(timeout=2)
        server.close()
    text = "".join(_pages(pdf))
    for value in values:
        assert value.replace(" ", "") in text.replace("\n", "").replace(" ", "")
    assert hits == []


def test_hidden_characters_are_stripped() -> None:
    value = "ab‮cd​ef\x00gh﻿ij"
    text = "".join(_pages(_pdf(fields=[{"label": "L", "value": value}])))
    assert "abcdefghij" in text
    for ch in "‮​\x00﻿":
        assert ch not in text


@pytest.mark.parametrize("data", [
    b"GIF89a" + b"\x01\x00\x01\x00" + b"\x00" * 40,
    b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 40,
    b'<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9"></svg>',
    _png()[:60],
    _png()[:33] + b"\x00garbage" * 40,
    b"not an image at all",
])
def test_a_logo_that_is_not_a_good_png_or_jpeg_is_refused(data: bytes) -> None:
    _refused(422, logo=data)


def test_a_logo_over_the_byte_cap_is_refused() -> None:
    _refused(413, logo=_png() + b"\x00" * (128 * 1024))


def test_a_logo_over_the_pixel_cap_is_refused() -> None:
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 2100, 8), False)
    big = pix.tobytes("png")
    assert len(big) < 128 * 1024
    _refused(422, logo=big)


def test_the_row_column_and_field_caps_answer_413() -> None:
    _refused(413, rows=[["a", "b", "c"]] * 501)
    _refused(413, columns=[f"c{i}" for i in range(13)], rows=[])
    _refused(413, fields=[{"label": "a", "value": "b"}] * 61)
    assert _pdf(fields=[{"label": "a", "value": "b"}] * 60)


@pytest.mark.parametrize("over", [
    {"extra": 1}, {"title": 5}, {"fields": "x"}, {"fields": [{"label": "a"}]},
    {"fields": [{"label": "a", "value": 1}]}, {"rows": [["a"]]}, {"columns": [1], "rows": []},
    {"rows": ["abc"]}, {"logo": 7},
])
def test_unknown_keys_and_wrong_types_are_refused_with_a_sentence(over: dict[str, Any]) -> None:
    if over == {"logo": 7}:
        with pytest.raises(PdfRenderError):
            record_source(_record(**over))
        return
    src = json.loads(record_source(_record()))
    src.update(over)
    with pytest.raises(PdfRenderError) as err:
        document_to_pdf(json.dumps(src))
    assert err.value.status in (413, 422)
    assert str(err.value).endswith(".")
    assert "Traceback" not in str(err.value)


def test_a_logo_that_is_not_base64_text_is_refused() -> None:
    src = json.loads(record_source(_record()))
    src["logo"] = "!!!not base64!!!"
    with pytest.raises(PdfRenderError):
        document_to_pdf(json.dumps(src))


def test_a_long_cell_is_cut_at_the_cap() -> None:
    text = "".join(_pages(_pdf(fields=[{"label": "L", "value": "x" * 5000}], rows=[])))
    assert text.count("x") == 2000


def test_a_500_row_table_has_the_header_on_each_page_within_the_time() -> None:
    rows = [[f"item-{i}", str(i), f"{i}.00"] for i in range(500)]
    start = time.monotonic()
    pdf = _pdf(rows=rows, columns=["Item", "Qty", "Amount"])
    assert time.monotonic() - start < 13.0
    pages = _pages(pdf)
    assert len(pages) > 1
    for page in pages:
        assert "Amount" in page
    assert "item-499" in pages[-1]
    assert len(pdf) < MAX_DOCUMENT_PDF_BYTES


def test_a_record_runs_through_render_pdf_in_the_child_process() -> None:
    pdf = asyncio.run(render_pdf("document", record_source(_record(logo=_png())),
                                 member="a@fracktal.in", org="org-test"))
    assert "Acme Works" in "".join(_pages(pdf))


def test_nothing_is_logged_at_any_level(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    _pdf(logo=_png(), fields=[{"label": "Key", "value": SECRET}],
         rows=[[SECRET, "1", "2"]])
    with pytest.raises(PdfRenderError):
        _pdf(fields=[{"label": "Key", "value": SECRET}], rows=[[SECRET]])
    assert SECRET not in caplog.text
    assert all(SECRET not in r.getMessage() for r in caplog.records)


def test_the_logo_travels_as_base64_in_the_source() -> None:
    src = json.loads(record_source(_record(logo=_png())))
    assert base64.b64decode(src["logo"]) == _png()
