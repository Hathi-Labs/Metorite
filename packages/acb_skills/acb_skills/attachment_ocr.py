"""OCR of a scanned PDF and an image, in a child process (WS-67 OA-3, D116).

On 2026-10-25 the assistant named two parts from the FILE NAME of a scanned
attachment, because the reader gave no text for a scan (spec
``one_assistant_context.md`` §1, cause C1). The owner chose a library, not an
image model (D116). This module is that library's one door.

**One reader.** :func:`acb_skills.attachment_tools.parse_bounded` is the ONE
bounded parse of the process. It calls this module when the flag is on, for
a PDF whose text layer gives no words (:func:`wants_ocr`) and for a ``.png``,
``.jpg`` or ``.jpeg`` image (:func:`image_suffix`). The chat tool
``read_attachment`` and the email text route both call ``parse_bounded``, so
both get OCR and neither holds a second reader. :mod:`acb_skills.attachment_text`
stays pure: it starts no process.

**Isolation.** Pillow, PDFium, OpenCV and onnxruntime are native code that
reads untrusted bytes. None of them runs in the gateway process. The child is
``attachment_ocr_worker.py``, started as ``python -I <path>`` with the
allowlisted env of :func:`acb_common.child_env.child_env` and no secret. It
gets the bytes on stdin and gives one JSON object on stdout. On Linux the
child caps its own memory (``RLIMIT_AS``), its CPU time (``RLIMIT_CPU``) and
its priority. Every library in it runs on one thread, because the box has two
cores.

**Confinement** (fix round 1, P1). The child runs as the gateway's user. So
before it reads one untrusted byte, it confines itself with Landlock: it may
read only the Python and library dirs, it may write nowhere, and it may not
bind or connect a TCP socket (Landlock ABI :data:`MIN_LANDLOCK_ABI`). It may
not fork or grow a file. A Linux kernel below that ABI makes the child answer
``unavailable``, so it fails closed. The worker's docstring gives the steps.

**The child dies with its read.** It starts in a new session. The parent
kills its process group at :data:`OCR_TIMEOUT_S`, on a cancel, and when its
answer grows past :data:`_MAX_ANSWER_BYTES`. The parent reads stdout in
chunks and never holds more than that cap.

**Fairness.** At most :data:`MAX_OCR_CHILDREN` children live in the process.
The wait for that slot is an ``await`` that holds no thread, and it gives up
after :data:`OCR_SLOT_WAIT_S`. The child is an ``asyncio`` subprocess, so a
read holds no pool thread and the event loop keeps serving every other run.
The slot has no share for each organization (spec §4.1, a condition before
the flag turns on).

**The wait budget** (fix round 1, P2). The email assistant waits 60 s for the
text route. The worst case is the text parse, then the slot wait, then the
OCR kill and the reap: ``DEADLINE_SECONDS + _WAIT_MARGIN + OCR_SLOT_WAIT_S +
OCR_TIMEOUT_S + _REAP_S``, 20 + 2 + 3 + 28 + 2 = 55 s. The fence holds the
sum at 55 s or less.

**Caps.** At most :data:`MAX_OCR_PAGES` pages, each at :data:`OCR_DPI` and at
most :data:`MAX_PAGE_PIXELS` pixels. An image is at most
:data:`MAX_IMAGE_BYTES` bytes and :data:`MAX_IMAGE_PIXELS` pixels. The child
stops between two pages at :data:`OCR_SOFT_SECONDS`, so a slow scan gives the
pages that it read, and the text says how many.

**The answer.** The text starts with :data:`OCR_MARKER`, and
``Extracted.ocr`` is true. A capped read ends with a sentence that gives the
count of pages read. A refusal, a crash, a timeout and a busy slot each give
one sentence, never a traceback.

**Dark.** With ``ATTACHMENT_OCR`` off (the default) nothing here runs, and
both read paths are byte-identical to the paths before OA-3.

Fence: ``tests/unit/test_attachment_ocr.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from acb_common import get_logger
from acb_common.child_env import child_env

from acb_skills.attachment_text import (
    _CONTROLS,
    MAX_FILE_BYTES,
    PAGE_UNREADABLE,
    Extracted,
    text_state,
)

__all__ = [
    "IMAGES_SENTENCE",
    "IMAGE_SUFFIXES",
    "MAX_IMAGE_BYTES",
    "MAX_OCR_PAGES",
    "NO_WORDS_SENTENCE",
    "OCR_MARKER",
    "OCR_TIMEOUT_S",
    "OTHER_IMAGE_SENTENCE",
    "image_suffix",
    "ocr_bounded",
    "ocr_enabled",
    "wants_ocr",
]

_log = get_logger("acb_skills.attachment_ocr")

#: The first line of every text that OCR read.
OCR_MARKER = "(Text read by OCR from a scanned file. It can hold misread words.)"

#: The image kinds that OCR reads. Pillow in the child opens PNG and JPEG only.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
#: An image with no name suffix is read by its type.
_IMAGE_MIMES = {"image/png": ".png", "image/jpeg": ".jpg"}
#: The one sentence that names the image kinds.
IMAGES_SENTENCE = "I read the text of a .png, .jpg or .jpeg image by OCR."
#: The sentence for an image of another kind, while the flag is on.
OTHER_IMAGE_SENTENCE = f"This attachment is an image of a kind that I do not read. {IMAGES_SENTENCE}"
#: The sentence for a scan in which OCR found no words.
NO_WORDS_SENTENCE = "I read this file by OCR, and OCR found no words in it."

#: The pages of one file that OCR reads.
MAX_OCR_PAGES = 15
#: The render resolution of a PDF page.
OCR_DPI = 150
#: The pixels of one rendered page or image. A4 at 150 DPI is 2.2 million.
MAX_PAGE_PIXELS = 4_000_000
#: The longest side of one rendered page or image, in pixels.
MAX_PAGE_SIDE = 4_000
#: The bytes of one image.
MAX_IMAGE_BYTES = 15 * 1024 * 1024
#: The pixels that an image header may claim. More is a decompression bomb.
#: A 48-megapixel phone photo passes.
MAX_IMAGE_PIXELS = 50_000_000
#: The text of one page that the child keeps.
MAX_PAGE_CHARS = 20_000
#: The child starts no new page after this many seconds, from its start.
#: Measured on Linux with one thread: about 5.5 s for a dense A4 page of 40
#: lines (2026-10-25).
OCR_SOFT_SECONDS = 20.0
#: The parent kills the child after this many seconds. With the text parse
#: and the slot wait, the worst case stays under 55 s (see the docstring).
OCR_TIMEOUT_S = 28.0
#: The address space of the child on Linux. A dense page peaked at 874 MB.
OCR_MEMORY_BYTES = 1536 * 1024 * 1024
#: The CPU seconds of the child on Linux. One thread, so CPU time is about
#: wall time, and the wall-clock kill comes first.
OCR_CPU_SECONDS = 40
#: The Landlock ABI that the child needs on Linux: 4 adds TCP bind and
#: connect to the file system rules. A kernel below it fails closed.
MIN_LANDLOCK_ABI = 4
#: The OCR children that live at one time in the process.
MAX_OCR_CHILDREN = 1
#: How long a read waits for a free child slot, then answers busy.
OCR_SLOT_WAIT_S = 3.0
_SLOT_POLL_S = 0.1
#: The bytes of the child's answer that the parent reads. One more byte kills
#: the child (fix round 1, P1: a child that wrote 600 MB once grew the parent
#: by 870 MB).
_MAX_ANSWER_BYTES = 4 * 1024 * 1024
#: The bytes of one read of the child's stdout.
_CHUNK = 64 * 1024
#: The longest wait for a killed child to end. It is part of the budget.
_REAP_S = 2.0

#: Each library in the child runs on one thread. glibc keeps two malloc
#: arenas, which took the address space from 3.0 GB to 0.9 GB. None of these
#: names holds a secret.
_THREAD_VALUES = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "MALLOC_ARENA_MAX": "2",
}

_WORKER = Path(__file__).resolve().with_name("attachment_ocr_worker.py")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

_BUSY = "Another scanned file is being read now. Try again in a moment."
_TOO_SLOW = (
    "Reading the scanned pages took too long, so I stopped. Ask the member "
    "for a shorter file or a text copy."
)
_NO_START = "The OCR reader could not start. Try again in a moment."
_FAILED = "I could not read the scanned pages of this file."
_REASONS = {
    "password": "This PDF has a password, so I cannot read its scanned pages.",
    "broken": "I could not read this file. It can be damaged.",
    "pixels": (
        f"This image has more than {MAX_IMAGE_PIXELS // 1_000_000} million "
        "pixels, so I did not read it."
    ),
    "size": f"This image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB, so I did not read it.",
    "unavailable": (
        "I cannot read scanned files on this server, because it cannot isolate "
        "the OCR reader."
    ),
}
_NOT_AN_IMAGE = (
    "I could not read this file as an image. Its name says image, but its "
    "content is a different kind of file."
)


def ocr_enabled() -> bool:
    """True when ``ATTACHMENT_OCR`` is on. THE one reader. It fails closed."""
    try:
        from acb_common import get_settings

        return bool(getattr(get_settings(), "attachment_ocr", False))
    except Exception:  # a fault in the settings read is off
        _log.warning("attachment_ocr.flag_read_failed")
        return False


def image_suffix(suffix: str, mime: str = "") -> str | None:
    """The image suffix that OCR reads for this file, else ``None``.

    A name suffix decides alone. With no name suffix, the type decides. With
    the flag off it is always ``None``.
    """
    if not ocr_enabled():
        return None
    named = (suffix or "").lower()
    if named:
        return named if named in IMAGE_SUFFIXES else None
    return _IMAGE_MIMES.get((mime or "").split(";", 1)[0].strip().lower())


def wants_ocr(got: Extracted) -> bool:
    """True for a PDF whose text layer gave no words, while the flag is on."""
    return got.kind == "pdf" and not got.ocr and text_state(got) != "words" and ocr_enabled()


# ── The child slot ───────────────────────────────────────────────────────────

_SLOT = threading.BoundedSemaphore(MAX_OCR_CHILDREN)
#: The child of a loop that cannot spawn one (the Windows selector loop of a
#: dev server) runs here, never on the default executor.
_POOL = ThreadPoolExecutor(max_workers=MAX_OCR_CHILDREN, thread_name_prefix="attachment-ocr")


class _Held:
    """One held child slot, released once.

    The slot is process-wide, never per event loop, so a second loop in the
    process cannot start a second child. A child that a thread waits on keeps
    the slot until that thread ends, also when the caller was cancelled.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held = True
        self.thread_owns = False

    def release(self) -> None:
        with self._lock:
            if not self._held:
                return
            self._held = False
        _SLOT.release()


async def _take_slot(wait: float) -> _Held | None:
    loop = asyncio.get_running_loop()
    end = loop.time() + wait
    while not _SLOT.acquire(blocking=False):
        if loop.time() >= end:
            return None
        await asyncio.sleep(_SLOT_POLL_S)
    return _Held()


# ── The child ────────────────────────────────────────────────────────────────


def _spec(kind: str, max_bytes: int) -> dict[str, Any]:
    """The caps that the child applies. Read at each call, so a test can lower them."""
    return {
        "kind": kind,
        "max_bytes": max_bytes,
        "max_pages": MAX_OCR_PAGES,
        "dpi": OCR_DPI,
        "max_page_pixels": MAX_PAGE_PIXELS,
        "max_page_side": MAX_PAGE_SIDE,
        "max_image_pixels": MAX_IMAGE_PIXELS,
        "max_page_chars": MAX_PAGE_CHARS,
        "soft_seconds": OCR_SOFT_SECONDS,
        "memory_bytes": OCR_MEMORY_BYTES,
        "cpu_seconds": OCR_CPU_SECONDS,
        "landlock_abi": MIN_LANDLOCK_ABI,
    }


def _worker_argv(spec: dict[str, Any]) -> list[str]:
    """The child's command line. A test replaces it to drive a fake child."""
    return [sys.executable, "-I", str(_WORKER), json.dumps(spec)]


def _child_env() -> dict[str, str]:
    """The child's env: the ``child_env`` allowlist and the thread names only.

    The gateway env holds the database URLs, the provider keys and the
    internal bearer. A child that reads untrusted bytes must not hold them
    (WS-49 BH-1).
    """
    return child_env(extra=_THREAD_VALUES)


def _answer_of(out: bytes) -> dict[str, Any] | None:
    if not out or len(out) > _MAX_ANSWER_BYTES:
        return None
    try:
        answer = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return answer if isinstance(answer, dict) else None


def _kill_group(proc: Any) -> None:
    """Kill the child and, on POSIX, its process group.

    The child starts in a new session, so its group id is its pid. A kill
    of the pid follows, for a child that has not called ``setsid`` yet. The
    caller reaps it. A child that has gone already is no error.
    """
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


_ALREADY_LOGGED_UNCONFINED: list[bool] = []


def _note_unconfined() -> None:
    """Log once that this system has no Landlock (a dev box, never the box)."""
    if sys.platform != "linux" and not _ALREADY_LOGGED_UNCONFINED:
        _ALREADY_LOGGED_UNCONFINED.append(True)
        _log.warning("attachment_ocr.unconfined", platform=sys.platform)


class _Running:
    """The child of the blocking path, so that a cancel can kill it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Any = None
        self._cancelled = False

    def started(self, proc: Any) -> None:
        with self._lock:
            self._proc = proc
            cancelled = self._cancelled
        if cancelled:
            _kill_group(proc)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            _kill_group(proc)


def _feed_blocking(stdin: Any, data: bytes) -> None:
    with contextlib.suppress(OSError, ValueError):
        stdin.write(data)
    with contextlib.suppress(OSError, ValueError):
        stdin.close()


def _read_capped_blocking(stdout: Any) -> bytes | None:
    """At most ``_MAX_ANSWER_BYTES`` of stdout, else ``None``."""
    out = bytearray()
    while chunk := stdout.read1(_CHUNK):
        out += chunk
        if len(out) > _MAX_ANSWER_BYTES:
            return None
    return bytes(out)


def _run_blocking(
    argv: list[str], data: bytes, timeout: float, held: _Held, running: _Running,
) -> dict[str, Any] | str:
    """The child, for a loop that cannot spawn one (a Windows dev server).

    It runs on :data:`_POOL`, and releases the slot when it ends. A timer
    kills the child at *timeout*, and a cancel of the caller kills it at once
    through *running*.
    """
    try:
        try:
            proc = subprocess.Popen(  # our own worker file, no shell
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=_child_env(), start_new_session=True,
            )
        except OSError:
            return _NO_START
        running.started(proc)
        expired = threading.Event()

        def _expire() -> None:
            expired.set()
            _kill_group(proc)

        timer = threading.Timer(timeout, _expire)
        timer.daemon = True
        timer.start()
        feeder = threading.Thread(target=_feed_blocking, args=(proc.stdin, data), daemon=True)
        feeder.start()
        try:
            out = _read_capped_blocking(proc.stdout)
        finally:
            timer.cancel()
            if proc.poll() is None:
                _kill_group(proc)
            proc.wait()
            with contextlib.suppress(OSError):
                proc.stdout.close()
        if expired.is_set():
            return _TOO_SLOW
        if out is None:
            _log.warning("attachment_ocr.answer_too_large", path="blocking")
            return _FAILED
        return _answer_of(out) or _FAILED
    finally:
        held.release()


async def _feed(stdin: Any, data: bytes) -> None:
    """Write *data* to the child, and close its stdin. A child that stopped
    reading is no error: its answer decides."""
    try:
        stdin.write(data)
        await stdin.drain()
    except (OSError, RuntimeError):
        pass
    finally:
        with contextlib.suppress(Exception):
            stdin.close()


async def _talk(proc: Any, data: bytes) -> dict[str, Any] | str:
    """Feed the child and read its answer, at most ``_MAX_ANSWER_BYTES``."""
    feed = asyncio.ensure_future(_feed(proc.stdin, data))
    out = bytearray()
    try:
        while chunk := await proc.stdout.read(_CHUNK):
            out += chunk
            if len(out) > _MAX_ANSWER_BYTES:
                _kill_group(proc)
                _log.warning("attachment_ocr.answer_too_large", path="async")
                return _FAILED
        await proc.wait()
    finally:
        feed.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await feed
    return _answer_of(bytes(out)) or _FAILED


async def _reap(proc: Any) -> None:
    """Wait for a killed child, within :data:`_REAP_S`.

    ``Process.wait()`` returns only when every pipe has closed. A stdout pipe
    that nobody reads stays open, so the wait would never end (found by the
    flood fence). The rest of stdout is read and thrown away: the child is
    dead, so that is at most one pipe buffer.
    """
    async def _drain_and_wait() -> None:
        with contextlib.suppress(Exception):
            while await proc.stdout.read(_CHUNK):
                pass
        await proc.wait()

    try:
        await asyncio.wait_for(_drain_and_wait(), _REAP_S)
    except TimeoutError:
        _log.warning("attachment_ocr.reap_timeout", pid=proc.pid)


async def _run_child(data: bytes, spec: dict[str, Any], held: _Held) -> dict[str, Any] | str:
    """The child's answer, or a sentence that says why there is none.

    The child's process group is killed and the child reaped on a timeout,
    on a cancel of the caller and on an answer past the cap, so no OCR
    outlives its read.
    """
    _note_unconfined()
    argv = _worker_argv(spec)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_child_env(),
            start_new_session=True,
        )
    except NotImplementedError:
        loop = asyncio.get_running_loop()
        running = _Running()
        job = loop.run_in_executor(
            _POOL, _run_blocking, argv, data, OCR_TIMEOUT_S, held, running,
        )
        held.thread_owns = True
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            running.cancel()
            raise
    except OSError:
        return _NO_START
    try:
        return await asyncio.wait_for(_talk(proc, data), OCR_TIMEOUT_S)
    except TimeoutError:
        return _TOO_SLOW
    finally:
        if proc.returncode is None:
            _kill_group(proc)
        # Always: a child that exited can still hold an unread pipe open.
        await _reap(proc)


# ── The answer ───────────────────────────────────────────────────────────────


def _clean(text: object) -> str:
    """One page's text with no control character, within the page cap."""
    return str(text).translate(_CONTROLS)[:MAX_PAGE_CHARS].strip()


def _pages_of(answer: dict[str, Any]) -> tuple[list[str | None], int, str | None] | None:
    """The checked fields of an ``ok`` answer, else ``None``."""
    pages, total, stop = answer.get("pages"), answer.get("total"), answer.get("stop")
    if not isinstance(pages, list) or not all(p is None or isinstance(p, str) for p in pages):
        return None
    if len(pages) > MAX_OCR_PAGES:  # the child never reads more than the cap
        return None
    if not isinstance(total, int) or isinstance(total, bool) or total < len(pages):
        return None
    if stop not in (None, "pages", "time"):
        return None
    return [None if p is None else _clean(p) for p in pages], total, stop


def _image_answer(pages: list[str | None]) -> Extracted:
    text = (pages[0] or "") if pages else ""
    body = f"{OCR_MARKER}\n{text}" if text else ""
    return Extracted(text=body, kind="image", unit="image", read=1, total=1,
                     stopped=False, ocr=True)


def _pdf_answer(
    pages: list[str | None], total: int, stop: str | None, text_layer: Extracted | None,
) -> Extracted | str:
    parts = [OCR_MARKER]
    words = 0
    for number, page in enumerate(pages, 1):
        head = f"[Page {number}]"
        if page is None:
            parts.append(f"{head}\n{PAGE_UNREADABLE}")
        elif page:
            parts.append(f"{head}\n{page}")
            words += len(page)
        else:
            parts.append(head)
    read = len(pages)
    if not words:
        if stop == "time":
            return _TOO_SLOW
        if text_layer is not None:
            return replace(text_layer, ocr=True)
        return Extracted(text="", kind="pdf", unit="page", read=read, total=total,
                         stopped=read < total, ocr=True)
    if stop == "pages":
        parts.append(
            f"(OCR read the first {read} of {total} pages. It reads at most "
            f"{MAX_OCR_PAGES} pages of one file.)"
        )
    elif stop == "time":
        parts.append(
            f"(OCR read the first {read} of {total} pages, and then it reached its time limit.)"
        )
    return Extracted(text="\n".join(parts), kind="pdf", unit="page", read=read, total=total,
                     stopped=read < total, ocr=True)


async def ocr_bounded(
    data: bytes, suffix: str, *, text_layer: Extracted | None = None,
) -> Extracted | str:
    """The text that OCR reads in *data*, or one sentence that says why not.

    *suffix* is ``.pdf`` or an image suffix of :data:`IMAGE_SUFFIXES`.
    *text_layer* is what the text reader gave for the PDF. When OCR finds no
    words, the answer is that, marked ``ocr``. It never raises: a refusal, a
    busy slot, a crash, a timeout and a bug each give one sentence.
    """
    kind = "pdf" if suffix.lower() == ".pdf" else "image"
    if kind == "image":
        if len(data) > MAX_IMAGE_BYTES:
            return _REASONS["size"]
        if not data.startswith((_PNG_MAGIC, _JPEG_MAGIC)):
            return _NOT_AN_IMAGE
    max_bytes = MAX_FILE_BYTES if kind == "pdf" else MAX_IMAGE_BYTES
    held = await _take_slot(OCR_SLOT_WAIT_S)
    if held is None:
        return _BUSY
    started = time.monotonic()
    try:
        answer = await _run_child(data, _spec(kind, max_bytes), held)
    except Exception as exc:  # a bug here is a refusal, never a crash
        _log.warning("attachment_ocr.failed", kind=kind, error=type(exc).__name__)
        return _FAILED
    finally:
        if not held.thread_owns:
            held.release()
    ms = int((time.monotonic() - started) * 1000)
    if isinstance(answer, str):
        _log.info("attachment_ocr.refused", kind=kind, size=len(data), ms=ms, why=answer[:40])
        return answer
    if answer.get("status") == "refused":
        reason = str(answer.get("reason") or "")
        _log.info("attachment_ocr.refused", kind=kind, size=len(data), ms=ms, why=reason[:20])
        return _REASONS.get(reason, _FAILED)
    fields = _pages_of(answer) if answer.get("status") == "ok" else None
    if fields is None:
        _log.warning("attachment_ocr.bad_answer", kind=kind, ms=ms)
        return _FAILED
    pages, total, stop = fields
    _log.info("attachment_ocr.read", kind=kind, size=len(data), ms=ms,
              pages=len(pages), total=total, stop=stop)
    if kind == "image":
        return _image_answer(pages)
    return _pdf_answer(pages, total, stop, text_layer)
