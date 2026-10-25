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

**The order of the steps** is the control (fix round 1, P1). The child runs
as the gateway's user, with the gateway's file system and network. So a bug
in a native parser must find nothing to read and nowhere to send it:

1. ``_limit_self``: the memory cap, the CPU cap and a low priority.
2. ``_check_landlock``: on Linux, a kernel with Landlock below the ABI that
   the spec asks for (4: the file system, and TCP bind and connect) refuses
   with ``unavailable``. It fails closed.
3. ``_preload``: every native library loads, all three models load, and one
   small OCR run warms the engine. After this step no library needs to open
   a new file or start a thread.
4. ``_confine``: Landlock lets the child READ only the Python dirs and the
   shared library dirs, and write nowhere. With ABI 4 or later it refuses
   each TCP bind and connect. Then ``RLIMIT_NPROC`` is 0, so no fork and no
   new thread, and ``RLIMIT_FSIZE`` is 0, so no file grows.
5. Only then does the child read the untrusted bytes from stdin.

Landlock is a Linux feature. On Windows, the dev box only, steps 2 and 4 do
nothing, and the parent logs ``attachment_ocr.unconfined``.

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
import ctypes
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

# ── Landlock (Linux), by syscall through ctypes ──────────────────────────────
# The numbers are the same on every architecture. See
# https://docs.kernel.org/userspace-api/landlock.html.
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_NET_BIND_TCP = 1 << 0
_NET_CONNECT_TCP = 1 << 1
_SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
_SCOPE_SIGNAL = 1 << 1
#: The shared library dirs that a later ``dlopen`` can need.
_LIBRARY_DIRS = ("/lib", "/lib64", "/usr/lib", "/usr/lib64")
#: Mapped paths that are not library files.
_NOT_LIBRARIES = ("/dev/", "/proc/", "/sys/", "/memfd:")


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


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
    if spec["kind"] not in ("pdf", "image") or spec["landlock_abi"] < 1:
        raise _Refused("failed")
    return spec


def _libc() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """The Landlock ABI of this kernel, or 0 when it has none."""
    if sys.platform != "linux":
        return 0
    try:
        got = _libc().syscall(
            ctypes.c_long(_SYS_CREATE_RULESET), None, ctypes.c_size_t(0),
            ctypes.c_uint32(_CREATE_RULESET_VERSION),
        )
    except Exception:  # no libc symbol is no Landlock
        return 0
    return max(int(got), 0)


def _limit_self(spec: dict[str, Any]) -> None:
    """The memory cap, the CPU cap and a low priority, on Linux.

    It runs before any native library loads. Measured on Linux with one
    thread for each library: a dense A4 page peaks at 874 MB of address
    space and 506 MB resident (2026-10-25). Windows is the dev box only.
    """
    sys.dont_write_bytecode = True
    if os.name != "posix":
        return
    import resource

    mem = spec["memory_bytes"]
    cpu = spec["cpu_seconds"]
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    with contextlib.suppress(OSError):
        os.nice(10)


def _check_landlock(spec: dict[str, Any]) -> None:
    """Refuse with ``unavailable`` on a Linux kernel below the asked ABI."""
    if sys.platform == "linux" and landlock_abi() < spec["landlock_abi"]:
        raise _Refused("unavailable")


def _beneath(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _read_dirs() -> list[str]:
    """The dirs that the confined child may read, and no other.

    The Python dirs on ``sys.path`` that lie in the venv or in the Python
    install, the dir of each mapped shared library, and the system library
    dirs. A dir that holds the cwd (the app dir, with its ``.env``) or this
    file never joins, so no rule can reach the app dir. Each dir is a real
    path, and a dir inside another one is dropped.
    """
    prefixes = {os.path.realpath(p) for p in (
        sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
    ) if p}
    found: set[str] = set()
    for entry in sys.path:
        real = os.path.realpath(entry) if entry else ""
        if real and os.path.isdir(real) and any(_beneath(real, p) for p in prefixes):
            found.add(real)
    with open("/proc/self/maps", encoding="utf-8", errors="replace") as maps:
        for line in maps:
            parts = line.split(None, 5)
            path = parts[5].strip() if len(parts) == 6 else ""
            if not path.startswith("/") or path.startswith(_NOT_LIBRARIES):
                continue
            if os.path.isfile(path):
                found.add(os.path.realpath(os.path.dirname(path)))
    found.update(os.path.realpath(d) for d in _LIBRARY_DIRS if os.path.isdir(d))
    guards = (os.path.realpath(os.getcwd()), os.path.dirname(os.path.realpath(__file__)))
    allowed = sorted(d for d in found if not any(_beneath(g, d) for g in guards))
    return [d for d in allowed if not any(o != d and _beneath(d, o) for o in allowed)]


def _handled_fs(abi: int) -> int:
    """Each file system right that this ABI knows, so each one is denied
    unless a rule grants it."""
    bits = (1 << 13) - 1  # ABI 1: EXECUTE up to MAKE_SYM
    if abi >= 2:
        bits |= 1 << 13  # REFER
    if abi >= 3:
        bits |= 1 << 14  # TRUNCATE
    if abi >= 5:
        bits |= 1 << 15  # IOCTL_DEV
    return bits


def _confine(spec: dict[str, Any]) -> None:
    """Landlock and the process caps, after the preload, before the bytes.

    It raises ``_Refused("unavailable")`` when a step fails, so the child
    never parses unconfined on Linux. It does nothing on other systems.
    """
    if sys.platform != "linux":
        return
    import resource
    import signal

    abi = landlock_abi()
    if abi < spec["landlock_abi"]:
        raise _Refused("unavailable")
    libc = _libc()
    attr = _RulesetAttr(
        handled_access_fs=_handled_fs(abi),
        handled_access_net=(_NET_BIND_TCP | _NET_CONNECT_TCP) if abi >= 4 else 0,
        scoped=(_SCOPE_ABSTRACT_UNIX_SOCKET | _SCOPE_SIGNAL) if abi >= 6 else 0,
    )
    ruleset = libc.syscall(
        ctypes.c_long(_SYS_CREATE_RULESET), ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0),
    )
    if ruleset < 0:
        raise _Refused("unavailable")
    try:
        for path in _read_dirs():
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = _PathBeneath(allowed_access=_FS_READ_FILE | _FS_READ_DIR, parent_fd=fd)
                if libc.syscall(
                    ctypes.c_long(_SYS_ADD_RULE), ctypes.c_int(ruleset),
                    ctypes.c_int(_RULE_PATH_BENEATH), ctypes.byref(rule), ctypes.c_uint32(0),
                ) != 0:
                    raise _Refused("unavailable")
            finally:
                os.close(fd)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise _Refused("unavailable")
        if libc.syscall(
            ctypes.c_long(_SYS_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint32(0),
        ) != 0:
            raise _Refused("unavailable")
    finally:
        os.close(ruleset)
    # A write past the cap fails with EFBIG and does not kill the child.
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


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
    threads at its first run. Both must happen here, before ``_confine``
    stops new files and new threads.
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


def _read(data: bytes, spec: dict[str, Any], started: float, engine: Any) -> dict[str, Any]:
    if spec["kind"] == "pdf":
        loaders, total = _pdf_pages(data, spec)
    else:
        loaders, total = _image_pages(data, spec)
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
        _check_landlock(spec)
        engine = _preload(spec["kind"])
        _confine(spec)
        # The untrusted bytes arrive only now, after the confinement.
        data = sys.stdin.buffer.read(spec["max_bytes"] + 1)
        if len(data) > spec["max_bytes"]:
            raise _Refused("size")
        answer = _read(data, spec, started, engine)
    except _Refused as exc:
        answer = {"status": "refused", "reason": exc.reason}
    except Exception:  # no traceback reaches the parent, only a code
        answer = {"status": "refused", "reason": "failed"}
    sys.stdout.write(json.dumps(answer))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
