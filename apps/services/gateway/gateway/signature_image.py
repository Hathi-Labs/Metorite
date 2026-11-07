"""The re-encode of a signature image, in a confined child (WS-17 SG-1).

Spec: ``project-docs/specs/email_app_master_plan.md`` §23.3.4, D-EM-67.

**What is stored is what the child produced, never what the member sent.** The
request process does two cheap checks: the byte cap, and the type sniff of
``gateway/avatar.py`` (an SVG never reaches a decoder). Then a child process,
``signature_image_worker.py``, decodes the bytes, scales them and writes PNG or
JPEG. The child confines itself with ``acb_skills/child_confine.py`` (Landlock,
seccomp, no fork) and caps its own memory. The parent kills its process group
at :data:`TIMEOUT_S` and reads at most :data:`_MAX_ANSWER_BYTES` of its answer.

This module holds no SQL and no route. ``routes/email/signature_images.py`` is
its one caller, and it owns the table. Every refusal is a
:class:`SignatureImageError` with a sentence for the member and an HTTP status.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib.util
import json
import math
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acb_common import get_logger
from acb_common.child_env import child_env

from gateway.avatar import MAX_UPLOAD_BYTES, AvatarError, sniff

_log = get_logger("gateway.signature_image")

#: The two kinds of image. A logo keeps its shape, and a photo is a square.
KINDS = ("logo", "photo")
#: The types that the sniff passes. MuPDF 1.28 cannot open WebP (measured on
#: 2026-11-07: ``FileDataError``), so a WebP is refused before the child.
ACCEPTED = ("JPEG", "PNG")
#: The bytes of one stored image (D-EM-67). The child steps the edge down to
#: fit, then refuses.
MAX_OUTPUT_BYTES = 200 * 1024
#: A source with more pixels, or a longer side, is refused before the decode.
MAX_SOURCE_PIXELS = 40_000_000
MAX_SOURCE_SIDE = 12_000
#: The parent kills the child after this many seconds (D-EM-67).
TIMEOUT_S = 10.0
#: The address space of the child on Linux. A 40-megapixel PNG needs about
#: 160 MB as RGBA, and MuPDF holds the source beside it.
MEMORY_BYTES = 1024 * 1024 * 1024
#: The CPU seconds of the child on Linux. The wall-clock kill comes first.
CPU_SECONDS = 20
#: The Landlock ABI that the child asks. 1 is the file system. The seccomp
#: filter refuses ``socket`` at every ABI, so the child has no network.
MIN_LANDLOCK_ABI = 1
JPEG_QUALITY = 85
#: The children that run at one time in the process, and how long an upload
#: waits for a free one.
MAX_CHILDREN = 2
SLOT_WAIT_S = 5.0
_SLOT_POLL_S = 0.1
#: The bytes of the child's answer that the parent reads (the base64 of
#: 200 KB, and the JSON around it). One more byte kills the child.
_MAX_ANSWER_BYTES = 512 * 1024
_CHUNK = 64 * 1024
#: The longest wait for a killed child to end.
_REAP_S = 2.0

_WORKER = Path(__file__).resolve().with_name("signature_image_worker.py")
#: Each library in the child runs on one thread. None of these names holds a
#: secret.
_THREAD_VALUES = {
    "OMP_NUM_THREADS": "1",
    "MALLOC_ARENA_MAX": "2",
}
_MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}

_UPLOAD_PNG = "Upload a PNG or a JPEG."
_REASONS: dict[str, tuple[str, int]] = {
    "broken": (f"That image could not be read. It may be damaged. {_UPLOAD_PNG}", 400),
    "size": (f"That image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
             "Upload a smaller picture.", 413),
    "pixels": (f"That image is too large: more than {MAX_SOURCE_PIXELS // 1_000_000} "
               f"million pixels, or more than {MAX_SOURCE_SIDE:,} pixels on a side. "
               "Upload a smaller picture.", 400),
    "too_big": (f"That image is still larger than {MAX_OUTPUT_BYTES // 1024} KB at "
                "240 pixels wide. Upload a simpler picture, such as a logo with "
                "few colours.", 400),
    "unavailable": ("This server cannot isolate the image reader, so it cannot take "
                    "signature images now. Ask your administrator.", 503),
    "failed": (f"That image could not be read. {_UPLOAD_PNG}", 400),
}
_TIMED_OUT = (
    f"Reading that image took longer than {int(TIMEOUT_S)} seconds, so it was "
    "stopped. Upload a smaller picture."
)
_BUSY = "Other images are being read now. Try again in a moment."
_NO_START = "The image reader could not start. Try again in a moment."


class SignatureImageError(ValueError):
    """A refusal. The text is a sentence for the member, ``status`` the HTTP
    answer: 4xx for the image, 503 for this server."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Encoded:
    """The image as the child wrote it. ``width`` and ``height`` are the real
    pixmap size. ``dark_share`` is the share of the visible pixels that are
    dark, and ``clear_share`` the share of all pixels that are transparent."""

    data: bytes
    content_type: str
    width: int
    height: int
    dark_share: float
    clear_share: float


def check_type(data: bytes) -> str:
    """The byte cap and the sniff, in the request, before any decoder.

    Raises :class:`SignatureImageError`. An SVG, a GIF, a WebP and a file that
    is no image never reach the child."""
    if not data:
        raise SignatureImageError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        message, status = _REASONS["size"]
        raise SignatureImageError(message, status)
    try:
        return sniff(data, what="a signature image", accepted=ACCEPTED)
    except AvatarError as exc:
        raise SignatureImageError(str(exc)) from exc


async def encode(data: bytes, kind: str) -> Encoded:
    """Check, decode and re-encode one upload. Raises :class:`SignatureImageError`."""
    if kind not in KINDS:
        raise SignatureImageError("The kind of image must be logo or photo.", 422)
    check_type(data)
    held = await _take_slot(SLOT_WAIT_S)
    if not held:
        raise SignatureImageError(_BUSY, 503)
    try:
        answer, timed_out = await _run_child(_worker_argv(_spec(kind)), data, TIMEOUT_S)
    finally:
        _SLOT.release()
    if timed_out:
        raise SignatureImageError(_TIMED_OUT, 503)
    return _parse(answer)


# ── The child ───────────────────────────────────────────────────────────────

_SLOT = threading.BoundedSemaphore(MAX_CHILDREN)


async def _take_slot(wait: float) -> bool:
    """A process-wide slot, so a second event loop starts no extra child."""
    loop = asyncio.get_running_loop()
    end = loop.time() + wait
    while not _SLOT.acquire(blocking=False):
        if loop.time() >= end:
            return False
        await asyncio.sleep(_SLOT_POLL_S)
    return True


def _confine_path() -> str:
    """The file of ``acb_skills.child_confine``, for the child to load by path.

    The child imports nothing from ``acb_skills``: the package ``__init__``
    loads the whole tool set (packages/acb_skills/AGENTS.md item 4a)."""
    found = importlib.util.find_spec("acb_skills.child_confine")
    if found is None or not found.origin:
        raise SignatureImageError(_REASONS["unavailable"][0], 503)
    return found.origin


def _spec(kind: str) -> dict[str, Any]:
    """The caps that the child applies. Read at each call, so a test can lower them."""
    return {
        "kind": kind,
        "max_bytes": MAX_UPLOAD_BYTES,
        "max_pixels": MAX_SOURCE_PIXELS,
        "max_side": MAX_SOURCE_SIDE,
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "jpeg_quality": JPEG_QUALITY,
        "memory_bytes": MEMORY_BYTES,
        "cpu_seconds": CPU_SECONDS,
        "landlock_abi": MIN_LANDLOCK_ABI,
        "confine_path": _confine_path(),
    }


def _worker_argv(spec: dict[str, Any]) -> list[str]:
    """The child's command line. A test replaces it to drive a fake child."""
    return [sys.executable, "-I", str(_WORKER), json.dumps(spec)]


def _child_env() -> dict[str, str]:
    """The child's env: the ``child_env`` allowlist and the thread names only.

    The gateway env holds the database URLs, the provider keys and the
    internal bearer. A child that reads a member's bytes must not hold them
    (WS-49 BH-1)."""
    return child_env(extra=_THREAD_VALUES)


_UNCONFINED_LOGGED: list[bool] = []


def _note_unconfined() -> None:
    """Log once that this system has no Landlock (a dev box, never the box)."""
    if sys.platform != "linux" and not _UNCONFINED_LOGGED:
        _UNCONFINED_LOGGED.append(True)
        _log.warning("signature_image.unconfined", platform=sys.platform)


def _kill_group(proc: Any) -> None:
    """Kill the child and, on POSIX, its process group. The caller reaps it."""
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError, ProcessLookupError):
        proc.kill()


async def _feed(stdin: Any, data: bytes) -> None:
    """Write the bytes to the child and close its stdin. A child that stopped
    reading is no error: its answer decides."""
    try:
        stdin.write(data)
        await stdin.drain()
    except (OSError, RuntimeError):
        pass
    finally:
        with contextlib.suppress(Exception):
            stdin.close()


async def _talk(proc: Any, data: bytes) -> bytes | None:
    """Feed the child and read its stdout up to the cap. ``None`` when the
    answer passed the cap: the child is killed then."""
    feed = asyncio.ensure_future(_feed(proc.stdin, data))
    out = bytearray()
    try:
        while chunk := await proc.stdout.read(_CHUNK):
            out += chunk
            if len(out) > _MAX_ANSWER_BYTES:
                _kill_group(proc)
                return None
        await proc.wait()
        return bytes(out)
    finally:
        feed.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feed


async def _reap(proc: Any) -> None:
    """Wait for a killed child within :data:`_REAP_S`. The rest of stdout is
    read and thrown away, because on Windows ``wait()`` returns only when
    that pipe closes (``attachment_ocr._reap`` measured it)."""
    async def _drain_and_wait() -> None:
        with contextlib.suppress(Exception):
            while await proc.stdout.read(_CHUNK):
                pass
        await proc.wait()

    try:
        await asyncio.wait_for(_drain_and_wait(), _REAP_S)
    except TimeoutError:
        _log.warning("signature_image.reap_timeout", pid=proc.pid)


def _run_blocking(argv: list[str], data: bytes, timeout: float) -> tuple[bytes | None, bool]:
    """The child, for a loop that cannot spawn one (a Windows dev server)."""
    try:
        done = subprocess.run(  # our own worker file, no shell
            argv, input=data, capture_output=True, timeout=timeout, check=False,
            env=_child_env(), start_new_session=os.name == "posix",
        )
    except subprocess.TimeoutExpired:
        return None, True
    except OSError as exc:
        raise SignatureImageError(_NO_START, 503) from exc
    out = done.stdout
    return (None if len(out) > _MAX_ANSWER_BYTES else out), False


async def _run_child(
    argv: list[str], data: bytes, timeout: float,
) -> tuple[bytes | None, bool]:
    """``(stdout, timed_out)``. The child's process group is killed and the
    child reaped on a timeout, on a cancel of the caller and on an answer past
    the cap, so no decode outlives its upload."""
    _note_unconfined()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_child_env(),
            start_new_session=os.name == "posix",
        )
    except NotImplementedError:
        return await asyncio.to_thread(_run_blocking, argv, data, timeout)
    except OSError as exc:
        raise SignatureImageError(_NO_START, 503) from exc
    try:
        return await asyncio.wait_for(_talk(proc, data), timeout), False
    except TimeoutError:
        return None, True
    finally:
        if proc.returncode is None:
            _kill_group(proc)
        await _reap(proc)


# ── The answer ──────────────────────────────────────────────────────────────


def _bad() -> SignatureImageError:
    return SignatureImageError(*_REASONS["failed"])


def _share(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _bad()
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise _bad()
    return number


def _size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 480:
        raise _bad()
    return value


def _was_confined(value: dict[str, Any]) -> bool:
    """True when the child decoded inside its confinement (fix round 1, P2).

    On Linux the child marks ``confined`` only after ``confine`` and
    ``close_limits`` returned, and the parent takes nothing else. On a dev
    box with no Landlock (Windows), it takes an answer that names this same
    system in ``unconfined_os``. A child that skipped the confinement sends
    neither mark, so its answer is refused on every system."""
    if value.get("confined") is True:
        return True
    return sys.platform != "linux" and value.get("unconfined_os") == sys.platform


def _parse(answer: bytes | None) -> Encoded:
    """The child's one line, checked field by field. The parent trusts no
    value of a child that read hostile bytes: the type, the size, the shares,
    and the magic bytes of the image must each hold."""
    if not answer:
        raise _bad()
    try:
        item = json.loads(answer.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _bad() from None
    if not isinstance(item, dict) or len(item) != 1:
        raise _bad()
    (name, value), = item.items()
    if name == "refused":
        message, status = _REASONS.get(value if isinstance(value, str) else "",
                                       _REASONS["failed"])
        raise SignatureImageError(message, status)
    if name != "ok" or not isinstance(value, dict):
        raise _bad()
    if not _was_confined(value):
        _log.warning("signature_image.unconfined_answer", platform=sys.platform)
        raise SignatureImageError(*_REASONS["unavailable"])
    content_type = value.get("content_type")
    if content_type not in _MAGIC or not isinstance(value.get("data"), str):
        raise _bad()
    try:
        data = base64.b64decode(value["data"], validate=True)
    except ValueError:
        raise _bad() from None
    if not data or len(data) > MAX_OUTPUT_BYTES or not data.startswith(_MAGIC[content_type]):
        raise _bad()
    return Encoded(
        data=data, content_type=content_type,
        width=_size(value.get("width")), height=_size(value.get("height")),
        dark_share=_share(value.get("dark_share")),
        clear_share=_share(value.get("clear_share")),
    )


__all__ = [
    "ACCEPTED",
    "KINDS",
    "MAX_OUTPUT_BYTES",
    "TIMEOUT_S",
    "Encoded",
    "SignatureImageError",
    "check_type",
    "encode",
]
