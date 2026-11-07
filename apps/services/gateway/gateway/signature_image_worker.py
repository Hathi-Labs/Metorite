"""The child process that decodes a signature image (WS-17 SG-1, D-EM-67).

The parent, :mod:`gateway.signature_image`, starts this file as a SCRIPT::

    python -I <path>/signature_image_worker.py '<spec as JSON>'

``-I`` ignores ``PYTHONPATH`` and the user site, and it puts no script dir on
``sys.path``, so no planted file can shadow a module here. This child imports
the standard library and ``pymupdf`` only, and nothing from ``gateway`` or
``acb_*``.

**Why a child.** The bytes come from a member, and a decoder is native code. A
2 MB JPEG of 12,000 pixels on a side decodes to 144 megapixels. In the request
process nothing could stop that. Here the parent kills the child at its time
bound, and the child caps its own memory.

**The contract.** The spec comes in ``argv[1]``. The image bytes come on stdin.
The child writes ONE JSON line on stdout and exits 0:

* ``{"ok": {"content_type", "width", "height", "dark_share", "clear_share",
  "data", "confined"}}``. ``data`` is the stored image as base64.
  ``confined`` is true only after ``confine`` and ``close_limits`` ran on
  Linux. On another system the answer carries ``unconfined_os`` in its place.
  The parent refuses an answer with neither (fix round 1, P2).
* ``{"refused": "<code>"}``. The parent owns every sentence for the member, so
  the child sends a code only: ``broken``, ``pixels``, ``too_big``, ``size``,
  ``unavailable`` or ``failed``.

**The order of the steps is the control**, as in ``attachment_ocr_worker``:

1. Load ``child_confine.py`` BY PATH (the spec names it), close each inherited
   descriptor from 3 up.
2. ``_limit_self``: the memory cap, the CPU cap and no core file.
3. ``confine``: Landlock and the seccomp filter of ``child_confine``, while the
   child has ONE thread. With them the child reads only the Python and library
   dirs, writes nowhere, and opens no socket.
4. ``_preload``: MuPDF loads and renders one tiny image.
5. ``close_limits``: no fork, no new thread, no file growth.
6. Only then does the child read the untrusted bytes from stdin.

Landlock and seccomp are Linux features. On Windows, the dev box only, steps 3
and 5 do nothing, and the parent logs ``signature_image.unconfined``.

**The image.** A logo keeps its aspect ratio. A photo is cropped to the centre
square, as ``gateway/avatar.py`` does. The long edge goes to at most 480 px, and
never up. The pixel size comes from the image header (``get_image_info``), and
the matrix comes from ``Rect.torect``, which maps the clip in POINTS onto the
exact pixel box. MuPDF opens a 1000x400 px image at 96 dpi as 750x300 points,
and ``Matrix(scale, scale)`` rounds outward, so neither shortcut is exact. The
render has ``alpha=True``. A logo is PNG. A photo is JPEG when no pixel is
transparent, else PNG. EXIF and trailing bytes do not survive a re-encode. An
output over the byte cap steps the edge down (480, then 360, then 240), then
refuses with ``too_big``.
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

#: The fields of a spec, each with its type. The parent sends every field.
_SPEC_FIELDS: dict[str, type] = {
    "kind": str,
    "max_bytes": int,
    "max_pixels": int,
    "max_side": int,
    "max_output_bytes": int,
    "jpeg_quality": int,
    "memory_bytes": int,
    "cpu_seconds": int,
    "landlock_abi": int,
    "confine_path": str,
}

#: The long edges that the child tries, largest first.
EDGES = (480, 360, 240)

#: A pixel darker than this luminance (0 to 255, Rec. 601 weights) is dark.
DARK_LUMA = 96

#: A pixel with less alpha than this is transparent.
CLEAR_ALPHA = 128

#: Words of a MuPDF warning that mean the image is damaged. MuPDF renders a
#: truncated PNG as a blank image and only warns ("missing pixel data in png
#: image; possibly truncated", measured 2026-11-07), so the child reads the
#: warnings after each render and refuses with ``broken``.
_DAMAGE_WORDS = ("truncated", "missing pixel data", "corrupt", "premature end",
                 "zlib error")


class _Refused(Exception):
    """An image that the child does not take. ``reason`` is a code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _load_confine(path: str) -> Any:
    """Load ``child_confine.py`` by its path, as the OCR child does.

    ``-I`` puts no dir on ``sys.path``, and ``import acb_skills`` would run the
    package ``__init__``. The parent names the file, and the load writes no
    bytecode next to it."""
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location("child_confine", Path(path))
        if spec is None or spec.loader is None:
            raise ImportError("child_confine.py does not load")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = keep
    return module


def _spec(raw: str) -> dict[str, Any]:
    spec = json.loads(raw)
    if not isinstance(spec, dict):
        raise _Refused("failed")
    for name, kind in _SPEC_FIELDS.items():
        value = spec.get(name)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise _Refused("failed")
    if spec["kind"] not in ("logo", "photo") or spec["landlock_abi"] < 1:
        raise _Refused("failed")
    return spec


def _limit_self(spec: dict[str, Any]) -> None:
    """The memory cap, the CPU cap and no core file, on POSIX.

    It runs before MuPDF loads. A crash writes no core file with the member's
    image in it. Windows is the dev box only."""
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


def _preload() -> Any:
    """Load MuPDF and render one tiny image, inside the confinement."""
    import pymupdf

    tiny = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), True)
    doc = pymupdf.open(stream=tiny.tobytes("png"), filetype="image")
    doc[0].get_pixmap(alpha=True).tobytes("png")
    return pymupdf


def _source_pixels(page: Any) -> tuple[int, int]:
    """The width and height of the source image in PIXELS.

    ``get_image_info`` reads the header and decodes nothing. A page with no
    image info falls back to its size in points."""
    try:
        info = page.get_image_info()
    except Exception:  # an odd file: fall back to the page size
        info = []
    if info and isinstance(info[0], dict):
        width, height = info[0].get("width"), info[0].get("height")
        if isinstance(width, int) and isinstance(height, int):
            return width, height
    return round(page.rect.width), round(page.rect.height)


def _shares(pix: Any) -> tuple[float, float]:
    """``(dark_share, clear_share)`` of a pixmap with alpha.

    ``clear_share`` is the share of all pixels that are transparent.
    ``dark_share`` is the share of the visible pixels that are dark. A dark
    logo on a clear ground has both high, and it disappears on a dark mail.
    MuPDF keeps the colour premultiplied by the alpha, so the dark test
    scales its limit by the alpha in place of a divide."""
    samples = pix.samples
    n = pix.n
    total = pix.width * pix.height
    if total <= 0 or n < 3:
        return 0.0, 0.0
    clear = visible = dark = 0
    for i in range(0, len(samples), n):
        a = samples[i + n - 1] if pix.alpha else 255
        if a < CLEAR_ALPHA:
            clear += 1
            continue
        visible += 1
        r, g, b = samples[i], samples[i + 1], samples[i + 2]
        # Premultiplied: luma * 255 < DARK_LUMA * a, with the 1000 scale.
        if (299 * r + 587 * g + 114 * b) * 255 < DARK_LUMA * 1000 * a:
            dark += 1
    dark_share = dark / visible if visible else 0.0
    return round(dark_share, 4), round(clear / total, 4)


def _encode(pymupdf: Any, data: bytes, spec: dict[str, Any]) -> dict[str, Any]:
    """Decode, crop, scale and encode. Returns the ``ok`` object."""
    _warnings(pymupdf)  # start with an empty list
    try:
        doc = pymupdf.open(stream=data, filetype="image")
        page = doc[0]
    except Exception:  # every decoder failure is one clean refusal
        raise _Refused("broken") from None
    width_px, height_px = _source_pixels(page)
    if width_px <= 0 or height_px <= 0:
        raise _Refused("broken")
    if max(width_px, height_px) > spec["max_side"] or width_px * height_px > spec["max_pixels"]:
        raise _Refused("pixels")
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        raise _Refused("broken")
    if spec["kind"] == "photo":
        side_pt = min(rect.width, rect.height)
        x = (rect.width - side_pt) / 2
        y = (rect.height - side_pt) / 2
        clip = pymupdf.Rect(rect.x0 + x, rect.y0 + y, rect.x0 + x + side_pt, rect.y0 + y + side_pt)
        src_w = src_h = min(width_px, height_px)
    else:
        clip = pymupdf.Rect(rect)
        src_w, src_h = width_px, height_px
    long_px = max(src_w, src_h)
    tried: set[tuple[int, int]] = set()
    for edge in EDGES:
        target = min(edge, long_px)
        out_w = max(1, round(src_w * target / long_px))
        out_h = max(1, round(src_h * target / long_px))
        if (out_w, out_h) in tried:
            continue
        tried.add((out_w, out_h))
        # torect maps the clip (points) onto the exact pixel box.
        matrix = clip.torect(pymupdf.Rect(0, 0, out_w, out_h))
        pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=True)
        said = _warnings(pymupdf).lower()
        if any(word in said for word in _DAMAGE_WORDS):
            raise _Refused("broken")
        dark_share, clear_share = _shares(pix)
        if spec["kind"] == "photo" and clear_share == 0.0 and _opaque(pix):
            flat = pymupdf.Pixmap(pix, 0)  # drop the alpha: no pixel uses it
            out = flat.tobytes("jpg", jpg_quality=spec["jpeg_quality"])
            content_type = "image/jpeg"
        else:
            out = pix.tobytes("png")
            content_type = "image/png"
        if len(out) <= spec["max_output_bytes"]:
            return {
                "content_type": content_type,
                "width": pix.width,
                "height": pix.height,
                "dark_share": dark_share,
                "clear_share": clear_share,
                "data": base64.b64encode(out).decode("ascii"),
            }
    raise _Refused("too_big")


def _warnings(pymupdf: Any) -> str:
    """The MuPDF warnings since the last call, and clear them."""
    try:
        return str(pymupdf.TOOLS.mupdf_warnings(reset=True) or "")
    except Exception:  # an older binding: no warnings to read
        return ""


def _opaque(pix: Any) -> bool:
    """True when every pixel has full alpha."""
    if not pix.alpha:
        return True
    alpha = bytes(pix.samples[pix.n - 1::pix.n])
    return alpha.count(255) == len(alpha)


def _emit(item: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()


def _confine(cc: Any, spec: dict[str, Any], state: dict[str, Any]) -> None:
    """Landlock and the seccomp filter, then the mark of the answer.

    On Linux ``confine`` raises when a step fails, so the mark ``confined``
    is set only after it returned. On another system ``confine`` does
    nothing, and the mark ``unconfined_os`` says so: the parent accepts such
    an answer only on that same system, never on Linux (fix round 1, P2)."""
    cc.confine(spec["landlock_abi"])
    if sys.platform == "linux":
        state["confined"] = True
    else:
        state["unconfined_os"] = sys.platform


def _close_limits(cc: Any, state: dict[str, Any]) -> None:
    """No fork, no new thread, no file growth, then the mark ``limits``."""
    cc.close_limits()
    state["limits"] = True


def _marks(state: dict[str, Any]) -> dict[str, Any]:
    """The confinement marks of the answer. ``confined`` is true only when
    both steps ran on Linux."""
    marks: dict[str, Any] = {
        "confined": bool(state.get("confined") and state.get("limits")),
    }
    if state.get("unconfined_os") and state.get("limits"):
        marks["unconfined_os"] = state["unconfined_os"]
    return marks


def main(argv: list[str]) -> int:
    state: dict[str, Any] = {}
    try:
        spec = _spec(argv[1] if len(argv) > 1 else "")
        cc = _load_confine(spec["confine_path"])
        # The parent passes no descriptor on purpose, and close_fds is not in
        # force under uvloop (WEB-2b repair 1).
        cc.close_inherited_fds()
        _limit_self(spec)
        # One thread here, so the confinement binds each later thread.
        _confine(cc, spec, state)
        pymupdf = _preload()
        _close_limits(cc, state)
        # The untrusted bytes arrive only now, after the confinement.
        data = sys.stdin.buffer.read(spec["max_bytes"] + 1)
        if not data:
            raise _Refused("broken")
        if len(data) > spec["max_bytes"]:
            raise _Refused("size")
        _emit({"ok": {**_encode(pymupdf, data, spec), **_marks(state)}})
    except _Refused as exc:
        _emit({"refused": exc.reason})
    except MemoryError:
        _emit({"refused": "pixels"})
    except Exception as exc:  # no traceback reaches the parent, only a code
        reason = getattr(exc, "reason", None)
        _emit({"refused": reason if reason == "unavailable" else "failed"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
