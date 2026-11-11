"""The Trafilatura child process of :mod:`acb_skills.web_tools` (WS-WEB WEB-2).

The parent starts this file as a SCRIPT, never with ``-m``::

    python -I <path>/web_reader_worker.py

``-m acb_skills...`` runs the package ``__init__``, which imports the whole
tool set of the agents and costs about 1.2 s more on each read. ``-I`` ignores
``PYTHONPATH`` and the user site, and it puts no script dir on ``sys.path``.
This file imports nothing from ``acb_skills``. It loads the one confinement
module of the platform, ``child_confine.py`` in the same dir, by its path.

**Why a child.** One Python regex holds the GIL for its whole pass, so a
thread cannot bound Trafilatura. One public page with a long
``<meta name="author">`` value stopped the event loop of the gateway for 21 s,
and a timeout on that loop cannot fire while the loop is stopped. The parent
can kill a child at any moment, so the parent stays free. WEB-3 and each later
parser run the same way.

**The contract.** The HTML comes on stdin as UTF-8 text, never as bytes that
Trafilatura could inflate. The child writes one JSON object on one line of
stdout and exits 0:

* ``{"text": "...", "title": "...", "date": "..."}``. ``text`` is the main
  text as Markdown. An empty ``text`` means that Trafilatura found none.
* ``{"fault": "<code>"}``: ``package`` when Trafilatura does not import,
  ``memory`` at the memory cap, ``unavailable`` when the confinement cannot
  apply and ``failed`` for each other fault. The parent
  owns each word that a person reads, so the child sends a code only.

**The limits.** The parent kills the child at its timeout. On Linux the child
also caps its address space and its CPU time, writes no core file and takes a
low priority. The CPU cap is the second net: it ends a child that outlives its
parent. Windows is the dev box only, and it has no ``resource`` module.

**No secret and no fetch.** The parent gives the allowlisted env of
``acb_common.child_env.child_env``, so no API key and no database URL reach a
process that parses a hostile page. ``extract`` gets text and no URL, so it
fetches nothing.

**The confinement** (WEB-2b). The child runs as the gateway's user, so a bug in
a parser could read ``.env`` or open a socket. The order of :func:`main` is the
control:

1. ``close_inherited_fds``: shut each descriptor from 3 up. ``close_fds`` is
   not in force when the parent runs on uvloop, so an inheritable descriptor of
   the parent reaches the child.
2. ``_limit_self``: the memory cap, the CPU cap and a low priority.
3. ``confine``, while the child has ONE thread: Landlock lets it READ only the
   Python and library dirs and write nowhere. With ABI 4 or later it refuses
   each TCP bind and connect. The seccomp filter refuses each new socket and
   each signal to another process.
4. ``_preload``: one small page runs through ``extract``, so each import
   happens inside the confinement.
5. ``close_limits``: no fork, no new thread and no file growth.
6. Only then does the child read the page from stdin.

Landlock ABI 1 (the file system) is the floor here, in :data:`LANDLOCK_ABI`.
The network needs no Landlock rule: seccomp refuses ``socket`` itself, and step
1 leaves no socket to connect. A kernel with no Landlock, or a machine that is
not x86-64, gives ``{"fault": "unavailable"}`` and no parse. The parent then
reads with its regex strip.

**File mode** (WEB-3). ``python -I <path>/web_reader_worker.py .pdf`` reads a
file, not a page. The extension is one of :data:`FILE_TYPES`. The parent picks
it from a fixed table, and never from the file. The bytes of the file come on
stdin, and the child opens no path. It runs the SAME ``main`` in the SAME
order: close the fds, limit, confine, one warm-up (a tiny CSV and a tiny PDF),
close the limits, and only then read the stdin. MarkItDown reads the file.
For a PDF that it cannot read, pdfplumber reads it. The child imports both
inside the confinement, so the parent never holds them. The answer adds
``reader`` (``markitdown`` or ``pdfplumber``) to the fields above.
:data:`MAX_FILE_TEXT_CHARS` caps the text, and the parent reads that as the
reader output cap.

Fence: ``tests/unit/test_web_readers.py`` and ``tests/unit/test_web_files.py``.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

#: The address space of the child on Linux. Pages of 192 KiB of many tags, of
#: deep nesting and of plain words peaked at 171 MB, and at 66 MB resident
#: (measured on Linux on 2026-11-02, with Trafilatura 2.3.1).
MEMORY_BYTES = 512 * 1024 * 1024
#: The CPU seconds of the child on Linux. One thread, so CPU time is about
#: wall time. The parent's kill at ``READER_TIMEOUT_S`` comes first.
CPU_SECONDS = 10

#: WEB-3, file mode. A file parse holds more than a page: MarkItDown loads
#: pandas, numpy, onnxruntime (its type guess) and pdfminer. So the address
#: space is larger. The CPU time stays below the kill of the parent
#: (``FILE_TIMEOUT_S`` in ``web_tools``).
FILE_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
FILE_CPU_SECONDS = 20
#: The reader output cap: the most characters of text that a file gives. The
#: parent spills the text of a file that is longer than the ask of the call.
MAX_FILE_TEXT_CHARS = 300_000
#: The file types of file mode: the extension, and the media type that goes to
#: MarkItDown as a hint. The parent maps a content type to an extension.
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".epub": "application/epub+zip",
}
#: The first bytes of each binary file type. MarkItDown reads a body that it
#: cannot place as plain text, so a mislabelled binary body would give noise.
_ZIP_MAGIC = b"PK\x03\x04"
_MAGIC = {".pdf": b"%PDF-", ".docx": _ZIP_MAGIC, ".pptx": _ZIP_MAGIC,
          ".xlsx": _ZIP_MAGIC, ".epub": _ZIP_MAGIC}
#: A PDF may start with junk. Readers look for the mark in this many bytes.
_PDF_MARK_WINDOW = 1024
_CUT_LINE = "\n\n[... the file text is cut at {n:,} characters]"

#: The Landlock ABI that the child asks of the kernel. 1 is the file system.
#: The kernel's own ABI decides the rest: from 4 on, TCP is refused too.
LANDLOCK_ABI = 1

#: One small page for :func:`_preload`.
_WARM_HTML = (
    "<html><head><title>Warm-up page</title>"
    '<meta name="author" content="Reader"><meta name="date" content="2026-01-02">'
    "</head><body><nav><a href='/'>Home</a><a href='/news'>News</a></nav><article>"
    "<h1>Warm-up page</h1>"
    + "<p>The reader runs one page like this before it reads the real page, so each "
      "module that a normal read needs is loaded inside the confinement.</p>" * 6
    + "</article><footer>Footer text of the warm-up page.</footer></body></html>"
)

#: Trafilatura's extensive search takes a year from prose ("founded in 2009")
#: or from a footer ("(c) 2019") and writes it as ``2009-01-01``. So it is off.
_DATE_PARAMS = {"extensive_search": False, "original_date": True}


def _load_confine() -> Any:
    """Load ``child_confine.py`` from this dir, by path.

    ``-I`` puts no dir on ``sys.path``, and ``import acb_skills`` would run the
    package ``__init__``. The module writes no bytecode into the package dir."""
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


def _limit_self(file_mode: bool = False) -> None:
    """The memory cap, the CPU cap, no core file and a low priority, on Linux.

    It runs before the import of Trafilatura and before one byte of the page.
    Windows has no ``resource`` module, and the child runs with no cap there.
    File mode (WEB-3) has larger caps.
    """
    sys.dont_write_bytecode = True
    # numpy starts a pool of threads at its import. One thread is all that the
    # confinement allows, and the file is read by one thread.
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    try:
        import resource
    except ImportError:
        return
    memory = FILE_MEMORY_BYTES if file_mode else MEMORY_BYTES
    cpu = FILE_CPU_SECONDS if file_mode else CPU_SECONDS
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with contextlib.suppress(OSError):
        os.nice(10)


def _before_confine(file_mode: bool) -> None:
    """What file mode must do while it can still read the whole disk.

    openpyxl builds a ``mimetypes.MimeTypes()`` at import. That reads
    ``/etc/mime.types``, which is outside the read dirs, so the import would
    fail with a permission error. ``mimetypes.init()`` reads the files once and
    sets the flag, and a later ``MimeTypes()`` then reads nothing. The module
    starts no thread and opens no socket."""
    if file_mode:
        import mimetypes

        mimetypes.init()


def _scalar(value: str) -> str:
    """One value of Trafilatura's front matter. It writes a plain value, or a
    JSON string when the value holds a special character."""
    if value.startswith('"'):
        try:
            loaded = json.loads(value)
        except ValueError:
            return value
        return loaded if isinstance(loaded, str) else value
    return value


def _front_matter(markdown: str) -> tuple[dict[str, str], str]:
    """``(fields, text)``: the front matter of Trafilatura's Markdown, and
    the text after it.

    Trafilatura writes ``---``, one ``name: value`` line for each field, and
    ``---``. A value with a line break or a special character is a JSON
    string, so no field line is ``---``. A title that holds ``---`` cannot
    end the front matter. With no front matter, ``fields`` is empty."""
    lines = markdown.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        return {}, markdown
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        name, sep, value = line.partition(": ")
        if sep:
            fields[name.strip()] = _scalar(value.strip())
    return fields, "\n".join(lines[end + 1:])


def _stated(date: str, html: str) -> bool:
    """True when a ``meta`` tag, a ``time`` tag or a JSON-LD date field of the
    page names *date*.

    With the extensive search off, Trafilatura still takes a bare
    ``2021-03-12`` from the prose, and a date from a heading or from a URL. A
    date that the page only mentions is not the date of the page, so it never
    reaches the header. A date in another format gives no line. That loss is
    safe, and a guess shown as a fact is not."""
    day = re.escape(date)
    pattern = rf'<(?:meta|time)\b[^<>]*{day}|"date\w*"\s*:\s*"{day}'
    return re.search(pattern, html, re.IGNORECASE) is not None


def _extract_markdown(html: str) -> dict[str, str] | None:
    """The fields of the answer for ``html``, or None when no text was found.

    ``extract`` gets the text and no URL, so it fetches nothing. ``html`` is
    text, not bytes: Trafilatura inflates bytes that look compressed, and that
    would pass the bound of the decode."""
    from trafilatura import extract

    markdown = extract(html, output_format="markdown", with_metadata=True,
                       include_comments=False, favor_precision=True,
                       date_extraction_params=_DATE_PARAMS)
    if not markdown:
        return None
    fields, text = _front_matter(markdown)
    text = text.strip()
    if not text:
        return None
    date = fields.get("date", "")
    return {"text": text, "title": fields.get("title", ""),
            "date": date if date and _stated(date, html) else ""}


def _answer(html: str) -> dict[str, str]:
    """The one object that the child writes. It never raises for a fault."""
    try:
        return _extract_markdown(html) or {"text": ""}
    except ImportError:
        return {"fault": "package"}
    except MemoryError:
        return {"fault": "memory"}
    except Exception:  # no traceback reaches the parent, only a code
        return {"fault": "failed"}


# ── File mode (WEB-3) ────────────────────────────────────────────────────────


def _warm_pdf() -> bytes:
    """A one-page PDF of our own text, for the warm-up. It holds the offsets
    of its own xref table, so no reader needs to repair it."""
    stream = b"BT /F1 12 Tf 20 100 Td (Warm-up file for the reader) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, start)
    return bytes(out)


_WARM_CSV = b"name,count\nwarm-up,1\nreader,2\n"
_converter_cache: list[Any] = []


def _converter() -> Any:
    """The one MarkItDown of this child. It loads its type guess (magika)
    and each converter. Plugins stay off, and it reads no URL."""
    if not _converter_cache:
        from markitdown import MarkItDown

        _converter_cache.append(MarkItDown(enable_plugins=False))
    return _converter_cache[0]


def _markitdown(data: bytes, ext: str) -> tuple[str, str]:
    """``(text, title)`` of *data* by MarkItDown. It reads a stream, never a
    path and never a URL. Raises on a file it cannot read."""
    from markitdown import StreamInfo

    result = _converter().convert_stream(
        io.BytesIO(data), stream_info=StreamInfo(extension=ext, mimetype=FILE_TYPES[ext]))
    return str(result.markdown or ""), str(result.title or "")


def _pdfplumber(data: bytes) -> tuple[str, str]:
    """``(text, title)`` of a PDF by pdfplumber, page by page. It stops at
    :data:`MAX_FILE_TEXT_CHARS`, so a long PDF costs no more than its cap."""
    import pdfplumber

    parts: list[str] = []
    size = 0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        info = pdf.metadata or {}
        title = info.get("Title")
        for page in pdf.pages:
            text = (page.extract_text() or "").strip()
            page.close()
            if text:
                parts.append(text)
                size += len(text)
            if size > MAX_FILE_TEXT_CHARS:
                break
    return "\n\n".join(parts), title if isinstance(title, str) else ""


def _has_magic(data: bytes, ext: str) -> bool:
    """True when *data* starts as its type does. CSV has no mark."""
    mark = _MAGIC.get(ext)
    if mark is None:
        return True
    window = _PDF_MARK_WINDOW if ext == ".pdf" else len(mark)
    return mark in data[:window]


def _file_text(data: bytes, ext: str) -> dict[str, str]:
    """The answer for one file: MarkItDown first, and pdfplumber for a PDF
    that MarkItDown cannot read or reads as empty. An empty text is the
    honest answer of a scan, so ``text`` is ``""``."""
    reader, text, title = "markitdown", "", ""
    if not _has_magic(data, ext):
        return {"text": "", "title": "", "date": "", "reader": reader}
    try:
        text, title = _markitdown(data, ext)
    except ImportError:
        raise
    except Exception:  # MarkItDown fails in many ways on a hostile file
        text = ""
    if not text.strip() and ext == ".pdf":
        reader = "pdfplumber"
        try:
            text, title = _pdfplumber(data)
        except ImportError:
            raise
        except Exception:
            text, title = "", ""
    text = text.strip()
    if len(text) > MAX_FILE_TEXT_CHARS:
        text = text[:MAX_FILE_TEXT_CHARS] + _CUT_LINE.format(n=MAX_FILE_TEXT_CHARS)
    return {"text": text, "title": title, "date": "", "reader": reader}


def _answer_file(data: bytes, ext: str) -> dict[str, str]:
    """The one object that file mode writes. It never raises for a fault."""
    if ext not in FILE_TYPES:
        return {"fault": "failed"}
    try:
        return _file_text(data, ext)
    except ImportError:
        return {"fault": "package"}
    except MemoryError:
        return {"fault": "memory"}
    except Exception:  # no traceback reaches the parent, only a code
        return {"fault": "failed"}


def _preload(file_mode: bool = False) -> None:
    """Run one small page, or one tiny file, inside the confinement.

    Each import of Trafilatura and of lxml happens here, so the read of the
    real page opens no file. In file mode MarkItDown, its type guess, pdfminer
    and pdfplumber load here, and so do the threads of onnxruntime. An import
    that fails raises ``ImportError``."""
    if not file_mode:
        _extract_markdown(_WARM_HTML)
        return
    _markitdown(_WARM_CSV, ".csv")
    _markitdown(_warm_pdf(), ".pdf")
    _pdfplumber(_warm_pdf())


def main(file_ext: str = "") -> int:
    """Read one page from stdin, or with *file_ext* one file. The order of the
    steps is the control, and file mode keeps it."""
    try:
        _cc.close_inherited_fds()
        _limit_self(bool(file_ext))
        _before_confine(bool(file_ext))
        # One thread here, so the confinement binds each thread.
        _cc.confine(LANDLOCK_ABI)
        _preload(bool(file_ext))
        _cc.close_limits()
        # The untrusted page arrives only now, after the confinement.
        raw = sys.stdin.buffer.read()
        item = (_answer_file(raw, file_ext) if file_ext
                else _answer(raw.decode("utf-8", errors="replace")))
    except _cc.Refused:  # no confinement means no parse
        item = {"fault": "unavailable"}
    except ImportError:
        item = {"fault": "package"}
    except MemoryError:
        item = {"fault": "memory"}
    except Exception:  # a limit or a read that fails is a fault of the child
        item = {"fault": "failed"}
    # json.dumps writes ASCII, so the code page of the console cannot change it.
    sys.stdout.write(json.dumps(item) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
