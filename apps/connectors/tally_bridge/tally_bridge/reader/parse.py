"""Read a Tally answer into an element tree (brief section 5.5).

1. **Encoding.** Read it from the answer: a byte order mark first, then the
   ``charset`` of the Content-Type, then the XML declaration, then UTF-8.
2. **Bad characters.** Remove each control character that XML 1.0 cannot
   hold, both raw and as a character reference such as ``&#4;``.
3. **Parse with defusedxml**, which refuses entity and DTD attacks.
4. **Status.** A ``STATUS`` of ``0`` is a refusal by TallyPrime.

The text of a refusal comes from Tally. The tools never show it to the
member as it is: they answer one plain sentence, and the log keeps the text.
"""

from __future__ import annotations

import codecs
import re
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET

_RAW_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CHAR_REF = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")
_DECL = re.compile(rb"^\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)[\"']", re.I)
_DECL_TEXT = re.compile(r"^\s*<\?xml[^>]*\?>")
_CHARSET = re.compile(r"charset=\"?([A-Za-z0-9._-]+)", re.I)
_BOMS = (
    # UTF-32 first: its little-endian mark starts with the UTF-16 one.
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


class TallyAnswerError(Exception):
    """An answer that the bridge cannot read, or that Tally marked as failed."""


class TallyRefused(TallyAnswerError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _encoding(body: bytes, content_type: str | None) -> str:
    for bom, name in _BOMS:
        if body.startswith(bom):
            return name
    if content_type:
        m = _CHARSET.search(content_type)
        if m:
            return m.group(1)
    m = _DECL.match(body)
    if m:
        return m.group(1).decode("ascii")
    return "utf-8"


def _drop_bad_ref(m: re.Match[str]) -> str:
    ref = m.group(1)
    code = int(ref[1:], 16) if ref[0] in "xX" else int(ref)
    surrogate = 0xD800 <= code <= 0xDFFF
    if code in (9, 10, 13) or (32 <= code <= 0x10FFFF and code not in (0xFFFE, 0xFFFF)):
        return "" if surrogate else m.group(0)
    return ""


def clean(body: bytes, content_type: str | None = None) -> str:
    """The answer as text, with the characters removed that XML cannot hold."""
    name = _encoding(body, content_type)
    try:
        text = body.decode(name)
    except (LookupError, UnicodeDecodeError) as exc:
        raise TallyAnswerError(f"The answer is not readable as {name}.") from exc
    text = _DECL_TEXT.sub("", text.lstrip("﻿"), count=1)
    text = _RAW_CTRL.sub("", text)
    return _CHAR_REF.sub(_drop_bad_ref, text)


def parse_answer(body: bytes, content_type: str | None = None) -> ET.Element:
    text = clean(body, content_type)
    try:
        root = SafeET.fromstring(text)
    except ET.ParseError as exc:
        raise TallyAnswerError("The answer is not well-formed XML.") from exc
    except Exception as exc:  # defusedxml refuses DTDs and entities with its own errors
        raise TallyAnswerError("The answer holds XML that the bridge refuses.") from exc
    check_status(root)
    return root


# Where TallyPrime puts the status of an answer. A STATUS deeper in the data
# can be a field of a record, so the check reads these places only.
_STATUS_AT = ("STATUS", "HEADER/STATUS", "BODY/STATUS", "BODY/DATA/STATUS")
# Where TallyPrime puts a bare <RESPONSE>text</RESPONSE> refusal.
_RESPONSE_AT = (".", "BODY/RESPONSE", "BODY/DATA/RESPONSE")


def check_status(root: ET.Element) -> None:
    """Raise TallyRefused when the answer is a refusal.

    A refusal is a STATUS of 0, any LINEERROR, or a RESPONSE that holds only text.
    """
    for path in _STATUS_AT:
        status = root.find(path)
        if status is not None and (status.text or "").strip() == "0":
            raise TallyRefused(_detail(root))
    if root.find(".//LINEERROR") is not None:
        raise TallyRefused(_detail(root))
    for path in _RESPONSE_AT:
        el = root if path == "." else root.find(path)
        if el is not None and el.tag == "RESPONSE" and len(el) == 0 and (el.text or "").strip():
            raise TallyRefused((el.text or "").strip())


def _detail(root: ET.Element) -> str:
    for path in (".//LINEERROR", ".//DESC", ".//ERROR"):
        el = root.find(path)
        if el is not None and (el.text or "").strip():
            return (el.text or "").strip()
    return "STATUS 0"
