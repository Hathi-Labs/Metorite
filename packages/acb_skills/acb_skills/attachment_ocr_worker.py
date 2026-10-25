"""The OCR child process of :mod:`acb_skills.attachment_ocr` (WS-67 OA-3).

The parent starts this file as a SCRIPT, never with ``-m``::

    python -I <path>/attachment_ocr_worker.py '<spec as JSON>'

``-m acb_skills...`` runs the package ``__init__``, which imports the whole
tool set of the agents. This child imports only the standard library and the
OCR stack: Pillow, pypdfium2, OpenCV and rapidocr with onnxruntime. ``-I``
ignores ``PYTHONPATH`` and the user site, and it puts no script dir on
``sys.path``. So no agent install and no planted file can shadow a module
here. This file imports nothing from ``acb_skills``.

**The contract.** The spec comes in ``argv[1]``. The file bytes come on
stdin. The child writes ONE JSON object to stdout and exits 0:

* ``{"status": "ok", "pages": [...], "total": N, "stop": null}``. Each item
  of ``pages`` is the text of one page, or ``null`` for a page that did not
  render. ``stop`` is ``"pages"`` (the page cap) or ``"time"`` (the soft
  deadline) when the child read fewer pages than the file holds.
* ``{"status": "refused", "reason": "<code>"}``. The parent owns every
  sentence for the member, so the child sends a code only.

**The limits.** On Linux the child sets ``RLIMIT_AS`` and ``RLIMIT_CPU`` on
itself and lowers its priority, before it imports a native library. Every
library runs on one thread: the parent sets the thread env names, the child
calls ``cv2.setNumThreads(1)``, and onnxruntime gets one intra-op and one
inter-op thread. The parent kills the child at its hard timeout. The child
stops at the soft deadline between two pages, so a slow scan gives the pages
that it read.

**No download.** rapidocr can fetch a model over the network. The child names
each bundled model file by path, and it replaces rapidocr's downloader with a
refusal, so a missing file fails the read and never fetches.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

#: The bundled model files of rapidocr 3.10.0 (``rapidocr/models``). A
#: version that renames them fails the read, and the test of the files fails.
DET_MODEL = "PP-OCRv6_det_small.onnx"
REC_MODEL = "PP-OCRv6_rec_small.onnx"
CLS_MODEL = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"

#: The longest side that the text detector works on. rapidocr's default
#: ("min" 736) scales a page UP and took 9 s for one page. "max" 960 took
#: 1.6 s and found the same lines (measured 2026-10-25).
DET_LIMIT_SIDE = 960

#: The fields of a spec, each with its type. The parent sends every field.
_SPEC_FIELDS: dict[str, type] = {
    "kind": str,
    "max_bytes": int,
    "max_pages": int,
    "dpi": int,
    "max_page_pixels": int,
    "max_page_side": int,
    "max_image_pixels": int,
    "max_page_chars": int,
    "soft_seconds": float,
    "memory_bytes": int,
    "cpu_seconds": int,
}


class _Refused(Exception):
    """A file that the child does not read. ``reason`` is a code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _spec(raw: str) -> dict[str, Any]:
    spec = json.loads(raw)
    if not isinstance(spec, dict):
        raise _Refused("failed")
    for name, kind in _SPEC_FIELDS.items():
        value = spec.get(name)
        if kind is float and isinstance(value, int):
            value = float(value)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise _Refused("failed")
        spec[name] = value
    if spec["kind"] not in ("pdf", "image"):
        raise _Refused("failed")
    return spec


def _limit_self(spec: dict[str, Any]) -> None:
    """The memory cap, the CPU cap and a low priority, on Linux.

    It runs before any native library loads. Measured on Linux with one
    thread for each library: a dense A4 page peaks at 874 MB of address
    space and 506 MB resident (2026-10-25). Windows is the dev box only.
    """
    if os.name != "posix":
        return
    import resource

    mem = spec["memory_bytes"]
    cpu = spec["cpu_seconds"]
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    with contextlib.suppress(OSError):
        os.nice(10)


def _scale(width_pt: float, height_pt: float, spec: dict[str, Any]) -> float | None:
    """The render scale of a page, or ``None`` for a page with no area.

    The page renders at ``dpi``. A large page renders smaller, so it holds at
    most ``max_page_pixels`` pixels and no side is longer than
    ``max_page_side``. So a page size in the file can never ask for a huge
    bitmap.
    """
    if not (width_pt > 0 and height_pt > 0) or not math.isfinite(width_pt * height_pt):
        return None
    scale = spec["dpi"] / 72.0
    pixels = width_pt * height_pt * scale * scale
    if pixels > spec["max_page_pixels"]:
        scale *= math.sqrt(spec["max_page_pixels"] / pixels)
    side = max(width_pt, height_pt) * scale
    if side > spec["max_page_side"]:
        scale *= spec["max_page_side"] / side
    if min(width_pt, height_pt) * scale < 1:
        return None
    return scale


def _pdf_pages(data: bytes, spec: dict[str, Any]) -> tuple[Iterator[Callable[[], Any]], int]:
    """A loader for each page up to the cap, and the count of pages."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        # PDFium's error code 4 is a password.
        raise _Refused("password" if getattr(exc, "err_code", None) == 4 else "broken") from None
    total = len(doc)

    def _render(index: int) -> Any:
        page = doc[index]
        try:
            width, height = page.get_size()
            scale = _scale(float(width), float(height), spec)
            if scale is None:
                return None
            bitmap = page.render(scale=scale, may_draw_forms=False)
            return bitmap.to_pil().convert("RGB")
        finally:
            page.close()

    loaders = (lambda i=i: _render(i) for i in range(min(total, spec["max_pages"])))
    return loaders, total


def _image_pages(data: bytes, spec: dict[str, Any]) -> tuple[Iterator[Callable[[], Any]], int]:
    """A loader for the one page of an image, and the count, 1.

    Pillow opens only PNG and JPEG. The size in the header is checked before
    one pixel is decoded, so a decompression bomb is refused. A JPEG decodes
    at a smaller scale when it is large (``draft``).
    """
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = spec["max_image_pixels"]
    try:
        image = Image.open(io.BytesIO(data), formats=["PNG", "JPEG"])
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > spec["max_image_pixels"]:
            raise _Refused("pixels")
        target = _fit(width, height, spec)
        image.draft("RGB", target)
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail(_fit(image.width, image.height, spec))
    except _Refused:
        raise
    except Image.DecompressionBombError:
        raise _Refused("pixels") from None
    except Exception:  # every decoder failure is one clean refusal
        raise _Refused("broken") from None
    return iter([lambda: image]), 1


def _fit(width: int, height: int, spec: dict[str, Any]) -> tuple[int, int]:
    """The largest size of this shape within the pixel cap and the side cap."""
    scale = min(1.0, math.sqrt(spec["max_page_pixels"] / (width * height)))
    scale = min(scale, spec["max_page_side"] / max(width, height))
    return max(1, int(width * scale)), max(1, int(height * scale))


def _block_downloads() -> None:
    """Replace rapidocr's downloader with a refusal, for this process."""
    from rapidocr.utils import download_file

    def _no_download(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the OCR child never downloads a file")

    download_file.DownloadFile.run = staticmethod(_no_download)  # type: ignore[method-assign]


def _engine() -> Any:
    """The OCR engine, on one thread, with the bundled models only."""
    import cv2

    cv2.setNumThreads(1)
    import rapidocr
    from rapidocr import RapidOCR

    _block_downloads()
    models = Path(rapidocr.__file__).resolve().parent / "models"
    return RapidOCR(params={
        "Global.log_level": "critical",
        "Det.model_path": str(models / DET_MODEL),
        "Rec.model_path": str(models / REC_MODEL),
        "Cls.model_path": str(models / CLS_MODEL),
        "Det.limit_type": "max",
        "Det.limit_side_len": DET_LIMIT_SIDE,
        "EngineConfig.onnxruntime.intra_op_num_threads": 1,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
    })


def _page_text(engine: Any, image: Any, spec: dict[str, Any]) -> str:
    """The lines that OCR read on one page, top to bottom."""
    result = engine(image)
    lines = getattr(result, "txts", None) or ()
    text = "\n".join(t.strip() for t in lines if isinstance(t, str) and t.strip())
    return text[: spec["max_page_chars"]]


def _read(data: bytes, spec: dict[str, Any], started: float) -> dict[str, Any]:
    if spec["kind"] == "pdf":
        loaders, total = _pdf_pages(data, spec)
    else:
        loaders, total = _image_pages(data, spec)
    # The file opened, so the engine loads now. A fault here is "failed".
    engine = _engine() if total else None
    pages: list[str | None] = []
    stop: str | None = None
    for load in loaders:
        if time.monotonic() - started > spec["soft_seconds"]:
            stop = "time"
            break
        try:
            image = load()
            pages.append("" if image is None else _page_text(engine, image, spec))
        except MemoryError:
            raise
        except Exception:  # one bad page is not a bad file
            pages.append(None)
    if stop is None and len(pages) < total:
        stop = "pages"
    return {"status": "ok", "pages": pages, "total": total, "stop": stop}


def main(argv: list[str]) -> int:
    started = time.monotonic()
    try:
        spec = _spec(argv[1] if len(argv) > 1 else "")
        _limit_self(spec)
        data = sys.stdin.buffer.read(spec["max_bytes"] + 1)
        if len(data) > spec["max_bytes"]:
            raise _Refused("size")
        answer = _read(data, spec, started)
    except _Refused as exc:
        answer = {"status": "refused", "reason": exc.reason}
    except Exception:  # no traceback reaches the parent, only a code
        answer = {"status": "refused", "reason": "failed"}
    sys.stdout.write(json.dumps(answer))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
