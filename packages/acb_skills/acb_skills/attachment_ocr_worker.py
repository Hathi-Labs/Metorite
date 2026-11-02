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
stdin. The child writes one JSON object on each line of stdout, flushes each
line, and exits 0 (fix round 2):

* ``{"total": N}``, once, when the file opened.
* ``{"page": "..."}`` for each page that it read, or ``{"page": null}`` for
  a page that did not render. Each page line goes out when the page ends, so
  the parent keeps the pages that arrived before a kill.
* ``{"end": null}``, or ``{"end": "pages"}`` (the page cap) or
  ``{"end": "time"}`` (the soft deadline) when it read fewer pages than the
  file holds.
* ``{"refused": "<code>"}`` in place of the rest. The parent owns every
  sentence for the member, so the child sends a code only.

**The order of the steps** is the control (fix rounds 1 to 3). The child runs
as the gateway's user, with the gateway's file system and network. So a bug
in a native parser must find nothing to read and nowhere to send it:

1. ``_limit_self``: the memory cap, the CPU cap and a low priority.
2. ``_check_landlock``: on Linux, a kernel with Landlock below the ABI that
   the spec asks for (4: the file system, and TCP bind and connect) refuses
   with ``unavailable``. It fails closed.
3. ``_confine``, while the child has ONE thread (fix round 3). Landlock and a
   seccomp filter bind the calling thread and each thread that it starts
   later, and not a thread that runs already. So the child counts its
   threads first, and answers ``unavailable`` for any count but 1. Landlock
   lets the child READ only the Python dirs and the shared library dirs, and
   write nowhere. With ABI 4 or later it refuses each TCP bind and connect.
   The seccomp filter is :func:`seccomp_program`, a DENY list.
4. ``_preload``: every native library loads, all three models load, and one
   small OCR run warms the engine. Loading onnxruntime starts a helper
   thread, which now starts confined.
5. ``_close_limits``: ``RLIMIT_NPROC`` is 0, so no fork and no new thread,
   and ``RLIMIT_FSIZE`` is 0, so no file grows. The filter lets
   ``prlimit64`` act on the child itself only, so these two calls pass.
6. Only then does the child read the untrusted bytes from stdin.

The probe of ``TestTheConfinement`` proves each of these on a confined child,
on Linux 6.8 with Landlock ABI 4: it cannot read a file of the app dir or
its parent's environ, write a file, fork, open a TCP, UDP or Unix socket, or
set up io_uring. It cannot send a signal to its parent, set the parent's
limits or priority, or make the kernel send the parent SIGIO through
``F_SETOWN`` or ``FIOSETOWN``. A glibc ``abort()`` still ends the child,
because abort falls back to a fault. The filter refuses each call that it
names. It does not refuse a call that it does not name, so it is no proof
against a call that nobody listed (spec §4.1, the residual risk).

Landlock and seccomp are Linux features. On Windows, the dev box only, steps
2, 3 and 5 do nothing, and the parent logs ``attachment_ocr.unconfined``. On
a Linux box that is not x86-64, the filter cannot be built, so the child
answers ``unavailable``.

**The limits.** Every library runs on one thread: the parent sets the thread
env names, the child calls ``cv2.setNumThreads(1)``, and onnxruntime gets one
intra-op and one inter-op thread. The parent kills the child's process group
at its hard timeout. The child stops at the soft deadline between two pages,
so a slow scan gives the pages that it read.

**No download.** rapidocr can fetch a model over the network. The child names
each bundled model file by path, and it replaces rapidocr's downloader with a
refusal, so a missing file fails the read and never fetches.
"""
from __future__ import annotations

import contextlib
import importlib.util
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
    "landlock_abi": int,
}

def _load_confine() -> Any:
    """Load ``child_confine.py`` from this dir, by path (WS-WEB WEB-2b).

    ``-I`` puts no dir on ``sys.path``, and ``import acb_skills`` would run the
    package ``__init__``. So the shared module loads by its file, and it writes
    no bytecode into the package dir."""
    path = Path(__file__).resolve().with_name("child_confine.py")
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location("child_confine", path)
        if spec is None or spec.loader is None:
            raise ImportError("child_confine.py does not load")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = keep
    return module


_cc = _load_confine()
# The confinement is the shared module's (WEB-2b). These names stay, for the
# fence of this child and for the order of ``main``.
_Refused = _cc.Refused
landlock_abi = _cc.landlock_abi
thread_count = _cc.thread_count
seccomp_program = _cc.seccomp_program
SECCOMP_DENIED = _cc.SECCOMP_DENIED
FCNTL_DENIED = _cc.FCNTL_DENIED
IOCTL_DENIED = _cc.IOCTL_DENIED
_SECCOMP_RET_ERRNO = _cc._SECCOMP_RET_ERRNO
_SECCOMP_RET_ALLOW = _cc._SECCOMP_RET_ALLOW
_SECCOMP_RET_KILL_PROCESS = _cc._SECCOMP_RET_KILL_PROCESS
_EPERM = _cc._EPERM
_AUDIT_ARCH_X86_64 = _cc._AUDIT_ARCH_X86_64
_X32_SYSCALL_BIT = _cc._X32_SYSCALL_BIT
_close_limits = _cc.close_limits


def _confine(spec: dict[str, Any]) -> None:
    """Landlock and the seccomp filter of ``child_confine``, BEFORE the
    preload and the bytes. The child must have ONE thread here (round 3)."""
    _cc.confine(spec["landlock_abi"])


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
    if spec["kind"] not in ("pdf", "image") or spec["landlock_abi"] < 1:
        raise _Refused("failed")
    return spec

def _limit_self(spec: dict[str, Any]) -> None:
    """The memory cap, the CPU cap, no core file and a low priority, on Linux.

    It runs before any native library loads. Measured on Linux with one
    thread for each library: a dense A4 page peaks at 874 MB of address
    space and 506 MB resident (2026-10-25). Windows is the dev box only.
    ``RLIMIT_CORE`` is 0 for soft and hard (fix round 4): a crash writes no
    core file with the member's file in it. The filter lets ``prlimit64`` act
    on the child, so only the hard limit of 0 stops a later raise.
    """
    sys.dont_write_bytecode = True
    if os.name != "posix":
        return
    import resource

    mem = spec["memory_bytes"]
    cpu = spec["cpu_seconds"]
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with contextlib.suppress(OSError):
        os.nice(10)


def _check_landlock(spec: dict[str, Any]) -> None:
    """Refuse with ``unavailable`` on a Linux kernel below the asked ABI."""
    if sys.platform == "linux" and landlock_abi() < spec["landlock_abi"]:
        raise _Refused("unavailable")




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


def _preload(kind: str) -> Any:
    """Load every library and model that the read uses, and warm the engine.

    rapidocr loads each model at its first use, and onnxruntime sets up its
    threads at its first run. Both happen here, inside the confinement of
    ``_confine`` and before ``_close_limits`` stops new threads. Landlock
    lets each library and model file be read here, because each one lies in
    a Python dir or a library dir.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageOps, TiffImagePlugin  # noqa: F401

    Image.preinit()
    if kind == "pdf":
        import pypdfium2  # noqa: F401
    engine = _engine()
    for name in ("_load_det_model", "_load_cls_model", "_load_rec_model"):
        load = getattr(engine, name, None)
        if callable(load):
            load()
    warm = Image.new("L", (240, 64), 255)
    ImageDraw.Draw(warm).text((10, 10), "OCR 42", fill=0, font=ImageFont.load_default(size=36))
    engine(warm.convert("RGB"))
    return engine


def _page_text(engine: Any, image: Any, spec: dict[str, Any]) -> str:
    """The lines that OCR read on one page, top to bottom."""
    result = engine(image)
    lines = getattr(result, "txts", None) or ()
    text = "\n".join(t.strip() for t in lines if isinstance(t, str) and t.strip())
    return text[: spec["max_page_chars"]]


def _emit(item: dict[str, Any]) -> None:
    """Write one line of the answer, and flush it at once."""
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()


def _read(data: bytes, spec: dict[str, Any], started: float, engine: Any) -> None:
    """Read the file, and write the total, each page and the end."""
    if spec["kind"] == "pdf":
        loaders, total = _pdf_pages(data, spec)
    else:
        loaders, total = _image_pages(data, spec)
    _emit({"total": total})
    read = 0
    for load in loaders:
        if time.monotonic() - started > spec["soft_seconds"]:
            _emit({"end": "time"})
            return
        try:
            image = load()
            text: str | None = "" if image is None else _page_text(engine, image, spec)
        except MemoryError:
            raise
        except Exception:  # one bad page is not a bad file
            text = None
        _emit({"page": text})
        read += 1
    _emit({"end": "pages" if read < total else None})


def main(argv: list[str]) -> int:
    started = time.monotonic()
    try:
        spec = _spec(argv[1] if len(argv) > 1 else "")
        _limit_self(spec)
        _check_landlock(spec)
        # One thread here, so the confinement binds each thread (round 3).
        _confine(spec)
        engine = _preload(spec["kind"])
        _close_limits()
        # The untrusted bytes arrive only now, after the confinement.
        data = sys.stdin.buffer.read(spec["max_bytes"] + 1)
        if len(data) > spec["max_bytes"]:
            raise _Refused("size")
        _read(data, spec, started, engine)
    except _Refused as exc:
        _emit({"refused": exc.reason})
    except Exception:  # no traceback reaches the parent, only a code
        _emit({"refused": "failed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
