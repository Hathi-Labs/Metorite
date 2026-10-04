"""The text of a chat attachment, by pure parsing (H-229).

D85 took ``code_task`` from every shared agent. On 2026-10-02 and 2026-10-03
the projects-assistant of a customer org used it on the production host to
read a member's ``.docx``. This module gives that flow back with no code
execution at all: it parses bytes in this process and returns text.

The rules, each with a test in ``tests/unit/test_read_attachment.py``:

* **No subprocess and no code.** ``.docx`` is a zip read with :mod:`zipfile`
  and :mod:`xml.etree.ElementTree` from the standard library. ``.pdf`` goes
  through ``pypdf``, which is pure Python. Its one subprocess, ``jbig2dec`` for
  a JBIG2 image, is switched off for every parse here
  (``jbig2dec_binary=None``).
* **Bounded.** The input is at most :data:`MAX_FILE_BYTES`. A ``.docx`` reads
  ONE part, the main document, and stops at :data:`MAX_DOCX_XML_BYTES` of
  decompressed XML, so a zip bomb costs no more than that. A PDF reads at most
  :data:`MAX_PDF_PAGES` pages, and each stream decompresses to at most
  :data:`PDF_STREAM_LIMIT`. Text files keep :data:`MAX_TEXT_LINES` lines. The
  text kept is at most :data:`MAX_EXTRACT_CHARS`. A deadline stops a parse
  that runs past :data:`DEADLINE_SECONDS`.
* **A clean refusal.** Every failure raises :class:`AttachmentRefused`, with
  a sentence for the member. No parser exception reaches the caller.

The module reads no file and no context. ``acb_skills.attachment_tools``
finds the file, and calls :func:`extract_text` on its bytes.
"""
from __future__ import annotations

import io
import time
import zipfile
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

__all__ = [
    "DEADLINE_SECONDS",
    "MAX_DOCX_PARAGRAPHS",
    "MAX_DOCX_XML_BYTES",
    "MAX_EXTRACT_CHARS",
    "MAX_FILE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_TEXT_LINES",
    "PDF_STREAM_LIMIT",
    "SUPPORTED_SUFFIXES",
    "AttachmentRefused",
    "Extracted",
    "extract_text",
]

#: The upload cap of ``gateway/routes/workspace.py`` (``_MAX_UPLOAD_BYTES``).
MAX_FILE_BYTES = 25 * 1024 * 1024
#: The main document part of a ``.docx``, decompressed. The zip-bomb cap.
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
#: The package relationships part, which names the main part.
MAX_DOCX_RELS_BYTES = 256 * 1024
#: A ``.docx`` with more zip entries than this is not a document.
MAX_ZIP_ENTRIES = 5_000
MAX_DOCX_PARAGRAPHS = 5_000
MAX_PDF_PAGES = 100
MAX_TEXT_LINES = 5_000
#: Text kept from one file. The tool shows it a page at a time.
MAX_EXTRACT_CHARS = 2_000_000
#: The decompressed size of one PDF stream. pypdf's own default is 75 MB.
PDF_STREAM_LIMIT = 8 * 1024 * 1024
DEADLINE_SECONDS = 20.0

_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv"})
SUPPORTED_SUFFIXES = frozenset({".docx", ".pdf"}) | _TEXT_SUFFIXES

#: How many parser events pass between two deadline checks.
_CHECK_EVERY = 256


class AttachmentRefused(ValueError):
    """A file this module will not read. ``str()`` is a sentence for the member."""


@dataclass(frozen=True)
class Extracted:
    """The text of one file, and how much of the file it covers."""

    text: str
    kind: str
    unit: str
    read: int
    total: int | None
    stopped: bool


class _Deadline:
    """A wall-clock limit that the parse loops check."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self._end:
            raise AttachmentRefused(
                "Reading this file took too long, so I stopped. "
                "Ask the member for a shorter file or a plain text copy."
            )


def extract_text(data: bytes, suffix: str, *, seconds: float = DEADLINE_SECONDS) -> Extracted:
    """The text of *data*, a file with the name suffix *suffix*.

    Raises :class:`AttachmentRefused` for a type it does not read, for a file
    over a cap and for a file that does not parse.
    """
    kind = suffix.lower()
    if kind not in SUPPORTED_SUFFIXES:
        raise AttachmentRefused(
            f"I cannot read a {kind or 'file without a type'} file. "
            "I read .docx, .pdf, .txt, .md and .csv files."
        )
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentRefused(
            f"This file is {len(data)} bytes, and I read files of at most "
            f"{MAX_FILE_BYTES} bytes."
        )
    deadline = _Deadline(seconds)
    if kind == ".docx":
        return _docx_text(data, deadline)
    if kind == ".pdf":
        return _pdf_text(data, deadline)
    return _plain_text(data, kind)


# ── .txt .md .csv ────────────────────────────────────────────────────────────


def _decode(data: bytes) -> str:
    """Text from bytes: a UTF-16 or UTF-8 byte order mark, else UTF-8."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    if "\x00" in text:
        raise AttachmentRefused("This file holds binary data, so it is not a text file.")
    return text


def _plain_text(data: bytes, kind: str) -> Extracted:
    lines = _decode(data).splitlines()
    kept = lines[:MAX_TEXT_LINES]
    text = "\n".join(kept)
    clipped = len(text) > MAX_EXTRACT_CHARS
    return Extracted(
        text=text[:MAX_EXTRACT_CHARS], kind=kind.lstrip("."), unit="line",
        read=len(kept), total=len(lines),
        stopped=clipped or len(kept) < len(lines),
    )


# ── .docx ───────────────────────────────────────────────────────────────────

_NOT_WORD = (
    "I could not read this file as a Word document. If it has a password, "
    "ask the member to remove it, or to attach a PDF or a text copy."
)
_OFFICE_DOCUMENT = "/officeDocument"


def _local(tag: object) -> str:
    """An XML tag without its namespace. Strict and transitional OOXML agree."""
    return str(tag).rsplit("}", 1)[-1]


def _read_part(zf: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    """At most *cap* decompressed bytes of one part, or a refusal.

    The read asks for ``cap + 1`` bytes, so a part that declares a small size
    and holds more still stops at the cap. :mod:`zipfile` also stops at the
    declared size and checks the CRC there.
    """
    if info.flag_bits & 0x1:
        raise AttachmentRefused(_NOT_WORD)
    if info.file_size > cap:
        raise AttachmentRefused(
            "This Word document is too large once unpacked, so I did not read it."
        )
    with zf.open(info) as fh:
        out = fh.read(cap + 1)
    if len(out) > cap:
        raise AttachmentRefused(
            "This Word document is too large once unpacked, so I did not read it."
        )
    return out


def _refuse_dtd(xml: bytes) -> None:
    """A Word part has no DTD. One that does is refused before any parse."""
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise AttachmentRefused(_NOT_WORD)


def _main_part(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    """The main document part, named by ``_rels/.rels``, else the usual name."""
    name = "word/document.xml"
    try:
        rels = zf.getinfo("_rels/.rels")
    except KeyError:
        rels = None
    if rels is not None:
        xml = _read_part(zf, rels, MAX_DOCX_RELS_BYTES)
        _refuse_dtd(xml)
        for el in ET.fromstring(xml):
            if str(el.get("Type", "")).endswith(_OFFICE_DOCUMENT) and el.get("Target"):
                name = str(el.get("Target")).lstrip("/")
                break
    try:
        return zf.getinfo(name)
    except KeyError:
        raise AttachmentRefused(_NOT_WORD) from None


def _paragraph(p: ET.Element) -> str:
    """The text of one ``w:p``. A tab, a break and a hyphen keep their meaning."""
    out: list[str] = []
    for el in p.iter():
        name = _local(el.tag)
        if name == "t" and el.text:
            out.append(el.text)
        elif name == "tab":
            out.append("\t")
        elif name in ("br", "cr"):
            out.append("\n")
        elif name == "noBreakHyphen":
            out.append("-")
    return "".join(out).strip()


class _Body:
    """The lines of a document body, in order. A table row is one line."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.chars = 0
        self.paragraphs = 0
        self._rows: list[list[str]] = []
        self._cells: list[list[str]] = []

    def full(self) -> bool:
        return self.paragraphs >= MAX_DOCX_PARAGRAPHS or self.chars >= MAX_EXTRACT_CHARS

    def _emit(self, line: str) -> None:
        if line:
            self.lines.append(line)
            self.chars += len(line) + 1

    def start(self, name: str) -> None:
        if name == "tbl":
            self._rows.append([])
            self._cells.append([])
        elif name == "tr" and self._rows:
            self._rows[-1] = []
        elif name == "tc" and self._cells:
            self._cells[-1] = []

    def end(self, name: str, el: ET.Element) -> None:
        if name == "p":
            self.paragraphs += 1
            text = _paragraph(el)
            if self._cells:
                self._cells[-1].append(text)
            else:
                self._emit(text)
            el.clear()
        elif name == "tc" and self._cells:
            self._rows[-1].append(" ".join(t for t in self._cells[-1] if t))
        elif name == "tr" and self._rows:
            row = " | ".join(self._rows[-1])
            if len(self._cells) > 1:
                self._cells[-2].append(row)
            else:
                self._emit(row)
            el.clear()
        elif name == "tbl" and self._rows:
            self._rows.pop()
            self._cells.pop()


def _docx_text(data: bytes, deadline: _Deadline) -> Extracted:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if len(zf.infolist()) > MAX_ZIP_ENTRIES:
                raise AttachmentRefused(_NOT_WORD)
            xml = _read_part(zf, _main_part(zf), MAX_DOCX_XML_BYTES)
        _refuse_dtd(xml)
        body = _Body()
        for n, (event, el) in enumerate(ET.iterparse(io.BytesIO(xml), events=("start", "end"))):
            if n % _CHECK_EVERY == 0:
                deadline.check()
            name = _local(el.tag)
            if event == "start":
                body.start(name)
            else:
                body.end(name, el)
            if body.full():
                break
    except AttachmentRefused:
        raise
    except Exception:  # every parser failure is one clean refusal
        raise AttachmentRefused(_NOT_WORD) from None
    stopped = body.full()
    return Extracted(
        text="\n".join(body.lines)[:MAX_EXTRACT_CHARS], kind="docx", unit="paragraph",
        read=body.paragraphs, total=None if stopped else body.paragraphs, stopped=stopped,
    )


# ── .pdf ────────────────────────────────────────────────────────────────────

_NOT_PDF = (
    "I could not read this file as a PDF. Ask the member for another copy, "
    "or for a Word or text version."
)

#: The pypdf limits for every parse here. ``jbig2dec_binary=None`` is the one
#: that matters most: it is the only path in pypdf that starts a subprocess.
_PDF_CONFIG = {
    "jbig2dec_binary": None,
    "disable_legacy_handling": True,
    "maximum_declared_stream_length": PDF_STREAM_LIMIT,
    "array_based_stream_maximum_output_length": PDF_STREAM_LIMIT,
    "lzw_maximum_output_length": PDF_STREAM_LIMIT,
    "run_length_maximum_output_length": PDF_STREAM_LIMIT,
    "zlib_maximum_output_length": PDF_STREAM_LIMIT,
    "image_maximum_buffer_size": PDF_STREAM_LIMIT,
}


def _open_pdf(data: bytes) -> Any:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception:  # a cipher that pypdf cannot run is a lock too
            opened = 0
        if not opened:
            raise AttachmentRefused(
                "This PDF has a password, so I cannot read it. Ask the member "
                "to attach a copy without one."
            )
    return reader


def _page_text(pages: Any, index: int) -> str:
    """One page's text. A page that does not parse says so, and the rest go on."""
    try:
        text = (pages[index].extract_text() or "").strip()
    except Exception:  # one bad page is not a bad file
        text = "(The text of this page could not be read.)"
    return f"[Page {index + 1}]\n{text}" if text else f"[Page {index + 1}]"


def _pdf_text(data: bytes, deadline: _Deadline) -> Extracted:
    import pypdf

    parts: list[str] = []
    chars = 0
    try:
        with pypdf.apply_configuration(**_PDF_CONFIG):
            pages = _open_pdf(data).pages
            total = len(pages)
            for index in range(min(total, MAX_PDF_PAGES)):
                deadline.check()
                parts.append(_page_text(pages, index))
                chars += len(parts[-1]) + 1
                if chars >= MAX_EXTRACT_CHARS:
                    break
    except AttachmentRefused:
        raise
    except Exception:  # every parser failure is one clean refusal
        raise AttachmentRefused(_NOT_PDF) from None
    read = len(parts)
    return Extracted(
        text="\n".join(parts)[:MAX_EXTRACT_CHARS], kind="pdf", unit="page",
        read=read, total=total, stopped=read < total,
    )
